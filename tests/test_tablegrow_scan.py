"""`fireflymcf.tablegrow` -- read-only patch-surface discovery for a bound-checked,
base-pointer GuiData table.

The expected address lists are a byte-derived oracle, not a fit: they were confirmed by a
spike disassembly of 13.6's GuiCode (working notes, not published) before this module existed, so a stub or a desynced scan would not
reproduce them by chance. This is discovery only -- Task 3 uses these to actually relocate
and grow the table; nothing here modifies an image.
"""
from __future__ import annotations

import pytest

from fireflymcf import guidata, theme, tablegrow


# The display-cell table's own base pointer, and a second, independent base pointer
# (`+0x44BC8` and a nearby table) -- two different constants, so a scan keyed to the wrong
# literal would fail one list without the other coincidentally matching.
DISPLAY_CELL_TABLE_BASE = 0x90044BC8
OTHER_TABLE_BASE = 0x9004492C

EXPECTED_DISPLAY_CELL_BASE_REFS = [
    0x2535b4, 0x25385c, 0x253d04, 0x25689c, 0x257e68, 0x258020, 0x2582c0,
]
EXPECTED_OTHER_BASE_REFS = [
    0x253d08, 0x257e6c, 0x258024, 0x2582c4,
]

# The display-cell table's row-count bound: every `cmp.w rX, #0x298` in 13.6's GuiCode
# gates an indexed read into some table (there are no false positives to filter out here).
DISPLAY_CELL_TABLE_BOUND = 0x298

EXPECTED_BOUND_SITES = [
    0x2533ba, 0x25347c, 0x25355c, 0x253654, 0x25383c, 0x253870, 0x25388e, 0x2538ac,
    0x2538c6, 0x2538e4, 0x253a76, 0x253ac6, 0x253bd8, 0x256870,
]


def test_find_base_refs_display_cell_table(mcf_136):
    assert tablegrow.find_base_refs(mcf_136, DISPLAY_CELL_TABLE_BASE) == EXPECTED_DISPLAY_CELL_BASE_REFS


def test_find_base_refs_other_table(mcf_136):
    """A second base pointer, scanned independently, to guard against a scan that always
    returns the first list regardless of `base_addr`."""
    assert tablegrow.find_base_refs(mcf_136, OTHER_TABLE_BASE) == EXPECTED_OTHER_BASE_REFS


def test_find_bound_sites_display_cell_table(mcf_136):
    assert tablegrow.find_bound_sites(mcf_136, DISPLAY_CELL_TABLE_BOUND) == EXPECTED_BOUND_SITES


def test_find_index_bound_display_cell_table(mcf_136):
    """`find_index_bound` recovers the display-cell/type table pair's row-count bound
    (`old_len`) purely from GuiCode -- by correlating a `cmp.w rIdx,#imm` to an indexed
    read whose base was loaded from this table's base literal -- with NO pinned bound-site
    address. On 13.6 the gating immediate is 0x298 (664), so old_len == 665, matching the
    value the (about-to-be-removed) pinned site `readout._CELL_BOUND_SITE` reports."""
    assert tablegrow.find_index_bound(mcf_136, DISPLAY_CELL_TABLE_BASE) == DISPLAY_CELL_TABLE_BOUND + 1


def test_find_index_bound_refuses_the_unsupported_movw_bound_encoding(mcf_1123):
    """Scope boundary: 11.23 gates the cell table with `movw rBnd,#imm; cmp rIdx,rBnd` -- a
    register compare against a movw-materialised bound -- not the 13.6 `cmp.w rIdx,#imm`.
    Neither `find_index_bound` nor the grow's `cmp.w #imm` bound-REBUMP recognises that form,
    so `find_index_bound` RAISES a clear `TableGrowError` rather than guessing a wrong count.
    Teaching both discovery and re-encoding the movw form -- the work to produce a working
    11.23/14.7 build -- is deferred (Scope B)."""
    base = 0x90000000 + guidata.find_variable_tables(mcf_1123)[0]
    with pytest.raises(tablegrow.TableGrowError):
        tablegrow.find_index_bound(mcf_1123, base)


def test_find_index_bound_correlates_to_the_given_base(mcf_136):
    """Non-vacuity: the type table (0x4492C) shares the 664 bound with the cell table, so
    both resolve to 665 -- but a base that indexes NOTHING must raise, proving the result is
    tied to the base argument, not a constant."""
    assert tablegrow.find_index_bound(mcf_136, OTHER_TABLE_BASE) == DISPLAY_CELL_TABLE_BOUND + 1
    with pytest.raises(tablegrow.TableGrowError):
        tablegrow.find_index_bound(mcf_136, 0x90000000)  # not a table base -> no gated read


def test_find_base_refs_result_is_sorted_and_deduplicated(mcf_136):
    hits = tablegrow.find_base_refs(mcf_136, DISPLAY_CELL_TABLE_BASE)
    assert hits == sorted(set(hits))


def test_find_bound_sites_result_is_sorted_and_deduplicated(mcf_136):
    hits = tablegrow.find_bound_sites(mcf_136, DISPLAY_CELL_TABLE_BOUND)
    assert hits == sorted(set(hits))


def test_indexed_table_dataclass_holds_base_and_width():
    table = tablegrow.IndexedTable(base_addr=DISPLAY_CELL_TABLE_BASE, width=4)
    assert table.base_addr == DISPLAY_CELL_TABLE_BASE
    assert table.width == 4


def test_table_grow_error_is_an_exception():
    assert issubclass(tablegrow.TableGrowError, Exception)


def _mutate_one_literal_byte(mcf_136: bytes, target_runtime_addr: int) -> bytes:
    """Flip one byte of the literal-pool word at `target_runtime_addr` in a copy of the
    image, leaving everything else -- including the rest of GuiCode -- untouched."""
    section = theme.guicode_section(mcf_136)
    base = theme.guicode_base(section)
    file_off = section.offset + (target_runtime_addr - base)
    mutated = bytearray(mcf_136)
    mutated[file_off] ^= 0xFF
    return bytes(mutated)


def test_scan_can_fail_a_one_byte_mutation_changes_the_hit_count(mcf_136):
    """The non-vacuity guard: stub-proofs `find_base_refs`. Flipping one byte of the literal
    word at the first known hit must break the exact match to `DISPLAY_CELL_TABLE_BASE`, so
    the hit count drops -- a scan that ignored the bytes and just replayed a fixture would
    not notice."""
    mutated = _mutate_one_literal_byte(mcf_136, EXPECTED_DISPLAY_CELL_BASE_REFS[0])
    original_hits = tablegrow.find_base_refs(mcf_136, DISPLAY_CELL_TABLE_BASE)
    mutated_hits = tablegrow.find_base_refs(mutated, DISPLAY_CELL_TABLE_BASE)
    assert len(mutated_hits) != len(original_hits)
    assert EXPECTED_DISPLAY_CELL_BASE_REFS[0] not in mutated_hits
