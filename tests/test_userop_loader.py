"""User ops: op folders loaded beside a project, gated on the Editor's ledger.

The fixture ops (`tests/fixtures/userops/`) hold no firmware bytes: the one that compiles patches
the per-tick hook site, expecting `followchannel.HOOK_STOCK`, which it reads from the library."""
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from fireflymcf import container, followchannel, recipe, userop

FIX = Path(__file__).resolve().parent / "fixtures" / "userops"


def _project(stock: bytes, *specs, **extra) -> recipe.Project:
    return recipe.Project(base_sha256=hashlib.sha256(stock).hexdigest(),
                          build_name="Jayco B-Van V13 user op test",
                          ops=[recipe.OpSpec(id=i, type=t, params={}) for i, t in specs],
                          **extra)


def _setup(tmp_path: Path, *names: str, manifest: dict | None = None,
           op_py: str | None = None) -> Path:
    """A project directory with `ops/<name>/` copied from the fixtures; returns the project file."""
    for n in names:
        dst = tmp_path / "ops" / n
        shutil.copytree(FIX / n, dst)
        if manifest is not None:
            m = json.loads((dst / "manifest.json").read_text())
            m.update(manifest)
            (dst / "manifest.json").write_text(json.dumps(m))
        if op_py is not None:
            (dst / "op.py").write_text(op_py)
    pf = tmp_path / "p.ffproj.json"
    pf.write_text("{}")
    return pf


def _code(fn) -> str:
    with pytest.raises(recipe.RecipeError) as e:
        fn()
    return e.value.code, str(e.value)


def test_loads_folder_and_compiles(tmp_path, mcf_136):
    pf = _setup(tmp_path, "hook_nop")
    proj = _project(mcf_136, ("nop", "user:hook-nop"))
    loaded = userop.load_user_ops(pf, proj)
    assert [l.type for l in loaded] == ["user:hook-nop"]
    sha = userop.folder_hash(tmp_path / "ops" / "hook_nop")
    assert loaded[0].folder_sha256 == sha and loaded[0].manifest["version"] == "1.0.0"
    assert "user:hook-nop" not in recipe.REGISTRY          # never in the global registry

    with userop.registered(loaded):
        res = recipe.compile_project(proj, mcf_136)
    assert res.ok, res.errors
    assert res.manifest == ["user op user:hook-nop v1.0.0 (%s): hook at GuiCode+%#x is 00bf00bf"
                            % (sha[:12], followchannel.HOOK_OFF)]
    assert res.unverified == ["nop"]
    gc = next(s for s in container.parse_container(res.data).sections if "GuiCode" in s.name)
    assert res.data[gc.offset + followchannel.HOOK_OFF:][:4] == bytes.fromhex("00bf00bf")

    # outside the block the type is unknown again: nothing leaks into the next compile
    res2 = recipe.compile_project(proj, mcf_136)
    assert not res2.ok and res2.errors[0]["code"] == "UNKNOWN_OP"


def test_apply_records_one_edit_per_patch(tmp_path, mcf_136):
    loaded = userop.load_user_ops(_setup(tmp_path, "hook_nop"), _project(mcf_136))
    o = loaded[0].cls(recipe.OpSpec(id="nop", type="user:hook-nop", params={}))
    ctx = recipe.Ctx(stock=mcf_136, edits=[], insertions=[], handles={})
    out = o.apply(mcf_136, ctx)
    assert len(ctx.edits) == 1
    e = ctx.edits[0]
    assert (e.old, e.new) == (followchannel.HOOK_STOCK, bytes.fromhex("00bf00bf"))
    assert out[e.file_offset:e.file_offset + 4] == e.new


def test_ext_ops_folder_is_loaded(tmp_path, mcf_136):
    shutil.copytree(FIX / "hook_nop", tmp_path / "elsewhere" / "hook")
    pf = tmp_path / "proj" / "p.ffproj.json"
    pf.parent.mkdir()
    pf.write_text("{}")
    proj = _project(mcf_136, ext_ops=["../elsewhere/hook"])
    assert [l.type for l in userop.load_user_ops(pf, proj)] == ["user:hook-nop"]


