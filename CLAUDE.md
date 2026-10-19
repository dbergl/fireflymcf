# CLAUDE.md

Reading, rendering and safely modifying the **Firefly Integrations MCF** container: the firmware and
configuration bundle (`mcf.bin`) for the RV multiplex system in Jayco coaches (Firefly G12 output
module, Spyder 7" touch panel). The reference coach is a Jayco B-Van on firmware 13.6, floorplan WD.

The project has two purposes: **describe** the format, and **modify** it safely. The library
(`src/fireflymcf/`, imported as `fireflymcf`) reads any 11.x/13.x/14.x MCF, renders its screens from
the bytes, and compiles an ordered list of typed edits (a "recipe") into a flashable image with every
structural gate this project has. `plugin/` is the Claude Code plugin built on it.

**This repository holds no firmware.** The corpus (`firefly-*/mcf.bin`, `cli.bin`), coach captures
(and `tests/answer_key.py`, their transcription),
the reference coach's recipes and builds, and the Ghidra projects live in a private research
repository (`firefly-firmware`). `scripts/link-research.sh` symlinks them in under gitignored names
so the full suite runs; without them, the tests that need them are not collected. The docs cite
those paths (`firefly-13.6/mcf.bin`, `captures/…`, `builds/…`) as they stand there.

## Start here

| If you are… | read |
|---|---|
| an agent asked to build a custom firmware from an owner's description | **`docs/AGENT-GUIDE.md`**, then `docs/RECIPE-OPS.md` |
| a coach owner | `docs/GETTING-STARTED.md`, then `docs/COOKBOOK.md` |
| fitting your own RV-C tank sender | `docs/TANK-PATCHER.md` |
| wondering what a build *can* change | `docs/CHANGE-CATALOG.md` (intent-first), then `docs/RECIPE-OPS.md` (the op reference) |
| studying the format | `docs/MCF-FORMAT.md` |

## Document map

| File | What it is |
|---|---|
| `README.md` | entry point |
| `docs/AGENT-GUIDE.md` | the build procedure for an agent: inspect the stock, map requests to ops, compile, render, hand over |
| `docs/RECIPE-OPS.md` | **generated** reference of every recipe op: parameters, error codes, hardware status. `python3 -m fireflymcf.ops_doc` regenerates; a test pins it |
| `docs/GETTING-STARTED.md` | owner's on-ramp: obtain stock, build, flash, recover |
| `docs/CHANGE-CATALOG.md` | owner-facing catalogue of every change, grouped by intent; hand-written, a test asserts it names every registered op |
| `docs/COOKBOOK.md` | exact command lines for the common edits |
| `docs/TESTING.md` | test commands, parallel runs, hazards |
| `docs/MCF-FORMAT.md` | **the specification.** Every field carries a confidence label |
| `docs/BUS-BASELINE.md` | PC.1 onward: everything observed on the physical coach. **Hardware evidence; it wins over the file** |
| `docs/HARDWARE-VERIFIED.md` | how each capability was proven on the coach; the PD-inverter bridge brief |
| `docs/GUICODE.md` | the GuiCode disassembly (native Thumb-2). Dated notebook, 70+ numbered findings; later findings correct earlier ones in place |
| `docs/LCD-FIRMWARE.md` | the STM32F7 panel firmware: container validation, GuiData header reads, the receive filter |
| `docs/G12-CODE.md` | the G12 rule program (table `0x67`, Thumb-1, Cortex-M4): the tick, the config defaults, the interlocks, the per-rule broadcast switch |
| `docs/CONTROL-BEHAVIOR.md` | what a control does when touched: gesture phases, hold thresholds, the five control types, the hit-region split |
| `docs/OBJECT-HIERARCHY.md` | how to place a button: object, record, child model; the live-redraw invariant; donor selection |
| `docs/RENDER.md` | the decoded renderer and its residuals |
| `docs/SECTION-INTERIOR-SURVEY.md` | the disassembly frontier: per-section function coverage (documented vs dark) and the largest undecoded functions with their call context |
| `docs/SETTLED-FINDINGS.md` | option interlocks, the tap/hold split, pin behaviour, derived slots, the cross-coach audit |
| `docs/CLOSED-THREADS.md` | closed threads, closed defects, closed owner work items. Read before building a mechanism on a symptom |
| `docs/LEVELING-PAGE.md` | screen 29, both directions, and why it stays dark |
| `docs/DGN-MAP.md` | what the panel receives and what it and the G12 send |
| `docs/STATUS-INDICATOR-MAP.md`, `docs/G12-OUTPUT-CONFIG.md` | generated. Regenerate, never hand-edit |
| `docs/RVC-GAPS.md` | report for the maintainer of `rvc2mqtt` |
| `docs/TANK-PATCHER.md` | `fireflymcf-tankpatch`, the standalone tank patcher, written for a stranger |
| `docs/LYRA-USB-CAN-GATEWAY.md` | the panel's USB port: a CAN mirror whose trailer is solved, plus a host-command parser read from code (untested) |
| `docs/PARSER-LOCATION.md` | where `cli.bin` parses the container, with instruction addresses |
| `studio/README.md` | Firefly Studio, the browser editor |
| `plugin/` | the Claude Code plugin: marketplace entry, skills |
| `ROADMAP.md` | the split from the research repo, and what comes next |

**Every module in `src/fireflymcf/` is real, tested tooling**, whether or not a doc names it.
Before deriving a format or a scan, `ls src/fireflymcf/` and grep `tests/`. A decoder was once
re-derived from scratch because `images.py` was not in a table.

## Commands

```
python3 -m venv .venv && .venv/bin/pip install -e '.[full,test]'
scripts/link-research.sh [RESEARCH_REPO]           # link the research data (default ../firefly-firmware)
python3 -m pytest tests/ -q -n auto                # the suite (see docs/TESTING.md); first run fills the compile cache
(cd ../firefly-firmware && .venv/bin/python -m pytest tests -q -n auto)   # after an engine change: the reference builds, byte for byte
fireflymcf doctor MCF [--bus]                      # what a build is and which edits are safe on it
fireflymcf bus-map MCF [--dgn X] [--json]          # every message received and sent, and what the panel calls it
fireflymcf validate MCF-PROJECT.ffproj.json        # pre-flash PASS/FAIL gate (writes nothing)
fireflymcf pages MCF                               # every screen with its --root index
fireflymcf labels MCF --containing TEXT            # label records with their object index
fireflymcf render MCF out.png --root N --config CFG.json --placements hits.json
fireflymcf compile P.ffproj.json STOCK -o build/mcf-NAME.bin
fireflymcf-tankpatch MCF                           # tank-broadcast report; see docs/TANK-PATCHER.md
fireflymcf-g12flash probe --interface can1         # read-only RV-C enumeration; transmits nothing
python3 -m fireflymcf.ops_doc                      # regenerate docs/RECIPE-OPS.md
python3 -m studio --stock MCF --no-browser         # Firefly Studio on 127.0.0.1:8676 (needs '.[studio]', a web build)
npm test --prefix studio/web                       # Studio web unit tests
claude plugin validate .                           # the marketplace and plugin manifests
```

Ghidra, `builds/from_stock.py` and the tests that pin the reference coach's builds byte for byte
are in the research repository: run them after any change to the engine.

## Evidence rules

**Two sources of truth, not equal.** The binary corpus, and live CAN captures from the coach
(`docs/BUS-BASELINE.md`, transcribed into `tests/answer_key.py`). **The capture wins.** The file has
twice been read as contradicting it and was wrong both times.

- **Never modify `tests/answer_key.py`'s data, the `.bin` corpus, the PDF, or `captures/`.**
- **Every claim cites bytes**: an address, a function, an instruction. Label inference as inference.
  `proven-from-code` requires a cited instruction in either image. The record decoder
  (`FUN_00252CB0`), layout engine (`FUN_00257BFC`), renderer (`FUN_00256C54`) and the LCD's header,
  directory and variable-table reads (`FUN_0021BE50`/`FUN_0021C760`) are located, so GuiData
  structure can be proven, and the group table is (LCD `FUN_0021B420`). Still `inferred-from-data`:
  what a binding's payload bytes mean per DGN (its DGN, `+2`/`+3` and payload order are proven,
  `GUICODE.md` Finding 83), the floorplan table,
  OPTIONS-row semantics, and undecoded fields.
