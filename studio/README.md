# Firefly Studio

A browser editor for the Firefly MCF firmware bundle. It edits a **project**, an ordered list of typed
changes, and compiles it from a **stock** MCF into a flashable `mcf-*.bin` with the same engine and
gates as `fireflymcf compile`.

## Run it

```bash
pip install -e '.[full,studio]'             # from the repository root
npm ci --prefix studio/web                  # once
npm run build --prefix studio/web           # produces studio/web/dist
python3 -m studio --stock /path/to/your-mcf.bin   # http://127.0.0.1:8676, your own coach's firmware
python3 -m studio                           # default stock firefly-13.6/mcf.bin (research data only)
```

The server is build-general: reference data (pins, colours, fonts, pages), compile, render,
placements, export, per-floorplan option defaults and the floorplan picker work on 11.23, 13.6 and
14.7 and should work on your build. Author against your own stock: object indices differ between
builds. The 11.23 floorplan map and slot were confirmed on a coach flashed to 11.23 (2026-08-30).

Without a web build, `python3 -m studio` still serves the API (`/api/docs`). For UI development run
`python3 -m studio --no-browser` in one shell and `npm run dev --prefix studio/web` in another; Vite
proxies `/api` to port 8676.

## Projects

The app edits one project at a time, named by `?project=NAME` (`default` if omitted). Opening a name
nobody has used shows a create-project control and lists the projects that exist. `POST /api/projects
{"name": ...}` creates one from the command line; `GET /api/projects` lists them. Project files are
`projects/NAME.ffproj.json` and compile against the stock the server was started with.

## Coach config (floorplan and options)

Several pages depend on the coach's configuration: LIGHTS draws "NO FLOORPLAN SELECTED" until a
floorplan is chosen, and the OPTIONS pages show which features are fitted. The **Preview** drawer
under the canvas picks both.

- Choosing a floorplan loads its defaults, derived from the GuiCode tap handler that transmits them
  (`GUICODE.md` Finding 42). Three floorplans (4NJ/3NG/3NK/3NJ) have no defaults handler; picking one
  says so and leaves the options unchanged.
- Options sharing a value slot are a radio group: AGS / AES / AES Single Output are one setting.
- Some display state the panel derives rather than receives. The HOME battery block is computed from
  the generator mode, so there is no separate battery-block setting; the picker says so.
- The three OPTIONS pages all carry the header "OPTIONS" and are listed as `options`,
  `options-electrical` and `options-2`.
- One selector, **Battery**, is placed by no page root and is listed but disabled. Object 247 is on
  screen on the coach by a binding this project has not located; the renderer cannot draw it.

Coach config is saved in the project's `preview` field, which is **not build input**: floorplan and
option state live in G12 storage and reach the panel over the bus. The compile etag excludes it, so
changing it never alters an output byte.

**Import from coach** (optional) decodes the G12's `0x15FCE` configuration broadcast from a
`candump -L` capture you paste or upload, shows a diff against the project, and applies it on
confirmation. It takes text, never a connection; a capture with no `0x15FCE` frames leaves the picker
unchanged.

## The panes

- **Changes**: the project in application order. Toggle an op off without deleting it, reorder, and
  annotate. A failed op shows its structured error and the engine's suggested fixes.
- **Canvas**: the page as the panel draws it, from the real renderer. Click a control to select it.
  Drag a caption to move it, or drag a control's box to move the whole control where the page carries
  an attach record for it. A readout shows the raw coordinates the change will write; Esc cancels.
- **Inspector**: the selected control's fields. Each edit becomes one op; editing the same field again
  updates that op. The record you clicked is the one edited.

## Adding a control

- **Button**: a boxed button on a page, wired to a pin. The caption is a textarea because a caption can
  be two lines (the firmware means the newline literally). Choose the pin, resting colour, caption font,
  and tap or 3-second-hold toggling.
- **Glyph button**: an icon button cloned from a donor control and wired to its pin in the same step.
  An unwired clone carries the donor's command group and the engine refuses to finish a build that
  contains one.
- **Import a glyph**: PNG artwork into an icon font. The image's alpha becomes the glyph; colours are
  discarded because the panel paints a glyph in the widget's ink. The y-bearing is computed from the
  font cell; a logo authored at `y = 0` drew 33 px above its button on the coach.

Once a control exists, **Control size** sets one width and height for all three of its records (box,
caption, touch region), and **Position on page** moves its attach record. Per-record fields remain
under **Size & position**.

## Worked example

A project file is an editable change list: copy one into `projects/` and it opens here, artwork
included as pixel data. The editor's export goes through the same engine as `fireflymcf compile`,
so a project built by clicking compiles to the same bytes as the same project written by hand,
whatever the op order.

## Before you flash

Each export writes `build/mcf-<project>.bin` and a `.json` sidecar with its SHA-256 and manifest. The
manifest's `unverified` list names ops whose mechanism has never run on a coach: `remove_control`
always, and per instance a tier-3 `add_register_readout` cell or a `fix_variant_gate` mode other than
`selector` (`docs/RECIPE-OPS.md`). The download reports `X-Firefly-Stale` if the project changed after the
export; a `.bin` copied off the disk carries no such marking, so re-export rather than reuse. Flash and
recover per `docs/GETTING-STARTED.md`.
