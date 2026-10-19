# What the Firefly firmware adds to rvc2mqtt — and what it does not

Written for whoever maintains `rvc2mqtt` and has to decide what is worth merging into
`rvc-spec.yml` and `entity/g12_configuration.py`. This project decodes the Firefly **MCF**
bundle (`mcf.bin`) for three builds — `Jayco Ethos/Swift V11`, `Jayco B-Van V13` (the
subject coach) and `Jayco B-Van V14`.

*Two standing claims in this paragraph have expired, and the corrections matter to how you
weigh what follows.* It used to say **"nothing was ever transmitted on the coach's bus"** —
true until 2026-08-23. Since then this project has run **transmit probes** (`BUS-BASELINE.md`
PC.20-PC.23), and several of the strongest findings below, `0x92` among them, exist only
because of them. It also said **"it changes nothing in your repository"**; as of 2026-08-24
the merge-worthy items in §7 have been applied on branch `rvc-gaps-sync`.

**Read the verdict table first.** Most of what you probably hoped for is in the "no"
column. The firmware turned out to be a much better source of *structure* than of
*meaning*: the MCF stores what the panel sends, not what any of it is called.

| Question you asked | Answer |
|---|---|
| Meanings for the twelve unidentified `15FCE` selectors | **One, now.** `0x92` is **`Max Chrge Rate`, in percent** — identified 2026-08-24 by a transmit probe on the coach plus the G12's relay table (§1), and merged. `0xF1`/`0xF2` gain a *role* (floorplan-adjacent) but no name. The rest gain only a *shape*: `0x11` a stepped number, `0x2E`/`0xE8` booleans, `0x06`/`0x00` "the panel writes it once". *(This row read "**No** … **None gains a meaning**" until 2026-08-24, and was already stale when §1's update landed. Two selectors, `0x00` and `0x04`, were briefly claimed and retracted the same day — see §1.)* |
| Are selectors global or per-floorplan? | **Not settled by the firmware.** The bus already reduced this worry substantially (PC.3, two models). The file neither confirms nor contradicts. But the **floorplan enum itself is build-specific** — see §2, this one does affect you. |
| The real AGS parameter limits (`g12_configuration.py:1015`) | **No.** No limits structure was found. The file does give the panel's own ± step sizes (§3). |
| A per-floorplan channel mapping for the abandoned-channel `TODO` (`g12_configuration.py:842`) | **No.** What exists is a *per-build* label → channel table (§4), not a per-floorplan one. *(Updated 2026-08-15: this row used to say the `E3`/`E4` binding "was not derived". A **cited candidate** has since been derived from GuiCode — `E3` → 25, `E4` → 26, matching the coach — but it is `inferred-from-code`, unread by any tool, untested, and checked only on 13.6, so it does **not** change this "No". See §4.2.)* Two untested leads (§4.2, §5c). |

---

## 1. The twelve unidentified selectors: still unidentified

`00`, `04`, `06`, `11`, `2E`, `75`, `92`, `95`, `E8`, `EE`, `F1`, `F2` — the list is
unchanged from PC.4/PC.7. The firmware does not name them, and here is exactly why.

GuiData carries a **variable table** (13.6: 298 entries of 8 bytes at section offset
`0x2A70`). Each entry is a declaration and nothing more:

```
ce 5f 1d 02 f5 00 00 00      DGN 0x15FCE, selector 0xF5   (the floorplan parameter)
ce 5f 1d 02 92 00 00 00      DGN 0x15FCE, selector 0x92   (one of the twelve)
```

There is no name, no unit, no range, no default beside any of them. 13.6 declares 48
distinct `15FCE` selectors, ten of the twelve unknowns among them (`04 06 11 2E 75 92 95
EE F1 F2`); `00` and `E8` are not declared in any of the three builds. The G12 section
declares the same selectors a second time, in the same nameless form.

The one thing the file adds is the **write shape** of three of them, from the panel's
stored-message table (see §5 for the caveat on how that table is read). Identical in all
three builds:

| selector | stored writes | reads as | coach value (PC.1) |
|---|---|---|---|
| `0x11` | `−5` / `+5` pairs | an adjustable number with a step of 5 | 45 |
| `0x92` | `−20` / `+20` pairs | an adjustable number with a step of 20 | 200 |
| `0x2E` | absolute `0` / `1` | a boolean toggle | 0 |

That is shape, not meaning — do not name a Home Assistant entity from it. The other nine
have no write entries at all **in that table**, which is consistent with them being
read-only broadcasts.

**Update, 2026-08-15 — a second store, and one selector moves.** That table is GuiData's
binding table. The panel has a *separate* one: GuiCode's `.rodata` holds ~118 pre-built
12-byte `0x1FED9` frames it copies wholesale and transmits (`GUICODE.md` Findings 7 and
23). **Four** of the twelve appear there in a write-shaped template — `0x06`, `0x92`, `0xE8`
and `0x00` (an earlier version of this section said "three", counting the first three and
relegating `0x00` to a parenthetical; `0x00` is one of the twelve, so it belongs in the
count). Template addresses are 13.6 GuiCode addresses, section base `0x00240000`:

