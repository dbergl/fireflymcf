# set_gesture: hold-to-toggle for variant-selected (multi-binding) groups

Date: 2026-10-19 · Owner request · Target: `fireflymcf` (`gestures.py`, `recipe_ops.py` `set_gesture`)

## Intent

The owner wants the **Low Volts** button on the compact AES ELECTRICAL page to need a long
hold (3 s, the same as the Overflow Valve and Starlink buttons) both to arm and to disarm, so
a stray touch can't change it.

`set_gesture kind="hold_to_toggle"` already does this for single-entry `DC_DIMMER_COMMAND_2`
command-5 toggles. It refuses Low Volts:

```
GESTURE_REFUSED: group 129 has 4 live bindings; hold-to-toggle needs exactly one
```

That refusal is correct for the reason given in `make_hold_to_toggle`'s docstring: editing
"the" entry of a multi-entry group may edit a copy that never runs. The fix is to support
groups where **every** live copy gets the same edit, so it doesn't matter which copy the
variant counter picks.

## What group 129 is (decoded from stock `d66b41ff…d382` and the current coach build)

| field | value |
|---|---|
| group raw | `11800c80bf010400` |
| hold threshold | 1 s (byte 0 high nibble = 1) |
| variant_count | 1 (byte 0 low nibble) |
| cycles_variant | **False**: byte 1 = 0x80, so release doesn't advance the variant; something else sets it |
| advances_global | True (byte 3 bit 7) |
| repeat rates press / held / release | 0 / 1000 / 0 ms |
| kinds | `{0: 'latch', 1: 'latch'}` |

Live bindings (all `0x1FED9` DGN_CONFIG, level byte 0x2F = the Low Volts selector, command 15,
phases 0x0A = tap-release | release-after-hold, repeat bit clear):

| binding | variant | raw |
|---|---|---|
| 447 | 0 | `d9fe010a0f2f96ffead10000` |
| 448 | 1 | `d9fe110a0f2f96ffead10001` |
| 449 | 0 | `d9fe010a0f2f96ffffd3ffff` |
| 450 | 1 | `d9fe110a0f2f96ffffd3ffff` |

