"""`add_rvc_button`: a button that sends RV-C frames on any page (tap, hold, tap + hold).

Corpus-reading: compiles against stock 13.6 and places the button on HOME's root object 4 at
(210, 332) 80 x 44, an area of the Fresh/Grey card that no touch region covers.
"""
import hashlib
import json
from pathlib import Path

import pytest

from fireflymcf import (aescompact, altreboot, guidata, newbutton, pagewalk, recipe, records,
                        rvcbutton, theme)

REPO = Path(__file__).resolve().parents[1]
STOCK = REPO / "firefly-13.6/mcf.bin"
SHA = "d66b41ffafaae4f5ec47cf90714f14bb1954fc3a5ae38defa98a94cd9643d382"
HOME = 4
BOX = (210, 332, 80, 44)
DGN = 0x1FEF9
TAP = "07f6ffffffffffff"
HOLD = "07f7ffffffffffff"
# `aescompact.build_batt_heat_button(stock, touch_id=46)`, joined, recorded at 8feb93b (before
# the builder delegated to `rvcbutton.button_records`).
BATT_HEAT_FRAGS_SHA = "8f6d2d4fcca56e5a247093827a0f24c738cf3465f1e5fc8d886b906e90ae3e2f"
BATT_HEAT_FRAGS_ALT_SHA = "0f06f75805aeb3f6461219e2d89cb9ef6b4e65f91695a285c691799f9b840688"
# a 16 x 16 ring for icon font 24 (cell 48), installed earlier in the project
RING = ["0" * 4 + "f" * 8 + "0" * 4] * 2 + ["00" + "f" * 12 + "00"] * 2 + \
       ["f" * 4 + "0" * 8 + "f" * 4] * 8 + ["00" + "f" * 12 + "00"] * 2 + \
       ["0" * 4 + "f" * 8 + "0" * 4] * 2
GLYPH_OP = {"id": "ring", "type": "install_glyph", "enabled": True,
            "params": {"slot": 24, "char": "Y", "y": 16, "rows": RING, "claim_block": True}}


def _stock():
    try:
        return STOCK.read_bytes()
    except FileNotFoundError:
        pytest.skip("needs the stock 13.6 image")


def _op(**over):
    x, y, w, h = BOX
    params = {"parent": HOME, "x": x, "y": y, "w": w, "h": h, "text": "Go", "dgn": DGN,
              "tap": {"payload": TAP}}
    params.update(over)
    return {"id": "btn", "type": "add_rvc_button", "enabled": True,
            "params": {k: v for k, v in params.items() if v is not ...}}


def _compile(ops):
    p = {"schema": 1, "base_sha256": SHA, "build_name": "rvc-button-test", "ops": ops}
    return recipe.compile_project(recipe.Project.from_json(json.dumps(p)), _stock())


def _ok(ops):
    res = _compile(ops)
    assert res.ok, res.errors
    return res


def _errors(ops):
    res = _compile(ops)
    assert not res.ok
    return " ".join(str(e) for e in res.errors)


def _button(data):
    """(object index, its records, group index, group entry, its bindings)."""
    gs, tab = guidata.guidata_extent(data)[0], guidata.object_tables(data)[1]
    obj = len(tab) - 1
    recs = records.object_records(data, gs + tab[obj])
    (touch,) = [r for r in recs if r.wtype == 12]
    group = data[touch.end - 2] | (data[touch.end - 1] << 8)
    g = guidata.group_table(data)[group]
    bt = guidata.binding_table(data)
    return obj, recs, group, g, [bt[j].raw for j in range(g.start, g.start + g.count)]


@pytest.fixture(scope="module")
def tap_built():
    return _ok([_op()])


@pytest.fixture(scope="module")
def tap_hold_built():
    return _ok([_op(hold={"payload": HOLD, "seconds": 3})])


def test_tap_button_frames(tap_built):
    _obj, _recs, group, g, binds = _button(tap_built.data)
    assert bytes(g.raw[:4]) == bytes.fromhex("10000000")
    assert len(binds) == 1
    assert binds[0][3] == 0x06 and binds[0][2] >> 4 == 0
    assert newbutton.binding_wire_frame(binds[0]) == (0x19FEF99F, bytes.fromhex(TAP))
    assert group in newbutton.free_group_slots(_stock())
    assert "btn" in tap_built.unverified


def test_tap_hold_frames(tap_hold_built):
    _obj, _recs, group, g, binds = _button(tap_hold_built.data)
    assert g.raw[0] >> 4 == 3, "the hold nibble is the seconds"
    stock = _stock()
    assert bytes(g.raw[1:4]) == bytes(guidata.group_table(stock)[17].raw[1:4]), \
        "Batt Heat's group-17 clone"
    # ruling R1: the hold binding first, then the tap
    assert [b[3] for b in binds] == [0x04, 0x02]
    assert [newbutton.binding_wire_frame(b) for b in binds] == [
        (0x19FEF99F, bytes.fromhex(HOLD)), (0x19FEF99F, bytes.fromhex(TAP))]
    assert not any(b[2] & 0x04 for b in binds), "no repeat on the held frame"
    assert group in newbutton.hold_safe_groups(stock, frozenset({68, 69}))


