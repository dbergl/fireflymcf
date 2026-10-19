"""The gated light->pump follower: author the routine, its two CAN templates and the detour.

**What this builds.** A dispatch stub in dead space that runs once per UI tick (it replaces one
`bl` inside the per-tick paint path). On a *change* of a watched G12 output's status bit it sends
one CAN frame -- turning a target channel on or off -- but only while a gate option is enabled.
It is edge-triggered (a latch byte remembers the last state) so it transmits exactly on
transitions, and it tail-calls the original per-tick function it displaced.

**Why hand-authored machine code is safe here.** Same argument as `thumb.py`: nothing is trusted
on inspection. `build` runs `thumb.assert_disassembles_to` over the whole routine, re-reading each
emitted instruction with capstone and comparing it to the text this module claims it authored;
every new encoder `thumb.py` gained for this routine is checked against a real instruction already
in stock 13.6. `resolve` derives every address structurally from the image and **refuses** (raises
`FollowChannelError`) rather than corrupt: if the byte it means to detour is not the stock `bl`,
or an option/register cannot be recovered, it stops.

**Addressing idiom (revised ruling 3).** 13.6 contains no `movt` at all -- the compiler
materialises 32-bit constants from a literal pool -- so the routine does the same: a pool of u32
absolute addresses is appended after the code and each address is loaded with `ldr rT,[pc,#imm]`.
The two templates sit at the very front of the blob so their absolute addresses are fixed at build
time and go straight into the pool.

Blob layout (offsets from `code_load_addr`):

    +0x00  on_template   (12 bytes, wire order)
    +0x0C  off_template  (12 bytes)
    +0x18  routine entry (code)
      ...  code ...
      pad to 4-align
      ...  literal pool: the u32 absolute addresses the ldr-literals reference

The register discipline (which register the CAN sender reads the frame pointer from, and whether
it clobbers r0-r3) is pinned in Task 3 against the real send site; Task 2 fixes the *shape* and
unit-tests the edge logic. The `push {r0-r3,lr} … pop {r0-r3,lr}` + `b.w 0x00247BF8` tail-call
contract is load-bearing and preserved (ruling 4).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from . import celltables, config, guidata, register, statusbind, statusjoin, theme, thumb

# The per-tick paint function the detoured `bl` originally called; our routine tail-branches to it.
TAIL_TARGET = 0x00247BF8

# The single caller: GuiCode-section offset of the `bl 0x00247BF8` we retarget, and its stock bytes.
HOOK_OFF = 0xEE3C
HOOK_STOCK = bytes.fromhex("f8f7dcfe")

# Stock 13.6's GuiCode append point (base + length - 4, the checksum word). Every routine this
# module authors is appended at or beyond it; a hook that already reaches into [here, append)
# is a PREVIOUS appended routine, and a new one CHAINS onto it (see `hook_state`).
STOCK_APPEND_POINT = 0x00259BB4

# Host CAN-frame sender: SRAM holds a function pointer at this address (host API +0x08). The
# routine does `ldr r2,[&sender]; blx r2`.
SENDER_PTR_ADDR = 0x2003986C

# Group-variant setter: SRAM holds a function pointer at this address. The stock Low Volts bridge
# block (0x0024A176-0x0024A1A6, proven-from-code) does `ldr rX,[0x20039908]; ldr rX,[rX];
# movs r1,#variant; movs r0,#group_id; blx rX` each tick, unconditionally, to drive a config-
# toggle's on-screen checkbox state and the tap direction of the next write.
VARIANT_SETTER = 0x20039908

# Receive-filter status slot array. When a subscribed frame arrives, the LCD firmware
# (FUN_0021C760) writes the extracted value to slot[var]. The store base is **index-split**
# (proven-from-code, `0x0021C9B0`-`0x0021C9C4`, and confirmed against PC.44): the filter does
# `cmp r5,#0xff` on the var *index*, then `strhls r2,[r1,#0x10c]` for index <= 255 versus
# `strhhi r2,[r1,#0x144]` for index > 255, where `r1 = r4 + idx*2` and `r4 = 0x20007EBC`. So a
# LOW-index var lands at `0x20007FC8 + idx*2` and a HIGH-index var at `0x20008000 + idx*2` -- two
# separate slot arrays.  `status_slot_addr()` must be used for every var; the old flat
# `STATUS_SLOT_BASE + var*2` was correct only for index <= 255 and put every high-index var
# (e.g. the 0xEE config selector, var 289) 56 bytes into unwritten space -- the PC.44 bug that
# made the v8 Pump Link follower read a slot the firmware never wrote (the value was live at
# 0x20008242 all along). §5.4c / its design note (not published).
# The mechanism now lives in `register.py`; re-exported here so existing references
# (`followchannel.status_slot_addr`, `followchannel.STATUS_SLOT_BASE`, ...) still resolve.
from .register import (STATUS_SLOT_BASE, STATUS_SLOT_BASE_HIGH,  # re-exported: the register
                       STATUS_SLOT_INDEX_SPLIT, status_slot_addr)  # mechanism now lives in register.py

# Firmware "no data" sentinel. The LCD firmware (FUN_0021BE50) initialises every status slot to
# 0xFFFF at boot, and FUN_0021C374 reverts a slot to 0xFFFF when its data goes stale (MCF-FORMAT
# §5.4a). Real DC_DIMMER_STATUS_3 values are small (payload[6] 0x04/0x44 on, 0x40 off, zero-extended
# to a halfword), so 0xFFFF unambiguously means "no valid frame". The follower must not act on it:
# reading the low byte of 0xFFFF as "on" is what fired the pump at boot on 2026-08-30. The routine
# stores the RAW slot halfword in its latch, so at boot the latch self-primes to 0xFFFF (the
# firmware's own default) and the first real frame is a silent baseline -- no invented state needed.
STALE_SENTINEL = 0xFFFF

# SRAM window the config/colour registers live in (theme.SRAM_LOW/HIGH); used to sanity-check
# the recovered gate register.
SRAM_LOW = 0x20030000
SRAM_HIGH = 0x20040000

# Blob layout offsets from code_load_addr.
ON_OFF = 0x00
OFF_OFF = 0x0C
ENTRY_OFF = 0x18


class FollowChannelError(Exception):
    """A derivation failed or a hardcoded stock byte did not match: refuse rather than corrupt."""


@dataclass
class Condition:
    """A register test: `(slot & mask) == value`. Subsumes the watch's (byte & 0x1C) == 0x04
    and a gate's == 1."""
    register: "register.Register"
    mask: int = 0xFFFF
    value: int = 1


def _check_gate_test(mask: int, value: int, what: str = "gate_on_value", *,
                     allow_wide_value: bool = False) -> None:
    """Refuse a gate test `(slot & mask) == value` that is malformed or unsatisfiable.

    Shared by BOTH gate shapes -- the single `gate_mask`/`gate_on_value` slot gate (follower and
    display bridge) and every `Condition` in `plan.gates` -- so the two cannot drift: a rule that
    protects one protects the other. Fix round 1 finding 1: the `Condition` path previously kept
    only u16-range guards, and accepted `mask=0xFF00, value=1` (a permanently dead gate -- this
    task's own defect class), `mask=0, value=0` (always true) and `mask=0xFF00, value=0xFF00`
    (satisfied by the unset sentinel, so the follower would TRANSMIT at boot, before the G12's
    first sweep -- a live bus write, not merely a dead feature).

    Three conditions are refused outright rather than authored:
      * a zero mask -- `(slot & 0) == 0` is always true and `(slot & 0) == nonzero` never is;
        neither is a gate,
      * value bits outside the mask -- the test could NEVER hold (a permanently dead gate is
        exactly the defect Task 13 exists to fix), and
      * a test the unset sentinel satisfies -- a receive slot reads 0xFFFF until the G12's
        first sweep, so such a gate would read "on" at boot.

    `allow_wide_value` distinguishes the two callers, and ONLY that. On the single slot gate it
    is False: `mask == 0xFFFF` there is the byte-pinned `cmp r0,#imm8` shape, so a value that
    does not fit an 8-bit immediate is refused rather than silently promoted to the wider
    `movw`+`cmp` encoding -- the caller must say `gate_mask` and mean it. The `Condition` path
    passes True, because `mask=0xFFFF, value>0xFF` has always been legal there (it is the
    `movw`+`cmp_reg` branch `follow_register` exercises) and refusing it would break a shipped
    capability. Everything else is checked identically for both.
    """
    if not (1 <= mask <= 0xFFFF):
        raise FollowChannelError(f"gate_mask must be a nonzero u16 (1..0xFFFF), got {mask:#x}")
    if not (0 <= value <= 0xFFFF):
        raise FollowChannelError(f"{what} {value:#x} is out of u16 range")
    if mask == 0xFFFF and not allow_wide_value and value > 0xFF:
        raise FollowChannelError(
            f"{what} {value:#x} must fit cmp_imm8 (0..0xFF) on the unmasked path; pass a "
            "gate_mask to compare a wider value")
    if value & ~mask:
        raise FollowChannelError(
            f"{what} {value:#x} has bits outside the mask {mask:#x}; "
            "(slot & mask) == value could never hold")
    if (STALE_SENTINEL & mask) == value:
        raise FollowChannelError(
            f"(slot & {mask:#x}) == {value:#x} is satisfied by the unset sentinel "
            f"{STALE_SENTINEL:#x}; the gate would read 'on' before the G12's first sweep")


