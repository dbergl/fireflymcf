# Settled findings, in full — option interlocks, the hold/tap split, and pin behaviour

Moved out of `CLAUDE.md` on 2026-08-25, **verbatim**. All three are settled and their detail also
lives in the per-area docs (`GUICODE.md` Findings 50/52/54/56, `CONTROL-BEHAVIOR.md` §9,
`G12-CODE.md`, `BUS-BASELINE.md` PC.21-PC.28). What is kept here is the *narrative* — how each was
reached, and in two cases how a confident wrong answer was reached first.

**The general rules these earned did not move.** They are in `CLAUDE.md`'s discipline section,
because they apply to work that has nothing to do with option interlocks or awnings:

- a register with one writer is not the whole story when the value round-trips through another node;
- verifying only what you set is not verification;
- a tool scoped to the question you asked last will answer the next one wrongly.

---

## Where an OPTION's interlocks are enforced — the panel, not the G12 (2026-08-23)

Asked by the owner: selecting **Progressive Inverter** also selects AGS and drops any AES mode —
where is that, and is the G12 enforcing it? **`GUICODE.md` Finding 50**, and it is entirely
panel-side. Each OPTIONS row's tap handler is a straight-line run that copies a fixed list of
`0x1FED9` write templates out of GuiCode's pool; a coupled option is just a handler whose list is
longer than one. PI-on (`0x0024CCF4`) emits `E6:=1` then `EF:=1`; selecting AGS (`0x0024D00C`)
emits `EF:=1, D7:=0, E6:=1, 15:=0x2801, F7:=0x3C`; either AES mode emits `E6:=0`. `EF` is one
four-valued mode register, so "deselects AES" needs no frame — writing `EF:=1` *is* the deselect.
PC.3's transactions match these runs frame-for-frame **and count-for-count**, all from source `9F`.

**This retracts Finding 19's "the coupling is upstream, not in GuiCode"** — that finding traced
only the config-payload *unpack* and reasoned from "no register store reads the other". No store
was ever the mechanism: the write side of an option is **the frame the panel sends**, and the
panel's own register updates afterwards from the G12's echo. *General rule, now paid for twice: a
register with one writer is not the whole story when the value round-trips through another node.*

**Two things to carry.** (a) The interlock is enforced on **selection only** — no deselect arm
clears its partner, so `mode = AGS, PI = 0` is reachable and the coach sat in it for 29 s in PC.3.
Never assume the pair is consistent. (b) The G12 re-derives the AGS/AES **parameter profile**
(`0x0A`, `0x0D`, `0x15`, `0x31`, …) on a mode change, but across all eleven captures it has
**never originated a change to an option-*selection* selector** — 84 such changes, all echoes.
Pinned by `tests/test_option_interlocks.py` (both halves, mutation-checked).

**CLOSED BY INJECTION, 2026-08-23** (`BUS-BASELINE.md` **PC.21**, this project's second *transmit*
entry). No screen tap could close the setting direction — a tap routes through the panel, which
supplies the coupling — so one frame went on the wire from SA `0xC0` with the coach's AGS/PI/AES
all off: `19FED9C0#FF96E60F0100D1EA`, `E6:=1` alone, no `EF`, no read-back. `E6` went 0→1 and held
five broadcasts (**the positive control** — the write landed, so the null result means something,
and the **G12's config-write path is SA-blind** like the panel's receive filter); `EF` stayed **0**
throughout; and the owner on the screen saw *"only PI is selected"*, so **the panel does not
re-impose the coupling on receipt either** — it lives in the tap handler alone. Restored with
`E6:=0`, sweep byte-identical to baseline. The asymmetry in (a) is `observed-on-hardware` too
(owner: disabling AGS leaves PI set; either AES then clears it).

*Bus access, for next time:* `ssh pi@<gateway>`, and the RV-C bus is **`can1`** (altname
`can_rvc`) — `can_bms` is `can0`, the 11-bit BMS bus, so `candump can_bms` shows none of this.
`0x42` is now **claimed** by the owner's PD-inverter bridge; `0xC0` was free and is the probe SA.

**REMOVED in reference build v6, shipped in v7 — CONFIRMED ON HARDWARE 2026-08-23.** Owner wants PI and
AES selectable together (the inverter is being replaced by a Renogy 48 V). `interlocks.uncouple`
NOPs the 36-byte emit pair behind each of the four cross-writes — **same-size**, so the image
stays 1,069,668 B — locating each block by walking the run and matching the selector, asserting
the twelve-instruction shape before writing, and re-walking after. The row is also renamed
`'Progressive Inverter'` → `'Renogy 48v Inverter'`, same-size in a 22-byte `h_align 1` (left)
field. Recipe op `remove_option_interlocks`; both tracked recipes carry it and still compile
byte-identically (sha `744cbeb7…`). **PC.21 is what licenses this edit** — had the G12 enforced
the interlock too, deleting the panel's copy would have made the screen disagree with the device
it commands. `D7 := 0` (Go Power!) is deliberately left, and the **deselect asymmetry is
unchanged**: this makes the two options *independent*, it does not make them consistent.

