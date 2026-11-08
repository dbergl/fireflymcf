# Firmware 14.x, Plan 3: The Colour Model and `set_theme` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the variable-type-table locator so 14.7 colours resolve. Locate the colour decode and the theme-init from structure. Enable `set_theme` on 14.7 after it passes its emulator test.

**Architecture:** Three locators:
- **(a)** `guidata.variable_type_table` reads the type table from the code's (data-pointer, type) literal pairs, instead of the backward scan.
- **(b)** `theme.colour_decode` finds the record decoder's colour-mode switch from its mode-2 anchor and reads the mode-3, mode-6 and mode-7 cases from it.
- **(c)** `themeswap.locate_theme_init` matches the 22-instruction theme-init skeleton and returns the role sites, cells and indices.

On 13.6 each one returns today's constants.

**Tech Stack:** Python 3.11+, capstone, pytest, `fireflymcf.emu`.

**Spec:** `docs/superpowers/specs/2026-11-06-firmware-14x-support-design.md` (pins 5 and 6; "Calls made: Colours")

**Research (2026-11-07), the evidence this plan cites:**
- **The "six-colour palette" on 14.7 is a library bug.** `guidata.find_variable_tables` (lines ~1887-1891) scans backwards over 8 zero bytes and rounds to GuiData+`0x42970`. The code loads the type table at `0x90042978`: the pair `(0x90042B74, 0x90042978)` at 14.7 `0x2565CC/D0`, loaded at `0x256356`, `0x2563A6` and `0x2564C0`, with three more agreeing pairs. On 13.6 and 11.23 the pairs equal today's value, `0x4492C`.
- **With the code's base,** 14.7's mode-7 palette has 18 static entries, including white (idx 325). All 18 equal emulator SRAM after boot.
- **14.7 takes inline RGB (mode 3) exactly as 13.6 does.** The decoder `FUN_00252CB0` corresponds to 14.7 `0x255578`. Mode 3 is `0x255DD0 bl 0x254A08; str r0,[r3,#0x68]`, and `0x254A08` is byte-identical to 13.6's `0x252140`. The spec's "palette indices only" branch does not arise.
- **The theme-init** is 13.6's 22 instructions at `0x248316..0x24834C`. On 14.7 the same block is at `0x248386..0x2483BC`, byte for byte, matched uniquely by a skeleton. 11.23 has no match.
- **`GUIDATA_PALETTE_LEN = 30` is unproven.** Code literals mark the next structure at `0x90010060` on 13.6 (20 entries) and `0x90010064` on 14.7 (21 entries). This is inference.

| | 13.6 | 14.7 |
|---|---|---|
| type table | `0x4492C` | `0x42978` (the scan gives `0x42970`) |
| mode-6 palette / length | `0x90010010` / 20 | `0x90010010` / 21 |
| mode-7 table / bound | `0x90044BC8` / `0x298` | `0x90042B74` / `0x1F8` |
| theme literals bg/panel/button | `0x2485CC/D8/DC` | `0x24863C/48/4C` |
| chains header/active/rail | `0x24831E/0x248336/0x248340` | `0x24838E/0x2483A6/0x2483B0` |
| indices bg/header/panel/rail/button/active/well | 3/4/8/10/161,15/35/161 | 8/9/1/15/146,31/30/146 |

## Global Constraints

- **13.6 stays byte-identical.** The full suite passes, and so do the research byte pins (`cd ../firefly-firmware && .venv/bin/python -m pytest tests -q -n auto`). This includes the Saffron and Hank `set_theme` pins.
- **No 14.7 constant becomes a locator.** The 14.7 values above are test expectations only.
- **Each locator refuses on zero or more than one candidate.** Its proof names the instruction addresses it relied on.
- **Never invent a colour.** `render.py`'s fill policy still governs.
- **Located values are memoised through `memo`**, keyed on the image bytes.
- **Emulator evidence never clears `--allow-unverified`.** `set_theme` joins 14.7 in `firmwares` and `emulated_on` only.
- **Docs cite evidence by date.** `scrub_check` exits 0.
- This plan runs **before** the display-cell-table plan, which depends on Task 1.

## Review Focus

