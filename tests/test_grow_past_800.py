"""Growing 13.6's display-cell table through 801 rows (bound 0x320 = 800).

Stock 13.6 GuiCode carries three `cmp.w rX,#0x320` that are 800-px screen-coordinate checks,
not table gates (0x24F358, 0x24F618: `x+1 < 800` behind a 0xFFFF sentinel; 0x258B78: `x < 800`
followed by `cmp.w r5,#0x1E0`, y < 480). The table grows in steps of 4 (665, ..., 797, 801,
805), so a grow that starts at 801 rows compares against 800, and the completeness scan once
took those three as uncredited gates (CELL_TABLE_BOUND_SITES, reported 2026-11-17). A gate the
locator misses keeps the stock bound, since only the surface is rebumped, so completeness is
proved against the stock image at the stock bound (`tablegrow.cell_table_bound_sites`).
"""
from __future__ import annotations

import dataclasses

import pytest

from fireflymcf import celltables, readout, stockscope, tablegrow

SCREEN_800 = (0x24F358, 0x24F618, 0x258B78)


@pytest.fixture(scope="module")
def at_801(mcf_136):
    with stockscope.stock_scope(mcf_136):
        out, _cells, _edits = readout.allocate_fresh_cells(mcf_136, [(2, None)] * 133)
    assert celltables.locate(out).rows == 801
    return out


def test_the_screen_compares_hold_800_in_stock(mcf_136):
    code, base = tablegrow._guicode_slice(mcf_136)
    at_800 = sorted(base + off for off, _a, _r, imm, form
                    in celltables._compares(bytes(code), base) if imm == 0x320)
    assert at_800 == list(SCREEN_800)
    assert not set(SCREEN_800) & set(celltables.locate(mcf_136).bound_sites)


def test_a_grow_from_801_rows_passes_under_the_stock(mcf_136, at_801):
    with stockscope.stock_scope(mcf_136):
        out, cells, _edits = readout.allocate_fresh_cells(at_801, [(2, None)])
    ct = celltables.locate(out)
    assert cells[0].slot == 801 and ct.rows == 805
    assert all(tablegrow.read_cmpw_bound(out, s) == 804 for s in ct.bound_sites)
    for s in SCREEN_800:                       # the screen checks are left alone
        assert tablegrow.read_cmpw_bound(out, s) == 0x320


def test_a_missed_gate_still_refuses_in_a_grown_image(mcf_136, at_801):
    """A grown image is judged by its stock: some other stock `cmp.w` rewritten to the stock
    bound (an uncredited gate) refuses a grow of the 801-row image, naming the site."""
    from fireflymcf import theme
    ct = celltables.locate(mcf_136)
    code, base = tablegrow._guicode_slice(mcf_136)
    for off, _size, op_str in tablegrow._cmp_w_sites(bytes(code), base):
        imm = int(op_str.rsplit("#", 1)[1], 0) if "#" in op_str else None
        if imm is not None and imm not in (ct.rows - 1, ct.rows) and op_str.startswith("r") \
                and base + off not in SCREEN_800:
            break
    site = base + off
    sec = theme.guicode_section(mcf_136)
    goff = tablegrow._guicode_off(sec, theme.guicode_base(sec), site)
    stock = bytearray(mcf_136)
    stock[goff:goff + 4] = tablegrow._reencode_cmpw(bytes(code[off:off + 4]), ct.rows - 1)
    stock = bytes(stock)
    assert site not in celltables.locate(stock).bound_sites
    with stockscope.stock_scope(stock), pytest.raises(celltables.CellTableError) as e:
        readout.allocate_fresh_cells(at_801, [(2, None)])
    assert e.value.code == "CELL_TABLE_BOUND_SITES" and "%#x" % site in str(e.value)


def test_a_grown_image_that_lost_a_stock_site_refuses(mcf_136, at_801):
    """Every stock surface site must still be on the grown image's surface."""
    ct = celltables.locate(at_801)
    fewer = dataclasses.replace(ct, bound_sites=tuple(sorted(ct.bound_sites))[1:])
    with stockscope.stock_scope(mcf_136), pytest.raises(celltables.CellTableError) as e:
        tablegrow.cell_table_bound_sites(at_801, fewer)
    assert e.value.code == "CELL_TABLE_BOUND_SITES" and "lacks the stock surface" in str(e.value)
