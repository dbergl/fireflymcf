# Coach-Aware Placement for add_button — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** when a recipe's `coach` block or `compile --config FILE` names the coach's settings, `add_button` refuses an overlap in what that coach draws and reports the rest. A recipe without settings compiles exactly as today.

**Architecture:**
- A project gains an optional `coach` block (an `options.CoachConfig`).
- `compile_project` resolves it once, against the stock image, into a validated slot map. It accepts an override that replaces the block.
- The map travels on `Ctx` to `AddButton`.
- `newbutton.check_placement` takes the map:
  - it refuses in the walk the map selects and in every arm path consistent with it;
  - it reports every other path, as today.
- The CLI's `--config` supplies the override. The sidecar records what was used.

**Tech Stack:** Python 3.11+, pytest.

**Spec:** `docs/superpowers/specs/2026-11-09-coach-aware-placement-design.md`. Its numbered points (1-13) are cited below as "spec N".

## Global Constraints

- **Byte identity.** No build's bytes change. These must all pass:
  - the full fireflymcf suite;
  - the research byte pins (`cd ../firefly-firmware && .venv/bin/python -m pytest tests -q -n auto`, 457);
  - the owner pins (`../myfirmware/tests/pin_builds.py`, with fireflymcf's CLI as its argument, 4/4).
- **A recipe with no `coach` block and no override behaves exactly as today:**
  - the same `_prefix_key`;
  - the same `to_json` text;
  - the same refusals, reports and manifest lines.
- **Studio's `preview` never affects compile** (spec 3).
- **Refuse, never guess.** These are the new codes:
  - `COACH_CONFIG`: a bad slot, value, option or floorplan; a shared-base disagreement;
  - `ARM_PATH_LIMIT`: the walk passes 512 sets.

  Each is documented in `ops_doc` / the CLI docs, and `tests/test_ops_doc.py` checks add_button's codes.
- **The memo trap.** Anything memoised takes module constants (`DERIVED_ON`, `ARM_PATH_LIMIT`) as arguments.
- **Docs cite evidence by date (2026-11-09).** No build names; `scrub_check` exits 0.
- **Never modify** `tests/answer_key.py` data, the `.bin` corpus, `captures/` or the PDF.

## Review Focus

1. **A recipe whose `coach` block is present but empty (`{}`).** It must behave as "no settings", not as "every slot unnamed, strict". Task 1 tests that it resolves to `None`.
2. **The prefix cache and the test disk cache serving a result compiled under other settings.**
   - Task 1 puts the resolved settings into `_prefix_key` (only when set).
   - Task 1 also puts the override into `tests/conftest.py`'s compile key.
3. **`--config` given to a recipe that has no `add_button`.** The settings are still validated (they are a project-level statement), so a bad file refuses `COACH_CONFIG` even then. Task 3 tests it.
4. **13.6-only derivation on 14.7.** It must not inject slot 6 or slot 453 (spec 12). Task 1 tests a 14.7 resolution.
5. **A shared-base switch with one slot named** must count as unnamed, which is the strict rule (spec 13). Task 2 tests it on 13.6 base 441 (slots 204/208).

---

### Task 1: the `coach` block, its resolution and validation, and the cache keys

**Files:**
- Modify: `src/fireflymcf/options.py` (new `CoachSlots`, `coach_slots`, `DERIVED_ON`).
- Modify: `src/fireflymcf/recipe.py`:
  - `Project.coach` (load/`to_json`);
  - `compile_project(project, stock, *, coach=None, coach_source=None)`;
  - `Ctx.coach`;
  - `_prefix_key` and its callers, including `compile_incremental` (or whatever calls it at `recipe.py:761,776`).
- Modify: `studio/server/engine.py:188` (pass the settings to `_prefix_key`).
- Modify: `tests/conftest.py` (the compile key includes the override).
- Test: `tests/test_coach_slots.py` (new); `tests/test_recipe.py`.

**Interfaces (produced):**
- `options.DERIVED_ON: frozenset = frozenset({"13.6"})`: the firmwares where `derived_slots`' derivations were proven (spec 12).
- `options.CoachSlots`, a frozen dataclass:
  - `slots: dict[int, int]`: the resolved `{value_slot: value}`;
  - `pinned: tuple[int, ...]`: the sorted slots named by `extra_slots`;
  - `source: str`: `"recipe"` or `"--config <path>"`.
- `options.coach_slots(data: bytes, config: CoachConfig, source: str, derive_on: frozenset = DERIVED_ON) -> CoachSlots | None`.
  - **Resolution:** `slot_values(data, config)`. Then, only when `firmware.identify(data) in derive_on`, apply `derived_slots`, letting `extra_slots` win, as in `render_slots`.
  - **Empty:** `None` when `config` has no floorplan, no options and no extra_slots.
  - **Refusals** (`RecipeError("COACH_CONFIG", ...)`, message naming the slot or name):
    - a floorplan or option name `slot_values` cannot resolve;
    - a slot no type-6 switch record on the image reads (`value_slot`) and that `slot_values`/derivation did not produce;
    - a value no switch reading that slot has an arm for, unless one of those switches has a `0xFFFF` default arm;
    - a switch `child_base` whose records read two or more slots that the settings name with different values (spec 13).
  - **Memoised** through `memo`, keyed on bytes plus a canonical form of `config`, with `derive_on` as an argument.
- `Project.coach: dict` (default `{}`):
  - load accepts an object with the `CoachConfig` keys `floorplan`, `options` and `extra_slots`;
  - a `preview` key inside it, or any other key, refuses `BAD_PROJECT` (spec 4);
  - `to_json` writes it only when non-empty.
- `compile_project(project, stock, *, coach: CoachConfig | None = None, coach_source: str | None = None)`:
  - `coach` (the `--config` override) replaces `project.coach` whole (spec 5);
  - the resolved `CoachSlots | None` goes on `Ctx.coach` and `CompileResult.coach`, a new additive field (`dict`: `{"slots": {...}, "pinned": [...], "source": ...}` or `{}`);
  - a `COACH_CONFIG` refusal returns `CompileResult(ok=False, ...)` with the error, as `_prepare` errors do.
- `_prefix_key(base_hash, specs, tick_chain_limit=None, coach=None)`: a non-`None` `coach` (its resolved slots plus pinned) is appended to the key text exactly as `tick_chain_limit` is; `None` leaves the key unchanged.

- [ ] **Step 1: Write the failing tests** (`tests/test_coach_slots.py`):
  - `test_reference_settings_resolve_on_136`:
    - `coach_slots(stock136, CoachConfig.from_json("build/coach-wd.json"), "recipe").slots == {5:8, 6:1, 37:3, 38:0, 39:0, 108:1, 109:1, 110:1, 112:1, 113:0, 453:1, 512:1}`;
    - `pinned == (6, 37, 38, 39, 113, 512)`.

    The expected map is the builder check's, 2026-11-09. If `build/coach-wd.json` is absent, the test skips (it is linked research data).
  - `test_empty_block_is_no_settings`: `coach_slots(stock136, CoachConfig(), "recipe") is None`.
  - `test_147_skips_136_derivations`: `coach_slots(stock147, CoachConfig(extra_slots={102: 1}), "recipe").slots == {102: 1}`. No 6 and no 453.
  - `test_bad_settings_refuse`, parametrised; each raises `RecipeError` with `code == "COACH_CONFIG"`:
    - an unknown option name;
    - an unknown floorplan;
    - `extra_slots {60000: 1}` (no switch reads it);
    - `{102: 99}` on 14.7 (no such arm), unless slot 102's switch has a default arm, in which case pick a slot whose switches have none and say which in a comment;
    - `{204: 1, 208: 2}` on 13.6 (base 441 disagrees).
  - `test_project_coach_round_trip`:
    - a project with `"coach": {"extra_slots": {"6": 1}}` survives `from_json(to_json())`;
    - a project without one gives `to_json` byte-equal to today's (compare against a fixture string built from an existing test project);
    - `"coach": {"preview": {}}` refuses `BAD_PROJECT`.
  - `test_prefix_key`:
    - the key is unchanged with no coach;
    - it changes when `coach` changes;
    - it is unchanged when `preview` changes.
  - `test_override_replaces_block`: a project whose block says `{6: 2}`, compiled with `coach=CoachConfig(extra_slots={6: 1})`, has `result.coach["slots"][6] == 1` and `source` equal to the passed `coach_source`.
- [ ] **Step 2:** Run `.venv/bin/python -m pytest tests/test_coach_slots.py -q`. The tests fail: `coach_slots` is undefined.
- [ ] **Step 3:** Implement the interfaces above.
  - For the image's switch slots and arms, reuse what `pagewalk`/`records` already decode (type-6 records' `value_slot`, candidate values and `0xFFFF` default). Scan every object, not one page.
  - In `tests/conftest.py`, add the override (its canonical JSON) to the compile `_disk_memo` key list next to `project.to_json()`.
