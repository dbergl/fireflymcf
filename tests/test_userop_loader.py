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


# --- review fix round 1: touches enforced, source-only import, no symlinks ---------------------

G12_FLIP = '''from fireflymcf import recipe
from fireflymcf.userop import UserOp


@recipe.op("user:hook-nop")
class FlipG12(UserOp):
    """Flip the G12 section's last byte."""
    def build(self, ed, ctx):
        s = ed.section("G12")
        off = s.offset + s.length - 1
        old = ed.data[off:off + 1]
        ed.patch(off, old, bytes([old[0] ^ 1]), "flip a G12 byte")

    def check(self, data, ctx):
        return "flipped"
'''


def test_undeclared_touch_refused(tmp_path, mcf_136):
    """`touches` is enforced: a G12 edit by an op declaring only guicode is refused."""
    pf = _setup(tmp_path, "hook_nop", op_py=G12_FLIP)          # manifest: touches ["guicode"]
    proj = _project(mcf_136, ("g", "user:hook-nop"))
    with userop.registered(userop.load_user_ops(pf, proj)):
        res = recipe.compile_project(proj, mcf_136)
    assert not res.ok
    [err] = res.errors
    assert err["code"] == "USER_OP_UNDECLARED_TOUCH" and err["op_id"] == "g"
    assert "g12" in err["message"] and "G12" in err["message"]


def test_declared_touch_passes(tmp_path, mcf_136):
    """Declaring g12 admits the edit at the op's own gate (the flipped byte would then fail the
    image's coverage gate, which is not what this tests)."""
    pf = _setup(tmp_path, "hook_nop", op_py=G12_FLIP, manifest={"touches": ["g12"]})
    loaded = userop.load_user_ops(pf, _project(mcf_136))
    o = loaded[0].cls(recipe.OpSpec(id="g", type="user:hook-nop", params={}))
    ctx = recipe.Ctx(stock=mcf_136, edits=[], insertions=[], handles={})
    out = o.apply(mcf_136, ctx)
    assert userop.touched_sections(mcf_136, out) == {"g12"}


def test_growth_counts_only_the_grown_section(tmp_path, mcf_136):
    """Growing GuiCode shifts GuiData and G12 in the file; only GuiCode counts as touched."""
    src = ('from fireflymcf import recipe\nfrom fireflymcf.userop import UserOp\n\n\n'
           '@recipe.op("user:hook-nop")\nclass Grow(UserOp):\n'
           '    def build(self, ed, ctx):\n        ed.grow_guicode(bytes(8), "pad")\n\n'
           '    def check(self, data, ctx):\n        return "grown"\n')
    pf = _setup(tmp_path, "hook_nop", op_py=src)               # touches ["guicode"]
    loaded = userop.load_user_ops(pf, _project(mcf_136))
    o = loaded[0].cls(recipe.OpSpec(id="g", type="user:hook-nop", params={}))
    ctx = recipe.Ctx(stock=mcf_136, edits=[], insertions=[], handles={})
    assert len(o.apply(mcf_136, ctx)) == len(mcf_136) + 8
    assert userop.touched_sections(mcf_136, o.apply(mcf_136, ctx)) == {"guicode"}


def _stale_pyc(src_path: Path, stale_source: str) -> None:
    """Plant `__pycache__/<name>.cpython-*.pyc` compiled from `stale_source`, which has the same
    size as the real source, with the real source's mtime: CPython's default loader would run it."""
    import importlib.util
    import os
    import py_compile
    real = src_path.read_text()
    assert len(stale_source.encode()) == len(real.encode())
    st = src_path.stat()
    tmp = src_path.with_name("_stale_tmp.py")
    tmp.write_text(stale_source)
    os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
    py_compile.compile(str(tmp), cfile=importlib.util.cache_from_source(str(src_path)),
                       doraise=True)
    tmp.unlink()
    os.utime(src_path, ns=(st.st_atime_ns, st.st_mtime_ns))


