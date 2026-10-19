"""The renderer's data-driven layer, anchored to the file.

Only layer 1 (`decode_widget`) makes falsifiable claims about the bytes, so that is what
is tested here; the compositor's anchors are calibrated-to-photo and the rasteriser's
output is judged by eye. The anchor is the Starlink/Bath-Fan button object at 0x97200 in
13.6, whose caption position (80, 75) is the value probe 1 moved on the coach.
"""
from __future__ import annotations

import pytest
from PIL import Image

from fireflymcf import records, render


MCF_136 = "firefly-13.6/mcf.bin"
STARLINK_BUTTON = 0x97200            # the Bath-Fan/Starlink widget object in stock 13.6


def load() -> bytes:
    with open(MCF_136, "rb") as handle:
        return handle.read()


def test_decode_widget_reads_the_button_from_the_file() -> None:
    prims = render.decode_widget(load(), STARLINK_BUTTON)
    kinds = [p.kind for p in prims]
    # the four records of the button: rounded frame, icon glyph, caption, touch region
    assert kinds.count("rrect") == 1
    assert kinds.count("touch") == 1
    texts = [p for p in prims if p.kind == "text"]
    assert [p.text for p in texts] == ["E", "Bath Fan"]      # icon glyph, then caption


def test_caption_position_matches_probe1() -> None:
    prims = render.decode_widget(load(), STARLINK_BUTTON)
    caption = next(p for p in prims if p.text == "Bath Fan")
    assert (caption.x, caption.y) == (80, 75)                 # probe-1 ground truth
    assert caption.src == "file@0x9722b"                     # provenance, not synthetic


def test_caption_text_is_data_driven_not_hardcoded() -> None:
    """The compositor's Starlink caption must come from the file, so a rename shows up."""
    placements = render.climate_placements(load())
    star = next(pl for pl in placements if pl.name == "starlink")
    from_file = [p for p in star.prims if p.src.startswith("file@")]
    assert any(p.text == "Bath Fan" for p in from_file), (
        "stock 13.6 caption should be read from the file, not hardcoded")


def test_rasterize_produces_a_panel_sized_image() -> None:
    img = render.rasterize(render.climate_placements(load()))
    assert isinstance(img, Image.Image)
    assert img.size == (render.PANEL_W, render.PANEL_H) == (800, 480)


def test_scan_labels_reads_the_grid_from_the_file() -> None:
    """The selector-pattern scanner reads labels the opcode walker stalls before."""
    texts = {lbl.text for lbl in render.scan_labels(load(), 0x99700, 0x999C0)}
    assert {"Cool", "High", "Low", "Auto"} <= texts


def test_label_text_matches_the_caption_probe() -> None:
    got = render.label_text(load(), 0x97237)          # Starlink caption selector in 13.6
    assert got is not None and got[0] == "Bath Fan"


def test_climate_text_is_sourced_from_the_file() -> None:
    placements = render.climate_placements(load())
    by_name = {pl.name: pl for pl in placements}
    file_texts = {p.text for pl in placements for p in pl.prims
                  if p.src.startswith("file@")}
    # the tab, the four grid labels, and the Starlink caption all come from the bytes
    assert {"CLIMATE", "Low", "High", "Auto"} <= file_texts
    assert any("Cool" in p.text for p in by_name["mode-grid"].prims)


def test_starlink_icon_is_a_real_glyph_from_the_file() -> None:
    """The button icon is decoded from the icon font, and its char is read from the file."""
    from PIL import Image
    data = load()
    star = next(pl for pl in render.climate_placements(data) if pl.name == "starlink")
    glyph = next(p for p in star.prims if p.kind == "glyph")
    assert glyph.src == "file@0x9721f"
    assert glyph.text == "E"                         # the icon char, read from the bytes
    assert isinstance(glyph.image, Image.Image) and glyph.image.height == 40


def test_cool_snowflake_is_a_data_driven_glyph() -> None:
    """The mode icon is a slot-23 glyph read from the Cool cell's own 0x0c selector."""
    grid = next(pl for pl in render.climate_placements(load()) if pl.name == "mode-grid")
    snow = next(p for p in grid.prims if p.kind == "glyph" and p.src.startswith("file@"))
    assert snow.src == "file@0x99848" and snow.text == "3" and snow.glyph_slot == 23


def test_glyph_image_returns_none_for_undecodable_glyph() -> None:
    """`GUICODE.md` Finding 22 (the RLE rewrite) closed the gap this test used to document:
    slot 7 'o' used to decode to a fraction of its declared height under the old flat reader
    and `glyph_image` returned None for it. It must now render the panel's own bitmap --
    byte-identical to `glyphs.decode_glyph`'s own rows scaled 0..15 -> 0..255, not a stand-in.
    Non-vacuous: stubbing `decode_glyph` to raise (the shape of the old failure) makes
    `glyph_image` return None again, which the first assert below would catch."""
    from fireflymcf import glyphs
    import numpy as np

    data = load()
    font = glyphs.font_for_slot(data, 7)
    g = glyphs.decode_glyph(data, font.glyph_index(ord("o")))
    assert g.height == data[g.offset + glyphs.OFF_HEIGHT]        # decodes to its full height now

    img = render.glyph_image(data, 7, "o", render.COL["white"])
    assert img is not None
    assert img.size == (g.width, g.height)
    alpha = np.asarray(img)[..., 3]
    assert np.array_equal(alpha, np.array(g.rows, dtype=np.uint8) * 17)

    # `glyph_image` must still fail soft for a genuinely undecodable glyph -- that residual
    # is real, just not the old "thin glyph decodes short" one: a space's record declares
    # height 0 (no bitmap for whitespace, `decode_glyph` raises `GlyphError` by design), and
    # `glyph_image` must swallow that rather than propagate it.
    with pytest.raises(glyphs.GlyphError):
        glyphs.decode_glyph(data, font.glyph_index(ord(" ")))
    assert render.glyph_image(data, 7, " ", render.COL["white"]) is None


def test_decoded_compositor_places_the_climate_page_from_the_bytes() -> None:
    """Approach A: climate_placements_decoded builds the page from layout.py, no calibration.
    The tab and the four mode labels are present as text, all sourced from the file, and land
    in a sane on-screen arrangement (mode row below the tab)."""
    placements = render.climate_placements_decoded(load())
    assert len(placements) == 1 and placements[0].name == "decoded"
    prims = placements[0].prims
    texts = {p.text.strip(): p for p in prims if p.kind == "text"}   # labels keep leading spaces
    for label in ("CLIMATE", "Cool", "High", "Low", "Auto"):
        assert label in texts, f"{label} not placed by the decoded compositor"
        assert texts[label].src.startswith("file@")     # decoded from the bytes, not synthetic
    # the mode column steps below the CLIMATE tab (the vertical fix, end to end)
    assert texts["High"].y < texts["Low"].y < texts["Auto"].y
    assert texts["Auto"].y > texts["CLIMATE"].y + 100


def test_decoded_compositor_uses_measured_width_for_alignment() -> None:
    """A centred/right label's left edge is shifted left of its pen by the measured text width
    (metrics.aligned_x0), not left at the pen — i.e. the width feeds the layout."""
    from fireflymcf import layout, metrics
    data = load()
    placed = layout.Screen(data).walk(layout.climate_root(data))
    centred = [p for p in placed
               if p.label and p.align in (2, 3) and p.font is not None and p.label.strip()]
    assert centred, "expected at least one centre/right-aligned label in the Climate page"
    p = centred[0]
    w = metrics.text_width(data, p.font, p.label)
    assert w and metrics.aligned_x0(p.x0, w, p.align) < p.x0


def test_decoded_render_is_panel_sized() -> None:
    img = render.rasterize(render.climate_placements_decoded(load()))
    assert img.size == (render.PANEL_W, render.PANEL_H)


def test_multiline_labels_render_as_separate_lines() -> None:
    """A `\\n` label (which is not str.isprintable()) must be split into stacked lines, not
    dropped. The LIGHTS page (floorplan WD) has 'Main\\nCeiling' etc.; both lines must appear as
    text, at increasing y."""
    from fireflymcf import layout
    data = load()
    pl = render.climate_placements_decoded(
        data, root=6, match_table_off=layout.MATCH_TABLE_136, slot_values={5: 8})
    texts = [p for p in pl[0].prims if p.kind == "text"]
    words = {p.text for p in texts}
    assert {"Main", "Ceiling", "Bed", "Lights"} <= words     # split, not dropped
    main = next(p for p in texts if p.text == "Main")
    ceiling = next(p for p in texts if p.text == "Ceiling")
    assert ceiling.y > main.y                                # second line below the first


def test_widget_labels_centre_in_their_box_but_indented_headers_stay_left() -> None:
    """A label centres in a box wider than its text (the 'Awning' middle label between the EXT
    and RET buttons), while a leading-whitespace header ('   HOME') stays left-indented."""
    from fireflymcf import layout
    data = load()
    pl = render.climate_placements_decoded(
        data, root=4, match_table_off=layout.MATCH_TABLE_136, slot_values={5: 8})
    texts = {p.text.strip(): p for p in pl[0].prims if p.kind == "text"}
    home, awning = texts["HOME"], texts["Awning"]
    ext = next(p for p in pl[0].prims if p.kind == "text" and p.text.strip() == "EXT")
    ret = next(p for p in pl[0].prims if p.kind == "text" and p.text.strip() == "RET")
    assert home.x < 40                                     # indented header hugs the left
    assert ext.x < awning.x < ret.x                       # EXT | Awning (centred) | RET


def test_glyphs_render_at_native_size_not_the_font_cell() -> None:
    """Icons blit at their native bitmap size, not scaled to the font cell — so the setpoint
    chevrons (native 55x28 in a 72px cell) don't overlap. Their glyph images are ~28px tall and
    their vertical spans are disjoint."""
    from fireflymcf import glyphs
    data = load()
    pl = render.climate_placements_decoded(data, root=7)
    chev = [p for p in pl[0].prims if p.kind == "glyph" and p.text in ("\x01", "\x02")]
    assert len(chev) == 2
    for p in chev:
        assert p.image.height < 40                          # native ~28, not the 72px cell
    a, b = sorted(chev, key=lambda p: p.y)
    assert a.y + a.image.height <= b.y + 4                  # up chevron above down chevron, no blob


def test_tank_gauge_bars_render() -> None:
    """The tank gauges are type-32 bar widgets; they must render as boxes (previously dropped —
    only types 4/33 were drawn). HOME (floorplan WD) has the FRESH/GREY/GREY2 gauge bars, tall
    and narrow in the top-right quadrant."""
    from fireflymcf import layout
    data = load()
    pl = render.climate_placements_decoded(
        data, root=4, match_table_off=layout.MATCH_TABLE_136, slot_values={5: 8})
    gauges = [p for p in pl[0].prims if p.kind in ("rect", "rrect")
              and p.x > 460 and p.y < 90 and p.h > p.w and p.h > 60]
    assert len(gauges) >= 3                              # the three tank gauge bars


def test_gauge_well_body_is_drawn_at_its_geometry_with_a_byte_derived_outline() -> None:
    """The tank well draws its decoded BODY (fillB) at EXACTLY the well record's own decoded box,
    plus a BYTE-DERIVED white outline (fillA = block-A border, mode 2 = white, GUICODE Finding 28) --
    not a synthetic border. Anchor: the FRESH-tank well (obj 721, 0x910d5 in 13.6). Non-vacuous:
    drawing the body at a fixed/approximate box fails the geometry asserts; a heuristic or absent
    outline fails the outline asserts."""
    from fireflymcf import cli, layout
    data = load()
    root = cli._resolve_page_root(data, "home")
    scr = layout.Screen(data, match_table_off=layout.MATCH_TABLE_136)
    placed = scr.walk(root, slot_values={5: 8})
    well = next(p for p in placed if p.record_offset == 0x910d5)
    assert well.wtype == 0x20                                # the FRESH tank well itself

    prims = render.screen_placements(data, root, match_table_off=layout.MATCH_TABLE_136,
                                     slot_values={5: 8})[0].prims
    well_prims = [p for p in prims if p.src == "file@0x910d5"]
    assert well_prims, "well body should draw"
    body = next(p for p in well_prims if not p.outline)
    assert body.color_src == "decoded"
    assert (body.x, body.y, body.w, body.h) == (
        well.x0, well.y0, well.x1 - well.x0, well.y1 - well.y0)
    outline = next(p for p in well_prims if p.outline)      # byte-derived white border
    assert outline.color == (255, 255, 255) and outline.color_src == "decoded"


def test_gauge_level_fill_uses_the_adjacent_records_mode3_colour() -> None:
    """The small coloured strip inside a tank well is a *separate* record (wtype 0x21,
    immediately after the well in `layout.Screen.walk`'s emission order) whose mode-3 field
    is a decoded inline BGR colour (GUICODE Finding 18). Anchor: the FRESH-tank level-fill
    record at 0x910e5, storing BGR 42909 (0x00A79D) -> RGB(0, 167, 157) -- the teal in the
    photo. The fill HEIGHT tracks the runtime `tank_level` (rendered here at 1.0 = full, the
    record's own 100% extent); the height's level-tracking is tested separately. Non-vacuous: a
    stub returning the heuristic cell colour, or dropping `color_src='decoded'`, fails the
    exact-colour assert; a stub returning a zero-size rect fails the width/height asserts."""
    from fireflymcf import cli, layout
    data = load()
    root = cli._resolve_page_root(data, "home")
    scr = layout.Screen(data, match_table_off=layout.MATCH_TABLE_136)
    placed = scr.walk(root, slot_values={5: 8})
    level = next(p for p in placed if p.record_offset == 0x910e5)
    assert level.wtype == 0x21
    stored = next(val for _, mode, val in level.colors if mode == 3)
    assert stored == 42909                                    # the stored BGR value itself

    prims = render.screen_placements(data, root, match_table_off=layout.MATCH_TABLE_136,
                                     slot_values={5: 8}, tank_level=1.0)[0].prims
    fill = next(p for p in prims if p.src == "file@0x910e5")
    # the fill rounds its BOTTOM corners at its own coord4 radius to follow the well (rrect,
    # corners=(F,F,T,T)); the liquid surface (top) stays flat.
    assert fill.kind == "rrect" and fill.color_src == "decoded"
    assert fill.corners == (False, False, True, True) and fill.radius == level.coord4
    assert fill.color == (0, 0xA7, 0x9D)                      # BGR 0x00A79D -> RGB(0,167,157)
    assert fill.w == abs(level.x1 - level.x0) > 0             # sign-normalised, not invented
    assert fill.h == abs(level.y1 - level.y0) > 0             # at level 1.0, the full extent


def test_gauge_well_splice_from_another_object_is_not_treated_as_its_fill() -> None:
    """Review fix: the real pairing invariant is 'same OBJECT', not flat-list adjacency in
    `placed`. A container (wtype 5/6/0x2e) splices a recursed child object's records inline,
    so the record right after a well in `placed` can belong to a different object entirely --
    real anchor, this corpus: object 368's well (`0x95551`) is immediately followed, when
    walked from 368, by object 640's own record (`0x91f6d`, wtype 0xb) via a wtype-6
    container at `0x95569` (`child_index=640`). Harmless here only because 0xb != 0x21; this
    test pins that the well still renders correctly (from its own geometry) across the splice,
    and that nothing is invented from the unrelated object."""
    from fireflymcf import layout
    data = load()
    scr = layout.Screen(data)
    placed = scr.walk(368)
    well = next(p for p in placed if p.record_offset == 0x95551)
    assert well.wtype == 0x20
    spliced = next(p for p in placed if p.record_offset == 0x91f6d)
    assert spliced.obj_index != well.obj_index          # the real, confirmed counterexample

    prims = render.screen_placements(data, 368)[0].prims
    # the well's own prims (its body, and its byte-derived border if fillA resolves) all sit at its
    # own decoded box -- nothing displaced by the spliced-in unrelated record.
    for p in prims:
        if p.src == "file@0x95551":
            assert (p.x, p.y) == (well.x0, well.y0)
    # the spliced-in record is never consumed as the well's level fill: any prim it produces sits
    # within its OWN decoded box (drawn as itself -- a text label centres vertically in its box,
    # so only the horizontal extent is pinned here), not at the well's position.
    for p in prims:
        if p.src == "file@0x91f6d":
            assert spliced.x0 <= p.x <= spliced.x1


