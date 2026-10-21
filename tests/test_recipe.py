import hashlib, json
import pytest
from fireflymcf import recipe

STOCK_PATH = "firefly-13.6/mcf.bin"

@pytest.fixture(scope="session")
def stock():
    with open(STOCK_PATH, "rb") as f:
        return f.read()

@pytest.fixture(scope="session")
def stock_hash(stock):
    return hashlib.sha256(stock).hexdigest()


def test_project_json_round_trip(stock_hash):
    p = recipe.Project(base_sha256=stock_hash, build_name="X",
                       ops=[recipe.OpSpec(id="a1", type="rename_label",
                                          params={"object": 35, "old": "a", "new": "b"})])
    q = recipe.Project.from_json(p.to_json())
    assert q == p
    assert json.loads(p.to_json())["schema"] == recipe.SCHEMA

def test_project_refuses_bad_schema(stock_hash):
    doc = {"schema": 999, "base_sha256": stock_hash, "build_name": None, "ops": []}
    with pytest.raises(recipe.RecipeError) as e:
        recipe.Project.from_json(json.dumps(doc))
    assert e.value.code == "BAD_SCHEMA"

def test_project_refuses_missing_field():
    with pytest.raises(recipe.RecipeError) as e:
        recipe.Project.from_json(json.dumps({"schema": 1}))
    assert e.value.code == "BAD_PROJECT"

def test_project_refuses_duplicate_op_ids(stock_hash):
    doc = {"schema": 1, "base_sha256": stock_hash, "build_name": None,
           "ops": [{"id": "a", "type": "t", "params": {}},
                   {"id": "a", "type": "t", "params": {}}]}
    with pytest.raises(recipe.RecipeError) as e:
        recipe.Project.from_json(json.dumps(doc))
    assert e.value.code == "DUPLICATE_OP_ID"

def test_error_to_json():
    e = recipe.RecipeError("LABEL_TOO_LONG", "does not fit", op_id="a1",
                           offers=["grow", "shorten"])
    assert e.to_json() == {"op_id": "a1", "code": "LABEL_TOO_LONG",
                           "message": "does not fit", "offers": ["grow", "shorten"]}

def test_registry_decorator():
    @recipe.op("test_noop")
    class NoOp(recipe.Op):
        def apply(self, data, ctx):
            return data
        def verify(self, data, ctx):
            return "noop"
    assert recipe.REGISTRY["test_noop"] is NoOp
    del recipe.REGISTRY["test_noop"]      # keep the registry clean for other tests

def test_resolve_target():
    ctx = recipe.Ctx(stock=b"", edits=[], insertions=[],
                     handles={"a1": {"object": 1422, "cell": 311, "group": None}})
    assert recipe.resolve_target(ctx, 35) == 35
    assert recipe.resolve_target(ctx, {"ref": "a1"}) == 1422
    with pytest.raises(recipe.RecipeError) as e:
        recipe.resolve_target(ctx, {"ref": "nope"})
    assert e.value.code == "BAD_REF"


def test_empty_project_is_stock(stock, stock_hash):
    p = recipe.Project(base_sha256=stock_hash, build_name=None, ops=[])
    r = recipe.compile_project(p, stock)
    assert r.ok and r.errors == []
    assert r.data == stock          # byte-identical: no ops, no name change
    assert r.manifest == [] and r.unverified == []

def test_compile_refuses_wrong_base(stock):
    p = recipe.Project(base_sha256="0" * 64, build_name=None, ops=[])
    with pytest.raises(recipe.RecipeError) as e:
        recipe.compile_project(p, stock)
    assert e.value.code == "BASE_MISMATCH"

def test_unknown_op_is_structured_error(stock, stock_hash):
    p = recipe.Project(base_sha256=stock_hash, build_name=None,
                       ops=[recipe.OpSpec(id="a", type="no_such_op", params={})])
    r = recipe.compile_project(p, stock)
    assert not r.ok and r.data is None
    assert r.errors[0]["code"] == "UNKNOWN_OP" and r.errors[0]["op_id"] == "a"

def test_disabled_op_is_skipped(stock, stock_hash):
    p = recipe.Project(base_sha256=stock_hash, build_name=None,
                       ops=[recipe.OpSpec(id="a", type="no_such_op", params={},
                                          enabled=False)])
    r = recipe.compile_project(p, stock)
    assert r.ok and r.data == stock