- **Answer from the code first; use the coach to validate.** Derive the mechanism, state its
  prediction, then spend coach time confirming it. Check `docs/BUS-BASELINE.md` before asking for
  coach time; it may already answer.
- **A defensible negative beats a fitted answer.** When a negative falls, it falls to a mechanism,
  never to a fit. Every stored-table search that came back negative here was correct; the answers
  were in code paths (`GUICODE.md` Findings 42, 61).
- **A register with one writer is not understood until its reader is named**, and not the whole
  story when the value round-trips through another node. The write side of an option is the frame
  the panel sends; the register updates from the G12's echo.
- **Verifying only what you set is not verification.** Diff the whole render, and diff the whole
  image against stock, classifying every differing run by region.
- **A tool scoped to the last question answers the next one wrongly.** Read all four rule tables,
  not one.
- **An unset switch is not a safe default.** The nav rail's unset state draws an icon for hardware
  the coach lacks.
- **An argument from absence needs a guarantee the thing would have been found.** Index-based reads
  (`DAT_00253D04[slot]`) and Thumb-1 immediates beyond `#imm5` defeat literal scans.
- **Check your tests can fail.** Stub the function to return empty and confirm the test breaks.
- **A system with one input describes the pair, not the consumer.** The panel does not quantise
  tank level; its only publisher did (PC.62).

