# Firmware 14.x, Plan 4: Template and Donor Controls — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** New buttons and labels stop cloning 13.6 object numbers. A builder's template is the library's own 13.6 bytes. A template is accepted on a firmware only when a located proof shows that firmware decodes, lays out and draws it the same way. The few true content donors are found by the op's own predicate. `add_text_label` and `add_glyph_button` then run on 14.7.

**Architecture:**
- `grammar.prove(image)` finds twins of 13.6's record decoder (`FUN_00252CB0`), layout engine (`FUN_00257BFC`) and shape renderer (`FUN_00256C54`). Each must match 13.6's instruction list once registers are abstracted. The only difference allowed is a named `cmp.w #imm` bound.
- `donors.template(image, name)` returns frozen 13.6 record bytes behind that proof. Text records (wt 11 and wt 8), whose drawers changed on 14.7, also need a stock **witness** of the same canonical form.
- `donors.glyph_button(image)` finds the one object matching `add_glyph_button`'s predicate.

**Tech Stack:** Python 3.11+, capstone, pytest, `fireflymcf.emu`.

**Spec:** `docs/superpowers/specs/2026-11-06-firmware-14x-support-design.md` (pin 2; "Left to the builder: whether the donors … are found by record shape on any page or chosen per firmware with a structural proof")

**Depends on:** Plan 3 (colours). `add_text_label` needs 14.7's real palette, and the readout template's background cell is a colour input.