def test_build_name_applied(stock, stock_hash):
    from fireflymcf import patch
    p = recipe.Project(base_sha256=stock_hash, build_name="Jayco B-Van V13 (T)", ops=[])
    r = recipe.compile_project(p, stock)
    assert r.ok
    assert patch.build_name(r.data) == "Jayco B-Van V13 (T)"
    assert len(r.data) == len(stock)

def test_capabilities_of_stock(stock):
    caps = recipe.capabilities(stock)
    assert (3, 1) in caps            # AWNING EXT on — the pair v11 famously lost
    assert all(isinstance(c, int) and isinstance(cmd, int) for c, cmd in caps)

def test_cache_hit_is_byte_identical_and_resumes(stock, stock_hash, monkeypatch):
    from fireflymcf import recipe_ops

    ops = [
        recipe.OpSpec(id="a", type="rename_label",
                      params={"object": 35, "old": "28 - ",
                              "new": "28 - Overflow Valve", "allow_grow": True}),
        recipe.OpSpec(id="b", type="move_element",
                      params={"target": 902, "selector": {"label": "Chassis"},
                              "set": {"y": 6}, "expect": {"y": 71}}),
    ]
    p = recipe.Project(base_sha256=stock_hash, build_name=None, ops=ops)
    cold = recipe.compile_project(p, stock)
    cache = {}
    warm1 = recipe.compile_cached(p, stock, cache)
    assert warm1.data == cold.data
    n_entries = len(cache)
    assert n_entries >= 1

    # The op-a prefix key must actually be sitting in the cache -- the thing
    # compile_cached(p2, ...) below is supposed to find on its longest-cached-prefix
    # scan.
    prefix_a_key = recipe._prefix_key(stock_hash, ops[:1])
    assert prefix_a_key in cache

    # Edit op b only: the op-a prefix must be reused (cache keys for it unchanged --
    # same id/type/params hashes identically regardless of which project it's in).
    ops2 = [ops[0],
            recipe.OpSpec(id="b", type="move_element",
                          params={"target": 902, "selector": {"label": "Chassis"},
                                  "set": {"y": 8}, "expect": {"y": 71}})]
    p2 = recipe.Project(base_sha256=stock_hash, build_name=None, ops=ops2)
    assert recipe._prefix_key(stock_hash, ops2[:1]) == prefix_a_key

    # Prove the resume actually happens, not just that the byte output is correct
    # (which a from-scratch recompute would also produce): op a's apply() must not
    # run again when compiling p2 against the warm cache.
    calls = []
    real_apply = recipe_ops.RenameLabel.apply

    def spy_apply(self, data, ctx):
        calls.append(self.spec.id)
        return real_apply(self, data, ctx)

    monkeypatch.setattr(recipe_ops.RenameLabel, "apply", spy_apply)
    warm2 = recipe.compile_cached(p2, stock, cache)
    assert not calls, "op a's apply() re-ran -- the cached prefix was not reused"
    monkeypatch.undo()

    cold2 = recipe.compile_project(p2, stock)
    assert warm2.data == cold2.data

def test_cache_ignores_disabled_ops(stock, stock_hash):
    ops = [recipe.OpSpec(id="a", type="rename_label", enabled=False,
                         params={"object": 35, "old": "z", "new": "zz"})]
    p = recipe.Project(base_sha256=stock_hash, build_name=None, ops=ops)
    cache = {}
    r = recipe.compile_cached(p, stock, cache)
    assert r.ok and r.data == stock

def test_capability_gate_catches_unexpected_gain(stock, stock_hash, monkeypatch):
    # An op that lies: it adds a capability but declares no delta.
    from fireflymcf import guidata, newbutton, patch, statusbind

    @recipe.op("test_rogue_button")
    class Rogue(recipe.Op):
        relocates = True
        def apply(self, data, ctx):
            data, cell, e = statusbind.bind_pin(data, 15, off_colour=(65, 81, 163))
            data, e2 = newbutton.build_home_button(
                data, channel=15, allow_commanded_channel=True,
                colour_index=cell, label="X")
            return data
        def verify(self, data, ctx):
            return "rogue"
    try:
        p = recipe.Project(base_sha256=stock_hash, build_name=None,
                           ops=[recipe.OpSpec(id="a", type="test_rogue_button", params={})])
        with pytest.raises(AssertionError):
            recipe.compile_project(p, stock)
    finally:
        del recipe.REGISTRY["test_rogue_button"]