def test_gauge_fill_pairing_is_gated_on_same_object_not_flat_adjacency(monkeypatch) -> None:
    """The dangerous case the real corpus doesn't happen to exercise (previous test): a
    same-wtype-0x21, DIFFERENT-object record that DOES carry a mode-3 colour, placed right
    after a well in `placed`. Pins both review findings with a controlled pair (real file,
    synthetic `Placed`s -- same pattern `test_text_uses_the_records_own_mode3_inline_fill`
    already uses to isolate `layout.Screen.walk`'s output from real geometry-decode noise):
    (1) it must NOT be drawn as this well's decoded level fill (that would be finding 1's
    bug -- a wrong colour drawn from an unrelated object); (2) it must NOT be dropped either
    -- it still reaches the generic wtype-0x21 rrect dispatch (finding 2's bug was consuming
    it unconditionally, vanishing an unrelated button)."""
    from fireflymcf import layout
    data = load()
    well = layout.Placed(wtype=0x20, label=None, x0=100, y0=50, x1=140, y1=130,
                         depth=0, obj_index=1, record_offset=0x1000,
                         font=None, align=0, colors=[("fillA", 2, None)])
    other = layout.Placed(wtype=0x21, label=None, x0=105, y0=60, x1=135, y1=120,
                          depth=0, obj_index=2, record_offset=0x2000,   # a DIFFERENT object
                          font=None, align=0, colors=[("fillA", 3, 0x00A79D)])
    monkeypatch.setattr(layout.Screen, "walk", lambda self, *a, **k: [well, other])
    prims = render.screen_placements(data, root=0)[0].prims

    # the well (fillA mode-2 = white border, no fillB body) paints ONLY its byte-derived white
    # outline (GUICODE Finding 28) -- no interior fill.
    well_prims = [p for p in prims if p.src == "file@0x1000"]
    assert well_prims and all(p.outline and p.color == (255, 255, 255) for p in well_prims)
    # the DIFFERENT-object wtype-0x21 is NOT consumed as the well's level fill (finding 1) and
    # is NOT dropped (finding 2): it reaches the generic dispatch and draws its own mode-3 colour
    # at its OWN box. Non-vacuous: if the obj_index gate regressed and it were consumed as the
    # well's level fill, the level path (tank_level default 0 -> height 0) would draw nothing.
    other_prim = next(p for p in prims if p.src == "file@0x2000")
    assert other_prim.kind == "rrect" and other_prim.color_src == "decoded"
    assert other_prim.color == (0, 0xA7, 0x9D)               # its own #00A79D, not the well's
    assert (other_prim.x, other_prim.y) == (105, 60)          # its own box, not consumed


def test_gauge_same_object_non_mode3_sibling_is_not_dropped(monkeypatch) -> None:
    """Finding 2 in isolation: even for a record that DOES belong to the same object as the
    well (so the `obj_index` guard from the previous test alone would let it through), it
    must only be consumed once it is actually drawn as the fill. A same-object wtype-0x21
    record with no mode-3 colour and an UNRESOLVED mode-7 index (neither `_inline_fill` nor
    the chrome palette back a fill -- not this well's fill, just some other same-object
    button/frame) must still reach the generic rrect dispatch, not vanish because
    `consumed.add` fired unconditionally on the object/wtype match alone.

    The mode-7 index is chosen to be verifiably absent from the real chrome palette. The sibling
    also carries a DECODED fillB (mode-3), so it has something to draw once it reaches the generic
    dispatch -- making "not dropped" observable under byte-only (an unresolved-only record would
    paint nothing either way, so it could not distinguish 'not consumed' from 'byte-only skip')."""
    from fireflymcf import layout, theme
    data = load()
    bogus_index = max(theme.mode7_palette(data), default=-1) + 1000
    well = layout.Placed(wtype=0x20, label=None, x0=100, y0=50, x1=140, y1=130,
                         depth=0, obj_index=1, record_offset=0x1000,
                         font=None, align=0, colors=[("fillA", 2, None)])
    sibling = layout.Placed(wtype=0x21, label=None, x0=105, y0=60, x1=135, y1=120,
                            depth=0, obj_index=1, record_offset=0x2001,  # SAME object as well
                            font=None, align=0,
                            # fillA unresolved mode-7 (so NOT the well's level fill), but fillB a
                            # decoded mode-3 -> must reach the generic dispatch and draw fillB
                            colors=[("fillA", 7, bogus_index), ("fillB", 3, 0x00A79D)])
    monkeypatch.setattr(layout.Screen, "walk", lambda self, *a, **k: [well, sibling])
    prims = render.screen_placements(data, root=0)[0].prims

    # not consumed as the well's level fill (fillA unresolved); reaches the generic dispatch and
    # draws its own decoded fillB -- so it is neither mis-drawn as the well's fill nor dropped.
    sibling_prim = next(p for p in prims if p.src == "file@0x2001")
    assert sibling_prim.kind == "rrect" and sibling_prim.color_src == "decoded"
    assert sibling_prim.color == (0, 0xA7, 0x9D)          # fillB, via the generic dispatch


def test_stacked_bulb_variants_draw_the_outline_state() -> None:
    """A light stacks a filled 'on' bulb (slot 25 '2') and an outline 'off' bulb-with-dimmer-
    arrows (slot 24 'O') at one pen; the render draws the sparser outline (the resting default,
    which also carries the dimmer arrows), not the filled blob."""
    from fireflymcf import layout
    data = load()
    pl = render.climate_placements_decoded(
        data, root=6, match_table_off=layout.MATCH_TABLE_136, slot_values={5: 8})
    glyph_chars = {p.text for p in pl[0].prims if p.kind == "glyph"}
    assert "O" in glyph_chars                          # the outline bulb-with-arrows is drawn
    assert "2" not in glyph_chars                      # the filled 'on' bulb is not


def test_inline_mode3_fill_is_read_from_the_bytes() -> None:
    """GUICODE Finding 18: a mode-3 colour field is an inline BGR value, used directly. The
    battery indicator (obj 721) carries the teal fill; render._inline_fill converts BGR->RGB."""
    from fireflymcf import records, layout
    data = load()
    scr = layout.Screen(data)
    battery = records.object_records(data, scr.gs + scr.table[721])
    inline = [val for r in battery for _, mode, val in r.colors if mode == 3]
    assert 0x00A79D in inline                               # the stored BGR teal

    class _P:
        colors = [("fillA", 3, 0x00A79D)]
    assert render._inline_fill(_P()) == (0, 0xA7, 0x9D)     # BGR 0x00A79D -> RGB(0,167,157)
    """Two icon-glyph state variants at one pen render once, not stacked on top of each other."""
    from fireflymcf import layout
    data = load()
    pl = render.climate_placements_decoded(
        data, root=6, match_table_off=layout.MATCH_TABLE_136, slot_values={5: 8})
    pens = [(p.x, p.y) for p in pl[0].prims if p.kind == "glyph"]
    assert len(pens) == len(set(pens))                        # no two glyphs share a pen


# --------------------------------------------------------------------------------------
# Home-render-fidelity Task 2: the tank value+unit ('%') widget.
#
# `HOME.jpg` shows a clean '0%' under each tank well, one line. The stored widget is two
# SEPARATE records at one pen (GUICODE Finding 9): a wtype-8 value (the runtime '0', label
# `None` -- not stored, never drawn) and a wtype-0 label carrying the unit string '%'. The
# bug: `screen_placements`'s icon/state-variant collector (the loop just above this section)
# keys on "single-character label", which a unit string like '%' or 'V' also matches -- so it
# was swept into logic meant for icon-font on/off state variants (`glyph_image` directly, no
# alignment/box handling, no honest missing-glyph fallback) instead of the normal per-label
# text loop.
# --------------------------------------------------------------------------------------

TANK_PERCENT_RECORDS = {
    "FRESH": 0x9111d,
    "GREY": 0x91099,
    "BLACK": 0x911a0,
    "LP": 0x924b6,
}


def test_tank_percent_units_use_the_text_path_not_the_icon_candidate_path() -> None:
    """Each tank's '%' unit (real corpus records, HOME page) must draw as a `text` Prim, not
    a `glyph` Prim -- `glyph` is the icon/state-variant path, and a value+unit label is not an
    icon. Non-vacuous: before this fix, every one of these records rendered `kind='glyph'`
    (confirmed by hand while diagnosing this bug); reverting the `wtype in (0, 8)` exclusion
    in `screen_placements` reproduces that and fails this assert immediately."""
    from fireflymcf import cli
    data = load()
    root = cli._resolve_page_root(data, "home")
    prims = render.screen_placements(data, root)[0].prims
    for tank, offset in TANK_PERCENT_RECORDS.items():
        p = next(p for p in prims if p.src == f"file@{offset:#x}")
        assert p.kind == "text", f"{tank}'s % unit rendered as {p.kind!r}, not text"
        assert p.text == "%"


def test_tank_percent_units_sit_at_the_decoded_pen_on_one_line() -> None:
    """Position/glyph come from the decoded record (the cardinal rule) -- HOME.jpg is a
    checked oracle, never a fit target, so this asserts the *byte-derived* pen, not a pixel
    match to the photo. All four tanks' '%' widgets share one y (one line, not wrapped to
    separate rows); the x is exactly the record's decoded x0 (horizontal placement has no
    centring to apply here -- align 0, degenerate box); y is the byte-derived single-line pen
    placement `pen_y - font_top` (FUN_002557C0 draws a value/unit at `pen_y - font[0x0b]`,
    0x00255810), so it lands just above the decoded pen -- tucking the '%' right under the tank
    well, not a cell-centre stand-in. `layout.Screen.walk` is the source of truth for the pen, so
    this cross-checks the render against it rather than a hardcoded literal. Non-vacuous: a stub
    that dropped the record's own x0/y0 (always (0,0), or collapsing all four to one pen) fails the
    distinctness asserts below; drawing at the raw pen (no font_top) fails the y."""
    from fireflymcf import cli, layout, metrics
    data = load()
    root = cli._resolve_page_root(data, "home")
    placed = layout.Screen(data).walk(root)
    prims = render.screen_placements(data, root)[0].prims

    ys = set()
    xs = set()
    for offset in TANK_PERCENT_RECORDS.values():
        decoded = next(p for p in placed if p.record_offset == offset)
        assert decoded.wtype == 0 and decoded.label == "%"
        drawn = next(p for p in prims if p.src == f"file@{offset:#x}")
        assert drawn.x == decoded.x0                       # exact: no centring to apply
        top = metrics.font_metrics(data, decoded.font).top
        assert drawn.y == max(1, decoded.y0 - top), (      # FUN_002557C0 pen_y - font_top
            f"{offset:#x}: drawn y={drawn.y} != pen {decoded.y0} - font_top {top}")
        assert "\n" not in drawn.text                     # one line, not wrapped
        xs.add(drawn.x)
        ys.add(drawn.y)
    assert len(ys) == 1, f"the four tanks' % units should share one y, got {ys}"
    assert len(xs) == len(TANK_PERCENT_RECORDS), "each tank's % should sit at its own x"


# The tank display is byte-derived except the runtime fill level (GUICODE Finding 18 dig + the
# owner's hardware oracle, 2026-08-15): each well (wtype 0x20) -> its level record (wtype 0x21).
TANK_WELLS = {
    "FRESH": (0x910d5, 0x910e5),
    "GREY": (0x91051, 0x91061),
    "GREY2": (0x90fcd, 0x90fdd),
    "BLACK": (0x91159, 0x91169),
}


def _home_prims_at_level(data: bytes, level: float):
    from fireflymcf import cli, layout
    root = cli._resolve_page_root(data, "home")
    return render.screen_placements(data, root, match_table_off=layout.MATCH_TABLE_136,
                                    slot_values={5: 8, 112: 1}, tank_level=level)[0].prims


@pytest.mark.parametrize("name", sorted(TANK_WELLS))
def test_tank_well_body_is_decoded_from_fillB(name: str) -> None:
    """The well BODY (the empty well behind the fill) is byte-derived from the well record's
    fillB = mode-7 index 161, resolved through `theme.mode7_palette` -- so the expected colour
    comes from the palette, never a literal tuned to the photo. Non-vacuous: before the fix the
    well drew only an outline (no interior body rect), so requiring a decoded body rect fails on
    the old code."""
    from fireflymcf import theme
    data = load()
    well_off = TANK_WELLS[name][0]
    expected = theme.mode7_palette(data)[161]
    prims = _home_prims_at_level(data, 0.0)
    bodies = [p for p in prims if p.src == f"file@{well_off:#x}"
              and p.kind in ("rect", "rrect") and not p.outline]
    assert any(p.color == expected and p.color_src == "decoded" for p in bodies), (
        f"{name}: no decoded body == {expected} in {[(p.color, p.color_src) for p in bodies]}")


def test_black_tank_level_fill_is_mode7_not_missed() -> None:
    """BLACK's level fill is a mode-7 colour (index 130 = near-black), not mode-3 -- the old
    mode-3-only level path missed it and BLACK rendered empty. Assert the level record now emits
    a decoded fill == palette[130]. Non-vacuous: reverting `_decoded_fill` to `_inline_fill`
    (mode-3 only) drops this fill entirely."""
    from fireflymcf import theme
    data = load()
    lvl_off = TANK_WELLS["BLACK"][1]
    expected = theme.mode7_palette(data)[130]
    prims = _home_prims_at_level(data, 1.0)
    fills = [p for p in prims if p.src == f"file@{lvl_off:#x}" and p.kind in ("rect", "rrect")]
    assert any(p.color == expected and p.color_src == "decoded" for p in fills), (
        f"BLACK level fill not decoded to {expected}: {[(p.color, p.color_src) for p in fills]}")


@pytest.mark.parametrize("name,expected", [
    ("FRESH", (0x00, 0xA7, 0x9D)),      # mode-3 inline #00A79D (the record's own BGR bytes)
    ("GREY", (0x80, 0x82, 0x85)),       # mode-3 inline #808285
])
def test_tank_level_fill_per_type_mode3_colour(name: str, expected: tuple) -> None:
    """FRESH/GREY level fills are mode-3 inline colours decoded byte-exact -- the literal here
    is the record's own BGR value (same convention as the battery-teal inline-fill test)."""
    data = load()
    lvl_off = TANK_WELLS[name][1]
    prims = _home_prims_at_level(data, 1.0)
    fills = [p for p in prims if p.src == f"file@{lvl_off:#x}" and p.kind in ("rect", "rrect")]
    assert any(p.color == expected and p.color_src == "decoded" for p in fills), (
        f"{name}: {expected} not among {[(p.color, p.color_src) for p in fills]}")


def test_tank_fill_height_tracks_level_and_defaults_empty() -> None:
    """The fill HEIGHT tracks the runtime `tank_level` and defaults to empty (the owner's 0%
    coach): no level fill at 0.0, a partial fill at 0.33, a taller one at 1.0. The record's own
    geometry is the 100% extent; the drawn height is `level * full`, never fitted to the photo."""
    data = load()
    lvl_off = TANK_WELLS["FRESH"][1]

    def fill_h(level: float) -> int:
        prims = _home_prims_at_level(data, level)
        fs = [p for p in prims if p.src == f"file@{lvl_off:#x}" and p.kind in ("rect", "rrect")]
        return max((p.h for p in fs), default=0)

    h0, h33, h100 = fill_h(0.0), fill_h(0.33), fill_h(1.0)
    assert h0 == 0, f"level 0 should draw no fill, got h={h0}"
    assert 0 < h33 < h100, f"expected 0 < h33({h33}) < h100({h100})"


