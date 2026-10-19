# The decoded renderer — approach A, its residuals, and the fitted-heuristic sweep

Moved out of `CLAUDE.md` on 2026-08-25, **verbatim**. The work is settled; the reason to keep all
of it is that most of these entries are *negatives* — places where a confident colour or position
would have been wrong, and the decode correctly declines to invent one.

**The two live rules, repeated here and kept in `CLAUDE.md`:**

- **`render.py`'s module docstring is the authority on fill policy**, not this file. A fill carries
  a `color_src` of `decoded` / `resting` / `voltage-state` / `heuristic`, and **`heuristic` is not
  painted** — the decoded parent shows through. A decode to-do, never an invented colour.
- **ALTERNATOR-SYSTEM is a relative-layout and relative-colour oracle only** (owner's note). Do not
  fit absolute pixels to it.

---

### GUI rendering and the layout engine (approach A) — 2026-08-14

Two renderers now exist, on purpose:

- **`src/fireflymcf/render.py` — two compositors, one rasteriser.** *Approach B*
  (`climate_placements`): data-driven text/glyphs but widget **placement hand-calibrated** to
  the coach photo (`build/climatepage.jpeg`), anchors tagged `src="calibrated"`. *Approach A*
  (`climate_placements_decoded`, **wired in 2026-08-14**): the whole page composed from
  `layout.py` with **no calibration** — positions, font sizes (`metrics.font_metrics`), kerned
  text width + the align byte (`metrics.text_width` / `aligned_x0`), and the panel's own icon
  glyphs, all byte-derived. Renders header + nav rail + CLIMATE tab + Cool/fan/High/Low/Auto
  matching the photo (`build/climate-A-*.png`); only colour stays a heuristic (open thread 1)
  and runtime values (temperatures, clock) are absent (not stored). `render_climate_decoded()`.
  The icon glyphs are slot-23 text-selector glyphs (snowflake `3`, fan `4`, chevrons `5`/`6`,
  wifi `@`); the glyph start-offset window is 17..20 (`GUICODE.md` Finding 11 note fixed).
- **`src/fireflymcf/records.py` — the byte-exact record decoder.** A Python replay of
  `FUN_00252CB0`, validated by the object-table boundary oracle on **every object in all three
  builds** (1421/1076/1200, zero derailments — `tests/test_records.py`). Yields each record's
  type, six geometry coords, container child index, label, base modes and register indices.
  This is the real thing, not `guidata.py`'s boundary-fitted heuristic. `GUICODE.md` Findings
  10–12.
- **`src/fireflymcf/layout.py` — the approach-A compositor.** Replays the layout engine
  (`FUN_00257BFC` pen arithmetic + `FUN_00258330` container recursion): an object-table index
  walked from base `(0,0)`, with
  `coord = geom + register_file[in_reg] + base_reference(mode)`, and the base frame
  (`+0x3a..+0x40`) reset per record by a type switch. **Validated:** the Climate page (obj 7)
  reproduces from the bytes with the header, nav rail, body, and the CLIMATE tab at `(211,76)`
  — matching the hand-calibrated `(224,70)`. `tests/test_layout.py`.

**Multi-root composition — checked, 2026-08-14 (thread 7 step 1).** Screens are *not* one
object each. `GuiCode_entry1` calls the screen-setup `FUN_00258650(index)` on a small set of
top-level roots: index **0** (home/background) plus popup overlays (**0x2f2** pairing,
**0x1ac/0x1ad**), all indexing the same `+0x46148` table (`DAT_0025887c = 0x90046148`,
`proven-from-code`). A page like Climate is **not** a `FUN_00258650` root — it is a child page
selected inside root 0's tree by a runtime **type-6 value-match switch** (obj 972 type-10 →
child 7 is part of that link; the switch's candidate-child *list* is the one thing `records.py`
does not yet expand). `climate_root()` returns the page-content object (obj 7), which already
carries the whole page — so the multi-root hypothesis **does not** explain the vertical
residual. It did correct the model: "screen root" was imprecise.

