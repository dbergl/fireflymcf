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

Measured 2026-11-07: 13.6 cell 0x44BC8 / type 0x4492C / 665 rows (`cmp.w r0,#0x298`, 8 sites);
14.7 0x42B74 / 0x42978 / 505 (`cmp.w #0x1F8`); 11.23 0x44B60 / 0x4492C / 562 (`movw r3,#0x231 ;
cmp r0,r3`); and the relocated pair on every grown reference build.
"""
from __future__ import annotations

import re
import struct
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
    bound_sites: tuple[int, ...]    # runtime addresses of the bound compares gating the cell table
    bound_form: str                 # "cmp", "cmp.w" or "movw+cmp" ("+"-joined if mixed)
    cell_refs: tuple[int, ...]      # GuiCode loads of cell_base (`tablegrow.find_base_refs`)
    type_refs: tuple[int, ...]
    proof: tuple[str, ...]

    @property
    def cell_off(self) -> int:
        return self.cell_base - _GD

    @property
    def type_off(self) -> int:
        return self.type_base - _GD


@memo.content_cached(copy=None)
def _bound_compares(code: bytes, base: int) -> tuple:
    """`(off, after, idx_reg, imm, form)` for every bound compare decoded at a halfword of
    `code` (a linear pass desyncs across literal pools) and followed by an unsigned branch.
    `cmp.w` comes from `tablegrow._cmp_w_sites` (memoised, incremental across GuiCode edits);
    the 16-bit `cmp` and the `movw` of the register form are prefiltered by their encodings."""
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
    out = []
    for site in sorted(found):
        nxt = one(site[1]) if site[1] + 2 <= len(code) else None
        if nxt is not None and nxt[2].split(".")[0] in _UNSIGNED_BRANCHES:
            out.append(site)
    return tuple(out)


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
    forms = sorted({f for _s, f in sites})
    cell_refs = tuple(tablegrow.find_base_refs(data, cell))
    type_refs = tuple(tablegrow.find_base_refs(data, typ))
    proof = (
        "bound %#x (%s) gates the cell table at %d sites: %s"
        % (bound, "+".join(forms), len(sites), ", ".join("%#x" % s for s, _f in sites)),
        "type table %#x shares the bound at %d sites: %s"
        % (typ, len(gated[typ][bound]), ", ".join("%#x" % s for s, _f in gated[typ][bound])),
        "%#x + align4(%d) == %#x" % (typ, rows, cell),
        "cell table loaded at %s; type table at %s"
        % (", ".join("%#x" % r for r in cell_refs), ", ".join("%#x" % r for r in type_refs)),
    )
    return CellTables(cell, typ, rows, tuple(sorted(s for s, _f in sites)), "+".join(forms),
                      cell_refs, type_refs, proof)


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