def test_tank_well_outline_is_byte_derived_white() -> None:
    """The well draws its decoded body AND a BYTE-DERIVED white outline -- not a synthetic border.
    The outline is the well's fillA (block A = border, GUICODE Finding 28), mode 2 = hardcoded
    white, so `color_src='decoded'`, colour white. (Superseded the earlier "no synthetic outline"
    negative once the border mechanism was decoded.) Non-vacuous: a heuristic/absent border, or one
    not white, fails this."""
    data = load()
    prims = _home_prims_at_level(data, 0.0)
    assert any(p.src == "file@0x910d5" and not p.outline for p in prims)   # the body still draws
    outs = [p for p in prims if p.src == "file@0x910d5" and p.outline]
    assert outs and outs[0].color == (255, 255, 255) and outs[0].color_src == "decoded"


def test_awning_bar_background_is_decoded_from_fillB() -> None:
    """The dark bar behind the 'Awning' label (and the whole EXT/Awning/RET row) is byte-derived:
    the Awning label record's own fillB = mode-7 idx 3 (the navy `(21,24,58)`), resolved through
    the palette and drawn as its background box. Byte-only reads fillB when fillA is inherit.
    Non-vacuous: dropping the fillB fallback in the box dispatch leaves the bar unpainted."""
    from fireflymcf import theme
    data = load()
    expected = theme.mode7_palette(data)[3]
    prims = _home_prims_at_level(data, 0.0)
    bar = next(p for p in prims if p.src == "file@0x97ed9")            # the Awning label record
    assert bar.color == expected and bar.color_src == "decoded"
    assert not bar.outline and bar.stroke is None


# The House battery icon (object 443). The terminal nub carries its own mode-7 idx 3 (navy); the
# body and the second nub are mode-0 inherit -> they inherit that navy via the persistent colour
# slots (GUICODE Finding 29), so all three paint navy. The voltage strip is a mode-7 level-indicator
# (idx 206) that is runtime-state-selected and NOT in the theme palette -> still not painted.
BATTERY_PARTS = {
    "fill": 0x93d2b,    # mode-7 206 (unresolved runtime) -> voltage strip, NOT painted
    "nub_decoded": 0x93cf9,   # mode-7 3 -> navy nub (its own field)
    "nub_mode0": 0x93d09,     # mode-0 inherit -> navy (inherited)
    "body": 0x93d19,          # mode-0 inherit -> navy (inherited)
}


def test_battery_body_and_nubs_paint_inherited_navy_strip_stays_runtime() -> None:
    """The battery body + both nubs paint navy: the nub carries mode-7 idx 3 (navy) and the mode-0
    body/second-nub INHERIT it via the persistent colour slots (GUICODE Finding 29). The voltage
    strip (mode-7 idx 206) is runtime-state (level/alarm), not in the theme palette, so it stays
    unpainted -- a defensible negative. Non-vacuous: the strip painting, or the mode-0 parts not
    inheriting navy, fails this."""
    from fireflymcf import theme
    data = load()
    navy = theme.mode7_palette(data)[3]
    prims = _home_prims_at_level(data, 0.0)
    for off in (BATTERY_PARTS["nub_decoded"], BATTERY_PARTS["nub_mode0"], BATTERY_PARTS["body"]):
        b = [p for p in prims if p.src == f"file@{off:#x}" and p.kind in ("rect", "rrect")]
        assert b and b[0].color == navy, f"{off:#x} should paint inherited navy"
    assert not any(p.src == f"file@{BATTERY_PARTS['fill']:#x}" for p in prims)   # voltage strip runtime


def test_battery_level_fraction_is_byte_derived() -> None:
    """The battery charge fraction is 1 - clamp((12.5V - v)*4, 0, 40)/40 (FUN_00247bf8 0x24a35a):
    full at/above 12.5V, empty at/below 11.5V, linear between."""
    assert render._battery_level_fraction(13.2) == 1.0
    assert render._battery_level_fraction(12.5) == 1.0
    assert render._battery_level_fraction(12.0) == 0.5
    assert render._battery_level_fraction(11.5) == 0.0
    assert render._battery_level_fraction(11.0) == 0.0


def test_home_battery_fill_is_coloured_and_levelled_by_voltage_byte_derived() -> None:
    """The HOME Chassis battery fill (mode-7 idx 202) is a voltage gauge, not SoC: FUN_00247bf8
    (0x24a326-0x24a36e) colours it green (theme idx 224) above 11.9V / red (idx 118) at-or-below, and
    the fill HEIGHT is its byte-derived charge fraction (0x24a35a). Byte-derived: the 11.9V threshold
    (0x77 decivolts) and the (12.5V-v)*4 level are code constants, the colours come from
    `theme.mode7_palette`. Non-vacuous: no preview voltage -> unpainted; 12.0V is a HALF-height fill
    with a rounded bottom + flat top; an empty (<=11.5V) fill draws nothing."""
    from fireflymcf import cli, layout, theme
    data = load()
    root = cli._resolve_page_root(data, "home")
    pal = theme.mode7_palette(data)
    green, red = pal[224], pal[118]
    fill_off = 0x93dc3                                        # Chassis battery fill (idx 202)

    def fill_at(volts):
        prev = {"chassis_voltage": volts} if volts is not None else None
        prims = render.screen_placements(data, root, match_table_off=layout.MATCH_TABLE_136,
                                         slot_values={5: 8, 112: 1, 6: 1}, preview=prev)[0].prims
        return [p for p in prims if p.src == f"file@{fill_off:#x}"]

    full = fill_at(13.2)[0]
    assert full.color == green and full.color_src == "voltage-state" and full.corners is None
    half = fill_at(12.0)[0]
    assert half.color == green and half.corners == (False, False, True, True)
    assert abs(half.h - full.h / 2) <= 1 and half.y > full.y   # half height, sitting lower (bottom-up)
    low = fill_at(11.8)[0]
    assert low.color == red and low.h < half.h                 # red and even less charge
    assert not fill_at(11.5)                                    # empty -> nothing drawn
    assert not fill_at(None)                                    # runtime value absent -> unpainted


def test_value_widget_single_char_label_is_excluded_from_icon_state_stacking(monkeypatch) -> None:
    """Isolated unit test (synthetic `Placed`s, same technique as the gauge-fill tests above):
    a wtype-0/8 value widget whose label is one character (a unit, not an icon) must not enter
    the icon/state-variant candidate collection at all, even when it shares a pen with nothing
    else -- proving the exclusion is on `wtype`, not on incidental pen collisions. A real icon
    record (wtype 11, per this corpus's HOME/LIGHTS pages) at a different pen must still go
    through the candidate path unaffected."""
    from fireflymcf import layout
    data = load()
    unit = layout.Placed(wtype=0, label="%", x0=500, y0=100, x1=500, y1=100,
                         depth=0, obj_index=1, record_offset=0x1000,
                         font=RELIABLE_SLOT, align=0, colors=[])
    icon = layout.Placed(wtype=11, label="a", x0=200, y0=100, x1=200, y1=100,
                         depth=0, obj_index=2, record_offset=0x2000,
                         font=RELIABLE_SLOT, align=0, colors=[])
    monkeypatch.setattr(layout.Screen, "walk", lambda self, *a, **k: [unit, icon])
    prims = render.screen_placements(data, root=0)[0].prims

    unit_prim = next(p for p in prims if p.src == "file@0x1000")
    assert unit_prim.kind == "text"
    icon_prim = next(p for p in prims if p.src == "file@0x2000")
    assert icon_prim.kind == "glyph"                # unaffected: still the icon path


@pytest.mark.parametrize("off", [0x9518d, 0x951b3, 0x951d9, 0x951ff, 0x95225])
def test_nav_rail_icon_glyph_is_centred_in_its_box(off: int) -> None:
    """The 5 nav-rail icons (font-24 glyphs, box (0,y)-(60,y+60) spanning the full 60px rail,
    align 0) are centred horizontally in their own decoded box -- the same byte-derived
    centre-in-a-wider-box rule the text path uses. Non-vacuous: before the fix the icon drew at
    the box's left edge (x=0); this asserts its drawn centre is ~the box centre (30), i.e. moved
    right of x0."""
    data = load()
    placed, prims = _home_placed_and_prims(data)
    box = next(p for p in placed if p.record_offset == off)
    assert box.x0 == 0 and box.x1 == 60                       # the full-width rail box
    g = next(p for p in prims if p.src == f"file@{off:#x}" and p.kind == "glyph")
    centre = g.x + g.image.width / 2
    assert abs(centre - (box.x0 + box.x1) / 2) <= 1, f"icon centre {centre} not ~30"
    assert g.x > box.x0, "icon should be centred, not at the box left edge"


def test_value_widget_draws_real_panel_glyphs_now_that_the_decode_gap_is_closed(monkeypatch) -> None:
    """Formerly `test_value_widget_honestly_falls_back_when_its_unit_glyph_is_unreliable`: it
    documented the flip side of the routing fix (`test_value_widget_single_char_label_is_
    excluded_from_icon_state_stacking` above) -- once a value widget's single-character label
    goes through the normal text loop, it used to hit `glyphs.is_reliable` and fall back to the
    DejaVu stand-in, because `RELIABLE_SLOT`'s 'I' decoded short under the old flat reader.
    `GUICODE.md` Finding 22's RLE rewrite fixed exactly that shape (a vertically-uniform stroke
    like an 'I' stem is the textbook case), so the same synthetic single-character unit label
    now proves the opposite: the value-widget path draws the panel's own bitmap
    (`font_src == 'panel'`), not that it silently keeps falling back. Non-vacuous: reintroducing
    the retired `is_reliable` gate, or a regression in the RLE decode, makes this label draw the
    DejaVu stand-in instead, and the asserts below would catch it."""
    from fireflymcf import layout, glyphs
    data = load()
    font = glyphs.font_for_slot(data, RELIABLE_SLOT)
    g = glyphs.decode_glyph(data, font.glyph_index(ord("I")))
    assert g.height == data[g.offset + glyphs.OFF_HEIGHT]   # decodes to its full height now

    fake = layout.Placed(wtype=0, label="I", x0=100, y0=50, x1=100, y1=50,
                         depth=0, obj_index=0, record_offset=0x9968b,
                         font=RELIABLE_SLOT, align=0, colors=[])
    monkeypatch.setattr(layout.Screen, "walk", lambda self, *a, **k: [fake])
    pl = render.climate_placements_decoded(data)
    texts = [p for p in pl[0].prims if p.kind == "text"]
    assert texts and texts[0].font_src == "panel"          # real panel bitmap, not the stand-in
    assert texts[0].glyph_images


def test_same_size_rename_flips_the_render_with_no_code_change() -> None:
    """The point of the data path: patch 'Bath Fan'->'Starlink' and the caption follows."""
    data = bytearray(load())
    at = data.find(b"Bath Fan", 0x97200, 0x97260)     # the Climate caption occurrence
    assert at != -1
    data[at:at + 8] = b"Starlink"                     # same size, no relocation
    star = next(pl for pl in render.climate_placements(bytes(data))
                if pl.name == "starlink")
    caption = next(p for p in star.prims
                   if p.kind == "text" and p.src.startswith("file@"))
    assert caption.text == "Starlink"


# --------------------------------------------------------------------------------------
# Task 3: proportional text from the panel's own glyph bitmaps, and mode-3 inline colour.
# --------------------------------------------------------------------------------------

# Real GuiData content, not a synthetic string: 13.6's font slot 14 draws "Bay" (a battery-
# page label at file offset 0x9968b). Named "reliable" from when `glyphs.is_reliable` still
# distinguished decodable glyphs from the pre-Finding-22 truncated-decode gap; that gate is
# retired now (every glyph decodes to its full declared height, Climate labels included --
# see `test_climate_labels_render_from_the_panels_own_glyphs_not_dejavu` below), so the name
# is kept only because it is this module's established handle for this real corpus string.
RELIABLE_SLOT, RELIABLE_TEXT = 14, "Bay"


def test_panel_text_images_uses_the_panels_own_glyph_bitmaps() -> None:
    """`panel_text_images` must draw from `glyphs.decode_glyph`'s real bitmap, not a system
    font: every character's pixels are byte-identical to `glyph_image`'s tinted decode of that
    exact glyph, and stubbing this to source from DejaVu instead would break the pixel
    comparison (not vacuous -- it fails if the function ever falls back silently)."""
    data = load()
    imgs = render.panel_text_images(data, RELIABLE_SLOT, RELIABLE_TEXT, render.COL["white"])
    assert imgs is not None and len(imgs) == len(RELIABLE_TEXT)
    for (dx, dy, img), ch in zip(imgs, RELIABLE_TEXT):
        expected = render.glyph_image(data, RELIABLE_SLOT, ch, render.COL["white"])
        assert expected is not None
        assert img.size == expected.size
        assert list(img.getdata()) == list(expected.getdata())   # the panel's own pixels
    # positions are the exact kerned advance metrics.glyph_positions replays, so strictly
    # ordered left to right -- not an evenly-spaced guess.
    xs = [dx for dx, _dy, _ in imgs]
    assert xs == sorted(xs) and len(set(xs)) == len(xs)


def test_panel_text_images_renders_the_previously_undecodable_string() -> None:
    """Formerly `test_panel_text_images_falls_back_honestly_on_the_known_decode_gap`: it
    documented `glyphs.py`'s third `unknown` -- thin/rectilinear-stroke letters (I L T H among
    them) that used to decode far short of their declared height, so a string touching one
    ('High', same font slot as the reliable 'Bay' case above) got rejected wholesale and fell
    back to DejaVu. `GUICODE.md` Finding 22's RLE rewrite closed that gap: every character in
    'High' now decodes to its full declared height, so `panel_text_images` must return real
    bitmaps for the whole string, byte-identical to `glyph_image`'s own tinted decode of each
    character (the same comparison `test_panel_text_images_uses_the_panels_own_glyph_bitmaps`
    makes for 'Bay'). Non-vacuous: reintroducing the retired `is_reliable` gate (or a
    regression in the RLE decode) makes this return `None` again."""
    from fireflymcf import glyphs
    data = load()
    for ch in "High":
        font = glyphs.font_for_slot(data, RELIABLE_SLOT)
        g = glyphs.decode_glyph(data, font.glyph_index(ord(ch)))
        assert g.height == data[g.offset + glyphs.OFF_HEIGHT]   # full height, not truncated

    imgs = render.panel_text_images(data, RELIABLE_SLOT, "High", render.COL["white"])
    assert imgs is not None and len(imgs) == len("High")
    for (dx, dy, img), ch in zip(imgs, "High"):
        expected = render.glyph_image(data, RELIABLE_SLOT, ch, render.COL["white"])
        assert expected is not None
        assert img.size == expected.size
        assert list(img.getdata()) == list(expected.getdata())   # the panel's own pixels
    xs = [dx for dx, _dy, _ in imgs]
    assert xs == sorted(xs) and len(set(xs)) == len(xs)


def test_panel_text_glyphs_carry_per_glyph_vertical_bearing() -> None:
    """Each glyph is placed at its own vertical bearing (glyph.y, header +0x0c) -- the
    cell-top-to-bitmap-top offset the blitter FUN_002510C0 applies (`extractions/blitter.txt`
    line 54: `sVar3 = sVar3 + param_2 + glyph[0xc]`). So x-height letters ('a','o') sit BELOW
    the caps while sharing their baseline, and descenders ('g','y') drop past it.
    `panel_text_images` returns `(dx, dy, img)` with `dy == glyph.y`; the old 2-tuple gave every
    glyph one shared vertical origin (top-aligned), which top-aligned x-height letters with caps
    and lifted descenders -- this test fails against that."""
    from fireflymcf import glyphs
    data = load()
    slot = RELIABLE_SLOT
    font = glyphs.font_for_slot(data, slot)
    bpp = glyphs.font_bpp(data, font)

    def gy(ch: str) -> int:
        return glyphs.decode_glyph(data, font.glyph_index(ord(ch)), bpp=bpp).y

    imgs = render.panel_text_images(data, slot, "Aag", render.COL["white"])
    assert imgs is not None and len(imgs) == 3
    (dx_A, dy_A, im_A), (dx_a, dy_a, im_a), (dx_g, dy_g, im_g) = imgs
    # dy is exactly the decoded per-glyph vertical bearing, byte-for-byte
    assert (dy_A, dy_a, dy_g) == (gy("A"), gy("a"), gy("g"))
    # x-height 'a' sits lower than cap 'A' ...
    assert dy_a > dy_A
    # ... but shares its baseline (the bitmap bottom lands at the same row)
    assert dy_a + im_a.height == dy_A + im_A.height
    # descender 'g' drops below that baseline
    assert dy_g + im_g.height > dy_A + im_A.height