**The option → selector join is READABLE OFF THE BUS — `observed-on-hardware` 2026-08-24**
(`BUS-BASELINE.md` **PC.34**, `GUICODE.md` Finding 23's PC.34 block). A single-row OPTIONS tap
emits the config-buffer **offset** on `0x1EE00` and the **selector** on `0x1FED9` about **0.5 ms
apart with the same value**. The owner swept all six of 13.6's rows and every pair reproduces
`config.selectors_by_offset` — `0x0A→0xEB`, `0x0D→0xE9`, `0x0E→0xEC`, `0x11→0xE5`, `0x19→0xE4`,
`0x1A→0xE3`. **This retires Finding 23's "weak link" for 13.6:** the `handler → selector` half no
longer depends on code adjacency, wire verification goes from two rows to six, and every
attribution that rested on the weaker Rule B is confirmed. The 14.7 rows (`0x1B`, `0x14`) stay
derived-but-unverified — but one tap on a 14.7 panel now settles them outright, since the `1EE00`
frame supplies the offset for free.

*Why twelve days of captures missed it, and it is a rule worth carrying:* `0x1EE00` is not new —
it appears in ten prior captures and Finding 50 cites two of its frames. **The pairing is only
visible on a deliberate single-row tap.** In a floorplan *burst* the panel sends twelve `1FED9`
writes as one block and five `1EE00` frames as a separate, shorter, differently-ordered block —
`1FED9` writes every selector in the profile while `1EE00` reports only the offsets that actually
**changed** — so nothing pairs. They are not 1:1 the other way either: a bouncing tap gave three
`1EE00` frames in 15 ms against one `1FED9`, so the panel's config buffer can move without a write
following. *A burst is the worst case for a correlation and one narrated press is the best; the
raw material for this sat unexploited in the corpus because nobody had asked for a single tap.*

**Two more things from the same sweep.** An option tap **turns off the device it reconfigures**,
and the DGN follows the device class — `1FEDB` command 3 for the light pair (PC.33), `1FEF9`
THERMOSTAT_COMMAND_1 mode 0 for Heat Pump, in both directions. Finding 23 had this for the lights
only; a second device class makes it a rule of the tap handlers. **Bunk Accent and Bath Fan emit
no companion at all**, so it is per-row, and their silence is as much a fact as Heat Pump's frame.
And `EB = 1` **reveals** two CLIMATE modes (`Heat Pump`, `Auto`) — the mirror of the `E5`/`EC`
`hides_control` behaviour that is still unexplained, and the first concrete example of the reveal
direction.

*A defect raised and withdrawn inside one session, worth carrying as method.* Heat Pump tapped
"on, off, on" produced **one** `EB := 1` and then held — the exact signature of PC.32's silent
`−`. It is not one: a later deliberate single tap emitted `EB := 0` normally, so two taps simply
never registered. The end state could not tell the two readings apart ("on, off, on" and one "on"
both land on 1). **What separated them was a deliberate, narrated, single press** — the same
control PC.32 needed, and the reason that defect took three sessions to pin.


---

## A HOLD beside a tap: the hit-region split — CONFIRMED ON HARDWARE 2026-08-23 (PC.22)

Owner asked for the HOME inverter button to keep its tap (on/off) and gain a 3 s hold onto the PD
Inverter page. **Not possible on one region** — the action dispatcher is edge-triggered on the
touch id (`cmp`/`beq` @`0x24ACBA`), so a tap fires on touch-down, 3 s before any hold; and a
*navigating* tap cancels its own hold by changing what is under the finger. Both shipped tap+hold
pairs are navigate+navigate or nothing+navigate. So: **two non-overlapping regions**
(`src/fireflymcf/holdregion.py`, `CONTROL-BEHAVIOR.md` §9, recipe op `split_hold_region`),
shipped in reference build v7 — tap `(197,382)-(340,439)`, hold `(345,382)-(417,439)`.

**v8 retargets that hold to `INVERTER / CHARGER SETTINGS` (object 17, screen 14) — CONFIRMED ON
HARDWARE 2026-08-24** (`BUS-BASELINE.md` **PC.29**). One recipe parameter, `screen_object 38 → 17`.
That page is **ORPHANED in stock** (`GUICODE.md` **Finding 53**): its only navigation site is
guarded by action id `0x7DE`, carried solely by objects 1052/1053, neither in the display graph.
v8 gives it its **first** route by writing the screen id straight to `state+0x20A`, and the page
renders correctly.

**Finding 53's predictions held 6 for 6** — made from the file and the 30 s PC.23 baseline about a
page nobody had ever displayed, and right on all six rows *including the negatives*:
`Max Chrge Rate` **100%** (the one live readout, var 114 / `0x15FCE` inst `0x92`),
`Shore Brkr Size` **255 A** and `VAC Dropout` **255 VAC** (`0xFF` unset, rendered literally),
`Low Batt. Cutout` **`---V`** (never received → dashes), `Battery Type` **blank**, and
`Absorb`/`Float` **not drawn** (slot-23 gate closed → object 302's empty box). Carry the three
display conventions: an unset `0xFF` shows its literal value, a never-received cell shows
**dashes**, a gated-off block shows its sibling's empty box — three different failures that all
read as "broken field", and only the last is intentional.

**The write side is CONFIRMED for four of seven controls — PC.30, 2026-08-24.** The owner pressed
them while the bus was watched. All four transmit, every wire delta matches Finding 51 (∓20 / ∓5 /
∓2 / ∓5), and the G12 relayed three to the DGNs Finding 52's rule table names — `0x92`→`1FF95`,
`0x86`→`1FF87`, `0x82`→`1FFD0`, instance 1, 1.6-10 ms — with the table reproducing each frame
byte-for-byte through its substitution mask. First time that mapping was driven by a finger rather
than by injection.

**`0x99` relayed NOTHING, which is the falsifiable negative holding.** The G12 stored it (one
`195FCE9C#990005…` at +2 ms) and broadcast no command of any DGN. A rule table that reproduced
only the frames that already exist could not have been wrong there.

**A control whose write works while its readout is dead, seen live.** `Low Batt. Cutout` shows
`---V` (nobody sends `0x1FFD2`) yet pressing it moved the G12 214 → 212 → 214 and relayed both
times — and **214 is the same raw PC.23 read back** four days earlier by a different route.
Finding 51's "two independent halves, joined only by sitting in the same object" is now observed:
*a control that looks broken may be working, and one that looks right may report something else.*

**`Battery Type` is an ENUMERATED CYCLE, not a delta — corrected on the bus, PC.31.** Six presses
relayed `p[6]` = 1, 2, 3, **13**, **0**, 1: the G12 holds a set of battery-type codes
`{0, 1, 2, 3, 13}` and `+1` advances to the next *member*, not the next integer. `3 → 13 → 0` is
neither addition nor a clamp, and every read-back returned exactly the byte just relayed — so the
G12 stores the RV-C code itself, with no index→code table to find. (What the codes *denote* is not
established; `0x0D` is where RV-C tends to put Lithium, but no chemistry is claimed.) The cycle
then served as a prediction: four probe `+1`s walked 1 → 2 → 3 → 13 → 0 exactly as modelled and
**restored the coach's original value**. Rule #55 reproduces all ten frames, completing PC.23's
fourth relay mapping under a real press.

*Why that was safe, and it is the reusable part:* `1FFC4` addresses **instance 1, which nothing on
this coach claims**, so ten commands went out and no device acted. Check the instance before
pressing, not after.

`0xBA`/`0xBC` remain unreachable while the slot-23 gate is shut.
**Left changed on the coach:** `Shore Brkr Size` is 5, was 0 — the one control not round-tripped.
It relays to nobody, so the stake is nil; one `−` press or one `19FED9C0#FF96990FFBFFD2EA`
restores it.

Owner's reason for the retarget: object 38 is a read-only readout, while object
17 carries **seven writable controls** and is otherwise reachable from exactly one navigation site
(`0x24B96C`). Every control is decoded in `GUICODE.md` **Finding 51**, write side
and read side both — and the reusability rule that finding ends on is the one to carry: repointing
a control is **two** edits in **two** sections (binding byte `+5` in GuiData, the
`ldrsh.w r3,[r4,#2*var]` immediate in GuiCode), and doing only the first gives a control whose
arrows change one setting while its number reports another.

Three traps, all now guarded: **an object's first byte is its RECORD COUNT** (insert without
bumping it and the decoder stops one record early); **an inserted record advances the pen by its
own x**, so the next record's x must absorb it; and positions are **flow-relative**, so the only
trustworthy check is a layout re-walk of the finished image.

**The defect worth carrying — it passed every gate.** The first working version placed both hit
regions exactly as asked and moved the block's label and boxes 148 px right, off the panel.
Boundary oracle, deep coverage, pointer repair, capability gate and the build's own assertion all
passed, because they checked the two regions and nothing else. Caught by looking at the render.
`split_touch_region` now asserts every **non-touch** element lands where it did before, and v7's
HOME render is pixel-identical to v6's. *Verifying only what you set is not verification.*

Id `0x1795` needed **no arm edit**: §7's "third arm" is a reachable dispatch leaf (any id >
`0xFA1`) whose deadline nothing consumed — spare capacity, and that answers "what it protects"
(nothing). The GuiCode half is three same-size edits, with the 3000 ms encoding **lifted
byte-for-byte** from the CLEAR EEPROM timer at `0x24EC54` rather than assembled. **No trade, and the
argument that said there had to be one was wrong twice** — first in which arm to take (the `0x838`
hold is the coach's ONLY route to SERVICE MODE: id `0x7E1` is registered only by objects
92/690/826 — 92 and 826 unattached, 690 a type-6 candidate under the header container, so drawn
only when that switch selects it; "unattached" was imprecise for 690, corrected 2026-08-24), then in thinking a choice was needed at all. GuiCode cannot *grow*, but step 12's four
36-byte NOP runs are dead space: a **dispatch stub** goes in one, the consumer's second arm becomes
a `b.w` to it, and the stub re-implements that arm *and* adds the new one. All three holds survive
(tank → PROBE TANK DIAG, gear → SERVICE MODE, inverter strip → the inverter page), read back by
`holdregion.hold_dispatch_map`. **This is the project's first authored machine code**
(`src/fireflymcf/thumb.py`): every encoder is validated against an instruction already in 13.6,
and every emitted instruction is disassembled back and checked. Service Mode (`0x2003A528 == 1`)
removes the delay, turning any hold into a tap — firmware behaviour, not ours.

