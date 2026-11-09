"""Growing the display-cell table on 14.7 (`readout.allocate_fresh_cells` -> `tablegrow`), and
refusing it on 11.23, whose bound compare (`movw rB,#imm ; cmp rI,rB`) the grow cannot rebump.

The numbers are test expectations only (the 2026-11-07 prototype grow of 14.7, reproduced
2026-11-08); every one is derived by the library from GuiCode, none is a locator input.
"""
from __future__ import annotations

import dataclasses
import struct

import pytest

from fireflymcf import celltables, coverage, guidata, readout, sramreach, tablegrow, theme

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
    # the full rebump surface, 8 gate and 6 clamp sites, unchanged in place by the grow
    assert before.bound_sites == after.bound_sites == (
        0x255C82, 0x255D44, 0x255E24, 0x255F1C, 0x256104, 0x256138, 0x256156, 0x256174,
        0x25618E, 0x2561AC, 0x25633E, 0x25638E, 0x2564A0, 0x259130)
    assert after.clamp_sites == before.clamp_sites and len(after.gate_sites) == 8
    assert all(tablegrow.read_cmpw_bound(out, a) == 506 for a in after.bound_sites)
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
    assert ct.rows == 509                  # 508 needed; 0x1FB is not encodable, 0x1FC is
    assert _row(out2, ct, 507) == (cells2[0].addr, 1)
    assert cells2[0].addr != _row(out, celltables.locate(out), 505)[0]
    assert coverage.analyse(out2, deep=True).unclaimed == []


def test_cell_table_copies_use_the_located_type_base(grown_147, monkeypatch):
    """`coverage._cell_table_copies` names the live copy from `celltables.locate` (type base,
    cell base, rows); with the locator stubbed to refuse, it names no copy (the check can
    fail)."""
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


def test_bound_surface_is_the_located_sites(mcf_136, mcf_147):
    """The grow rebumps exactly `located.bound_sites`; on 13.6 that is what `find_bound_sites`
    has always rebumped, so 13.6 output is unchanged."""
    for data in (mcf_136, mcf_147):
        ct = celltables.locate(data)
        sites = tablegrow.cell_table_bound_sites(data, ct)
        assert sites == list(ct.bound_sites) == tablegrow.find_bound_sites(data, ct.rows - 1)
        assert len(sites) == 14


def _guicode_patch(data: bytes, runtime: int, new: bytes) -> bytes:
    sec = theme.guicode_section(data)
    off = tablegrow._guicode_off(sec, theme.guicode_base(sec), runtime)
    out = bytearray(data)
    out[off:off + len(new)] = new
    return bytes(out)


def test_an_extra_uncredited_bound_compare_refuses(mcf_147):
    """Turn some other `cmp.w rX,#imm` into `cmp.w rX,#bound`: no gate or clamp credits it, so
    the surface is incomplete and the grow refuses, naming it."""
    ct = celltables.locate(mcf_147)
    code, base = tablegrow._guicode_slice(mcf_147)
    for off, _size, op_str in tablegrow._cmp_w_sites(bytes(code), base):
        imm = int(op_str.rsplit("#", 1)[1], 0) if "#" in op_str else None
        if imm is not None and imm not in (ct.rows - 1, ct.rows) and op_str.startswith("r"):
            break
    site = base + off
    mutated = _guicode_patch(mcf_147, site,
                             tablegrow._reencode_cmpw(bytes(code[off:off + 4]), ct.rows - 1))
    mct = celltables.locate(mutated)
    assert site not in mct.bound_sites
    with pytest.raises(celltables.CellTableError) as e:
        readout.allocate_fresh_cells(mutated, [(2, 3)])
    assert e.value.code == "CELL_TABLE_BOUND_SITES"
    assert "%#x" % site in str(e.value)


def test_a_clamp_without_its_reader_is_not_credited(mcf_136):
    """Replace the one read of 13.6's clamp-0x25383C store (`ldrh r2,[r3,r2]` at 0x258208,
    state+0x6BF8) with `movs r2,#0`: the clamp is no longer credited, and the grow refuses."""
    assert celltables.locate(mcf_136).clamp_sites[0] == 0x25383C
    mutated = _guicode_patch(mcf_136, 0x258208, bytes.fromhex("0022"))
    ct = celltables.locate(mutated)
    assert 0x25383C not in ct.clamp_sites and len(ct.clamp_sites) == 5
    with pytest.raises(celltables.CellTableError) as e:
        tablegrow.cell_table_bound_sites(mutated, ct)
    assert e.value.code == "CELL_TABLE_BOUND_SITES" and "0x25383c" in str(e.value)


def test_a_surface_site_that_is_not_the_compare_refuses(mcf_147):
    """A surface site whose bytes no longer decode as `cmp.w #bound` refuses: here a clamp
    site rewritten to compare against 0x1F0 (the surface is the stock image's)."""
    ct = celltables.locate(mcf_147)
    site = ct.clamp_sites[0]
    sec = theme.guicode_section(mcf_147)
    off = tablegrow._guicode_off(sec, theme.guicode_base(sec), site)
    mutated = _guicode_patch(mcf_147, site, tablegrow._reencode_cmpw(mcf_147[off:off + 4], 0x1F0))
    with pytest.raises(celltables.CellTableError) as e:
        tablegrow.cell_table_bound_sites(mutated, ct)
    assert e.value.code == "CELL_TABLE_BOUND_SITES"
    assert "not cmp.w #0x1f8: %#x" % site in str(e.value)