def test_decoded_compositor_uses_real_panel_glyphs_when_the_string_is_reliable(monkeypatch) -> None:
    """Wired end-to-end, not just the standalone helper: a label whose glyphs all decode
    reliably is drawn by the compositor with real bitmaps (`font_src == 'panel'`,
    `glyph_images` populated), sourced from `glyphs.decode_glyph` at the record's own decoded
    font slot (`records.WRecord.font`, surfaced here as `layout.Placed.font`)."""
    from fireflymcf import layout
    data = load()
    fake = layout.Placed(wtype=0, label=RELIABLE_TEXT, x0=100, y0=50, x1=220, y1=90,
                         depth=0, obj_index=0, record_offset=0x9968b,
                         font=RELIABLE_SLOT, align=0, colors=[])
    monkeypatch.setattr(layout.Screen, "walk", lambda self, *a, **k: [fake])
    pl = render.climate_placements_decoded(data)
    texts = [p for p in pl[0].prims if p.kind == "text"]
    assert texts, "expected the fake reliable label to produce a text prim"
    t = texts[0]
    assert t.font_src == "panel" and t.glyph_images
    assert t.text.strip() == RELIABLE_TEXT


def test_climate_labels_render_from_the_panels_own_glyphs_not_dejavu() -> None:
    """Formerly `test_climate_labels_fall_back_honestly_given_the_known_decode_gap`: every real
    Climate label in 13.6 (CLIMATE, Cool, High, Low, Auto) contains a letter from the class
    `GUICODE.md` Finding 22 fixed (thin/tall vertical strokes: I, L, T, H, ...), so before the
    RLE rewrite every one of these fell back to the labelled DejaVu stand-in. This is Task 2's
    acceptance test: the decoded render must now draw all five with `font_src == 'panel'` and
    populated `glyph_images` -- pixel-faithful panel text, not the retired fallback. Non-
    vacuous: reverting the fallback retirement, or regressing the RLE decode, flips these back
    to `'heuristic'` with no `glyph_images` and fails this."""
    pl = render.climate_placements_decoded(load())
    texts = {p.text.strip(): p for p in pl[0].prims if p.kind == "text"}
    for label in ("CLIMATE", "Cool", "High", "Low", "Auto"):
        assert texts[label].font_src == "panel", f"{label} fell back to {texts[label].font_src!r}"
        assert texts[label].glyph_images, f"{label} has no panel glyph images"


def test_text_uses_the_records_own_mode3_inline_fill(monkeypatch) -> None:
    """A label whose own record carries a mode-3 inline BGR fill (`records.WRecord.colors`,
    GUICODE Finding 18) is drawn in that colour, tagged `color_src == 'decoded'` -- not the
    eyeballed heuristic white/palette default. Same field and same BGR->RGB conversion
    `test_inline_mode3_fill_is_read_from_the_bytes` already proved for box fills; this proves
    the text path reads it too."""
    from fireflymcf import layout
    data = load()
    fake = layout.Placed(wtype=0, label=RELIABLE_TEXT, x0=100, y0=50, x1=220, y1=90,
                         depth=0, obj_index=0, record_offset=0x9968b,
                         font=RELIABLE_SLOT, align=0,
                         colors=[("fillA", 3, 0x00A79D)])              # stored BGR teal
    monkeypatch.setattr(layout.Screen, "walk", lambda self, *a, **k: [fake])
    pl = render.climate_placements_decoded(data)
    texts = [p for p in pl[0].prims if p.kind == "text"]
    assert texts
    assert texts[0].color == (0, 0xA7, 0x9D)          # BGR 0x00A79D -> RGB(0,167,157)
    assert texts[0].color_src == "decoded"


def test_text_ink_is_decoded_from_its_mode_not_defaulted_to_white() -> None:
    """This used to assert the flip side of the test above -- that a Climate label with no mode-3
    colour "stays the labelled heuristic", because the renderer only resolved mode 3. That was a
    statement about the renderer's limits, not about the bytes: Climate's text records carry
    fillA modes 2/6/7 (and one 3), every one of which decodes (GUICODE Finding 11). They now
    report `decoded`, at the same pixels -- the CLIMATE render is byte-identical before and after,
    because mode 2 IS white; only the honesty label changed.

    The "never claim decoded when it isn't" intent is preserved by asserting the colour actually
    came from the record's own mode, so a regression that hardcodes white would still be caught
    by the mode-7 half of `test_text_ink_resolves_every_colour_mode_not_just_inline`."""
    from fireflymcf import layout
    data = load()
    texts = [p for p in render.climate_placements_decoded(data)[0].prims if p.kind == "text"]
    assert texts and all(p.color_src == "decoded" for p in texts)
    # ...and every Climate label really does carry a resolvable mode (2/3/6/7), never mode 0-only
    modes = set()
    for q in layout.Screen(data).walk(layout.climate_root(data)):
        if getattr(q, "label", None) is None:
            continue
        modes |= {m for role, m, _ in (q.colors or []) if role == "fillA"}
    assert modes and modes <= {2, 3, 6, 7}, modes


# --------------------------------------------------------------------------------------
# Task 4: `screen_placements` -- the general any-root compositor. `climate_placements_decoded`
# is now a thin wrapper over it (proved above, unchanged); these tests exercise the general
# entry directly, on roots that are NOT Climate, so a regression that only special-cased
# Climate's root would be caught.
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize("page_name", ["home", "lights"])
def test_screen_placements_is_byte_derived_for_multiple_roots(page_name: str) -> None:
    """`screen_placements` must actually walk the object graph from whatever root it is
    given, not draw from a hardcoded per-page table. Proof, for two different real pages:
    (a) the root itself is resolved from the file's own header-label bytes
    (`cli._resolve_page_root`), never a literal chosen to make this test pass; (b) every text
    prim it emits carries `file@<offset>` provenance (`Prim.src`) -- traced to one specific
    GuiData record, never `'synthetic'`; (c) the rasterised image has drawn content beyond the
    flat background fill. Stubbing `screen_placements` to return `[Placement(.... prims=[])]`
    breaks (b) and (c) immediately, so this is not vacuous."""
    from fireflymcf import cli, layout
    data = load()
    root = cli._resolve_page_root(data, page_name)
    match_table_off = layout.MATCH_TABLE_136 if page_name == "lights" else None
    slot_values = {5: 8} if page_name == "lights" else None       # floorplan WD: LIGHTS needs it

    placed = render.screen_placements(data, root, match_table_off=match_table_off,
                                      slot_values=slot_values)
    prims = placed[0].prims
    assert prims, f"{page_name} (root {root}) produced no primitives at all"
    text_prims = [p for p in prims if p.kind == "text"]
    assert text_prims, f"{page_name} produced no text -- did the walk actually run?"
    assert all(p.src.startswith("file@") for p in text_prims), (
        "every text prim must be traced to a GuiData record offset, not hardcoded")

    img = render.rasterize(placed)
    assert img.size == (render.PANEL_W, render.PANEL_H)
    colors = img.getcolors(maxcolors=2_000_000)
    assert colors is not None and len(colors) > 1, (
        "expected drawn content beyond the background fill (getbbox() alone can't tell, "
        "since the navy background is itself non-zero)")


def test_screen_placements_differs_by_root_not_a_fixed_table() -> None:
    """Two different real pages must decode to different content. If `screen_placements`
    ignored `root` (or fell back to a fixed/calibrated table for anything it didn't
    recognise), HOME and LIGHTS would render identically -- they must not."""
    from fireflymcf import cli, layout
    data = load()
    home_root = cli._resolve_page_root(data, "home")
    lights_root = cli._resolve_page_root(data, "lights")
    assert home_root != lights_root            # genuinely different objects in the table

    def texts(root: int, **kw) -> set[str]:
        placed = render.screen_placements(data, root, **kw)
        return {p.text.strip() for p in placed[0].prims if p.kind == "text" and p.text.strip()}

    home_texts = texts(home_root)
    lights_texts = texts(lights_root, match_table_off=layout.MATCH_TABLE_136,
                         slot_values={5: 8})
    assert "HOME" in home_texts             # HOME's own nav caption
    assert "LIGHTS" in lights_texts          # LIGHTS' own nav caption
    assert home_texts != lights_texts        # not the same content drawn twice


# --------------------------------------------------------------------------------------
# Chrome-colour Task 2: wire `theme.mode7_palette` into the mode-7 fill path.
#
# A mode-7 fill is a colour-table index (GUICODE Finding 18); the chrome-colour dig
# (2026-08-15, `theme.py`) recovered the GuiCode theme-init constants a resolvable subset of
# those SRAM cells are written with at boot. `_decoded_fill`/`_mode7_fill` look the index up
# in a once-per-render `theme.mode7_palette(data)`: resolved -> `color_src="decoded"`, the
# real colour; unresolved (a named residual) -> the record's own position heuristic,
# unchanged from before this task.
# --------------------------------------------------------------------------------------

CHROME_HEADER_OFFSET = 0x9e0d3     # the full-width top strip, HOME page, mode-7 index 4


def test_mode7_fill_decodes_the_chrome_red_from_the_palette() -> None:
    """The header bar (wtype 4, full-width top strip, HOME page) carries a mode-7 fill at
    index 4 -- `theme.mode7_palette` resolves this to the panel's real header/ON-OFF red
    (`build/13.6-images/HOME.jpg` shows this bar red; the heuristic it replaces,
    `COL['crimson']`, is a different eyeballed guess). Non-vacuous: the expected colour is
    derived from `theme.mode7_palette(data)[4]` itself, never a literal tuned to the photo --
    a stub that fell the mode-7 branch through to the heuristic fails the `color`/`color_src`
    asserts immediately, and was confirmed to do so during development (mode-7 branch
    stubbed out, this test failed, restored)."""
    from fireflymcf import cli, layout, theme
    data = load()
    palette = theme.mode7_palette(data)
    assert 4 in palette, "index 4 should resolve -- test's premise"

    root = cli._resolve_page_root(data, "home")
    scr = layout.Screen(data, match_table_off=layout.MATCH_TABLE_136)
    placed = scr.walk(root, slot_values={5: 8})
    header = next(p for p in placed if p.record_offset == CHROME_HEADER_OFFSET)
    assert header.wtype == 4
    index = next(v for role, mode, v in header.colors if role == "fillA" and mode == 7)
    assert index == 4

    prims = render.screen_placements(data, root, match_table_off=layout.MATCH_TABLE_136,
                                     slot_values={5: 8})[0].prims
    prim = next(p for p in prims if p.src == f"file@{CHROME_HEADER_OFFSET:#x}")
    assert prim.color_src == "decoded"
    assert prim.color == palette[4]
    # sanity: the decoded colour must actually differ from the heuristic guess it replaces,
    # or this test would pass even if the heuristic were used by mistake
    assert prim.color != render.COL["crimson"]


def test_mode7_fill_unresolved_index_is_not_painted(monkeypatch) -> None:
    """Byte-only: an index `theme.mode7_palette` could not resolve (a named residual -- a
    runtime-copied SRAM cell, or one the tracer's arithmetic can't follow, GUICODE Finding 18)
    must never be guessed. With no decoded fill in EITHER role, the box is NOT painted (the
    decoded parent shows through) -- never an invented heuristic. The index is chosen to be
    verifiably absent from the real palette (`max(palette) + 1000`), not an arbitrary literal
    that might start resolving if a future extractor improvement widens the palette."""
    from fireflymcf import layout, theme
    data = load()
    palette = theme.mode7_palette(data)
    bogus_index = max(palette, default=-1) + 1000
    assert bogus_index not in palette

    box = layout.Placed(wtype=33, label=None, x0=200, y0=100, x1=260, y1=140,
                        depth=0, obj_index=9, record_offset=0x4000,
                        font=None, align=0, colors=[("fillA", 7, bogus_index)])
    monkeypatch.setattr(layout.Screen, "walk", lambda self, *a, **k: [box])
    prims = render.screen_placements(data, root=0)[0].prims
    assert not any(p.src == "file@0x4000" for p in prims)   # unresolved -> not painted


# --------------------------------------------------------------------------------------
# Home-render-fidelity Task 3: button-box visibility (mode-0/7 outline) + label centring.
#
# `HOME.jpg` (WD floorplan, `slot_values={5: 8}` -- the same override that surfaces GREY2/
# Tank Heater elsewhere in this file) shows Water Pump / Tank Heater / ON / OFF / EXT / RET
# as visible rounded boxes with centred labels. Root cause: all six are wtype-33 records
# whose OWN fill is mode-0/7 (inherit/runtime, GUICODE Finding 18) -- nothing decodable backs
# a fill, and before this task they painted the same heuristic `COL["button"]` as the
# container/bar record they sit inside (e.g. ON/OFF's own 70px boxes sit inside a wider
# "Light Master" bar, all three mode-7/non-inline, all identically coloured), so the button
# was never visually distinct from its surroundings.
# --------------------------------------------------------------------------------------

HOME_BUTTON_BOXES = {
    "ON": 0x95312, "OFF": 0x9534b, "EXT": 0x97865, "RET": 0x96c69,
    "Water Pump": 0x966c9, "Tank Heater": 0x7f750,
}
HOME_BUTTON_LABELS = {
    "ON": 0x95322, "OFF": 0x9535f, "EXT": 0x97873, "RET": 0x96c77,
    "Water Pump": 0x966d9, "Tank Heater": 0x7f764,
}


def _home_placed_and_prims(data: bytes):
    return _page_placed_and_prims(data, "home")


def _page_placed_and_prims(data: bytes, page: str):
    from fireflymcf import cli, layout
    root = cli._resolve_page_root(data, page)
    scr = layout.Screen(data, match_table_off=layout.MATCH_TABLE_136)
    placed = scr.walk(root, slot_values={5: 8})
    prims = render.screen_placements(data, root, match_table_off=layout.MATCH_TABLE_136,
                                     slot_values={5: 8})[0].prims
    return placed, prims


def _label_yspan(prims, off: int):
    """(top, bottom) of a label's drawn text lines, or None if not drawn."""
    ys = [p for p in prims if p.src == f"file@{off:#x}" and p.kind == "text"]
    if not ys:
        return None
    tops = [p.y for p in ys]
    heights = [max((gi.height for _, _dy, gi in p.glyph_images), default=0) for p in ys]
    return min(tops), max(t + h for t, h in zip(tops, heights))


# Same-pen label pairs with IDENTICAL box height whose DRAWN horizontal extents are disjoint --
# a false collision the overlap-aware stacking must NOT stack: each centres in its own box, so
# the two share a line. (page, wide_label, narrow_label). (ELECTRICAL 'Stop'/'Engine' also share
# a pen but their boxes differ in height, so vertical-centring -- not stacking -- sets their y;
# that's a different case with no clean shared-line invariant, so it isn't asserted here.)
DISJOINT_SAME_PEN_PAIRS = [
    ("home", 0x952f1, 0x95322),        # 'Light Master' + 'ON'
    ("lights", 0x952f1, 0x95322),      # 'Light Master' + 'ON'
    ("lights", 0x960a1, 0x960d5),      # 'Exterior Master' + 'ON'
]


# Every HOME button box now paints a byte-derived colour (GUICODE Findings 28/29 closed the last of
# the previously-unpainted negatives): ON/OFF via the chrome palette (mode-7 idx 10); Water Pump /
# Tank Heater via their resting constant (idx 160/455); EXT/RET via the mode-0-inherited resting
# fill (idx 131/133 -> (65,81,163)). None fall back to unpainted anymore.
@pytest.mark.parametrize("name", sorted(HOME_BUTTON_BOXES))
def test_every_home_button_box_is_painted_byte_derived(name: str) -> None:
    """All six HOME button boxes draw a box prim in a byte-derived colour (decoded palette /
    inherited / resting) -- the mode-0-inherit and runtime-resting mechanisms resolved every one.
    Non-vacuous: a button that fell back to unpainted (or to a 'heuristic' colour) fails this."""
    data = load()
    offset = HOME_BUTTON_BOXES[name]
    placed, prims = _home_placed_and_prims(data)
    box = next(p for p in placed if p.record_offset == offset)
    assert box.wtype == 33
    painted = [p for p in prims if p.src == f"file@{offset:#x}"
               and p.kind in ("rect", "rrect") and not p.outline]
    assert painted, f"{name} button box not painted"
    assert painted[0].color_src in ("decoded", "resting"), f"{name} painted only heuristically"


