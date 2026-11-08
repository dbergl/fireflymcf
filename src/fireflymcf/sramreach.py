"""Which GuiCode-working-area SRAM is reached by ANY form this project can see — and so which
words are genuinely free for an authored routine's scratch or display cell.

Why this exists (2026-09-04). `followchannel._guicode_references` is the safety scan behind
every scratch-cell allocation (`scan_display_cell`, `_scan_latch_cell`, the pumpauto cells). It
checks raw pool words and T1 `ldr rX,[pc]` targets, and its docstring argued that was complete
because "13.6 contains no movw/movt". That premise is false — 13.6 has hundreds of `movw`, used as
16-bit OFFSETS off a base register (`movw r3,#0x6bb4; ldr r3,[r2,r3]` with r2 = the render state
at 0x200301B0). So a cell can be reached with no pool word naming it: base + offset. That form is
what this module traces, per base, following the register until it is redefined.

What it does NOT see, stated so the limit is not forgotten (the standing "argument from absence"
rule): a base that is a function PARAMETER (r0-r3 at entry) rather than a literal, pointer walks
with writeback, and register-indexed tables whose index is computed. The register-indexed bases
found on 13.6 are reported (`Reach.indexed_bases`) and bounded by hand in GUICODE.md: the render
state (+12*depth, +(nibble+0x2d)*4), the digit buffer 0x200371D1 (+strlen), and two tables above
the cell area (0x2003A8B8 at stride 8, 0x2003A9A8 at stride 4) that index upward only.

**Located, not pinned (2026-11-08).** The render state is the one literal SRAM base GuiCode
addresses through `movw` offsets (`locate_render_state`; 13.6 0x200301B0, 14.7 0x200301B4, 11.23
0x20030160, measured 2026-11-07). It ends at its largest `movw` offset + 4. A register-indexed
base inside it (the digit buffer, base + 0x7021 on 13.6 and 14.7) is a struct field, bounded by
the struct's end -- an inference: GUICODE.md bounds 13.6's buffer at 0x40 by hand, inside a
struct that runs further. Any other indexed base reaches everything above it. The free cell area
(`cell_area`) runs from the struct's end to the lowest indexed base above it, and every cell the
display-cell table names must lie in it, or the module refuses (`SramReachError`).
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field

from . import celltables, container, memo, status_map, theme

SRAM_LOW, SRAM_HIGH = status_map.SRAM_LOW, status_map.SRAM_HIGH
_MAX_STEPS = 400                    # longest observed lifetime is 246 instructions on 13.6


class SramReachError(ValueError):
    """The render state or the cell area does not prove out on this image. `code` is one of
    RENDER_STATE_BASES (not exactly one literal SRAM base is addressed through `movw` offsets),
    CELL_AREA_NO_CEILING (no register-indexed base lies above the render state to bound the
    area) or CELL_OUTSIDE_AREA (a display-cell table row names SRAM outside the area).
    RENDER_STATE_BASES also refuses an image where an authored routine addresses its own
    literal SRAM base through `movw` offsets: that is a second such base."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class RenderState:
    """The renderer's state struct: the one SRAM base GuiCode reaches as `movw rZ,#off ;
    ldr [rB,rZ]`. `end` is base + the largest such offset + 4; `indexed_fields` are the
    register-indexed bases in [base, end), base included (13.6: the base itself, indexed
    +12*depth and +(nibble+0x2d)*4, and the digit buffer 0x200371D1)."""
    base: int
    end: int
    movw_sites: tuple[int, ...]       # GuiCode addresses of the [rB, rZ] accesses, rZ from movw
    indexed_fields: tuple[int, ...]


@dataclass(frozen=True)
class _Trace:
    """What the per-base trace of GuiCode saw; a function of the GuiCode bytes alone."""
    traced: frozenset
    base_offset: dict                 # addr -> frozenset {(site, width)}
    indexed: dict                     # base -> frozenset {sites} of [rX, rZ] with unknown rZ
    movw: dict                        # base -> tuple ((site, movw offset), ...)


