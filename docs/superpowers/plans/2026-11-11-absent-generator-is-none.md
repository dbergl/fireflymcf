# Absent generator mode is "none" — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a coach's settings express a configuration but name no generator mode, derive
everything as the panel does for "none" (AGS slot = 0), on 13.6 and 14.7; and model 13.6's slot 7.

**Architecture:** All in `options.derived_slots` (per-firmware `_derived_slots_136` /
`_derived_slots_147`), still gated by `located_derivation`. Caller-set values keep winning.

**Tech Stack:** Python, pytest (`-n auto`), the research corpus via `scripts/link-research.sh`.

**Spec:** none on disk. The design was approved in chat on 2026-11-11 by the owner:
"It seems like Option AGS selected means you have a generator, Options AES and AES-Single
Output, selected means no generator. If all 3 are not selected assume no generator." and, to the
three-point proposal below, "yes, set it to 0 too, go ahead". The proposal:

1. Settings that name options but no generator mode mean "none" (AGS slot value 0) on 13.6 and
   14.7. An empty `values` (no configuration at all) still derives nothing.
2. The AGS slot itself is derived as 0 in that case (13.6 slot 37, 14.7 slot 33), not only the
   slots computed from it. A caller-set value still wins.
3. 13.6's slot 7 is modelled: AGS in {2, 3} -> 0, else 1 (stores at 0x00249EE0 and 0x00249D1E,
   register 0x20039F24, literals 0x00249E34 / 0x00249F28). The 13.6 guard is widened to cover
   those two literal words.

## Global Constraints

- Never modify `tests/answer_key.py` data, the `.bin` corpus, the PDF, or `captures/`.
- No build names in fireflymcf docs (`scripts/scrub_check.py` must report 0).
- Cached functions take module constants as arguments (the memo trap); `DERIVATION_GUARDS` stays bound as a default arg.
- Every claim in docs cites bytes (address + instruction).
- Do not push, merge, or tag.

## Review Focus

- Settings with only `extra_slots` (no options): non-empty `values`, so the AGS slot defaults to 0 too. Expected: `{102: 1}` on 14.7 gives `{102: 1, 33: 0, 11: 0, 12: 1, 319: 0}`.
- A caller-set AGS slot (via options or `extra_slots`) is never overwritten by the derived 0.
- Lithionics on with AGS absent (14.7): slot 11 = 3, slot 12 = 1, slot 33 = 0.
- The placement gate now pins slots 11/12 (14.7) and 6/7/37 (13.6) for settings that omit the generator mode: fewer arms walked, so fewer refusals. The owner's builds (AES coach) must stay byte-identical.
- A changed byte at 0x00249E34 or 0x00249F28 (13.6) now derives nothing.

---

### Task 1: Derive "none" for an absent generator mode; model 13.6 slot 7

**Files:**
- Modify: `src/fireflymcf/options.py` (comment block ~600-760, `DERIVATION_GUARDS`, `_derived_slots_136`, `_derived_slots_147`, `derived_slots` docstring; add `ELECTRICAL_LAYOUT_SLOT = 7` beside `BATTERY_BLOCK_SLOT`)
- Modify: every test that asserted the old "unset when AGS absent" behaviour (start with `tests/test_coach_slots.py`, `tests/test_newbutton.py`, `tests/test_cli.py`, `tests/test_livecfg.py`, `studio` tests; find the rest by running the suite)
- Modify docs: `docs/GUICODE.md` Finding 92 (and the two 13.6 slot-7 "Superseded" passages near lines 2553 and 2581 and the bullet near 6514), `src/fireflymcf/recipe.py` / `recipe_ops.py` add_button docstring, `src/fireflymcf/ops_doc.py` then regenerate `docs/RECIPE-OPS.md` with `python -m fireflymcf.ops_doc`, `studio/server/reference.py` per-firmware "derives" notes (add slot 7 for 13.6)

