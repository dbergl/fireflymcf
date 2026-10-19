"""render_screen_image (bytes in, PIL image out) must be pixel-identical to the
path-based render_screen it was extracted from — the refactor guard."""
import io

import pytest

from fireflymcf import cli, render

STOCK_PATH = "firefly-13.6/mcf.bin"


def test_bytes_render_matches_path_render(tmp_path):
    with open(STOCK_PATH, "rb") as f:
        data = f.read()
    root = cli._resolve_page_root(data, "home")

    out = tmp_path / "home-path.png"
    render.render_screen(STOCK_PATH, str(out), root)

    im = render.render_screen_image(data, root)
    buf = io.BytesIO()
    im.save(buf, format="PNG")

    from PIL import Image
    a = Image.open(str(out)).convert("RGB")
    b = Image.open(io.BytesIO(buf.getvalue())).convert("RGB")
    assert a.size == b.size == (800, 480)
    assert list(a.getdata()) == list(b.getdata()), "refactor changed pixels"


def test_render_screen_image_is_keyword_only():
    """match_table_off and every param after root must be keyword-only -- later
    tasks (the Studio server) call this by keyword, so a positional third arg
    must be rejected rather than silently binding to match_table_off."""
    with open(STOCK_PATH, "rb") as f:
        data = f.read()
    root = cli._resolve_page_root(data, "home")

    with pytest.raises(TypeError):
        render.render_screen_image(data, root, None)


def test_render_screen_image_accepts_kwarg():
    """A non-default kwarg (show_touch) must work through the bytes-based entry
    point, not just the defaults path exercised by the pixel-identity test."""
    with open(STOCK_PATH, "rb") as f:
        data = f.read()
    root = cli._resolve_page_root(data, "home")

    im = render.render_screen_image(data, root, show_touch=True)
    assert im.size == (800, 480)


# ---------------------------------------------------------------------------------------------
# Boxless wtype-11/13 text: the shape v20 shipped, which the renderer used to draw in full
# ---------------------------------------------------------------------------------------------

def _v20_shaped_mark(data, *, dx=80, dy=26, char="A", font=13):
    """Object 295 with a corner mark built the v20 way: a wtype-11 label with coordinates 0 and 1
    only and NO declared width -- `aescompact._flow_anchor`'s shape, which is for records that
    never draw. The coach drew a single column of the glyph; the renderer drew it whole."""
    import struct
    from fireflymcf import cornermark, guidata, insert, records, relocate

    raw = char.encode("latin-1")
    boxless = (bytes([0x0B, 0x55, 0x03, 0x00, 0x00, 0x00, 0x00, 0x00])
               + struct.pack("<hh", dx, dy)
               + bytes([0x01, font, 0xFF, 0x02, 0x02, 0x0A, 0x00])
               + bytes([len(raw)]) + raw + b"\x00")
    assert records.decode_record(boxless, 0).geom[2] is None, "this fixture must be boxless"

    def obj(parts):
        payload = bytes([len(parts)]) + b"".join(parts)
        return payload + b"\x00" * (-len(payload) % 4)

    out, _ = relocate.grow_match_table(data, cornermark.MATCH_VALUES)
    out, _, base = insert._append_object(out, obj([boxless, cornermark.build_empty_record(-dx, -dy)]))
    out, _, _ = insert._append_object(out, obj([cornermark.build_empty_record()]))
    gs, _ = guidata.guidata_extent(out)
    tab = guidata.object_tables(out)[1]
    recs = records.object_records(out, gs + tab[295])
    stream = [out[r.offset:r.offset + r.size] for r in recs]
    stream.append(cornermark.build_switch_record(217, base))
    out, _ = relocate.replace_object_records(out, 295, obj(stream))
    return out, base


