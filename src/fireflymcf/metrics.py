"""Text metrics — the content-render extents the layout engine reads back for type-0/8 widgets.

`docs/GUICODE.md` Finding 14. A single-line text/value widget is drawn by `FUN_002557C0`,
which — after measuring the string — writes its *rendered* bounding box into the render
state at `+0x6c12..+0x6c18`. `FUN_00257BFC`'s per-type frame switch then copies that box into
the frame reference `+0x3a..+0x40` **for widget types 0 and 8 only** (`state.txt`):

    +0x3a = +0x6c12 = x0     +0x3e = +0x6c16 + 1 = x1 + 1
    +0x3c = +0x6c14 = y0     +0x40 = +0x6c18     = y1

so a later sibling that lays out relative to this widget (base-mode 2/3) is pushed past the
*pixels it drew*, not past its declared geometry box. `layout.py` approximates the box with
the static geometry, which collapses to zero height; that is the sole remaining cause of the
Climate mode-grid riding up over the tab (open thread 7).

The extent formula, straight from `FUN_002557C0` (`proven-from-code`):

    x0 = pen_x(+0x2a) adjusted by alignment byte(+9): 2 -> centre (x0 -= width/2),
                                                       3 -> right  (x0 -= width - 1)
    y0 = pen_y(+0x2c) - font[0x0b]          # font[0x0b] = ascent / top offset
    x1 = x0 + width - 1                      # width from FUN_002518F8 (the measurer)
    y1 = y0 + font[0x07] - 1                 # font[0x07] = cell height

**What is exact here vs. approximate.** The *height* (`font[0x07]`) and *top* (`font[0x0b]`)
are read straight from the 20-byte font descriptor — `proven-from-code`, byte-exact. The
*width* now replays `FUN_002518F8` / `FUN_002517AC` (`text_width`): each glyph header carries a
left-edge profile `[0..4]` and right-edge profile `[5..9]`, and the kerned advance from one
glyph to the next is the closest approach of the former's right edge to the latter's left edge
plus the font letter-spacing `[0x12]`. This is a faithful transcription and the per-glyph
positions come out strictly monotone, but there is **no independent pixel oracle** for width
(as there is none for the glyph bitmap `start`), so it is validated by that transcription and
by magnitude, not against measured pixels. Two caveats it does not yet cover: the per-glyph
*char-class* is a runtime widget byte (`+0x0a`) the record does not carry, so `text_width`
assumes class 2 (the proportional-kerned mode); numeric right-aligned fields (class 3, fixed
per-digit cells) would measure wider. Height, the field the vertical layout needs, is exact.

The font descriptor metric bytes (all `proven-from-code`, read out of `FUN_002557C0` /
`FUN_002518F8` / `FUN_002517AC`):

    [0x06] trailing advance after the last glyph      [0x07] cell height (rows)
    [0x0b] top offset (pen_y to glyph top)            [0x11]/[0x12] inter-glyph spacing
"""
from __future__ import annotations

from dataclasses import dataclass

from . import glyphs

# Font-descriptor byte offsets beyond the four glyphs.py already names (first/last/base/fallback).
OFF_TRAILING = 0x06
OFF_HEIGHT = 0x07
OFF_TOP = 0x0B
OFF_SPACE_A = 0x11
OFF_SPACE_B = 0x12


@dataclass(frozen=True)
class FontMetrics:
    """The line-box metrics of one font slot, read from its 20-byte descriptor."""
    slot: int
    height: int          # descriptor[0x07] — cell height in rows (the frame's y-extent)
    top: int             # descriptor[0x0b] — pen_y to glyph top (frame y0 = pen_y - top)
    trailing: int        # descriptor[0x06] — advance added after the final glyph
    spacing: int         # descriptor[0x11] + [0x12] — default inter-glyph gap


