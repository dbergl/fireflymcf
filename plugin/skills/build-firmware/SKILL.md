---
name: build-firmware
description: Use when an RV owner wants to change what their Firefly / Spyder multiplex touch panel (Jayco and similar coaches) shows or does — rename, move, recolour or add a button, change a tap or hold, show a bus value — or asks for an mcf.bin / firmware file to put on a USB stick.
---

# Build a custom Firefly firmware

Turn an owner's description into a flashable `mcf-<name>.bin`, compiled from **their own** stock
`mcf.bin` with the `fireflymcf` CLI (`pip install 'fireflymcf[full]'`). The panel checks checksums,
not whether the content suits the coach, so a build must be right by construction and described
honestly. One change you can stand behind beats four you guessed at.

## What you need first

1. **The owner's stock `mcf.bin` and `cli.bin`**, from Firefly for this coach. Never another
   coach's file or an example. Record `sha256sum mcf.bin`.
2. **The floorplan code and fitted options.** `fireflymcf options mcf.bin` ends with the build's
   floorplan codes (`WD`, `RT`, …). They decide what each page draws. Record them with
   `fireflymcf coach-config mcf.bin -o coach.json --floorplan WD [--option NAME]...`, or from
   a `candump -L` log of the bus with `--capture LOG` (the coach's own settings). Ask; if you
   cannot, render every listed floorplan, change each variant the page draws, and say so.
3. **Whether stock is what the coach runs now.** A build replaces GuiCode and GuiData together, so
   it erases any earlier custom build.
4. Each request restated as one line. Anything that reads two ways (which "blue", which button)
   is a question, not a guess.

## Inspect (all read-only)

```bash
F=fireflymcf; M=stock/mcf.bin
$F doctor $M                          # build name, sections, interlocks, tank broadcasts
$F pages $M                           # each screen: --root index, --page name, header
$F labels $M --containing "Pump"      # every record with that text, by object index
$F pins $M                            # each G12 output: status cells and consumers
$F bus-map $M                         # every message received and sent, and the panel's name
$F patch $M /dev/null --list-colors   # the only colours a recolour can use
$F ops                                # every change the engine can make; `$F ops NAME` for params
$F render $M work/home.png --page home --config coach.json --placements work/home.json
```

`labels`, `pages`, `pins`, `bus-map`, `options`, `doctor`, `ops`, `validate`, `compile` and `verify` take
`--json` when you want to parse the output.
`--page` takes `home electrical lights climate settings options outputs floorplan`, or use
`--root N`. `--page` finds a page by its header text, so on a build that renamed a header use
the `--root` that `pages` printed for stock. The hit map's `placements` list gives each drawn record's `obj_index`, `box`, `label`,
`wtype`. Look at the PNG.

- **Object indices come from the owner's file, under their floorplan.** One caption often lives
  in several objects (floorplan variants, switch arms): HOME's "Water Pump" is object 295 under
  `WD` and 293 with no floorplan. Find every copy with `labels` and decide which to change.
- **An output number needs proof.** Render `--page outputs` (the panel's G12 OUTPUTS list) under
  the owner's floorplan and read `pins`. If the owner's number is named for another load there,
  stop and ask: a wrong channel switches the wrong load. `add_button` and `wire_button` refuse
  a named output (`NAMED_OUTPUT`); pass `allow_named_output` only after the owner confirms.
  The firmware also holds other numbered lists (other modules' diagnostics); an installer's
  number may come from one of those, so ask which module it is on.
- **Every bus number comes from a tool run on the owner's file**, never from memory or the
  RV-C spec: a tank instance, G12 channel, DGN or source address. Builds can differ. For any
  question about a message the panel receives or sends (what it does with it, which control
  sends it, what the panel calls it), run `$F bus-map $M` (`--dgn X` for one DGN, `--json`
  for all of it) and quote it; its RV-C name is the specification's, not Firefly's. Tank
  instances: `doctor` lists what this file's G12 broadcasts on `1FFB7` TANK_STATUS, and
  `fireflymcf-tankpatch MCF` (report-only without a tank flag) is the tool that silences
  them (op `suppress_tank_status`). Quote that table. Its panel name is the caption the
  panel draws on the gauge each instance feeds, read from the file; the RV-C name is only
  the standard's for the number. Name each tank by its panel name. Where the panel name is
  `-` (no gauge traced), use the RV-C name and tell the owner it is the RV-C
  specification's name, which may not match what Firefly calls that tank. Where the two
  disagree, a panel name lists variants (`GREY / GREY1`), or the owner's floorplan draws
  no such gauge, ask the owner before silencing it.
- **Check the target is the control, not its container.** An object index can be a whole card
  of controls. `recolor` refuses one (`CONTAINER`); other ops may not, so compare the record's
  `box` in the hit map with the control you mean, and diff the render.
- **Map each request to an op** with `$F ops`. `verified: NO`, or a status of built-not-flashed,
  means no coach has run it: use it only if the owner agrees to be first. An op this coach already
  runs in its current build carries no new risk; say so instead of asking again.

**Say up front what cannot be done:** a value the panel never receives (the file holds widgets,
not data); a colour not in `--list-colors`; floorplan option defaults (emitted by code, not
stored); an op that refuses this build (ops written against 13.6 check its bytes). Never work
around a refusal. An `allow_*` parameter is an acknowledgement the owner gives, not a fix.

## Author, validate, compile

```json
{"schema": 1,
 "base_sha256": "<sha256 of the owner's mcf.bin>",
 "build_name": "Jayco B-Van V13 pump-rename v1",
 "ops": [{"id": "pump", "type": "rename_label",
          "params": {"object": 295, "old": "Water\nPump", "new": "Pump", "align": "center"},
          "note": "owner asked for the short name"}],
 "preview": {}}
```

`fireflymcf schema` prints the file's JSON Schema. `build_name` is what the boot menu lists:
keep the vendor prefix; make it distinct from stock and from earlier builds. Ops apply in order;
`{"ref": "<id>"}` names an earlier op. Every change goes in one project, compiled from stock.
Never patch a patched file.

**Changing a build the coach already runs:** copy that build's project and append the new ops
(a renamed page header goes last: ops before it find the page by its stock name). Never start
from stock alone, which would drop every earlier change.

```bash
$F validate pump.ffproj.json $M --require-verified    # every gate; writes nothing
$F compile  pump.ffproj.json $M -o build/mcf-pump-rename-v1.bin
```

A failure names the op id and an error code (`$F ops NAME` explains it), sometimes with an `offer`.
`compile` refuses a project with an unverified op; add `--allow-unverified` only after the owner
has agreed to be first.

## Verify the whole build, not only your change

```bash
$F verify build/mcf-pump-rename-v1.bin $M --config coach.json   # every page, ~1 min
```

For a change to an earlier build, also run `$F verify NEW.bin PREVIOUS.bin --config coach.json`:
only the new ops' changes may differ (a grown label also moves GuiCode's pointers past it and
its checksum). Every checksum must verify whatever the size.

It lists each section, every differing byte run by region (build name, checksum, GuiData object
N), every object changed or added, and every page whose render changed, with the changed area.
Account for **each** line: a run, object or page an op did not mean to change is a defect, not a
side effect. Exit 1 (a checksum that fails, a byte nothing claims, an unexplained run, a page that will not render, a
build name equal to stock's) means do not hand it over. Also read the compile manifest: each line
is an op checking itself on the finished bytes. Look at the changed pages' PNGs
(`$F render`).

## Hand over

The owner's message has **every** slot below, in this order:

1. **File:** path, SHA-256, and the build name the boot menu will show.
2. **Done:** each change in the owner's words. **Not done:** each request left out, and why.
3. **Unverified:** ops no coach has run yet, or "none".
4. **Replaces:** it replaces everything the panel runs now, including any earlier custom build.
5. **Flash:** FAT32 stick → folder `SpyderConfig` → the build, the stock `mcf.bin` and `cli.bin`
   inside (the loader needs `cli.bin`) → power-cycle the panel → choose the build → touch each
   unit that asks to update → let it finish.
6. **Recover:** power-cycle with the stick in and choose the stock `mcf.bin`.
7. **Check at the coach:** what to look at, and the questions still open.

A later change is a new op in the same project, recompiled from stock.