def test_a_boxless_record_draws_nothing(mcf_136):
    """v20's mark, previewed the way the coach actually showed it: not at all.

    `FUN_002559A0` clips its text to `box ∩ enclosing clip` and the rectangle is INCLUSIVE, so a
    record whose box collapsed to `x0 == x1, y0 == y1` gets a 1x1 clip -- its glyph's top-left
    pixel and nothing else. For the timer glyph that pixel has coverage 0, so nothing is drawn.

    This was read wrong twice. The owner reported "one column of pixels" and that was taken as
    the mark surviving a horizontal-only clip; measuring the photo showed the ink is at panel
    y 204-212, inside `'Water'`'s own row band (201-214), with none at all at x 573-594. It was
    the caption's `r` bleeding at photo scale.
    """
    from fireflymcf import render

    data, base = _v20_shaped_mark(mcf_136, char="A")
    slots = {5: 8, 6: 1, 37: 3, 112: 1, 113: 0, 217: 900}
    marks = [q for pl in render.screen_placements(data, 4, slot_values=slots)
             for q in pl.prims if q.text == "A" and q.kind in ("glyph", "text")]
    assert len(marks) == 1, f"expected the mark once, got {len(marks)}"
    cx0, cy0, cx1, cy1 = marks[0].clip
    assert (cx0, cy0) == (cx1, cy1), f"a boxless record must clip to 1x1, got {marks[0].clip}"

    img = render.rasterize(render.screen_placements(data, 4, slot_values=slots))
    plain = render.rasterize(render.screen_placements(mcf_136, 4, slot_values=slots))
    changed = [(x, y) for y in range(190, 250) for x in range(560, 600)
               if img.getpixel((x, y)) != plain.getpixel((x, y))]
    assert not changed, f"the boxless mark drew {len(changed)} pixels; the coach showed none"


def test_a_boxed_mark_IS_drawn(mcf_136):
    """The contrast: the same mark WITH a box renders in full, which is what v21 ships.

    Uses `A`, a character font 13 already carries, because this fixture builds on STOCK where
    v21's char 141 is still the shared placeholder."""
    from fireflymcf import cornermark, render

    data, info = cornermark.add_mark(mcf_136, object_index=295, value_slot=217, dx=80, dy=26,
                                     w=20, h=21, font_slot=13, char="A")
    slots = {5: 8, 6: 1, 37: 3, 112: 1, 113: 0, 217: 900}
    marks = [q for pl in render.screen_placements(data, 4, slot_values=slots)
             for q in pl.prims if q.text == "A" and q.kind in ("glyph", "text")]
    assert len(marks) == 1
    cx0, _cy0, cx1, _cy1 = marks[0].clip
    assert cx1 - cx0 >= (marks[0].image.width if marks[0].image else 0) - 1, \
        "a boxed mark must not be clipped"


def test_the_clip_covers_both_axes(mcf_136):
    """Both bounds are applied. The vertical one was held back twice -- once for a 2 px error of
    ours (`_effective_line_height`, fixed) and once for a misread of the coach photo (see
    `test_a_boxless_record_draws_nothing`) -- and neither reason survived."""
    from fireflymcf import cli, render

    root = cli._resolve_page_root(mcf_136, "options")
    clips = [q.clip for pl in render.screen_placements(mcf_136, root) for q in pl.prims if q.clip]
    assert clips, "wtype-11/13 text must carry a clip"
    assert all(None not in c for c in clips), "both axes must be bounded"


def test_the_line_advance_uses_the_records_own_byte(mcf_136):
    """GUICODE Finding 37's rule, which shipped implemented and DEAD.

    `_effective_line_height` referenced a `metrics` that `render` does not import at module
    scope, so it raised `NameError` on every call; a bare `except Exception` caught it and
    returned `line_height or None`, and since all 126 adds-font records carry `line_height = 0`
    that is always `None` -- the caller fell through to its `size + 2` heuristic every time.

    13.6's `Go Power!\nSettings`: line-height byte 0 with the adds-font flag, font 13 whose
    descriptor height is 21, so the advance is 21 and the second line pens at 441. The heuristic
    gave 23 and penned it at 443.
    """
    from fireflymcf import cli, layout, render

    p = next(q for q in layout.Screen(mcf_136).walk(cli._resolve_page_root(mcf_136, "options"))
             if q.label == "Go Power!\nSettings")
    assert (p.line_height, p.line_height_adds_font, p.font) == (0, 1, 13)
    assert render._effective_line_height(mcf_136, p, None) == 21, \
        "the rule is dead again -- check for a swallowed exception"

    pens = [pl.y + q.y for pl in render.screen_placements(mcf_136, 46) for q in pl.prims
            if q.kind == "text" and q.text in ("Go Power!", "Settings")]
    assert sorted(pens) == [420, 441], f"line pens {sorted(pens)}, expected [420, 441]"


def test_stock_pages_are_unaffected_by_the_clip(mcf_136):
    """The clip must be a no-op on every page we have a photo oracle for."""
    import hashlib
    from fireflymcf import cli, render

    for name in cli._PAGE_NAMES:
        root = cli._resolve_page_root(mcf_136, name)
        img = render.render_screen_image(mcf_136, root)
        assert hashlib.sha256(img.tobytes()).digest(), name        # renders at all
        for pl in render.screen_placements(mcf_136, root):
            for q in pl.prims:
                if q.clip and q.glyph_images:
                    cx0, _, cx1, _ = q.clip
                    w = max(dx + im.width for dx, dy, im in q.glyph_images)
                    assert pl.x + q.x >= cx0 - 1 and pl.x + q.x + w <= cx1 + 2, \
                        f"{name}: {q.text!r} would be clipped at {q.clip}"


