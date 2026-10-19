"""The `add_batt_heat_battery_glyph` recipe op: while the battery's internal heater CONTACTOR is
closed, the House battery icons on HOME (object 1432, the coach-48V clone) and on the compact
ELECTRICAL frame (object 443) carry a WHITE wavy-lines glyph in their bottom-right corner, and
their voltage number sits 11 px higher in every state so the glyph never touches it (owner,
2026-09-24, chosen from mockups).

Each battery is repainted whole by an enrolled wt5 container watching the voltage cell, so a glyph
laid on top as a sibling would be wiped on the next voltage tick. The op therefore rewrites each
battery object into ONE enrolled wt6 switch on the Batt Heat state cell whose four arms each draw
the WHOLE battery (the glyph arms add the glyph): a voltage repaint walks the switch and picks the
current arm, and a contactor change repaints the whole battery through the switch itself -- the
v32 one-switch rule applied to the battery.

Exercises the op on the running v32 recipe, so every neighbouring op is present.
"""
import copy
import functools
import json

from fireflymcf import aescompact, battglyph, cli, guidata, layout, recipe, records, \
    render

STOCK = "firefly-13.6/mcf.bin"
V32 = "builds/mcf-13.6-batt-heater-status.ffproj.json"
BATTERIES = (443, 1432)          # ELECTRICAL (compact AES frame) and HOME (coach48v clone)
GLYPH_OP = {"id": "battglyph", "type": "add_batt_heat_battery_glyph",
            "params": {"status": "status", "batteries": [{"object": 443}, {"ref": "coach48v"}]},
            "enabled": True}


def _project(with_glyph: bool):
    p = json.load(open(V32))
    p = copy.deepcopy(p)
    if with_glyph:
        p["ops"].append(dict(GLYPH_OP))
    return recipe.Project.from_json(json.dumps(p))


@functools.lru_cache(maxsize=None)
def _compile(with_glyph: bool):
    stock = open(STOCK, "rb").read()
    res = recipe.compile_project(_project(with_glyph), stock)
    assert res.ok, res.errors
    return res


def _build(with_glyph: bool = True):
    return _compile(with_glyph).data


def _recs(data, oi):
    gs, tab = guidata.guidata_extent(data)[0], guidata.object_tables(data)[1]
    return records.object_records(data, gs + tab[oi])


def _state_slot(data):
    """The Batt Heat state cell: the value_slot of the 4-arm switch on the compact AES frame."""
    sw = [r for r in _recs(data, aescompact.ELECTRICAL_AES_BODY)
          if r.wtype == 6 and r.count == len(aescompact.BATT_HEAT_STATE_ARMS)
          and r.header[4] & 0x20]
    assert len(sw) == 1, sw
    return sw[0].value_slot


def _render(data, page, state):
    """Render through `cli render`, the path that feeds the voltage number from the config's
    preview block -- the direct renderer call draws only the 'V', which would let the
    number-clearance checks below pass without ever seeing a digit."""
    import tempfile
    from pathlib import Path
    from PIL import Image
    with tempfile.TemporaryDirectory() as td:
        img_path, out = Path(td) / "mcf-test.bin", Path(td) / "out.png"
        img_path.write_bytes(data)
        assert cli.main(["render", str(img_path), str(out), "--page", page,
                         "--config", "build/coach-wd.json",
                         "--slot", "%d=%d" % (_state_slot(data), state)]) == 0
        return Image.open(out).convert("RGB")


# Body boxes of the two House batteries, from the v32 layout walk (wt-33 body record): HOME
# 511..601 x 276..326, ELECTRICAL 305..395 x 78..128 (inclusive).
BODY = {"home": (511, 276, 601, 326), "electrical": (305, 78, 395, 128)}


def _white_bbox(img, box):
    x0, y0, x1, y1 = box
    pts = [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)
           if min(img.getpixel((x, y))) > 200]
    if not pts:
        return None
    return (min(p[0] for p in pts), min(p[1] for p in pts),
            max(p[0] for p in pts), max(p[1] for p in pts))


def test_each_battery_is_one_enrolled_switch_on_the_state_cell():
    data = _build()
    slot = _state_slot(data)
    for oi in BATTERIES:
        recs = _recs(data, oi)
        assert len(recs) == 1, "object %d has %d records, expected the one switch" % (oi, len(recs))
        sw = recs[0]
        assert sw.wtype == 6 and sw.value_slot == slot, (oi, sw.wtype, sw.value_slot)
        assert sw.count == len(aescompact.BATT_HEAT_STATE_ARMS)
        assert sw.header[4] & 0x20, "battery %d switch not enrolled for live redraw" % oi


def test_containers_untouched_and_still_watch_the_voltage():
    """The five enrolled containers keep pointing at the battery objects and watching cell 209:
    the voltage repaint path v25 proved is unchanged."""
    before, after = _build(False), _build(True)
    for data in (before, after):
        gs, tab = guidata.guidata_extent(data)[0], guidata.object_tables(data)[1]
        found = sorted((oi, r.child_index) for oi in range(len(tab))
                       for r in records.object_records(data, gs + tab[oi])
                       if r.wtype == 5 and r.child_index in BATTERIES
                       and r.header[4] & 0x20 and r.value_slot == 209)
        assert found == [(829, 443), (830, 443), (901, 443), (902, 1432), (903, 443)], found


