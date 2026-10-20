"""`move_control`: one op moves a control between objects, puts back what the removal moved on
the source page, and asserts nothing else moved on either page.

Proven on the v52 Low Volts recipe (`tests/lvbuilds.py`): `lv_hold` still carries Low Volts on
the compact AES ELECTRICAL card (object 1427, drawn by ELECTRICAL roots 5 and 39), as the
wtype-5 attach of child 285 (record 8, `value_slot` 45) and the wtype-12 touch region with id
129 (record 9). Removing those two lifts the "AES" gear (record 10, absolute (248, 400)) by 29 px,
because the attach carried a +29 y flow offset. `lv_move` is the flashed LV-move build: its Low
Volts sits on the AES settings page (root 31, body object 1425), and that walk is where
`move_control` must put it.
"""
import pytest

from fireflymcf import gestures, guidata, pagewalk, records, recipe, recipe_ops
from tests import lvbuilds

pytestmark = pytest.mark.skipif(not lvbuilds.available(),
                                reason="builds/ or firefly-13.6/mcf.bin not linked in")

SRC, DST, AES_SETTINGS_ROOT = 1427, 1425, 31
# lv-off-elec's entries: the Low Volts attach (child 285) and its touch region (id 129)
LV = [{"selector": {"wtype": 5, "nth": 1}, "child": 285},
      {"selector": {"wtype": 12, "nth": 1}, "touch_id": 129}]
# The bounding box of LV-move's Low Volts records on root 31 starts here (`test_at_is_lv_moves`).
AT = {"x": 618, "y": 89}
GEAR_BOX = (248, 400)
# LV-move's add_low_volts_control attach on 1425 (record 52) and its new object's "Triggers:"
# title (record 0), which move_control does not carry.
LV_MOVE_ATTACH = (DST, 52)


def mc(op_id="mv", **kw):
    params = {"from": SRC, "to": DST, "records": LV, "at": AT}
    params.update(kw)
    return recipe.OpSpec(id=op_id, type="move_control", params=params)


def run(*specs):
    return lvbuilds.compile_with(lvbuilds.LOW_VOLTS_MOVE, *specs, drop=lvbuilds.HOLD_DROP)


def err(res):
    assert not res.ok, "compile unexpectedly succeeded"
    return res.errors[0]


@pytest.fixture(scope="module")
def moved():
    res = run(mc())
    assert res.ok, res.errors
    return res


def _new_object(data):
    """The object move_control appended: the top index (`insert.insert_object`)."""
    return len(guidata.object_tables(data)[1]) - 1


def _under(walk, step):
    return [r for r in walk if step in r.key]


def _lv_move_records(lv_move):
    """LV-move's Low Volts on root 31: every record under its attach on 1425 except the attach
    itself and the "Triggers:" title."""
    return [r for r in _under(pagewalk.walk_records(lv_move, AES_SETTINGS_ROOT), LV_MOVE_ATTACH)
            if r.key[-1] != LV_MOVE_ATTACH and (r.label or "").strip() != "Triggers:"]


def _moved_records(data):
    attach = (DST, _attach_index(data))
    return [r for r in _under(pagewalk.walk_records(data, AES_SETTINGS_ROOT), attach)
            if r.key[-1] != attach]


def _attach_index(data):
    gs, tab = guidata.guidata_extent(data)[0], guidata.object_tables(data)[1]
    new = _new_object(data)
    (i,) = [i for i, r in enumerate(records.object_records(data, gs + tab[DST]))
            if r.wtype == 5 and r.child_index == new]
    return i


def _removal_remap(walk):
    removed = {8, 9}
    return {r.key: pagewalk.remap_after_removal(r.key, SRC, removed) for r in walk}


def test_at_is_lv_moves(lv_move):
    """`AT` is the top-left of LV-move's Low Volts records (drawn ones) on root 31."""
    drawn = [r for r in _lv_move_records(lv_move) if r.wtype not in (5, 6)]
    assert (min(r.box[0] for r in drawn), min(r.box[1] for r in drawn)) == (AT["x"], AT["y"])