def test_hold_only():
    """The `add_alt_reboot` shape: a 3-s hold, no held repeat, one held-phase binding."""
    res = _ok([_op(tap=None, hold={"payload": HOLD, "seconds": 3})])
    _obj, _recs, _group, g, binds = _button(res.data)
    stock = _stock()
    want = altreboot.group_entry(guidata.group_table(stock)[17].raw, g.start)
    assert bytes(g.raw) == want
    assert len(binds) == 1 and binds[0][3] == 0x04
    assert newbutton.binding_wire_frame(binds[0]) == (0x19FEF99F, bytes.fromhex(HOLD))


def _new_walk_recs(data, obj):
    return [r for r in pagewalk.walk_records(data, HOME) if r.obj == obj]


def test_records_and_layout(tap_built):
    data, stock = tap_built.data, _stock()
    obj, recs, group, _g, _b = _button(data)
    gs, tab = guidata.guidata_extent(data)[0], guidata.object_tables(data)[1]
    home = records.object_records(data, gs + tab[HOME])
    assert home[-1].wtype == 5 and home[-1].child_index == obj
    walked = _new_walk_recs(data, obj)
    x, y, w, h = BOX
    assert walked[0].wtype == 32 and walked[0].box == (x, y, x + w, y + h)
    (cap,) = [r for r in walked if r.wtype == 11 and r.label]
    assert cap.label == "Go"
    (touch,) = [r for r in walked if r.wtype == 12]
    inset = aescompact.PUMP_OUTLINE_INSET
    assert touch.box == (x + inset, y + inset, x + w - inset, y + h - inset)
    assert newbutton.binding_wire_frame(
        guidata.binding_table(data)[guidata.group_table(data)[group].start].raw)[0] == 0x19FEF99F
    fill = theme.resolve_fill_index(stock, color=(0x41, 0x51, 0xA3))
    (capsule,) = [r for r in recs if r.wtype == 33]
    assert capsule.colors[0] == ("fillA", 7, fill)
    # nothing else on any page reaching HOME moved or changed state
    from fireflymcf import enumlabel
    assert enumlabel.foreign_deltas(stock, data, HOME, obj) == []


def test_restore_hands_back_the_entering_state(tap_built):
    """The object ends with an empty label writing back the font/align the walk carried in."""
    data = tap_built.data
    obj, recs, _group, _g, _b = _button(data)
    assert recs[-1].wtype == 11 and not recs[-1].label
    walk = pagewalk.walk_records(data, HOME)
    first = next(i for i, r in enumerate(walk) if r.obj == obj)
    last = max(i for i, r in enumerate(walk) if r.obj == obj)
    entering = (walk[first - 2].font, walk[first - 2].align)     # before the attach record
    assert (walk[last].font, walk[last].align) == entering


GBOX = (440, 400, 70, 60)         # the House battery card's lower left: no touch region


def _glyph_op(**over):
    x, y, w, h = GBOX
    return _op(**({"x": x, "y": y, "w": w, "h": h, "glyph": {"slot": 24, "char": "Y"}} | over))


