# Control types and their behaviours — tap, hold, ramp, repeat, momentary

*Dug 2026-08-19. The question: a light button toggles on a tap but ramps if you hold it; the
awning runs only while held; some buttons must be held for seconds. Where is that written?*

**Answer: in two fields this project had labelled `unknown`,** and the code that consumes them
is in the **LCD Firmware**, not GuiCode:

| field | meaning | was |
|---|---|---|
| binding entry `+0x03` | 4-bit mask: **which gesture phases send this frame** | `unknown` — "varies within a group" (`MCF-FORMAT.md` §5.3) |
| group entry byte 0 high nibble | **hold threshold**, in seconds | undocumented (§5.2 called bytes 0-3 "two leading u16s, `unknown`") |
| group entry byte 0 low nibble | variant cycle length | undocumented |
| group entry byte 2 | three 2-bit **repeat-rate** selectors | undocumented |
| group entry byte 3 bit 7 | also advance the shared/global variant | undocumented |
| binding entry `+0x02` bit 2 | this frame repeats, not just fires once | part of the `unknown` "variant selector" byte |
| binding entry `+0x02` high nibble | variant, **matched against a runtime counter** | `unknown` — §5.2 open question 9 |

Tooling: `src/fireflymcf/gestures.py`, `tests/test_gestures.py`,
`python3 -m fireflymcf.cli gestures <image>`.

---

## 1. The two dispatch classes

A touch resolves to a **touch id**, and there are two entirely separate things that id can be.
Getting this wrong is the easiest mistake here, so it comes first.

```
      touch (x,y)
           |
   GuiCode entry2 = 0x00247B80 ── calibrate, clamp, hit-test ──> id  (or -1)
           |
           +-- id <  group count (233 on 13.6) --> LCD Firmware phase machine
           |                                       --> binding group --> CAN frames
           |
           +-- id >= group count                --> GuiCode action dispatcher
                                                    (screen nav, config writes, popups)
```

* **Class 1 — binding groups.** Everything in this document. The LCD Firmware's control
  scanner bounds its loop with the group count (`ldrb r3,[r8]` / `cmp r3,r4` / `bhi` at
  `0x0021BAD4`-`0x0021BADA`), so an id at or above it can never reach the phase machine.
* **Class 2 — GuiCode action ids.** These run their own timers off `GuiCodeState+0x204`, a
  millisecond clock. CLEAR EEPROM is id `0xB5F` and its handler adds `0xBB8` = **3000 ms**
  (`addw r3,r3,#0xbb8` @`0x0024EC54`), which is exactly what its own screen text promises.
  Section 7 is the full mechanism.

### `state+0x20A` is a screen id, and the mapping is a TABLE — ~~not~~ the object index minus 4

> **RETRACTED 2026-08-23, on the coach.** This section said the screen id is `object − 4`. It is
> not. The owner held the gear icon for 5 seconds, got **SERVICE MODE**, and photographed it;
> `−4` predicts AUTO ENGINE START ENABLE. Read `layout.screen_object_map` instead — and never
> compute a screen id.

**The real mechanism.** `GuiCode_entry1` always calls the screen setup as `FUN_00258650(0, …)`,
so a screen is not a root: object 0 → object 1 is a single **type-6 value-match switch**
(`value_slot 2`, `child_base 3`, `count 47`) whose candidates are every screen object and whose
match values are the ids written to `state+0x20A`. The mapping is therefore **data**, in the
match table at GuiData `+0x4562C`, and `layout.screen_object_map(data)` reads it.

**Why `−4` looked right for four years of reading.** The ids are contiguous over exactly the
range the nav rail exercises — screens 0-4 → objects 4-8, HOME / ELECTRICAL / LIGHTS / CLIMATE /
SETTINGS — and that is where the rule was derived. Screen **6 does not exist**, so from object 10
onward every prediction is off by one, and more ids are missing later (35-38). The anchor was
ambiguous too: objects **3 and 4 both render HOME** (object 3 is the `0xFFFF` no-match default),
so the rail could not distinguish `+3` from `+4`.

| screen | object | | screen | object |
|---|---|---|---|---|
| 0-5 | 4-9 | | 17 | **20 SERVICE MODE** |
| 7-16 | 10-19 | | 34 | 37 GO POWER! CONFIGURATION |
| 18-34 | 21-37 | | **39** | **38 PD Inverter** |

**The cost of the wrong rule, recorded because it nearly shipped.** reference build v7
aimed its new hold at "screen 34, the PD Inverter page". Screen 34 is GO POWER! CONFIGURATION;
the PD Inverter page is screen 39. The build was gated, tested and committed with the wrong
target, and only the owner's photograph of a *different* screen exposed the rule behind it.
**Pass object indices, not screen ids** — `layout.screen_for_object` does the lookup, and an
object index is checkable by rendering it.

`gestures.is_group_id(data, id)` answers which class an id is; `gestures.touch_ids(data, obj)`
lists the ids a screen object registers.

### When a class-2 handler fires: the ID-CHANGE EDGE — on PRESS for 22 ids, on RELEASE for the rest — `proven-from-code`

> **CORRECTED 2026-10-19 (`GUICODE.md` Finding 83).** This section said every class-2 handler
> runs on touch-down. Only the **22** ids of the first search do (13.6: `0xFF`, `0x7E8`, `0x80C`,
> `0x838`, `0x906`-`0x90C`, `0xB5F`-`0xB63`, `0xBC3`, `0xBD0`, `0xFA0`, `0xFA1`, `0x1795`,
> `0x1C90`). The `0xFF` handler -- finger lifted -- clears the two deadlines and then runs a
> **second binary search on `r2`, the id latched on the previous pass**, which still holds the id
> that was under the finger: `movw r3,#0x846` / `cmp r2,r3` / `beq.w 0x24CB4E` at `0x0024B024`.
> `r2` is loaded at `0x0024ACB8` (`ldrh r2,[r2,#0xe]`) and the search path to the `0xFF`
> handler writes only `r1`/`r3`. **108** ids are dispatched there, on release: every OPTIONS
> row, the nav rail, the floorplan picks, `Tank Heater` (`0x851`), `0x838`'s tap. So "a
> release-edge handler keyed on the latched previous id, which nothing implements" (below) is
> wrong: the stock dispatcher is mostly that. It also explains the `cmp r3,#0x11` guard on
> `0x838`'s tap at `0x0024C8B0` (§7): the tap runs at lift, after a completed hold has already
> written screen 17. `sendmap.touch_dispatch` walks both searches; the table below holds for the
> 22 press ids. *Prediction:* an OPTIONS row's `1FED9` write leaves the bus when the finger
> lifts, not when it lands -- hold the row for three seconds and the frame comes at the lift.

