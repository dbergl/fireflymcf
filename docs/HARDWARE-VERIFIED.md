# Hardware-verified capabilities — the full record, and the bridge project

Moved out of `CLAUDE.md` on 2026-08-25, **verbatim**. Every entry below was proven by flashing an
image to the coach or by putting a frame on its bus. `CLAUDE.md` keeps the capability table and the
hard constraints; this file keeps *how each was proven*, which is what makes the constraints
believable and what stops them being re-derived.

---

## The capabilities, in full

**Renaming / rendering** (`MCF-FORMAT.md` §6): the renderer reproduces leading spaces
literally and left-aligns, so padding goes on the **right** — *for a left-aligned label*. A
record whose R49 h-align byte is **2 centres each line by its PADDED width**, so trailing pad
shifts the text and `patch.repad(align="center")` is the correct mode there (see the reference build v3 entry
in the reference coach's build log (not published); owner-observed 2026-08-21). A same-size rename **cannot add
a character** — `'Star\nLink'` (9) will not fit an 8-byte field; `'Bath Fan'` wrapped on its
*space*. An icon is a character; `'['` on a font that lacks it draws the fallback glyph.
**A *longer* label is now possible via the relocator** — hardware-confirmed 2026-08-18,
`BUS-BASELINE.md` PC.18 — so that limit binds `patch.set_label`, not the tooling.

**Remap** (spec §11–12, `BUS-BASELINE.md` PC.13–16): the `Bath Fan → Starlink` job is
complete and flashed. The panel **does not cache bindings** — a two-byte GuiData edit
changed what it transmits (instance 38, not 40). The panel only ever sends **command 5
(toggle)**; the G12 holds and alternates state. **Command and status are separate tables** —
a binding-only remap gives a working button with a dead indicator; the indicator and the
`G12 OUTPUTS` diagnostics row read the *same* status variable, so lighting the button makes
the old channel's row report the new one — a trade-off (`move_display_list`), not a bug.
`dimmable_channels` = channels ever sent command 0 (*set brightness*), reproducing the pin
sheet's `DIM`/`LATCH`; **command 0 marks a light**, not 21 (21 = "act while held", shared
with motors). Levels above `0xC8` are "use remembered level" sentinels (master memory). The
**G12 acts on any RV-C instance** regardless of the pin sheet (owner's `rvc2mqtt`), so a
remap's destination needs no enabling. Source `0x82` on the bus is the owner's automation.

**Adding a new button — DONE, flashed and confirmed 2026-08-17** (`MCF-FORMAT.md` §6.0a,
`src/fireflymcf/newbutton.py`, `tests/test_newbutton.py`). `build_home_button` clones a boxed
button, attaches it to the HOME root, gives it its own command group, and relabels it — all gates
green. On the coach it toggles pin 15, tracks its status colour, and **repaints in place**. Three
things had to be right, each found by a hardware round-trip:
- **Command**: a **single** command-5 binding. A two-binding group `[toggle, off]` is a
  *smart toggle* — the panel picks off-vs-toggle from the commanded instance's tracked state, and
  an instance with no status defaults to "believed on" and only ever sends OFF (v5). Binding order
  does **not** decide this (Water Pump is `[5,3]`, Tank Heater `[3,5]`; both work). The wire
  command is binding **byte[4]**; byte[3]=`0x0a` is metadata.
- **Donor**: must carry a `value_slot` field on **both** box and caption. Field *presence* is
  header-gated, so a same-size edit cannot add one. Water Pump (295) has none — it inherits one
  from its **parent wtype-5 container** (obj 989/990, `value_slot 160` == its `fillA` index 160),
  which a generated attach record does not supply, so a 295 clone only repaints on page re-entry
  (v6). Donor 279 carries both.
- **The live-redraw invariant**: `fillA` mode-7 index == box `value_slot` == caption `value_slot`,
  all one cell. The colour table *is* the value-slot table (Finding 18), so index N and slot N are
  the same cell. All 9 live buttons obey it; the 6 that don't are unattached **template** objects
  (247, 278-281, 1038) whose `value_slot` is a **type-1 config gate**, not a status cell. v7 set
  the slots to 32 while `fillA` stayed 145 — the box repainted in place, the caption vanished
  until a full render. v8 made all three equal and works.
- **Correction**: `FUN_0021a664` (LCD firmware) is the **CAN-event/switch matcher** (24-byte
  records, timers), *not* the GUI redraw dispatch. An earlier note here claimed otherwise. The
  widget redraw lives in GuiCode; it has still not been located, so the invariant above is
  `inferred-from-data` (corpus survey + three hardware round-trips), **not** `proven-from-code`.

**Binding any pin to any button — `src/fireflymcf/statusbind.py`, CONFIRMED ON HARDWARE
2026-08-18** (`MCF-FORMAT.md` §5.4c, `tests/test_statusbind.py`, `BUS-BASELINE.md` PC.18). The
`set_state_colour` off-colour repoint (build v9) works on the coach: the pin-15 button is now
visible when off. The status chain is proven end to end and `var` is **one number**
throughout — receive-filter entry index == slot index == GuiCode's `2*var` immediate (checked on
all 136 bridge blocks). Three joints are same-size edits, all exposed with premise assertions:
`set_filter_instance` (1 B, entry `+4`), `index_cell` (4 B, `+0x44BC8`), `set_state_colour`
(2 B — repoints a GuiCode `ldr`'s `imm8` at another colour master, so the masters themselves are
untouched and no other cell moves). `bind_pin(data, pin, off_colour=…)` orchestrates and returns
the table index to write into the box record's `fillA` **and** `value_slot`.
`cli pins <image>` prints the map. **The one hard constraint:** GuiCode's per-var blocks are
straight-line code, so the set of 46 bridged vars is fixed — a var with no block cannot be given
one, and the module refuses rather than inventing a cell. On 13.6 that barely binds: all 44 pins
are already watched, 16 of the 26 uncommanded pins are ready now, 7 more need one spare index
(16 are free), and only pin 42 is unreachable. Off colours are **per-var, not per-button**:
every ON branch loads the same `(0,174,239)` cyan; the off masters differ — `(65,81,163)` button
blue, `(39,40,98)` *the quadrant panel colour* (invisible when off — this was the pin-15 bug),
`(192,178,60)` olive. States are keyed by their `cmp` value, not branch order: 3-way blocks
compare 4 before 1, so "the first branch" is the fault red, not the on colour.

