"""Render a GuiData screen to a PNG, so GUI edits can be previewed without flashing.

Three layers, deliberately separated so the two compositors — hand-authored *B* and
code-derived *A* — share the decoder and rasteriser:

    1. widget decoder  -- bytes -> primitives in a widget's *local* coordinates. Purely
                          data-driven; the same under A and B. `decode_widget`.
    2. compositor      -- produces `Placement`s: primitives plus an *absolute* screen anchor.
                          THE LAYER THAT DIFFERS between A and B.
                          * B (`climate_placements`): a per-screen table calibrated by eye to
                            a hardware photo (`build/climatepage.jpeg`); every entry carries a
                            `src` tag so a calibrated anchor is never mistaken for a derived one.
                          * A (`screen_placements`): the byte-exact layout engine (`layout.py`
                            → `records.py`), walked from an arbitrary object-table `root` --
                            no calibration, no per-screen anchor table. Positions, font sizes
                            (`metrics.font_metrics`), kerned text width + alignment
                            (`metrics.text_width` / the record's align byte) and the panel's own
                            icon glyphs (`glyphs.py`) all come from the bytes. Only colour stays
                            a heuristic (chrome colour is open thread 1) and runtime values
                            (temperatures, clock) are absent because they are not stored.
                            `climate_placements_decoded` is a thin wrapper over it that
                            defaults `root` to the Climate page (`layout.climate_root`) --
                            any other page is `screen_placements(data, that_root)` directly,
                            or `render_screen(...)`/`cli render --root/--page` at the file level.
    3. rasteriser      -- absolute-placed primitives -> an 800x480 image. Shared. `rasterize`.

The IR between the layers is `Prim` / `Placement`; keep it stable.

Colour is explicitly *not* the point of this tool (the owner asked to judge layout, not
colour); the palette below is eyeballed from the photo and labelled approximate, used only
where a widget carries no colour of its own. Where a record's own mode-3 inline BGR fill is
present (`records.WRecord.colors`, `GUICODE.md` Finding 18), that is used instead and tagged
`color_src="decoded"` (`_inline_fill`). A mode-7 fill (the colour-table index chrome uses) is
also decoded when the index resolves through `theme.mode7_palette` -- the chrome-colour dig
(2026-08-15) recovered the GuiCode theme-init constants those indices point at; an index the
extractor could not resolve is a named residual and stays `color_src="heuristic"`
(`_decoded_fill`). Text and boxes with neither are `color_src="heuristic"`.

Proportional text in the decoded compositor (`climate_placements_decoded`/`screen_placements`)
is drawn from the panel's own font bitmaps (`glyphs.py`, at the decoded font slot --
`records.WRecord.font`, the R38 selector) via `panel_text_images`, each character positioned by
the exact kerned advance `metrics.glyph_positions` replays (the same `FUN_002518F8` measurer the
alignment code uses -- no new positions invented). Tagged `font_src="panel"` -- this is now the
norm: `GUICODE.md` Finding 22's RLE rewrite of `glyphs.decode_glyph` closed the "thin/tall glyph
decodes short" gap that used to force every real label (CLIMATE, Cool, High, Low, Auto, …) to
the DejaVu stand-in, and the vestigial `glyphs.is_reliable` gate that used to detect it has been
retired (it was always `True` post-rewrite; see `glyphs.py`'s history note). DejaVu Sans remains
only as a *labelled fallback* (`font_src="heuristic"`) for: a genuine per-character resolution
failure (the font/slot itself missing, or a character whose kerning can't be measured -- the
same trigger `metrics.text_width` uses; a blank/space glyph, which genuinely declares height 0
and makes `decode_glyph` raise, is a normal instance of this, not corruption), the hand-
calibrated compositor B (`climate_placements`, which never reads panel bitmaps at all), and
runtime values (temperatures, clock) that are not stored in the file to begin with. Icon-font
glyphs (chevrons, snowflake, nav rail, …) already used `glyphs.py` before this; unchanged here.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import glyphs, guidata, images, records

PANEL_W, PANEL_H = 800, 480

# Approximate palette, eyeballed from build/climatepage.jpeg. Not proven-from-code.
#
# SCOPE, 2026-08-18: this is now used ONLY by the calibrated compositor `climate_placements`
# (approach B, kept deliberately as a parallel implementation) and as the last-resort text-ink
# fallback, which since the ink fix fires only when a label carries no resolvable colour mode at
# all. The decoded compositor `screen_placements` paints NO chrome from it -- the chrome-colour
# dig (2026-08-15, `theme.mode7_palette`) closed open thread 1 (`docs/CLOSED-THREADS.md`), and unresolved fills are
# left unpainted rather than guessed. `COL["outline"]` is likewise no longer used for box borders;
# those come from `_decoded_fill(..., border=True)`.
COL = {
    "crimson": (0xD6, 0x33, 0x6C),   # header bar, nav rail, CLIMATE tab
    "navy":    (0x1C, 0x1C, 0x8C),   # body background
    "button":  (0x2E, 0x6D, 0xB4),   # blue buttons / Starlink
    "cell":    (0x34, 0x3C, 0xB4),   # mode-grid cells
    "cyan":    (0x25, 0xC0, 0xE0),   # selected cell, chevrons
    "white":   (0xFF, 0xFF, 0xFF),
    "black":   (0x00, 0x00, 0x00),
    "outline": (0xFF, 0xFF, 0xFF),   # button-box border stroke (render-fidelity Task 3) --
                                     # white so it reads against COL["button"] fill even when
                                     # a button sits on a same-heuristic-coloured container.
}

_FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
_FONT_BLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
#: (bold, regular) file names tried after the Debian path, found by Pillow in the platform's
#: own font directories: DejaVu elsewhere on Linux, Arial on Windows and macOS.
_FONT_NAMES = (("DejaVuSans-Bold.ttf", "DejaVuSans.ttf"), ("arialbd.ttf", "arial.ttf"),
               ("Arial Bold.ttf", "Arial.ttf"))
_font_cache: dict[tuple[bool, int], ImageFont.FreeTypeFont] = {}


def _font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    """The text-fallback font (a glyph the panel file cannot supply). Any machine has one: the
    last resort is Pillow's own bundled font, which needs no system font at all."""
    key = (bold, size)
    if key not in _font_cache:
        paths = [_FONT_BLD if bold else _FONT_REG] + [n[0 if bold else 1] for n in _FONT_NAMES]
        for path in paths:
            try:
                _font_cache[key] = ImageFont.truetype(path, size)
                break
            except OSError:
                continue
        else:
            _font_cache[key] = ImageFont.load_default(size)
    return _font_cache[key]


def glyph_image(data: bytes, slot: int, char: str, color: tuple,
                target_h: int | None = None,
                table: "glyphs.GlyphTable | None" = None) -> Image.Image | None:
    """A panel glyph as an RGBA image tinted `color`, from `glyphs.py`.

    The coverage (0..15, whatever the font's own bpp) becomes the alpha channel, so the real
    panel bitmap is drawn -- pixel-faithful since `GUICODE.md` Finding 22's RLE rewrite of
    `glyphs.decode_glyph`, proportional text included. Returns None only for a genuine
    resolution failure: the font/slot itself is missing, the character's index is out of
    range, or the glyph is a blank/space (declares height 0, `decode_glyph` raises) or carries no
    ink at all. **Thin glyphs are drawn**: a 5x1 hyphen is real type, not noise. Optionally
    scaled to `target_h` pixels tall.

    `table` (`glyphs.resolve_glyph_table`), if given, skips the per-call glyph-table scan --
    pass one when drawing many glyphs from the same, unmutated `data` in one render pass.
    """
    _, img = _glyph_and_image(data, slot, char, color, table=table)
    if img is None:
        return None
    if target_h:
        h = img.height
        if h:
            img = img.resize((max(1, round(img.width * target_h / h)), target_h),
                             Image.BILINEAR)
    return img


def _glyph_and_image(data: bytes, slot: int, char: str, color: tuple,
                     table: "glyphs.GlyphTable | None" = None,
                     ) -> "tuple[glyphs.Glyph | None, Image.Image | None]":
    """Decode + tint one glyph at native size, returning both the decoded `Glyph` and its RGBA
    image (or `(None, None)` on the same resolution failures `glyph_image` returns `None` for).

    The `Glyph` is returned so a caller can read its per-glyph vertical bearing `Glyph.y`
    (header `+0x0c`) -- the cell-top-to-bitmap-top offset the blitter `FUN_002510C0` places each
    character at (`extractions/blitter.txt` line 54). `glyph_image` (single icon, may scale) and
    `panel_text_images` (proportional text, needs the bearing) both build on this.
    """
    try:
        font = glyphs.font_for_slot(data, slot, table=table)
        # this call already resolved `font`, so its descriptor byte is known -- pass `bpp`
        # explicitly rather than have `decode_glyph` re-derive it via its own O(fonts)
        # `_font_for_index` scan (`glyphs.font_bpp` just reads `font.offset + OFF_BPP`).
        bpp = glyphs.font_bpp(data, font)
        g = glyphs.decode_glyph(data, font.glyph_index(ord(char)), table=table, bpp=bpp)
    except (glyphs.GlyphError, ValueError, IndexError):
        return None, None
    # Reject only a glyph with NO INK. There used to be a `height < 2 or width < 2` guard here,
    # described as "too small to matter" -- a fitted judgement, and wrong: it discarded 30 real
    # inked glyphs across the corpus, every small-font hyphen and underscore among them (slot 11
    # '-' is 5x1, slot 0 'i'/'l' are 1x7). That is why the `G12 OUTPUTS` rows rendered as
    # "03  Awning EXT" instead of "03 - Awning EXT" (owner, 2026-08-19). A blank glyph is already
    # handled upstream: `decode_glyph` raises on a declared height/width of 0, which is how a
    # space arrives here.
    if not any(v for row in g.rows for v in row):
        return None, None
    alpha = (np.array(g.rows, dtype=np.uint8) * 17)          # 0..15 -> 0..255
    h, w = alpha.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[..., 0], rgba[..., 1], rgba[..., 2] = color
    rgba[..., 3] = alpha
    return g, Image.fromarray(rgba, "RGBA")


def panel_text_images(data: bytes, slot: int, text: str, color: tuple,
                      table: "glyphs.GlyphTable | None" = None,
                      ) -> list[tuple[int, int, Image.Image]] | None:
    """Per-character tinted glyph images for `text` set in the panel's own font `slot`,
    positioned by `metrics.glyph_positions` -- the exact kerned-advance replay `text_width`'s
    alignment already uses, kept per-character instead of summed to one total. Returns
    `[(dx, dy, image), …]`: `dx` the kerned horizontal advance relative to the string's own local
    origin (position 0), `dy` the glyph's own vertical bearing (`glyphs.Glyph.y`, header `+0x0c`)
    -- the cell-top-to-bitmap-top offset the blitter `FUN_002510C0` places each character at
    (`extractions/blitter.txt` line 54: `sVar3 + param_2 + glyph[0xc]`). Returns `None` only if
    the font/slot or the string's kerning can't be resolved at all (a glyph missing or the shared
    out-of-range placeholder) -- the same trigger `metrics.text_width` already uses.

    The `dy` is why proportional text sets on a real baseline: x-height letters ('a','o') carry a
    larger bearing than the caps and so sit lower while sharing the baseline, and descenders
    ('g','y') carry the x-height bearing but a taller bitmap that drops past it. An earlier
    version returned only `(dx, image)` and the rasteriser drew every glyph at one shared `ay`,
    which top-aligned x-height letters with the caps and lifted descenders (`metrics.glyph_positions`
    still notes only the *horizontal* advance is its concern; the vertical bearing is applied
    here).

    This used to also bail the *whole string* whenever any character decoded unreliably
    (`glyphs.is_reliable`) -- the pre-`GUICODE.md`-Finding-22 gap where a repeatable class of
    thin/tall glyphs decoded to a fraction of their declared height. That gate is retired:
    `glyphs.decode_glyph`'s RLE rewrite always yields the full declared row count for every
    glyph, so the condition it detected no longer exists -- every character below now draws the
    panel's own bitmap, not a fitted average.

    A character whose own bitmap doesn't decode to a real image at all (`glyph_image` returns
    `None` -- a blank/space glyph genuinely declares height 0, or its index is out of range) is
    simply omitted: its advance was still counted, so later characters are not shifted, it just
    draws nothing there -- which is what the panel does for a blank glyph too.

    `table` (`glyphs.resolve_glyph_table`), if given, is threaded through every glyph lookup
    below so a whole render pass over many labels scans the glyph table once, not once per
    character -- see `glyphs.find_glyph_table`'s docstring for why this replaced an id(data)-
    keyed cache instead of living inside `glyphs.py` as a cross-call cache.
    """
    from . import metrics
    try:
        positions = metrics.glyph_positions(data, slot, text, table=table)
    except glyphs.GlyphError:
        return None
    if positions is None:
        return None
    # Apply the per-glyph HORIZONTAL bearing `Glyph.x` (header +0x0a) as well as the vertical
    # one. `metrics.glyph_positions` returns PEN positions; the blitter draws each bitmap at
    # `pen + glyph[0x0a]`, which is why `metrics.text_width` seeds with `-first.x` and closes
    # with `last.x + last.width`. Omitting it here (on the since-corrected claim that "Glyph.x
    # is 0 for the proportional text fonts in this corpus") mis-set 204 of 1989 printable
    # glyphs -- 10%, and concentrated exactly on the narrow characters '.' ',' ':' '1' 'i' 'l'
    # '-' '(' where a bearing does the most work. Owner-reported as bad kerning on screen 24,
    # 2026-08-19.
    #
    # `first_x` re-bases the run so the first glyph's INK starts at dx 0, which is the frame
    # `text_width` measures in -- so a drawn run's extent equals its measured width, and every
    # alignment path that compares the two stays consistent.
    from . import metrics
    hdrs = [metrics._glyph_hdr(data, glyphs.font_for_slot(data, slot, table=table),
                               ord(ch), table=table) for ch in text]
    first_x = 0
    for h in hdrs:
        if h is not None:
            first_x = h.x
            break

    out: list[tuple[int, int, Image.Image]] = []
    for ch, dx in zip(text, positions):
        g, img = _glyph_and_image(data, slot, ch, color, table=table)
        if img is not None:
            out.append((dx + g.x - first_x, g.y, img))
    return out


@dataclass
class Prim:
    """One drawable, in the coordinates of whatever contains it (widget-local until a
    Placement anchors it, then absolute)."""
    kind: str                         # 'rect' | 'rrect' | 'text' | 'icon' | 'touch'
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0
    radius: int = 0
    color: tuple = COL["white"]
    text: str = ""
    size: int = 18
    align: str = "left"               # 'left' | 'center' | 'right'
    valign: str = "top"               # 'top' | 'middle' (vertical text anchor)
    bold: bool = True
    src: str = "synthetic"            # provenance: 'synthetic' or 'file@0x…'
    glyph_slot: int = -1              # for kind 'glyph': the icon-font slot; text holds char
    image: object = None              # for kind 'glyph': a pre-rendered RGBA PIL image
    color_src: str = "heuristic"      # 'decoded' when `color` is a record's own mode-3 inline
                                       # BGR fill or a resolved mode-7 colour-table index
                                       # (`theme.mode7_palette`); 'heuristic' when it is the
                                       # eyeballed palette
    clip: tuple | None = None         # for text/glyph from a wtype-11/13 record: the INCLUSIVE
                                      # (x0, y0, x1, y1) the panel clips it to. `FUN_002559A0`
                                      # intersects the record's box with the enclosing clip
                                      # (`FUN_00253E38` at 0x255d..), draws every line, then
                                      # restores. Inclusive is why a box with x0 == x1 leaves
                                      # exactly ONE pixel column -- which is what the coach drew
                                      # for v20's boxless mark.
    outline: bool = False             # for kind 'rect'/'rrect': stroke only, no interior fill
                                       # (the tank gauge well -- render-fidelity Task 1)
    line_w: int = 2                   # for kind 'rect'/'rrect' with `outline`/`stroke`: the border
                                       # width in px -- the box primitive's byte-derived edge width
                                       # (a tank well strokes its fillA border at coord4; Finding 28)
    stroke: tuple | None = None       # for kind 'rect'/'rrect': an ADDITIONAL border colour
                                       # drawn around the fill (fill still drawn, unlike
                                       # `outline`) -- the button-box visibility fix
                                       # (render-fidelity Task 3): a mode-0/7 button record's
                                       # own colour is unrecoverable (runtime), so the box would
                                       # otherwise paint indistinguishably from a same-heuristic-
                                       # coloured sibling/container behind it; the stroke is what
                                       # makes its own decoded geometry visible as a boundary.
    font_src: str = "heuristic"       # for kind 'text': 'panel' when drawn from the panel's own
                                       # glyph bitmaps at the decoded font slot; 'heuristic' when
                                       # it falls back to the DejaVu stand-in (font unknown, or a
                                       # glyph/kerning in the string didn't resolve)
    glyph_images: list = field(default_factory=list)   # for kind 'text' + font_src='panel':
                                       # [(dx, dy, RGBA image), …] from `panel_text_images`; dx
                                       # relative to this Prim's x, dy the glyph's own vertical
                                       # bearing (glyph.y) relative to this Prim's y (the line's
                                       # cell top) -- so baselines align and descenders drop
    corners: tuple | None = None      # for kind 'rrect': per-corner rounding
                                       # (top_left, top_right, bottom_right, bottom_left); None =
                                       # all four rounded. Used by the tank level fill, which rounds
                                       # only its BOTTOM two corners to follow the well (the liquid
                                       # surface stays flat). NB type-3 boxes are NOT rounded at all
                                       # (square 1px frame, FUN_00255e90) -- they draw as 'rect'.


