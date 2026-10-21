"""Text metrics: the content-render extents FUN_002557C0 writes and FUN_00257BFC reads back.

Height and top are byte-exact from the font descriptor; these tests pin that against the
raw bytes and check width is well-formed. Nothing here is fitted to a render or the photo —
the extents are validated against the font descriptor and the value string, per the standing
rule (`docs/GUICODE.md` Finding 14).
"""
from __future__ import annotations

import struct

import pytest

from fireflymcf import glyphs, metrics

BUILDS = {
    "11.23": "firefly-11.23/mcf.bin",
    "13.6": "firefly-13.6/mcf.bin",
    "14.7": "firefly-14.7 (Terrain)/mcf.bin",
}


def _load(build):
    with open(BUILDS[build], "rb") as handle:
        return handle.read()


@pytest.mark.parametrize("build", sorted(BUILDS))
def test_font_metrics_match_descriptor_bytes(build):
    """height=descriptor[7], top=descriptor[0xb] — read straight from the 20-byte descriptor."""
    data = _load(build)
    for font in glyphs.fonts(data):
        fm = metrics.font_metrics(data, font.slot)
        assert fm.height == data[font.offset + 0x07]
        assert fm.top == data[font.offset + 0x0B]
        assert fm.trailing == data[font.offset + 0x06]
        assert fm.spacing == data[font.offset + 0x11] + data[font.offset + 0x12]


def test_font_metrics_form_a_sane_ladder():
    """Descriptor heights ascend with slot (small proportional fonts up to big digit fonts)."""
    data = _load("13.6")
    heights = [metrics.font_metrics(data, f.slot).height for f in glyphs.fonts(data)[:10]]
    assert heights == sorted(heights)          # slots 0..9 are the proportional-size ladder
    assert 0 < heights[0] < heights[-1]
    # top offset is always inside the cell (ascent can't exceed the box height)
    for f in glyphs.fonts(data):
        fm = metrics.font_metrics(data, f.slot)
        assert 0 < fm.top <= fm.height


def test_text_extent_height_is_exact_and_width_grows_with_text():
    """A string's box height is the font cell height; width is monotone in glyph count."""
    data = _load("13.6")
    slot = 3                                   # a mid-size proportional font (height 17)
    one = metrics.text_extent(data, slot, "C")
    many = metrics.text_extent(data, slot, "CLIMATE")
    assert one.height == many.height == metrics.font_metrics(data, slot).height
    assert many.width > one.width              # more glyphs -> wider box
    assert many.width_exact                     # every glyph resolved -> FUN_002518F8 replay
    assert many.width < 800                     # a label fits on the 800px panel


def test_text_width_is_the_faithful_replay():
    """text_width replays FUN_002518F8: a single glyph is ~its own cell, and a longer string
    scales with a bigger font. Magnitudes, not a pixel oracle (none exists)."""
    data = _load("13.6")
    # "CLIMATE" is wider in the 31px tab font (16) than the 17px body font (3).
    assert metrics.text_width(data, 16, "CLIMATE") > metrics.text_width(data, 3, "CLIMATE")
    # a lone glyph's width is on the order of the font's default advance, not zero or huge
    w = metrics.text_width(data, 3, "C")
    assert 4 < w < 3 * metrics.font_metrics(data, 3).height
    # the empty string measures zero; a run of digits in the big setpoint font stays on-screen
    assert metrics.text_width(data, 9, "") == 0
    assert 0 < metrics.text_width(data, 9, "888") < 800


def test_text_width_positions_are_monotone_across_builds():
    """The per-glyph pen never moves backwards — the check a wrong advance would fail — and
    the same string decodes to the same width from each build's own glyph tables."""
    for build in sorted(BUILDS):
        data = _load(build)
        font = glyphs.font_for_slot(data, 3)
        hdrs = [metrics._glyph_hdr(data, font, ord(c)) for c in "CLIMATE"]
        space = data[font.offset + metrics.OFF_SPACE_B]
        pos, acc = [], -(hdrs[0].x)
        pos.append(acc)
        for prev, nxt in zip(hdrs, hdrs[1:]):
            acc += metrics._advance(prev, nxt, space)
            pos.append(acc)
        assert pos == sorted(pos)              # strictly non-decreasing: glyphs march rightward
        assert metrics.text_width(data, 3, "CLIMATE") > pos[-1]


def test_aligned_x0_matches_the_primitive():
    """Alignment byte (+9): 2 centres, 3 right-aligns, else left (FUN_002557C0)."""
    assert metrics.aligned_x0(100, 40, 0) == 100        # left: pen unchanged
    assert metrics.aligned_x0(100, 40, 2) == 80          # centre: -width/2
    assert metrics.aligned_x0(100, 40, 3) == 61          # right: -(width-1)


# ---------------------------------------------------------------- owner-reported render bugs
# Both found by the owner comparing rendered screens against the panel, 2026-08-19.


