# The levelling page (screen 29) — what it sends, what it listens for, and why it stays dark

**Status:** page decode `proven-from-data`; the `0x1FFEE` payload layout `inferred-from-data`
(the RV-C spec names the DGN but carries no parameter list); the display-cell negative
`proven-from-code` with the caveat in §4. Reached on the coach for the first time by
`mcf-experiment-v14.bin`, 2026-09-03.

Screen 29 is the second page titled `SLIDES/AWNINGS` — the auto-levelling page. It is orphaned in
stock: its only ACTION leaf is `0x7ED` and **no `wt-12` region in the image carries that id**
(`navmap`), so nothing can navigate to it. v14 reveals the nav rail's authored sixth icon and
points its `0x7DC` leaf here (`navicon`, the reference coach's build log (not published)).

The owner's interest is **indication, not actuation**: publishing levelling/attitude data onto
RV-C and having the coach show how level it is. This document is the brief for that.

## 1. What the page transmits

Eleven controls, all `momentary` with a 1000 ms repeat while held. Every one emits on **press+held**
and again on **release**, and the manual jack arrows emit on *two* DGNs per event.

Payload order is the panel-binding convention `[7,6,5,4,11,10,9,8]` (`MCF-FORMAT.md`) — validated
here against the known `Shore Brkr` binding, which reproduces `FF 96 99 0F FB FF D2 FF` exactly.

### `0x1FFEE` LEVELING_CONTROL_COMMAND

| control | touch/group | press payload | release payload |
|---|---|---|---|
| `Auto Level` | 218 | `08 00 00 00 00 00 00 00` | `00 00 …` |
| `All Retract` | 219 | `03 00 00 00 00 00 00 00` | `00 00 …` |
| `Cancel` | 220 | `0B 00 00 00 00 00 00 00` | `00 00 …` |
| FRONT arrow A | 221 | `48 40 00 00 …` | `00 00 …` |
| FRONT arrow B | 222 | `48 10 00 00 …` | `00 00 …` |
| REAR arrow A | 223 | `48 04 00 00 …` | `00 00 …` |
| REAR arrow B | 224 | `48 01 00 00 …` | `00 00 …` |
| LEFT arrow A | 225 | `48 00 40 00 …` | `00 00 …` |
| LEFT arrow B | 226 | `48 00 10 00 …` | `00 00 …` |
| Right arrow A | 227 | `48 00 04 00 …` | `00 00 …` |
| Right arrow B | 228 | `48 00 01 00 …` | `00 00 …` |

**CONFIRMED against the official layout, 2026-09-03**, after the owner filled in `0x1FFED`,
`0x1FFEE` and `0x1FFEC` in `rvc-spec.yml`. The static decode was structurally right — byte 0 a mode,
bytes 1-2 four 2-bit fields each — and **wrong about which corner is which**, which the spec fixes.

**Byte 0 = `Operating mode`, and all five observed values are exact:**

| observed | spec |
|---|---|
| `0x08` `Auto Level` | Automatic hydraulic/electric leveling started |
| `0x03` `All Retract` | Store active (store jacks / go to ride height) |
| `0x0B` `Cancel` | Abort Function |
| `0x48` manual arrows | **Manual hydraulic/electric leveling — Biax mode** |
| `0x00` release | Inactive |

**`0x48` is the key.** It says this page is a **Biax (two-axis) hydraulic/electric** levelling UI,
and that resolves the corner mapping, because in Biax mode the spec names jack 1 = Rear, 2 = Front,
3 = Right, 4 = Left. Bytes 1-2 pair `[Retract n][Extend n]` per jack:

| page control | payload | spec field | Biax corner |
|---|---|---|---|
| FRONT arrow A | byte 1 = `0x40` | Extend 2 | **Front, extend** |
| FRONT arrow B | byte 1 = `0x10` | Retract 2 | Front, retract |
| REAR arrow A | byte 1 = `0x04` | Extend 1 | **Rear, extend** |
| REAR arrow B | byte 1 = `0x01` | Retract 1 | Rear, retract |
| LEFT arrow A | byte 2 = `0x40` | Extend 4 | **Left, extend** |
| LEFT arrow B | byte 2 = `0x10` | Retract 4 | Left, retract |
| Right arrow A | byte 2 = `0x04` | Extend 3 | **Right, extend** |
| Right arrow B | byte 2 = `0x01` | Retract 3 | Right, retract |

Every label lands on its own Biax corner, and arrow **A is always extend, B always retract**. Four
independent facts (the mode byte, the jack numbering, the page's four labels and the diamond
layout) agree, which is why this is recorded as confirmed rather than inferred.

### `0x1FBDA` G12_INPUT_STATUS — the eight arrows only

The spec gives byte 0 = instance, byte 1 = input state, and the bindings match exactly: the panel
reports **instance 1** with the state set to which arrow is held.

| arrow | 221 | 222 | 223 | 224 | 225 | 226 | 227 | 228 |
|---|---|---|---|---|---|---|---|---|
| `input state` | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |

Release sends `01 00 …` (state 0) on every one. `Auto Level`, `All Retract` and `Cancel` do **not**
emit on this DGN — they are `0x1FFEE` only.

## 2. What the page subscribes to

Four receive-filter entries, all extraction mode 1 (`guidata.variable_table`):

| var | DGN | | payload byte | compare | status slot |
|---|---|---|---|---|---|
| 232 | `0x1FFED` | LEVELING_CONTROL_STATUS | 0 | open (`0xFF`) | `0x20008198` |
| 234 | `0x1FFED` | LEVELING_CONTROL_STATUS | 2 | `== 0` | `0x2000819C` |
| 236 | `0x1FFEC` | LEVELING_JACK_STATUS | 0 | open | `0x200081A0` |
| 240 | `0x1FFEE` | LEVELING_CONTROL_COMMAND | 0 | open | `0x200081A8` |

Slot addresses follow the index-split rule (`idx <= 255` → `0x20007FC8 + idx*2`). Note var 240:
the panel subscribes to the **command** DGN it also sends, i.e. it watches for another node
commanding levelling.

### Spec coverage — what the YAML still needs

Every levelling DGN this page touches is **named** in `rvc-spec.yml` but carries **no parameter
list**, so payload layouts have to come from the official RV-C document:

| DGN | name | parameters in the YAML | used here |
|---|---|---|---|
| `0x1FFED` | LEVELING_CONTROL_STATUS | **0** | subscribed (vars 232, 234) |
| `0x1FFEE` | LEVELING_CONTROL_COMMAND | **0** | published *and* subscribed (var 240) |
| `0x1FFEC` | LEVELING_JACK_STATUS | **0** | subscribed (var 236) |
| `0x1FFEB` | LEVELING_SENSOR_STATUS | **0** | not used by this page |
| `0x1FFEA` | HYDRAULIC_PUMP_STATUS | **0** | not used by this page |
| `0x1FFE9` | LEVELING_AIR_STATUS | **0** | not used by this page |
| `0x1FFEF` | AAS_SENSOR_STATUS | 26 | **not subscribed** |
| `0x1FFF0` | AAS_STATUS | 7 | **not subscribed** |
| `0x1FBDA` | G12_INPUT_STATUS | 2 | published (the eight arrows) |

**The two air-suspension DGNs are the trap.** `AAS_SENSOR_STATUS` and `AAS_STATUS` are the only
fully-specified attitude messages in the family, and they are the obvious place to publish "how
level is the coach" — but **the panel subscribes to neither**, so nothing sent on them can ever
reach this screen. Anything meant for this page has to arrive on `0x1FFED`, `0x1FFEC` or `0x1FFEE`.

**The var 234 filter, now explained.** It is not "store byte 2 when byte 2 is 0". The compare index
is payload byte **0** and the stored index is byte **2**, so it reads: *accept the frame only when
`Operating mode == 0` (Inactive), then store byte 2* — and byte 2 is the warnings byte
(`Excessive slope initial warning` bits 0-1, `final warning` bits 2-3, `Leveling system master
warning` bits 4-5). So the panel captures the slope/master warnings only while the system is idle,
which is a sensible gate and matches the page's `EXCESSIVE SLOPE` row.

The rest of the `Status` list has obvious sources in the same message: `OPERATING` from byte 0,
`ENGAGE PARK BRAKE` from byte 7 bits 0-1 (`Park Brake`: 0 = system may operate, 1 = may not).
`LOW VOLTAGE` and `IGNITION ON` have no field in `0x1FFED` and are presumably panel-local.

**The filters are blind to the sender** — 18-bit DGN only (`CLAUDE.md`) — so a bridge publishing
`0x1FFED` from any source address will land in these slots.

## 3. Why nothing lights up — the page's readouts have no writer

Screen 29 draws 18 state-driven elements, and **every one of them reads a GuiCode-RAM cell that no
instruction in either image writes** (`slotsource`, `bootlottery`):

| element | slots | address range | class |
|---|---|---|---|
| `Status` dots — OPERATING, LOW VOLTAGE, EXCESSIVE SLOPE, IGNITION ON, ENGAGE PARK BRAKE | 176-180 | `0x20039990`… | `unreferenced` |
| the four jack-pad gates | 170-173 | `0x20039CC8`… | `unreferenced` |
| `Auto Level` / `All Retract` ↔ `Cancel` toggles | 174, 175 | `0x2003A62C`, `0x20039DDD` | `unreferenced` |
| the eight arrow recolour cells | 424-430 | `0x20039DB8`… | `unreferenced` |

So the subscription in §2 writes the **status slots** at `0x200081xx`, and nothing copies those to
the **display cells** at `0x2003xxxx`. The two are separate address spaces joined only by code, and
here the code is absent. That is why the page renders in one uniform state on the coach: all five
dots the same colour, all eight arrows unhighlighted, regardless of the bus.

**Confirmed on the coach, and it is the best demonstration of the SRAM reading we have.** Two
photos of this page two minutes apart, nothing on the bus changed between them:

| | after the flash, no power cycle | after a power cycle |
|---|---|---|
| the two buttons | `Auto Level` / `All Retract` (174/175 = 0) | **`Cancel` / `Cancel`** (= 1) |
| the eight arrows | uniformly dark | **eight different colours** |
| the `Status` and jack dots | blue | **red** |

A flash ends in a warm reset, which preserves SRAM; pulling power re-rolls it. Seven of those cells
are colour indices, so the second photo is close to a direct visual read-out of uninitialised
memory (`GUICODE.md` "The 210 explained").

**This is also good news for the project**, and it answers something the `Absorb`/`Float` thread
could not: the cells are genuinely **read and painted every tick**. The display path works end to
end; the only missing piece is something to write them.

## 4. The one caveat on that negative

`unreferenced` means no instruction writes the address *directly*. A cell can also be written
**by index** through a firmware routine, which no literal scan can see — that is exactly how the
`aeslowvolts` bridge drives recolour cell 45, calling the firmware's own `setVariant` through the
function pointer at `0x20039908`. Cell 45 is **type 10, the same type as the arrow cells 424-430**.

The check that keeps the negative honest: slot 45 classifies as **`written`, with 2 writers** —
the stock block in `FUN_00247BF8` — so the classifier does distinguish, and the levelling cells
genuinely have nothing. Stock never drives this page. What §4 rules out is only the stronger claim
that the cells are *undrivable*.

**The indexed-write caveat is now closed, by observation rather than by scanning
(`BUS-BASELINE.md` PC.57).** Two independent results:

- **Injection changed nothing.** With the owner on screen 29 for 75 s, 25 frames each of
  `0x1FFED` (operating mode, then the slope/master warnings) and `0x1FFEC` (jack status) moved no
  dot, no arrow and no button. No other node emitted on those DGNs, so nothing masked the test.
- **The eight arrows came up in eight arbitrary colours after a power cycle and stayed that way.**
  Any writer on the paint path — indexed or direct — would have overwritten that garbage within one
  tick. Persistent uninitialised values *while the page is displayed* prove no writer runs, which
  the code scan alone could not establish.

So the cells are read and painted every tick, and nothing writes them. §5 is the only route.

**And closed from the code as well, 2026-09-04 (`GUICODE.md` Finding 67):** every pointer-mediated
store form in both images — struct-field, stack-parked, parameter, table-fed, bulk-call, block-store,
heap — was enumerated and each hit read; none can hold this page's cell addresses. That also closes
the one case PC.57 could not, an *event-driven* writer on the `Auto Level`/`All Retract` press.

## 5. What would make it work — and it is a mechanism this coach already runs

Everything needed is proven and already shipping in the owner's image:

1. **The subscription exists** (§2) — `0x1FFED` already lands in `0x20008198` / `0x2000819C`, so no
   new receive-filter entry is needed for LEVELING_CONTROL_STATUS. A brand-new var-table entry
   would be needed only for a DGN not in §2 (an attitude/inclination DGN, say), and appending one
   is the known-unverified part of `register.resolve_register`.
2. **The write path is hardware-verified** — `add_aes_low_volts_trigger` appends a per-tick routine
   onto the GuiCode hook at `+0xEE3C`, reads a receive slot, and drives a recolour cell through
   `setVariant`. It has run on this coach since v10, and the ops **chain**, so another routine can
   be added beside it (`followchannel.chain_from_hook`).
3. **So the feature is**: a routine that reads `0x20008198`/`0x2000819C`, derives the five status
   booleans and the eight arrow states, and writes cells 176-180 / 424-430 the way the AES bridge
   writes cell 45.

The honest unknowns, in order of risk: what `0x1FFED`'s bytes actually mean (the spec is silent, so
this needs either a real levelling controller on a bus or a decision to define it ourselves, since
**we would be both publisher and consumer**); whether the arrow cells respond to `setVariant` the
same way cell 45 does; and how many cells one appended routine can drive before it needs splitting.

Since the owner is writing the publisher too, the cheapest first step is a **one-cell spike**: drive
a single `Status` dot from a byte of `0x1FFED` and confirm it changes colour on the coach. That
answers "do these cells paint at all" for a fraction of the work of the full page, and it is the
same shape as the Low Volts bridge that already works.

## 6. Verifying §1 on the bus

The transmit table is a static decode and wants a capture to confirm. `candump -L can1` while
pressing each control, then match against §1. **Read before you press:** `Auto Level` and
`All Retract` command jack motion on a coach that has jacks. On this coach they should raise no
hardware, but that is an expectation, not a guarantee — the frames go out either way, and any
levelling controller on the bus would act on them.
