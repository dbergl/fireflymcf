"""Goal 5 (14.x design spec): every refusal is a clear refusal; no raw exception reaches the user.
Regression tests for two raw-exception paths and a sweep of the ops enabled on 14.7."""
import hashlib

import pytest

from fireflymcf import guidata, records, recipe

STOCK_PATH = "firefly-13.6/mcf.bin"


@pytest.fixture(scope="module")
def stock():
    with open(STOCK_PATH, "rb") as f:
        return f.read()


@pytest.fixture(scope="module")
def stock_hash(stock):
    return hashlib.sha256(stock).hexdigest()


def _compile(stock, stock_hash, *specs):
    p = recipe.Project(base_sha256=stock_hash, build_name=None, ops=list(specs))
    return recipe.compile_project(p, stock)


def _recs(data, obj):
    gs, tab = guidata.guidata_extent(data)[0], guidata.object_tables(data)[1]
    return records.object_records(data, gs + tab[obj])


# --- remove_records: an object that attaches one child several times -------------------------

def test_remove_records_one_of_several_attaches_of_a_child(stock, stock_hash):
    """Object 647 attaches child 481 four times (records 1-4). Removing one is a legitimate
    edit: three remain. verify used to assert none remained and raised AssertionError."""
    before = [r for r in _recs(stock, 647) if r.wtype == 5 and r.child_index == 481]
    assert len(before) == 4
    r = _compile(stock, stock_hash, recipe.OpSpec(
        id="rm", type="remove_records",
        params={"target": 647, "records": [
            {"selector": {"wtype": 5, "nth": 1}, "child": 481}]}))
    assert r.ok, r.errors
    after = [x for x in _recs(r.data, 647) if x.wtype == 5 and x.child_index == 481]
    assert len(after) == 3


def test_remove_records_verify_refuses_a_wrong_remaining_count(stock, stock_hash):
    """verify keeps its check: if the attaches left differ from stock minus removed, it
    raises the op's RecipeError, not AssertionError."""
    op = recipe.REGISTRY["remove_records"](recipe.OpSpec(
        id="rm", type="remove_records",
        params={"target": 647, "records": [
            {"selector": {"wtype": 5, "nth": 1}, "child": 481}]}))
    ctx = recipe.Ctx(stock=stock, edits=[], insertions=[], handles={}, firmware="13.6")
    out = op.apply(stock, ctx)
    ctx.handles["rm"]["attach_left"] = {(5, 481): 2}     # pretend two should remain
    with pytest.raises(recipe.RecipeError) as ei:
        op.verify(out, ctx)
    assert ei.value.code == "VERIFY_FAILED"


# --- move_attach: signed offsets ---------------------------------------------------------------

def _attach_params(x=None, y=None):
    return {"page": 647, "child": 481, "x": x, "y": y}


def test_move_attach_negative_offset_is_stored_twos_complement(stock, stock_hash):
    r = _compile(stock, stock_hash, recipe.OpSpec(
        id="ma", type="move_attach", params=_attach_params(x=-8)))
    assert r.ok, r.errors
    moved = [x for x in _recs(r.data, 647) if x.wtype == 5 and x.child_index == 481]
    assert moved[0].geom[0] == 0xFFF8


@pytest.mark.parametrize("bad", [-32769, 65536, 1.5, "3", True])
def test_move_attach_refuses_out_of_range_or_wrong_type(stock, stock_hash, bad):
    r = _compile(stock, stock_hash, recipe.OpSpec(
        id="ma", type="move_attach", params=_attach_params(y=bad)))
    assert not r.ok and r.errors[0]["code"] == "BAD_PARAMS"


# --- sweep: bad inputs to every 14.7-enabled op never raise a raw exception --------------------

