# Agent guide — building a custom firmware from an owner's description

This is the procedure for turning "I want the water pump button to say *Pump* and a new button for the
awning light" into a flashable `mcf-*.bin`, using only this repository and the owner's own firmware
file. It is written for an agent, but a person can follow it.

Read `docs/RECIPE-OPS.md` beside this file. It is the vocabulary; this is the procedure.

## What you need from the owner

1. **Their coach's stock `mcf.bin`**, obtained from Firefly for that specific coach. Never build from
   any other coach's file (the docs' `firefly-*` corpus included) for this coach: the file must match the vehicle,
   and the panel cannot tell. Record its SHA-256; it goes into the project file.
2. The **`cli.bin`** that came with it. It is the loader and must be on the USB stick beside the build.
3. Their **floorplan code** and fitted options, if they know them. They drive which screen content the
   panel draws, so renders must use them. `cli options MCF` lists the floorplan codes and option
   names the build knows; the coach's live values can be decoded from a `candump -L` capture with
   `src/fireflymcf/livecfg.py` if one is available.
4. **The description of the change**, in their words. Restate each request as one line before mapping
   it to an op, and confirm anything that reads two ways.

## Step 1 — inspect the stock

All of these only read.

```bash
fireflymcf doctor mcf.bin                       # build name, sections, screens, options, interlocks
fireflymcf pages mcf.bin                        # every screen: --root index and header text
fireflymcf labels mcf.bin --containing "Pump"   # label records: text offset, object index, screen
fireflymcf options mcf.bin                      # OPTIONS rows, floorplan codes, selectors
fireflymcf pins mcf.bin                         # which G12 pins can drive an indicator, at what cost
fireflymcf gestures mcf.bin                     # how each control group responds to tap / hold
fireflymcf image-export mcf.bin                 # the built-in images and their sizes
fireflymcf patch mcf.bin /dev/null --list-colors   # the only colours a recolour may use
```

Render the pages you will touch, under the owner's configuration, and write the hit map:

```bash
fireflymcf render mcf.bin home.png --root 4 --config coach.json --placements home.json
```

`coach.json` is a `CoachConfig`:

```json
{"floorplan": "WD",
 "options": ["AES Single Output", "Black Tank"],
 "extra_slots": {"6": 1}}
```

The hit map lists every placed record with `obj_index`, `record_offset`, `wtype`, `box`, `label`,
`label_offset`, `font` and `value_slot`. **Object indices are build-specific.** Take them from the
owner's file, never from this repository's examples or another coach's recipe. Where two objects carry
the same caption (switch arms), the hit map names the one the walk placed under that configuration.

## Step 2 — map each request to an op

| The owner wants… | op | find first |
|---|---|---|
| different text on a label | `rename_label` | the object index (hit map or `labels`). Same length or shorter is same-size; longer needs `allow_grow` |
| a control moved or resized | `move_element` (one record), `move_attach` (where the page places the whole control) | the record via the hit map. A control is three records: box, caption, touch region |
| a different font size or face | `set_font` | the record and the target font slot (`/api/reference/fonts` in Studio lists specimens) |
| a different icon | `set_icon` | the icon font slot and character; `glyph-export` shows what a font holds |
| a different button colour | `recolor` | a palette colour from `--list-colors`; no other colour is possible |
| a button to drive a different output | `remap_channel` | the channel number from the pin sheet; crossing dim/latch needs `allow_capability_change` and costs the pin its switching. The default retargets the indicator (unverified); `move_display_list` is the coach-proven path |
| a new button on a page | `add_button` (HOME, boxed, captioned) or `add_glyph_button` + `wire_button` (icon button, any page) | the channel, the off colour, the page root. Build on the target pin |
| a hold instead of a tap, or both | `set_gesture`, `split_hold_region` | the control's group (`gestures`) |
| their own artwork as an icon | `install_glyph` | the font slot and a free character; pixels as hex rows (`glyph-export --as-op` shows the shape) |
| a logo or QR image replaced | not an op: `fireflymcf swap-image` (same-size, in place) | the image index from `image-export` |
| a readout fixed for a 48 V coach, or any bus value shown on screen | `add_register_readout`, with `resolve_register` for the source | the DGN and instance or selector (`docs/DGN-MAP.md`); whether the stock decode is fenced (`decodeguards.py`) |
| one output to follow another | `add_option_follower` (with an OPTIONS checkbox), `follow_channel`, `follow_register` | watch and target channels |
| a tank instance freed for their own sender | `suppress_tank_status`, or `fireflymcf-tankpatch` standalone | the instances (0 fresh, 1 black, 2 grey, 3 LPG, 18 grey 2) |
| a wrong value that appears after something restarts on the bus | `pin_filter_instance` | the variable index and the right instance (`docs/DGN-MAP.md`, PC.46) |
| the AES Electrical page compacted, a live Low Volts arm, the inverter gear retargeted | `swap_aes_electrical`, `add_aes_low_volts_trigger`, `retarget_inverter_gear` | **13.6-specific**; these ops assert stock bytes and refuse elsewhere |
| the Water Pump Auto Off timer | `add_pump_auto_off`, `add_corner_mark`, `install_glyph` | 13.6-specific as shipped |