def _emit_slot_test(emit, imm, mask: int, value: int, scratch: int) -> None:
    """Emit the flag-setting half of `(r0 & mask) == value`; the caller emits the branch.

    `scratch` must be a DEAD low register at the emission point (the mask/value literals are
    materialised into it) -- r1 in the follower's bridge block, where r2 holds the display-cell
    pointer; r2 in the follower's gate section and in the display bridge, where r1 is live or
    about to be written. r4-r7 are never candidates.

    With `mask == 0xFFFF` and `value <= 0xFF` this emits exactly the one `cmp r0, #value` the
    pre-mask code emitted, so an unmasked plan is byte-identical. `0x0100` does not fit the 8-bit
    cmp immediate, so the masked form uses `movw` + `cmp r0, rS` -- the same encoding the
    multi-gate branch already uses (and which `thumb.assert_disassembles_to` re-reads)."""
    if mask != 0xFFFF:
        if mask <= 0xFF:
            emit(2, lambda h, s, m=mask, r=scratch: (thumb.movs_imm(r, m),
                                                     f"movs r{r}, #{imm(m)}"))
        else:
            emit(4, lambda h, s, m=mask, r=scratch: (thumb.movw(r, m), f"movw r{r}, #{m:#x}"))
        emit(2, lambda h, s, r=scratch: (thumb.ands_reg(0, r), f"ands r0, r{r}"))
    if value <= 0xFF:
        emit(2, lambda h, s, v=value: (thumb.cmp_imm8(0, v), f"cmp r0, #{imm(v)}"))
    else:
        emit(4, lambda h, s, v=value, r=scratch: (thumb.movw(r, v), f"movw r{r}, #{v:#x}"))
        emit(2, lambda h, s, r=scratch: (thumb.cmp_reg(0, r), f"cmp r0, r{r}"))


@dataclass
class FollowPlan:
    watch_addr: int       # &(watched output's status slot halfword), read each tick
    on_mask: int          # operating-status field bits; the light is on iff (slot & on_mask) == on_value
    on_value: int         # the masked value that means "on" (matches the firmware's own decode)
    latch_addr: int       # &(edge latch halfword): the last RAW slot value we saw (0xFFFF = unseen)
    gate_addr: int        # &(gate option's config register, or a DGN_CONFIG receive slot)
    gate_is_slot: bool    # False: gate_addr is a config register, byte, nonzero = on (legacy path)
                           # True: gate_addr is a DGN_CONFIG receive slot, halfword, ==gate_on_value
    gate_on_value: int    # the value the MASKED gate slot must equal when gate_is_slot (selector
                           # semantics: unset = 0xFFFF, on = 1, off = 0); with gate_mask it is the
                           # masked value, e.g. 0x0100 for "flag byte == 1" on the shared 0xEE word.
                           # Unused (default 1) on the register path
    on_template: bytes    # 12-byte CAN frame, wire order, that turns the target channel on
    off_template: bytes   # 12-byte CAN frame that turns it off
    hook_at: int          # GuiCode-section offset of the detoured `bl`
    hook_old: bytes       # its current 4 bytes (stock, or a bl into an earlier appended routine)
    tail_target: int = TAIL_TARGET   # what our routine tail-branches to: the paint fn, or the
                                     # previous appended routine's entry (`hook_state`)
    display_addr: int | None = None    # &(spare display cell): strb'd the gate's on/off flag byte
    display_slot: int | None = None    # its +0x44BC8 cell index (for a later wtype-6 widget)
    variant_group: int | None = None   # group id `build` hands to the setVariant bridge each tick
    gate_mask: int = 0xFFFF   # the mask the gate slot is ANDed with before the == gate_on_value
                              # test: `(slot & gate_mask) == gate_on_value`. 0xFFFF (the default)
                              # is the whole-halfword test the proven builds emit, byte for byte.
                              # A narrower mask is Task 13's opt-in for a selector carrying two
                              # fields in one word (0xEE: minutes low byte | link flag high byte),
                              # where the whole word is never 1 and an unmasked gate is dead.
    gates: list = field(default_factory=list)   # Condition list, all-must-hold (plan_from_registers)
    use_gate_list: bool = False   # True marks a register/gates-path plan (set by plan_from_registers):
                                  # build() routes its gate section through the multi-gate loop, which
                                  # over an EMPTY list emits no gate test (the correct "always transmit
                                  # on edge"). resolve() leaves it False, so the legacy single-gate
                                  # branch stays byte-identical. `gates` emptiness must NOT be the
                                  # discriminator -- an empty-gates register plan and resolve()'s
                                  # legacy single gate both have gates == [] but need opposite branches.


def _template(instance: int, level: int, command: int) -> bytes:
    """A 12-byte CAN-frame template in the pool's wire order.

    Byte order verified this session against real pool records: `00 db fe 19 | inst ff level cmd
    ff 00 ff ff` (the Finding-7 pool record `00dbfe1912ff0003ff00ffff` is byte-identical in shape,
    differing only in the instance byte).
    """
    for name, v in (("instance", instance), ("level", level), ("command", command)):
        if not 0 <= v <= 0xFF:
            raise FollowChannelError(f"{name} out of byte range: {v}")
    return bytes([0x00, 0xDB, 0xFE, 0x19, instance, 0xFF, level, command, 0xFF, 0x00, 0xFF, 0xFF])


def _guicode_references(section: bytes, base: int, addr: int) -> bool:
    """True if `addr` is referenced by GuiCode `section` (mapped at `base`).

    Two forms are checked, matching the standing scan rule:
      * a raw little-endian u32 occurrence of `addr` anywhere in the section (a literal-pool word,
        or a base+offset table entry that happens to be a bare pointer), and
      * a T1 `ldr rX,[pc,#imm8]` (halfword 0x4800..0x4FFF) whose PC-relative target word equals
        `addr`. Both 2-byte alignments are scanned for the instruction halfword and for the raw
        u32, because a literal pool need not be 4-aligned within the section slice.

    13.6 contains no `movt`, so a 32-bit constant can only reach code as a pool word (T1 or T2
    `ldr [pc]`; the raw-u32 scan covers both) — this scan is complete for DIRECT references.
    *(Corrected 2026-09-04: this docstring used to say "no movw/movt (0 occurrences)". 13.6 has
    hundreds of `movw`, used as 16-bit OFFSETS off a base register — `movw r3,#0x6bb4; ldr r3,[r2,r3]`
    with r2 = the render state at 0x200301B0. That base+offset form is what this scan cannot see;
    `sramreach.reach` traces it per base, and GUICODE Finding 66 bounds the register-indexed
    residual. Every cell the shipping builds allocate through this scan was re-checked there.)*
    """
    want = struct.pack("<I", addr)
    # Raw little-endian u32 occurrence (any byte alignment).
    if section.find(want) != -1:
        return True
    # T1 ldr-literal loads, scanning both halfword alignments.
    n = len(section)
    for pos in range(0, n - 1):
        instr = section[pos] | (section[pos + 1] << 8)
        if 0x4800 <= instr <= 0x4FFF:
            imm8 = instr & 0xFF
            instr_addr = base + pos
            target = ((instr_addr + 4) & ~3) + imm8 * 4
            off = target - base
            if 0 <= off + 4 <= n:
                word = struct.unpack_from("<I", section, off)[0]
                if word == addr:
                    return True
    return False


def _gate_register(data: bytes, option_name: str) -> int:
    """The config register a named OPTIONS row writes: name -> selector -> cfg offset -> register.

    Each hop is one of the project's already-proven joins (`guidata.find_option_bindings`,
    `config.selectors_by_offset`, `config.config_offsets`). Any ambiguity is refused, never
    guessed.
    """
    matches = [b for b in guidata.find_option_bindings(data) if b.name == option_name]
    if not matches:
        raise FollowChannelError(f"OPTIONS row {option_name!r} not found (or its selector "
                                 "was unrecoverable)")
    if len({b.selector for b in matches}) != 1:
        raise FollowChannelError(f"OPTIONS row {option_name!r} maps to multiple selectors")
    selector = matches[0].selector

    offsets = [off for off, sels in config.selectors_by_offset(data).items() if selector in sels]
    if len(offsets) != 1:
        raise FollowChannelError(
            f"selector {selector:#x} for {option_name!r} does not resolve to one config offset "
            f"(got {[hex(o) for o in offsets]})")
    register = config.config_offsets(data).get(offsets[0])
    if not register or not (SRAM_LOW <= register < SRAM_HIGH):
        raise FollowChannelError(
            f"config offset {offsets[0]:#x} for {option_name!r} has no plausible SRAM register "
            f"(got {register!r})")
    return register


def _gate_slot(data: bytes, selector: int) -> int:
    """The DGN_CONFIG receive slot for `selector`, at its index-split address
    (`status_slot_addr`, which handles var index > 255 -- the PC.44 bug).

    Scans `guidata.variable_table` for the (exactly one) DGN_CONFIG entry whose value is
    `selector`; refuses if there is not exactly one, same discipline as `_gate_register`.
    """
    matches = [v for v in guidata.variable_table(data)
               if v.dgn_low == guidata.DGN_CONFIG and v.value == selector]
    if len(matches) != 1:
        raise FollowChannelError(
            f"selector {selector:#x} maps to {len(matches)} DGN_CONFIG variable-table entries, "
            "not exactly one")
    return status_slot_addr(matches[0].index)