`layout.py` is now **wired into `render.py`** (`climate_placements_decoded`) after the vertical
origin was resolved (open thread 7 below). The remaining approximations are colour (heuristic),
the proportional-text stand-in font (DejaVu; sizing/placement are real), and objects outside the
page's own walk. That last one was chased (Finding 15) and is a **defensible negative**: the
Starlink button (obj 247) attaches by *none* of the graph mechanisms — no `child_index` parent,
no absolute pointer, not in any type-6 switch's `[base,base+count)` range (every one of the 896
switches resolves to a single child). It is almost certainly placed by the **option → placement
binding** (open thread 4), not the page/screen composition. Also settled there: **types 5/6/0x2e
are the only container recursions** (`layout.py` now gates on them); a type-10/18 `+0x28` is an
**image index** into `FUN_00255F80`'s 8-image table, not an object index. For the record, the
diagnosis behind the vertical fix: the Climate setpoint is a **type-0/8 value widget**;
`FUN_00257BFC`'s
per-type frame switch sets the frame box `+0x3a..+0x40` from the **content-render extents**
`+0x6c12..+0x6c18` for types 0/8 (`state.txt`: `+0x3a=+0x6c12`, `+0x3c=+0x6c14`, `+0x3e=+0x6c16+1`,
`+0x40=+0x6c18`), not from the geometry box. Those extents are the *rendered* bounding box,
written by the value/text drawing primitive **inside `FUN_00256c54`** (invoked at `FUN_00257BFC`
line before the switch), so they need **glyph/text metrics** to reproduce. `_update_frame`
approximates them with the geometry box → collapses to zero height → the mode grid, whose y
reads that frame via **base-mode 2**, rides up to y=76 and overlaps the tab instead of sitting
below the setpoint. Reproducing the extents is a fresh subsystem (glyph/text metrics),
comparable in size to the decoder dig; it also improves render.py's text sizing.

### Render fidelity (approach A) — 2026-08-15 (merged to master)

The decode-derived renderer was extended so a render can preview a patch before flashing —
all byte-derived, no fitting (SDD: working notes, not published):
- **Per-record-type geometry** is byte-faithful against the full `FUN_00257BFC` frame switch,
  now transcribed as `GUICODE.md` **Finding 21** (`layout._update_frame`).
- **Horizontal alignment** (align byte `header[3]>>6`: 0/2/3) is applied to a label's own drawn
  box via `layout._placed_label_box` + exact `metrics.text_width`.
- **Panel glyphs** draw proportional text from the decoded font slot (`glyphs.decode_glyph`,
  threaded via a per-render `GlyphTable`), with mode-3 inline colour and honest
  `font_src`/`color_src` labelling.
- **Generic any-screen render**: `render.screen_placements(data, root)` composes any page;
  `climate_placements_decoded` is now a wrapper. `cli render --page NAME|--root N`, where NAME is
  one of home / lights / electrical / climate / settings / options / outputs / floorplan. Each is
  resolved per build by its header record's label (`outputs` structurally), so a page name works
  across builds or refuses — `floorplan` resolves to obj 19 on 13.6 and 21 on 14.7, and correctly
  refuses on 11.23, which has no selection screen (`MCF-FORMAT.md` §7).

**Photo oracles exist — correction.** `build/13.6-images/` holds hardware photos for **HOME,
LIGHTS, ELECTRICAL, SETTINGS** (+ HOME-INVERTER-AGS). The render-fidelity SDD wrongly assumed
only Climate had an oracle, so it validated against Climate — which does **not** exercise
tank-bars, value+unit widgets, or button boxes. Checking `render-screen-home` against `HOME.jpg`
exposes a punch-list (below).

**Home render fidelity — items 2-4 RESOLVED, 2026-08-15 (merged, SDD
working notes, not published, oracle `HOME.jpg`):**
- **Gauge wells** now draw a byte-derived **outline** (new `Prim.outline`) + the adjacent
  record's **mode-3 decoded level fill**, paired by same-object `obj_index` (a flat-adjacency
  mis-pairing was caught in review).
- **Value+unit (`%`)** fixed: the unit widgets (wtype 0/8) were being swept into the icon
  state-stacking path; excluding them routes them through the text path, so `0%` sits one-line
  at its decoded pen.
- **Button boxes** (Water Pump / Tank Heater / ON-OFF / EXT-RET) now visible via a byte-derived
  **`Prim.stroke`** (border); **labels centre** in their box geometry (fixes ON/OFF, EXT/RET).
  *(Their **fill** was a labelled heuristic at this date. Superseded twice since — see residual 4
  and the `color_src` note below. Do not read this bullet as the current fill policy.)*

