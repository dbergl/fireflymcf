# What you can change — a catalogue by intent

This is the list to read first if you are wondering *"can the tooling do X?"* It covers every
change the compiler can make, grouped by **what you want to do** rather than by the internal op
name. Each entry names the op (or ops) that carry it out and links to the full technical reference.

- **Discover it here.** This file is intent-first and plain-language.
- **`docs/COOKBOOK.md`** has the exact command lines for the common changes.
- **`docs/RECIPE-OPS.md`** is the complete reference — every parameter, default and error code —
  generated from the code. Where this catalogue names an op like [`rename_label`](RECIPE-OPS.md#rename_label),
  the link goes there.
- **`docs/AGENT-GUIDE.md`** is the end-to-end build procedure.

Every change is one **op** in a project recipe (`*.ffproj.json`), applied to *your* stock firmware.
Object and channel numbers are **specific to your build** — find them on your own firmware with
`pages`, `labels` and `render`, never copied from another coach. A recipe can chain as many ops as
you like; the compiler runs every structural gate and writes a flashable `mcf-*.bin`.

## How to read "Status"

| Status | Meaning |
|---|---|
| **Proven** | The mechanism has been flashed to the reference coach (Jayco B-Van, firmware 13.6) and verified. |
| **In v26** | Also part of the image currently running on the reference coach (`builds/experiment-v26-coach48v.ffproj.json`). |
| **Unverified** | The tooling builds it and the gates pass, but it has never been flashed. Needs a coach check with stock on the stick before you rely on it. |
| **Superseded** | A better op replaced it; listed for completeness only. |

Statuses here are summaries. `docs/RECIPE-OPS.md` and the reference coach's build log (not published) carry the dated detail and
the proof behind each one.

---

## Rename and re-word

| What you want | How | The choices you have | Status |
|---|---|---|---|
| Rename a control or a readout label ("Water Pump" → "Pump") | [`rename_label`](RECIPE-OPS.md#rename_label) | Same-size by default (the new text is padded to fit the old field). A **longer** name is allowed with `allow_grow`, which relocates the record. Pick which side the padding sits (`align`: right / left / center). | Proven · in v26 |
| Fix the tank-heater diagnostic-row names so your floorplan's four tanks are all named | [`tank_heater_names`](RECIPE-OPS.md#tank_heater_names) | No options. It widens the name whitelist for floorplan WD; the trade is that SY loses its Grey row and WT its Black row, so don't hand such a build to those floorplans without saying so. | Proven · in v26 |
| Rename the **build itself** (what shows in the coach's boot/flash menu) | not an op — set `build_name` at the top of the recipe | Any text. Give every experimental build a distinct name so the flasher can tell them apart. | — |

## Move, resize and remove

| What you want | How | The choices you have | Status |
|---|---|---|---|
| Move a control on the screen | [`move_element`](RECIPE-OPS.md#move_element) | Set any of x, y, width, height on one record inside the object. You can assert the prior values (`expect`) so the edit refuses if the build has drifted. Remember a control is three records (draw, touch region, box) — move them together. | Proven · in v26 |
| Move **where a page attaches** a child object | [`move_attach`](RECIPE-OPS.md#move_attach) | New x and/or y for the attach point. | Proven · in v26 |
| Resize a button or label | [`move_element`](RECIPE-OPS.md#move_element) | Set width and/or height. (Growing a label's *text* past its field is `rename_label` with `allow_grow`, above.) | Proven · in v26 |
| Remove a control from the screen | [`remove_control`](RECIPE-OPS.md#remove_control) | Parks the control off the visible 480 px canvas (its bindings stay). Choose the park-y (default 900). | **Unverified** |
| Delete a control from a card, touch area included (e.g. to move it to another page) | [`remove_records`](RECIPE-OPS.md#remove_records) | Deletes the chosen records from one object, which shrinks it. Choose the records, each with an identity check (its child or touch id). A later control in the same card can move, because it was placed relative to the removed one; check the render and correct it with `move_element`. | Proven · 2026-10-19 |
| Move a control to another page | [`move_control`](RECIPE-OPS.md#move_control) | Give the object holding the control, its records (as for `remove_records`, each with an identity check), the destination object and the absolute top-left (`at`). The op removes the control, puts back anything on the source page the removal moved, pastes it on the destination with its font, align, colours and live-redraw cell, and fails the compile if anything else on either page moved. The touch region keeps its id, so the control runs the same action from its new page. | Unverified (built from coach-proven halves) |
| Check that nothing on a page moved, or that a control sits where you expect | [`assert_layout`](RECIPE-OPS.md#assert_layout) | Put it anywhere in a recipe; the compile fails there if the check fails. Give a page root and either the absolute box (x, y, w, h) of chosen records, or `snapshot` early and `unchanged_since` later, with an `except` list for the records you meant to change. Records are named by their path from the root or by label. | Proven (check-only; changes no bytes) |
| Take one light out of a master switch (so "Light Master" no longer turns it on or off) | [`drop_from_master`](RECIPE-OPS.md#drop_from_master) | Give the master's screen title (e.g. "Light Master") and the G12 channel to drop. It leaves both the ON and OFF buttons, so the master neither turns it on nor off; the light's own button still works. Fails loudly if that channel is not in that master. | Proven · in v26 |

## Colour, font and artwork

| What you want | How | The choices you have | Status |
|---|---|---|---|
| Recolour a button box | [`recolor`](RECIPE-OPS.md#recolor) | Give an `[r, g, b]` triple or a palette `{"index": n}`. The colour must exist in the firmware palette — you cannot invent one. | Proven |
| Change a control's font | [`set_font`](RECIPE-OPS.md#set_font) | Pick the font slot. Note the selector is persistent: later records inherit it. | Proven |
| Change or replace an icon on a control | [`set_icon`](RECIPE-OPS.md#set_icon) | Repoint the icon's font slot and/or its character; optionally blank the caption. An icon is just a character — if no glyph is installed for it, the fallback draws. `current` picks one icon in an object that holds several (the nav rail's arms: v38 turns the rail's slide-out 'S' into the LEVEL icon 'Y'). | Proven · in v26 |
| Install brand-new artwork (a logo, a QR, a badge glyph) | [`install_glyph`](RECIPE-OPS.md#install_glyph) | Supply the pixels as hex rows (4-bit-per-pixel fonts); `fireflymcf glyph-import` makes them from a PNG, JPEG, SVG or other picture and picks a free character. Pick the font slot and the character to point at it. This is how the Starlink logo and the timer mark were added. `claim_block` also claims a character whose glyph is its font's solid placeholder block (icon font 24's unused slots), when no record draws it; v38's spirit-level rail icon uses it. | Proven · in v26 |

## Add a button

| What you want | How | The choices you have | Status |
|---|---|---|---|
| Add a working on/off button on the HOME page, wired to a G12 output | [`add_button`](RECIPE-OPS.md#add_button) | Pick the G12 channel (the output pin), the caption (`\n` for two lines), the off-colour, and optionally the caption font. | Proven · in v26 |
| Add a custom button (clone a donor, put your own artwork on it) | [`add_glyph_button`](RECIPE-OPS.md#add_glyph_button) then [`wire_button`](RECIPE-OPS.md#wire_button) | `add_glyph_button` places a bare shell at x/y sized w×h; `wire_button` then binds it to a G12 channel with an off-colour indicator. Usually paired with [`install_glyph`](RECIPE-OPS.md#install_glyph) for the artwork. This is how the Starlink button was built. | Proven · in v26 |
| Put a small state-gated mark or badge in the corner of an existing button | [`add_corner_mark`](RECIPE-OPS.md#add_corner_mark) | Choose the character and font, the corner offset (dx, dy), and the state cell that gates whether it shows. Pair with [`install_glyph`](RECIPE-OPS.md#install_glyph) if the mark is custom art. | Proven · in v26 |

## How a control responds to touch (gestures)

| What you want | How | The choices you have | Status |
|---|---|---|---|
| Require a hold to switch a control ("gate it behind a 3-second long press") | [`set_gesture`](RECIPE-OPS.md#set_gesture) | `kind: hold_to_toggle`, and the hold time in `seconds` (default 3). Converts a normal tap-toggle into hold-to-toggle so a stray tap can't flip it. | Proven · in v26 |
| Make one control do two things: a short tap acts normally, a long hold opens a screen (e.g. an "off gate" or a settings jump) | [`split_hold_region`](RECIPE-OPS.md#split_hold_region) | Split the touch area into a tap strip and a hold strip; set the hold delay (default 3000 ms) and which screen/object the hold opens. | Proven |

*(For a **time-based** auto-off — "turn the pump off after N minutes" — see Timers below; that is a
countdown, not a touch gesture.)*

## Reveal something the firmware hides ("un-gate")

| What you want | How | The choices you have | Status |
|---|---|---|---|
| Un-gate a control the firmware disables every tick (e.g. 13.6's greyed-out `Shore Brkr Size` minus) | [`fix_variant_gate`](RECIPE-OPS.md#fix_variant_gate) | Mode `selector` changes one config byte so the gate reads "enabled"; mode `ungate` removes the test. Mode `selector` is proven; other modes are unverified. | Proven · in v26 |
| Force a value-match switch to always show one arm (reveal the page/control it would otherwise hide) | [`pin_switch_arm`](RECIPE-OPS.md#pin_switch_arm) | Pick the object, the record, and which arm to pin to — regardless of the value the panel receives. | Proven · in v26 |
| Reveal the nav rail's authored sixth icon and point it at a screen | [`show_nav_slides_icon`](RECIPE-OPS.md#show_nav_slides_icon) | Choose the destination screen (12 = slide/awning controls, 29 = auto-levelling). | Proven · in v26 |
| Remove a factory interlock between two OPTIONS rows (so selecting one no longer forces the other) | [`remove_option_interlocks`](RECIPE-OPS.md#remove_option_interlocks) | Choose which interlocks to break, or all of them. 13.6 only; refuses on 14.7, no-op on 11.23. | Proven · in v26 |

## OPTIONS rows and outputs that follow each other

| What you want | How | The choices you have | Status |
|---|---|---|---|
| Add a new OPTIONS checkbox that gates a behaviour | [`add_option_follower`](RECIPE-OPS.md#add_option_follower) | Set the watched and driven G12 channels, the config selector the checkbox writes, the on-level, and the row's caption and position. | Proven · in v26 |
| Make one G12 output follow another's on/off (gated by an OPTIONS row) | [`follow_channel`](RECIPE-OPS.md#follow_channel) | Set the watch channel, the target channel, the on-level, and which OPTIONS row gates it. This is the light→pump follower. | Proven |
| Drive a channel from any bus register on an edge, gated by register conditions | [`follow_register`](RECIPE-OPS.md#follow_register) | Set the watch register (by DGN+instance or a handle), the target channel, on-level, and any gate registers. The routine is shared with the proven follower, but no specific instance has been flashed — a coach check is warranted. | **Unverified** |

## Live numbers and indicators (readouts)

| What you want | How | The choices you have | Status |
|---|---|---|---|
| Show any bus value as a live number on any page | [`add_register_readout`](RECIPE-OPS.md#add_register_readout) | Choose the register (DGN + instance/selector), scaling (mask, add, shift, byte width), the page and x/y, and the number format. It can also **feed an existing readout** past a stock decode fence (`cell_slot` + `silence`). | Proven · in v26 |
| Add a live, recolouring status arm to the compact AES electrical frame (build-specific) | [`add_aes_low_volts_trigger`](RECIPE-OPS.md#add_aes_low_volts_trigger) | Set the checkbox anchor x/y. Depends on the compact AES page (see below). | Proven · in v26 |
| Make the panel receive a DGN it has never listened for, without giving up an existing subscription | [`append_filter`](RECIPE-OPS.md#append_filter) | Give the DGN, the extraction mode and the payload byte. It adds a new receive entry (up to the firmware's 83-distinct-DGN registry cap). Show it with `add_register_readout` (`register: {ref: <this op>}`). | Unverified (probe pending) |
| Add a LEVEL page fed by the level sensor (build-specific) | [`add_level_page`](RECIPE-OPS.md#add_level_page) | `preview` (static example state, no receive side) or live. Live adds 14 record-gated subscriptions on `dgn` (default `0x0FFB0`; v37 uses RV-C's custom `0x1EFBF`), one per-tick routine and 20 display cells, and redraws screen 29 (the SLIDES/AWNINGS page the nav rail's slide icon reaches) as a top-down van with a tile per wheel, pitch/roll, a LEVEL banner and blocks needed/carried. `orientation` `rear` (the default) draws the van rotated 180 degrees, front at the bottom; `front` is the original drawing v34b pins. `controls` adds a Blocks-carried stepper (- / + send `18EFFF9F#4C5604…`/`#4C5603…` once per press) and a Zero Level button (a 3-s hold sends `19FDE89F` and `19FDEA9F`), on three fresh groups and four new bindings; `repaint` wraps each live value in a switch that repaints its whole box when the value changes, and `popup_background` gives the popup its own background (v43); `geometry` adds Wheelbase and Track rows to the popup, -/+ 0.5 in on level-sensor ops 0Bh-0Eh, repeating every 250 ms once held for 1 s, read from record 04h bytes 1-4 (v45); `setup` moves Zero Level and the carried stepper into a LEVEL SETUP popup on screen 12 with block height and tolerance steppers and AUTO, reached from a Setup button (v42); `arrow_last` draws each tile's arrow after its readouts, whose backgrounds otherwise paint over it (v41; `fill_safe`, v38's narrower fields, did not fix it and is superseded); `stepper_dgn` `0x1EFBF` sends the - / + as RV-C custom `19EFBF9F`, addressed to the sensor, instead (v37); allowed with `preview` too, to test the transmit side alone. Needs the arrow glyph from `install_glyph` first; `levelpage.recipe_ops` emits the title rename, the glyph and this op together. | Unverified |
| Name an existing bus value so later ops can reference it | [`resolve_register`](RECIPE-OPS.md#resolve_register) | Give the DGN (number or name) and optional instance/selector. This changes no bytes — it is a handle for other ops. | Proven (no byte change) |
| Add a second battery gauge beside an existing one, live and recolouring (build-specific) | [`add_voltage_battery`](RECIPE-OPS.md#add_voltage_battery) | Clones a donor battery graphic beside a chosen one, drives its value from a bus voltage register, and gives it its own colour threshold (green at or above, red below) and its own fill level window (empty→full over a voltage range) — independent of the battery it's cloned from. Ships in this coach's build as the coach 48V pack beside the chassis 12V battery on HOME: sourced from `DC_SOURCE_STATUS_1` instance 1, red below 50.4V / green at or above, fill empty at 48.0V to full at 54.8V, labelled "House". | Proven · in v26 |

## Timers

| What you want | How | The choices you have | Status |
|---|---|---|---|
| Water Pump Auto Off — a minutes stepper, a live countdown readout, and an automatic OFF | [`add_pump_auto_off`](RECIPE-OPS.md#add_pump_auto_off) | Set the default minutes (30). With `allow_disable`, 0 becomes a legal setting that means "off / no timer". | Proven · in v26 |

## Repoint a control to a different output

| What you want | How | The choices you have | Status |
|---|---|---|---|
| Make a labelled control command a **different** G12 output channel | [`remap_channel`](RECIPE-OPS.md#remap_channel) | Give the control label and the destination channel. `move_display_list` (coach-proven) moves the status variable too; the plain indicator retarget is **unverified**. | Proven with `move_display_list` |
| Narrow a receive filter so a readout listens to just **one** RV-C instance | [`pin_filter_instance`](RECIPE-OPS.md#pin_filter_instance) | Give the variable-table entry and the instance to match. Useful when two publishers collide and the last frame wins. | Proven · in v26 |

## Whole-page reworks (build-specific)

These reshape a specific 13.6 page; they are here so you know they exist, but they are tailored to
the reference coach's layout.

| What you want | How | The choices you have | Status |
|---|---|---|---|
| Replace the AES ELECTRICAL page with a compact frame and a settings sub-page | [`swap_aes_electrical`](RECIPE-OPS.md#swap_aes_electrical) | Optionally carry a Water Pump section ([`add_pump_auto_off`](RECIPE-OPS.md#add_pump_auto_off)) and a Pump-Link indicator; optionally show "Off" instead of "0 Min". | Proven · in v26 |
| Add a "Batt Heat" button to the compact AES frame that commands the BMS battery heater | [`add_aes_batt_heat`](RECIPE-OPS.md#add_aes_batt_heat) | Any BMS EF70 command: `enable_wire`/`off_wire` (v49 Ignore Alarms), `group_pool: hold_safe` for a second button. Optional `x/y/w/h` and caption `text` (v48 uses "Battery Heater"); pass the op to `add_aes_batt_heat_status` as `button: {ref}` so the status arms match. Otherwise no choices: press-and-hold sends a 60-minute enable, tap sends off (DGN `0x0EF70` to SA `0x70`, DP=0). Layer after `swap_aes_electrical`. | Proven (2026-09-21) |
| Make the "Batt Heat" button show live BMS status | [`add_aes_batt_heat_status`](RECIPE-OPS.md#add_aes_batt_heat_status) | No choices: the body rests at the standard button blue and turns cyan while the battery heater is on; a red wavy-lines glyph shows while the battery's internal heater **contactor** is closed (status DGN `0x18FF7070`). Adds its own two subscriptions ([`append_filter`](RECIPE-OPS.md#append_filter)) and one display cell that both bits are folded into, so no existing subscription or indicator is disturbed. Layer after `add_aes_batt_heat`. | Flashed v32 (2026-09-22); coach-verified through every state transition |
| Show the battery heater contactor on the House battery icons | [`add_batt_heat_battery_glyph`](RECIPE-OPS.md#add_batt_heat_battery_glyph) | Name the batteries (`443` is the ELECTRICAL House battery; a ref to an `add_voltage_battery` op takes its HOME clone). While the contactor is closed, a white wavy-lines glyph sits in each battery's bottom-right corner; the voltage number is raised 11 px in every state to make room. Or `style: "outline"`: the terminals and a 2 px ring round the body turn `outline_rgb` (orange by default) while the contactor is closed, and the number stays put. Layer after `add_aes_batt_heat_status`. | Glyph: flashed in v34a 2026-09-26, verified. Outline: flashed in v47, verified 2026-10-10 |
| Add a hold-to-reboot button for the APS-500 alternator charger on the ALTERNATOR SYSTEM page | [`add_alt_reboot`](RECIPE-OPS.md#add_alt_reboot) | Reboot alone (default) or, with `clear_faults`, reboot and clear faults (rvc2mqtt's reset frame). The button sits beside CLEAR EEPROM on this page only; a 3-second hold sends RV-C `GENERAL_RESET` (`0x17F80`) once, a tap sends nothing. 13.6 only. | Coach-verified on v46 (2026-10-05) |
| Show captioned live readouts in an ELECTRICAL card (v49: AC In V, In/Out Amps, Batt Amps in the Inverter card) | [`add_card_readouts`](RECIPE-OPS.md#add_card_readouts) | Rows of caption, value (a `draw: false` [`add_register_readout`](RECIPE-OPS.md#add_register_readout) ref, or an existing display slot) and unit; position and pitch. Run after `aes_card_layout`. | Built · v49 (2026-10-15), not flashed |
| Light a second BMS button (e.g. Ignore Alarms) from one bit of the BMS status | [`add_aes_bms_button_status`](RECIPE-OPS.md#add_aes_bms_button_status) | The button op, the heater status op, and the bit mask inside its byte-2 field (2 = alarm mask active). | Built · v49 (2026-10-15), not flashed |
| Lay the AES ELECTRICAL page out as four cards (Batteries, Inverter, Auto Engine Start, Water Pump) | [`aes_card_layout`](RECIPE-OPS.md#aes_card_layout) | No choices: HOME's card colour and radius, controls at their stock sizes, the AES gear beside Low Volts, "Triggers:" dropped. Build the heater with `add_aes_batt_heat` `x 84, y 156, w 328, h 54` (any `text`) and `add_aes_batt_heat_status` `button: {ref}` first; refuses any other shape. | Built · v48 (2026-10-15), not flashed |
| Send the Inverter capsule's gear icon to the INVERTER / CHARGER SETTINGS screen | [`retarget_inverter_gear`](RECIPE-OPS.md#retarget_inverter_gear) | Choose the destination screen id (default 14). | Proven · in v26 |
| Put a Low Volts button (with a "Triggers:" title) on another AES page | [`add_low_volts_control`](RECIPE-OPS.md#add_low_volts_control) | Choose the page object and the title and button position. It recolours and toggles only alongside [`add_aes_low_volts_trigger`](RECIPE-OPS.md#add_aes_low_volts_trigger). | Proven · 2026-10-19 (AES settings page) |

## Tank sender / G12 (advanced)

| What you want | How | The choices you have | Status |
|---|---|---|---|
| Stop the G12 broadcasting a tank's level, so your own RV-C sender owns that instance | [`suppress_tank_status`](RECIPE-OPS.md#suppress_tank_status) | List the tank instances to silence. This edits the G12 rule program, which the bootloader flashes on its own. See `docs/TANK-PATCHER.md`. | Proven |

---

## What is *not* on this list

A few limits bind every build (full detail in `CLAUDE.md` and `docs/RECIPE-OPS.md`):

- **You cannot add a readout the firmware has no code to paint.** A received value only shows if a
  GuiCode routine copies it to a screen cell; [`add_register_readout`](RECIPE-OPS.md#add_register_readout)
  authors that routine, but a bare filter entry alone paints nothing.
- **The panel can subscribe to at most 83 DGNs** (13.6 uses 66), and the cap fails silently.
- **A same-size rename cannot add a character**; use `allow_grow`.
- **GuiCode and GuiData flash as one unit** — always build from stock in one recipe that re-applies
  every feature, and say what a build drops when you hand it over.