**What cannot be done, so say so up front:**

- Show a value the panel never receives. Runtime values arrive over RV-C; the file holds widgets, not
  numbers.
- Paint a colour the firmware palette lacks. Fill colours are indices into a palette baked into
  GuiCode's theme init.
- Add a character to a label without relocating. Same-size is the default; `allow_grow` relocates and
  is hardware-proven, but say which you used.
- Change a floorplan's option defaults from the file: they are emitted by GuiCode tap handlers, not
  stored (`GUICODE.md` Finding 42).
- Use an op marked 13.6-specific on another build. It refuses; do not force it.
- `remove_control` has never been flashed. Use it only with the owner's knowledge and mark the build.

## Step 3 — author the project file

```json
{
  "schema": 1,
  "base_sha256": "<sha256 of the owner's stock mcf.bin>",
  "build_name": "Jayco B-Van V13 pump-rename v1",
  "ops": [
    {"id": "pump-caption", "type": "rename_label",
     "params": {"object": 295, "old": "Water\nPump", "new": "Pump"},
     "enabled": true, "note": "owner asked for the short name, 2026-09-10"}
  ],
  "preview": {}
}
```

- `build_name` must be distinct from stock and from the owner's earlier builds: the boot menu lists
  images by this string, not by filename. Keep the vendor prefix so the owner recognises the coach.
- Give each op a readable `id` and put the owner's reason in `note`.
- Ops apply in order. Where an op consumes another's output (a `{"ref": "<id>"}`), the producer comes
  first. Order otherwise does not change the bytes.
- `preview` is render-only state and never build input.

## Step 4 — compile

```bash
fireflymcf compile pump.ffproj.json mcf.bin -o build/mcf-pump-rename-v1.bin
```

The command refuses a stock whose hash is not `base_sha256`, refuses any name not matching
`mcf-*.bin`, refuses to overwrite, and writes nothing when any op fails. A failure prints the op id,
a code from `docs/RECIPE-OPS.md` and, where the engine knows one, an `offer` (for example `grow` or
`shorten` on `LABEL_TOO_LONG`). Success prints the manifest, one line per op. A project with an op
whose mechanism has never run on a coach is refused unless `--allow-unverified` is given: that is
the owner's decision, so ask first, and the build then prints an `UNVERIFIED` line. A sidecar `build/mcf-pump-rename-v1.bin.json`
records the SHA-256, manifest and unverified list.

Every compile reseals all nine checksums and runs the structural gates: the object-table boundary
oracle, record geometry, deep byte coverage, GuiCode→GuiData pointer repair, the LCD-pointer strand
check, and the capability gate (the image must send exactly the channel/command pairs stock sent,
plus or minus what the ops declared). A build that passes is safe on those axes, not on every axis.

## Step 5 — verify before handing over

1. **Render every touched page** from the build under the owner's configuration and compare with the
   stock render. Everything that moved should be something an op moved. A placement shift in an
   unrelated control is a defect, not a side effect.
2. **Read the manifest.** Each line is the op's own check against the finished bytes.
3. **Disclose `unverified`.** If the list is non-empty, the owner is the first person to run that
   mechanism.
4. For a GuiCode-growing or structural op on a build other than 13.6, say that the coach is the first
   to run it and recommend flashing with stock on the stick.

## Step 6 — hand over

Give the owner:

- the file (`mcf-<name>.bin`), its SHA-256, and its build name;
- the list of changes in their words, one line per op, and anything requested that was not done;
- the unverified list, or the statement that it is empty;
- the flash and recovery steps from `docs/GETTING-STARTED.md`: FAT32 stick, directory `SpyderConfig`,
  the build and `cli.bin` inside it, power cycle, pick the build, touch each unit to update; recover by
  picking the stock `mcf.bin` from the same stick.

Remind them that GuiCode and GuiData flash together, so this build replaces any earlier custom build
entirely. A later change is a new op on this project, recompiled from stock, never a patch on the
patched file.

## Build-specific facts to carry

- **13.6 is the only build flashed here.** Reading, rendering, and the same-size ops are
  build-general and pass the verification ladder on 11.23, 13.6 and 14.7. Structural and GuiCode
  ops written against 13.6 addresses check the stock bytes first and refuse on a mismatch.
- **Indices differ per build**: 13.6's HOME root is object 4 and its OPTIONS pages 46/47; 11.23 and
  14.7 differ. Always resolve on the owner's file.
- **Option interlocks differ per build** and are panel-side: none on 11.23, generator mode ↔
  Progressive Inverter on 13.6, mode ↔ Lithionics on 14.7 (`remove_option_interlocks` refuses 14.7).
- The panel's **receive filter is blind to the sender.** Two publishers of one DGN alternate; the
  panel shows whichever frame arrived last (PC.46).
- **Seven readouts are fenced** by value-range tests in GuiCode (`src/fireflymcf/decodeguards.py`).
  `Absorb`/`Float` cannot show a value outside 12-18 V without `add_register_readout`'s `cell_slot`
  path.