**Proportional-glyph decode — RESOLVED, 2026-08-15 (`GUICODE.md` Finding 22, merged, SDD
working notes, not published).** Never corruption: the glyph bitmap is 4bpp/1bpp
pixel rows under a **per-row vertical-repeat RLE** (blitter `FUN_002510C0`) — a bitstream at glyph
`+0xe` repeats each stored row `n`× vertically, so the old flat decoder undercounted rows on
vertically-uniform strokes. `glyphs.decode_glyph` now transcribes it byte-exact (bpp from the
descriptor byte `font+8`); text is **pixel-faithful** (the panel's own letterforms), `is_reliable`/
DejaVu fallback retired. Validated: no glyph over-reads its record across all three builds, proof
glyphs reproduce, icons/value fonts unregressed, boundary oracle untouched.

**Remaining render residuals (honest, not fitted — now only the not-in-file ones):**
1. **Chrome fill colour — RESOLVED for the static indices, 2026-08-15** (thread 1 below;
   `theme.mode7_palette`). The mode-7 chrome palette is now byte-derived from GuiCode's theme-init,
   so the header/nav rail/ON-OFF **red** `(190,30,45)`, the **background** (idx 3), the **quadrant
   panels** (idx 8), the **nav rail** (idx 184) all render decoded (match `HOME.jpg`). The **HOME
   tank display is byte-derived too** (dig + owner hardware oracle, 2026-08-15): well **body** = each
   well's `fillB` mode-7 **161** = `(65,81,163)` (a blue lighter than the quadrant, as the owner
   describes); per-type **fill colour** = FRESH mode-3 `#00A79D`, GREY/GREY2 `#808285`, BLACK mode-7
   **130** (near-black) — `render.py` reads the level via `_decoded_fill` (mode-3 **or** mode-7, which
   fixed BLACK rendering empty), and draws it only up to a `tank_level` preview (default 0 = the owner's
   0%; the fill *height* is the runtime value, thread 5). **Residual — proven defensible negative, 2026-08-15
   dig** (`build/residuals-dig-notes.md`): the still-heuristic fills are **runtime-state-selected**, not
   missing — Water Pump / Tank Heater (idx 160/455, cells `0x20039eac`/`0x20039b74`) are written *three*
   theme constants chosen at boot by widget on/off/unavailable state; a separate green/red pair (idx
   202/206) is a level/alarm indicator — **owner-verified on the coach (2026-08-15): NOT the four HOME
   tanks' fill** (fresh/grey/grey2/black keep their per-type colour at 0/33/67/100%, never red), so the
   render draws the per-type colour at all levels; EXT/RET/battery/Inverter/CLIMATE-box fills are **mode-0 inherit**
   (no colour byte). A confident copy-forward would print a wrong colour (it sees one of three branches),
   so the render keeps them `heuristic`. `theme.py` stays as-is.
2. **Runtime values** (tank fill *level/height*, temperatures, clock, voltages) are bus-delivered
   (thread 5), not stored — the render shows the widget and its byte-derived colour, not the live value.
3. **Label/box overlap — RESOLVED for tall-box labels, 2026-08-15** (`render.py` label loop):
   a caption whose decoded box `[y0,y1]` is taller than its text block is now **vertically
   centred** in the box (byte-derived from box geometry + `metrics.font_metrics`; the vertical
   twin of the horizontal centre-in-box), so Water Pump / Tank Heater and the House / Chassis
   battery captions no longer ride the box border / battery terminals. Guarded so a header over
   its box (`CLIMATE`) and degenerate (h≈0) boxes (the tank `%`) stay at `y0`. The **`ON`
   false-collision — RESOLVED, 2026-08-15**: label collision-stacking is now **overlap-aware** —
   two labels sharing a pen (`Light Master`+`ON`, `Exterior Master`+`ON`) stack only when their
   *drawn* horizontal extents actually overlap, so `ON` centres in its own box beside its wide
   caption instead of stacking below it (a genuine same-x overlap still stacks; tested both ways).
   The **floorplan tank off-canvas** was **not reproducible** on HOME under any floorplan/Black-Tank
   config (diagnosis 2026-08-15) — nothing exceeds 800×480; the phrasing was stale.
4. **Byte-only render + two position fixes — 2026-08-15** (owner direction; branch `render-residuals`).
   The decoded compositor now paints **only byte-derived colours**; mode-0-inherit / unresolved-mode-7
   fills are left unpainted (the decoded parent shows through), a decode to-do rather than an invented
   heuristic. Newly painted because it *is* in the bytes: a record's box draws from fillA (primary) or
   fillB (resting), so the navy bar behind **Awning** (its fillB=idx3) and the CLIMATE fillB=idx161 box
   show; the HOME tank display (well body idx161, FRESH/GREY/BLACK per-type fills) is byte-derived (only
   the fill *level* is runtime). Two position fixes, both `proven-from-code` (**`GUICODE.md` Finding 24**):
   the 5 nav-rail icons **centre** in their full-width rail box, and **OFF** right-justifies above RET —
   the align byte is *persistent* render state (like the font selector) and right-justifies a path-1
   record's box by its geometry width, so OFF's box and its inheriting align-0 label both land at
   `327-397`. `layout.py` (`_State.align`, `_writes_align`, `_align_path1`, `_placed_label_box`).

