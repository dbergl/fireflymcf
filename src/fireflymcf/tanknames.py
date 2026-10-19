"""Which tank each RV-C `1FFB7` TANK_STATUS instance is, read from the firmware -- not the standard.

`tankbroadcast.INSTANCE_NAMES` is RV-C's table (0 fresh, 1 black, 2 grey, 3 LPG, 18 second
grey...). A build is free to use other instances, and the standard says nothing about what a
panel *draws* for one. This module answers from the bytes: for every `1FFB7` entry in the panel's
receive filter it follows the received level to the gauge that paints it and reads that gauge's
caption.

## The chain (13.6 addresses; the module finds every joint per build)

```
receive filter (GuiData +0x2A70) entry `b7 ff 29 01 <inst> ...`        guidata.variable_table
  |  LCD FUN_0021C760 writes slot[var] (MCF-FORMAT §5.4a)                [proven-from-code]
  v
GuiCode per-tick decoder (13.6 FUN_00247BF8: push / mov r4,r0 / ... ldr.w [r4,#0x214])
  |  ldrsh.w rD,[r4,#2*var]   e.g. 0x24838A var 0 -> uxtb/ubfx/mla/sdiv:
  |    pct = min(100, (100*level + res/2) / res)                        [proven-from-code]
  v  strb pct -> cell 0x200399E0           (and a 73-px "empty" offset -> 0x2003A6D5)
cell table (GuiData +0x44BC8, status_map.cell_table): index 255 -> 0x200399E0      [proven-from-code]
  v
HOME tank cluster record list:  wt-11 'FRESH'  ,  wt-5 container value_slot=255 child=721
  (721 is the gauge; its wt-8 '%' readout names value_slot 255 too)    [record decode: proven]
```

**What is inferred.** Everything down to "a wt-5 gauge container whose value_slot is the cell the
instance's level lands in" is cited instructions and decoded records. That the wt-11 label
*immediately preceding* that container in the same record list is its **caption** is
`inferred-from-data`: it is the record order every cluster on every corpus build uses (label, then
its gauge), the label is drawn directly above the gauge, and it is independently repeated on the
PROBE TANK DIAGNOSTICS rows (13.6 objects 1016-1022: `'FRESH'` + the same container + the ADC
readout). It is not read from an instruction, because no instruction joins a caption to a gauge:
the layout engine just draws records in order.

**Nothing is hard-coded per build.** The instances come from the receive filter. The decoder is
located by its own structure (`decoder_ranges`: a function that does `mov r4,r0` at entry and
dereferences the config pointer at `[r4,#0x214]` -- `config.CONFIG_BUFFER_OFFSET` -- up to the
first instruction that writes r4 again). The level's destination cells are found by register
dataflow from the `ldrsh` of that var, and the cell indices and captions come from GuiData. A build
with no such region raises `TankNameError`; a var whose level reaches no captioned gauge gets the
name `None`. Neither falls back to the RV-C table.

**Floorplan gating.** Each tank's caption sits inside per-floorplan HOME clusters (13.6 objects
911-921, chosen by the floorplan switch) and, for BLACK, behind the Black Tank option's type-6
switch (slot 112, base 801 -> arm 802). This module reads every object regardless of which
floorplan would show it, so it names a tank the current configuration hides. `TankGauge.captions`
lists the objects each caption was found in, so a caller can say which floorplan reveals it
(`GUICODE.md` Finding 80 has the per-floorplan table for the corpus).
"""
from __future__ import annotations

import struct
from collections import Counter
from dataclasses import dataclass

from . import config, guidata, memo, records, status_map, theme

TANK_STATUS_DGN = 0x1FFB7
SLOT_BASE_REG = "r4"
SRAM_LOW, SRAM_HIGH = 0x20000000, 0x20080000

# A dataflow trace from one slot load ends when no register carries the value any more, at a
# call or an unconditional branch, or after this many instructions -- a decode block is a few
# dozen instructions (13.6's longest tank block, var 138, is 43 from load to last store).
TRACE_LIMIT = 96


class TankNameError(Exception):
    """The image does not have the shape this module reads; nothing is guessed."""