**Interfaces:**
- `options.derived_slots(data, values) -> dict[int, int]` unchanged in signature. New outputs:
  - 13.6, `values` non-empty: `ags = values.get(37, 0)`; out has `37: 0` iff 37 absent; `6: _battery_block_for(ags)`; `7: 0 if ags in (2, 3) else 1`; `453` as now.
  - 14.7, `values` non-empty: `ags = values.get(33, 0)`; out has `33: 0` iff 33 absent; `11: 3 if lithionics else _battery_card_for_147(ags, 0)`; `12: _generator_flag_for_147(ags)`; `319` as now.
  - Slot numbers come from `source_slot(data, "AGS")` as now; if it is None, skip the AGS-keyed slots.

- [ ] **Step 1: Write failing tests** in `tests/test_coach_slots.py` (extend the parametrized `test_147_derivation` and `test_136_derivation_unchanged`, renaming the latter `test_136_derivation`):
  - 14.7 `{102: 1}` (via `options.derived_slots` merged by `coach_slots`, as `test_147_extra_slots_only_derive_the_rail` does) → slots `{102: 1, 33: 0, 11: 0, 12: 1, 319: 0}`.
  - 14.7 values `{105: 1}` → derived `{33: 0, 11: 3, 12: 1, 319: 0}`.
  - 14.7 values `{33: 2}` → derived `{11: 0, 12: 0, 319: 1}` (no 33 in the output).
  - 13.6 values `{39: 0}` → derived `{37: 0, 6: 0, 7: 1, 453: 0}`.
  - 13.6 values `{37: 2}` → `{6: 1, 7: 0, 453: 1}`; `{37: 1}` → `{6: 2, 7: 1, 453: 1}`; `{37: 3}` → `{6: 1, 7: 0, 453: 1}`.
  - `test_derivation_is_an_argument`: with-derive slots become `{37: 3, 6: 1, 7: 0, 453: 1}`.
  - `test_one_changed_byte_derives_nothing` gains 13.6 addresses `0x00249E34` and `0x00249F28`.
  - An explicit caller value wins: `coach_slots(mcf_147, _cfg(extra_slots={33: 1}), ...)` has `33: 1, 11: 2, 12: 1`.
  - Empty values still derive `{}` on both.
- [ ] **Step 2: Run them; confirm they fail.** `python -m pytest tests/test_coach_slots.py -q -n auto`
- [ ] **Step 3: Implement** in `options.py`. Widen the 13.6 guard ranges to include `(0x00249E34, 0x00249E38)` and `(0x00249F28, 0x00249F2C)` (merge with adjacent ranges where they touch) and recompute its sha256 from stock `firefly-13.6/mcf.bin` the way the existing hash was computed. Move the slot-7 comment from "Not modelled" to modelled.
- [ ] **Step 4: Run the full suite** `python -m pytest tests -q -n auto`. Every failure must be an assertion of the old behaviour; update each to the new rule and say why in the report. A failure that is not explainable by the rule is a stop-and-report.
- [ ] **Step 5: Docs.** Rewrite the `derived_slots` docstring's "absent source" section: an absent AGS slot is "none" (owner ruling 2026-11-11) and the cost (a coach with AGS whose settings omit the mode is checked as no-generator). Update Finding 92 with a dated paragraph (2026-11-11) stating the ruling and slot 7 as modelled, citing the stores; remove the "unset slot 12 draws the AES arm" and "slot 7 unmodelled" bullets' present-tense claims (mark as superseded, keep history). Update the add_button docstring, ops_doc + regenerate RECIPE-OPS.md, and Studio's notes.
- [ ] **Step 6: Checks.** `python scripts/scrub_check.py` → 0; owner pins: `cd ../firefly-firmware && .venv/bin/python -m pytest tests -q -n auto` and `../myfirmware/tests/pin_builds.py` (as previous plans ran them) unchanged.
- [ ] **Step 7: Commit** (one or more commits on branch `absent-generator-is-none`).
