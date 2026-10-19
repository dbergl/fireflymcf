"""v45: Wheelbase and Track rows on the LEVEL SETUP popup (`add_level_page` `geometry`).

- the four steppers send level-sensor ops 0Bh-0Eh on 0x1EFBF: one frame per tap, and after a
  1-s hold one every 250 ms (the stock arrows' shape), nothing on release;
- two more receive entries read record 04h bytes 1-2 and 3-4 as u16;
- the popup grows to five rows inside the page area, its touches last, its readouts on the new
  cells with one decimal place;
- v45 is v44's recipe with `geometry` on, and rebuilds to the image handed over.
"""
import hashlib
import json

import pytest

from fireflymcf import guidata, layout, levelcontrols, levelfeed, levelsetup, newbutton
from fireflymcf import recipe, records
from fireflymcf.recipe_ops import _gs_tab

STOCK = "firefly-13.6/mcf.bin"
V44 = "builds/mcf-13.6-alt-reboot.ffproj.json"
V45 = "builds/mcf-13.6-level-geometry.ffproj.json"
V46 = "builds/mcf-13.6-hold-fix.ffproj.json"


@pytest.fixture(scope="module")
def built():
    res = recipe.compile_project(recipe.Project.from_json(open(V46).read()),
                                 open(STOCK, "rb").read())
    assert res.ok, res.errors
    return res


# ---- the controls ---------------------------------------------------------------------------

@pytest.mark.parametrize("name, op", sorted(levelcontrols.GEOMETRY_OPCODES.items()))
def test_a_geometry_stepper_sends_its_op_once_per_tap_and_repeats_while_held(name, op):
    stock = open(STOCK, "rb").read()
    entry = levelcontrols.group_entry(stock, name, 0)
    assert entry[:4] == bytes.fromhex("10000400"), "1-s hold, 250 ms held repeat, no second loop"
    frame = (0x19EFBF9F, bytes([0x4C, 0x56, op]) + b"\xff" * 5)
    p = levelcontrols.presses(entry, levelcontrols.bindings(name, levelcontrols.STEPPER_DGN_RVC))
    assert p["tap"] == [frame]
    # held at 1000 ms, then 1250 / 1500 / 1750 before a 2-s release; the release sends nothing
    assert p["held_2s"] == [frame] * 4
    assert p["held_3s"] == [frame] * 8


def test_the_other_setup_controls_still_send_once():
    stock = open(STOCK, "rb").read()
    for name in levelcontrols.SETUP_CONTROLS:
        entry = levelcontrols.group_entry(stock, name, 0)
        p = levelcontrols.presses(entry, levelcontrols.bindings(name))
        assert all(len(v) == 1 for v in p.values()), name


# ---- the receive side -----------------------------------------------------------------------

def test_geometry_reads_record_04h_bytes_1_and_3_as_u16():
    assert levelfeed.GEOMETRY_VARS == (("wb", 4, 1, 2), ("track", 4, 3, 2))
    assert levelfeed.vars_for(True) == levelfeed.VARS + levelfeed.SETUP_VARS
    assert levelfeed.vars_for(True, True)[-2:] == levelfeed.GEOMETRY_VARS


def test_geometry_needs_setup():
    proj = json.load(open(V45))
    lv = next(o for o in proj["ops"] if o["id"] == "level")
    lv["params"]["setup"] = False
    lv["params"]["repaint"] = lv["params"]["popup_background"] = False
    res = recipe.compile_project(recipe.Project.from_json(json.dumps(proj)),
                                 open(STOCK, "rb").read())
    assert not res.ok and "geometry needs setup" in json.dumps(res.errors)


# ---- the popup ------------------------------------------------------------------------------

def test_the_five_row_dialog_fits_the_page_and_no_touch_overlaps():
    x, y, w, h, _r = levelsetup.GEOMETRY_DIALOG
    assert y >= 37 and y + h <= 479, "the dialog must sit below the header, inside the page"
    rr = levelsetup.rects(True)
    names = list(rr)
    hit = lambda a, b: a[0] <= b[0] + b[2] and b[0] <= a[0] + a[2] and \
        a[1] <= b[1] + b[3] and b[1] <= a[1] + a[3]
    assert not [(a, b) for i, a in enumerate(names) for b in names[i + 1:] if hit(rr[a], rr[b])]
    assert all(x <= r[0] and r[0] + r[2] <= x + w and y <= r[1] and r[1] + r[3] <= y + h
               for r in rr.values())
    assert set(levelsetup.layout(True)["touch_names"]) == set(rr)