def font_metrics(data: bytes, slot: int,
                 table: "glyphs.GlyphTable | None" = None) -> FontMetrics:
    """Read the line-box metrics of `slot`'s font descriptor (`proven-from-code`).

    `table`, if given, is passed through to `glyphs.font_for_slot` to skip its table scan --
    worth passing when this is called once per placed record in a render pass (see
    `render.climate_placements_decoded`'s `_size_top`).
    """
    font = glyphs.font_for_slot(data, slot, table=table)
    o = font.offset
    return FontMetrics(
        slot=slot,
        height=data[o + OFF_HEIGHT],
        top=data[o + OFF_TOP],
        trailing=data[o + OFF_TRAILING],
        spacing=data[o + OFF_SPACE_A] + data[o + OFF_SPACE_B],
    )


@dataclass(frozen=True)
class TextExtent:
    """The rendered box of a string, in the coordinate frame of `FUN_002557C0`.

    `x0/y0/x1/y1` are relative to the pen `(pen_x, pen_y)`; add the pen to place them. `width`
    and `height` are the box size. `width_exact` is True when width came from the `FUN_002518F8`
    replay, False when it fell back to the glyph-width sum — see the module docstring.
    """
    width: int
    height: int
    top: int
    width_exact: bool


# A glyph header carries an ink profile the measurer kerns against: bytes [0..4] are the
# glyph's left edge sampled at 5 heights, [5..9] its right edge, then [0x0a]=x, [0x0b]=width,
# [0x0e] a flag byte (bit0 selects which bearing the endpoints use). `proven-from-code`
# (FUN_002517AC / FUN_002518F8, on top of glyphs.py's OFF_X/OFF_WIDTH).
class _GHdr:
    __slots__ = ("left", "right", "x", "width", "flag")

    def __init__(self, data: bytes, off: int):
        self.left = data[off:off + 5]
        self.right = data[off + 5:off + 10]
        self.x = data[off + glyphs.OFF_X]
        self.width = data[off + glyphs.OFF_WIDTH]
        self.flag = data[off + 0x0E]


def _glyph_hdr(data: bytes, slot_font: "glyphs.Font", char: int,
              table: "glyphs.GlyphTable | None" = None) -> _GHdr | None:
    """The header of the glyph `slot_font` draws `char` with (fallback if out of range).

    Returns None only if the resolved glyph is not in the table at all. A short byte-extent is
    *not* a rejection here: a real small proportional glyph (a lowercase 'i') is a couple dozen
    bytes; only the ink-bitmap decode (glyphs.py) treats a tiny extent as the shared block.

    `table`, if given, is passed through to `glyphs.glyph_extent` to skip its table scan.
    """
    index = slot_font.glyph_index(char)
    try:
        off, _ = glyphs.glyph_extent(data, index, table=table)
    except glyphs.GlyphError:
        return None
    return _GHdr(data, off)


def _advance(prev: _GHdr, nxt: _GHdr, space: int) -> int:
    """Kerned advance from glyph `prev`'s origin to `nxt`'s (FUN_002517AC, class-2 path):
    the closest horizontal approach of prev's right edge to nxt's left edge, over 5 samples."""
    reach = max(prev.right[k] - nxt.left[k] for k in range(5))
    return space + reach + 1


