# Closed threads and closed defects — the full record

Moved out of `CLAUDE.md` on 2026-08-25, **verbatim and without edits**, because it was 65.4k
tokens and loaded into every session. Nothing here was deleted; CLAUDE.md keeps a one-line
pointer per item. Everything below is settled — kept because the *reasoning* is the value,
especially the retractions.

Each closed thread cites the doc and finding that holds its detail, and every one of those
citations was checked to resolve before the move (GUICODE Findings 14/16/17/18/19/22/23,
`BUS-BASELINE.md` PC.17, `MCF-FORMAT.md` §5.4c, the relocator spec).

**Where to find what other documents cite as `CLAUDE.md` defect 0a/0b/0c, `CLAUDE.md` owner work
item 0-5, or `CLAUDE.md`'s "Open threads" / "State and next step":** those sections left
`CLAUDE.md` on 2026-09-10 and are appended verbatim at the end of this file (defects, work items,
open threads) and of the reference coach's build log (not published) (state and next step).

---

*Status note, 2026-09-10: the suite carries no `xfail` today. Every "stays `xfail`" below is a
dated statement; the closures are `MCF-FORMAT.md` §10 item 11a (2026-08-26) and `GUICODE.md`
Finding 61 (2026-09-02). The "seven closed" in the heading is also dated: the eighth closed on
2026-08-26, see `CLAUDE.md`'s former "Open threads" section appended at the end of this file.*

## The eight original open threads — seven closed

