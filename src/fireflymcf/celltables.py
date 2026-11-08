"""The live display-cell table (`DAT_00253D04`, stock 13.6 GuiData+0x44BC8) and its parallel type
table (stock 13.6 +0x4492C), wherever they are on THIS image.

`tablegrow` relocates both when a recipe grows them (`readout.allocate_fresh_cells`), leaving the
stock copy in place but unread. Every reader and writer resolves through here; a fixed
`gs + CELL_TABLE_OFF` indexes the dead copy (2026-09-25; guarded by tests/test_celltables.py).

`locate` finds both from GuiCode alone, so a stale copy in GuiData can never be picked:

1. Collect every bound compare: `cmp rI,#imm`, `cmp.w rI,#imm`, or `movw rB,#imm ; cmp rI,rB`
   (11.23's form), kept only when the next instruction is an unsigned ordering branch
   (`bls`/`bhi`/`bhs`/`blo`). A `beq`/`bne` after it is a sentinel test, not a bound.
2. Credit a compare's `imm` to a GuiData-mapped literal T when, within two branch hops, it
   reaches an indexed `ldr` with the compare's index register off a base literal-loaded with T
   (`tablegrow._reaches_indexed_read_off_base`).
3. The cell table is the one T whose `imm + 1` words are all 0 or SRAM pointers, at least half
   of them real; it must carry exactly one distinct bound.
4. The type table is the other T gated by the same bound, ending 4-aligned where the cell
   table starts: `T + align4(rows) == cell`.
5. The type table has one source of truth: the located pair must equal the literal pair
   `guidata.variable_table_literals` names, or the locator refuses.
6. The bound's other compares are clamp sites: GuiCode stores an index with `strh` just before
   comparing it to the bound (and stores 0 when it is out of range), and a later `ldrh` of that
   SRAM halfword indexes the cell table. A compare counts as a clamp site only when
   `_halfword_moves` resolves both the store and a read whose loaded register reaches an indexed
   `ldr` off the cell table's base (`_clamps`); `proof` names the store, the read and the indexed
   load. `bound_sites` = gate sites (step 2) + clamp sites: everything a grow rebumps
   (`tablegrow.cell_table_bound_sites` refuses unless every compare against the bound is in it).

   Measured 2026-11-08 (compare: strh -> SRAM; ldrh reader -> indexed cell read):
   13.6  0x25383C: 0x25383A -> 0x20036DA8; 0x258208 -> 0x25820C
         0x253870: 0x25386E -> 0x20036DAA; 0x257C56 -> 0x257C5A
         0x25388E: 0x25388C -> 0x20036DAC; 0x257D38 -> 0x257D3C
         0x2538AC: 0x2538AA -> 0x20036DAE; 0x257E3C -> 0x257E40
         0x2538C6: 0x2538C4 -> 0x20036DB0; 0x257ED4 -> 0x257ED8
         0x2538E4: 0x2538E2 -> 0x20036DB2; 0x257FF4 -> 0x257FF8
   14.7  0x256104: 0x256102 -> 0x20036DAC; 0x25AAC4 -> 0x25AAC8
         0x256138: 0x256136 -> 0x20036DAE; 0x25A512 -> 0x25A516
         0x256156: 0x256154 -> 0x20036DB0; 0x25A5F4 -> 0x25A5F8
         0x256174: 0x256172 -> 0x20036DB2; 0x25A6F8 -> 0x25A6FC
         0x25618E: 0x25618C -> 0x20036DB4; 0x25A790 -> 0x25A794
         0x2561AC: 0x2561AA -> 0x20036DB6; 0x25A8B0 -> 0x25A8B4
   11.23 credits six too (0x251548 ... 0x25163E, `movw+cmp`), which the grow refuses by form.

Measured 2026-11-07: 13.6 cell 0x44BC8 / type 0x4492C / 665 rows (`cmp.w r0,#0x298`, 8 gate sites);
14.7 0x42B74 / 0x42978 / 505 (`cmp.w #0x1F8`); 11.23 0x44B60 / 0x4492C / 562 (`movw r3,#0x231 ;
cmp r0,r3`); and the relocated pair on every grown reference build.
"""
from __future__ import annotations

import re
import struct
from collections import deque
from dataclasses import dataclass

from . import guidata, memo

