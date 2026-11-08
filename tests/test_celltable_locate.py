"""`celltables.locate`: the display-cell table and its parallel type table, found from GuiCode's
bound compares on any image (stock 11.23/13.6/14.7, and a grown image used as stock).

The expected values are test expectations only (measured 2026-11-07); none is a locator input.
"""
from __future__ import annotations

import dataclasses
import struct
from pathlib import Path

import pytest

from fireflymcf import celltables, guidata, recipe, theme

REPO_ROOT = Path(__file__).resolve().parent.parent
GD = 0x90000000
_GROWN_BUILDS = sorted((REPO_ROOT / "builds").glob("*.bin"))


def test_locate_136(mcf_136):
    ct = celltables.locate(mcf_136)
    assert (ct.cell_off, ct.type_off, ct.rows) == (0x44BC8, 0x4492C, 665)
    assert (ct.cell_base, ct.type_base) == (0x90044BC8, 0x9004492C)
    assert ct.bound_form == "cmp.w"
    assert len(ct.bound_sites) == 8 and 0x2533BA in ct.bound_sites
    assert ct.cell_refs == (0x2535B4, 0x25385C, 0x253D04, 0x25689C, 0x257E68, 0x258020,
                            0x2582C0)
    assert ct.type_refs == (0x253D08, 0x257E6C, 0x258024, 0x2582C4)
    assert any("align4(665)" in p for p in ct.proof)


def test_locate_147(mcf_147):
    ct = celltables.locate(mcf_147)
    assert (ct.type_off, ct.rows) == (0x42978, 505)
    assert ct.cell_off == ct.type_off + 0x1FC          # align4(505): the cell table as found
    assert ct.bound_form == "cmp.w"
    assert 0x255C82 in ct.bound_sites


def test_locate_1123_reads_the_movw_bound(mcf_1123):
    ct = celltables.locate(mcf_1123)
    assert (ct.cell_off, ct.type_off, ct.rows) == (0x44B60, 0x4492C, 562)
    assert ct.bound_form == "movw+cmp"
    assert 0x250CEA in ct.bound_sites


def test_accessors_follow_locate(corpus):
    for version in ("11.23", "13.6", "14.7"):
        data = corpus[version]["mcf"]
        ct = celltables.locate(data)
        assert celltables.cell_table_off(data) == ct.cell_off, version
        assert celltables.type_table_off(data) == ct.type_off, version
        assert celltables.row_count(data) == ct.rows, version


def test_find_variable_tables_delegates(mcf_147, monkeypatch):
    assert guidata.find_variable_tables(mcf_147) == (0x42B74, 0x42978)

    def refuse(_data):
        raise celltables.CellTableError("CELL_TABLE_NO_BOUND", "stubbed")
    monkeypatch.setattr(celltables, "locate", refuse)
    with pytest.raises(guidata.VariableTableNotFound):
        guidata.find_variable_tables(mcf_147)


@pytest.mark.parametrize("version", ["11.23", "13.6", "14.7"])
def test_agrees_with_variable_type_table(corpus, version):
    data = corpus[version]["mcf"]
    assert celltables.locate(data).type_off == guidata.variable_type_table(data)


@pytest.mark.skipif(not _GROWN_BUILDS, reason="research builds/ not linked")
@pytest.mark.parametrize("path", _GROWN_BUILDS, ids=lambda p: p.name)
def test_locate_on_grown_builds(path):
    """A grown image used as stock: the live tables are the relocated pair GuiCode loads, never
    the stale stock copy left in GuiData (which no GuiCode literal names any more)."""
    from fireflymcf import tablegrow
    data = path.read_bytes()
    ct = celltables.locate(data)
    assert ct.rows > 665
    assert ct.cell_off != 0x44BC8 and ct.type_off != 0x4492C
    assert ct.type_off == guidata.variable_type_table(data)
    assert guidata.find_variable_tables(data) == (ct.cell_off, ct.type_off)
    assert tablegrow.find_base_refs(data, 0x90044BC8) == []      # the stale copy is unread
    gs, _ge = guidata.guidata_extent(data)
    words = struct.unpack_from(f"<{ct.rows}I", data, gs + ct.cell_off)
    assert all(w == 0 or guidata.SRAM_LOW <= w < guidata.SRAM_HIGH for w in words)


def _second_bound(data: bytes) -> bytes:
    """13.6 with one of the cell table's eight `cmp.w r,#0x298` bounds re-encoded to #0x290."""
    from fireflymcf import tablegrow
    sec = theme.guicode_section(data)
    base = theme.guicode_base(sec)
    off = sec.offset + 0x2533BA - base
    out = bytearray(data)
    out[off:off + 4] = tablegrow._reencode_cmpw(bytes(data[off:off + 4]), 0x290)
    return bytes(out)


def test_a_second_bound_refuses(mcf_136):
    with pytest.raises(celltables.CellTableError) as e:
        celltables.locate(_second_bound(mcf_136))
    assert e.value.code == "CELL_TABLE_BOUND_AMBIGUOUS"
    assert "0x290" in str(e.value) and "0x298" in str(e.value)


def test_no_bound_refuses(mcf_136, monkeypatch):
    """The locator fails when the bound scan finds nothing (the test can fail)."""
    monkeypatch.setattr(celltables, "_gated", lambda *a: {})
    celltables._locate.cache_clear()
    try:
        with pytest.raises(celltables.CellTableError) as e:
            celltables.locate(mcf_136)
        assert e.value.code == "CELL_TABLE_NO_BOUND"
    finally:
        celltables._locate.cache_clear()


def test_disagreement_with_the_literal_pair_refuses(mcf_136, monkeypatch):
    real = guidata.variable_table_literals(mcf_136)
    monkeypatch.setattr(guidata, "variable_table_literals",
                        lambda d: dataclasses.replace(real, type_off=real.type_off - 8))
    with pytest.raises(celltables.CellTableError) as e:
        celltables.locate(mcf_136)
    assert e.value.code == "TYPE_TABLE_DISAGREES"


def test_adjacency_refuses(mcf_136, monkeypatch):
    """A lone type candidate that does not end where the cell table starts is refused."""
    real = celltables._gated

    def shifted(code, base, lits):
        out = dict(real(code, base, lits))
        out[0x90044920] = out.pop(0x9004492C)       # a type table 12 bytes early
        return out
    monkeypatch.setattr(celltables, "_gated", shifted)
    celltables._locate.cache_clear()
    try:
        with pytest.raises(celltables.CellTableError) as e:
            celltables.locate(mcf_136)
        assert e.value.code == "CELL_TABLE_ADJACENCY"
    finally:
        celltables._locate.cache_clear()


def test_error_is_coded_for_recipes():
    assert issubclass(celltables.CellTableError, guidata.VariableTableNotFound)
    assert celltables.CellTableError in recipe._CODED
