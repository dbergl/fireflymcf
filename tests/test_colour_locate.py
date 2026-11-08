"""The variable type table is read from the code that indexes it, not from a byte scan.

The panel's value-slot reader loads the variable data-pointer table and its parallel u8 type
table from an adjacent pair of GuiCode literal words: on 13.6 the pair at `0x253D04/0x253D08`
(`0x90044BC8`, `0x9004492C`; the type word is loaded at `0x253A8E`, `0x253ADE`, `0x253BF8`), on
14.7 the pair at `0x2565CC/0x2565D0` (`0x90042B74`, `0x90042978`; loaded at `0x256356`,
`0x2563A6`, `0x2564C0`). The backward byte scan this replaces overran 14.7's eight bytes of zero
padding and rounded to `0x42970`, so every 14.7 type was read from the entry 8 places earlier and
the mode-7 palette lost the navy body (index 1) and the background (index 8) (measured
2026-11-07).
"""
from __future__ import annotations

import struct
from pathlib import Path

import pytest

from fireflymcf import guidata, tablegrow, theme

GD = 0x90000000
REPO_ROOT = Path(__file__).resolve().parent.parent

# The 18 static mode-7 entries of stock 14.7 under the code-cited type table, each equal to the
# SRAM cell the emulator reads after booting stock 14.7 to HOME (2026-11-07, 18/18 agree).
PALETTE_147 = {
    1: 0x272862, 8: 0x15183A, 9: 0xBE1E2D, 15: 0x7C151D, 30: 0x00AEEF, 31: 0x4151A3,
    96: 0xC0B23C, 106: 0xCC2127, 111: 0xF15A29, 115: 0x141414, 146: 0x4151A3, 164: 0x7C151D,
    194: 0x2BB673, 220: 0xFEC52D, 325: 0xFFFFFF, 380: 0xA0C03C, 501: 0xCC2127, 503: 0x2BB673,
}


