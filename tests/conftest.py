"""Corpus fixtures, and the shared recipe-compile cache.

The corpus files are read once per session — they total ~3 MB, so caching them avoids
re-reading on every test without any meaningful memory cost. `compiled_image` (bottom of the
file) is the other half: the in-test recipe compiles that replaced the untracked local .bin
files several modules used to read, memoised so `pytest -n auto` pays for each one once
instead of once per xdist worker.
"""
import copy
import hashlib
import json
import os
import pickle
import time
import uuid
from pathlib import Path
from typing import NamedTuple

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Directory names are not uniform: the 14.7 directory has a space and a parenthesis.
VERSION_DIRS = {
    "11.23": "firefly-11.23",
    "13.6": "firefly-13.6",
    "14.7": "firefly-14.7 (Terrain)",
}


# ---------------------------------------------------------------------------------------
# Research data. The corpus, captures, recipes and docs live in the private research repository
# and are symlinked in by `scripts/link-research.sh`. Without them, a test module that reaches
# for any of them (by path, or through a corpus fixture or `compiled_image`) is not collected,
# since several read the corpus at import time; the run's header says how many were left out. A
# module that slips past this scan fails loudly on a missing file rather than passing.
# ---------------------------------------------------------------------------------------

RESEARCH_PATHS = (*VERSION_DIRS.values(), "captures", "builds", "configs",
                  "build/coach-wd.json", "tests/answer_key.py")