@dataclass
class Reach:
    """Every working-area address GuiCode can reach, by form."""
    raw_words: set = field(default_factory=set)      # u32 pool words / table entries (GuiCode + LCD)
    traced: set = field(default_factory=set)         # pool words whose loads were traced (width known)
    base_offset: dict = field(default_factory=dict)  # addr -> {(site, width)} via [rX,#imm] / movw
    indexed_bases: dict = field(default_factory=dict)  # base -> {sites} of [rX, rZ] with unknown rZ
    state_span: tuple = (0, 0)                       # render state: base .. base+max movw offset+4
    cells: set = field(default_factory=set)          # +0x44BC8 cell-table entries

    cell_width: dict = field(default_factory=dict)   # cell addr -> bytes its variable type reads
    poisoned: list = field(default_factory=list)     # (lo, hi) spans an unbounded indexed table may reach

    def referenced(self, addr: int, width: int = 1) -> bool:
        """True if any byte of [addr, addr+width) is reached by any form above. A pool word is
        a pointer whose use width is unknown, so it is taken as 4 bytes; a base+offset access
        has the width of its instruction; a cell has the width of its variable type."""
        return bool(self.forms(addr, width, first=True))

    def forms(self, addr: int, width: int = 1, first: bool = False) -> list:
        """[(form, detail)] for every reach into [addr, addr+width)."""
        out = []
        lo, hi = addr, addr + width
        for w in self._near(self.raw_words, addr, width, 4):
            # a traced pointer's use width is in base_offset; an untraced one is taken as 4
            if w in self.traced:
                if w < hi and w + max((n for _, n in self.base_offset.get(w, ())), default=1) > lo:
                    out.append(("raw_word", w))
            else:
                out.append(("raw_word", w))
            if out and first:
                return out
        for w in self._near(self.base_offset, addr, width, 8):
            for site, n in self.base_offset[w]:
                if w < hi and w + n > lo:
                    out.append(("base_offset", site))
                    if first:
                        return out
        if self.state_span[0] < hi and self.state_span[1] > lo:
            out.append(("state_struct", self.state_span))
            if first:
                return out
        for plo, phi in self.poisoned:
            if plo < hi and phi > lo:
                out.append(("indexed_table", (plo, phi)))
                if first:
                    return out
        for c in self._near(self.cells, addr, width, 8):
            if c < hi and c + self.cell_width.get(c, 4) > lo:
                out.append(("cell_table", c))
                if first:
                    return out
        return out

    @staticmethod
    def _near(coll, addr: int, width: int, back: int):
        return [w for w in range(addr - back + 1, addr + width) if w in coll]


def _lcd_words(data: bytes) -> set:
    for section in container.parse_container(data).sections:
        if "LCD Firmware" in (section.name or ""):
            body = data[section.offset:section.offset + section.length]
            return {w for w in _words(body) if SRAM_LOW <= w < SRAM_HIGH}
    return set()


def _words(body: bytes):
    for i in range(0, len(body) - 3, 2):
        yield struct.unpack_from("<I", body, i)[0]


def _literal_loads(body: bytes, base: int):
    """(site, rX, value, size) for every T1 `ldr rX,[pc,#imm8]` and T2 `ldr.w rX,[pc,#imm12]`."""
    n = len(body)
    for a in range(0, n - 1, 2):
        hw = body[a] | body[a + 1] << 8
        if (hw & 0xF800) == 0x4800:
            off = ((a + 4) & ~3) + (hw & 0xFF) * 4
            if off + 4 <= n:
                yield base + a, (hw >> 8) & 7, struct.unpack_from("<I", body, off)[0], 2
        elif hw in (0xF8DF, 0xF85F) and a + 3 < n:
            hw2 = body[a + 2] | body[a + 3] << 8
            imm = hw2 & 0xFFF
            off = ((a + 4) & ~3) + (imm if hw == 0xF8DF else -imm)
            if 0 <= off and off + 4 <= n:
                yield base + a, (hw2 >> 12) & 15, struct.unpack_from("<I", body, off)[0], 4


