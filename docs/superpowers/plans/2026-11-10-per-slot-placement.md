# Per-slot coach settings in the placement gate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `add_button`'s coach-aware placement gate drives each named setting by its own value slot (`slot_values`), so a switch base read by independent slots no longer refuses COACH_CONFIG.

**Architecture:** `pagewalk.switch_arm_paths` gains `slot_values`: its walks follow the settings, and a switch record whose `value_slot` the settings name is pinned (its arms are not enumerated). `newbutton.check_placement` with settings walks every path that enumeration returns, each as `walk(slot_values=coach, switch_values=sv)`. `_named_bases` and `CoachConfigConflict` are deleted. Without settings nothing changes.

**Tech Stack:** Python 3, pytest (`-n auto`).

**Spec:** `docs/superpowers/specs/2026-11-09-coach-aware-placement-design.md` (point 13 is superseded by this plan; point 10's one-switch-at-a-time limit stands). Owner approved the per-slot design in chat 2026-11-10.

## Global Constraints

- No build's bytes change: full fireflymcf suite, `../firefly-firmware` research pins (457), owner pins (`../myfirmware/tests/pin_builds.py`, 4/4).
- Without settings (`coach=None`) `check_placement`'s result is unchanged: `walked == ({},)`, same `arms`.
- Refuse, never guess; every refusal code documented in `ops_doc` and checked by `tests/test_ops_doc.py`.
- No build names in fireflymcf docs; `python scripts/scrub_check.py` (or the repo's scrub check) exits 0.
- Memo trap: memoised functions take module constants as arguments.
- Never modify `tests/answer_key.py` data, the `.bin` corpus, or `captures/`.

## Review Focus

- A base read by two slots, one named and one not: the named record follows its slot, the unnamed record's arms are all walked (Task 1 test).
- A switch nested inside the arm the settings select (not the base arm) is enumerated (Task 1 test: 13.6 HOME under slot 6 = 1 reaches card 902's switches).
- `ARM_PATH_LIMIT` still raised under settings (existing test, keep passing).
- Report (`Placement.arms`) without settings identical to before (existing tests).
- 14.7 `{102:0, 11:0}` builds, `{102:1}` refuses on object 240, `{102:0}` alone refuses (existing tests unchanged).

---

### Task 1: per-slot walk in the gate

**Files:**
- Modify: `src/fireflymcf/pagewalk.py` (`switch_arm_paths`)
- Modify: `src/fireflymcf/newbutton.py` (`check_placement`, `Placement` comments, `PlacementOverlap` docstring; delete `_named_bases`, `CoachConfigConflict`)
- Modify: `src/fireflymcf/recipe.py:661` and `src/fireflymcf/recipe_ops.py:~1965` (drop `CoachConfigConflict` from the except tuples), `newbutton.py:~1611` docstring mention
- Test: `tests/test_newbutton.py` (replace `test_shared_base_partly_named_is_unnamed`), `tests/test_pagewalk*.py` if one covers `switch_arm_paths`

**Interfaces:**
- Produces: `pagewalk.switch_arm_paths(data, roots, limit=ARM_PATH_LIMIT, slot_values: dict | None = None) -> ArmPaths`. With `slot_values`, every walk passes it to `walk_records`; a type-6 record whose `value_slot` is in `slot_values` contributes no arms (it is pinned), but its `child_base → value_slot` still goes into `ArmPaths.slots`. Without it, output identical to today.
- Produces: `check_placement(data, root, obj, coach=None) -> Placement`. With `coach`: `refused` is `[sv for sv in switch_arm_paths(..., slot_values=coach).sets]` (first is `{}`, i.e. `S` alone), each walked as `screen.walk(root, switch_values=sv or None, slot_values=coach)`. `Placement.walked` holds those `sv` (switch_values only; `{}` first). The refusal message names the settings (`_steps_text` over the slots the page reads that `coach` names, or "none read on this page") and, if `sv` is non-empty, "the unnamed arm <sv steps>".
- Report under settings: walk the plain (no-settings) paths as today; skip a path when every `(child_base, value)` in it has all of `paths.slots[child_base]` named in `coach`, each equal to `value`.

- [ ] **Step 1: Write the failing tests** in `tests/test_newbutton.py`, replacing `test_shared_base_partly_named_is_unnamed` (reuse its `_home_clone(_d())` helper):

```python
def test_shared_base_mixed_values_build():
    """13.6 base 441 is read by slots 204 and 208 on HOME. Each record follows its own slot, so
    naming them differently is no longer a conflict."""
    out, root, idx = _home_clone(_d())
    p = newbutton.check_placement(out, root, idx, coach={6: 1, 204: 0, 208: 1})
    assert p.coach
    assert all(441 not in sv for sv in p.walked), p.walked     # both slots pinned


def test_partly_named_base_walks_the_unnamed_record():
    """Naming 204 only: the record reading 208 is unnamed, so base 441's arms are walked."""
    out, root, idx = _home_clone(_d())
    p = newbutton.check_placement(out, root, idx, coach={6: 1, 204: 0})
    assert p.walked[0] == {}
    assert any(sv.get(441) == 1 for sv in p.walked), p.walked


def test_settings_pin_named_switches_and_reach_nested_ones():
    """Under slot 6 = 1, slot 6's switch is pinned (its base never appears in a walked set)."""
    out, root, idx = _home_clone(_d())
    p = newbutton.check_placement(out, root, idx, coach={6: 1})
    paths = pagewalk.switch_arm_paths(out, [root])
    base6 = [cb for cb, s in paths.slots.items() if s == (6,)]
    assert base6 and all(cb not in sv for sv in p.walked for cb in base6), p.walked
```

and in the pagewalk tests:

```python
def test_switch_arm_paths_slot_values_pins_named_switches():
    data = _d()   # 13.6 stock, as the module's other tests read it
    root = <HOME root as the module's other switch_arm_paths tests use>
    plain = pagewalk.switch_arm_paths(data, [root])
    pinned = pagewalk.switch_arm_paths(data, [root], slot_values={6: 1})
    assert pagewalk.switch_arm_paths(data, [root], slot_values=None) == plain
    base6 = [cb for cb, s in plain.slots.items() if s == (6,)]
    assert base6 and not any(cb in sv for sv in pinned.sets for cb in base6)
    assert pinned.sets[0] == {}
```

Check each can fail: the first raises CoachConfigConflict today; stub the pinning out and confirm the pin tests break.

- [ ] **Step 2: Run them, expect FAIL.** `.venv/bin/python -m pytest tests/test_newbutton.py tests/test_pagewalk*.py -q -n auto -k "shared_base or partly_named or pin_named or slot_values"`

- [ ] **Step 3: Implement** per the Interfaces block. Delete `_named_bases` and `CoachConfigConflict` and every reference (`grep -rn CoachConfigConflict src tests studio`). Rewrite `check_placement`'s docstring paragraph on settings: each named slot drives the switch records that read it; a switch reading an unnamed slot is checked in every arm, nested ones included, one switch at a time.

- [ ] **Step 4: Run the gate's tests and the coach tests.** `.venv/bin/python -m pytest tests/test_newbutton.py tests/test_coach_slots.py tests/test_cli.py tests/emu/test_ops147.py tests/test_pagewalk*.py -q -n auto` → all pass. Then the full suite `.venv/bin/python -m pytest tests -q -n auto` → 0 failures.

- [ ] **Step 5: Commit** `placement gate: drive each coach setting by its own slot`.

### Task 2: docs

**Files:**
- Modify: `src/fireflymcf/ops_doc.py` and regenerate/edit `docs/RECIPE-OPS.md:704` (COACH_CONFIG line: drop the shared-base sentence; keep the project-level settings check sentence)
- Modify: `docs/AGENT-GUIDE.md`, `docs/COOKBOOK.md` wherever they describe the shared-base conflict or "all to one value" (grep `independent slots|one value|441`)
- Modify: `docs/superpowers/specs/2026-11-09-coach-aware-placement-design.md` point 13: append "Superseded 2026-11-10: each named slot drives the records that read it (`slot_values`); a partly named base walks its unnamed records' arms; no conflict refusal."

- [ ] **Step 1:** Edit the docs. `RECIPE-OPS.md` must match what `ops_doc` generates (check how `tests/test_ops_doc.py` compares them).
- [ ] **Step 2:** `.venv/bin/python -m pytest tests/test_ops_doc.py -q` → pass; scrub check exits 0.
- [ ] **Step 3: Commit** `docs: per-slot coach settings in the placement gate`.
