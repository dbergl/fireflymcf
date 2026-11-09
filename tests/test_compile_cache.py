"""The shared recipe-compile cache in `tests/conftest.py`: failed compiles are cached, a patch made
anywhere sends a compile around the cache, and dead entries are pruned.

Everything runs against a scratch cache directory and a stub compile, so no test here compiles a
recipe or reads research data. The scratch directory and the stub are installed by plain
assignment, not through pytest's patch fixture: a patch on the conftest is itself a live patch, and
would send every compile around the cache.
"""
import hashlib
import json
import os
import time
import types
import uuid
from unittest import mock

import pytest

from fireflymcf import recipe
from tests import conftest as cc
from tests.cache_patch_helpers import (  # noqa: F401 - fixtures, used by name
    emulator_patched_elsewhere, engine_patched_elsewhere)


@pytest.fixture
def scratch(tmp_path):
    """A scratch cache directory, an empty in-process memo, and a stub compile whose calls are
    counted. `scratch.result` is what the stub returns (or raises, if an exception)."""
    saved = (cc.IMAGE_CACHE_DIR, cc._UNCACHED_COMPILE, dict(cc._MEMO))
    state = types.SimpleNamespace(calls=0, result=None, dir=tmp_path / "cache")

    def stub(project, stock):
        state.calls += 1
        if isinstance(state.result, BaseException):
            raise state.result
        return state.result

    cc.IMAGE_CACHE_DIR = state.dir
    cc._UNCACHED_COMPILE = stub
    cc._MEMO.clear()
    try:
        yield state
    finally:
        cc.IMAGE_CACHE_DIR, cc._UNCACHED_COMPILE = saved[0], saved[1]
        cc._MEMO.clear()
        cc._MEMO.update(saved[2])


def _case():
    """A unique (project, stock) pair the cache has never seen."""
    stock = b"stock-" + uuid.uuid4().bytes
    return recipe.Project(base_sha256=hashlib.sha256(stock).hexdigest(), build_name=None,
                          ops=[]), stock


def _failure():
    return recipe.CompileResult(False, None, [{"code": "X", "message": "no", "op": "a"}], [], [],
                                firmware="13.6")


def _success():
    return recipe.CompileResult(True, b"image", [], ["a"], [], {}, firmware="13.6")


def _from_disk():
    cc._MEMO.clear()        # the next read must come from the scratch directory, not the process


def test_the_routing_is_installed_from_this_conftest():
    assert recipe.compile_project is cc._compile_through_cache


# --- failed compiles --------------------------------------------------------------------------


def test_a_failed_compile_is_served_from_the_cache(scratch):
    project, stock = _case()
    scratch.result = _failure()
    _, first = cc._cached_entry(project, stock)
    _from_disk()
    _, second = cc._cached_entry(project, stock)
    assert scratch.calls == 1
    assert second == first == _failure()
    assert not second.ok and second.errors == _failure().errors
    second.errors.append("mutated by one caller")
    _, third = cc._cached_entry(project, stock)
    assert third.errors == _failure().errors      # nothing mutable is shared between callers


def test_a_failed_compile_is_served_through_the_routing(scratch):
    project, stock = _case()
    scratch.result = _failure()
    cc._compile_through_cache(project, stock)
    _from_disk()
    assert cc._compile_through_cache(project, stock) == _failure()
    assert scratch.calls == 1


def test_a_corrupted_failure_blob_is_a_miss(scratch):
    project, stock = _case()
    scratch.result = _failure()
    cc._cached_entry(project, stock)
    (blob,) = scratch.dir.glob("*.bin")
    blob.write_bytes(blob.read_bytes()[:-1] + b"\0")
    _from_disk()
    _, again = cc._cached_entry(project, stock)
    assert scratch.calls == 2
    assert again == _failure()


def test_a_compile_that_raises_is_not_cached(scratch):
    project, stock = _case()
    scratch.result = RuntimeError("boom")
    for _ in range(2):
        with pytest.raises(RuntimeError):
            cc._cached_entry(project, stock)
        _from_disk()
    assert scratch.calls == 2
    assert not list(scratch.dir.glob("*.bin"))