def _scan_latch_cell(data: bytes) -> int:
    """The edge-latch address: the first spare +0x44BC8 colour cell whose SRAM address is *also*
    unreferenced by GuiCode, read directly from the cell table (ruling 1: statusbind.index_cell is
    a writer; there is no reader helper). "Spare" means unused by any WIDGET, which is NOT the same
    as unused by CODE: stock GuiCode keeps live working memory in some of those cells (e.g. cell 203
    / 0x2003A2F8 is loaded by FUN_00247BF8 at 0x24a600, the very per-tick function we hook), so a
    widget-spare cell can still be firmware-live. Latching into such a cell would corrupt it every
    tick, so we skip any spare whose address GuiCode references (pool literal or ldr-literal).

    The latch stores the raw slot HALFWORD, so its cell must be 2-aligned and BOTH of its bytes
    must be GuiCode-unreferenced (a strh/ldrh touches addr and addr+1). Returns the first spare
    cell that qualifies, or raises `FollowChannelError`.

    Factored out of `resolve()` so `plan_from_registers()` shares the exact same discipline; this
    is a pure function of `data` and reproduces `resolve()`'s prior inline scan byte-for-byte.
    """
    gs, _ = guidata.guidata_extent(data)
    spares = statusbind.spare_cell_indices(data)
    if not spares:
        raise FollowChannelError("no spare +0x44BC8 cell to use as the edge latch")
    sec = theme.guicode_section(data)
    section = data[sec.offset:sec.offset + sec.length]
    base = theme.guicode_base(sec)

    def _safe(addr: int) -> bool:
        return (SRAM_LOW <= addr < SRAM_HIGH) and not _guicode_references(section, base, addr)

    for idx in spares:
        cand = struct.unpack_from("<I", data, gs + celltables.cell_table_off(data) + idx * 4)[0]
        if cand % 2 != 0:
            continue                       # strh/ldrh need a 2-aligned address
        if _safe(cand) and _safe(cand + 1):
            return cand
    raise FollowChannelError(
        "no 2-aligned spare +0x44BC8 cell with both halfword bytes in SRAM and GuiCode-"
        "unreferenced; refusing to latch into firmware-live working memory")


def hook_state(data: bytes) -> tuple[bytes, int]:
    """`(hook_bytes, tail_target)` for the detour site -- the 4 bytes at GuiCode+HOOK_OFF and
    the address the routine we append must tail-branch to.

    Two states are accepted, everything else is refused (cross-coach safety):
      * the stock `bl 0x00247BF8` (HOOK_STOCK) -> tail_target is the paint fn itself;
      * a `bl` into the APPENDED region [STOCK_APPEND_POINT, current append point) -> a routine
        an earlier op appended. The new routine tail-branches to THAT entry and the hook is
        retargeted to the new one, so the per-tick chain becomes hook -> new -> old -> paint fn.
        Every routine here saves/restores r0-r3+lr, never touches r4-r7 and ends in a
        tail-branch, so each link is transparent to the next (the AAPCS argument that licensed
        the single detour licenses the chain).

    Factored out of `resolve()` so `plan_from_registers()` and `resolve_display_bridge()`
    share the exact same discipline.
    """
    sec = theme.guicode_section(data)
    section = data[sec.offset:sec.offset + sec.length]
    hook_old = bytes(section[HOOK_OFF:HOOK_OFF + 4])
    if hook_old == HOOK_STOCK:
        return hook_old, TAIL_TARGET
    append_point = theme.GUICODE_BASE_DEFAULT + sec.length - 4
    insns = thumb.disassemble(hook_old, theme.GUICODE_BASE_DEFAULT + HOOK_OFF)
    text = insns[0][1] if insns else ""
    if text.startswith("bl #"):
        target = int(text[4:], 16)
        if STOCK_APPEND_POINT <= target < append_point and target % 2 == 0:
            return hook_old, target
    raise FollowChannelError(
        f"hook at GuiCode+{HOOK_OFF:#x} is {hook_old.hex()} ({text or 'undecodable'}), neither "
        f"the stock {HOOK_STOCK.hex()} nor a bl into the appended region "
        f"[{STOCK_APPEND_POINT:#x},{append_point:#x}); refusing to detour")


def chain_from_hook(data: bytes, limit: int = 16) -> list[int]:
    """The per-tick call chain the hook reaches, as runtime addresses: each appended routine's
    entry in call order, ending with TAIL_TARGET (the displaced paint fn). On stock this is just
    `[TAIL_TARGET]`. Each link is found by disassembling linearly from its entry to its first
    `b.w` -- the only wide unconditional branch a routine here emits is its tail-branch, and the
    literal pool sits after it. Refuses a link that leaves the appended region or a chain longer
    than `limit` (a loop)."""
    sec = theme.guicode_section(data)
    section = data[sec.offset:sec.offset + sec.length]
    base = theme.GUICODE_BASE_DEFAULT
    append_point = base + sec.length - 4
    _, target = hook_state(data)
    chain = []
    while target != TAIL_TARGET:
        if not (STOCK_APPEND_POINT <= target < append_point):
            raise FollowChannelError(f"chain link {target:#x} is outside the appended region")
        if len(chain) >= limit:
            raise FollowChannelError(f"per-tick chain exceeds {limit} links: {chain}")
        chain.append(target)
        code = section[target - base:append_point - base]
        tail = next((t for _, t in thumb.disassemble(code, target) if t.startswith("b.w #")), None)
        if tail is None:
            raise FollowChannelError(f"routine at {target:#x} has no b.w tail-branch")
        target = int(tail[5:], 16)
    chain.append(TAIL_TARGET)
    return chain


def _check_hook(data: bytes) -> bytes:
    """The hook's current 4 bytes, refused unless `hook_state` accepts them."""
    return hook_state(data)[0]


def resolve(data: bytes, watch_channel: int, target_channel: int, on_level: int,
            gate_option: str | None = None, on_mask: int = 0x1C, on_value: int = 0x04,
            gate_slot_selector: int | None = None, gate_on_value: int = 1,
            gate_mask: int = 0xFFFF,
            variant_group: int | None = None, alloc_display: bool = False) -> FollowPlan:
    """Derive every address the follower needs from `data`, or raise `FollowChannelError`.

    `data` is the full MCF container. `watch_channel`/`target_channel` are G12 output instances;
    `on_level` is the level byte the on-frame carries. Exactly one of `gate_option` (an OPTIONS
    caption whose config REGISTER gates transmission, byte-nonzero semantics) or
    `gate_slot_selector` (a DGN_CONFIG selector like 0xEE whose receive SLOT gates transmission,
    `(halfword & gate_mask) == gate_on_value` semantics) must be given.

    `gate_mask` defaults to 0xFFFF -- the whole-halfword test the hardware-verified builds emit,
    byte for byte. Pass a narrower mask when the selector's word carries more than one field:
    0xEE now packs the auto-off minutes in its low byte and the Pump Link flag in its high byte
    (2026-09-03 pump-auto-off design §2.2/§3.2), so the flag gate is `gate_mask=0xFF00,
    gate_on_value=0x0100` -- the whole word is never 1, and an unmasked gate on it is dead.

    `on_mask`/`on_value` decode the watched status byte the way the firmware does: the light is "on"
    when `(slot & on_mask) == on_value`. The receive filter writes the DC_DIMMER_STATUS_3 byte the
    variable-table entry names (for ch25 that is `payload[6]`, §5.4a mode 1, n=6), and the firmware's
    own indicator block for ch25 (`FUN_00247BF8` @0x248afc) decodes it as `ubfx r2,#2,#3` -- a 3-bit
    operating-status field (bits 2..4) -- and paints the standard ON colour (0x2003a1a0) only when
    that field == 1 (a distinct field == 4 gets a different colour, and everything else the off
    colour). Field == 1 in the raw byte is `(byte & 0x1C) == 0x04`, so those are the defaults. This
    matches the firmware exactly, rather than the earlier single-bit `& 0x04` guess which happened to
    agree only for the field 0/1 values ch25 emits in practice. `on_mask` must be a nonzero byte and
    `on_value` must be a subset of it.
    """
    if (gate_option is None) == (gate_slot_selector is None):
        raise FollowChannelError(
            "exactly one of gate_option / gate_slot_selector must be given "
            f"(got gate_option={gate_option!r}, gate_slot_selector={gate_slot_selector!r})")
    if not (1 <= on_mask <= 0xFF):
        raise FollowChannelError(f"on_mask must be a nonzero byte (1..0xFF), got {on_mask:#x}")
    if not (0 <= on_value <= 0xFF) or (on_value & ~on_mask):
        raise FollowChannelError(
            f"on_value {on_value:#x} must be a byte within on_mask {on_mask:#x}")
    if gate_slot_selector is not None:
        _check_gate_test(gate_mask, gate_on_value)
    elif gate_mask != 0xFFFF:
        raise FollowChannelError(
            "gate_mask only applies to a slot gate (gate_slot_selector); the register path "
            "reads a byte and tests it nonzero")
    if variant_group is not None and not (0 <= variant_group <= 255):
        raise FollowChannelError(
            f"variant_group {variant_group:#x} must fit a single `movs r0,#imm8` (0..255); "
            "a larger group id would need movw, which 13.6 never uses")
    # Watched status: instance -> receive-filter status var -> the slot the LCD firmware writes when
    # that channel's DC_DIMMER_STATUS_3 arrives (STATUS_SLOT_BASE + var*2, §5.4c). We verify the
    # variable-table entry really filters this channel's DC_DIMMER_STATUS_3 before trusting the slot.
    var = statusjoin.instance_status_var(watch_channel)
    table = guidata.variable_table(data)
    if not (0 <= var < len(table)):
        raise FollowChannelError(
            f"status var {var} for channel {watch_channel} is out of the variable table")
    entry = table[var]
    if entry.dgn_low != guidata.DGN_DC_DIMMER_STATUS_3:
        raise FollowChannelError(
            f"status var {var} subscribes to DGN {entry.dgn_low:#06x}, not DC_DIMMER_STATUS_3 "
            f"({guidata.DGN_DC_DIMMER_STATUS_3:#06x}); cannot watch channel {watch_channel}")
    if entry.channel != watch_channel:
        raise FollowChannelError(
            f"status var {var} filters instance {entry.channel}, not the requested watch channel "
            f"{watch_channel}")
    watch_addr = status_slot_addr(var)

    # Edge latch: factored into _scan_latch_cell, shared with plan_from_registers.
    latch_addr = _scan_latch_cell(data)

    # Display cell: a SECOND spare cell (same discipline as the latch -- 2-aligned, both bytes
    # GuiCode-unreferenced), distinct from the latch. A later wtype-6 checkbox widget reads this
    # cell; `build`'s bridge block writes the gate's live on/off flag (0/1) into it every tick.
    display_addr = None
    display_slot = None
    if alloc_display:
        gs, _ = guidata.guidata_extent(data)
        spares = statusbind.spare_cell_indices(data)
        sec = theme.guicode_section(data)
        section = data[sec.offset:sec.offset + sec.length]
        base = theme.guicode_base(sec)

        def _safe(addr: int) -> bool:
            return (SRAM_LOW <= addr < SRAM_HIGH) and not _guicode_references(section, base, addr)

        for idx in spares:
            cand = struct.unpack_from("<I", data, gs + celltables.cell_table_off(data) + idx * 4)[0]
            if cand == latch_addr or cand % 2 != 0:
                continue
            if _safe(cand) and _safe(cand + 1):
                display_addr = cand
                display_slot = idx
                break
        if display_addr is None:
            raise FollowChannelError(
                "no second 2-aligned spare +0x44BC8 cell (distinct from the latch) with both "
                "halfword bytes in SRAM and GuiCode-unreferenced; refusing to display into "
                "firmware-live working memory")

    # Gate address: either the OPTIONS row's config register, or a DGN_CONFIG receive slot.
    if gate_slot_selector is not None:
        gate_addr = _gate_slot(data, gate_slot_selector)
        gate_is_slot = True
    else:
        gate_addr = _gate_register(data, gate_option)
        gate_is_slot = False

    # The stock hook: factored into _check_hook, shared with plan_from_registers.
    hook_old, tail_target = hook_state(data)

    return FollowPlan(
        watch_addr=watch_addr,
        on_mask=on_mask,
        on_value=on_value,
        latch_addr=latch_addr,
        gate_addr=gate_addr,
        gate_is_slot=gate_is_slot,
        gate_on_value=gate_on_value,
        gate_mask=gate_mask,
        on_template=_template(target_channel, on_level, 1),
        off_template=_template(target_channel, 0, 3),
        hook_at=HOOK_OFF,
        hook_old=hook_old,
        tail_target=tail_target,
        display_addr=display_addr,
        display_slot=display_slot,
        variant_group=variant_group,
    )


