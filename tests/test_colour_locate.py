"""The variable type table is read from the code that indexes it, not from a byte scan.

The panel's value-slot reader loads the variable data-pointer table and its parallel u8 type
table from an adjacent pair of GuiCode literal words: on 13.6 the pair at `0x253D04/0x253D08`
(`0x90044BC8`, `0x9004492C`; the type word is loaded at `0x253A8E`, `0x253ADE`, `0x253BF8`), on
14.7 the pair at `0x2565CC/0x2565D0` (`0x90042B74`, `0x90042978`; loaded at `0x256356`,
`0x2563A6`, `0x2564C0`). The backward byte scan this replaces overran 14.7's eight bytes of zero
padding and rounded to `0x42970`, so every 14.7 type was read from the entry 8 places earlier and
the mode-7 palette lost the navy body (index 1) and the background (index 8) (measured
2026-11-07).

`theme.colour_decode` is the record decoder's colour-mode switch (13.6 `FUN_00252CB0`, 14.7
`0x255578`), located by its mode-2 store. The expected values are measured from the corpus
(2026-11-07) and appear only here; the locator reads them from the code:

| | 13.6 | 14.7 |
|---|---|---|
| mode-6 palette / length | `0x90010010` / 20 | `0x90010010` / 21 |
| mode-7 table / bound | `0x90044BC8` / `0x298` | `0x90042B74` / `0x1F8` |
| mode-3 reader | `0x252140` | `0x254A08` (byte-identical) |

11.23's mode-3 arm calls its reader with a different shape and is refused.
"""
from __future__ import annotations

import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from fireflymcf import guidata, recipe, recipe_ops, tablegrow, theme, themeswap

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



# ------------------------------------------------------------------- the colour decode switch

def _code_at(data, addr, n):
    sec = theme.guicode_section(data)
    off = sec.offset + addr - theme.guicode_base(sec)
    return data[off:off + n]


def test_colour_decode_136(mcf_136):
    cd = theme.colour_decode(mcf_136)
    assert (cd.palette_base, cd.palette_len) == (0x90010010, 20)
    assert (cd.mode7_table, cd.mode7_bound) == (0x90044BC8, 0x298)
    assert cd.mode3_reader == 0x252140
    assert cd.palette_offset == 0x10010
    p = dict(cd.proof)
    assert p["mode 2 mvn"] == 0x2534F8
    assert p["mode 3 arm"] == 0x253508
    assert p["mode 3 bl"] == 0x25350A
    assert p["mode 6 palette literal"] == 0x2535B0
    assert p["mode 7 cmp.w"] == 0x25355C
    assert p["mode 7 table literal"] == 0x2535B4
    assert p["next structure"] == 0x90010060
    assert p["next structure literal"] == 0x255FBC
    assert p["next structure ldr"] == 0x255F92                      # ldr r4, [pc, #0x28]


def test_colour_decode_147(mcf_147):
    cd = theme.colour_decode(mcf_147)
    assert (cd.palette_base, cd.palette_len) == (0x90010010, 21)
    assert (cd.mode7_table, cd.mode7_bound) == (0x90042B74, 0x1F8)
    assert cd.mode3_reader == 0x254A08
    p = dict(cd.proof)
    assert p["mode 2 mvn"] == 0x255DC0
    assert p["mode 3 arm"] == 0x255DD0
    assert p["mode 3 bl"] == 0x255DD2
    assert p["mode 7 cmp.w"] == 0x255E24
    assert p["mode 6 palette literal"] == 0x255E78
    assert p["mode 7 table literal"] == 0x255E7C
    assert p["next structure"] == 0x90010064
    assert p["next structure literal"] == 0x25887C
    assert p["next structure ldr"] == 0x258852                      # ldr r4, [pc, #0x28]


def test_colour_decode_147_reader_is_136s(mcf_136, mcf_147):
    a, b = theme.colour_decode(mcf_136), theme.colour_decode(mcf_147)
    n = dict(a.proof)["mode 3 reader bx"] + 2 - a.mode3_reader
    assert n == 0x20
    assert _code_at(mcf_136, a.mode3_reader, n) == _code_at(mcf_147, b.mode3_reader, n)


