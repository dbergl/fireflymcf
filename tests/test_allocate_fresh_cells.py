"""`readout.allocate_fresh_cells` mints genuinely-private display cells by growing the
`+0x44BC8` cell/type table set, rather than reusing a "spare" index that (on 13.6) is
actually read by an object in another GuiData object-pointer table -- the coach-48V
background-bar aliasing. Every cell it returns must be a brand-new table index backed by a
fresh, distinct, 4-aligned SRAM address, and the grown image must be structurally sound.
"""
import struct
from pathlib import Path

from fireflymcf import coverage, guidata, readout, status_map, tablegrow

ROOT = Path(__file__).resolve().parents[1]
STOCK = (ROOT / "firefly-13.6" / "mcf.bin").read_bytes()

# The three cells the coach-48V battery needs: a u32 colour word (no formatter type), a
# 1-byte gauge level, a 2-byte voltage value.
SPECS = [(4, None), (1, 1), (2, 3)]


def test_resolve_cell_tables_is_structural_and_matches_136():
    """`resolve_cell_tables` derives the cell/type base and row count with NO pinned GuiCode
    literal address -- via `find_variable_tables` + `find_index_bound`. On 13.6 it returns
    exactly the values the reference constants name (cell 0x90044BC8, type 0x9004492C, 665),
    matching what the old pinned bound site reported."""
    cell_base, type_base, old_len = readout.resolve_cell_tables(STOCK)
    assert cell_base == 0x90044BC8
    assert type_base == 0x9004492C
    assert old_len == tablegrow.read_cmpw_bound(STOCK, 0x2533BA) + 1 == 665


def test_allocates_fresh_growing_indices_backed_by_distinct_sram():
    old_len = tablegrow.read_cmpw_bound(STOCK, 0x2533BA) + 1
    image, cells, edits = readout.allocate_fresh_cells(STOCK, SPECS)

    assert len(cells) == 3
    # every cell is a NEW index (>= the pre-grow row count), and they are distinct
    idxs = [c.slot for c in cells]
    assert all(i >= old_len for i in idxs)
    assert len(set(idxs)) == 3
    assert idxs == list(range(old_len, old_len + 3))     # contiguous appended indices

    # widths carried through, tier-3 fresh
    assert [c.width for c in cells] == [4, 1, 2]
    assert all(c.tier == 3 and c.fresh for c in cells)

    # each backed by a distinct, 4-aligned SRAM address in the working range
    addrs = [c.addr for c in cells]
    assert len(set(addrs)) == 3
    for a in addrs:
        assert a % 4 == 0
        assert status_map.SRAM_LOW <= a < status_map.SRAM_HIGH

    # the grown table's row-count bound was bumped past the new indices
    new_len = tablegrow.read_cmpw_bound(image, 0x2533BA) + 1
    assert new_len >= old_len + 3

    # and the table base literals now point at the relocated table, whose new entries hold the
    # reserved SRAM pointers (read the table at its CURRENT, relocated base)
    cell_base = tablegrow.read_guicode_word(image, 0x2535B4)
    gs, _ge = guidata.guidata_extent(image)
    cell_off = gs + (cell_base - 0x90000000)
    for i, c in enumerate(cells):
        entry = struct.unpack_from("<I", image, cell_off + c.slot * 4)[0]
        assert entry == c.addr, "new cell %d entry %#x != reserved addr %#x" % (c.slot, entry, c.addr)

    # the image is structurally sound end to end
    coverage.analyse(image, deep=True)


def test_new_indices_are_referenced_by_nothing_in_any_table():
    """The whole point: a fresh index aliases nothing. (Nothing yet references it -- only the
    consuming op's own widget will, in a later task.)"""
    image, cells, _ = readout.allocate_fresh_cells(STOCK, SPECS)
    consumed = status_map.all_table_cell_consumers(image)
    for c in cells:
        assert c.slot not in consumed


def test_empty_specs_is_a_noop():
    image, cells, edits = readout.allocate_fresh_cells(STOCK, [])
    assert image == STOCK and cells == [] and edits == []