def plan_from_registers(data: bytes, *, watch: Condition, target_channel: int,
                        on_level: int = 200, gates: list) -> FollowPlan:
    """Build a FollowPlan from already-resolved registers (Task 2). No display cell, no
    variant group, no bridge block -- control logic only. `watch` is the edge source (tested
    (slot & mask) == value each tick); `gates` are all-must-hold conditions on the transmit,
    each validated by `_check_gate_test` -- a zero mask, a value outside its mask, or a test the
    0xFFFF unset sentinel satisfies is refused here rather than authored.

    The routine reads watch/gate slots as generic halfwords, so watching a different existing
    register is the verified v9 routine with different resolved inputs, not new machine code --
    but the first use against a NEW watch register still warrants a coach check (spec risk c)."""
    if not (1 <= watch.mask <= 0xFF):
        raise FollowChannelError(f"watch mask must be a nonzero byte (1..0xFF), got {watch.mask:#x}")
    if watch.value & ~watch.mask:
        raise FollowChannelError(
            f"watch value {watch.value:#x} must be within mask {watch.mask:#x}")
    # Fix round 1 finding 1: refuse a nonsense gate at PLAN time, not only in `build` -- the
    # same `_check_gate_test` the single slot gate uses (see its docstring for the three cases
    # this catches, one of which would transmit on the bus at boot).
    for gi, cond in enumerate(gates):
        _check_gate_test(cond.mask, cond.value, f"gate {gi} value", allow_wide_value=True)
    latch_addr = _scan_latch_cell(data)          # factored from resolve(), same discipline
    hook_old, tail_target = hook_state(data)     # factored: stock `bl`, or chain onto a prior routine
    return FollowPlan(
        watch_addr=watch.register.slot_addr, on_mask=watch.mask & 0xFF, on_value=watch.value,
        latch_addr=latch_addr, gate_addr=0, gate_is_slot=False, gate_on_value=1,
        on_template=_template(target_channel, on_level, 1),
        off_template=_template(target_channel, 0, 3),
        hook_at=HOOK_OFF, hook_old=hook_old, tail_target=tail_target, gates=list(gates),
        use_gate_list=True)


