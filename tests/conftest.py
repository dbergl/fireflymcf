"""Corpus fixtures, and the shared recipe-compile cache.

The corpus files are read once per session — they total ~3 MB, so caching them avoids
re-reading on every test without any meaningful memory cost. The other half (bottom of the
file) routes every test's `recipe.compile_project` through a disk cache, so `pytest -n auto`
pays for each compile once instead of once per xdist worker.

The tests that pin the reference coach's builds (its recipes and committed images) live in the
private research repository, with a copy of this cache.
"""
import hashlib
import json
import os
import pickle
import time
import uuid
from pathlib import Path
from typing import NamedTuple

import pytest


def pytest_configure(config):
    """Distribute with worksteal under xdist. `--dist load` (xdist's default once `-n` is given) hands
    each worker a contiguous initial chunk it never gives back, which stranded the emulator tests on
    four workers (8m31s against 4m22s). Set here, not in addopts, so `-p no:xdist` still parses.
    An explicit `--dist load` is indistinguishable from the default and is also mapped."""
    opt = config.option
    if getattr(opt, "numprocesses", None) and getattr(opt, "dist", None) == "load":
        opt.dist = "worksteal"


def pytest_collection_modifyitems(items):
    """Everything under tests/emu/ boots an emulated image and is slow by construction; mark it so
    `-m "not slow"` is the edit-loop tier. Other slow tests carry the marker themselves."""
    here = Path(__file__).parent
    for item in items:
        path = Path(str(item.path))
        if path.is_relative_to(here) and "emu" in path.relative_to(here).parts[:1]:
            item.add_marker(pytest.mark.slow)


REPO_ROOT = Path(__file__).resolve().parent.parent

# Directory names are not uniform: the 14.7 directory has a space and a parenthesis.
VERSION_DIRS = {
    "11.23": "firefly-11.23",
    "13.6": "firefly-13.6",
    "14.7": "firefly-14.7 (Terrain)",
}


# ---------------------------------------------------------------------------------------
# Research data. The corpus, captures and configurations live in the private research repository
# and are symlinked in by `scripts/link-research.sh`. Without them, a test module that reaches
# for any of them (by path, or through a corpus fixture) is not collected, since several read
# the corpus at import time; the run's header says how many were left out. A module that slips
# past this scan fails loudly on a missing file rather than passing.
# ---------------------------------------------------------------------------------------

RESEARCH_PATHS = (*VERSION_DIRS.values(), "captures", "configs",
                  "build/coach-wd.json", "tests/answer_key.py")
_RESEARCH_MISSING = [p for p in RESEARCH_PATHS if not (REPO_ROOT / p).exists()]
# A module whose need is indirect (a library function reads a research path) declares it with a
# module-level `NEEDS_RESEARCH_DATA = "<why>"`; the scan below sees the name.
_RESEARCH_TOKENS = ("firefly-1", "captures", "configs", "coach-wd",
                    "answer_key", "corpus", "mcf_136", "mcf_1123", "mcf_147",
                    "cli_136", "cli_1123", "cli_147", "NEEDS_RESEARCH_DATA")


def pytest_ignore_collect(collection_path, config):
    if not _RESEARCH_MISSING or collection_path.suffix != ".py":
        return None
    if not collection_path.name.startswith("test_"):
        return None
    if any(token in collection_path.read_text() for token in _RESEARCH_TOKENS):
        return True
    return None


def pytest_report_header(config):
    if _RESEARCH_MISSING:
        return ("research data missing (" + ", ".join(_RESEARCH_MISSING) + "): modules that need "
                "it are not collected; run scripts/link-research.sh for the full suite")
    return None


def pytest_terminal_summary(terminalreporter):
    # Counted here: under xdist, collection happens in the workers.
    if _RESEARCH_MISSING:
        left_out = [p for p in (REPO_ROOT / "tests").rglob("test_*.py")
                    if any(token in p.read_text() for token in _RESEARCH_TOKENS)]
        terminalreporter.write_line(
            f"{len(left_out)} test modules not collected: they need research data "
            "(scripts/link-research.sh)")