def text_width(data: bytes, slot: int, text: str,
              table: "glyphs.GlyphTable | None" = None) -> int | None:
    """Exact rendered width of `text` in font `slot`, replaying `FUN_002518F8` (class-2 /
    proportional-kerned path). Returns None if a glyph is missing/placeholder so the caller
    can fall back.

    `FUN_002518F8`: seed by the first glyph's left bearing (`-x`, or `-left[0]` when the flag
    bit is set), add each kerned pair advance (`_advance`), then close with the last glyph's
    right edge (`x + width`, or `left[0] + 1` under the flag). The per-glyph char-class comes
    from a runtime widget byte (`+0x0a`) the record does not carry; class 2 (kerned) is the
    proportional-text mode. Numeric right-aligned fields (class 3, per-digit fixed cells) are
    not modelled — a value string measured here is a lower bound for those.

    `table` (from `glyphs.resolve_glyph_table`), if given, skips the per-call glyph-table scan
    -- pass one when measuring many strings against the same, unmutated `data` in one pass.
    """
    if not text:
        return 0
    font = glyphs.font_for_slot(data, slot, table=table)
    space = data[font.offset + OFF_SPACE_B]
    hdrs = [_glyph_hdr(data, font, ord(ch), table=table) for ch in text]
    if any(h is None for h in hdrs):
        return None
    first = hdrs[0]
    acc = -(first.left[0] if (first.flag & 1) else first.x)
    for prev, nxt in zip(hdrs, hdrs[1:]):
        acc += _advance(prev, nxt, space)
    last = hdrs[-1]
    return (last.left[0] + acc + 1) if (last.flag & 1) else (last.x + last.width + acc)


def text_extent(data: bytes, slot: int, text: str,
                table: "glyphs.GlyphTable | None" = None) -> TextExtent:
    """The rendered extent of `text` drawn in font `slot`.

    Height and top are byte-exact from the descriptor. Width is the exact `FUN_002518F8`
    replay (`text_width`) when every glyph resolves; if one is missing it falls back to the
    approximate sum of glyph bitmap widths plus the font gap, and `width_exact` says which.

    `table`, if given, is threaded through to `text_width`/`glyphs.glyph_extent`/
    `glyphs.decode_glyph` to skip their table scans.
    """
    fm = font_metrics(data, slot, table=table)
    exact = text_width(data, slot, text, table=table)
    if exact is not None:
        return TextExtent(width=exact, height=fm.height, top=fm.top, width_exact=True)

    font = glyphs.font_for_slot(data, slot, table=table)
    width = 0
    n = 0
    for ch in text:
        index = font.glyph_index(ord(ch))
        try:
            _, extent = glyphs.glyph_extent(data, index, table=table)
            width += (fm.spacing if extent < 64
                      else glyphs.decode_glyph(data, index, table=table).width)
        except glyphs.GlyphError:
            width += fm.spacing
        n += 1
    if n:
        width += fm.spacing * (n - 1) + fm.trailing
    return TextExtent(width=width, height=fm.height, top=fm.top, width_exact=False)


def glyph_positions(data: bytes, slot: int, text: str,
                    table: "glyphs.GlyphTable | None" = None) -> list[int] | None:
    """Per-character pen x-offsets for `text` set in font `slot`, in the string's own local
    frame (position 0 = the first glyph's pen). This is the *same* `FUN_002518F8` kerned-
    advance replay `text_width` sums to a single number (`_glyph_hdr`/`_advance` above,
    unchanged) — it just keeps every intermediate value instead of only the final total, so a
    caller can paste each character's own bitmap (from `glyphs.py`) at its real advance rather
    than an evenly-spaced guess. Returns `None` under the same condition as `text_width`: any
    character's glyph is missing or resolves to the shared placeholder — the caller then falls
    back to the labelled heuristic stand-in, same as a width miss does today.

    No new position math is invented: `positions[i]` is exactly the cumulative `_advance` total
    `text_width`'s loop computes before folding in the final glyph's own bearing (this function
    only returns positions, not overall width). This returns the *horizontal* advance only; the
    per-glyph *vertical* bearing (`glyphs.Glyph.y`, header `+0x0c`) is applied by the caller when
    it composites (`render.panel_text_images` reads `Glyph.y` and the rasteriser draws each glyph
    at `pen + (dx, glyph.y)`, the blitter's `pen_y + glyph[0xc]`) — so baselines align and
    descenders drop, which a shared top-align would break. The horizontal `Glyph.x` is 0 for the
    proportional text fonts in this corpus, so it is not separately added here.

    `table` (from `glyphs.resolve_glyph_table`), if given, skips the per-character glyph-table
    scan/sort -- this is what makes drawing a whole label's worth of real bitmaps
    (`render.panel_text_images`) one scan per render pass instead of one per character. See
    `glyphs.find_glyph_table`'s docstring for why that resolve-once-and-thread-through shape
    replaced an earlier id(data)-keyed cache: the cache survived GC id-reuse but not in-place
    mutation of the same buffer, which is exactly the failure mode this project has been
    burned by before.
    """
    if not text:
        return []
    font = glyphs.font_for_slot(data, slot, table=table)
    space = data[font.offset + OFF_SPACE_B]
    hdrs = [_glyph_hdr(data, font, ord(ch), table=table) for ch in text]
    if any(h is None for h in hdrs):
        return None
    positions = [0]
    acc = 0
    for prev, nxt in zip(hdrs, hdrs[1:]):
        acc += _advance(prev, nxt, space)
        positions.append(acc)
    return positions


