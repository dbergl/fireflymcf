"""`fireflymcf.tablegrow` -- the image-modifying half: relocate, grow, repoint,
rebump, reseal.

Grows the display-cell table set (the u32 cell/colour table at runtime `0x90044BC8` and its
parallel u8 type table at `0x9004492C`) by appending grown copies to the end of GuiData,
repointing every GuiCode base literal to the new home, bumping every GuiCode index bound
from `old_len-1` to `new_len-1`, and resealing. The expected patch surfaces (7 + 4 base
literals, 14 bound sites) are the byte-derived oracle established by Task 2's read-only
scan; this task rewrites them.
"""
from __future__ import annotations

import struct

import capstone
import pytest

from fireflymcf import coverage, guidata, tablegrow, theme


CELL_TABLE_BASE = 0x90044BC8   # u32 cell/colour table
TYPE_TABLE_BASE = 0x9004492C   # parallel u8 type table
GUIDATA_MAP_BASE = 0x90000000

OLD_LEN = 665
EXTRA = 8
EXPECT_BASE_REFS = {CELL_TABLE_BASE: 7, TYPE_TABLE_BASE: 4}
EXPECT_BOUND_SITES = 14
OLD_BOUND = 0x298  # == OLD_LEN - 1


def _tables():
    return [tablegrow.IndexedTable(CELL_TABLE_BASE, 4),
            tablegrow.IndexedTable(TYPE_TABLE_BASE, 1)]


def _smallest_encodable_new_len(need: int) -> int:
    """The `new_len` `grow_indexed_tables` should pick: smallest `>= need` whose bound
    `new_len-1` is an encodable Thumb-2 modified immediate. Derived independently here from
    the public `thumb_expand_imm_encode`, so the test confirms the selection rule rather
    than trusting the implementation's own choice."""
    new_len = need
    while tablegrow.thumb_expand_imm_encode(new_len - 1) is None:
        new_len += 1
    return new_len


def _read_u32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


# --------------------------------------------------------------------------------------
# thumb_expand_imm_encode: invert ThumbExpandImm -> imm12 (or None)
# --------------------------------------------------------------------------------------

def test_thumb_expand_imm_encode_known_values():
    assert tablegrow.thumb_expand_imm_encode(0x298) == 0xF26
    assert tablegrow.thumb_expand_imm_encode(0x300) == 0xF40


def test_thumb_expand_imm_encode_rejects_non_encodable():
    assert tablegrow.thumb_expand_imm_encode(0x123) is None


# --------------------------------------------------------------------------------------
# grow_indexed_tables: the full relocate/grow/repoint/rebump/reseal
# --------------------------------------------------------------------------------------

