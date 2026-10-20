"""`glyphimport`: an owner's picture into coverage rows, in every format an owner might hand over.

Data-free (no corpus), so it runs in the public CI on every platform; the end-to-end import
against a real font is `test_glyphimport_corpus.py`.
"""
import pytest
from PIL import Image, ImageDraw

from fireflymcf import glyphimport as gi

SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100">'
       '<rect x="20" y="10" width="160" height="80" fill="#0a0"/></svg>')


def _mark(mode, bg, fg, size=(120, 80)):
    """A filled rectangle `fg` on `bg`, inset 20 px: the simplest logo with a plain border."""
    im = Image.new(mode, size, bg)
    ImageDraw.Draw(im).rectangle([20, 20, size[0] - 21, size[1] - 21], fill=fg)
    return im


def _rows_of(tmp_path, im, name, cell=128, **kw):
    path = tmp_path / name
    im.save(path)
    cov, rule = gi.coverage(gi.load_image(path), kw.pop("ink", "auto"))
    return gi.rows(gi.fit(cov, cell, **kw)), rule


@pytest.mark.parametrize("name,mode,bg,fg,rule", [
    ("logo.png", "RGBA", (0, 0, 0, 0), (255, 255, 255, 255), "alpha"),
    ("logo.webp", "RGBA", (0, 0, 0, 0), (200, 30, 30, 255), "alpha"),
    ("logo.gif", "P", 0, 1, "dark"),      # palette, no transparency: index 0 black...
    ("logo.jpg", "RGB", (255, 255, 255), (0, 0, 0), "dark"),
    ("logo.bmp", "RGB", (0, 0, 0), (255, 255, 255), "light"),
    ("logo.tiff", "L", 250, 10, "dark"),
    ("flat.png", "RGB", (255, 255, 255), (20, 40, 160), "dark"),
])
def test_every_format_trims_to_the_mark(tmp_path, name, mode, bg, fg, rule):
    im = _mark(mode, bg, fg)
    if mode == "P":
        im.putpalette([255, 255, 255, 0, 0, 0] + [0] * 762)   # ...is white, index 1 black
    glyph_rows, used = _rows_of(tmp_path, im, name)
    assert used == rule
    w, h = len(glyph_rows[0]), len(glyph_rows)
    assert abs(w - 80) <= 2 and abs(h - 40) <= 2         # trimmed to the inset mark (JPEG rings)
    assert glyph_rows[h // 2][w // 2] in "ef"            # the mark is full ink, whatever colour


def test_transparent_png_uses_alpha_not_colour(tmp_path):
    """A black mark on transparency is ink; colour is discarded (the panel supplies the ink)."""
    glyph_rows, rule = _rows_of(tmp_path, _mark("RGBA", (255, 255, 255, 0), (0, 0, 0, 255)),
                                "black.png")
    assert rule == "alpha" and set("".join(glyph_rows)) == {"f"}


def test_svg_rasterises_with_transparency(tmp_path):
    pytest.importorskip("resvg_py")
    path = tmp_path / "logo.svg"
    path.write_text(SVG, encoding="utf-8")
    cov, rule = gi.coverage(gi.load_image(path))
    glyph_rows = gi.rows(gi.fit(cov, 48))
    assert rule == "alpha"
    assert len(glyph_rows) == 48 and len(glyph_rows[0]) == 96    # 2:1 kept, fit to the cell


def test_svg_is_sniffed_without_its_extension(tmp_path):
    pytest.importorskip("resvg_py")
    path = tmp_path / "logo.dat"
    path.write_text('<?xml version="1.0"?>\n' + SVG, encoding="utf-8")
    assert gi.load_image(path).mode == "RGBA"


def test_fit_shrinks_to_the_cell_and_never_enlarges():
    cov = _mark("L", 0, 255, (400, 200))
    assert gi.fit(cov, 48).size == (108, 48)         # 360x160 mark, shrunk to 48 tall
    assert gi.fit(_mark("L", 0, 255, (100, 60)), 128).size == (60, 20)   # small: not enlarged


def test_fit_honours_height_width_and_no_trim():
    cov = _mark("L", 0, 255, (120, 80))
    assert gi.fit(cov, 128, height=20).size == (40, 20)
    assert gi.fit(cov, 128, width=40).size == (40, 20)
    assert gi.fit(cov, 128, height=10, width=50).size == (50, 10)
    assert gi.fit(cov, 128, trim=False).size == (120, 80)


def test_fit_refuses_what_the_font_cannot_hold():
    cov = _mark("L", 0, 255, (120, 80))
    with pytest.raises(gi.ArtworkError, match="cell"):
        gi.fit(cov, 32, height=40)
    with pytest.raises(gi.ArtworkError, match="255 px"):
        gi.fit(cov, 128, height=20, width=300)
    with pytest.raises(gi.ArtworkError, match="no ink"):
        gi.fit(Image.new("L", (10, 10), 0), 128)


def test_a_photo_is_refused(tmp_path):
    """No plain border, no mark to find: refuse rather than guess."""
    im = Image.linear_gradient("L").resize((120, 80)).convert("RGB")
    path = tmp_path / "photo.jpg"
    im.save(path)
    with pytest.raises(gi.ArtworkError, match="plain colour"):
        gi.coverage(gi.load_image(path))


def test_alpha_on_an_opaque_image_is_refused(tmp_path):
    """The solid-block trap: an opaque image's alpha is all ink."""
    with pytest.raises(gi.ArtworkError, match="solid block"):
        gi.coverage(_mark("RGB", (255, 255, 255), (0, 0, 0)), "alpha")


def test_rows_refuse_an_empty_glyph():
    with pytest.raises(gi.ArtworkError, match="nothing"):
        gi.rows(Image.new("L", (8, 8), 15))


def test_rows_use_the_shared_quantisation():
    """`min(15, v >> 4)`, as `from_stock.py` and Studio's importer quantise."""
    im = Image.new("L", (4, 1))
    im.putdata([0, 15, 16, 255])
    assert gi.rows(im) == ["001f"]


def test_unreadable_file_is_refused(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("not a picture", encoding="utf-8")
    with pytest.raises(gi.ArtworkError, match="cannot read"):
        gi.load_image(path)


def test_preview_paints_the_coverage(tmp_path):
    out = tmp_path / "p.png"
    gi.preview(["0f", "f0"], out)
    im = Image.open(out)
    assert im.size == (16, 16)                       # 2 px tall, scaled 8x (the cap)
    assert im.getpixel((12, 4)) == (255, 255, 255) and im.getpixel((4, 4)) == (40, 44, 52)