def test_a_failure_that_json_would_alter_is_not_cached(scratch):
    project, stock = _case()
    scratch.result = recipe.CompileResult(False, None, [("a", "tuple")], [], [])
    _, first = cc._cached_entry(project, stock)
    _from_disk()
    _, second = cc._cached_entry(project, stock)
    assert scratch.calls == 2
    assert first.errors == second.errors == [("a", "tuple")]


def test_a_success_is_still_served(scratch):
    project, stock = _case()
    scratch.result = _success()
    cc._compile_through_cache(project, stock)
    _from_disk()
    assert cc._compile_through_cache(project, stock) == _success()
    assert scratch.calls == 1


# --- patches ----------------------------------------------------------------------------------


def _compiled_twice(scratch) -> int:
    project, stock = _case()
    scratch.result = _success()
    return _compiled_twice_as(scratch, project, stock)


def _compiled_twice_as(scratch, project, stock) -> int:
    for _ in range(2):
        assert cc._compile_through_cache(project, stock) == scratch.result
        _from_disk()
    return scratch.calls


def test_nothing_patched_hits(scratch):
    assert not cc._engine_patched()
    assert _compiled_twice(scratch) == 1


def test_an_engine_patch_from_a_fixture_in_another_file_bypasses(scratch,
                                                                 engine_patched_elsewhere):
    assert _compiled_twice(scratch) == 2
    assert not list(scratch.dir.glob("*.bin"))


def test_an_emulator_only_patch_still_hits(scratch, emulator_patched_elsewhere):
    assert _compiled_twice(scratch) == 1


def test_a_mock_patch_on_an_engine_function_bypasses(scratch):
    with mock.patch.object(recipe, "_prepare", wraps=recipe._prepare):
        assert _compiled_twice(scratch) == 2
    assert not cc._ACTIVE_MOCK_PATCHES


def test_a_started_mock_patch_bypasses_until_stopped(scratch):
    patcher = mock.patch("fireflymcf.recipe._prepare", wraps=recipe._prepare)
    patcher.start()
    try:
        assert cc._engine_patched()
    finally:
        patcher.stop()
    assert not cc._engine_patched()
    assert _compiled_twice(scratch) == 1


def test_a_mock_dict_patch_bypasses_in_both_forms():
    with mock.patch.dict(os.environ, {"FIREFLYMCF_CACHE_TEST": "1"}):
        assert cc._engine_patched()

    @mock.patch.dict(os.environ, {"FIREFLYMCF_CACHE_TEST": "1"})
    def decorated():
        return cc._engine_patched()

    assert decorated()
    assert not cc._engine_patched()


@pytest.mark.parametrize("how", ["setattr", "delattr", "setitem", "setenv", "delenv",
                                 "syspath_prepend", "chdir"])
def test_every_kind_of_monkeypatch_but_an_emulator_setattr_bypasses(monkeypatch, tmp_path, how):
    assert not cc._engine_patched()
    os.environ.setdefault("FIREFLYMCF_CACHE_TEST_DEL", "1")
    recipe._cache_test_attr = 1
    try:
        {
            "setattr": lambda: monkeypatch.setattr(recipe, "_prepare", recipe._prepare),
            "delattr": lambda: monkeypatch.delattr(recipe, "_cache_test_attr"),
            "setitem": lambda: monkeypatch.setitem({}, "k", 1),
            "setenv": lambda: monkeypatch.setenv("FIREFLYMCF_CACHE_TEST", "1"),
            "delenv": lambda: monkeypatch.delenv("FIREFLYMCF_CACHE_TEST_DEL"),
            "syspath_prepend": lambda: monkeypatch.syspath_prepend(str(tmp_path)),
            "chdir": lambda: monkeypatch.chdir(tmp_path),
        }[how]()
        assert cc._engine_patched()
        monkeypatch.undo()
        assert not cc._engine_patched()
    finally:
        del recipe._cache_test_attr
        os.environ.pop("FIREFLYMCF_CACHE_TEST_DEL", None)