**Control behaviour — tap / hold / ramp / momentary, `proven-from-code` 2026-08-19**
(`docs/CONTROL-BEHAVIOR.md`, `src/fireflymcf/gestures.py`, `cli gestures`). A control's
*gesture* model is two fields this project had labelled `unknown`, and the code that reads them
is in the **LCD Firmware**, not GuiCode. Binding byte `+0x03` is a **phase mask** — `1` press,
`2` short-tap release, `4` while held, `8` release-after-hold (`FUN_0021B8B4`'s `tbb`
@`0x0021B95C` and its four arms). Group entry byte 0's **high nibble is the hold threshold in
seconds** (`mla r0,#1000,r3,r0` @`0x0021BD86`; only 1/3/5 occur), its low nibble the variant
cycle length; byte 2 holds three 2-bit **repeat-rate** selectors indexing `{0,250,500,1000}` ms
(`0x0021F2A2`). So a light is `tap→toggle(5)`, `held→ramp(21)`, `release→stop(4)`; the awning is
momentary (`press→RET off`, `held→EXT on` repeating, `any release→EXT off`); Water Pump is a
plain latch. **Independently checked, not fitted:** object 9's own caption says *"Hold the enable
button for 3 seconds"* and its group's nibble is 3; and across all three builds command 4 (stop)
fires only on `release-after-hold` and command 21 (ramp) only on `held`. Two side effects — the
**variant selector is a runtime counter**, not a stored byte (§5.2 open question 9, closed), and
the record→`state+0x208` copy §8.3 said was unlocated is a **371-entry hit-region table** at
`state+0x707C` filled by `FUN_00256B84`. **Two dispatch classes**: touch ids below the group
count go to this machine; ids at or above it are GuiCode action ids with their own timers off
`state+0x204` (CLEAR EEPROM = id `0xB5F`, `+0xBB8` = 3000 ms @`0x0024EC54`).

**Subscribing the panel to a NEW DGN — `statusbind.retarget_filter`, CONFIRMED ON HARDWARE
2026-08-22** (`MCF-FORMAT.md` §5.4a). The panel can be made to receive a DGN it has never
subscribed to by **editing four bytes** of one receive-filter entry. Nothing else: no code
change, no table growth, no relocation, image length unchanged.

*Why four bytes is enough.* The variable table is not only matched against at runtime, it is
what **registers** the subscription at boot — `FUN_0021BE50`'s per-entry loop calls
`FUN_0021A618(entry_u32 & 0x3FFFF)` at `0x0021C03E` for every entry, bounded by the descriptor
count. Rewrite the DGN bytes and the new DGN is registered on the next boot.

*The round trip.* Var 32 was pointed at **`0x1FEA0` GPS_DATE_TIME_STATUS**, mode 1 of
`payload[3]` (day of week) — a DGN absent from all three builds and from every capture. Var 32
is both a bridged var and the `G12 OUTPUTS` row-15 painter, and pin 15 is released in this build,
so the row was already blank and nothing was lost. Driving the frame from the Pi, row 15 turned
**green** on one day-of-week value and **off** on another, exactly as the compare branches
predicted. That confirms the whole chain end to end on a DGN Firefly never shipped support for:
registration, the 18-bit match, extraction mode 1 at a chosen payload byte, the slot→cell bridge,
and the paint.

*The cap is real and it fails silently.* `FUN_0021A618` dedupes and refuses past **83** distinct
DGNs (`cmp r2,#0x53` @`0x0021A62A`), returning 0 — no error, no log, no boot failure, the DGN
just never arrives. 13.6 registers 66 from this table, so headroom is ~15–17 (four further
registration call sites are uncounted). `retarget_filter` refuses to cross it rather than
letting a build ship a dead subscription.

**A LIVE INDICATOR on an APPENDED subscription — recipe op `add_aes_batt_heat_status`, CONFIRMED
ON HARDWARE 2026-09-22.** The "Batt Heat" button shows BMS status DGN `0x18FF7070`: the body
tracks the **battery heater** (byte 2 bit 0) and a **red wavy-lines glyph** (font slot 23 char
`'2'`) appears in the top-right while the battery's internal **heater contactor** is closed (byte 1
bit 2). Build v31; all four combinations were driven from the Pi with `18FF70C0#…` at 5 Hz and all
four appeared on the panel, the two indicators independent of each other.

*What this does and does not establish.* The RECEIVE half is proven end to end. The DRAWING half
of v31 was **wrong in a way this test could not show**, because a held state looks correct however
it was painted: the owner found that *transitions* were broken. Enabling the heater wiped the
'Batt Heat' caption and nothing redrew it, and disabling the contactor left the red glyph on the
glass — each until the page was navigated away and back. The cause is that the panel repaints only
the **arm a switch selects**, and v31 drew the button with TWO switches, so each left whatever the
other owned untouched: the body arm painted over the sibling caption, and the glyph switch's "off"
arm drew nothing at all. v32 folds both bits into ONE cell (`bitcombine`) and drives ONE four-arm
switch whose every arm repaints the whole button — opaque capsule, then caption, then the glyph if
that state has one — so no state can leave another's pixels. **Test a state indicator by
TRANSITIONING it, not by holding each state.**

*v32 CONFIRMED ON HARDWARE 2026-09-22.* Flashed `mcf-13.6-BattHeatStatus-v32.bin`, then drove six
held-15-s states from the Pi (`18FF70C0#70 <b1> <b2> 41 00 00 A5 00` at 10 Hz, over the real BMS's
2-s off frame) so that every edge was crossed: off -> heater on -> heater + contactor -> contactor
opens -> heater off -> contactor only -> released. The owner confirmed on the panel: blue/cyan body
tracks the heater, the caption survives the enable, the glyph appears with the contactor and
clears when it opens, all without leaving the page. `add_aes_batt_heat_status` is
`hardware_verified` again.

*The chain the v31 flash does prove, which two earlier builds got wrong.* Appended entry ->
receive slot -> **per-tick GuiCode routine** -> a fresh private display cell
(`readout.allocate_fresh_cells`) -> the cell-table index a wtype-6 switch names as its
`value_slot`. v28 and v29 pointed the switch straight at the *variable* index, which indexes the
**cell** table instead — so they read an unrelated constant cell and would never have moved.
Renders could not catch it (the renderer injects by `value_slot`, i.e. at the broken joint);
`tests/test_batt_heat_status.py`'s `test_switch_watches_a_cell_fed_from_both_variables` now does,
pairing routine to variables through the literal pool, and
`test_every_arm_redraws_the_whole_button` refuses the v31 repaint shape.

*Also confirmed: the receive filter really is source-address blind on the bus, not just in the
decompiler.* The frames were sent from SA `0xC0`, never from the BMS's own `0x70`, and the panel
tracked them — a live cross-check of the 2026-08-23 `proven-from-code` finding below, and the
reason this test needed no impersonation of the BMS.

**APPENDING a NEW receive entry — `appendfilter.append_filters` / recipe op `append_filter`,
CONFIRMED ON HARDWARE 2026-09-22.** The table can be *grown*, not merely retargeted, so a new
subscription costs no existing one. Probe build "v30 Probe New DGN" (v27 + two ops) appended a
299th entry for **`0x1FEA0` GPS_DATE_TIME_STATUS**, mode 1 of `payload[0]` — var 298, slot
`0x20008254` — and showed it on HOME through `add_register_readout`. The panel **booted normally**,
read **255** with nothing publishing, then followed injected `19FEA0C0#2A…` → **42** and
`19FEA0C0#07…` → **7**, each held a minute. A 60 s census first confirmed no `0x1FEA0` on `can1`
and SA `0xC0` free, so the number could only have come from the injection.

