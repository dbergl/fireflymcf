# Firmware 14.x, Plan 9: add_button and follow_channel — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `add_button` and `follow_channel` run on 14.7 (emulated) by locating or proving what they pin today, and they refuse 11.23 for a stated reason. Both 13.6 builds stay byte-identical.

**Architecture:**
- **`add_button`'s donor, 13.6 object 279, is a form, not an identity.** It is one of four byte-identical unattached "… Tank Heater" objects: 278-281 on 13.6, 244-247 on 14.7, none on 11.23. They differ only in fields `add_button` rewrites.
- **So it becomes a Plan 4 frozen template:** three records, each proven per image by `donors.template`, with the four objects as witnesses. A locator would have to break a four-way tie, which would be a fit.
- **The other changes to `add_button`:**
  - the caption check reads the template;
  - placement becomes a parameter with an overlap gate;
  - the group edit gets an LCD proof;
  - the colour cell gets a mode-7 bound.
- **`follow_channel`'s `GATE_OBJECT = 46` is the OPTIONS page root,** which `cli._resolve_page_root(data, "options")` already locates.

**Tech Stack:** Python 3.11+, capstone, pytest, `fireflymcf.emu`.

**Spec:** `docs/superpowers/specs/2026-11-06-firmware-14x-support-design.md` ("locate, don't pin"; frozen donors).

**Depends on:**
- Plan 4: `donors.template`, `grammar.prove`.
- Plan 6: `grammar.prove_lcd`, `lcdgroups`.
- Plan 2: `hooklocate`.
- Plan 8's `slow` marker: tests under `tests/emu/` are marked automatically.

**Research (2026-11-09), the evidence this plan cites.** The notes are `scratchpad/locators/addbutton.md`; the scripts are in `addbutton/`.
- **279's records** (`clone_onto_home`, `newbutton.py:696`):
  - box wt 33: fillA mode 7, value_slot;
  - caption wt 11: value_slot, label "Fresh Tank Heater" (17 bytes), h_align 2, font 0xff (inherit);
  - touch wt 12: group tail.
- **The object is a 76-byte span:** `[3] + box(20) + caption(40) + touch(14) + 1 pad`.
- **Which fields are rewritten:** `add_button` rewrites fillA, both value_slots, the group and the label. What it keeps carries no per-image index.
- **14.7's 245 differs from 279 only in the three rewritten words.**
- **Choosing among the four is not a locator's job.** A 13.6 build that clones 278 instead of 279 is byte-identical (`ebd4c2ef326c…` both times).
- **The shape predicate matches four objects on 13.6 and on 14.7, and none on 11.23:**
  - records `[33,11,12]`;
  - box fillA mode 7;
  - value_slot on the box and the caption;
  - caption labelled.
- **Template prototype:**
  - `button.box`, `button.caption` and `button.touch`, sourced from `(279, 0..2)`;
  - each passes `donors.template` on 13.6 and 14.7 with 4 witnesses (14.7 needs a caption witness because its text drawers are not twins);
  - 11.23 refuses `GRAMMAR`;
  - the assembled bytes equal `insert.clone_object(stock136, 279)`.
- **What `add_button` hits today:**
  - 14.7: the op's `firmwares` default `{"13.6"}` (`recipe.py:221`); with that admitted, the pinned 279 clones an unrelated object, and only the label check catches it (`BUILD_REFUSED`);
  - 11.23: it fails with "no IMAGE_OPCODE". Both refusals are accidental.
- **With 245 swapped in, on 14.7:**
  - pins 14, 15 and 16 build and verify;
  - in the emulator, `tap("Pin 15")` sends exactly one `1FEDB 0fffc805ff00ffff`;
  - the status repaints inside the box only, and no stock HOME pixel outside the footprint changes;
  - pin 13 refuses `COMMANDED_CHANNEL`, as on 13.6.
- **Placement `NEW_X/NEW_Y = 435,404` is a 13.6 pin.** On 14.7 it lands in free space only because HOME matches: inside card 765, where 13.6 has card 901.
- **Two emulator observations, the same on 13.6 and 14.7:**
  - the button is invisible until the pin's first status arrives;
  - the centre-padded caption "Pin 15" wraps to "Pin" over "15".
- **follow_channel:**
  - `_resolve_page_root(d, "options")` gives 46 on 13.6 and 42 on 14.7;
  - 11.23 refuses: it has no "Heat Pump" row, and no spare cell at +0x44BC8;
  - with 42 and the gate admitted, it builds on 14.7, and `followchannel.resolve(..., gate_option="Heat Pump")` derives gate register `0x20039e0f`;
  - the strict xfail at `tests/emu/test_ops147.py:1189-1197` sees `NO_SUCH_LABEL` today.

## Global Constraints

- **13.6 stays byte-identical:**
  - `tests/test_newbutton.py` and `tests/test_followchannel.py`;
  - the research byte pins: `../firefly-firmware`, `tests/test_recipe_hank*.py`, 457 passed;
  - the owner pins: `../myfirmware/tests/pin_builds.py`, run with fireflymcf's `.venv`, 4/4.