def test_grow_preserves_all_and_adds(mcf_136):
    out, new_indices, edits = tablegrow.grow_indexed_tables(
        mcf_136, _tables(), OLD_LEN, EXTRA,
        expect_base_refs=EXPECT_BASE_REFS, expect_bound_sites=EXPECT_BOUND_SITES)

    # Returned new indices are exactly the requested range.
    assert new_indices == list(range(OLD_LEN, OLD_LEN + EXTRA))
    assert edits  # a non-empty audit trail of what changed

    new_len = _smallest_encodable_new_len(OLD_LEN + EXTRA)
    new_bound = new_len - 1
    span = _guidata_span(mcf_136)
    # The new home of the two tables in the grown image (contiguous, cell then type).
    gs, _ge = guidata.guidata_extent(out)
    cell_new_base = GUIDATA_MAP_BASE + (span - 4)
    type_new_base = cell_new_base + new_len * 4
    cell_new_off = gs + (cell_new_base - GUIDATA_MAP_BASE)
    type_new_off = gs + (type_new_base - GUIDATA_MAP_BASE)

    # (a) Every base literal now equals the NEW base for its table, at the same runtime
    #     addresses Task 2's scan found; and no literal still points at the old base.
    assert tablegrow.find_base_refs(out, cell_new_base) == \
        tablegrow.find_base_refs(mcf_136, CELL_TABLE_BASE)
    assert tablegrow.find_base_refs(out, type_new_base) == \
        tablegrow.find_base_refs(mcf_136, TYPE_TABLE_BASE)
    assert tablegrow.find_base_refs(out, CELL_TABLE_BASE) == []
    assert tablegrow.find_base_refs(out, TYPE_TABLE_BASE) == []

    # (b) Every bound site now carries the new bound and disassembles as cmp.w rX,#new.
    bound_sites = tablegrow.find_bound_sites(out, new_bound)
    assert len(bound_sites) == EXPECT_BOUND_SITES
    assert bound_sites == tablegrow.find_bound_sites(mcf_136, OLD_BOUND)
    assert tablegrow.find_bound_sites(out, OLD_BOUND) == []

    section = theme.guicode_section(out)
    gc_base = theme.guicode_base(section)
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    for site in bound_sites:
        off = section.offset + (site - gc_base)
        insn = next(iter(md.disasm(out[off:off + 4], site)))
        assert insn.mnemonic == "cmp.w"
        assert insn.op_str.endswith(f"#{new_bound:#x}")

    # (c) Every stock entry copied faithfully.
    stock_gs, _ = guidata.guidata_extent(mcf_136)
    cell_old_off = stock_gs + (CELL_TABLE_BASE - GUIDATA_MAP_BASE)
    type_old_off = stock_gs + (TYPE_TABLE_BASE - GUIDATA_MAP_BASE)
    for k in range(OLD_LEN):
        assert _read_u32(out, cell_new_off + k * 4) == _read_u32(mcf_136, cell_old_off + k * 4)
        assert out[type_new_off + k] == mcf_136[type_old_off + k]

    # (d) The new indices exist and are zero-filled.
    for k in range(OLD_LEN, new_len):
        assert _read_u32(out, cell_new_off + k * 4) == 0
        assert out[type_new_off + k] == 0

    # (e) GuiData extent and every checksum are consistent (deep coverage passes).
    coverage.analyse(out, deep=True)


def test_grow_new_entry_fill_is_applied(mcf_136):
    # new_entry_fill now carries the DISTINCT initial bytes of the `extra` requested entries
    # (extra*width bytes), so each new cell can get its own value (e.g. its own SRAM pointer);
    # any padding entries added to reach an encodable bound stay zero.
    cell_vals = [0xDEAD0000 + i for i in range(EXTRA)]
    type_vals = [0x50 + i for i in range(EXTRA)]
    fill = {CELL_TABLE_BASE: b"".join(struct.pack("<I", v) for v in cell_vals),
            TYPE_TABLE_BASE: bytes(type_vals)}
    out, new_indices, _edits = tablegrow.grow_indexed_tables(
        mcf_136, _tables(), OLD_LEN, EXTRA,
        expect_base_refs=EXPECT_BASE_REFS, expect_bound_sites=EXPECT_BOUND_SITES,
        new_entry_fill=fill)

    new_len = _smallest_encodable_new_len(OLD_LEN + EXTRA)
    span = _guidata_span(mcf_136)
    gs, _ge = guidata.guidata_extent(out)
    cell_new_off = gs + (span - 4)
    type_new_off = cell_new_off + new_len * 4
    for i in range(EXTRA):                       # the requested entries carry their fill values
        assert _read_u32(out, cell_new_off + (OLD_LEN + i) * 4) == cell_vals[i]
        assert out[type_new_off + (OLD_LEN + i)] == type_vals[i]
    for k in range(OLD_LEN + EXTRA, new_len):    # padding to an encodable bound stays zero
        assert _read_u32(out, cell_new_off + k * 4) == 0
        assert out[type_new_off + k] == 0


def test_grow_refuses_wrong_base_ref_count(mcf_136):
    with pytest.raises(tablegrow.TableGrowError):
        tablegrow.grow_indexed_tables(
            mcf_136, _tables(), OLD_LEN, EXTRA,
            expect_base_refs={CELL_TABLE_BASE: 6, TYPE_TABLE_BASE: 4},
            expect_bound_sites=EXPECT_BOUND_SITES)


def test_grow_refuses_wrong_bound_site_count(mcf_136):
    with pytest.raises(tablegrow.TableGrowError):
        tablegrow.grow_indexed_tables(
            mcf_136, _tables(), OLD_LEN, EXTRA,
            expect_base_refs=EXPECT_BASE_REFS, expect_bound_sites=13)


def _guidata_span(data: bytes) -> int:
    gs, ge = guidata.guidata_extent(data)
    return ge - gs