@pytest.mark.parametrize("version", ["13.6", "14.7"])
def test_colour_decode_mode7_table_is_the_variable_pointer_table(corpus, version):
    data = corpus[version]["mcf"]
    cd = theme.colour_decode(data)
    assert cd.mode7_table - GD == guidata.find_variable_tables(data)[0]


def test_colour_decode_refuses_1123(mcf_1123):
    """11.23 reaches its reader through `movw r0,#0x67e4; add r0,r6; bl` and stores through r6:
    not the shape this locator proves, so it refuses rather than guess."""
    with pytest.raises(theme.ColourDecodeError, match="mode-3 arm"):
        theme.colour_decode(mcf_1123)


def test_colour_decode_refuses_without_the_mode2_anchor(mcf_136):
    out = bytearray(mcf_136)
    sec = theme.guicode_section(mcf_136)
    off = sec.offset + 0x2534F8 - theme.guicode_base(sec)
    assert bytes(out[off:off + 4]) == bytes.fromhex("6ff07f42")     # mvn r2, #0xff000000
    out[off:off + 4] = bytes.fromhex("00bf00bf")                      # nop; nop
    with pytest.raises(theme.ColourDecodeError, match="mode-2"):
        theme.colour_decode(bytes(out))


def test_colour_decode_refuses_two_mode2_anchors(mcf_136):
    """A second `mvn r2,#0xff000000; str r2,[r3,#0x68]` (here over the mode-1 arm's
    `ldr r2,=..; str r2,[r3,#0x68]; ldr r2,[r3]`) is ambiguous."""
    out = bytearray(mcf_136)
    sec = theme.guicode_section(mcf_136)
    base = theme.guicode_base(sec)
    src, dst = sec.offset + 0x2534F8 - base, sec.offset + 0x2534E8 - base
    out[dst:dst + 6] = out[src:src + 6]
    with pytest.raises(theme.ColourDecodeError, match="2 mode-2"):
        theme.colour_decode(bytes(out))


def test_themeswap_bounds_the_palette_by_the_code(mcf_136):
    """13.6's word 20 (`0x96007d`) is past the 20-entry palette: an `extra` naming it no longer
    reaches it."""
    gs, _ = guidata.guidata_extent(mcf_136)
    assert struct.unpack_from("<I", mcf_136, gs + 0x10010 + 4 * 20)[0] == 0x96007D
    p = themeswap.plan(mcf_136, {}, None, [((150, 0, 125), (1, 2, 3))])
    words = range(gs + 0x10010, gs + 0x10010 + 4 * 30)
    assert not [e for e in p.edits if e[0] in words]
    assert p.palette == 0


def test_black_refused_on_147(mcf_147):
    """14.7's code-cited static palette has no black (the old idx-34 black was a mistyped
    entry). The label ops refuse it as NO_COLOUR (`add_text_label` is 13.6-only, so its
    firmware gate answers first in a compile; this calls the shared resolver directly), and
    the message lists the palette."""
    with pytest.raises(theme.PaletteError) as e:
        theme.resolve_fill_index(mcf_147, color=(0, 0, 0))
    msg = str(e.value)
    assert "(0, 0, 0)" in msg and "palette" in msg
    assert "(255, 255, 255)" in msg and "(39, 40, 98)" in msg       # white, the navy body
    box = SimpleNamespace(colour=(0, 0, 0), background=None, spec=SimpleNamespace(id="lbl"))
    with pytest.raises(recipe.RecipeError) as r:
        recipe_ops._LabelBox._resolve_colours(box, mcf_147)
    assert r.value.code == "NO_COLOUR"
    assert "(255, 255, 255)" in str(r.value)


def test_colour_decode_refuses_a_reader_of_another_shape(mcf_136):
    """The reader's last merge (`add.w r0,r1,r3,lsl #16` at `0x25215A`) gone: not a 24-bit
    inline colour, refuse."""
    out = bytearray(mcf_136)
    sec = theme.guicode_section(mcf_136)
    off = sec.offset + 0x25215A - theme.guicode_base(sec)
    out[off:off + 4] = bytes.fromhex("00bf00bf")
    with pytest.raises(theme.ColourDecodeError, match="mode-3 reader 0x252140"):
        theme.colour_decode(bytes(out))