def test_import_runs_the_hashed_source_not_stale_bytecode(tmp_path, mcf_136):
    """R7: what runs is the source that was hashed, for op.py and for a sibling it imports."""
    src = (FIX / "hook_nop" / "op.py").read_text().replace(
        'return "hook at', 'return helper.WORD + " hook at').replace(
        "from fireflymcf.userop import UserOp", "from fireflymcf.userop import UserOp\n"
        "from . import helper\nWHO = 'SOURCE'")
    pf = _setup(tmp_path, "hook_nop", op_py=src)
    folder = tmp_path / "ops" / "hook_nop"
    (folder / "helper.py").write_text("WORD = 'SOURCE'\n")
    _stale_pyc(folder / "op.py", src.replace("WHO = 'SOURCE'", "WHO = 'STALE!'"))
    _stale_pyc(folder / "helper.py", "WORD = 'STALE!'\n")
    pycs = sorted(p.name for p in (folder / "__pycache__").iterdir())

    proj = _project(mcf_136, ("nop", "user:hook-nop"))
    loaded = userop.load_user_ops(pf, proj)
    mod = __import__("sys").modules[loaded[0].cls.__module__]
    assert mod.WHO == "SOURCE"
    with userop.registered(loaded):
        res = recipe.compile_project(proj, mcf_136)
    assert res.ok, res.errors
    assert ": SOURCE hook at" in res.manifest[0]
    # and nothing new was written to (or rewritten in) the cache
    assert sorted(p.name for p in (folder / "__pycache__").iterdir()) == pycs


def test_symlink_in_op_folder_refused(tmp_path, mcf_136):
    secret = tmp_path / "secret.key"
    secret.write_text("PRIVATE")
    pf = _setup(tmp_path, "hook_nop")
    (tmp_path / "ops" / "hook_nop" / "key.txt").symlink_to(secret)
    code, msg = _code(lambda: userop.discover_user_ops(pf, _project(mcf_136)))
    assert code == "BAD_USER_OP" and "key.txt" in msg and "symbolic link" in msg
    code, _ = _code(lambda: userop.folder_hash(tmp_path / "ops" / "hook_nop"))
    assert code == "BAD_USER_OP"


# --- review fix round 2: descriptors and the header count; resealing alone never does ---------

def _desc_offset(data: bytes, marker: str) -> int:
    c = container.parse_container(data)
    i = next(s.index for s in c.sections if marker in s.name)
    return container._descriptor_offsets(data, c.section_count, c.header_word_20)[i]


DESC_PATCH = '''from fireflymcf import container, recipe
from fireflymcf.userop import UserOp


@recipe.op("user:hook-nop")
class DescPatch(UserOp):
    def build(self, ed, ctx):
        off = {off}
        old = ed.data[off:off + 1]
        ed.patch(off, old, bytes([old[0] ^ 1]), "flip a byte outside every payload")

    def check(self, data, ctx):
        return "flipped"
'''


@pytest.mark.parametrize("where, field, kind", [
    ("G12", 0x5C, "g12"),           # the first tagged entry's value
    ("G12", 0x44, "g12"),           # the descriptor type byte
    ("LCD", 0x5C, "lcd"),           # the LCD flash destination
    (None, 0x30, "header"),         # the container header (config name)
])
def test_descriptor_and_header_changes_are_touches(tmp_path, mcf_136, where, field, kind):
    off = (_desc_offset(mcf_136, where) if where else 0) + field
    pf = _setup(tmp_path, "hook_nop", op_py=DESC_PATCH.replace("{off}", hex(off)))
    assert userop.touched_sections(mcf_136, _flip(mcf_136, off)) == {kind}
    proj = _project(mcf_136, ("d", "user:hook-nop"))           # declares only guicode
    with userop.registered(userop.load_user_ops(pf, proj)):
        res = recipe.compile_project(proj, mcf_136)
    assert not res.ok and res.errors[0]["code"] == "USER_OP_UNDECLARED_TOUCH", res.errors
    assert kind in res.errors[0]["message"]


def _flip(data: bytes, off: int) -> bytes:
    return data[:off] + bytes([data[off] ^ 1]) + data[off + 1:]


def test_resealing_alone_touches_nothing(mcf_136):
    """An earlier op left GuiData edited but unsealed; growing GuiCode reseals the image. Only
    GuiCode counts as touched: GuiData's checksum changed, its content did not."""
    gd = next(s for s in container.parse_container(mcf_136).sections if "GuiData" in s.name)
    stale = _flip(mcf_136, gd.offset + 0x100)
    ed = userop.Editor(stale)
    ed.grow_guicode(bytes(8), "pad")
    assert userop.touched_sections(stale, ed.data) == {"guicode"}
    from fireflymcf import patch
    assert userop.touched_sections(stale, patch.reseal(stale)[0]) == set()


GROW_GUIDATA = ('from fireflymcf import recipe\nfrom fireflymcf.userop import UserOp\n\n\n'
                '@recipe.op("user:hook-nop")\nclass Grow(UserOp):\n'
                '    def build(self, ed, ctx):\n'
                '        ctx.handles[self.spec.id] = ed.grow_guidata(b"FFMCtest", "pad")\n\n'
                '    def check(self, data, ctx):\n        return "grown"\n')