_GD = 0x90000000
_UNSIGNED_BRANCHES = ("bls", "bhi", "bhs", "blo", "bcs", "bcc")
_REG_IMM = re.compile(r"r(\d+), #(0x[0-9a-f]+|\d+)")
_REG_REG = re.compile(r"r(\d+), r(\d+)")


class CellTableError(guidata.VariableTableNotFound):
    """The cell or type table does not prove out on this image. `code` is one of
    CELL_TABLE_NO_BOUND (no unsigned bound compare gates any GuiData table),
    CELL_TABLE_CANDIDATES (not exactly one SRAM-pointer table), CELL_TABLE_BOUND_AMBIGUOUS
    (more than one distinct bound gates it), TYPE_TABLE_CANDIDATES (not exactly one table
    shares its bound), CELL_TABLE_ADJACENCY (the type table does not end where the cell table
    starts) or TYPE_TABLE_DISAGREES (the GuiCode literal pair names another pair). Growing the
    table adds CELL_TABLE_BOUND_FORM (a bound compare in a form the grow cannot rebump, 11.23's
    `movw+cmp`) and CELL_TABLE_BOUND_SITES (the rebump surface misses a bound compare), both
    from `tablegrow.cell_table_bound_sites`."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class CellTables:
    cell_base: int                  # runtime address (0x9xxxxxxx) of the u32 cell table
    type_base: int                  # runtime address of the u8 type table
    rows: int                       # bound + 1
    bound_sites: tuple[int, ...]    # gate_sites + clamp_sites: every compare a grow rebumps
    bound_form: str                 # "cmp", "cmp.w" or "movw+cmp" ("+"-joined if mixed)
    cell_refs: tuple[int, ...]      # GuiCode loads of cell_base (`tablegrow.find_base_refs`)
    type_refs: tuple[int, ...]
    proof: tuple[str, ...]
    gate_sites: tuple[int, ...] = ()    # compares that gate an indexed read of the cell table
    clamp_sites: tuple[int, ...] = ()   # compares that clamp an index stored for a later read

    @property
    def cell_off(self) -> int:
        return self.cell_base - _GD

    @property
    def type_off(self) -> int:
        return self.type_base - _GD


@memo.content_cached(copy=None)
def _bound_compares(code: bytes, base: int) -> tuple:
    """`(off, after, idx_reg, imm, form)` for every compare of `_compares` followed by an
    unsigned branch: the bound compares."""
    from . import tablegrow
    md = tablegrow._capstone_thumb()
    out = []
    for site in _compares(code, base):
        nxt = (next(md.disasm_lite(code[site[1]:site[1] + 4], base + site[1], 1), None)
               if site[1] + 2 <= len(code) else None)
        if nxt is not None and nxt[2].split(".")[0] in _UNSIGNED_BRANCHES:
            out.append(site)
    return tuple(out)


@memo.content_cached(copy=None)
def _compares(code: bytes, base: int) -> tuple:
    """`(off, after, idx_reg, imm, form)` for every register-against-constant compare decoded
    at a halfword of `code` (a linear pass desyncs across literal pools), whatever follows it:
    `cmp rI,#imm8`, `cmp.w rI,#imm` and `movw rB,#imm ; cmp rI,rB`. `cmp.w` comes from
    `tablegrow._cmp_w_sites` (memoised, incremental across GuiCode edits); the 16-bit `cmp` and
    the `movw` of the register form are prefiltered by their encodings."""
    import numpy as np
    from . import tablegrow
    md = tablegrow._capstone_thumb()

    def one(off: int):
        return next(md.disasm_lite(code[off:off + 4], base + off, 1), None)

    found = []
    for off, size, op_str in tablegrow._cmp_w_sites(code, base):
        m = _REG_IMM.fullmatch(op_str)
        if m:
            found.append((off, off + size, int(m.group(1)), int(m.group(2), 0), "cmp.w"))
    n = (len(code) - 4) // 2
    hw = np.frombuffer(code[:2 * n], dtype="<u2")
    for off in (2 * np.flatnonzero((hw & 0xF800) == 0x2800)).tolist():
        i = one(off)                                   # 16-bit `cmp rI, #imm8`
        if i is not None and i[2] == "cmp":
            m = _REG_IMM.fullmatch(i[3])
            if m:
                found.append((off, off + i[1], int(m.group(1)), int(m.group(2), 0), "cmp"))
    for off in (2 * np.flatnonzero((hw & 0xFBF0) == 0xF240)).tolist():
        if off + 8 > len(code):
            continue
        i = one(off)                                   # `movw rB, #imm ; cmp rI, rB`
        if i is None or i[2] != "movw":
            continue
        m = _REG_IMM.fullmatch(i[3])
        j = one(off + 4)
        if not m or j is None or j[2] not in ("cmp", "cmp.w"):
            continue
        mr = _REG_REG.fullmatch(j[3])
        if mr and int(mr.group(2)) == int(m.group(1)):
            found.append((off, off + 4 + j[1], int(mr.group(1)), int(m.group(2), 0), "movw+cmp"))
    return tuple(sorted(found))


@memo.content_cached(copy=None)
def _gated(code: bytes, base: int, lits: tuple) -> dict:
    """`{T: {imm: ((site, form), ...)}}` for each literal T in `lits` that a bound compare gates
    (step 2 of the module docstring). Only literals with a gating bound appear. Callers must
    not mutate the result (it is cached)."""
    from . import tablegrow, xref
    md = _DecodeCache(tablegrow._capstone_thumb())
    pools = {t: {base + o for o in xref.find_u32(code, t)} for t in lits}
    out: dict = {}
    for off, after, ri, imm, form in _bound_compares(code, base):
        # One walk with no table: a table none of whose literals it loads walks the same states
        # and is not reached (`_reaches_indexed_read_off_base`, `loads_seen`), so only the
        # tables whose literals it loads need their own walk.
        seen: set = set()
        tablegrow._reaches_indexed_read_off_base(md, code, base, after, ri, set(), loads_seen=seen)
        for t in lits:
            if pools[t] & seen and tablegrow._reaches_indexed_read_off_base(
                    md, code, base, after, ri, pools[t]):
                out.setdefault(t, {}).setdefault(imm, []).append((base + off, form))
    return {t: {k: tuple(v) for k, v in b.items()} for t, b in out.items()}


class _Insn:
    __slots__ = ("address", "size", "mnemonic", "op_str")

    def __init__(self, address, size, mnemonic, op_str):
        self.address, self.size, self.mnemonic, self.op_str = address, size, mnemonic, op_str


class _DecodeCache:
    """A capstone handle whose `disasm(code, addr)` (one instruction, as the walks call it) is
    cached by address: every walk over one GuiCode decodes the same few thousand offsets."""

    def __init__(self, md):
        self._md, self._seen = md, {}

    def disasm(self, code: bytes, addr: int):
        key = (addr, bytes(code))
        hit = self._seen.get(key)
        if hit is None:
            hit = self._seen[key] = [_Insn(*i) for i in self._md.disasm_lite(code, addr, 1)]
        return hit


_REGNO = {**{"r%d" % i: i for i in range(13)}, "sb": 9, "sl": 10, "fp": 11, "ip": 12}
_HALF_STEPS = 400                   # as `sramreach._MAX_STEPS`: a literal's longest lifetime
_REG_LIST = re.compile(r"\{([^}]*)\}")
_PC_LIT = re.compile(r"^(\w+), \[pc, #(0x[0-9a-f]+|\d+)\]$")
_MEM = re.compile(r"\[(\w+)(?:, (?:#(-?0x[0-9a-f]+|-?\d+)|(\w+)))?\]$")


@memo.content_cached(copy=None)
def _halfword_moves(code: bytes, base: int, sram_lo: int, sram_hi: int) -> tuple:
    """`(stores, reads)`: every `strh` and `ldrh`/`ldrsh` of GuiCode whose SRAM address a
    straight-line evaluation resolves. `stores` maps a store's address to `{(src_reg, addr)}`;
    `reads` maps an SRAM address to `{(load address, dest_reg, load size)}`.

    The evaluation starts at every literal load of an SRAM word (`sramreach._literal_loads`) and
    follows the registers forward through literal loads, `movw`/`mov #imm`, register copies
    (`mov r3, r2`) and `add` of known values or an immediate (`add.w r2, r2, #0x6c00`). A branch
    within `_BRANCH_DEPTH` hops is taken as well as fallen through (13.6's reader at 0x257FF4
    is reached only through a `blt`); a return or `pop` ends a path, and so does a point where
    no register holds an SRAM value. Each start decodes at most `_HALF_STEPS` instructions. A
    call clobbers r0-r3 and r12; any other write forgets its destination. It is blind to bases
    passed in as parameters, to conditional writes inside an IT block (decoded one at a time,
    they read as unconditional), and to computed offsets, so it credits only what it resolves:
    a store or read it misses is not credited, and the grow then refuses."""
    from . import sramreach
    import numpy as np
    md = _decoder()
    seen: dict = {}
    touched: set = set()

    def ins(addr: int):
        off = addr - base
        touched.add(off)
        hit = seen.get(addr)
        if hit is None:
            hit = seen[addr] = (next(md.disasm_lite(code[off:off + 4], addr, 1), None)
                                if 0 <= off and off + 2 <= len(code) else None)
        return hit

    def regs_of(text: str) -> list:
        return [_REGNO[t] for t in (x.strip() for x in text.split(",")) if t in _REGNO]

    # Each start's result depends only on the bytes it decoded (instructions and literal
    # words, `touched`), so a recent evaluation of the same base is reused for every start none
    # of whose touched offsets lies within 3 bytes before a changed byte: a compile asks again
    # after each GuiCode edit, which changes a few literals and appends at the end.
    affected = None
    prev_starts: dict = {}
    for prev_code, prev_key, prev in reversed(_RECENT_HALF):
        if prev_key == (base, sram_lo, sram_hi):
            common = min(len(code), len(prev_code))
            changed = np.flatnonzero(np.frombuffer(code, np.uint8, common)
                                     != np.frombuffer(prev_code, np.uint8, common)).tolist()
            if len(code) != len(prev_code):
                changed += range(max(0, common - 4), max(len(code), len(prev_code)))
            affected = {o for c in changed for o in range(c - 3, c + 1)}
            prev_starts = prev
            break

    per_start: dict = {}
    for site, rx, value, size in sramreach._literal_loads(code, base):
        if not sram_lo <= value < sram_hi:
            continue
        old = prev_starts.get((site, rx, value, size))
        if old is not None and affected is not None and not (old[0] & affected):
            per_start[(site, rx, value, size)] = old
            continue
        touched = set()
        st: dict = {}
        rd: dict = {}
        work = [(site + size, {rx: value}, 0)]
        budget = _HALF_STEPS
        visited: set = set()
        while work and budget > 0:
            pos, regs, depth = work.pop()
            state = (pos, frozenset(regs.items()))
            if state in visited:
                continue
            visited.add(state)
            budget -= _walk_half(ins, code, base, pos, regs, depth, work, st, rd,
                                 sram_lo, sram_hi, regs_of, budget, touched)
        touched.update(range(site - base, site - base + size))
        per_start[(site, rx, value, size)] = (
            frozenset(touched),
            tuple((k, x) for k, v in st.items() for x in v),
            tuple((k, x) for k, v in rd.items() for x in v))
    _RECENT_HALF.append((code, (base, sram_lo, sram_hi), per_start))
    stores: dict = {}
    reads: dict = {}
    for _touched, st_items, rd_items in per_start.values():
        for k, x in st_items:
            stores.setdefault(k, set()).add(x)
        for k, x in rd_items:
            reads.setdefault(k, set()).add(x)
    return ({k: frozenset(v) for k, v in stores.items()},
            {k: frozenset(v) for k, v in reads.items()})


_RECENT_HALF: deque = deque(maxlen=2)


_COND_BRANCH = re.compile(r"^b(?:eq|ne|cs|cc|hs|lo|mi|pl|vs|vc|hi|ls|ge|lt|gt|le)$")
_BRANCH_DEPTH = 2                   # branch hops followed from one literal load


def _walk_half(ins, code, base, pos, regs, depth, work, stores, reads, sram_lo, sram_hi,
               regs_of, budget, touched) -> int:
    """One straight-line run of `_halfword_moves` from `pos`, of at most `budget` instructions;
    a branch within `_BRANCH_DEPTH` hops queues its target on `work` (a conditional one also
    falls through). Returns the instructions it decoded (at least 1)."""
    steps = 0
    while steps < budget:
        steps += 1
        i = ins(pos)
        if i is None:
            break
        _addr, isize, mn, ops = i
        op = mn.split(".")[0]
        parts = [x.strip() for x in ops.split(",")]
        dst = _REGNO.get(parts[0]) if parts else None
        if op in ("bx", "pop") or op.startswith(("tbb", "tbh")):
            break
        if op == "b" or _COND_BRANCH.match(op) or op in ("cbz", "cbnz"):
            m = re.search(r"#(0x[0-9a-f]+)$", ops)
            if m and depth < _BRANCH_DEPTH:
                work.append((int(m.group(1), 16), dict(regs), depth + 1))
            if op == "b":
                break
            pos += isize
            continue
        if op in ("bl", "blx"):
            for r in (0, 1, 2, 3, 12):
                regs.pop(r, None)
        elif op in ("ldrh", "ldrsh", "strh"):
            m = _MEM.search(ops)
            where = None
            if m and _REGNO.get(m.group(1)) in regs:
                b = regs[_REGNO[m.group(1)]]
                if m.group(3) is None:
                    where = b + (int(m.group(2), 0) if m.group(2) else 0)
                elif _REGNO.get(m.group(3)) in regs:
                    where = b + regs[_REGNO[m.group(3)]]
            if where is not None and dst is not None:
                if op == "strh":
                    stores.setdefault(pos, set()).add((dst, where))
                else:
                    reads.setdefault(where, set()).add((pos, dst, isize))
            if "!" in ops or "], #" in ops:
                m2 = re.search(r"\[(\w+)", ops)
                if m2:
                    regs.pop(_REGNO.get(m2.group(1)), None)
            if op != "strh":
                regs.pop(dst, None)
        elif op.startswith(("ldm", "stm", "push")):
            m = _REG_LIST.search(ops)
            if op.startswith("ldm") and m:
                for r in regs_of(m.group(1)):
                    regs.pop(r, None)
            if "!" in ops:
                regs.pop(dst, None)
        elif op.startswith(("str", "cmp", "cmn", "tst", "teq", "it", "nop")):
            pass
        elif dst is not None:
            new = None
            lit = _PC_LIT.match(ops)
            if op == "ldr" and lit:
                at = ((pos + 4) & ~3) + int(lit.group(2), 0) - base
                touched.update(range(at, at + 4))
                if 0 <= at and at + 4 <= len(code):
                    new = struct.unpack_from("<I", code, at)[0]
            elif op in ("movw", "mov", "movs") and len(parts) == 2:
                if parts[1].startswith("#"):
                    new = int(parts[1][1:], 0)
                elif _REGNO.get(parts[1]) in regs:
                    new = regs[_REGNO[parts[1]]]
            elif op in ("add", "adds", "addw"):
                terms = parts[1:] if len(parts) == 3 else parts[:2] if len(parts) == 2 else []
                vals = []
                for t in terms:
                    if t.startswith("#"):
                        vals.append(int(t[1:], 0))
                    elif _REGNO.get(t) in regs:
                        vals.append(regs[_REGNO[t]])
                if terms and len(vals) == len(terms):
                    new = sum(vals) & 0xFFFFFFFF
            if op.startswith(("umull", "smull", "umlal", "smlal", "ldrd", "ldrexd")):
                regs.pop(_REGNO.get(parts[1]) if len(parts) > 1 else None, None)
            if new is None:
                regs.pop(dst, None)
            else:
                regs[dst] = new
        if not any(sram_lo <= v < sram_hi for v in regs.values()):
            break
        pos += isize
    return max(steps, 1)


@memo.content_cached(copy=None)
def _clamps(code: bytes, base: int, cell: int, bound: int, gates: tuple, sram_lo: int,
            sram_hi: int) -> tuple:
    """`(site, form, store, addr, ((reader, read), ...))` for every clamp site of the cell table:
    a bound compare (`_bound_compares`, imm == `bound`, not a gate site) whose index register
    the instruction just before it stores with `strh` to SRAM `addr`, where some `ldrh` of
    `addr` (`reader`) loads a register that indexes the cell table (`read`, the indexed `ldr`
    off a base literal-loaded with `cell`, `tablegrow._reaches_indexed_read_off_base`). A
    compare with no such reader is not credited."""
    from . import tablegrow, xref
    stores, reads = _halfword_moves(code, base, sram_lo, sram_hi)
    md = _DecodeCache(tablegrow._capstone_thumb())
    pools = {base + o for o in xref.find_u32(code, cell)}
    out = []
    for off, _after, ri, imm, form in _bound_compares(code, base):
        site = base + off
        if imm != bound or site in gates:
            continue
        for st in range(site - 8, site):
            for reg, addr in sorted(stores.get(st, ())):
                if reg != ri:
                    continue
                found = []
                for reader, rd, rsize in sorted(reads.get(addr, ())):
                    hits: list = []
                    if tablegrow._reaches_indexed_read_off_base(
                            md, code, base, reader - base + rsize, rd, pools, hits=hits):
                        found.append((reader, hits[0]))
                if found:
                    out.append((site, form, st, addr, tuple(found)))
    return tuple(sorted(set(out)))


def _decoder():
    from . import tablegrow
    return tablegrow._capstone_thumb()


def _guidata_literals(code: bytes, base: int, span: int) -> tuple:
    """Every distinct 4-aligned GuiCode word that maps into GuiData."""
    lead = (-base) % 4
    body = code[lead:lead + ((len(code) - lead) & ~3)]
    return tuple(sorted({w for (w,) in struct.iter_unpack("<I", body) if _GD <= w < _GD + span}))


@memo.content_cached(copy=None)
def _locate(data: bytes, sram_lo: int, sram_hi: int) -> CellTables:
    """`locate` before its cross-check; the SRAM bounds are arguments so the cache never reads
    a module constant."""
    from . import tablegrow
    gs, ge = guidata.guidata_extent(data)
    code, base = tablegrow._guicode_slice(data)
    code = bytes(code)
    gated = _gated(code, base, _guidata_literals(code, base, ge - gs))
    if not gated:
        raise CellTableError("CELL_TABLE_NO_BOUND",
                             "no unsigned bound compare in GuiCode gates an indexed read of any "
                             "GuiData table; the cell table cannot be located")

    def pointer_shaped(t: int, rows: int) -> bool:
        at = gs + t - _GD
        if at + 4 * rows > ge:
            return False
        words = struct.unpack_from(f"<{rows}I", data, at)
        real = sum(1 for w in words if sram_lo <= w < sram_hi)
        return real >= rows // 2 and all(w == 0 or sram_lo <= w < sram_hi for w in words)

    cells = [t for t, b in gated.items() if any(pointer_shaped(t, imm + 1) for imm in b)]
    if len(cells) != 1:
        raise CellTableError(
            "CELL_TABLE_CANDIDATES",
            "%d GuiData tables gated by a bound compare hold SRAM pointers (%s); expected exactly "
            "one among %s" % (len(cells), ", ".join("%#x" % t for t in cells),
                              ", ".join("%#x" % t for t in gated)))
    cell = cells[0]
    bounds = gated[cell]
    if len(bounds) != 1:
        raise CellTableError(
            "CELL_TABLE_BOUND_AMBIGUOUS",
            "cell table %#x is gated by %d distinct bounds: %s" % (
                cell, len(bounds),
                ", ".join("%#x (%d sites)" % (k, len(v)) for k, v in sorted(bounds.items()))))
    bound, sites = next(iter(bounds.items()))
    rows = bound + 1
    span4 = (rows + 3) & ~3
    types = [t for t, b in gated.items() if t != cell and bound in b]
    if len(types) > 1:
        types = [t for t in types if t + span4 == cell]
    if len(types) != 1:
        raise CellTableError(
            "TYPE_TABLE_CANDIDATES",
            "%d GuiData tables share the cell table's bound %#x with it (%s); expected exactly one"
            % (len(types), bound, ", ".join("%#x" % t for t in types)))
    typ = types[0]
    if typ + span4 != cell:
        raise CellTableError(
            "CELL_TABLE_ADJACENCY",
            "type table %#x + align4(%d) = %#x is not the cell table's base %#x"
            % (typ, rows, typ + span4, cell))
    gates = tuple(sorted(s for s, _f in sites))
    clamps = _clamps(code, base, cell, bound, gates, sram_lo, sram_hi)
    forms = sorted({f for _s, f in sites} | {c[1] for c in clamps})
    cell_refs = tuple(tablegrow.find_base_refs(data, cell))
    type_refs = tuple(tablegrow.find_base_refs(data, typ))
    proof = (
        "bound %#x (%s) gates the cell table at %d sites: %s"
        % (bound, "+".join(sorted({f for _s, f in sites})), len(sites),
           ", ".join("%#x" % s for s, _f in sites)),
        *("clamp %#x (%s): strh at %#x stores the index to %#x; %s"
          % (site, form, st, addr, "; ".join(
              "ldrh at %#x reads it back and indexes the cell table at %#x" % rr
              for rr in readers))
          for site, form, st, addr, readers in clamps),
        "type table %#x shares the bound at %d sites: %s"
        % (typ, len(gated[typ][bound]), ", ".join("%#x" % s for s, _f in gated[typ][bound])),
        "%#x + align4(%d) == %#x" % (typ, rows, cell),
        "cell table loaded at %s; type table at %s"
        % (", ".join("%#x" % r for r in cell_refs), ", ".join("%#x" % r for r in type_refs)),
    )
    clamp_sites = tuple(c[0] for c in clamps)
    return CellTables(cell, typ, rows, tuple(sorted(gates + clamp_sites)), "+".join(forms),
                      cell_refs, type_refs, proof, gates, clamp_sites)


def locate(data: bytes) -> CellTables:
    """The live cell and type tables of `data`, from GuiCode's bound compares (module
    docstring), checked against `guidata.variable_table_literals` outside the cache (it reads
    a module constant). Raises `CellTableError` (a `guidata.VariableTableNotFound`) rather than
    guess. Needs the `[full]` extra (capstone and numpy)."""
    import capstone  # noqa: F401 -- before the cache, so a bare install fails the same way,
    import numpy  # noqa: F401    -- with the ModuleNotFoundError the CLI reports as [full]
    found = _locate(data, guidata.SRAM_LOW, guidata.SRAM_HIGH)
    lits = guidata.variable_table_literals(data)
    if (lits.dptr_off, lits.type_off) != (found.cell_off, found.type_off):
        raise CellTableError(
            "TYPE_TABLE_DISAGREES",
            "the bound compares locate cell %#x / type %#x; the GuiCode literal pair names "
            "%#x / %#x" % (found.cell_off, found.type_off, lits.dptr_off, lits.type_off))
    return found


def cell_table_off(data: bytes) -> int:
    return locate(data).cell_off


def type_table_off(data: bytes) -> int:
    return locate(data).type_off


def row_count(data: bytes) -> int:
    return locate(data).rows



def stale_pair(data: bytes) -> int | None:
    """The GuiData offset of a cell table GuiCode no longer reads, or None: the sign that this
    image's display-cell table was grown (`tablegrow.grow_indexed_tables` copies the pair to the
    end of GuiData and leaves the old pair in place, unread).

    A stale pair is accepted only when, below the located pair, all of these hold: the live cell
    table's first four rows repeat at a 4-aligned offset C (a grow copies old rows verbatim); the
    live type table's first 16 bytes repeat at a 4-aligned T with 0 < C - T <= align4(rows)
    (the stock adjacency, type table ending where its cell table starts); and no GuiCode literal
    loads either base (`tablegrow.find_base_refs`). It answers only "grown or not"; the old pair's
    row count is not read from it. Measured 2026-11-08: +0x44BC8 on every grown reference build;
    None on stock 11.23/13.6/14.7. Refuses as `locate` does."""
    locate(data)
    return _stale_pair(data, guidata.SRAM_LOW, guidata.SRAM_HIGH)


@memo.content_cached(copy=None)
def _stale_pair(data: bytes, sram_lo: int, sram_hi: int) -> int | None:
    from . import tablegrow
    ct = _locate(data, sram_lo, sram_hi)
    gs, _ = guidata.guidata_extent(data)
    live, live_t = gs + ct.cell_off, gs + ct.type_off
    cell16, type16 = data[live:live + 16], data[live_t:live_t + 16]
    span4 = (ct.rows + 3) & ~3
    at = gs
    while True:
        at = data.find(cell16, at, min(live, live_t))
        if at < 0:
            return None
        if (at - gs) % 4 == 0:
            t = data.rfind(type16, max(gs, at - span4), at)
            if (t >= 0 and (t - gs) % 4 == 0
                    and not tablegrow.find_base_refs(data, _GD + at - gs)
                    and not tablegrow.find_base_refs(data, _GD + t - gs)):
                return at - gs
        at += 1