**Text line advance — byte-derived since 2026-08-18** (`GUICODE.md` Finding 37). R49 is **four**
bytes, not three: `h_align`, `v_align`, `line_height`, and a **flag** at state `+0x97` that makes
the multi-line drawer add the **font cell height** to `line_height` once. So the advance is
`line_height + font[0x07]` when set, `line_height` otherwise — `records.WRecord.line_height_adds_font`
/ `render._effective_line_height`. 126 records on 13.6 use it, nearly all with `line_height = 0`
(i.e. "space by the font's own cell height"), which was previously the `size + 2` guess. Visual
effect ~1 px; this is decode completeness, not a visible fix.

**Word-wrap — RESOLVED, `proven-from-code`** (`GUICODE.md` Finding 38/39). Two parts, and a
retraction. (a) The drawer is chosen by **widget type**, via the `tbh` table at `0x256cd2` on
`record+0x8a`: type 0 → `FUN_002557C0` (single line), type 11 → `FUN_002559A0` (**wraps**). The
`state+0x704c` / `state+0x4` predicate an earlier note said to trace was a red herring. (b) The
wrap budget is the box's **inclusive** width, `x1 - x0 + 1` (`0x255a7a`-`0x255a8e`), and a run
breaks only when it **strictly exceeds** it (`0x255b0c` `ble` = fits) — the same inclusive
convention as Finding 14's `y1 = y0 + font[0x07] - 1`. `render.py` used the exclusive width and so
wrapped two labels that fit by exactly **one pixel**. *(An intermediate fix gated wrapping on
`line_height >= font ascent`; it separated every oracle case and was **fitted**. Retracted.)*

**mode-5 transparency is PERSISTENT, and mode 0 inherits it** (Finding 38). Every explicit colour
mode does `bic #2`, mode 5 does `orr #2`, mode 0 touches neither — so a mode-0 field after a mode-5
one is still transparent. `layout._State.fill_transparent`, saved/restored per container exactly
like `fill_slots`. Modelling mode 5 as per-record painted the ALTERNATOR `CLEAR EEPROM` caption's
inherited navy over its olive button box.