| selector | GuiCode write templates (13.6) | reads as |
|---|---|---|
| `0x06` | one, value `0` (`0x00259544`) | a write exists; nothing more |
| `0x92` | absolute `0xC8` = **200**, plus a read-back (`0x002595F8`/`0x00259604`) | consistent with the ±20 step and the coach's 200 |
| **`0xE8`** | `0` **and** `1`, plus a read-back (`0x0025970C`/`0x00259724`/`0x00259718`) | **a boolean the panel writes** — *not* read-only |
| `0x00` | one, value `1` (`0x00259A90` = `ff 96 00 0f 01 00 d1 ff`) — write-shaped, but its trailing byte pair is `d1 ff` where the ordinary write form ends `d1 ea` (four other templates in the pool share that ending: `0x9B`×3 and `0x01`) | a write exists; the odd tail is **not interpreted** |

A fifth, `0x2E`, appears in that pool **only** in read-back form
(`0x00259AA8` = `ff 96 2e 0f ff ff d3 ff`), so it adds nothing to the `0`/`1` shape the
binding table already gave it.

`0xE8` moves the furthest: it is written from a tap handler at **config byte `0x10`**, both
arms cited, in `GUICODE.md` Finding 23's strongest confidence class (**A2** — Rule A, both
toggle arms agreeing). So `0xE8` is a panel **option toggle**. **Confidence class is not
verification, and the two must not be read as one** — Finding 23 makes exactly this
distinction, and it applies here: `0x10 → 0xE8` (like `0x0F → 0xEF`) is a **new** pair with
**no oracle entry and no capture** behind it. A2 says the derivation is the tightest kind
available *within the method*; it does not say anyone has seen the frame on a wire and
matched it to that option. Its *name* is not recovered either: no OPTIONS row in 13.6's
GuiData carries selector `0xE8`, and the config-unpack tracer resolves no SRAM register for
config byte `0x10` in that build, so there is nothing to hang a name on. This is again
**shape, not meaning**; do not name an entity from it.

**Update, 2026-08-24 — one of the twelve is IDENTIFIED, four more have a decoded role.**
This came from the method the next paragraph recommends, so that recommendation stands and is
now demonstrated. Two new sources: the **G12's own rule table** (`GUICODE.md` Finding 52 — 151
12-byte transmit templates in the G12 section, decoded and validated against captured frames),
and a **transmit probe on the coach** (`BUS-BASELINE.md` PC.23).

| selector | status | evidence |
|---|---|---|
| **`0x92`** | **`Max Chrge Rate`, in percent** — *identified* | The panel control is object 928, labelled `Max Chrge Rate`, unit `%`. G12 rule #53 relays it to **`1FF95` payload[1], instance 1**. Read-back returned **200**; adjusting by ∓20 moved it 200↔180 and the G12 transmitted `01 C8 …` / `01 B4 …` within 6 ms. 200 at RV-C's 0.5 %/bit = **100 %**, and the panel divides by 2 to display it. That also explains the ±20 step below: 20 raw = 10 % on screen. |
| ~~`0x00`~~ | **RETRACTED 2026-08-24** — see below | |
| ~~`0x04`~~ | **RETRACTED 2026-08-24** — see below | |
| `0xF1`, `0xF2` | role, not name | Both ride in one `1FACF` broadcast (rule #44) at payload[2] and payload[7], **alongside `0xF5`, the floorplan**, at payload[5]. Coach values `0x0D` and `0x06`. Floorplan-adjacent configuration. *(Unaffected by the retraction: rule #44 is in table `0x62`, where the mask reading is validated against captured frames.)* |
| `00 04 06 11 2E 75 95 E8 EE` | still unknown | They drive **no** rule at all, which is itself information: whatever they are, the G12 does not forward them to RV-C. |

**Retraction, 2026-08-24 — `0x00` and `0x04` do NOT have the roles claimed above.** They were
published here for one day and are wrong; the correction is ours and is worth stating plainly.

The claims came from applying table `0x62`'s **substitution-mask** reading of record byte 3 to
tables `0x20` and `0x30`, where that byte means something else. In table `0x20` it is a gesture
**phase mask** (`GUICODE.md` Finding 54a), so the light-switch record

    db fe 01 08 04 00 ff 16 ff ff 00 00

is *"on release-after-hold, send `1FEDB` instance 22 command 4 (stop)"* — a literal command byte
in a standard toggle/ramp/stop triple — and **not** *"substitute selector `0x04` into the command
byte"*. Likewise every `0x00` attributed to `1FBDA` instances 161–170 and 251 was the literal
`00` in an indicator's OFF record.

Counting only tables where the mask reading is proven, and excluding each selector's own `0x15FCE`
sweep template (which merely rebroadcasts its value and implies no role), `0x00` drives **0**
rules and `0x04` drives **0**. They belong in the unknown row, which is where they now are.

*The general lesson, which is the reusable part:* the four rule tables share a record **format**
but not a record **kind**, and byte 2 carries an undecoded kind field precisely because the kind
changes what the later bytes mean. A decoder that reads one table's semantics into another
produces confident, specific, wrong claims — of the twelve selectors, this method briefly
"identified" two that the evidence does not support. `g12rules.CommandRule.selectors` now returns
nothing outside table `0x62`, and a test enforces it.

**Two things you can use directly, independent of naming.**

*A safe read.* `FF 96 <selector> 0F FF FF D3 FF` is a **read-back request**: the G12 answers on
the very next frame with `195FCE9C#<sel> 00 <u16 value LE> 00 00 00 00`, and it provokes **no**
other traffic — verified across five selectors, zero non-baseline frames in the capture. So you
can poll any selector's current value without writing anything. Selectors absent from the G12's
periodic sweep still answer: only `0x92` of the five tested is swept, all five replied.

*A wider delta hazard than §PC.11 recorded.* PC.11 proved `CC`/`CD`/`CE` are **signed deltas**
and explicitly left the scope open. Five more are now confirmed delta: **`0x82`, `0x86`, `0x88`,
`0x92`, `0x99`** — each moved by exactly the signed amount written and returned exactly on the
reverse. If `g12_configuration.py` writes any of those as absolutes, it has the same defect the
`CC`/`CD`/`CE` fix addressed. Still not a general rule — `0x16` was observed absolute — so treat
delta-vs-absolute as per-selector and unknown until tested.

**What would actually answer this:** the PC method that already worked — drive one screen
at a time on the coach and diff the `15FCE` broadcast. The remaining candidates are the
screens nobody exercised: the AES/inverter panel beyond what PC.3 covered, and the hidden
diagnostics pages. ~~Failing that, the `Lyra … GuiCode` bytecode is where the panel's
screen-to-variable wiring must live, and no task in this project decoded it.~~ **Corrected,
2026-08-15:** `GuiCode` is not bytecode — it is native Thumb-2 — and it *has* since been
disassembled (`docs/GUICODE.md`). Reading it is what produced the update above, and it is
where any further progress on these twelve will come from.

---

## 2. Global vs per-floorplan selectors — and a caution about the floorplan enum

**The selector question stands where PC.3 left it.** Four selectors derived on an Ethos
20DL (`E9`, `E6`, `D8`, `D7`) were reconfirmed unchanged on a B-Van `WD`, along with
`E3`/`E5`/`EC`/`EF`/`F5`. That is evidence from two models, not proof across all. The
firmware adds only weak corroboration: each build has exactly one variable table with no
floorplan dimension anywhere in it — but since **no per-floorplan option structure of any
kind was ever located** (§4), the absence of one proves little. Keep `rvc-spec.yml`'s
assumption and keep the note that it is an assumption.

**And weight that note a little more heavily, on one new piece of evidence — inference,
not measurement (added 2026-08-15).** The disassembly gives a case where a selector does
**not** keep its option *across builds*: `GUICODE.md` Finding 23 reads 13.6's `0xE4` as the
**Bath Light** row (config byte `0x19`) and 14.7's `0xE4` as **Starlink Power** (config byte
`0x1B`), and 14.7's config-unpack resolves neither config byte `0x0D` nor `0x19` — so both
13.6 options that could have carried `0xE4` forward are gone from that build, and the byte
looks genuinely freed and reused rather than merely renamed. **Do not act on this as fact:**
the two 14.7 rows come from the weaker of Finding 23's two recovery rules, with no oracle
entry and no capture behind either. It is a reason to keep the assumption *labelled* and to
be wary of carrying a selector's meaning between builds, not a reason to drop the assumption
— and it says nothing new about the per-floorplan question above, which is a within-build
question. The one thing that would settle it is the usual one: toggle Starlink Power on a
14.7 panel and diff the `15FCE` write.