CORPUS = ("firefly-11.23/mcf.bin", "firefly-13.6/mcf.bin", "firefly-14.7 (Terrain)/mcf.bin")

# GUICODE Finding 70. Only these two widget types can narrow the drawer's clip rectangle:
# FUN_00256C54 cases 7 and 0x0E set it to their own placed box (record flag bit 0x200) at
# 0x257A0E / 0x257A7C, and they are the only record-driven writers of state+0x58..0x5E.
CLIP_NARROWING_WTYPES = (7, 14)


def test_no_record_in_the_corpus_can_narrow_the_enclosing_clip():
    """`render._text_clip` returns the record's OWN box, and that is exact rather than
    approximate only because nothing installs a tighter enclosing clip.

    The base clip is the whole panel — `FUN_00258C60` writes (0, 0, 799, 479) at GUI init and
    nothing else ever writes that rectangle — so `box n enclosing == box` for every text record
    here. This pins the fact the model rests on: a build that grows a wtype-7 or wtype-14 record
    (Firefly's or one of ours) would make the renderer silently too generous, and should fail
    here instead. `docs/RENDER.md`, `GUICODE.md` Finding 70.
    """
    from fireflymcf import guidata, records

    for path in CORPUS:
        with open(path, "rb") as f:
            data = f.read()
        gs, _ = guidata.guidata_extent(data)
        table = guidata.object_tables(data)[1]

        seen, total = set(), 0
        for off in table:
            for rec in records.object_records(data, gs + off):   # must not raise: full coverage
                seen.add(rec.wtype)
                total += 1

        assert total > 5000, f"{path}: only {total} records — the walk missed objects"
        assert not seen & set(CLIP_NARROWING_WTYPES), \
            f"{path}: grew a clip-installing record {sorted(seen & set(CLIP_NARROWING_WTYPES))}"


def test_the_clip_pin_can_fail():
    """The standing 'check your tests can fail' rule — the assertion above is a negative over a
    set, which is exactly the shape that passes vacuously when the walk finds nothing."""
    seen = {0, 5, 6, 11, 14}
    assert seen & set(CLIP_NARROWING_WTYPES) == {14}


# GUICODE Finding 71. Object 303's records 3-13 are eleven wtype-4 filled rectangles that stack
# into the upward triangle over the `Absorb`/`Float` divider on INVERTER / CHARGER SETTINGS.
TRIANGLE_RECORDS = range(0x96340, 0x963E0)


def test_the_absorb_float_triangle_draws(mcf_136):
    """A 1 px box is a box. `FUN_0025572C` is `for (y = y0; y <= y1; y++) hline(x0, x1, y)`, so a
    single-row record paints a single row; the box loop used to open with a `2 <` floor and
    dropped all eleven of these, which is why the render omitted a triangle the coach draws."""
    from fireflymcf import render

    rows = [(pl.x + q.x, pl.y + q.y, q.w, q.h, q.color, q.color_src)
            for pl in render.screen_placements(mcf_136, 17, slot_values={23: 1})
            for q in pl.prims
            if q.src and int(q.src.split("@")[1], 16) in TRIANGLE_RECORDS and q.h == 1]

    assert len(rows) == 11, f"expected 11 triangle rows, got {len(rows)}"
    assert [r[2] for r in rows] == [21, 19, 17, 15, 13, 11, 9, 7, 5, 3, 1], "not a taper"
    assert [r[1] for r in rows] == list(range(370, 359, -1)), "rows must climb one per bar"
    # the ten inheriting bars must resolve to the SAME decoded colour as the one that sets it --
    # the persistent fill register, not a heuristic (records 4-13 carry fillA mode 0).
    assert {r[4] for r in rows} == {(65, 81, 163)}, "the taper must be one inherited colour"
    assert {r[5] for r in rows} == {"decoded"}


def test_the_oracle_pages_are_untouched_by_the_one_pixel_boxes(mcf_136):
    """Relaxing the size floor must not move any page we hold a photo of. None of the eight
    contains a thin record at all, which is what makes that safe rather than lucky."""
    from fireflymcf import cli, layout

    scr = layout.Screen(mcf_136)
    for name in cli._PAGE_NAMES:
        thin = [p for p in scr.walk(cli._resolve_page_root(mcf_136, name))
                if 0 < abs(p.x1 - p.x0) <= 2 or 0 < abs(p.y1 - p.y0) <= 2]
        assert not [p for p in thin if abs(p.x1 - p.x0) and abs(p.y1 - p.y0)], \
            f"{name} gained a thin record -- re-check the oracle renders"