def test_the_three_row_layout_is_unchanged():
    lay = levelsetup.layout(False)
    assert lay["dialog"] == (150, 60, 580, 360, 10)
    assert lay["row_y"] == {"height": 118, "tol": 190, "carried": 262}
    assert lay["touch_names"] == levelsetup.TOUCH_NAMES


def test_v45_popup_carries_the_geometry_rows_and_their_groups(built):
    data = built.data
    gs, tab = _gs_tab(data)
    recs = records.object_records(data, gs + tab[levelsetup.POPUP_ROOT])
    touches = [r for r in recs if r.wtype == 12]
    assert len(touches) == 13 and recs[-13:] == touches
    rr = levelsetup.rects(True)
    ids = {}
    for name, r in zip(levelsetup.GEOMETRY_TOUCH_NAMES, touches):
        assert tuple(r.geom[:4]) == tuple(rr[name]), name
        ids[name] = data[r.end - 2] | (data[r.end - 1] << 8)
    gt, bt = guidata.group_table(data), guidata.binding_table(data)
    for name, op in levelcontrols.GEOMETRY_OPCODES.items():
        g = gt[ids[name]]
        assert g.count == 1 and g.raw[:4] == bytes.fromhex("10000400"), name
        raw = bt[g.start].raw
        assert newbutton.binding_wire_frame(raw) == \
            (0x19EFBF9F, bytes([0x4C, 0x56, op]) + b"\xff" * 5), name
        assert raw[3] == levelcontrols.STEPPER_MASK and raw[2] & 0x04, name
    labels = [p.label for p in layout.Screen(data).walk(levelsetup.POPUP_ROOT) if p.label]
    assert "Wheelbase" in labels and "Track" in labels


def test_v45_is_v44_with_geometry_on():
    v44, v45 = json.load(open(V44)), json.load(open(V45))
    assert v45["build_name"] == "Jayco B-Van V13 v45 Level Geometry"
    for a, b in zip(v44["ops"], v45["ops"]):
        if a["id"] == "level":
            assert b["params"] == dict(a["params"], geometry=True)
        else:
            assert a == b
    assert len(v44["ops"]) == len(v45["ops"])


def test_v46_is_v45_renamed_and_rebuilds_to_the_image_handed_over(built):
    """v45 (flashed 2026-09-30) put Track + on group 136 and the reboot on 137, which GuiCode
    drives; its recipe reproduces its image only at commit 2e5d91b. v46 is the same recipe under
    a new name, compiled with the allocator that avoids them."""
    v45, v46 = json.load(open(V45)), json.load(open(V46))
    assert v46["ops"] == v45["ops"] and v46["build_name"] == "Jayco B-Van V13 v46 Hold Fix"
    assert hashlib.sha256(built.data).hexdigest() == \
        "dd8d148cc4eab905da43601e3d1dbc3485405e2a7485873c824e484fa56d64ff"


def test_v46_hold_controls_sit_on_hold_safe_groups(built):
    """Every group v46 minted for a HOLD (the four geometry steppers, the reboot) is one
    `newbutton.hold_safe_groups` offered on stock: not GuiCode-driven, press-cycled."""
    stock = open(STOCK, "rb").read()
    safe = set(newbutton.hold_safe_groups(stock, levelcontrols.RESERVED_GROUPS))
    data = built.data
    gt, bt = guidata.group_table(data), guidata.binding_table(data)

    def is_hold_control(g):
        if not g.count:
            return False
        can_id, payload = newbutton.binding_wire_frame(bt[g.start].raw)
        return (can_id == 0x19EFBF9F and payload[2] in levelcontrols.GEOMETRY_OPCODES.values()) \
            or can_id == 0x197F809F

    held = [gi for gi, g in enumerate(gt) if is_hold_control(g)]
    assert len(held) == 5, held
    assert set(held) <= safe, sorted(set(held) - safe)
    assert not set(held) & {136, 137}