**The floorplan enum, however, is build-specific, and this one is actionable.** The
complete `0xF5` enum was recovered independently from 13.6 and matches the coach exactly:

| `F5` | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|
| code | RT | RA | RD | SY | TB | CB | WA | **WD** | WT | RY |

Family, from the coach's own FLOORPLAN SELECTION screen: Generator = RA, RY, RD, RT, CB;
Li-AES = WA, WD, WT, TB, SY.

The same reader on the other two builds returns **different enums**:

* 14.7 (`B-Van V14`) has nine codes including `RE`, which 13.6 does not have, and lacks
  `RD` and `WD`. The values therefore do **not** line up between 13.6 and 14.7.
* 11.23 (`Ethos/Swift`) uses a different naming scheme entirely (`3RT / 4RT`,
  `3WD / 4WD`, …) and has no selection screen at all, so its values come back
  unconfirmed and are not trustworthy.

Since rvc2mqtt's original four-value enum came from an Ethos 20DL, treat the `F5` table as
**per model/build data**, not a protocol constant. The values above are correct for
`Jayco B-Van V13`; they are the coach's own, confirmed by PC.5, and are safe to ship for
that coach.

---

## 3. AGS parameter limits: **FOUND — 2026-08-24.** They are in the G12 code blob

`g12_configuration.py:1015` hopes for "reading them out of the Firefly firmware". **Here they
are.** They are two-sided clamps in the G12 section's table-`0x67` Thumb-1 blob
(`docs/G12-CODE.md`), at `+0x0A14`…`+0x0AB8`, each written as *"if above the max, store the max;
else if at or below the min, store the min; else keep"*:

| selector | parameter | min | max |
|---|---|---|---|
| `0x31` | start at voltage | 1000 = **50.0 V** | 1080 = **54.0 V** |
| `0x0D` | stop at voltage | 1040 = **52.0 V** | 1096 = **54.8 V** |
| `0x0C` | time at start volts | **10** | **300** |
| `0x0E` | time at stop volts | **600** | **3600** |
| `0x16` | max engine run time | **60** | **115** |

Voltages are the standard 0.05 V/unit. `0x0C` and `0x0E` are seconds — the panel labels `0x0C`
as `60 Sec` (owner, `BUS-BASELINE.md` PC.37). **`0x16`'s unit is not established**; the values are
given raw.

**And there is a relational constraint the table cannot express** (`+0x0A76`): the G12 requires

```
stop_at >= start_at + 8          (0.4 V of separation)
```

