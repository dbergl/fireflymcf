"""Artwork into `install_glyph` parameters: the inverse of `glyphenc.export_glyph`.

A glyph carries **coverage**, not colour: one 4-bit value per pixel, painted later in whatever
ink the widget's text colour resolves to. So the importer's whole job is to decide, from an
owner's picture, which pixels are the mark:

- **An image with transparency** (PNG, WebP, GIF, an SVG): the alpha channel is the coverage,
  `min(15, alpha >> 4)` -- the rule the original build script applied to the Starlink SVG and
  Studio's importer (`studio/web/src/glyphimport.ts`) transcribes, so the three agree.
- **An opaque image** (JPEG, BMP, a flattened PNG): the background is read from the border,
  which must be one plain colour; a light background makes the dark pixels the mark and a dark
  one the light pixels. Coverage is each pixel's distance from the background. A photo, whose
  border is not one colour, is refused: there is no mark to find.

The art is then trimmed to its ink, scaled (never up, unless asked) to fit the font's cell, and
quantised. Taking only alpha from an opaque image would make every pixel full coverage: a solid
block on the panel, which is why that case is read by luminance and `--ink alpha` on it refused.
"""
from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageOps

from . import glyphs, metrics

INKS = ("auto", "alpha", "dark", "light")
#: Every coverage value below this quantises to 0 (`>> 4`), so it is not ink.
_INK_MIN = 16
#: The working resolution an SVG is rasterised at before it is trimmed and scaled down.
_SVG_RENDER = 1024
#: A plain border: this share of its pixels lie within `_BORDER_TOL` of the median.
_BORDER_SHARE, _BORDER_TOL = 0.9, 40


class ArtworkError(ValueError):
    """Artwork the importer cannot turn into a glyph; the message says what to supply."""


def _is_svg(path: Path, head: bytes) -> bool:
    if path.suffix.lower() in (".svg", ".svgz"):
        return True
    text = head.lstrip()[:256].lower()
    return text.startswith(b"<svg") or (text.startswith(b"<?xml") and b"<svg" in text)


def _rasterise_svg(path: Path) -> Image.Image:
    """An SVG as RGBA at `_SVG_RENDER` px on its longer side: `resvg_py` (the `full` extra),
    else Inkscape or `rsvg-convert` from PATH."""
    try:
        import resvg_py
    except ImportError:
        resvg_py = None
    if resvg_py is not None:
        probe = Image.open(io.BytesIO(resvg_py.svg_to_bytes(svg_path=str(path))))
        side = ("width" if probe.width >= probe.height else "height")
        png = resvg_py.svg_to_bytes(svg_path=str(path), **{side: _SVG_RENDER})
        return Image.open(io.BytesIO(png)).convert("RGBA")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "art.png"
        for tool, argv in (("inkscape", ["inkscape", str(path), "-w", str(_SVG_RENDER),
                                         "-o", str(out)]),
                           ("rsvg-convert", ["rsvg-convert", "-w", str(_SVG_RENDER),
                                             "-o", str(out), str(path)])):
            if shutil.which(tool):
                subprocess.run(argv, check=True, capture_output=True)
                return Image.open(out).convert("RGBA")
    raise ArtworkError("an SVG needs a rasteriser: pip install 'fireflymcf[full]' (resvg-py), "
                       "or put Inkscape or rsvg-convert on PATH, or export the SVG as a PNG "
                       "with a transparent background")


def load_image(path) -> Image.Image:
    """Any image Pillow reads (PNG, JPEG, WebP, GIF, BMP, TIFF, ...) or an SVG, upright."""
    path = Path(path)
    head = path.read_bytes()[:512]
    if _is_svg(path, head):
        return _rasterise_svg(path)
    try:
        im = Image.open(path)
        im.seek(0)                        # an animated GIF/WebP: its first frame
        im = ImageOps.exif_transpose(im)  # a phone JPEG's rotation flag
        im.load()
    except (OSError, SyntaxError) as e:
        raise ArtworkError("cannot read %s as an image: %s" % (path.name, e))
    return im