*The four edits, and why they are all that is needed.* `FUN_0021BE50` reads the entry count from
the tag-0x41 descriptor as `((desc[1] & 3) << 8) | desc[3]` (`0x0021BF8A`-`0x0021BF9E`) and then
**cross-checks it against the u16 at GuiCode header +0x20**, which must match and be `<= 0x3E8`
(`0x0021BFE6`) or the GUI never starts. So: the new entries go at the table's end, the descriptor's
count *and* size are bumped, the following tag-0x42 table shifts down, and the GuiCode header count
is bumped. The growth is absorbed by the ~52 KB zero padding arena, exactly as
`optionrow._grow_bindings` does for the binding table, so nothing at or after the pinned image
sub-container (GuiData+0x10000) moves — verified byte-identical, LCD Firmware included.

*Why the SRAM was never the limit.* `FUN_0021C760` writes the slot at `state+0x144+2*idx` for
`idx > 0xFF` and the deadline at `state+0x914+4*idx` (`0x0021C9B0`-`0x0021C9DA`), with u16 index
arithmetic; the struct is laid out for the firmware's full 1000 variables, and no instruction
addresses var 298's slot or timer as a fixed field. The real budget is the registry:
`FUN_0021A618` still drops silently past **83** distinct DGNs, and 13.6 registers 66 from the table.

*Corollary — appending does not make a var drawable.* A widget's `value_slot` indexes the
**display-cell** table (`+0x44BC8`), not the variable table, so naming var 298 as a `value_slot`
would read an unrelated cell. A new var reaches the screen only through a routine that copies its
slot into a cell (`add_register_readout`), which is how this probe displayed it.

*What this does NOT cover:* the **display** side is still bounded — a received value is only
painted if one of the **46** GuiCode bridge blocks copies its slot to a cell, and that set is
fixed by hard-coded addresses. So a new DGN can **re-source an existing indicator** (which is
what this probe did) but cannot add a new readout without inserting GuiCode. Also untested:
growing the table (tier 2), and the transmit side — 5 all-`FF` binding stubs sit in single-entry
groups 5/9/15/16/61 and look like the same-size route to sending a new DGN, but none has been
flashed. Feasibility survey: the 2026-08-22 artifact, `builds/from_stock.py` step 11 (behind
`FIREFLY_PROBE=filter-dgn`, off by default so the shipped reference image is unchanged).

*It also retroactively rescued the GPS negative.* The three 2026-08-21 injection runs concluded
"the G12 ignores `1FEA0`", with two caveats left open: the payload layout was inferred, and there
was **no positive control**. This probe closes both for the frame itself — the panel matched
those exact frames and extracted a sensible `payload[3]`, so the DGN number was right and the
bytes were receivable. What remains open is only whether the *G12* would accept them from an
unclaimed source address.