@pytest.fixture(scope="session")
def corpus() -> dict[str, dict[str, bytes]]:
    out: dict[str, dict[str, bytes]] = {}
    for version, dirname in VERSION_DIRS.items():
        base = REPO_ROOT / dirname
        assert base.is_dir(), f"missing corpus directory: {base}"
        out[version] = {
            "mcf": (base / "mcf.bin").read_bytes(),
            "cli": (base / "cli.bin").read_bytes(),
        }
    return out


@pytest.fixture(scope="session")
def mcf_136(corpus) -> bytes:
    return corpus["13.6"]["mcf"]


@pytest.fixture(scope="session")
def mcf_1123(corpus) -> bytes:
    return corpus["11.23"]["mcf"]


@pytest.fixture(scope="session")
def mcf_147(corpus) -> bytes:
    return corpus["14.7"]["mcf"]


@pytest.fixture(scope="session")
def cli_136(corpus) -> bytes:
    return corpus["13.6"]["cli"]


@pytest.fixture(scope="session")
def cli_1123(corpus) -> bytes:
    return corpus["11.23"]["cli"]


@pytest.fixture(scope="session")
def cli_147(corpus) -> bytes:
    return corpus["14.7"]["cli"]


# ---------------------------------------------------------------------------------------
# The shared recipe-compile cache.
#
# A full compile is ~10 s of CPU. Under `pytest -n auto` every xdist worker is a separate
# process, so a per-module cache is one compile PER WORKER; this one is shared across workers
# on disk.
#
# `compile_project` is a pure function of three things -- the stock image, the exact recipe
# JSON, and the code in `src/` -- so the cache key is the SHA-256 of all three. Any edit to a
# library module, a packaged data file or a recipe changes the key, and a stale image can never
# be served. The cache lives under `build/` (gitignored, where generated images already go); one
# process compiles while holding a lock file and the rest wait on it and then read its result.
#
# A cached blob is also CHECKSUMMED, not merely present. The entry ordering below (blob first,
# metadata last) covers an ordinary interleaved write, but not a crash mid-write, and not any
# out-of-band mutation of a 1 MB file sitting in a gitignored directory, so the metadata
# carries `sha256(data)` and a mismatch is treated as a MISS: the reader recompiles and
# overwrites the bad entry rather than serving it. Each file is fsynced before its rename, so
# the rename cannot commit over unwritten data.
# ---------------------------------------------------------------------------------------

IMAGE_CACHE_DIR = REPO_ROOT / "build" / ".compiled-images"
# A compile is ~10 s. A lock file older than this can only mean its holder was killed, so it is
# broken rather than waited on -- the cache directory persists between runs, and a stale lock
# left by one Ctrl-C must not stall every run after it.
_STALE_LOCK_AFTER = 300.0
# Pruning (`_prune_cache`, at session start): an entry written under another `src/` digest can
# never be served again once nothing has used it for this long. A `.tmp` this old is a crash
# leftover of `_write_atomic`.
_PRUNE_ENTRY_AFTER = 86400.0
_PRUNE_TMP_AFTER = 3600.0
_MEMO: dict[str, "CompiledImage"] = {}


class _Entry(NamedTuple):
    """A successful `compile_project` result as stored: everything but the mutable lists."""
    data: bytes
    manifest: tuple[str, ...]
    unverified: tuple[str, ...]
    shifts: str          # `CompileResult.shifts` as JSON, so a served copy is never shared
    firmware: str | None = None


class _FailedEntry(NamedTuple):
    """A failed (`ok=False`) `compile_project` result as stored. The mutable fields are JSON, so
    every caller is served fresh lists and dicts."""
    data: bytes | None
    errors: str
    manifest: str
    unverified: str
    shifts: str
    sends: str
    firmware: str | None


def _tools_digest(_cache: list = []) -> str:
    if not _cache:
        h = hashlib.sha256()
        for path in sorted((REPO_ROOT / "src").rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            h.update(str(path.relative_to(REPO_ROOT)).encode())
            h.update(b"\0")
            h.update(path.read_bytes())
        _cache.append(h.hexdigest())
    return _cache[0]


class _Failed(Exception):
    """Raised by a producer whose result must not be cached; carries what to return."""

    def __init__(self, value):
        super().__init__()
        self.value = value


def _read_cached(blob, meta):
    # `meta` is written last, so its presence is what commits an entry -- and its recorded
    # digest is what makes the entry trustworthy. Anything unreadable, incomplete or not
    # matching its digest is a MISS, never a served value. A hit touches `meta`: its mtime is
    # "last used", which `_prune_cache` reads.
    if not meta.exists() or not blob.exists():
        return None
    try:
        digest = json.loads(meta.read_text())["sha256"]
        payload = blob.read_bytes()
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if hashlib.sha256(payload).hexdigest() != digest:   # corrupt, truncated, or tampered
        return None
    try:
        value = pickle.loads(payload)
    except Exception:  # noqa: BLE001 - an unreadable entry is a miss
        return None
    try:
        os.utime(meta)
    except OSError:
        pass
    return value


def _write_atomic(path, payload: bytes) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    with open(tmp, "wb") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())      # the rename must not be able to commit over unwritten data
    os.replace(tmp, path)