- [ ] **Step 4:** Run, and expect a pass:
  - the focused tests;
  - `tests/test_recipe.py`;
  - the fast tier (`-m "not slow"`);
  - `scrub_check`.
- [ ] **Step 5:** Commit: `"recipe: a coach block (or --config override) resolves to validated slot values"`.

### Task 2: the placement gate uses the settings

**Files:**
- Modify: `src/fireflymcf/newbutton.py` (`check_placement`, `Placement`, `build_home_button`, `placement_text`).
- Modify: `src/fireflymcf/recipe_ops.py` (`AddButton.apply` / `verify`, its docstring and refusal list).
- Modify: `src/fireflymcf/ops_doc.py`; regenerate `docs/RECIPE-OPS.md`.
- Test: `tests/test_newbutton.py`; `tests/test_ops_doc.py`.

**Interfaces:**
- Consumes `Ctx.coach: CoachSlots | None` (Task 1).
- `check_placement(data, root, obj, coach: dict[int, int] | None = None) -> Placement`:
  - **`coach is None`:** today's behaviour exactly.
  - **Otherwise:**
    - Map `coach` to `S`, a `switch_values` dict. Each switch `child_base` among the paths' `slots` is *named* when the coach sets every slot it reads, all to the same value. A disagreement already refused in Task 1.
    - **The refusal set** `R` = `{S}` ∪ `{S` overlaid with `p : p ∈ switch_arm_paths(...).sets`, where `p` agrees with `S` on every named base `}`.
    - Each walk in `R` applies today's base-arm rule: inside the canvas; touches only enclosing root wt-4 fills and one enclosing wt-33 card. A hit refuses `PlacementOverlap`, naming the arm path as `slot N = v` and the covered objects.
    - **The report:** every path not in `R` that covers something, in today's entry shape.