def test_every_arm_draws_the_whole_battery_with_the_number_raised():
    """THE GUARD for the repaint rule: every arm carries all seven battery records (so selecting
    any arm repaints the whole icon over whatever the last one left), byte-identical to the v32
    battery except the number's y, raised by NUMBER_RAISE. Exactly the contactor arms add the
    glyph (plus its flow-cancelling empty record)."""
    before, data = _build(False), _build(True)
    for oi in BATTERIES:
        stock_recs = _recs(before, oi)
        stock_bytes = [before[r.offset:r.offset + r.size] for r in stock_recs]
        sw = _recs(data, oi)[0]
        for k, (match, _heater, contactor) in enumerate(aescompact.BATT_HEAT_STATE_ARMS):
            arm = _recs(data, sw.child_index + k)
            battery = [r for r in arm if not (r.wtype == 11)]
            assert [r.wtype for r in battery] == [r.wtype for r in stock_recs], (oi, match)
            for r, sb, sr in zip(battery, stock_bytes, stock_recs):
                got = data[r.offset:r.offset + r.size]
                if r.wtype == 8:
                    assert layout._s16(r.geom[1]) == layout._s16(sr.geom[1]) - \
                        battglyph.NUMBER_RAISE, (oi, match, r.geom[1], sr.geom[1])
                    assert r.value_slot == sr.value_slot
                else:
                    assert got == sb, (oi, match, r.wtype)
            glyphs = [r for r in arm if r.wtype == 11 and r.label == battglyph.GLYPH_CHAR
                      and r.font == battglyph.GLYPH_FONT_SLOT]
            assert len(glyphs) == (1 if contactor else 0), (oi, match)


def test_glyph_is_white():
    data = _build()
    for oi in BATTERIES:
        sw = _recs(data, oi)[0]
        for k, (_m, _h, contactor) in enumerate(aescompact.BATT_HEAT_STATE_ARMS):
            for r in _recs(data, sw.child_index + k):
                if r.wtype == 11 and r.label == battglyph.GLYPH_CHAR:
                    color, _src = render._glyph_color(r, {})
                    assert color == (255, 255, 255), color


def test_render_glyph_bottom_right_and_clear_of_the_number():
    """Rendered from the compiled image: with the contactor closed (states 2 and 3) a white glyph
    sits in the body's bottom-right corner, 3 px in from the right and bottom edges, and below the
    raised number; with it open (0 and 1) the corner is clear."""
    data = _build()
    for page, (x0, y0, x1, y1) in BODY.items():
        for state in (0, 1, 2, 3):
            img = _render(data, page, state)
            corner = (x1 - 30, y1 - 26, x1, y1)
            wb = _white_bbox(img, corner)
            if state & 2:
                assert wb is not None, (page, state)
                gx0, gy0, gx1, gy1 = wb
                assert x1 - gx1 == battglyph.GLYPH_MARGIN, (page, state, wb)
                assert y1 - gy1 == battglyph.GLYPH_MARGIN, (page, state, wb)
                assert 18 <= gx1 - gx0 + 1 <= 24 and 18 <= gy1 - gy0 + 1 <= 24, (page, wb)
            num = _white_bbox(img, (x0, y0, x1, y1 - 26))
            assert num is not None and num[2] - num[0] > 40, \
                "no voltage digits drawn on %s (white bbox %s): the check would be vacuous" % (
                    page, num)
            if state & 2:
                assert num[3] < gy0 - 1, "number %s touches glyph %s on %s" % (num, wb, page)
            else:
                assert _white_bbox(img, (num[0], num[3] + 2, x1, y1)) is None, \
                    "white pixels below the number on %s with the contactor open" % page


def test_number_moves_up_by_the_raise_and_nothing_else_moves():
    """Whole-render diff against v32 with the contactor open: the only change on either page is
    the number moving up NUMBER_RAISE px inside its own battery body."""
    before, after = _build(False), _build(True)
    for page, (x0, y0, x1, y1) in BODY.items():
        a, b = _render(before, page, 0), _render(after, page, 0)
        na, nb = _white_bbox(a, (x0, y0, x1, y1)), _white_bbox(b, (x0, y0, x1, y1))
        assert (na[0], na[2]) == (nb[0], nb[2]), (page, na, nb)
        assert na[1] - nb[1] == battglyph.NUMBER_RAISE, (page, na, nb)
        diffs = [(x, y) for x in range(a.width) for y in range(a.height)
                 if a.getpixel((x, y)) != b.getpixel((x, y))]
        outside = [p for p in diffs if not (x0 <= p[0] <= x1 and y0 <= p[1] <= y1)]
        assert not outside, "%s changed outside the battery body: %s" % (page, outside[:10])


def test_op_is_hardware_verified():
    """Coach-verified 2026-09-26 on v34a, which carries v33's recipe."""
    assert "battglyph" not in _compile(True).unverified
