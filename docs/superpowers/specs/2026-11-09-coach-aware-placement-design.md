# Coach-aware placement for add_button — design

Date: 2026-11-09. Status: draft for the owner's approval.

## Intent

`add_button`'s placement gate (`newbutton.check_placement`, Plan 9) walks every type-6 switch arm on the target page. It refuses an overlap only in the **base arm**, which is each switch's `child_base` (value 0). Overlaps in every other arm are reported, not refused (`CompileResult.placement`, the sidecar's `placement`, a manifest suffix).

The base arm is not the arm a coach draws. The reference coach runs slot 6 = 1 (`build/coach-wd.json`), so HOME draws card 902, a non-base arm. Today the gate therefore:
- refuses overlaps under a configuration the reference coach does not run;
- only reports overlaps under the one it does run.

On 14.7, with slot 102 = 1, the default spot covers the working "Star Link" button and its touch region, and only a report line says so.

The goal: when the recipe or the command line says which settings the coach runs, the gate refuses an overlap in what that coach draws, and reports the rest.

## Goals

- A recipe can carry the coach's settings, so a recipe handed to another owner carries theirs. This follows the build-hygiene rule that one recipe states everything a build does.
- `compile --config FILE` overrides the recipe's settings. Its use: checking someone else's recipe against your own coach.
- With settings, `add_button` refuses `PLACEMENT_OVERLAP` when the footprint overlaps a record the coach draws under those settings. All other arm overlaps are still reported, as today.
- A build's sidecar records the settings the gate used and where they came from.

## Non-goals

- Other placing ops (`add_text_label`, `move_element`, `move_control`, …). This covers `add_button` only, for now (owner, 2026-11-09).
- Reading the settings from the image. Most of the deciding values (AES/AGS mode, floorplan, fitted options) are chosen on the panel's OPTIONS page or come from the G12, so the file alone cannot say which arm a coach draws. That is inference, not checked against the code; nothing here depends on it.
- Changing any build's bytes. The settings change only what is refused and what is reported.

## Anti-goals

- A recipe that compiles today must not stop compiling when it has no settings.
- Studio's `preview` must never decide whether a build compiles.
- An unknown or unnamed setting must never count as safe.

## Constraints

- **13.6 stays byte-identical** for every existing recipe. That covers the full fireflymcf suite, the research byte pins (`../firefly-firmware`, 457), and the owner pins (`../myfirmware/tests/pin_builds.py`, 4/4).
- **Refuse, never guess.** Every refusal has a named code, documented in `ops_doc` and checked by `tests/test_ops_doc.py`.
- **No build names** in fireflymcf docs; `scrub_check` exits 0. Docs cite evidence by date.
- **Memo trap.** Anything memoised takes module constants as arguments.
- **The compile cache.** The settings change compile outcomes, so they must be part of any cache key that a compile result is served from:
  - `recipe._prefix_key` (the in-process prefix cache);
  - the test suite's disk cache (`tests/conftest.py` and its mirror in `../firefly-firmware/tests/engine/conftest.py`).

## Decided (owner, 2026-11-09)

1. **Where the settings come from:** a `coach` block in the recipe (`.ffproj.json`), with `compile --config FILE` as an override.
2. **Scope:** `add_button` only.

## Decided (my calls; the owner can overrule)

3. **The block is separate from `preview`.**
   - `preview` is documented as "render-only state and never build input" (`docs/AGENT-GUIDE.md`, `recipe.py`), and `_prefix_key` deliberately ignores it.
   - Reusing it would let a Studio preview toggle decide whether a build compiles.
   - So the new block is a top-level field named `coach`.
4. **The block has the same form as the existing coach configuration** (`options.CoachConfig`): `{"floorplan": "WD", "options": [...], "extra_slots": {"6": 1}}`.
   - `build/coach-wd.json` already has this form, which `render --config`, `verify --config` and `coach-config` use.
   - It resolves to a `{value_slot: value}` map through `options.render_slots(data, config)`, the same path renders take, including the slots GuiCode derives for itself (`derived_slots`).
   - `CoachConfig` also carries `preview` (render values). It is ignored here: in the `coach` block it is either refused or dropped. That is the builder's call; say which.
5. **Override semantics:**
   - `--config FILE` replaces the recipe's `coach` block whole. It is not merged.
   - A partial merge of two coaches' settings would describe neither coach.
6. **What the gate does with a resolved slot map `S`:**
   - **Refuse:** the footprint overlaps any record placed under the arm path that `S` selects. The comparison follows the walk `newbutton.check_placement` already does, but with `S` in place of the all-base arm. It admits only the page root's enclosing background and one enclosing wt-33 card. A slot the page's switches read but `S` does not name is treated as the next point.
   - **A slot `S` doesn't name:** every value of that switch is checked, and an overlap under any of them refuses.
     - The refusal names the slot, so the owner can add it to the settings.
     - Nested switches follow the same rule. This uses `pagewalk.switch_arm_paths` (Plan 9, opt-in, capped at 512 sets), restricted to paths consistent with `S`.
     - A switch driven by a live value (a status, not a setting) cannot be named in the settings. Its arms are therefore always checked, and an overlap under any of them refuses. That is a real overlap at runtime.
     - Confirmed 2026-11-09 (the builder check): under `build/coach-wd.json` the 13.6 default spot is not refused.
       - `S` resolves to `{5:8, 6:1, 37:3, 38:0, 39:0, 108:1, 109:1, 110:1, 112:1, 113:0, 453:1, 512:1}`.
       - Under `S` alone, HOME admits only card 902. There are 38 consistent paths, with 0 overlaps.
       - The live-status switches that do overlap the spot (slots 11, 166 and 169) are drawn only under slot 6 = 2 or 3.
   - **Report:** overlaps under arms `S` does not select are reported exactly as today.
   - **No settings at all** (no `coach` block and no `--config`): today's behaviour, unchanged. The all-base arm refuses and the rest is reported.
7. **Validation:**
   - Each named slot must be one that some type-6 switch on the image reads (`value_slot`), or one `derived_slots`/`slot_values` knows.
   - Each value must be a value one of that slot's switches has an arm for. The `0xFFFF` default arm admits any value.
   - Otherwise the build refuses with a new code, `COACH_CONFIG`, naming the slot and the values it can take.
   - A floorplan or option name `CoachConfig` cannot resolve refuses `COACH_CONFIG` too.
8. **Sidecar:**
   - It gains a `coach` entry: the resolved slot map, plus `source` (`"recipe"`, `"--config <path>"`, or absent when there were no settings).
   - The manifest line for an `add_button` says "checked against the coach's settings" when settings were used.
9. **Studio.** The recipe round-trips the `coach` block unchanged: load, save and `to_json`. An editor for it in Studio is out of scope.
10. **How unnamed switches are checked.**
    - The check is one switch at a time, with nested arms, over the paths consistent with `S`. That is `pagewalk.switch_arm_paths` restricted to `S`, the same coverage the Plan 9 report gives.
    - It does not check every combination of independent switches. Under the reference settings on 13.6 HOME that would be 6,336 walks per compile.
    - An overlap the per-switch check would miss needs two independent unnamed switches to move flow together. The builder check's exhaustive search (2026-11-09: all 6,336 combinations under `build/coach-wd.json`, 0 overlaps) found none at the default spot. The docs must state this limit.
    - If the walk hits `switch_arm_paths`' 512-set cap, the build refuses with its own named code, never `BUILD_REFUSED`.
11. **The settings may pin a live slot.**
    - `extra_slots` is `CoachConfig`'s documented escape hatch for raw slot values, and nothing yet classifies a slot as a setting or a live status.
    - So a slot the settings name is taken as the owner's explicit statement, even if a status drives it at runtime.
    - The sidecar's `coach` entry lists every slot the settings pinned, so a reviewer can see one.
    - The anti-goal holds for unnamed slots; a named slot is not "unknown".
12. **Derived slots apply only where they are located.**
    - `options.derived_slots` computes 13.6's slot 6 and slot 453 from the options. They are 13.6 facts; on 14.7, for example, the battery card reads slot 11.
    - On any image where those derivations are not located, they are skipped, and their slots count as unnamed. Unnamed slots are checked strictly.
    - That can over-refuse on 14.7 until the derivation is located there. A refusal is the safe failure.
    - Locating the derivation on 14.7 is out of scope.
13. **One switch base that reads two slots** (13.6: 441 reads slots 204 and 208; 1411 reads 647 and 649; 635 reads 648 and 650).
    - The base counts as named only when `S` names every slot it reads, all with the same value.
    - If `S` gives those slots different values, the build refuses `COACH_CONFIG`.
    - If `S` names some but not all of them, the base counts as unnamed and is checked strictly.

## Left to the builder

- Where the resolution lives (`recipe`, `options` or `newbutton`), and how the resolved map reaches `AddButton.apply` (a `ctx` field is the natural route).
- Report wording, as long as it keys entries by value slot as the Plan 9 report does.
- Whether `--config` also applies to `verify` for `add_button` placement. If it is cheap and consistent, do it; otherwise leave it.
- The tests. They must show at least:
  - on 13.6 under `build/coach-wd.json`'s settings (slot 6 = 1, …), the default spot builds byte-identically and the sidecar names the settings;
  - on 13.6, a spot clear in the base arm but covered under slot 6 = 1 (in card 902) refuses with settings, and only reports without them;
  - on 14.7 with `extra_slots {"102": 1}`, the default spot refuses on object 240 ("Star Link"); with `{"102": 0}` it builds;
  - `--config` replaces the recipe block, shown by a recipe whose block and file disagree;
  - an unnamed slot with an overlapping arm refuses and names the slot;
  - bad slots, values and option names refuse `COACH_CONFIG`;
  - a change to the `coach` block changes the prefix cache key, and a change to `preview` does not.