def test_thin_glyphs_are_drawn_not_discarded(mcf_136):
    """A 5x1 hyphen is real type, not noise.

    `render._glyph_and_image` used to drop any glyph under 2px in either dimension as "too
    small to matter". That discarded 30 inked glyphs across the corpus -- every small-font
    hyphen and underscore, plus '!', '.', ':', 'i', 'l' in the smallest font -- which is why
    the `G12 OUTPUTS` rows rendered as "03  Awning EXT" instead of "03 - Awning EXT".
    """
    from fireflymcf import render

    for slot in (0, 11, 14):
        img = render.glyph_image(mcf_136, slot, "-", (255, 255, 255))
        assert img is not None, f"slot {slot}: the hyphen was discarded"
        assert img.width >= 2 and img.height >= 1

    imgs = render.panel_text_images(mcf_136, 11, "03 - Awning EXT", (255, 255, 255))
    assert imgs is not None
    # 15 characters, 3 of them spaces (which genuinely declare height 0) -> 12 drawn
    assert len(imgs) == 12, f"{len(imgs)} glyph images; the hyphen is missing again"


def test_a_glyph_with_no_ink_is_still_skipped(mcf_136):
    """The replacement guard must still reject a blank, or every space would draw a box."""
    from fireflymcf import render
    assert render.glyph_image(mcf_136, 11, " ", (255, 255, 255)) is None


@pytest.mark.parametrize("slot,text", [
    (3, "Desired DC Voltage:"),
    (3, "Max Charging Current (%):"),
    (11, "03 - Awning EXT"),
    (14, "Charging Algorithm:"),
])
def test_drawn_extent_equals_measured_width(mcf_136, slot, text):
    """The per-glyph horizontal bearing `Glyph.x` must be applied when compositing.

    `metrics.text_width` seeds with `-first.x` and closes with `last.x + last.width`, i.e. it
    measures INK. `render.panel_text_images` returned raw pen positions and applied only the
    vertical bearing, on the claim that "Glyph.x is 0 for the proportional text fonts in this
    corpus". It is not: 204 of 1989 printable glyphs carry one, concentrated on '.' ',' ':'
    '1' 'i' 'l' '-' '(' -- the narrow characters where a bearing does the most work. The
    mismatch showed as bad kerning on screen 24 ("Vo ltage").

    Drawn ink extent and measured width must therefore agree exactly.
    """
    from fireflymcf import metrics, render

    imgs = render.panel_text_images(mcf_136, slot, text, (255, 255, 255))
    assert imgs, f"no glyphs drawn for {text!r}"
    left = min(dx for dx, _dy, _im in imgs)
    right = max(dx + im.width for dx, _dy, im in imgs)
    assert left == 0, f"run does not start at its own ink origin (starts {left})"
    assert right - left == metrics.text_width(mcf_136, slot, text), (
        f"drawn {right - left}px vs measured {metrics.text_width(mcf_136, slot, text)}px")


def test_horizontal_bearing_actually_moves_a_glyph(mcf_136):
    """Guard against the fix being a no-op: a bearing-carrying glyph must shift."""
    from fireflymcf import glyphs, render

    font = glyphs.font_for_slot(mcf_136, 3)
    bearing = {c: glyphs.decode_glyph(mcf_136, font.glyph_index(ord(c))).x for c in "ol"}
    assert bearing["l"] > 0, "slot 3 'l' no longer carries a bearing; pick another probe"

    imgs = render.panel_text_images(mcf_136, 3, "ol", (255, 255, 255))
    positions = metrics_positions = None
    from fireflymcf import metrics
    metrics_positions = metrics.glyph_positions(mcf_136, 3, "ol")
    assert imgs[1][0] == metrics_positions[1] + bearing["l"] - bearing["o"], (
        "the 'l' bitmap is not offset by its own horizontal bearing")


# --- where a wtype-0 single character lands relative to its pen (GUICODE Finding 84) ---------

def _ink_top_bottom(data, slot, char, pen_y):
    dx, dy = metrics.wt0_ink_offset(data, slot, char)
    g = glyphs.decode_glyph(data, glyphs.font_for_slot(data, slot).glyph_index(ord(char)))
    return pen_y + dy, pen_y + dy + g.height - 1


@pytest.mark.parametrize("obj", [443, 440])
def test_a_wt0_unit_draws_centred_in_its_battery_capsule(mcf_136, obj):
    """`HOME.jpg` shows `12.4V` vertically centred in the green battery body. The body is the
    object's wtype-33 record 2 (y 341..391); the `V` is its wtype-0 record 6, pen y 374. By the
    firmware rule (cell top `pen_y - font[0x0b]`, `0x255810`/`0x255812`, plus the glyph's
    `+0x0c` bearing in `FUN_002510C0`) the `V` inks rows 359..374, centred in the body. The rule
    this replaced (ink at `pen + glyph.y`) inks rows 379..394, through the body's bottom edge.
    This is the test that tells the two rules apart: a unit beside its value cannot, because
    the value is drawn by the same function and moves with it."""
    from fireflymcf import pagewalk
    recs = {r.index: r for r in pagewalk.walk_records(mcf_136, 4) if r.obj == obj}
    body, unit = recs[2], recs[6]
    assert body.wtype == 33 and unit.wtype == 0 and unit.label == "V"
    top, bot = _ink_top_bottom(mcf_136, unit.font, "V", unit.box[1])
    assert body.box[1] < top and bot < body.box[3]
    assert abs((top + bot) - (body.box[1] + body.box[3])) <= 2