**`state+0x20A` is a TABLE lookup, NOT `object − 4`** — retracted on the coach 2026-08-23, when the
owner photographed SERVICE MODE from the gear hold that `−4` says is AUTO ENGINE START ENABLE.
Screens are candidates of object 1's type-6 switch (`value_slot 2`, base 3, count 47);
`layout.screen_object_map` reads the real map from the match table. The old rule holds only over
screens 0-4, where it was derived — screen 6 does not exist, so everything after drifts, and the
anchor was ambiguous anyway (objects 3 AND 4 both render HOME). It had already aimed v7 at GO
POWER! CONFIGURATION (screen 34) when the PD Inverter page is screen **39**, through every gate,
the tests and a commit. **Pass object indices, never screen ids** (`layout.screen_for_object`).


---

## Where a pin's behaviour is enforced — the rest of it

The four-row table this narrative supports is reproduced here (moved from `CLAUDE.md` on
2026-09-10); everything after it is the derivation, including the ignition-interlock chain that had
five pieces of corroboration and was still wrong.

### Where a pin's behaviour is enforced — three places, not one (2026-08-19)

Asked by the owner: does the G12 enforce an output's behaviour (e.g. the awning's ignition
lock), or is it a GuiCode/GuiData setting? **It is split**, and `src/fireflymcf/pinconfig.py`
(`describe_pin`) gathers every MCF-side reference to one pin in a single report.