- **No 14.7 constant becomes a locator.**
  - Objects 244-247, 245, 42, card 765 and register `0x20039e0f` are test expectations only.
  - Index 279 may remain only as the template's documented `source` and in tests.
- **Refuse, never guess.** Each refusal has a named code, and `tests/test_ops_doc.py` documents it:
  - `GRAMMAR` / `NO_TEMPLATE` (donors);
  - `LCD_GRAMMAR`;
  - `MODE7_INDEX`;
  - `PLACEMENT_OVERLAP`;
  - `UNSUPPORTED_FIRMWARE` on 11.23 where the cause is the image.
- **Op gating:**
  - `firmwares` lists 14.7 only once an emulator test proves the op on 14.7;
  - `emulated_on` gets 14.7;
  - `verified_on` is never set from emulator evidence.
- **Located results are memoised through `memo`,** keyed on bytes, with module constants passed as arguments.
- **Docs cite evidence by date.** No build names; `scrub_check` exits 0.
- **Never modify** `tests/answer_key.py` data, the `.bin` corpus, `captures/` or the PDF. Research-repo changes, if any are needed, go on the branch `add-button-tests`.

## Review Focus

1. **A recipe that relies on the old caption check.** `TARGET_LABEL_RAW` refused anything but "Fresh Tank Heater". The template check must refuse an image whose witnesses don't prove the caption form, with `NO_TEMPLATE`, rather than relabel whatever is there.
2. **The `at` placement on another page or off-canvas.**
   - The overlap gate must walk the target page's own records.
   - It must refuse a footprint that leaves the canvas or overlaps any placed record other than the page root and one enclosing wt-33 card.
   - The default (HOME, 435, 404) keeps 13.6 byte-identical.
3. **The "invisible until first status" behaviour.** Emulator tests must set the output before asserting that the button is drawn. Do not "fix" the behaviour in this plan: it is unchanged on 13.6 and unverified on the coach. Report it.
4. **The follower's gate register on 14.7 is a by-name join** until the emulator shows that toggling the "Heat Pump" row stops the follower. Task 4 must show both directions.
5. **11.23** must refuse each op for its stated reason (`GRAMMAR`; `NO_SUCH_LABEL` or the spare-cell refusal), not for an accidental one.

---

### Task 1: the button templates

**Files:** `src/fireflymcf/donors.py`; `tests/test_donors.py`.

- [ ] **Steps:**
  1. **Write the tests:**
     - `donors.button_object(stock136)` returns bytes equal to `insert.clone_object(stock136, 279)`, with the proof's witnesses `{278, 279, 280, 281}`;
     - on 14.7 the witnesses are `{244, 245, 246, 247}`, and the bytes equal 245's span with its three rewritten words set to the template's values;
     - 11.23 raises `DonorError` with code `GRAMMAR`;
     - with `donors._witnesses` stubbed to return `()` on 14.7, the caption refuses `NO_TEMPLATE`;
     - `check_mode7_index` refuses an index at or above the image's limit (`MODE7_INDEX`).
  2. Run the tests; they should fail.
  3. **Implement:**
     - templates `button.box` (rewrites `fillA`, `value_slot`), `button.caption` (rewrites `value_slot`, `label`; a text template) and `button.touch` (rewrites `touch`), sourced from `(279, 0..2)` as frozen 13.6 bytes;
     - `button_object(data) -> tuple[bytes, ButtonProof]`, which assembles `[3] + box + caption + touch`, zero-padded to a multiple of 4, and is memoised.
  4. Run the focused tests, the fast tier, and `scrub_check`.
  5. Commit: `"donors: add_button's object is a frozen template proven per image"`.

### Task 2: add_button's build reads the template; gates

**Files:** `src/fireflymcf/newbutton.py` (`clone_onto_home`, `build_home_button`, the relabel at `:1405-1432`, `DONOR_OBJ`, `TARGET_LABEL_RAW`, `NEW_X/NEW_Y`); `src/fireflymcf/recipe_ops.py` (`AddButton`); `tests/test_newbutton.py`.

