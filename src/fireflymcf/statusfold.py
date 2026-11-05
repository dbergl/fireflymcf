"""One appended per-tick routine that lights cells, maps enums and drives group variants.

`bitcombine` folds several bits into one cell; this module is the other direction: each item reads
one u16 receive slot and writes its own u16 display cell, in item order, inside ONE routine on
`followchannel`'s per-tick hook. Two item kinds:

- `Light`: the cell is 1 (lit) or 0. The slot's field `v = (slot & mask) >> shift` is tested by
  `kind`: `bit` (`v & value != 0`), `eq`, `ne` or `ge` (`v >= value`). With `valid_max` set, a
  field above it is off (0xFA is the documented RV-C not-available guard). With `variant_group`
  set, the routine then calls GuiCode's `setVariant(group, lit)` through the fn-pointer at
  `setter_addr`, the call shape of `followchannel.build_display_bridge`.
- `Enum`: the cell is the index of the first key equal to the field, or `len(keys)` otherwise.
- `States`: N `Match` rules, each on its OWN slot (`(slot & mask) >> shift` in `[lo, hi]`),
  tried in order; the cell is the first matching rule's `value`, or `unknown`. With `gate` set
  (another `Match`), the cell is that plus `gate_offset` while the gate does NOT hold. With
  `variant_group` set, the routine then calls `setVariant(group, variants[state])`, `state` the
  ungated cell value (an index into `variants`): a multi-state button's next payload follows the
  state its unit reports (`add_button_states`).

A slot still holding `followchannel.STALE_SENTINEL` (nothing received yet) is tested BEFORE the
mask, so it reads off / `len(keys)` however the mask would have cut it.

The contract is `readout.build`'s: `push {r0-r3,lr}`, work in r0-r3 only (r4-r11 untouched), then
`pop.w {r0-r3,lr}` and `b.w` to the per-tick function the hook displaced. `blx` clobbers r0-r3,
r12 and lr; every item reloads its own pool words, so nothing is carried across a call. The blob
is assembled by `thumb.Routine` and verified by `thumb.assert_disassembles_to`. A plan whose
literal pool sits past T1 `ldr`'s 1020-byte reach is rebuilt with `ldr.w` literal loads; each
item's branches are local to the item, so the narrow conditional branches always reach.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import followchannel, readout, theme, thumb


class StatusFoldError(ValueError):
    """A status fold could not be resolved or built (bad item, slot, cell or hook)."""


KINDS = ("bit", "eq", "ne", "ge")
MAX_KEYS = 16


@dataclass(frozen=True)
class Light:
    slot_addr: int
    mask: int
    shift: int
    kind: str                       # "bit" | "eq" | "ne" | "ge"
    value: int
    valid_max: int | None
    cell_addr: int                  # u16 cell: 0 off, 1 on
    variant_group: int | None       # setVariant(group, lit) after the write


@dataclass(frozen=True)
class Enum:
    slot_addr: int
    mask: int
    shift: int
    keys: tuple[int, ...]           # cell = index of the matching key, len(keys) otherwise
    cell_addr: int


@dataclass(frozen=True)
class Match:
    """`(slot & mask) >> shift` in `[lo, hi]`; a slot still holding the stale sentinel fails."""
    slot_addr: int
    mask: int
    shift: int
    lo: int
    hi: int


@dataclass(frozen=True)
class States:
    rules: tuple                    # Match, tried in order
    values: tuple                   # cell value when rules[i] matches first
    unknown: int                    # cell value when none does
    cell_addr: int                  # u16 cell
    variant_group: int | None = None
    variants: tuple = ()            # setVariant(group, variants[cell]) per ungated cell value
    gate: Match | None = None
    gate_offset: int = 0            # added to the cell while `gate` does not hold


@dataclass(frozen=True)
class FoldPlan:
    items: tuple                    # Light | Enum, emitted in order
    hook_at: int
    hook_old: bytes
    tail_target: int
    setter_addr: int = followchannel.VARIANT_SETTER


def _check_addr(name: str, addr: int) -> None:
    if addr % 2:
        raise StatusFoldError(f"{name} {addr:#x} is odd; a u16 slot or cell must be 2-aligned")
    if not theme.SRAM_LOW <= addr < theme.SRAM_HIGH - 1:
        raise StatusFoldError(
            f"{name} {addr:#x} is outside SRAM {theme.SRAM_LOW:#x}..{theme.SRAM_HIGH:#x}")


def _check_u16(name: str, v: int) -> None:
    if not 0 <= v <= 0xFFFF:
        raise StatusFoldError(f"{name} {v:#x} does not fit the u16 field it is compared with")


def validate(items) -> tuple:
    """The item checks `resolve` makes before it reads the image; returns the items as a tuple."""
    items = tuple(items)
    if not items:
        raise StatusFoldError("a status fold needs at least one item")
    cells = set()
    for it in items:
        if not isinstance(it, (Light, Enum, States)):
            raise StatusFoldError(f"item {it!r} is not a Light, an Enum or a States")
        _check_addr("cell_addr", it.cell_addr)
        if isinstance(it, States):
            _validate_states(it)
        else:
            _check_match_field(it)
        if it.cell_addr in cells:
            raise StatusFoldError(f"duplicate cell_addr {it.cell_addr:#x}: two items would "
                                  "overwrite each other every tick")
        cells.add(it.cell_addr)
        if isinstance(it, States):
            pass
        elif isinstance(it, Light):
            if it.kind not in KINDS:
                raise StatusFoldError(f"kind {it.kind!r} is not one of {KINDS}")
            _check_u16("value", it.value)
            if it.valid_max is not None:
                _check_u16("valid_max", it.valid_max)
            if it.variant_group is not None and not 0 <= it.variant_group <= 0xFF:
                raise StatusFoldError(f"variant_group {it.variant_group:#x} must be 0..0xFF "
                                      "(`movs r0, #imm8`)")
        else:
            if len(it.keys) > MAX_KEYS:
                raise StatusFoldError(f"{len(it.keys)} keys; an Enum takes at most {MAX_KEYS}")
            if len(set(it.keys)) != len(it.keys):
                raise StatusFoldError(f"duplicate key in {it.keys}")
            for k in it.keys:
                _check_u16("key", k)
    return items


def _check_match_field(it) -> None:
    _check_addr("slot_addr", it.slot_addr)
    if not 1 <= it.mask <= 0xFFFF:
        raise StatusFoldError(f"mask {it.mask:#x} must be 1..0xFFFF")
    if not 0 <= it.shift <= 15:
        raise StatusFoldError(f"shift {it.shift} must be 0..15")


def _validate_states(it: States) -> None:
    if not it.rules or len(it.rules) != len(it.values):
        raise StatusFoldError(f"a States item needs one value per rule, got {len(it.rules)} "
                              f"rule(s) and {len(it.values)} value(s)")
    if len(it.rules) > MAX_KEYS:
        raise StatusFoldError(f"{len(it.rules)} rules; a States item takes at most {MAX_KEYS}")
    for m in it.rules + ((it.gate,) if it.gate is not None else ()):
        if not isinstance(m, Match):
            raise StatusFoldError(f"rule {m!r} is not a Match")
        _check_match_field(m)
        # the stale sentinel is tested before the mask, so a whole-slot hi of 0xFFFF could never
        # be reached; anything above it does not fit the field
        if not 0 <= m.lo <= m.hi <= 0xFFFE:
            raise StatusFoldError(f"match range [{m.lo}, {m.hi}] must satisfy "
                                  "0 <= lo <= hi <= 0xFFFE")
    values = tuple(it.values) + (it.unknown,)
    top = max(values) + (it.gate_offset if it.gate is not None else 0)
    if min(values) < 0 or top > 0xFF:
        raise StatusFoldError(f"cell values {values} (+ gate offset {it.gate_offset}) must be "
                              "0..255 (`movs r1, #imm8`)")
    if it.gate is not None and not 1 <= it.gate_offset <= 0xFF:
        raise StatusFoldError(f"gate_offset {it.gate_offset} must be 1..255")
    if it.variant_group is not None:
        if not 0 <= it.variant_group <= 0xFF:
            raise StatusFoldError(f"variant_group {it.variant_group:#x} must be 0..0xFF")
        if len(it.variants) <= max(values) or any(not 0 <= v <= 0xF for v in it.variants):
            raise StatusFoldError(f"variants {it.variants} must name a variant 0..15 for every "
                                  f"cell value {sorted(set(values))}")


def resolve(data: bytes, items) -> FoldPlan:
    """Validate `items`, then read the current detour state off `data` (chaining ahead of any
    routine already on the hook, as `readout.resolve` does)."""
    items = validate(items)
    hook_old, tail = followchannel.hook_state(data)
    return FoldPlan(items, followchannel.HOOK_OFF, hook_old, tail)


def _imm(v: int) -> str:
    return f"{v:#x}" if v >= 10 else str(v)


def _load_const(r: thumb.Routine, rd: int, v: int) -> None:
    """`rD = v`: `movs` for imm8 (sets flags), `movw` otherwise (flags kept)."""
    if v <= 0xFF:
        r.emit(2, lambda h, s: (thumb.movs_imm(rd, v), f"movs r{rd}, #{_imm(v)}"))
    else:
        r.emit(4, lambda h, s: (thumb.movw(rd, v), f"movw r{rd}, #{v:#x}"))


def _cmp_const(r: thumb.Routine, v: int) -> None:
    """Flags from `r3 - v`, through r2 when `v` is past `cmp`'s imm8."""
    if v <= 0xFF:
        r.emit(2, lambda h, s: (thumb.cmp_imm8(3, v), f"cmp r3, #{_imm(v)}"))
    else:
        _load_const(r, 2, v)
        r.emit(2, lambda h, s: (thumb.cmp_reg(3, 2), "cmp r3, r2"))