and if that fails it **forces `stop_at = start_at + 10`**, adjusting `start_at` down by 10 in one
branch. So the two setpoints are not independent, and a client that writes them one at a time may
see the other move.

*Why this stayed missing so long, and it is worth carrying:* the search looked for a min/max
**structure** — a table of bounds beside the values. There isn't one. The limits exist only as
`cmp`/`bhi`/`bls` pairs in compiled code, in the one section this project left undisassembled the
longest. The negative was honest and the reasoning was sound; the assumption that a limit must be
*data* is what made it unfindable.

*(The paragraph this replaces read: "Nobody found them. No min/max/clamp structure was located in
the MCF for any parameter, in any of the three builds… This is an honest negative, not an
unexplored area." The caveat it carried — that it was a negative from not finding, not a proof of
absence — turned out to be exactly right.)*

Two things this work can offer instead.

**a. The panel's own ± step sizes**, from stored adjust messages, identical across all
three builds:

| selector | step | your current `step` |
|---|---|---|
| `0x0D` stop at voltage | ±2 counts = **0.1 V** | 0.1 ✔ |
| `0x31` start at voltage | ±2 counts = **0.1 V** | 0.1 ✔ |
| `0x16` max engine run time | ±5 min | 5 (AGS) / 1 (AES) |
| `0x0E` time at stop volts | ±300 s | 300 ✔ |
| `0x0C` time at start volts | ±5 and ±10 (13.6/14.7); ±1 (11.23) | 10 |
| `0x2B`, `0x2C` quiet time start/stop | ±15 min | — |
| `0x15` AGS config | ±1 | — |
| `0xCC`, `0xCD`, `0xCE` **tank level thresholds** | **±1000** (raw ADC counts) | — |

**c. Three settings you may not have at all — the tank level thresholds.** Added 2026-08-24
(`BUS-BASELINE.md` PC.35). Selectors `0xCC`, `0xCD`, `0xCE` are the boundaries the panel's
`PROBE TANK DIAGNOSTICS` screen edits, in **raw 16-bit ADC counts, where higher means emptier**:

| selector | default | boundary |
|---|---|---|
| `0xCC` | 54000 | 0% / 33% |
| `0xCD` | 40000 | 33% / 67% |
| `0xCE` | 25000 | 67% / 100% |