- [ ] **Steps:**
  1. **Write the tests:**
     - **13.6 stays byte-identical:** a 13.6 `add_button` build is byte-identical to today's;
     - **placement:**
       - `at` defaults to HOME (435, 404);
       - an `at` overlapping the Chassis battery refuses `PLACEMENT_OVERLAP`;
       - an off-canvas `at` refuses `PLACEMENT_OVERLAP`;
     - **LCD proof:** with `grammar.prove_lcd` stubbed to fail, the build refuses `LCD_GRAMMAR`;
     - **the old accidental refusals are gone:**
       - on 14.7 with the firmware gate admitted in the test, the label mismatch no longer occurs;
       - on 11.23 the op refuses `GRAMMAR`, not "no IMAGE_OPCODE".
  2. Run the tests; they should fail.
  3. **Implement:**
     - **the donor:** `clone_onto_home` takes the object from `donors.button_object`. Delete `DONOR_OBJ`.
     - **the caption check:** the expected caption comes from the template's label and field width. Delete `TARGET_LABEL_RAW`, or derive it.
     - **the colour cell:** `check_mode7_index` runs on the cell `retarget_indicator` writes.
     - **the group edit:** a `grammar.prove_lcd` gate runs before `retarget_command`.
     - **placement:**
       - an optional `at: {page, x, y}` param, documented in `AddButton`'s docstring;
       - a footprint gate over `layout.Screen(img).walk(root)` that admits only the page root and one enclosing wt-33 card;
       - `NEW_X/NEW_Y` become the 13.6 default values, named as such.
     - **The direct-caller defaults** `TARGET_CHANNEL` and `INDICATOR_COLOUR_INDEX` stay. Document them as 13.6 test defaults.
  4. **Run:**
     - the focused tests, the full suite and `scrub_check`;
     - the research byte pins (`../firefly-firmware`, `.venv/bin/python -m pytest tests -q -n auto`, 457);
     - the owner pins (`../myfirmware/tests/pin_builds.py` with fireflymcf's `.venv`, 4/4).
  5. Commit: `"add_button: the donor is the proven template; placement and LCD gates"`.

### Task 3: add_button on 14.7, proved in the emulator

**Files:** `src/fireflymcf/recipe_ops.py` (`AddButton.firmwares`, `emulated_on`); `src/fireflymcf/ops_doc.py`; `tests/emu/test_ops147.py` (`test_add_button`, and `TESTED_147`); a refusal test on 11.23 (in `tests/test_newbutton.py`); regenerate `docs/RECIPE-OPS.md`.

- [ ] **Steps:**
  1. **Write the tests.**
     - **`test_add_button` on stock 14.7, pin 15:**
       - settle, then `goto("HOME")`;
       - `set_g12_output(15, 200)`: every changed pixel lies inside `control_box("Pin 15")`;
       - `set_g12_output(15, 0)`: the box draws the off colour, and the diff against stock is confined to the box;
       - `tap("Pin 15")`: exactly one `1FEDB` frame, `0fffc805ff00ffff`;
       - after the echo, the box is lit again, with the change confined to the box.
     - **The 11.23 refusal:** 11.23 refuses with its stated code.
  2. Run the tests; they should fail with `UNSUPPORTED_FIRMWARE`.
  3. **Implement:**
     - `firmwares = {"13.6", "14.7"}` and `emulated_on` adds 14.7;
     - the op doc lists the new codes;
     - regenerate `docs/RECIPE-OPS.md`.
  4. Run `tests/emu`, both suites and `scrub_check`.
  5. Commit: `"add_button: runs on 14.7 (emulated)"`.

### Task 4: follow_channel's gate object located; 14.7 emulated

**Files:**
- `src/fireflymcf/recipe_ops.py` (`FollowChannel.apply` at `:5536-5546`, `GATE_OBJECT`, `firmwares`, `emulated_on`);
- `src/fireflymcf/followchannel.py`: remove the constants already marked deprecated, if nothing else reads them;
- `tests/emu/test_ops147.py`: replace the strict xfail at `:1189-1197`;
- `tests/test_followchannel.py`;
- `ops_doc.py` and `docs/RECIPE-OPS.md`.

- [ ] **Steps:**
  1. **Write the tests:**
     - the gate object resolves to 46 on 13.6 (13.6 build byte-identical) and to 42 on 14.7 (expectation only);
     - 11.23 refuses for its stated reason;
     - **`test_follow_channel` on stock 14.7** (source pin 3 → target pin 4 at level 200, gated by "Heat Pump"):
       - with the row enabled, setting pin 3 on makes the panel send a `1FEDB` frame for pin 4 at level 200;
       - with the row disabled, the panel sends no frame for pin 4;
       - drive the row through the panel (`goto("OPTIONS")`, `tap`) so that the gate register is the one the row writes. Writing the register directly does not test the by-name join.
  2. Run the tests; they should fail.
  3. **Implement:**
     - `GATE_OBJECT` becomes `cli._resolve_page_root(data, "options")`. Move it to a library helper if `recipe_ops` must not import `cli`, and memoise it;
     - `firmwares` and `emulated_on` gain 14.7;
     - update the op docs.
  4. Run `tests/emu`, both suites, the owner pins and `scrub_check`.
  5. Commit: `"follow_channel: the OPTIONS root is located; runs on 14.7 (emulated)"`.

### Task 5: hand-off

- [ ] **Report to the owner:**
  - `add_button` and `follow_channel` on 14.7 (emulated);
  - the codes 11.23 refuses with;
  - the two emulator observations, for a coach check:
    - the button is invisible until its first status;
    - the centre-padded caption wraps.

  Release only with the owner's approval.
