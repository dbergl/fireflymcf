# Firmware 14.x, Plan 7: The Per-Tick Decoder's Range — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `decodeguards.decoder_range` finds the per-tick decoder's extent from the code on any image, so `add_register_readout`'s `cell_slot` mode runs on 14.7.

**Architecture:** the range starts at the hooked per-tick callee (`hooklocate`). It ends at that function's only epilogue, which is the one `tanknames.decoder_ranges` entry starting at the callee. A proof shows no second return and no branch out except to short tail blocks that rejoin the function without loading a receive slot. The 13.6 pin `DECODER_END_136` and the 13.6-only gate are removed.

**Tech Stack:** Python 3.11+, capstone, pytest, `fireflymcf.emu`.

**Spec:** `docs/superpowers/specs/2026-11-06-firmware-14x-support-design.md` ("locate, don't pin"; "Left to the builder": each locator's method).

**Depends on:** Plan 2 (`hooklocate.locate_hook(...).stock_callee`) and Plan 5 (cell and type tables for `cell_slot`). Both are merged into `main`.

**Research (2026-11-08), the evidence this plan cites.** The prototype is `scratchpad/locators/decoder/proto_locator.py`, written up in `scratchpad/locators/decoder.md`.
- **The old end was an artefact.** 13.6's end `0x24AB00` sits in a literal pool, and nothing documents why it was chosen. Scanning to the epilogue `0x24EDB0` gives exactly today's seven guards on 13.6.
- **What the locator returns:**
  - 13.6: `(0x247BF8, 0x24EDB0)`;
  - 14.7: `(0x247CAC, 0x24F1FC)`, with the same seven variables, and the Absorb/Float fence at `0x24ABE0`;
  - 11.23: `(0x247590, 0x24CDFA)`.
- **Uniqueness:** each image has exactly one `decoder_ranges` entry that starts at the callee.
- **Negatives:** a NOP over the 13.6 epilogue refuses, and so does a `bx lr` planted at `0x24A000`.
- **Emulator, stock 14.7:**
  - Float raw 239-360 is stored, as `(absorb+1)>>1`; 238, 361 and 1080 are rejected. The inherited case also holds: a good Absorb is discarded when Float is out of range.
  - A rejected decode does not keep the old value. The cell reverts every tick to the config-byte copy, 14.7 `0x247CE8` (13.6 `0x247C34`).
- **What the guards really are:**
  - Only Absorb/Float truly skips its store.
  - Vars 51, 52, 47, 48 and 211 store a fixed substitute instead (100, 1000 or 60).
  - Var 271 is a scanner artefact: the fence tests a config byte, and the store is unconditional.
  - 14.7's var 52 bound differs from 13.6's: it admits 0..99 rather than 1..99.
- **`cell_slot` uses the range only as a gate**, and everything else it needs already locates on 14.7: cell 147 resolves to `0x20039B3C`, and the config-byte copy to silence is at `0x247CE8`.

## Global Constraints

- **13.6 stays byte-identical.** `range_guards` over the located range equals today's output on 13.6. Both suites pass: the full fireflymcf suite and the research byte pins.
- **No 14.7 constant becomes a locator.** The 14.7 range, cell `0x20039B3C` and silence address `0x247CE8` are test expectations only.
- **Refuse, never guess.** Each of these refuses with `DECODER_RANGE_UNKNOWN`:
  - the hook doesn't prove out;
  - zero, or more than one, region starts at the callee;
  - the end is not a `pop {..r4..pc}`;
  - a second return;
  - a branch out that is not a rejoining tail;
  - a tail that loads a slot.
- **The guard scanner is not changed.** Its 13.6 output stays as it is. The corrections are documentation.
- **Located results are memoised through `memo`**, keyed on bytes, with module constants passed as arguments.
- **Docs cite evidence by date.** `scrub_check` exits 0.
- **`cell_slot` is not enabled on 11.23.** The scanner's variable lists leak there.

## Review Focus

1. **A literal pool mis-decoded inside the range** could fake an `r4` write or a return. The epilogue check (`pop {..r4..pc}`) and the tail rule must refuse rather than truncate (Task 1 negatives).
2. **A `cell_slot` readout on a value whose stock store is unfenced** would fight the routine every tick. Today's `silence` covers the config copy. State in the op docs which stock writers remain, and leave any new refusal out of scope.
3. **A grown or built image used as stock.** The range comes from GuiCode alone, and authored routines live outside the callee (Task 1: the range is unchanged on a reference build).

---

### Task 1: `decoder_range` located from the code

**Files:**
- Modify: `src/fireflymcf/decodeguards.py`.
  - `decoder_range(data)` follows the research algorithm. It returns `(start, end)`, with a proof (`decoder_range_proof`, or a richer return type the callers can ignore).
  - Delete `DECODER_END_136` and the `firmware.identify == "13.6"` gate.
  - Correct the module docstring:
    - the admitted Float range is raw 239-360;
    - a rejected decode reverts to the config byte rather than keeping the old value;
    - only Absorb/Float truly skips; the substitutes and the var-271 artefact are listed (inference where it is inference).
- Modify: `tests/test_hooklocate.py:367-410`.
  - The `_PIN_DECODER_END` oracle becomes "the guards over the old 13.6 pin equal the guards over the located range".
  - The monkeypatch test becomes: stub `tanknames.decoder_ranges` to return `()`, and `range_guards` refuses.
- Modify: `docs/GUICODE.md`. Add a dated finding (2026-11-08): the decoder's extent, the 14.7 fence at `0x24ABE0`, the 239-360 correction, and the skip-vs-substitute classification.
- Test: `tests/test_decodeguards.py`.

- [ ] **Steps:**
  1. Write the tests:
     - 13.6 returns `(0x247BF8, 0x24EDB0)`, and its guards equal the old-pin guards;
     - 14.7 returns `(0x247CAC, 0x24F1FC)`, with 7 guards and the Absorb/Float fence at `0x24ABE0`;
     - 11.23 locates;
     - the two negatives refuse (epilogue NOP'd; planted `bx lr`);
     - a reference build used as stock gives 13.6's range.
  2. Run the tests; they should fail.
  3. Implement.
  4. Run the focused tests, the full suite, the research pins and `scrub_check`.
  5. Commit: `"decodeguards: the per-tick decoder's range is located from its own epilogue"`.

### Task 2: `cell_slot` on 14.7, proved in the emulator

**Files:**
- Test: `tests/emu/test_ops147.py`.
  - Replace the `cell_slot` refusal test and its strict xfail with the emulator proof.
  - Add `test_decoder_range_147_fence`.
- Modify: `recipe_ops.py`, the `AddRegisterReadout` docstring and its 14.7 note, and `ops_doc.py`. Remove the DECODER_RANGE_UNKNOWN-beyond-13.6 wording, and say which stock writers `silence` must cover.
- Regenerate: `docs/RECIPE-OPS.md`.

**Tests (stock 14.7):**
- **`test_decoder_range_147_fence`:**
  - for the guard `range_guards` names on var 207, publish Float 239 and 360; the cell equals `(absorb+1)>>1`;
  - publish 238, 361 and 1080; the cell is not that value and equals the config byte.
- **`add_register_readout` with `cell_slot` 147 and `silence` set to the located config copy:**
  - register `{dgn: 0x1FECC, index: 206}`, `add 1`, `shift 1`;
  - publish 1152 and 1080; the cell holds 576, where stock leaves 255;
  - the Absorb row's digits repaint, and only inside its box.

- [ ] **Steps:**
  1. Write the tests.
  2. Run them; they should fail with `DECODER_RANGE_UNKNOWN`.
  3. Implement the doc changes. No op-level `firmwares` change is needed, since `add_register_readout` already lists 14.7.
  4. Run `tests/emu`, both suites and `scrub_check`, and regenerate the docs.
  5. Commit: `"add_register_readout: cell_slot runs on 14.7 (emulated)"`.

### Task 3: Hand-off

- [ ] Report to the owner:
  - that `cell_slot` runs on 14.7 (emulated);
  - the skip-vs-substitute finding: only Absorb/Float truly refuses a value; the others substitute a fixed value, and one was a scanner artefact;
  - the two docstring corrections (239-360, and the reversion to the config byte).

  Release only with the owner's approval.