@dataclass
class Placement:
    """A group of primitives and the absolute screen anchor they are drawn relative to."""
    name: str
    x: int
    y: int
    prims: list[Prim] = field(default_factory=list)
    src: str = "synthetic"


# --------------------------------------------------------------------------------------
# Layer 1: widget decoder (data-driven; identical under A and B)
# --------------------------------------------------------------------------------------

def _geometry_u16s(data: bytes, rec: guidata.Record) -> list[int]:
    """The presence-masked geometry u16s of a record, in stored order."""
    import struct
    vals: list[int] = []
    off = rec.offset + guidata.HEAD_SIZE
    for bit in range(16):
        if rec.flags & guidata.GEOMETRY_FLAG_BITS & (1 << bit):
            vals.append(struct.unpack_from("<H", data, off)[0]); off += 2
    for bit in range(32):
        if rec.extra & guidata.EXTRA_U16_BITS & (1 << bit):
            vals.append(struct.unpack_from("<H", data, off)[0]); off += 2
    return vals


_LABEL_OK = frozenset(list(range(0x20, 0x7F)) + [0x0A])


def label_text(data: bytes, anchor: int, limit: int = 12) -> tuple[str, int] | None:
    """Read the label of a draw-element whose `01 <font> ff` selector is at `anchor`.

    The bytes between `ff` and the length prefix vary by record (`65 00 02 02 …` for the
    Starlink caption, `02 02 0a 00` for a grid label), so the length byte is found by scan,
    not a fixed offset: the first byte in the window that is a valid length for a printable,
    NUL-terminated run. Returns (text, length_offset) or None. This is how the panel finds
    text too -- by the selector pattern, independent of the containing opcode.
    """
    if data[anchor] != 0x01 or data[anchor + 2] != 0xFF:
        return None
    for lp in range(anchor + 3, anchor + 3 + limit):
        ln = data[lp]
        if 1 <= ln <= 40 and data[lp + 1 + ln] == 0x00:
            raw = data[lp + 1:lp + 1 + ln]
            if set(raw) <= _LABEL_OK:
                return raw.decode("ascii"), lp
    return None


@dataclass(frozen=True)
class Label:
    offset: int          # the `01 <font> ff` selector offset
    x: int
    y: int
    font: int
    text: str


def scan_labels(data: bytes, start: int, end: int) -> list[Label]:
    """Every `01 <font> ff … <len> <text> 00` draw-label in [start, end).

    Robust to unknown opcodes: it keys on the selector pattern, not on record walking, so
    it reads labels the opcode-tail parser stalls before. The two u16 before the selector
    are the element's local (x, y) -- the same fields probe 1 moved.
    """
    import struct
    out: list[Label] = []
    o = start
    while o < end - 4:
        if data[o] == 0x01 and data[o + 2] == 0xFF:
            got = label_text(data, o, limit=min(12, end - o - 3))
            if got is not None and o >= start + 4:
                text, _ = got
                x = struct.unpack_from("<H", data, o - 4)[0]
                y = struct.unpack_from("<H", data, o - 2)[0]
                out.append(Label(o, x, y, data[o + 1], text))
        o += 1
    return out


def decode_widget(data: bytes, file_off: int) -> list[Prim]:
    """Decode a widget object (`<count> <records…>`) into local-coordinate primitives.

    Field *semantics* per opcode are only partly pinned (the probes fixed the text x/y and
    a few box/touch offsets, not every field), so this maps what is known and leaves the
    rest for later refinement -- it never invents a coordinate it cannot read. Every Prim
    is tagged with the record's file offset for provenance.
    """
    prims: list[Prim] = []
    count = data[file_off]
    off = file_off + 1
    for _ in range(count):
        try:
            rec = guidata.parse_record(data, off, len(data))
        except guidata.UnknownRecord:
            break
        g = _geometry_u16s(data, rec)
        tag = f"file@{rec.offset:#x}"
        if rec.opcode == guidata.TEXT_OPCODE and rec.label is not None:
            x, y = (g[0], g[1]) if len(g) >= 2 else (0, 0)
            prims.append(Prim("text", x=x, y=y, text=rec.label, color=COL["white"],
                              src=tag))
        elif rec.opcode == 0x21:                       # rounded button frame
            w, h = (g[-2], g[-1]) if len(g) >= 2 else (0, 0)
            # the corner radius is byte-derived (record +0x32 = geom[4] = `corner_radius`), not a
            # fixed 8: the shape drawer FUN_00254F26 offsets its arc centres by exactly this value
            # (GUICODE Finding 73). The decoded compositor `screen_placements` already reads it;
            # this legacy layer-1 path now matches (the Bath-Fan button is radius 5, not 8).
            cr = records.decode_record(data, rec.offset).corner_radius
            prims.append(Prim("rrect", x=0, y=0, w=w, h=h, radius=max(0, cr or 0),
                              color=COL["button"], src=tag))
        elif rec.opcode == 0x04:                       # visible box [x?, w, h, radius?]
            w, h = (g[-2], g[-1]) if len(g) >= 2 else (0, 0)
            prims.append(Prim("rect", x=0, y=0, w=w, h=h, color=COL["cell"], src=tag))
        elif rec.opcode == guidata.IMAGE_OPCODE:       # touch / image region
            prims.append(Prim("touch", x=0, y=0, w=0, h=0, src=tag))
        off = rec.end
    return prims


# --------------------------------------------------------------------------------------
# Layer 2: compositor (approach B -- hand-authored, calibrated to the photo)
# --------------------------------------------------------------------------------------

_NAV_ICONS = ["⌂", "⚡", "\U0001F4A1", "\U0001F321", "⚙"]  # home power light temp gear

# Climate-page text sourced from the file: name -> the `01 <font> ff` selector offset in
# 13.6. The TEXT is read from the bytes there -- a same-size rename renders -- while WHICH
# label (this offset) and WHERE it sits (the calibrated grid below) are the boundary approach
# A dissolves: A derives both from FUN_00247BF8 instead of this hand-picked table.
_CLIMATE_TEXT = {
    "tab":       0x92bac,      # 'CLIMATE'
    "mode-cool": 0x99863,      # left column, mode
    "fan-high":  0x997f5,      # right column, fan speed
    "fan-low":   0x997cd,
    "fan-auto":  0x9981d,
}
_STARLINK_CAPTION = 0x97237                  # 'Bath Fan' selector (patched -> 'Starlink')


_STARLINK_ICON = 0x9721f                     # the 'E' icon-font selector in the Starlink button
_COOL_ICON = 0x99848                          # slot-23 '3' snowflake, inside the Cool cell

# The header clock's value widget reads this variable slot -> SRAM 0x200399DC, written with
# minutes-since-midnight unpacked from a received CAN time message (GuiCode 0x00249912-24, dig
# 2026-08-16). It uniquely marks the clock, so a value widget on this slot is identified as the
# clock byte-derived -- not by top-right screen position. value_slot numbers are shared across
# builds (CLAUDE.md), so 226 is the clock on all three.
CLOCK_SLOT = 226

# The 'V' voltage value-widgets, each keyed by its byte-derived value_slot (2026-08-16 dig; slots
# stable across builds). 205 = HOME Chassis (0x2003A272); 209 = the House/battery register
# (0x2003A594) -- byte-certain the SAME register drives HOME "House" (obj 443) AND the ELECTRICAL
# "Battery Voltage" box (obj 829), both structurally identical (a slot-208 visibility switch + the
# slot-209 value). The owner reports these show DIFFERENT physical batteries -- HOME the 12V house
# (never 48V), ELECTRICAL the 48V pack -- i.e. two different CAN messages feed 0x2003A594 upstream
# in the RV-C receive filter, a split the GuiData display graph does not encode. So the slot-209
# value is page-selected in cli (`_cmd_render`): the 12V `house_voltage` on HOME, the 48V
# `coach_voltage` on ELECTRICAL. 272 / 274 = ELECTRICAL AGS "Start at" / "Stop at" setpoints.
_VOLTAGE_SLOTS = {205: "chassis_voltage", 209: "coach_voltage",
                  272: "start_volts", 274: "stop_volts"}

# The ELECTRICAL run-time caption is a wtype-13 dynamic string filled at config time from a
# 32-byte-strided GuiCode mode table (GUICODE Finding 31): each entry is a 4-byte indicator
# ("AES"/"AGS") 8 bytes ahead of its null-terminated caption. slot 129 shows the indicator,
# slot 269 the caption.
AES_CAPTION_SLOT = 269
AES_INDICATOR_SLOT = 129
# The generator-mode register: the value the HOME/ELECTRICAL slot-37 switches read (GUICODE
# Finding 31). 1 = AGS, 2 = AES single, 3 = AES dual, 0 = none. Both the wtype-13 label text and
# the switch-selected page content read this one slot, so a label derived from it cannot disagree
# with the content. Slot numbers are build-stable (CLAUDE.md); AES itself is 13.6+ (11.23 lacks it).
MODE_SLOT = 37


def aes_ags_labels(data: bytes) -> dict:
    """The AES/AGS mode labels read byte-derived from the GuiCode table (GUICODE Finding 31):
    `{"AES": ("AES", "Max Engine\\nRun Time"), "AGS": ("AGS", "Max Gen\\nRun Time")}`. Located
    by the standalone `<TAG>\\0` + 4 pad + caption layout, so no hardcoded offsets. A build
    without the AES table (11.23, pre-AES) yields only the entries it actually contains."""
    out: dict = {}
    for tag in (b"AES", b"AGS"):
        marker = tag + b"\x00\x00\x00\x00\x00"        # TAG\0 + 4 pad, caption at marker+8
        i = data.find(marker)
        if i < 0:
            continue
        start = i + 8
        end = data.find(b"\x00", start)
        if end < 0 or end - start > 64:
            continue
        try:
            out[tag.decode()] = (tag.decode(), data[start:end].decode("ascii"))
        except UnicodeDecodeError:
            continue
    return out

# Icon-font glyphs identified in slot 23 (build/slot23-icons.png): the panel's real icons.
ICON_SLOT = 23
ICON = {"snowflake": "3", "fan": "4", "chevron-up": "5", "chevron-down": "6",
        "wrench": "B", "wifi": "@"}


def _file_text(data: bytes, offset: int, default: str) -> tuple[str, str]:
    """(text, provenance). Reads the label at a selector offset; falls back if it moved."""
    got = label_text(data, offset)
    if got is None:
        return default, "synthetic"
    return got[0], f"file@{offset:#x}"


def _file_glyph_prim(data: bytes, selector: int, x: int, y: int, target_h: int,
                     color=COL["white"], fallback: str = "•") -> Prim:
    """A glyph Prim whose slot+char are read from the file at a `01 <font> ff` selector.

    Both which icon (the char) and its font come from the bytes, so this is data-driven; the
    glyph itself is rendered by glyphs.py. Falls back to a text placeholder if the selector
    moved or the glyph does not decode.
    """
    if data[selector] == 0x01 and data[selector + 2] == 0xFF:
        got = label_text(data, selector)
        if got is not None and len(got[0]) == 1:
            slot, char = data[selector + 1], got[0]
            img = glyph_image(data, slot, char, color, target_h=target_h)
            if img is not None:
                return Prim("glyph", x=x, y=y, text=char, glyph_slot=slot, image=img,
                            align="center", src=f"file@{selector:#x}")
    return Prim("text", x=x, y=y, text=fallback, size=target_h, align="center",
                color=color)


def _icon_prim(data: bytes, name: str, x: int, y: int, target_h: int,
               color=COL["white"], fallback: str = "•") -> Prim:
    """A real panel glyph chosen by name (a selection, not a file reference).

    Used where the icon is composed at runtime / by chrome, so there is no GuiData selector
    to read (grid fan header, setpoint chevrons, nav rail). The GLYPH is the panel's own
    (slot 23); the CHOICE of which is ours, so it is tagged `synthetic-glyph`, distinct from
    the `file@` icons whose char is read from the bytes.
    """
    img = glyph_image(data, ICON_SLOT, ICON[name], color, target_h=target_h)
    if img is not None:
        return Prim("glyph", x=x, y=y, text=ICON[name], glyph_slot=ICON_SLOT, image=img,
                    align="center", src="synthetic-glyph")
    return Prim("text", x=x, y=y, text=fallback, size=target_h, align="center", color=color)