def test_button_box_mode7_fill_decodes_through_the_chrome_palette() -> None:
    """ON/OFF's shared mode-7 index (10) DOES resolve in `theme.mode7_palette` (Task 2) --
    unlike the four boxes above, they draw their real decoded colour and get no synthetic
    stroke (the same no-stroke rule already applied to a mode-3 inline fill below: once a
    box's own colour is genuinely known, the border workaround is not needed). Non-vacuous:
    the expected colour is derived from `theme.mode7_palette(data)[index]` itself, never a
    literal tuned to `HOME.jpg` -- a stub that fell mode-7 through to the heuristic fails the
    `color_src`/`color`/`stroke` asserts immediately."""
    from fireflymcf import theme
    data = load()
    palette = theme.mode7_palette(data)
    placed, prims = _home_placed_and_prims(data)
    for name in ("ON", "OFF"):
        offset = HOME_BUTTON_BOXES[name]
        box = next(p for p in placed if p.record_offset == offset)
        index = next(v for role, mode, v in box.colors if role == "fillA" and mode == 7)
        assert index in palette, f"{name}'s mode-7 index {index} unexpectedly unresolved"
        prim = next(p for p in prims if p.src == f"file@{offset:#x}")
        assert prim.color_src == "decoded"
        assert prim.color == palette[index]
        assert prim.stroke is None


def test_button_box_with_inline_mode3_fill_keeps_decoded_and_no_stroke(monkeypatch) -> None:
    """Flip side, isolated (same monkeypatch technique as the gauge-fill tests above): a
    wtype-33 record that DOES carry a mode-3 inline colour must not get the mode-0/7 stroke
    treatment -- it is drawn solid in its own decoded colour, `color_src='decoded'`, and
    `stroke=None` (no border invented on top of a colour that's already known)."""
    from fireflymcf import layout
    data = load()
    coloured = layout.Placed(wtype=33, label=None, x0=200, y0=100, x1=260, y1=140,
                             depth=0, obj_index=9, record_offset=0x3000,
                             font=None, align=0, colors=[("fillA", 3, 0x00A79D)])
    monkeypatch.setattr(layout.Screen, "walk", lambda self, *a, **k: [coloured])
    prims = render.screen_placements(data, root=0)[0].prims
    prim = next(p for p in prims if p.src == "file@0x3000")
    assert prim.color_src == "decoded"
    assert prim.color == (0, 0xA7, 0x9D)
    assert prim.stroke is None


# Labels whose decoded box is TALLER than their text block: the panel centres the block
# vertically in the box (Water Pump / Tank Heater's two lines in a 50px box; House / Chassis
# beside the battery in a 57px box), the vertical twin of the horizontal centre-in-box below.
# Offsets are the same label records HOME_BUTTON_LABELS already anchors, plus the two battery
# captions -- real corpus records, HOME page.
HOME_VCENTRE_LABELS = {
    "Water Pump": 0x966d9, "Tank Heater": 0x7f764,
    "House": 0x84ec2, "Chassis": 0x84ef0,
}
# Labels whose box is NOT taller than their text block, so they must stay top-anchored:
# CLIMATE is a header over (not inside) its box (20px box < 24px cell); the tank '%' units
# sit in a degenerate (h=0) box. These guard the fix against dragging them off their pen.
HOME_TOP_ANCHORED_LABELS = {"CLIMATE": 0x92b9e}


def _vcentre_geom(data, label):
    """Byte-derived vertical-centre inputs for a label record, mirroring the compositor:
    font cell height (`metrics.font_metrics`), line count, and the block height the fix
    compares against the decoded box height."""
    from fireflymcf import metrics
    size = metrics.font_metrics(data, label.font).height
    lines = [ln for ln in label.label.split("\n") if ln.strip()]
    n_lines = len(lines)
    multiline = n_lines > 1
    # Mirror render.py: a multi-line block is (n-1)*line_height + font cell height (FUN_002559A0
    # advances by the decoded +0x96 line-height); single-line keeps the size+2 default.
    lh = label.line_height if (multiline and label.line_height) else size + 2
    block_h = ((n_lines - 1) * lh + size) if multiline \
        else (n_lines * size + max(0, n_lines - 1) * 2)
    box_h = label.y1 - label.y0
    return size, block_h, box_h


@pytest.mark.parametrize("page,wide,narrow", DISJOINT_SAME_PEN_PAIRS,
                         ids=[f"{p}:{w:#x}" for p, w, _ in DISJOINT_SAME_PEN_PAIRS])
def test_same_pen_labels_with_disjoint_x_are_not_stacked(page: str, wide: int, narrow: int) -> None:
    """Two labels authored at the same pen (x0,y0) but with non-overlapping DRAWN horizontal
    extents (a wide caption that centres into the middle of its box, and a narrow button label
    that sits at the left of a narrow box) must share a line, not stack. Asserted by their drawn
    y-spans OVERLAPPING (same line). Non-vacuous: before the overlap-aware fix the narrow label
    stacked a full line below the wide one (its span sat entirely below), which fails this."""
    data = load()
    _, prims = _page_placed_and_prims(data, page)
    a = _label_yspan(prims, wide)
    b = _label_yspan(prims, narrow)
    assert a and b, f"{page}: labels {wide:#x}/{narrow:#x} not both drawn"
    assert a[0] < b[1] and b[0] < a[1], (
        f"{page}: {wide:#x} y={a} and {narrow:#x} y={b} do not share a line -- one is stacked")


def test_home_on_and_off_share_the_same_centred_y() -> None:
    """ON and OFF are authored with identical box height and font; once neither falsely stacks,
    each centres in its own box, so the two land at the same y. Non-vacuous: pre-fix ON stacked
    below 'Light Master' and sat ~20px lower than OFF."""
    data = load()
    placed, prims = _home_placed_and_prims(data)
    on = next(p for p in placed if p.record_offset == 0x95322)
    off = next(p for p in placed if p.record_offset == 0x9535f)
    assert (on.y1 - on.y0) == (off.y1 - off.y0) and on.font == off.font   # identical geometry
    on_y = _label_yspan(prims, 0x95322)
    off_y = _label_yspan(prims, 0x9535f)
    assert on_y[0] == off_y[0], f"ON top {on_y} != OFF top {off_y}"


def test_same_pen_overlapping_labels_still_stack(monkeypatch) -> None:
    """The overlap-aware stacking must still fire for a GENUINE collision: two labels at one pen
    whose drawn extents overlap (here identical box + text, so both centre to the same x) must be
    stacked one below the other, not drawn on top of each other. Isolated synthetic records
    (same monkeypatch technique as the fill tests)."""
    from fireflymcf import layout
    data = load()
    a = layout.Placed(wtype=11, label="ABC", x0=100, y0=100, x1=260, y1=140,
                      depth=0, obj_index=1, record_offset=0x4000, font=14, align=0, colors=[])
    b = layout.Placed(wtype=11, label="ABC", x0=100, y0=100, x1=260, y1=140,
                      depth=0, obj_index=2, record_offset=0x4010, font=14, align=0, colors=[])
    monkeypatch.setattr(layout.Screen, "walk", lambda self, *a_, **k: [a, b])
    prims = render.screen_placements(data, root=0)[0].prims
    ya = _label_yspan(prims, 0x4000)
    yb = _label_yspan(prims, 0x4010)
    assert ya and yb, "both synthetic labels should draw"
    assert yb[0] >= ya[1], f"overlapping same-pen labels must stack: {ya} then {yb}"


@pytest.mark.parametrize("name", sorted(HOME_VCENTRE_LABELS))
def test_label_centres_vertically_in_its_taller_box(name: str) -> None:
    """The cardinal rule, applied to vertical placement (the twin of the horizontal
    centre-in-box test below): when a label's own decoded box is taller than its text block,
    the block is centred in the box height -- byte-derived from the record's geometry
    (`layout.Placed.y0/y1`) and the decoded font cell (`metrics.font_metrics`), never fitted
    to HOME.jpg. The Prim anchors at the line's cell TOP, `y0 + (box_h - block_h)//2` (each
    glyph then places itself within the cell by its own vertical bearing -- see
    `test_panel_text_glyphs_carry_per_glyph_vertical_bearing`). Non-vacuous: before the fix the
    block was pinned to the box top (`base_y = y0`), so `line0.y` equalled the top-anchored
    position; this asserts it moved strictly below that (by `centre_off`: 3px for the button
    captions, 16px for the battery ones)."""
    off = HOME_VCENTRE_LABELS[name]
    data = load()
    placed, prims = _home_placed_and_prims(data)
    label = next(p for p in placed if p.record_offset == off)
    size, block_h, box_h = _vcentre_geom(data, label)
    assert box_h > block_h, f"{name}: precondition -- box {box_h} should exceed block {block_h}"

    lines = [p for p in prims if p.src == f"file@{off:#x}" and p.kind == "text"]
    assert lines, f"{name}: no text prim emitted"
    line0 = lines[0]
    centre_off = (box_h - block_h) // 2
    expected = max(1, label.y0 + centre_off)
    top_anchored = max(1, label.y0)
    assert line0.y == expected, f"{name}: line0.y={line0.y}, expected centred {expected}"
    assert line0.y > top_anchored, (        # the non-vacuous half: it is centred, not top-pinned
        f"{name}: line0.y={line0.y} not below top-anchored {top_anchored} -- not centred")


@pytest.mark.parametrize("name", sorted(HOME_TOP_ANCHORED_LABELS))
def test_label_in_short_box_stays_top_anchored(name: str) -> None:
    """The guard on the vertical-centre fix: a label whose box is NOT taller than its text
    block (a header over its box, or the degenerate-box '%' units) must stay at its decoded
    pen, `base_y = y0`. This keeps the fix from dragging `test_tank_percent_units_sit_at_the_
    decoded_pen_on_one_line`'s '%' widgets (box h=0) off their row."""
    off = HOME_TOP_ANCHORED_LABELS[name]
    data = load()
    placed, prims = _home_placed_and_prims(data)
    label = next(p for p in placed if p.record_offset == off)
    size, block_h, box_h = _vcentre_geom(data, label)
    assert box_h <= block_h, f"{name}: precondition -- box {box_h} should not exceed block {block_h}"

    line0 = next(p for p in prims if p.src == f"file@{off:#x}" and p.kind == "text")
    assert line0.y == max(1, label.y0), (
        f"{name}: line0.y={line0.y} not top-anchored at y0={label.y0}")


@pytest.mark.parametrize("name", sorted(HOME_BUTTON_BOXES))
def test_button_label_centres_on_its_own_box_records_geometry(name: str) -> None:
    """The cardinal rule, applied to centring: the label's drawn `x0` must equal
    `box_centre - text_width // 2` computed from the BUTTON BOX record's own decoded
    geometry (`layout.Placed`, `HOME_BUTTON_BOXES[name]`) -- not merely trusted from the
    label's own declared box, which is architecturally a SEPARATE record (`CLAUDE.md`:
    draw record / touch region / visible box are three independent widgets that can move
    independently). For all six of these the label record happens to be authored with the
    identical box to its button (asserted below, not assumed) -- so this cross-checks two
    independently-decoded records agree, rather than silently relying on that coincidence.
    Non-vacuous: a stub that left the label at its raw (uncentred) `x0` fails this for every
    one of the six, since none of their text widths equal their box widths."""
    from fireflymcf import metrics
    data = load()
    box_off = HOME_BUTTON_BOXES[name]
    label_off = HOME_BUTTON_LABELS[name]
    placed, prims = _home_placed_and_prims(data)
    box = next(p for p in placed if p.record_offset == box_off)
    label = next(p for p in placed if p.record_offset == label_off)
    # the authored coincidence this test PINS rather than assumes (see docstring):
    assert (label.x0, label.x1) == (box.x0, box.x1)

    prim = next(p for p in prims if p.src == f"file@{label_off:#x}")
    line = label.label.split("\n")[0]                # multi-line labels draw one prim/line
    width = metrics.text_width(data, label.font, line)
    assert width, f"{name}'s label width did not resolve"
    box_centre = (box.x0 + box.x1) / 2
    expected_x0 = round(box_centre - width / 2)
    assert abs(prim.x - expected_x0) <= 1, (     # +/-1: integer-division rounding only
        f"{name}: drawn x={prim.x}, expected centred x≈{expected_x0} "
        f"(box centre {box_centre}, text width {width})")


def test_multiline_label_uses_decoded_vertical_align_below_the_glyph() -> None:
    """The HOME bulb captions ('Main\\nCeiling' etc., obj 905) are multi-line labels with a
    decoded v-align byte of 3 (bottom) -- FUN_002559A0 places the block at y1 - block_h + 1 inside
    the record's own box, which puts it BELOW the bulb glyph (obj 258, box top 144-204) instead of
    being shoved down by the glyph_bottom heuristic. Byte-derived from records.py R49 (+0x95
    v-align, +0x96 line-height), threaded through layout.Placed. Non-vacuous: the old glyph_bottom
    path drew 'Main' ~17px higher (at the glyph bottom, not the cell bottom)."""
    from fireflymcf import layout, metrics
    data = load()
    placed, prims = _home_placed_and_prims(data)
    lab = next(p for p in placed if (p.label or "") == "Main\nCeiling")
    assert lab.v_align == 3 and lab.line_height == 18          # bottom-aligned, decoded
    size = metrics.font_metrics(data, lab.font).height          # font 13 cell height
    block_h = (2 - 1) * lab.line_height + size
    expected_top = lab.y1 - block_h + 1                         # FUN_002559A0 bottom placement
    main = next(p for p in prims if p.src == f"file@{lab.record_offset:#x}"
                and p.kind == "text" and p.text.strip() == "Main")
    assert main.y == max(1, expected_top)                       # exactly the byte-derived pen
    assert main.y > 200                                         # sits low in the cell, below the glyph


def test_preview_values_inject_runtime_digits() -> None:
    """The `preview` block injects sample RUNTIME values (bus-delivered, open thread 5 -- not in
    the MCF) so a render looks live. Each is tagged src='preview' (never 'decoded'), placed at a
    byte-derived pen, with the widget's ROLE chosen by byte-derived signals: '%' + a gauge well ->
    tank_level; 'V' -> a voltage routed by the widget's value_slot; degree unit (0xb0) ->
    temperature; the top-right unit-less header widget -> clock. Non-vacuous: without a preview
    block none of these strings are drawn."""
    from fireflymcf import cli, layout, metrics
    data = load()
    root = cli._resolve_page_root(data, "home")
    sv = {5: 8, 37: 1, 108: 1, 110: 1, 112: 1, 6: 1, 113: 0}
    # HOME's one visible 'V' widget under Batteries=1 is the Chassis battery (slot 205, obj 440);
    # it is routed by that slot, not a shared `battery_voltage` (2026-08-16 voltage dig).
    preview = {"tank_level": 33, "chassis_voltage": 12.2, "temperature_c": 22, "time_24": "14:49"}
    placed = layout.Screen(data, match_table_off=layout.MATCH_TABLE_136).walk(root, slot_values=sv)

    def texts(prims, want):
        return [p for p in prims if p.kind == "text" and p.text.strip() == want]

    base = render.screen_placements(data, root, match_table_off=layout.MATCH_TABLE_136,
                                    slot_values=sv)[0].prims
    prims = render.screen_placements(data, root, match_table_off=layout.MATCH_TABLE_136,
                                     slot_values=sv, tank_level=0.33, preview=preview)[0].prims

    # none of the injected digits exist without a preview block
    for want in ("33", "12.2", "22", "14:49"):
        assert not texts(base, want), f"{want!r} drawn without preview (should be runtime-only)"

    tanks = texts(prims, "33")
    assert len(tanks) == 4 and all(p.src == "preview" for p in tanks)     # four tank %s
    assert texts(prims, "12.2") and texts(prims, "12.2")[0].src == "preview"   # battery
    assert texts(prims, "22") and texts(prims, "22")[0].src == "preview"       # temperature
    # injected digits are raised to pen_y - font_top (FUN_002557C0), like the units -- byte-derived,
    # not the raw pen (which rendered them too low). Check the chassis value against its decoded pen.
    bval = next(p for p in placed if p.wtype == 8 and p.obj_index == 440)
    btop = metrics.font_metrics(data, bval.font).top
    assert texts(prims, "12.2")[0].y == max(1, bval.y0 - btop)
    assert texts(prims, "°"), "degree unit drawn alongside the temperature"
    clock = texts(prims, "14:49")
    assert clock and clock[0].src == "preview"
    # the clock is identified byte-derived: the value widget on the time register (value_slot 226 ->
    # 0x200399DC), NOT by screen position. Confirm such a widget exists and the clock drew at its pen.
    clockw = next(p for p in placed if p.wtype == 8 and p.value_slot == render.CLOCK_SLOT)
    ctop = metrics.font_metrics(data, clockw.font).top
    assert clock[0].y == max(1, clockw.y0 - ctop)

    # the tank fill rects are drawn (33% height > 0)
    fills = [p for p in prims if p.kind in ("rect", "rrect") and 60 < p.y < 200 and p.x > 470]
    assert fills, "tank fill bars should render at 33%"