_WIDTH = {"b": 1, "sb": 1, "h": 2, "sh": 2, "": 4, "d": 8}
_TYPE_WIDTH = {0: 1, 1: 1, 2: 1, 3: 2, 4: 2, 5: 4, 6: 4, 7: 4, 8: 8, 9: 1, 10: 4}   # 9 reads nothing, but is named


def _access_width(mnemonic: str) -> int:
    m = re.fullmatch(r"(?:v)?(?:ldr|str)(b|sb|h|sh|d)?(?:\.w)?", mnemonic)
    if m:
        return _WIDTH[m.group(1) or ""]
    if mnemonic == "tbb":
        return 1
    if mnemonic == "tbh":
        return 2
    return 4


def _cell_widths(data: bytes) -> dict:
    """cell addr -> bytes read, from the variable-type table (stock `+0x4492C`, FUN_00251E38;
    resolved live via `celltables.type_table_off` -- a fixed offset reads the dead copy on a
    relocated table, e.g. the heater-status builds."""
    from . import guidata
    gs, _ = guidata.guidata_extent(data)
    cells = status_map.cell_table(data)
    type_off = celltables.type_table_off(data)
    types = data[gs + type_off: gs + type_off + len(cells)]
    out: dict = {}
    for c, t in zip(cells, types):
        out[c] = max(out.get(c, 0), _TYPE_WIDTH.get(t, 4))
    return out


_MEM_IMM = r"\[r{r}(?:, #(-?0x[0-9a-f]+|-?\d+))?\]"
_MEM_REG = r"\[r{r}, r(\d+)(?:, lsl #(\d))?\]"
_WRITEBACK = r"\[r{r}\], #|\[r{r}, #-?\w+\]!"


@memo.content_cached(copy=None)
def _trace(code: bytes, base: int, sram_lo: int, sram_hi: int) -> _Trace:
    """Trace every literal-loaded working-area base of GuiCode `code` (loaded at `base`) through
    the instructions that use it, until the register is redefined. Keyed on the GuiCode bytes,
    so a GuiData-only edit reuses it; the SRAM bounds are arguments, never module reads."""
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)

    def ins_at(addr: int):
        off = addr - base
        if off < 0 or off + 2 > len(code):
            return None
        return next(md.disasm(code[off:off + 4], addr, 1), None)

    traced: set = set()
    base_offset: dict = {}
    indexed: dict = {}
    movw_access: dict = {}
    for site, rx, value, size in _literal_loads(code, base):
        if not (sram_lo <= value < sram_hi):
            continue
        traced.add(value)
        mem_imm = re.compile(_MEM_IMM.format(r=rx))
        mem_reg = re.compile(_MEM_REG.format(r=rx))
        writeback = re.compile(_WRITEBACK.format(r=rx))
        movw: dict = {}
        pos = site + size
        for _ in range(_MAX_STEPS):
            i = ins_at(pos)
            if i is None:
                break
            if i.mnemonic == "movw":
                m = re.fullmatch(r"r(\d+), #0x([0-9a-f]+)", i.op_str)
                if m:
                    movw[int(m.group(1))] = int(m.group(2), 16)
            width = _access_width(i.mnemonic)
            for m in mem_imm.finditer(i.op_str):
                imm = int(m.group(1), 0) if m.group(1) else 0
                base_offset.setdefault(value + imm, set()).add((pos, width))
            m = mem_reg.search(i.op_str)
            if m:
                rz = int(m.group(1))
                if rz in movw:
                    base_offset.setdefault(value + movw[rz], set()).add((pos, width))
                    movw_access.setdefault(value, set()).add((pos, movw[rz]))
                else:
                    indexed.setdefault(value, set()).add(pos)
            if writeback.search(i.op_str):
                indexed.setdefault(value, set()).add(pos)
            # redefinition of rX ends the lifetime
            dst = re.match(r"r(\d+)", i.op_str)
            writes = (dst is not None and int(dst.group(1)) == rx
                      and not i.mnemonic.startswith(("str", "cmp", "tst", "teq", "cmn", "push")))
            if i.mnemonic in ("pop", "pop.w") and f"r{rx}" in i.op_str:
                writes = True
            if i.mnemonic in ("bl", "blx") and rx <= 3:
                writes = True                       # caller-saved
            if writes or i.mnemonic in ("pop", "pop.w", "bx", "b", "b.w"):
                break
            pos += i.size
    return _Trace(frozenset(traced),
                  {a: frozenset(v) for a, v in base_offset.items()},
                  {b: frozenset(v) for b, v in indexed.items()},
                  {b: tuple(sorted(v)) for b, v in movw_access.items()})