def build(plan: FollowPlan, code_load_addr: int) -> tuple[bytes, list]:
    """Author the appended blob (templates + routine + literal pool) and verify it.

    Returns `(appended_bytes, disassembly)`. `appended_bytes` is 4-aligned. `disassembly` is the
    routine's `[(address, text), ...]` after `thumb.assert_disassembles_to` has confirmed every
    instruction decodes to exactly the text this builder claimed.
    """
    if len(plan.on_template) != 12 or len(plan.off_template) != 12:
        raise FollowChannelError("templates must be 12 bytes")

    # The bridge block (this task) is emitted only when both a display cell and a variant group
    # were resolved. It reads the gate as a live halfword, so it only makes sense on the slot path
    # -- refuse rather than guess if asked for on the legacy register path.
    has_bridge = plan.display_addr is not None and plan.variant_group is not None
    if has_bridge and not plan.gate_is_slot:
        raise FollowChannelError(
            "display_addr/variant_group require gate_is_slot (the bridge reads the gate as a "
            "live halfword slot, which the register path does not have)")

    # capstone prints small immediates in decimal but >= 10 in hex; match it so the round-trip
    # check (assert_disassembles_to) passes for any immediate this routine emits.
    def imm(v: int) -> str:
        return f"{v:#x}" if v >= 10 else str(v)

    # Literal pool: the u32 absolute addresses the routine's ldr-literals load. Order is the
    # pool's own; each entry's absolute address is computed once the code length is known.
    # The LEGACY path (use_gate_list False) keeps the single "gate" pool entry (proven, byte-
    # stable). The register/gates path swaps it for one "gateI" entry per condition, each mapping
    # to that gate's register slot address -- and NO "gate" entry, so an empty-gates register plan
    # never authors a pool literal for the bogus gate_addr==0. Only one shape is ever present.
    if plan.use_gate_list:
        pool_order = ["watch", "latch"]
        pool_val = {"watch": plan.watch_addr, "latch": plan.latch_addr}
        for gi, cond in enumerate(plan.gates):
            name = f"gate{gi}"
            pool_order.append(name)
            pool_val[name] = cond.register.slot_addr
    else:
        pool_order = ["watch", "latch", "gate"]
        pool_val = {
            "watch": plan.watch_addr,
            "latch": plan.latch_addr,
            "gate": plan.gate_addr,
        }
    if has_bridge:
        pool_order += ["display", "setter"]
        pool_val["display"] = plan.display_addr
        pool_val["setter"] = VARIANT_SETTER
    pool_order += ["sender", "on", "off"]
    pool_val["sender"] = SENDER_PTR_ADDR
    pool_val["on"] = code_load_addr + ON_OFF
    pool_val["off"] = code_load_addr + OFF_OFF

    # The routine as an ordered stream. Each instruction is a thunk (here, sym) -> (bytes, text);
    # `sym(name)` resolves a label or a pool entry to its absolute address. Sizes are fixed
    # regardless of the resolved addresses, so a size pass fixes every label offset first.
    stream: list = []

    def emit(size: int, fn) -> None:
        stream.append(("ins", size, fn))

    def label(name: str) -> None:
        stream.append(("lbl", name))

    def ldrlit_text(rt: int, here: int, target: int) -> str:
        off = target - ((here + 4) & ~3)
        return f"ldr r{rt}, [pc, #{off:#x}]" if off else f"ldr r{rt}, [pc]"

    # push {r0-r3, lr}
    emit(2, lambda h, s: (thumb.push_reglist([0, 1, 2, 3, 14]), "push {r0, r1, r2, r3, lr}"))

    # The bridge block (heart of this task): runs UNCONDITIONALLY every tick, before the edge
    # logic below, reproducing the stock Low Volts bridge (0x0024A176-0x0024A1A6) for our new
    # group/cell. r0-r3 are all freshly reloaded by the edge logic right after this block (its
    # first instructions load "watch" into r0 and "latch" into r2), so clobbering them here is
    # safe; r4-r7 are never touched (they are not in the push list, and the tail-called
    # FUN_00247BF8 must see them unchanged, per AAPCS, across the whole detoured `bl`).
    if has_bridge:
        _check_gate_test(plan.gate_mask, plan.gate_on_value)
        # flag = ((gate slot & gate_mask) == gate_on_value); write it to the display cell (a byte
        # 0/1 a later wtype-6 checkbox widget reads), then setVariant(group, flag ? 0 : 1) -- the
        # inversion rule proven from the stock block: ON means the *next* tap should write 0.
        # The mask/value scratch is r1: r2 already holds the display-cell pointer both arms
        # strb through, and r1 is dead here (each arm writes it immediately below). Unmasked,
        # `_emit_slot_test` collapses to the one `cmp r0, #gate_on_value` this always emitted.
        emit(2, lambda h, s: (thumb.ldr_literal(0, h, s("gate")), ldrlit_text(0, h, s("gate"))))
        emit(2, lambda h, s: (thumb.ldrh_imm(0, 0, 0), "ldrh r0, [r0]"))
        emit(2, lambda h, s: (thumb.ldr_literal(2, h, s("display")), ldrlit_text(2, h, s("display"))))
        _emit_slot_test(emit, imm, plan.gate_mask, plan.gate_on_value, scratch=1)
        emit(2, lambda h, s: (thumb.bcc_n(h, s("boff"), "ne"), f"bne #{s('boff'):#x}"))
        # on: flag=1, so the checkbox shows checked and the next tap must write variant 0.
        emit(2, lambda h, s: (thumb.movs_imm(1, 1), "movs r1, #1"))
        emit(4, lambda h, s: (thumb.strb_imm12(1, 2, 0), "strb.w r1, [r2]"))
        emit(2, lambda h, s: (thumb.movs_imm(1, 0), "movs r1, #0"))
        emit(2, lambda h, s: (thumb.b_n(h, s("bset")), f"b #{s('bset'):#x}"))
        label("boff")
        # off: flag=0, so the checkbox shows unchecked and the next tap must write variant 1.
        emit(2, lambda h, s: (thumb.movs_imm(1, 0), "movs r1, #0"))
        emit(4, lambda h, s: (thumb.strb_imm12(1, 2, 0), "strb.w r1, [r2]"))
        emit(2, lambda h, s: (thumb.movs_imm(1, 1), "movs r1, #1"))
        label("bset")
        emit(2, lambda h, s: (thumb.ldr_literal(2, h, s("setter")), ldrlit_text(2, h, s("setter"))))
        emit(2, lambda h, s: (thumb.ldr_imm(2, 2, 0), "ldr r2, [r2]"))
        emit(2, lambda h, s: (thumb.movs_imm(0, plan.variant_group),
                              f"movs r0, #{imm(plan.variant_group)}"))
        emit(2, lambda h, s: (thumb.blx_reg(2), "blx r2"))

    # r1 = current RAW slot halfword; r3 = the last raw slot we saw (latch). We store the raw slot,
    # not a normalised bool, so the latch self-primes to the firmware's 0xFFFF "no data" default at
    # boot -- no invented boot flag. If the raw slot is unchanged, there is nothing to do.
    emit(2, lambda h, s: (thumb.ldr_literal(0, h, s("watch")), ldrlit_text(0, h, s("watch"))))
    emit(2, lambda h, s: (thumb.ldrh_imm(1, 0, 0), "ldrh r1, [r0]"))
    emit(2, lambda h, s: (thumb.ldr_literal(2, h, s("latch")), ldrlit_text(2, h, s("latch"))))
    emit(2, lambda h, s: (thumb.ldrh_imm(3, 2, 0), "ldrh r3, [r2]"))
    emit(2, lambda h, s: (thumb.cmp_reg(1, 3), "cmp r1, r3"))
    emit(2, lambda h, s: (thumb.bcc_n(h, s("done"), "eq"), f"beq #{s('done'):#x}"))
    # remember the new raw slot (0x100C: strh rT,[rN,#0] is the T1 form)
    emit(2, lambda h, s: (thumb.strh_imm(1, 2, 0), "strh r1, [r2]"))
    # Do nothing while the CURRENT slot is the firmware "no data" sentinel 0xFFFF (boot / stale) --
    # there is no real state to act on. We do NOT special-case the *previous* slot being 0xFFFF: the
    # firmware decodes 0xFFFF as OFF (its operating-status field is 7, not 1), so the on-state test
    # below treats a 0xFFFF baseline as a genuine "off". That makes the first real ON after boot a
    # true off->on transition that fires -- fixing the "took a second toggle" the coach showed on v6,
    # while the current-sentinel guard keeps boot itself silent.
    emit(4, lambda h, s: (thumb.movw(0, STALE_SENTINEL), f"movw r0, #{STALE_SENTINEL:#x}"))
    emit(2, lambda h, s: (thumb.cmp_reg(1, 0), "cmp r1, r0"))
    emit(2, lambda h, s: (thumb.bcc_n(h, s("done"), "eq"), f"beq #{s('done'):#x}"))
    # Decode each raw slot to a 0/1 on-state the firmware's own way: the light is on iff the
    # operating-status field (slot & on_mask) equals on_value. r1 <- on(current), r3 <- on(last);
    # transmit only when they differ (a genuine on<->off transition, not a within-state wobble like
    # 0x04<->0x44 or a different-but-still-off field value).
    emit(2, lambda h, s: (thumb.movs_imm(0, plan.on_mask), f"movs r0, #{imm(plan.on_mask)}"))
    emit(2, lambda h, s: (thumb.ands_reg(1, 0), "ands r1, r0"))
    emit(2, lambda h, s: (thumb.cmp_imm8(1, plan.on_value), f"cmp r1, #{imm(plan.on_value)}"))
    emit(2, lambda h, s: (thumb.bcc_n(h, s("con"), "ne"), f"bne #{s('con'):#x}"))
    emit(2, lambda h, s: (thumb.movs_imm(1, 1), "movs r1, #1"))
    emit(2, lambda h, s: (thumb.b_n(h, s("cdn")), f"b #{s('cdn'):#x}"))
    label("con")
    emit(2, lambda h, s: (thumb.movs_imm(1, 0), "movs r1, #0"))
    label("cdn")
    emit(2, lambda h, s: (thumb.ands_reg(3, 0), "ands r3, r0"))
    emit(2, lambda h, s: (thumb.cmp_imm8(3, plan.on_value), f"cmp r3, #{imm(plan.on_value)}"))
    emit(2, lambda h, s: (thumb.bcc_n(h, s("lon"), "ne"), f"bne #{s('lon'):#x}"))
    emit(2, lambda h, s: (thumb.movs_imm(3, 1), "movs r3, #1"))
    emit(2, lambda h, s: (thumb.b_n(h, s("ldn")), f"b #{s('ldn'):#x}"))
    label("lon")
    emit(2, lambda h, s: (thumb.movs_imm(3, 0), "movs r3, #0"))
    label("ldn")
    emit(2, lambda h, s: (thumb.cmp_reg(1, 3), "cmp r1, r3"))
    emit(2, lambda h, s: (thumb.bcc_n(h, s("done"), "eq"), f"beq #{s('done'):#x}"))
    # gate: transmit only while the gate condition holds.
    #  - register path (gate_is_slot False): the option register is a byte, nonzero = on.
    #  - slot path (gate_is_slot True), no bridge: the DGN_CONFIG receive slot is a halfword
    #    (0xFFFF when unset), so it must be read with ldrh and compared for EXACT equality to
    #    gate_on_value -- a byte read of 0xFFFF's low byte is 0xFF and would wrongly read as "on"
    #    (selector semantics: unset=0xFFFF, on=1, off=0).
    #  - slot path WITH a bridge: the bridge block above already computed this exact flag and
    #    wrote it to the display cell as a byte (0/1). Re-derive nothing: reload that byte and
    #    test it nonzero -- the same shape as the register path, just a different source address.
    #
    # The register/gates branch (use_gate_list True) supersedes all three: it emits one general
    # `(slot & mask) == value -> bne done` test per Condition, each reading its own gateI pool
    # entry, and NOTHING for an empty list (a pure edge-follower). The legacy branch (use_gate_list
    # False) is byte-for-byte the proven single-gate code. Routing on use_gate_list -- not on
    # `plan.gates` emptiness -- is what keeps an empty-gates register plan out of the legacy branch,
    # which would author a dereference of `plan.gate_addr` (== 0 on the register path).
    if not plan.use_gate_list:
        # Legacy single-gate branch. It reads `plan.gate_addr` as a pool literal (directly, or via
        # the bridge block above); a 0/None here would author a read of address 0. resolve() always
        # sets a real SRAM gate_addr, and the register path never reaches here -- so a falsy
        # gate_addr in this branch is corruption, refuse it (belt-and-braces for Finding 1).
        if not plan.gate_addr:
            raise FollowChannelError(
                "legacy single-gate branch reached with gate_addr=%r; a register/gates plan must "
                "set use_gate_list=True to route through the multi-gate section rather than "
                "dereference a zero gate address" % (plan.gate_addr,))
        if plan.gate_is_slot and has_bridge:
            emit(2, lambda h, s: (thumb.ldr_literal(0, h, s("display")), ldrlit_text(0, h, s("display"))))
            emit(2, lambda h, s: (thumb.ldrb_imm(0, 0, 0), "ldrb r0, [r0]"))
            emit(2, lambda h, s: (thumb.cmp_imm8(0, 0), "cmp r0, #0"))
            emit(2, lambda h, s: (thumb.bcc_n(h, s("done"), "eq"), f"beq #{s('done'):#x}"))
        else:
            emit(2, lambda h, s: (thumb.ldr_literal(0, h, s("gate")), ldrlit_text(0, h, s("gate"))))
            if plan.gate_is_slot:
                # `_check_gate_test` subsumes the old pair of guards (fits cmp_imm8; is not the
                # 0xFFFF unset sentinel) and adds the masked cases. The scratch here is r2, NOT
                # r1: r1 holds the live new on-state the template selection below reads.
                _check_gate_test(plan.gate_mask, plan.gate_on_value)
                emit(2, lambda h, s: (thumb.ldrh_imm(0, 0, 0), "ldrh r0, [r0]"))
                _emit_slot_test(emit, imm, plan.gate_mask, plan.gate_on_value, scratch=2)
                emit(2, lambda h, s: (thumb.bcc_n(h, s("done"), "ne"), f"bne #{s('done'):#x}"))
            else:
                emit(2, lambda h, s: (thumb.ldrb_imm(0, 0, 0), "ldrb r0, [r0]"))
                emit(2, lambda h, s: (thumb.cmp_imm8(0, 0), "cmp r0, #0"))
                emit(2, lambda h, s: (thumb.bcc_n(h, s("done"), "eq"), f"beq #{s('done'):#x}"))
    else:
        # Multi-gate: each Condition is `(slot & mask) == value`; ALL must hold (each a `bne done`),
        # matching the single-gate semantics. Every emission is fixed-size (the size is a pure
        # function of cond.mask/cond.value, known at build time), so the size pass stays correct
        # and assert_disassembles_to re-reads all of it.
        for gi, cond in enumerate(plan.gates):
            sym_name = f"gate{gi}"
            # Fix round 1 finding 1: the SAME validator the single slot gate uses. It subsumes
            # the two u16-range guards this loop had, and adds the three refusals they missed
            # (zero mask, value outside its mask, and a test the 0xFFFF unset sentinel
            # satisfies -- the last of which would transmit on the bus at boot). No emitted
            # byte changes for any gate that was already valid.
            _check_gate_test(cond.mask, cond.value, f"gate {gi} value", allow_wide_value=True)
            # r0 = the gate's slot halfword. The mask/value scratch is r2, NOT r1: at the gate
            # section r1 holds the live new on-state that the template-selection code below reads
            # (`cmp r1, #0`); r2 is dead here (last used as the latch pointer at `strh r1, [r2]`)
            # and is caller-saved (restored by the `pop {r0-r3,lr}` at `done`). r0 and r2 are
            # both re-established every iteration, so nothing carries across gates.
            emit(2, lambda h, s, n=sym_name: (thumb.ldr_literal(0, h, s(n)), ldrlit_text(0, h, s(n))))
            emit(2, lambda h, s: (thumb.ldrh_imm(0, 0, 0), "ldrh r0, [r0]"))
            # Mask (unless the condition compares the full halfword) then compare -- the same
            # emitter the single slot gate and the display bridge use, so all three shapes are
            # one piece of code. Byte-for-byte what this loop emitted inline before Task 13.
            _emit_slot_test(emit, imm, cond.mask, cond.value, scratch=2)
            emit(2, lambda h, s: (thumb.bcc_n(h, s("done"), "ne"), f"bne #{s('done'):#x}"))
    # choose the template by the new state (r1 nonzero = on, 0 = off)
    emit(2, lambda h, s: (thumb.cmp_imm8(1, 0), "cmp r1, #0"))
    emit(2, lambda h, s: (thumb.bcc_n(h, s("loff"), "eq"), f"beq #{s('loff'):#x}"))
    emit(2, lambda h, s: (thumb.ldr_literal(0, h, s("on")), ldrlit_text(0, h, s("on"))))
    emit(2, lambda h, s: (thumb.b_n(h, s("copy")), f"b #{s('copy'):#x}"))
    label("loff")
    emit(2, lambda h, s: (thumb.ldr_literal(0, h, s("off")), ldrlit_text(0, h, s("off"))))
    label("copy")
    # copy the 12-byte frame into a stack buffer, then send it (the Finding-7 way).
    # The frame is only 12 bytes, but sp is decremented by 0x14 (not 0x10) so that at the `blx`
    # the total sp decrement from entry — push {r0-r3,lr} (20 B) + 0x14 (20 B) = 40 B — is a
    # multiple of 8, giving AAPCS 8-byte sp alignment at the call. This mirrors FUN_00247BF8's
    # own frame (push 5 words + sub sp,#0x14). The stm destination and the r0 handed to the
    # sender are both `sp`, i.e. the same address within the enlarged buffer (Task 3 ABI pin).
    emit(2, lambda h, s: (thumb.sub_sp_imm(20), "sub sp, #0x14"))
    emit(2, lambda h, s: (thumb.ldr_imm(1, 0, 0), "ldr r1, [r0]"))
    emit(2, lambda h, s: (thumb.ldr_imm(2, 0, 4), "ldr r2, [r0, #4]"))
    emit(2, lambda h, s: (thumb.ldr_imm(3, 0, 8), "ldr r3, [r0, #8]"))
    emit(2, lambda h, s: (thumb.str_sp_imm(1, 0), "str r1, [sp]"))
    emit(2, lambda h, s: (thumb.str_sp_imm(2, 4), "str r2, [sp, #4]"))
    emit(2, lambda h, s: (thumb.str_sp_imm(3, 8), "str r3, [sp, #8]"))
    emit(2, lambda h, s: (thumb.mov_reg(0, 13), "mov r0, sp"))
    emit(2, lambda h, s: (thumb.ldr_literal(2, h, s("sender")), ldrlit_text(2, h, s("sender"))))
    emit(2, lambda h, s: (thumb.ldr_imm(2, 2, 0), "ldr r2, [r2]"))
    emit(2, lambda h, s: (thumb.blx_reg(2), "blx r2"))
    emit(2, lambda h, s: (thumb.add_sp_imm(20), "add sp, #0x14"))
    label("done")
    # restore caller state INCLUDING lr, then tail-branch to the original per-tick fn (ruling 4)
    emit(4, lambda h, s: (thumb.pop_w_reglist([0, 1, 2, 3, 14]),
                          "pop.w {r0, r1, r2, r3, lr}"))
    emit(4, lambda h, s: (thumb.b_w(h, plan.tail_target), f"b.w #{plan.tail_target:#x}"))

    # Size pass: fix each label's offset within the code (offsets relative to code start).
    labels: dict[str, int] = {}
    code_len = 0
    for item in stream:
        if item[0] == "lbl":
            labels[item[1]] = code_len
        else:
            code_len += item[1]

    entry = code_load_addr + ENTRY_OFF
    pool_blob_off = ENTRY_OFF + code_len
    pool_blob_off = (pool_blob_off + 3) & ~3            # 4-align the pool
    pool_abs = {name: code_load_addr + pool_blob_off + i * 4
                for i, name in enumerate(pool_order)}

    def sym(name: str) -> int:
        if name in labels:
            return entry + labels[name]
        return pool_abs[name]

    # Encode pass.
    code = bytearray()
    expected: list[str] = []
    for item in stream:
        if item[0] == "lbl":
            continue
        _kind, size, fn = item
        here = entry + len(code)
        raw, text = fn(here, sym)
        if len(raw) != size:
            raise FollowChannelError(f"instruction size mismatch at {here:#x}: {text!r}")
        code += raw
        expected.append(text)

    # Verify: re-read every emitted instruction and require it to say what we claimed.
    thumb.assert_disassembles_to(bytes(code), entry, expected)

    # Assemble the blob: templates, code, pad, pool.
    blob = bytearray()
    blob += plan.on_template
    blob += plan.off_template
    assert len(blob) == ENTRY_OFF
    blob += code
    while len(blob) < pool_blob_off:
        blob += b"\x00"
    for name in pool_order:
        blob += struct.pack("<I", pool_val[name])
    assert len(blob) % 4 == 0

    return bytes(blob), thumb.disassemble(bytes(code), entry)