# --- Box borders / outlines (GUICODE Finding 28) --------------------------------------------
CLIMATE_BOX = 0x92b8d          # obj539 wt-3, hollow (transparent interior) -> outline only
WATER_PUMP_BOX = 0x966c9       # obj295 wt-33, fillA mode-7 idx160 (runtime-state; resting recolour)


def _make_rec(**kw):
    from fireflymcf import layout
    kw.setdefault("wtype", 4); kw.setdefault("label", None)
    kw.setdefault("x0", 10); kw.setdefault("y0", 10); kw.setdefault("x1", 60); kw.setdefault("y1", 60)
    kw.setdefault("depth", 0); kw.setdefault("obj_index", 0); kw.setdefault("record_offset", 0)
    return layout.Placed(**kw)


def test_mode2_is_white_only_as_a_border() -> None:
    """A colour field tagged mode 2 resolves to hardcoded white 0xFFFFFF (GUICODE Finding 28), but
    ONLY when drawn as a border/outline (`border=True`). In the general fill path mode 2 must NOT
    resolve (return None) -- many boxes carry a mode-2 fillA border with no interior, and resolving
    it as a fill floods them white (a real regression this guards). fillB (interior) is unaffected."""
    p = _make_rec(colors=[("fillA", 2, None), ("fillB", 7, 8)])
    assert render._decoded_fill(p, {}, roles=("fillA", "fillA2"), border=True) == ((255, 255, 255), "decoded")
    assert render._decoded_fill(p, {}, roles=("fillA", "fillA2")) == (None, "heuristic")   # fill path: no white


def test_tank_wells_have_a_white_byte_derived_rounded_outline() -> None:
    """Each tank well (wt-0x20) strokes its fillA border (block A, GUICODE Finding 28) as a thin
    ROUNDED outline: geom[4]/coord4 is the box CORNER RADIUS (the visible-box quad is
    [x, width, height, radius], MCF-FORMAT §6), not an edge width -- the earlier code mis-read
    coord4=5 as a 5px border, drawing a thick square outline. fillA is mode 2 = white, so the wells
    get a thin white rounded outline (matching HOME.jpg). Non-vacuous: the outline Prim must be
    present, white, outline-only, an rrect whose radius == coord4 and whose border is a thin 2px."""
    from fireflymcf import cli, layout
    data = load()
    root = cli._resolve_page_root(data, "home")
    placed = layout.Screen(data, match_table_off=layout.MATCH_TABLE_136).walk(root, slot_values={5: 8, 112: 1})
    prims = render.screen_placements(data, root, match_table_off=layout.MATCH_TABLE_136,
                                     slot_values={5: 8, 112: 1})[0].prims
    for name, (well_off, _lvl) in TANK_WELLS.items():
        well = next(p for p in placed if p.record_offset == well_off)
        outs = [p for p in prims if p.src == f"file@{well_off:#x}" and p.outline
                and p.color == (255, 255, 255)]
        assert outs, f"{name}: no white outline drawn"
        assert outs[0].kind == "rrect"                    # rounded, not a square rect
        assert outs[0].radius == well.coord4              # coord4 is the corner RADIUS (=5)
        assert outs[0].line_w == 2                         # a thin border, not the coord4 width


def test_climate_box_renders_as_a_hollow_outline() -> None:
    """The CLIMATE box (obj539 wt-3) has the transparent-interior flag (byte0 0x43 -> Placed.hollow),
    so the box primitive draws an OUTLINE only in fillA=idx15, no interior -- the quadrant shows
    through (matches the coach). Non-vacuous: an outline Prim exists for the box and there is NO
    filled (non-outline) rect/rrect for it."""
    from fireflymcf import cli, layout
    data = load()
    root = cli._resolve_page_root(data, "home")
    placed = layout.Screen(data, match_table_off=layout.MATCH_TABLE_136).walk(root, slot_values={5: 8})
    box = next(p for p in placed if p.record_offset == CLIMATE_BOX)
    assert box.wtype == 3 and box.hollow
    prims = render.screen_placements(data, root, match_table_off=layout.MATCH_TABLE_136,
                                     slot_values={5: 8})[0].prims
    box_prims = [p for p in prims if p.src == f"file@{CLIMATE_BOX:#x}" and p.kind in ("rect", "rrect")]
    assert any(p.outline for p in box_prims), "CLIMATE box should have an outline"
    assert not any(not p.outline for p in box_prims), "hollow box must not draw a filled interior"
    # a type-3 frame is SQUARE (FUN_00255e90 draws four straight borders, no arc) with a 1px border
    # width (state+0x6c10, init 1) -- not rounded. Both CLIMATE and the LIGHTS Interior/Exterior
    # frames are this type; the 1px width also collapses two abutting boxes' shared edge to one line.
    outline = next(p for p in box_prims if p.outline)
    assert outline.kind == "rect" and outline.line_w == 1


def test_climate_page_state_selected_fills_are_byte_derived() -> None:
    """The CLIMATE page's runtime-state-selected chrome fills are recovered from their GuiCode COPY
    sources (single str site, single source; theme.mode7_palette omits them as ambiguous): the header
    band (idx375) copies idx10 = (124,21,29) the nav-rail red on its active tab; the Cool mode cell
    (idx120) copies idx8 = (39,40,98); the Bath Fan option button box (idx144) copies idx15 =
    (65,81,163). All labelled color_src='resting' (a state pick, not a proven-invariant constant).
    Non-vacuous: each box must paint its cited colour, and Bath Fan appears only when enabled."""
    from fireflymcf import cli, layout
    data = load()
    root = cli._resolve_page_root(data, "climate")
    sv = {5: 8, 109: 1}                                       # WD floorplan; Bath Fan enabled (slot 109)
    prims = render.screen_placements(data, root, match_table_off=layout.MATCH_TABLE_136,
                                     slot_values=sv)[0].prims

    def box_at(off):
        return [p for p in prims if p.src == f"file@{off:#x}" and p.kind in ("rect", "rrect")]

    hdr = box_at(0x857d9)                                     # CLIMATE header band (idx375)
    assert any(p.color == (124, 21, 29) and p.color_src == "resting" for p in hdr)
    cool = box_at(0x99871)                                    # Cool mode cell (idx120)
    assert any(p.color == (39, 40, 98) and p.color_src == "resting" for p in cool)
    bathfan = box_at(0x97201)                                 # Bath Fan option button box (idx144)
    assert any(p.color == (65, 81, 163) and p.color_src == "resting" for p in bathfan)
    # Bath Fan is an option widget: with the option DISABLED (slot 109 = 0) the button is not placed
    off_prims = render.screen_placements(data, root, match_table_off=layout.MATCH_TABLE_136,
                                         slot_values={5: 8, 109: 0})[0].prims
    assert not [p for p in off_prims if p.src == "file@0x97201"]


def test_climate_mode6_glyphs_and_wrap_are_byte_derived() -> None:
    """More CLIMATE byte-derived rendering:
    * the empty mode-grid cells carry a MODE-6 fill (idx16) -> the GuiData sub-container palette's
      (39,40,98) dark blue, so the whole grid container renders (not just cells with text);
    * icon glyphs take their own mode-7 ink colour: the setpoint chevrons carry idx35 -> cyan
      (0,174,239); the header recirc icon carries idx375 -> the header red (124,21,29), so it is
      red-on-red = invisible (why the coach shows nothing there);
    * "Bath Fan" (92px) word-wraps to Bath / Fan inside its 80px button box."""
    import numpy as np
    from fireflymcf import cli, layout
    data = load()
    root = cli._resolve_page_root(data, "climate")
    prims = render.screen_placements(data, root, match_table_off=layout.MATCH_TABLE_136,
                                     slot_values={5: 8, 109: 1})[0].prims

    def glyph_mean(off):
        p = next(p for p in prims if p.src == f"file@{off:#x}" and p.kind == "glyph")
        a = np.array(p.image)
        rgb = a[a[..., 3] > 128][:, :3]
        return tuple(rgb.mean(0).astype(int))

    cell = [p for p in prims if p.src == "file@0x86cda" and p.kind in ("rect", "rrect")]
    assert any(p.color == (39, 40, 98) and p.color_src == "decoded" for p in cell)   # mode-6 idx16
    assert glyph_mean(0x86bc3) == (0, 174, 239)                                       # chevron cyan idx35
    assert glyph_mean(0x857e5) == (124, 21, 29)                                       # recirc red idx375
    # Bath Fan wrapped to two lines, no single-line "Bath Fan"
    texts = {p.text for p in prims if p.kind == "text"}
    assert "Bath" in texts and "Fan" in texts and "Bath Fan" not in texts