def test_moves_low_volts_and_restores_the_gear(moved):
    for root in (5, 39):
        gear = [r for r in pagewalk.walk_records(moved.data, root)
                if r.obj == SRC and r.wtype == 33][-1]
        assert gear.box[:2] == GEAR_BOX, root
    assert 129 not in gestures.touch_ids(moved.data, SRC)
    assert 129 in gestures.touch_ids(moved.data, _new_object(moved.data))
    assert recipe.REGISTRY["move_control"].hardware_verified is False
    assert "mv" in moved.unverified
    line = [m for m in moved.manifest if m.startswith("move_control:")]
    assert line == ["move_control: 2 record(s) from object 1427 to object 1425 at (618, 89); "
                    "source compensated by 1 edit(s); UNVERIFIED on a coach"]


def test_lands_where_lv_move_drew_it(moved, lv_move):
    """Every Low Volts record LV-move draws on root 31 (the title aside) is drawn by this move
    at the same box, with the same font, align, colours and value slot."""
    def shape(r):
        return (r.wtype, r.label, r.box, r.font, r.align, r.colors, r.value_slot)
    assert [shape(r) for r in _moved_records(moved.data)] == \
        [shape(r) for r in _lv_move_records(lv_move)]


@pytest.mark.parametrize("root", [5, 39])
def test_source_page_otherwise_unchanged(moved, lv_hold, root):
    for arm in pagewalk.switch_arm_sets(lv_hold, [root]):
        before = pagewalk.walk_records(lv_hold, root, switch_values=arm)
        after = pagewalk.walk_records(moved.data, root, switch_values=arm)
        assert pagewalk.diff(before, after, _removal_remap(before)) == [], arm


def test_destination_otherwise_unchanged(moved, lv_hold):
    attach = (DST, _attach_index(moved.data))
    for arm in pagewalk.switch_arm_sets(lv_hold, [AES_SETTINGS_ROOT]):
        deltas = pagewalk.diff(
            pagewalk.walk_records(lv_hold, AES_SETTINGS_ROOT, switch_values=arm),
            pagewalk.walk_records(moved.data, AES_SETTINGS_ROOT, switch_values=arm))
        assert deltas and all(d.kind == "appeared" and attach in d.key for d in deltas), \
            pagewalk.describe(deltas)


def test_moved_face_repaints_from_cell_45(moved):
    """OBJECT-HIERARCHY 1a: the attach the face hangs from names cell 45, the cell child 285's
    box fills from (mode 7), so box and caption repaint on a status change."""
    data = moved.data
    gs, tab = guidata.guidata_extent(data)[0], guidata.object_tables(data)[1]
    new = records.object_records(data, gs + tab[_new_object(data)])
    (attach,) = [r for r in new if r.wtype == 5]
    assert (attach.child_index, attach.value_slot) == (285, 45)
    box = [r for r in records.object_records(data, gs + tab[285]) if r.wtype == 33][0]
    assert ("fillA", 7, 45) in box.colors


def test_handle_resolves_to_the_new_object():
    """A later op addresses the moved control by {ref}: its touch region's absolute y is the
    pasted one, and move_element can change it there."""
    res = run(mc(), recipe.OpSpec(id="mv-y", type="move_element", params={
        "target": {"ref": "mv"}, "selector": {"wtype": 12}, "set": {"y": AT["y"] + 1},
        "expect": {"y": AT["y"]}}))
    assert res.ok, res.errors
    gs, tab = guidata.guidata_extent(res.data)[0], guidata.object_tables(res.data)[1]
    touch = [r for r in records.object_records(res.data, gs + tab[_new_object(res.data)])
             if r.wtype == 12]
    assert touch[0].geom[1] == AT["y"] + 1


def test_without_compensation_it_refuses(monkeypatch):
    """Stub the compensation pass to do nothing: the after-walk names the gear, 29 px high."""
    monkeypatch.setattr(recipe_ops.MoveControl, "_compensate",
                        lambda self, data, ctx, *a, **k: (data, 0))
    e = err(run(mc("mv-nocomp")))
    assert e["code"] == "LAYOUT_MOVED" and e["op_id"] == "mv-nocomp"
    # the gear's before-key: record 10 of 1427, (248, 400) -> (248, 371)
    assert "5:5/829:14/1427:10 wtype 33 None moved (0,-29)" in e["message"]


def test_later_structural_op_still_verifies():
    res = run(mc(), recipe.OpSpec(id="rm315", type="remove_records", params={
        "target": 315, "records": [{"selector": {"wtype": 12, "nth": 1}, "touch_id": 129}]}))
    assert res.ok, res.errors