def test_name_must_be_namespaced(tmp_path, mcf_136):
    pf = _setup(tmp_path, "hook_nop", manifest={"type": "hook-nop"})
    code, msg = _code(lambda: userop.load_user_ops(pf, _project(mcf_136)))
    assert code == "USER_OP_NAME" and "user:" in msg


def test_cannot_shadow_builtin(tmp_path, mcf_136):
    before = recipe.REGISTRY["rename_label"]
    pf = _setup(tmp_path, "hook_nop", manifest={"type": "rename_label"})
    assert _code(lambda: userop.load_user_ops(pf, _project(mcf_136)))[0] == "USER_OP_NAME"

    # op.py registering a built-in's name never reaches the global registry, even transiently
    src = (FIX / "hook_nop" / "op.py").read_text().replace('"user:hook-nop"', '"rename_label"')
    pf = _setup(tmp_path / "b", "hook_nop", op_py=src)
    code, msg = _code(lambda: userop.load_user_ops(pf, _project(mcf_136)))
    assert code == "BAD_USER_OP" and "user:hook-nop" in msg
    assert recipe.REGISTRY["rename_label"] is before


def test_duplicate_type_refused(tmp_path, mcf_136):
    pf = _setup(tmp_path, "hook_nop")
    shutil.copytree(FIX / "hook_nop", tmp_path / "ops" / "copy")
    code, msg = _code(lambda: userop.load_user_ops(pf, _project(mcf_136)))
    assert code == "USER_OP_DUPLICATE" and "hook_nop" in msg and "copy" in msg


def test_bad_manifest_and_import_errors(tmp_path, mcf_136):
    pf = _setup(tmp_path / "a", "hook_nop", manifest={"touches": ["guicode", "flash"]})
    assert _code(lambda: userop.load_user_ops(pf, _project(mcf_136)))[0] == "BAD_USER_OP"
    # malformed manifests are refusals, never a TypeError out of the loader
    for i, bad in enumerate([{"touches": [{}]}, {"touches": [["guicode"]]}, {"name": 5},
                             {"version": 1}, {"type": ["user:x"]}, {"author": None},
                             {"description": {}}, {"written_against": ["13.6"]},
                             {"provenance": [3]}, {"touches": "guicode"}]):
        pf = _setup(tmp_path / f"m{i}", "hook_nop", manifest=bad)
        code, msg = _code(lambda: userop.load_user_ops(pf, _project(mcf_136)))
        assert code == "BAD_USER_OP", (bad, code, msg)
    for i, raw in enumerate([b"\xff\xfe not utf-8", b"[" * 100000, b"[1, 2]"]):
        pf = _setup(tmp_path / f"r{i}", "hook_nop")
        (tmp_path / f"r{i}" / "ops" / "hook_nop" / "manifest.json").write_bytes(raw)
        assert _code(lambda: userop.load_user_ops(pf, _project(mcf_136)))[0] == "BAD_USER_OP"
    pf = _setup(tmp_path / "b", "hook_nop", op_py="import os\nx = 1 +\n")
    code, msg = _code(lambda: userop.load_user_ops(pf, _project(mcf_136)))
    assert code == "BAD_USER_OP" and "op.py:2" in msg
    pf = _setup(tmp_path / "c", "hook_nop", op_py="raise RuntimeError('boom')\n")
    code, msg = _code(lambda: userop.load_user_ops(pf, _project(mcf_136)))
    assert code == "BAD_USER_OP" and "RuntimeError: boom" in msg and "op.py:1" in msg
    pf = _setup(tmp_path / "e", "hook_nop", op_py="raise SystemExit(3)\n")
    code, msg = _code(lambda: userop.load_user_ops(pf, _project(mcf_136)))
    assert code == "BAD_USER_OP" and "SystemExit: 3" in msg and "op.py:1" in msg
    # an op that overrides apply() would bypass the Editor
    src = (FIX / "hook_nop" / "op.py").read_text() + \
        "\n    def apply(self, data, ctx):\n        return data\n"
    pf = _setup(tmp_path / "d", "hook_nop", op_py=src)
    code, msg = _code(lambda: userop.load_user_ops(pf, _project(mcf_136)))
    assert code == "BAD_USER_OP" and "apply" in msg