- `Placement.coach: bool`: whether the settings drove the gate.
- `ArmPathLimit` surfaces from `add_button` as `ARM_PATH_LIMIT` (spec 10), not `BUILD_REFUSED`. It applies with or without settings.
- The manifest line appends `; checked against the coach's settings (<source>)` when `Placement.coach`.

- [ ] **Step 1: Write the failing tests** (`tests/test_newbutton.py`):
  - `test_reference_settings_admit_the_136_default`:
    - with Task 1's resolved reference map, the default spot builds;
    - its bytes equal the existing pinned 13.6 hash;
    - `Placement.coach` is true;
    - the reported paths are all inconsistent with `S` (each sets slot 6 ≠ 1, or another named slot differently).
  - `test_settings_refuse_an_overlap_the_base_arm_admits`:
    - find a spot on 13.6 HOME that is clear in the all-base walk (card 901) but covers a record of card 902's arm (slot 6 = 1);
    - with `coach {6: 1}` it refuses `PLACEMENT_OVERLAP` and names `slot 6 = 1`;
    - without settings it builds and reports `slot 6 = 1`.

    Record the chosen spot and the object it covers in a comment, with the walk evidence.
  - `test_147_star_link`, default spot on stock 14.7:
    - `coach {102: 1}` refuses, naming object 240;
    - `coach {102: 0}` builds.
  - `test_unnamed_slot_is_strict`: on 14.7 with `coach {}` resolved to `None`, it builds (today's behaviour). With a coach that names some other slot but not 102, the default spot refuses on object 240 and names slot 102.
  - `test_shared_base_partly_named_is_unnamed`: with 13.6 base 441, naming only slot 204 treats the base as unnamed. Assert the walk set includes base 441's non-base arms, by inspecting which paths `check_placement` walked: expose them on `Placement` for the test, or test through a spot that only those arms cover.
  - `test_arm_path_limit_has_its_code`: with `pagewalk.ARM_PATH_LIMIT` passed as 10 through the op's path (monkeypatch the argument default, and clear the memo as the existing limit test does), `add_button` refuses `ARM_PATH_LIMIT`.
  - In `tests/test_ops_doc.py`, add_button's documented codes include `COACH_CONFIG` and `ARM_PATH_LIMIT`.
- [ ] **Step 2:** Run the new tests. They fail.
- [ ] **Step 3: Implement.**
  - `build_home_button` gains `coach: dict | None = None` and passes it to `check_placement`. `AddButton.apply` passes `ctx.coach.slots if ctx.coach else None`.
  - **Docs:**
    - Update AddButton's docstring (`at` and the refusal list) and `ops_doc`: what is walked, the one-switch-at-a-time limit (spec 10, with the 2026-11-09 exhaustive-check evidence), pinned live slots (spec 11), and the 13.6-only derivation (spec 12).
    - Regenerate `RECIPE-OPS.md` with the generator.
- [ ] **Step 4:** Run:
  - the focused tests;
  - the full suite (`.venv/bin/python -m pytest tests -q -n auto`);
  - the research pins (457);
  - the owner pins (4/4);
  - `scrub_check`.
- [ ] **Step 5:** Commit: `"add_button: the placement gate refuses in the arms the coach's settings draw"`.

### Task 3: `compile --config`, the sidecar and docs

**Files:**
- Modify: `src/fireflymcf/cli.py`:
  - `compile` gains `--config FILE`, passed to `compile_project` as `coach=CoachConfig.from_json(FILE)`, `coach_source="--config <FILE>"`;
  - the sidecar gains `"coach": result.coach` (`{}` when none);
  - the CLI help text and the module docstring.
- Modify: `docs/CLI.md` (or wherever `compile`'s flags are documented; find it), `docs/AGENT-GUIDE.md` (the project-file fields: `coach` beside `preview`, and that `preview` stays render-only) and `docs/COOKBOOK.md` if it lists project fields.
- Test: `tests/test_cli.py`.

- [ ] **Step 1: Write the failing tests** (`tests/test_cli.py`):
  - `test_compile_config_overrides_the_recipe`:
    - a recipe with `coach {"extra_slots": {"102": 1}}` and add_button at the default spot on 14.7 refuses `PLACEMENT_OVERLAP`;
    - the same recipe with `--config` naming `{"102": 0}` builds;
    - its sidecar has `coach.slots == {"102": 0}` and `coach.source` starts with `"--config"`.
  - `test_compile_bad_config_refuses_without_add_button`: a 13.6 recipe with one `rename_label` op and `--config` naming an unknown option exits 1, with error `COACH_CONFIG`, and writes nothing.
  - `test_sidecar_without_settings`: an existing 13.6 add_button recipe's sidecar has `"coach": {}`, and its manifest line has no "checked against" suffix.
- [ ] **Step 2:** Run the tests. They fail with "unrecognized arguments: --config".
- [ ] **Step 3:** Implement and update the docs.
- [ ] **Step 4:** Run the focused tests, the full suite and `scrub_check`.
- [ ] **Step 5:** Commit: `"compile --config: check a recipe against a coach's settings; the sidecar records them"`.

### Task 4: hand-off

- [ ] **Report to the owner:**
  - how to use it: the `coach` block, or `compile --config build/coach-wd.json`;
  - what the reference settings change on 13.6: nothing refused, and which reported paths remain;
  - 14.7 Star Link, with `{102: 1}`;
  - the limits:
    - one switch at a time;
    - pinned live slots;
    - the 13.6-only derivation;
  - a suggested `coach` block for the owner's next recipe (from `build/coach-wd.json`).

  Release only with the owner's approval.