def test_glyph_and_caption():
    res = _ok([GLYPH_OP, _glyph_op(text="Pump")])
    obj, recs, _group, _g, _b = _button(res.data)
    (gl,) = [r for r in recs if r.wtype == 0]
    assert gl.label == "Y" and gl.font == 24
    walked = _new_walk_recs(res.data, obj)
    x, y, w, h = GBOX
    inset = aescompact.PUMP_OUTLINE_INSET
    (g_w,) = [r for r in walked if r.wtype == 0]
    (cap,) = [r for r in walked if r.wtype == 11 and r.label]
    (touch,) = [r for r in walked if r.wtype == 12]
    # the caption takes a 24-px strip (font 14's cell) at the bottom; the ring (16 x 16, x
    # bearing 0, y bearing 16 in its 48-px cell) is centred in the 32 px above it
    assert cap.label == "Pump" and cap.box == (x + inset, y + h - inset - 24,
                                               x + w - inset, y + h - inset)
    assert g_w.box[:2] == (x + inset + (w - 2 * inset - 16) // 2,
                           y + inset + (h - 2 * inset - 24 - 16) // 2 - 16)
    assert touch.box == (x + inset, y + inset, x + w - inset, y + h - inset)
    from fireflymcf import enumlabel
    assert enumlabel.foreign_deltas(_stock(), res.data, HOME, obj) == []


def test_glyph_only_is_centred():
    res = _ok([GLYPH_OP, _glyph_op(text=None)])
    obj, recs, _group, _g, _b = _button(res.data)
    assert not [r for r in recs if r.wtype == 11 and r.label]
    walked = _new_walk_recs(res.data, obj)
    (g_w,) = [r for r in walked if r.wtype == 0]
    (touch,) = [r for r in walked if r.wtype == 12]
    x, y, w, h = GBOX
    inset = aescompact.PUMP_OUTLINE_INSET
    assert g_w.box[:2] == (x + inset + (w - 2 * inset - 16) // 2,
                           y + inset + (h - 2 * inset - 16) // 2 - 16)
    assert touch.box == (x + inset, y + inset, x + w - inset, y + h - inset)


def test_glyph_too_big_for_the_box():
    assert "BAD_PARAMS" in _errors([GLYPH_OP, _glyph_op(text="Pump", h=40)])


@pytest.mark.parametrize("ops,code", [
    ([_op(tap=None)], "BAD_PARAMS"),
    ([_op(tap={"payload": "07f6ffffffffff"})], "BAD_PARAMS"),
    ([_op(hold={"payload": HOLD, "seconds": 3}, group_pool="free")], "BAD_PARAMS"),
    ([_op(glyph={"slot": 27, "char": "E"})], "NO_GLYPH"),
    ([_glyph_op()], "NO_GLYPH"),
    ([_glyph_op(glyph={"slot": 200, "char": "A"})], "NO_GLYPH"),  # no font descriptor                                 # icon font 24's unused block
    ([_op(colour=[1, 2, 3])], "NO_BODY_COLOUR"),
    ([_op(x=102, y=281)], "TOUCH_OVERLAP"),                      # HOME's Fresh tank button
    ([_op(x=760)], "OFF_CANVAS"),
])
def test_refusals(ops, code):
    assert code in _errors(ops)


def test_refused_when_no_group_is_free(monkeypatch):
    monkeypatch.setattr(rvcbutton, "allocate", lambda *a, **k: [])
    assert "NO_FREE_GROUP" in _errors([_op()])


def test_batt_heat_records_unchanged():
    stock = _stock()
    frags = aescompact.build_batt_heat_button(stock, touch_id=46)
    assert hashlib.sha256(b"".join(frags)).hexdigest() == BATT_HEAT_FRAGS_SHA
    frags = aescompact.build_batt_heat_button(stock, touch_id=200, x=10, y=20, w=90, h=60,
                                              text="Two\nLines")
    assert hashlib.sha256(b"".join(frags)).hexdigest() == BATT_HEAT_FRAGS_ALT_SHA


@pytest.mark.parametrize("parent,via", [
    (899, "4:8/899:4"),          # HOME's Climate parent: its first wtype-5 attach carries slot 0
    (905, "4:5/907:0"),          # a slot-free attach of its own, under HOME's slot-0 907 attach
])
def test_parent_with_an_inherited_slot_refuses(parent, via):
    """The capsule (wtype 33) and caption (wtype 11) carry no value slot of their own, so under
    a wtype-5 attach that carries one they would inherit it (`_effective_slot`'s rule, the
    rule `move_control` refuses on); the paste is refused, whether the slot is on the cloned
    attach (899) or on an attach above the parent (905)."""
    err = _errors([_op(parent=parent)])
    assert "INSERT_REFUSED" in err and "inherits value_slot 0 from attach " + via in err, err
    assert "(wtype 33) inherits" in err and "(wtype 11) inherits" in err, err


def test_parent_reached_only_through_a_switch_arm():
    """HOME's 904 is a non-base arm of the slot-6 switch: no base walk reaches it. Apply's
    gates walk every arm set, so verify must too (it took `[0]` over base walks, IndexError)."""
    from fireflymcf import pagewalk
    stock = _stock()
    assert not any(r.obj == 904 for r in pagewalk.walk_records(stock, HOME))
    res = _ok([_op(parent=904, x=700, y=420)])
    assert any("under 904" in m for m in res.manifest), res.manifest


def test_free_pool_never_offers_a_guicode_driven_group():
    """The `free` pool used to hand out 13.6's 136-139 once the lower slots were taken, and the
    finish gate (`recipe._assert_no_guicode_selected_group_reused`) then failed the whole build
    with an assertion. The pool now skips every GuiCode-driven group, so running it dry is the
    op's own NO_FREE_GROUP."""
    from fireflymcf import levelcontrols, rvcbutton
    data = STOCK.read_bytes()
    driven = newbutton.guicode_selected_groups(data)
    assert {136, 137, 138, 139} <= set(driven)
    assert {136, 137, 138, 139} <= set(newbutton.free_group_slots(data))     # the old pool
    pool = [g for g in newbutton.assignable_groups(data)
            if g not in levelcontrols.RESERVED_GROUPS]
    got = rvcbutton.allocate(data, pool="free", need=len(pool))
    assert got == pool and not set(got) & set(driven)
    with pytest.raises(rvcbutton.RvcButtonError, match="holds %d slot" % len(pool)):
        rvcbutton.allocate(data, pool="free", need=len(pool) + 1)