def test_from_equals_to_is_bad_params():
    assert err(run(mc("mv-same", to=SRC)))["code"] == "BAD_PARAMS"


def test_unreachable_destination_is_no_root(lv_hold):
    """Object 279 is an unattached button template (OBJECT-HIERARCHY 1a): no root draws it."""
    assert pagewalk.reaching_roots(lv_hold, 279) == []
    assert err(run(mc("mv-noroot", to=279)))["code"] == "NO_ROOT"


def test_a_root_that_misses_the_object_is_no_root():
    assert err(run(mc("mv-badroot", source_root=AES_SETTINGS_ROOT)))["code"] == "NO_ROOT"


@pytest.mark.parametrize("change, code", [
    pytest.param({"at": {"x": 790, "y": 89}}, "OFF_CANVAS", id="off canvas"),
    # 1425 record 1 is a touch region at (80, 59)-(300, 109) on root 31
    pytest.param({"at": {"x": 100, "y": 70}}, "TOUCH_OVERLAP", id="touch overlap"),
    # object 48's record 4 is a wtype-10 image (its +0x28 is image index 1)
    pytest.param({"from": 48, "records": [{"selector": {"wtype": 10, "nth": 0}, "child": 1}]},
                 "UNSUPPORTED_RECORD", id="unsupported"),
    pytest.param({"records": [{"selector": {"wtype": 5, "nth": 1}, "child": 286}]},
                 "EXPECT_MISMATCH", id="expect mismatch"),
    pytest.param({"records": [{"selector": {"wtype": 5, "nth": 9}, "child": 285}]},
                 "NO_SUCH_RECORD", id="no such record"),
    pytest.param({"records": [{"selector": {"wtype": 5, "nth": 1}}]}, "BAD_PARAMS",
                 id="no identity"),
    pytest.param({"at": {"x": 618}}, "BAD_PARAMS", id="at without y"),
    # 523 (the header clock digit) has no wtype-5 record for insert_object to clone
    pytest.param({"to": 523, "dest_root": AES_SETTINGS_ROOT}, "INSERT_REFUSED",
                 id="insert refused"),
])
def test_refusals(change, code):
    e = err(run(mc("mv-" + code.lower(), **change)))
    assert e["code"] == code, e


def test_touch_overlap_coordinates_are_a_touch_region(lv_hold):
    walk = pagewalk.walk_records(lv_hold, AES_SETTINGS_ROOT)
    (touch,) = [r for r in walk if r.key[-1] == (DST, 1)]
    assert touch.wtype == 12 and touch.box == (80, 59, 300, 109)


def test_register_dependency():
    """1425 record 14 attaches child 786 and writes x register 2; records 15-17 read it."""
    e = err(run(mc("mv-reg", **{"from": DST, "to": SRC, "at": {"x": 400, "y": 100},
                               "records": [{"selector": {"wtype": 5, "nth": 1},
                                            "child": 786}]})))
    assert e["code"] == "REGISTER_DEPENDENCY" and "1425:15" in e["message"]


def test_state_changed():
    """1425's 'V' (record 12, wtype 0) writes align 1 after the align-3 readout; without it the
    records after it inherit align 3."""
    e = err(run(mc("mv-state", **{"from": DST, "to": SRC, "at": {"x": 400, "y": 300},
                                 "records": [{"selector": {"label": "V"}}]})))
    assert e["code"] == "STATE_CHANGED" and "1425:13" in e["message"]


def test_cannot_compensate():
    """1427's 'D' icon (record 4): the wtype-13 caption after it (record 5) is placed from the
    frame 'D' left (x base mode 3) and has no x field to put the 59 px back into."""
    e = err(run(mc("mv-nocomp2", **{"at": {"x": 400, "y": 300},
                                   "records": [{"selector": {"label": "D"}}]})))
    assert e["code"] == "CANNOT_COMPENSATE" and "(59,0)" in e["message"]


