# Plan 8: Test-Suite Speed — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** the full suite runs in about half its current wall time, a `-m "not slow"` fast tier runs in about 1–1.5 minutes for the edit loop, and the compile cache stops growing without bound. No test asserts less than it does today.

**Architecture:** the time is lost to scheduling, not test cost. xdist's default `--dist load` hands each worker a contiguous chunk of 60 tests and never takes it back, and `tests/emu/` (187 tests, ~1023 worker-s) is collected first, so it all lands on 4 workers. Change the distribution, add a `slow` marker, make the conftest compile cache cover failed compiles and detect patches dynamically, prune dead cache entries, and share a few duplicated fixtures.

**Tech Stack:** Python 3.11+, pytest, pytest-xdist 3.8.0.

**Spec:** none; this plan is infrastructure. The owner chose it on 2026-11-09 ("All three, in order": the test speedup first). Rulings made without a spec are provisional.

**Research (2026-11-09), the evidence this plan cites.** The report is `scratchpad/testspeed.md` (session scratchpad). It was measured on main `4f06469` on 16 threads with a warm cache.
- **Wall time:** 504 s. The tests sum to 3283 worker-s (205 s if spread over 16 workers), and 11 of the 16 workers were idle from about 170 s.
- **Scheduler replay** (`scratchpad/sim.py`; it reproduced today's run as 492 s simulated vs 495 s real):
  - `worksteal`: 249 s;
  - `--maxschedchunk 1`: 241 s;
  - `--maxschedchunk 8`: 235 s.
- **Failed compiles are never cached** (`tests/conftest.py` `_cached_entry`). They cost 217 worker-s across 334 calls, 130 s of it in `test_button_status`, where each refusal first builds a 4-op `BASE`.
- **`_may_patch` is textual.** It bypasses the cache for 275 items.
  - `emu/test_ops147` and `emu/test_hook147` bypass only because of a clock patch on `fireflymcf.emu` objects, which the compile path never imports.
  - The rule also cannot see a patch made by a fixture defined in another file.
- **Cache size:** fireflymcf's `build/.compiled-images` is 23 GB and 44k files; firefly-firmware's is 30 GB. Only entries for the current `src/` digest can ever hit.
- **Duplicated work:**
  - `test_ops147` settles stock 14.7 five times; two of those are identical rail shots.
  - `test_button_status` and `test_enum_label` each compile one spy recipe twice.
  - `test_repaint`'s `runs` fixture builds two images serially, in one 86 s setup.

## Global Constraints

- **No assertion is weakened, removed or skipped.** Every changed run reports the same `passed / skipped / xfailed` counts as today: fireflymcf 3843/2/2, research 457.
- **The cache never serves an engine that a test has patched.** This is the memo trap in CLAUDE.md. A stricter bypass is allowed; a looser one is allowed only for patches on `fireflymcf.emu*` objects, which the compile path cannot reach.
- **A cache entry that is missing, partial, corrupt or deleted is a miss**, never a wrong image. Pruning may only ever cost a recompile.
- **The full suite stays the merge gate.** The fast tier is for the edit loop only, and docs must say so.
- **Never modify** `tests/answer_key.py` data, the `.bin` corpus, `captures/` or the PDF. No build names in fireflymcf docs; `scrub_check` exits 0.
- **Research-repo changes** go on a branch `test-speed-engine` in `../firefly-firmware`. Its CLAUDE.md is the owner's file: suggest edits, don't make them.
- **Public CI** (`.github/workflows/test.yml`, `-n auto`, data-free) must keep passing. A plain `pytest` run with no `-n` must keep working.

## Review Focus

1. **`--dist` in `addopts` without `-n`.** Check whether xdist 3.8.0 errors or misbehaves when `--dist worksteal` is given without workers. If it does, set the distribution in `pytest_configure` only when `numprocesses` is set. Test: run `pytest tests/test_corpus.py -q` with no `-n`.
2. **Caching a failure whose `errors` text is not deterministic** (an address, a set order, an object repr) would freeze one run's text. Task 3 compiles every failing recipe twice and compares, and a compile that raises stays uncached.
3. **A patch made in a fixture in another file, or with `unittest.mock.patch`,** must still bypass the cache. Task 3 has a test for each.
4. **Pruning racing a concurrent run in the same checkout** (on a different `src/` state). Only entries for a different digest that are older than 1 day are deleted; locks are left alone.
5. **A shared fixture that a test mutates** would make results depend on order. Task 4's shared objects are read-only; confirm by running each module with `-p no:randomly` and in reverse order (`-p no:cacheprovider --reverse` if `pytest-reverse` is available, otherwise by `-k` subsets).

---

### Task 1: distribution

**Files:** `pyproject.toml` (or `tests/conftest.py` `pytest_configure`, per Review Focus 1); `docs/TESTING.md`; `CLAUDE.md` (fireflymcf) test-command lines.

- [ ] **Steps:**
  1. Run the full suite three ways, each once on a warm cache, with `-n 16 -p no:cacheprovider`:
     - today's default;
     - `--dist worksteal`;
     - `--dist load --maxschedchunk 1`.
     Record the wall time and counts of each in the report.
  2. Adopt the fastest as the default, so a bare `pytest tests -n auto` uses it.
     - Optional, only if a measured run shows at least 15 s more: order slow-marked items first, stable within each module. This depends on Task 2's marker, so if adopted it lands in Task 2.
  3. Verify:
     - a run without `-n` still works (Review Focus 1);
     - counts are 3843/2/2.
  4. Update `docs/TESTING.md` and CLAUDE.md's command lines with the measured time.
  5. Commit: `"tests: distribute with <mode>; the emulator tests no longer queue on four workers"`.

### Task 2: fast tier

**Files:** `pyproject.toml` (register `markers = ["slow: ..."]`); `tests/conftest.py` (`pytest_collection_modifyitems`: auto-mark everything under `tests/emu/`); the non-emu modules in `scratchpad/slow-tests.txt` (183 tests of 3 s or more; mark per test, or `pytestmark` where a whole module is slow); `docs/TESTING.md`, CLAUDE.md.

- [ ] **Steps:**
  1. **Test `test_every_emu_module_is_slow`** (in a test module that runs in the fast tier): every collected item under `tests/emu/` carries the `slow` marker. Check it can fail by disabling the auto-mark.
  2. Mark the slow non-emu tests. Use the measured list; a test under 3 s stays unmarked even if its module is slow.
  3. **Verify:**
     - `--collect-only -m slow` plus `--collect-only -m "not slow"` equals the full collection;
     - time `-m "not slow" -n 16` and record it (target under 90 s);
     - the full suite's counts are unchanged.
  4. **Docs:**
     - `docs/TESTING.md` gives the fast command for the edit loop, and the full suite as the gate before merge and after any engine change;
     - CLAUDE.md gets the fast command next to the full one.
  5. Commit: `"tests: a slow marker; -m 'not slow' is the edit-loop tier"`.

### Task 3: the compile cache: failures, dynamic patch detection, pruning

**Files:** `tests/conftest.py`; tests in a new `tests/test_compile_cache.py`. Mirror the same changes in `../firefly-firmware/tests/engine/conftest.py` on branch `test-speed-engine`.

- [ ] **Steps:**
  1. **Failed compiles are cached.**
     - Store an `ok=False` result as its own entry kind under the same key. Return the same `CompileResult` shape as today: `errors` preserved, and nothing mutable shared between callers.
     - A compile that raises stays uncached.
     - Before enabling it, compile every failing recipe seen in a full run twice in fresh processes and assert identical `errors` JSON. Do this as a one-off script; record the result in the report.
  2. **Dynamic patch detection replaces the textual `_may_patch`.**
     - Track live `pytest.MonkeyPatch` instances (a `WeakSet`, registered by wrapping `__init__`).
     - At compile time, bypass the cache if any live instance has a `setattr` whose target is outside `fireflymcf.emu*`. The target's module is `obj.__name__` for a module, else `obj.__module__`.
     - Also bypass on any `setitem`, `setenv`/`delenv`, `syspath_prepend` or `chdir`, or on any active `unittest.mock` patch. Wrap `mock._patch.__enter__`/`start`, or, if that proves brittle, refuse `mock.patch` in tests with a scrub test.
     - Keep the user-op, non-stock-base and `args/kwargs` bypasses, and "inactive outside setup/call/teardown".
  3. **Pruning.**
     - Each entry's meta records `"tools": _tools_digest()`, and a hit touches the meta's mtime.
     - In `pytest_sessionstart` on the controller only (no `PYTEST_XDIST_WORKER`), delete:
       - entry pairs whose recorded digest differs from the current one (or is absent) and whose meta mtime is more than 1 day old, meta first, then the blob;
       - `*.tmp` older than 1 hour.
     - Locks are handled only by the existing stale-lock rule.
  4. **Tests in `tests/test_compile_cache.py`**, against a scratch cache directory: monkeypatch `IMAGE_CACHE_DIR` and drive `_disk_memo` and the pruner directly.
     - A failed compile is served from the cache on a second call, with equal `errors`. A corrupted failure blob is a miss.
     - A patch on an engine function made by a fixture defined in `tests/conftest`-level helper code (not in the test module) bypasses the cache. This fails under the old textual rule; show that by stubbing.
     - A patch on a `fireflymcf.emu` object alone still hits.
     - A `mock.patch` on an engine function bypasses (or the scrub test refuses it).
     - The pruner deletes exactly the stale-digest entries older than a day and old `.tmp` files. A fresh other-digest entry and a current-digest entry survive. A held lock survives.
  5. **Verify:** the full suite twice in a row with counts unchanged; the second run is warm and failures hit. The research suite on its branch: 457 passed. `scrub_check` exits 0.
  6. Commit in each repo:
     - fireflymcf: `"tests: the compile cache keeps failures, sees patches from any file, and prunes dead entries"`;
     - firefly-firmware: `"engine tests: mirror fireflymcf's compile-cache changes"`.

### Task 4: shared fixtures

**Files:** `tests/emu/test_ops147.py`, `tests/test_button_status.py`, `tests/test_enum_label.py`, `tests/test_repaint.py`.

- [ ] **Steps:**
  1. **test_ops147:** a module fixture `stock_rails` holds `_rail_shots(_settled(mcf_147))` once. `test_add_text_label` and `test_set_theme` read it. Any other identical stock settle with the same pinned clock shares it too.
  2. **test_button_status and test_enum_label:** the two `_spy_apply` tests in each module share one spy compile through a module fixture. The fixture uses `pytest.MonkeyPatch.context()`, so the spy is never live outside it.
  3. **test_repaint:** split `runs` into one module fixture per image, so two workers can share the 86 s.
  4. **Verify:**
     - every changed test body differs only in where `seen`/`stock`/`runs` comes from (diff in the report);
     - each module three times, and once in reverse `-k` order, all passing;
     - the full suite's counts are unchanged; record the final wall time.
  5. Commit: `"tests: share identical stock settles and spy compiles"`.

### Task 5: hand-off

- [ ] **Report to the owner:**
  - wall times before and after (full warm, full cold if measured, fast tier);
  - disk reclaimed;
  - a suggested line for firefly-firmware's CLAUDE.md, if its commands change.

  **Deferred and named:**
  - test_cli running the CLI in-process instead of in subprocesses (about 3–6 s);
  - the emulator's Python hot path (`_on_intr`, `next_event`, `take_pending`), about 10–25 % of emulator time;
  - an emulator snapshot facility (not recommended).