Adjusted with the usual `D2` signed delta at ±1000, read back with `D3`, and restored by an
absolute `D1 EA` set carrying the defaults above (the screen's `Reset Default Thresholds` button).
The G12 applies them itself — the comparison ladder is in its table-`0x67` code
(`docs/G12-CODE.md`) — so a tank's reported percentage is a *bucketed* value, not a measurement:
this coach's four senders read 65520 / 65515 / 57412 / 65520 raw, all above the top threshold, and
all four gauges therefore report **0%**. If you surface tank levels, that is worth knowing before
treating 0% as a reading.

Steps are not bounds. They corroborate your existing choices; they do not close the TODO.
They also rest on the stored-message reading described in §5b, which was derived while
writing this document and is **not covered by the test suite** — treat them as
corroboration, never as a source of truth on their own.

**b. PC.3 already gives you the more important half.** The same selectors carry an
entirely different parameter set per `EF` mode — 13.50 V / 12.20 V / 720 min in AGS versus
54.40 V / 51.00 V / 115 min in AES Single. Whatever the true limits are, the Home Assistant
ranges have to be mode-dependent, which is what your provisional AGS branch already does.
The only way to pin the real stops that this project can see is the one your own comment
names first: drive each setting to its stop on the touchscreen in AGS mode and watch where
it clamps.

---

## 4. The abandoned-channel `TODO`: no per-floorplan mapping

### 4.1 What exists, and what it is not

`g12_configuration.py:842` needs, for an arbitrary coach, the channel pair that the
`E3`/`E4` option rebinds — 25/26 on `WD`, unknown elsewhere. **This work does not supply
that**, for two separate reasons:

1. **The option → channel binding was not derived from the file.** Task 9 searched for it
   and failed, and the failure is encoded as strict `xfail` tests. Measured negatives: the
   OPTIONS screen object (848 bytes) holds none of the six selector bytes; the binding
   table holds zero `0x15FCE` entries in any build; and — checked while writing this —
   among the 190 group-`0x96` writes in 13.6 of the form `ff 96 <sel> 0f`, the selector
   position holds 37 distinct bytes and **none** of the six option selectors. Note the
   scope: that is the group-`0x96` selector position, not "the option toggles are absent
   from the file". See §5c for the group-`0x94` overlap, which is the reason for the
   hedge.

   *Scope note, 2026-08-15.* Every negative in this item is about **GuiData** — screen
   records, the binding table, the group tables. A candidate has since turned up in
   **GuiCode** instead: the Bath/Cargo tap handler emits a `0x1FEDB` command-3 (OFF)
   frame to the channel it is abandoning, and pairing each arm's single `0x1FED9` write
   with its single OFF gives `E3` → 25 and `E4` → 26, matching the coach
   (`GUICODE.md` Finding 23). **Do not build on it yet** — it is `inferred-from-code`,
   no tool reads it, no test covers it, and it has only been checked on 13.6, so it
   says nothing about what floorplan `RT` binds. Item 2 below is unaffected.
2. **What the file *does* give is per build, not per floorplan.** GuiData's group →
   binding join yields, for a labelled control, the G12 channels it commands. That is a
   real result, verified against the coach without matching on any channel number or label
   text (interior master → 22, 24, 25, 26, 32, 34, 35; 14.7's four tank heaters and water
   pump → 5, 6, 7, 8, 44). But a build serves *all ten floorplans*, and no per-floorplan
   dimension was found anywhere in it. Reading channels out of a build tells you what the
   panel can command, not what floorplan `RT` binds.

If you want the per-build table anyway, `src/fireflymcf/guidata.py` exposes
`find_control_groups(data)` (label, screen, group index, channels) and
`find_channel_references(data)`. Carry the union caveat in §6 with any channel set you
quote.

### 4.2 One lead, explicitly not a finding

While writing this document I noticed that **group 93** — byte-identical in all three
builds — contains exactly the shape PC.8 observed when the option is switched:

```
byte2 = 0x01 :  Off(cmd 3) -> ch 25,  cmd 0x21 -> 25,  cmd 1 -> ch 26,  cmd 0x22 -> 25
byte2 = 0x11 :  Off(cmd 3) -> ch 26,  cmd 0x21 -> 26,  cmd 1 -> ch 25,  cmd 0x22 -> 26
```

Two variants that swap the roles of channels 25 and 26, each beginning with an explicit
Off to the channel being abandoned — which is precisely the behaviour PC.8 recorded and
which your `TODO` describes.

**Treat this as a lead, not an answer.** I found it by searching for command-3 entries on
channels 25 and 26 — that is, by searching with the answer already in hand, which is the
exact failure mode this project has caught repeatedly. Nothing independent ties group 93
to the `E3`/`E4` option: no control record referencing group 93 was located, and the same
group is byte-identical in the Ethos build, where the channel pair may well mean something
else. What would settle it is finding what references group 93, and whether binding byte 2
(`0x01`/`0x11`) tracks option state. If it does, it would answer both this `TODO` and the
open question about byte 2 at once.

---

## 5. Other findings you may care about

**a. Group `0x94` is real, and PC.7's unexplained frames are stored in the firmware.**
PC.7 caught four `1FED9` frames from the touchscreen using group `0x94` instead of `0x96`
and could not explain them. 13.6's binding table contains **33** group-`0x94` messages,
and all four captured frames — three distinct payloads — appear in it byte for byte:

```
ff 94 ff ff ff ff f0 ff      (PC.7 t=236.8, 244.4)
ff 94 01 ff 1c ff e5 ff      (PC.7 t=299.4)
ff 94 00 ff 1e ff e3 ff      (PC.7 t=299.6)
```

Layout is `FF 94 <selector> FF <value> FF <function> FF` — the value and function sit in
different bytes than in group `0x96`, exactly as PC.7 suspected. This confirms they are
deliberate panel messages rather than noise. It does **not** say what they do, and
rvc2mqtt decodes only group `0x96` today. Decoding `0x94` without knowing its semantics
would be guessing.

**b. The function-byte pairs in stored writes.** The panel's stored `1FED9` messages use
`D1 EA` with an absolute value, `D2 FF` / `D2 EA` with a small signed value in ± pairs,
and `D3 FF` with `FFFF` — the last being your read-back request, byte for byte.

*How the table is read, and how much to trust it.* A 12-byte binding entry is
`<u16 DGN low><u16 ?><8 CAN data bytes, in the order 3,2,1,0,7,6,5,4>`. That byte order
was worked out while writing this document — it is **not covered by the test suite** — and
it rests on five independent matches against your own captures: the master-on frame (level
251, command 0) and master-off frame (level 0, command 6 `Memory Off`) from PC.8, the
`FF 96 <sel> 0F <lo> <hi> …` set shape and the `FF 96 <sel> 0F FF FF D3 FF` read-back from
PC.2, and the group-`0x94` frames in (a). Strong, but inference.

> **Do not act on this for `CC`/`CD`/`CE`.** PC.11 observed the *touchscreen* writing the
> tank thresholds with `D1 EA` and the stored value nonetheless moving by exactly ±1000,
> which is why your delta fix exists and works. The file contains **no** `CC`/`CD`/`CE`
> write entries at all, so it says nothing about that path. The function byte's semantics
> are not settled, and the hardware capture remains the authority. One further wrinkle
> against a tidy story: the `D1` entries for `0xF5` carry values 0–6 only, though the
> coach's floorplan enum runs 1–10.

**c. Group `0x94` and the option selectors overlap — a lead, and the reason §4.1's
negative is scoped.** In the byte PC.7 labels "function", group-`0x94` entries hold `0xE3`,
`0xE4` or `0xE5` — 15 times in 13.6 (`E3`×7, `E4`×2, `E5`×6), 15 in 14.7, 24 in 11.23 —
clustered in groups 37, 38, 39, 41, 42, 45, 46, 49, 50, 85 and 86 (11.23 also 15 and 43,
and lacks 86). Two of the frames quoted in (a) are among them. Those are three of the six
option selectors, and no others appear.

Tempting, and probably not what it looks like: the same byte position holds `0xE0` more
often than anything else (15 of 33 entries in 13.6), plus `0x12`, `0x13` and `0xF0`, so it
behaves like a small function space that merely overlaps the option-selector range. Reading
it as a selector would assume a byte role this work has explicitly left `unknown` — so it
is recorded as the most option-shaped structure left in the file, and as nothing more. If
you go looking for the `E3`/`E4` binding yourself, this and group 93 (§4.2) are the two
places to start.

**d. The option gating mechanisms are still hardware-only knowledge.** `E3`/`E4`
rebinding (PC.8) and `E5`/`EC` show/hide (PC.9) were both observed on the coach; neither
structure was located in the file. If you implement either, PC.8/PC.9 are your evidence,
not this decode.

**e. Sanity check on the pinout.** RV-C instance == G12 channel == PDF pin number holds
across the 20 channels captured, and the file independently predicts the pinout's names
for five 14.7 channels through a chain that never touches text. If a channel number you
derive disagrees with the sheet, suspect the floorplan, not the sheet.

**f. The G12's INPUT pins are on the bus too — `0x1FBDA`, instance `0xA0 + pin`.**
New 2026-08-24 (`GUICODE.md` Finding 54). This is probably the most directly useful item
here for a bridge, because it is *state you may want to publish* rather than a setting:

```
19FBDA9C#A101FFFFFFFFFFFF      input 1 active
19FBDA9C#A100FFFFFFFFFFFF      input 1 inactive
```

`payload[0]` is the instance (`0xA1`..`0xAA` = pins 1..10), `payload[1]` is the state,
the rest is `0xFF`. The DGN is **GENERIC_INDICATOR_COMMAND**, which is a *command* DGN
being used to carry status — so do not assume a `1FBDA` frame is somebody commanding an
indicator. Read the instance: the panel itself also transmits `1FBDA`, on instances
`0xF9`/`0xBF`, and the two uses share the DGN and nothing else.

On this coach (13.6, WD) the eight named pins are:

| pin | inst | name |
|---|---|---|
| 1 | `0xA1` | Kitchen OHC Lts (GND) |
| 2 | `0xA2` | *(unnamed)* |
| 3 | `0xA3` | Bath Fan (GND) |
| 4 | `0xA4` | *(unnamed)* |
| 6 | `0xA6` | Awning EXT (12V) |
| 7 | `0xA7` | Awning RET (12V) |
| 9 | `0xA9` | Water Pump (12V) |
| 10 | `0xAA` | **Ignition (12V)** |

Pins 5 and 8 are watched by the panel but broadcast by nobody, so expect them to be
permanently absent rather than intermittently missing — that is now structural, not an
observation: the G12's input dispatch table gives both a rule count of **0**.

**Instance `0xFB` is the same input as `0xAA`.** Pin 10 fires four rules — its own `0xAA`
pair and an `0xFB` pair that is byte-identical apart from the instance. The panel reads
`0xFB` as *engine running* on its ELECTRICAL page and `0xAA` as *input 10* on its
diagnostics page, but they are one 12V sense line reported twice and can never disagree.
If you publish both, publish them as one signal.

**CONFIRMED ON HARDWARE 2026-08-24 — and one of the two caveats below turned out to be a
wrong claim, not a caveat.** `BUS-BASELINE.md` **PC.24**,
`captures/pc24-input-pins-2026-08-24.log`. What was predicted above is now recorded:

* **The frame format is right, byte for byte.** `19FBDA9C#A101FFFFFFFFFFFF` and the rest are
  exactly what `cli g12-inputs` printed before anyone turned a key.
* **`0xFB` is pin 10's twin instance.** 14 `FB` frames, 14 `AA` frames, **not one `FB`
  without a matching `AA`** within `0.55`–`0.59 ms`, and **zero `FB` frames** in the
  water-pump-only window. It tracks the key alone.
* **`payload[1]` is that instance's own state.** GND-sense pins 1, 2 and 4 all report `01`
  while held with the **ignition off**, which no global-12V-flag reading survives.
* **Pins 5 and 8 never appear**, as the dispatch table's rule count of 0 predicted.

**The retraction: there IS a heartbeat.** The paragraph this replaces said the rules "look
edge-triggered … so a bridge should not expect a heartbeat". Wrong. Every instance repeats
while held at **`1.000 s`** — GND and 12V alike — as a refresh from the ON edge, not a
free-running timer: eight taps between 82 ms and 872 ms each produced a single ON/OFF pair
with no repeat. *Why the file could not show this:* the rule table holds the ON and OFF
**templates**; the re-send is a property of the code that fires them, G12 table `0x67`, which
is still mostly unread. "No periodic template in the table" was true; the conclusion drawn
from it was not.

**Still true, and now for a better reason:** treat an unseen pin as *unknown* rather than
*inactive*. Not because there is no heartbeat, but because the refresh only runs while a pin
is **held** — an idle pin is silent, so silence still carries no information.

**`0xBF` exists and neither side can decode it.** Two frames, both bracketing ignition-off,
with an all-**zero** tail rather than the inputs' `FF`. It is in no G12 rule table and no
dispatch entry. `rvc-spec.yml` had independently recorded it as "a transition frame during
some deactivation sequences" — corroborated, unexplained, do not guess.

Pin 10 being a labelled **ignition** sense is the useful one: it is a far more direct
ignition signal than the `1FFB7` instance-3 bit that §5's awning-interlock note leaves
unproven, and one key-turn with `candump` settles both.

**rvc2mqtt had a CONTRADICTING model of this DGN, built from real traffic — found
2026-08-24 while syncing these findings into that repo, and RESOLVED the same day by PC.24.**
Recorded in full because the shape of the disagreement is the reusable part: their model was
observed and ours was static, so the standing rule said theirs wins. **It did not.** The
capture that settled it went against the observed model on both contested points and against
the static decode on a third nobody was arguing about.

`rvc-spec.yml` has carried a `G12_INPUT_STATUS` entry for `1FBDA` for some time, and
`entity/g12_configuration.py` has a tested handler built on it. It reads the frame
differently in two ways that matter:

| | this decode (13.6 MCF, static) | rvc2mqtt (Ethos 20DL, observed) |
|---|---|---|
| `payload[1]` | that instance's own state, 1/0 | `aux_12v_active` — a **global** 12V-line flag |
| `0xFB` | a **second instance for pin 10**, byte-identical rules apart from the instance | the **idle** code |
| cadence | edge-triggered, no periodic template | 12V inputs alternate `0xAn`↔`0xFB` at **~1 Hz** while held |

**PC.24's verdict, per row:** their `payload[1]` and `0xFB` readings are **wrong**; their
`~1 Hz` cadence is **right** and ours was wrong; their `0xBF` observation is **corroborated**.
Both handlers needed a change, and both got one.

*How they came to see an alternation that is not one:* with the ignition on **and** another
input held, `FB`/`AA` and (say) `A9` interleave once a second. That reads exactly like an
active code alternating with an idle code — unless you already know `FB` is pin 10. Their
capture could not separate two instances that always travel together, which is the blind spot
a static decode does not have. Ours was blind to behaviour that lives in code we have not
read. **Neither method was bad; each was blind where the other could see** — which is the
argument for doing both rather than for ranking them.

*What their handler actually did wrong,* now fixed rather than merely recorded: `FB`/aux=1 was
swallowed as a heartbeat, so ignition-on never published; `FB`/aux=0 was read as "all inputs
released", so ignition-off cleared the published state of **every** held input; and any GND
input reporting `01` was mis-filed as a 12V input.

---

## 6. Two caveats to carry if you quote this work

**On channel sets:**

> GuiData's binding table gives the UNION of channels commanded by a control's group
> across its byte-2 variant copies; which variant applies in a given build/vehicle is not
> known, so for groups with more than one variant (Light Master, Exterior Master — where
> channel 35 sits) the exact per-variant membership is unsettled by this file alone. The
> CAN-capture answer key remains the direct evidence for exact membership; GuiData
> corroborates rather than overrides it.

In particular: `INTERIOR_MASTER` and `EXTERIOR_MASTER` as captured on the coach are
**disjoint**, and that is correct. The file agrees once read per variant.

**On coverage — "100%" does not mean "decoded":**

> 100.0000% of every corpus file's bytes lie within a claimed span — every byte is
> accounted for by some structure. This does NOT mean the format is decoded: only
> **~1.8–2.6% of the GuiData section is decoded at the record level** — measured as the
> union of the spans `guidata.iter_screens` walks, over the section length: **1.8% in
> 11.23, 2.4% in 13.6, 2.6% in 14.7** (`parse_screen`'s opcode table is known
> incomplete). Additionally, ~5.5–7.5% of each file (58,092–60,148 bytes) sits in
> "trailing object" spans whose end is asserted only by a size-bound heuristic, not by a
> decode or a next-pointer — that bound has essentially zero margin on one table (exact
> equality in all three files) and up to ~55 KB of slack on another. "Claimed" means "a
> parser drew a boundary here that the file's structure is consistent with", not "the
> bytes inside are understood."

---

## 7. Suggested dispositions

*Status, 2026-08-24: **everything in this section has now been merged**, on branch
`rvc-gaps-sync` of `dbergl/rvc2mqtt`. The list below is kept as the reasoning, and each
item says where it landed. The bottom half of the section — what NOT to merge — still
binds.*

**Worth merging**

* The `0xF5` enum for `Jayco B-Van V13` (§2), labelled as per-build data rather than a
  protocol constant, with the existing four values kept as the overlap they are.
  *(Merged earlier; `g12_configuration.py` carries the enum with the per-build caveat.)*
* A note in `rvc-spec.yml` that `1FED9` group `0x94` exists, is used by the touchscreen,
  and has a different byte layout — recorded as unknown, not decoded (§5a). *(Merged
  earlier, byte-for-byte with the three captured payloads.)*
* **`0x92` = `Max Chrge Rate`, in percent** (§1, 2026-08-24). The one selector here that
  gained a real name, with a hardware probe behind it. Merged as a **read-only** sensor:
  raw/2 for display, no command topic, because `0x92` is also on the delta list below.
* **The delta-vs-absolute list** (§1, 2026-08-24). This is the item with teeth. `0x82`,
  `0x86`, `0x88`, `0x92`, `0x99` join `CC`/`CD`/`CE` as confirmed **signed deltas**;
  `0x16` is confirmed absolute; and the four that repo writes as absolutes — `0x0C`,
  `0x0D`, `0x0E`, `0x31` — are **untested, not safe**. Merged as a comment on the write
  path and in the spec, since writing an absolute to a delta selector adds it to the
  current value.
* **The read-back is universal and side-effect-free** (§1, 2026-08-24). `FF 96 <sel> 0F FF
  FF D3 FF` answers for any selector, including ones absent from the periodic sweep, and
  provokes no other traffic. Merged as a note — it is the safe way to poll.
* **`0xF1`/`0xF2` are floorplan-adjacent** (§1, 2026-08-24) — a role, not a name. Merged
  as a spec annotation only, explicitly not enough to name an entity from.
* **The `1FBDA` input-pin decode** (§5f), added 2026-08-24 **after** PC.24 settled it against
  the model that repo was using. Their handler's `aux_12v_active` and `0xFB`-as-idle readings
  are both refuted on the wire; our "no heartbeat" claim is refuted too. Merged as a rewrite
  of both the spec entry and the handler, with the 1 Hz refresh included.

*One thing that needed no merge, worth recording anyway:* the `0x00`/`0x04` retraction
(§1). That spec had always listed both as unidentified, so it was never wrong. The
retraction is noted there so a later reader does not "re-discover" it as news.

**Worth knowing, not merging**

* The three unknown selectors with a known write shape (§1). Enough to recognise a value
  as adjustable; not enough to name it.
* The step sizes in §3 — they agree with what you already chose.

**Explicitly do not merge**

* Any `D1`/`D2` function-byte rule (§5b). The thresholds path you have works and is backed
  by a capture; this file cannot improve on it.
* Any per-floorplan channel mapping. This work does not have one (§4).
*(An entry here for our `1FBDA` decode was removed on 2026-08-24. It said their observed
model stood over our static one until a key-turn settled it. **PC.24 settled it the same
day**, and it went the other way on both contested points — see §5f. The `1FBDA` decode is
now merged, and the correction to our own "no heartbeat" claim went with it.)*

---

## 8. Reproducing any of it

```bash
python3 -m fireflymcf.cli extract "firefly-13.6/mcf.bin" extractions/13.6
python3 -m fireflymcf.cli coverage "firefly-13.6/mcf.bin" --deep
python3 -m pytest tests/ -q -n auto  # the whole suite; counts in docs/TESTING.md
python3 -m fireflymcf.cli g12-rules  "firefly-13.6/mcf.bin"   # the G12 relay tables
python3 -m fireflymcf.cli g12-inputs "firefly-13.6/mcf.bin"   # the input pins of §5f
```

*Correction, 2026-09-10: no `xfail` remains. The three listed below closed on 2026-08-26 (the
status pairing, `MCF-FORMAT.md` §10 item 11a) and 2026-09-02 (`E3`/`E4` and `E5`/`EC`,
`GUICODE.md` Finding 61), each by finding the mechanism. The list is kept as the record of what
was searched for.* The `xfail`s were the honest ones. There were seven; three remained at the time
of writing, strict, so they would fail the day any of them was actually found:

* the `E3`/`E4` → channel 25/26 binding (`OptionBinding.channel` stays `None` rather than
  being filled from the answer key — §4.1's negative, still standing);
* the `E5`/`EC` visibility flag (`hides_control` stays `False`);
* the object → status-variable pairing (`GUICODE.md` Findings 32/33).

Two closed on **2026-08-15** with the option → selector join (`GUICODE.md` Finding 23 — the
selector is a literal byte in a static CAN-frame template in GuiCode, joined to the option's
config-buffer offset by code adjacency in a per-row tap handler). Two more closed on
**2026-08-18**: `find_option_defaults` was recovered by finding the eleven tap handlers that
*emit* the per-floorplan defaults as CAN-template literals, so all 90 answer-key entries now
reproduce without the derivation ever reading the answer key. *(This paragraph said "five
remain" until 2026-08-24; it was written before those two closed.)* Note for this audience: the panel writes options with DGN **`0x1FED9`**
(`FF 96 <sel> 0F <val> 00 D1 EA`); `0x15FCE` is the G12's broadcast of the same selectors. `docs/MCF-FORMAT.md` has the field-level
detail and the confidence label on every field; `docs/BUS-BASELINE.md` (PC.1–PC.11) is the
hardware evidence, and stays the authority wherever it and the file disagree.

## 9. Addendum, 2026-08-30 — two flash sessions decoded several of the loose ends above

The coach ran stock 11.23 and stock 14.7 for an afternoon (panel pair only, G12 unchanged;
`BUS-BASELINE.md` PC.40/PC.41). Directly relevant to this report:

- **The `d1 ff` tail (§1's "not interpreted") is a real write class: a periodic assert with no
  read-back.** Two live examples: 11.23's panel writes `0x93 := 1` (Service Mode) once a second
  with `D1 FF`, and 14.7's writes `0xDA := <SoC×2>` every ~3 s the same way. So the `0x00`/`0x9B`/
  `0x01` templates §1 flags with that tail are most plausibly periodic-assert frames, not
  tap-driven writes.
- **`0x2B` and `0x2C` decode at last**: quiet-time start/stop as `hour<<8 | minute`
  (`5632 = 0x1600` = 22:00, `2048 = 0x0800` = 08:00). Adjusted from the panel's AES page as
  `D2` deltas like the rest of the profile.
- **`0x0C`/`0x0E`/`0x16`** are time-at-start (s) / time-at-stop (s) / max-engine-run (min) —
  field↔selector pinned by stated-order ± presses on the 14.7 panel; the G12 clamps `0x16` to
  [60,115] live.
- **Three new 14.x-era selectors**: `0xDA` = live SoC in half-percent (panel-bridged to the G12),
  `0xDB`/`0xDC` = SoC-based start-at/stop-at in half-percent, delta-written. A 13.6 G12 stores
  and ignores all three.
- **`0xD8` (batteries count) is zero-based** — the panel radio "1"/"2" writes 0/1.
- **The floorplan enum's caution (§2) is now hardware-quantified**: the same stored value reads
  as a different floorplan per firmware version (8 = WD on 13.6, RY on 14.7, out-of-range on
  11.23; full per-version maps in PC.40/PC.41). Never label the `0xF5` value without knowing the
  coach's firmware version.
