# Firmware 14.x, Plan 5: The Display-Cell Table and Free SRAM — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** fireflymcf finds the display-cell table, the render state and the free SRAM cell area from GuiCode on any image, so ops that mint a value cell work on 14.7. With Plans 2-4 in place, it then enables the live-status ops on 14.7.

**Architecture:** three locators, each derived from GuiCode alone:
- `celltables.locate`: the cell table is the one GuiData literal whose indexed reads sit behind a single unsigned bound compare. The type table is the literal behind the same bound that ends exactly where the cell table starts.
- `sramreach.locate_render_state`: the render state is the one SRAM base GuiCode addresses through `movw` offsets.
- `sramreach.cell_area`: the free area runs from the end of the render state to the first indexed table above it.

They replace the 13.6 pins that empty the free pool on 14.7. On 13.6 they reproduce today's 225 free runs and an `allocate_fresh_cells` image with the same sha256.

**Tech Stack:** Python 3.11+, capstone, pytest, `fireflymcf.emu`.

**Spec:** `docs/superpowers/specs/2026-11-06-firmware-14x-support-design.md` (pin 4; LCD-firmware constants in pin 7)

**Depends on:**
- Plan 3 Task 1 (the type table). Its `guidata.variable_type_table` is cross-checked here.
- Plan 2 (the hook): every readout needs it.
- Plan 4 (donors): the readout template and the status-light templates.

**Research (2026-11-07), the evidence this plan cites:**
- **Why 14.7 fails: two pins, not an exhausted pool.**
  - `sramreach.RENDER_STATE_BASE = 0x200301B0` (`sramreach.py:28`) and `INDEXED_EXTENT = {0x200371D1: 0x40}` (`:30-32`) are 13.6 values. On 14.7 the render state is at `0x200301B4` and the digit buffer at `0x200371D5`. Both are treated as unbounded, so the whole window counts as used.
  - The type table is the second pin (Plan 3).
  - With only the type-table fix, the failure remains; both fixes are needed.
  - raminit derives `.bss` end `0x2000AD98` on all three images, so it is not a cause.
- **Further pins:**
  - `guidata.py:1907-1908,1935`: `_VARTAB_*_LIT`, stock `0x44BC8`.
  - `status_map.py:37-38` and `statusbind.py:50-51,144`: `CELL_TABLE_OFF = 0x44BC8`, `CELL_TABLE_LEN = 665`. On 14.7 (505 rows) the latter lists indices 505-664 as "spare".
  - `readout.py:38`: `VAR_TYPE_TABLE_OFF`.
  - `sramreach.py:256,267`: the window `0x20038000..0x2003B000`, which is not derived.
  - `tablegrow.py:171,223,317`: the bound is recognised only as `cmp.w`.
- **Prototype results:**
  - 13.6: the same 225 runs, the same pick (index 665 at `0x2003A858`), `allocate_fresh_cells` sha256 `af03339157ec…`, byte-identical.
  - 14.7: grows index 505 at `0x20039F20`. The relocated pair is re-located from GuiCode (`0x90095168`/`0x90094F6C`, 507 rows), and deep coverage leaves 0 bytes unclaimed.
- **Reuse hazard.** 21 of 14.7's "spare" indices are read by other object tables, so on 14.x the `draw: true` readout path mints fresh cells instead of reusing spares.

## Global Constraints

- **13.6 stays byte-identical.** Both suites pass: this repo's full suite and the research byte pins, which include every grown reference build.
- **No 14.7 constant becomes a locator.** `0x200301B4`, index 505 and the 14.7 table offsets are test expectations only.
- **Refuse, never guess.** Each of these gets a named error:
  - no bound, or more than one distinct bound;
  - not exactly one table candidate;
  - the adjacency check fails;
  - zero or several render-state bases;
  - no indexed base above the struct;
  - a cell outside the area.
- **The type table has one source of truth.** `celltables.locate(...).type_base` must equal Plan 3's `guidata.variable_type_table` on every corpus and grown image. If they disagree, raise rather than choose. `find_variable_tables` delegates to `celltables.locate` when GuiCode is present.
- **Located results are memoised** through `memo`. The halfword compare decode takes about 2-3 s per image; key the memo as `tablegrow._cmp_w_sites` does.
- **Docs cite evidence by date.** `scrub_check` exits 0.
- **11.23's `movw`+`cmp` bound form is recognised by `locate`.** `tablegrow` on 11.23 stays out of scope (non-goal), and refuses cleanly.

## Review Focus

1. **A grown image used as stock** (any reference build that grew the table). The locator finds the moved tables from GuiCode, never a stale GuiData copy (Task 1 `test_locate_on_grown_builds`).
2. **A fresh cell before its first write.** The routine writes before the renderer reads. Prove this in the emulator on 14.7: the cell never draws garbage on the first frame after boot (Task 4).
3. **Spare-cell reuse on 14.7.** `spare_cell_indices` uses the located row count, and a "spare" that another table reads is excluded (Task 2 `test_no_spare_beyond_rows`, `test_spare_not_read_elsewhere`).
4. **`coverage._cell_table_copies`** recognises the copy with the corrected type base, so deep coverage doesn't report "trailing object … absorbing something else" on a 14.7 grow (Task 3).
5. **The digit buffer and render-state fields** are bounded by the struct, not poisoned to `SRAM_HIGH`, and the rule changes nothing on 13.6 (Task 2).

---

### Task 1: `celltables.locate`