# The characters a mode-0 number readout can draw: digits, the decimal point, the width-overflow
# and n/a dash, and the justification pad.
NUMBER_CHARS = "0123456789.- "


def worst_fill_width(data: bytes, slot: int, field: int) -> int:
    """The widest background a number readout of `field` characters can fill in font `slot`.

    `FUN_002557C0` fills exactly the text extent `FUN_002518F8` measures (`FUN_00251C10`), and the
    formatter always draws `field` characters: justified with spaces, or `field` dashes when the
    value is wider (GUICODE Finding 65; `render.format_cell_value`). The measuring class is a
    render-state byte (`+0x0A`) that varies at runtime, so this bounds every class
    `FUN_002517AC` implements, per character: class 1's fixed `font[0x06]`, class 3's fixed
    `font[0x11] + font[0x12]`, and the kerned advance of the widest number glyph
    (`x + width + font[0x12] + 1`). v37 on the coach showed a class-1 fill (`LAYERS_FIELD`)
    that `text_width`'s kerned model placed 29 px further right."""
    font = glyphs.font_for_slot(data, slot)
    d = data[font.offset:font.offset + 20]
    kerned = 0
    for ch in NUMBER_CHARS:
        h = _glyph_hdr(data, font, ord(ch))
        if h is not None:
            kerned = max(kerned, h.x + h.width + d[OFF_SPACE_B] + 1)
    return field * max(d[OFF_TRAILING], d[OFF_SPACE_A] + d[OFF_SPACE_B], kerned)


def aligned_x0(pen_x: int, width: int, align: int) -> int:
    """Left edge of the box after the alignment byte (+9): 2 = centre, 3 = right, else left."""
    if align == 2:
        return pen_x - width // 2
    if align == 3:
        return pen_x - width + 1
    return pen_x


# ======================================================================================
# Word wrap -- a transcription of `FUN_002559A0`'s line-breaking loop (2026-09-06)
# ======================================================================================

WRAP_MAX_LINES = 30            # `if (0x1d < line_count) break` -- 29 is the last index it takes


def break_opportunity(text: str, i: int) -> int:
    """The index `FUN_002559A0`'s inner scan stops at, starting from `i`.

    Transcribed from the loop condition, which advances while ALL of:

        i < len-1  and  s[i] != '\\n'  and  (s[i] == ' ' or s[i+1] != ' ')
                                       and  (s[i] != '-' or s[i+1] == ' ')

    so it STOPS on the first index where any of them fails, i.e. at:

      * `len - 1`                          -- the last character;
      * a `'\\n'`                           -- a hard break;
      * the last non-space before a space  -- the end of a word;
      * a `'-'` NOT followed by a space    -- so `auto-start` may break after the hyphen,
                                              while `A - B` does not break at that dash.

    The returned index is the LAST character of the chunk, inclusive -- not the position after
    it, which is what makes the caller's `end` values inclusive too.
    """
    n = len(text)
    while (i < n - 1 and text[i] != "\n"
           and (text[i] == " " or text[i + 1] != " ")
           and (text[i] != "-" or text[i + 1] == " ")):
        i += 1
    return i


