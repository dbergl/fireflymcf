# Testing — commands, parallel runs, and the speed pass

```
python3 -m pytest tests/ -q -n auto               # the whole suite: ~4 min after a src/ edit, ~80 s otherwise
python3 -m pytest tests/test_x.py -v              # serial, one module; use it to see WHY something is red
npm test --prefix studio/web                      # the web editor's tests
```

`pytest-xdist` is installed with `sudo apt install python3-pytest-xdist`; PEP 668 blocks
`pip install` into the system Python here.

`unicorn` (the CPU emulator the routine tests run authored GuiCode on, `tests/emu.py`) is a
required test dependency. It has no apt package at a usable version, so install it per-user the
way capstone is: `python3 -m pip install --user --break-system-packages 'unicorn>=2.1'`. Elsewhere,
`pip install -e '.[full,test]'`. Without it the routine tests fail at import, by design.

A hazard when running in parallel, inert today:

- `test_static_mount_never_shadows_api` creates and deletes `studio/web/dist/index.html` when the
  web bundle is absent. Harmless once `npm run build --prefix studio/web` has run.

**The shared compile cache** (`tests/conftest.py`, `build/.compiled-images/`). Every test's
`recipe.compile_project` goes through a disk cache shared by all xdist workers, keyed on the digest
of `src/`, the canonical project JSON and the stock hash, so a recipe compiles once per code
version, not once per test or per worker. Editing anything under `src/` invalidates it, which is
why the first run after an edit is the slow one. A test that requests `monkeypatch` (or a module
using `pytest.MonkeyPatch`) always compiles for real: a patched engine must never be served an
unpatched result. Delete the directory to force a cold run.

Two test-hygiene rules from `CLAUDE.md` that apply here:

- **Check a test can fail.** Stub the function under test to return empty and confirm the test
  breaks; several tests here once passed on empty results.
- **Clear `__pycache__` between mutation runs.** Python invalidates bytecode on mtime (one-second
  granularity) plus size, so a same-length edit within the same second runs stale bytecode:
  `find tests -name __pycache__ -type d -exec rm -rf {} +`.

## The reference coach's builds

The tests that read the reference coach's builds — its recipes and committed images, the
auto-discovery check that every recipe compiles green through the gates, and the byte-for-byte
pins of frozen builds — live in the private research repository, with their own copy of the
compile cache (and its `compile_with_handles`, `compile_prefix_seed` and `shared_memo` fixtures).
Run them there after any engine change. The tests here need only the stock corpus, the captures
and the render configurations, or nothing.

---

## Moved out of `CLAUDE.md` on 2026-09-10 — the 2026-09-01 speed pass and the parallel-run measurements

Verbatim, as part of the documentation cleanup. Dated statements below describe the state on the date they carry.

**The 2026-09-01 speed pass (serial 26m49s → 4m42s; parallel 6m49s → 60 s; `from_stock.py`
67 s → 7.5 s, byte-identical output).** Three things, all in the library, none in the tests:
the CRC-16 is `binascii.crc_hqx` (the pure-Python bit loop was 44% of a build — every reseal
recomputes nine checksums, three over whole sections; `_crc16_aug_ccitt_reference` keeps the
definition and a test pins them equal); the pure `f(data)` readers are memoised on content
(`memo.py`, the trap above — 2,585 binding-table parses and 29 GuiCode disassemblies per build
became one each); and the pointer-literal scans use capstone's `disasm_lite` (no `CsInsn`
objects; compared literal-for-literal against the operand-object form on all three builds).
The remaining tail is real work: the full recipe compiles (~10 s each) and a handful of
GuiCode tracers that still run once per build per worker.

**Running the suite in parallel** (measured 2026-08-21: 26m49s → **3m56s**, same 1062/1/3).
`pytest-xdist` forks **processes**; threads would not help, since this is CPU-bound pure Python
and the GIL would serialise it. Install with `sudo apt install python3-pytest-xdist` — PEP 668
blocks `pip install` into the system Python here. Plain `-n auto` is the fastest mode: `--dist
loadscope` looks better on paper (many modules have expensive module-scoped fixtures, which
plain `load` re-runs per worker) and measured **slower**, 4m53s, because finer-grained balancing
wins by more than the duplicated fixtures cost. `--dist worksteal` is no better either (3m57s):
it rebalances a worker's *queue*, and the tail here is one irreducible 60s test
(`test_statusbind`), not a queue. That test is the floor, so there is little left without
splitting it.

Deliberately **not** in a `pytest.ini` `addopts`: xdist interleaves output, disables `pdb` and
makes `-x` unpredictable. Parallel answers *"is it green"*; run serial to find out *why it is
red*. A shared-path hazard, inert today but worth knowing before trusting a green run:
- `test_static_mount_never_shadows_api` creates `studio/web/dist/index.html` when the web bundle
  is absent and deletes it afterwards; a concurrent worker's `create_app` can see that flicker.
  Harmless while `dist` exists, a real race on a clean checkout that has never run `npm run
  build`.

## The 2026-10-16 speed pass: 13m43s → 3m46s cold, 1m17s warm

Measured with `-n auto` on 16 cores, 2,548 tests. Compiles were 3,729 s of the suite's 4,926 s of
test time; one run asked for 292 compiles of 198 distinct (recipe, stock) pairs.

- **The engine** (every user's compile, not only the tests): a v50 compile went from 119 s to 37 s,
  and all 28 reference recipes compile byte-identical to before (sha256 compared). GuiCode's
  literal scan and `tablegrow`'s `cmp.w` scan are memoised on the GuiCode section's bytes rather
  than the file's, since a GuiData edit never changes GuiCode; the two object-table scans share a
  numpy run finder (`wordruns.py`, pure-Python fallback, the two held equal by
  `tests/test_wordruns.py`); and `relocate._boundary_oracle` caches each object's decode on the
  object's own bytes, re-decoding in place whenever that does not settle it.
- **The tests**: the shared compile cache above, extended from a few modules to every
  compile, plus `compile_with_handles` and `compile_prefix_seed` for `test_level_page`, which kept
  its own per-process cache and so recompiled its recipes on every worker.
- `--dist worksteal` measured even again (17 s faster cold, 6 s slower warm), so the default
  scheduling stays.