Added 2026-08-23. §1 said which ids reach the GuiCode action dispatcher but never said *when* it
runs, and the answer constrains what a class-2 control can be made to do.

The dispatcher is gated on the touch id **changing**, and the handler that runs is the one for the
**new** id:

```
0x0024ACB2  ldrh.w r3,[r4,#0x208]        ; the current touch id
0x0024ACB6  ldr    r2,=0x20030190
0x0024ACB8  ldrh   r2,[r2,#0xe]          ; the id latched on the previous pass
0x0024ACBA  cmp    r3,r2
0x0024ACBC  beq.w  0x0024B2A6            ; UNCHANGED -> skip the whole dispatch
            ...binary search on r3 -> handler...
0x0024B29E  ldrh.w r2,[r4,#0x208]        ; the shared tail every handler branches to:
0x0024B2A4  strh   r2,[0x2003019E]       ;   re-latch the id
```

So, for a region carrying a class-2 id:

| event | id transition | what runs |
|---|---|---|
| finger lands on it | `0xFF` -> `id` | **the id's handler, once — on touch-down** |
| finger held still | `id` -> `id` | nothing (`beq` skips); handlers never repeat |
| finger lifts | `id` -> `0xFF` | the `0xFF` handler (`0x0024B014`), which zeroes both deadline cells `0x200398C0` and `0x200398A8` — this is what makes an early lift cancel a hold |

`0xFF` is "nothing under the finger". The hold consumer (§7) runs *outside* this edge gate and
re-tests the current id, which is why a hold only completes if the finger never leaves the region.

**The consequence, and it is a design constraint, not a detail.** A class-2 region's tap action
fires *before* any hold on that same region can be measured — so a tap action and a hold action
can coexist only when **the tap action is harmless to perform on the way to the hold**. Both
shipped examples respect this: id `0x838` is navigate-then-navigate (landing on screen 4 and then
17 is invisible), and id `0x7E8` has no tap handler at all.

It follows that **a region cannot be "tap = do X, hold = do Y" for any X with a side effect** —
e.g. tap to toggle a load, hold to open a page: the load would switch every time you reached for
the page. Nor does swapping them help. If the tap navigates, the navigation changes what sits
under the finger, the id changes, and the hold consumer's "id still matches" test fails — a
navigating tap **cancels its own hold**. Giving one region a side-effecting tap *and* a hold needs
new GuiCode, not a data edit (the release-edge dispatch this sentence said nothing implements
exists -- see the correction above -- but a hold consumer on a release id still sees the tap
after the hold, so the composition is a GuiCode change either way). The buildable alternative is the tank-cluster pattern: put the hold on a
**separate, non-overlapping** region beside the control.

### How a widget's id reaches the hit test — `proven-from-code`

A type-12 (touch) record's trailing `u16` is decoder field **R50** (`records.decode_record`,
mask `aed & 0x1000`, i.e. widget type 12 exactly). `FUN_00252CB0` stores it at render-struct
`+0x94` (`strh.w r0,[r3,#0x94]` @`0x00253D5A`). `FUN_00256B84` copies it, with the record's
box, into a **371-entry hit-region table** at `state+0x707C`, stride 12
(`strh r1,[r3,#0x7084]` @`0x00256BF4`; the box comes from `state+0x2a..+0x30`, and
`FUN_00250E12` normalises each coordinate pair). The hit test scans that table for a box
containing the point and returns the **lowest** matching id (`0x00258BB2`-`0x00258C26`).

That closes a standing gap: `MCF-FORMAT.md` §8.3 recorded that "the instruction that copies a
record's `+0x0E` u16 into `state+0x208` has not been located". The copy is not one
instruction — it is this table, and the id makes a round trip out through GuiCode's export 2
and back in.

---

## 2. The gesture phase machine — `FUN_0021BAB4`, `proven-from-code`

The LCD Firmware keeps a **16-byte state record per control**, indexed by the control (== group)
index, based at SRAM `0x20006E6C`:

| offset | meaning |
|---|---|
| `+0x00` | u32 — hold deadline (absolute ms), 0 = not running |
| `+0x04` | u32 — next-send time (absolute ms), 0 = nothing scheduled |
| `+0x08` | last input level seen |
| `+0x09` | **current phase**, 1-8; 0 = idle |
| `+0x0A` | per-control variant counter |
| `+0x0C` | "already fired once in this phase" flag |
| `+0x0D` | gate for phases > 4 |

A sibling struct at `0x20006E5C` holds the table pointers: `+0x04` group table, `+0x08` binding
table, `+0x0C` a count, `+0x0E` the **global** variant counter.

### The phases

Each tick the scanner reads the control's input level and compares it with the last one.

```
   level 0 -> 1                level 0 -> 2
        |                           |
     phase 1  (press)            phase 5
        |                           |
   hold deadline expires       hold deadline expires
        |                           |
     phase 3  (held)            phase 7
```
and a release maps `1->2`, `3->4`, `5->6`, `7->8`.

| phase | set at | meaning |
|---|---|---|
| 1 | `0x21BD72` | pressed, before the hold threshold |
| 2 | `0x21BC58` | released from 1 — a **short tap** |
| 3 | `0x21BDB0` | hold threshold passed — **held** |
| 4 | `0x21BCB6` | released from 3 — **release after a hold** |
| 5-8 | `0x21BDA2` / `0x21BDB8` / `0x21BCF4` / `0x21BD2E` | the same four, entered from input level 2 |

**Phases 5-8 test exactly the same byte-3 bits as 1-4.** The sender's `tbb` table at
`0x0021B95C` reads `04 4b 0a 4f 04 4b 0a 4f` — the second half repeats the first. That is why
one 4-bit mask covers all eight phases, and it is a fact about the bytes, not an assumption.

*Input level 2's source is not pinned.* Levels come from two getters — a bit-per-index switch
mask (`0x0021A034`, returns 0/1) and a byte array (`0x00216414`, returns 0-255, so it can
return 2). The byte array is adjacent to code that sets one of **ten** slots (`0x00216420`),
which matches the ten-button wireless switch panel, but that is a lead, not a finding.

### The hold threshold

```asm
0x21bd7c  mov.w fp, #0x3e8          ; 1000
0x21bd80  ldrb.w r3, [r3, r4, lsl #3]  ; group entry byte 0
0x21bd84  lsrs  r3, r3, #4          ; high nibble
0x21bd86  mla   r0, fp, r3, r0      ; deadline = now + 1000 * nibble
```

So **hold time = (group byte 0 >> 4) seconds**. Across the whole corpus only three values
occur: **1 s** (the default, 214-217 groups per build), **3 s** (5 groups) and **5 s** (5-9).

### Repeat