@pytest.mark.parametrize("unit,slot,pen_y", [
    ("V", 15, 374),            # HOME House/Chassis battery: obj 443/440, pen (578|751, 374)
    ("%", 13, 178),            # HOME tank percent: obj 721/723/719/613, pen (…, 178)
])
def test_a_wt0_unit_sits_on_its_values_baseline(mcf_136, unit, slot, pen_y):
    """`HOME.jpg` shows `12.4V` and the tank `%` set on the digits' own line, at the digits'
    height. Under the firmware rule (`FUN_002557C0`: cell top `pen_y - font[0x0b]` at
    `0x255810`/`0x255812`, the blitter adds `glyph[0x0c]` at `0x2510c0`) the unit and an `8` of
    the same font drawn at the same pen share their top and bottom rows exactly. That relation is
    inside one drawer, so it checks the bearings, not the `font[0x0b]` term; the term is what puts
    the shared baseline on the pen row: `font[0x0b]` rows below the cell top, where the digits'
    last row sits. A rule that drew from the pen down (`dy` 0, or `pen + glyph.y`) puts the
    digits' bottom below the pen."""
    assert _ink_top_bottom(mcf_136, slot, unit, pen_y) == \
        _ink_top_bottom(mcf_136, slot, "8", pen_y)
    assert _ink_top_bottom(mcf_136, slot, "8", pen_y)[1] == pen_y


def test_a_wt0_percent_sits_just_under_its_tank_gauge(mcf_136):
    """`HOME.jpg` shows each tank's `0%` directly under its gauge, the gap far smaller than the
    text. The gauge is obj 721's wtype-32 record 0 (y 76..161), drawn by another function, so
    this relation tells the rules apart: the firmware rule inks the `%` (record 4, pen y 178)
    rows 166..178, 5 px under the gauge; ink at the pen (`dy` 0) or at `pen + glyph.y` starts
    17 or 21 px under it, further than the 13-px glyph is tall."""
    from fireflymcf import pagewalk
    recs = {r.index: r for r in pagewalk.walk_records(mcf_136, 4) if r.obj == 721}
    gauge, unit = recs[0], recs[4]
    assert gauge.wtype == 32 and unit.wtype == 0 and unit.label == "%"
    top, bot = _ink_top_bottom(mcf_136, unit.font, "%", unit.box[1])
    assert gauge.box[3] < top and top - gauge.box[3] < bot - top + 1


@pytest.mark.parametrize("slot,pen_y", [(17, 409), (7, 142), (9, 203)])
def test_a_wt0_degree_is_a_superscript(mcf_136, slot, pen_y):
    """HOME's `91°` (font 17) and CLIMATE's two temperatures (fonts 7 and 9): the photos show
    the degree sign at the top of the digits. By the firmware rule its ink starts within 3 px of
    the digit top and ends in the upper half of the digit. Both glyphs share one pen, so that
    relation checks glyph data; the `font[0x0b]` term is checked by the baseline: the digits'
    last row is the pen row, and the whole degree sign is above it."""
    deg_top, deg_bot = _ink_top_bottom(mcf_136, slot, "°", pen_y)
    dig_top, dig_bot = _ink_top_bottom(mcf_136, slot, "8", pen_y)
    assert abs(deg_top - dig_top) <= 3
    assert deg_bot < (dig_top + dig_bot) // 2
    assert dig_bot == pen_y and deg_bot < pen_y


def test_wt0_ink_offset_horizontal_follows_the_measuring_class(mcf_136):
    """`FUN_002518F8` seeds the first pen offset with `-glyph[0x0a]` only for a character
    `FUN_00251AA0` flagged (class 2: all; class 3: all but digits, space and `+ - * / =`);
    the blitter then adds `glyph[0x0a]` back. So a flagged character's ink starts at the pen and
    an unflagged one's at `pen + glyph.x`. 13.6's font-7 `:` has x bearing 4."""
    assert metrics.wt0_ink_offset(mcf_136, 7, ":")[0] == 0                   # class 2 (default)
    assert metrics.wt0_ink_offset(mcf_136, 7, ":", char_class=3)[0] == 0     # ':' is flagged
    assert metrics.wt0_ink_offset(mcf_136, 7, ":", char_class=1)[0] == 4     # never flagged
    # class 3 leaves every digit unflagged, 9 included (`cmp.ne r4,#0x9` / `bls` at 0x251B4C)
    assert not any(metrics.class_flags_char(3, c) for c in "0123456789 +-*/=")
    assert metrics.class_flags_char(3, "V") and metrics.class_flags_char(3, "\x03")