@dataclass(frozen=True)
class TankGauge:
    """One `1FFB7` receive-filter entry and where the panel paints it."""
    instance: int               # RV-C TANK_STATUS instance (filter entry byte 4)
    var: int                    # receive-filter entry index == slot index
    load_at: tuple              # GuiCode addresses of `ldrsh rD,[r4,#2*var]` in the decoder
    stores: tuple               # ((store address, SRAM cell), ...) the loaded value reaches
    cell_indices: tuple         # cell-table indices naming any of those cells
    captions: tuple             # ((object index, caption record offset, text), ...)

    @property
    def names(self) -> Counter:
        return Counter(_norm(t) for _o, _r, t in self.captions)

    @property
    def name(self) -> str | None:
        """The caption, when every place the gauge is captioned agrees; else None."""
        names = self.names
        return next(iter(names)) if len(names) == 1 else None

    @property
    def source(self) -> str:
        objs = sorted({o for o, _r, _t in self.captions})
        loads = ", ".join(f"{a:#x}" for a in self.load_at)
        cells = ", ".join(f"{c:#x}" for _a, c in self.stores)
        return (f"panel gauge caption: filter var {self.var} -> GuiCode ldrsh [r4,#{2 * self.var:#x}]"
                f" @{loads} -> cell {cells} -> cell index {list(self.cell_indices)}"
                f" -> wt-5 gauge after caption in object(s) {_ranges(objs)}")


def _norm(text: str) -> str:
    return " ".join(text.split())


def _ranges(nums) -> str:
    out, run = [], []
    for n in nums:
        if run and n == run[-1] + 1:
            run.append(n)
            continue
        if run:
            out.append(f"{run[0]}-{run[-1]}" if len(run) > 1 else str(run[0]))
        run = [n]
    if run:
        out.append(f"{run[0]}-{run[-1]}" if len(run) > 1 else str(run[0]))
    return ", ".join(out)


# ------------------------------------------------------------------------------------ GuiCode

def _sweep(md, code: bytes, base: int, pools: set) -> list:
    """Linear sweep that steps over known literal-pool words and undecodable bytes."""
    out, off = [], 0
    while off < len(code) - 1:
        if base + off in pools:
            off += 2
            continue
        moved = False
        for ins in md.disasm(code[off:], base + off):
            if ins.address in pools or (ins.size == 4 and ins.address + 2 in pools):
                break
            out.append(ins)
            moved = True
            off = ins.address - base + ins.size
        if not moved:
            off += 2
    return out


def _pool_words(code: bytes, base: int, ins: list) -> set:
    """Halfword addresses covered by the 4-byte words some `ldr rX,[pc,#imm]` loads."""
    import capstone.arm as A
    pools = set()
    for i in ins:
        if (i.mnemonic in ("ldr", "ldr.w") and len(i.operands) == 2
                and i.operands[1].type == A.ARM_OP_MEM and i.operands[1].mem.base == A.ARM_REG_PC):
            at = ((i.address + 4) & ~3) + i.operands[1].mem.disp
            if base <= at < base + len(code) - 3:
                pools.update((at, at + 2))
    return pools


def _disassemble(code: bytes, base: int) -> tuple:
    """Every instruction with operand detail, never decoding a literal-pool word as code.

    A plain linear sweep decodes the pools that follow each function as instructions (`adr r4`,
    `ldr r4,[sp,#..]` out of a pointer word), which both fakes register writes and hides the next
    function's prologue. Two passes: collect the words PC-relative loads name, then re-sweep
    stepping over them; repeat until the pool set stops growing.
    """
    import capstone
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    md.detail = True
    pools: set = set()
    for _ in range(4):
        out = _sweep(md, code, base, pools)
        grown = pools | _pool_words(code, base, out)
        if grown == pools:
            break
        pools = grown
    return tuple(out)


def _writes(ins) -> set:
    """Register names an instruction writes (capstone's own access sets)."""
    try:
        _r, w = ins.regs_access()
    except Exception:        # pragma: no cover - capstone without detail
        return set()
    return {ins.reg_name(r) for r in w}


def _reads(ins) -> set:
    try:
        r, _w = ins.regs_access()
    except Exception:        # pragma: no cover
        return set()
    return {ins.reg_name(x) for x in r}


def _is_push_lr(ins) -> bool:
    return ins.mnemonic in ("push", "push.w", "stmdb") and "lr" in ins.op_str


def _is_call(ins) -> bool:
    return ins.mnemonic in ("bl", "blx")


def _is_unconditional_exit(ins) -> bool:
    import capstone.arm as A
    m = ins.mnemonic
    if m in ("b", "b.w") and ins.cc in (0, A.ARM_CC_AL):
        return True
    if m in ("bx",) or (m.startswith("pop") and "pc" in ins.op_str):
        return True
    return False


@memo.content_cached(copy=None)
def _guicode(data: bytes) -> tuple:
    section = theme.guicode_section(data)
    base = theme.guicode_base(section)
    code = bytes(data[section.offset:section.offset + section.length])
    ins = _disassemble(code, base)
    return code, base, ins, {x.address: n for n, x in enumerate(ins)}


def _r4_disp(ins):
    """`disp` when `ins` is a load/store through `[r4,#disp]` with no index register, else None."""
    import capstone.arm as A
    for op in ins.operands:
        if op.type == A.ARM_OP_MEM:
            if ins.reg_name(op.mem.base) == SLOT_BASE_REG and op.mem.index == 0:
                return op.mem.disp
            return None
    return None