## Traps

- `mem.getBytes(addr, bytearray)` **returns zeros** on this Ghidra build. Read images from disk or use
  `mem.getInt()`. Ghidra's raw loader maps the whole file at the base; strip headers first (`cli.bin`
  has a `0x2C` header, `cli regions`).
- **Heavy readers are memoised on content** (`memo.py`): keyed on `bytes(data)` plus arguments,
  never on `id()`. A cached function that reads a module constant must take it as an argument, or a
  test that monkeypatches the constant poisons every later test in that worker. Add caches through
  `memo`, not a fresh `lru_cache`.
- **Mutation-testing needs `__pycache__` cleared between runs** (mtime plus size invalidation).
- **A section's last 4 bytes are its checksum and `guidata_extent` includes them.** Append at
  `ge - gs - 4`, pad to a multiple of 4, and read glyphs back pixel for pixel: the structural gates
  check structure, not pixels.
- **GuiCode and GuiData flash as one inseparable unit.** Every build writes both, so a build derived
  from stock silently replaces a customised GuiData. Derive a new build from the recipe of the image
  that is running. Flashable files are named `mcf*.bin`; name builds `mcf-<name>.bin`.
- **The variable-table slot base is index-split**: var index ≤ 255 at `0x20007FC8 + idx*2`, > 255 at
  `0x20008000 + idx*2` (`followchannel.status_slot_addr`). The flat formula caused the PC.44 failure.
- **Build on the target pin, never build then retarget**: `retarget_command` allocates a fresh group
  each call and the abandoned group stays live in the table.
- **`records.object_records` takes `gs + Screen.table[i]`, not an object index.** An index yields
  plausible-looking derailed decodes.
- **Pass object indices, never screen ids**: `state+0x20A` is a table lookup
  (`layout.screen_object_map`).
- **A visible wtype-11/13 label must declare a box**; the panel clips text to it inclusively, so a
  zero-width box draws one column. A wtype-0 single character is not clipped (`GUICODE.md`
  Findings 69, 70).
- **Switch arms must be flow-neutral** and the `0xFFFF` default arm at the base (`cornermark.py`).
- **L3 asserts containment, not equality**; the corpus write guard matches `firefly-<digit>`.
- `cli._resolve_page_root` finds a page by its exact header; a renamed page needs `--root N`.
- **`render.py`'s module docstring is the authority on fill policy.** A fill with `color_src`
  `heuristic` is not painted; the decoded parent shows through. Never invent a colour. The
  ALTERNATOR-SYSTEM photo oracle is relative layout and colour only; do not fit absolute pixels to it.
- **Photo oracles** live in `build/13.6-images/` (gitignored); the default render configuration for
  the reference coach is `build/coach-wd.json`.
- **Scanning either image:** search `movw`/`movt` pairs and modified immediates as well as literal
  pools; only `ldr` hits are literal loads for `0x9xxxxxxx`/`0xCxxxxxxx` (branch encodings collide).
- Ghidra 12.1.3 dropped Jython: run `.py` scripts through `ghidra/headless`, never
  `support/analyzeHeadless`.