_BAD = [
    ("append_filter", {"dgn": "x"}),
    ("append_filter", {"dgn": -1}),
    ("append_filter", {}),
    ("drop_from_master", {"target": 99999, "channel": 1}),
    ("drop_from_master", {"target": "a", "channel": 1}),
    ("drop_from_master", {"target": 31, "channel": -5}),
    ("rename_label", {"object": 99999, "old": "a", "new": "b"}),
    ("rename_label", {"object": 31, "old": 5, "new": "b"}),
    ("rename_label", {"object": -1, "old": "a", "new": "b"}),
    ("set_record_color", {"object": 99999, "record": 0, "color": 1}),
    ("set_record_color", {"object": 31, "record": 999, "color": 1}),
    ("set_record_color", {"object": 31, "record": 0, "color": "red"}),
    ("set_record_color", {"object": 31, "record": 0, "color": 1 << 40}),
    ("install_glyph", {"object": 99999}),
    ("install_glyph", {"glyph": 5}),
    ("set_corner_radius", {"object": 99999, "record": 0, "radius": 4}),
    ("set_corner_radius", {"object": 31, "record": 999, "radius": 4}),
    ("set_corner_radius", {"object": 31, "record": 0, "radius": -4}),
    ("set_corner_radius", {"object": 31, "record": 0, "radius": 1 << 20}),
    ("set_corner_radius", {"object": 31, "record": 0, "radius": "x"}),
    ("move_element", {"target": 99999, "selector": {"wtype": 5}, "set": {"x": 1}}),
    ("move_element", {"target": -1, "selector": {"wtype": 5}, "set": {"x": 1}}),
    ("move_element", {"target": 31, "selector": {"wtype": 5}, "set": {"x": 1 << 20}}),
    ("move_element", {"target": 31, "selector": {"wtype": 5}, "set": {"x": "a"}}),
    ("move_element", {"target": 31, "selector": {"wtype": 99}, "set": {"x": 1}}),
    ("move_element", {"target": 31, "selector": 5, "set": {"x": 1}}),
    ("recolor", {"object": 99999}),
    ("recolor", {"object": 31, "color": "zz"}),
    ("recolor", {"object": 31, "color": 1 << 40}),
    ("remove_control", {"target": 99999}),
    ("remove_control", {"target": "a"}),
    ("remove_control", {"target": -1}),
    ("set_icon", {"object": 99999, "icon": 1}),
    ("set_icon", {"object": 31, "icon": "x"}),
    ("set_icon", {"object": 31, "icon": 1 << 40}),
    ("fill_to_capsule", {"object": 99999, "record": 0}),
    ("fill_to_capsule", {"object": 31, "record": 999}),
    ("fill_to_capsule", {"object": 31, "record": "a"}),
    ("remove_records", {"target": 99999, "records": [{"selector": {"wtype": 5}, "child": 1}]}),
    ("remove_records", {"target": -1, "records": [{"selector": {"wtype": 5}, "child": 1}]}),
    ("remove_records", {"target": 31, "records": "x"}),
    ("remove_records", {"target": 31, "records": [5]}),
    ("remove_records", {"target": 31, "records": [{"child": 1}]}),
    ("remove_records", {"target": 31, "records": [{"selector": 5, "child": 1}]}),
    ("move_attach", {"page": 99999, "child": 481, "x": 1}),
    ("move_attach", {"page": "nowhere", "child": 481, "x": 1}),
    ("move_attach", {"page": 647, "child": 99999, "x": 1}),
    ("move_attach", {"page": 647, "child": "a", "x": 1}),
    ("move_attach", {"page": 647, "child": 481, "x": 1 << 20}),
    ("move_attach", {"page": [1], "child": 481, "x": 1}),
]


@pytest.mark.parametrize("op_type, params", _BAD,
                         ids=["%s-%d" % (t, i) for i, (t, _) in enumerate(_BAD)])
def test_bad_input_is_a_structured_error(stock, stock_hash, op_type, params):
    try:
        r = _compile(stock, stock_hash, recipe.OpSpec(id="x", type=op_type, params=params))
    except recipe.RecipeError:
        return                       # a structured refusal
    assert not r.ok, "bad input compiled: %s %r" % (op_type, params)
    assert r.errors and r.errors[0].get("code")