def wrap_lines(data: bytes, slot: int, text: str, box_w: int,
               table: "glyphs.GlyphTable | None" = None) -> list[tuple[int, int]] | None:
    """`[(start, end), ...]` inclusive character ranges, one per drawn line.

    A transcription of the wrapping half of `FUN_002559A0` (the wtype-11/13 text drawer),
    decompiled 2026-09-06 from the `guicode136` Ghidra project. `box_w` is the record's own
    `x1 - x0 + 1` -- the firmware computes exactly that (`sVar5 = (sVar5 - sVar3) + 1`) before
    the loop, and compares it with `<`, so a line FITS when its measured width is `<= box_w`.

    The algorithm is greedy with one memory: it walks break opportunities, measuring the line
    from its start to each one, and when a candidate overflows it falls back to the LAST
    opportunity that fitted (`ends[k] != -1`) and starts the next line after it, skipping the
    spaces between. If no opportunity has fitted yet -- a single chunk wider than the box -- the
    line takes that whole chunk anyway and **overflows**, which the drawer's clip rectangle then
    cuts off. That is not a defect in this transcription: it is why a long unbreakable token
    appears truncated on the panel rather than broken mid-word.

    Returns `None` when the text cannot be measured (a missing glyph), matching `text_width`'s
    contract, so a caller falls back to its own stand-in rather than inventing a line break.

    NOT transcribed, because it is unreachable: both `if (line_count < 2)` arms inside the loop.
    `line_count` starts at 1 and each arm runs only after an increment, so the test is never
    true. Recorded rather than silently dropped -- a future build with a different initial count
    would need them.
    """
    n = len(text)
    if n == 0:
        return []

    def width(a: int, b: int) -> int | None:
        if a > b:
            return 0
        return text_width(data, slot, text[a:b + 1], table=table)

    starts = [0] * (WRAP_MAX_LINES + 3)          # local_86c
    ends = [-1] * (WRAP_MAX_LINES + 3)           # local_8ae; line j spans starts[j]..ends[j+1]
    k = 1                                        # iVar21
    count = 1                                    # iVar20
    i = 0
    while i < n:
        cur = break_opportunity(text, i)
        w = width(starts[k - 1], cur)
        if w is None:
            return None
        hard = False
        if w > box_w:
            if ends[k] == -1:
                # nothing on this line has fitted yet: keep the over-wide chunk and overflow
                hard = text[cur] == "\n"
                ends[k] = cur - 1 if hard else cur
                starts[k] = cur + 1
                ends[k + 1] = -1
            else:
                starts[k] = ends[k] + 1
                while starts[k] < cur and text[starts[k]] == " ":
                    starts[k] += 1                # the spaces broken at are not drawn
                ends[k + 1] = cur
            if count > WRAP_MAX_LINES - 1:
                break
            count += 1
            k = count
        else:
            ends[k] = cur
        if text[cur] == "\n" and not hard:
            ends[k] = cur - 1
            starts[k] = cur + 1
            ends[k + 1] = -1
            if count > WRAP_MAX_LINES - 1:
                break
            count += 1
            k = count
        i = cur + 1
    return [(starts[j], ends[j + 1]) for j in range(count)]


def wrap_text(data: bytes, slot: int, text: str, box_w: int,
              table: "glyphs.GlyphTable | None" = None) -> list[str] | None:
    """`wrap_lines` as the drawn strings. A range with `start > end` is an empty line, which the
    drawer skips (`if (iVar19 <= sVar4)`) but which still advances the pen by one line height."""
    spans = wrap_lines(data, slot, text, box_w, table=table)
    if spans is None:
        return None
    return ["" if a > b else text[a:b + 1] for a, b in spans]