After each send the scanner schedules the next one. The interval is a **2-bit index into a
4-entry table** at `0x0021F2A2` = `{0, 250, 500, 1000}` ms, where 0 means "send once and stop":

| group byte 2 bits | applies to |
|---|---|
| 0-1 | phases 1/5 (pressed, before the threshold) |
| 2-3 | phases 3/7 (**held**) |
| 4-5 | phases 2/4/6/8 (released) |

Selected at `0x21BBAC`-`0x21BBCC` by testing `1 << phase` against `0x154` / `0x88` / `0x22`,
then `ldrh r7,[r2,r3,lsl #1]` @`0x21BBD2`.

A binding entry only participates in the *repeats* if **byte 2 bit 2** is set; without it the
frame is sent on the first tick of the phase and then skipped (`ldrb r1,[r7,#0xc]` /
`lsls r1,r2,#0x1d` / `bpl` at `0x21B94C`-`0x21B952`, with the flag set at `0x21BBA6`).

---

## 3. Binding byte 3 — the phase mask — `proven-from-code`

The sender `FUN_0021B8B4` jumps on `phase - 1` and tests one bit of the entry's byte 3:

| bit | phases | arm | meaning |
|---|---|---|---|
| `0x01` | 1, 5 | `0x21B968` `tst r2,#1` | send on **press** |
| `0x02` | 2, 6 | `0x21B9F6` `tst r2,#2` | send on **short-tap release** |
| `0x04` | 3, 7 | `0x21B974` `lsls r2,#0x1d` | send **while held** (and on each repeat) |
| `0x08` | 4, 8 | `0x21B9FE` `tst r2,#8` | send on **release after a hold** |

Only these four bits are ever tested, and only these four are ever set: byte 3's high nibble is
`0` on every real entry in all three builds (the `0xFF` stubs aside) — `test_the_phase_mask_is_four_bits`.

The eight values that occur, and what each means:

| byte 3 | phases | reads as |
|---|---|---|
| `1` | press | fire the instant it goes down |
| `2` | tap | fire only on a short tap |
| `4` | held | fire only once held, repeating |
| `5` | press + held | fire down, then keep firing while held |
| `6` | tap + held | one action on a tap, another while held |
| `8` | release-after-hold | fire only when a hold ends |
| `10` | tap + release-after-hold | fire on **any** release |
| `14` | tap + held + release-after-hold | everything except press |

---

## 4. The control types, and the bytes that make each one

`gestures.GroupBehaviour.kind` names these purely from the phase masks, **for variant 0**;
`kinds` gives every variant separately, because a group's variants can implement different
gestures (§5). Counts below are 13.6 variant-0 kinds. Every group classifies — the full census
is `latch` 88, `tap-or-hold` 50, `momentary` 49, `press-only` 26, `hold-only` 6, `hold-release`
6, `unbound` 5 (the `ff ff …` stubs), `tap-only` 3.

### Light button — tap toggles, hold ramps, release stops (`tap-or-hold`, 50 groups)

Group 17, `10 00 0c 00 …` → hold 1 s, held-phase repeat 1000 ms.

| phases | RV-C command | effect |
|---|---|---|
| tap-release | 5 — toggle | a tap flips the light |
| held (repeats) | 21 — ramp | holding past 1 s starts a ramp, re-sent every second |
| release-after-hold | 4 — stop | letting go freezes the level |

This is the pattern the owner described, recovered from bytes. It is also self-checking: across
**all three builds**, command 4 is sent on `release-after-hold` and nowhere else, and command 21
on `held` and nowhere else. Permute the bit-to-phase assignment any way and that stops being
true — which is what `test_stop_is_only_ever_sent_on_release_after_hold` exists to catch.

Channels carrying a hold-to-ramp button on 13.6: **2, 10, 21, 22, 23, 24, 25, 26, 27, 32, 34,
35, 36, 86**. That is a **superset** of `guidata.dimmable_channels` (21-26, 32, 34, 35 — the
ones ever sent command 0). Recorded as an observation, not a claim: five channels (2, 10, 27,
36, 86) have a ramp-on-hold button but are never sent *set brightness*, and why is not
established here.

### Awning / motor — runs only while held (`momentary`, 49 groups)

Group 3, the AWNING EXT button:

| phases | frame |
|---|---|
| press | channel **4** (RET) command 3 — **off** |
| press + held (repeats) | channel **3** (EXT) command 1 — on, re-sent every second |
| tap + release-after-hold | channel 3 command 3 — off |

Two things worth carrying. The EXT/RET mutual exclusion `CLAUDE.md` records as a group property
("group 3 carries `(4,3)` RET-off *and* `(3,1)` EXT-on, so the pair cannot drive together") is
specifically a **press-phase** emission — the opposite direction is commanded off
*before* this one is commanded on, which is what makes it safe. And the release arm carries
`tap + release-after-hold`, i.e. "any release", so a stray quick tap cannot leave a motor
running.

### Plain latch — Water Pump, Tank Heater (`latch`, 88 groups)

Group 18: one entry, byte 3 = `10` (any release), command 5 (toggle). Nothing on press,
nothing on hold. Holding a latch button does exactly nothing, which matches the coach.

**This is the shape a new button gets by default** (`newbutton.build_home_button` clones a
single command-5 binding) — so the Pin-15 and Starlink buttons are latches, deliberately.

### Hold to confirm — service and safety actions (`hold-only` 6, `hold-release` 6)

Nothing fires on press or on a tap. Two sub-shapes exist and they are not the same gesture:

* **`hold-only`** (byte 3 = `4`) — the frame goes out the moment the threshold passes, while
  your finger is still down.
* **`hold-release`** (byte 3 = `8`) — nothing goes out until you complete the hold *and let go*,
  so sliding off the button is a way to abort.

| group | hold | shape | screen |
|---|---|---|---|
| 64 | **3 s** | hold-only | AUTO ENGINE START ENABLE (object 9) |
| 85 | **3 s** | hold-only | wireless switch pairing — CLEAR (objects 10 / 301 / 361) |
| 36 | 3 s | hold-only | (no located screen) |
| 86 | 1 s | hold-only | (no located screen) |
| 39, 41, 42, 45, 46 | **5 s** | hold-release | per-location pairing (Kitchen, Mid Bath, Rear Bath, …) |
| 171 | **5 s** | hold-release | AUTO GEN START SETTINGS |
| 170 | **5 s** | hold-only (variant 0) / press-only (variant 1) | AUTO GEN START SETTINGS |

Groups 37 and 38 also hold for 5 s and sit on the same pairing screens, but they are **`latch`**,
not hold-shaped: one frame on press, another on any release. Their 5-second threshold is
therefore inert — nothing they emit is gated on it. Recorded because it is the sort of detail a
summary would smooth over.