def _rgb(v):
    return ((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)


@pytest.mark.parametrize("version, expected", [
    ("13.6", 0x4492C), ("14.7", 0x42978), ("11.23", 0x4492C)])
def test_type_table_from_code(corpus, version, expected):
    data = corpus[version]["mcf"]
    assert guidata.variable_type_table(data) == expected
    assert guidata.find_variable_tables(data)[1] == expected


def test_type_table_proof_names_the_literal_pairs(mcf_136, mcf_147):
    """The proof is the literal pairs and the `ldr` sites that load the type word."""
    p136 = guidata.variable_table_literals(mcf_136)
    assert [(c, t) for c, t, _v, _l in p136.pairs][0] == (0x253D04, 0x253D08)
    assert len(p136.pairs) == 4
    assert {0x253A8E, 0x253ADE, 0x253BF8} <= set(p136.pairs[0][3])
    p147 = guidata.variable_table_literals(mcf_147)
    assert [(c, t) for c, t, _v, _l in p147.pairs][0] == (0x2565CC, 0x2565D0)
    assert len(p147.pairs) == 4
    assert {0x256356, 0x2563A6, 0x2564C0} <= set(p147.pairs[0][3])
    assert (p147.dptr_off, p147.type_off) == (0x42B74, 0x42978)


def _grown(data):
    """`data` with its variable tables grown the way the readout op grows them (type table
    first, then the cell table, appended to GuiData; every base literal repointed)."""
    dptr, toff = guidata.find_variable_tables(data)
    cell_base, type_base = GD + dptr, GD + toff
    old_len = tablegrow.find_index_bound(data, cell_base) + 1
    tables = [tablegrow.IndexedTable(type_base, 1), tablegrow.IndexedTable(cell_base, 4)]
    refs = {b: len(tablegrow.find_base_refs(data, b)) for b in (cell_base, type_base)}
    sites = len(tablegrow.find_bound_sites(data, old_len - 1))
    fill = {cell_base: struct.pack("<II", 0x2004F000, 0x2004F004),
            type_base: bytes([10, 10])}
    out, _new, _edits = tablegrow.grow_indexed_tables(
        data, tables, old_len, 2, expect_base_refs=refs, expect_bound_sites=sites,
        new_entry_fill=fill)
    return out


def _stock_literal_reading(data):
    """What the removed 13.6-only override read: the cell-base literal at `0x2535B4` (the
    mode-7 lookup's) and the type-base literal at `0x253D08`, as GuiData offsets."""
    sec = theme.guicode_section(data)
    base = theme.guicode_base(sec)
    cell = struct.unpack_from("<I", data, sec.offset + 0x2535B4 - base)[0]
    typ = struct.unpack_from("<I", data, sec.offset + 0x253D08 - base)[0]
    return cell - GD, typ - GD


def test_type_table_on_grown_image(mcf_136):
    """After a grow the code's literals name the relocated tables; the locator follows them and
    agrees with what the 13.6-literal reading it replaces gave."""
    grown = _grown(mcf_136)
    lits = guidata.variable_table_literals(grown)
    assert lits.dptr_off > 0x44BC8 and lits.type_off > 0x4492C       # the relocated copies
    assert lits.type_off < lits.dptr_off
    assert (lits.dptr_off, lits.type_off) == _stock_literal_reading(grown)
    assert guidata.find_variable_tables(grown) == (lits.dptr_off, lits.type_off)
    assert guidata.variable_type_table(grown) == lits.type_off


_GROWN_BUILDS = sorted((REPO_ROOT / "builds").glob("*.bin"))


@pytest.mark.skipif(not _GROWN_BUILDS, reason="research builds/ not linked")
@pytest.mark.parametrize("path", _GROWN_BUILDS, ids=lambda p: p.name)
def test_type_table_on_grown_builds(path):
    """Every reference build in the research repository has grown tables (several stale copies
    stay in GuiData); the code's literal pair names the live one, as the old reading did."""
    data = path.read_bytes()
    lits = guidata.variable_table_literals(data)
    assert lits.dptr_off != 0x44BC8
    assert (lits.dptr_off, lits.type_off) == _stock_literal_reading(data)
    assert guidata.find_variable_tables(data) == (lits.dptr_off, lits.type_off)


def test_palette147(mcf_147):
    palette = theme.mode7_palette(mcf_147)
    assert palette == {i: _rgb(v) for i, v in PALETTE_147.items()}
    assert theme.resolve_fill_index(mcf_147, color=(255, 255, 255)) == 325
    assert theme.resolve_fill_index(mcf_147, index=1) == 1
    assert theme.resolve_fill_index(mcf_147, index=8) == 8


def _set_word(data, runtime, value):
    sec = theme.guicode_section(data)
    base = theme.guicode_base(sec)
    out = bytearray(data)
    struct.pack_into("<I", out, sec.offset + runtime - base, value)
    return bytes(out)


def test_type_table_refuses_disagreeing_pairs(mcf_147):
    """Corrupt the type word of one pair (still a GuiData pointer below the cell table): the
    pairs disagree and the locator refuses, naming the literal addresses."""
    pairs = guidata.variable_table_literals(mcf_147).pairs
    _cell_at, type_at, _value, _loads = pairs[1]
    bad = _set_word(mcf_147, type_at, GD + 0x42970)
    with pytest.raises(guidata.VariableTableNotFound) as e:
        guidata.variable_type_table(bad)
    assert f"{type_at:#x}" in str(e.value)
    assert "0x90042970" in str(e.value) and "0x90042978" in str(e.value)


def test_type_table_refuses_with_no_pair(mcf_136):
    """No literal names the cell table: refuse, never fall back to the byte scan."""
    bad = mcf_136
    for cell_at, _t, _v, _l in guidata.variable_table_literals(mcf_136).pairs:
        bad = _set_word(bad, cell_at, 0)
    with pytest.raises(guidata.VariableTableNotFound, match="no GuiCode literal pair"):
        guidata.variable_type_table(bad)