def _write_cached(blob, meta, value) -> None:
    payload = pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
    _write_atomic(blob, payload)
    _write_atomic(meta, json.dumps({"sha256": hashlib.sha256(payload).hexdigest(),
                                    "tools": _tools_digest()}).encode())


def _prune_cache(cache_dir=None, current=None, now=None) -> tuple[int, int]:
    """Delete the cache entries no run can be served any more: an entry pair whose meta records a
    `src/` digest other than `current` (or none) and was last used more than
    `_PRUNE_ENTRY_AFTER` ago, meta first so the entry is uncommitted before its blob goes; a
    blob with no meta that old; and a `.tmp` older than `_PRUNE_TMP_AFTER`. Locks are left to
    `_disk_memo`'s stale-lock rule. A run racing this one on another `src/` state can only lose
    an entry to a recompile: a missing entry is a miss. Returns (files, bytes) deleted."""
    cache_dir = IMAGE_CACHE_DIR if cache_dir is None else cache_dir
    current = _tools_digest() if current is None else current
    now = time.time() if now is None else now
    files = size = 0

    def unlink(path) -> None:
        nonlocal files, size
        try:
            n = path.stat().st_size
            path.unlink()
        except OSError:
            return
        files += 1
        size += n

    try:
        listing = list(os.scandir(cache_dir))
    except OSError:
        return 0, 0
    names = {e.name for e in listing}
    for e in listing:
        try:
            age = now - e.stat(follow_symlinks=False).st_mtime
        except OSError:
            continue
        path = Path(e.path)
        if e.name.endswith(".tmp"):
            if age > _PRUNE_TMP_AFTER:
                unlink(path)
        elif e.name.endswith(".json"):
            if age <= _PRUNE_ENTRY_AFTER:
                continue
            try:
                tools = json.loads(path.read_text()).get("tools")
            except (OSError, ValueError, AttributeError):
                tools = None
            if tools != current:
                unlink(path)
                unlink(path.with_suffix(".bin"))
        elif e.name.endswith(".bin") and e.name[:-4] + ".json" not in names:
            if age > _PRUNE_ENTRY_AFTER:
                unlink(path)
    return files, size


def _disk_memo(kind: str, parts: list, produce):
    """`produce()`, memoised in process and on disk across xdist workers under a key made of
    `kind`, the `src/` digest and `parts`. One process produces while holding a lock file; the
    rest wait and read its result. `produce` raises `_Failed(value)` for a result that must not
    be cached, and that value is returned as is."""
    key = hashlib.sha256(b"\0".join([kind.encode(), _tools_digest().encode(),
                                      *(x if isinstance(x, bytes) else x.encode()
                                        for x in parts)])).hexdigest()
    if key in _MEMO:
        return _MEMO[key]

    IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    blob = IMAGE_CACHE_DIR / f"{key}.bin"
    meta = IMAGE_CACHE_DIR / f"{key}.json"
    lock = IMAGE_CACHE_DIR / f"{key}.lock"

    value = _read_cached(blob, meta)
    while value is None:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            value = _read_cached(blob, meta)
            if value is not None:
                continue
            try:
                stale = time.time() - lock.stat().st_mtime > _STALE_LOCK_AFTER
            except FileNotFoundError:
                stale = False                      # the holder just finished; loop and read it
            if stale:
                lock.unlink(missing_ok=True)
            else:
                time.sleep(0.25)
            continue
        try:
            value = _read_cached(blob, meta)
            if value is None:
                try:
                    value = produce()
                except _Failed as failed:
                    return failed.value
                _write_cached(blob, meta, value)
        finally:
            os.close(fd)
            lock.unlink(missing_ok=True)

    _MEMO[key] = value
    return value