def test_wtype_2_records_draw_as_lines(mcf_136):
    """GUICODE Finding 72. `FUN_00254064` is a 1 px line: `x0 == x1` takes a vertical span,
    `y0 == y1` a horizontal one, both inclusive. Every one of these was silently dropped, because
    an axis-aligned line has a zero extent on one axis and the box loop's guard rejects that."""
    from fireflymcf import render

    def lines(root):
        return [(q.x, q.y, q.w, q.h, q.color, q.color_src)
                for pl in render.screen_placements(mcf_136, root)
                for q in pl.prims if q.kind == "line"]

    # FLOORPLAN SELECTION: the white rules under the two column headings.
    assert lines(19) == [(104, 88, 300, 0, (255, 255, 255), "decoded"),
                         (455, 88, 300, 0, (255, 255, 255), "decoded")]
    # SLIDES/AWNINGS: four 45-degree diagonals -- the levelling diamond joining FRONT/LEFT/
    # RIGHT/REAR. Signed deltas, so the sign carries the direction.
    diag = lines(32)
    assert len(diag) == 4 and all(abs(w) == abs(h) == 80 for _, _, w, h, _, _ in diag)
    assert {c for *_, c, _ in diag} == {(128, 130, 133)}

    assert all(src == "decoded" for *_, src in lines(27) + lines(28)), \
        "a line with no decodable colour must not be painted"


def test_no_wtype_2_record_needs_the_general_slope_walk():
    """The line primitive draws Bresenham, which matches the firmware only because every wtype-2
    record in the corpus is axis-aligned or exactly 45 degrees -- at 45 the firmware's fixed-point
    run-length walk reduces to one pixel per step. A record at any other slope would need that
    walk transcribed, so it must fail here rather than render approximately.

    The styled drawer (`FUN_00254350`, flag bit 2, parameterised by state+0x92) is unexercised for
    the same reason: header byte 0's style nibble is zero on every one.
    """
    from fireflymcf import guidata, records

    for path in CORPUS:
        with open(path, "rb") as f:
            data = f.read()
        gs, _ = guidata.guidata_extent(data)
        seen = 0
        for off in guidata.object_tables(data)[1]:
            for rec in records.object_records(data, gs + off):
                if rec.wtype != 2:
                    continue
                seen += 1
                w, h = rec.geom[2] or 0, rec.geom[3] or 0
                w = w - 0x10000 if w > 0x8000 else w
                h = h - 0x10000 if h > 0x8000 else h
                assert w == 0 or h == 0 or abs(w) == abs(h), \
                    f"{path}: wtype-2 record at {rec.offset:#x} is {w}x{h} -- a general slope"
                assert data[rec.offset] & 0x3C == 0, \
                    f"{path}: wtype-2 record at {rec.offset:#x} carries a style nibble"
        assert seen > 20, f"{path}: only {seen} wtype-2 records -- the walk missed some"


def test_the_image_records_draw(mcf_136):
    """`images.py` has decoded the eight GuiData bitmaps since 2026-08-29; nothing ever asked it
    for one during a render, so all four wtype-10 records drew nothing. Root 48 is the Terms of
    Use screen, whose QR code and Firefly logo are both blits."""
    from fireflymcf import render

    got = {root: sorted((q.x, q.y, q.w, q.h)
                        for pl in render.screen_placements(mcf_136, root)
                        for q in pl.prims if q.kind == "image")
           for root in (11, 23, 48)}
    assert got == {11: [(620, 260, 150, 150)],
                   23: [(529, 335, 125, 125)],
                   48: [(326, 195, 150, 150), (537, 397, 208, 61)]}


def test_an_image_records_box_is_one_pixel_short_of_its_bitmap(mcf_136):
    """The record declares `w - 1`, `h - 1`: the blitter's extent is `x + w - 1`
    (`FUN_002513A4` @ 0x2513B4), inclusive like every other box in this format. Pinning it stops
    a future reader 'fixing' the render to the declared box and losing a row and a column."""
    from fireflymcf import guidata, images, records

    gs, _ = guidata.guidata_extent(mcf_136)
    seen = 0
    for off in guidata.object_tables(mcf_136)[1]:
        for rec in records.object_records(mcf_136, gs + off):
            if rec.wtype not in (10, 18) or rec.child_index is None:
                continue
            if not (rec.geom[2] and rec.geom[3]):
                continue
            w, h = images.dimensions(mcf_136, rec.child_index)
            assert (rec.geom[2], rec.geom[3]) == (w - 1, h - 1)
            seen += 1
    assert seen >= 4, f"only {seen} sized image records -- the walk missed some"


