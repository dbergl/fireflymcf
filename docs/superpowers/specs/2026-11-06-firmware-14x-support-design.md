# Recipe ops on firmware 14.x — design

Date: 2026-11-06. Status: approved by the owner, 2026-11-06.

## Intent

Anyone, on any firmware version, should be able to customise their panel with fireflymcf. Today
almost everything is 13.6-only. Several owners run 14.x, so 14.x is first. Success: a 14.x owner
starts from their own stock `mcf.bin` and builds the same kinds of changes a 13.6 owner can, with
the same refusals, gates and verification.

The work is not a 14.7 port. It teaches fireflymcf to **find** each piece it now takes from a
13.6 address, on whatever image it is given. 14.x is the first target and the acceptance test;
11.x should mostly follow from the same work, and is a non-goal here.

## Where things stand (measured 2026-11-05/06)

- 56 op types are registered; 12 are unverified on hardware.
- The reference coach's current recipe (49 op types, 147 ops) was compiled op by op, each with
  the ops it references, against 11.23, 13.6 and 14.7 stock (`compile_project`, no flash, no
  emulator). Against 14.7:
  - **Run unchanged:** `append_filter`, `drop_from_master`, `rename_label`, `set_record_color`,
    `install_glyph` (refused only where 14.7 already uses the character slot), `set_gesture`,
    `set_corner_radius`.
  - **Generic, but the recipe's 13.6 targets don't exist on 14.7:** `move_element`,
    `move_attach`, `recolor`, `remove_records`, `remove_control`, `set_icon`, `fill_to_capsule`.
    Every failure was a target lookup (`NO_SUCH_RECORD`, `NO_SUCH_LABEL`, `NO_FIELD`). None of
    them has run on 14.7 with a real target.
  - **Refuse 14.7:** about 35 op types: every op that adds a control, a label, a readout or live
    status, plus `set_theme`, `tank_heater_names`, `pin_filter_instance`, `set_binding_bytes`,
    `remove_option_interlocks`, `fix_variant_gate`, `pin_switch_arm` and the coach-specific ops.
    Most refuse with a clear code. Some die with a raw `IndexError`.
- A project names its stock image's sha256 (`BASE_MISMATCH` otherwise) and 13.6 object numbers,
  so a recipe never moves between firmware versions. A 14.x owner writes a recipe against their
  own stock image.

### The shared 13.6 pins

Most of the refusals trace back to a few pins:

1. **The GuiData section's name is hard-coded** as `"section-checksum:Lyra 13v6 GuiData"` in the
   reseal check (`newbutton.py` five times, `statusbind.GUIDATA_CHECKSUM`). 14.7's section is
   `Lyra SW20 14v7 GuiData`. This blocks `add_button`, `pin_filter_instance`,
   `set_binding_bytes`, `add_pump_auto_off` and their dependants.
2. **Donor (template) controls by 13.6 object number.** New buttons and labels clone 13.6
   objects; examples are 829 record 7 (`add_rvc_button`, the AES ops) and 285 record 1
   (`add_text_label`, `add_enum_label`, refused as `drifted` against a pinned byte string).
3. **The per-tick hook.** `followchannel.HOOK_OFF = 0xEE3C`, `TAIL_TARGET = 0x00247BF8` and
   `STOCK_APPEND_POINT = 0x00259BB4` are 13.6 constants. On 14.7, GuiCode+0xEE3C holds
   `ldr r6, [sp, #0x130]`. Every live-status op chains here: readouts, status lights, followers,
   button states, pump auto-off and Low Volts.
4. **The display-cell table.** `readout`/`celltables` cannot find a cell for a 2-byte readout on
   14.7 ("no spare halfword … no free SRAM run"). On 11.23 the locator fails outright.
5. **GuiCode routines traced on 13.6** that check exact bytes: `set_theme` (theme-init add
   chain), `tank_heater_names`, `remove_option_interlocks`, `retarget_inverter_gear`,
   `show_nav_slides_icon`, `fix_variant_gate`.
