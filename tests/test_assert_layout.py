"""`assert_layout`: a check-only op over `pagewalk`, proven on the v52 Low Volts recipe.

`lv_move` is `builds/mcf-13.6-low-volts-move.ffproj.json` compiled as is. Its compact AES card
(object 1427, reached from ELECTRICAL root 5) holds the "AES" gear: the wtype-33 box at record 8,
placed at absolute (193, 400) once `lv-gear` restored its offset and centred it.
"""
import json

import pytest

from fireflymcf import pagewalk, recipe
from tests import lvbuilds

pytestmark = pytest.mark.skipif(not lvbuilds.available(),
                                reason="builds/ or firefly-13.6/mcf.bin not linked in")

# The gear's walk key on root 5: root -> card attach -> 1427 record 8 (wtype 33).
GEAR_KEY = ((5, 5), (829, 14), (1427, 8))
GEAR_SELECT = {"key": [list(step) for step in GEAR_KEY]}
# The records the gear's offset carries: the gear box, its "AES" label, its touch region and
# its "L" icon (1427 records 8-11). `lv-gear` moves all four by (-55, 29).
GEAR_RUN = [{"key": [[5, 5], [829, 14], [1427, i]]} for i in (8, 9, 10, 11)]


def al(op_id, **params):
    return recipe.OpSpec(id=op_id, type="assert_layout", params=params)


def lv_gear():
    """The recipe's own `lv-gear` op (the gear's offset restored and centred)."""
    project = recipe.Project.from_json(lvbuilds.LOW_VOLTS_MOVE.read_text())
    (spec,) = [s for s in project.ops if s.id == "lv-gear"]
    return spec


def gear_moved(snap, check):
    """`lv-gear` taken out of the recipe and re-applied between `snap` and `check`, so the
    check sees LV-move's own gear fix as a change. (A second `move_element` on the gear after
    `lv-gear` would fail `lv-gear`'s verify, which asserts the gear's y field.)"""
    return run(snap, lv_gear(), check, drop=("lv-gear",))


def run(*specs, drop=()):
    return lvbuilds.compile_with(lvbuilds.LOW_VOLTS_MOVE, *specs, drop=drop)


def err(res):
    assert not res.ok, "compile unexpectedly succeeded"
    return res.errors[0]


def test_gear_key_is_the_aes_gear(lv_move):
    """The pinned key still names the gear: a wtype-33 box of 1427 at (193, 400), with the
    "AES" label after it."""
    walk = pagewalk.walk_records(lv_move, 5)
    by_key = {r.key: r for r in walk}
    gear = by_key[GEAR_KEY]
    assert (gear.obj, gear.wtype, gear.box[:2]) == (1427, 33, (193, 400))
    nxt = walk[walk.index(gear) + 1]
    assert nxt.obj == 1427 and (nxt.label or "").strip() == "AES"


def test_expect_passes_on_the_flashed_gear(lv_move):
    res = run(al("chk", root=5, expect=[{"select": GEAR_SELECT, "x": 193, "y": 400,
                                          "w": 110, "h": 50}]))
    assert res.ok, res.errors
    assert res.data == lv_move


def test_expect_mismatch_names_the_record_and_delta():
    e = err(run(al("chk-x", root=5, expect=[{"select": GEAR_SELECT, "x": 248}])))
    assert e["code"] == "LAYOUT_MISMATCH" and e["op_id"] == "chk-x"
    assert "5:5/829:14/1427:8" in e["message"] and "wtype 33" in e["message"]
    assert "(-55,0)" in e["message"]


def test_snapshot_then_unchanged_passes(lv_move):
    res = run(al("snap", root=5, snapshot=True), al("chk", root=5, unchanged_since="snap"))
    assert res.ok, res.errors
    assert res.data == lv_move


def test_unchanged_since_catches_a_move():
    e = err(gear_moved(al("snap", root=5, snapshot=True),
                       al("chk", root=5, unchanged_since="snap")))
    assert e["code"] == "LAYOUT_MOVED" and e["op_id"] == "chk"
    assert ("5:5/829:14/1427:8 wtype 33 None moved (-55,29) (248,371,358,421)→(193,400,303,450)"
            in e["message"])
    assert "4 record(s)" in e["message"]