**The in-file oracle.** Object 9's own caption reads *"Hold the enable button for 3 seconds or
more and then release to enable."* Its touch id is 64; group 64's byte 0 is `0x30`, high nibble
3. The caption is a string in a screen record; the threshold is a nibble in a different table.
Neither was derived from the other, and they agree. Object 769's pairing text —
*"TO CLEAR THE PAIRING, HOLD THE CLEAR BUTTON FOR 3 SECONDS"* — agrees with group 85 the same
way. These two are why the seconds scale is a finding and not a guess.

*One thing the AES caption does **not** confirm.* It says "hold … and then release to enable",
but group 64 is `hold-only` — the frame goes at the 3-second mark, not on release. Read the
caption as evidence for the **duration** only; the phase comes from the byte, and where the two
could be read as disagreeing, the byte is what the firmware executes.

### Master buttons (`tap-or-hold`, no release arm)

Group 1 (Light Master ON) and 216 (Exterior Master ON):

| phases | frame |
|---|---|
| tap-release | command 0 (set brightness), level `0xFB` — above `0xC8`, the **"use remembered level"** sentinel |
| held | command 1 (on), level `0xC8` — full |

So a tap restores each light's remembered level and a hold forces everything to full. Both
groups also set **byte 3 bit 7**, which advances the global variant counter (§5).

---

## 5. Variants — what selects them, answered

`MCF-FORMAT.md` §5.2 open question 9 and `guidata.py`'s "Byte 2 of a binding entry" docstring
both say **"what selects a variant is not known"**. It is now known, and both readings that were
tried before were wrong for the same reason: the selector is not in the file at all, it is a
**runtime counter**.

The sender matches each entry's byte-2 high nibble against a counter
(`cmp sl, r2, lsr #4` @`0x21B946`) and skips anything that does not match. Two counters exist:

* **Per-control** — state record `+0x0A`. Wrapped at the group's **byte 0 low nibble**
  (`and r0,#0xf` / `cmp r0,r1` / `bhs` @`0x21BC6E`-`0x21BC7C`).
* **Global** — table struct `+0x0E`, wrapped at `+0x0D & 0x0F`. Advanced only by groups whose
  **byte 3 bit 7** is set (`ldrsb r3,[ip,#3]` / `cmp r3,#0` / `bge` @`0x21BC7E`) — 184 of 233
  groups on 13.6, the masters among them. It selects entries in the sender's *second* loop,
  which walks a global binding list rather than the group's own slice.

**Two things move the per-control counter, and conflating them would be wrong.**

1. **Pressing the control**, when the group allows it. Group byte 1 bits 6-7 gate this, and the
   gate differs by phase: the short-tap arms (`0x21BC5E`, `0x21BCFA`) skip only on exactly
   `0x80`, while the after-hold arms (`0x21BCB8`, `0x21BD3A`) skip on **any** set bit. On this
   corpus byte 1 is only ever `0x00` or `0x80`, so in practice it reads "cycles" or "never":
   137 of 233 groups on 13.6 cycle.
2. **An external setter.** `0x0021B60A` writes the counter directly (bounded by the same byte-0
   low nibble), and `0x0021B694` bulk-sets it for every control whose **group byte 2 bit 7** is
   set. So for the 96 groups that do *not* cycle, the live variant is chosen by something else —
   configuration, most plausibly.

### 5a. The setters traced — 2026-08-24, `proven-from-code`