def test_pointer_inside_is_refused(lv_hold, monkeypatch):
    """remove_records' shrink-hole refusal reaches move_control through `_remove_records`."""
    from fireflymcf import theme
    gs, tab = guidata.guidata_extent(lv_hold)[0], guidata.object_tables(lv_hold)[1]
    last = records.object_records(lv_hold, gs + tab[SRC])[-1]
    real = theme.section_guidata_pointers

    def fake(data, marker):
        out = list(real(data, marker))
        return out + [(0, last.offset - gs)] if marker == "GuiCode" else out
    monkeypatch.setattr(theme, "section_guidata_pointers", fake)
    assert err(run(mc("mv-ptr")))["code"] == "POINTER_INSIDE"


def _delta(obj, index, dx, dy, root=5):
    rec = pagewalk.WalkRec(key=((root, 0), (obj, index)), wtype=33, label=None, obj=obj,
                           index=index, box=(0, 0, 10, 10), font=None, align=0, colors=(),
                           value_slot=None, in_regs=(), out_regs=())
    return pagewalk.Delta(rec.key, 33, None, "moved", rec, rec, dx, dy)


def test_arms_that_disagree_are_refused():
    with pytest.raises(recipe.RecipeError) as e:
        recipe_ops._compensation_target([[_delta(SRC, 8, 0, -29)], [_delta(SRC, 8, 0, -30)]],
                                        SRC)
    assert e.value.code == "ARM_DISAGREES"
    with pytest.raises(recipe.RecipeError) as e:
        recipe_ops._compensation_target([[_delta(SRC, 8, 0, -29)], [_delta(SRC, 9, 0, -29)]],
                                        SRC)
    assert e.value.code == "ARM_DISAGREES"
    assert recipe_ops._compensation_target([[_delta(SRC, 8, 0, -29)], [],
                                            [_delta(SRC, 8, 0, -29, root=39)]],
                                           SRC) == (8, 0, -29)
    assert recipe_ops._compensation_target([[], []], SRC) is None


def test_pen_inverts_the_align_shift(lv_hold):
    """`absrecord.absolute` takes the PEN; the walk shifts a path-1 box under align 3 left by its
    width and under align 2 by half (`layout._placed_label_box`), and a labelled wtype 0 by its
    text width (`metrics.aligned_x0`). `_pen_x` undoes each, so the box lands at `x0`."""
    from types import SimpleNamespace

    from fireflymcf import layout, metrics
    op = recipe_ops.MoveControl(mc())

    def placed(wtype, align, box, font=None):
        return pagewalk.WalkRec(key=((0, 0),), wtype=wtype, label=None, obj=0, index=0,
                                box=box, font=font, align=align, colors=(), value_slot=None,
                                in_regs=(), out_regs=())
    box = SimpleNamespace(wtype=33, label=None)
    assert layout._align_path1(33) and not layout._align_path1(0)
    assert op._pen_x(lv_hold, box, placed(33, 3, (100, 0, 140, 10)), 200) == 240
    assert op._pen_x(lv_hold, box, placed(33, 2, (100, 0, 141, 10)), 200) == 220
    assert op._pen_x(lv_hold, box, placed(33, 1, (100, 0, 140, 10)), 200) == 200
    text = SimpleNamespace(wtype=0, label="V")
    width = metrics.text_width(lv_hold, 14, "V")
    pen = op._pen_x(lv_hold, text, placed(0, 3, (0, 0, width, 0), font=14), 200)
    assert metrics.aligned_x0(pen, width, 3) == 200
    pen = op._pen_x(lv_hold, text, placed(0, 2, (0, 0, width, 0), font=14), 200)
    assert metrics.aligned_x0(pen, width, 2) == 200


def test_an_inherited_value_slot_is_refused():
    """HOME's 'Water\\nPump' caption (object 293 record 2) has no value slot of its own; it
    inherits 154 from the attach that places 293, which does not move."""
    e = err(run(mc("mv-inherit", **{"from": 293, "at": {"x": 400, "y": 100},
                                   "records": [{"selector": {"label": "Water\nPump"}}]})))
    assert e["code"] == "UNSUPPORTED_RECORD" and "inherits value_slot 154" in e["message"], e


def test_a_template_with_a_value_slot_is_refused():
    """Object 795's first wtype-5 record (record 7), the attach insert_object would clone,
    carries value_slot 45."""
    e = err(run(mc("mv-template", to=795)))
    assert e["code"] == "UNSUPPORTED_RECORD" and "carries value_slot 45" in e["message"], e