def _has_transparency(im: Image.Image) -> bool:
    if im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info:
        lo, _ = im.convert("RGBA").getchannel("A").getextrema()
        return lo < 255
    return False


def _border(gray: Image.Image) -> list[int]:
    w, h = gray.size
    px = gray.load()
    return ([px[x, 0] for x in range(w)] + [px[x, h - 1] for x in range(w)]
            + [px[0, y] for y in range(h)] + [px[w - 1, y] for y in range(h)])


def coverage(im: Image.Image, ink: str = "auto") -> tuple[Image.Image, str]:
    """The art's coverage as an 8-bit `L` image (255 = full ink), and which rule made it."""
    if ink not in INKS:
        raise ArtworkError("ink must be one of %s" % ", ".join(INKS))
    if ink == "alpha" or (ink == "auto" and _has_transparency(im)):
        if not _has_transparency(im):
            raise ArtworkError("--ink alpha, but the image has no transparency: every pixel "
                               "would be ink (a solid block); use --ink dark or --ink light")
        return im.convert("RGBA").getchannel("A"), "alpha"
    # Composite any (fully opaque) alpha away so a palette or LA image reads as it displays.
    gray = im.convert("RGBA")
    flat = Image.new("RGBA", gray.size, (255, 255, 255, 255))
    flat.alpha_composite(gray)
    gray = flat.convert("L")
    border = sorted(_border(gray))
    bg = border[len(border) // 2]
    plain = sum(abs(v - bg) <= _BORDER_TOL for v in border) / len(border)
    if ink == "auto":
        if plain < _BORDER_SHARE:
            raise ArtworkError(
                "the image's background is not one plain colour (only %d%% of its border "
                "matches), so the mark cannot be told from it; supply the logo on a plain or "
                "transparent background" % round(100 * plain))
        ink = "dark" if bg >= 128 else "light"
    if ink == "dark":
        cov = gray.point(lambda v: max(0, bg - v))
    else:
        cov = gray.point(lambda v: max(0, v - bg))
    # Stretch so the mark's strongest pixel is full ink: a navy logo on white is a logo, not a
    # 75% one. (Alpha art is left alone; its partial transparency is the artist's.)
    _, peak = cov.getextrema()
    if peak:
        cov = cov.point(lambda v: v * 255 // peak)
    return cov, ink


def fit(cov: Image.Image, cell: int, *, height: int | None = None, width: int | None = None,
        trim: bool = True) -> Image.Image:
    """Trim `cov` to its ink, then scale it: to `height`/`width` if given (aspect kept when
    only one is), else down to fit `cell` (never up). Refuses a result the font cannot hold."""
    if trim:
        box = cov.point(lambda v: 255 if v >= _INK_MIN else 0).getbbox()
        if box is None:
            raise ArtworkError("the artwork has no ink: nothing would draw")
        cov = cov.crop(box)
    w, h = cov.size
    if height and width:
        size = (width, height)
    elif height:
        size = (max(1, round(w * height / h)), height)
    elif width:
        size = (width, max(1, round(h * width / w)))
    elif h > cell:
        size = (max(1, round(w * cell / h)), cell)
    else:
        size = (w, h)
    if size[1] > cell:
        raise ArtworkError("a %d px tall glyph does not fit the font's %d px cell; pass "
                           "--height %d or less" % (size[1], cell, cell))
    if size[0] > 255:
        raise ArtworkError("a %d px wide glyph exceeds the 255 px a glyph can be; pass "
                           "--width 255 or less" % size[0])
    if size != cov.size:
        cov = cov.resize(size, Image.LANCZOS)
    return cov


def rows(cov: Image.Image) -> list[str]:
    """`install_glyph`'s rows: one hex digit, `min(15, v >> 4)`, per pixel."""
    px = cov.load()
    w, h = cov.size
    out = ["".join("%x" % min(15, px[x, y] >> 4) for x in range(w)) for y in range(h)]
    if not any(c != "0" for r in out for c in r):
        raise ArtworkError("the artwork quantised to nothing at this size; make it larger")
    return out


def preview(glyph_rows: list[str], path, ink=(255, 255, 255), paper=(40, 44, 52)) -> None:
    """The glyph as the panel would paint it in `ink`, scaled up to at least 256 px tall."""
    h, w = len(glyph_rows), len(glyph_rows[0])
    scale = max(1, min(8, -(-256 // h)))
    im = Image.new("RGB", (w, h), paper)
    px = im.load()
    for y, row in enumerate(glyph_rows):
        for x, c in enumerate(row):
            a = int(c, 16) / 15
            px[x, y] = tuple(round(p + (i - p) * a) for p, i in zip(paper, ink))
    im.resize((w * scale, h * scale), Image.NEAREST).save(path)


def font_slots(data: bytes) -> list[dict]:
    """Every 4bpp font `install_glyph` can write to, with its cell height and character range."""
    out = []
    for slot in range(34):
        try:
            font = glyphs.font_for_slot(data, slot)
        except Exception:
            continue
        if glyphs.font_bpp(data, font) != 4:
            continue
        out.append({"slot": slot, "cell": metrics.font_metrics(data, slot).height,
                    "first": chr(font.first), "last": chr(font.last)})
    return out


#: The refusals that mean only "this character is taken"; the search moves to the next one.
_CHAR_TAKEN = ("GLYPH_OCCUPIED", "GLYPH_STUB_SHARED")


def _try(data: bytes, params: dict):
    from . import recipe
    doc = {"schema": recipe.SCHEMA, "base_sha256": hashlib.sha256(data).hexdigest(),
           "build_name": None, "ops": [{"id": "glyph", "type": "install_glyph",
                                         "params": params}]}
    result = recipe.compile_project(recipe.Project.from_json(json.dumps(doc)), data)
    return None if result.ok else result.errors[0]


def import_glyph(data: bytes, art, slot: int, char: str | None = None, *, ink: str = "auto",
                 height: int | None = None, width: int | None = None, trim: bool = True,
                 claim_block: bool = False) -> tuple[dict, str]:
    """`install_glyph` params for `art` in `slot`, proven by compiling them against `data`.

    With no `char`, the first character of the font `install_glyph` accepts is taken. Returns
    the params and the coverage rule used (`alpha`, `dark` or `light`)."""
    font = glyphs.font_for_slot(data, slot)
    if glyphs.font_bpp(data, font) != 4:
        raise ArtworkError("slot %d is not a 4bpp icon font; `glyph-import --list` shows "
                           "the ones that are" % slot)
    cell = metrics.font_metrics(data, slot).height
    cov, rule = coverage(load_image(art), ink)
    glyph_rows = rows(fit(cov, cell, height=height, width=width, trim=trim))
    params = {"slot": slot, "char": None, "y": (cell - len(glyph_rows)) // 2,
              "rows": glyph_rows}
    if claim_block:
        params["claim_block"] = True
    candidates = [char] if char else [chr(c) for c in range(font.first, font.last + 1)]
    for c in candidates:
        params["char"] = c
        err = _try(data, params)
        if err is None:
            return params, rule
        if char or err["code"] not in _CHAR_TAKEN:
            # Not about the character (a relocation the image cannot take, say): every other
            # character would meet it too, so say what it is rather than search on.
            hint = ("; a smaller --height makes a smaller record"
                    if err["code"] == "RELOCATE_REFUSED" else "")
            raise ArtworkError("install_glyph refuses %r in slot %d: %s %s%s"
                               % (c, slot, err["code"], err["message"], hint))
    raise ArtworkError("slot %d has no character install_glyph can claim%s" % (
        slot, "" if claim_block else "; --claim-block also takes its unused placeholder blocks"))