**Photo oracles now: HOME, LIGHTS, ELECTRICAL, SETTINGS, CLIMATE, ALTERNATOR-SYSTEM** (+
HOME-INVERTER-AGS, MAIN_OPTIONS, ELECTRICAL_OPTIONS) in `build/13.6-images/`. ALTERNATOR-SYSTEM is
**relative layout and relative colour only** (owner's note) — do not fit absolute pixels to it.

**Fitted-heuristic sweep — 2026-08-18.** Prompted by Finding 39 (a gate that separated every
oracle case and was still fitted). What the sweep found, and what was done:

| item | verdict |
|---|---|
| **text ink resolved mode 3 only**, hardcoding white otherwise | **FIXED** — 128 mode-7 labels (red idx 118 / cyan idx 35 status glyphs) painted white, and 1848 mode-2 labels were mislabelled `heuristic` when the byte says white outright. Now routed through `_decoded_fill(..., border=True)` + mode 6. Oracle pages pixel-identical |
| `COL` eyeballed palette / `COL["outline"]` synthetic border | **already dead** in the decoded compositor; only the *comments* still described them. Comments corrected — there is no position-based colour assignment left |
| `_VOLTAGE_SLOTS` | **keep** — a *preview* mapping (which runtime voltage to substitute), genuinely not in the file |
| `_RESTING_FILLS` idx 455 / 375 | **keep** — the ambiguity guard is right: both cells have **three** copy masters (blue/red/cyan = off/fault/on), so `resting_defaults` correctly omits them. The curated values are painted and labelled `resting` |
| `_BATTERY_FILL_SLOTS` | **keep, documented** — derivable in principle (obj 440 idx202 ↔ wt-8 `value_slot` 205; obj 443 idx206 ↔ 209, same object) but a selecting signature would need tuning, i.e. a new fit for an old one |
| `climate_placements` (approach B), `metrics` `width_exact=False` fallback | **keep** — calibrated/fallback by design and labelled as such |

**The current fill policy, in one place** (this supersedes the fill wording in every bullet
above; `render.py`'s module docstring is the authority). A fill carries a `color_src`:
- `decoded` — the record's own mode-3 inline BGR, a mode-6 GuiData-palette entry, or a mode-7
  index `theme.mode7_palette` resolves. Painted.
- `resting` — a **runtime-state-selected** mode-7 cell whose *default* constant is recoverable
  from the theme-init (`theme.resting_defaults`, `GUICODE.md` Findings 28/34). Painted, labelled
  as the resting state rather than the live one. Water Pump / Tank Heater / EXT / RET and the
  FLOORPLAN SELECTION buttons are this class. `_merged_resting_fills` is **every index the
  resolver derives** plus the two curated ones it cannot reach (455, 375) — *widened 2026-08-18;
  it used to be scoped to a hand-curated list, which left ten floorplan buttons unpainted for no
  reason but omission (Finding 34 addendum).* Order matters: `_battery_fill` runs first, since
  idx202/206 resolve in both and the voltage-state reading is the more specific.
- `voltage-state` — the HOME battery boxes (idx 202/206), coloured by the firmware's own
  byte-derived 11.9 V threshold and drawn to a byte-derived charge fraction.
- `heuristic` — nothing resolved. **Not painted** on the decoded compositor (`if color is not
  None` gates every fill); the decoded parent shows through. A decode to-do, never an invented
  colour.

---

## Text layout: the wrap and the clip are derived now (2026-09-06)

Both halves come from `FUN_002559A0`, decompiled from the `guicode136` Ghidra project
(`GUICODE.md` **Finding 69**).

**Wrap — `metrics.wrap_lines`.** A transcription, not a fit. It replaces a greedy break-on-space
that stood here; the two agree on every one of the eight photo-oracle pages, pixel for pixel, so
the swap cost nothing and the rule is now sourced. What the heuristic did not have: the firmware
also breaks after a `-` that is **not** followed by a space, and it stops at 30 lines. A chunk
wider than the box is not broken mid-word — it takes its own line and overflows, and the clip
cuts it.

**Clip — `render._text_clip`, horizontal only.** The drawer installs `box ∩ enclosing clip` before
drawing and restores it after, and the rectangle is **inclusive** — which is why a record whose box
collapsed to `x0 == x1` draws exactly one pixel column (v20's corner mark; the reference coach's build log (not published)
"Experimental v20"). This is specific to wtype 11/13: a **wtype-0** single character is drawn by
`FUN_002557C0` and is not bounded, which is why HOME's `V` and CLIMATE's `°` render in full from
0×0 boxes.

### Two halves, both now closed — kept because each says why it was held open

1. ~~**The vertical bound is not applied.**~~ **CLOSED 2026-09-06 — both axes are clipped.** It
   was held back twice and neither reason survived:

   *A 2 px error of ours.* This renderer penned 13.6's `Go Power!\nSettings` second line at 443
   where the firmware pens it at 441, so a vertical clip would have shaved 8 pixels
   `build/13.6-images/MAIN_OPTIONS.jpg` shows the panel drawing. That was **Finding 37's rule
   shipped dead**: `_effective_line_height` referenced a `metrics` that `render` does not import
   at module scope, so every call raised `NameError` into a bare `except Exception` that returned
   `line_height or None` — and since all 126 adds-font records carry `line_height = 0`, that is
   always `None`. The caller fell through to `size + 2` every single time. Fixed with a
   module-local import and a narrow `except glyphs.GlyphError`; the render moved 537 pixels
   closer to the photo on that button.

   *A misread of the coach photo.* v20's boxless mark would clip to 1×1, which seemed unable to
   produce the "one column of pixels" the owner reported. Measuring the photo settled it: the
   surviving ink is at panel **y 204–212**, inside `'Water'`'s own row band (201–214), and there
   is **none at all** at x 573–594. The column is the caption's `r` bleeding at 6.5 photo pixels
   per panel pixel. Nothing of the mark was drawn — which is what a 1×1 clip gives, since the
   timer glyph's pixel at (0,0) has coverage 0. Observation and code agree, and the render now
   draws zero pixels there too.

2. ~~**The enclosing clip is not modelled.**~~ **CLOSED 2026-09-06 — it is the whole screen, and
   nothing in the corpus can narrow it** (`GUICODE.md` **Finding 70**). Both halves of the note
   that stood here were wrong. `FUN_00251BB4` is not a clip at all: it unions its argument
   rectangle into an accumulator at `0x2003A994` (min on `x0`/`y0`, max on `x1`/`y1`) — the drawn
   extent — and never touches the clip rectangle. And the clip belongs to wtypes **7 and 14**, not
   5/6: `FUN_00256C54` cases 7 and `0x0E` set it to their own placed box when the record's flag
   bit `0x200` is on, and they are the only record-driven writers among the 28 stores GuiCode
   makes to that rectangle. The base is `(0, 0, 799, 479)`, written once by `FUN_00258C60` at GUI
   init and by nothing else.

   **No wtype-7 or wtype-14 record exists in any build.** All 1201/1422/1077 objects decode with
   zero derailments, giving 6109/8272/5311 records, and the wtype set is identical in all three —
   `{0,2,3,4,5,6,8,9,10,11,12,13,16,19,32,33}`. Our own v21 adds none. So `box ∩ enclosing` is
   `box`, and `_text_clip` is **exact rather than approximate**. The absence is what the model
   rests on, so it is pinned: `test_no_record_in_the_corpus_can_narrow_the_enclosing_clip`.

   One rule was read on the way and deliberately left out. The drawer culls a whole line with
   `clip_y0 <= line_y + font_cell_height && line_y <= clip_y1` (`0x255BFC`), which is redundant
   with per-pixel clipping while a glyph's ink stays inside `[line_y, line_y + cell]`.

## ~~Known gap — a shape record that does not draw~~ CLOSED 2026-09-06: it was a 2 px floor

*The gap as it stood (2026-09-03):* on `INVERTER / CHARGER SETTINGS` (root 17) with slot 23 = 1 the
coach draws a small **upward triangle** on the divider line above the `Absorb`/`Float` rows; the
render drew the line and omitted the triangle (owner photo vs `cli render --root 17 --slot 23=1`,
`GUICODE.md` Finding 63). Object 303's leading records are wt-33 and wt-4 shapes and one of them
was the marker.

**It is object 303 records 3-13, and the shape path was never involved** (`GUICODE.md`
**Finding 71**). Those eleven records are wtype-4 filled rectangles of `21×1, 19×1, … 1×1`, each
one row higher and two pixels narrower than the last, so they stack into the triangle; the first
carries `fillA` mode 7 **index 161** and the other ten inherit it through the persistent fill
register — the colour the divider line right above them is drawn in. `FUN_00256C54` case 4 hands
the placed box straight to `FUN_0025572C`, whose body is `for (y = y0; y <= y1; y++) hline(...)`,
so a one-row box paints one row.

What stopped it was the generic box loop's opening test, `2 < gw` and `2 < gh` — **a minimum-size
floor with nothing behind it**. It dropped every hairline in the image: the eleven triangle bars,
the 710×1 rules under the four `GO POWER!` headers, and root 23's 1×80 column divider in the
alternator two-column box. Now `0 <`.

**The blast radius was measured, not assumed.** Of 46 screens exactly five change (23, 36, 37, 41,
42) plus root 17 under slot 23 = 1, and all eight photo-oracle pages render **byte-identical** —
they contain no thin record at all.

**Zero extents stay dropped, and the first version of this paragraph gave the wrong reason for it.**
It said "all but a single wtype-33 are wtype 0/8/9/16 value, directive and flow-anchor shapes that
never draw" — which read past wtype 2 in its own histogram: `{0: 175, 2: 13, 8: 150, 9: 133,
16: 12, 33: 1}`. **Wtype 2 is the line drawer**, so a zero extent is the correct shape for one and
those 13 are dropped *wrongly*. They need a line primitive this module does not have; see the punch
list below. The 0/8/9/16/33 records are still right to drop.

## Next: the rest of the shape path (2026-09-06)

The triangle was found because the owner photographed it. That is the wrong way to find these, so
the same question was asked of the whole image instead: **for every record placed on any of the 46
screens, does the renderer emit a primitive, and if not, why?** 545 of 1723 emitted nothing —
532 now that the lines draw. Most are
correct — wtype 12 touch regions (216), wtype 9 format directives (133) and wtype 8 value widgets
(150, whose numbers are bus-delivered and not in the file) are *supposed* to be invisible. What is
left is a short, bounded punch list:

| wtype | silent | what it is | why it is missing |
|---|---|---|---|
| ~~**2**~~ | ~~13~~ | **a LINE** — `FUN_00254064`: `x0 == x1` a vertical span, `y0 == y1` a horizontal one, both inclusive and 1 px | **DONE 2026-09-06, `GUICODE.md` Finding 72** — `Prim("line")`, `w`/`h` carrying signed deltas. Four screens change (19, 27, 28, 32) and no photo-oracle page carries one. The find inside the find: root 32's four records are 45° diagonals that draw **the levelling diamond** on `SLIDES/AWNINGS`, joining the FRONT/LEFT/RIGHT/REAR controls — a whole shape the render had been omitting |
| ~~**10**~~ | ~~4~~ | a **bitmap** — `FUN_00255F80(record's child_index, x, y, key)` indexes parallel width/height/pointer tables | **DONE 2026-09-06** — and it needed no decoding at all: **`images.py` has decoded these since 2026-08-29** (`MCF-FORMAT.md` §5.5, the per-row RLE), it was simply never wired to a render. Roots 11, 23, 48 change; root 48 is the Terms of Use screen, whose QR code and Firefly logo are both blits. The record's declared box is `w-1 × h-1` because the blitter's extent is `x + w - 1` |
| ~~3~~ | ~~1~~ | a framed box, root 32, `fillA` mode **6** | **DONE 2026-09-06** — `_decoded_fill` resolves modes 3 and 7 only, and the wtype-3 branch returned early instead of chaining to `_mode6_fill`/`_mode2_white` the way the generic path does. It is the grey frame around `SLIDES/AWNINGS`'s levelling controls — mode 6 index 3, the same grey as the diamond it encloses |
| ~~19~~ | ~~1~~ | root 32, 20×20 | **NOT A GAP, 2026-09-06** — case `0x13` fills only `if ((flags & 3) >> 1) == 0`, the transparent-interior bit `records` exposes as `hollow`. All 27 wtype-19 records in the three builds set it, so the firmware never draws one either. Pinned as a negative |

Wtype 33's 10 "silent" records are an artefact of the audit, not a gap: they are gauge level fills
already drawn as the second half of a well pair, under the *well's* record tag.

**The list is now empty, and what is left is named policy rather than omission** (527 silent of
1723, down from 545). Every remaining record falls into one of four classes, each already
deliberate: wtype 12 touch regions (216), wtype 9 directives (133) and wtype 8 value widgets (150)
never draw or have no value in the file; eight wtype-0 flow anchors are 0×0 with an empty label;
four wtype-11 labels are suppressed by the preview's own overlap resolution; and two wtype-4 fills
on root 32 inherit mode-7 indices 424/425 that `theme.mode7_palette` does not resolve.

**Those two fills are not a decode to-do. Settled 2026-09-10.** They are the backgrounds of the two
arrow buttons on the levelling FRONT control. Object 934 places each arrow (objects 286 and 236)
through a wt-5 container whose `fillA` and `value_slot` name 424 or 425, and the arrow's wtype-4 fill
is mode 0, so it inherits that index. Slots 424/425 are two of the levelling page's arrow recolour
cells. Nothing in either image writes them and the C runtime never initialises them
(`slotsource.never_established`; `LEVELING-PAGE.md` §3-4, `GUICODE.md` Finding 67). On the coach
they power up in arbitrary colours (`BUS-BASELINE.md` PC.57). There is no colour to find, and
leaving the fills unpainted is the policy. `test_the_levelling_arrow_cells_have_no_colour_to_decode`
pins it; forcing the two indices to resolve makes the render paint both fills, so the test can fail.

### The corner radius is byte-derived, and the decoded compositor already draws it (2026-09-10)

The rounded widgets — the rounded rectangle (`0x20`), the capsule (`0x21`) and the circle/dot
(`0x10`) — are drawn by GuiCode's `FUN_00254F26`, an 8-octant circle rasteriser, with a per-widget
**corner radius** at record `+0x32` (geometry slot 4) and, for the circle, an **octant mask** at
`+0x8b` (`GUICODE.md` Finding 73, decoded 2026-09-10 from the largest dark GuiCode function; the
type-3 box, by contrast, is a square 1 px frame — see the type-3 note above).

**The decoded compositor `screen_placements` already honours it.** The 0x20 well, the 0x21 capsule
(the generic `p.wtype in (0x21, 33)` dispatch), the tank/battery level fill and the 0x10 circle all
draw at `max(0, p.coord4 or 0)` — the record's real radius — and `test_render.py` pins it
(`fill.radius == level.coord4`, the standalone-capsule rrect dispatch). So there was **no residual
here in the live renderer**; the corners are already at their byte-derived size. (An earlier draft of
this note claimed a hardcoded `radius=8` gap — that was a misread: the `8` was in `decode_widget`,
the legacy layer-1 / compositor-B path used only by the Starlink-button unit tests, not the
byte-exact compositor. `decode_widget` was wired to `records.WRecord.corner_radius` on 2026-09-10 too,
so no hardcoded radius remains anywhere in `render.py`.) `corner_radius`/`corner_mask` are pinned by
`test_the_rounded_shapes_carry_a_corner_radius_and_the_circle_a_full_octant_mask`; the octant mask
itself is not yet consumed by the renderer (every on-screen `0x10` carries `0xff` = full circle, so
nothing partial is exercised).

Three wtype names were wrong and are corrected in `records.WTYPES`: **2 was "rect"** (it is a
line), **4 was "gated_fill"** (it has no per-record gate — only the global draw-enable every case
tests), and 0x13 keeps that name because it is the one that really is gated.

**A process note, paid for the hard way.** Wiring the images took twenty minutes; *re-deriving a
decoder that already existed* took an hour before anyone noticed `src/fireflymcf/images.py` in
the directory listing. The audit above says a record draws nothing — it does not say whether the
missing piece is a decoder or a wire, and those cost very different amounts. **Check
`src/fireflymcf/` and the test suite for the capability before deriving it.** The reason it was
easy to miss is worth fixing too: neither `images.py` nor `imageenc.py` (which *encodes*, and
backs `cli image-export` / `cli swap-image`) is listed in `README.md` and `docs/COOKBOOK.md`.
*Fixed 2026-09-10: both name them now.*

**The audit script is the deliverable as much as the fixes are.** Every one of this module's
recent defects — the dead line-advance rule, the 2 px floor, and the wrong reason above — was a
silent omission that only a photograph would have caught. A standing check that every placed record
either draws or is on a named do-not-draw list turns the next one into a test failure.



---

## Moved out of `CLAUDE.md` on 2026-09-10 — the rendering section (Findings 71/72, tank level)

Verbatim, as part of the documentation cleanup (its design note (not published)). Dated statements below describe the state on the date they carry.

### Rendering — two compositors, and what the render will not guess

**`src/fireflymcf/render.py`** draws any page from the bytes. *Approach A*
(`screen_placements`, `cli render --page NAME|--root N`) composes from `layout.py` with **no
calibration** — positions, font sizes, kerned text width, the align byte and the panel's own icon
glyphs, all byte-derived. *Approach B* (`climate_placements`) is the older hand-calibrated path,
kept and labelled as such. `records.py` is the byte-exact record decoder (a Python replay of
`FUN_00252CB0`, validated by the object-table boundary oracle on every object in all three builds);
`layout.py` replays the layout engine; `metrics.py` replays the text measurer.

**The full history — approach A's derivation, every resolved residual, and the 2026-08-18
fitted-heuristic sweep — is `docs/RENDER.md`.** Two rules from it that bind here:

- **`render.py`'s module docstring is the authority on fill policy.** A fill carries a `color_src`;
  `heuristic` means nothing resolved and is **not painted**, so the decoded parent shows through. A
  decode to-do, never an invented colour. The still-unresolved fills are *runtime-state-selected*,
  not missing — a copy-forward would print a confidently wrong colour.
- **Photo oracles** live in `build/13.6-images/`: HOME, LIGHTS, ELECTRICAL, SETTINGS, CLIMATE,
  ALTERNATOR-SYSTEM (+ HOME-INVERTER-AGS, MAIN_OPTIONS, ELECTRICAL_OPTIONS). **ALTERNATOR-SYSTEM is
  relative layout and relative colour only — do not fit absolute pixels to it.**

**A 1 px box is a box — `GUICODE.md` Finding 71, 2026-09-06.** `render.py`'s box loop carried a
`2 <` minimum-size floor with nothing behind it, and it silently dropped every hairline the panel
draws: the eleven wtype-4 bars (`21x1` down to `1x1`) that stack into the upward triangle over the
`Absorb`/`Float` divider on root 17, the 710x1 rules under the four `GO POWER!` headers, and root
23's 1x80 column divider. Widget **type 4 is a filled rectangle** — `FUN_00256C54` case 4 hands the
placed box to `FUN_0025572C`, whose body is `for (y = y0; y <= y1; y++) hline(x0, x1, y)`, inclusive
on both axes — and a mode-0 `fillA` **inherits** (the decoder's fillA mode switch has no case 0), so
ten of those eleven bars take their colour from the first. Zero extents are still dropped, but that is a **limit, not a rule**: 13 of them are **wtype 2,
the LINE drawer** (`FUN_00254064`), for which a zero extent is the normal shape — so `render.py`
had omitted every line in the image until one was written — **`GUICODE.md` Finding 72**, the same
day: 1 px, endpoints inclusive, colour from the same persistent register, four screens changed and
no photo oracle among them. The best of them is `SLIDES/AWNINGS`, whose four 45° records draw the
**levelling diamond** joining the FRONT/LEFT/RIGHT/REAR controls — a shape the render had never
drawn. Bresenham is exact here only because every wtype-2 record in all three builds is
axis-aligned or exactly 45°, which a test pins. Blast radius measured: 5 of 46 screens change and all eight photo oracles are
byte-identical.

**Runtime values are not in the file** (tank fill level, temperatures, clock, voltages): they are
bus-delivered, so the render shows the widget and its byte-derived colour, not the live value.

**And the panel does NOT quantise tank level — corrected 2026-09-07, PC.62.** `render.py` recorded
"levels are quantised 0/33/67/100% on hardware (owner-confirmed)". It computes
`relative_level / resolution` and shows the percent: an injected 34/56 read **61%**, 13/100 read
**13%**. The quarters were an artefact of the only publisher the panel had ever had — the G12's
`CC`/`CD`/`CE` bucketing ladder emits nothing else. The 2026-08-15 observation was honest and
could not distinguish the panel from its sole source; suppressing that source is what settled it.
An unclaimed tank reads the `--%` no-data sentinel, not a stale 0%. *Carry the shape: an
observation of a system with one input describes the pair, not the consumer.*