def climate_placements(data: bytes) -> list[Placement]:
    """The Climate page. Text is data-driven (read from the file); geometry/anchors are
    calibrated by eye to build/climatepage.jpeg and tagged `calibrated` so no reader mistakes
    them for proven-from-code. Approach A replaces only this layer.
    """
    P: list[Placement] = []

    # Header bar: crimson strip, title left, clock right. (Title/clock are runtime strings,
    # not stored labels -- kept synthetic.)
    P.append(Placement("header", 0, 0, src="calibrated", prims=[
        Prim("rect", 0, 0, PANEL_W, 40, color=COL["crimson"]),
        Prim("text", 12, 8, text="CLIMATE CONTROL", size=24, color=COL["white"]),
        Prim("text", PANEL_W - 20, 8, text="7:43PM", size=22, align="right",
             color=COL["white"]),
    ]))

    # Left nav rail: crimson column with five icons (icons still synthetic placeholders).
    rail_w = 64
    rail = [Prim("rect", 0, 0, rail_w, PANEL_H - 40, color=COL["crimson"])]
    for i, ic in enumerate(_NAV_ICONS):
        rail.append(Prim("text", rail_w // 2, 40 + i * 78, text=ic, size=30,
                         align="center", color=COL["white"]))
    P.append(Placement("nav", 0, 40, src="calibrated", prims=rail))

    # Grid geometry (defined here so the CLIMATE tab can match the grid's total width).
    gx, gy = 224, 268
    lw, rw, ch = 162, 65, 45
    grid_w = lw + 2 + rw

    # CLIMATE tab: crimson label, text from the file. Same width and x as the grid below.
    tab_text, tab_src = _file_text(data, _CLIMATE_TEXT["tab"], "CLIMATE")
    P.append(Placement("climate-tab", gx, 70, src="calibrated", prims=[
        Prim("rect", 0, 0, grid_w, 44, color=COL["crimson"]),
        Prim("text", 14, 6, text=tab_text, size=28, color=COL["white"], src=tab_src),
    ]))

    # Setpoint readout: the two temperatures are LIVE sensor/setpoint values, not stored in
    # GuiData, so they stay synthetic placeholders.
    P.append(Placement("temp-current", 235, 128, src="synthetic", prims=[
        Prim("text", 0, 0, text="72°", size=44, color=COL["white"]),
    ]))
    P.append(Placement("chevrons", 258, 192, src="synthetic", prims=[
        _icon_prim(data, "chevron-up", 0, 0, target_h=30, color=COL["cyan"]),
        _icon_prim(data, "chevron-down", 0, 40, target_h=30, color=COL["cyan"]),
    ]))
    P.append(Placement("temp-set", 360, 195, src="synthetic", prims=[
        Prim("text", 0, 0, text="87°", size=52, color=COL["white"]),
    ]))

    # Mode / fan grid: cell widths are the file's (left mode cell 162 px, right fan cell
    # 65 px, height 45 -- from the 0x04 box records at 0x9982c / 0x997b4). Labels read from
    # the file; the grid anchor is calibrated and kept clear of the screen edge. Left column
    # = mode (Cool + blanks); right column = fan (icon / High / Low / Auto, Auto selected).
    grid: list[Prim] = []
    for r in range(4):
        grid.append(Prim("rect", 0, r * ch, lw, ch - 2, color=COL["cell"]))
        grid.append(Prim("rect", lw + 2, r * ch, rw, ch - 2,
                         color=COL["cyan"] if r == 3 else COL["cell"]))
    # Left cell: snowflake (data-driven, read from the Cool cell's own 0x0c selector) + label.
    grid.append(_file_glyph_prim(data, _COOL_ICON, x=26, y=8, target_h=26, fallback="❄"))
    cool, cool_src = _file_text(data, _CLIMATE_TEXT["mode-cool"], "Cool")
    grid.append(Prim("text", 48, 10, text=cool, size=22, color=COL["white"], src=cool_src))
    # Right column row 0: the fan header glyph (real slot-23 icon; chosen, not file-sourced).
    grid.append(_icon_prim(data, "fan", lw + 2 + rw // 2, 6, target_h=30))
    for r, key in [(1, "fan-high"), (2, "fan-low"), (3, "fan-auto")]:
        txt, src = _file_text(data, _CLIMATE_TEXT[key], key.split("-")[1].title())
        grid.append(Prim("text", lw + 2 + rw // 2, r * ch + 12, text=txt, size=20,
                         align="center", color=COL["white"], src=src))
    P.append(Placement("mode-grid", gx, gy, src="calibrated", prims=grid))

    # Starlink button: frame calibrated; the icon glyph AND caption are read from the file
    # (0x9721f icon, 0x97237 caption in 13.6).
    cap, cap_src = _file_text(data, _STARLINK_CAPTION, "Starlink")
    # Top aligned with the CLIMATE bar (y=70); right edge at the clock's colon (~705 px),
    # width just enough for the icon + caption.
    P.append(Placement("starlink", 520, 70, src="mixed", prims=[
        Prim("rrect", 0, 0, 185, 70, radius=10, color=COL["button"]),
        _file_glyph_prim(data, _STARLINK_ICON, x=32, y=15, target_h=40),
        Prim("text", 60, 24, text=cap, size=22, color=COL["white"], src=cap_src),
    ]))

    return P


# --------------------------------------------------------------------------------------
# Layer 2 (approach A): compositor derived from the byte-exact layout engine
# --------------------------------------------------------------------------------------

# A widget's fill colour is mode-tagged (GUICODE Finding 18): mode 3 is an inline BGR value,
# readable straight from the bytes. Mode 7 is an index into the variable table (0x90044BC8),
# an SRAM pointer -- but the chrome-colour dig (2026-08-15, `theme.py`) recovered the GuiCode
# theme-init constants a resolvable subset of those SRAM cells are written with at boot, so a
# mode-7 index IS decodable when `theme.mode7_palette` resolved it (a named residual -- an
# index it could not resolve, e.g. a runtime-copied cell -- stays unresolved, never guessed).
# Precedence: mode-3 inline (exact, always present in the bytes) wins outright; then mode-6
# (GuiData palette) and mode-7 (theme palette) when the index resolves; otherwise the field is
# left UNPAINTED. The "fall back to a position heuristic for the chrome (header/nav/tab crimson,
# background navy, buttons/cells blue)" this comment used to describe was removed when the render
# went byte-only -- there is no position-based colour assignment left in this module.
_FILL_A = ("fillA", "fillA2")


def _inline_fill(p: "object", roles: tuple = _FILL_A):
    """The widget's mode-3 inline fill colour as an RGB tuple, or None. `roles` selects which
    style-field pair to read -- the default fillA/fillA2, or fillB/fillB2 for the off/second
    state (the tank-well body carries its colour in fillB; GUICODE Finding 18)."""
    for role, mode, value in getattr(p, "colors", []):
        if role in roles and mode == 3 and value is not None:
            return ((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF)   # BGR -> RGB
    return None


def _mode7_fill(p: "object", palette: dict, roles: tuple = _FILL_A) -> tuple | None:
    """The widget's own mode-7 colour-table fill, resolved through `palette`
    (`theme.mode7_palette(data)`, computed once per render pass -- see `screen_placements`),
    or `None` if the record carries no mode-7 fill field in `roles`, or the palette does not
    carry that index (the extractor could not recover the SRAM cell's theme-init constant -- a
    named residual, GUICODE Finding 18's colour-dig correction). Never a guess: an unresolved
    index falls through exactly like no colour field at all, so the caller keeps its heuristic."""
    for role, mode, value in getattr(p, "colors", []):
        if role in roles and mode == 7 and value is not None:
            color = palette.get(value)
            if color is not None:
                return color
    return None


# The GuiData sub-container's own u32 colour palette (`0x8F7E6D5C` container at GuiData +0x10000,
# palette at +0x10; MCF-FORMAT §5.5). A colour field tagged MODE 6 is a u8 index into it. Each u32
# is the RGB in its low three bytes (0x00RRGGBB): idx15 = (190,30,45) the nav red, idx16 = (39,40,98)
# the dark blue -- the CLIMATE empty mode-cells. `guidata.py`'s coverage notes a 30-entry table here.
_GUIDATA_PALETTE_OFFSET = 0x10010
_GUIDATA_PALETTE_LEN = 30


def _guidata_palette(data: bytes) -> dict:
    """`{index: (r,g,b)}` from the GuiData sub-container palette -- byte-derived, read straight from
    the file (no disassembly). Used for mode-6 colour fields."""
    from . import guidata
    import struct as _struct
    gs, _ge = guidata.guidata_extent(data)
    base = gs + _GUIDATA_PALETTE_OFFSET
    out = {}
    for i in range(_GUIDATA_PALETTE_LEN):
        v = _struct.unpack_from("<I", data, base + i * 4)[0]
        out[i] = ((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)
    return out


def _mode6_fill(p: "object", gd_palette: dict, roles: tuple = _FILL_A) -> tuple | None:
    """The widget's own mode-6 fill, resolved through the GuiData palette (`_guidata_palette`), or
    `None` if it carries no mode-6 field in `roles`. This is the CLIMATE empty mode-cells (idx16)."""
    for role, mode, value in getattr(p, "colors", []):
        if role in roles and mode == 6 and value is not None:
            color = gd_palette.get(value)
            if color is not None:
                return color
    return None


def _mode2_white(p: "object", roles: tuple = _FILL_A) -> tuple | None:
    """White `(255,255,255)` if a field in `roles` is mode 2. The widget-decoder resolves a
    colour field tagged mode 2 to the hardcoded constant `0xFFFFFF` (`FUN_00252CB0`
    `str …,=0xffffff`; GUICODE Finding 28) -- it is a concrete white, NOT an inherit. This is the
    tank wells' white outline (their fillA border is mode 2)."""
    for role, mode, _value in getattr(p, "colors", []):
        if role in roles and mode == 2:
            return (255, 255, 255)
    return None


def _glyph_color(p: "object", palette: dict, resting: dict | None = None) -> tuple:
    """`(color, src)` for an ICON glyph's foreground: the record's own fillA colour -- mode-3 inline,
    else a resolved mode-7 (theme palette or a cited resting fill), else mode-2 white -- or
    `(None, 'heuristic')` to fall back to white. For icons the fillA IS the ink colour: the CLIMATE
    setpoint chevrons carry idx35 cyan, and the CLIMATE recirc icon carries idx375 (the header red),
    so it draws red-on-red = invisible, matching the coach. Nav/mode icons are mode-2 (white).
    `resting` is the per-render `_merged_resting_fills` palette (byte-derived where available)."""
    fill = _inline_fill(p)
    if fill is not None:
        return fill, "decoded"
    m7 = _mode7_fill(p, palette)
    if m7 is not None:
        return m7, "decoded"
    rest, _ = _resting_fill(p, fills=resting)
    if rest is not None:
        return rest, "resting"
    white = _mode2_white(p)
    if white is not None:
        return white, "decoded"
    return None, "heuristic"


# Runtime-STATE-selected mode-7 fills whose RESTING/default constant is recoverable from GuiCode's
# theme-init (GUICODE Finding 28; the Finding-18 residuals addendum). theme.mode7_palette omits these
# (its ambiguity guard drops cells written >1 constant), so the render supplies the resting value
# only, labelled -- never the runtime on/off/unavailable branches. idx160 (Water Pump base cell
# 0x20039A58 = 0x004151A3) is proven-from-code; idx455 (Tank Heater) is the structural twin, resting
# inferred-from-code (its base-cell store uses computed addressing a linear sweep did not isolate).
_RESTING_FILLS = {160: (65, 81, 163), 455: (65, 81, 163),   # Water Pump, Tank Heater
                  131: (65, 81, 163), 133: (65, 81, 163),   # EXT, RET (inherited fillA; Finding 29)
                  # ELECTRICAL Start (idx167) / Stop (idx168) engine buttons: FUN_00247bf8 COPIES
                  # idx15's cell (0x20039A58) into their cells at config time -- 0x249f96-9c stores
                  # *(idx15) into idx167 (0x20039FFC), 0x249fa4-aa into idx168 (0x2003A61C) -- so both
                  # take idx15's colour, the AES-badge blue. `proven-from-code` (a direct SRAM copy).
                  167: (65, 81, 163), 168: (65, 81, 163),   # Start / Stop = idx15 (copied at config)
                  # CLIMATE header band (idx375): FUN_00247bf8 copies idx10 (124,21,29 -- the nav-rail
                  # red) into its cell when the CLIMATE tab is ACTIVE, or idx35 (cyan) when not (both
                  # traced, 0x24ab30-0x24ad7c). The band is only ever drawn on its OWN climate page,
                  # where the tab is by definition active, so its state is the red. `inferred-from-code`.
                  375: (124, 21, 29),                       # CLIMATE header = idx10 red (active tab)
                  # CLIMATE mode-grid cells (0x24ab2c block): each cell's colour cell is filled by a
                  # COPY of a resolved theme cell -- Cool (idx120) <- idx8 (39,40,98) dark blue; High
                  # (idx376) / Low (idx377) <- idx15 (65,81,163) blue; Auto (idx378) <- idx35 cyan (the
                  # selected fan-speed highlight). Traced from the copy sources and oracle-confirmed
                  # (CLIMATE.jpg). `inferred-from-code` -- the selected-vs-idle highlight is runtime, so
                  # these are the shipped/default state (Auto selected), not a proven-invariant colour.
                  120: (39, 40, 98), 376: (65, 81, 163),
                  377: (65, 81, 163), 378: (0, 174, 239),
                  # CLIMATE option button box (idx144 -- Bath Fan on 13.6 / Starlink on 14.7): a single
                  # copy of idx15 (65,81,163) blue at 0x248dec (one str site, one source). Oracle-
                  # confirmed (the button is blue). `inferred-from-code`.
                  144: (65, 81, 163),
                  # idx145 -- instance-15's own button-indicator cell (0x2003a2fc), the target of
                  # newbutton.retarget_indicator's Pin-15 button. Its resting/default is now
                  # BYTE-DERIVED (`theme.resting_defaults`, GUICODE Finding 34): the theme-init copies
                  # master cell 0x20039974 into it (fall-through at 0x2488ea-f0), and that master holds
                  # the single constant 0x00272862 = (39,40,98) -- the dark navy, NOT the button blue.
                  # A branch (0x249272) overrides it with a runtime on-state master (0x2003a1a0) when the
                  # widget's live slot is set, so this is the resting default only, never the live state.
                  # This constant is only the capstone-unavailable fallback; `_merged_resting_fills`
                  # overlays the same value from `theme.resting_defaults` at render time.
                  145: (39, 40, 98)}


# Mode-7 indices that are LEVEL GAUGES, not flat fills: the HOME battery strips (Chassis 202 /
# House 206), whose height encodes charge and whose colour is the firmware's own 11.9V green/red
# branch (`_battery_fill`). `theme.resting_defaults` *can* derive a resting constant for them, but
# using it would be wrong in a way the rest of the resting palette is not: a gauge painted at its
# resting colour is drawn FULL-HEIGHT, i.e. a full battery, whenever no voltage is supplied. That
# is a confidently-wrong value where the honest render is nothing at all -- so these indices are
# held out of the resting palette and left to `_battery_fill`, which paints only when it has a
# reading. Caught by `test_home_battery_fill_is_coloured_and_levelled_by_voltage_byte_derived`
# ("runtime value absent -> unpainted") when the 2026-08-18 widening first swept them in.
_GAUGE_INDICES = frozenset({202, 206})



# The floorplan register (GUICODE Finding 17) is read from the floorplan-name switch, NOT
# hardcoded: it is slot 5 on 13.6 but slot 10 on 14.7, so a fixed 5 read the wrong cell on 14.7
# and the FLOORPLAN SELECTION highlight never followed the picked floorplan there. `_floorplan_slot`
# resolves it per build via `options._find_floorplan_switch`, the same structural source
# `slot_values` uses to WRITE it -- so the read key and the write key are guaranteed to agree.


_floorplan_slot_cache: dict[int, int | None] = {}


def _floorplan_slot(data: bytes) -> int | None:
    """This build's floorplan value-slot, or None when it cannot be located.

    Resolved from the floorplan-name switch where one exists (all three corpus builds,
    11.23's included since PC.40 unmasked it -- slot 7); a build with no matchable switch
    falls back to `config.floorplan_slot`, the receive-mechanism derivation (config-unpack
    cfg-0x00 register -> variable-table index) -- the same fallback order
    `options.slot_values` uses to WRITE the slot, so the read key and the write key agree
    on every build.

    Memoised per build: `_find_floorplan_switch` scans the whole object table (~0.4 s), and
    `_selection_overrides` runs on EVERY page render, not just the FLOORPLAN SELECTION page --
    so without this every render of every page paid that scan. The key is `hash(data)` (a real
    content key, not `id`, which a freed-and-reused buffer would alias); the cache holds one
    entry per build seen, a handful at most."""
    key = hash(data)
    if key not in _floorplan_slot_cache:
        from . import config, options
        try:
            slot, _ = options._find_floorplan_switch(data)
        except Exception:
            try:
                slot = config.floorplan_slot(data)
            except Exception:
                slot = None
        _floorplan_slot_cache[key] = slot
    return _floorplan_slot_cache[key]


def _selection_overrides(data: bytes, slot_values: dict | None) -> dict:
    """`{mode-7 index: (R,G,B)}` for a value-selected highlight cascade, at the floorplan value
    in `slot_values` (default 0 -- the boot state, which is what the cells actually hold until a
    floorplan arrives over CAN).

    Byte-derived end to end: `theme.selection_fills` emulates GuiCode's own cascade, so the
    highlighted button is the one the firmware would highlight, not one this module picks.
    Returns `{}` when the build has no such cascade (11.23 has no selection screen) or capstone
    is unavailable -- in which case the buttons keep their plain resting fill.
    """
    from . import layout, theme
    slot = _floorplan_slot(data)
    if slot is None:
        return {}
    try:
        register, _ = layout.variable_register(data, slot)
    except Exception:
        return {}
    try:
        by_value = theme.selection_fills(data, register)
    except theme.CapstoneUnavailable:
        return {}
    value = (slot_values or {}).get(slot, 0)
    return by_value.get(value, {})


def _label_lines(text: str) -> list[str]:
    """Split a label into the lines the panel draws, keeping each line's own spaces.

    Trailing BLANK lines are dropped -- a label ending in a newline does not draw an empty row --
    but a leading or interior blank line is kept, because it advances the pen exactly as
    `FUN_002559A0` does (ELECTRICAL's `'\\nStart at'` must land on row two, lining up with
    `'Time at\\nStart Volts'`).

    **Each surviving line keeps its trailing spaces.** This function used to `rstrip()` them, and
    that was wrong in a way only hardware could show: the panel centres a line by the width of
    the string it was given, pad included, so a centre-aligned caption padded to fit its field
    draws left of where a stripped measurement puts it. Owner-observed 2026-08-21 on the
    `'Overflow\\nValve   '` button, whose visible second line sits 9 px left of centre while the
    render drew it centred. The old CLAUDE.md rule "trailing spaces are invisible" holds only for
    LEFT-aligned labels; 37 stock labels are centre- or right-aligned with a trailing pad.
    """
    lines = text.split("\n")
    while len(lines) > 1 and not lines[-1].strip():
        lines.pop()
    return lines


def _effective_line_height(data: bytes, p: "object", table=None) -> int | None:
    """The byte-derived inter-line advance for a multi-line label, or `None` if the record carries
    no R49 block (the caller then keeps its `size + 2` heuristic).

    `proven-from-code` (GUICODE Finding 37). R49 decodes **four** bytes, not three
    (`FUN_00252CB0` `0x253d2e`/`d38`/`d42`/`d4c` -> render state `+0x94..+0x97`): h-align,
    v-align, line-height, and a **flag**. The multi-line drawer `FUN_002559A0` applies the flag
    once, before the first advance (`0x255a0c`-`0x255a26`):

        if state[0x97]:                       # ldrb +0x97 ; cbz
            state[0x96] += font[0x07]         # ldr +0x98 -> font ; ldrb [font,#7] ; add ; strb
            state[0x97] = 0

    so the advance is `line_height + font_cell_height` when the flag is set and `line_height`
    otherwise. On 13.6 the flag is set on **126** records, every one of which carries
    `line_height = 0` -- i.e. it is how a record says "space the lines by the font's own cell
    height", which is exactly the case the renderer previously had to approximate with
    `size + 2`.
    """
    from . import metrics                 # module-local: `render` has no module-scope `metrics`

    lh = getattr(p, "line_height", None)
    if lh is None:
        return None
    if getattr(p, "line_height_adds_font", None) and p.font is not None:
        try:
            cell = metrics.font_metrics(data, p.font, table=table)
        except glyphs.GlyphError:
            # the font/slot does not resolve -- the caller keeps its `size + 2` heuristic. NOT a
            # bare `except Exception`: this function shipped with one, and it swallowed the
            # `NameError` from the missing import above for as long as the function existed. With
            # `line_height` 0 (which is what all 126 adds-font records carry) the handler returned
            # `0 or None` = None, so the caller fell through to the heuristic every single time
            # and Finding 37's rule never once ran. A narrow except is what makes that visible.
            return lh or None
        height = getattr(cell, "height", None)
        if height:
            lh += height
    return lh or None


def _merged_resting_fills(data: bytes) -> dict:
    """The resting-fill palette for one render pass: **every** index `theme.resting_defaults(data)`
    derives, plus the curated `_RESTING_FILLS` entries the resolver cannot reach.

    `theme.resting_defaults` derives the resting/default colour of copy-fed runtime-state cells
    straight from the theme-init (a copy from a single-constant master cell, GUICODE Finding 34).
    It is conservative by construction: a cell with more than one default master, an
    uncharacterised store idiom, or a master holding more than one constant is omitted rather than
    guessed.

    *Widened 2026-08-18.* This used to intersect the derived palette with a hand-curated index
    list, "to keep the render's painted set unchanged". That made the curated list load-bearing,
    and it was incomplete: the FLOORPLAN SELECTION buttons (idx 24-34) inherit their wrapper's
    mode-7 index exactly as EXT/RET (idx 131/133) do, but 131/133 happened to be curated and
    24-34 did not, so ten buttons rendered with no fill at all. The intersection was also
    self-validating in the wrong direction -- it could only ever confirm hand-entered values.
    Dropping it is safe *because* the two agree: of the 13 curated indices, 11 are derivable and
    all 11 match the curated value exactly. The remaining two (455 Tank Heater, 375) are not
    derivable and keep their curated value, now the only hand-entered colours left here.

    `_GAUGE_INDICES` are held back: see that constant.

    `CapstoneUnavailable` degrades to the curated dict."""
    from . import theme
    fills = dict(_RESTING_FILLS)
    try:
        derived = theme.resting_defaults(data)
    except theme.CapstoneUnavailable:
        return fills
    fills.update({i: c for i, c in derived.items() if i not in _GAUGE_INDICES})
    return fills


def _resting_fill(p: "object", roles: tuple = _FILL_A,
                  fills: dict | None = None) -> tuple[tuple | None, str]:
    """`(color, 'resting')` if a field in `roles` is a mode-7 index with a known resting constant
    (`fills`, defaulting to the curated `_RESTING_FILLS`), else `(None, 'heuristic')`. Lets the
    Water Pump / Tank Heater buttons draw their resting fill; the src label 'resting' marks it as
    the default state, not the live one. `fills` is the per-render `_merged_resting_fills` palette
    (byte-derived where available); it defaults to the curated dict for callers without `data`."""
    if fills is None:
        fills = _RESTING_FILLS
    for role, mode, value in getattr(p, "colors", []):
        if role in roles and mode == 7 and value in fills:
            return fills[value], "resting"
    return None, "heuristic"


# HOME battery-voltage fill: the Chassis (mode-7 idx 202) and House (idx 206) battery boxes are
# coloured by their VOLTAGE, not SoC -- FUN_00247bf8 (0x24a326-0x24a36e) reads the voltage register,
# compares it to a 12V threshold, and COPIES a green or red theme colour into the fill's colour cell:
#   * voltage > 11.9V (0x77 decivolts) -> green  = mode-7 idx 224  (43,182,115)
#   * voltage <= 11.9V                 -> red    = mode-7 idx 118  (204,33,39)
#   * 999.9V (0x270f) is the no-data sentinel; an 11.5-12.5V band also feeds a separate gradient byte
# The source per fill is byte-traced: idx202 cell (0x20039D5C) <- slot 205 (Chassis, 0x2003A272);
# idx206 cell (0x2003A664) <- slot 209 (House, 0x2003A594). NB the colour block runs in EVERY mode:
# the AES gate (0x24a2ec, slot 37 in {2,3}) only re-sources the HOUSE voltage from a different CAN
# field, then falls back into the SAME colour logic (0x24a538 -> 0x24a326), so both fills are still
# coloured under AES. So this is NOT gated on AES; the 48V case is out of scope anyway -- on HOME the
# House box is dropped under AES by the slot-6 switch, and the 48V ELECTRICAL "Battery Voltage" box is
# a different widget that never carries idx202/206, so it is untouched. The 11.9V threshold and the
# green/red colours are byte-derived (code constant + theme palette); only WHICH voltage a preview
# supplies for slot 205/209 is the runtime value, and on HOME both are the 12V registers.
# Fill mode-7 idx -> the value_slot holding its voltage. Hand-entered, and DERIVABLE in principle:
# each fill record shares its object with a wtype-8 value widget carrying that very slot -- obj 440
# (idx 202) holds value_slot 205, obj 443 (idx 206) holds 209, the same same-object `obj_index`
# pairing the tank wells use. It is left as a table because the structural signature that would
# select *these* two objects and not every other "mode-7 fill + wt-8 slot + 'V' label" object in
# the build needs tuning, and a tuned signature would just be a new fit replacing an old one
# (2026-08-18 sweep). Only 13.6 is verified; another build needs this pair re-derived.
_BATTERY_FILL_SLOTS = {202: 205, 206: 209}
_BATTERY_GOOD_MIN_DECIVOLTS = 119            # > 11.9V is "good" (green); <= is red (0x77, from code)
_BATTERY_GREEN_IDX = 224                     # theme palette index for the good colour (byte-derived)
_BATTERY_RED_IDX = 118                       # theme palette index for the low colour (byte-derived)

# The coach 48V pack battery -- HOME object 902's clone of the house battery graphic (see
# api-notes.md / task-6-brief.md, 2026-09-11). Its fill's colour cell and its level are FRESH cells
# allocated at build time (`insert.clone_object` + the authored per-tick routine of an earlier task
# in this series), so unlike the stock Chassis/House gauges (idx 202/206, a fixed pair) there is no
# constant mode-7 index that names it ahead of time. Threshold and window are given by the design
# (register.resolve_register dgn=0x1FFFD instance=1 var=105, raw u16 x0.05V): green at raw >= 1008
# (50.4V) else red; fill level linear over the 48.0-54.8V window, clamped both ends.
#
# "Unresolved mode-7 fillA that isn't 202/206" is NOT by itself a safe identifier for the clone --
# reviewed 2026-09-11 (Task 6 review): every OTHER page has its own unresolved mode-7 gauge fills
# too (e.g. the LEVELING page, root 32, indices 424-431), and passing `coach_pack_voltage` on a
# render of one of those pages painted 8 spurious green/red gauges there -- a real violation of the
# "never invent a colour" fill policy (this module's docstring), caught by reproducing it on a
# STOCK render with no coach clone present at all. HOME (root 4) carries exactly one such fill (the
# clone), so the identification is scoped to `root == _COACH_BATTERY_ROOT` as well as the preview
# key -- both must hold, not either alone.
_BATTERY_GREEN_RGB = (0x2B, 0xB6, 0x73)
_BATTERY_RED_RGB = (0xCC, 0x21, 0x27)
_COACH_BATTERY_MIN_VOLTS = 48.0
_COACH_BATTERY_MAX_VOLTS = 54.8
_COACH_BATTERY_GOOD_MIN_RAW = 1008
_COACH_BATTERY_ROOT = 4               # HOME -- the only page the clone can appear on


def coach_battery_color(volts: float) -> tuple:
    """`(R,G,B)` for the coach 48V pack battery fill: green at raw `volts*20 >= 1008` (50.4V), else
    red. Design threshold (task-6-brief.md), not a decoded firmware branch -- the coach pack has no
    stock gauge to derive one from."""
    return _BATTERY_GREEN_RGB if volts * 20 >= _COACH_BATTERY_GOOD_MIN_RAW else _BATTERY_RED_RGB


def coach_battery_fraction(volts: float) -> float:
    """The coach 48V pack battery's charge fraction (0=empty .. 1=full), linear over the 48.0V
    (empty) .. 54.8V (full) window and clamped at both ends -- the design window (task-6-brief.md),
    distinct from the stock 11.5-12.5V house/chassis window (`_battery_level_fraction`)."""
    span = _COACH_BATTERY_MAX_VOLTS - _COACH_BATTERY_MIN_VOLTS
    return min(1.0, max(0.0, (volts - _COACH_BATTERY_MIN_VOLTS) / span))


def _battery_level_fraction(volts: float) -> float:
    """The battery fill's charge fraction (0=empty .. 1=full) from its voltage, byte-derived:
    FUN_00247bf8 (0x24a35a-0x24a36e) computes an "empty from top" level = clamp((12.5V - v)*4, 0, 40)
    -- 0 at/above 12.5V, 40 at/below 11.5V. The visible charge is the complement, normalised by that
    40 max: 1 - level/40. So 12.5V+ reads full, 11.5V- empty, linear between (like the tank fill)."""
    level = min(40, max(0, (125 - round(volts * 10)) * 4))
    return 1.0 - level / 40.0


def _battery_fill(p: "object", palette: dict, preview: dict,
                  root: int | None = None) -> tuple[tuple | None, float, str]:
    """`(color, level_fraction, 'voltage-state')` for a battery fill box: either a HOME stock gauge
    (mode-7 idx 202/206, coloured/levelled from `preview[chassis_voltage|coach_voltage]`) or, ONLY
    on HOME (`root == 4`), the coach 48V pack clone (coloured/levelled from
    `preview["coach_pack_voltage"]`).

    The clone's colour cell is fresh (allocated at build time, see the module comment above
    `_BATTERY_GREEN_RGB`), so it carries no fixed mode-7 index the way 202/206 do, and this
    function is only reached from `screen_placements` after the decoded/mode6/resting fallback
    chain has already failed to resolve the fill's own colour. That is NOT by itself enough to
    identify the clone: every page has its own unresolved mode-7 gauge fills (the LEVELING page's
    indices 424-431 reproduce this exactly), so "unresolved mode-7, not 202/206" alone painted 8
    spurious gauges there on a stock render with no clone present at all (Task 6 review,
    2026-09-11) -- a real "never invent a colour" violation, not a hypothetical one. The fix is the
    `root` check: HOME (root 4) carries exactly one such fill (the clone itself), so the coach
    branch requires BOTH `root == _COACH_BATTERY_ROOT` AND the caller supplying
    `coach_pack_voltage` -- neither alone is sufficient. A caller that omits `root` (or passes a
    non-HOME root) never takes the coach branch, so an unresolved mode-7 fill there still falls
    through to `(None, 1.0, "heuristic")` exactly as before this change.

    `(None, 1.0, ...)` if it is not a battery fill, has no preview voltage, or (for the stock pair)
    the palette lacks the colour."""
    if not preview:
        return None, 1.0, "heuristic"
    for role, mode, value in getattr(p, "colors", []):
        if role not in _FILL_A or mode != 7:
            continue
        if value in _BATTERY_FILL_SLOTS:
            slot = _BATTERY_FILL_SLOTS[value]
            volts = preview.get(_VOLTAGE_SLOTS.get(slot, ""))
            good = palette.get(_BATTERY_GREEN_IDX)
            low = palette.get(_BATTERY_RED_IDX)
            if volts is None or good is None or low is None:
                return None, 1.0, "heuristic"
            color = good if volts * 10 > _BATTERY_GOOD_MIN_DECIVOLTS else low
            return color, _battery_level_fraction(volts), "voltage-state"
        if root == _COACH_BATTERY_ROOT and "coach_pack_voltage" in preview:
            volts = preview["coach_pack_voltage"]
            return coach_battery_color(volts), coach_battery_fraction(volts), "voltage-state"
    return None, 1.0, "heuristic"


def _decoded_fill(p: "object", palette: dict, roles: tuple = _FILL_A,
                  border: bool = False) -> tuple[tuple | None, str]:
    """`(color, color_src)` for a widget's own decoded colour field in `roles`. Mode-3 inline BGR
    always wins when present (exact, byte-resident); then a mode-7 colour-table index looked up in
    `palette`. Returns `(None, "heuristic")` when none resolves -- the caller then falls back to its
    own heuristic, never inventing a colour. `roles` picks the style-field pair: fillA (block A) is
    the BORDER/outline colour, fillB (block B) the interior fill, at rest (GUICODE Finding 28) --
    except on a LABEL record, where fillA is the text ink (see the ink resolution in
    `screen_placements`, which calls this with `border=True` for exactly that reason).

    `border=True` (only when this colour is drawn as a border/outline, not a solid fill) also
    resolves mode 2 -> hardcoded white: mode 2 is white ONLY as a stroke colour (the tank well /
    box border). Many boxes carry a mode-2 fillA border but are drawn with no interior; resolving
    mode 2 in the general fill path would flood them white, so it is gated behind `border`."""
    inline = _inline_fill(p, roles)
    if inline is not None:
        return inline, "decoded"
    if border:
        white = _mode2_white(p, roles)
        if white is not None:
            return white, "decoded"
    mode7 = _mode7_fill(p, palette, roles)
    if mode7 is not None:
        return mode7, "decoded"
    return None, "heuristic"


def screen_placements(data: bytes, root: int,
                      match_table_off: int | None = None,
                      switch_values: dict | None = None,
                      slot_values: dict | None = None,
                      tank_level: float = 0.0,
                      preview: dict | None = None) -> list[Placement]:
    """Approach A, generalised: ANY page composed from `layout.Screen.walk(root)`'s decoded
    widgets — no calibration, no per-screen anchor table. This is the one compositor Task 4
    adds; `climate_placements_decoded` (below) is now a thin wrapper over it that only
    supplies the Climate-specific default root, and any future named-page helper should do
    the same rather than duplicate this body.

    Boxes come from the type-4/rounded records, text/glyphs from the labelled records (font +
    size + alignment all byte-derived: `metrics.font_metrics` for the cell height/top,
    `metrics.text_width` + the record's align byte for horizontal placement, and `glyphs.py`
    for the panel's own icon glyphs, at the decoded font slot -- `records.WRecord.font`). Fill
    colour is mode-3 inline BGR when the record carries one (`color_src="decoded"`); otherwise
    a mode-7 colour-table index is looked up in a once-per-render `theme.mode7_palette(data)`
    (`color_src="decoded"` when that index resolves); anything else falls back to the same
    position heuristic used everywhere in this module (`color_src="heuristic"`). Value widgets
    (runtime numbers/temperatures) are not stored, so they are left out. This slots into the
    same rasteriser as approach B.

    Tank gauge wells (wtype 0x20, `build/13.6-images/HOME.jpg`; GUICODE Finding 18 dig +
    owner hardware oracle, 2026-08-15) are byte-derived except the runtime fill level:
      * the well BODY is the well record's own `fillB` -- a mode-7 colour-table index (161 on
        13.6 = a blue lighter than the quadrant, matching the owner's description), resolved
        through the palette (`color_src="decoded"`; heuristic only if that index is unresolved);
      * the per-type level COLOUR is a *separate* wtype-0x21 record belonging to the **same
        object** as the well (`Placed.obj_index` equality -- the real invariant, confirmed for
        all four Home tanks in 13.6/14.7), read via `_decoded_fill` so both mode-3 inline (FRESH
        `#00A79D` teal, GREY/GREY2 `#808285`) and mode-7 (BLACK index 130 = near-black) resolve;
      * a heuristic outline is drawn over both at the well's own decoded box.
    Flat-list adjacency in `placed` is NOT sufficient to pair the level record: a container
    (wtype 5/6/0x2e) splices a recursed child object's records inline, so the record right after
    a well can belong to a different object -- the `obj_index` check keeps an unrelated spliced-in
    record from ever being drawn or dropped as this well's fill; only a same-object 0x21 with a
    decoded colour is consumed. The **residual, named honestly**: the fill LEVEL (height) is a
    runtime value with no stored representation (`docs/CLOSED-THREADS.md` thread 5; `HOME.jpg` shows 0%),
    so it is drawn only up to `tank_level` (default 0 = empty, the owner's coach; the record's own
    geometry is the 100% extent, which would otherwise read as a falsely-full tank). Levels are
    ~~quantised 0/33/67/100% on hardware (owner-confirmed)~~ -- **CORRECTED 2026-09-07
    (`BUS-BASELINE.md` PC.62): the PANEL does not quantise; the G12 did.** With the G12's own
    `1FFB7` broadcast suppressed (v22) an injected `19FFB7AF#002238FFFFFFFFFF` -- relative level
    34, resolution 56 -- read **61%** on HOME's FRESH gauge, and `000D64...` (13/100) read
    **13%**. The panel computes `level / resolution` and shows the percent. The quarters were an
    artefact of the only source that had ever fed it: the G12's `CC`/`CD`/`CE` bucketing ladder
    emits nothing but 0/33/67/100, so no intermediate value had reached the panel before. A tank
    with no claimant reads `--%`, the no-data sentinel, not 0%.
    Owner-verified on the coach (2026-08-15): fresh/grey/grey2/
    black keep their per-type colour at EVERY level -- none turns red at 0/33/67/100% -- so the dig's
    green/red cell (idx 202/206) is a different indicator, not these four tanks' fill; drawing the
    per-type colour at all levels (never red) is correct.

    **Button-box visibility and label centring** (wtype 0x21/33, render-fidelity Task 3:
    `build/13.6-images/HOME.jpg`'s Water Pump / Tank Heater / ON / OFF / EXT / RET). Every one
    of these is a mode-0/7 fill (GUICODE Finding 18: inherit at the time, or a colour-table
    index) -- so before render-fidelity Task 3 they painted the same heuristic
    `COL["button"]` as the container/bar record they sit inside (e.g. ON and OFF's own 70px
    boxes sit inside a wider "Light Master" wrapping bar, all three wtype 33, all three
    mode-7/non-inline), rendering as one undifferentiated rectangle with no visible button
    edge. Task 3's fix was `Prim.stroke`: a mode-0/7 box with no resolvable colour keeps the
    labelled heuristic fill (`color_src="heuristic"`) but also strokes its own decoded
    `(x0,y0,x1,y1)` box in a fixed, distinguishable border colour (`COL["outline"]`), so each
    button's real extent is visible even nested inside a same-coloured sibling. The
    chrome-colour dig (Task 2, 2026-08-15) added a middle tier: a mode-7 index that
    `theme.mode7_palette` DOES resolve -- ON/OFF's shared index (10) resolves to the true
    hardware red, `RGB(124,21,29)` -- is drawn `color_src="decoded"`, no stroke (the box no
    longer needs a synthetic border once its own colour is genuinely known, the same
    reasoning already applied to mode-3). Water Pump / Tank Heater's own indices (160/455)
    do not resolve, so they keep the Task 3 heuristic-fill-plus-stroke treatment. A mode-3
    inline fill (none of these six, but some other 0x21/33 records) is unaffected: drawn
    solid, `color_src="decoded"`, no stroke added.

    A button's own label is centred in that same decoded box by the pre-existing per-label
    centring this module already does for any wider-than-its-text box (`box_centre -
    text_width // 2`, via `metrics.text_width` -- see the label loop below); for all six of
    these buttons the label record's own declared box is authored identical to the button
    box record's (`Screen.walk`'s `Placed.x0/x1`, verified byte-for-byte against the box
    record for all six in 13.6), so centring against the label's own box *is* centring
    against the button's box, not a coincidence being relied on blindly -- `tests/
    test_render.py`'s Task 3 tests cross-check both records' geometry to pin this rather than
    assuming it.

    `root` is the *only* thing that varies per page -- an object-table index (`layout.Screen`'s
    `table`), found generically per build by `cli._resolve_page_root` (header-label lookup) or
    passed explicitly. Nothing below this line is root-specific.

    `switch_values` (with `match_table_off`) selects option/state-conditional widgets — e.g.
    `{246: 1}` renders the Bath Fan/Starlink button (obj 247) that the obj-881 switch shows when
    its value is 1 (GUICODE Finding 16). `slot_values` drives a switch by the variable register
    it reads instead (e.g. `{5: 8}` = floorplan WD) -- the more principled form, since a switch's
    `child_base` is a build-specific object index while a value-slot is stable across builds
    that share the same option (see `layout.Screen.walk`'s docstring).
    """
    from . import layout, metrics, theme
    scr = layout.Screen(data, match_table_off=match_table_off)
    placed = scr.walk(root, switch_values=switch_values, slot_values=slot_values)
    prims: list[Prim] = []

    # Resolve the glyph table ONCE for this whole render pass, not once per character/label --
    # a page walks hundreds of labelled records. This is the structural fix for the redundant-
    # scan cost (see glyphs.find_glyph_table's docstring): resolve here, thread `table=` through
    # every glyph_image/panel_text_images/text_width call below, rather than caching inside
    # glyphs.py keyed off id(data) -- an earlier version of that cache was unsafe against
    # mutate-and-reuse of the same buffer object (id(data) doesn't change on in-place mutation).
    try:
        table = glyphs.resolve_glyph_table(data)
    except glyphs.GlyphError:
        table = None    # no glyph table in this image; per-call lookups below will also fail

    # Resolve the mode-7 chrome palette ONCE for this whole render pass too, not once per
    # widget: it disassembles the entire GuiCode section (`theme.mode7_palette`), so per-call
    # would redo that work for every chrome fill on the page. `_decoded_fill`/`_decoded_color`
    # below thread it through as `palette`. `CapstoneUnavailable` degrades to an empty
    # palette (every mode-7 fill stays the labelled heuristic) rather than failing the whole
    # render -- capstone is a real but undeclared dependency (`theme.py`'s docstring).
    try:
        palette = theme.mode7_palette(data)
    except theme.CapstoneUnavailable:
        palette = {}
    resting_fills = _merged_resting_fills(data)  # curated dict, byte-derived where the resolver can
    # A value-selected highlight overrides the resting default for the cells it drives. The
    # FLOORPLAN SELECTION page is the case: GuiCode's cascade gives exactly one button the cyan
    # highlight for the current floorplan value (GUICODE Finding 36). Without a floorplan value
    # the page renders its boot state (value 0 -> Ship Mode highlighted), which is what the
    # firmware itself leaves in those cells.
    resting_fills.update(_selection_overrides(data, slot_values))
    gd_palette = _guidata_palette(data)         # mode-6 fills (CLIMATE empty mode-cells)

    # boxes first (drawn under the text). `consumed` holds indices of level-fill records already
    # emitted as the second half of a gauge-well pair, so the generic per-type dispatch below
    # does not also see (and, for most, skip -- its own declared box has an inverted height) them.
    consumed: set[int] = set()
    for i, p in enumerate(placed):
        if i in consumed:
            continue
        w, h = p.x1 - p.x0, p.y1 - p.y0
        tag = f"file@{p.record_offset:#x}"
        if p.wtype == 0x20:                  # tank/bar gauge well (render-fidelity Task 1)
            if 2 < w <= PANEL_W and 2 < h <= PANEL_H:
                # the well BODY (the empty well behind the fill) is byte-derived: the well's own
                # fillB is a mode-7 colour-table index (161 = a blue lighter than the quadrant on
                # 13.6, matching the owner's hardware description). GUICODE Finding 18 dig,
                # 2026-08-15. Draw it under the level fill and outline.
                # geom[4] (coord4) is the box CORNER RADIUS, not a border width: the visible-box
                # quad is [x, width, height, radius] (MCF-FORMAT §6). The well is a thin, fully-
                # rounded outline on the coach (HOME.jpg oracle), so body/fill/outline all round at
                # this radius and the border is 2px -- earlier code mis-read coord4=5 as a 5px edge.
                well_r = max(0, p.coord4 or 0)
                body, body_src = _decoded_fill(p, palette, roles=("fillB", "fillB2"))
                if body is not None:
                    prims.append(Prim("rrect", p.x0, p.y0, w=w, h=h, radius=well_r, color=body,
                                      color_src=body_src, src=tag))
                # the level fill: a wtype-0x21 record belonging to the SAME object as the well
                # (`obj_index` equality -- the real invariant; see docstring above). Flat-list
                # adjacency in `placed` alone is not enough: a container splices a recursed child
                # object's records inline, so `placed[i + 1]` can belong to a different object
                # entirely -- checking `obj_index` is what stops that spliced-in record (whatever
                # its wtype) from ever being claimed as this well's fill.
                if (i + 1 < len(placed) and placed[i + 1].wtype == 0x21
                        and placed[i + 1].obj_index == p.obj_index):
                    lvl = placed[i + 1]
                    # the per-type fill colour is byte-derived: FRESH mode-3 inline #00A79D,
                    # GREY/GREY2 mode-3 #808285, BLACK mode-7 index 130 (=near-black) -- so read
                    # it through _decoded_fill (mode-3 OR mode-7), not mode-3 alone (which missed
                    # BLACK). GUICODE Finding 18 dig, 2026-08-15.
                    fill, fill_src = _decoded_fill(lvl, palette)
                    if fill is not None:
                        # consumed here, not unconditionally: a same-object 0x21 with no decoded
                        # colour is not this well's fill and must fall through to the generic
                        # dispatch below untouched, not silently vanish.
                        consumed.add(i + 1)
                        fx0, fx1 = sorted((lvl.x0, lvl.x1))
                        fy0, fy1 = sorted((lvl.y0, lvl.y1))   # own y0/y1 come out inverted; the
                        fw = fx1 - fx0                        # record box is the 100% extent.
                        # the fill LEVEL (height) is a runtime value, not in the file (thread 5):
                        # draw only up to `tank_level` (default 0 = empty, matching the owner's 0%
                        # coach). The record's full height would falsely read as a 100%-full tank.
                        frac = max(0.0, min(1.0, tank_level))
                        fh = int(round((fy1 - fy0) * frac))
                        if fw > 0 and fh > 0:                 # fill grows from the bottom up
                            # the level fill is a type-0x21 rounded rect; geom[4]/coord4 is its own
                            # corner radius. Clipped to the runtime level it rounds its BOTTOM corners
                            # to follow the well's inner curve, the liquid surface (top) staying flat.
                            fr = max(0, lvl.coord4 or 0)
                            prims.append(Prim("rrect", fx0, fy1 - fh, w=fw, h=fh, radius=fr,
                                              corners=(False, False, True, True),
                                              color=fill, color_src=fill_src,
                                              src=f"file@{lvl.record_offset:#x}"))
                # the well OUTLINE: the box primitive strokes the well's fillA (block A = border,
                # GUICODE Finding 28) as a thin (2px) rounded edge at the geom[4] corner radius.
                # fillA here is mode 2 = hardcoded white -> the white outline the owner sees. Drawn
                # last, over the body/fill.
                border, border_src = _decoded_fill(p, palette, roles=("fillA", "fillA2"),
                                                   border=True)
                if border is not None:
                    prims.append(Prim("rrect", p.x0, p.y0, w=w, h=h, radius=well_r, color=border,
                                      color_src=border_src, outline=True, line_w=2, src=tag))
            continue
        if p.wtype == 0x10:
            # a circle/ellipse shape (GUICODE Finding 9 "shapes"): centred at (x0,y0) with the
            # geom[4] radius (coord4); block B (fillB) is the interior, block A (fillA) the border
            # (Finding 28). The OPTIONS selection dots are pairs of these -- an outer white-outlined
            # navy ring (r12: fillB=idx3 navy, fillA=mode-2 white) plus an inner disc (r9) that is
            # navy when the option is off and cyan (idx35) when it is selected (the switch picks the
            # candidate). fillA=mode-2 resolves to white via _mode2_white (not _decoded_fill).
            # the circle CENTRE is (coord0 + radius, coord1) -- the type-0x10 handler computes the
            # centre x as x0+r (GuiCode 0x2575f2) and takes coord1 as the centre y directly, so the
            # outer ring (x0=95,r12) and the inner disc (x0=98,r9) both centre on (107, y0) and are
            # concentric. (Treating (x0,y0) as the centre put the dot a radius too far left and made
            # the fill overflow the ring on one side.)
            r = max(1, p.coord4 or 0)
            fill, fsrc = _decoded_fill(p, palette, roles=("fillB", "fillB2"))
            border, bsrc = _decoded_fill(p, palette, roles=("fillA", "fillA2"), border=True)
            if border is None:
                border = _mode2_white(p)
                bsrc = "decoded" if border is not None else "heuristic"
            if fill is not None or border is not None:
                prims.append(Prim("ellipse", p.x0, p.y0 - r, w=2 * r, h=2 * r,
                                  color=(fill if fill is not None else border),
                                  color_src=(fsrc if fill is not None else bsrc),
                                  stroke=(border if (border is not None and border != fill) else None),
                                  line_w=2, src=tag))
            continue
        if p.wtype in (10, 18):
            # A BITMAP. `FUN_00256C54` case 10/0x12 calls `FUN_00255F80(record+0x28, x, y, key)`,
            # so the record's `child_index` is an IMAGE index, not an object index -- which is
            # what `layout.py`'s warning about that field means. `images.py` has decoded these
            # since 2026-08-29 (the per-row RLE, `MCF-FORMAT.md` §5.5); it was only never wired
            # to a render, so all four placed records drew nothing.
            #
            # The blitter takes a colour KEY and skips pixels matching it, passed as `state+0x94`
            # when the record's flag byte `+0x7032` has bit 1 set. None of 13.6's four records
            # carries that field, so all four blit opaque -- `images.py` decodes to opaque RGB888
            # for the same reason, and a keyed record would need it threaded through both.
            #
            # `Placed` does not carry `child_index`, and widening a struct every walk builds to
            # serve four records is the wrong trade; re-decode the one record instead.
            idx = records.decode_record(data, p.record_offset).child_index
            if idx is not None and 0 <= idx < images.image_count(data):
                art = images.decode_image(data, idx)
                prims.append(Prim("image", p.x0, p.y0, w=art.width, h=art.height,
                                  image=Image.frombytes("RGB", (art.width, art.height),
                                                        art.pixels),
                                  color_src="decoded", src=tag))
            continue
        if p.wtype == 2:
            # A LINE, and the reason the size guard below must not see it: an axis-aligned line
            # has a zero extent on one axis by construction.
            #
            # `FUN_00254064(x0, y0, x1, y1, colour)` (GUICODE Finding 72) branches three ways on
            # the translated endpoints, and every branch is ONE PIXEL wide -- there is no
            # thickness to choose:
            #   x0 == x1  -> FUN_00251058, a vertical span, INCLUSIVE of both ends
            #   y0 == y1  -> FUN_00250FF4, a horizontal span, likewise
            #   otherwise -> a fixed-point run-length walk emitting spans (or single pixels)
            # The colour is the same persistent fill register wtype 4 uses, so the resolution
            # order here mirrors the box path's: decoded fillA, then a mode-6 GuiData-palette
            # entry, then mode-2 white. Nothing else is painted, per this module's fill policy.
            #
            # Slopes: all 43/31/36 wtype-2 records in 13.6/11.23/14.7 are axis-aligned or exactly
            # 45 degrees, and at 45 the firmware's walk reduces to one pixel per step -- which is
            # what Bresenham draws, so the two agree pixel-for-pixel on everything in the corpus.
            # A general slope would need that walk transcribed; it is a named limit, not a silent
            # one, and nothing in any build exercises it. The styled variant (`FUN_00254350`,
            # taken when the record's flag bit 2 is set and parameterised by state+0x92) is
            # likewise unexercised: header byte 0's style nibble is 0 on all 110 records.
            colour, csrc = _decoded_fill(p, palette)
            if colour is None:
                m6 = _mode6_fill(p, gd_palette)
                if m6 is not None:
                    colour, csrc = m6, "decoded"
            if colour is None:
                colour = _mode2_white(p)
                csrc = "decoded" if colour is not None else "heuristic"
            if colour is not None:
                prims.append(Prim("line", p.x0, p.y0, w=p.x1 - p.x0, h=p.y1 - p.y0,
                                  color=colour, color_src=csrc, line_w=1, src=tag))
            continue
        # normalise the box: a fill/level record stores its box y-INVERTED (y0 is the bottom, e.g.
        # the battery fill 601,389->687,343), so paint from the min corner with an absolute size.
        gx0, gy0 = min(p.x0, p.x1), min(p.y0, p.y1)
        gw, gh = abs(w), abs(h)
        # A ONE-PIXEL box is a real box. This guard read `2 < gw` / `2 < gh` and so dropped every
        # hairline the panel draws: the 710x1 rules under the ADDITIONAL OPTIONS / NETWORK
        # DIAGNOSTICS headers, root 23's 1x80 vertical divider, and -- the case that exposed it --
        # the eleven stacked wtype-4 bars (21x1 down to 1x1) that draw the upward triangle over the
        # `Absorb`/`Float` divider on INVERTER / CHARGER SETTINGS. Nothing in the firmware supports
        # a 2 px floor: `FUN_00256C54` case 4 takes the placed box, normalises both axes and calls
        # the filled-rect primitive `FUN_0025572C` (GUICODE Finding 71), which fills INCLUSIVELY --
        # so the panel paints a row even where this exclusive convention measures zero.
        #
        # Zero is still dropped here, but that is a LIMIT, not a settled rule -- corrected the
        # same day it was written. 484 records in 13.6 have a zero extent on an axis. Most are
        # wtype 0/8/9/16 (value, directive and flow-anchor shapes that never draw) plus one
        # wtype-33, and dropping those is right. But **13 are wtype 2, and wtype 2 is the LINE
        # drawer** -- `FUN_00254064`, which branches on `x0 == x1` to a vertical-line primitive
        # and on `y0 != y1` to a fixed-point slope walk. A zero extent is the NORMAL shape for a
        # line, so those 13 (root 19's two 300x0 white rules under the floorplan headers, root
        # 27's two 0x36 verticals, and nine more) are dropped wrongly. Fixing that needs a line
        # primitive this module does not have yet, which is why it is named here rather than
        # bodged into the box loop. See `docs/RENDER.md`.
        #
        # The earlier text here read "all but one are wtype 0/8/9/16 ... and painting them would
        # invent content". That read past wtype 2 in its own histogram.
        if not (0 < gw <= PANEL_W and 0 < gh <= PANEL_H):
            continue
        if p.wtype == 3:
            # Type-3 rounded box: block A (fillA) = border, block B (fillB) = interior fill
            # (GUICODE Finding 28). Draw the interior UNLESS the transparent-interior flag is set
            # (`p.hollow`, header byte0 bit1) -- the CLIMATE box is hollow, so it renders as an
            # OUTLINE only (fillA = idx15), the quadrant showing through. Then the border on top.
            # A type-3 box is a SQUARE frame, not rounded: its drawer FUN_00255e90 paints four
            # straight borders (FUN_0025572c filled rects, no arc), the border WIDTH taken from
            # state+0x6c10 -- initialised to 1 at screen setup (GuiCode 0x2586c8) and never changed
            # -- so a 1px square outline. `proven-from-code`. Both the HOME CLIMATE frame and the
            # LIGHTS Interior/Exterior frames are this type; drawing them square with a 1px border
            # also collapses two abutting boxes' shared vertical edge to a single line (they overlap
            # at the shared x). The earlier rounded/top-rounded render was a mis-fit to the photo.
            interior, isrc = _decoded_fill(p, palette, roles=("fillB", "fillB2"))
            border, bsrc = _decoded_fill(p, palette, roles=("fillA", "fillA2"), border=True)
            # ...and the two colour modes `_decoded_fill` does not cover. It resolves mode 3
            # (inline) and mode 7 (theme index) only, so a border in mode 6 (the GuiData palette)
            # or mode 2 (the hardcoded white) fell through as `None` and the box drew NOTHING.
            # The generic box path below already chains these; this branch returned early and so
            # never reached them. That is what hid `SLIDES/AWNINGS`'s grey frame -- fillA mode 6
            # index 3, the same grey as the levelling diamond it encloses.
            if border is None:
                m6 = _mode6_fill(p, gd_palette)
                border, bsrc = (m6, "decoded") if m6 is not None else (_mode2_white(p), "decoded")
                if border is None:
                    bsrc = "heuristic"
            if interior is None:
                interior = _mode6_fill(p, gd_palette, roles=("fillB", "fillB2"))
                isrc = "decoded" if interior is not None else "heuristic"
            if interior is not None and not p.hollow:
                prims.append(Prim("rect", p.x0, p.y0, w=w, h=h,
                                  color=interior, color_src=isrc, src=tag))
            if border is not None:
                prims.append(Prim("rect", p.x0, p.y0, w=w, h=h,
                                  color=border, color_src=bsrc, outline=True, line_w=1, src=tag))
            continue
        # A text/label/value record (types 0/8 value, 9 spacer, 0xb label, 0xc touch, 0xd dynamic
        # string) is not a filled box, but its BACKGROUND can be a real fill: the 'Awning' bar carries
        # its navy in fillB = idx3. That background is fillB only -- a label's fillA is its text/
        # foreground colour or a value inherited from a sibling (Finding 29), never a box fill. So for
        # a label we consider fillB alone, and NOT the button-resting path: applying idx168 (the Stop
        # button's resting blue, inherited onto the "Engine" label's fillA) painted a blue bar across
        # the ELECTRICAL Engine widget that also SQUARED the Start/Stop button tops it overlapped. On
        # the coach "Engine Not Running" is plain white text on the page background.
        is_label = p.wtype in (0, 8, 9, 0x0b, 0x0c, 0x0d)
        # byte-only: paint a record's box ONLY when its own colour decodes -- fillA (the fill for
        # the solid types wt-4/wt-33: background, quadrant panels, buttons) if present, else fillB
        # (the resting/off state; e.g. the dark bar behind 'Awning' carries its navy in fillB =
        # idx3). A mode-0 "inherit" fill or an unresolved mode-7 index is NOT painted -- the decoded
        # parent/quadrant behind shows through (its true colour is a runtime SRAM cell we have not
        # decoded). The one exception: a runtime-STATE fill whose RESTING constant is recoverable
        # (idx160 Water Pump, idx455 Tank Heater; GUICODE Finding 28) is drawn at that resting value.
        batt_frac = 1.0
        if is_label:
            color, color_src = _decoded_fill(p, palette, roles=("fillB", "fillB2"))
        else:
            color, color_src = _decoded_fill(p, palette)
            if color is None:
                m6 = _mode6_fill(p, gd_palette)          # GuiData-palette fill (mode 6)
                if m6 is not None:
                    color, color_src = m6, "decoded"
            if color is None:
                color, color_src = _resting_fill(p, fills=resting_fills)
            if color is None:
                # HOME battery fill: the stock Chassis/House gauges (idx202/206), coloured from
                # the preview voltage via the byte-derived 11.9V threshold + green/red palette
                # colours (the firmware's own branch) -- OR, ONLY on HOME (`root == 4`), the coach
                # 48V pack clone (Task 6, 2026-09-11), whose colour cell is fresh and so unresolved
                # by every branch above; `_battery_fill` tells the two apart and is passed `root`
                # because "unresolved mode-7, not 202/206" is NOT page-scoped by itself -- every
                # other page has its own unresolved mode-7 gauges too (see `_battery_fill`'s
                # docstring). Both stock and coach cases get the byte-derived charge fraction so
                # the fill is drawn partial, not full. `_GAUGE_INDICES` keeps 202/206 out of the
                # resting palette so this branch stays the only reader of them -- see
                # `_merged_resting_fills`.
                color, batt_frac, color_src = _battery_fill(p, palette, preview or {}, root=root)
            if color is None:
                color, color_src = _decoded_fill(p, palette, roles=("fillB", "fillB2"))
        if color is None and not is_label:
            # Nothing filled this box, but a mode-2 fillA is still a colour: white. Paint it as a
            # SOLID fill, not a stroke.
            #
            # The visible "frame" on the ALTERNATOR value boxes is not a border attribute at all --
            # it is LAYERING, and therefore byte-derived with no line width to choose. Obj 1377's
            # wt-33 is (97,350)-(397,420) filled white; the wt-6 switch record right after it draws
            # its child inset to (102,354)-(392,414), so ~5 px of the white box survives on every
            # side. Drawing the box as a 2 px stroke instead (the first attempt here) produced a
            # frame visibly thinner than the coach's -- the giveaway that the thickness was being
            # invented rather than derived. GUICODE Finding 40.
            edge = _mode2_white(p)
            if edge is not None:
                prims.append(Prim("rrect", gx0, gy0, w=gw, h=gh,
                                  radius=max(0, p.coord4 or 0), color=edge,
                                  color_src="decoded", src=tag))
        if color is not None:
            if color_src == "voltage-state":
                # a battery fill is a level gauge like the tank: draw it only up to its byte-derived
                # charge fraction, from the bottom, rounding its bottom corners (radius = coord4) to
                # follow the box while the charge surface (top) stays flat. Full (frac 1.0) rounds all.
                fh = int(round(gh * batt_frac))
                if fh > 0:
                    corners = (False, False, True, True) if batt_frac < 1.0 else None
                    # Clamp the radius to what a partial-corner rrect can actually support:
                    # PIL's rounded_rectangle only computes its full_x/full_y "joined corner"
                    # collapse when ALL FOUR corners are requested (`if all(corners)`); with a
                    # two-corner mask like ours it always takes the "four separate corners"
                    # branch and insets each requested edge by `radius+1` with NO clamp of its
                    # own, so a radius that doesn't fit inside a very short fill (a near-empty
                    # battery, e.g. coach48v at 48.5V: fh=3 vs a donor radius of 4) flips that
                    # edge past its opposite one and PIL raises "y1 must be greater than or
                    # equal to y0". Keeping radius <= fh-1 keeps the bottom (or top) inset
                    # within the box; below that a square corner is the correct degrade, not a
                    # crash -- a 1-3px sliver has no visible curvature to show anyway.
                    fill_radius = max(0, min(p.coord4 or 0, fh - 1))
                    prims.append(Prim("rrect", gx0, gy0 + gh - fh, w=gw, h=fh,
                                      radius=fill_radius, corners=corners,
                                      color=color, color_src=color_src, src=tag))
            elif p.wtype in (0x21, 33):
                # coord4 (geom[4], render state +0x32) is the CORNER RADIUS. The type-0x21 drawer
                # reads it at 0x257458, offsets the arc centre to `(x0+c4, y0+c4)` and calls
                # FUN_0025451A(.., c4, c4) -- a corner arc -- then computes `(x1-x0+1) - 2*c4`
                # (0x25749c) as the STRAIGHT SPAN between the two arcs; when c4 <= 0 it falls
                # through to a plain filled rect (0x257554). `HOME.jpg`'s Water Pump / Tank Heater
                # buttons (c4 = 5) are visibly rounded, which settles it.
                #
                # (`width - 2*c4` briefly looked like an interior inset, i.e. a border width, and
                # this line was changed to `radius=0` on that reading. The arc call and the photo
                # both refute it -- GUICODE Finding 40 records the trap.)
                prims.append(Prim("rrect", gx0, gy0, w=gw, h=gh, radius=max(0, p.coord4 or 0),
                                  color=color, color_src=color_src, src=tag))
            else:
                prims.append(Prim("rect", gx0, gy0, w=gw, h=gh,
                                  color=color, color_src=color_src, src=tag))

    # text / glyphs over the boxes. Icons and labels are collected first, then overlaps are
    # resolved for a legible preview: many controls stack an icon and its label (and on/off
    # state variants) at one pen because the panel separates them by the icon's *rendered*
    # extent, which this preview does not model. So: draw one glyph per pen (drop state-variant
    # duplicates), drop a label that shares a pen with an icon *below* that icon, and stack any
    # remaining labels that still collide. Multi-line labels (`\n`) are split into stacked lines.
    def _size_top(p):
        if p.font is None:
            return 20, 0
        try:
            fm = metrics.font_metrics(data, p.font, table=table)
            return fm.height, fm.top
        except glyphs.GlyphError:
            return 20, 0

    # collect glyph candidates per pen; a control stacks on/off state variants (a lit filled
    # bulb vs an outline bulb with dimmer arrows) at one pen. Draw the lowest-ink one: that is
    # the resting/off appearance (outline), which is the sensible default and matches a coach
    # with the load off — and it is the variant carrying the ▲▼ dimmer arrows.
    candidates: dict = {}          # pen (x,y) -> list of (ink, p, img)
    for p in placed:
        if not (p.label and len(p.label) == 1 and p.font is not None):
            continue

        if p.wtype in (0, 8):
            # value/text widgets (GUICODE Findings 9/14) are never icon controls with on/off
            # state variants -- that stacking is a property of icon-type records only. A
            # value+unit widget's label just happens to be one character wide (a tank's '%',
            # a battery's 'V', render-fidelity Task 2's bug): swept in here, it draws via
            # `glyph_image` directly (no `panel_text_images`, so no alignment/box handling and
            # no honest missing-glyph fallback) at a pen this loop's dedup/stacking logic
            # never intended for it. Routing it through the label loop below instead makes it
            # go through the same box/alignment math and fallback handling every other labelled
            # record gets. (wtype 8 never actually carries a label in this corpus -- Finding
            # 14: it draws a runtime number, never a stored string -- so this half of the
            # exclusion is defensive/structural, not observed to fire; kept for the same
            # reason the type-0/8 pairing is kept together everywhere else in this module.)
            continue
        # ink-scoring only cares about alpha (coverage), so the tint colour here is irrelevant
        # to the choice of variant -- always score in white, then re-render the chosen one
        # below with its real fill.
        img = glyph_image(data, p.font, p.label, COL["white"], table=table)
        if img is None:
            continue
        ink = int(np.asarray(img)[..., 3].sum())      # total alpha = how filled the glyph is
        candidates.setdefault((p.x0, p.y0), []).append((ink, p, img))

    glyph_bottom: dict = {}        # pen (x,y) -> y just below the icon drawn there
    for (x0, y0), lst in candidates.items():
        _, p, img = min(lst, key=lambda t: t[0])       # the sparsest (resting/outline) variant
        fill, fsrc = _glyph_color(p, palette, resting_fills)  # the icon's own ink colour (mode-3/7/2)
        if fill is not None:                            # re-tint with the record's own colour
            img = glyph_image(data, p.font, p.label, fill, table=table) or img
        size, _ = _size_top(p)
        # draw at NATIVE bitmap size (the panel blits 1:1); the font cell height is line spacing,
        # not the glyph's scale — scaling a 55x28 chevron to a 72px cell made the setpoint
        # chevrons overlap into a blob. Centre the native glyph in its own decoded BOX per its
        # v-align byte (GUICODE Finding 26/30): v-align 3 = bottom, else (2/centre or absent)
        # centre it vertically in [y0, y1]. Centring in the box (not just the font cell) is what
        # puts the value +/- buttons in the middle of their tall button box instead of the top;
        # for a box no taller than the glyph (bulbs, nav icons) this is unchanged. Horizontal:
        # centre in [x0, x1] when the box is wider than the glyph, else x0.
        # Vertical placement honours the record's own v-align byte (state+0x95), the SAME byte
        # the text-label loop reads -- 3 = bottom, 2 = centre in the box, 0/1 = top (y0). Box- /
        # font-cell-centring is only the HEURISTIC stand-in and applies solely when the record
        # carries NO v-align byte. Without this, a small icon in a short box but a TALL font cell
        # (the settings network-status battery glyph: a 17px glyph, 16px box, 48px font-24 cell,
        # v-align 1 = top) fell into the `size`-cell centre and dropped ~15px below its box; its
        # byte says top, so it sits at the box top. `proven-from-code` (v-align byte; Finding 30).
        box_h_g = p.y1 - p.y0
        if p.v_align == 3:                                    # bottom
            gy = max(1, p.y1 - img.height)
        elif p.v_align == 2:                                  # centre in the box
            gy = max(1, y0 + (box_h_g - img.height) // 2) if box_h_g > img.height else max(1, y0)
        elif p.v_align in (0, 1):                             # top
            gy = max(1, y0)
        elif box_h_g > img.height:                            # heuristic centre (no v-align byte)
            gy = max(1, y0 + (box_h_g - img.height) // 2)
        else:                                                 # heuristic: centre in the font cell
            gy = max(1, y0 + (size - img.height) // 2)
        # Horizontal placement honours the record's own h-align byte (R49 +0x94), the SAME
        # byte `_line_x` reads for text labels -- 3 = right-justify in the box, 2 = centre,
        # 0/1 = left. Box-centring is only the HEURISTIC stand-in for the flow pen and applies
        # solely when the record carries NO h-align byte (matching `_line_x`'s `p.h_align is
        # None` guard); an explicit left byte keeps the glyph at x0. Without this, a single-char
        # label swept into this icon path (the Batteries '1'/'2', a Climate ':', the settings
        # floorplan 'D') was centred in its wide box regardless of its byte, opening a ~12px gap
        # after the widget it labels. `proven-from-code` (the h-align byte; GUICODE Finding 30).
        box_w = p.x1 - p.x0
        g_align = p.h_align if p.h_align is not None else p.align
        if g_align == 3:                                      # right-justified in the box
            gx = max(p.x0, p.x1 - img.width)
        elif g_align == 2 and box_w >= img.width:             # explicitly centred
            gx = p.x0 + (box_w - img.width) // 2
        elif p.h_align is None and box_w > img.width + 4:     # heuristic centre (no h-align byte)
            gx = x0 + (box_w - img.width) // 2
        else:                                                 # explicit left (0/1)
            gx = x0
        prims.append(Prim("glyph", gx, gy, text=p.label, glyph_slot=p.font,
                          image=img, color_src=fsrc if fill is not None else "heuristic",
                          clip=_text_clip(p), src=f"file@{p.record_offset:#x}"))
        glyph_bottom[(x0, y0)] = gy + img.height


    # a widget label centres in its geometry box [x0, x1] when the box is wider than the text
    # (EXT/High/Low/Auto/RET and the wide 'Awning' middle label all do this — their box is the
    # button/slot, and FUN_00257BFC centres the pen in it); an explicit right align (byte 3)
    # right-justifies; otherwise the text sits at the left edge. A label with leading whitespace
    # ('   HOME', ' CLIMATE') is a left-indent, never centred.
    def _line_x(p, width):
        box = p.x1 - p.x0
        # A label's own horizontal alignment is its R49 h_align byte (+0x94: 2 centre, 3 right, else
        # left; GUICODE Finding 26/30 -- the byte FUN_002559A0 reads per line), NOT the header align
        # byte (which positions the box). Value-box captions ('Start at', 'Time at Start Volts') are
        # header-align 3 but h_align 2, so they CENTRE in their box instead of right-justifying into
        # the +/- button. Falls back to the header align when the record carries no R49 byte.
        align = p.h_align if p.h_align is not None else p.align
        if align == 3 and width:                            # right-justified in the box
            return max(p.x0, p.x1 - width)
        if align == 2 and width and box >= width:           # explicitly centred in the box
            return p.x0 + (box - width) // 2
        # The wider-box centring below is a HEURISTIC stand-in for the flow pen, so it applies ONLY
        # when the record carries NO explicit R49 h-align byte. An explicit h-align of 0 or 1 means
        # LEFT: every line stays at x0, so a multi-line label like 'Battery\nVoltage:' (h-align 1)
        # left-aligns both lines instead of centring the narrower 'Battery' over the wider 'Voltage:'.
        # leading-space check preserves a left indent (' CLIMATE'); a wtype-13 dynamic string
        # (p.label is None -- its text came from the dynamic_text map) never has one.
        if (p.h_align is None and width and box > width + 4
                and not (p.label or "")[:1].isspace()):     # centred in a wider box (no h-align byte)
            return p.x0 + (box - width) // 2
        return p.x0

    def _line_w(p, line):
        if p.font is None or not line:
            return 0
        try:
            return metrics.text_width(data, p.font, line, table=table) or 0
        except glyphs.GlyphError:
            return 0

    def _overlap(a0, a1, b0, b1):
        return a0 < b1 and b0 < a1

    # Labels at the same pen only "collide" (and stack downward) when their DRAWN extents
    # actually overlap. Same pen with disjoint drawn-x is a false collision -- e.g. 'Light
    # Master' centres into the middle of its wide box while its 'ON' button label sits at the
    # left of a narrow box; they share a pen (x0, y0) but never touch, so each keeps its own
    # vertically-centred position instead of one stacking below the other. (Also seen on LIGHTS
    # 'Exterior Master'/'ON' and ELECTRICAL 'Stop'/'Engine'.)
    dyn_text = (preview or {}).get("dynamic_text", {}) if preview else {}
    stack_at: dict = {}            # pen (x,y) -> [(dx0, dx1, top, bot)] already placed there
    for p in placed:
        # A wtype-13 dynamic-string widget carries no inline label -- its on-screen text is the
        # runtime value of `value_slot`'s variable (GUICODE Finding 31). When a `dynamic_text`
        # map supplies that slot's string (byte-derived and mode-selected -- e.g. the ELECTRICAL
        # AES/AGS run-time caption, slot 269, and the "AES"/"AGS" indicator, slot 129), draw it
        # through this same label path so it inherits the record's font, box and h/v-align.
        eff_label = p.label
        dyn = False
        if not eff_label and p.wtype == 13 and p.value_slot in dyn_text:
            eff_label = dyn_text[p.value_slot]
            dyn = True
        # allow multi-line labels: `\n` is not "printable" but each line is (split below).
        # A wtype-0 label is exempt from the printable test: `FUN_002557C0` hands every byte to
        # `FUN_00251AA0`, which indexes the font by it with no character test (0x251AA0), and
        # icon font 25's characters ARE control bytes (`\x01`..`\x04`). The panel draws them;
        # this filter dropped them (GUICODE Finding 84). No stock wtype-0 label holds one.
        if p.wtype == 0:
            if not (eff_label and eff_label.strip(" ")):
                continue
        elif not (eff_label and eff_label.strip()
                  and eff_label.replace("\n", "").isprintable()):
            continue
        if len(eff_label) == 1 and p.font is not None and (p.x0, p.y0) in glyph_bottom:
            # this record is (one state of) the icon already drawn — skip its text form
            if glyph_image(data, p.font, eff_label, COL["white"], table=table) is not None:
                continue
        size, top = _size_top(p)
        # Split into lines, keeping a LEADING/interior blank line (it advances the pen one row, the
        # way FUN_002559A0 does) but dropping trailing blanks. So ELECTRICAL "\nStart at" is one word
        # on the SECOND row -- it lines up with "Start Volts" (row 2 of "Time at\nStart Volts"), not at
        # the box top. `raw_lines` (blanks kept) drives the vertical advance; `lines` (content only)
        # drives width/alignment. (An earlier fix wrongly dropped the leading blank, printing too high.)
        raw_lines = _label_lines(eff_label)
        # word-wrap a line too wide for its own box, breaking on spaces (the panel does this: the
        # CLIMATE "Bath Fan" option label is wider than its 80px button cell and wraps to Bath / Fan,
        # per MCF-FORMAT §6). Only fires when the single line actually overflows AND has a space, so a
        # label that fits is untouched.
        #
        # The budget is the box's INCLUSIVE width and the break test is STRICT, both
        # `proven-from-code` (GUICODE Finding 39). `FUN_002559A0` computes it at 0x255a7a-0x255a8e:
        #
        #     r3 = x0 (state+0x2a) ; r2 = x1 (state+0x2e) ; r3 = r2 - r3 ; r3 += 1   <- inclusive
        #
        # and breaks only when the measured run EXCEEDS it (0x255b0c `cmp r0,r3` / `ble` = fits).
        # So a line of exactly `box_w + 1` px still fits. That one pixel is the whole difference
        # between the render and the coach on two labels: 'Desired Charge State:' measures 161 in a
        # 160-wide box and 'I/C Firmware Rev.' 151 in a 150-wide box, and `ALTERNATOR-SYSTEM.jpg`
        # shows each drawn on ONE line. Labels that genuinely overflow still wrap -- 'DEVICES/
        # FAULTS' is 148 against 116, 'Bath Fan' 92 against 81.
        box_w = p.x1 - p.x0
        budget = box_w + 1

        # Since 2026-09-06 the break itself is `metrics.wrap_text` -- a transcription of
        # `FUN_002559A0`'s own loop, decompiled from the `guicode136` project -- rather than the
        # greedy break-on-space that stood here. The two agree on ordinary captions; the firmware
        # additionally breaks after a `-` that is not followed by a space (so `auto-start` may
        # split at the hyphen while `A - B` does not split at that dash) and stops at 30 lines.
        # The fallback keeps the old shape for the one case the transcription declines: a string
        # whose glyphs do not all resolve, where `wrap_text` returns None rather than invent a
        # break, exactly as `text_width` declines to invent a width.
        def _wrap(line):
            if box_w <= 2 or _line_w(p, line) <= budget:
                return [line]
            out = metrics.wrap_text(data, p.font, line, budget, table=table) \
                if p.font is not None else None
            if out is None:
                if " " not in line.strip():
                    return [line]
                out, cur = [], ""
                for word in line.split(" "):
                    if not word:
                        continue
                    trial = f"{cur} {word}".strip()
                    if not cur or _line_w(p, trial) <= budget:
                        cur = trial
                    else:
                        out.append(cur)
                        cur = word
                if cur:
                    out.append(cur)
            return out or [line]

        raw_lines = [w for ln in raw_lines for w in (_wrap(ln) if ln.strip() else [ln])]
        lines = [ln for ln in raw_lines if ln.strip()]
        multiline = len(raw_lines) > 1
        # A multi-line label advances by the record's own line-height byte (R49 +0x96 =
        # `p.line_height`) and measures the block as (n-1)*lineHeight + font cell height. Single-line
        # uses the size+2 default. The advance counts the blank rows (len(raw_lines)), so a leading
        # blank lands the text exactly one line_height below the box top.
        lh = _effective_line_height(data, p, table) if multiline else None
        if not lh:
            lh = size + 2
        block_h = ((len(raw_lines) - 1) * lh + size) if multiline \
            else (len(lines) * size + max(0, len(lines) - 1) * 2)
        # this block's drawn horizontal span = the widest placement across its lines
        span0, span1 = p.x1, p.x0
        for line in lines:
            w = _line_w(p, line)
            lx = _line_x(p, w)
            span0, span1 = min(span0, lx), max(span1, lx + w)

        key = (p.x0, p.y0)
        if p.wtype in (0, 8):
            # Value/unit widget (a tank '%', battery 'V', temperature '°'): FUN_002557C0 draws it
            # single-line at pen_y - font[0x0b] (0x00255810), i.e. the glyph top sits `top` (the
            # font ascent) above the pen. p.y0 is that pen, so the text rises above it -- byte-
            # derived, and the reason these read too low when drawn at the raw pen. Keeps a value
            # and its unit aligned (both use their own pen - top).
            base_y = max(1, p.y0 - top)
        elif p.v_align is not None:
            # A label's vertical placement is its decoded v-align byte (state+0x95, 0x00255C44;
            # GUICODE Finding 26/30): 2 = centre in the box, 3 = bottom (y1 - block_h + 1), else
            # (0/1) top. Applies to ANY label carrying it -- single OR multi-line -- so it
            # supersedes the glyph_bottom / box-centre heuristics. The HOME bulb captions are
            # v-align 3 (bottom, below the glyph); ELECTRICAL 'Start at' is v-align 1 (top, above
            # its value, not centred onto it); the House/Chassis/Water Pump captions are v-align 2
            # (centre). The centre case keeps the box_h>block_h guard so a label in a box no taller
            # than its text stays at y0 rather than riding up.
            box_h = p.y1 - p.y0
            if p.v_align == 2:
                base_y = p.y0 + (box_h - block_h) // 2 if box_h > block_h else p.y0
            elif p.v_align == 3:
                base_y = p.y1 - block_h + 1
            else:
                base_y = p.y0
        else:
            base_y = glyph_bottom.get(key)             # sits below an icon at the same pen?
            if base_y is None:
                # Single-line labels: no decoded box-vertical align (FUN_002557C0 draws at the
                # pen). Centre the block in the box when the decoded box [y0, y1] is taller than
                # the block (House/Chassis battery captions) -- a heuristic stand-in for the flow
                # pen, guarded so a header over its box (CLIMATE) and degenerate (h≈0) boxes (the
                # '%' units) stay at y0.
                box_h = p.y1 - p.y0
                base_y = p.y0 + (box_h - block_h) // 2 if box_h > block_h else p.y0
        # push below any already-placed label at this pen whose drawn-x AND y actually overlap
        for (ox0, ox1, ot, ob) in stack_at.get(key, []):
            if _overlap(span0, span1, ox0, ox1) and _overlap(base_y, base_y + block_h, ot, ob):
                base_y = max(base_y, ob + 2)
        stack_at.setdefault(key, []).append((span0, span1, base_y, base_y + block_h))

        # Text INK is the record's own fillA, and it decodes exactly like any other colour field
        # (GUICODE Finding 11's mode table): mode 3 inline BGR, mode 2 = white, mode 7 = a
        # colour-table index, mode 6 = a GuiData-palette index -- and mode 0 has already been
        # substituted by the inherited slot in `layout._apply_colors` (Finding 29). This used to
        # resolve mode 3 ONLY and default everything else to a hardcoded `COL["white"]`, which was
        # right for the 1848 mode-2 labels by accident and WRONG for the 128 mode-7 ones: the red
        # (idx 118) and cyan (idx 35) status glyphs all painted white. `border=True` is the correct
        # sense here -- for a label, fillA is the ink, so mode 2 really is white.
        fill, color_src = _decoded_fill(p, palette, border=True)
        if fill is None:
            m6 = _mode6_fill(p, gd_palette)
            if m6 is not None:
                fill, color_src = m6, "decoded"
        if fill is None:
            color_src = "heuristic"
        color = fill if fill is not None else COL["white"]
        # iterate `raw_lines` (blank rows kept): a blank row only advances the pen by lh (the way
        # FUN_002559A0 does), landing a leading-"\n" caption like "Start at" on its second row.
        for line in raw_lines:
            if not line:
                base_y += lh
                continue
            width = _line_w(p, line)
            x0 = _line_x(p, width)
            # dynamic-string text is byte-derived from the file but mode-SELECTED at runtime, so
            # it is tagged distinctly from a stored inline label (`file@...`).
            tag = (f"dynamic@slot{p.value_slot}" if dyn else f"file@{p.record_offset:#x}")
            # the panel's own glyph bitmaps, at the decoded font slot -- real pixels, not a
            # stand-in; this is the norm now (`GUICODE.md` Finding 22 closed the decode gap
            # that used to force every real label here to the fallback). `panel_text_images`
            # returns None only if a glyph in the line doesn't resolve at all (missing font/
            # slot, or a character the font/table can't place); only then does the DejaVu
            # stand-in get used, and it stays labelled as such.
            imgs = (panel_text_images(data, p.font, line, color, table=table)
                    if p.font is not None and line else None)
            if imgs:
                # Anchor the Prim at the line's cell TOP (base_y = pen_y): each glyph carries its
                # own vertical bearing dy (glyph.y) and places itself within the cell, so
                # baselines align, x-height letters sit below the caps, and descenders drop past
                # the baseline (the blitter's `pen_y + glyph[0xc]`). The old code took one shared
                # top with a `(size - max_glyph_height)//2` centre and drew every glyph there,
                # which top-aligned x-height letters with the caps and lifted the descenders.
                prims.append(Prim("text", x0, max(1, base_y), text=line, align="left",
                                  color=color, color_src=color_src, font_src="panel",
                                  glyph_images=imgs, clip=_text_clip(p), src=tag))
            else:
                prims.append(Prim("text", x0, max(1, base_y + size // 2), text=line,
                                  valign="middle", size=max(10, round(size * 0.82)),
                                  align="left", color=color, color_src=color_src,
                                  font_src="heuristic", clip=_text_clip(p), src=tag))
            base_y += lh

    # Preview runtime values (NOT byte-derived): the value-widget DIGITS are bus-delivered (open
    # thread 5), never in the MCF. When a `preview` block is supplied, inject sample values so the
    # render looks live -- every injected Prim is tagged `src="preview"`, distinct from "decoded".
    # Only the digits are preview; each widget's font and pen are byte-derived, and WHICH value a
    # widget takes is decided by byte-derived discriminators (its unit char / gauge-well sibling /
    # header position), never guessed.
    if preview:
        _inject_preview_values(data, placed, prims, preview, table, root=root)

    return [Placement("decoded", 0, 0, prims=prims, src="decoded")]


def _inject_preview_values(data: bytes, placed: list, prims: list, preview: dict,
                           table: "glyphs.GlyphTable | None", root: int | None = None) -> None:
    """Draw sample runtime values into value widgets (wt-8) for a live-looking preview.

    Role of each value widget, by byte-derived signal:
      - '%' unit + a gauge well (wt 0x20) in the same object -> a TANK: show `tank_level` (percent).
        The bare '%' with no well (screen brightness) is left alone.
      - 'V' unit -> a voltage, routed by the widget's value_slot (`_VOLTAGE_SLOTS`): Chassis
        (`chassis_voltage`), House/Coach battery (`coach_voltage`, ~48V on a Li coach), and the
        ELECTRICAL AGS Start/Stop setpoints (`start_volts` / `stop_volts`).
      - a degree unit (raw char 0xb0, non-ASCII so it has no decoded label) -> a TEMPERATURE: show
        `temperature_c` (the fixed-default unit; `temperature_f` is carried in the config but not
        drawn -- see the CoachConfig.preview note).
      - value widget reading the time register (value_slot `CLOCK_SLOT` = 226 -> 0x200399DC,
        minutes-since-midnight from a CAN time message) -> the CLOCK: show `time_24`. Identified by
        its byte-derived register, NOT by screen position (GuiCode dig, 2026-08-16).
    Each number is right-justified with its right edge at the value widget's own pen (the align-3
    value convention, GUICODE Finding 26), matching how the panel lays out value+unit.
    """
    from . import metrics
    tank_pct = preview.get("tank_level")
    temp_c = preview.get("temperature_c")
    time_24 = preview.get("time_24")
    if preview.get("cell_values"):
        _inject_cell_values(data, placed, prims, preview["cell_values"], table)

    # objects that contain a gauge well -- the byte-derived "this is a tank" signal
    well_objs = {p.obj_index for p in placed if p.wtype == 0x20}

    def unit_after(i: int):
        """The unit record (wt-0) paired with the value widget at index i (same object)."""
        for q in placed[i + 1:i + 5]:
            if q.wtype == 0 and q.obj_index == placed[i].obj_index:
                return q
        return None

    def draw(value: "int|float|str", pen_x: int, y: int, font: int) -> None:
        # Right-justified (right edge at the value pen, the align-3 convention) AND raised to
        # pen_y - font_top: FUN_002557C0 draws a value single-line at pen_y - font[0x0b], so the
        # glyph top sits the font ascent above the pen. Without it the digits render too low.
        text = f"{value}"
        try:
            w = metrics.text_width(data, font, text) or 0
            top = metrics.font_metrics(data, font).top
        except glyphs.GlyphError:
            return
        imgs = panel_text_images(data, font, text, COL["white"], table=table)
        if not imgs:
            return
        prims.append(Prim("text", pen_x - w, max(1, y - top), text=text, align="left",
                          color=COL["white"], color_src="preview", font_src="panel",
                          glyph_images=imgs, src="preview"))

    for i, p in enumerate(placed):
        if p.wtype != 8 or p.font is None:
            continue
        unit = unit_after(i)
        uchar = None
        if unit is not None:
            uchar = (unit.label or "").strip() or (
                "°" if (unit.label_offset and data[unit.label_offset] == 0xB0) else "")
        if uchar == "%" and p.obj_index in well_objs and tank_pct is not None:
            draw(int(round(tank_pct)), p.x0, p.y0, p.font)
        elif uchar == "V":
            # Each 'V' widget is a DISTINCT voltage, identified by its byte-derived value_slot
            # (register), never by screen position (2026-08-16 dig): HOME Chassis (205) and
            # House/Coach battery (209 -- the same register the ELECTRICAL "Battery Voltage" box
            # reads, ~48V/54V on a Li coach), and the ELECTRICAL AGS setpoints Start (272) / Stop
            # (274). Route each to its own preview key; a 'V' slot with no key is left undrawn
            # (its digits are bus-delivered, not stored) rather than shown a wrong shared number.
            # The coach-48V clone's value widget reads slot 209 (the live cell the ELECTRICAL box
            # reads), so it resolves through `_VOLTAGE_SLOTS[209] = "coach_voltage"` like any other
            # 'V' widget -- no clone-specific value injection is needed (only the FILL is special).
            key = _VOLTAGE_SLOTS.get(p.value_slot)
            volts = preview.get(key) if key else None
            if volts is not None:
                draw(volts, p.x0, p.y0, p.font)
        elif uchar == "°" and temp_c is not None:
            draw(temp_c, p.x0, p.y0, p.font)
            # the degree unit (0xb0) is byte-derived content the ASCII label loop skips (non-ASCII);
            # draw it at the unit widget's own pen so the temperature reads "22°".
            dimgs = panel_text_images(data, p.font, "°", COL["white"], table=table)
            if dimgs:
                dtop = metrics.font_metrics(data, p.font).top
                prims.append(Prim("text", unit.x0, max(1, p.y0 - dtop), text="°", align="left",
                                  color=COL["white"], color_src="preview", font_src="panel",
                                  glyph_images=dimgs, src="preview"))
        elif p.value_slot == CLOCK_SLOT and time_24 is not None:
            draw(time_24, p.x0, p.y0, p.font)      # the clock: value widget on the time register


def format_cell_value(value: int, fmt: tuple) -> str:
    """The panel's mode-0 number formatter (`FUN_00251E38`/`FUN_002521C8`, GUICODE Finding 65)
    for a u16 cell `value` under directive `fmt = (mode, flags, width, places)`: zero-padded to
    `places + 1` digits, trailing fractional zeros trimmed to one unless flag bit 4 (`0x10`) --
    INFERENCE: whether this trim applies unconditionally, or only to format types > 6
    (`cmp r7,#6; ite ls; movls r3,#0; cbz r3` at `0x002526D4`-`0x002526E4`; `r7` holds the type but
    its origin is not traced), is an open question. LEVEL's own directives use `places <= 1`, so
    the trim never fires here regardless of the answer.

    A `.` sits at `len - places` and counts toward `width` (proven: `adds r5,#1` at `0x0025275E`
    runs ahead of the width compare `cmp r1,r5; blo` at `0x002528A4`). A string wider than a
    non-zero `width` is drawn as exactly `width` dashes (proven: the width-dash loop at
    `0x002529A4`, which supplies the dash glyph via `movs r4,#0x2d`). The field padding
    (justification) is not applied: every caller anchors by the wt-8's own header align, which is
    where the ink lands."""
    mode, flags, width, places = fmt
    if mode not in (0, 1):
        raise ValueError(f"format mode {mode} is not the plain decimal this preview models")
    s = str(value).zfill(places + 1)
    if places:
        whole, frac = s[:-places], s[-places:]
        if not flags & 0x10:
            frac = frac.rstrip("0") or "0"
        s = whole + "." + frac
    if width and len(s) > width:
        s = "-" * width
    return s


def _inject_cell_values(data: bytes, placed: list, prims: list, cell_values: dict,
                        table: "glyphs.GlyphTable | None") -> None:
    """PREVIEW ONLY (not byte-derived): draw `cell_values[value_slot]` into every wt-8 readout on
    that display cell, formatted by the last wt-9 directive walked before it (the directive state
    is persistent along the walk, Finding 65; the walk resets it to width 10, places 0) and
    anchored at the wt-8's pen by its header align (`FUN_002557C0`: `x0 = pen`, `pen - w/2`,
    `pen - (w - 1)`), the cell top at `pen_y - font[0x0b]`. Ink is the record's mode-3 fillA,
    else white. Lets a test force a live readout the way `slot_values` forces a switch."""
    from . import metrics
    fmt = (0, 0x18, 10, 0)
    for p in placed:
        if p.wtype == 9:
            f = records.decode_record(data, p.record_offset).fmt
            if f:
                fmt = tuple(f)
            continue
        if p.wtype != 8 or p.font is None or p.value_slot not in cell_values:
            continue
        try:
            text = format_cell_value(cell_values[p.value_slot], fmt)
        except ValueError:
            continue                      # a clock/hex readout: not modelled, left undrawn
        ink = _inline_fill(p) or COL["white"]
        try:
            w = metrics.text_width(data, p.font, text) or 0
            top = metrics.font_metrics(data, p.font).top
        except glyphs.GlyphError:
            continue
        imgs = panel_text_images(data, p.font, text, ink, table=table)
        if not imgs:
            continue
        x = {1: p.x0, 2: p.x0 - w // 2}.get(p.align, p.x0 - (w - 1))
        prims.append(Prim("text", x, max(1, p.y0 - top), text=text, align="left", color=ink,
                          color_src="preview", font_src="panel", glyph_images=imgs,
                          src="preview"))


def climate_placements_decoded(data: bytes, root: int | None = None,
                               match_table_off: int | None = None,
                               switch_values: dict | None = None,
                               slot_values: dict | None = None) -> list[Placement]:
    """Approach A, Climate-specific: a thin wrapper over `screen_placements` that supplies
    the Climate page's default root (`layout.climate_root`) when `root` is not given. Kept
    under its original name for the existing call sites (`cli.py`, `render_climate_decoded`,
    the Task 1-3 tests); it carries no logic of its own beyond that default -- see
    `screen_placements` for the actual compositor.
    """
    from . import layout
    if root is None:
        root = layout.climate_root(data)
    return screen_placements(data, root, match_table_off=match_table_off,
                             switch_values=switch_values, slot_values=slot_values)


# --------------------------------------------------------------------------------------
# Layer 3: rasteriser (shared)
# --------------------------------------------------------------------------------------

# Fill sources whose colour is chosen at RUNTIME from live state, so the value drawn here is only
# the resting/default constant, NOT what the panel actually shows in a given state (a Low Volts /
# AGS-trigger toggle reads black-vs-cyan from config 0x2F; a battery box reads red-vs-green from its
# voltage). The renderer is not a hardware oracle for these (see the
# `firefly-renderer-not-a-hardware-oracle` note): it silently drew a confident blue for a button the
# coach showed black. `mark_dynamic` tags each such fill with a small magenta corner triangle so a
# reviewer reads "this colour is runtime-determined -- verify on hardware" instead of trusting it.
_DYNAMIC_FILL_SRCS = frozenset({"resting", "voltage-state"})
_DYNAMIC_MARK = (255, 0, 255)     # magenta: not in the panel palette, so unmistakably a tool marker


def _text_clip(p) -> tuple | None:
    """The INCLUSIVE clip box a wtype-11/13 record's text is drawn inside, or `None`.

    `FUN_002559A0` -- the wtype-11/13 text drawer, decompiled 2026-09-06 -- intersects the
    record's own box with the enclosing clip and installs it before drawing any line, restoring
    the previous one afterwards::

        iVar21 = max(x0, clip_x0)   iVar17 = min(x1, clip_x1)
        iVar12 = max(y0, clip_y0)   local_8c8 = min(y1, clip_y1)
        FUN_00253e38(iVar21, iVar12, iVar17, local_8c8)   # set
            ... every line ...
        FUN_00253e38(iVar10, iVar9, iVar22, iVar11)       # restore

    The box is inclusive on both ends (the same `x1 - x0 + 1` width the wrap uses), which is why
    a record whose box collapsed to `x0 == x1` still draws exactly ONE pixel column -- v20's
    corner mark, and the observation that started this.

    Only wtype 11/13 get it. A **wtype-0** single character goes through a different drawer and
    is NOT bounded this way: HOME's `V` and CLIMATE's `°` have 0x0 placed boxes and draw in full
    on the coach. Clipping every text prim to its box would truncate them, which is why the first
    attempt at this fix was refuted before it shipped.

    **Both axes.** It took three attempts to get here and the last one was decided by measuring
    the coach photo rather than reasoning about it:

    * *Held back once for a 2 px error of our own* -- this renderer penned 13.6's
      `'Go Power!\nSettings'` second line at 443 where the firmware pens it at 441. That was
      `_effective_line_height` shipped dead (a swallowed `NameError`, see there). Fixed; the two
      now agree and a vertical clip cuts zero pixels on every stock page.
    * *Held back again because v20's mark "showed one column of pixels"*, which a 1x1 clip
      seemed unable to produce. Measuring the photo settled it: the surviving ink is at panel
      **y 204-212**, inside `'Water'`'s own row band (201-214), and there is **no ink at all** at
      x 573-594. The column is the caption's `r` bleeding at photo scale, not the mark. Nothing
      of the mark was drawn -- and that is exactly what a 1x1 clip gives, because the timer
      glyph's own pixel at (0, 0) has coverage 0. The observation and the code agree.

    So a record whose box collapses draws its glyph's top-left pixel and nothing else, which is
    usually nothing at all.

    **The enclosing clip is not an approximation here -- it is provably the whole screen**
    (`GUICODE.md` Finding 70, 2026-09-06). Only two record types can ever narrow it: wtype 7 and
    wtype 14, in `FUN_00256C54` at `0x257A0E`/`0x257A7C`, each gated on record flag bit `0x200`,
    and they are the ONLY writers of the rectangle besides the screen-paint reset. `FUN_00258C60`
    sets the screen rectangle to `(0, 0, 799, 479)` at GUI init and nothing else ever writes it.
    **No wtype-7 or wtype-14 record exists in 11.23, 13.6 or 14.7** -- every object in all three
    decodes, 6109/8272/5311 records, and all three carry the identical wtype set
    `{0,2,3,4,5,6,8,9,10,11,12,13,16,19,32,33}`. So `box n enclosing == box`, and returning the
    record's own box is exact. `tests/test_render_bytes.py` pins the absence, so a build that
    grows such a record fails a test instead of silently rendering too generously.

    This replaces an earlier note here that `FUN_00251BB4` installs a container clip in the
    wtype-5/6 branch. It does not: it unions its argument rect into an accumulator at
    `0x2003A994` (drawn extent) and never touches the clip.
    """
    if p.wtype not in (11, 13):
        return None
    return (p.x0, p.y0, p.x1, p.y1)


def _paste_clipped(img, glyph, x: int, y: int, clip: tuple | None) -> None:
    """Paste one glyph bitmap, cut to `clip` (INCLUSIVE, as the firmware's rectangle is).

    `None` means unbounded -- a wtype-0 character drawn at the pen, which the panel does not
    bound (see `_text_clip`). Cropping the SOURCE rather than masking the destination keeps the
    glyph's own alpha intact, so a partially-clipped character keeps its anti-aliasing on the
    surviving edge, which is what the coach shows.
    """
    if clip is None:
        img.paste(glyph, (x, y), glyph)
        return
    cx0, cy0, cx1, cy1 = clip                        # a None bound is "not clipped on this axis"
    left = max(x, cx0) if cx0 is not None else x
    top = max(y, cy0) if cy0 is not None else y
    right = min(x + glyph.width - 1, cx1) if cx1 is not None else x + glyph.width - 1
    bottom = min(y + glyph.height - 1, cy1) if cy1 is not None else y + glyph.height - 1
    if right < left or bottom < top:
        return                                       # entirely outside the clip: nothing drawn
    if (left, top, right, bottom) == (x, y, x + glyph.width - 1, y + glyph.height - 1):
        img.paste(glyph, (x, y), glyph)              # wholly inside: the common case, no crop
        return
    sub = glyph.crop((left - x, top - y, right - x + 1, bottom - y + 1))
    img.paste(sub, (left, top), sub)


def rasterize(placements: list[Placement], show_touch: bool = False,
              mark_dynamic: bool = True) -> Image.Image:
    """Draw absolute-placed primitives onto an 800x480 image.

    `mark_dynamic` (default on) flags runtime-state fills (`_DYNAMIC_FILL_SRCS`) with a magenta
    corner triangle; pass False for a clean render with no tool markers.
    """
    img = Image.new("RGB", (PANEL_W, PANEL_H), COL["navy"])
    draw = ImageDraw.Draw(img)
    for pl in placements:
        for p in pl.prims:
            ax, ay = pl.x + p.x, pl.y + p.y
            dyn_mark = (mark_dynamic and p.kind in ("rect", "rrect") and not p.outline
                        and p.color_src in _DYNAMIC_FILL_SRCS and p.w > 6 and p.h > 6)
            # box coords are x1/y1-INCLUSIVE, matching the firmware's box primitives (e.g. the
            # type-3 frame FUN_00255e90 draws its right border AT x1, not x1-1). So the drawn
            # rectangle spans [x0, x0+w] = [x0, x1]: two boxes that share an edge (x1==next x0)
            # then overlap on that column and read as ONE line (the LIGHTS Interior/Exterior divider).
            if p.kind == "rect":
                if p.outline:
                    draw.rectangle([ax, ay, ax + p.w, ay + p.h],
                                   outline=p.color, width=p.line_w)
                else:
                    draw.rectangle([ax, ay, ax + p.w, ay + p.h], fill=p.color,
                                   outline=p.stroke, width=p.line_w if p.stroke else 1)
            elif p.kind == "rrect":
                corners = p.corners if p.corners is not None else (True, True, True, True)
                if p.outline:
                    draw.rounded_rectangle([ax, ay, ax + p.w, ay + p.h],
                                           radius=p.radius, outline=p.color, width=p.line_w,
                                           corners=corners)
                else:
                    draw.rounded_rectangle([ax, ay, ax + p.w, ay + p.h],
                                           radius=p.radius, fill=p.color,
                                           outline=p.stroke, width=p.line_w if p.stroke else 1,
                                           corners=corners)
            if dyn_mark:                          # overlay AFTER the fill, else the box hides it
                t = min(12, p.w // 3, p.h // 3)
                draw.polygon([(ax, ay), (ax + t, ay), (ax, ay + t)], fill=_DYNAMIC_MARK)
            if p.kind == "image" and p.image is not None:
                img.paste(p.image, (ax, ay))
            if p.kind == "line":
                # endpoints, both inclusive: `w`/`h` are the SIGNED deltas, not sizes, so a
                # vertical line is w=0 and a horizontal one h=0. 1 px wide -- see `screen_placements`.
                draw.line([ax, ay, ax + p.w, ay + p.h], fill=p.color, width=p.line_w or 1)
            if p.kind == "ellipse":
                draw.ellipse([ax, ay, ax + p.w, ay + p.h], fill=p.color,
                             outline=p.stroke, width=p.line_w if p.stroke else 1)
            elif p.kind == "glyph" and p.image is not None:
                gi = p.image
                gx = ax - (gi.width // 2 if p.align == "center"
                           else gi.width if p.align == "right" else 0)
                _paste_clipped(img, gi, gx, ay, p.clip)
            elif p.kind == "text" and p.glyph_images:
                # real panel glyph bitmaps, one per character, at their own kerned advance dx and
                # per-glyph vertical bearing dy (`panel_text_images`) -- native size, matching how
                # the blitter blits each glyph at `cell top + (glyph.x, glyph.y)`. The prim's y is
                # the CELL TOP, not the record's pen: for a wtype-0 record it is already
                # `pen_y - font[0x0b]` (GUICODE Finding 84, `metrics.wt0_ink_offset`).
                for dx, dy, gi in p.glyph_images:
                    _paste_clipped(img, gi, ax + dx, ay + dy, p.clip)
            elif p.kind in ("text", "icon", "glyph"):    # glyph w/o image -> text fallback
                fnt = _font(p.size, p.bold)
                vert = "m" if p.valign == "middle" else "a"
                anchor = {"left": "l", "center": "m", "right": "r"}[p.align] + vert
                draw.text((ax, ay), p.text, font=fnt, fill=p.color, anchor=anchor)
            elif p.kind == "touch" and show_touch:
                if p.w and p.h:
                    draw.rectangle([ax, ay, ax + p.w - 1, ay + p.h - 1],
                                   outline=(0, 255, 0))
    return img


def render_climate(mcf_path: str, out_path: str, show_touch: bool = False) -> str:
    data = open(mcf_path, "rb").read()
    img = rasterize(climate_placements(data), show_touch=show_touch)
    img.save(out_path)
    return out_path


def render_screen_image(data: bytes, root: int, *,
                        match_table_off: int | None = None,
                        switch_values: dict | None = None,
                        slot_values: dict | None = None,
                        show_touch: bool = False,
                        tank_level: float = 0.0,
                        preview: dict | None = None) -> Image.Image:
    """`render_screen` on in-memory bytes, returning the PIL image instead of saving --
    the Studio server's entry point (it renders compiled images that exist only in
    memory, never a file on disk). Extracted 1:1 from `render_screen`'s body; the
    path-based function is now a thin wrapper over this one, so the two can never drift.
    """
    placements = screen_placements(data, root, match_table_off=match_table_off,
                                   switch_values=switch_values, slot_values=slot_values,
                                   tank_level=tank_level, preview=preview)
    img = rasterize(placements, show_touch=show_touch)
    return img


def render_screen(mcf_path: str, out_path: str, root: int,
                  match_table_off: int | None = None,
                  switch_values: dict | None = None,
                  slot_values: dict | None = None,
                  show_touch: bool = False,
                  tank_level: float = 0.0,
                  preview: dict | None = None) -> str:
    """Render ANY GuiData page to a PNG, by object-table root index -- the general entry
    point Task 4 adds. `root` is the only per-page input (`layout.Screen.walk`'s starting
    object); everything drawn comes from `screen_placements`, so a new page needs no new
    code here, only its root. `cli.py`'s `render` subcommand already exposes this by
    `--root N` (an explicit index) or `--page NAME` (resolved generically per build by
    `_resolve_page_root`'s header-label lookup) -- both ultimately call this function.
    `tank_level` (0..1) is a preview of the runtime tank fill height (default 0 = empty).
    `preview` (runtime sample values, NOT byte-derived) injects value-widget digits -- see
    `_inject_preview_values`.
    """
    data = open(mcf_path, "rb").read()
    img = render_screen_image(data, root, match_table_off=match_table_off,
                              switch_values=switch_values, slot_values=slot_values,
                              show_touch=show_touch, tank_level=tank_level,
                              preview=preview)
    img.save(out_path)
    return out_path


def render_climate_decoded(mcf_path: str, out_path: str, root: int | None = None,
                           match_table_off: int | None = None,
                           switch_values: dict | None = None,
                           slot_values: dict | None = None,
                           show_touch: bool = False,
                           tank_level: float = 0.0,
                           preview: dict | None = None) -> str:
    """Approach A, Climate-specific: a thin wrapper over `render_screen` that resolves
    `root` to the Climate page (`layout.climate_root`) when not given. Kept under its
    original name for `cli.py` and the Task 1-3 tests, which predate `render_screen`'s
    generalisation (Climate was the first, and for a while the only, page done)."""
    if root is None:
        from . import layout
        root = layout.climate_root(open(mcf_path, "rb").read())
    return render_screen(mcf_path, out_path, root, match_table_off=match_table_off,
                         switch_values=switch_values, slot_values=slot_values,
                         show_touch=show_touch, tank_level=tank_level, preview=preview)


if __name__ == "__main__":
    import sys
    src = sys.argv[1] if len(sys.argv) > 1 else "firefly-13.6/mcf.bin"
    out = sys.argv[2] if len(sys.argv) > 2 else "build/climate-render.png"
    print(render_climate(src, out))