def _guicode_trace(data: bytes) -> _Trace:
    sec = theme.guicode_section(data)
    return _trace(bytes(data[sec.offset:sec.offset + sec.length]), theme.guicode_base(sec),
                  SRAM_LOW, SRAM_HIGH)


def _render_state(t: _Trace) -> RenderState:
    bases = {b: sites for b, sites in t.movw.items() if sites}
    if len(bases) != 1:
        raise SramReachError(
            "RENDER_STATE_BASES",
            "%d literal SRAM bases are addressed through movw offsets (%s); the render state is "
            "the one such base, so it cannot be located" % (
                len(bases), ", ".join("%#x (%d sites)" % (b, len(v))
                                      for b, v in sorted(bases.items())) or "none"))
    (b, accesses), = bases.items()
    end = b + max(off for _site, off in accesses) + 4
    return RenderState(b, end, tuple(sorted({site for site, _off in accesses})),
                       tuple(sorted(x for x in t.indexed if b <= x < end)))


def locate_render_state(data: bytes) -> RenderState:
    """The render state of `data`, from GuiCode alone (module docstring). Raises
    `SramReachError` (RENDER_STATE_BASES) unless exactly one literal SRAM base is addressed
    through `movw` offsets."""
    return _render_state(_guicode_trace(data))


def cell_area(data: bytes) -> tuple[int, int]:
    """`(lo, hi)`: the SRAM between the render state's end and the lowest register-indexed base
    at or above it, where every display cell lives and a fresh one may be minted. Refuses
    (`SramReachError`) when no indexed base lies above the struct (CELL_AREA_NO_CEILING) or a
    cell-table row names SRAM outside the area (CELL_OUTSIDE_AREA)."""
    t = _guicode_trace(data)
    rs = _render_state(t)
    above = [b for b in t.indexed if b >= rs.end]
    if not above:
        raise SramReachError(
            "CELL_AREA_NO_CEILING",
            "no register-indexed SRAM base lies above the render state (%#x..%#x), so nothing "
            "bounds the cell area from above" % (rs.base, rs.end))
    lo, hi = rs.end, min(above)
    from . import guidata
    ct = celltables.locate(data)
    gs, _ = guidata.guidata_extent(data)
    rows = struct.unpack_from("<%dI" % ct.rows, data, gs + ct.cell_off)
    outside = [(i, c) for i, c in enumerate(rows) if c and not lo <= c < hi]
    if outside:
        raise SramReachError(
            "CELL_OUTSIDE_AREA",
            "%d display-cell table rows name SRAM outside the cell area [%#x, %#x): %s" % (
                len(outside), lo, hi,
                ", ".join("row %d -> %#x" % (i, c) for i, c in outside[:8])))
    return lo, hi


def reach(data: bytes) -> Reach:
    """Every working-area address `data` reaches, by form (`Reach`). Raises `SramReachError`
    when the render state cannot be located."""
    return _reach(data, SRAM_LOW, SRAM_HIGH)