def _check_plan_hook(plan) -> None:
    """The plan's hook bytes must be the stock `bl` (tail = paint fn) or a `bl` to exactly the
    plan's tail target (a previous appended routine); anything else is not a plan from
    `hook_state` and is refused."""
    if plan.hook_old == HOOK_STOCK and plan.tail_target == TAIL_TARGET:
        return
    insns = thumb.disassemble(plan.hook_old, theme.GUICODE_BASE_DEFAULT + plan.hook_at)
    text = insns[0][1] if insns else ""
    if (text == f"bl #{plan.tail_target:#x}" and plan.tail_target >= STOCK_APPEND_POINT):
        return
    raise FollowChannelError(
        f"plan hook bytes {plan.hook_old.hex()} ({text or 'undecodable'}) are neither the stock "
        f"hook nor a bl to the plan's tail target {plan.tail_target:#x}; refusing to author detour")


def detour_edit(plan: FollowPlan, code_load_addr: int) -> tuple[int, bytes, bytes]:
    """Retarget the single caller's `bl` to the routine entry.

    Returns `(file_offset_in_guicode_bin, old4, new4)` -- the GuiCode-section offset, the stock
    bytes (asserted), and the new `bl` bytes reaching `code_load_addr + 0x18`.
    """
    _check_plan_hook(plan)
    # The `bl` displacement is relative, so it needs the hook's runtime address: the GuiCode
    # section maps at theme.GUICODE_BASE_DEFAULT (0x00240000 on all three builds).
    hook_runtime = theme.GUICODE_BASE_DEFAULT + plan.hook_at
    new = thumb.bl(hook_runtime, code_load_addr + ENTRY_OFF)
    return plan.hook_at, plan.hook_old, new


# ======================================================================================
# The display-only "arm indicator" bridge (Task 7 -- live Low Volts trigger on AES)
# ======================================================================================
#
# It MIRRORS the stock cell-45 recolor block un-gated, every tick, and does NOTHING else --
# no edge latch, no CAN transmit. It exists to un-freeze the Low Volts arm state AND its tap
# direction on the compact AES ELECTRICAL page.
#
# Stock GATES the cell-45 recolor decode off in AES mode: `FUN_00247BF8` runs
# `subs r3,#2 / cmp r3,#1 / bls.w 0x24a42e` on the AGS/AES mode global (slot 37) at
# 0x0024A172, so while object 829 is on screen (which only happens BECAUSE the mode is
# AES) the var-42 block at 0x0024A176 is skipped. That block does TWO things this bridge
# reproduces (proven-from-code, `extractions/13.6/GuiCode.bin`, disasm 0x0024A176-0x0024A1A6
# + branch targets 0x24a4e8/0x24a4f4):
#   * the group-129 TAP-DIRECTION counter (SRAM 0x20007686, group state
#     0x20006E6C + 129*16 + 0xA; +hold-twin 0x20007687): stock calls setVariant(129, !armed)
#     -- disarmed -> 1 (arm-ready), armed -> 0 (disarm-ready). Frozen in AES, this is why the
#     Low Volts tap only ever fired one direction (CLAUDE.md defect 0b's variant-counter
#     mechanism). REV 3: we update it the FIRMWARE'S OWN way -- a call to the group-variant
#     setter through the fn-ptr at 0x20039908 (stock 0x24a18c/0x24a4e8: `ldr r3,[0x20039908];
#     ldr r3,[r3]; movs r1,!armed; movs r0,#0x81; blx r3`), NOT a direct byte-write. The setter
#     drives the same counter + hold-twin the earlier rev poked directly, but via the routine
#     the firmware uses, so any bookkeeping that call does (not just the two bytes) happens too.
#   * the ARMED-FLAG byte (SRAM 0x20039A79): stock 0x24a182 does `strb armed,[0x20039A79]`
#     before the branch; the bridge writes it too, for faithfulness (its only stock reader is
#     the frozen block, but replicating the block exactly is the rev-3 directive).
#   * the RECOLOR cell 45 (SRAM 0x2003A814, a u32 BGR colour): armed -> the ON master colour
#     word [0x2003A1A0], disarmed -> the OFF master [0x20039A58] (direct str, as stock).
# The live 0x2F arm state is NOT frozen: its DGN_CONFIG receive-filter entry (var 42) writes
# slot 0x2000801C in EVERY mode, so the bridge reads it un-gated (armed == slot halfword == 1).
#
# Because the bridge runs BEFORE the tail-call to FUN_00247BF8, in AGS the stock block re-runs
# after it and OVERWRITES cell 45 + the counter with the identical values (its gate is false in
# AGS) -- so AGS runtime is unchanged. In AES the stock block's decode is gated off, so the
# bridge's writes stand. It is the Mechanism-B alternative to nop-ing the freeze gate
# (Mechanism C): no GuiCode CODE patch, just an appended routine on the same hook/append-point
# the coach-proven v9 follower uses.
#
# Blob layout (entry at offset 0 -- there are no CAN templates to sit in front of it):
#     +0x00  routine entry (code)
#       ...  code ...
#       pad to 4-align
#       ...  literal pool: &gate_slot, &counter, &on_master, &off_master, &cell45
#
# The routine preserves r4-r7 (never touched) and tail-branches to the displaced
# per-tick paint fn (FUN_00247BF8), exactly like the follower -- r0-r3/lr are push/pop'd.