6. **The colour model.** On 14.7, `add_text_label` and `add_enum_label` refuse white with
   `NO_COLOUR`: "not in the firmware palette", which is six colours. Whether 14.7 takes inline
   RGB as 13.6 does, or only palette indices, is not yet known.

7. **LCD-firmware constants.** Several facts the ops rely on belong to the LCD firmware, not to
   GuiCode:
   - the variable-table slot bases `0x20007FC8`/`0x20008000` (`followchannel.status_slot_addr`);
   - the SRAM initialised only to `0x2000AD98` (`raminit.py`), which the display-cell search
     depends on;
   - the DGN registration cap of 83;
   - the receive filter.

   13.6 has its own LCD firmware (sha256 `a55344…`); 11.23 and 14.7 share another
   (`cbed1028…`). These constants must be located in, or proven equal for, the LCD firmware
   inside the stock image being built.

### What a panel runs

The boot menu flashes three units separately: LCD Firmware, the GuiCode+GuiData pair, and the
G12 (`MCF-FORMAT.md` §2). `cli.bin` is identical on 13.6 and 14.7 (`8f93fc0d…`); 11.23's
differs. A real 14.x owner runs 14.7's LCD Firmware and pair. The coach's only 14.7 run
(BUS-BASELINE PC.41, 2026-08-30) flashed the pair alone over 13.6's LCD Firmware and G12, so
nothing about the 14.7 LCD firmware has been seen on hardware.

### The emulator

`emu.scenario.supported` accepts 13.6's LCD firmware only. 11.23 and 14.7 share a different LCD
firmware (sha256 `cbed1028…`). With the check bypassed, 14.7 boots in 3.4 s and draws its stock
HOME correctly. That covers drawing only: taps, navigation, bus traffic and the G12 peer
on 14.7 have not been exercised. `emu/g12.py` already models the 14.7 G12 differences (ABI 17).
Ghidra has `guicode147` and `g12code147` projects, but no LCD project for the shared 11.23/14.7
LCD firmware.

## Goals