@memo.content_cached(copy=None)
def decoder_ranges(data: bytes) -> tuple:
    """`((entry, end), ...)`: every GuiCode region in which r4 is provably GuiCodeState.

    A region starts at a `push {..., lr}` followed within four instructions by `mov r4, r0`, and
    runs up to the first instruction that writes r4 again (13.6's decoder: its one epilogue
    `pop {r4-r7,pc}` at 0x24EDB0) or the next prologue. It qualifies only if it dereferences the
    config-buffer pointer `[r4,#0x214]` (`config.CONFIG_BUFFER_OFFSET`, GUICODE Finding 20), which
    is what says the r0 it was handed is GuiCodeState -- whose first bytes are the receive-slot
    array, so `[r4,#2*var]` is `slot[var]` (GUICODE Finding 33). 13.6 has two such regions
    (`FUN_00247BF8` and one at 0x250984); 11.23 two; 14.7 one. At least one must exist.
    """
    _code, _base, ins, _at = _guicode(data)
    found = []
    for k, i in enumerate(ins):
        if not _is_push_lr(i):
            continue
        mov_at = None
        for j in range(k + 1, min(k + 5, len(ins))):
            if ins[j].mnemonic in ("mov", "movs") and ins[j].op_str == "r4, r0":
                mov_at = j
                break
        if mov_at is None:
            continue
        end = len(ins)
        for j in range(mov_at + 1, len(ins)):
            if _is_push_lr(ins[j]) or SLOT_BASE_REG in _writes(ins[j]):
                end = j
                break
        body = ins[mov_at + 1:end]
        if not any(b.mnemonic.startswith("ldr") and _r4_disp(b) == config.CONFIG_BUFFER_OFFSET
                   for b in body):
            continue
        found.append((i.address, ins[end].address if end < len(ins) else ins[-1].address + 2))
    if not found:
        raise TankNameError(
            "no GuiCode region with r4 = GuiCodeState (push / mov r4,r0 / ... ldr [r4,#0x214]) "
            "on this build -- the decoder is shaped differently; refusing to guess")
    return tuple(found)


def _literal(code: bytes, base: int, ins) -> int | None:
    op = ins.operands[1]
    at = ((ins.address + 4) & ~3) + op.mem.disp - base
    if 0 <= at <= len(code) - 4:
        return struct.unpack_from("<I", code, at)[0]
    return None


def _branch_target(ins) -> int | None:
    import capstone.arm as A
    ops = ins.operands
    if len(ops) == 1 and ops[0].type == A.ARM_OP_IMM:
        return ops[0].imm
    return None


def _trace_from(code: bytes, base: int, ins: tuple, at: dict, k: int, dest: str) -> list:
    """Forward register dataflow from the slot load at `ins[k]` into `dest`.

    Returns `[(store address, SRAM cell), ...]` for every `str*` whose value register carries the
    loaded value and whose address register holds a concrete SRAM literal. An instruction's
    destination is tainted iff one of its sources is (a predicated one keeps its old taint as
    well); a load or literal overwrites. The trace follows the fall-through path only.
    """
    import capstone.arm as A
    tainted = {dest}
    concrete: dict = {}
    out = []
    j = k + 1
    for _step in range(TRACE_LIMIT):
        if j >= len(ins):
            break
        i = ins[j]
        j += 1
        m = i.mnemonic
        if _is_call(i):
            break
        if _is_unconditional_exit(i):
            # A forward `b` is the join of an if/else diamond (11.23: `b` over the 0xFF
            # sentinel arm to the shared `strb`); follow it. Anything else ends the path.
            target = _branch_target(i)
            if m in ("b", "b.w") and target is not None and target > i.address and target in at:
                j = at[target]
                continue
            break
        ops = i.operands
        if m.startswith("str") and len(ops) == 2 and ops[1].type == A.ARM_OP_MEM:
            val = i.reg_name(ops[0].reg)
            base_reg = i.reg_name(ops[1].mem.base)
            if (val in tainted and ops[1].mem.index == 0 and base_reg in concrete):
                cell = concrete[base_reg] + ops[1].mem.disp
                if SRAM_LOW <= cell < SRAM_HIGH:
                    out.append((i.address, cell))
            continue
        if (m in ("ldr", "ldr.w") and len(ops) == 2 and ops[1].type == A.ARM_OP_MEM
                and ops[1].mem.base == A.ARM_REG_PC):
            rt = i.reg_name(ops[0].reg)
            tainted.discard(rt)
            val = _literal(code, base, i)
            if val is None:
                concrete.pop(rt, None)
            else:
                concrete[rt] = val
            continue
        writes = _writes(i) - {"pc", "sp", "itstate", "cpsr", "apsr"}
        if not writes:
            continue
        predicated = i.cc not in (0, A.ARM_CC_AL) and not m.startswith("b")
        src_tainted = bool(_reads(i) & tainted)
        for w in writes:
            concrete.pop(w, None)
            if src_tainted:
                tainted.add(w)
            elif not predicated:
                tainted.discard(w)
        if not tainted:
            break
    return out