def test_grow_guidata_needs_guidata_declared(tmp_path, mcf_136):
    """A GuiData growth by an op that declares only guicode is refused, naming GuiData."""
    pf = _setup(tmp_path, "hook_nop", op_py=GROW_GUIDATA)       # touches ["guicode"]
    proj = _project(mcf_136, ("g", "user:hook-nop"))
    with userop.registered(userop.load_user_ops(pf, proj)):
        res = recipe.compile_project(proj, mcf_136)
    assert not res.ok
    [err] = res.errors
    assert err["code"] == "USER_OP_UNDECLARED_TOUCH" and err["op_id"] == "g"
    assert "GuiData" in err["message"] and "guidata" in err["message"]


def test_grow_guidata_declared_compiles(tmp_path, mcf_136):
    """Declared, the growth passes every gate the compile runs (structure, coverage, replay) and
    lands at the address the op was given."""
    pf = _setup(tmp_path, "hook_nop", op_py=GROW_GUIDATA, manifest={"touches": ["guidata"]})
    proj = _project(mcf_136, ("g", "user:hook-nop"))
    with userop.registered(userop.load_user_ops(pf, proj)):
        res = recipe.compile_project(proj, mcf_136)
    assert res.ok, res.errors
    ed = userop.Editor(res.data)
    gd = ed.section("GuiData")
    at = gd.offset + gd.length - 4 - 8
    assert res.data[at:at + 8] == b"FFMCtest"


# A user op that nops whatever the located hook holds (stock `bl` or a chained one).
_NOP_LOCATED_HOOK = '''
from fireflymcf import hooklocate, recipe, theme
from fireflymcf.userop import UserOp


@recipe.op("user:hook-nop")
class HookNop(UserOp):
    def build(self, ed, ctx):
        h = hooklocate.locate_hook(ed.data)
        addr = theme.guicode_base(ed.section("GuiCode")) + h.hook_off
        ed.patch_addr("GuiCode", addr, h.hook_bytes, bytes.fromhex("00bf00bf"), "nop the hook")

    def check(self, data, ctx):
        return "hook nopped"
'''

_READOUT = recipe.OpSpec(id="show", type="add_register_readout", params={
    "register": {"ref": "probe"}, "mask": 255, "width_bytes": 1, "page": 4, "x": 300, "y": 357})
_PROBE = recipe.OpSpec(id="probe", type="append_filter",
                       params={"dgn": 0x1FEA0, "mode": 1, "payload_byte": 0})


@pytest.mark.parametrize("order,code,op_id", [
    ("user_last", "HOOK_REFUSED", "show"),       # the built-in tick op's verify walks a nopped hook
    ("user_first", "READOUT_REFUSED", "show"),   # its apply finds no hook to chain onto
])
def test_a_user_op_that_breaks_the_hook_is_refused_not_raised(tmp_path, mcf_136, order, code,
                                                              op_id):
    pf = _setup(tmp_path, "hook_nop", op_py=_NOP_LOCATED_HOOK)
    nop = recipe.OpSpec(id="nop", type="user:hook-nop", params={})
    ops = [_PROBE, _READOUT, nop] if order == "user_last" else [nop, _PROBE, _READOUT]
    proj = recipe.Project(base_sha256=hashlib.sha256(mcf_136).hexdigest(),
                          build_name="Jayco B-Van V13 user op test", ops=ops)
    with userop.registered(userop.load_user_ops(pf, proj)):
        res = recipe.compile_project(proj, mcf_136)
    assert not res.ok and res.data is None
    assert [(e["code"], e["op_id"]) for e in res.errors] == [(code, op_id)], res.errors
    assert "per-tick hook" in res.errors[0]["message"]


def test_a_hook_refusal_escaping_an_apply_is_mapped(mcf_136):
    """An op whose apply lets a `FollowChannelError` through (the hook did not prove out) is
    refused as DETOUR_REFUSED naming it, not raised: the compile loop maps it."""
    @recipe.op("test_raw_hook_error")
    class Raw(recipe.Op):
        def apply(self, data, ctx):
            followchannel.hook_site(bytes(data[:0x100]))

    try:
        proj = recipe.Project(base_sha256=hashlib.sha256(mcf_136).hexdigest(), build_name="x",
                              ops=[recipe.OpSpec(id="raw", type="test_raw_hook_error", params={})])
        for res in (recipe.compile_project(proj, mcf_136),
                    recipe.compile_cached(proj, mcf_136, {})):
            assert not res.ok
            assert [(e["code"], e["op_id"]) for e in res.errors] == [("DETOUR_REFUSED", "raw")]
    finally:
        del recipe.REGISTRY["test_raw_hook_error"]