def test_bad_build_name_is_a_structured_error(stock, stock_hash):
    # patch.set_build_name refuses a non-ASCII or over-long name with PatchError, which
    # is NOT a RecipeError -- so before this it escaped compile as an unstructured
    # crash. Nothing could reach it while the name was unsettable; the UI's build-name
    # field makes it reachable, so it has to fail like every other project problem.
    for bad in ("Jayco B-Van V13 (Ünicode)", "x" * 200):
        p = recipe.Project(base_sha256=stock_hash, build_name=bad, ops=[])
        with pytest.raises(recipe.RecipeError) as e:
            recipe.compile_project(p, stock)
        assert e.value.code == "BAD_BUILD_NAME", bad
        assert bad[:12] in str(e.value) or "127" in str(e.value)


def test_a_valid_build_name_still_applies(stock, stock_hash):
    from fireflymcf import patch
    p = recipe.Project(base_sha256=stock_hash,
                       build_name="Jayco B-Van V13 (Studio)", ops=[])
    r = recipe.compile_project(p, stock)
    assert r.ok
    assert patch.build_name(r.data) == "Jayco B-Van V13 (Studio)"


# --- preview block (Studio coach-config): persisted, but NOT part of the build ---

def test_project_preview_round_trips(stock_hash):
    p = recipe.Project(base_sha256=stock_hash, build_name=None, ops=[],
                       preview={"floorplan": "WD", "options": ["Black Tank"]})
    q = recipe.Project.from_json(p.to_json())
    assert q.preview == {"floorplan": "WD", "options": ["Black Tank"]}
    assert q == p

def test_project_preview_defaults_to_empty_and_old_files_still_load(stock_hash):
    """A schema-1 file written before preview existed must still open."""
    doc = {"schema": 1, "base_sha256": stock_hash, "build_name": None, "ops": []}
    assert recipe.Project.from_json(json.dumps(doc)).preview == {}

def test_preview_does_not_change_the_compile_prefix_key(stock_hash):
    """The property the whole design rests on: preview is not build input.

    `Engine.etag` is built from `_prefix_key(base_sha256, enabled ops)` plus build_name,
    so a preview change must leave that key byte-identical -- otherwise picking a
    floorplan would invalidate every cached build.
    """
    ops = [recipe.OpSpec(id="a1", type="rename_label",
                         params={"object": 35, "old": "a", "new": "b"})]
    a = recipe.Project(base_sha256=stock_hash, build_name="X", ops=list(ops))
    b = recipe.Project(base_sha256=stock_hash, build_name="X", ops=list(ops),
                       preview={"floorplan": "WT", "options": ["AGS"]})
    key = lambda p: recipe._prefix_key(p.base_sha256, [o for o in p.ops if o.enabled])
    assert key(a) == key(b)


def test_cache_resume_keeps_the_authored_cells_gate(stock, stock_hash):
    """A resumed compile must see the display cells the cached prefix's ops minted: the
    cell-aliasing gate (`_assert_authored_cells_fresh`) reads `ctx.authored_cells`, so a
    snapshot that drops the set lets a reused stock cell through on every warm compile."""
    @recipe.op("test_reused_cell")
    class ReusedCell(recipe.Op):
        def apply(self, data, ctx):
            ctx.authored_cells.add(256)          # a stock index: the gate must refuse it
            return data

        def verify(self, data, ctx):
            return "reused"
    try:
        p = recipe.Project(base_sha256=stock_hash, build_name=None,
                           ops=[recipe.OpSpec(id="a", type="test_reused_cell", params={})])
        cache = {}
        with pytest.raises(AssertionError, match="below the stock cell-table row count"):
            recipe.compile_cached(p, stock, cache)
        assert recipe._prefix_key(stock_hash, p.ops) in cache
        with pytest.raises(AssertionError, match="below the stock cell-table row count"):
            recipe.compile_cached(p, stock, cache)      # resumed from the cached prefix
    finally:
        del recipe.REGISTRY["test_reused_cell"]
