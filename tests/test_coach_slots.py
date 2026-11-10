"""The `coach` block (or `compile --config`): a coach's settings resolve to validated slot values.

`options.coach_slots` resolves a `CoachConfig` the way a render does (`slot_values`, then
`derived_slots` where those derivations are located), validates the slots `extra_slots` names
against the image's type-6 switches, and refuses a bad setting `COACH_CONFIG`. A recipe carries
the settings as its `coach` block; `compile_project(coach=...)` replaces it whole.
"""
import hashlib
import json
import os

import pytest

from fireflymcf import options, recipe

COACH_WD = "build/coach-wd.json"

# The builder check's resolution of `build/coach-wd.json` on 13.6 (2026-11-09).
REFERENCE_SLOTS = {5: 8, 6: 1, 37: 3, 38: 0, 39: 0, 108: 1, 109: 1, 110: 1, 112: 1, 113: 0,
                   453: 1, 512: 1}


def _cfg(**kw):
    return options.CoachConfig(**kw)


def test_reference_settings_resolve_on_136(mcf_136):
    if not os.path.exists(COACH_WD):
        pytest.skip("build/coach-wd.json is linked research data (scripts/link-research.sh)")
    got = options.coach_slots(mcf_136, options.CoachConfig.from_json(COACH_WD), "recipe")
    assert got.slots == REFERENCE_SLOTS
    assert got.pinned == (6, 37, 38, 39, 113, 512)
    assert got.source == "recipe"


def test_empty_block_is_no_settings(mcf_136):
    assert options.coach_slots(mcf_136, _cfg(), "recipe") is None
    assert options.coach_slots(mcf_136, _cfg(options={}), "recipe") is None


def test_147_skips_136_derivations(mcf_147):
    got = options.coach_slots(mcf_147, _cfg(extra_slots={102: 1}), "--config x.json")
    assert got.slots == {102: 1}           # no slot 6, no slot 453: those are 13.6's
    assert got.pinned == (102,)
    assert got.source == "--config x.json"


def test_derivation_is_an_argument(mcf_136):
    """`derive_on` is passed, not read from the module (the memo trap)."""
    with_derive = options.coach_slots(mcf_136, _cfg(extra_slots={37: 3}), "recipe")
    without = options.coach_slots(mcf_136, _cfg(extra_slots={37: 3}), "recipe",
                                  frozenset())
    assert with_derive.slots == {37: 3, 6: 1, 453: 1}
    assert without.slots == {37: 3}


@pytest.mark.parametrize("image, config, names", [
    ("mcf_136", {"options": ["No Such Option"]}, "No Such Option"),
    ("mcf_136", {"floorplan": "ZZ"}, "ZZ"),
    ("mcf_136", {"extra_slots": {60000: 1}}, "60000"),
    # slot 102's switches on 14.7 (bases 352 and 239) carry no 0xFFFF default arm
    ("mcf_147", {"extra_slots": {102: 99}}, "102"),
], ids=["option", "floorplan", "unread-slot", "no-such-arm"])
def test_bad_settings_refuse(request, image, config, names):
    data = request.getfixturevalue(image)
    with pytest.raises(recipe.RecipeError) as e:
        options.coach_slots(data, _cfg(**config), "recipe")
    assert e.value.code == "COACH_CONFIG"
    assert names in str(e.value)


def test_a_shared_base_is_not_refused_here(mcf_136):
    """Base 441 is read by slots 204 and 208; different values resolve. The shared-base rule
    is the placement gate's, per page (`coach_slots`' docstring)."""
    got = options.coach_slots(mcf_136, _cfg(extra_slots={204: 0, 208: 1}), "recipe",
                              frozenset())
    assert got.slots == {204: 0, 208: 1}


def test_a_default_arm_admits_any_value(mcf_136):
    """Slot 453's switch (base 385) has a 0xFFFF default arm: a value no candidate names is
    still one the panel draws (the default)."""
    got = options.coach_slots(mcf_136, _cfg(extra_slots={453: 7}), "recipe", frozenset())
    assert got.slots == {453: 7}


# --- the recipe ------------------------------------------------------------------------------