def _failed_entry(result):
    """`result` (`ok=False`) as a `_FailedEntry`, or None when JSON would not hand back an equal
    copy (a field that is not plain JSON), in which case it is not cached."""
    try:
        entry = _FailedEntry(result.data, json.dumps(result.errors), json.dumps(result.manifest),
                             json.dumps(result.unverified), json.dumps(result.shifts),
                             json.dumps(result.sends), result.firmware)
    except (TypeError, ValueError):
        return None
    if _served_failure(entry) != result:
        return None
    return entry


def _served_failure(entry: "_FailedEntry"):
    return _recipe.CompileResult(False, entry.data, json.loads(entry.errors),
                                 json.loads(entry.manifest), json.loads(entry.unverified),
                                 json.loads(entry.shifts), json.loads(entry.sends),
                                 entry.firmware)


def _cached_entry(project, stock: bytes):
    """`compile_project(project, stock)` through `_disk_memo`. Returns `(_Entry, None)` for a
    successful compile, or `(None, CompileResult)` for a failed one. A failure is as pure a
    function of the key as a success and is cached too (a fresh `CompileResult` per caller); one
    whose fields do not survive JSON is returned uncached, and a compile that raises is never
    cached."""
    def produce():
        result = _UNCACHED_COMPILE(project, stock)
        if not result.ok:
            entry = _failed_entry(result)
            if entry is None:
                raise _Failed(result)
            return entry
        return _Entry(result.data, tuple(result.manifest), tuple(result.unverified),
                      json.dumps(result.shifts), result.firmware)

    value = _disk_memo("compile", [hashlib.sha256(stock).hexdigest(), project.to_json()],
                       produce)
    if isinstance(value, _Entry):
        return value, None
    if isinstance(value, _FailedEntry):
        return None, _served_failure(value)
    return None, value


# ---------------------------------------------------------------------------------------
# Every test's `recipe.compile_project` goes through the cache above, unless something live can
# change what a compile does. A full recipe compile is 10-60 s and one run of the suite asked
# for 292 compiles of 198 distinct (recipe, stock) pairs, so a quarter of the suite's CPU went
# to compiling something another test, often on another worker, had already compiled.
#
# A patched engine must not be served an unpatched result (the memo trap in CLAUDE.md). The
# check is made per compile, against what is live at that moment, so it sees a patch made in any
# file and by a fixture of any scope:
# - every `pytest.MonkeyPatch` made from session start on is tracked (its `__init__` is wrapped;
#   collection, and so a test module's import-time code, comes after session start, while pytest's
#   own configure-time instances, which patch pytest internals for the whole session, come
#   before). The references are strong: an instance dropped without `undo()` leaves its patches in
#   place, so it must keep forcing the bypass. A compile runs for real if any tracked one has an
#   attribute patch on an object outside `fireflymcf.emu*` (which the compile path never
#   imports), or any `setitem`/`setenv`/`delenv`, `syspath_prepend` or `chdir`;
# - every `unittest.mock` patch is tracked while active (`_patch.__enter__`/`__exit__`,
#   `_patch_dict._patch_dict`/`_unpatch_dict`); any active one sends the compile through for real.
# Plain assignment to an engine attribute (`module.attr = ...`) is invisible to this check, so a
# test that changes the engine must do it through `monkeypatch` or `mock.patch`.
# Before session start nothing is tracked, so every compile runs for real. Outside a test's
# setup/call/teardown (collection, session fixtures) it compiles for real too, as does a compile
# with user ops loaded, one naming an op type that is not a `fireflymcf` op (one a test
# registered), or one with extra arguments. The key carries the digest of `src/`, so any library
# edit misses.
# ---------------------------------------------------------------------------------------

import types  # noqa: E402
from unittest import mock as _mock  # noqa: E402

from fireflymcf import recipe as _recipe  # noqa: E402

_UNCACHED_COMPILE = _recipe.compile_project
_CACHE_ACTIVE = [False]
_TRACKING = [False]
_LIVE_MONKEYPATCHES: list = []     # strong: see the comment above
_ACTIVE_MOCK_PATCHES: list = []


