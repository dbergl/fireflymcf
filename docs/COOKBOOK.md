# Cookbook — command lines for common changes

Read `GETTING-STARTED.md` first. Throughout, `mcf.bin` is **your** stock file and the output is a new
file; the tools refuse to overwrite the input, refuse to write into the read-only corpus, and append
`" (Custom)"` to the build name unless you pass `--name`. Once installed, `fireflymcf` is the same as
`python3 -m fireflymcf.cli`.

## Look before you edit

```bash
fireflymcf doctor mcf.bin                        # build, sections, screens, options, interlocks, safe edits
fireflymcf pages mcf.bin                         # every screen: --root index, --page name, header
fireflymcf labels mcf.bin --containing "Pump"    # matching label records: offset, length, OBJECT index, text, screen
fireflymcf options mcf.bin                       # OPTIONS rows, floorplan codes, selectors
fireflymcf image-export mcf.bin                  # built-in images and sizes
fireflymcf patch mcf.bin /dev/null --list-colors # the fill palette: the only legal recolour values
fireflymcf pins mcf.bin                          # which G12 pins can drive an indicator, at what cost
fireflymcf gestures mcf.bin                      # how each control responds to tap / hold / release
fireflymcf render mcf.bin out.png --root 4 --placements hits.json   # draw a page and write its hit map
```

Object and image indices differ between builds. Take them from your own file.

## Rename a label (same-size)

```bash
fireflymcf patch mcf.bin mcf-renamed.bin --rename "Bath Fan=Vent Fan"
```

- Every record containing the text is renamed and listed. Limit with `--on-screen "TITLE"` or
  `--site HEX` (the text offset from `labels`; repeatable).
- The new text must fit the field: `"Fan"` → `"Blower"` fits an 8-character field,
  `"AC"` → `"Air Conditioner"` does not. A longer label needs the relocating path: the
  `rename_label` op with `allow_grow: true` in a project (hardware-proven, PC.18).
- An icon is a character in an icon font, so a rename can change a glyph, but only to a character that
  font contains.

## Move a text or icon element (same-size)

```bash
fireflymcf patch mcf.bin mcf-moved.bin --move "Trigger:=250,120"
```

- If the text matches more than one draw element the command stops and lists them; pick one with
  `--site HEX`.
- A control is three records: the drawn text, the touch region, the box. Moving the text alone leaves
  the other two. To move a whole control use `move_attach` in a project or Studio.

## Recolour a button box (same-size)

```bash
fireflymcf patch mcf.bin mcf-recolored.bin --recolor 830=idx15       # by palette index
fireflymcf patch mcf.bin mcf-recolored.bin --recolor 830=65,81,163   # by colour; must equal a palette entry
```

`830` is the object index from the hit map. Only palette colours are possible; the palette is baked into
GuiCode.

## Swap a logo or QR image (in place)

```bash
fireflymcf image-export mcf.bin --index 4 --out logo.ppm
# edit logo.ppm, same pixel size, save as binary P6 PPM
fireflymcf swap-image mcf.bin logo.ppm --index 4 --out mcf-logo.bin
```

The new image must compress into the original's byte span; the command refuses otherwise. Dimensions
may change if the encoding still fits. `images.py` decodes the images and `imageenc.py` encodes
them. Passes every gate; not yet flashed to a coach.

## Repoint a control to another G12 output (same-size)

```bash
fireflymcf patch mcf.bin mcf-remap.bin --remap "Bath Fan=38"
```

- Reports whether source and destination are dimmable or latch-only. A remap across that boundary
  costs the pin its switching behaviour and is refused without `--allow-capability-change`.
- It moves the command and points the button's own indicator at the new pin's status cells. No
  status variable moves, so every G12 OUTPUTS row keeps reporting its own channel. **The indicator
  half is unverified on a coach.** The `remap_channel` op with `move_display_list` takes the
  coach-proven path instead (PC.14): the status variable moves, and the old channel's diagnostics
  row reports the new channel from then on.
- Refused when the control's indicator does not show its own pin in stock. On 13.6 that is `C`,
  two of the `E` controls and the Water Pump. It is also refused when the destination has no status
  bridge (pin 42), and on any build but 13.6.

## Compile a project (any change, including structural ones)

A project file is an ordered list of typed ops; `docs/RECIPE-OPS.md` lists them all.

```json
{
  "schema": 1,
  "base_sha256": "<sha256sum mcf.bin>",
  "build_name": "Jayco B-Van V13 mine v1",
  "ops": [
    {"id": "vent", "type": "rename_label",
     "params": {"object": 247, "old": "Bath Fan", "new": "Vent Fan"}},
    {"id": "left", "type": "add_button",
     "params": {"channel": 28, "label": "Overflow\nValve", "off_colour": [65, 81, 163],
                "font": 13, "allow_commanded_channel": true}}
  ],
  "preview": {}
}
```

```bash
fireflymcf compile mine.ffproj.json mcf.bin -o build/mcf-mine-v1.bin
```

Refuses a stock whose hash is not `base_sha256`, refuses a name not matching `mcf-*.bin`, refuses to
overwrite, and writes nothing if any op fails. Refuses a project with an op whose mechanism has
never run on a coach unless you pass `--allow-unverified` (you are then the first to run it; flash
with stock on the stick). Prints the manifest (one line per op). Writes
`build/mcf-mine-v1.bin.json` with the hash, manifest and unverified list.

Check the whole build against stock before flashing:

```bash
fireflymcf verify build/mcf-mine-v1.bin mcf.bin --config coach.json
```

## Preview before you flash

```bash
fireflymcf render build/mcf-mine-v1.bin home.png --root 4 --config coach.json
```

- `--page home|lights|electrical|climate|settings|options|outputs|floorplan` resolves by header text
  and does not exist on every build; `--root N` from `pages` always works.
- `--config FILE` is a `CoachConfig` JSON (`{"floorplan": "WD", "options": [...], "extra_slots": {}}`);
  `--slot SLOT=VALUE` overrides one value slot.
- A render is a layout check. Live values and state-driven colours come over the bus and are not in the
  file.

## Read the verification ladder

Every `patch` and `swap-image` prints five rungs:

```
  L1  coverage: 1067000/1067000 bytes claimed, all 9 checksums verify
  L2  6 edit(s) present and re-readable
  L3  26 byte(s) differ, all inside 6 edit(s) + 1 resealed checksum(s)
  L4  inverse patch restores the original byte for byte
  L5  header, descriptor and payload CRCs all hold
```

If a rung fails the file is not written. Do not use `--no-verify` to get past it.

## Free a tank instance for your own sender (G12 only)

```bash
fireflymcf-tankpatch mcf.bin                                  # report, write nothing
fireflymcf-tankpatch mcf.bin --all-water -o mcf-notank.bin    # fresh, black, grey, grey-2
```

`docs/TANK-PATCHER.md` explains the audit it runs on an unfamiliar build.