def test_a_mode_6_border_resolves_on_a_type_3_box(mcf_136):
    """`_decoded_fill` handles mode 3 and mode 7 only. The wtype-3 branch called it and returned
    early, so a border in mode 6 (the GuiData palette) or mode 2 (white) fell through as None and
    the box drew nothing -- the grey frame around `SLIDES/AWNINGS`'s levelling controls, whose
    fillA is mode 6 index 3: the same grey as the diamond it encloses."""
    from fireflymcf import render

    frames = [(q.x, q.y, q.w, q.h, q.color) for pl in render.screen_placements(mcf_136, 32)
              for q in pl.prims if q.kind == "rect" and q.outline and q.w > 300]
    assert (350, 50, 420, 412, (128, 130, 133)) in frames
    diag = {q.color for pl in render.screen_placements(mcf_136, 32)
            for q in pl.prims if q.kind == "line"}
    assert diag == {(128, 130, 133)}, "frame and diamond must share the mode-6 grey"


def test_wtype_19_is_correctly_invisible():
    """The audit flags one wtype-19 record as drawing nothing, and the firmware agrees: case 0x13
    fills only `if ((flags & 3) >> 1) == 0` -- the transparent-interior bit `records` exposes as
    `hollow`, the same bit the wtype-3 branch already tests against the CLIMATE photo oracle.
    Every wtype-19 record in all three builds sets it, so none of them ever draws. A defensible
    negative, not a gap; this pins the premise so a build with a non-hollow one fails here."""
    from fireflymcf import guidata, records

    for path in CORPUS:
        with open(path, "rb") as f:
            data = f.read()
        gs, _ = guidata.guidata_extent(data)
        seen = 0
        for off in guidata.object_tables(data)[1]:
            for rec in records.object_records(data, gs + off):
                if rec.wtype != 19:
                    continue
                seen += 1
                assert data[rec.offset] & 0x02, \
                    f"{path}: wtype-19 record at {rec.offset:#x} is NOT hollow -- it draws"
        assert seen, f"{path}: no wtype-19 records found"


# The levelling arrow cells on SLIDES/AWNINGS (root 32, screen 29). Object 934 -- the FRONT control
# -- places its two arrow buttons (objects 286 and 236) through wt-5 containers whose fillA and
# value_slot name slots 424/425; each arrow's wtype-4 background is fillA mode 0 and inherits that
# colour. Nothing in either image writes those cells and the C runtime does not initialise them
# (`GUICODE.md` Finding 67, `LEVELING-PAGE.md` §3-4); on the coach they power up in arbitrary colours
# (`BUS-BASELINE.md` PC.57). So there is no colour to decode, and the render leaving the two fills
# unpainted is the policy, not a gap. `RENDER.md` once listed them as a decode to-do.
LEVELLING_ROOT = 32
LEVELLING_FRONT_OBJECT = 934
LEVELLING_ARROW_SLOTS = (424, 425)


def test_the_levelling_arrow_cells_have_no_colour_to_decode(mcf_136):
    from fireflymcf import guidata, records, render, slotsource, theme
    gs, _ = guidata.guidata_extent(mcf_136)
    table = guidata.object_tables(mcf_136)[1]
    recs = records.object_records(mcf_136, gs + table[LEVELLING_FRONT_OBJECT])
    containers = [r for r in recs if r.wtype == 5]
    assert [(r.colors[0], r.value_slot) for r in containers] == \
        [(("fillA", 7, slot), slot) for slot in LEVELLING_ARROW_SLOTS]

    never = {entry.slot for entry in slotsource.never_established(mcf_136)}
    assert set(LEVELLING_ARROW_SLOTS) <= never
    assert not set(LEVELLING_ARROW_SLOTS) & set(theme.mode7_palette(mcf_136))

    placed = {q.src for p in render.screen_placements(mcf_136, LEVELLING_ROOT) for q in p.prims}
    for container in containers:
        arrow = records.object_records(mcf_136, gs + table[container.child_index])
        fill = next(r for r in arrow if r.wtype == 4)
        glyph = next(r for r in arrow if r.wtype == 11)
        assert fill.colors[0] == ("fillA", 0, None)          # inherits the container's colour
        assert f"file@{fill.offset:#x}" not in placed
        # The arrow is reached and its glyph draws, so the fill is silent for its colour alone.
        assert f"file@{glyph.offset:#x}" in placed