def _install_patch_tracking() -> None:
    mp_cls = pytest.MonkeyPatch
    probe = mp_cls()
    for field in ("_setattr", "_setitem", "_cwd", "_savesyspath"):
        # Fail loudly if pytest renames what `_engine_patched` reads.
        assert hasattr(probe, field), f"pytest.MonkeyPatch has no {field}"
    mp_init = mp_cls.__init__

    def init(self, *args, **kwargs):
        mp_init(self, *args, **kwargs)
        if _TRACKING[0]:
            _LIVE_MONKEYPATCHES.append(self)

    def entered(enter):
        if getattr(enter, "_cache_tracked", False):
            return enter

        def wrapper(self, *args, **kwargs):
            result = enter(self, *args, **kwargs)
            _ACTIVE_MOCK_PATCHES.append(self)
            return result
        wrapper._cache_tracked = True
        return wrapper

    def exited(exit_):
        if getattr(exit_, "_cache_tracked", False):
            return exit_

        def wrapper(self, *args, **kwargs):
            try:
                _ACTIVE_MOCK_PATCHES.remove(self)
            except ValueError:
                pass
            return exit_(self, *args, **kwargs)
        wrapper._cache_tracked = True
        return wrapper

    init._cache_tracked = True
    if not getattr(mp_init, "_cache_tracked", False):
        mp_cls.__init__ = init
    # `start`/`stop` and decoration go through these for `patch`, `patch.object` and
    # `patch.multiple`; `patch.dict`'s decorator bypasses its `__enter__`, so its inner pair.
    _mock._patch.__enter__ = entered(_mock._patch.__enter__)
    _mock._patch.__exit__ = exited(_mock._patch.__exit__)
    _mock._patch_dict._patch_dict = entered(_mock._patch_dict._patch_dict)
    _mock._patch_dict._unpatch_dict = exited(_mock._patch_dict._unpatch_dict)


_install_patch_tracking()


def _emu_object(obj) -> bool:
    try:
        name = obj.__name__ if isinstance(obj, types.ModuleType) else obj.__module__
    except Exception:  # noqa: BLE001 - an object we cannot place is not an emulator object
        return False
    return isinstance(name, str) and (name == "fireflymcf.emu"
                                      or name.startswith("fireflymcf.emu."))


def _engine_patched() -> bool:
    """True if anything live could change what a compile does (see the comment above)."""
    if not _TRACKING[0] or _ACTIVE_MOCK_PATCHES:
        return True
    for mp in list(_LIVE_MONKEYPATCHES):
        if mp._setitem or mp._cwd is not None or mp._savesyspath is not None:
            return True
        if any(not _emu_object(obj) for obj, _name, _old in mp._setattr):
            return True
    return False


def _foreign_ops(project) -> bool:
    """True if an op the project names is not a `fireflymcf` op: one a test registered (its code
    is not in `src/`, so not in the key), or one not registered at all (its failure would be wrong
    in a process that has registered it)."""
    for spec in project.ops:
        cls = _recipe.REGISTRY.get(spec.type)
        if cls is None or not str(getattr(cls, "__module__", "")).startswith("fireflymcf."):
            return True
    return False


def _compile_through_cache(project, stock, *args, **kwargs):
    # A compile with user ops loaded (`userop.registered`) reads op folders the key cannot see.
    if args or kwargs or not _CACHE_ACTIVE[0] or _engine_patched() or _recipe._user_ops() or \
            _foreign_ops(project) or \
            hashlib.sha256(stock).hexdigest() != project.base_sha256:
        return _UNCACHED_COMPILE(project, stock, *args, **kwargs)
    entry, failed = _cached_entry(project, stock)
    if failed is not None:
        return failed
    return _recipe.CompileResult(True, entry.data, [], list(entry.manifest),
                                 list(entry.unverified), json.loads(entry.shifts),
                                 firmware=entry.firmware)


_recipe.compile_project = _compile_through_cache


@pytest.hookimpl(tryfirst=True)
def pytest_sessionstart(session):
    _TRACKING[0] = True
    # The controller only: xdist workers start together and would race one another.
    if not os.environ.get("PYTEST_XDIST_WORKER"):
        _prune_cache()


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    _CACHE_ACTIVE[0] = True


@pytest.hookimpl(trylast=True)
def pytest_runtest_teardown(item, nextitem):
    _CACHE_ACTIVE[0] = False