_RESEARCH_MISSING = [p for p in RESEARCH_PATHS if not (REPO_ROOT / p).exists()]
# A module whose need is indirect (a library function reads a research path) declares it with a
# module-level `NEEDS_RESEARCH_DATA = "<why>"`; the scan below sees the name.
_RESEARCH_TOKENS = ("firefly-1", "captures", "builds", "configs", "coach-wd",
                    "compiled_image", "answer_key", "corpus", "mcf_136", "mcf_1123", "mcf_147",
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
        left_out = [p for p in (REPO_ROOT / "tests").glob("test_*.py")
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
# Three test modules now compile a recipe IN-TEST rather than reading an untracked, gitignored
# .bin that no script in this tree produces: `test_registers.py` (the v9 characterization
# oracle and the full v14 build), `test_aes_layout_nav.py` (the full build, plus a two-op AES
# subset), and `test_pumpauto.py` (the pre-append v12 image the scratch allocation is derived
# from). A full compile is ~10 s of CPU.
#
# Under `pytest -n auto` every xdist worker is a separate process, so a per-module cache is one
# compile PER WORKER. Measured on a 16-way run: adding test_pumpauto's compile behind a plain
# module-level cache took the suite from 77 s to 117 s, because its 28 image tests are spread
# across workers and each worker paid the compile again. With this cache it is back to the
# noise around the original number.
#
# `compile_project` is a pure function of three things -- the stock image, the exact recipe
# JSON, and the code in `tools/` -- so the cache key is the SHA-256 of all three. Any edit to a
# tool module, a packaged data file or a recipe changes the key, and a stale image can never be
# served. The cache lives under `build/` (gitignored, where generated images already go); one
# process compiles while holding a lock file and the rest wait on it and then read its result.
#
# A cached blob is also CHECKSUMMED, not merely present. The entry ordering below (blob first,
# metadata last) covers an ordinary interleaved write, but not a crash mid-write, and not any
# out-of-band mutation of a 1 MB file sitting in a gitignored directory. A cache outlives the
# consumers that happen to pin their result today -- `test_aes_layout_nav`'s two-op subset pins
# nothing -- so the metadata carries `sha256(data)` and a mismatch is treated as a MISS: the
# reader recompiles and overwrites the bad entry rather than serving it. Each file is fsynced
# before its rename, so the rename cannot commit over unwritten data.
# ---------------------------------------------------------------------------------------

IMAGE_CACHE_DIR = REPO_ROOT / "build" / ".compiled-images"
# A compile is ~10 s. A lock file older than this can only mean its holder was killed, so it is
# broken rather than waited on -- the cache directory persists between runs, and a stale lock
# left by one Ctrl-C must not stall every run after it.
_STALE_LOCK_AFTER = 300.0
_MEMO: dict[str, "CompiledImage"] = {}


class CompiledImage(NamedTuple):
    data: bytes
    unverified: tuple[str, ...]


class _Entry(NamedTuple):
    """A successful `compile_project` result as stored: everything but the mutable lists."""
    data: bytes
    manifest: tuple[str, ...]
    unverified: tuple[str, ...]


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
    # matching its digest is a MISS, never a served value.
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
        return pickle.loads(payload)
    except Exception:  # noqa: BLE001 - an unreadable entry is a miss
        return None


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
    _write_atomic(meta, json.dumps({"sha256": hashlib.sha256(payload).hexdigest()}).encode())


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


def _cached_entry(project, stock: bytes):
    """`compile_project(project, stock)` through `_disk_memo`. Returns `(_Entry, None)` for a
    successful compile, or `(None, CompileResult)` for a failed one, which is never cached
    (failures are fast and their errors are the test's subject)."""
    def produce():
        result = _UNCACHED_COMPILE(project, stock)
        if not result.ok:
            raise _Failed(result)
        return _Entry(result.data, tuple(result.manifest), tuple(result.unverified))

    value = _disk_memo("compile", [hashlib.sha256(stock).hexdigest(), project.to_json()],
                       produce)
    return (value, None) if isinstance(value, _Entry) else (None, value)


def compiled_image(project_json: str, stock: bytes) -> CompiledImage:
    """Compile `project_json` against `stock` through the shared cache.

    Returns the image bytes and the recipe's unverified manifest. Raises through
    `recipe`'s own errors; asserts the project's `base_sha256` really is this stock.
    """
    from fireflymcf import recipe

    project = recipe.Project.from_json(project_json)
    assert hashlib.sha256(stock).hexdigest() == project.base_sha256, "wrong stock base"
    entry, failed = _cached_entry(project, stock)
    assert failed is None, failed.errors
    return CompiledImage(entry.data, entry.unverified)


@pytest.fixture(scope="session")
def compile_with_handles():
    """`f(project, stock) -> (CompileResult, handles)`: a compile plus the op handles of its
    final state (`compile_cached`'s last prefix snapshot), shared across workers like every
    other compile. For tests that inspect what an op recorded (cells, groups, arms)."""
    from fireflymcf import recipe

    def run(project, stock):
        def produce():
            cache: dict = {}
            res = recipe.compile_cached(project, stock, cache)
            if not res.ok:
                raise _Failed((res, None))
            enabled = [s for s in project.ops if s.enabled]
            _data, snap = cache[recipe._prefix_key(project.base_sha256, enabled)]
            return (_Entry(res.data, tuple(res.manifest), tuple(res.unverified)), snap[2])

        value = _disk_memo("handles", [hashlib.sha256(stock).hexdigest(), project.to_json()],
                           produce)
        entry, handles = value
        if not isinstance(entry, _Entry):          # a failed compile, returned uncached
            return entry, handles
        return (recipe.CompileResult(True, entry.data, [], list(entry.manifest),
                                     list(entry.unverified)), copy.deepcopy(handles))

    return run


@pytest.fixture(scope="session")
def shared_memo():
    """`f(kind, parts, produce)`: `produce()` cached across tests and xdist workers under
    (`kind`, the `src/` digest, `parts`). For an expensive pure result -- a page render of a fixed
    image -- that several tests or parameters ask for."""
    return _disk_memo


@pytest.fixture(scope="session")
def compile_prefix_seed():
    """`f(project, stock, upto) -> dict`: a fresh `compile_cached` cache holding only the state
    after the project's first `upto` enabled ops, shared across workers. A test that patches the
    op after the prefix re-applies just that op (and what follows) under its patch."""
    from fireflymcf import recipe

    def run(project, stock, upto):
        enabled = [s for s in project.ops if s.enabled][:upto]
        key = recipe._prefix_key(project.base_sha256, enabled)
        prefix = recipe.Project(base_sha256=project.base_sha256,
                                build_name=project.build_name, ops=enabled)

        def produce():
            cache: dict = {}
            res = recipe.compile_cached(prefix, stock, cache)
            assert res.ok, res.errors
            return cache[key]

        state = _disk_memo("prefix", [hashlib.sha256(stock).hexdigest(), prefix.to_json()],
                           produce)
        return {key: copy.deepcopy(state)}

    return run


# ---------------------------------------------------------------------------------------
# Every test's `recipe.compile_project` goes through the cache above, unless the test can
# change what a compile does. A full recipe compile is 10-60 s and one run of the suite asked
# for 292 compiles of 198 distinct (recipe, stock) pairs, so a quarter of the suite's CPU went
# to compiling something another test, often on another worker, had already compiled.
#
# A test that requests `monkeypatch`, or a module that uses `pytest.MonkeyPatch` directly,
# compiles for real: a patched engine must not be served an unpatched result (the memo trap in
# CLAUDE.md). Outside a test's setup/call/teardown (collection, session fixtures) it compiles
# for real too. The key carries the digest of `src/`, so any library edit misses.
# ---------------------------------------------------------------------------------------

from fireflymcf import recipe as _recipe  # noqa: E402

_UNCACHED_COMPILE = _recipe.compile_project
_CACHE_ACTIVE = [False]


def _compile_through_cache(project, stock, *args, **kwargs):
    if args or kwargs or not _CACHE_ACTIVE[0] or \
            hashlib.sha256(stock).hexdigest() != project.base_sha256:
        return _UNCACHED_COMPILE(project, stock, *args, **kwargs)
    entry, failed = _cached_entry(project, stock)
    if failed is not None:
        return failed
    return _recipe.CompileResult(True, entry.data, [], list(entry.manifest),
                                 list(entry.unverified))


_recipe.compile_project = _compile_through_cache


def _may_patch(item) -> bool:
    if "monkeypatch" in getattr(item, "fixturenames", ()):
        return True
    try:
        return "MonkeyPatch" in Path(str(item.fspath)).read_text()
    except OSError:
        return True


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    _CACHE_ACTIVE[0] = not _may_patch(item)


@pytest.hookimpl(trylast=True)
def pytest_runtest_teardown(item, nextitem):
    _CACHE_ACTIVE[0] = False