**Research (2026-11-07), the evidence this plan cites:**
- **Builders keep only grammar.** Every template builder rewrites geometry, text, value_slot and touch id. The donor contributes header bytes, field presence, colour *modes* (mode 2 is the decoder's hard-coded white), the radius, R39/R49 and the font slot. Measured per template.
- **The pinned indices are wrong on other firmware.** On 14.7, 829[7] is a wt-0 glyph and 285 has a single record. On 11.23, 829 has two records (the raw `IndexError`).
- **No drift check today.** `rvcbutton.button_records` (`rvcbutton.py:267-321`) and `statelights.capsule` (`statelights.py:107`) read 829/285/315 by index with no drift check, so a wrong clone on 14.7 is possible before any refusal.
- **Shape search fails as a locator.** 829[7]'s box shape exists only at 13.6 829[7], so "find by shape on any page" cannot work for templates.
- **Twins on 14.7:**
  - decoder `0x255578`: exact except one bound, `cmp.w r0,#0x298` → `#0x1f8` (664 → 504), at 11 sites;
  - layout `0x25A4B8`: exact;
  - renderer `0x259514`: exact;
  - text drawers `FUN_002559A0` ↔ `0x258264` and `FUN_002557C0` ↔ `0x258088`: **not** twins.
  - A one-immediate mutation of the 14.7 decoder makes the search refuse.
  - 11.23 has no twin.
- **Witness counts on 14.7:** rvc.caption 4, rvc.capsule 35, rvc.touch 43, switch 36, readout.wt8 0 with fillB kept (21 with fillB masked), pd.frame 9, pd.touch 8, pd.hline 0.
- **Glyph-button donor.** Records `[33,11,11,12]`, box fillA mode 7, icon with font, every geom present, one shared value_slot. Unique everywhere: 13.6 247, 14.7 214, 11.23 178 (all Bath Fan).
- **Group prefixes** at groups 17/103/104/129 are equal in all three images. This is data evidence only; group semantics live in the LCD firmware.

## Global Constraints

- **13.6 stays byte-identical.** Every frozen template equals the 13.6 record at its old pin; assert this in a test. Both suites must pass: the full fireflymcf suite and the research byte pins.
- **No 14.7 constant becomes a locator.** Object 214, the 14.7 twin addresses and the bound 504 appear only as test expectations.
- **Refuse, never guess.**
  - No twin, more than one twin, or an unexplained difference: refuse with `GRAMMAR`.
  - A text template without a witness: refuse with `NO_TEMPLATE`.
  - A content donor with zero or several matches: refuse with `NO_DONOR`.
  - No raw `IndexError` on any firmware.
- **Fingerprints, not addresses.** The library ships normalised instruction lists derived from 13.6, with exact function extents taken from the Ghidra `guicode136` project. The prototype used windows.
- **Located results are memoised** through `memo`.
- **Docs cite evidence by date.** `scrub_check` exits 0.
- **Out of scope; these ops stay 13.6-only:**
  - `add_button` (five other 13.6 pins);
  - `add_voltage_battery`;
  - `add_low_volts_control`;
  - `add_option_follower`;
  - the coach-specific aes/cardlayout/altreboot donors.

## Review Focus

1. **A recipe passing an explicit `donor` param to `add_glyph_button`.** It is validated by the same predicate and refuses cleanly (Task 3 `test_bad_donor_param_refused`).
2. **A built image used as stock** whose donor records were already edited. Templates no longer read the image, so the result is unchanged. The content donor predicate still finds the one match, or refuses (Task 3).
3. **11.23.** Every template-using op refuses with `GRAMMAR`, never `IndexError` (Task 2 `test_templates_refuse_on_1123`).
4. **Font slot.** The template's kept font (14 for the caption, 15 for the readout) must exist and cover the text on the target image (Task 2).
5. **The decoder bound.** A mode-7 index of 504 or more must be refused on 14.7 by whatever writes one. This is the bound `grammar.prove` reports (Task 2 `test_mode7_index_bound`).

---

### Task 1: `grammar.prove` — the twin proof

**Files:**
- Create: `src/fireflymcf/grammar.py`, plus a generated fingerprint data file `src/fireflymcf/data/grammar136.json`.
- Create: `scripts/gen_grammar_fingerprints.py`. It regenerates the data file from 13.6 stock, with extents from the Ghidra project or a documented extent rule.
- Test: `tests/test_grammar.py`

**Interfaces (produces):**
- `@dataclass(frozen=True) class GrammarProof: decoder: int; layout: int; renderer: int; bounds: dict[str, tuple[int, int]]; proof: tuple[str, ...]`
- `prove(data: bytes) -> GrammarProof`
- `class GrammarError(Exception)` with `code = "GRAMMAR"`

**Method:** sweep GuiCode for each fingerprint's prologue. At each hit:
- disassemble linearly, skipping literal pools (`ldr Rt,[pc,#]` targets) and absolute jump tables (after `ldr.w pc,[Rn,Rm,lsl #2]`);
- compare item by item, allowing only register renaming and a differing `cmp.w R,#imm`, which is reported as a bound.

- [ ] **Steps:**
  1. Write the tests:
     - 13.6 gives its own addresses and no bounds;
     - 14.7 gives the twins and `{"mode7_index": (0x298, 0x1F8)}`;
     - 11.23 raises `GRAMMAR`;
     - flipping one decoder immediate on a 14.7 copy raises `GRAMMAR`, naming the instruction.
  2. Run the tests; they should fail.
  3. Implement.
  4. Run the tests and the full suite.
  5. Commit: `"grammar: prove an image decodes, lays out and draws records as 13.6 does"`.

### Task 2: Frozen templates replace index reads

**Files:**
- Create: `src/fireflymcf/donors.py`: `template(data, name) -> tuple[bytes, TemplateProof]`, `TEMPLATES` (name → frozen bytes, form, rewritten fields, whether it is text) and `canonical(record_bytes, name) -> bytes`.
- Modify:
  - `rvcbutton.py:267-321`;
  - `statelights.py:107`;
  - `aescompact.py:188,281-291` (only the constants the general ops use; coach ops keep theirs);
  - `pagedraw.py:70-108` (`_donor` and its pinned compares);
  - `switchbuild.py:33-35,71`;
  - `readout.py:402-403`;
  - the group-prefix constants in `rvcbutton.py:72-74`, which become frozen 4-byte templates.

  Each of these reads its template from `donors.template`.
- Test: `tests/test_donors.py`

**Gates:**
1. `grammar.prove` succeeds.
2. For text templates (wt 11 or wt 8): at least one stock record whose `canonical` bytes equal the template's. Canonical zeroes every field the builder rewrites. It also masks a colour field that the colour locator supplies, which here is the readout's fillB.

The readout template's fillB background cell comes from Plan 3. `theme.locate_theme_init` gives the bg/panel role index: 8 on 13.6. If 14.7's value isn't established from code, refuse readout templates on 14.7 and record it.

- [ ] **Steps:**
  1. Write the tests:
     - every template on 13.6 equals the record at its old pin (12 cases);
     - every template on 14.7 is accepted with its witnesses, or refused where none exists (`pd.hline`);
     - `test_templates_refuse_on_1123`;
     - `test_font_slot_checked`;
     - `test_mode7_index_bound`: a builder writing a mode-7 index ≥ the located bound refuses.
  2. Run the tests; they should fail.
  3. Implement.
  4. Run the full suite and the research byte pins. Every Hank, Saffron and engine pin must pass.
  5. Commit: `"donors: frozen templates behind the grammar proof replace donor index reads"`.

### Task 3: The glyph-button content donor

**Files:**
- Modify: `recipe_ops.py:1928-1987` (`AddGlyphButton`). With no `donor` param it uses `donors.glyph_button(ctx.stock)`. With a `donor` param it validates it against the predicate.
- Test: `tests/test_donors.py`

**Predicate:** records `[33,11,11,12]`, box fillA mode 7, an icon with a font, every `geom[2]/[3]` present, and box, icon and caption sharing one value_slot.

- [ ] **Steps:**
  1. Write the tests:
     - 13.6 gives 247, 14.7 gives 214, 11.23 gives 178;
     - `test_bad_donor_param_refused` (`NO_DONOR`, not `IndexError`);
     - a mutation that gives a second object the shape makes the predicate refuse.
  2. Run the tests; they should fail.
  3. Implement.
  4. Run both suites.
  5. Commit: `"add_glyph_button: the donor is found by its predicate and a param is validated"`.

### Task 4: `add_text_label` and `add_glyph_button` + `wire_button` on 14.7, proved in the emulator

**Files:**
- Test: `tests/emu/test_ops147.py`. Add both ops to `TESTED_147`.
- Modify: `recipe_ops.py`. Each op that passes gets `firmwares` with 14.7 and `emulated_on` with 14.7.
- Regenerate: `docs/RECIPE-OPS.md`.

**Tests:**
- `add_text_label`: white text on a 14.7 page. Its pixels appear in the box and are `ffffff`. No pixel outside the box changes on any page that reaches the parent.
- `add_glyph_button` + `wire_button`:
  - the clone of 14.7's donor draws at its new place;
  - a tap sends the wired pin's toggle;
  - `set_g12_output` on that pin recolours it in place.
  - First check which group the donor's clone carries. An unwired clone must not drive the donor's live group.

`wire_button` may carry its own 13.6 pins. If it does, mark its test `xfail(strict=True)` with the pin named, and leave both ops off 14.7. Do not fix `wire_button` here.

`add_rvc_button`, `add_enum_label` and `add_button_status`/`add_button_states` are unblocked on the donor side only. They still need:
- group pools (`newbutton.hold_safe_groups`, `guicode_selected_groups` and `levelcontrols.RESERVED_GROUPS` are 13.6 numbers);
- the cell table (Plan 5);
- the hook (Plan 2).

They wait for those plans.

- [ ] **Steps:**
  1. Write the tests.
  2. Run them; they should fail with `UNSUPPORTED_FIRMWARE`.
  3. Enable each op that passes; xfail any that fail, with the reason.
  4. Run `tests/emu`, both suites and `scrub_check`, and regenerate the docs.
  5. Commit: `"add_text_label, add_glyph_button: run on 14.7 (emulated)"`.

### Task 5: Hand-off

- [ ] Report to the owner:
  - which ops were enabled;
  - which donor-side ops wait for group pools, the hook or the cell table;
  - that 14.7's text drawers differ from 13.6's. That a label draws right is emulator evidence; one 14.x panel check of a label is worth asking for.

  Release only with the owner's approval.