Prompted by PC.32 (`Shore Brkr Size`'s `−` emits nothing). This closes "what calls these setters
was not traced" and sharpens the gate; the initial-value question below stays open.

**The setter is one dispatcher, `FUN_0021B5E8(index, variant)`, with three write sites**, and all
of the state table's seven pool references were enumerated to confirm there are no others:

| site | writes | gate |
|---|---|---|
| `0x21B618`/`0x21B61A` | `state + index*16 + 0x0A` **and** `+0x0B` | group byte 1 bits 6-7 **== `0x80`** (`0x21B5FE`-`0x21B604`), then `blo` on byte 0's low nibble |
| `0x21B66A`/`0x21B66C` | same, index from `[struct+0xF]` | the **same** byte-1 gate at `0x21B650`-`0x21B656` |
| `0x21B684`/`0x21B686` | same, index offset by `[struct+0xF]` | — (fragment; see call-site note) |

`index == 0xF` and `0xE` are special arms: `0xF` sets the **global** counter at `[struct+0x0E]`,
gated on `[struct+0x0D]` bits 6-7 == `0x80` and wrapped by its low nibble — the exact mirror of
the per-control rule.

**So the two mechanisms are mutually exclusive by construction, and byte 1 is the switch.**
`0x00` = the counter press-cycles and the setter *refuses to touch it*; `0x80` = press-cycling is
skipped and **only** the setter can move it. A `0x80` group is therefore not a broken group — it is
an **externally-selected** control, authored that way.

**One call site.** A BL scan of the whole image finds exactly one caller of `0x21B5E8`, at
**`0x21B002`** inside `FUN_0021AB88`, and it passes `variant = ([r4+0x108] + sb)[2] >> 4` — a
runtime record field, not a constant. The bulk entry at `0x21B68C` has **zero** callers in 13.6.

*Confirmed in passing:* the group entry's bytes 4-5 are the binding **base index** (u16) and byte 6
the count — group 199 reads base 630, count 3, and bindings 630-632 are exactly its three
emissions. Byte 7 is a **second** count, used by the after-hold path at `0x21B934`, which reads the
**`+0x0B`** counter instead of `+0x0A`. Two counters per control, not one.

**ANSWERED 2026-09-04 (`GUICODE.md` Finding 67, via `raminit`): the state table is inside the
`.bss` zero loop (`0x20000228`–`0x2000AD98`), so every counter — group 199's at `0x20007AE6`
included — is 0 at power-on.** A `0x80` group whose bindings are all variant 0 therefore fires
normally from cold until the setter moves it, and PC.32's cold-boot silence needs either a setter
write that was never seen or the `[state+0x09]` phase-byte route below. *(The paragraph that
follows is the question as it stood.)*

**And the setter's GuiCode side is the answer to PC.32 (`GUICODE.md` Finding 68, 2026-09-05):**
`FUN_00247BF8` calls `setVariant(199, 1)` every tick while receive var 213 (`DGN_CONFIG` `0x95`)
reads 0, and group 199 has no variant-1 binding — a designed disable, wired to the wrong selector.

~~**Still open, and it is the whole question:** what `+0x0A` holds at power-on.~~ The state table is
SRAM (`0x20006E6C`); if it is BSS-zeroed then a `0x80` group whose bindings are all variant 0 fires
normally until something sets it, and PC.32's silent button would need a different explanation —
for instance `[state+0x09]`, the phase byte the sender bails on at `0x21B8CC`, which would produce
identical silence by an unrelated route. **That alternative has not been excluded.**

That distinction matters for reading the data. Group 22 (channel 22, a ceiling light) carries
variant 1 = the full dimmer gesture and variant 0 = a plain "off on any release", and its byte 1
is `0x80` — so pressing it never switches between them; something outside this subsystem picks.
Do **not** read variant 0 as "the default because the counter starts at zero"; the counter's
initial value was not established.

*(Group byte 2 bits 6-7 are `0` on every group of all three builds, so the bulk setter's
selector is never exercised by this corpus — same shape of caveat as `LCD-FIRMWARE.md`
Finding 12.)*

**This is what makes channel 35's master membership unanswerable from the file** — it depends on
a counter's runtime value, which is why `answer_key`'s CAN capture stays the direct evidence.
The existing caveat in `guidata.py` was right for a better reason than it knew.

`gestures.describe_group` reports `variant_count`, `cycles_variant` and `advances_global`;
`Emission.variant` tags each entry; `kinds` gives the gesture of each variant separately,
because they genuinely differ (group 0 fires on `held` in variant 0 and on `press` in variant 1).
The module **does not** predict which variant is live, because nothing in the file does.

---

## 6. What this does not cover

* **Which variant is active** — runtime, see §5.
* **Input level 2** (phases 5-8) — the code path is proven, the physical source is not (§2).
* **Class-2 action ids** beyond the three hold timers located: `0xB5F` → 3000 ms,
  `0xBC3` → 3000 ms (object 44, a WARNING screen), `0x1795` → 1000 ms (no located object).
  Other GuiCode timeouts exist (`0x7D0` = 2000 ms @`0x00247F1E`, `0x1F4` = 500 ms
  @`0x0024EA42`) but their guarding ids were not traced.
* **Which touch ids are "protected touch areas" beyond the two found** (§7). The arming sites
  for the shared deadline are `0x0024B294` (5000 ms), `0x0024B008` (5000 ms), `0x0024B7E6`
  (1000 ms, id `0x1795`, no located object) and `0x0024EBB6` (5000 ms, a re-arm); only the
  first two have been tied to an id and a screen.
* **The group-`0x94` messages.** `MCF-FORMAT.md` §8.3 calls these "the most option-shaped thing
  left in the file" and says what they do "is not understood at all". One new fact, stated at
  the size it deserves: every emission of every long-hold (3 s / 5 s) group on 13.6 is a
  `0x1FED9` frame, and its payload byte 1 is `0x94` (19 entries) or `0x96` (19), never anything
  else — function bytes `0xE0`, `0xE3`, `0xE5`, `0xD1`, `0xD3`, `0xE4`, `0xCF`. So the `0x94`
  family is **among what a hold-to-confirm control sends**, sharing that role with `0x96`. That
  narrows where to look; it does not decode either family, and it says nothing about the `0x94`
  frames emitted by controls that are *not* long-hold.

---

## 7. Protected touch areas — hidden screens behind a hold — `proven-from-code`

*Found 2026-08-19 from the owner's report: "getting to the probe tank diagnostics can be done by
touching and holding anywhere in the area of the 4 tanks. It doesn't seem to be tied to a
specific tank icon." Both halves of that are in the bytes.*

**It is one hit region, not four — but it is not the whole quadrant either.** HOME object 4
registers a type-12 record with box `(485,49)-(745,179)` carrying touch id **`0x7E8`**. There is
no per-tank hit region; the individual gauges register nothing. The box hugs the **well
cluster**, not the panel behind it:

| element | box |
|---|---|
| quadrant panel (obj 919) | `(435, 49)-(790,254)` |
| **hit region** (id `0x7E8`) | `(485, 49)-(745,179)` |
| the four wells (obj 719/721/723/725) | x `482`-`743`, y `76`-`161` |
| the `%` readouts | y `178` |

So there is dead space **inside the panel**: ~50 px left of the region, ~45 px right of it, and
everything below y=179 — where Water Pump and Tank Heater sit, each with its own hit region.
Holding the panel to the left or right of the tanks does nothing. Confirmed on the coach by the
owner (2026-08-19), and the reason this paragraph no longer says "the whole top-right quadrant",
which was an overstatement in the first draft.

**The hold is 5 seconds**, and it uses the class-2 timer, not the LCD phase machine:

| step | code |
|---|---|
| id `0x7E8` arrives → arm `now + 0x1388` (**5000 ms**) in `0x200398A8` | `0x0024B288`-`0x0024B29C` |
| id `0xFF` (finger lifted, nothing under it) → clear the deadline | `0x0024B014`-`0x0024B022` |
| deadline passes **and the id is still `0x7E8`** → write `0x18` (24) to `state+0x20A` | `0x0024E8EC`-`0x0024E90A`, `0x0024EB50` |

Screen 24 is object **27**, `PROBE TANK DIAGNOSTICS` *(object 28 under the retracted
`−4` rule; the behaviour described here was right, the object number was not)* (§1's `+4` mapping). So the owner's
observation reproduces from the file exactly, and it is what exposed the `+0x20A` numbering
error that §1 now records.

**Service Mode removes the delay, and the firmware says so.** A second arm for the same id
(`0x0024C7E8`) is gated on the byte at SRAM `0x2003A528`: when it is `1`, a plain **tap** jumps
straight to screen 24 with no hold at all. That byte is written from the received configuration
payload at offset `+0x12` (`ldrb r1,[r3,#0x12]` @`0x00247C72`), the `FUN_00247BF8` path Finding 19
traced. ~~so Service Mode is **bus-delivered**, not set locally.~~ **RETRACTED — `BUS-BASELINE.md`
PC.36, 2026-08-24.** The offset is right and confirmed on the wire (the panel writes
`19EE009F#120001` / `120000`), but **nothing answers**: the G12's `1FED8` is periodic and
byte-identical either side of a toggle, no selector moves, and the screen changes anyway. The user
toggle does **not** round-trip. *Why the reasoning failed: it rested on a pool-literal scan finding
one writer of `0x2003A528`, and a write through a computed address never appears as an immediate —
the same trap as `CLAUDE.md` defect 0a. An argument from "only one writer" is only as good as the
guarantee that a second would have been visible.* A **third** reference exists at `0x0024EBC0`
(read), also missed by the original pass. And object 20's own caption reads:

> *"Service Mode will remove the delay for protected touch areas."*

"Protected touch areas" is the firmware's own name for this mechanism, and "the delay" is the
5-second arm. That caption is an independent confirmation of the decode, in the same shape as
the AES screen's "3 seconds" confirming the group-byte nibble.

**The second protected area found:** the SETTINGS nav icon, box `(0,375)-(60,434)`, id `0x838`.
A tap writes screen 4 (object 8, SETTINGS); a **5-second hold** (`0x0024B008`) writes screen 17
— which is object **20, `SERVICE MODE`**, not object 21. *(Corrected 2026-08-23: this said
`AUTO ENGINE START ENABLE`, from the retracted `object − 4` rule. The owner photographed
SERVICE MODE. That hold is also the coach's **only** route to Service Mode — id `0x7E1`, the
other writer of screen 17, is registered solely by objects 92/690/826, none of which is attached
to any page.)* The tap handler's `cmp r3,#0x11` guard at `0x0024C8B0` corroborates it: the tap is
suppressed when you are already on screen 17, which only makes sense if that is where the hold
lands. A third arm exists at `0x0024B7E6` for id `0x1795` with
a **1000 ms** delay, but no object on 13.6 registers that id, so what it protects is unknown.

*This also resolves the "id 2016 → screen 16" contradiction reported earlier.* Screen 16 is
object 20, `SERVICE MODE`, so a Service Mode button legitimately writes 16. The apparent problem
was a label-association error on the reading side — pairing each touch record with the following
caption rather than the preceding one — not a defect in the mechanism.

---

## 7a. Where a glyph lands vertically — `observed-on-hardware`, 2026-08-19

*2026-08-19. Written twice: the first version blamed `render.py` and was wrong. The renderer is
right; the authored artwork broke a convention the file follows everywhere.*

A `wtype 11` label is drawn by the wrapping text drawer, and its glyph lands at:

```
block_top  = box_y0 + ((box_y1 - box_y0 + 1 - font[0x07]) >> 1)     ; v-align 2, centre
glyph_top  = block_top + glyph[0x0c]                                ; the y-bearing
```

| step | code |
|---|---|
| block height for one line is the **font cell**, not the glyph | `ldrb r1,[r3,#7]` @`0x00255C30` |
| centre arm, with **no guard** against a negative result | `0x00255D3C`; the `bmi` @`0x00255D46` adds 1 and falls through |
| the blitter adds the glyph's y-bearing (both the 1bpp and 4bpp paths — 4bpp rejoins at `0x251130`) | `ldrb r1,[r3,#0xc]` / `add r2,r1` @`0x00251138` |

**Why the cell and not the glyph.** Because the file compensates in the *bearing*: every
icon/symbol font sets `y = (cell - height) / 2`, so the two terms cancel and the glyph centres in
the box. Zero is the commonest delta in slots 23, 24, 25, 9 and 19 on all three builds, with a
handful of genuine strays (`test_icon_fonts_set_the_y_bearing_to_centre_the_glyph_in_its_cell`).

**The calibration.** CLIMATE's setpoint chevron: slot 25, cell 72, glyph 28, bearing 22 =
(72−28)/2, box y 140–195. The formula gives **154** — and `render.py`, centring independently by
the glyph's own bitmap, also gives 154, which is where `CLIMATE.jpg` shows it. Two models, one
answer, one photo. They agree *for any glyph that follows the convention*, and only diverge for
one that does not.

**How the Starlink logo broke it.** Slot 27's cell is **128** against a 61 px button box, so
`block_top` is 33 px **above** the box. The authored glyph carried `y = 0`, so it drew at 355
instead of 392 — the owner's "placed too high". A first fix of `y = 3` moved it three pixels
against a thirty-three pixel error, which reads as *"the image did not move"*. The derived value
is `y = (128 − 54) / 2 = 37`, giving 392: 4 px above the glyph, 3 px below.

**Slot 27's own four Go Power! glyphs sit at 3/4/7/17 and do *not* follow the convention** — and
that is consistent rather than contradictory: object 1230 draws them with **v-align 1 (top)**,
where the centring never runs and the bearing is a plain offset from the box top. 19 of slot 27's
23 glyphs *do* follow it. Reading those four as the slot's habit is what produced the wrong first
fix.

**Rule for authoring a glyph:** set the y-bearing to `(font cell height − glyph height) / 2`
unless the record draws with v-align 1, in which case it is a direct offset from the box top.
`builds/from_stock.py` asserts this against the font's own cell rather than hardcoding 37.

**Confirmed on the coach, 2026-08-19** (owner, `Custom v22`): with `y = 37` the logo sits centred
in its button. Three flashes were needed and each ruled something out — v20 (`y = 0`) showed the
error, v21 (`y = 3`) showed that a nudge sized by eye is invisible against it, and v22 (`y = 37`,
derived) fixed it. The lesson is the project's own: the formula was recoverable from the code the
whole time, and two rounds were spent calibrating instead of deriving.

---

## 8. Consequences for building controls

* A cloned button inherits its donor's **group**, and therefore its whole gesture model. Cloning
  a light gives you a control that ramps on hold; cloning Water Pump gives a latch. Neither
  `newbutton` nor `statusbind` currently reasons about this.
* **Making a new button hold-to-ramp is a same-size edit**: set the group's byte 2 bits 2-3 to a
  non-zero repeat index and give it binding entries with byte 3 = `4` (plus byte 2 bit 2) and
  `8`. It needs binding-table space, so it is a relocator job, not a byte edit, unless spare
  entries exist in the group's slice.
* **Changing a hold threshold is one nibble** — group entry byte 0. Note the AES-enable screen's
  caption says "3 seconds" in text; changing the nibble without editing the caption would make
  the firmware lie to the user.
* A control that should *never* act on a stray touch wants byte 3 = `4` or `8` (hold-only), not
  `2`. The firmware already uses exactly that for its own destructive actions.
* **A new control's group must not be one GuiCode drives by number** (observed on hardware, v45,
  2026-09-30). 13.6 calls `setVariant` on groups 130-139 as a block (`0x24AC66`-`0x24ACB0`); no
  stock touch region uses 136-139, so `newbutton.free_group_slots` offers them. Minted there, a
  tap still sends, but a hold never registers: v45's Track + (136) did not repeat and its
  hold-only reboot (137) sent nothing, while identical controls on 96/126/127 worked. The mechanism
  is not traced (the setter refuses a byte-1 `0x00` group, so it is not its write). Allocate holds
  from `newbutton.hold_safe_groups`; `recipe._assert_no_guicode_selected_group_reused` gates reuse.

### Hold-to-toggle — `gestures.make_hold_to_toggle`, CONFIRMED ON HARDWARE 2026-08-19

"Hold 3 s to switch it on, hold 3 s to switch it off" is **three same-size bytes** on a
single-entry command-5 group, and `builds/from_stock.py` step 9 applies them to the Starlink
(pin 29) button:

| byte | from | to | why |
|---|---|---|---|
| group `+0x00` | `10` | `30` | hold threshold 1 s → 3 s |
| binding `+0x03` | `0a` | `04` | any-release → **held** only, so a tap does nothing |
| group `+0x02` | `0c` | `00` | held-phase repeat 1000 ms → send once |

The third byte is defence in depth, not necessity: the binding already lacks byte 2's repeat bit,
so the sender would skip it after the first send. But if that reading is wrong, the failure mode
with a live repeat rate is **a real output toggling once a second for as long as a finger rests
on the button**. Zeroing the rate makes that unreachable, and costs nothing — entering the held
phase sets the next-send time directly (`0x0021BB64`), so the first send is unaffected.

`make_hold_to_toggle` refuses rather than guesses when its premises fail. A single-entry group's
binding must be a `DC_DIMMER_COMMAND_2` command 5 and must not already repeat.

**A multi-entry group is converted only when every live copy can take the same edit**, because
a **runtime variant counter** picks which copy fires and an edit to some copies leaves the others
on the old gesture. Five premises, each refused separately (`gestures._make_multi_hold_to_toggle`):
no copy repeats; every copy sends one DGN to one target (command, selector, instance); the
variants agree on one release toggle (`latch` or `tap-only`); the variants pick the direction
(two variants whose position-paired copies differ only in the variant nibble and, for `0x1FED9`,
the state byte, with 0 and 1 in at least one pair; or every copy a command-5 toggle); and the
group does not cycle its variant on release (byte 1 `0x80`), so the external setter alone (§5)
picks the copy. Then `PHASE_HELD` goes on **every** live copy, the hold threshold and the held
repeat once on the group. On 13.6 groups 82, 88 and 129 pass (each a `0x1FED9` selector write
of 0/1 plus its read-back); group 129 is Low Volts, whose variant GuiCode sets from selector
`0x2F` (`0x0024A176`). **Confirmed on the coach, 2026-10-19** (owner): group 129 with a 3 s
hold, pressed from the compact AES ELECTRICAL page, works. AGS mode and groups 82/88 have not
run.

**Confirmed on the coach, 2026-08-19** (owner, build `Custom v20`): a 3-second hold turns pin 29
on, another turns it off, a tap does nothing, and holding past the threshold does not re-toggle.
Pin 15, deliberately left a plain latch in the same build, still switches on a tap — so the change
was surgical rather than a global shift in how buttons behave.

That single trial promotes three separate decodes from `proven-from-code` to
`observed-on-hardware`: binding byte 3's **bit `0x04` is the held phase**, group byte 0's high
nibble is **seconds**, and the repeat gating really does emit **one frame per hold**.

It was worth flagging beforehand that no group in any corpus build sends a DC_DIMMER command on
the held phase, and no hold-only group drives a load at all — every shipped hold-to-confirm
control emits `0x1FED9` config frames. This composition followed from the decoded mechanism with
no shipped precedent, which is exactly why it needed the round-trip.

**A second pin, and that is what makes it general — 2026-08-21** (owner, reference build v4). The same three bytes applied to the **Overflow Valve** on **pin 28** behave identically: 3 s
on, 3 s off, a tap does nothing. One confirmed instance of a precedent-free composition leaves
open whether it worked for a reason particular to that button — a different group, a different
pin, a button built by a different route (`build_home_button` rather than `wire_button` on a
cloned glyph shell) closes that. Two details make it a stronger trial than a repeat:

- **The static prediction was exact.** `gestures.describe_group` reported `kind=hold-only`,
  `hold_ms=3000`, one `(5, 28)` emission on the held phase, `repeats=False`, nothing on tap —
  read back out of the finished image before it was flashed — and the coach then did precisely
  that. `describe_group` is therefore validated as a *prediction of behaviour*, not only as a
  decode of bytes.
- **It coexists with a stock group on the same pin.** Pin 28 is also named by **group 94**, the
  lock/unlock slide-out pairing with pin 27 (commands 33/34 are RV-C Lock/Unlock, PC.27), which that build deliberately leaves intact. The
  valve's single-entry group being the one that fires is the runtime answer to the question
  `make_hold_to_toggle`'s single-live-entry assertion can only ask statically.

## 9. Adding a HOLD beside a tap — the region split, CONFIRMED ON HARDWARE 2026-08-23

Owner request: keep the HOME inverter button's tap (on/off) and add a 3-second hold that opens
the PD Inverter page. §1's edge rule says that cannot be one region — the tap fires on touch-down,
so the inverter would switch every time you reached for the page — and swapping them is no better,
because a navigating tap changes what is under the finger and **cancels its own hold**. Two
non-overlapping regions are the way, and overlapping ones are useless: the hit test returns the
**lowest** matching id, and every class-1 id is below every class-2 id.

`src/fireflymcf/holdregion.py`, shipped in reference build v7. The block's single
221 px region becomes a 144 px tap `(197,382)-(340,439)` and a 73 px hold `(345,382)-(417,439)`,
4 px apart.

**v8 changes only where the hold goes**: `INVERTER / CHARGER SETTINGS` (object **17**, screen 14)
instead of the read-only `PD Inverter` readout (object 38, screen 39). The regions, the id
`0x1795`, the 3000 ms delay and the dispatch stub are untouched — it is one constant inside the
stub, and `holdregion.hold_dispatch_map` reads all three holds back from the finished image. The
seven controls that made object 17 the better target are decoded in `GUICODE.md` Finding 51.

**The GuiData half.** A touch record is `<8-byte header><u16 w><u16 h><u16 id>`, header byte 2 is
the geometry presence mask (bit 0 adds a leading `u16 x`), so the inserted record is 16 bytes —
already 4-aligned, which `relocate` requires. Three things had to be right:

1. **An object's first byte is its RECORD COUNT.** Insert without bumping it and the decoder stops
   one record early; the boundary oracle reports the object ending short. This is what the first
   attempt got wrong, and it is a one-byte edit.
2. **An inserted record advances the pen by its own x**, shifting every later record in the
   object. The fix is to subtract the same value from the *next* record's x — one same-size `u16`.
3. **Positions are flow-relative**, so where a region lands is a property of the layout replay,
   not of the bytes. Everything is verified by re-walking the finished image.

**The defect worth carrying, because it shipped through a full green build.** The first working
version placed both hit regions *exactly* where they were asked to and moved the block's label and
boxes 148 px right, off the panel and over the battery quadrant. Every gate passed — boundary
oracle, deep coverage, pointer repair, capability gate — and so did the build's own assertion,
because it checked the two regions and nothing else. It was caught by *looking at the render*.
`split_touch_region` now asserts that every **non-touch** element of the object lands where it did
before (`_drawn_placements`), and v7's HOME render is pixel-identical to v6's. **Verifying only
what you set is not verification; check what you did not intend to change.**

**The GuiCode half**, three same-size edits:

| site | stock | v7 |
|---|---|---|
| `0x0024B7E6` | `add.w r3,r3,#0x3e8` (1000 ms) | `addw r3,r3,#0xbb8` (3000 ms) |
| `0x0024E90E` | `movw r2,#0x838` | `movw r2,#0x1795` |
| `0x0024E916` | `movs r3,#0x11` (screen 17) | `movs r3,#0x22` (screen 34) |

The 3000 ms encoding is **lifted byte-for-byte from the CLEAR EEPROM timer** at `0x0024EC54`
rather than assembled, and `holdregion.encode_movw`/`encode_movs` are checked against the two
instructions already in the image before being trusted with new immediates.

**Why id `0x1795` needs no arm edit.** §7 called it a third arm "for id `0x1795` … no object
registers that id, so what it protects is unknown". It is reachable as an ordinary leaf of the
dispatch tree — `0x24ACD8 bhi → 0x24B164 bhi → 0x24B1C0` for any id > `0xFA1` — and its arm at
`0x24B7DC` stores a deadline into the shared cell `0x200398A8`. Nothing consumed that deadline,
which is why it did nothing. Registering a region with that id and pointing a consumer branch at
it is the whole wiring. *What it protects is now answered: nothing — it was a complete arm with no
consumer, i.e. spare capacity.*

**No trade, in the end — and the reasoning that said there had to be one was wrong twice.**

The consumer chain has two arms and no room for a third, and GuiCode cannot **grow** (no
instruction relocator: every branch offset and literal-pool reference is position-dependent).
Both facts are true. The conclusion drawn from them — "so an existing shortcut must be
sacrificed" — was not, and it was quietly load-bearing: it was presented to the owner as a
three-way choice, none of which was necessary.

*Wrong the first time* in which arm to take. The plan was to repurpose `0x838`, on the grounds
that its screen "keeps an ordinary button route". It does not: id `0x7E1` is registered only by
objects 92/690/826, **none attached to any page**, so that hold is the coach's only route to
SERVICE MODE and taking it would have removed Service Mode entirely. (The `0x7E8` arm would have
been the cheap one — PROBE TANK DIAGNOSTICS is also reachable from HELP, ids `0x232F` on object
26 and `0x233C` on object 43.)

*Wrong the second time* in thinking a choice was needed at all. GuiCode cannot grow, but it does
not have to: **removing the option interlocks left four 36-byte runs of NOPs**, and a dispatch
stub fits in one —

```
0x24CD24  b.w 0x24CD48         step over the stub; the Progressive Inverter tap handler
                               still falls straight through to its own tail
0x24CD28  movw r2,#0x838 / cmp r3,r2 / bne .. / movs r3,#0x11 / b write
          movw r2,#0x1795 / cmp r3,r2 / bne out / movs r3,#0x27
   write: strb.w r3,[r4,#0x20a]
     out: b.w 0x24E91C          rejoin the consumer
```

The consumer's `0x838` arm becomes `b.w` to the stub, which re-implements it **and** adds the
new one. `0x7E8` is untouched. All three holds work: tank → PROBE TANK DIAGNOSTICS, gear →
SERVICE MODE, inverter strip → PD Inverter. `holdregion.hold_dispatch_map` reads the result back
out of the finished image by walking the chain and following the stub, and the build asserts all
three resolve to the right *objects*.

**This is the project's first authored machine code.** `src/fireflymcf/thumb.py` encodes it;
every encoder is validated against an instruction already in 13.6 (so it is checked against
Firefly's own compiler, not against my reading of the ISA), and every emitted instruction is
disassembled back and compared with what it claims to be. The stub is entered from exactly one
branch, and the step-over is asserted — without it, tapping the Progressive Inverter row would
execute the stub with whatever was in `r3` and write a garbage screen id.

**And Service Mode bypasses it**: with `0x2003A528 == 1` the protected-area delay is removed, so
the hold becomes a plain tap (`0x0024C7E8`). Firmware behaviour, not a defect of this edit.

---

## 10. Moving a control to another page — `move_control`, UNVERIFIED

A control is three records (draw, touch region, box), and a touch id maps to its group
**globally**: `FUN_00256B84` copies the id into the hit-region table whatever page hosts the
record (§1). So a control moves between pages by moving its records; no group, binding or DGN
changes, and the moved control runs the same action. What breaks is **layout**, which is
flow-relative (`FUN_00257BFC`, `layout.py`): each record lays out from the frame the previous
drawn record left, and inherits font, align and colours from its predecessors.

`move_control` does the whole move as one recipe op and measures it with `pagewalk` (the layout
replay, every record keyed by its `(object, record)` path from the page root, every switch arm
walked):

* **Source.** It removes the records (`remove_records`' removal), then re-walks the page and
  puts back what the removal moved: the first moved record's x/y geometry field takes the shift
  back, and the records placed from it return with it. It refuses (`CANNOT_COMPENSATE`) when the
  moved record has no x/y field or no record of the source object places it, and
  (`STATE_CHANGED`) when the removal changed a surviving record's inherited font, align, colours
  or value slot: fixing its position would leave it styled wrong.
* **Destination.** It pastes the records as a new object attached to the destination
  (`insert.insert_object`, whose attach is a byte clone of the destination's first wtype-5
  record; refused if that clone carries a value slot), re-encoded with absolute x, y
  (`absrecord.absolute`), the first font field and the first non-zero align made explicit,
  register fields dropped. A record that only inherits a value slot from an attach that stays
  behind is refused. A byte-shared child
  (the button face behind an attach) is attached, never cloned, and the attach keeps its
  `value_slot`, so the face repaints from the same cell (`OBJECT-HIERARCHY.md` §1a).
* **Assertion.** It re-walks both pages and refuses unless every other record is where it was
  and the moved records sit at their source boxes translated to `at`, styled as they were.

On the reference coach's compact AES card (object 1427), moving Low Volts (the attach of child
285, `value_slot` 45, and touch id 129) to the AES settings page (object 1425) takes one
compensation edit: the "AES ⚙" gear's y field gets back the +29 px the removed attach carried.
The pasted attach needed font 14 and align 1 made explicit (the settings page leaves font 9 and
align 3 in effect where the paste lands); its colours inherited correctly. The result draws Low
Volts at the boxes the flashed LV-move build draws it at (`tests/test_move_control.py`).

It has not run on a coach. The halves it reuses have (`remove_records` and `insert_object`,
flashed in LV-move, 2026-10-19); the generic re-encoding and the automatic compensation have
not, so `hardware_verified` stays False until a build made with it is flashed.
