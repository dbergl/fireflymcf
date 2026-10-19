"""`add_batt_heat_battery_glyph` with `style: "outline"` (owner, 2026-10-05; v47): while the
battery's internal heater CONTACTOR is closed, the House battery icons on HOME (object 1432, the
coach-48V clone) and on the compact ELECTRICAL frame (object 443) draw their terminals and a ring
round the body in orange. Replaces the glyph style's white glyph and 11 px raised number: the
number stays at its stock position in every state.

The switch mechanism is the glyph style's (coach-verified on v34a): each battery is ONE enrolled
wt6 switch on the Batt Heat state cell over four whole-battery arms. Only the contactor arms'
drawing differs: the first terminal's fillA becomes mode-3 orange (the second terminal and the
body inherit it), and a dark capsule in the terminal's stock mode-7 colour is laid
`OUTLINE_INSET` px inside the body, followed by an empty record that cancels its flow offset.

Exercises the op on the running v32 recipe, so every neighbouring op is present.
"""
import copy
import functools
import json

import pytest

from fireflymcf import aescompact, battglyph, cli, guidata, layout, recipe, records

STOCK = "firefly-13.6/mcf.bin"
V32 = "builds/mcf-13.6-batt-heater-status.ffproj.json"
BATTERIES = (443, 1432)          # ELECTRICAL (compact AES frame) and HOME (coach48v clone)
OUTLINE_OP = {"id": "battglyph", "type": "add_batt_heat_battery_glyph",
              "params": {"status": "status", "batteries": [{"object": 443}, {"ref": "coach48v"}],
                         "style": "outline"},
              "enabled": True}
ORANGE = battglyph.OUTLINE_RGB

# Body boxes of the two House batteries (wt-33 body record, inclusive), as in
# test_batt_heat_battery_glyph; the terminals sit in the 10 rows above the body.
BODY = {"home": (511, 276, 601, 326), "electrical": (305, 78, 395, 128)}
TERMINAL_ROWS = 10


def _project(with_outline: bool, **params):
    p = copy.deepcopy(json.load(open(V32)))
    if with_outline:
        op = copy.deepcopy(OUTLINE_OP)
        op["params"].update(params)
        p["ops"].append(op)
    return recipe.Project.from_json(json.dumps(p))


@functools.lru_cache(maxsize=None)
def _compile(with_outline: bool):
    stock = open(STOCK, "rb").read()
    res = recipe.compile_project(_project(with_outline), stock)
    assert res.ok, res.errors
    return res


def _build(with_outline: bool = True):
    return _compile(with_outline).data


def _recs(data, oi):
    gs, tab = guidata.guidata_extent(data)[0], guidata.object_tables(data)[1]
    return records.object_records(data, gs + tab[oi])


def _raw(data, recs):
    return [data[r.offset:r.offset + r.size] for r in recs]


def _state_slot(data):
    sw = [r for r in _recs(data, aescompact.ELECTRICAL_AES_BODY)
          if r.wtype == 6 and r.count == len(aescompact.BATT_HEAT_STATE_ARMS)
          and r.header[4] & 0x20]
    assert len(sw) == 1, sw
    return sw[0].value_slot


@functools.lru_cache(maxsize=None)
def _render(with_outline: bool, page: str, state: int):
    """Rendered through `cli render`, the path that feeds the voltage number from the config's
    preview block, with the Batt Heat state cell forced to `state`."""
    import tempfile
    from pathlib import Path
    from PIL import Image
    data = _build(with_outline)
    with tempfile.TemporaryDirectory() as td:
        img_path, out = Path(td) / "mcf-test.bin", Path(td) / "out.png"
        img_path.write_bytes(data)
        assert cli.main(["render", str(img_path), str(out), "--page", page,
                         "--config", "build/coach-wd.json",
                         "--slot", "%d=%d" % (_state_slot(data), state)]) == 0
        return Image.open(out).convert("RGB")


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


def test_open_contactor_arms_are_the_v32_battery_byte_for_byte():
    """No raised number in the outline style: an arm with the contactor open is exactly the
    battery v32 drew."""
    before, data = _build(False), _build(True)
    for oi in BATTERIES:
        stock = _raw(before, _recs(before, oi))
        sw = _recs(data, oi)[0]
        for k, (match, _heater, contactor) in enumerate(aescompact.BATT_HEAT_STATE_ARMS):
            if not contactor:
                assert _raw(data, _recs(data, sw.child_index + k)) == stock, (oi, match)