**Files:**
- Modify: `src/fireflymcf/celltables.py`. Add `locate(data) -> CellTables(cell_base, type_base, rows, bound_sites, bound_form, cell_refs, type_refs, proof)`.
- Modify: `src/fireflymcf/guidata.py`. `find_variable_tables` delegates to `celltables.locate`, and `_VARTAB_*_LIT` are removed if the grown-build test passes.
- Test: `tests/test_celltable_locate.py`

**Method:**
1. Collect bound compares (`cmp rI,#imm`, `cmp.w rI,#imm`, `movw rB,#imm; cmp rI,rB`) followed by an unsigned branch.
2. Credit each to a GuiData-mapped literal T when, within two branch hops, it reaches an indexed `ldr` off a base literal-loaded with T. `tablegrow._reaches_indexed_read_off_base` does this.
3. The cell table is the one T with exactly one bound whose `imm+1` words are all 0 or SRAM pointers, at least half of them real.
4. The type table is the one other T with the same bound where `T + align4(rows) == cell`.

- [ ] **Steps:**
  1. Write the tests:
     - 13.6 gives cell `0x44BC8`, type `0x4492C`, 665 rows;
     - 14.7 gives type `0x42978`, 505 rows, cell as found;
     - 11.23 locates via the `movw` form;
     - `test_locate_on_grown_builds` (every grown `builds/*.bin`);
     - `test_agrees_with_variable_type_table`;
     - a mutation adding a second bound refuses.
  2. Run the tests; they should fail.
  3. Implement.
  4. Run both suites.
  5. Commit: `"celltables: locate the cell and type tables from GuiCode's bound compares"`.

### Task 2: The render state, the cell area, and spare cells

**Files:**
- Modify `src/fireflymcf/sramreach.py`:
  - add `locate_render_state(data) -> RenderState(base, end, movw_sites, indexed_fields)` and `cell_area(data) -> tuple[int, int]`;
  - replace `RENDER_STATE_BASE`, `INDEXED_EXTENT` and the `free_words`/`free_runs` default window (lines 28-32, 256, 267);
  - register-indexed bases inside `[base, end)` are struct fields bounded by `end`.
- Modify `status_map.py:37-38`, `statusbind.py:50-51,144` and `readout.py:38`: use `celltables.locate`.
- Modify `spare_cell_indices` to iterate the located rows and exclude any index another object table reads.
- Test: `tests/test_celltable_locate.py`.

- [ ] **Steps:**
  1. Write the tests:
     - 13.6: `free_runs` identical to today's 225, pick index 665 at `0x2003A858`;
     - 14.7: the render state at `0x200301B4`, a non-empty area, every cell in `[lo, hi)`;
     - `test_no_spare_beyond_rows`;
     - `test_spare_not_read_elsewhere`;
     - `test_allocate_fresh_cells_136_unchanged` (sha256 `af03339157ec…`).
  2. Run the tests and confirm they fail.
  3. Implement.
  4. Run both suites.
  5. Commit: `"sramreach: locate the render state and free cell area; spare cells within located rows"`.

### Task 3: Growing the table on 14.7

**Files:**
- Modify: `tablegrow.py:171,223,317`. Accept the bound sites `celltables.locate` reports; on 11.23's `movw` form, refuse cleanly.
- Modify: `coverage._cell_table_copies`. Use the located type base.
- Test: `test_grow_147`. After `allocate_fresh_cells` on 14.7:
  - the new index is 505 and points into the area;
  - the table is re-located from GuiCode, with 507 rows after a second grow;
  - deep coverage leaves 0 bytes unclaimed;
  - the type of the new row is correct.

- [ ] **Steps:**
  1. Write the test.
  2. Run it and confirm it fails.
  3. Implement.
  4. Run both suites.
  5. Commit: `"tablegrow, coverage: grow and verify the cell table on 14.7"`.

### Task 4: Live-status ops on 14.7, proved in the emulator

Start only when Plans 2, 3 and 4 are merged into this branch's base.

**Files:**
- Test: `tests/emu/test_ops147.py`. Add each op that passes to `TESTED_147`.
- Modify: `recipe_ops.py`. Each op that passes gets 14.7 in `firmwares` and `emulated_on`.
- Regenerate: `docs/RECIPE-OPS.md`.

Ops and their assertions on 14.7 stock, each target found from 14.7's records:
- **`add_register_readout`:** draw false and draw true.
  - Inject the register's frame; the digits change in the screenshot.
  - Inject a different value; they change again.
  - Before any frame, the first frame after boot shows no garbage.
- **`add_button_status` / `add_button_states`:** `set_g12_output` on the bound channel changes the control's state colour in place, and nothing outside its box changes.
- **`add_enum_label`:** writing each enum value draws the matching text.
- **`follow_channel` / `follow_register`:** these remain blocked if their OPTIONS-row donor (`GATE_OBJECT`) or gate registers carry 13.6 pins. Check first. If they are blocked, record the pin and leave them off 14.7.

Not covered here: `add_rvc_button` still needs located group pools (`newbutton.hold_safe_groups`, `guicode_selected_groups`, `levelcontrols.RESERVED_GROUPS`). Record that as the next locator. Readouts fenced by `decodeguards` need Plan 2 Task 5's decoder range.

- [ ] **Steps:**
  1. Write the tests.
  2. Run them; they should fail with `UNSUPPORTED_FIRMWARE`.
  3. Enable each op that passes. Mark each failure `xfail(strict=True)` with the observed reason.
  4. Run `tests/emu`, both suites and `scrub_check`, and regenerate the docs.
  5. Commit: `"live-status ops: run on 14.7 (emulated)"`.

### Task 5: Hand-off

- [ ] Report to the owner:
  - which live-status ops run on 14.7, and which wait, with the reason (group pools, follower donors);
  - that all of them are unverified on hardware.

  Release only with the owner's approval.