def test_emulator_objects_are_told_apart_by_module():
    import fireflymcf.emu

    assert cc._emu_object(fireflymcf.emu)
    assert cc._emu_object(type("C", (), {"__module__": "fireflymcf.emu.peers"})())
    assert not cc._emu_object(types.ModuleType("fireflymcf.emulator"))
    assert not cc._emu_object(recipe)
    assert not cc._emu_object(recipe.Project)


def test_an_op_a_test_registered_bypasses(scratch):
    project, stock = _case()
    project.ops.append(recipe.OpSpec(id="t", type="cache_test_op", params={}))
    scratch.result = _failure()
    assert cc._foreign_ops(project)               # not registered at all

    @recipe.op("cache_test_op")
    class CacheTestOp(recipe.Op):
        pass

    try:
        assert cc._foreign_ops(project)           # registered, but not by fireflymcf
        assert _compiled_twice_as(scratch, project, stock) == 2
    finally:
        del recipe.REGISTRY["cache_test_op"]
    builtin = next(t for t, c in recipe.REGISTRY.items() if c.__module__.startswith("fireflymcf."))
    project.ops[0] = recipe.OpSpec(id="t", type=builtin, params={})
    assert not cc._foreign_ops(project)


def test_nothing_is_cached_before_session_start(scratch):
    cc._TRACKING[0] = False
    try:
        assert cc._engine_patched()
    finally:
        cc._TRACKING[0] = True


# --- pruning ----------------------------------------------------------------------------------

DAY = 86400.0


def _entry(where, name, tools, age, now, blob=True):
    meta = {"sha256": "0" * 64}
    if tools is not None:
        meta["tools"] = tools
    paths = [where / f"{name}.json"]
    paths[0].write_text(json.dumps(meta))
    if blob:
        paths.append(where / f"{name}.bin")
        paths[1].write_bytes(b"x" * 10)
    for p in paths:
        os.utime(p, (now - age, now - age))
    return paths


def _file(where, name, age, now):
    p = where / name
    p.write_bytes(b"")
    os.utime(p, (now - age, now - age))
    return p


def test_the_pruner_deletes_exactly_the_dead_entries(tmp_path):
    now = time.time()
    current, other = "c" * 64, "d" * 64
    gone = [*_entry(tmp_path, "stale", other, 2 * DAY, now),
            *_entry(tmp_path, "pre_change", None, 2 * DAY, now),
            _file(tmp_path, "orphan.bin", 2 * DAY, now),
            _file(tmp_path, "x.bin.1.ab.tmp", 2 * 3600, now)]
    kept = [*_entry(tmp_path, "fresh_other", other, 3600, now),
            *_entry(tmp_path, "old_current", current, 30 * DAY, now),
            _file(tmp_path, "fresh_orphan.bin", 60, now),
            _file(tmp_path, "y.json.1.cd.tmp", 600, now),
            _file(tmp_path, "held.lock", 1, now),
            _file(tmp_path, "abandoned.lock", 30 * DAY, now)]
    gone_bytes = sum(p.stat().st_size for p in gone)
    files, size = cc._prune_cache(tmp_path, current, now)
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(p.name for p in kept)
    assert files == len(gone)
    assert size == gone_bytes > 0


def test_session_start_prunes_on_the_controller_only(scratch, monkeypatch):
    scratch.dir.mkdir()
    stale = _entry(scratch.dir, "stale", "d" * 64, 2 * DAY, time.time())
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
    cc.pytest_sessionstart(None)
    assert all(p.exists() for p in stale)
    monkeypatch.delenv("PYTEST_XDIST_WORKER")
    cc.pytest_sessionstart(None)
    assert not any(p.exists() for p in stale)


def test_an_entry_records_the_digest_and_a_hit_touches_it(scratch):
    project, stock = _case()
    scratch.result = _success()
    cc._cached_entry(project, stock)
    (meta,) = scratch.dir.glob("*.json")
    assert json.loads(meta.read_text())["tools"] == cc._tools_digest()
    os.utime(meta, (time.time() - 2 * DAY,) * 2)
    _from_disk()
    cc._cached_entry(project, stock)
    assert time.time() - meta.stat().st_mtime < 60
    assert scratch.calls == 1