def test_user_op_exception_is_structured(tmp_path, mcf_136):
    pf = _setup(tmp_path, "crash")
    proj = _project(mcf_136, ("c", "user:crash"))
    with userop.registered(userop.load_user_ops(pf, proj)):
        res = recipe.compile_project(proj, mcf_136)
    assert not res.ok
    [err] = res.errors
    assert err["code"] == "USER_OP_CRASHED" and err["op_id"] == "c"
    assert err["message"].startswith("user:crash: KeyError: 'x' (at ")
    assert err["message"].endswith("op.py:9)")


def test_check_exception_is_structured(tmp_path, mcf_136):
    src = (FIX / "hook_nop" / "op.py").read_text().replace(
        "        assert got == NOPS", "        1 / 0\n        assert got == NOPS")
    pf = _setup(tmp_path, "hook_nop", op_py=src)
    proj = _project(mcf_136, ("nop", "user:hook-nop"))
    with userop.registered(userop.load_user_ops(pf, proj)):
        results = [recipe.compile_project(proj, mcf_136),
                   recipe.compile_cached(proj, mcf_136, {})]
    for res in results:          # the same shape as a crash in build, from both compile paths
        assert not res.ok and res.data is None
        [err] = res.errors
        assert err["code"] == "USER_OP_CRASHED" and err["op_id"] == "nop"
        assert "ZeroDivisionError" in err["message"]


def _hook_nop_with(tmp_path, extra: str = "", replace=("", "")) -> Path:
    src = (FIX / "hook_nop" / "op.py").read_text().replace(*replace) + extra
    return _setup(tmp_path, "hook_nop", op_py=src)


@pytest.mark.parametrize("where, extra, replace", [
    ("validate", "\n    def validate(self):\n        raise SystemExit(1)\n", ("", "")),
    ("build", "", ("        addr = theme", "        raise SystemExit(2)\n        addr = theme")),
    ("check", "", ("        s = next(", "        raise SystemExit(4)\n        s = next(")),
    ("capability_delta", "\n    def capability_delta(self, ctx):\n        return {}['k']\n",
     ("", "")),
    ("capability_delta shape", "\n    def capability_delta(self, ctx):\n        return 7\n",
     ("", "")),
])
def test_every_user_op_failure_is_a_failed_result(tmp_path, mcf_136, where, extra, replace):
    pf = _hook_nop_with(tmp_path, extra, replace)
    proj = _project(mcf_136, ("nop", "user:hook-nop"))
    with userop.registered(userop.load_user_ops(pf, proj)):
        results = [recipe.compile_project(proj, mcf_136),
                   recipe.compile_cached(proj, mcf_136, {})]
    for res in results:
        assert not res.ok, where
        [err] = res.errors
        assert err["code"] == "USER_OP_CRASHED" and err["op_id"] == "nop", (where, err)


def test_user_op_recipe_error_in_check_is_a_failed_result(tmp_path, mcf_136):
    pf = _hook_nop_with(tmp_path, replace=(
        "        s = next(", "        raise recipe.RecipeError('MY_CODE', 'nope')\n        s = next("))
    proj = _project(mcf_136, ("nop", "user:hook-nop"))
    with userop.registered(userop.load_user_ops(pf, proj)):
        res = recipe.compile_project(proj, mcf_136)
    assert not res.ok and res.errors[0]["code"] == "MY_CODE" and res.errors[0]["op_id"] == "nop"