## What can be built

`docs/RECIPE-OPS.md` lists every op with its hardware status and the proof behind it.
`docs/HARDWARE-VERIFIED.md` holds how each was proven. In one table:

| capability | op or tool | status |
|---|---|---|
| rename, move, restyle, recolour, re-font, re-icon a control; rename the build | `rename_label`, `move_element`, `move_attach`, `set_font`, `set_icon`, `recolor`; `patch.py` | flashed |
| grow a label past its field | `rename_label` with `allow_grow` (`relocate.py`) | PC.18 |
| repoint a control's G12 channel | `remap_channel` | PC.13-16 with `move_display_list`; the default indicator retarget is **unverified** |
| add a working button on a page | `add_button`; `add_glyph_button` + `wire_button` | flashed 2026-08-17 |
| tap / hold / ramp / momentary gestures; hold-to-toggle; tap and hold on one control | `set_gesture`, `split_hold_region` | PC.22 |
| install artwork as a glyph | `install_glyph` | flashed |
| narrow a receive filter to one instance | `pin_filter_instance` | v17 |
| subscribe the panel to a new DGN (4 bytes); transmit an unknown DGN (1 byte) | `statusbind.retarget_filter`, `newbutton.set_binding_dgn` | 2026-08-22/23 |
| new OPTIONS row gating a channel follower; generic register follower | `add_option_follower`, `follow_channel`, `follow_register` | PC.45 |
| show any register as a live number; feed an existing readout past a decode fence | `add_register_readout` | v17, v18 |
| compact AES Electrical page; live Low Volts arm; inverter gear retarget | `swap_aes_electrical`, `add_aes_low_volts_trigger`, `retarget_inverter_gear` | v10-v12 |
| Water Pump Auto Off with corner mark | `add_pump_auto_off`, `add_corner_mark`, `install_glyph` | v17, v21 |
| pin a type-6 switch to one arm; repair a per-tick disabled control; sixth nav icon | `pin_switch_arm`, `fix_variant_gate`, `show_nav_slides_icon` | v18, v22, v17 |
| stop the G12 claiming a tank instance | `suppress_tank_status`; `fireflymcf-tankpatch` | v22, PC.62 |
| remove the panel-side option interlock | `remove_option_interlocks` | reference build v7, PC.21 |
| remove a control (park off-canvas) | `remove_control` | **unverified** |
| remove one channel from a master ON/OFF control | `drop_from_master` | v26 |
| subscribe the panel to a NEW DGN without giving up an existing one | `append_filter` | v30 |
| BMS battery-heater button on the compact AES frame, with live heater/contactor status | `add_aes_batt_heat`, `add_aes_batt_heat_status` | v27, v32 |
| white heater-contactor glyph on the House battery icons (HOME, ELECTRICAL) | `add_batt_heat_battery_glyph` | v33 recipe, flashed in v34a 2026-09-26 |
| orange House-battery outline while the heater contactor is closed (replaces the glyph) | `add_batt_heat_battery_glyph` `style: "outline"` | v47, verified (outline seen 2026-10-10) |
| LEVEL page on screen 29 fed by the level sensor (RV-C custom DGN `0x1EFBF` since v37) | `add_level_page` | v35 flashed 2026-09-27: live page, rear orientation and Blocks-carried stepper and NO SENSOR verified; v37 flashed 2026-09-28 on `0x1EFBF`; Zero Level **unverified** |
| number readouts whose background fill cannot wipe a neighbour, in any measuring class | `readoutfill`, `metrics.worst_fill_width`; `add_level_page` `fill_safe` | v38 built |
| hold-to-reboot the APS-500 from the ALTERNATOR SYSTEM page (RV-C `GENERAL_RESET`) | `add_alt_reboot` | v46 flashed 2026-10-05, verified |
| claim an icon font's unused block character for new artwork; re-icon one of several icons | `install_glyph` `claim_block`; `set_icon` `current` | v38 built |
| Inverter-card readouts (AC In V, In/Out Amps with the RV-C offset, house-battery Batt Amps in v49, inverter DC current (slot 652) in v50); Ignore Alarms BMS button with bit status | `add_card_readouts`, `add_register_readout` `sub`/`draw`; `add_aes_batt_heat` `enable_wire`/`off_wire`/`group_pool`, `add_aes_bms_button_status` | v49, v50 flashed 2026-10-15 (readouts live); v51 built |
| ELECTRICAL as four cards (Batteries, Inverter, Auto Engine Start, Water Pump); "Battery Heater" caption; AES DISABLED half in navy | `aes_card_layout`; `add_aes_batt_heat` `text` + status `button` ref | v48 flashed 2026-10-15 (draws; three defects, fixed for v49) |