def test_except_allows_the_listed_records():
    res = gear_moved(al("snap", root=5, snapshot=True),
                     al("chk-except", root=5, unchanged_since="snap", **{"except": GEAR_RUN}))
    assert res.ok, res.errors


def test_except_leaves_the_rest_checked():
    """Excepting the gear alone still reports the three records its offset carries."""
    e = err(gear_moved(al("snap", root=5, snapshot=True),
                       al("chk-except1", root=5, unchanged_since="snap",
                          **{"except": [GEAR_SELECT]})))
    assert e["code"] == "LAYOUT_MOVED" and "3 record(s)" in e["message"]
    assert "1427:8 " not in e["message"] and "'  AES' moved (-55,29)" in e["message"]


def test_appeared_record_is_reported():
    """Root 31 is the AES settings page (body 1425): the Low Volts control added between the
    snapshot and the check appears on it."""
    project = recipe.Project.from_json(lvbuilds.LOW_VOLTS_MOVE.read_text())
    (aes_settings,) = [s for s in project.ops if s.id == "lv-aes-settings"]
    e = err(run(al("snap31", root=31, snapshot=True), aes_settings,
                al("chk31", root=31, unchanged_since="snap31"),
                drop=("lv-aes-settings",)))
    assert e["code"] == "LAYOUT_MOVED"
    assert "appeared" in e["message"]


@pytest.mark.parametrize("specs", [
    pytest.param([al("chk", root=5)], id="no form"),
    pytest.param([al("chk", root=5, snapshot=True, unchanged_since="x")], id="two forms"),
    pytest.param([al("chk", root=5, snapshot=False)], id="snapshot false"),
    pytest.param([al("chk", root=5, expect=[])], id="empty expect"),
    pytest.param([al("chk", root=5, expect=[{"select": GEAR_SELECT}])], id="expect no field"),
    pytest.param([al("chk", root=5, expect=[{"select": {"nope": 1}, "x": 1}])],
                 id="bad select"),
    pytest.param([al("chk", root=5, expect=[{"select": GEAR_SELECT, "x": 1}],
                     **{"except": [GEAR_SELECT]})], id="except without unchanged_since"),
    pytest.param([al("chk", root=5, unchanged_since="nobody")], id="unknown id"),
    pytest.param([al("exp", root=5, expect=[{"select": GEAR_SELECT, "x": 193}]),
                  al("chk", root=5, unchanged_since="exp")], id="not a snapshot"),
    pytest.param([al("snap", root=5, snapshot=True),
                  al("chk", root=31, unchanged_since="snap")], id="other root"),
    pytest.param([al("snap", root=5, snapshot=True),
                  al("chk", root=5, unchanged_since="snap", switch_values={"1": 2})],
                 id="other switch values"),
])
def test_bad_params(specs):
    e = err(run(*specs))
    assert e["code"] == "BAD_PARAMS", e


def test_no_match_and_ambiguous():
    e = err(run(al("chk", root=31, expect=[{"select": {"label": "nope"}, "x": 0}])))
    assert e["code"] == "NO_MATCH"
    e = err(run(al("chk", root=31, expect=[{"select": {"key": [[31, 999]]}, "x": 0}])))
    assert e["code"] == "NO_MATCH"
    # Root 31 draws seven "+" steppers.
    e = err(run(al("chk", root=31, expect=[{"select": {"label": "+"}, "x": 0}])))
    assert e["code"] == "AMBIGUOUS" and e["message"].count("\n") >= 6
    e = err(run(al("chk", root=31, expect=[{"select": {"label": "+", "nth": 99}, "x": 0}])))
    assert e["code"] == "NO_MATCH"


def test_label_select_with_nth_and_wtype(lv_move):
    plus = [r for r in pagewalk.walk_records(lv_move, 31) if (r.label or "").strip() == "+"]
    target = plus[2]
    res = run(al("chk", root=31, expect=[{"select": {"label": "+", "wtype": target.wtype,
                                                     "nth": 2},
                                          "x": target.box[0], "y": target.box[1]}]))
    assert res.ok, res.errors