**The receive filter is blind to WHO sent the frame — `proven-from-code` 2026-08-23**
(`MCF-FORMAT.md` §5.4a). `FUN_0021C760` matches on the 18-bit DGN and nothing else (`ubfx
r2,r2,#8,#0x12` @`0x0021C930`): the CAN id's **source address** (bits 0-7) and **priority** (bits
26-28) are never read, in that function, in its caller, or anywhere in the entry format — there is
no field to hold an address. So a DGN is accepted from *every* node, and where the entry also
carries the `0xFF` sentinel at `+4` (66 of 13.6's 298 entries) the last frame to arrive wins. The
only discrimination available is the masked **payload** compare at `+4`/`+5` — a test of frame
contents, not bus identity, which is why "the instance filter" understates it.

`statusbind.pin_filter_instance` turns an open filter on in **one byte**, asserting the DGN, the
existing `+5`, and that the extraction mode is one the `tbb` dispatches; it refuses to re-point a
live filter or to write `0xFF`, so it only ever narrows. Recipe op `pin_filter_instance`.


**Transmitting a NEW DGN — `newbutton.set_binding_dgn`, CONFIRMED ON HARDWARE 2026-08-23.** The
other direction, and it is cheaper still: **one byte**. The Starlink button's single binding was
moved from `1FEDB` to `1FEA0` (`db` → `a0`; byte 1 is `fe` in both and byte 2's DGN bit 16 is
already set), payload and gesture untouched. Holding the button put

    19FEA09F#1DFFC805FF00FFFF

on the wire — **byte-for-byte what the build predicted, CAN id included**, four times across four
manual holds with no repeat burst.

*Three things that confirms beyond "it transmits".*
- **A binding's payload is two little-endian u32 words** — the wire bytes are
  `raw[7,6,5,4,11,10,9,8]` (`newbutton.binding_payload`). That was derived by matching three
  stock bindings to captured `19FEDB9F#` frames; here it **predicted** a frame that had never
  existed. It is plain byte order, **not a per-DGN layout**, which is exactly why the runtime
  will emit a DGN it knows nothing about.
- **The runtime supplies priority 6 for an unfamiliar DGN.** The binding carries no priority
  field, so this was explicitly flagged as unknown before the flash ("match on the DGN, not the
  whole id"). The whole id matched. Closed.
- **The gesture machinery is DGN-agnostic**: one frame per hold, no repeats, so the held-phase
  mask and `repeat_held_ms = 0` work on a binding that is not a DC_DIMMER_COMMAND_2 at all.

*The cost, and it is the second observable:* pin 29 stops switching. `set_binding_dgn` therefore
gates on `allow_capability_change` exactly as `patch.set_channel` does — v11 and v18 both shipped
having silently lost a real control this way, and the build's capability gate caught this edit
too (narrowed to expect only `(28,5)`, with the reason stated, rather than disabled).

*A claim to NOT carry forward.* The feasibility write-up called the five all-`FF` binding stubs
"pre-wired transmit slots". **They are not.** Groups 15/16/61 are referenced by no object, so a
stub still needs a control wired to it — the same work as allocating a group. Groups 5 and 9 are
referenced, but by a full-screen 800×480 touch target and a light grid. They are not the
allocator pool either (`unused_command_groups` returns 164/169/229). The confirmed route is
repointing an existing control's binding.

Both probes live in `builds/from_stock.py` (steps 12 and 13) behind `FIREFLY_PROBE=filter-dgn` /
`tx-dgn`, **off by default** so the shipped reference image stays byte-identical — 1,069,668 B, sha
`3b30bb31…` at v5 (`4ad8a4e4…` was v4, before the charger filter pins).

**Hold-to-toggle — CONFIRMED ON HARDWARE 2026-08-19.** `gestures.make_hold_to_toggle` turns a
single-entry command-5 group into "hold N seconds to switch, either way" in **three same-size
bytes** (group `+0x00` hold nibble, binding `+0x03` phase mask -> `PHASE_HELD`, group `+0x02`
held-repeat -> 0). Applied to the pin-29 Starlink button by `builds/from_stock.py` step 9 and
verified on the coach: 3 s on, 3 s off, a tap does nothing, holding past the threshold does not
re-toggle, and pin 15 (left a latch in the same build) still switches on a tap. There was **no
shipped precedent** — no group in any corpus build sends a DC_DIMMER command on the held phase —
so this was mechanism-derived until the round-trip settled it.

**Two pins now, and that is what makes it general — CONFIRMED ON HARDWARE 2026-08-21** (reference build
v4). One confirmed instance of a composition with no shipped precedent leaves
open whether it worked for a reason particular to that button. It did not: the same three bytes
on **pin 28**, a different group, on a button built by a different route (`add_button` rather
than `wire_button` on a cloned shell), behave identically — and they do so while pin 28's stock
**group 94** (the lock/unlock slide-out pairing with pin 27) is left intact beside them, which
is the runtime confirmation that the right group is being picked. Treat `make_hold_to_toggle` as
a general primitive rather than a one-off.

**Where a glyph lands vertically — `proven-from-code`** (`CONTROL-BEHAVIOR.md` §7a). A `wtype 11`
label's glyph draws at `box_y0 + ((box_h - font[0x07]) >> 1) + glyph[0x0c]`: the centring block is
the **font cell** (`ldrb r1,[r3,#7]` @`0x00255C30`, centre arm @`0x00255D3C`, no guard against a
negative result), and the blitter then adds the glyph's **y-bearing** (`ldrb r1,[r3,#0xc]`
@`0x00251138`). The two cancel because every icon font sets `y = (cell - height) / 2` — zero is the
commonest delta in slots 23/24/25/9/19 across all three builds. Calibrated on the CLIMATE setpoint
chevron (cell 72, glyph 28, bearing 22, box 140-195 → 154), where the formula, `render.py`'s
independent bitmap-centring and `CLIMATE.jpg` all agree. **Authoring rule:** a new glyph needs
`y = (cell - height) / 2`, unless its record uses v-align 1 (top), where the bearing is a plain
offset from the box top. The Starlink logo was authored with `y = 0` in a 128-cell font and drew
33 px above its button; slot 27's four Go Power! glyphs look like a counter-example but are drawn
v-align 1 (obj 1230), and 19 of that slot's 23 glyphs follow the convention.

**Widget anatomy — three independent records** (`MCF-FORMAT.md` §6, three hardware probes):
a control is a **draw record** (`<x><y> 01 <font> ff … <len> <text>`; probe 1 moved the
caption via `0x97233`), a **touch/hit region** (opcode `0x0C`, render type 12; probe 2 grew
it via `0x97256`), and a **visible box** (opcode `0x04`, render type 4, quad `[x, width,
height, radius]`; probe 3 widened it via `0x9720B`). Visual, touch and text are separate —
moving one leaves the others behind.

**Render architecture** (`GUICODE.md` Findings 9–11): render state is a 120-slot × 224-byte
widget array at SRAM `0x200301B0`. `FUN_00256C54` dispatches on a type byte at `record+0x8A`
(11 types: icon, shapes, rounded button, container/list, text/value, touch target).
`FUN_00252CB0` decodes a record as one contiguous stream (all three "cursors" are one at
`state+0x6BA4`): 8-byte header (byte0 low-6 = type, byte7 = flags), presence-masked
geometry, mode-tagged colours (mode 3 = inline BGR, 6/7 = palette/table index). On/off
appearance is two style-field pairs selected by a flag; the flag is sourced from the RV-C
value slot. **The variable table is the RV-C receive filter** (§5.4a): each 8-byte entry
matches a DGN + instance and writes a `u16` slot; that decode is `proven-from-code`.

**Palette** (`MCF-FORMAT.md` §5.5): the `0x8F7E6D5C` sub-container at `GuiData +0x10000`
holds a `u32` BGR colour palette at `+0x10`. It is **live but partial** — editing it recolours
placeholder/indicator elements, not the main chrome (probe 5). The chrome's colour source is
a different mechanism: **mode-7 cells written by GuiCode's theme-init** — RESOLVED 2026-08-15,
see open thread 1 and `theme.mode7_palette` (`GUICODE.md` Finding 18).

**Compact AES Electrical page — DONE, flashed and confirmed 2026-09-01**
(ops `swap_aes_electrical` + `add_aes_low_volts_trigger`, once carried as the standalone
`builds/aes-compact.ffproj.json` and now exercised inline by `tests/test_aes_electrical_build.py`
and `tests/test_aes_trigger.py`; full narrative in the reference coach's build log (not published), "The compact AES Electrical
page"). In AES mode the Electrical
page now shows a **compact frame mirroring the AGS compact layout** (built by rebuilding object
829 in place with the new reusable `relocate.replace_object_records` primitive, then cloning the
AGS frame's children with their donor geometry preserved), and the AES **Low Volts** indicator is
made **live where stock firmware freezes it in AES**. Five things each needed a hardware round-trip
(see the build log for the round-by-round): the freeze is defeated by an **un-gated appended bridge**
(hook `0x0024EE3C` → routine `0x00259BB4`) that faithfully replicates the stock `0x2F` paint block
— reading the un-frozen receive slot `0x2000801C` and calling the firmware's own `setVariant` via
fn-pointer `[0x20039908]` (not a byte-poke) to refresh the group-129 variant counter, so the
recolour box **arms and disarms both ways in AES**; the settings **⚙** navigates by **tap** through
the action dispatcher by repurposing the orphaned `0x7DE` leaf (a guarded 2-byte GuiCode edit,
screen 14→28) — a bare touch region with no visible capsule/glyph is invisible, and a touch region
absolutised away from its capsule is untappable; the engine control must be the real AES **Engine**
control (object 353, no generator-hours), not the AGS **GEN** control (352) relabelled; and the
enable pill must carry the AES enable **action `0x7E2`** (routes through the "Notice:" page), not
AGS group 128 (skips it). The AGS path stays **byte-identical** by construction (donors are cloned
or referenced, never written). Owner-confirmed on the coach: AGS unchanged; compact AES page with
live House/Chassis voltages (House read the coach's real 48 V), working gear-tap, both-way Low
Volts, the AES Engine control, and the Notice-first enable flow.

**A second battery, driven by fresh grown cells — the first tier-3 op on hardware** (`coach48v`,
`add_voltage_battery`; v25, verified 2026-09-12): the coach 48 V "House" pack now sits beside HOME's
stock chassis 12 V battery. It clones donor 443, GROWS two new `+0x44BC8` cell-table indices
(665/666) for its colour and gauge level — unprecedented on hardware until this flash — and drives
them from a per-tick GuiCode routine (green→red at 50.4 V, fill 48.0–54.8 V) sourced from
`DC_SOURCE_STATUS_1` instance 1, reading the live receive-fed cell 209 for the number. The **live
repaint** was the coach's teaching: a battery is drawn by an enrolled wt5 **container** that watches
a voltage cell and repaints its whole child (number AND fill); the House container was cloned from
the chassis template so it watched the chassis cell 205, and retargeting it to 209 made the whole
House battery track live. The ELECTRICAL coach battery was never a repaint gap — its containers
already watched 209 — but a **scale** one: its fill cell is driven on a 12 V scale a ~53 V pack
saturates, so repointing it onto the 48 V cell made the existing live repaint show a moving level.
Owner-confirmed 2026-09-12: number, fill and colour update live on both HOME and ELECTRICAL. This
retires the tier-3 caveat that kept every fresh-cell op `hardware_verified = False`.


---

## The PD-inverter bridge — the owner's next hardware project

This sat under "hardware-verified facts" but is a forward-looking brief, not a verified fact. It is
referenced from the owner work items, now in `docs/CLOSED-THREADS.md`.

**Why this came up, and it is the owner's next hardware project.** He is adding a bridge that
publishes the coach's inverter/charger onto RV-C, which makes **two chargers**. The alternator DC
charger (SA `0x80`, charger instance `0x31`) owns `1FFC7`, `1FEA3` and `1FF99`, all three watched
by open entries (vars 115/260/261) — so the ALTERNATOR SYSTEM page would start showing whichever
charger transmitted last. reference build v5 pins those three (3 bytes + checksum, image
still 1,069,668 B, sha `3b30bb31…`). **Not yet confirmed:** with one charger transmitting the
correct outcome is *no visible change*, which only proves the compare byte is right; the
discrimination is unexercised until the bridge is live.

Two things worth carrying for that build-out, both from the file and the captures:
- **The bridge's display route exists, and six fields of it are now CONFIRMED ON HARDWARE
  2026-08-23** (`BUS-BASELINE.md` **PC.20** — this document's first *transmit* entry). Synthetic
  frames from the Pi light up the **PD Inverter** page: Enabled (`1FFD4` byte 1), DC Voltage /
  Current (`1FEE8`), AC Out Volts / Freq (`1FFD7` byte 0 = `0x41`, output) and AC Input Volts
  (`1FFD7` byte 0 = `0x01`, input). Two traps a bridge must get right: **the panel NEGATES DC
  current** (it computes `1600 − raw×0.05`, proven by two runs at opposite signs), and the
  input/output distinction is byte 0's sub-field, so a bridge sends *two* `1FFD7` frames per line.
  **`Firmware Rev.` too, since 2026-08-23** — seven of eight fields (`GUICODE.md` **Finding 48**).
  Only `Inverter Fault` is left, and its requirement is known (DM_RV with `payload[1]=='B'` and
  `payload[3]==` slot index); it stays unsent because the alternator shares those vars.
- **The OTHER inverter page feeds from entirely different DGNs — do not assume one bridge feed
  serves both** (`GUICODE.md` Finding 49, observed PC.29 2026-08-24). Object 17's
  `Inverter/Charger Status` block reads `1FFD7` for AC out, but **`1FFFD` DC_SOURCE_STATUS_1
  instance 1** (the *house battery*) for its DC pair and **`1FFCA` CHARGER_AC_STATUS_1** for AC
  in — not `1FEE8`, and not `1FFD7`'s input nibble. Consequences the coach showed: with one load
  running, the PD Inverter page read **−25 A** while object 17 read **0 A**. And `AC Input Volts`
  sits at `---V` because nothing on this bus sends `1FFCA` at all.
  **The DC field is not broken — it measures a different thing** (owner, PC.30). The
  **alternator (SA `0x80`)** publishes `1FFFD` instance 1 at 2 Hz and acts as a **virtual BMS**,
  reporting **net house-battery current, inclusive of every source** — so it can never mean
  "inverter load" however well it is decoded. It reads 0 A whenever charge in and draw out
  cancel, which is what PC.29's single sample caught; PC.30 has it live across −7 A … +8 A,
  quantised to whole amps. The field is `1FFFD` bytes **4-7** (32-bit, 1 mA, −2,000,000 A offset);
  read as a 16-bit at `[4:6]` the same bytes give 37888 / 38888 / 35888, not a current at any
  scaling.
  **Do not fix this by publishing `1FFFD` instance 1 from the bridge** — the alternator already
  owns it and the receive filter is blind to source address, so the two would fight, last writer
  wins, and the *voltage* would flap too. The clean route is `statusbind.retarget_filter`
  (4 bytes, confirmed 2026-08-22) pointing var 106's entry at `1FEE8`; that changes what fills the
  var, not which var the readout reads, so Finding 51's second edit is not needed — **and `1FEE8`
  is already among the table's 66 registered DGNs** (`statusbind.registered_dgns`), so it costs
  nothing against the 83-DGN registration cap. Not built. Note the extraction mode and the
  panel's DC-current negation (PC.20) both have to be right, and Finding 49's var→display
  extraction for that cell is itself unconfirmed.
- **The page is NOT fed by the variable table — it has its own parser, `FUN_0024F1FC`**
  (`GUICODE.md` **Finding 48**, `proven-from-code`). It keeps a **two-entry device table** of
  56-byte structs at `0x2003A8B8`, adopts a free slot when a `1FFD4` INVERTER_STATUS frame arrives
  whose `payload[0]` is the slot index (1 or 2), stores that device's **source address**, and
  thereafter accepts everything from it. So this parser *does* bind by SA, unlike the receive
  filter, which never reads it — the two mechanisms coexist and must not be conflated. Its `1FFD7`
  arm branches on `payload[0] >> 4`, which is why AC in and AC out coexist from one DGN.
  `Firmware Rev.` is a **byte-position hack**: the `0EBFF` arm wants TP.DT packet 5 starting
  `05 'v'`, so the product-ID string must carry `vN.MM` at **index 28**.
- **THE TRAP, and it cost an hour: objects 1418 and 1419 are twin inverter pages with identical
  labels.** 1418's eight value cells each have a GuiCode literal; 1419's have **none**; the coach
  draws **1418**. Analysing 1419 yields a confident, wrong "this cell has no writer" for every
  field. The control that catches it is that known-good cells (AES setpoints, slots 272/274) *do*
  carry literals — a negative about a cell means nothing without one.
- **Linear capstone sweeps are not trustworthy on GuiCode.** The section interleaves code and
  literal pools and a sweep desynchronises on every pool; three wrong negatives this session came
  from that. Use the Ghidra projects (`ghidra/projects/guicode136`, `find_reg_write.py`,
  `find_cell_writer.py`) for anything that has to be exhaustive.
- **The panel does device discovery.** It polls `Request PGN 0FEEB` (PRODUCT_IDENTIFICATION)
  broadcast *and* addressed to `0x42`, and asked our probe address to identify itself within
  seconds of it appearing. Real responders answer `Make*Model*Serial*Unit*` over BAM, terminator
  included (three examples in PC.20). Claim **`0x42`** — that is where the panel's own
  `0EA42`/`0EF42` bindings expect the PD inverter.
- **Commands need no edit at all.** All seven charger/inverter command DGNs (`1FFC5`, `1FFD3`,
  `1FFC4`, `1FF95`, `1FEBE`, `1FF87`, `1FFD0`) address **instance 1**, which nothing on this coach
  claims. **Four of the seven are now `observed-on-hardware` (`BUS-BASELINE.md` PC.23):** the G12
  is the RV-C speaker — the panel writes a `0x1FED9` config selector, the G12 stores it and 2-10 ms
  later **broadcasts** the command with instance `0x01` in payload byte 0. `0x82` → `1FFD0`,
  `0x86` → `1FF87`, `0x92` → `1FF95`, `0x88` → `1FFC4`. They are PDU2, so there is **no destination
  address** — "who is it sent to" does not apply; claim instance 1 and the frames are yours.
  **The relay table is decoded — `GUICODE.md` Finding 52, `src/fireflymcf/g12rules.py`,
  `cli g12-rules`.** G12 table `0x62` holds 12-byte rules: `<u16 DGN lo><u8 flags><u8 mask><8-byte
  payload template>`, the template in the panel's own `[7,6,5,4,11,10,9,8]` order, a masked byte
  naming a config selector the G12 substitutes. Every field is a **same-size byte edit whose blast
  radius is that byte plus the G12 section's checksum** — and since the G12 section is
  independently flashable, a command change ships as a **G12-only build** that never writes the
  GuiCode/GuiData pair. That is the lowest-risk build shape available in this project; nothing
  else here avoids the paired reflash. Payload[0] (record byte 7) is the **instance**, `0x01` on
  every shipped charger/inverter rule. Not yet flashed. Have the bridge claim charger/inverter instance 1 and the panel's existing controls
  drive it. Groups 68/69 are two-variant toggles — variant 1 enable, variant 0 disable, tap-release.
- **What is NOT reachable:** showing both chargers' operating state. One `1FFC7` entry exists, and
  a second pointed at the bridge would write a slot nothing on screen reads.



---

## Moved out of `CLAUDE.md` on 2026-09-10 — the capability table as of 2026-09-10, the hook chain, and the constraints

Verbatim, as part of the documentation cleanup (its design note (not published)). Dated statements below describe the state on the date they carry.

### Hardware-verified capabilities — what can be done, and what it costs

Every row was proven on the coach. **`docs/HARDWARE-VERIFIED.md` holds how each was proven**;
below is the table and the constraints that bite.

| capability | tool | confidence |
|---|---|---|
| rename a label, move/restyle an element, recolour, change a font or icon | `patch.py` (`set_label`, `set_position`, `set_fill_color`, …) | flashed |
| grow a label past its field width | `relocate.py` | PC.18 |
| repoint a G12 channel | `patch.set_channel` + `move_display_list` | PC.13-16 |
| add a working button to a page | `newbutton.build_home_button` | 2026-08-17 |
| bind any watched pin to any button's indicator | `statusbind.bind_pin` | PC.18 |
| give a control a tap / hold / ramp / momentary gesture | `gestures.py`, `cli gestures` | `proven-from-code` + two pins |
| hold-to-toggle (3 s each way) | `gestures.make_hold_to_toggle` | pins 29 **and** 28 |
| put a tap and a hold on one control | `holdregion.split_hold_region` (two regions) | PC.22, v7/v8 |
| subscribe the panel to a DGN it has never seen | `statusbind.retarget_filter` — **4 bytes** | 2026-08-22 |
| narrow an open receive filter to one instance | `statusbind.pin_filter_instance` — **1 byte** | `proven-from-code` |
| transmit a DGN the firmware knows nothing about | `newbutton.set_binding_dgn` — **1 byte** | 2026-08-23 |
| change what a G12 rule broadcasts | `g12rules.py` — a **G12-only** build, the lowest-risk shape here | not yet flashed |
| **grow the GuiCode section** (append authored ARM Thumb + a literal pool, then detour a single-caller `bl` into it) | `growsection.grow_section` + the `follow_channel` recipe op (`followchannel.resolve`/`build`/`detour_edit`) | **HARDWARE-VERIFIED 2026-08-31** — the coach flashed and ran a grown-GuiCode image (the light→pump follower), so the flasher accepting a grown *GuiCode* section is now proven, not the open question it was. This is the project's first authored-machine-code output that runs on the coach |
| **add a genuinely NEW OPTIONS row** (not a repurposed one): a cloned wtype-6 checkbox on a chosen config selector + the follower re-gated to read that selector's status slot | `add_option_follower` recipe op (`optionrow.build_config_toggle`/`append_row` + `followchannel`) | **HARDWARE-VERIFIED 2026-08-31** (`BUS-BASELINE.md` PC.45, `mcf-experiment-v9.bin`) — "Pump Link" on selector `0xEE`: checkbox fills on tap, drives the pump, persists across reboot, Heat Pump left stock. First *new option* on the coach |
| **create a new panel "register" from a bus value** (a var-table receive-filter entry) | `guidata.variable_table` + read at `followchannel.status_slot_addr` | mechanism proven; its design note (not published). **The slot base is index-split**: var idx ≤ 255 → `0x20007FC8 + idx*2`, idx > 255 → `0x20008000 + idx*2` (`FUN_0021C760` `0x0021C9B0`-`0x0021C9C4`). The flat `base + idx*2` is a **trap** for idx > 255 — it caused the PC.44 v8 failure. Our own routine reading the slot sidesteps the 46-bridge-block paint limit |
| **expose a var-table entry's slot address as a handle** (reuse an existing subscription) | `register.resolve_register` recipe op | **tooling-verified**; reuse-existing only. Consumes a named DGN subscribed in the variable table, names its slot address, and exposes it as a `{"ref"}` handle any later op can consume. See its design note (not published) and plan. Appending a brand-new var-table entry is future work (hardware-unverified) |
| **watch any register for edges and drive a channel with gate conditions** (no on-screen display) | `register.follow_register` recipe op | shares the **hardware-verified v9 follower routine** with `follow_channel` and `add_option_follower`. Watches ANY register (by slot address) for an edge, drives a channel, gated by ANY register condition(s). See its design note (not published) and plan. **Coach check warranted the first time a genuinely NEW watch register is used** (a register not previously watched by the shipping follower instances). The on-screen-readout path remains future work (hardware-unverified) |
| **replace an existing GuiData object's record stream in place** (shrink or grow, at its own index) | `relocate.replace_object_records` | **tooling-verified, reusable beyond this feature — and now the foundation of two coach-verified ops (below).** Overwrites an object's `count`+records at its own table index (so a switch that selects it by contiguous band stays valid), computes the signed `delta`, runs `relocate._apply_downstream_shift`, reseals, and re-runs all five relocate gates. This is what makes an in-place rebuild of a *direct-content* switch candidate possible — `relocate.relocate` alone is insert-only. See its design note (not published) and its design note (not published) (Task 1) |
| **replace the AES Electrical page with a compact frame, reusing the stock all-in-one layout as a settings sub-page** | `aescompact.py` (`swap_electrical_to_compact`/`build_settings_screen`/`wire_settings_nav`) + `swap_aes_electrical` recipe op (`aesswap`) | **HARDWARE-VERIFIED, firefly-13.6, 2026-09-01** (5 flash/verify rounds — the reference coach's build log (not published)). Object 829 (the AES-mode Electrical body) is rebuilt as a **faithful structural mirror of the AGS compact frame (object 830)** using **preserved container wrappers** — donor geometry composes correctly because it stays flow-relative inside its own container, rather than being flattened/absolutised — carrying: House/Chassis voltage gauges (objects 443/440), the compact cluster (a clone of object 315), and the Inverter block (object 1421, gated on slot 113); Solar is omitted. AGS path **byte-golden by construction** — only object 829 (via `replace_object_records`) and the stranded screen-31 root are touched; no AGS object is created, modified or renumbered. AES-specific retargets inside the cluster clone: the settings **⚙** gear navigates by **TAP through the ACTION dispatcher** — a guarded, same-size 2-byte GuiCode edit at runtime `0x0024B96A` (file `0x1B1EA`: `movs r3,#0xe`→`#0x1c`, screen 14→28) repurposing the orphaned action leaf `0x7DE`, with the touch region stamped `0x7DE` and left at donor-relative geometry so it stays aligned with the visible capsule; the engine control is the **real AES Engine control, object 353** (touch groups 13/14, status `value_slot` 169 → band 526–529, Start/Stop indicators 167/168 → shared objects 249/251) — **not** the AGS GEN control (352) — plus an invisible flow-gated spacer (an empty wtype-11 label; wtype-9 is flow-exempt and can't substitute) restoring 352's vertical rhythm since 353 carries no gen-hours line; the enable pill's touch id is `0x7E2` (the AES enable ACTION, which routes through the "Notice:" page and then the 3-second-hold Warning) instead of AGS group 128 (`0x0080`, which skips the Notice). The all-in-one AES content is cloned out first and rebuilt into the stranded screen-28 root (object 31) as the settings sub-page. its design note (not published), its design note (not published)–3.2 |
| **chain several authored per-tick routines on the one GuiCode hook** (any mix of `add_option_follower` / `follow_channel` / `follow_register` / `add_aes_low_volts_trigger` in one recipe) | `followchannel.hook_state` / `chain_from_hook`; no new op — the growing ops chain by themselves | **HARDWARE-VERIFIED 2026-09-01**, `build/mcf-experiment-v10.bin`: follower + Low Volts bridge both work through hook → bridge → follower → `FUN_00247BF8` |
| **send the Inverter capsule's gear to `INVERTER / CHARGER SETTINGS`** instead of the read-only `PD Inverter` readout | `retarget_inverter_gear` op (`aescompact.retarget_inverter_gear`) — one guarded 2-byte GuiCode edit | **HARDWARE-VERIFIED 2026-09-02** (v12). Action `0x7F7`'s leaf at `0x0024C842`: screen 39 → 14. Object 1421 is the id's only carrier and draws on BOTH compact frames, so the AGS capsule follows **by design**; screen 39 is left with no route |
| **expose the AES Low Volts arming state as a live, recoloring control** (stock GuiCode freezes its display in AES) | `aeslowvolts` op (`add_aes_low_volts_trigger`) | **HARDWARE-VERIFIED, firefly-13.6, 2026-09-01** — toggles both ways in AES, confirmed on the coach by switching AGS↔AES to force a repaint. An un-gated appended bridge (hook `0x0024EE3C` → routine `0x00259BB4` → tail-call `FUN_00247BF8`) is a **faithful un-gated replica of the stock `0x2F` block**: it reads the un-frozen `0x2F` receive slot `0x2000801C` and — calling the firmware's own `setVariant` via fn-pointer `[0x20039908]` (r0=0x81, r1=`!armed`), **not a byte-poke** — refreshes the group-129 variant counter *and* writes recolor cell 45 (`0x2003A814`), so the AES page's Low Volts box (a verbatim clone of 315's Triggers/Low-Volts recolor box, `value_slot 45`/child 285) recolors and toggles both ways, matching the AGS behavior. Surfaces a control stock firmware hides in AES rather than editing the freeze gate itself. its design note (not published), §3.3 (Mechanism B, as revised on the coach) |
| **feed an EXISTING readout's cell from any register, past a stock decode fence** (no widget authored; the routine above targets the stock cell and the stock per-tick stores that would overwrite it are replaced by `nop`, each guarded as a proven store to that cell) | `add_register_readout` with `cell_slot` + `silence` (`readout.existing_display_cell`/`silence_writers`; `register.resolve_register(var_index=)` for `0x1FECC`'s two same-filter entries) | **HARDWARE-VERIFIED 2026-09-05** (v18, `absorb-rows`/`float-rows`, `BUS-BASELINE.md` PC.60): the `Absorb`/`Float` rows read dashes with nothing publishing and 57.6/54.0 V from an injected `0x1FECC` — values the stock fence can never pass — fed from vars 206/207 with the panel's own `(raw+1)>>1`. The one ordering fact: the hook chain runs *before* `FUN_00247BF8`, so its config-byte copies at `0x247C34`/`0x247C3A` had to be silenced or they win every tick |
| **stop the G12 broadcasting `1FFB7` TANK_STATUS for a tank instance** (so a different RV-C sender can own it) | `suppress_tank_status` recipe op (`tankbroadcast.py`); `src/fireflymcf/tankpatch.py` for an arbitrary user-supplied MCF | **HARDWARE-VERIFIED 2026-09-07 (v22, `BUS-BASELINE.md` PC.62)**: zero `19FFB7` in a 12 s capture against ~48 in every earlier one, with `18BFC19C` and `19AAB79C` untouched — and then an injected `19FFB7AF#002238FFFFFFFFFF` from a foreign SA drove HOME's FRESH gauge to **61%** (34/56), `000D64…` to **13%**, while unclaimed tanks read `--%`. So freeing an instance for another RV-C sender works end to end with no GuiData or GuiCode edit in the path. The rule engine exposes `[r4,#8]`/`[r4,#0xC]`/`[r4,#0x10]` = activate / deactivate / is-active per rule index, pinned by rule #93: activated at `+0x18F6`, deactivated at `+0x19AC`, and that is the LPG broadcast the coach is seen starting and stopping with the `0xEF` mode write. The edit NOPs the activating `blx r3` — 2 bytes per instance, in the **independently flashable** G12 section, records left intact so the revert is one byte-pair. **Instance 1 (black) needs no patch at all**: it is gated on `cfg[0xEC]`, the Black Tank OPTIONS row, re-read every tick — `19FED99F#FF96EC0F0000D1EA` silences it and `…0100D1EA` restores it (absolute `D1 EA`, both already in the captures), though that lever also *hides* the HOME BLACK gauge where the patch does not. Instances 0/2 have no gate anywhere and are patch-only |
| **pin a type-6 switch to one arm regardless of its slot** (three same-size u16s: `child_base`, `count := 1`, `match[arm] := 0xFFFF` — the firmware's default-arm rule) | `pin_switch_arm` recipe op (`switchpin.py`) | **HARDWARE-VERIFIED 2026-09-05** (v18, `rows-permanent`, PC.60): object 17 record 11 → 303; the `Absorb`/`Float` rows were present straight after the flash and after a power cycle, where stock hid them after every flash. Refuses an arm whose match value another switch reads |
| **put a state-gated mark in a corner of an existing button** (a wtype-6 switch appended as the host's last record over a one-character arm and an empty arm, both flow-neutral) | `add_corner_mark` recipe op (`cornermark.py`) | **HARDWARE-VERIFIED 2026-09-06** (v21, PC.61). Every piece is coach-proven separately — the wtype-6 two-arm switch (v16's Pump Link button, v18's pinned `Absorb`/`Float` rows), `replace_object_records` (v11's compact AES page) and `install_glyph` (the Starlink logo) — and the composition is now proven too. **Three rules it enforces, each paid for once on the coach:** a **visible label MUST declare a box** (v20 copied `_flow_anchor`'s two-coordinate shape, which is for records that never draw, and the panel clipped the glyph to a zero-width box — one column of pixels; `render.py` does not clip text to the record box, so the preview was identical and only the layout walk's `(x0,y0,x1,y1)` showed it); the arms are **flow-neutral** (every text wtype commits its coordinates as the new flow origin and that carries back out to the PARENT, so an absolutely-placed mark drags the host's siblings — it moved HOME's `Tank Heater` 467 px), and the **`0xFFFF` default arm must be at the BASE** or a no-match draws nothing. `verify` re-walks the page in both switch states, refuses if anything but the mark moves, and — after v20 shipped a mark clipped to one column — refuses if the **placed box** is smaller than the glyph. A visible label MUST declare width and height; `_flow_anchor`'s two-coordinate shape is for records that never draw. `render.py` now reproduces it: `FUN_002559A0` clips wtype-11/13 text to its box **inclusively**, so `x0 == x1` leaves exactly one column (`GUICODE.md` Finding 69, `render._text_clip`). A **wtype-0** single character is drawn by a different routine and is NOT bounded (HOME's `V`, CLIMATE's `°`, both 0×0 and both fine on the coach) — so "the panel clips text to its box" is true for 11/13 and false in general. Both axes are clipped, so a collapsed box draws its glyph's top-left pixel and usually nothing. **And the record's own box is the WHOLE clip** — `GUICODE.md` **Finding 70**, 2026-09-06: only a wtype-7 or wtype-14 record can narrow the enclosing clip (`FUN_00256C54` cases 7/`0x0E`, gated on record flag `0x200`), the base rectangle is the whole 800×480 panel written once at GUI init, and **no wtype-7 or wtype-14 record exists in any of the three builds** (every object decodes; identical wtype sets). So `_text_clip` is exact, not approximate — pinned by `tests/test_render_bytes.py` so a build that grows one fails a test. That also retired a wrong note: `FUN_00251BB4` is a drawn-extent accumulator (it *unions* into `0x2003A994`), not a container clip. Getting there fixed a second defect: **Finding 37's line-advance rule had shipped DEAD** — `_effective_line_height` referenced a `metrics` `render` does not import, and a bare `except Exception` swallowed the `NameError` on every call (`docs/RENDER.md`) |
| **show ANY register as a live number on ANY page** (a display cell, a per-tick copy routine on the hook chain, an authored directive+readout object) | `add_register_readout` recipe op (`readout.py`: `allocate_display_cell`, `resolve`/`build`/`detour_edit`, `author_readout_object`) | **HARDWARE-VERIFIED 2026-09-04 (v17)**, tier-2 byte cell — `builds/golden-v22-anchor.ffproj.json`'s `minutes-home` op (the reference coach's build log (not published) "Experimental v17" -> "### v17 FLASHED AND VERIFIED, 2026-09-04"; `docs/BUS-BASELINE.md` PC.59) shows the Auto Off minute byte (`DGN_CONFIG` selector `0xEE`) on HOME via display cell 219 (`0x20039f51`, tier 2) and a routine chained ahead of `aeslowvolts`/`pumpauto`/`pumplink`; also confirmed the sentinel-to-dashes width-overflow rule (`GUICODE.md` Finding 65) on hardware for a byte cell. `add_register_readout.hardware_verified` is `True` by default now, but **tier 3 (fresh SRAM) remains unverified and reports so per instance** — a coach photo of one tier does not vouch for an address nothing has ever touched (its design note (not published) item 7) |

**GuiCode-growing ops CHAIN on the per-tick hook (2026-09-01, `followchannel.hook_state`).**
`add_option_follower`, `follow_channel`, `follow_register` and `add_aes_low_volts_trigger` all detour
the one `bl` at GuiCode+`0xEE3C`; they used to pin the append point to stock's `0x00259BB4`, so any
two of them in one recipe refused each other. Now a routine appended onto a detoured image
tail-branches to the routine the hook currently reaches, and the hook moves to the new one:
hook → newest → … → `FUN_00247BF8`. Each routine stays byte-identical to its proven form apart from
that branch target, and `chain_from_hook` walks the result. **HARDWARE-VERIFIED 2026-09-01:**
`build/mcf-experiment-v10.bin` (the reference coach's build log (not published)) runs on the coach with both the follower and
the AES Low Volts bridge working through the chain — and it is the **currently-running image**,
the first single build carrying every proven feature (reference build v8 + Pump Link + compact AES page). A
preprocessor merging the routines was considered and rejected: it would emit new machine code no
coach test covers.

**The constraints that bite, and each has cost someone something:**

- **The DGN registration cap is 83 and it fails SILENTLY.** 13.6 registers 66, so headroom is
  ~15-17. `retarget_filter` refuses to cross it rather than shipping a dead subscription.
- **The display side is bounded and cannot grow.** A received value is painted only if one of the
  **46** GuiCode bridge blocks copies its slot to a cell, and that set is fixed by hard-coded
  addresses. A new DGN can **re-source an existing indicator**; it cannot add a readout.
- **A same-size rename cannot add a character** — `'Star\nLink'` (9) will not fit an 8-byte field.
  An icon *is* a character; `'['` on a font that lacks it draws the fallback glyph.
- **A control is three independent records** — draw, touch region, visible box. Moving one leaves
  the others behind.
- **One command-5 binding, not two.** `[toggle, off]` is a *smart toggle*: the panel picks from the
  commanded instance's tracked state, and an instance with no status defaults to "believed on" and
  only ever sends OFF. Binding order does not decide this.
- **The live-redraw invariant**: `fillA` mode-7 index == box `value_slot` == caption `value_slot`,
  all one cell. A donor must carry `value_slot` on **both** box and caption; field presence is
  header-gated, so a same-size edit cannot add one.
- **Repointing a control is two edits in two sections** — the binding byte in GuiData *and* the
  GuiCode `ldrsh.w` immediate. Doing only the first gives a control whose arrows change one setting
  while its number reports another.
- **`set_binding_dgn` and `set_channel` cost the pin its switching**, so both gate on
  `allow_capability_change`. Two builds shipped having silently lost a real control this way.
- **A new glyph needs `y = (cell - height) / 2`** unless its record uses v-align 1. The Starlink
  logo was authored `y = 0` and drew 33 px above its button.
- **The panel only ever sends command 5 (toggle)**; the G12 holds and alternates state. **Command 0
  marks a light** (21 = "act while held", shared with motors). The **G12 acts on any RV-C instance**
  regardless of the pin sheet, so a remap's destination needs no enabling.
- **A channel can be dropped from a master control** (`drop_from_master`, v26, 2026-09-14). The
  bath light (ch25) was removed from the Light Master by neutralising its six bindings in the ON/OFF
  groups — DGN → `0xFFFF` and phase mask → 0. On the coach, Light Master ON/OFF no longer switches
  the bath light while its own button still works. The phase-mask zero is the hard guarantee (the
  sender emits only on a matching phase bit, `CONTROL-BEHAVIOR.md` §3), so no frame goes out for the
  entry regardless of the DGN. Consequence confirmed in passing: the ch25-following water pump
  (`pumplink`) is no longer driven by the master.
- **The receive filter is blind to WHO sent the frame** — the 18-bit DGN and nothing else. Where the
  entry also carries the `0xFF` sentinel (66 of 298), the last frame to arrive wins. The only
  discrimination available is the masked payload compare. **This is no longer hypothetical
  (`BUS-BASELINE.md` PC.46, 2026-09-02):** with two devices answering `0x1FFC6`, screen 14's
  `Battery Type` drew the *alternator regulator's* battery on a lithium coach. Fixed by
  `statusbind.pin_filter_instance` — one byte. The symptom to recognise is a field that is right
  almost always and wrong just after something on the bus restarts; and note the wrong value can
  **latch**, since a request-driven answer has no periodic writer to correct it.