**Constraints that bind:**

- **A hold only works on a group GuiCode does not drive.** `newbutton.free_group_slots` lists
  13.6's 130-139, which GuiCode sets as a block; v45's holds on 136/137 were dead. Mint hold
  controls from `newbutton.hold_safe_groups`; a compile gate refuses reusing a GuiCode-driven group.
- The DGN registration cap is **83** and fails silently; 13.6 registers 66. `retarget_filter` refuses
  to cross it.
- A received value is painted only if fixed GuiCode copies its slot to a cell a widget draws: on 13.6,
  128 of 298 receive entries reach one (`fireflymcf bus-map`, GUICODE Finding 82; the "46" were only the
  simple colour/bool blocks). A new DGN can re-source an indicator; only an authored routine
  (`add_register_readout`) can add a readout.
- A same-size rename cannot add a character. An icon is a character; a missing glyph draws the
  fallback.
- **A control is three records**: draw, touch region, box. Move them together.
- One command-5 binding, not two. The panel only sends toggle; the G12 holds state. The G12 acts on
  any instance regardless of the pin sheet.
- The live-redraw invariant: `fillA` index, box `value_slot` and caption `value_slot` are one cell; a
  donor must carry `value_slot` on both.
- Repointing a control is two edits in two sections (binding byte and the GuiCode `ldrsh.w`).
- `set_binding_dgn` and `set_channel` cost the pin its switching; both gate on
  `allow_capability_change`.
- A new glyph needs `y = (cell - height) / 2` unless its record uses v-align 1.
- The receive filter is blind to the sender; with two publishers the last frame wins (PC.46).
- Seven readouts are fenced by magnitude tests (`decodeguards.py`); `Absorb`/`Float` are fenced to
  12-18 V. Check before concluding an injection did not land.
- The C runtime initialises SRAM only to `0x2000AD98` (`raminit.py`); every GuiCode widget slot lives
  above it and holds a value only if something writes it. 210 of 13.6's 664 slots are written by
  nothing (`slotsource.py`, `indirectwrites.py`, `GUICODE.md` Finding 67).
- GuiCode-growing ops chain on the one per-tick hook at GuiCode+`0xEE3C`
  (`followchannel.hook_state`); the chain runs before `FUN_00247BF8`, so its config-byte stores must
  be silenced when a routine feeds a stock cell.
- Option interlocks are enforced by the panel on selection only, and differ per build: none on
  11.23, mode ↔ Progressive Inverter on 13.6, mode ↔ Lithionics on 14.7. `uncouple` refuses 14.7.
- **Most class-2 touch actions run on release.** GuiCode's dispatcher handles 22 ids on press;
  its `0xFF` (finger lifted) handler dispatches 108 more (13.6) on the latched previous id: the
  OPTIONS rows, the nav rail, the floorplan picks (`sendmap.touch_dispatch`, GUICODE Finding 83).
  `fireflymcf bus-map` lists every message the panel sends and what sends it.
- Tools pinned to 13.6 addresses assert the expected bytes and refuse on another build.

## Working here

**Build hygiene (owner, 2026-08-19): always build from stock, in one recipe that applies every
feature explicitly, and assert every feature is present.** A build carries whatever its base carried,
and `build/` is gitignored. Say what a build drops when handing it over. Give every build a distinct
header name so the boot menu distinguishes it.

**Never commit firmware**, or anything derived from a Firefly image as a file (`*.bin` is ignored).
The research data reaches this checkout only through `scripts/link-research.sh`'s symlinks.

The tank-heater name fix (`tank_heater_names`) trades SY's Grey and WT's Black rows for WD's four;
it ships in every build of the reference coach. Do not hand such images to another floorplan's owner
without saying so.

The reference coach's running build and its history are in the research
repository's `CLAUDE.md`.

Work on a feature branch; `main` is the mainline.