def test_closed_contactor_arms_recolour_the_terminal_and_add_the_inset():
    """With the contactor closed: the first terminal is mode-3 orange, the inset capsule follows
    the body in the terminal's stock mode-7 colour, an empty record cancels its offset, and every
    other record is the v32 battery's byte for byte."""
    before, data = _build(False), _build(True)
    rgb = (ORANGE[0] << 16) | (ORANGE[1] << 8) | ORANGE[2]
    for oi in BATTERIES:
        srecs = _recs(before, oi)
        stock = _raw(before, srecs)
        term_colour = next(c for c in srecs[0].colors if c[0] == "fillA")
        assert term_colour[1] == 7, term_colour
        body = srecs[2]
        sw = _recs(data, oi)[0]
        for k, (match, _heater, contactor) in enumerate(aescompact.BATT_HEAT_STATE_ARMS):
            if not contactor:
                continue
            arm = _recs(data, sw.child_index + k)
            assert [r.wtype for r in arm] == [33, 33, 33, 33, 11, 33, 6, 8, 0], (oi, match)
            assert ("fillA", 3, rgb) in arm[0].colors, (oi, match, arm[0].colors)
            inset = arm[3]
            i = battglyph.OUTLINE_INSET
            assert [layout._s16(g) for g in inset.geom[:4]] == \
                [i, i, body.geom[2] - 2 * i, body.geom[3] - 2 * i], (oi, inset.geom)
            assert term_colour in inset.colors, (oi, inset.colors)
            assert [layout._s16(g) for g in arm[4].geom[:2]] == [-i, -i]
            assert (arm[4].label or "") == ""
            raw = _raw(data, arm)
            assert raw[1:3] == stock[1:3] and raw[5:] == stock[3:], (oi, match)


def test_open_contactor_render_is_identical_to_v32():
    """Whole-render diff with the contactor open (states 0 and 1): nothing on either page moves."""
    for page in BODY:
        for state in (0, 1):
            a, b = _render(False, page, state), _render(True, page, state)
            assert list(a.getdata()) == list(b.getdata()), (page, state)


def test_closed_contactor_render_draws_an_orange_outline_and_nothing_else():
    """With the contactor closed (states 2 and 3): the body's outer two columns/rows and the
    terminals are orange, the inside of the ring is not, and no pixel outside the battery's
    footprint differs from v32."""
    for page, (x0, y0, x1, y1) in BODY.items():
        for state in (2, 3):
            a, b = _render(False, page, state), _render(True, page, state)
            ym, xm = (y0 + y1) // 2, (x0 + x1) // 2
            for x in (x0, x0 + 1, x1 - 1, x1):
                assert b.getpixel((x, ym)) == ORANGE, (page, state, x, b.getpixel((x, ym)))
            for y in (y0, y0 + 1, y1 - 1, y1):
                assert b.getpixel((xm, y)) == ORANGE, (page, state, y, b.getpixel((xm, y)))
            for x in (x0 + 3, x1 - 3):
                assert b.getpixel((x, ym)) != ORANGE, (page, state, x)
            terminal = [(x, y) for x in range(x0, x1 + 1) for y in range(y0 - TERMINAL_ROWS, y0)
                        if b.getpixel((x, y)) == ORANGE]
            assert len(terminal) > 100, (page, state, len(terminal))
            diffs = [(x, y) for x in range(a.width) for y in range(a.height)
                     if a.getpixel((x, y)) != b.getpixel((x, y))]
            assert diffs, (page, state)
            outside = [p for p in diffs if not (x0 <= p[0] <= x1
                                                and y0 - TERMINAL_ROWS <= p[1] <= y1)]
            assert not outside, "%s changed outside the battery: %s" % (page, outside[:10])


def test_outline_style_is_hardware_verified():
    """Coach-verified on v47: numbers at their stock position, orange outline seen with the heater
    running (reported 2026-10-10)."""
    assert "battglyph" not in _compile(True).unverified


@pytest.mark.parametrize("params", [{"style": "ring"}, {"outline_rgb": [255, 140]},
                                    {"outline_rgb": [256, 0, 0]}])
def test_bad_params_are_refused(params):
    res = recipe.compile_project(_project(True, **params), open(STOCK, "rb").read())
    assert not res.ok and res.errors[0]["code"] == "BAD_PARAMS", res.errors