def _branch(r: thumb.Routine, cond: str, target: str) -> None:
    text = {"eq": "beq", "ne": "bne", "cc": "blo", "hi": "bhi"}[cond]
    r.emit(2, lambda h, s: (thumb.bcc_n(h, s(target), cond), f"{text} #{s(target):#x}"))


def _emit_field(r: thumb.Routine, it, stale_r1: int, store: str) -> None:
    """r3 = the slot's field; with `r1 = stale_r1` and a branch to `store` when it is stale."""
    r.ldr_lit(0, "a%x" % it.slot_addr)
    r.emit(2, lambda h, s: (thumb.ldrh_imm(3, 0, 0), "ldrh r3, [r0]"))
    r.emit(2, lambda h, s: (thumb.movs_imm(1, stale_r1), f"movs r1, #{_imm(stale_r1)}"))
    r.emit(4, lambda h, s: (thumb.movw(2, followchannel.STALE_SENTINEL),
                            f"movw r2, #{followchannel.STALE_SENTINEL:#x}"))
    r.emit(2, lambda h, s: (thumb.cmp_reg(3, 2), "cmp r3, r2"))
    _branch(r, "eq", store)
    if it.mask != 0xFFFF:
        _load_const(r, 2, it.mask)
        r.emit(2, lambda h, s: (thumb.ands_reg(3, 2), "ands r3, r2"))
    if it.shift:
        r.emit(2, lambda h, s: (thumb.lsrs_imm(3, 3, it.shift), f"lsrs r3, r3, #{it.shift}"))