def test_snapshot_survives_compile_cached(monkeypatch):
    """The check resumes from a cache warmed past the snapshot: the snapshot's apply does not
    run again, its walk comes back from the cached handles, and the moved gear is still
    caught."""
    snap = al("snap-cached", root=5, snapshot=True)
    assert run(snap, drop=("lv-gear",)).ok
    from fireflymcf import recipe_ops
    calls = []
    real = recipe_ops.AssertLayout.apply

    def counted(self, data, ctx):
        calls.append(self.spec.id)
        return real(self, data, ctx)
    monkeypatch.setattr(recipe_ops.AssertLayout, "apply", counted)
    e = err(gear_moved(snap, al("chk-cached", root=5, unchanged_since="snap-cached")))
    assert calls == ["chk-cached"]
    assert e["code"] == "LAYOUT_MOVED" and "moved (-55,29)" in e["message"]


def test_snapshot_handle_is_json(lv_move):
    """The snapshot handle is plain data: it survives a JSON round trip and still compares."""
    from fireflymcf import recipe_ops
    ctx = recipe.Ctx(stock=b"", edits=[], insertions=[], handles={})
    op_ = recipe_ops.AssertLayout(al("snap", root=5, snapshot=True))
    assert op_.apply(lv_move, ctx) is lv_move
    ctx.handles = json.loads(json.dumps(ctx.handles))
    check = recipe_ops.AssertLayout(al("chk", root=5, unchanged_since="snap"))
    assert check.apply(lv_move, ctx) is lv_move


def test_comparing_nothing_breaks_the_tests(monkeypatch):
    """Guard the guard: with `pagewalk.diff` stubbed to report nothing, the moved gear passes
    (a distinct op id keeps this run out of the shared compile cache's later hits)."""
    monkeypatch.setattr(pagewalk, "diff", lambda *a, **k: [])
    res = gear_moved(al("snap", root=5, snapshot=True),
                     al("chk-stubbed", root=5, unchanged_since="snap"))
    assert res.ok, res.errors


def test_object_record_select(lv_move):
    """`{object, record}` names a record by its index in its own object: 1427 record 8 on root 5
    is the gear; object 393 is a shared child root 31 draws more than once, so its record 0
    is AMBIGUOUS; a record the root never draws is NO_MATCH."""
    res = run(al("chk-objrec", root=5, expect=[{"select": {"object": 1427, "record": 8},
                                                "x": 193, "y": 400}]))
    assert res.ok, res.errors
    walk31 = pagewalk.walk_records(lv_move, 31)
    assert sum(1 for r in walk31 if (r.obj, r.index) == (393, 0)) > 1
    e = err(run(al("chk", root=31, expect=[{"select": {"object": 393, "record": 0}, "x": 0}])))
    assert e["code"] == "AMBIGUOUS" and "shared child" in e["message"]
    e = err(run(al("chk", root=5, expect=[{"select": {"object": 1427, "record": 99}, "x": 0}])))
    assert e["code"] == "NO_MATCH"


def test_empty_values_match_absent():
    """A snapshot with no switch_values and a check with `{}` walk alike, so they compare."""
    res = run(al("snap", root=5, snapshot=True),
              al("chk-empty", root=5, unchanged_since="snap", switch_values={}, slot_values={}))
    assert res.ok, res.errors


def test_expect_comparing_nothing_breaks_the_tests(monkeypatch):
    """Guard the guard for `expect`: with its comparison stubbed to find nothing, the x-248
    mismatch passes (a distinct op id keeps this run out of the shared compile cache)."""
    from fireflymcf import recipe_ops
    monkeypatch.setattr(recipe_ops, "_expect_delta", lambda r, entry: None)
    res = run(al("chk-x-stubbed", root=5, expect=[{"select": GEAR_SELECT, "x": 248}]))
    assert res.ok, res.errors