def _project(stock_hash, **kw):
    return recipe.Project(base_sha256=stock_hash, build_name="X",
                          ops=[recipe.OpSpec(id="a1", type="rename_label",
                                             params={"object": 35, "old": "a", "new": "b"})],
                          **kw)


# `_project(...).to_json()` as it was before the `coach` block existed.
_NO_COACH_JSON = """{
  "schema": 1,
  "base_sha256": "%s",
  "build_name": "X",
  "ops": [
    {
      "id": "a1",
      "type": "rename_label",
      "params": {
        "object": 35,
        "old": "a",
        "new": "b"
      },
      "enabled": true,
      "note": ""
    }
  ],
  "preview": {}
}"""


def test_project_coach_round_trip():
    sha = "0" * 64
    p = _project(sha, coach={"extra_slots": {"6": 1}})
    text = p.to_json()
    assert json.loads(text)["coach"] == {"extra_slots": {"6": 1}}
    assert recipe.Project.from_json(text) == p
    assert _project(sha).to_json() == _NO_COACH_JSON % sha
    assert "coach" not in json.loads(_project(sha, coach={}).to_json())


@pytest.mark.parametrize("block", [
    {"preview": {}},
    {"extra_slots": {"6": 1}, "colour": "red"},
    {"extra_slots": {"six": 1}},
    {"extra_slots": {"6": "1"}},
    {"options": "AGS"},
    {"floorplan": 8},
    [],
])
def test_bad_coach_block_refuses(block):
    doc = json.loads(_project("0" * 64).to_json())
    doc["coach"] = block
    with pytest.raises(recipe.RecipeError) as e:
        recipe.Project.from_json(json.dumps(doc))
    assert e.value.code == "BAD_PROJECT"


def test_prefix_key():
    specs = _project("0" * 64).ops
    a = options.CoachSlots({6: 1}, (6,), "recipe")
    b = options.CoachSlots({6: 2}, (6,), "recipe")
    c = options.CoachSlots({6: 1}, (), "recipe")
    plain = recipe._prefix_key("0" * 64, specs)
    assert recipe._prefix_key("0" * 64, specs, None, None) == plain
    keys = {recipe._prefix_key("0" * 64, specs, None, x) for x in (a, b, c)}
    assert len(keys) == 3 and plain not in keys
    # the label of where the settings came from changes nothing compile does
    assert recipe._prefix_key("0" * 64, specs, None, a) == recipe._prefix_key(
        "0" * 64, specs, None, options.CoachSlots({6: 1}, (6,), "--config f.json"))


def test_prefix_key_ignores_preview_with_a_coach_block(mcf_136):
    sha = hashlib.sha256(mcf_136).hexdigest()
    a = _project(sha, coach={"extra_slots": {"6": 1}})
    b = _project(sha, coach={"extra_slots": {"6": 1}},
                 preview={"floorplan": "WT", "options": ["AGS"]})
    c = _project(sha, coach={"extra_slots": {"6": 2}})

    def key(p):
        return recipe._prefix_key(p.base_sha256, p.ops, p.tick_chain_limit,
                                  recipe.resolve_coach(p, mcf_136))
    assert key(a) == key(b)
    assert key(a) != key(c)
    assert key(_project(sha)) == recipe._prefix_key(sha, _project(sha).ops)


def _empty_project(stock, coach):
    return recipe.Project(base_sha256=hashlib.sha256(stock).hexdigest(), build_name=None,
                          ops=[], coach=coach)


def test_override_replaces_block(mcf_136):
    p = _empty_project(mcf_136, {"extra_slots": {"6": 2}})
    own = recipe.compile_project(p, mcf_136)
    assert own.ok, own.errors
    assert own.coach["slots"][6] == 2
    assert own.coach["source"] == "recipe"
    res = recipe.compile_project(p, mcf_136, coach=_cfg(extra_slots={6: 1}),
                                 coach_source="--config mine.json")
    assert res.ok, res.errors
    assert res.coach["slots"][6] == 1
    assert res.coach["pinned"] == [6]
    assert res.coach["source"] == "--config mine.json"
    assert res.data == own.data             # settings never change a byte