@memo.content_cached(copy=None)
def level_stores(data: bytes, var: int) -> tuple:
    """`(load addresses, ((store address, cell), ...))` for slot `var` inside the decoder."""
    code, base, ins, at = _guicode(data)
    ranges = decoder_ranges(data)
    loads, stores = [], []
    for k, i in enumerate(ins):
        if not any(lo <= i.address < hi for lo, hi in ranges):
            continue
        if i.mnemonic in ("ldrsh", "ldrsh.w", "ldrh", "ldrh.w") and _r4_disp(i) == 2 * var:
            loads.append(i.address)
            dest = i.reg_name(i.operands[0].reg)
            stores += _trace_from(code, base, ins, at, k, dest)
    return tuple(loads), tuple(dict.fromkeys(stores))


# ------------------------------------------------------------------------------------ GuiData

@memo.content_cached(copy=None)
def _captioned_containers(data: bytes) -> tuple:
    """`((object, caption offset, caption text, container value_slot), ...)` across table 1.

    A caption is a labelled record immediately followed (in the same object's own record list)
    by a wt-5 container that carries a `value_slot`.
    """
    gs, _ge = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    out = []
    for obj, off in enumerate(table):
        try:
            recs = records.object_records(data, gs + off)
        except Exception:
            continue
        for a, b in zip(recs, recs[1:]):
            if (a.label is not None and a.label.strip() and b.wtype == 5
                    and isinstance(getattr(b, "value_slot", None), int)):
                out.append((obj, a.offset, a.label, b.value_slot))
    return tuple(out)


def _filter_entries(data: bytes) -> list:
    out = []
    for v in guidata.variable_table(data):
        dgn = v.raw[0] | (v.raw[1] << 8) | ((v.raw[2] & 0x03) << 16)
        if dgn == TANK_STATUS_DGN:
            out.append((v.index, v.raw[4]))
    return out


@memo.content_cached(copy=None)
def tank_gauges(data: bytes) -> tuple:
    """A `TankGauge` per `1FFB7` receive-filter entry, sorted by instance."""
    entries = _filter_entries(data)
    if not entries:
        raise TankNameError("the panel's receive filter has no 1FFB7 TANK_STATUS entry")
    table = status_map.cell_table(data)
    cell_to_idx: dict = {}
    for idx, c in enumerate(table):
        cell_to_idx.setdefault(c, []).append(idx)
    captioned = _captioned_containers(data)
    out = []
    for var, inst in entries:
        loads, stores = level_stores(data, var)
        idxs = sorted({i for _a, c in stores for i in cell_to_idx.get(c, [])})
        caps = tuple((o, r, t) for o, r, t, vs in captioned if vs in idxs)
        out.append(TankGauge(instance=inst, var=var, load_at=loads, stores=stores,
                             cell_indices=tuple(idxs), captions=caps))
    return tuple(sorted(out, key=lambda g: (g.instance, g.var)))


def tank_names(data: bytes) -> dict:
    """`{instance: (name, source)}` for every `1FFB7` instance the panel receives.

    `name` is the gauge caption, whitespace-normalised. When layouts caption the same gauge
    differently (11.23: instance 2 is `GREY` in five HOME clusters and the PROBE row but `GREY1`
    in cluster 872, the one that also draws `GREY2`), every variant is returned, most-used first,
    joined by `" / "` -- each is the firmware's own word, and choosing one would be a fit. `name`
    is `None` only when the level reaches no captioned gauge. `source` cites the chain.
    """
    out = {}
    for g in tank_gauges(data):
        if g.captions:
            variants = sorted(g.names.items(), key=lambda kv: (-kv[1], kv[0]))
            name = " / ".join(n for n, _c in variants)
            src = g.source
            if len(variants) > 1:
                per = "; ".join(
                    f"{n!r} in object(s) {_ranges(sorted({o for o, _r, t in g.captions if _norm(t) == n}))}"
                    for n, _c in variants)
                src += f" [captions differ by layout: {per}]"
            out[g.instance] = (name, src)
        else:
            out[g.instance] = (None, f"filter var {g.var}: no captioned gauge reached "
                                     f"(loads {[hex(a) for a in g.load_at]}, "
                                     f"cells {[hex(c) for _a, c in g.stores]})")
    return out