DISPLAY_BRIDGE_ENTRY_OFF = 0x00     # no templates: the routine is the first thing in the blob

# The stock cell-45 recolor block's own addresses (proven-from-code, 13.6). The bridge writes
# the same cells the stock block writes, so AGS (where the stock block re-runs after the bridge)
# is byte-for-byte unchanged at runtime.
VARIANT_COUNTER_ADDR = 0x20007686   # group-129 tap-direction counter (0x20006E6C+129*16+0xA);
                                    # rev 3 updates it via setVariant(129,...), not a direct write
VARIANT_COUNTER_TWIN = 0x20007687   # its hold-twin (the +0xB byte); the setter drives it too
ON_MASTER_COLOR_ADDR = 0x2003A1A0   # armed -> cell 45 = [this] (stock 0x24a4f4 load)
OFF_MASTER_COLOR_ADDR = 0x20039A58  # disarmed -> cell 45 = [this] (stock 0x24a1a0 load)
RECOLOR_CELL_ADDR = 0x2003A814      # cell 45, the wtype-5 Low Volts container's value_slot 45
ARM_FLAG_ADDR = 0x20039A79          # armed-flag byte (stock 0x24a182 `strb armed,[0x20039A79]`)
VARIANT_GROUP_LOW_VOLTS = 0x81      # group id 129, the arg to setVariant (stock movs r0,#0x81)


@dataclass
class DisplayBridgePlan:
    """Everything the display-only bridge needs, all derived from the image or refused."""
    gate_addr: int          # &(the DGN_CONFIG receive slot the indicator tracks, e.g. 0x2000801C)
    gate_on_value: int      # the (masked) slot value that means "armed" (1 for a whole-halfword
                            # config selector; 0x0100 for a flag in the high byte of 0xEE)
    counter_addr: int       # &(group-129 tap-direction counter); the setter drives it + twin
    on_master_addr: int     # &(ON master colour word); armed -> cell 45 = [this]
    off_master_addr: int    # &(OFF master colour word); disarmed -> cell 45 = [this]
    cell_addr: int          # &(cell 45), the wtype-5 Low Volts container's value_slot 45
    hook_at: int            # GuiCode-section offset of the detoured `bl` (== HOOK_OFF)
    hook_old: bytes         # its current 4 bytes (stock, or a bl into an earlier appended routine)
    tail_target: int = TAIL_TARGET   # the paint fn, or the previous routine's entry (`hook_state`)
    setter_addr: int = VARIANT_SETTER          # &(fn-ptr to setVariant); `ldr r3,[this]; ldr
                                               # r3,[r3]; blx r3` -- the firmware's own updater
    armflag_addr: int = ARM_FLAG_ADDR          # &(armed-flag byte); stock strb'd armed here
    variant_group: int = VARIANT_GROUP_LOW_VOLTS   # setVariant group arg (129 = movs r0,#0x81)
    gate_mask: int = 0xFFFF     # `(slot & gate_mask) == gate_on_value`. 0xFFFF (default) is the
                                # whole-halfword test the hardware-verified bridge emits, byte for
                                # byte; a narrower mask is Task 13's opt-in for a selector packing
                                # two fields in one word (0xEE: minutes | link flag << 8)


def scan_display_cell(data: bytes, exclude: tuple = ()) -> tuple[int, int]:
    """The first spare `+0x44BC8` cell safe to write from an appended routine: 2-aligned,
    both halfword bytes in SRAM and GuiCode-unreferenced, and not in `exclude`.

    Same discipline as `_scan_latch_cell`/`resolve`'s `alloc_display` -- "widget-spare"
    (`statusbind.spare_cell_indices`) is NOT enough, because stock GuiCode keeps live working
    memory in some spare cells (cell 203 / 0x2003A2F8 is loaded by the very per-tick function we
    hook), so the GuiCode-reference scan is what actually excludes the the build log trap cell 203.
    Returns `(cell_index, cell_addr)` or raises `FollowChannelError`."""
    gs, _ = guidata.guidata_extent(data)
    spares = statusbind.spare_cell_indices(data)
    sec = theme.guicode_section(data)
    section = data[sec.offset:sec.offset + sec.length]
    base = theme.guicode_base(sec)

    def _safe(addr: int) -> bool:
        return (SRAM_LOW <= addr < SRAM_HIGH) and not _guicode_references(section, base, addr)

    for idx in spares:
        if idx in exclude:
            continue
        cand = struct.unpack_from("<I", data, gs + celltables.cell_table_off(data) + idx * 4)[0]
        if cand % 2 != 0:
            continue                       # strb touches one byte, but a checkbox value cell
                                           # is read as a byte; keep it 2-aligned for parity
        if _safe(cand) and _safe(cand + 1):
            return idx, cand
    raise FollowChannelError(
        "no 2-aligned spare +0x44BC8 cell in SRAM and GuiCode-unreferenced; refusing to write "
        "an arm flag into firmware-live working memory")


def resolve_display_bridge(data: bytes, *, gate_addr: int, gate_on_value: int = 1,
                           gate_mask: int = 0xFFFF,
                           counter_addr: int = VARIANT_COUNTER_ADDR,
                           on_master_addr: int = ON_MASTER_COLOR_ADDR,
                           off_master_addr: int = OFF_MASTER_COLOR_ADDR,
                           cell_addr: int = RECOLOR_CELL_ADDR,
                           setter_addr: int = VARIANT_SETTER,
                           armflag_addr: int = ARM_FLAG_ADDR,
                           variant_group: int = VARIANT_GROUP_LOW_VOLTS) -> DisplayBridgePlan:
    """Validate the gate slot, the stock cell-45 recolor addresses and the detour site, then
    return a `DisplayBridgePlan`. `gate_addr` is the DGN_CONFIG receive slot (0x2000801C for
    selector 0x2F, from `register.resolve_register`); the rest default to the stock block's own
    pinned addresses so the bridge writes exactly what the stock block writes and AGS runtime is
    unchanged. REV 3: the group-129 counter is updated through the firmware's own variant setter
    (the fn-ptr at `setter_addr` = 0x20039908), NOT a direct byte-write, and the armed-flag byte
    at `armflag_addr` (stock 0x24a182) is written too. Refuses rather than corrupt if an address
    is out of SRAM, mis-aligned, or the hook is not stock.

    `gate_mask` (Task 13) narrows the test to `(slot & gate_mask) == gate_on_value`. It defaults
    to 0xFFFF -- the whole-halfword test the coach-verified Low Volts bridge emits, byte for byte.
    Selector 0xEE now carries the auto-off minutes in its low byte and the Pump Link flag in its
    high byte, so a bridge that highlights that button must pass `gate_mask=0xFF00,
    gate_on_value=0x0100`; the unmasked test can never hold on such a word."""
    # gate_addr/counter_addr live in the receive/var-state window (0x2000xxxx); the masters,
    # cell 45, setter fn-ptr and armed flag in the colour/config window (0x2003xxxx) -- validate
    # all against the full SRAM window (theme.SRAM_LOW/HIGH), not the narrower SRAM_LOW/HIGH used
    # only for the safety scan.
    for name, addr in (("gate_addr", gate_addr), ("counter_addr", counter_addr),
                       ("on_master_addr", on_master_addr), ("off_master_addr", off_master_addr),
                       ("cell_addr", cell_addr), ("setter_addr", setter_addr),
                       ("armflag_addr", armflag_addr)):
        if not (theme.SRAM_LOW <= addr < theme.SRAM_HIGH):
            raise FollowChannelError(
                f"{name} {addr:#x} is outside SRAM [{theme.SRAM_LOW:#x},{theme.SRAM_HIGH:#x})")
    if gate_addr % 2:
        raise FollowChannelError("gate_addr must be 2-aligned (ldrh)")
    for name, addr in (("on_master_addr", on_master_addr), ("off_master_addr", off_master_addr),
                       ("cell_addr", cell_addr), ("setter_addr", setter_addr)):
        if addr % 4:
            raise FollowChannelError(f"{name} {addr:#x} must be 4-aligned (ldr/str word)")
    if counter_addr + 1 >= theme.SRAM_HIGH:
        raise FollowChannelError("counter twin byte falls outside SRAM")
    _check_gate_test(gate_mask, gate_on_value)
    if not (0 <= variant_group <= 0xFF):
        raise FollowChannelError(
            f"variant_group {variant_group:#x} must fit `movs r0,#imm8` (0..0xFF)")
    hook_old, tail_target = hook_state(data)
    return DisplayBridgePlan(gate_addr=gate_addr, gate_on_value=gate_on_value,
                             gate_mask=gate_mask,
                             counter_addr=counter_addr, on_master_addr=on_master_addr,
                             off_master_addr=off_master_addr, cell_addr=cell_addr,
                             hook_at=HOOK_OFF, hook_old=hook_old, tail_target=tail_target,
                             setter_addr=setter_addr,
                             armflag_addr=armflag_addr, variant_group=variant_group)