1. **Chrome colour source — RESOLVED, 2026-08-15 (`GUICODE.md` Finding 18 + its colour-dig
   correction; SDD working notes, not published, merged to master `1714f9b`).** Each
   widget record carries mode-tagged colour fields (`records.WRecord.colors`): **mode 3 = inline
   BGR**, **mode 6 = palette index**, **mode 7 = colour-table index**, 0/4 = inherit. The chrome
   uses **mode 7**, and the mode-7 table `DAT_002535B4 = 0x90044BC8` is the **same table as the
   value-slot variable table** (`DAT_00253D04`, Finding 17) — its entries are **SRAM pointers**.
   The dig overturned the earlier "runtime-only, not in the file" read: those SRAM cells are written
   at boot by a **straight-line theme-init block inside GuiCode's `FUN_00247bf8`** as **hardcoded
   RGB888 (`0x00RRGGBB`) constants** — so the palette **is recoverable from the GuiCode section**
   (which GuiData never held; that's why it looked runtime). `src/fireflymcf/theme.py`
   `mode7_palette(data) -> {index: (r,g,b)}` extracts it build-generally: locate each type-10 cell's
   pool literal in GuiCode, trace the `str` with a small register tracer (ldr-literal / add-chain /
   movw·movt / mov·mvn), recover the constant. Traced 13.6: idx 4 = `0x00BE1E2D` → `(190,30,45)` (the
   header/nav/ON-OFF red, via an add-chain), idx 10 = `0x007C151D` → `(124,21,29)`, idx 3 = `(21,24,58)`,
   idx 8 = `(39,40,98)`. Resolves 20/18/6 cells on 13.6/11.23/14.7; **ambiguity guard** omits cells
   written >1 distinct constant (runtime value-matches), and runtime copies / untracked arithmetic
   fail safe to omission — never a wrong colour reported as decoded. `render.py` (`_mode7_fill`/
   `_decoded_fill`, palette computed once per render) draws resolved indices `color_src="decoded"`;
   **unresolved indices stay `heuristic` and are named.** The 2026-08-15 residuals dig
   (`build/residuals-dig-notes.md`) proved the still-heuristic HOME fills are **runtime-state-selected,
   not missing**: Water Pump / Tank Heater (idx 160/455) get one of three theme constants by widget
   state; tank-well level colours (idx 202/206) are green/red by live tank level; EXT/RET/battery/
   Inverter/CLIMATE-box are mode-0 inherit. A copy-forward would print a confidently-wrong colour, so
   omission is correct (defensible negative). Mode-3 inline fills *are* in the bytes too
   (`render._inline_fill`: battery teal `RGB(0,167,157)`, the Inverter black). The render matches
   `HOME.jpg`'s reds/panels (`build/render-home-coach-13.6.png`).
2. **The control → status-variable link — mostly closed 2026-08-16; one narrow question left.**
   `FUN_00247BF8` was read (`GUICODE.md` Findings 32–33), which this entry used to name as the
   confirming step. The **pin → lit widget** chain is now proven end to end and editable at four
   joints (`MCF-FORMAT.md` §5.4c, shipped as `statusbind.py`). **The "positional / construction
   order" reading is withdrawn** — a widget does hold a stored reference, just not to a variable:
   it names a *cell-table index*, and a per-var GuiCode block writes that cell. What is still
   unfound is only the **pairing inside one control**: nothing says *this* button's command and
   *that* status variable belong together, which is why `set_channel` still needs
   `move_display_list`. No candidate structure remains unexamined;
   `test_predictor_reproduces_every_known_pairing` is the strict `xfail` standing over it.
3. **The "phantom" channel 40** — **RESOLVED, `proven-from-bus`** (2026-08-14). Closed by
   the captures, no multimeter needed: the panel is only a bus participant, so the bus is the
   oracle. When 40 was genuinely mapped (`pc12`, `pc5`) the G12 (`0x9c`) broadcasts STAT3 for
   instance 40 and the panel (`0x9f`) commands it. After the remap (`pc13`–`pc16`) instance 40
   is **absent from the wire — zero frames, command or status** — while 38 flows normally. The
   G12 reports every output it drives, so its silence on 40 proves **pin 40 is not energized**;
   the lit row 40 on the G12 OUTPUTS diagnostics screen is a **panel-internal display artifact**
   — the positional status-variable reuse (`move_display_list`), the panel painting row 40 from
   the `u16` slot that now tracks 38. See `BUS-BASELINE.md` PC.17.
4. **The option → widget binding — display side RESOLVED, 2026-08-14 (`GUICODE.md` Findings
   16-17).** An option-conditional widget is a **candidate in a type-6 value-match switch**
   (Finding 16): the switch reads a value, renders the object in `[child_base, child_base+count)`
   whose entry in the parallel match table (`DAT_00257BF8 = 0x9004562C`) equals it; obj 247 (Bath
   Fan/Starlink) is the value-1 candidate of the obj-881 switch. **The value's source is now
   found** (Finding 17): the switch carries a **value-slot** (`records.WRecord.value_slot`,
   R40/41/42) indexing the variable tables in GuiData (`DAT_00253D04 = +0x44BC8` data pointers,
   `DAT_00253D08 = +0x4492C` types); `layout.variable_register(data, slot)` resolves it. The Bath
   Fan switch's slot 109 → **bool at SRAM `0x20039B49`**, and the OPTIONS-page "Bath Fan" checkbox
   (obj 46) reads the **same slot 109** — one register drives both. `layout.py`/`render.py` show
   the widget on demand (`switch_values={246: 1}`, `build/climate-A-starlink-on.png`).
   The **floorplan** works the same way: the page-gating switches read **slot 5 → SRAM
   `0x2003A77A` (u8)**, and the match-table group names the floorplans (…7=WA, **8=WD**, 9=WT…).
   Driving `slot_values={5: 8}` reproduces the probe5 coach's WD config from the bytes — LIGHTS
   becomes the light grid, SETTINGS shows "Floorplan: WD", HOME gains GREY2 — matching the
   hardware photos (`build/cmp-*.png`). `Screen.walk(..., slot_values={slot: value})` is the
   principled driver. **Only the write side remains:** what sets `0x20039B49`/`0x2003A77A` on a
   tap or config load (touch handler / EEPROM, likely `FUN_00247BF8`). 14.7's
   `'   Starlink Power'` is the shipped target for a *new* entry.
5. **Per-floorplan option defaults — write side settled negative, 2026-08-14 (`GUICODE.md`
   Finding 19).** `find_option_defaults` stays `xfail(strict)`. The `FUN_00247BF8` write side was
   read: every option/floorplan register (`0x2003A77A` floorplan, `0x20039B49` Bath Fan, …) is
   written **once, from a received CAN config payload** (`payload[0x00/0x0D/…]`, stores at
   `0x00247C20…C82`), never from a file constant or a boot-time copy. So the state — and thus any
   per-floorplan *default* — is delivered over the bus, **not stored in the MCF**; there is no
   in-file table to find or fit. This is the write-side confirmation of Finding 6's capture-proven
   negative. The **display** side is fully mapped (`options.py`: `option_bindings`,
   `floorplan_options`, `variable_register`; each option → its SRAM register), which is the ceiling
   of what the file supports.
6. **Record insertion** — **RESOLVED byte-wise 2026-08-15; runtime boot-loop fixed 2026-08-17.**
   The same-size rule is lifted: a Phase-1 spike proved GuiData records embed **no live
   self-pointers** (only the four object tables do; all inter-object refs are by index —
   `src/fireflymcf/relocator_scan.py`). So `src/fireflymcf/relocate.py` inserts bytes and
   repairs the object tables + header directory + container offsets, reseals, and self-verifies.
   `insert.py` clones an object and appends it at the top index, attaching it to a page.
   **The first hardware flash of an inserted build BOOT-LOOPED** (2026-08-17), and the dig found
   the gap: `relocator_scan.py` only checked **GuiData→GuiData** refs (index-based, true) and
   missed **GuiCode→GuiData** refs. GuiCode holds **12 absolute `ldr`-literal pointers** into
   GuiData (the four pointer tables `0x10080/0x46148/0x74d80/0x7503c`, plus a few object-data
   addresses); an insertion that shifts the two **high** tables (`0x74d80`,`0x7503c`) left those
   literals stale, so the panel dereferenced shifted content at boot → watchdog reset loop. The
   same-size edits never hit this (they shift nothing). **Fix:** `_apply_downstream_shift` now
   also relocates GuiCode's ldr-literals whose target `≥ at` by the insertion delta (both the
   append and attach steps), a **fifth gate** `_verify_guicode_pointers` re-derives them from the
   output and refuses a stale one, and `_verify_no_stranded_lcd_pointers` refuses if the
   separately-flashed **LCD Firmware** ever pointed above the insertion (on 13.6 it only names
   GuiData's base `rel 0`, so it passes). No **movw/movt**-built GuiData pointers exist (checked),
   so ldr-literals are the complete set. `theme.section_guidata_pointers`/`guicode_guidata_pointers`
   do the scan. Reflashing stock recovered the panel (matched pair).
   **CONFIRMED ON HARDWARE 2026-08-17**: the reloc-fixed build boots, and a new HOME button
   (`newbutton.build_home_button`) toggles G12 pin 15, shows its live state, and repaints in
   place — see "Adding a new button" below.
   Design: its design note (not published). Authoring is
   **clone + same-size retarget**, not a from-scratch encoder.
7. **Content-render extents** (`+0x6c12..+0x6c18`) — **RESOLVED, 2026-08-14 (`GUICODE.md`
   Finding 14).** Approach A now reproduces the Climate page's vertical layout from the bytes,
   no calibration. Chain: (a) the two extent writers are `FUN_002557C0` (single-line text/value,
   auto-sizes: `y0 = pen_y − font[0x0b]`, `y1 = y0 + font[0x07] − 1`) and `FUN_002559A0`
   (multi-line); `FUN_00257BFC` copies the box into the frame `+0x3a..+0x40` for types 0/8 only.
   (b) `src/fireflymcf/metrics.py` reads the font cell `height`/`top` byte-exact from the
   descriptor (`[0x07]`/`[0x0b]`), and `text_width` replays the measurer `FUN_002518F8` /
   `FUN_002517AC` — per-glyph left/right ink profiles (`[0..4]`/`[5..9]`) kerned by closest
   approach — so `text_extent` returns `width_exact=True` for resolvable strings (no pixel
   oracle exists, so validated by strictly-monotone glyph positions + magnitude; assumes
   char-class 2, does not model class-3 numeric fields). (c) The
   value-widget font is `state[2]`, set by `FUN_00252CB0` lines 659-664 from the **R38 selector**
   (raw byte = `glyphs.py` slot, persistent across records; the setpoint is slot 9, height 47) —
   surfaced as `records.WRecord.font`, threaded through `layout.py`. (d) The last bug was a
   second one: the flow origin `+0x36/+0x38` is **type-gated** (`FUN_00257BFC` line 33,
   `DAT_002582bc = 0x600FFDFF`) — **type-9 spacers are excluded** (bit 9 clear) and must not
   advance the flow; updating it unconditionally let an `obj93` spacer reset the setpoint's
   origin. Result, identical across all three builds: CLIMATE tab `(211,76)`, mode row Cool/High/
   Low/Auto at y = 252 / 300 / 348 / 396, below the setpoint. `tests/test_layout.py`,
   `tests/test_metrics.py`. Text width is now the exact `FUN_002518F8` replay (see (b)); what
   remains is horizontal *centring* in the layout (using that width for the align byte's
   centre/right cases) and wiring `layout.py` into `render.py`.
8. **The option → `0x15FCE` selector join — RESOLVED, 2026-08-15 (`GUICODE.md` Finding 23).**
   The panel writes an option by config-buffer **offset** (Finding 20); the wire frame carries a
   **selector**. The selector is never computed and never looked up: it is a **literal byte in a
   static 12-byte CAN-frame template** in GuiCode `.rodata` (Finding 7's pool,
   `0x00259430..0x00259ACC`), copied wholesale by `ldm {r0,r1,r2}`, and joined to the offset
   **only by code adjacency** inside a per-row tap handler (13.6 Bath Fan: `movs r1,#0xd` →
   `bl 0x00259100` @`0x0024CA8C`, then template `0x002596A0` carrying `0xE9`). **There is no
   offset-indexed table**, which is why Finding 20's search for one was correctly negative. Two
   rules recover it (A: adjacency at the `config_set` call; B: the gate register), shipped as
   `src/fireflymcf/config.py`; `guidata.find_option_bindings` now returns name + selector and
   two strict `xfail`s closed. **Honest weight:** all 7 oracle pairs reproduce, but the strong
   Rule A carries only 4 of them on 13.6 (2 come from the weaker Rule B, 1 from 11.23) — so the
   *mechanism* is `proven-from-code` while individual Rule-B attributions are
   `inferred-from-code`. **Retraction:** Finding 20's `0x1B → 0xE7` (Starlink, by elimination) is
   **wrong** — the trace derives `0x1B → 0xE4` and `0x14 → 0xE7` (Lithionics); both 14.7 rows are
   derived and **unverified**, so confirm on a 14.7 panel by toggling and diffing the broadcast.
   Two by-products, both cited, neither implemented: the OPTIONS row's closing `0x0C` u16 is the
   **tap action id** (all six rows resolve — this is the GuiData end of the chain), and the
   `E3`/`E4` → channel 25/26 binding falls out of the adjacent `0x1FEDB` command-3 OFF template
   (`MCF-FORMAT.md` §8.3 row 2 — still `xfail(strict)` until an extractor and test exist).


---

## Closed firmware defects

These were `Known firmware defects (not ours to have caused)`. **Defect 0b (`Shore Brkr Size`'s `−`) was closed 2026-09-07 and its full record is
appended below.** Two of the three below were closed by *retraction*, and the superseded
text is retained deliberately: mechanisms were built on both symptoms and both had to be withdrawn.

### Defect 0c — AES did not auto-start

0c. ~~**AES does not auto-start with every readable gate satisfied.**~~ **CLOSED 2026-08-25 — NOT
   A FIRMWARE DEFECT. It was a setting.** (`BUS-BASELINE.md` **PC.39**.) The AGS page's
   **`Low Volts` trigger** (config selector `0x2F`) was **off**, and the G12's `0x67` blob writes
   `config[0x0B] = config[0x2F] ? config[0x31] : 0` — so the start threshold was being set to **0**,
   which the battery can never fall below. `0x2F` had read 0 in all 28 prior captures.

   Enabling it was the only change made to the coach, and the auto-start that had never fired
   across a whole evening fired within one battery sag: **`19FEDB9C#12FFC801FF00FFFF` — from the
   G12**, ~10 s after the battery crossed the threshold, against `time at start volts` = 10 s.
   **Do not reopen.** The control is on `AUTO GEN START SETTINGS` (screen 28, object 31) and is
   reachable only in AGS mode, so an AES-only coach can be stuck here with nothing on the AES page
   to explain it. Set it once from AGS, then switch back.

   *Original symptom, for the record* (`BUS-BASELINE.md` **PC.38**): mode `EF = 3`, `0x01` = 1,
   house battery below the 52.70 V start-at **continuously for 107 s**, `time at start volts` =
   **30 s**, outside quiet time, `EF` never rewritten, **no start command sent**.

   *Two explanations were offered and both died to the data*: that channel 18 has no start event
   (it does — asserting the level **is** the start, owner-verified with pin 18 lit on
   `G12 OUTPUTS`), and that the panel believed the engine was already running (it did not — `0x01`
   is an **enable** flag, not a run state).

   **A third now exists, and it is `proven-from-code` up to one honest gap** (`G12-CODE.md`,
   "What arms the automatic engine start"; `g12code.start_arming`). The tail of the `0x67` blob's
   `+0x0918` entry, at `+0x18FE`, is:

       config[0x0A] = config[0xEF] in {2,3} ? config[0x55] : config[0x56]   # measured voltage
       config[0x0B] = config[0x2F] ? config[0x31] : 0                      # start threshold

   `0x0A` is the live house-battery voltage (it drifts 52.45–53.10 V across PC.38, which is what
   identifies it); `0x0B` is the threshold, defaulted to 1000 = 50.0 V / 223 = 11.15 V by the
   sentinel routine. **`config[0x2F]` reads 0 in all 28 captures**, 2,000+ frames over twelve
   days, while its own sentinel default is **1** — so 0 is a stored value, not an unset one, and
   the threshold is being written **0**, which the battery can never fall below.

   *The one gap left:* the comparison consuming `0x0A` and `0x0B` is in the G12 application at
   `0x08006000`, so the file shows the threshold being zeroed and the coach shows the consequence.
   Between them the chain is closed; the compare instruction itself is still unread.

   **Also settled by PC.39:** `19FBDA9C#BF02` — the `0xBF` state this project had never observed —
   is the **start** indicator, and the channel-18 **ON** command is **not** in the frame table, so
   the application owns the command while the `0x67` blob owns the configuration and the indicator.

   *Retracted with this:* "the AES logic is not in the MCF". It is. Channel 18 is absent from
   11.23's blob and present in 13.6/14.7 — in the frame table, in the profile defaults
   (`config[0x73] := 18` for AES against `:= 19` for AGS), and in the 48 V parameter set. The
   owner's argument — AES did not exist in 11.x, and Firefly ships only `cli.bin` and `mcf.bin` —
   was right, and the reasoning that dismissed it rested on **two scans that were structurally
   blind**: a `ldrh [rX,#imm5]` scan cannot encode offset 98, and a constant scan misses an
   address formed as `adds r3,#0x62`. Four routes reaching the same wall was four instances of
   one blind spot, not convergence.


### Defect 0a — `Locked by Ign` staleness

0a. ~~`Locked by Ign` has the same staleness as defect 0.~~ **WRONG — refuted on the coach the
   same day it was written** (`BUS-BASELINE.md` **PC.26**). The HOME `EXT` button relabels
   **immediately** when the ignition comes on. The claim rested on "the register is written and
   never read", from a **pool-literal scan** — but a value-slot register is read as
   `DAT_00253D04[slot]`, by index off a table base, so its address never appears as an immediate
   and that scan could not have found the read. *An argument from absence is only as good as the
   guarantee that what you searched for would have been present.* This also withdraws **defect 0's
   explanation** (same step, same scan); `Engine Running`'s staleness stays an owner-observed
   symptom with no mechanism.
   Two instances of this now, from one root — see Finding 56's rule about type-6 switches and
   runtime values.


### Defect 0 — `Engine Running` / `Engine Not Running`

0. ~~**`Engine Running` / `Engine Not Running` on ELECTRICAL only updates on screen re-entry.**~~
   **CLOSED 2026-08-24 — NOT A DEFECT. Do not reopen.** Correct behaviour is confirmed three
   independent ways (ignition on, ignition off, and an AES auto-start — PC.28). The owner
   attributes the original observation to **an early build of a bad firmware**, i.e. to an image
   that is not in this corpus, so there is nothing here to find. Detail retained below only
   because two mechanisms were built on this symptom and both had to be withdrawn. The owner re-tested: on
   ELECTRICAL, turning the ignition on changed the text to `Engine Running` **immediately**, and
   off returned it; it also tracked the van's own AES auto-start. The display path works, so the
   whole diagnosis below is moot. **The last residual is closed too, 2026-08-24 (owner): the
   `Start`-button case works.** That was the one narrow reading left open — that the non-updating
   symptom might be specific to having used the ELECTRICAL page's `Start` button — and it is not a
   defect either. Nothing about this symptom remains open. *Two mechanisms were
   built on this symptom before it was pinned down (see Finding 55); both died to one observation.
   Get an exact reproduction before explaining a fault.* Superseded text follows.

   **(superseded)** diagnosed 2026-08-24, NOT FIXABLE by any same-size edit (`GUICODE.md`
   **Finding 55**).
   Owner-reported. The text is a **type-6 value-match switch** (object 353, display slot 169,
   four candidates: default `Not Running`, 2 `Starting`, 3 `Running`, 4 `Stopping`), and slot
   169's register `0x2003A50C` has **one pool literal and six references, all six stores** — so
   nothing in GuiCode reads it and its only consumer is the **layout walk at compose time**. The
   live-repaint path is driven by the GuiCode bridge blocks that write colour cells; with no
   block, there is no repaint trigger. *The owner's own observation proves the data path is
   fine:* if re-entering the screen shows the right state, the register was already current while
   the wrong text was displayed, which isolates the fault to compose-time selection.

   **On this coach it is two-state, driven by `0x1FBDA` instance `0xFB`** — and `0xFB` is
   **G12 input pin 10, the ignition sense** (`GUICODE.md` **Finding 54a**): pin 10's dispatch
   entry fires four rules, its own `0xAA` pair and the `0xFB` pair, byte-identical apart from the
   instance. So `Engine Running` means exactly *"12V present on input 10"*, and it must always
   agree with the `10 - Ignition(12V)` dot on DEVICES/FAULTS — a prediction checkable on the panel
   with no bus at all.
   The generator branch (`0x1FFFD` GENERATOR_STATUS_1, giving `Starting`/`Stopping`) is gated on
   `0x15FCE` selector `0x01 == 1`, and that selector reads **0 in 910 frames across every
   capture**, so those two states are dead here. No same-size fix exists: the text is
   compose-time, and a live colour indicator would need var 56 to be bridged — the bridged set is
   vars **18-43 and 66-85**, the pin-status vars, and is fixed by hard-coded addresses.
   **General rule this earns:** a type-6 switch is a *configuration* mechanism (floorplan, options,
   the derived battery block and nav rail all use it). Never gate a display on one for a value
   that changes at runtime — use a value widget or a bridged colour cell.



---

## Moved out of `CLAUDE.md` on 2026-09-10 — known firmware defects, owner work items, open threads, foundations

Verbatim, as part of the documentation cleanup (its design note (not published)). Dated statements below describe the state on the date they carry.

### Known firmware defects (not ours to have caused)

0c/0a/0. **Three defects closed, two by retraction** — AES auto-start (a
   setting, not a defect: PC.39), `Locked by Ign` staleness (refuted the day it was written), and
   `Engine Running` staleness (an early build of a bad firmware, not in this corpus). **Do not
   reopen any of them.** Full text, including the superseded mechanisms that had to be withdrawn,
   in `docs/CLOSED-THREADS.md` — worth reading before building a mechanism on a symptom, because
   two were built here and both died to one observation.

0b. **`Shore Brkr Size`'s `−` button transmits nothing — CLOSED 2026-09-07, FIXED AND VERIFIED ON
   THE COACH (`BUS-BASELINE.md` PC.62).** The `−` put `19FED99F#FF96990FFBFFD2FF` on the bus, and a
   5 → `−` → `−` → `+` sequence read back 5, which only happens if the second `−` was silent at
   0 A. Two things the run added: the gate is a **disable-at-exactly-zero, not a clamp** (step 5,
   signed compare against 0, so a size off the multiple-of-5 grid walks straight past and the G12
   stores the negative — found at −245 A mid-test), and **`0x99` is not in the periodic `15FCE`
   sweep**, so read it with a provoked read-back `cansend can1 19FED9C0#FF96990FFFFFD3FF`. The
   number on screen is never the observable: it is fed by `1FF96` var 118, which nothing on this
   coach publishes, so it reads 255 throughout. *(Diagnosis as it stood:)* **RESOLVED FROM CODE 2026-09-05, fix built
   (`GUICODE.md` Finding 68, `variantgates.py`, `fix_variant_gate` op).** It is disabled *by design*:
   `FUN_00247BF8` calls `setVariant(199, 1)` every tick while var 213 reads 0, group 199 has no
   variant-1 binding, and var 213 is `DGN_CONFIG` selector `0x95` — the G12's "AES parameters at
   seed values" flag, 0 on every tuned coach. 11.23 has the identical gate on selector `0x99`, the
   breaker size (a floor at 0 A); 13.6 duplicated `0x95` into that byte; 14.7 dropped the call. The
   fix is one GuiData byte back to `0x99`. *(The entry below is the record as it stood.)*

   ~~**`Shore Brkr Size`'s `−` button transmits nothing — NEW, `observed-on-hardware` 2026-08-24**~~
   (`BUS-BASELINE.md` **PC.32**). Five presses across three sessions, at stored values 0, 5, 35,
   35, 35: **zero frames**. `+` transmits every time, including seconds either side of a silent
   `−` on the same block, so the page is responsive and the finger is in the right place.

   Two hypotheses were raised and both killed. A **bottom clamp** (the owner's point that 0 A is
   an invalid breaker size while pedestals are 15/20/30/50 A) is refuted by pressing `−` at **35**,
   where any floor has headroom. A **G12-side refusal of negative deltas** is refuted by the
   restore: an injected `−45` was applied instantly and read back as 0. The fault is panel-side,
   in the `−` half of **object 929**.

   **Second defect on the same control** — it is also the *non-relaying* variant (selector `0x99`,
   which the G12 stores and forwards to nobody) while its relaying twin object 930 on `0x90` sits
   unattached. One unfinished control, not two coincidences, and consistent with the page having
   been **orphaned** (Finding 53) so neither half was ever exercised.

   **The `−` IS wired — the dig refuted the obvious premise.** All four links are present and
   correct: the `wtype 12` hit region at `(565,165)-(640,245)` is shape-identical to the working
   `−`s and **nothing overlaps it** (all 16 of the page's touch regions were walked); touch id
   **199** is a valid group id against a count of 233; group 199 exists with three emissions; and
   binding **630** (`d9 fe 05 06 0f 99 96 ff ff d2 ff fb`) carries the correct DGN, selector and a
   **−5** payload, byte-identical to its `+` twin apart from the value. *The frame the panel fails
   to send is fully described in the file.*

   **The LCD dig (`CONTROL-BEHAVIOR.md` §5a) says byte 1 `0x80` is a DESIGN SWITCH, not damage.**
   Every write to the per-control variant counter is gated on group byte 1 bits 6-7 == `0x80`, and
   press-cycling *skips* exactly those groups — the two are mutually exclusive by construction.
   `0x00` = the press cycles the variant; `0x80` = something outside chooses it. All seven pool refs
   to the state table were enumerated, so the three write sites are the complete set, and the
   setter has exactly **one** caller (`0x21B002` in `FUN_0021AB88`) taking the variant from a
   runtime record field.

   **The real defect shape: a group declaring a variant range wider than its bindings cover.**
   Group 199 declares `variant_count = 1` and carries variant-0 bindings only, so a counter of 1
   silences it and the press cannot move it back. Group **34** shows the pattern done right —
   externally selected, `variant_count = 2`, seven bindings spanning variants 0/1/2, so every
   counter value fires something. Four groups have 199's incomplete shape (33, 86, 87, 199) and
   **object 929 is the only one reachable on a screen** — 408/409 (`'Patio Awning'`), 862, 103 and
   105 have **no parent of any kind** (Finding 15 template objects). An incomplete control on an
   orphaned page: exactly the class that ships unnoticed.

   **Still not proof — and the power-on half is now answered (2026-09-04, `GUICODE.md` Finding 67):**
   the counter at `0x20006E6C + 199*16 + 0x0A = 0x20007AE6` is inside the `.bss` zero loop
   (`raminit`), so it is **0 from cold** and the group's variant-0 bindings are live until the setter
   moves it. A cold-boot silent `−` therefore needs a setter write nobody has seen, or `[state+0x09]`
   — the phase byte the sender bails on at `0x21B8CC` — which would give identical silence by an
   unrelated route. Settling *which* still needs a live read of the counter; `add_register_readout`
   could show it on screen if its register spec grew a bare-address form.

   *Trap, paid for twice in one session:* `records.object_records` wants **`gs + Screen.table[i]`**,
   not an object index. Handing it an index yields derailed decodes that look plausible enough to
   quote — 115/218/145 records for 7-record objects, geometry of 31503.



1. **Tank-heater diagnostic names: no floorplan populates all four — FIXED AND CONFIRMED ON
   HARDWARE 2026-08-18** (`BUS-BASELINE.md` **PC.18b**; `GUICODE.md` Finding 35). *This entry
   read "Not built or flashed" until 2026-08-20; it was already false the day it was written —
   the flash landed the same day and nothing updated it.*

   Diagnosed from the owner's WD coach (rows 05/08 named, 06/07 blank, all four warmers fitted).
   The `G12 OUTPUTS` dynamic row names are filled by a straight-line block at GuiCode
   `0x002494b2` that gates each name behind **its own hardcoded whitelist of floorplan codes**
   (`r5 = [0x2003a77a]`); the four whitelists are inconsistent, and simulating them over all 14
   floorplans gave a best of **3/4** (WT) and **4/4 for none**. WD was absent from the Grey and
   Black lists.

   **The fix is two 1-byte same-size edits** (`0x002494d8` `04 2d`→`08 2d`, `0x0024951e`
   `09 2d`→`08 2d`), shipped by `tankgates.widen` as 9 GuiCode edits / 26 bytes with GuiData
   byte-identical to stock. Flashed as `mcf-tankheat-v10.bin`: **all four rows populate on WD**
   (PC.18b Result 7), and row 07 tracks the Black Tank option live (Result 8) — which also
   confirmed `0x2003A4B4` as that option's register and that the firmware's *second* gate at
   `0x249528`, deliberately left intact, is re-read per render rather than latched at boot.

   **The trade is real and is now shipping.** The whitelist has no spare slot, so **SY loses Grey
   and WT loses Black**. Invisible on a WD coach, but baked into every build derived from
   `builds/from_stock.py` (step 1) and into the recipe engine's `tank_heater_names` op — so it is
   in reference build v1, confirmed on the coach 2026-08-20 (PC.19). Personal-build only:
   do not hand these images to another floorplan's owner without saying so.


### Owner work items — each carries its status; most are closed, item 0 (the bridge) is live

5. **11.23's floorplan variable slot — CLOSED 2026-08-30, slot 7 CONFIRMED ON HARDWARE** (`BUS-BASELINE.md` **PC.40**: the owner flashed stock 11.23's GuiCode+GuiData pair to the coach — G12/LCD Firmware left at current, the mixed panel ran fine — and the session settled this item *plus* three things it did not ask). The four headlines, all `observed-on-hardware`: **slot 7 is right** (a NETWORK DIAGNOSTICS photo at `0xF5 = 6` matches the slot-7 render on every floorplan-driven row); **the value→code map was off by one** — the run counts from 0 on 11.23 (`0=Ship Mode … 7=3RD/4RD`, pinned by stated-order taps + photographed highlights; `guidata._floorplan_table` now aligns the run base to the tap-handler oracle, and value 8 is *out of range* — an 11.23 panel shows "No floorplan selected" for a 13.6 WD coach, while 13.6 shows **TB** for 11.23's 3WD/4WD=5: the G12 stores a number, each build maps it); **all 8 tap-handler default bursts validated on the wire** (every burst a zero-extra subset of its `floorplandefaults` templates — the set helper is change-gated); and **Service Mode joined across versions** (11.23 asserts selector `0x93` once a second, fn `D1 FF`; it maps to 13.6's cfg offset `0x12` in one G12-stored flag — the stored `1` made the reflashed 13.6 panel boot with Service Mode enabled, exercising PC.36's `+0x12` receive path live). Residuals in PC.40: the boot `F5:=8` re-assertion burst, `0xD8`/`0x15`, and 11.23-only selectors parked in G12 storage.

   **A fifth headline the fix itself surfaced: 11.23 HAS a floorplan-name switch after all** — objects **852/853**, group **235**, a type-6 switch on **slot 7** whose labels are the exact hardware map (`0='Ship Mode' … 7='3RD / 4RD'`, default arm = `'No Floorplan Selected'` — precisely what the panel drew at out-of-range 8). The 2026-08-29 "no name switch" conclusion was **circular**: the off-by-one table made `_find_floorplan_switch`'s oracle unmatchable, and the failed match was read as absence. The mv-0 arm's meaning also **inverts across builds** — 13.6/14.7 carry the anchor text at mv 0 (0 = none) and `Ship Mode` as the *default* arm; 11.23 carries `Ship Mode` at mv 0 and the anchor as default — so `_find_floorplan_switch` now skips the 0-arm **by its label** (anchor text), never by the bare value. Net: 11.23 resolves slot and names through the same switch path as 13.6/14.7, `config.floorplan_slot`'s unpack derivation stands as the independent second mechanism (both say 7), and the Studio picker, `doctor`, `options` and per-floorplan defaults all serve the wire-true map.

   **The variable slot is LOCATED — slot 7, by the static route, 2026-08-30** (`config.floorplan_slot`, delivered). The mechanism, two proven joints and no fitting: the config-unpack's cfg-`0x00` store names 11.23's floorplan register **`0x20039A8E`** (`config.config_offsets`, the Finding 45/59 receive path — the pair the module docstring records as recovered after the `strb`-tag fix), and that register appears **exactly once** in Finding 17's variable pointer table (11.23 `dptr_off = +0x44B60`; entry 7 at GuiData `+0x44B7C`, file `0x6B94C`, type 1). **Validated by reproducing both known answers first**: the identical procedure gives 13.6 → slot 5 and 14.7 → slot 10 — 14.7 being the build where the rejected read-histogram heuristic picked 114 (that heuristic stays rejected; this is a mechanism, not a fit). Corroboration on 11.23: slot 7 is read by 59 type-6 switches whose group match values span exactly the floorplan range 0–8 (+`0xFFFF` default), and the layout replay renders **NETWORK DIAGNOSTICS** (root 17) and **ADDITIONAL OPTIONS** (root 22) differently as slot 7 changes. Wired through: `options.slot_values` writes `{7: value}` on 11.23 (no-op only if the derivation itself fails), `render._floorplan_slot` falls back to the same derivation, so read and write keys agree; pinned by `tests/test_config_offsets.py::test_floorplan_slot_is_derived_from_the_unpack_on_every_build` and the updated options/render tests. A byte found on the way, re-read after PC.40: one switch group carries a match-value-**8** arm (a `G12 #1 OUTPUTS` variant grouped with value 7's) even though **8 is out of the floorplan range** (0–7) — dead-or-defensive content for exactly the out-of-range value a cross-flashed coach presents, not evidence of a ninth floorplan (this line originally read it as 3RD/4RD content; the off-by-one again).

   **The in-coach test ran 2026-08-30 and both sub-questions closed the other way than the code assumed** — full record `BUS-BASELINE.md` PC.40, headline in this item's title. Two standing rules it re-paid: the name pairing that rested on the label run's *position* was the part that was wrong (the handler *values* were bytes and held), and `Ship Mode` at "value 1" plus `3RD/4RD` "unconfirmed at 8" were both artifacts of one off-by-one — the picker offers **both**, at 0 and 7.

4. **AES-page "Low Volts" button recolor — CLOSED, HARDWARE-VERIFIED 2026-09-01.** Shipped as the
   `aeslowvolts` op (capability table above; the reference coach's build log (not published) "The compact AES Electrical page";
   design spec `2026-09-01-compact-aes-electrical-page-design.md` §3.3). The cause stood: cell 45's
   sole writer at `0x0024A176` is skipped in AES mode by the `bls.w` at `0x0024A172`. The fix did
   **not** patch that gate — it appends an un-gated replica of the block onto the per-tick hook,
   calling the firmware's own `setVariant`, so stock code is untouched and the control recolors both
   ways in AES. The earlier `add_low_volts_control` op (records only, no recolor) is superseded.

0. **The PD-inverter bridge — the owner's next hardware project.** He is adding a bridge that
   publishes the coach's inverter/charger onto RV-C, which makes **two chargers**. The full brief —
   which DGNs the two inverter pages feed from (they are *different*, do not assume one feed serves
   both), the panel's own `FUN_0024F1FC` device table that binds by source address, the DC-current
   negation trap, the `0x42` address to claim, and why the commands need no MCF edit at all — is
   **`docs/HARDWARE-VERIFIED.md`**. reference build v5 already pins the three alternator
   filters; that discrimination is **unexercised** until the bridge is live.

   **Three bridge-side results from 2026-09-03** (`BUS-BASELINE.md` PC.52, PC.53, PC.55; synced to
   the owner's `rvc2mqtt` spec the same day): the panel **adopts an inverter on `1FFD4` alone** and
   then sends a *directed* `PRODUCT_IDENTIFICATION` request (`18EA429F#EBFE00…`, ~17 s) that the
   bridge never answers — hence the blank `I/C Model` / `I/C Firmware Rev. ----`; answering with a
   BAM + `0EBFF` packets (`make*model*serial*unit*`) fills them with no MCF change. `Absorb`/`Float`
   feed from `0x1FECC` payload [3..4]/[5..6] at 0.05 V, but the decode is **fenced to 12.0-18.0 V**
   (`decodeguards.py`), so no 48 V value can ever land; lifting that fence is a GuiCode code edit.

3. **What does G12 rule record 59 do? — RECLASSIFIED 2026-08-26; residual is one device, not a trigger.**
   The record is not a config-relay (byte 2 = `0x01`) like the Finding-52 rules; **byte 2 = `0xC1`**,
   which Finding 52 left undecoded. `0xC1` is now decoded as the G12's **periodic-broadcast class**:
   of its **54** records, **52 are the `0x15FCE` config-value sweep** (each carries one selector in
   payload[2]/[3] — the constant `195FCE9C` we see 88,930×), and the two that target other DGNs are
   record **44** (`0x1FACF`) and record **59** (`0x1FED9`). **Record 44 is the live sibling that
   settles the mechanism:** the G12 is its *only* source and it broadcasts **every 5.00 s** (2,049
   frames, `19FACF9C#49440D080D080006`), synthesising selectors `0xF1`–`0xF5` — incl. the floorplan —
   into one frame (`RVC-GAPS.md` §a). So a `0xC1` non-sweep record fires **periodically and
   unconditionally when its feature is present**; it is not option-change-triggered (that experiment
   correctly came back negative — wrong target).

   **So record 59 is a gated periodic broadcast, and its gate is a device this coach lacks.** It
   would emit `19FED99C#FF96[E3]FF[E4]00CF00` — Bath/Cargo light options (`0xE3`/`0xE4`) relayed as a
   `0x1FED9` config write with function `0xCF` to some node that mirrors light state. It fires neither
   in the 5 s sweep (unlike record 44) nor on an option toggle, so it is suppressed by a per-record
   condition — the presence of the `0xCF` consumer — that lives in the **G12 application at
   `0x08006000`** (not the MCF), exactly like the AES trigger. *(The one thing that is NOT record 59:
   `BUS-BASELINE.md` earlier read "zero `19FED99C` from the G12". Across all captures there are two,
   both in pc39 — `FF96790F0100D1FF` and `FF967D0F0200D1FF`, selectors `0x79`/`0x7D` fn `D1`, the AES
   watchdog's own writes, NOT record 59's `E3`/`E4`/`CF` — and now attributed: they are records #2/#3 of table `0x62`'s kind-`0x09` **AES-start group**, all four of which fired within 2 ms, `MCF-FORMAT.md` §9a.3.)*

   **Residual (the only open part):** what `0xCF` means and which device consumes it — unanswerable on
   this coach (the gate never opens) and not in the MCF. It needs either a coach that ships the feature
   or the `0x08006000` app dump; there is no cheaper experiment, and the option-toggle watch is retired.
   *Original negative retained below for the record:*

   *The sweep paid for itself elsewhere* — see the offset→selector confirmation below and
   `GUICODE.md` Finding 23's PC.34 block. Original brief retained:

   *What is already known, so the dig does not start cold:*
   - The record is `0x102984` = `d9 fe c1 14 ff e3 96 ff 00 cf 00 e4` — a `0x1FED9` transmit
     template, group `0x96`, mask `0x14`, so `FF 96 [E3] FF [E4] 00 CF 00` with payload[2] and
     payload[4] taking the **live values** of option selectors `0xE3` and `0xE4`. Both are
     booleans (`E3` = 1, `E4` = 0 on this coach), so the frame today would be
     `FF 96 01 FF 00 00 CF 00`.
   - Its shape is the **group-`0x94`** layout of `RVC-GAPS.md` §a, not the `0x96` layout the
     panel uses — so the byte roles may not be selector/value at all.
   - Function `0xCF` occurs in **no other record** and in **none** of the 782 captured `1FED9`
     frames. The G12 has **never** been observed sourcing `1FED9` in eleven captures.
   - Byte 2 (`0xC1`) is **not** the trigger: registry records and record 44 share it and both
     broadcast periodically, while this one does not.

   *The experiment, and it is cheap.* `E3`/`E4` are the Bath/Cargo light options
   (`GUICODE.md` Finding 23). Run `candump -L can1` on the bus, have the owner toggle those two
   OPTIONS rows on the panel, and watch for **`19FED99C`** — the G12 sourcing a config write,
   which has never been seen. If it appears, the payload says what `0xCF` means. If it does not,
   the trigger is something else and the rule may be dead code for a product this coach lacks.

   *Do not confuse it with:* grepping captures for the substring `FED9` returns nine hits from
   `18EF8070` that are `0001FED9` inside a proprietary **payload**, not DGN frames. Match the
   CAN id.

   *It does NOT bear on open thread 8* (which of `E3`/`E4` binds to channel 25 vs 26) — record 59
   names no channel.

2. **GO POWER! tab shows with no controllers if the count is unset — latent firmware defect**
   (`GUICODE.md` **Finding 41**, 2026-08-18). The tab on screens 14/24/35 is gated on slot 39
   (`Go Power! Controllers`): 0 hides it, anything else shows it. But the config-payload clamp at
   `0x247c48` maps **any count > 5 to 1**, not 0 — so an `0xFF` "unset" byte would show the tab
   with no hardware. **ANSWERED 2026-08-18 by Finding 42** — the count comes from the
   floorplan-selection burst, and `D7` defaults to **0** on WA/WD/WT/SY/TB (1 on the Generator
   plans), so WD writes 0 and the tab is correctly hidden. The clamp defect is therefore **latent**
   and the 1-byte fix (`movs r2,#1` → `#0` at `0x247c4e`) stays unbuilt: nothing sends an
   out-of-range count. Closed unless one is ever observed.


1. **AES "Start At" default voltage — ANSWERED 2026-08-18, and CONFIRMED ON HARDWARE 2026-08-24**
   (`GUICODE.md` **Finding 43**; `BUS-BASELINE.md` **PC.36**). The owner pressed the ELECTRICAL
   page's **`Reset to Defaults`** button and the panel emitted a run of **eight** absolute
   `D1 EA` sets — the AGS/AES parameter profile — including **`0x31 := 1020`**, i.e. **51.0 V**,
   from the template at **`0x002599D0`**: Finding 43's exact constant at Finding 43's exact
   address, put on the wire by a route that reads no disassembly. The other seven are `0x2B`
   5632, `0x2C` 2048, `0x0C` 60, `0x0D` 1060, `0x0E` 1800, `0x0F` 1800, `0x16` 115, stored at a
   regular 24-byte stride (set + read-back) from file `+0x29221`. **So those eight are the
   factory defaults for the profile, and the button that restores them is decoded.**
   It is **already 51.0 V**, so the owner's target needs no change at all. The whole AES parameter
   profile is a run of static `0x1FED9` templates in GuiCode's pool, emitted by one handler at
   `0x24E500`; `0x31` (start at voltage) sits at `0x2599D0` carrying `fc 03` = 1020 × 0.05 V.
   Every value matches PC.4 Result 4's AES column, which also closes that section's open note
   ("the MCF decode should show where these profiles live").

   **It is global, not per-floorplan:** one `0x31` template in the whole section, and no
   floorplan handler writes it — floorplan selection sets only the mode (`0xEF`). So WA/WD/WT/TB
   all default `EF = 3` (AES Single Output) and all start at 51.0 V; **SY** is the exception, and
   it is a *mode* exception (`EF = 0`, none), not a voltage one.

   *If a different value is ever wanted:* a **2-byte same-size** edit to the u16 at file offset
   `0x29258` (50.0 V = `e8 03`, 52.0 V = `10 04`) — and it would move every AES floorplan at once.
   Bounds worth carrying: only the **AES** profile is in the pool — the AGS profile (start
   12.20 V) is not, so its source is still unknown; user ± adjustments are **deltas** (PC.11), so
   this is the baseline, not the live value; and which action id invokes the handler was not
   pinned.

   *The original brief and the dig that led here, kept for the record:*

   **Original brief — AES "Start At" default voltage: where does the pre-user value come from,
   and can it be changed to 51.0 V?** Raised by the owner 2026-08-18. Not investigated; recorded with the
   evidence that already bears on it so the dig does not start cold.

   *What is already known, and it points AWAY from an MCF edit:*
   - The ELECTRICAL "Start at" / "Stop at" setpoints are **value slots 272 and 274**, resolving
     to SRAM `0x20039D52` and `0x2003A22C` (both type 3). `render._VOLTAGE_SLOTS` maps them for
     the preview; the coach's current values are 51.0 / 53.0 V (`build/coach-wd.json`).
   - **Finding 19:** every option/config register is written **once, from a received CAN config
     payload** — never from a file constant. If Start-At follows that pattern, the live value is
     bus-delivered and there is no MCF byte holding it.
   - **PC.11:** the panel writes thresholds as **signed DELTAS, not absolute values**, so it
     adjusts relative to whatever the G12 already holds — which is another sign the authoritative
     copy lives in the G12, not the panel.
   - **`MCF-FORMAT.md` §10, "answered elsewhere":** floorplan and option *state* live in **G12
     non-volatile storage** and reach the display over RV-C; the MCF is a schema, not an instance.
   - **`RVC-GAPS.md` §3:** the AGS parameter *limits* were searched for and not found in the
     firmware — a related negative.

   *The AGS/AES parameter **limits** are now found too — `RVC-GAPS.md` §3, closed 2026-08-24.*
   They are two-sided clamps in the G12's table-`0x67` blob (`docs/G12-CODE.md`), not a table:
   start-at **[50.0, 54.0 V]**, stop-at **[52.0, 54.8 V]**, time@start **[10, 300]**, time@stop
   **[600, 3600]**, max-run **[60, 115]** — plus a relational rule that `stop_at >= start_at + 8`
   or the G12 rewrites `stop_at`. `g12code.AGS_LIMITS` / `clamp_ags_parameter`.

   *So the likely answer is: the default is in G12 NVM, an MCF patch cannot change it, and the
   practical route is to set it once on the panel or over the bus.* ~~That is a hypothesis, not a
   finding — it has not been checked.~~ **Step (a) is now checked and the panel side is closed —
   `GUICODE.md` Finding 59, 2026-08-25.** The sole writer of `0x20039D52` is `FUN_00247BF8`, which
   decodes it as a sentinel-guarded signed-halfword field (`[r4,#0x58]`, `(raw+1)>>1`, `-1`=unset)
   of the RAM config struct passed in as `r0` — the Finding 19 config-payload path, confirmed for
   this slot specifically. There is no MCF byte holding the default, so a GuiData/GuiCode patch
   cannot change it. What remains open is **(b/c) where the value ultimately comes from**: it is
   bus-delivered, so setting it is a bus write (PC.11 deltas), unless the G12 seeds an NVM default
   worth a G12-section dig.

   *Concrete next steps, in order:* ~~(a) confirm what writes `0x20039D52`~~ **(a DONE, Finding 59:
   the `FUN_00247BF8` config-payload path);** ~~(b) it is the CAN path, so the remaining panel-side
   work is a bus write~~ **(b DONE — the bus-write recipe is `BUS-BASELINE.md` "The AES 'Start At'
   bus-write recipe", 2026-08-25).** Selector `0x31`, `0.05 V/LSB`; the panel sets it with a signed
   `int16` **delta** in a `D2 FF`+`D2 EA`+`D3 FF` trio (`19FED99F#FF96310F <±deltaLE> D2FF` …),
   proven frame-exact in PC.39 (1020→1058, +2/press). Set a target by replaying N such trios and
   watching the `195FCE9C#31` sweep; 51.0 V = `FC 03`. **Absolute set is unconfirmed — treat as
   delta-only (the PC.11 trap).** Cleanest force-to-51.0: switch to AES mode (`0xEF := 3`), which
   re-derives `0x31 → 1020` absolutely. **And start-at is inert unless `Low Volts` (`0x2F`) is
   enabled** — the watchdog reads `0x0B = 0x2F ? 0x31 : 0` (PC.39). **(c DONE — and the first answer
   was WRONG; the owner corrected it. There IS a patchable MCF default: `GUICODE.md` Finding 60,
   2026-08-25.)** The dig first scanned the **G12** section and concluded "no", on the false premise
   that the G12 seeds the default. It does not — the **panel** does, transmitting `0x31 := 1020` to
   the G12 from a **GuiCode CAN-frame template** (Finding 7 pool) as an absolute `D1 EA` set in the
   config-defaults burst, observed byte-identical on the wire in **five captures** (pc3, pc8, pc36,
   pc37×2). The template is at GuiCode file `0x0199D0` (abs `0x002599D0`); its value field `fc 03` =
   1020 = 51.0 V is at file `0x0199D8` (abs `0x002599D8`). It is **build-specific — 1020 in 13.6, 244
   (12.20 V) in 14.7, ABSENT in 11.23** — which is exactly why it is in the MCF: AES is new in 13.x,
   so its defaults shipped with the 13.x firmware. **A same-size 2-byte edit at `0x002599D8` changes
   the default the panel establishes** (target = V/0.05 LE, within clamp [1000,1080]; e.g. 52.0 V →
   `10 04`); high-confidence, not yet flashed. The G12's own sentinel seed (600) is only a
   cleared-NVM fallback, and pc39's frameless 244→1020 was the G12 *recalling* a value the panel had
   pushed earlier — not a G12-resident constant.

   **THREAD CLOSED 2026-08-25.** (a) panel *decode* path is read-only (Finding 59); (b) bus-write
   delta recipe pinned (`BUS-BASELINE.md`); (c) **the default IS a patchable MCF byte** — GuiCode
   `0x002599D8`, a 2-byte same-size edit (Finding 60). The answer to the owner: Start-At is 51.0 V by
   default; to hold a *different* value **permanently**, patch that GuiCode byte, or set it ad-hoc
   over the bus with the delta recipe — and it only bites the auto-start once `Low Volts` (`0x2F`) is
   enabled.

   *Trap, stated because it is the obvious first move and it does not work:* scanning the sections
   for the constants 510/530 (decivolts) or 51/53 is **not evidence**. GuiData alone contains 350
   occurrences of `u16 510` and 3958 of `u8 51`; the corpus is far too dense for a bare constant
   scan to mean anything. Two of this project's eleven confident-but-wrong assertions came from
   exactly that kind of reconnaissance artifact. Find the *writer*, not the value.

### Open threads — all resolved

**All eight of the original threads are RESOLVED**; the full record of each, with the reasoning and
the negatives, is `docs/CLOSED-THREADS.md`. In brief: chrome colour source (GUICODE Finding 18), the
phantom channel 40 (PC.17), the option -> widget binding (Findings 16/17), per-floorplan option
defaults (Finding 19, settled negative), record insertion (`relocate.py`), content-render extents
(Finding 14), and the option -> `0x15FCE` selector join (Finding 23, retired for 13.6 by PC.34).

**2. The control -> status-variable link — RESOLVED 2026-08-26.** The pin -> lit widget chain is
proven end to end and editable at four joints (`MCF-FORMAT.md` §5.4c, `statusbind.py`). The last
question — what pairs *this* button's command with *that* status variable — is a **settled negative
with the rule recovered**: the pairing is neither a stored cross-reference nor encoded in the
indicator widget (both searched; object 295 commands channel 44 but var 86 is drawn by no widget at
all), it is the shared **G12 output instance**. A control commands channel `n`; the paired status
var is `18+(n-1)` / `66+(n-24)` (`statusjoin.instance_status_var`). `predicted_status_slot` derives
it from the control's own command channel and reproduces every known pairing on 13.6 *and* 14.7; the
strict `xfail` is lifted (`test_predictor_reproduces_every_known_pairing` now passes). `set_channel`
still needs `move_display_list` because they are two distinct fields — but the indicator target is
now *computable* (from `instance_status_var(new_channel)`), so that move could be automated. Full
write-up: `MCF-FORMAT.md` §10 item 11a.
*Automated 2026-09-10:* `set_channel`'s default retargets the control's own indicator records at
the new pin's cells and moves no variable; `move_display_list` keeps the coach-proven v9. See
`MCF-FORMAT.md` §10 item 11a.
### Foundations complete — do not re-derive

Container format (proven from `cli.bin`), the GuiData tables and screen/widget records, the
floorplan table, the control→channel join, 100% byte coverage of all three MCFs. The panel
is an **STM32F7** (image base `0x00210000`, LTDC `0x40016800`, 800×480, framebuffer
`0xC0000000`, GuiData in QUADSPI at `0x90000000`). GuiCode is **native Thumb-2, not
bytecode** — a five-entry function-pointer vector at header `+0x0C`; no interpreter exists.
The **font/glyph mechanism** is complete (`GUICODE.md` Findings 3 and 22): 20-byte descriptor
`<u8 first><u8 last><u16 base><u16 fallback>`, glyph bitmaps 4bpp/1bpp under a **per-row
vertical-repeat RLE**; `glyphs.py` renders any font pixel-faithfully, so **do not re-derive
glyphs by eye** — four rendering claims were made and retracted before the format was right,
and a fifth ("the bitmaps are corrupt") was the RLE being missed.

**Glyphs are portable as data — `glyphenc.export_glyph` / `cli glyph-export`, 2026-08-21**
(`GUICODE.md` **Finding 46**). A glyph exported to JSON (`rows` as hex coverage, plus `x`, `y`,
`profiles`, `flag`) and re-encoded reproduces the record **byte for byte** on every glyph in all
three builds, so a project file can carry artwork with no rasteriser and no reference image.
Three things had to be right, and two of them are traps:
- **The RLE is written greedy**, matching 2,875 of 2,884 shipped glyphs. The old identity
  encoding was legal but unique in the corpus to our own builds.
- **Byte `+0x0e` bit 0 is the MEASURER's flag, not RLE.** The blitter skips it (row `r` reads bit
  `r+1`) while `metrics._GHdr` reads the same byte as `FUN_002518F8`'s bearing flag. So dropping
  it is invisible to a pixel round-trip **and still changes `text_width`** — a pixel comparison
  cannot catch it, which is why the encoder carries it explicitly.
- **The 10 ink-profile (kerning) bytes are NOT derivable from the bitmap** — a defensible
  negative with four independent legs (no contiguous banding fits: 102/2884 by DP; no fixed row
  window fits any font cohort: zero solutions; 630 values fall outside the glyph's own ink;
  cell-space banding 93/2884). They come from Firefly's font tool's source outline, which is in
  neither image. `_profiles` remains an approximation (6/1115) used **only** when authoring new
  artwork; an exported glyph carries its real ones. Do not "improve" `_profiles` by fitting.

The **record decoder** is complete and is not a heuristic: `records.py` replays `FUN_00252CB0`
and is validated by the object-table boundary oracle on **every object in all three builds**
(1421/1076/1200, zero derailments). Use it — `guidata.py`'s older boundary-fitted screen walk
is the thing it replaced.