@memo.content_cached(copy=lambda r: r)
def _reach(data: bytes, sram_lo: int, sram_hi: int) -> Reach:
    sec = theme.guicode_section(data)
    body = bytes(data[sec.offset:sec.offset + sec.length])
    t = _trace(body, theme.guicode_base(sec), sram_lo, sram_hi)
    rs = _render_state(t)

    out = Reach()
    out.raw_words = {w for w in _words(body) if sram_lo <= w < sram_hi} | _lcd_words(data)
    out.cells = {c for c in status_map.cell_table(data) if sram_lo <= c < sram_hi}
    out.cell_width = _cell_widths(data)
    out.traced = set(t.traced)
    out.base_offset = {a: set(v) for a, v in t.base_offset.items()}
    out.indexed_bases = {b: set(v) for b, v in t.indexed.items()}
    out.state_span = (rs.base, rs.end)
    # An indexed table whose index this trace cannot bound reaches, as far as the file can say,
    # everything above its base. A base inside the render state is a field of it (the digit
    # buffer), bounded by the struct; the struct's own base is its state span.
    for b in out.indexed_bases:
        if b == rs.base:
            continue
        out.poisoned.append((b, rs.end) if rs.base <= b < rs.end else (b, sram_hi))
    return out


def is_referenced(data: bytes, addr: int, width: int = 1) -> bool:
    return reach(data).referenced(addr, width)


def reaching_forms(data: bytes, addr: int, width: int = 1) -> dict:
    """Which forms reach [addr, addr+width): a report for a candidate cell. Five keys:
    `raw_word`, `base_offset`, `state_struct` (bool), `indexed_table`, `cell_table`."""
    r = reach(data)
    out = {"raw_word": [], "base_offset": [], "state_struct": False, "indexed_table": [],
           "cell_table": []}
    for form, detail in r.forms(addr, width):
        if form == "state_struct":
            out[form] = True
        elif form == "indexed_table":
            lo, hi = detail
            out[form].append("0x%X-0x%X" % (lo, hi))
        else:
            out[form].append(hex(detail))
    return {k: (sorted(set(v)) if isinstance(v, list) else v) for k, v in out.items()}


def free_words(data: bytes, lo: int | None = None, hi: int | None = None) -> list:
    """4-aligned words in [lo, hi) with all four bytes unreached by every form traced. The
    window defaults to the located `cell_area`.

    Candidates for a fresh u16/u32 display or scratch cell (`statusbind.index_cell` a spare index
    at one). Unreferenced by every form this module can see is NOT "unused": see the module
    docstring's limits, and the standing rule that power-on contents are garbage (raminit).
    """
    lo, hi = _window(data, lo, hi)
    r = reach(data)
    return [a for a in range((lo + 3) & ~3, hi - 3, 4) if not r.referenced(a, 4)]


def free_runs(data: bytes, min_bytes: int = 8, lo: int | None = None,
              hi: int | None = None) -> list:
    """(start, length) of every run of >= `min_bytes` unreached bytes in [lo, hi), longest first.
    A long hole is a better candidate than a word squeezed between two byte cells: a mis-typed
    neighbour cannot explain 30 free bytes. The window defaults to the located `cell_area`."""
    lo, hi = _window(data, lo, hi)
    r = reach(data)
    runs = []
    start = None
    for a in range(lo, hi + 1):
        free = a < hi and not r.referenced(a, 1)
        if free and start is None:
            start = a
        elif not free and start is not None:
            if a - start >= min_bytes:
                runs.append((start, a - start))
            start = None
    return sorted(runs, key=lambda t: (-t[1], t[0]))


def _window(data: bytes, lo: int | None, hi: int | None) -> tuple[int, int]:
    if lo is None or hi is None:
        alo, ahi = cell_area(data)
        lo = alo if lo is None else lo
        hi = ahi if hi is None else hi
    return lo, hi