def _emit_light(r: thumb.Routine, it: Light, store: str) -> None:
    _emit_field(r, it, 0, store)                    # r1 = 0 (off) until the field passes
    if it.valid_max is not None:
        _cmp_const(r, it.valid_max)
        _branch(r, "hi", store)
    if it.kind == "bit":
        _load_const(r, 2, it.value)
        r.emit(2, lambda h, s: (thumb.ands_reg(3, 2), "ands r3, r2"))
        _branch(r, "eq", store)
    else:
        _cmp_const(r, it.value)
        _branch(r, {"eq": "ne", "ne": "eq", "ge": "cc"}[it.kind], store)
    r.emit(2, lambda h, s: (thumb.movs_imm(1, 1), "movs r1, #1"))


def _emit_enum(r: thumb.Routine, it: Enum, store: str) -> None:
    n = len(it.keys)
    _emit_field(r, it, n, store)
    for idx, key in enumerate(it.keys):
        # `movs` sets the flags, so the index goes in before the compare, never between it and
        # its branch.
        r.emit(2, lambda h, s, i=idx: (thumb.movs_imm(1, i), f"movs r1, #{_imm(i)}"))
        _cmp_const(r, key)
        _branch(r, "eq", store)
    r.emit(2, lambda h, s: (thumb.movs_imm(1, n), f"movs r1, #{_imm(n)}"))


