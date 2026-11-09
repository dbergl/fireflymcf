# Firmware 14.x, Plan 6: Button Groups — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** fireflymcf finds, on any image, the groups GuiCode drives, the reserved inverter groups and the receive-slot-to-display-cell bridge, and proves the LCD firmware handles groups as 13.6's does. With those, `add_rvc_button`, `add_button_status`/`add_button_states`, `wire_button` and `add_glyph_button` run on 14.7.

**Architecture:** four locators, each refusing on ambiguity and returning 13.6's current values on 13.6:
- `lcdgroups.host_writer(data)`: the LCD's host-API table entry `+0x04` is the ungated group-variant writer.
- `newbutton.guicode_selected_groups(data)`: reworked. It scans the located setter cell and proves every dereference reaches a `blx` with a constant `r0`.
- `newbutton.reserved_groups(data)`: the unique group whose whole slice is `1FFD3` (resp. `1FFC5`).
- `status_map.derived_bridge(data)`: the bridge derived from `slottrace` instead of the shipped 13.6 Ghidra dump.

A grammar-style twin compare of the LCD firmware upgrades the group-table semantics from data evidence to code proof.

**Tech Stack:** Python 3.11+, capstone, pytest, `fireflymcf.emu`.

**Spec:** `docs/superpowers/specs/2026-11-06-firmware-14x-support-design.md` (pin 7, LCD-firmware constants; "Left to the builder": each locator's method).

**Depends on:** Plans 2 (hook: `hooklocate.host_api_cells`), 4 (donors: `group_prefix`, `button_records`), 5 (cell table: `index_cell`, spare rows on 14.7). This branch is stacked on Plan 5's branch.

**Research (2026-11-08), the evidence this plan cites.** The prototype is `scratchpad/locators/groups/locate_groups.py`, written up in `scratchpad/locators/groups.md`.
- **GuiCode's setter is `FUN_0021B678`, not `FUN_0021B5E8`.**
  - The LCD builds a host-API table on its stack; `+0x04 = 0x21B679` (13.6 LCD `0x21C18E`/`0x21C196`, call at `0x21C1AE`).
  - `FUN_0021B678` writes both variant counters of record `(b3 + group)`, with no byte-1 gate.
  - `FUN_0021B5E8`, the gated one, is called only from `FUN_0021AB88`.
  - Docs that cite B5E8 as GuiCode's setter need correcting: CONTROL-BEHAVIOR §5a, GUICODE ~8054, `donors.py:85-86`, the `rvcbutton.TOGGLE_VARIANT_MAX` comment.
- **Every use of the setter cell is a call.** Dereferences equal calls with constant `r0`: 105/105 on 13.6, 113/113 on 14.7 (setter cell `0x200395FC`), 90/90 on 11.23.
  - Driven groups: 49 on 13.6 (equal to today's scan), 47 on 14.7, 39 on 11.23.
  - The current scan's shape misses `movs r1,#k; mov r0,r1; blx`, and a deref that branches to a shared `blx` (14.7 `0x2490D0`). The set happens to be unchanged.
- **Setter identity on 14.7, in the emulator:** after boot, every group with non-zero LCD variant counters lies in the located driven set.
- **The reserved templates are groups 68/69 on all three images.**
- **The bridge rule:** a read is kept iff every sink is a `0x2003xxxx` cell store, all before the next base slot read, with exactly one word/halfword cell and one byte cell.
  - It equals the dump on all 42 bridged `DC_DIMMER_STATUS_3` vars on 13.6. Vars 124 and 42 differ; neither is a status var, and the dump is wrong on 124.
  - It bridges 43 of 75 such vars on 14.7.
  - In the emulator, pin 3's derived cells flip on 14.7, and 13.6's dump cell `0x20039BB0` does not.
- **LCD: 11.23 and 14.7 share `cbed1028`, a relocation twin of 13.6's `a55344f9`.** In `0x21A000`-`0x21E124` only 17 bytes differ: rodata pointers shifted +4 and `bl` displacements +2. `FUN_0021B420`/`B5E8`/`B678`/`B8B4`/`AB88` are at the same addresses.
- **14.7's free pool is larger**: 88 free and 44 hold-safe, against 13.6's 50 and 26.

## Global Constraints

- **13.6 stays byte-identical.** Both suites pass: the full fireflymcf suite and the research byte pins (`../firefly-firmware`, branch `groups-tests`).
- **No 14.7 constant becomes a locator.** The setter cell `0x200395FC`, the 47-group set and the bridge cells are test expectations only.
- **Refuse, never guess.** Each failure gets a named error:
  - setter cell unlocated, a dereference that reaches no `blx`, a non-constant `r0`, an unscanned `movw`/`movt`/`ldr.w` form of the cell, or the LCD proof failing: `VARIANT_SETTER_UNKNOWN`;
  - zero or several reserved candidates: `RESERVED_GROUP_UNKNOWN`;
  - `slottrace` cannot follow the slot base: `BRIDGE_UNKNOWN`;
  - an LCD function that is no twin: `LCD_GRAMMAR`.
- **Located results are memoised through `memo`**, keyed on bytes, with module constants passed as arguments.
- **Every new refusal code is documented** on the ops that can raise it; `tests/test_ops_doc.py` derives them.
- **Docs cite evidence by date.** `scrub_check` exits 0.
- **Out of scope:**
  - `add_button` (`newbutton.DONOR_OBJ = 279` and its HOME-placement pins);
  - the coach-specific pool users (`add_aes_batt_heat`, `add_alt_reboot`, `add_level_page`), which get the pool fix but keep their other pins;
  - `cell_slot` readouts (the decoder range).

## Review Focus

1. **A build whose new group lands on a GuiCode-driven group on 14.7.** The allocator must never offer one, and the finish gate must refuse a forced one (Task 5 negative control).
2. **A 14.7 hold-safe group GuiCode selects only through a GuiData record** (`FUN_0021AB88` → `FUN_0021B5E8`). It would look free. Hold-safe keeps only byte-1 `0x00` groups, which B5E8 refuses. State this as inference, and ask for one coach hold probe in the hand-off.
3. **A built image used as stock.** Every locator reads GuiCode and the LCD only, so the earlier build's new groups are seen as used, not free (Task 2 test on a reference build).
4. **The derived bridge on a 13.6 build**: `backbone`, `pin_bindings`, `bind_pin` and `state_loads` give exactly today's values (Task 3).
5. **A container without its own LCD section.** `lcdgroups` refuses cleanly instead of raising (Task 1).

---

### Task 1: `lcdgroups.host_writer` and the reworked driven-group scan

**Files:**
- Create: `src/fireflymcf/lcdgroups.py`. `host_writer(data) -> HostWriter(builder, target, state, records, b3, proof)`.
- Modify: `src/fireflymcf/newbutton.py`. `guicode_selected_groups` scans the located cell, requires `host_writer` to prove out, and drops the `cell != SET_VARIANT_PTR` refusal. `SET_VARIANT_PTR` stays only as a test expectation.
- Modify: `sendmap.py:~1043`. It reports the driven groups as unknown on `VariantSetterUnknown` instead of empty.
- Modify docs, correcting B5E8 to B678 as GuiCode's setter: CONTROL-BEHAVIOR §5a framing, GUICODE ~8054, `donors.py:85-86`, the `rvcbutton.TOGGLE_VARIANT_MAX` comment.
- Test: `tests/test_groups_locate.py`.

**Method:**
- `host_writer`:
  - find the unique `ldr rA,[rB,#0xc]; add r0,sp,#T; blx rA` whose preceding 48 bytes store LCD code addresses to `[sp,#T+4k]`;
  - take `+0x04`;
  - match the 8-instruction writer shape (`ldr; ldrb [,#0xf]; add; ldr; add.w lsl #4; strb [,#0xa]; strb [,#0xb]; bx lr`);
  - require the GuiData tag-0x10 directory `b3 == 0`.
- The scan:
  - visit every `ldr rN,[pc,#]` of the cell's pool word, and every `ldr rX,[rN]` while `rN` holds it;
  - each must reach `blx rX` within 16 instructions, following at most one unconditional `b`;
  - resolve `r0` by constant propagation;
  - the proof records the counts, with `n_derefs == n_calls` as the completeness claim.

- [ ] **Steps:**
  1. Write the tests:
     - 13.6 returns today's 49-group set, and 13.6's `host_writer` target is `0x21B678`;
     - 14.7 returns the 47 groups in the research, with 113 calls;
     - 11.23 returns 39 groups;
     - the emulator check on stock 14.7: every group with non-zero LCD counters after boot is in the set, and no hold-safe group is non-zero;
     - mutations refuse `VARIANT_SETTER_UNKNOWN`: a deref without its `blx`, a non-constant `r0`, and a broken writer shape;
     - a container with no LCD section refuses cleanly;
     - the branch-follow can-fail check (skip it and 14.7 refuses).
  2. Run the tests; they should fail.
  3. Implement.
  4. Run the focused tests, the full suite, the research pins and `scrub_check`.
  5. Commit: `"lcdgroups, newbutton: the variant writer is proven and every setter call is located"`.

### Task 2: `reserved_groups` and the pools

**Files:**
- Modify: `src/fireflymcf/newbutton.py`. Add `reserved_groups(data) -> dict[int, int]` (group → DGN).
  - `hold_safe_groups` uses it.
  - `unused_command_groups` (`wire_button`'s allocator) subtracts `guicode_selected_groups`.
- Modify: `levelcontrols.RESERVED_GROUPS` (`:104`), `recipe.RESERVED_GROUPS` (`:351`), `rvcbutton.allocate` (`:181,184`) and the `recipe_ops` pool users. Each reads `reserved_groups`. The constants stay only as test expectations, or are deleted.
- Test: `tests/test_groups_locate.py`.

- [ ] **Steps:**
  1. Write the tests:
     - `{68: 0x1FFD3, 69: 0x1FFC5}` on all three images;
     - `hold_safe_groups` on 13.6 equals today's list;
     - 14.7's hold-safe list has no driven group and no byte-1 bits 6-7;
     - `unused_command_groups` on 14.7 offers no driven group;
     - a reference build used as stock (any grown `builds/*.bin` that added groups) never offers that build's own new groups;
     - a mutation giving a second group a pure `1FFD3` slice refuses `RESERVED_GROUP_UNKNOWN`.
  2. Run the tests; they should fail.
  3. Implement.
  4. Run both suites and `scrub_check`.
  5. Commit: `"newbutton: reserved groups located; pools exclude driven groups on any image"`.

### Task 3: The derived bridge

**Files:**
- Modify: `src/fireflymcf/status_map.py`. Add `derived_bridge(data)`.
  - `backbone` and `per_floorplan` use it.
  - `load_bridge` keeps the 13.6 file as the tests' positive control.
- Modify: `statusbind.py`.
  - `state_loads` and `bind_pin` use the derived bridge.
  - `require_bridge` / `BRIDGE_FIRMWARE` become "the derived bridge locates".
  - The `build="136"` defaults are removed where the bridge is derived.
- Modify: `patch.py:479,785`. `BRIDGE_DUMP_BUILD`'s gate becomes the derived bridge.
- Modify: `tests/test_ops_doc.py`. Its try/except mask must treat a handler that re-raises the caught error's `.code` as passing the code through. Plan 5 parked this: `BRIDGE_UNKNOWN` was hidden on `add_button`/`wire_button`.
- Test: `tests/test_groups_locate.py` and `tests/test_busmap.py`.

- [ ] **Steps:**
  1. Write the tests:
     - on 13.6, `derived_bridge` equals the dump cell for cell on every `DC_DIMMER_STATUS_3` var;
     - `backbone`, `pin_bindings`, `state_loads` and `bind_pin` on 13.6 equal today's values;
     - 14.7 bridges 43 status vars;
     - the emulator, for a sample of 14.7 pins: `set_g12_output(p, 200)` sets the derived colour cell to the ON master and the bool cell to 1, `set_g12_output(p, 0)` restores both, and 13.6's dump cell does not move;
     - a can-fail check: stub `derived_bridge` to return `{}` and the emulator test fails;
     - `BRIDGE_UNKNOWN` appears in the derived docs of `wire_button` and `add_button`.
  2. Run the tests; they should fail.
  3. Implement.
  4. Run both suites and `scrub_check`, and regenerate `docs/RECIPE-OPS.md`.
  5. Commit: `"status_map, statusbind: the status bridge is derived from GuiCode on any image"`.

### Task 4: The LCD twin compare

**Files:**
- Modify: `src/fireflymcf/grammar.py`.
  - Add a section selector: GuiCode, or the LCD at base `0x210000`.
  - Add an LCD-flash address class.
  - Add `prove_lcd(data) -> LcdGrammarProof`, covering `FUN_0021B420`, `FUN_0021B5E8`, `FUN_0021B678`, `FUN_0021B8B4`, `FUN_0021BAB4` and `FUN_0021AB88`.
- Create: `src/fireflymcf/data/lcdgroups136.json`. Generate it with `scripts/gen_grammar_fingerprints.py`, extended to take exact extents from the Ghidra `lcd136` project. Copy the project first, because another session may hold its lock.
- Modify: `donors.group_prefix`. Its gate becomes `prove_lcd`. Update the docstring, which said "data evidence".
- Test: `tests/test_grammar.py`.

- [ ] **Steps:**
  1. Write the tests:
     - 13.6 proves its own functions;
     - 14.7 and 11.23 prove them, with the shifts reported: rodata +4, callees +2;
     - one flipped immediate in a 14.7 copy refuses `LCD_GRAMMAR`, naming the instruction;
     - a container without its own LCD section refuses cleanly;
     - `group_prefix` refuses when `prove_lcd` fails.
  2. Run the tests; they should fail.
  3. Implement.
  4. Run both suites and `scrub_check`.
  5. Commit: `"grammar: prove the LCD firmware's group handling is 13.6's"`.

### Task 5: The button ops on 14.7, proved in the emulator

**Files:**
- Test: `tests/emu/test_ops147.py`. Add each op that passes to `TESTED_147`, and replace the Plan 4/5 strict xfails it resolves.
- Modify: `recipe_ops.py`. Each op that passes gets 14.7 in `firmwares` and `emulated_on`.
- Regenerate: `docs/RECIPE-OPS.md`.

**Tests, each target found from 14.7's records:**
- **`add_rvc_button`:**
  - hold form on the first hold-safe slot: `tap(hold_s=4)` sends exactly one frame with the binding's DGN after the threshold, and `tap(hold_s=0.2)` sends none;
  - a tap form sends on tap.
- **Negative control:** the same recipe forced onto a driven group is refused by the finish gate, not built.
- **`add_button_status` / `add_button_states`:**
  - driving the entry's source slot flips the cell, and the toggle group's LCD record gets `variant = lit`;
  - a tap then sends the opposite command;
  - nothing outside the control's box changes.
- **`add_glyph_button` + `wire_button`:**
  - a tap sends the wired pin's toggle;
  - `set_g12_output` on that pin recolours the button in place.
  - This replaces Plan 4's strict xfail.

Any op still blocked keeps `xfail(strict=True)` with the observed reason, and stays off 14.7.

- [ ] **Steps:**
  1. Write the tests.
  2. Run them; they should fail with `UNSUPPORTED_FIRMWARE`.
  3. Enable each op that passes. Mark each failure `xfail(strict=True)` with its reason.
  4. Run `tests/emu`, both suites and `scrub_check`, and regenerate the docs.
  5. Commit: `"button ops: run on 14.7 (emulated)"`.

### Task 6: Hand-off

- [ ] Report to the owner:
  - which button ops run on 14.7, and which wait, with the reason;
  - that all of them are unverified on hardware;
  - that 14.7's hold-safe pool is larger than 13.6's and rests on the inference in Review Focus 2; one coach hold probe of the first 14.7 hold-safe slot is worth asking for;
  - the docs correction (GuiCode's setter is `FUN_0021B678`).

  Release only with the owner's approval.