1. **A `tablegrow`n image.** The code literal pairs move with the table, so `variable_type_table` must still agree with the 13.6-only `_relocated_variable_tables` override on every grown reference build (Task 1 `test_type_table_on_grown_builds`).
2. **Value-slot typing on 14.7.** `readout`, `slotsource`, `options`, `outputs` and `status_map` change results on 14.7. Record which results change, and confirm no 13.6 result changes (Task 1).
3. **An already re-themed image** (Saffron-built 13.6). It must still get today's "already re-themed" refusal, not a vague "not found" (Task 3).
4. **Palette length.** Bounding 30 → 20 must change no 13.6 byte on any reference recipe (Task 2).
5. **Black on 14.7.** It has no static `000000` entry, so a label asking for black refuses with `NO_COLOUR` and a message that names the palette (Task 2).

---

### Task 1: The variable type table comes from the code

**Files:**
- Modify: `src/fireflymcf/guidata.py` (`find_variable_tables` ~1887-1891). Add `variable_type_table(data) -> int` and use it there.
- Test: `tests/test_colour_locate.py`

**Interfaces (produces):** `guidata.variable_type_table(data: bytes) -> int` (the GuiData offset). It raises `guidata.TableNotFound` (or the module's existing error class) when no pair exists or the pairs disagree.

**Method:**
1. Take `dptr` from the existing structural run.
2. Collect every 4-aligned GuiCode literal equal to `0x90000000 + dptr` whose next word is a GuiData pointer below it, and that a PC-relative `ldr` loads.
3. Every pair must give the same type base.

- [ ] **Step 1: Failing tests:**
  - `test_type_table_from_code`: 13.6 → `0x4492C`, 14.7 → `0x42978`, 11.23 → `0x4492C`.
  - `test_type_table_on_grown_builds`: on each grown `builds/*.bin`, it equals `_relocated_variable_tables`.
  - `test_palette147`: `theme.mode7_palette(mcf_147)` has the 18 entries; `resolve_fill_index` of white is 325; index 1 resolves.
  - `test_type_table_refuses_disagreeing_pairs`: corrupt one pair.
- [ ] **Step 2:** Run them. Expected: 14.7 gives `0x42970`, so FAIL.
- [ ] **Step 3:** Implement. If the code literal is authoritative on grown images as well, remove `_VARTAB_*_LIT` and the 13.6-only override. Otherwise keep them, and record why.
- [ ] **Step 4:** Run the full suite and the research byte pins. List every 14.7 test whose expectation changed (value-slot types shifted by 8). Re-derive each changed expectation from 14.7's records; never just update a number to make it pass.
- [ ] **Step 5:** Run `tests/emu/test_ops147.py`. `recolor`'s 14.7 test may now accept indices 1/8. Add a case using index 1.
- [ ] **Step 6: Commit** — `"guidata: the variable type table is read from the code's literal pairs (fixes 14.7's palette)"`

### Task 2: Locate the colour decode; bound the palette

**Files:**
- Modify: `src/fireflymcf/theme.py`. Add `colour_decode(data) -> ColourDecode` (`mode3_reader`, `palette_base`, `palette_len`, `mode7_table`, `mode7_bound`, `proof`).
- Modify: `themeswap.py:67-68`. `GUIDATA_PALETTE_OFFSET`/`_LEN` come from `colour_decode`.
- Test: `tests/test_colour_locate.py`

**Method:**
1. Find the unique `mvn r2,#0xff000000; str r2,[r3,#0x68]`.
2. Walk the same switch to:
   - the mode-3 `bl X; … str r0,[r3,#0x68]`, where X is three `ldrb` merged little-endian;
   - the mode-6 `ldr r3,=PAL; ldr.w r2,[r3,r0,lsl #2]`;
   - the mode-7 `cmp.w r0,#BOUND … ldr r3,=TAB`.
3. `palette_len` is the distance from `PAL` to the lowest other GuiCode literal into the same sub-container, divided by 4.

- [ ] **Steps:**
  1. Write the tests: the table values for 13.6 and 14.7, an 11.23 refusal (mode 3 differs), and a mutation that removes the mode-2 anchor and so refuses.
  2. Run them and see them fail.
  3. Implement.
  4. Run both suites. If bounding to 20 changes any 13.6 reference byte, stop and report the recipe and colour. Do not keep 30 silently.
  5. Add `test_black_refused_on_147` (`NO_COLOUR`, message names the palette).
  6. Commit: `"theme: locate the colour decode; bound the mode-6 palette by the code"`.

### Task 3: Locate the theme-init; `set_theme` runs on the located sites

**Files:**
- Modify: `src/fireflymcf/themeswap.py`:
  - add `locate_theme_init(data) -> ThemeInit(sites, cells, values, indices, well_index, proof)`;
  - replace `STATIC_INDEX`, `TANK_WELL_INDEX`, `_LIT_*`, `_HEADER_CHAIN`, `_ACTIVE_MOVW` and `_RAIL_CHAIN` (lines 60-65) and the 13.6 byte checks in `plan()` (~377-393);
  - `_tank_fill_offsets` uses the located well index, not 161.
- Test: `tests/test_colour_locate.py`

**Method:**
1. Do a resyncing linear decode (`theme.detail_runs`).
2. Match the 22-instruction skeleton on mnemonic and register operands. PC-literal displacements and immediates are wildcards. The instructions must be contiguous, and there must be exactly one match.
3. Read the roles by position:
   - bg: position 1 literal;
   - header chain: positions 4-5;
   - panel: position 8 literal;
   - button: position 10 literal;
   - active: position 14 `movw`;
   - rail chain: positions 18-19.
4. Read the cells from the store literals. Each must be SRAM and type 10 under Task 1.
5. Take the indices from the variable table; the well index is the position-11 cell's.
6. Replay the six values and compare them with `STOCK`.
7. Add a second skeleton for a re-themed image (`movw/movt` chains), which yields today's "already re-themed" refusal.

- [ ] **Steps:**
  1. Write the tests:
     - 13.6 returns today's constants exactly;
     - 14.7 returns the table's values;
     - 11.23 refuses;
     - a Saffron-built 13.6 gets the "already re-themed" message;
     - a mutation of one chain refuses.
  2. Run them and see them fail.
  3. Implement.
  4. Run both suites, including the Saffron/Hank theme pins.
  5. Commit: `"themeswap: locate the theme-init; tank wells by located index"`.

### Task 4: `set_theme` on 14.7, proved in the emulator

**Files:**
- Test: `tests/emu/test_theme147.py`
- Modify: `recipe_ops.py`. The `SetTheme` class gets `firmwares = {"13.6","14.7"}` and `emulated_on = {"14.7"}`.
- Modify: `tests/emu/test_ops147.py`. Add `set_theme` to `TESTED_147`, or move the test there; the registry-invariant test requires one or the other.
- Regenerate: `docs/RECIPE-OPS.md`

**Test:** `test_set_theme147`:
1. Compile `set_theme` against stock 14.7 with six distinct role colours (copy the existing 13.6 `set_theme` test's colours) and `tank_wells: panel`.
2. Classify the edit runs by region. Expect 6 in GuiCode; 13 in GuiData (1 inline, 7 palette, 5 wells).
3. Boot. Each role cell (`read_mem` at the located cell) holds its applied colour.
4. The HOME screenshot, and each of the five rail pages, contains zero pixels of any of the six stock colours. Before applying the theme, confirm that stock contains each of them, so the test can fail.

- [ ] **Steps:**
  1. Write the test.
  2. Run it. Expect a FAIL with `UNSUPPORTED_FIRMWARE`.
  3. List `set_theme` on 14.7.
  4. Run the test, `tests/emu`, the full suite, the research byte pins and `scrub_check`; regenerate the docs.
  5. Commit: `"set_theme: runs on 14.7 (emulated)"`.

### Task 5: Hand-off

- [ ] Report to the owner:
  - 14.7's real palette, 18 entries;
  - that `set_theme` and `recolor` work on 14.7 in the emulator and are unverified on hardware;
  - that black is not in 14.7's palette.

  Writing mode-3 inline colours from label ops is a possible follow-up; it is not this plan. Release through the tag workflow only with the owner's approval.