def test_options_selection_circles_are_byte_derived() -> None:
    """The OPTIONS-page selection dots are wt-0x10 circle shapes (GUICODE Finding 9), a pair per
    row: an outer white-outlined navy ring (fillB idx3 navy + fillA mode-2 white) and an inner disc
    (fillB) that is navy when the option is off, cyan (idx35) when its switch selects the 'checked'
    candidate. Byte-derived geometry: centre = (x0 + radius, y0) from the type-0x10 handler
    (0x2575f2), so the inner (r9) and outer (r12) are CONCENTRIC, not offset. Non-vacuous: the
    selected row's inner is cyan, an off row's is navy, and every outer ring carries a white stroke."""
    from fireflymcf import layout
    data = load()
    prims = render.screen_placements(data, 46, match_table_off=layout.MATCH_TABLE_136,
                                     slot_values={5: 8, 109: 1, 107: 0})[0].prims   # Bath Fan on, Heat Pump off
    circles = [p for p in prims if p.kind == "ellipse"]
    assert len(circles) >= 12                                        # 6 rows x (ring + disc)
    # every outer ring (r12) is a navy disc with a white stroke; inner discs (r9) have no stroke
    rings = [p for p in circles if p.w // 2 == 12]
    assert rings and all(p.color == (21, 24, 58) and p.stroke == (255, 255, 255) for p in rings)
    inners = [p for p in circles if p.w // 2 == 9]
    assert any(p.color == (0, 174, 239) for p in inners)             # >=1 selected (cyan)
    assert any(p.color == (21, 24, 58) for p in inners)              # >=1 off (navy)
    # concentric: for each inner there is a ring sharing its centre
    def centre(p):
        return (p.x + p.w // 2, p.y + p.h // 2)
    ring_centres = {centre(p) for p in rings}
    assert all(centre(p) in ring_centres for p in inners)


def test_water_pump_button_uses_the_resting_fill() -> None:
    """Water Pump / Tank Heater buttons (wt-33) fill in fillA = mode-7 idx160/455, runtime-state-
    selected indices the theme palette omits. The render supplies their RESTING constant
    (65,81,163) (GUICODE Finding 28; idx160 proven-from-code), labelled src/color_src to mark it a
    resting default, not the live state. Non-vacuous: the button draws a filled rrect at that colour."""
    from fireflymcf import cli, layout
    data = load()
    root = cli._resolve_page_root(data, "home")
    prims = render.screen_placements(data, root, match_table_off=layout.MATCH_TABLE_136,
                                     slot_values={5: 8, 112: 1})[0].prims
    wp = [p for p in prims if p.src == f"file@{WATER_PUMP_BOX:#x}" and p.kind == "rrect" and not p.outline]
    assert wp and wp[0].color == (65, 81, 163) and wp[0].color_src == "resting"


@pytest.mark.parametrize("page", ["home", "climate", "electrical", "settings", "lights"])
def test_no_solid_white_boxes_regression(page: str) -> None:
    """Guard against the mode-2-in-the-fill-path flood: no page should paint a large SOLID white
    box. mode 2 is white only as a border stroke; if it leaks into the fill path, every box with a
    mode-2 fillA border fills white (caught in review). Outlined white (the tank wells) is fine."""
    from fireflymcf import cli, layout
    data = load()
    root = cli._resolve_page_root(data, page)
    prims = render.screen_placements(data, root, match_table_off=layout.MATCH_TABLE_136,
                                     slot_values={5: 8, 112: 1})[0].prims
    flooded = [p for p in prims if p.kind in ("rect", "rrect") and p.color == (255, 255, 255)
               and not p.outline and p.w > 40 and p.h > 40]
    assert not flooded, f"{page}: {len(flooded)} solid white boxes (mode-2 leaked into the fill path)"


def test_ext_ret_buttons_paint_inherited_resting_fill() -> None:
    """EXT (0x97865) / RET (0x96c69) are wt-33 buttons whose fillA is mode-0 inherit; via the
    persistent colour slots (GUICODE Finding 29) they inherit their wrapper's fillA = mode-7
    idx131/133 -- runtime-state cells whose RESTING value copies idx15 = (65,81,163). So both paint
    that periwinkle-blue, src='resting' (matching HOME.jpg). Non-vacuous: without mode-0 inheritance
    they stay unpainted; the assert requires the resolved resting colour."""
    from fireflymcf import cli, layout
    data = load()
    root = cli._resolve_page_root(data, "home")
    prims = render.screen_placements(data, root, match_table_off=layout.MATCH_TABLE_136,
                                     slot_values={5: 8})[0].prims
    for off, name in ((0x97865, "EXT"), (0x96c69, "RET")):
        box = [p for p in prims if p.src == f"file@{off:#x}" and p.kind == "rrect" and not p.outline]
        assert box, f"{name} button not painted"
        assert box[0].color == (65, 81, 163) and box[0].color_src == "resting"


def test_label_horizontal_align_uses_the_r49_h_align_byte() -> None:
    """A label's horizontal alignment is its R49 h_align byte (+0x94; GUICODE Finding 26/30), NOT
    the header align byte. ELECTRICAL value captions ('Time at Start Volts', 'Start at', 'Stop at')
    are header-align 3 (right) but h_align 2 (centre), so they CENTRE in their box instead of
    right-justifying into the + button. Non-vacuous: using the header align draws them at the box
    right edge (x >= box centre + text/2); this asserts they sit at the centred x."""
    from fireflymcf import cli, layout, metrics
    data = load()
    root = cli._resolve_page_root(data, "electrical")
    placed = layout.Screen(data, match_table_off=layout.MATCH_TABLE_136).walk(root, slot_values={5: 8, 37: 1})
    prims = render.screen_placements(data, root, match_table_off=layout.MATCH_TABLE_136,
                                     slot_values={5: 8, 37: 1})[0].prims
    for text in ("Start at", "Time at", "Stop at"):
        lab = next(p for p in placed if (p.label or "").strip().split("\n")[0] == text and p.x0 < 560)
        assert lab.h_align == 2, f"{text!r} should be h_align 2 (centre)"
        drawn = next(p for p in prims if p.kind == "text" and p.text.strip() == text and 320 <= p.x <= 560)
        w = metrics.text_width(data, lab.font, text) or 0
        centre_x = lab.x0 + ((lab.x1 - lab.x0) - w) // 2
        assert abs(drawn.x - centre_x) <= 1, f"{text!r} not centred: x={drawn.x} vs {centre_x}"


def test_label_vertical_align_applies_to_single_line_labels() -> None:
    """v_align (+0x95) governs a label's vertical placement whether single OR multi-line (Finding
    30). ELECTRICAL 'Start at' is v_align 1 (top), so it sits at the TOP of its box -- above its
    value widget -- not centred onto it. Non-vacuous: box-centring it (the old single-line path)
    puts it near the box centre, on top of the value; this asserts it is at the box top."""
    from fireflymcf import cli, layout
    data = load()
    root = cli._resolve_page_root(data, "electrical")
    placed = layout.Screen(data, match_table_off=layout.MATCH_TABLE_136).walk(root, slot_values={5: 8, 37: 1})
    prims = render.screen_placements(data, root, match_table_off=layout.MATCH_TABLE_136,
                                     slot_values={5: 8, 37: 1})[0].prims
    lab = next(p for p in placed if (p.label or "").strip().split("\n")[0] == "Start at" and p.x0 < 560)
    assert lab.v_align == 1                                    # top
    drawn = next(p for p in prims if p.kind == "text" and p.text.strip() == "Start at")
    assert drawn.y < lab.y0 + (lab.y1 - lab.y0) // 2          # in the TOP half of its box


def test_value_adjuster_glyphs_center_vertically_in_their_box() -> None:
    """The value +/- adjuster glyphs (font-9 '-'/'+') centre vertically in their own tall button
    box (v_align 2), not in the font cell -- cell-centring left them in the top ~half of the 80px
    box (GUICODE Finding 30, glyph path). Byte-derived from the record's box + glyph height.
    Non-vacuous: cell-centring puts the glyph centre well above the box centre; this asserts the
    drawn glyph is vertically centred in [y0, y1]."""
    from fireflymcf import cli, layout
    data = load()
    root = cli._resolve_page_root(data, "electrical")
    placed = layout.Screen(data, match_table_off=layout.MATCH_TABLE_136).walk(root, slot_values={5: 8, 37: 1})
    prims = render.screen_placements(data, root, match_table_off=layout.MATCH_TABLE_136,
                                     slot_values={5: 8, 37: 1})[0].prims
    for off in (0x94e05, 0x94e3f):                       # top-row '-' and '+'
        box = next(p for p in placed if p.record_offset == off)
        g = next(p for p in prims if p.src == f"file@{off:#x}" and p.kind == "glyph")
        glyph_centre = g.y + g.image.height / 2
        box_centre = (box.y0 + box.y1) / 2
        assert abs(glyph_centre - box_centre) <= 2, (
            f"{off:#x}: glyph centre {glyph_centre} vs box centre {box_centre}")


def test_electrical_dynamic_caption_and_voltage_routing() -> None:
    """The ELECTRICAL run-time caption is a wtype-13 dynamic string (GUICODE Finding 31). The
    renderer draws it from a byte-derived, mode-selected `dynamic_text` map (tagged
    src='dynamic@slot269', distinct from a stored `file@` label), and routes each 'V' widget to
    its OWN preview key by value_slot -- House/Coach battery (209, the ELECTRICAL "Battery
    Voltage" box, ~54V on a Li coach) and the AGS Start (272) / Stop (274) setpoints. Non-vacuous:
    without the dynamic_text / voltage keys, none of these strings are drawn."""
    from fireflymcf import cli, layout
    data = load()
    root = cli._resolve_page_root(data, "electrical")
    sv = {5: 8, 37: 1, 108: 1, 110: 1, 112: 1, 6: 1, 113: 0}

    labels = render.aes_ags_labels(data)
    assert labels["AES"] == ("AES", "Max Engine\nRun Time")     # byte-derived, no hardcoded offset
    assert labels["AGS"] == ("AGS", "Max Gen\nRun Time")
    preview = {"coach_voltage": 54.0, "start_volts": 51.0, "stop_volts": 53.0,
               "dynamic_text": {render.AES_CAPTION_SLOT: labels["AES"][1],
                                render.AES_INDICATOR_SLOT: labels["AES"][0]}}

    def texts(prims, want):
        return [p for p in prims if p.kind == "text" and p.text.strip() == want]

    base = render.screen_placements(data, root, match_table_off=layout.MATCH_TABLE_136,
                                    slot_values=sv)[0].prims
    prims = render.screen_placements(data, root, match_table_off=layout.MATCH_TABLE_136,
                                     slot_values=sv, preview=preview)[0].prims

    # the dynamic caption draws only with the preview, and is tagged as mode-selected (not file@)
    assert not texts(base, "Max Engine"), "caption drawn without a dynamic_text preview"
    cap = texts(prims, "Max Engine")
    assert cap and cap[0].src == f"dynamic@slot{render.AES_CAPTION_SLOT}"
    assert texts(prims, "Run Time"), "second caption line missing"

    # each 'V' widget routes to its own value; none appear without the preview
    for v in ("54.0", "51.0", "53.0"):
        assert not texts(base, v), f"{v} drawn without preview"
        assert texts(prims, v) and texts(prims, v)[0].src == "preview", f"{v} not routed"


def test_slot209_voltage_is_page_selected_home_12v_electrical_48v() -> None:
    """HOME "House" (obj 443) and the ELECTRICAL "Battery Voltage" box (obj 829) bind the SAME
    register (slot 209, 0x2003A594) -- byte-certain, identical record structure -- but show
    different physical batteries: HOME the 12V house (never 48V, even on a WD/Li coach),
    ELECTRICAL the 48V pack (owner-reported; GUICODE Finding 31 voltage note). Because a render is
    one page, cli selects slot 209's value by page. Here: HOME (Batteries=2) shows chassis 13.2 +
    house 13.4, both 12V and NEVER 54.0; ELECTRICAL shows 54.0. Non-vacuous: a stub that ignored
    the page swap would print 54.0 on HOME and fail the negative assertion."""
    from fireflymcf import cli, layout
    data = load()
    home = cli._resolve_page_root(data, "home")
    elec = cli._resolve_page_root(data, "electrical")
    prev = {"chassis_voltage": 13.2, "house_voltage": 13.4, "coach_voltage": 54.0}

    def texts(prims, want):
        return [p for p in prims if p.kind == "text" and p.text.strip() == want]

    # HOME with Batteries=2 so both 'V' widgets show; cli swaps slot 209 -> the 12V house_voltage.
    home_sv = {5: 8, 37: 1, 108: 1, 110: 1, 112: 1, 6: 2, 113: 0}
    home_prev = {**prev, "coach_voltage": prev["house_voltage"]}
    hp = render.screen_placements(data, home, match_table_off=layout.MATCH_TABLE_136,
                                  slot_values=home_sv, preview=home_prev)[0].prims
    assert texts(hp, "13.2") and texts(hp, "13.4"), "HOME should show chassis + house, both 12V"
    assert not texts(hp, "54.0"), "HOME must never show the 48V pack"

    # ELECTRICAL keeps slot 209 = coach_voltage (the 48V pack).
    elec_sv = {5: 8, 37: 1, 108: 1, 110: 1, 112: 1, 6: 1, 113: 0}
    ep = render.screen_placements(data, elec, match_table_off=layout.MATCH_TABLE_136,
                                  slot_values=elec_sv, preview=prev)[0].prims
    assert texts(ep, "54.0"), "ELECTRICAL Battery Voltage should show the 48V pack"


def test_merged_resting_fills_is_byte_derived_and_agrees_with_the_curated_list() -> None:
    """`_merged_resting_fills` is **every** index `theme.resting_defaults` derives, plus the
    curated `_RESTING_FILLS` entries the resolver cannot reach.

    Widened 2026-08-18: it used to intersect the derived palette with the curated index list, which
    left the FLOORPLAN SELECTION buttons (idx 24-34) unpainted purely because nobody had hand-listed
    them. The safety argument for dropping the intersection is asserted here rather than asserted in
    prose -- **every curated index the resolver can derive must match the curated value**, so the
    hand-entered list is confirmed by the bytes wherever the two overlap. Only idx455 (Tank Heater)
    and idx375 (CLIMATE header) are not derivable, and they keep their curated value."""
    from fireflymcf import theme
    data = load()
    merged = render._merged_resting_fills(data)
    derived = theme.resting_defaults(data)

    # Nothing derived is dropped EXCEPT the level gauges, which are deliberately held back so
    # `_battery_fill` stays their only reader (a gauge at its resting colour paints full-height,
    # i.e. a full battery, when no voltage is known -- see `render._GAUGE_INDICES`).
    assert set(derived) - set(merged) == set(render._GAUGE_INDICES) & set(derived)
    assert render._GAUGE_INDICES & set(merged) == set()
    overlap = set(render._RESTING_FILLS) & set(derived) - render._GAUGE_INDICES
    assert len(overlap) >= 10, "too little overlap to validate the curated list"
    for index in sorted(overlap):                         # the justification for dropping the scope
        assert merged[index] == render._RESTING_FILLS[index] == derived[index], (
            f"curated idx{index} disagrees with the byte-derived value")
    assert merged[145] == (39, 40, 98)                    # byte-derived navy, not (65,81,163)
    for index in (455, 375):                              # resolver-omitted -> curated kept
        assert index not in derived and merged[index] == render._RESTING_FILLS[index]


def test_floorplan_buttons_paint_their_inherited_resting_fill() -> None:
    """The ten FLOORPLAN SELECTION buttons are wt-33 boxes with fillA **mode 0 (inherit)** inside a
    child object; via the persistent parent-scoped colour slots (GUICODE Finding 29) each inherits
    its wt-5 wrapper's fillA = mode-7 idx 24..34 -- copy-fed runtime-state cells whose resting
    default is byte-derived (Finding 34). Ten copy from the (65,81,163) button-blue master; **Ship
    Mode (idx28) copies from the (0,174,239) cyan master**, i.e. the highlight colour is its resting
    value. This is the same shape as EXT/RET above; before 2026-08-18 these painted nothing because
    the resting palette was scoped to a hand-curated index list.

    Non-vacuous: it requires eleven painted boxes AND the cyan/blue split, so a regression that
    stops painting them, or one that paints them all one colour, fails."""
    from fireflymcf import cli
    data = load()
    root = cli._resolve_page_root(data, "floorplan")
    prims = render.screen_placements(data, root)[0].prims
    boxes = [p for p in prims
             if p.kind == "rrect" and not p.outline and p.w == 130 and p.h == 50
             and p.color_src == "resting"]
    assert len(boxes) == 11, f"expected 11 floorplan buttons, painted {len(boxes)}"
    colours = sorted(p.color for p in boxes)
    assert colours.count((65, 81, 163)) == 10, colours
    assert colours.count((0, 174, 239)) == 1, colours


def _floorplan_button_indices(data: bytes) -> dict:
    """`{floorplan code: mode-7 fillA index}` read out of GuiData: each FLOORPLAN SELECTION
    button is a wt-5 wrapper carrying the index and a child_index to the object holding the
    caption. Derived here rather than hardcoded so the test below is a real cross-check between
    GuiData's records and GuiCode's cascade."""
    from fireflymcf import cli, guidata, records
    gs, _ = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    out = {}
    for rec in records.object_records(data, gs + table[cli._resolve_page_root(data, "floorplan")]):
        if rec.wtype != 5 or rec.child_index is None:
            continue
        idx = next((v for role, mode, v in (rec.colors or [])
                    if role == "fillA" and mode == 7), None)
        if idx is None:
            continue
        labels = [r.label for r in records.object_records(data, gs + table[rec.child_index])
                  if getattr(r, "label", None)]
        if labels:
            out[labels[0].strip()] = idx
    return out


def test_floorplan_highlight_cascade_matches_the_floorplan_enum() -> None:
    """GUICODE Finding 36: GuiCode's cascade gives exactly ONE floorplan button the (0,174,239)
    cyan highlight per floorplan value, every other button the (65,81,163) blue.

    This is a three-way cross-check, and that is the point of it -- the value->button mapping is
    asserted between three sources that were derived independently:
      1. `options.floorplan_options` -- the positional enum from GuiData's floorplan objects,
      2. `_floorplan_button_indices` -- which fillA index each *captioned* button carries,
      3. `theme.selection_fills` -- which cell index GuiCode's `cmp <value>` highlights.
    A disagreement in any one of them fails here."""
    from fireflymcf import layout, options, theme
    data = load()
    register, _ = layout.variable_register(data, 5)
    by_value = theme.selection_fills(data, register)
    codes = options.floorplan_options(data)
    buttons = _floorplan_button_indices(data)

    assert set(codes) >= set(range(1, 11)), "expected floorplan values 1..10"
    for value in range(1, 11):
        fills = by_value[value]
        cyan = [i for i, c in fills.items() if c == (0, 174, 239)]
        assert len(cyan) == 1, f"value {value}: expected one highlight, got {cyan}"
        assert cyan[0] == buttons[codes[value]], (
            f"value {value} ({codes[value]}): GuiCode highlights idx{cyan[0]}, "
            f"but GuiData gives that button idx{buttons[codes[value]]}")
        others = [c for i, c in fills.items() if i != cyan[0]]
        assert others and set(others) == {(65, 81, 163)}, others


def test_floorplan_value_zero_highlights_ship_mode_and_11_to_14_highlight_nothing() -> None:
    """Value 0 is the boot state: `Ship Mode` -- which is why the page renders with Ship Mode lit
    until a floorplan arrives over CAN. Values 11-14 (`4NJ`/`3NG`/`3NK`/`3NJ`, the codes with
    objects but no button on the screen) highlight **nothing**, an independent confirmation from
    GuiCode that those four are unreachable (MCF-FORMAT §7)."""
    from fireflymcf import layout, theme
    data = load()
    register, _ = layout.variable_register(data, 5)
    by_value = theme.selection_fills(data, register)
    buttons = _floorplan_button_indices(data)

    cyan0 = [i for i, c in by_value[0].items() if c == (0, 174, 239)]
    assert cyan0 == [buttons["Ship Mode"]]
    for value in range(11, 15):
        assert not [i for i, c in by_value[value].items() if c == (0, 174, 239)], value


def test_render_highlights_the_selected_floorplan_button() -> None:
    """End to end: at floorplan 8 the WD button paints cyan and the other ten paint blue. Guards
    the wiring (`_selection_overrides`), not just the extractor -- a render that ignored the
    floorplan value would leave Ship Mode lit and fail."""
    from fireflymcf import cli
    data = load()
    root = cli._resolve_page_root(data, "floorplan")
    prims = render.screen_placements(data, root, slot_values={5: 8})[0].prims
    boxes = [p for p in prims if p.kind == "rrect" and not p.outline
             and p.w == 130 and p.h == 50 and p.color_src == "resting"]
    assert len(boxes) == 11
    cyan = [p for p in boxes if p.color == (0, 174, 239)]
    assert len(cyan) == 1, [p.color for p in boxes]
    # and it is the WD button: same drawn box as the WD caption
    wd = [p for p in prims if p.kind == "text" and p.text.strip() == "WD"]
    assert wd and abs(wd[0].y - cyan[0].y) < 50 and abs(wd[0].x - cyan[0].x) < 140


def test_mode0_fill_inherits_the_transparency_flag_not_just_the_colour() -> None:
    """GUICODE Finding 38: the mode-5 transparency bit is PERSISTENT render state, like the colour
    slot itself -- `orr r3,#2` at 0x253532 sets it, every explicit colour mode clears it with
    `bic r2,#2` (0x2534ee/0x253500/0x253514/0x253526/0x25354c), and mode 0 has no case so it
    touches neither. A mode-0 field after a mode-5 one is therefore still transparent.

    The ALTERNATOR 'CLEAR EEPROM' caption (obj 24 rec 20) is exactly that: fillB mode 0, following
    tab captions whose fillB is mode 5. Treating its mode-0 as 'inherit the colour and paint it'
    filled the caption's background with the inherited quadrant navy, painting OVER the olive
    button box beneath it (`ALTERNATOR-SYSTEM.jpg` shows olive with text on it, no navy).

    Non-vacuous: it requires the olive box to be the LAST thing drawn in that cell, so a
    regression that repaints over it fails."""
    from fireflymcf import theme
    data = load()
    olive = theme.mode7_palette(data)[40]
    prims = [pr for p in render.screen_placements(data, 24, slot_values={5: 8}) for pr in p.prims
             if pr.kind in ("rect", "rrect") and pr.x is not None
             and pr.x >= 540 and (pr.y or 0) > 400 and not pr.outline]
    assert prims, "CLEAR EEPROM button box not drawn"
    assert prims[-1].color == olive, [pr.color for pr in prims]
    assert len(prims) == 1, f"something is painted over the olive box: {[p.color for p in prims]}"


def test_wrap_budget_is_the_inclusive_box_width() -> None:
    """GUICODE Finding 39: `FUN_002559A0` computes its wrap budget as `x1 - x0 + 1` (0x255a7a-
    0x255a8e -- the box is INCLUSIVE of its last pixel) and breaks only when a run strictly
    EXCEEDS it (0x255b0c `cmp r0,r3` / `ble` = fits). So a line measuring exactly `box_w + 1`
    still fits on one line.

    That one pixel is the entire defect `ALTERNATOR-SYSTEM.jpg` exposed: 'Desired Charge State:'
    measures 161 px in a 160-wide box and 'I/C Firmware Rev.' 151 in a 150-wide box, and the coach
    draws each on ONE line. Labels that genuinely overflow must still wrap, which is what makes
    this a real off-by-one and not "never wrap": 'DEVICES/ FAULTS' is 148 against a 116 budget and
    'Bath Fan' 92 against 81."""
    data = load()
    got = {pr.text.strip() for p in render.screen_placements(data, 24, slot_values={5: 8})
           for pr in p.prims if pr.kind == "text" and (pr.text or "").strip()}
    assert "Desired Charge State:" in got, "1px over the box must still fit on one line"
    assert "Charging Temperature:" in got
    assert "DEVICES/" in got and "FAULTS" in got, "a genuine overflow must still wrap"
    assert "DEVICES/ FAULTS" not in got


def test_no_text_overlaps_on_the_alternator_and_inverter_screens() -> None:
    """The end-to-end assertion for both fixes above: no two drawn labels may overlap on the two
    screens the wrap heuristic used to break. Overlap is measured on the drawn extents, so it
    fails if either the wrap gate or the line advance regresses."""
    data = load()
    for root in (24, 17):
        boxes = []
        for p in render.screen_placements(data, root, slot_values={5: 8}):
            for pr in p.prims:
                if pr.kind == "text" and (pr.text or "").strip():
                    w = pr.w or 8 * len(pr.text)
                    h = pr.h or (pr.size or 18)
                    boxes.append((pr.x, pr.y, pr.x + w, pr.y + h, pr.text.strip()))
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                ix = min(a[2], b[2]) - max(a[0], b[0])
                iy = min(a[3], b[3]) - max(a[1], b[1])
                assert not (ix > 4 and iy > 6), f"obj {root}: {a[4]!r} overlaps {b[4]!r}"


def test_text_ink_resolves_every_colour_mode_not_just_inline() -> None:
    """A label's ink is its own fillA, and it decodes through the same mode table as any other
    colour field (GUICODE Finding 11): mode 3 inline BGR, mode 2 = white, mode 7 = a colour-table
    index, mode 6 = a GuiData-palette index; mode 0 arrives pre-substituted with the inherited slot
    (Finding 29).

    The renderer used to resolve mode 3 ONLY and default the rest to a hardcoded white. That was
    right for the ~1848 mode-2 labels by accident and wrong for the ~128 mode-7 ones -- red
    (idx 118) and cyan (idx 35) status glyphs all painted white. It also mislabelled every mode-2
    label `heuristic` when the byte says white outright.

    Asserts both halves: mode-2 labels are reported `decoded` (not `heuristic`), and a mode-7 ink
    whose index resolves is painted that colour rather than white."""
    from fireflymcf import guidata, records, theme
    data = load()
    palette = theme.mode7_palette(data)

    # (a) mode-2 ink is decoded white, not a heuristic guess
    prims = [pr for p in render.screen_placements(data, 24, slot_values={5: 8})
             for pr in p.prims if pr.kind == "text" and (pr.text or "").strip()]
    assert prims
    assert all(pr.color_src == "decoded" for pr in prims), \
        sorted({pr.color_src for pr in prims})
    assert all(pr.color == (255, 255, 255) for pr in prims)

    # (b) somewhere in the build, a mode-7 ink index that resolves must exist -- otherwise this
    # test would pass vacuously on a corpus where the fix cannot matter.
    gs, _ = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    resolvable = 0
    for off in table:
        try:
            recs = records.object_records(data, gs + off)
        except Exception:
            continue
        for rec in recs:
            if getattr(rec, "label", None) is None:
                continue
            for role, mode, value in (rec.colors or []):
                if role == "fillA" and mode == 7 and value in palette \
                        and palette[value] != (255, 255, 255):
                    resolvable += 1
    assert resolvable >= 20, f"expected mode-7 inks to exist; found {resolvable}"


def test_mode2_fill_a_box_is_a_solid_white_box_under_an_inset_child() -> None:
    """A box whose only colour is a mode-2 fillA paints SOLID WHITE, and the visible "frame" comes
    from LAYERING: the next record draws an inset child over it, leaving a white margin.

    ALTERNATOR's value boxes are the case. Obj 1377's wt-33 is (97,350)-(397,420) filled white; the
    wt-6 switch record right after it draws its child at (102,354)-(392,414), so ~5 px of white
    survives on every side -- which is the thick frame `ALTERNATOR-SYSTEM.jpg` shows.

    This started as a 2 px STROKE, and the owner reported the result as visibly too thin against
    the coach. That was the tell: the thickness was being chosen, not derived. Asserting the
    layering keeps it derived -- the margin is whatever the two decoded boxes say it is."""
    data = load()
    prims = [pr for p in render.screen_placements(data, 24, slot_values={5: 8, 38: 0, 39: 0})
             for pr in p.prims if pr.kind in ("rect", "rrect")]
    white = [pr for pr in prims
             if pr.color == (255, 255, 255) and not pr.outline and pr.w == 300 and pr.h == 70]
    assert len(white) == 2, [(pr.x, pr.y, pr.w, pr.h) for pr in white]
    for box in white:
        inner = [pr for pr in prims
                 if not pr.outline and pr.color != (255, 255, 255)
                 and pr.x > box.x and pr.y > box.y
                 and pr.x + pr.w < box.x + box.w and pr.y + pr.h < box.y + box.h]
        assert inner, f"nothing painted inside the white box at {(box.x, box.y)}"
        # the surviving white margin is the frame; it must be several px, not hairline
        margin = min(pr.x - box.x for pr in inner)
        assert margin >= 4, margin


def test_coord4_is_the_corner_radius_not_a_border_width() -> None:
    """Regression guard for a mid-course error. `width - 2*coord4` in the type-0x21 drawer
    (0x25749c) looks like an interior inset, i.e. a border width -- but the accompanying
    FUN_0025451A(x0+c4, y0+c4, c4, c4) is a corner ARC, so that span is the straight run BETWEEN
    two arcs, and `HOME.jpg`'s Water Pump / Tank Heater buttons (coord4 = 5) are visibly rounded.

    Asserts the HOME buttons keep a non-zero radius equal to their own coord4, so a future
    "coord4 is the border width" reading fails here instead of squaring every button."""
    from fireflymcf import cli
    data = load()
    root = cli._resolve_page_root(data, "home")
    boxes = [pr for p in render.screen_placements(data, root, slot_values={5: 8}) for pr in p.prims
             if pr.kind == "rrect" and pr.w == 104 and pr.h == 50 and not pr.outline]
    assert boxes, "HOME Water Pump / Tank Heater boxes not drawn"
    assert all(pr.radius == 5 for pr in boxes), [pr.radius for pr in boxes]


def test_alternator_fault_switch_selects_none_at_code_zero() -> None:
    """The Faults value is a type-6 value-match switch on slot 38 over 44 candidates -- the
    Wakespeed alternator fault-code table, byte-derived. Candidate 1306 carries match `0xFFFF`
    (the DEFAULT, an unknown code rendered as `<code> : Wakespeed Internal Error`), and 1307
    matches **0** with the label `None`.

    So the stray `:` in an earlier render was not a decode bug: with no value for slot 38 the walk
    fell to the default candidate. The coach reads `None`, i.e. slot 38 = 0. Non-vacuous: it
    requires the ':' candidate to be reachable too, so a switch that always returned one child
    would fail."""
    from fireflymcf import guidata, records
    data = load()
    gs, _ = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]

    def labels(obj):
        return [r.label for r in records.object_records(data, gs + table[obj])
                if getattr(r, "label", None)]

    assert labels(1307) == ["None"]
    assert labels(1306) == [":", "Wakespeed Internal Error"]

    texts = {pr.text.strip() for p in render.screen_placements(data, 24, slot_values={5: 8, 38: 0})
             for pr in p.prims if pr.kind == "text" and (pr.text or "").strip()}
    assert "None" in texts
    assert "Wakespeed Internal" not in texts and ":" not in texts


def test_go_power_tab_is_gated_on_the_controller_count() -> None:
    """The GO POWER! tab on NETWORK DIAGNOSTICS (obj 14), ALTERNATOR SYSTEM (24) and the
    diagnostics grid (35) is a type-6 value-match switch on **slot 39** with two candidates:
    obj 359 matches **0** and is EMPTY (tab hidden), obj 358 is the `0xFFFF` DEFAULT and carries
    the tab. So the tab shows for any value except exactly 0.

    Slot 39 is the count behind the OPTIONS row `'Go Power! Controllers:'` (obj 1236, a wt-8 value
    widget on that same slot), so the gate really is "controllers > 0". `ALTERNATOR-SYSTEM.jpg`
    shows the tab ABSENT on this coach, i.e. slot 39 = 0.

    Non-vacuous on both sides: it requires the tab to appear at a non-zero count, so a switch that
    always hid it would fail too."""
    from fireflymcf import guidata, records
    data = load()
    gs, _ = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    # the option row and the tab switch read the SAME slot -- that is the whole claim
    row = records.object_records(data, gs + table[1236])
    assert any(r.label == "Go Power! Controllers:" for r in row)
    assert 39 in {r.value_slot for r in row if r.value_slot is not None}

    for root in (14, 24, 35):
        def shown(count):
            sv = {5: 8} if count is None else {5: 8, 39: count}
            return "GO POWER!" in {pr.text.strip()
                                   for p in render.screen_placements(data, root, slot_values=sv)
                                   for pr in p.prims if pr.kind == "text" and (pr.text or "").strip()}
        assert not shown(0), f"obj {root}: 0 controllers must hide the tab"
        assert shown(2), f"obj {root}: 2 controllers must show the tab"


def test_go_power_count_above_five_is_clamped_to_one_not_zero() -> None:
    """A firmware quirk, `proven-from-code`, worth pinning because it defeats the gate above.

    `FUN_00247BF8`'s config-payload unpack takes the controller count from payload byte 0x0B and
    clamps it (0x247c48-0x247c52):

        ldrb r2,[r3,#0xb] ; cmp r2,#5 ; bls .keep ; movs r2,#1 ; .keep: strb r2,[0x2003a4e9]

    Any value ABOVE 5 -- including 0xFF, the usual "unset" sentinel -- becomes **1**, not 0. So an
    unconfigured or out-of-range count makes the GO POWER! tab appear even with no controllers
    fitted. Asserted here against the bytes so a future edit to that clamp is noticed."""
    from fireflymcf import theme
    data = load()
    section = theme.guicode_section(data)
    base = theme.guicode_base(section)
    off = section.offset + (0x247C48 - base)
    # ldrb r2,[r3,#0xb] ; cmp r2,#5 ; bls +2 ; movs r2,#1
    assert data[off:off + 8].hex() == "da7a052a00d90122", data[off:off + 8].hex()


def test_a_centred_line_keeps_its_trailing_pad(mcf_136) -> None:
    """The panel centres a line INCLUDING its trailing spaces. The render used to strip them.

    Owner-observed on the coach 2026-08-21: the `Overflow\\nValve` button's second line looks
    off-centre while its first does not, and `Water\\nPump` beside it looks right. The cause is
    not the newline -- Water Pump carries the same literal 0x0A -- it is that a same-size rename
    padded the 17-byte donor field on the RIGHT, so the pad landed inside line 2 (`'Valve   '`)
    and the panel centred the padded string, putting the visible word 9 px left of centre.

    `render.py` rstripped every line before measuring, so it drew the caption correctly centred
    and the preview disagreed with the panel -- which is how this shipped in v1 and v2. Stock
    object 8 has the same shape (`'Cleaning\\nMode '`, centre-aligned, one trailing space), so it
    pins the behaviour without depending on a build.

    CLAUDE.md's rule "padding goes on the right, trailing spaces are invisible" was derived from
    LEFT-aligned labels and is false for centred ones; 37 stock labels are in this state.
    """
    prims = render.screen_placements(mcf_136, 8)[0].prims
    mode = [p for p in prims if p.text.strip() == "Mode"]
    assert len(mode) == 1, "expected exactly one 'Mode' line in object 8"
    assert mode[0].text == "Mode ", "the trailing pad was stripped before measuring"
    # The record draws in font 15, where 'Mode' is 61 px and 'Mode ' is 69, so centring the
    # PADDED string moves the visible word 4 px left: it drew at 494 while the pad was being
    # stripped, and the panel draws it at 490.
    assert mode[0].x == 490, "a centred line must be placed by its PADDED width"


def test_a_line_of_only_spaces_still_counts_as_blank(mcf_136) -> None:
    """Preserving the pad must not turn a whitespace-only line into content.

    The blank-line handling is load-bearing (ELECTRICAL's '\\nStart at' must sit on row two), and
    it used to fall out of rstripping every line. Now that lines keep their spaces, `strip()` is
    what decides blankness -- this is the guard on that.
    """
    lines = render._label_lines("a\n   \nb")
    assert lines == ["a", "   ", "b"], lines
    assert render._label_lines("a\n   ") == ["a"], "a trailing whitespace-only line must go"
    assert render._label_lines("a\n\nb\n \n \n") == ["a", "", "b"], "only trailing blanks drop"


def test_floorplan_slot_is_derived_per_build_not_hardcoded(mcf_136, mcf_147, mcf_1123) -> None:
    """The FLOORPLAN SELECTION highlight cascade reads the floorplan register, and that
    register's value-slot is NOT a fixed 5: it is slot 5 on 13.6 but slot 10 on 14.7, so a
    hardcoded 5 read the wrong cell on 14.7 and the highlight never followed the picked
    floorplan there. `_floorplan_slot` resolves it from the same structural source
    `slot_values` uses to write it -- 11.23's name switch (objects 852/853) gives 7,
    hardware-confirmed (BUS-BASELINE PC.40), with the config-unpack derivation
    (`config.floorplan_slot`) as the agreeing fallback -- so the read key and the write
    key agree on every build."""
    assert render._floorplan_slot(mcf_136) == 5
    assert render._floorplan_slot(mcf_147) == 10
    assert render._floorplan_slot(mcf_1123) == 7


def test_decode_widget_uses_the_records_real_corner_radius_not_a_fixed_8() -> None:
    """The legacy layer-1 path now reads the byte-derived corner radius (GUICODE Finding 73).

    The Bath-Fan/Starlink button's 0x21 frame carries corner_radius 5 (record +0x32); decode_widget
    used to hardcode 8. Pins that it now matches the record and the decoded compositor.
    """
    data = load()
    frame = next(p for p in render.decode_widget(data, STARLINK_BUTTON) if p.kind == "rrect")
    assert frame.radius == records.decode_record(data, STARLINK_BUTTON + 1).corner_radius == 5