def _emit_match(r: thumb.Routine, m: Match, miss: str) -> None:
    """Branch to `miss` unless `m` holds; r0, r2 and r3 are scratch, r1 is kept."""
    r.ldr_lit(0, "a%x" % m.slot_addr)
    r.emit(2, lambda h, s: (thumb.ldrh_imm(3, 0, 0), "ldrh r3, [r0]"))
    r.emit(4, lambda h, s: (thumb.movw(2, followchannel.STALE_SENTINEL),
                            f"movw r2, #{followchannel.STALE_SENTINEL:#x}"))
    r.emit(2, lambda h, s: (thumb.cmp_reg(3, 2), "cmp r3, r2"))
    _branch(r, "eq", miss)
    if m.mask != 0xFFFF:
        _load_const(r, 2, m.mask)
        r.emit(2, lambda h, s: (thumb.ands_reg(3, 2), "ands r3, r2"))
    if m.shift:
        r.emit(2, lambda h, s: (thumb.lsrs_imm(3, 3, m.shift), f"lsrs r3, r3, #{m.shift}"))
    if m.lo:
        _cmp_const(r, m.lo)
        _branch(r, "cc", miss)
    _cmp_const(r, m.hi)
    _branch(r, "hi", miss)


def _emit_states(r: thumb.Routine, it: States, i: int) -> None:
    """The first matching rule's value (else `unknown`) -> the cell; the variant call; then the
    gate's offset. The cell may hold the ungated value between the two stores: the live redraw
    reads it only after the whole tick chain has run (`followchannel` module docstring)."""
    found = "found%d" % i
    for k, (m, v) in enumerate(zip(it.rules, it.values)):
        miss = "miss%d_%d" % (i, k)
        _emit_match(r, m, miss)
        r.emit(2, lambda h, s, v=v: (thumb.movs_imm(1, v), f"movs r1, #{_imm(v)}"))
        r.emit(2, lambda h, s: (thumb.b_n(h, s(found)), f"b #{s(found):#x}"))
        r.label(miss)
    r.emit(2, lambda h, s: (thumb.movs_imm(1, it.unknown), f"movs r1, #{_imm(it.unknown)}"))
    r.label(found)
    r.ldr_lit(0, "a%x" % it.cell_addr)
    r.emit(2, lambda h, s: (thumb.strh_imm(1, 0, 0), "strh r1, [r0]"))
    if it.variant_group is not None:
        # r1 = variants[r1], by a compare chain over the values the cell can hold (each arm
        # is local, so the narrow branches always reach)
        call = "call%d" % i
        values = sorted(set(it.values) | {it.unknown})
        for v in values[:-1]:
            nxt = "var%d_%d" % (i, v)
            r.emit(2, lambda h, s, v=v: (thumb.cmp_imm8(1, v), f"cmp r1, #{_imm(v)}"))
            _branch(r, "ne", nxt)
            w = it.variants[v]
            r.emit(2, lambda h, s, w=w: (thumb.movs_imm(1, w), f"movs r1, #{_imm(w)}"))
            r.emit(2, lambda h, s: (thumb.b_n(h, s(call)), f"b #{s(call):#x}"))
            r.label(nxt)
        w = it.variants[values[-1]]
        r.emit(2, lambda h, s, w=w: (thumb.movs_imm(1, w), f"movs r1, #{_imm(w)}"))
        r.label(call)
        g = it.variant_group
        r.ldr_lit(3, "setter")
        r.emit(2, lambda h, s: (thumb.ldr_imm(3, 3, 0), "ldr r3, [r3]"))
        r.emit(2, lambda h, s, g=g: (thumb.movs_imm(0, g), f"movs r0, #{_imm(g)}"))
        r.emit(2, lambda h, s: (thumb.blx_reg(3), "blx r3"))
    if it.gate is not None:
        done = "gated%d" % i
        _emit_match(r, it.gate, "shut%d" % i)
        r.emit(2, lambda h, s: (thumb.b_n(h, s(done)), f"b #{s(done):#x}"))
        r.label("shut%d" % i)
        r.ldr_lit(0, "a%x" % it.cell_addr)
        r.emit(2, lambda h, s: (thumb.ldrh_imm(1, 0, 0), "ldrh r1, [r0]"))
        off = it.gate_offset
        r.emit(2, lambda h, s: (thumb.adds_imm8(1, off), f"adds r1, #{_imm(off)}"))
        r.emit(2, lambda h, s: (thumb.strh_imm(1, 0, 0), "strh r1, [r0]"))
        r.label(done)