def test_no_settings_leave_the_result_as_today(mcf_136):
    res = recipe.compile_project(_empty_project(mcf_136, {}), mcf_136)
    assert res.ok and res.coach == {}
    # an empty override replaces a block whole: no settings
    p = _empty_project(mcf_136, {"extra_slots": {"6": 2}})
    assert recipe.compile_project(p, mcf_136, coach=_cfg()).coach == {}


def test_a_bad_block_refuses_the_compile(mcf_136):
    res = recipe.compile_project(_empty_project(mcf_136, {"extra_slots": {"60000": 1}}),
                                 mcf_136)
    assert not res.ok
    assert [e["code"] for e in res.errors] == ["COACH_CONFIG"]
    cache: dict = {}
    res = recipe.compile_cached(_empty_project(mcf_136, {"extra_slots": {"60000": 1}}),
                                mcf_136, cache)
    assert [e["code"] for e in res.errors] == ["COACH_CONFIG"]


def test_compile_cached_carries_the_settings(mcf_136):
    p = _empty_project(mcf_136, {"extra_slots": {"6": 1}})
    res = recipe.compile_cached(p, mcf_136, {})
    assert res.ok and res.coach["slots"][6] == 1


# --- malformed slot keys, and the manifest's source across a cache (2026-11-09) ----------------

@pytest.mark.parametrize("keys", [{"²": 1}, {"٣": 1}, {"6": 1, "06": 1}, {"6": 1, "006": 2}])
def test_malformed_slot_keys_refuse(mcf_136, keys):
    """A non-ASCII digit passes str.isdigit(), and two spellings of one slot collapse silently:
    both refuse BAD_PROJECT rather than crash or pick one."""
    doc = json.loads(_project(hashlib.sha256(mcf_136).hexdigest()).to_json())
    doc["coach"] = {"extra_slots": keys}
    with pytest.raises(recipe.RecipeError) as e:
        recipe.Project.from_json(json.dumps(doc))
    assert e.value.code == "BAD_PROJECT"
    # a block built in memory (not through from_json) refuses COACH_CONFIG-side, not by crash
    with pytest.raises(ValueError):
        options.CoachConfig.from_dict({"extra_slots": keys})


@pytest.mark.parametrize("keys", [{"²": 1}, {"6": 1, "06": 1}])
def test_compile_config_file_with_bad_keys_is_coach_config(tmp_path, keys):
    from tests.conftest import REPO_ROOT
    from tests.test_cli import _run
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"extra_slots": keys}), encoding="utf-8")
    stock = (REPO_ROOT / "firefly-13.6/mcf.bin").read_bytes()
    proj = tmp_path / "p.ffproj.json"
    proj.write_text(json.dumps({"schema": 1, "base_sha256": hashlib.sha256(stock).hexdigest(),
                                "build_name": "x", "ops": []}))
    r = _run("compile", str(proj), "firefly-13.6/mcf.bin", "-o", str(tmp_path / "mcf-x.bin"),
             "--config", str(cfg), "--json", cwd=REPO_ROOT)
    assert r.returncode == 1, r.stdout + r.stderr
    assert json.loads(r.stdout)["errors"][0]["code"] == "COACH_CONFIG", r.stdout


def test_manifest_source_is_live_through_a_shared_cache(mcf_136):
    """The cache key leaves the settings' label out, so the second compile reuses the first's
    snapshot; its manifest line still names its own source."""
    sha = hashlib.sha256(mcf_136).hexdigest()
    p = recipe.Project(base_sha256=sha, build_name=None, coach={"extra_slots": {"6": 1}},
                       ops=[recipe.OpSpec(id="b", type="add_button",
                                          params={"channel": 15, "label": "Pin 15",
                                                  "off_colour": [65, 81, 163]})])
    cache = {}
    a = recipe.compile_cached(p, mcf_136, cache)
    b = recipe.compile_cached(p, mcf_136, cache, coach=_cfg(extra_slots={6: 1}),
                              coach_source="--config two.json")
    assert a.ok and b.ok, (a.errors, b.errors)
    line = lambda r: next(m for m in r.manifest if m.startswith("button:"))
    assert "(recipe)" in line(a) and "two.json" not in line(a)
    assert "(--config two.json)" in line(b) and "(recipe)" not in line(b)