def build_display_bridge(plan: DisplayBridgePlan, code_load_addr: int) -> tuple[bytes, list]:
    """Author the appended display-only bridge blob (routine + literal pool) and verify it.

    Returns `(appended_bytes, disassembly)`. Every emitted instruction is re-read with
    `thumb.assert_disassembles_to` and checked against the text this builder claims, the same
    no-trust-on-inspection contract as `build`."""
    def imm(v: int) -> str:
        return f"{v:#x}" if v >= 10 else str(v)

    # Pool: the stock cell-45 block's own operands. REV 3 drops the direct "counter" pool word
    # (the setter drives that counter by group id, not by address) and adds the setter fn-ptr and
    # the armed-flag byte, so the bridge is a byte-faithful un-gated replica of the stock block.
    pool_order = ["gate", "setter", "armflag", "on_master", "off_master", "cell"]
    pool_val = {"gate": plan.gate_addr, "setter": plan.setter_addr,
                "armflag": plan.armflag_addr, "on_master": plan.on_master_addr,
                "off_master": plan.off_master_addr, "cell": plan.cell_addr}

    stream: list = []

    def emit(size: int, fn) -> None:
        stream.append(("ins", size, fn))

    def label(name: str) -> None:
        stream.append(("lbl", name))

    def ldrlit_text(rt: int, here: int, target: int) -> str:
        off = target - ((here + 4) & ~3)
        return f"ldr r{rt}, [pc, #{off:#x}]" if off else f"ldr r{rt}, [pc]"

    grp = plan.variant_group

    # This is the stock Low Volts block (0x0024A176-0x0024A1A6 + arms 0x24a4e8/0x24a4f4) with the
    # mode gate removed, authored instruction-for-instruction. push {r0-r3,lr}: r0-r3 are scratch
    # and are clobbered by the setVariant `blx` below; lr must survive the tail-branch (ruling 4).
    # The `blx` sets the lr REGISTER, not the pushed lr slot, so the saved lr stays intact and the
    # pop restores the caller's lr; every pool address needed AFTER the blx is reloaded past it.
    emit(2, lambda h, s: (thumb.push_reglist([0, 1, 2, 3, 14]), "push {r0, r1, r2, r3, lr}"))
    # armed = ((gate slot & gate_mask) == gate_on_value). Materialise it as 0/1 (stock uses
    # `ite`; we branch, reusing existing encoders -- same result). The unset slot 0xFFFF can
    # never satisfy the test (`_check_gate_test` refuses any mask/value pair it would) -> armed
    # 0, the safe OFF. The mask/value scratch is r2: it is dead until the armflag pointer is
    # loaded below, and r1 is the flag the two arms set. Unmasked, `_emit_slot_test` collapses
    # to the single `cmp r0, #gate_on_value` this bridge has always emitted.
    _check_gate_test(plan.gate_mask, plan.gate_on_value)
    emit(2, lambda h, s: (thumb.ldr_literal(0, h, s("gate")), ldrlit_text(0, h, s("gate"))))
    emit(2, lambda h, s: (thumb.ldrh_imm(0, 0, 0), "ldrh r0, [r0]"))
    _emit_slot_test(emit, imm, plan.gate_mask, plan.gate_on_value, scratch=2)
    emit(2, lambda h, s: (thumb.bcc_n(h, s("barmed"), "eq"), f"beq #{s('barmed'):#x}"))
    emit(2, lambda h, s: (thumb.movs_imm(1, 0), "movs r1, #0"))
    emit(2, lambda h, s: (thumb.b_n(h, s("bflag")), f"b #{s('bflag'):#x}"))
    label("barmed")
    emit(2, lambda h, s: (thumb.movs_imm(1, 1), "movs r1, #1"))
    label("bflag")
    # armed-flag = armed (stock 0x24a182 `strb armed,[0x20039A79]`), written once before the branch.
    emit(2, lambda h, s: (thumb.ldr_literal(2, h, s("armflag")), ldrlit_text(2, h, s("armflag"))))
    emit(4, lambda h, s: (thumb.strb_imm12(1, 2, 0), "strb.w r1, [r2]"))
    emit(2, lambda h, s: (thumb.cmp_imm8(1, 0), "cmp r1, #0"))
    emit(2, lambda h, s: (thumb.bcc_n(h, s("bdisarmed"), "eq"), f"beq #{s('bdisarmed'):#x}"))
    # ARMED arm (stock 0x24a4e8/0x24a4f4): setVariant(0x81, 0); select the ON master pointer.
    emit(2, lambda h, s: (thumb.ldr_literal(3, h, s("setter")), ldrlit_text(3, h, s("setter"))))
    emit(2, lambda h, s: (thumb.ldr_imm(3, 3, 0), "ldr r3, [r3]"))
    emit(2, lambda h, s: (thumb.movs_imm(1, 0), "movs r1, #0"))
    emit(2, lambda h, s: (thumb.movs_imm(0, grp), f"movs r0, #{imm(grp)}"))
    emit(2, lambda h, s: (thumb.blx_reg(3), "blx r3"))
    emit(2, lambda h, s: (thumb.ldr_literal(2, h, s("on_master")),
                          ldrlit_text(2, h, s("on_master"))))
    emit(2, lambda h, s: (thumb.b_n(h, s("bcolor")), f"b #{s('bcolor'):#x}"))
    label("bdisarmed")
    # DISARMED arm (stock 0x24a18c/0x24a1a0): setVariant(0x81, 1); select the OFF master pointer.
    emit(2, lambda h, s: (thumb.ldr_literal(3, h, s("setter")), ldrlit_text(3, h, s("setter"))))
    emit(2, lambda h, s: (thumb.ldr_imm(3, 3, 0), "ldr r3, [r3]"))
    emit(2, lambda h, s: (thumb.movs_imm(1, 1), "movs r1, #1"))
    emit(2, lambda h, s: (thumb.movs_imm(0, grp), f"movs r0, #{imm(grp)}"))
    emit(2, lambda h, s: (thumb.blx_reg(3), "blx r3"))
    emit(2, lambda h, s: (thumb.ldr_literal(2, h, s("off_master")),
                          ldrlit_text(2, h, s("off_master"))))
    label("bcolor")
    # Shared cell-45 store: cell 45 = *(selected master). This is byte-for-byte the coach-proven
    # rev-2 recolor tail (`ldr r1,[r2]; ldr r2,[=cell]; str r1,[r2]`), kept SHARED so the ARMED
    # arm's ON-master load is separated from this store by the `b bcolor` above -- which clears
    # `theme._copy_store_masters`'s linear-decode history, exactly as in rev 2. Only the fall-
    # through OFF-master copy (0x20039A58 == stock's own cell-45 master) is therefore recorded, so
    # the static AGS render sees cell 45's single master unchanged and the golden net stays green.
    # Inlining a second `ldr on_master; ldr [r2]; str cell` copy (0x2003A1A0) instead would add a
    # foreign master and drop cell 45 from `resting_defaults`, changing the AGS render.
    emit(2, lambda h, s: (thumb.ldr_imm(1, 2, 0), "ldr r1, [r2]"))
    emit(2, lambda h, s: (thumb.ldr_literal(2, h, s("cell")), ldrlit_text(2, h, s("cell"))))
    emit(2, lambda h, s: (thumb.str_imm(1, 2, 0), "str r1, [r2]"))
    emit(4, lambda h, s: (thumb.pop_w_reglist([0, 1, 2, 3, 14]),
                          "pop.w {r0, r1, r2, r3, lr}"))
    emit(4, lambda h, s: (thumb.b_w(h, plan.tail_target), f"b.w #{plan.tail_target:#x}"))

    # Size pass: fix each label's offset (relative to code start).
    labels: dict[str, int] = {}
    code_len = 0
    for item in stream:
        if item[0] == "lbl":
            labels[item[1]] = code_len
        else:
            code_len += item[1]

    entry = code_load_addr + DISPLAY_BRIDGE_ENTRY_OFF
    pool_blob_off = DISPLAY_BRIDGE_ENTRY_OFF + code_len
    pool_blob_off = (pool_blob_off + 3) & ~3            # 4-align the pool
    pool_abs = {name: code_load_addr + pool_blob_off + i * 4
                for i, name in enumerate(pool_order)}

    def sym(name: str) -> int:
        if name in labels:
            return entry + labels[name]
        return pool_abs[name]

    code = bytearray()
    expected: list[str] = []
    for item in stream:
        if item[0] == "lbl":
            continue
        _kind, size, fn = item
        here = entry + len(code)
        raw, text = fn(here, sym)
        if len(raw) != size:
            raise FollowChannelError(f"instruction size mismatch at {here:#x}: {text!r}")
        code += raw
        expected.append(text)

    thumb.assert_disassembles_to(bytes(code), entry, expected)

    blob = bytearray(code)
    while len(blob) < pool_blob_off:
        blob += b"\x00"
    for name in pool_order:
        blob += struct.pack("<I", pool_val[name])
    assert len(blob) % 4 == 0
    return bytes(blob), thumb.disassemble(bytes(code), entry)


def detour_display_bridge(plan: DisplayBridgePlan, code_load_addr: int) -> tuple[int, bytes, bytes]:
    """Retarget the single caller's `bl` to the display bridge's entry (offset 0 of its blob).

    Returns `(file_offset_in_guicode_bin, old4, new4)`. Unlike `detour_edit`, the entry is at the
    blob's start (there are no CAN templates), so the `bl` targets `code_load_addr` itself."""
    _check_plan_hook(plan)
    hook_runtime = theme.GUICODE_BASE_DEFAULT + plan.hook_at
    new = thumb.bl(hook_runtime, code_load_addr + DISPLAY_BRIDGE_ENTRY_OFF)
    return plan.hook_at, plan.hook_old, new