def test_keyboard_interrupt_is_not_swallowed(tmp_path, mcf_136):
    pf = _hook_nop_with(tmp_path, replace=(
        "        addr = theme", "        raise KeyboardInterrupt\n        addr = theme"))
    proj = _project(mcf_136, ("nop", "user:hook-nop"))
    with userop.registered(userop.load_user_ops(pf, proj)):
        with pytest.raises(KeyboardInterrupt):
            recipe.compile_project(proj, mcf_136)


def test_builtin_refusal_still_raises(mcf_136):
    # the user-op shape change must not alter how a project-level refusal reaches the caller
    proj = _project(mcf_136)
    proj.build_name = "\u00e9" * 3
    with pytest.raises(recipe.RecipeError) as e:
        recipe.compile_project(proj, mcf_136)
    assert e.value.code == "BAD_BUILD_NAME"


def test_tamper_detected(tmp_path, mcf_136):
    pf = _setup(tmp_path, "tamper")
    proj = _project(mcf_136, ("t", "user:tamper"))
    with userop.registered(userop.load_user_ops(pf, proj)):
        res = recipe.compile_project(proj, mcf_136)
    assert not res.ok and res.errors[0]["code"] == "USER_OP_TAMPERED"
    assert res.errors[0]["op_id"] == "t"


def test_cache_key_carries_the_folder_hash(tmp_path, mcf_136):
    pf = _setup(tmp_path, "hook_nop")
    proj = _project(mcf_136, ("nop", "user:hook-nop"))
    loaded = userop.load_user_ops(pf, proj)
    enabled = [s for s in proj.ops]
    with userop.registered(loaded):
        k1 = recipe._prefix_key(proj.base_sha256, enabled)
    with userop.registered([userop.LoadedOp(l.type, l.cls, l.folder, l.manifest, "f" * 64)
                            for l in loaded]):
        k2 = recipe._prefix_key(proj.base_sha256, enabled)
    assert k1 != k2


def test_folder_hash_ignores_pycache(tmp_path):
    shutil.copytree(FIX / "hook_nop", tmp_path / "h")
    h = userop.folder_hash(tmp_path / "h")
    (tmp_path / "h" / "__pycache__").mkdir()
    (tmp_path / "h" / "__pycache__" / "op.cpython-312.pyc").write_bytes(b"x")
    (tmp_path / "h" / "stray.pyc").write_bytes(b"y")
    assert userop.folder_hash(tmp_path / "h") == h
    (tmp_path / "h" / "op.py").write_text("# changed\n")
    assert userop.folder_hash(tmp_path / "h") != h


def test_project_roundtrips_new_fields(mcf_136):
    p = _project(mcf_136, ext_ops=["../shared/op"],
                 user_op_acceptance={"user:hook-nop": {"folder_sha256": "a" * 64}})
    doc = json.loads(p.to_json())
    assert doc["ext_ops"] == ["../shared/op"]
    assert recipe.Project.from_json(p.to_json()) == p
    # a project without them writes exactly what it wrote before
    bare = _project(mcf_136)
    assert "ext_ops" not in json.loads(bare.to_json())
    assert "user_op_acceptance" not in json.loads(bare.to_json())
    bad = dict(doc, ext_ops="x")
    assert _code(lambda: recipe.Project.from_json(json.dumps(bad)))[0] == "BAD_PROJECT"


def test_validate_exception_is_structured(tmp_path, mcf_136):
    src = (FIX / "hook_nop" / "op.py").read_text() + \
        "\n    def validate(self):\n        [][0]\n"
    pf = _setup(tmp_path, "hook_nop", op_py=src)
    proj = _project(mcf_136, ("nop", "user:hook-nop"))
    with userop.registered(userop.load_user_ops(pf, proj)):
        res = recipe.compile_project(proj, mcf_136)
    assert not res.ok and res.errors[0]["code"] == "USER_OP_CRASHED"
    assert "IndexError" in res.errors[0]["message"] and res.errors[0]["op_id"] == "nop"
