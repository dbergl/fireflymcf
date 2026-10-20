"""`glyph-import` end to end against 13.6's fonts: the params it prints compile, and the glyph
`install_glyph` writes decodes to exactly the imported pixels."""
import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from fireflymcf import cli, glyphimport as gi, glyphs, recipe

BUILDS = Path(__file__).resolve().parent.parent / "builds"


@pytest.fixture
def ring(tmp_path):
    """A white ring on transparency, 200 px: a logo larger than any icon cell but 28's."""
    im = Image.new("RGBA", (220, 220), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse([10, 10, 209, 209], outline=(255, 255, 255, 255), width=24)
    path = tmp_path / "ring.png"
    im.save(path)
    return path


def _compile(data, op):
    doc = {"schema": recipe.SCHEMA, "base_sha256": hashlib.sha256(data).hexdigest(),
           "build_name": None, "ops": [op]}
    return recipe.compile_project(recipe.Project.from_json(json.dumps(doc)), data)


def test_import_picks_the_first_free_char_and_compiles(mcf_136, ring):
    params, rule = gi.import_glyph(mcf_136, ring, 27)
    assert rule == "alpha" and params["char"] == "E"        # 'A'..'D' are drawn glyphs
    assert len(params["rows"]) == 128                       # shrunk to slot 27's cell
    assert params["y"] == 0
    res = _compile(mcf_136, {"id": "g", "type": "install_glyph", "params": params})
    assert res.ok, res.errors
    font = glyphs.font_for_slot(res.data, 27)
    g = glyphs.decode_glyph(res.data, font.glyph_index(ord("E")))
    assert ["".join("%x" % v for v in row) for row in g.rows] == params["rows"]


def test_height_sets_the_size_and_the_bearing_centres_it(mcf_136, ring):
    params, _ = gi.import_glyph(mcf_136, ring, 27, "F", height=60)
    assert (len(params["rows"]), len(params["rows"][0])) == (60, 60)
    assert params["y"] == (128 - 60) // 2 and params["char"] == "F"


def test_an_occupied_char_is_refused_with_the_ops_code(mcf_136, ring):
    with pytest.raises(gi.ArtworkError, match="GLYPH_OCCUPIED"):
        gi.import_glyph(mcf_136, ring, 27, "A")


def test_a_build_that_used_the_first_char_gets_the_next(ring):
    """The reference coach's builds put the Starlink logo on slot 27 'E'."""
    data = (BUILDS / "mcf-13.6-LowVoltsMove-v52.bin").read_bytes()
    params, _ = gi.import_glyph(data, ring, 27, height=64)
    assert params["char"] == "F"


def test_a_refusal_not_about_the_char_is_reported_not_searched_past(ring):
    """A full-cell ring (~8 KB of record) is more than that grown build's GuiData takes."""
    data = (BUILDS / "mcf-13.6-LowVoltsMove-v52.bin").read_bytes()
    with pytest.raises(gi.ArtworkError, match="'F' in slot 27: RELOCATE_REFUSED.*--height"):
        gi.import_glyph(data, ring, 27)


def test_claim_block_reaches_icon_font_24(mcf_136, ring):
    with pytest.raises(gi.ArtworkError, match="claim-block"):
        gi.import_glyph(mcf_136, ring, 24)
    params, _ = gi.import_glyph(mcf_136, ring, 24, claim_block=True)
    assert params["claim_block"] is True and len(params["rows"]) == 48


def test_a_non_4bpp_slot_is_refused(mcf_136, ring):
    slots = {f["slot"] for f in gi.font_slots(mcf_136)}
    assert 27 in slots and 24 in slots
    other = next(s for s in range(34) if s not in slots and _has_font(mcf_136, s))
    with pytest.raises(gi.ArtworkError, match="4bpp"):
        gi.import_glyph(mcf_136, ring, other)


def _has_font(data, slot):
    try:
        glyphs.font_for_slot(data, slot)
        return True
    except Exception:
        return False


def test_cli_writes_an_op_that_compiles(tmp_path, ring, capsys):
    stock = BUILDS.parent / "firefly-13.6" / "mcf.bin"
    out, prev = tmp_path / "op.json", tmp_path / "p.png"
    assert cli.main(["glyph-import", str(stock), str(ring), "--slot", "27", "--height", "64",
                     "--as-op", "--id", "logo", "--out", str(out), "--preview", str(prev)]) == 0
    op = json.loads(out.read_text(encoding="utf-8"))
    assert op["id"] == "logo" and op["type"] == "install_glyph"
    assert _compile(stock.read_bytes(), op).ok
    assert Image.open(prev).size[1] >= 256
    assert "64x64 glyph from ring.png (alpha coverage), slot 27 char 'E', y 32" \
        in capsys.readouterr().out


def test_cli_list_and_errors(capsys, tmp_path):
    stock = str(BUILDS.parent / "firefly-13.6" / "mcf.bin")
    assert cli.main(["glyph-import", stock, "--list"]) == 0
    assert "  27   128" in capsys.readouterr().out
    assert cli.main(["glyph-import", stock]) == 2
    bad = tmp_path / "x.txt"
    bad.write_text("no", encoding="utf-8")
    assert cli.main(["glyph-import", stock, str(bad), "--slot", "27"]) == 1
    assert "cannot read" in capsys.readouterr().err