| behaviour | enforced by | confidence |
|---|---|---|
| electrical limits, hardware type, 6 A/12 A OCP class | **G12 table 0x82** | `proven-from-file` (matches the owner's PDF) |
| latch / dim / momentary, master membership | **GuiData bindings** — what the panel sends | `proven` (Bath Fan is "latch" only because the panel never sends it command 0) |
| EXT/RET mutual exclusion | **GuiData *and* the G12, independently.** Group 3 carries `(4,3)` RET-off *and* `(3,1)` EXT-on, so the pair cannot drive together. Sharpened 2026-08-19 (`CONTROL-BEHAVIOR.md`): the RET-off is a **press-phase** emission and the EXT-on a press+held one, so the other direction is killed *before* this one starts. **Corrected 2026-08-24** (`GUICODE.md` Finding 54): this row used to say GuiData *alone*. G12 table `0x20` records `0x1023B4`/`0x1023C0` are byte-for-byte the same `(4,3)`/`(3,1)` pairing for the **physical switch** path, which the panel's bindings never see | `proven` (matches PC capture) |
| ignition / park interlock | **the G12, by broadcasting an RV-C `DC_DIMMER_COMMAND_2` command 33 (Lock) on the channel** — captured 2026-08-24, PC.27, **and the code that sends it is now read** (`G12-CODE.md`, table `0x67` `+0x10A8`/`+0x183A`): an edge detect on state cell `+0x50`, then a block-copy of frame-table records 11/12 (OFF, LOCK) or 13 (UNLOCK) | `observed-on-hardware` **+ `proven-from-code`** |

So **table 0x82 holds no behaviour at all**, and a pin you wire yourself gets no
interlock unless a G12 rule *or the table-`0x67` code* gives it one. The G12 runs a real
event→action program: 151 rule records over tables 0x20/0x30/0x61/0x62, plus a 6,848-byte
**Thumb-1** blob at table `0x67` whose transmit path, awning interlock, input pins and AES arming
are all decoded (`G12-CODE.md`). **The derivation — including the ignition-interlock chain that had
five pieces of corroboration and was still wrong — is `docs/SETTLED-FINDINGS.md`.**

*One byte-order trap worth carrying up here:* the `0x67` frame table's payload is in **wire order**,
*not* the `[7,6,5,4,11,10,9,8]` order the panel's bindings and the `0x62` rules use. Do not carry
that byte order across.



Its 180-byte data tail is a **14-entry frame table** at `+0x1A0C` of 12-byte records
`<u8 flags><u16 DGN-lo LE><u8 CAN-id high byte><8-byte payload>`, the payload in **wire order** —
*not* the `[7,6,5,4,11,10,9,8]` order the panel's bindings and the `0x62` rules use, so do not
carry that byte order across. **Six of the fourteen reproduce a captured frame byte for byte.** All
**19** `add rX, pc` sites in the whole blob resolve into this one table, which is why earlier
absolute-pointer and `ADR` scans found nothing: the code is PIC and selects a record with
`adds rX,#imm`, then block-copies it with a single `ldm`/`stm` register triple and calls a transmit
function pointer — **the same design as the panel's `ldm {r0,r1,r2}` template copy** (Finding 23),
arrived at independently on both devices.

**The awning interlock is that path's clearest user, and it is now fully cited.** `+0x10A8`
compares state cell `+0x50` against latch `+0x52`; on a change it sends record 11 (ch 3 cmd 3 OFF)
then record 12 (ch 3 cmd 33 **LOCK**) when the cell is non-zero, or record 13 (cmd 34 **UNLOCK**)
via the tail at `+0x183A` when it is zero, then latches. That is PC.27 frame for frame and order
for order, and it explains PC.28 with nothing added — **there is no key, no ignition concept and no
timer in this code**, only `[+0x50] != [+0x52]`, which is exactly why an AES auto-start locks the
awning too.

**Three config-write shapes, one per control class — `observed-on-hardware` 2026-08-24**
(`BUS-BASELINE.md` **PC.34/PC.35/PC.36**). An OPTIONS row writes **both** the config-buffer offset
(`0x1EE00`) and the selector (`0x1FED9`); a **± adjust control** writes the selector only; **Service
Mode** writes the offset only, and has **no selector at all** — so `config.selectors_by_offset`'s
silence about offset `0x12` was correct and complete, not a gap. Do not assume every config value
has a selector, and do not read a missing `1EE00` beside an adjust control as the mechanism having
changed.

**Service Mode also broadcasts `0x1FBDA` instance `0xB0`** (panel-sourced, `payload[1]` 1/0, 0.56 ms
after the offset write) — an instance that appears in **no earlier capture**. That widens Finding
54: `1FBDA` instance `0xA0+N` is the **G12's** input pins, while the **panel** sources its own
family on the same DGN (`0xB0`, and `0xF9` still undecoded). "Instance means input pin" holds only
for the G12's range.

**And the Service Mode toggle does NOT round-trip** — `CONTROL-BEHAVIOR.md` §5's "bus-delivered,
not set locally" is retracted. Nothing answers the panel's write and the screen changes anyway.
*The reasoning failed the same way `defect 0a` did: it argued from a pool-literal scan finding one
writer, and a write through a computed address is invisible to that scan.*

**The tank gauges are BUCKETED, not measured — `observed-on-hardware` 2026-08-24**
(`BUS-BASELINE.md` **PC.35**). Config selectors `0xCC`/`0xCD`/`0xCE` are the **tank level
thresholds** the panel's `PROBE TANK DIAGNOSTICS` screen edits — **raw 16-bit ADC counts, higher =
emptier**: 54000 = the 0%/33% boundary, 40000 = 33%/67%, 25000 = 67%/100%, step ±1000, with a
`Reset Default Thresholds` button that is three absolute `D1 EA` sets. The comparison ladder is
**G12-side**, in the table-`0x67` block at `+0x1858` that this project had filed as *unreached,
possibly dead code*. **It predicts the screen**: the coach's four senders read 65520 / 65515 /
57412 / 65520, all above the top threshold, so all four gauges report **0%** — which is what they
show, and which explains PC.25's stray note about the tanks reading 0 % mechanically for the first
time. *A reported tank percentage is a bucket, not a measurement.*

*How it was identified, and it is worth copying:* the file gave the step, the defaults and the
reading code but **no unit** — 1.000 V, 1 s and 1 A all fitted equally well and the write-up
declined to pick one. All three were wrong. The owner recognised the numbers, walked to the screen,
and one press confirmed the predicted frame. **When a value resists the file, ask the person who
owns the coach before inventing a scale.**

Two spin-offs. **`0xBF` is located** — PC.24's "undecoded by anyone" frame is frame-table records
0/1/2, `1FBDA` instance `0xBF` with an all-zero tail and three states (1, 2, 0), emitted from six
call sites; **state 2 has never been seen** and what the states mean is still unknown. And **the
G12 does carry a `0x1FED9` transmit template** (record 4, a `D3` read of selector `0xF7`) — a
second dormant `1FED9` transmitter beside rule record 59, and a different one, worth holding
against PC.33/PC.34's zero-frames negative: the capability is in the code, it has not fired.

*Honest limits:* **nothing in the blob writes `+0x50`** — all its accesses are reads, so its writer
is in the G12 **application** at `0x08006000`, which the MCF does not contain; "`+0x50` is the
ignition sense" stays `inferred` from bus behaviour. It is also compared against **1**, so it is
not proven boolean. And **channel 18 looks gated on the same cell** — record 3 (`instance 0x12`,
command 3 OFF) is referenced from four sites in the region that reads `+0x50`, unread and never
observed on the wire.

**The ignition interlock chain — RETRACTED 2026-08-24, refuted on the bus** (`BUS-BASELINE.md`
**PC.25**). This entry used to run: G12 rule #93 triggers on `0xFFB7` and targets output 3
(AWNING EXT); `1FFB7` is broadcast by the G12 on instances 0/1/2/3/18; the panel subscribes with
five matching filter entries and carries the labels `Awning Locked by Ignition`; instance 3 has a
bit flipping `0x50`↔`0x51` and its byte2 `0x64` matches the rule's constant. It flagged one
unproven link — that the bit is ignition — and named the decisive test.

**The test was run and the bit is not ignition. Nor is any of the rest of it.** Across a 28.3 s
ignition-on window every `1FFB7` instance held **exactly one payload**, before, during and after,
while `19FBDA9C#AA01`/`AA00` tracked the key on the same trace. Reading all five `1FFB7` rules
together instead of #93 alone shows what they are:

    t62 #45  inst 0x00  00__03FF…  p[1]<-0xb5      t62 #47  inst 0x01  01__03FF…  p[1]<-0xb7
    t62 #46  inst 0x02  02__03FF…  p[1]<-0xb6      t62 #48  inst 0x12  12__03FF…  p[1]<-0xb8
    t62 #93  inst 0x03  03__64FF…  p[1]<-0xc2

Five instances of **one** rule — standard RV-C **`TANK_STATUS`**. `inst 0x03` is the **LPG tank
instance**, not "G12 output 3": an RV-C instance byte was read as an output number. `0x64` is the
**resolution** field (100, as an LPG sender uses, against the waste tanks' 3). `0x50`↔`0x51` is a
propane level jittering 80↔81 of 100. The panel's five filter entries are the **tank display** —
fresh/black/gray/gray2/LPG — and the four resolution-3 tanks read 0 %, matching the coach.

**`Awning Locked by Ignition` — TRACED 2026-08-24** (`GUICODE.md` **Finding 56**). The candidate
was right: object **181**'s type-6 switch on `value_slot 132` picks obj 220 `'EXT'` (match 0) or
obj 221 `'Locked by Ign'` (match 1); slot 132 → `0x2003A4E5`, written by the pin-10 arm of the
input mirror run at `0x24B41E`; PC.24 proved that signal tracks the key. *(The long-form strings
in objects 182/701 have no parent — unattached templates. The label on screen is `'Locked by
Ign'`.)*

**But it is DISPLAY-ONLY, and it is STALE.** Both switch arms are structurally identical and the
**touch region sits outside the switch**, so the button stays live and group 3 still sends
`cmd 1` on channel 3 — the panel relabels, it does not disable. And `0x2003A4E5` has one pool
literal and one reference, the store, so the only consumer is the compose-time walk: var 201 is
not among the 44 bridged vars, so there is no repaint trigger. **Turn the key while on the awning
screen and the button still reads `EXT` until you leave and return** — the same defect as
`Engine Running`, and the second instance of it.

**THE INTERLOCK IS FOUND — `observed-on-hardware` 2026-08-24** (`BUS-BASELINE.md` **PC.27**). It
is not hidden in table `0x67` and it is not proprietary. **The G12 broadcasts a standard RV-C
`DC_DIMMER_COMMAND_2` command 33 (Lock) on channel 3**, 8 ms after ignition-on, having first
forced the channel off; command 34 (Unlock) follows 2 ms after ignition-off:

    29.974  19FEDB9C#03FF0003FF00FFFF    G12 -> ch 3, cmd  3 OFF
    29.975  19FEDB9C#03FF0021FF00FFFF    G12 -> ch 3, cmd 33 LOCK
    66.934  19FEDB9C#03FF0022FF00FFFF    G12 -> ch 3, cmd 34 UNLOCK

`DC_DIMMER_STATUS_3` carries both halves: **byte 3** is the lock flag (`0xFC` unlocked / `0xFD`
locked) and **byte 5** is the last command applied. While locked the G12 **receives the panel's ON
command and records it in byte 5 while leaving brightness at 0** — it declines, it does not ignore.
Only **EXTEND** is locked; RET energised to 200 on the same trip.

*The command byte is `payload[3]`, not `payload[2]`.*

**Retracted with it: the keypad design argument.** It ran "the keypad commands the G12 directly, so
a panel-side inhibit would be bypassed, so enforcement must be G12-side". **The keypad has no
source address at all** — only `19FEDB9C` and `19FEDB9F` send `1FEDB` in 7,003 frames, and keypad
presses come out as **`19FEDB9F`, from the panel**. It is a wireless input to the panel, not a bus
node. The conclusion was right and the reasoning was not; *do not reason from a device's
independence without confirming it has a source address.*

*What this cost and what it buys:* the chain had five pieces of corroboration and was still wrong,
because they were assembled around the question "where is the ignition interlock?" rather than
derived. **The check that would have caught it is free: read the other records of the same kind.**
#93 alone looks like an awning rule; #45-#48 beside it make all five obviously one family. That is
the second time a narrowly-scoped listing produced a confident wrong answer (Finding 54 was the
first) — *a tool scoped to the question you asked last will answer the next one wrongly.*

**A better probe for the same trip, found 2026-08-24** (`GUICODE.md` **Finding 54**): the G12
also has a **labelled ignition sense INPUT** — pin 10, broadcast as `0x1FBDA`
GENERIC_INDICATOR_COMMAND instance `0xAA`, which the panel paints on DEVICES/FAULTS
(object 14, screen 11) via slot 132. So one key-turn with

    candump -L can1 | grep -i 19FBDA9C

gives `AA 01` / `AA 00` directly, and the screen row confirms it without the bus at all. Watch
both DGNs on the same trip and the interlock chain closes. `cli g12-inputs` prints every input
pin's frames (confirmed exact by PC.24).

**DONE, 2026-08-24 — and it closed the question negatively.** PC.24 proved `AA 01`/`AA 00` and
`FB 01`/`FB 00` track the key exactly, so the **ignition signal is confirmed and is the one to
use**. PC.25 then watched both DGNs on one trace and found `1FFB7` **completely static** across a
28.3 s ignition-on window — see the retraction above. So this probe succeeded at what it was for
(a direct, labelled ignition signal) and destroyed the chain it was meant to corroborate.

**The G12 INPUT pins, decoded 2026-08-24** (`GUICODE.md` **Finding 54**, `MCF-FORMAT.md` §9a.1).
Asked by the owner: is input status one of the G12 config values? **No** — it is a separate DGN
on a separate path. Input pin N is broadcast as `0x1FBDA`, **instance `0xA0 + N`**, state in
`payload[1]`; the templates are G12 table **`0x20`** (18 records, 9 instances × ON/OFF), the
panel matches them at receive-filter **vars 192-201**, `FUN_00247BF8` @`0x24b382`-`0x24b420`
normalises each to 0/1 into ten SRAM bytes, and object 864 paints them. Nothing touches a
`0x15FCE` selector. Three independently-read sources agree, including two slots no guess would
produce (pin 2 → slot **242**, pin 10 → slot **132**, off the otherwise tidy 300-307 run), and
pins **5 and 8** are watched by the panel but broadcast by nobody — the negative that would have
broken a fitted decode.

**The interlock keys on the SENSE LINE, not the key — and an AES auto-start locks the awning**
(`BUS-BASELINE.md` **PC.28**, 2026-08-24). With the van's AES idling the engine and nobody at the
panel, input 10 was high and channel 3 was **already locked**; when the engine stopped, the sense
dropped and the G12 broadcast `cmd 34 UNLOCK` **4 ms later**. Same mechanism as PC.27, reached
without touching a key.

**CONFIRMED ON HARDWARE 2026-08-24** (`BUS-BASELINE.md` **PC.24**,
`captures/pc24-input-pins-2026-08-24.log`) — this entry read "Not observed on the wire: zero
`19FBDA9C` frames in eleven captures" until the owner turned the key. Every predicted frame
appears **byte for byte**; instance `0xA0 + pin` and `payload[1]` as state are both confirmed;
pins 5 and 8 are silent as predicted; and **`0xFB` is pin 10's twin** — 14 `FB` frames, 14 `AA`
frames, never one without the other within 0.6 ms, and zero `FB` while the water pump alone is
held.

**But the "edge-triggered, no heartbeat" reading was WRONG.** Every instance **refreshes at
`1.000 s` while held**, GND and 12V alike; a tap under ~1 s sends one ON/OFF pair and no repeat.
The re-send is not a template, so it was never going to be in the rule table — it lives in G12
table `0x67`, still mostly unread. *Carry the rule: a table of ON and OFF records tells you what
is sent, never how often.* One frame remains undecoded by anyone: `0xBF`, twice, bracketing
ignition-off, with an all-**zero** tail instead of the inputs' `FF`.

*This also resolved a cross-repo disagreement, and not in the direction the standing rule
predicted.* `rvc2mqtt` had a **tested handler built from observed traffic** reading `payload[1]`
as a global 12V flag and `0xFB` as an idle code. "The capture wins" said theirs should stand over
our static decode; PC.24 refuted **both** of their contested readings and our cadence claim.
Their `~1 Hz` observation was real but mis-attributed — with the ignition on *and* another input
held, `FB`/`AA` and `A9` interleave once a second, which reads as active/idle alternation unless
you know `FB` is pin 10. **A capture cannot separate two signals that always travel together;**
that is the blind spot a static decode does not have, and the reason to keep doing both.

*Why it stayed hidden, and the rule to carry:* every entry point in `g12rules.py` read table
`0x62`, so `cli g12-rules` showed 94 of 151 records and three whole tables were invisible to the
only tool that would have revealed them. Finding 52's decode was right; its **listing** was
narrow. `cli g12-rules` now opens with a four-table census and a test fails if that narrows
again. **A tool scoped to the question you asked last will answer the next one wrongly.**



---

## Derived slots — the HOME battery block and the left nav rail (2026-08-21)

Both are computed by `FUN_00247BF8` from config, not configured directly, so a preview that ignores
them shows a screen the panel cannot draw. `GUICODE.md` **Finding 47** holds the nav-rail decode;
this is the record as it stood in `CLAUDE.md`, including the tooling layer.

**The two general rules this earned are in `CLAUDE.md`'s discipline section**, because they apply
far beyond these two widgets: a register whose *writer* is understood is not understood until its
*reader* is named; and an unset switch is not a safe default.

**The HOME battery block is DERIVED, not configured — `proven-from-code` 2026-08-21.**
Object 4's type-6 switch on **slot 6** picks between four battery blocks
(901/902/903/904 for values 0/1/2/3). Slot 6 is not a config field and no tap handler
writes it: `FUN_00247BF8` computes it from the **AGS register** `0x2003A38C` (= option
slot 37, the generator radio) on every config payload —

    0x00249CFA  ldr r3,[0x00249e2c]   ; = 0x2003A38C
    0x00249CFC  ldrb r2,[r3,#0x0]
    0x00249CFE  subs r3,r2,#0x2       ; AGS in {2,3} (AES / AES Single Output)?
    0x00249D04  bls.w 0x00249ECC      ;   -> slot 6 = 1  (object 902)
    0x00249D08  cmp r2,#0x1           ; AGS == 1?
    0x00249D0A  beq.w 0x00249ED4      ;   -> slot 6 = 2  (object 903)
    0x00249D12  strb r1,[r2,#0x0]     ; else 0           (object 901), r1 = 0

so **nothing in 13.6 ever writes slot 6 = 3**: object 904 (the SOC/alternator variant,
`GUICODE.md`'s decode of the candidates) is real but unselected here, and the scan is complete —
every reference to both pool slots holding the register is one of the three writes above. Found with
`ghidra/find_reg_write.py 2003A03E` against the `guicode136` project. Studio applies it
in `reference.derived_slots` / `engine.preview_slots`, so picking a floorplan shows the
battery block that coach draws — a default **WD** selects AES Single Output and
therefore object 902, the one the reference build's `battery` op edits. It is deliberately **not** a
pickable control: offering one would let a preview show a screen the panel cannot draw.
`/api/reference/config` returns only the *linkage* (`derives`), never the mapping, so the
arithmetic has exactly one copy. An explicit `extra_slots` entry still overrides it, as
for the runtime gates.

**The LEFT NAV RAIL is derived too — and unlike the battery block, its default was the WRONG
picture. `proven-from-code` 2026-08-21** (`GUICODE.md` **Finding 47**), owner-reported from the
coach. Object 974's type-6 switch on **slot 453** picks the icon column: value 0 → object 386
(`P Q R L`, four icons, 20 px gaps), anything else → the `0xffff` default candidate 385
(`P D Q R L`, five icons, 12 px gaps, `D` = the bolt-in-a-circle). **Both rails are authored by
Firefly** — 386 is a second complete rail with its own spacing, not 385 minus a record, which is
why the icons visibly respace. `FUN_00247BF8` computes slot 453 at `0x00249D3E`–`0x00249D60`
(store 0) / `0x00249F02` (store 1): it is **1 iff AGS ≠ 0 OR Go Power! count ≠ 0 OR Progressive
Inverter ≠ 0** — the Electrical icon shows iff the coach has something electrical. Those two
stores are the whole writer set (`0x2003A617`: two pool literals, no `movw`/`movt`, one further
file-wide hit which is slot 453's own variable-table entry).

Three things worth carrying beyond the fix:
- **A register whose *writer* is understood is not understood until its *reader* is named.**
  Finding 19 had already cited these exact instructions and called `0x2003A617` "a visibility
  flag"; because it never said what reads it, no renderer drove the slot and the mismatch
  survived until someone looked at the panel.
- **An unset switch is not a safe default.** Slot 6's default candidate happens to equal its
  value-0 block, so deriving nothing draws the right thing; slot 453's default is the *five*-icon
  rail, so deriving nothing draws the icon exactly for the coach that lacks it. `derived_slots`
  therefore treats an absent source as **0** for the rail and as **unset** for the battery block,
  and says why in its docstring.
- **The arithmetic moved down a layer.** It now lives in `options.derived_slots` (tools), not in
  the studio server, because `cli render` applies it too — two renderers disagreeing about what
  the panel computes is worse than neither doing it. `studio.server.reference.derived_slots`
  delegates; `engine.resolve_slots` runs an explicit `?slots=` through it as well, so the query
  escape hatch still reproduces a saved preview without the caller having to know which slots the
  firmware computes. `livecfg._COUNT_SLOTS` gained `0xD7 → 39` (Go Power! count) so a live import
  carries the third source.

*Cross-check, not a fit:* run the floorplan bursts' own defaults through the derivation and
**SY alone** comes out with the four-icon rail — the floorplan already recorded above as the
AGS-mode exception (`EF = 0`). Nothing in the derivation reads that list.

Then, for the owner rather than the test: whole-control resize (one width/height writes box +
caption + touch, the three records §6 warns come apart), **Position on page** in the inspector
(`move_attach` without looking up an object index), a multi-line caption field with a font
picker on the add-button card, and a selection that re-points itself after each compile instead
of showing stale geometry to build the next `expect` from. The shortened path — 16 ops in 13
gestures instead of 18 — was re-run end to end and still exports **the same bytes**.



---

## Moved out of `CLAUDE.md` on 2026-09-10 — the build-general interlock analysis and the cross-coach constant audit

Verbatim, as part of the documentation cleanup (its design note (not published)). Dated statements below describe the state on the date they carry.

### Where an OPTION's interlocks are enforced — the panel, not the G12

Selecting **Progressive Inverter** also selects AGS and drops any AES mode, and that coupling is
**entirely panel-side**: each OPTIONS row's tap handler is a straight-line run of `0x1FED9` write
templates, and a coupled option is just a handler whose list is longer than one (`GUICODE.md`
**Finding 50**; **CLOSED BY INJECTION**, `BUS-BASELINE.md` **PC.21** — one frame with no coupling
landed and neither the G12 nor the panel re-imposed it). Two things bind:

- **The interlock is enforced on selection only.** No deselect arm clears its partner, so
  `mode = AGS, PI = 0` is reachable and the coach sat in it for 29 s. Never assume the pair is
  consistent.
- **The G12 has never originated a change to an option-*selection* selector** — 84 such changes
  across eleven captures, all echoes. Pinned by `tests/test_option_interlocks.py`.

`interlocks.uncouple` removes the coupling in same-size NOPs and ships in reference build v7+ (the owner wants PI and AES together). **PC.21 is what licenses that edit**: had the G12
enforced the interlock too, deleting the panel's copy would have made the screen disagree with the
device it commands. Full narrative in `docs/SETTLED-FINDINGS.md`.

**The interlock analysis is build-general as of 2026-08-29** (`interlocks.find_coupled_handlers`,
`has_interlocks`): couplings are located *structurally* — scan GuiCode for the `ldr [pc]` pool-base
load beginning an emission run, derive this build's OPTIONS toggles (selectors with select/deselect
arms), and keep runs writing the mode selector `0xEF` alongside a toggle. Validated to reproduce
13.6's four `CROSS_EMISSIONS` exactly. It settles that the interlock is **not a fixed PI↔mode
pairing but a mode↔option coupling that differs per build**: **11.23** has none (predates AES, no
`0xE6` template) → `uncouple` a clean no-op; **13.6** couples the mode to Progressive Inverter
(`0xE6`), four handlers, `uncouple` removes them (byte-exact, reference-build oracle green); **14.7** has the
coupling **moved** — `0xE6` (PI) is present but *decoupled*, and the mode is instead mutually
exclusive with a **new** toggle `0xE7` (select `0xE7` → mode `:=0`; select AGS → `0xE7 :=0`). So
14.7 is NOT interlock-free (an earlier note here wrongly said "rows already independent" — corrected
2026-08-29 after the generalized detector caught the `0xE7` coupling a `0xE6`-only scan had missed);
`uncouple` reports it but refuses to *edit* it (`BuildNotSupported` — unvalidated removal on a build
with no hardware to verify). **All of this ran on a coach flashed to 14.7 on 2026-08-30
(`BUS-BASELINE.md` PC.41), which sharpened one arm:** select-AGS → `E7:=0` fired every time, and
PI's decoupling is real (`E6` toggled with no mode write, where stock 13.6 the same day emitted the
coupled pair) — but **select-`0xE7`'s `mode:=0` is CONDITIONAL on the mode being AGS**: from
AES/AES-Single, `E7:=1` goes out alone (twice observed); from AGS, `E7:=1` + `EF:=0` (once). The
detector's template is real; a mode==1 gate guards it, so 14.7's rule is "Lithionics excludes
AGS specifically", not "Lithionics excludes any mode" — WA's own defaults ship `EF=3, E7=1`
together. **`0xE7` = the new Lithionics Batteries option, PROVEN**
(`interlocks.option_for_selector`, 2026-08-29): the GuiCode handler that emits `0xE7` gates on
`0x2003a009`, the exact stored-state register the Lithionics OPTIONS widget is bound to — and the
same method reproduces `0xE6` → Progressive Inverter on both 13.6 and 14.7, so it is a validated
selector→row link, not a fit. (The dispatch itself was not needed: a select handler always begins by
reading its own row's register to decide select-vs-deselect, so that first `ldrb` names the row.)
Lithionics replaced 13.6's uncoupled Renogy Battery row; being mutually exclusive with auto-gen-start
fits a lithium system that manages its own charging.

**Cross-coach constant audit — the 13.6-pinned WRITE tools refuse rather than corrupt (2026-08-30).**
Every tool that carries hardcoded 13.6 GuiCode addresses was checked for what it does on 11.23/14.7,
and the answer is uniform: it **refuses on a build it was not validated for**, never stamping 13.6
offsets into a foreign image. `holdregion` (hold-region split) and `tankgates.widen` assert the
expected stock bytes at every site before writing (`got != old` raises); `interlocks.uncouple` gates
on `addresses_apply` — a content-check, not a version string — so it is a clean no-op on 11.23 and
raises `BuildNotSupported` on 14.7 before any byte is written. `interlocks.ALL_MODE_ARMS` looked like
dead code in a single-file scan but is load-bearing in `builds/from_stock.py` and
`tests/test_interlocks.py` (the neighbour-clobber proof) — an argument-from-absence caught by grepping
the whole repo, not one file. The only remaining build-specific surface is **preview-only**:
`options.derived_slots` resolves its inputs by option name (build-general) but writes the HOME
battery-block / nav-rail SRAM slots (6, 453) and reads Go Power slot 39 by number — these are
build-stable cell indices (present as switch-read cells on 13.6 and 14.7, absent on 11.23, and Go
Power is not an OPTIONS binding so it cannot be name-resolved), and `derived_slots` feeds render only,
so it can never change a flashed byte. Skipping the derivation is NOT the safe default here — the nav
rail's unset state is the five-icon rail, so not deriving draws the Electrical icon for a coach that
lacks it (the standing "an unset switch is not a safe default" rule).

