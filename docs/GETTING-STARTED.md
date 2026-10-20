# Getting started — customising your coach's Firefly firmware

For a coach owner who wants to change what the Firefly (Spyder) panel shows and does: rename a label,
move or recolour a control, add a button, swap a logo, fix a readout. This page covers getting your
firmware, building a change, flashing it, and recovering.

> **You can recover.** The USB mechanism that flashes a change also reloads a known-good file, and it
> has recovered a boot-looping panel on the reference coach. Keep your original firmware file and a bad
> build is a re-flash, not a brick.

## What you need

- A Jayco RV with a Firefly Integrations / Spyder multiplex touchscreen.
- **Your coach's own firmware package from Firefly**: `mcf.bin` and `cli.bin`. This is your starting
  point and your recovery image.
- A FAT32 USB stick.
- The tools here: `pip install '.[full]'` from the repository root (`pip install .` covers the simple
  edits), or Firefly Studio (`studio/README.md`).

## The rules

1. **Only flash a file built from your coach's own firmware.** The panel verifies checksums, not
   whether the content matches your hardware. A valid file for another coach loads and can misconfigure
   outputs. No firmware is distributed with these tools: the `firefly-*` files named throughout the
   docs are the author's reverse-engineering corpus, not your firmware.
2. **Keep the original `mcf.bin` untouched, and keep it on the stick.** It is your recovery image and
   the base every build is compiled from.
3. **Prefer same-size edits.** Renames, moves, recolours and filter changes rewrite bytes in place.
   Adding a control relocates data; the tools repair and gate it, but it is the higher-risk class.
4. **Put every change in one project.** GuiCode and GuiData flash together, so each build replaces the
   previous build completely. Add to the project and recompile; never patch a patched file.

## Step 1 — get your stock firmware

There is no way to read the firmware off the panel. Request it from Firefly customer service with your
coach's model, configuration and serial number. Record the hash so you always know which file you are
building from:

```bash
sha256sum mcf.bin
```

## Step 2 — know what a build changes

An MCF holds four sections. The boot menu lists four entries but flashes **three units**:

| unit | flashed |
|---|---|
| LCD Firmware | on its own |
| GuiCode + GuiData | **together, always** |
| G12 | on its own |

A UI build therefore rewrites both GuiCode and GuiData. A build compiled from stock replaces every
earlier UI change, which is why rule 4 exists. The G12 section is independent: a tank-sender patch
(`docs/TANK-PATCHER.md`) touches only it.

Get your bearings first. This reads only:

```bash
fireflymcf doctor mcf.bin
```

It prints the build name, the sections, how many screens, images and options the build has, any
option interlocks, and which edits are safe on it.

## Step 3 — build your change

Two front-ends, one engine:

- **Firefly Studio** (visual): a project is an ordered list of typed changes, compiled to a flashable
  `.bin` with every gate run. `studio/README.md`.
- **The command line**: `fireflymcf patch` for one same-size edit; a project file
  (`*.ffproj.json`) with `fireflymcf compile` for anything larger. `docs/COOKBOOK.md` has the exact
  commands and `docs/RECIPE-OPS.md` every available change.

Either way the build reseals the container's checksums and runs the gates (object-table boundary,
deep byte coverage, GuiCode→GuiData pointers, capability, geometry). A build that fails a gate is not
written. That is the tooling refusing an unsafe file, not a bug.

**Which tools work on which build.** Reading, rendering and the same-size edits (`patch`,
`swap-image`, `rename_label`, `move_element`, `recolor`, `set_font`, `set_icon`, `remap_channel`,
`pin_filter_instance`) locate their target by content and work on 11.23, 13.6 and 14.7. Anything that
names an object **by index** is specific to the file it was authored against: find your indices on
your own file with `pages`, `labels` and `render --placements`. Tools written against 13.6's GuiCode
addresses (the AES page ops, the Auto Off timer, the interlock removal, the tank-heater names) check
the stock bytes first and refuse on another build rather than corrupt it.

### A first change: swap a logo

Replacing a built-in image changes bytes in place and needs no relocation, so it is a good way to prove
the flash-and-recover loop on something visible.

```bash
fireflymcf image-export mcf.bin                          # list the images and their sizes
fireflymcf image-export mcf.bin --index 4 --out logo.ppm # export one as a PPM
# edit logo.ppm in any image editor, same pixel size, save as binary P6 PPM
fireflymcf swap-image mcf.bin logo.ppm --index 4 --out mcf-mylogo.bin
```

The swap refuses if the new picture does not compress into the original's byte span; flat artwork fits,
a busy photo may not. It verifies that the result decodes back to your image and passes the gates. This
edit has passed every gate but has not been flashed to a coach; flash it with stock on the stick.

## Step 4 — flash

1. Format a USB stick as FAT32.
2. Create a directory named `SpyderConfig` on it.
3. Put your build (`mcf-<name>.bin`), your stock `mcf.bin`, and `cli.bin` in that directory. The
   loader lists every `mcf*.bin`; `cli.bin` is the loader itself and must be present.
4. Insert the stick and power-cycle the panel. Without a stick the panel boots normally; with a stick
   but no `mcf*.bin` it stops at "Master Config File Missing".
5. Choose which file to load.
6. Touch each unit that shows it needs updating.
7. Let it finish. The panel reads each section back from flash and verifies its checksum before
   accepting it.

Builds from this repository carry a distinct build name (stock plus `(Custom)`, or a name you set) so
the menu tells them apart from stock.

## Step 5 — recover

If a build misbehaves or the panel boot-loops: power-cycle with the stick inserted and choose the stock
`mcf.bin`. This has been done on the reference coach after an early relocating build boot-looped.

One thing is not established: whether a GuiData broken badly enough to crash before the menu draws
would block recovery. The tools do not produce such a file (same-size edits leave every structure
intact; relocations are gated), which is the reason to flash only tool-validated builds.

## Where to go next

- `docs/CHANGE-CATALOG.md`: everything you can change, grouped by what you want to do — read this to see what is possible.
- `docs/COOKBOOK.md`: command lines for the common changes, and compiling a project.
- `docs/RECIPE-OPS.md`: every change the engine can make, with its hardware status.
- `docs/OBJECT-HIERARCHY.md`: how a control is composed, for adding or cloning one.