1. **Locate, don't pin.** Each 13.6 pin above becomes a structural locator. It finds the piece in
   the image it is given, proves what it found (cited instructions or decoded records, the same
   standard as 13.6's `proven-from-code`), and refuses when it cannot. It returns 13.6's current
   values on 13.6, so every 13.6 build stays byte-identical.
2. **14.7 runs the general-purpose ops.** In priority order, by how many ops each unblocks:
   1. section name;
   2. donors for new buttons and labels;
   3. the per-tick hook and chain;
   4. the display-cell table;
   5. the colour model;
   6. `set_theme`;
   7. the remaining traced routines that 14.x owners would want: interlocks, tank-heater names,
      variant gates, switch arms.
3. **The emulator runs 14.7.** Lift the check for the shared LCD firmware once boot, page
   navigation, taps and panel sends are shown to behave. Each op enabled on 14.7 gets an
   emulator test on 14.7 stock. An agent extends the emulator where 14.7 differs.
4. **Verification is recorded per firmware.** `hardware_verified` becomes per firmware version.
   An op the coach ran on 13.6 is unverified on 14.x until a 14.x panel runs it. `compile` keeps
   requiring `--allow-unverified` for it there, and `fireflymcf ops` and `RECIPE-OPS.md` show
   the status per firmware.
5. **Every refusal is a clear refusal.** No raw `IndexError`/`ValueError` reaches the user on
   any firmware. A 14.x refusal says what was not found.

## Non-goals

- 11.x support. It should mostly follow, and it gets its own pass later.
- Coach-specific ops: APS-500 reboot, BMS battery heater, the LEVEL page and its sensor, 48 V
  battery, inverter gear, the compact AES layout, `swap_aes_electrical`. They stay 13.6-only and
  refuse 14.x by name.
- Moving a recipe between firmware versions. Recipes stay per stock image.
- Hardware verification of anything that changes the G12's own configuration on 14.x. The
  emulator is the evidence there.

## Anti-goals (a failure even if it "works")

- **A second hard-coded table of 14.7 addresses.** It would work for 14.7 and teach nothing for
  the next version. A 14.7 constant is acceptable only as a test expectation of what the locator
  finds, never as the locator.
- **A guessed match.** A locator that picks "the most likely" candidate, or fits bytes to a
  photo. When there is more than one candidate, or none, it refuses (CLAUDE.md evidence rules: a
  defensible negative beats a fitted answer).
- **Any change to a 13.6 build.** The reference builds' byte pins in the research repo
  (`tests/test_recipe_hank*.py`, `tests/engine/`) and the full suite must stay green, byte for
  byte.
- **An emulator pass on 14.7 that only proves the screen drew.** A pass has to exercise the op's
  behaviour: the tap, the send, the redraw on status.

## Constraints

- The CLAUDE.md evidence rules apply to 14.7 as they do to 13.6. Every located address cites the
  instruction or record that proves it. The capture wins over the file.
- Stock images are never modified (the corpus write guard).
- Public fireflymcf docs and code cite evidence by date, never by build name (`scrub_check`).
- Hardware: the owner flashes a 14.x build's **LCD Firmware and GuiCode+GuiData** units on
  their own panel, leaving their 13.6 G12 in place, and confirms anything panel-side (drawing,
  taps, sends). This matches a real 14.x owner's panel except for the G12. Nobody checks G12
  configuration changes on a 14.x coach. 14.7's LCD Firmware has never run against a 13.6 G12;
  that they interoperate is an inference (11.23 and 14.7 run it with different G12s). The first
  14.x flash checks it with stock 14.7 units before any built image.
- Only one 14.x stock image exists in the corpus (`firefly-14.7 (Terrain)`). The owner is looking
  for more. Each one found becomes an acceptance test for every locator.
- Work on a feature branch per pin. Release through the tag-triggered workflow.

## Decided by the owner

- 14.x first; "any version" is the end goal.
- Generalise by locating, in the priority order above.
- Verify with the emulator for most tests, extended by an agent where needed, plus the owner's
  14.x panel for panel-side checks. G12 changes are not hardware-checked.
- Hardware verification is per firmware. `--allow-unverified` stays required on 14.x until a
  14.x panel has run the op.
- 14.x hardware tests run 14.7's LCD Firmware and pair on the owner's panel, over their 13.6 G12.
- The owner will look for more 14.x stock images.

## Calls made in writing this (the owner should check these)

- **The verification key is the GuiCode version and the LCD firmware sha256 together.** A 14.x
  hardware check counts only when the panel ran 14.x's own LCD firmware.
- **Emulator evidence never clears `--allow-unverified`.** It is recorded and shown, for
  example as "emulated on 14.7", but the gate stays two-level. Ops that change the G12's own
  configuration stay unverified on 14.x, and their owners use the override.
- **A firmware image nobody has seen is refused, not guessed.** An op enables a firmware version
  by an allow-list of identified (GuiCode name, LCD sha256) pairs that its locators have been
  tested against. An unlisted image gets a clear `UNSUPPORTED_FIRMWARE` refusal from that op,
  never a best-effort run. A new 14.x image is added to the list after its locators pass on it.
- **Colours:** if 14.7 takes palette indices only, ops refuse a colour outside the palette, as
  they do now (`NO_COLOUR`). Extending the palette is a later, separate piece of work. `set_theme`
  on 14.x then covers only what the palette allows, or waits for that work.
- **Floorplan-specific ops** (`tank_heater_names`, `remove_option_interlocks`) are ported only if
  their 14.x meaning can be located structurally, for example "remove whichever option interlock
  this image has". Otherwise they keep refusing 14.x and leave this project.

## Left to the builder

- How the per-firmware verification status is stored (a set of versions per op, a mapping from
  version to evidence, …), within the key chosen above.
- Each locator's method, as long as it proves its find and refuses ambiguity.
- Whether the donors for new controls are found by record shape on any page or chosen per
  firmware with a structural proof.
- The order inside a priority step, and how the work splits into branches and releases.
- How far the emulator is extended before its check is lifted. The minimum is boot, the five
  rail pages, a tap that sends on the bus, and a status frame that repaints.
