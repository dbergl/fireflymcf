"""Growing the display-cell table on 14.7 (`readout.allocate_fresh_cells` -> `tablegrow`), and
refusing it on 11.23, whose bound compare (`movw rB,#imm ; cmp rI,rB`) the grow cannot rebump.

The numbers are test expectations only (the 2026-11-07 prototype grow of 14.7, reproduced
2026-11-08); every one is derived by the library from GuiCode, none is a locator input.
"""
from __future__ import annotations

import struct

import pytest

from fireflymcf import celltables, coverage, guidata, readout, sramreach, tablegrow

GD = 0x90000000


@pytest.fixture(scope="module")
def grown_147(mcf_147):
    return readout.allocate_fresh_cells(mcf_147, [(2, 3)])


def _row(data: bytes, ct, idx: int) -> tuple[int, int]:
    """`(cell word, type byte)` of row `idx` of the located pair."""
    gs, _ = guidata.guidata_extent(data)
    cell = struct.unpack_from("<I", data, gs + ct.cell_off + 4 * idx)[0]
    return cell, data[gs + ct.type_off + idx]


def test_grow_147_mints_index_505_inside_the_cell_area(mcf_147, grown_147):
    _out, cells, _edits = grown_147
    (cell,) = cells
    assert cell.slot == 505 == celltables.locate(mcf_147).rows
    assert cell.addr == 0x20039F20
    lo, hi = sramreach.cell_area(mcf_147)
    assert lo <= cell.addr and cell.addr + cell.width <= hi


def test_grow_147_relocates_the_pair_and_locates_it_again(mcf_147, grown_147):
    out, cells, _edits = grown_147
    before, after = celltables.locate(mcf_147), celltables.locate(out)
    assert (after.cell_base, after.type_base, after.rows) == (0x90095168, 0x90094F6C, 507)
    assert after.bound_form == "cmp.w"
    assert len(after.bound_sites) == len(before.bound_sites)
    assert guidata.find_variable_tables(out) == (after.cell_off, after.type_off)
    assert celltables.stale_pair(out) == before.cell_off       # the old pair, left unread
    # the new row names the fresh cell with the requested type; the stock rows are verbatim
    assert _row(out, after, 505) == (cells[0].addr, 3)
    assert _row(out, after, 506) == (0, 0)                     # padding to an encodable bound
    for i in (0, 1, 250, 504):
        assert _row(out, after, i) == _row(mcf_147, before, i)
    # the grown table still lies inside the area (CELL_OUTSIDE_AREA would refuse)
    assert sramreach.cell_area(out)[0] <= cells[0].addr


def test_grow_147_deep_coverage_claims_every_byte(grown_147):
    out, _cells, _edits = grown_147
    rep = coverage.analyse(out, deep=True)
    assert rep.unclaimed == []
    labels = [label for _start, _end, label in rep.spans]
    assert any(l.startswith("variable-cell-table:") and l.endswith("[507]") for l in labels)
    assert any(l.startswith("variable-type-table:") and l.endswith("[507]") for l in labels)


def test_grow_147_twice(grown_147):
    """A second grow reads the first's relocated pair and bound (507 rows) and grows from it."""
    out, _cells, _edits = grown_147
    out2, cells2, _ = readout.allocate_fresh_cells(out, [(1, 1)])
    assert cells2[0].slot == 507
    ct = celltables.locate(out2)
    assert ct.rows >= 508
    assert _row(out2, ct, 507) == (cells2[0].addr, 1)
    assert cells2[0].addr != _row(out, celltables.locate(out), 505)[0]
    assert coverage.analyse(out2, deep=True).unclaimed == []


def test_cell_table_copies_use_the_located_type_base(grown_147, monkeypatch):
    """`coverage._cell_table_copies` names the live copy from `celltables.locate`: with the
    locator refusing, it recognises nothing, so the grow's appended pair falls to the trailing
    object and deep coverage refuses (the check can fail)."""
    out, _cells, _edits = grown_147
    gs, ge = guidata.guidata_extent(out)
    copies = coverage._cell_table_copies(out, gs, gs, ge - 4)
    ct = celltables.locate(out)
    assert [(t - gs, c - gs, r, live) for t, c, r, live in copies][-1] == (
        ct.type_off, ct.cell_off, 507, True)

    def refuse(_data):
        raise celltables.CellTableError("CELL_TABLE_NO_BOUND", "stubbed")
    monkeypatch.setattr(celltables, "locate", refuse)
    assert coverage._cell_table_copies(out, gs, gs, ge - 4) == []


def test_grow_refuses_the_1123_movw_bound(mcf_1123):
    with pytest.raises(celltables.CellTableError) as e:
        readout.allocate_fresh_cells(mcf_1123, [(2, 3)])
    assert e.value.code == "CELL_TABLE_BOUND_FORM"
    assert "movw+cmp" in str(e.value)


def test_bound_surface_covers_the_located_sites(mcf_136, mcf_147):
    """The rewrite surface is every unsigned bound compare at the table's bound: the located
    sites plus the clamp-and-store sites (`cmp.w r0,#bound ; bls.w`) the locator does not credit
    to the table, all `cmp.w`; 13.6's is exactly what `find_bound_sites` has always rebumped."""
    for data in (mcf_136, mcf_147):
        ct = celltables.locate(data)
        sites = tablegrow.cell_table_bound_sites(data, ct)
        assert set(ct.bound_sites) < set(sites)
        assert sites == tablegrow.find_bound_sites(data, ct.rows - 1)
        assert len(sites) == 14


def test_bound_surface_refuses_a_missed_compare(mcf_147, monkeypatch):
    """A bound compare the rebump surface leaves out refuses (stub the surface one site short)."""
    ct = celltables.locate(mcf_147)
    full = tablegrow.find_bound_sites(mcf_147, ct.rows - 1)
    monkeypatch.setattr(tablegrow, "find_bound_sites", lambda data, bound: full[:-1])
    with pytest.raises(celltables.CellTableError) as e:
        tablegrow.cell_table_bound_sites(mcf_147, ct)
    assert e.value.code == "CELL_TABLE_BOUND_SITES"
    assert "%#x" % full[-1] in str(e.value)