def _routine(plan: FoldPlan, wide: bool) -> thumb.Routine:
    pool: dict = {}
    for it in plan.items:
        slots = ([m.slot_addr for m in it.rules + ((it.gate,) if it.gate else ())]
                 if isinstance(it, States) else [it.slot_addr])
        for a in slots + [it.cell_addr]:
            pool.setdefault("a%x" % a, a)
    if any(isinstance(it, (Light, States)) and it.variant_group is not None
           for it in plan.items):
        pool["setter"] = plan.setter_addr
    r = thumb.Routine(pool, wide_literals=wide, error=StatusFoldError)
    r.emit(2, lambda h, s: (thumb.push_reglist([0, 1, 2, 3, 14]), "push {r0, r1, r2, r3, lr}"))
    for i, it in enumerate(plan.items):
        if isinstance(it, States):
            _emit_states(r, it, i)
            continue
        store = "store%d" % i
        (_emit_light if isinstance(it, Light) else _emit_enum)(r, it, store)
        r.label(store)
        r.ldr_lit(0, "a%x" % it.cell_addr)
        r.emit(2, lambda h, s: (thumb.strh_imm(1, 0, 0), "strh r1, [r0]"))
        if isinstance(it, Light) and it.variant_group is not None:
            # build_display_bridge's call: r1 already holds lit, the setter's second argument.
            g = it.variant_group
            r.ldr_lit(3, "setter")
            r.emit(2, lambda h, s: (thumb.ldr_imm(3, 3, 0), "ldr r3, [r3]"))
            r.emit(2, lambda h, s, g=g: (thumb.movs_imm(0, g), f"movs r0, #{_imm(g)}"))
            r.emit(2, lambda h, s: (thumb.blx_reg(3), "blx r3"))
    r.emit(4, lambda h, s: (thumb.pop_w_reglist([0, 1, 2, 3, 14]), "pop.w {r0, r1, r2, r3, lr}"))
    r.emit(4, lambda h, s: (thumb.b_w(h, plan.tail_target), f"b.w #{plan.tail_target:#x}"))
    return r


def build(plan: FoldPlan, code_load_addr: int) -> tuple[bytes, list]:
    """`(blob, disassembly)`: the routine and its literal pool, verified with capstone."""
    if code_load_addr % 4:
        raise StatusFoldError(f"code_load_addr {code_load_addr:#x} is not 4-aligned")
    validate(plan.items)
    try:
        return _routine(plan, wide=False).assemble(code_load_addr, readout.ENTRY_OFF)
    except ValueError as e:
        if "ldr (literal) displacement" not in str(e):
            raise
    return _routine(plan, wide=True).assemble(code_load_addr, readout.ENTRY_OFF)


def detour_edit(plan: FoldPlan, code_load_addr: int) -> tuple[int, bytes, bytes]:
    """`(hook_offset, stock_bytes, new_bl)`: identical contract to `readout.detour_edit`."""
    followchannel._check_plan_hook(plan)
    hook_runtime = theme.GUICODE_BASE_DEFAULT + plan.hook_at
    return plan.hook_at, plan.hook_old, thumb.bl(hook_runtime, code_load_addr + readout.ENTRY_OFF)