The variant is the arm/disarm choice: variant 0 writes 0, variant 1 writes 1 (last byte of
447/448). On the coach build, `add_aes_low_volts_trigger` drives the counter for touch 129
from the 0x2F status each tick (its "UN-GATED bridge that mirrors the stock cell-45 block →
counter + cell 45"). So whichever copy runs is the right direction, and a gesture edit
applied to **both** variants keeps that.

Touch id 129 → group 129 is registered by objects **315, 795 and 1427** in the coach build
(31, 315 and 795 in stock). 1427 is the compact AES ELECTRICAL page. 795 is the "Triggers:
Low Volts" pill on AUTO ENGINE START SETTINGS (root 45 in the coach build). The owner chose to change **all of them**: every Low Volts button
becomes hold-to-toggle. No group cloning.

## Goals

1. `set_gesture {"group": 129, "kind": "hold_to_toggle", "seconds": 3}` (and via `target`)
   compiles on the owner's stock file inside the coach's project, and gives:
   - group 129 hold threshold → 3 s (low nibble/variant wrap kept)
   - group 129 held-phase repeat → 0
   - **every** live binding in the group's slice → phases = `PHASE_HELD` (0x04) alone
   - no other byte changed (same-size; `verify` accounts for every run)
2. On the coach: a short tap on any Low Volts button does nothing. A 3 s hold arms it if it's
   disarmed and disarms it if it's armed, firing once per hold, and the colour follows.
3. The existing single-binding path (groups 169 and 229 in the coach build, hardware-verified
   2026-08-19) produces **byte-identical** output to today.

## Non-goals

- Cloning group 129 so only the compact page changes (the owner declined).
- `kind: "latch"` conversion (still `NOT_SUPPORTED`).
- Groups whose variants carry different gestures, or that mix DGNs or selectors (see the
  premises below). Refuse them; don't try to cover them.

## Anti-goals (a build that compiles but is still wrong)

- An edit that changes only some live copies. One variant then stays a tap, so Low Volts
  works by tap in one direction and by hold in the other.
- A hold that fires repeatedly while the finger stays down, flipping arm/disarm each second.
  Keep the "send once" belt and braces: the repeat bit must be clear on every binding, and
  the held repeat is set to 0.
- Relaxing the premises for single-entry DC_DIMMER groups. The existing checks stay exactly
  as they are on that path.

## The extended premises (the multi-binding path)

Take this path when the group has more than one live binding. Refuse with `GESTURE_REFUSED`
and a specific message if any of these fail:

1. Every live binding has the **repeat bit clear**.
2. All live bindings share **one DGN** and **one selector/command identity** (here: DGN
   0x1FED9, command 15, level 0x2F). Different DGNs or targets in one group mean it isn't a
   single two-state control.
3. Every variant has the **same current phase mask** (`variants_agree`), and that kind is a
   release-driven toggle (`latch`, `tap-only`). Don't convert momentary, ramp or tap-or-hold
   groups.
4. **The variants pick the direction:** either (a) the group has exactly two variants whose
   bindings differ only in the written state value (0 vs 1), or (b) every binding is a
   DC_DIMMER command 5 toggle. Case (b) is the old reasoning ("the same gesture for on and
   off") extended to more copies.
5. The group does **not** cycle its variant on release (`cycles_variant == False`). If it did,
   each hold would also advance the counter, and whether the next hold fires the opposite
   copy would depend on the timing of the cycle and the external setter. That's unproven, so
   refuse it.

When all five hold, apply `set_phases(PHASE_HELD)` to every live binding in the slice, then
`set_hold_seconds` and `set_repeat_index("held", 0)` once on the group.

## Decided by the owner

- 3 s hold, both directions.
- Applies to every button that uses group 129.

## My calls (the builder may revisit them, saying why)

- **Use `PHASE_HELD` (fires when the threshold passes), not `PHASE_RELEASE_HELD`.** This
  matches the existing hold buttons on the coach, so the panel acts the same everywhere.
- **Bindings 449/450's tail (`ffd3ffff`) differs from 447/448's (`ead1000x`).** I haven't
  decoded what that means. The design converts both pairs the same way, and premise 2 checks
  only DGN/command/selector, not the tail. If the builder finds that 449/450 go to a
  different consumer that should *not* be held, stop and report back; don't guess.
- The op stays `verified: yes` for the single-binding path. The multi-binding path is
  **unverified** until the coach flashes it. Show that in the manifest line
  (`HARDWARE-UNVERIFIED (coach test: tap does nothing; 3 s hold arms; 3 s hold disarms;
  colour follows; fires once per hold)`) and in the op's `status`. Use the mechanism that
  other half-verified ops use; the builder picks it.

- **How premise 4(a) compares (settled after the builder check).** Pair the bindings by their
  position within each variant: 447↔448 and 449↔450. Each pair must be byte-identical except
  for the variant nibble (byte 2 high nibble) and, for 0x1FED9, the final state byte. A pair
  that is identical in both variants (449/450, tail `ffd3ffff`) is allowed. At least one pair
  must differ in its state byte (0 vs 1), or the group isn't two-state and is refused. Group
  129 passes.
- **No check that something sets the variant counter.** The byte premises are enough for
  the op. The direction depends on an outside setter, which on this coach is the
  `add_aes_low_volts_trigger` bridge (already in the project, hardware-verified) and the stock
  cell-45 block in AGS. The manifest line for the multi-binding path must say "direction is
  chosen by the group's external variant setter". The coach test (arm *and* disarm) is what
  proves it. Adding a dependency check on the bridge op would change the interface for a case
  this coach doesn't have.
- **Mode.** The coach runs AES (`AES Single Output` in `coach.json`), so the coach test covers
  AES only. AGS behaviour follows from the same group edit but is not tested; say so in the
  unverified note.

## Left to the builder

- Code structure (a new function in `gestures.py` next to `make_hold_to_toggle`, or that
  function growing a branch), names, and error message text.
- Tests: at least (1) group 129 on the stock fixture gives exactly the expected 6 byte edits
  (1 hold, 1 repeat, 4 phases); (2) groups 169/229 give the same output as before; (3) each
  premise 1–5 refusing on a synthesised group; (4) the coach project with
  `lv-hold {"group": 129, …}` validates and `verify` accounts for every run.
- Updating `ops_doc` / `fireflymcf ops set_gesture` text, and `docs/CONTROL-BEHAVIOR.md` if
  it says multi-binding groups can't be converted.

## Handoff back

When it's done: the fireflymcf commit, a note that the owner's environment needs
reinstalling, and the result of `fireflymcf validate` on the coach project
(the owner's project file, which already contains
`lv-hold {"target": 285, "group": 129, "kind": "hold_to_toggle", "seconds": 3}`). The owner's
session will then compile, verify and hand over the flashable build.

## Outcome (2026-10-19)

Implemented in `gestures._make_multi_hold_to_toggle`. Bindings 449/450 are the selector `0x2F`
read-back request (`D3`) that follows each write, the same consumer, which PC.39 shows is what
repaints the button; both pairs are converted. Flashed and verified by the owner the same day:
the 3 s hold works. The op is now verified for this path; AGS mode is untested.

**Correction to "What group 129 is" above.** Object 795 is not on AUTO ENGINE START SETTINGS.
`fireflymcf bus-map --name "Low Volts"` places stock's reachable group-129 control on AUTO GEN
START SETTINGS (AGS mode), and the owner reports that the AES floorplan's AUTO ENGINE START
SETTINGS page has no Low Volts button. On this coach, the compact ELECTRICAL page (object 1427)
is the only Low Volts button.
