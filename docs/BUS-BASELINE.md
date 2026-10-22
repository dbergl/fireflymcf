# Live Bus Baseline — PC.1

**Captured:** 2026-08-12 12:19:41, 20 s, 1139 frames (~57 frames/s)
**Coach:** Jayco B-Van, the subject vehicle
**Host:** a Linux machine on the coach's RV-C bus (SocketCAN, `candump`/`cansend`)
**Interface:** `can1`, altname `can_rvc` — `can_rvc` is *not* a separate device
**Log:** `captures/candump-2026-08-12_121941.log`
**Method:** `candump -l -L can1`, listen-only. Nothing was transmitted. `rvc2mqtt` was
running throughout and was not disturbed.

Every fact in this document is **observed-on-hardware** — the highest confidence level
available in this project.

## Frame decoding

RV-C over 29-bit CAN. For an ID such as `195FCE9C`:

- `DGN = (id >> 8) & 0x1FFFF` → `0x15FCE`
- `source address = id & 0xFF` → `0x9C` (the G12)

For `15FCE` payloads, byte 0 is the message-type selector and bytes 2–3 are a
little-endian `uint16` value. Confirmed against known selectors — e.g. `0D 00 40 04` →
`0x0440` = 1088 × 0.05 = **54.4 V** stop-at-voltage, correct for this coach's battery.

## Bus inventory

| DGN | Source | Frames | Note |
|---|---|---|---|
| `15FCE` | `9C` | 171 | G12_CONFIGURATION — the target |
| `0EF70` | `80` | 134 | Wakespeed BMS |
| `0EF80` | `70` | 94 | |
| `1FFB7` | `9C` | 80 | |
| `0BFC1` | `9C` | 80 | |
| `0FECA` | `FD` | 40 | |
| `1FFFD`/`1FFFC`/`1FFFB`/`1FEC8`/`1FEA3` | `80` | 40 each | |
| `1AAFD` | `9C` | 40 | |

## The coach's configuration, as broadcast

| Selector | Meaning | Raw | Decoded |
|---|---|---|---|
| `F5` | **selected floorplan** | `08 00` | **`8` = `WD`** |
| `EF` | gen/AES mode | `03 00` | `3` = AES Single Output |
| `9B` | AES enabled | `00 00` | off |
| `E3` | cargo/bath light ch.25 | `01 00` | **on** |
| `E5` | bunk accent | `01 00` | **on** |
| `E9` | bath fan | `00 00` | off |
| `EC` | black tank | `01 00` | **on** |
| `E6` | progressive inverter | `00 00` | off |
| `D7` | Go Power! controllers | `00 00` | 0 |
| `D8` | battery count | `00 00` | 1 battery |
| `0D` | stop at voltage | `40 04` | 54.4 V |
| `31` | start at voltage | `FC 03` | 51.0 V |
| `0C` | time at start volts | `3C 00` | 60 |
| `0E` | time at stop volts | `08 07` | 1800 |
| `16` | max engine run time | `73 00` | 115 min |
| `2B` | quiet time start | `2D 16` | 22:45 |
| `2C` | quiet time stop | `00 08` | 08:00 |
| `2F` | AGS low volts trigger | `00 00` | off |
| `F7` | AGS retry interval | `3C 00` | 60 s |
| `CC` | 0–33% threshold | `F0 D2` | 54000 |
| `CD` | 33–67% threshold | `40 9C` | 40000 |
| `CE` | 67–100% threshold | `A8 61` | 25000 |

**The floorplan question is settled for this coach: `0xF5 = 8 = WD`.** P4 no longer needs
to infer which floorplan the PDF and OPTIONS screen belong to.

## Sixteen selectors absent from rvc2mqtt

The G12 broadcasts these; `rvc-spec.yml`'s 27-entry table does not list them.

| Selector | Observed payload | Value | Note |
|---|---|---|---|
| `00` | `00 00 01 00` | 1 | |
| `04` | `04 00 01 00` | 1 | |
| `06` | `06 00 00 00` | 0 | |
| `0A` | `0A 00 22 04` | 1058 | |
| `0F` | `0F 00 08 07` | 1800 | same value as `0E` |
| `11` | `11 00 2D 00` | 45 | |
| `2E` | `2E 00 00 00` | 0 | appears in rvc2mqtt's AES-enable frame sequence |
| `75` | `75 00 01 00` | 1 | |
| `92` | `92 00 C8 00` | 200 | |
| `95` | `95 00 00 00` | 0 | |
| **`E4`** | `E4 00 00 00` | 0 | **option-block candidate** |
| **`E8`** | `E8 00 00 00` | 0 | **option-block candidate** |
| **`EB`** | `EB 00 00 00` | 0 | **option-block candidate** |
| `EE` | `EE 00 FF FF` | 65535 | `FFFF` reads as unset/not-applicable |
| `F1` | `F1 00 0D 00` | 13 | |
| `F2` | `F2 00 06 00` | 6 | |

## Candidates for `Heat Pump` and `Bath Light`

The coach's OPTIONS screen lists six toggles. Four have confirmed selectors, all in the
`0xE0` block:

| Option | Selector |
|---|---|
| Heat Pump | **unknown** |
| Bunk Accent | `E5` |
| Bath Fan | `E9` |
| Cargo / Bath Light (CH. 25) | `E3` |
| Bath Light | **unknown** |
| Black Tank | `EC` |

The unknown selectors in the same block are **`E4`, `E8`, `EB`** — all currently `0`,
consistent with two disabled options plus one unrelated setting. `EE` is likely excluded
by its `FFFF` payload.

**This is a hypothesis from adjacency, not a finding.** Confirming it requires PC.2:
toggling each option on the touchscreen and observing which selector changes.

## Not yet observed in PC.1

No `1FED9` frames appeared — expected, since that DGN carries *writes* from the
touchscreen (`0x9F`) and nobody touched the screen during the capture. PC.2 depends on
generating exactly these frames.

---

# PC.2 — Owner-driven toggles

**Captured:** 2026-08-12, 79.8 s, 4837 frames
**Log:** `captures/pc2-toggles-2026-08-12.log`
**Method:** listen-only. The owner operated the touchscreen; nothing was transmitted from
this side.

Both unknown selectors are now **observed-on-hardware**.

## Result 1 — `Heat Pump` = `0xEB`

A plain boolean, one selector:

| t (s) | Frame | Meaning |
|---|---|---|
| 9.8 | `FF 96 EB 0F 01 00 D1 EA` | set `EB` = 1 |
| 17.4 | `FF 96 EB 0F 00 00 D1 EA` | set `EB` = 0 |

The `15FCE` broadcast followed: `EB` went `0 → 1 → 0` at those exact times.

## Result 2 — `Bath Light` is a mutually exclusive pair, `0xE3` / `0xE4`

The owner reported that Bath Light "toggles between the two bath light options rather
than on/off", and the capture shows exactly that — **two selectors written together, in
opposite directions**:

| t (s) | Frames | Meaning |
|---|---|---|
| 27.2 | `FF 96 E4 0F 01 00 …` **and** `FF 96 E3 0F 00 00 …` | `E4` = 1, `E3` = 0 |
| 35.5 | `FF 96 E3 0F 01 00 …` **and** `FF 96 E4 0F 00 00 …` | `E3` = 1, `E4` = 0 |

Broadcast state confirms the anti-correlation: `E3` `1 → 0 → 1` while `E4` `0 → 1 → 0`.

So the two OPTIONS-screen entries are one **radio choice**, not two independent switches:

- **`E3`** — `Cargo / Bath Light (CH. 25)`: the combined light on channel 25.
- **`E4`** — `Bath Light`: a separate bath light.

Exactly one is active. This is why the screen presents them adjacently with `(CH. 25)`
as a continuation line.

## Result 3 — the write protocol, confirmed

Every setting is written as a **set followed by a read-back**:

```
FF 96 <selector> 0F <val_lo> <val_hi> D1 EA     set
FF 96 <selector> 0F FF FF        D3 FF          read-back request
```

For the paired write, both sets are sent first, then both read-backs — the pair is
updated together rather than sequentially. This matches rvc2mqtt's implementation of the
frame shape.

## Implication for rvc2mqtt

`rvc-spec.yml` lists `E3` as `cargo/bath light ch.25` and `g12_configuration.py` exposes
it to Home Assistant as an **independent switch** (`cargo_bath_light`), with no knowledge
of `E4`. On the evidence above the two are mutually exclusive, so writing `E3` alone —
which the current `cargo_bath_light_set_topic` handler does — can drive the G12 into a
state the touchscreen never produces (both on, or both off).

Whether the G12 rejects or silently accepts such a state is **not established here**; the
capture only shows what the touchscreen does. Flagged for the owner as a correctness
question in working software, not asserted as a defect.

## Correction to the PC.1 hypothesis

PC.1 proposed `E4`, `E8`, `EB` as the candidates by block adjacency. Two were right
(`E4`, `EB`); **`E8` was not involved** and remains unidentified. Adjacency was a useful
way to prioritise the experiment and a poor way to conclude — which is why it was labelled
a hypothesis rather than recorded as a finding.

## Still unidentified after PC.2

Fourteen selectors from PC.1 remain unexplained: `00`, `04`, `06`, `0A`, `0F`, `11`,
`2E`, `75`, `92`, `95`, `E8`, `EE`, `F1`, `F2`.

---

# PC.3 — Electrical Options and counts

**Captured:** 2026-08-12, 250.8 s, 15252 frames
**Log:** `captures/pc3-electrical-2026-08-12.log`
**Method:** listen-only; the owner operated the touchscreen throughout.

Owner actions: Bath Fan on/off; Progressive Inverter on/off; AGS disabled manually, then
AES selected, then AES Single Output; battery count 1→2→1; Go Power! controllers 0→1→0.

## Result 1 — rvc2mqtt's selectors DO transfer across models

This was open question 14, and `rvc-spec.yml`'s stated worry. Four selectors derived on
an **Ethos 20DL** were confirmed unchanged on this **B-Van `WD`**:

| Selector | Meaning | Confirmed by |
|---|---|---|
| `E9` | bath fan | on at t=51.0, off at t=58.1 |
| `E6` | progressive inverter | on at t=69.2, off at t=76.7 |
| `D8` | battery count | 1→2 at t=169.0, back at t=176.7 |
| `D7` | Go Power! controllers | 0→1 at t=203.5, back at t=210.8 |

Together with `E3`/`E5`/`EC`/`EF`/`F5` already matching, **the selector namespace appears
global rather than per-floorplan.** That validates a load-bearing assumption in working
software. It is evidence from two models, not proof across all — but the worry recorded
in `rvc-spec.yml` is now substantially reduced.

## Result 2 — options have dependencies: `E6` forces `EF` = AGS

The owner observed that selecting Progressive Inverter also force-selected AGS. The
capture shows both written in the **same transaction**:

```
t=69.2  FF 96 E6 0F 01 00 D1 EA     progressive inverter = 1
t=69.2  FF 96 EF 0F 01 00 D1 EA     gen/AES mode        = 1 (AGS)
```

So options are not independent — enabling one can force another. Whatever encodes that
rule is a structure the MCF decode must account for, and a behaviour any writer of these
settings must respect.

Mode changes also always rewrite `0x15` (AGS config) and `0xF7` (retry interval), and
selecting AES modes additionally writes `D7` and `E6` to `0`.

## Result 3 — `EF` has a fourth value: `0`

rvc2mqtt's enum lists `1: AGS`, `2: AES`, `3: AES Single Output`. Disabling AGS manually
produced **`EF` = 0** at t=105.9 — a disabled/none state absent from the spec.

*(Naming note: the owner referred to the third mode as "AES Single **Input**" while
`rvc-spec.yml` calls it "AES Single **Output**". Worth checking the screen wording; the
value `3` is not in doubt.)*

## Result 4 — the same selectors carry a different parameter set per mode

The most structurally significant finding. Identical selectors, entirely different values
and physical ranges depending on `EF`:

| Selector | AES Single (`EF`=3) | AGS (`EF`=1) | AES (`EF`=2) |
|---|---|---|---|
| `0D` stop at voltage | 1088 → **54.40 V** | 270 → **13.50 V** | 1060 → 53.00 V |
| `31` start at voltage | 1020 → **51.00 V** | 244 → **12.20 V** | 1020 → 51.00 V |
| `0E` time at stop volts | 1800 s | 7200 s | 1800 s |
| `0F` *(companion of `0E`)* | 1800 | 900 | 1800 |
| `16` max run time | 115 min | 720 min | 115 min |

The AES profile describes the **48 V engine/lithium** system; the AGS profile describes a
**12 V generator** system. The encoding is the same (`×0.05` volts) — the *applicable
range* is what changes.

This is the sense in which Firefly "reuses message types": not across floorplans, but
across **modes**. The MCF decode should show where these profiles live.

**Concrete implication for rvc2mqtt.** `g12_configuration.py` publishes fixed Home
Assistant ranges — `stop_at_voltage` min 50.0 / max 58.8, `start_at_voltage` min 51.0 /
max 54.8, `max_engine_run_time` min 60 / max 115. In AGS mode the coach reports 13.50 V,
12.20 V and 720 min, all far outside those bounds, so the HA number entities would
misrepresent and refuse valid values. The ranges need to be mode-dependent.

## Result 5 — `0x0F` is bound to `0x0E`

`0F` moves whenever `0E` moves and is written alongside it in the AES parameter restore
at t=235 (both set to 1800). It is a companion field of "time at stop volts", not an
independent setting. Its exact role is still unknown.

## Result 6 — `0x0A` is a live measurement, not a setting

`0A` tracks `0D` closely but sits slightly below it (1058 vs 1088 in AES; 247 vs 270 in
AGS) and — unlike every genuine setting — **drifts continuously by one LSB**.

Hypothesised here as the measured pack voltage; **confirmed by the PC.4 load test below**
and now *observed-on-hardware*.

## Selectors still unidentified

Of the fourteen unknowns from PC.1, `0A` and `0F` are now partially characterised above.
**Twelve remain fully unidentified:** `00`, `04`, `06`, `11`, `2E`, `75`, `92`, `95`,
`E8`, `EE`, `F1`, `F2`. These are targets for the static analysis.

---

# PC.4 — Load test: `0x0A` confirmed as measured pack voltage

**Captured:** 2026-08-12, 180 s, 10854 frames
**Log:** `captures/pc4-loadtest-2026-08-12.log`
**Method:** listen-only. The owner applied a load to the lithium pack and read voltages
from the AES screen.

## Design

If `0A` is a measurement it must fall under load and recover afterwards, tracking the
display. If it is a setting it cannot move at all. `0D` and `31` act as the control: as
setpoints they must stay fixed throughout.

## Result — confirmed, and cleanly

| Source | Before | Minimum under load | After |
|---|---|---|---|
| **AES screen** (owner) | 53.0 V | 52.1 V | 52.4 V |
| **`0x0A` × 0.05** | 52.95 V | 52.05 V | 52.50 V (still recovering) |

The trace follows the load precisely — a slow decline from 52.95 V to 52.05 V over ~130 s,
then monotonic recovery once the load was removed:

```
t=  4.6  1059  52.95 V     baseline
t= 89.6  1052  52.60 V     under load
t=134.6  1041  52.05 V     minimum
t=144.6  1046  52.30 V     load removed, recovering
t=179.6  1050  52.50 V     still climbing at end of capture
```

Screen and bus agree within one display digit throughout; the residual 0.05 V is exactly
the difference between the screen's one-decimal rounding and `0A`'s 0.05 V resolution
(52.95 rounds to 53.0).

**`0x0A` = measured battery voltage, `uint16 × 0.05` V**, for whichever pack the current
`EF` mode monitors. Promoted from *inferred* to **observed-on-hardware**.

## The control held, and the negative result is informative

**`0A` was the only selector in the entire capture that moved.** `0D` and `31` stayed
fixed at their setpoints, as required — so the experiment distinguishes measurements from
settings rather than merely showing that something changed.

It also means **none of the twelve unknown selectors track voltage**: no current, state of
charge, or power field is present in `15FCE`. If those exist they live in another DGN.
That removes a whole class of hypothesis for the remaining unknowns.

## Selectors still unidentified

Twelve, unchanged by this test: `00`, `04`, `06`, `11`, `2E`, `75`, `92`, `95`, `E8`,
`EE`, `F1`, `F2`.

---

# PC.5 — Complete floorplan enumeration

**Captured:** 2026-08-12, 286 s
**Log:** `captures/pc5-floorplans-2026-08-12.log`
**Method:** listen-only; the owner selected each floorplan on the touchscreen.

## The roster (from the FLOORPLAN SELECTION screen)

The screen groups floorplans into two families, plus a `Ship Mode` button:

| Generator Models | Li-AES Models |
|---|---|
| RA, RY, RD, RT, CB, *Ship Mode* | WA, **WD** *(this coach)*, WT, TB, SY |

## Complete `0xF5` enum — observed, values 1–10 with no gaps

| `F5` | Floorplan | Family | Previously known? |
|---|---|---|---|
| 1 | RT | Generator | **new** |
| 2 | RA | Generator | **new** |
| 3 | RD | Generator | **new** |
| 4 | SY | Li-AES | confirmed |
| 5 | TB | Li-AES | **new** |
| 6 | CB | Generator | **new** |
| 7 | WA | Li-AES | confirmed |
| 8 | **WD** | Li-AES | confirmed *(this coach)* |
| 9 | WT | Li-AES | confirmed |
| 10 | RY | Generator | **new** |

Six values are new; the four rvc2mqtt already had were independently reconfirmed in the
same pass, which validates the existing table rather than merely extending it.

`Ship Mode` was deliberately **not** selected — the name suggests a factory
transport/storage state with unknown exit behaviour, and it is not worth a spec entry.

## Selecting a floorplan writes a 12-selector transaction

Floorplan selection is not a single write. Every selection emitted the same twelve
selectors, in the same order:

```
F5, EF, E5, E9, E4, EB, E3, E6, D7, EC, 15, F7
```

**CORRECTED 2026-08-13 — see PC.12.** This was originally read as the touchscreen
re-asserting the owner's *current* settings, which would explain why they survived the
cycling. That is wrong. The transaction writes the **newly selected floorplan's
defaults**. At t=136.1 in this same capture, selecting `TB` wrote `E5=0` and `E3=0`
while the owner's settings were both `1`. The settings appeared to survive only because
`WD`'s defaults happen to equal what the owner had set.

## The option list is shared; only defaults differ

The owner reports that **every floorplan showed the same six options**, with different
default selections per floorplan. This corrects the assumption in the design spec that
each floorplan carries its own option *list*.

The structure to look for in GuiData is therefore a **shared option list plus a
per-floorplan defaults table**, not per-floorplan option lists. This is a much more
specific target than the spec previously assumed, and it came from the coach rather than
from the firmware.

## Restore verified

All twelve baseline selectors were compared against PC.1 after the owner returned to
`WD`. Every one matched: `E3`=1, `E5`=1, `EC`=1; `E4`, `E6`, `E9`, `EB`, `D7`, `D8`,
`9B`=0; `EF`=3; `F5`=8. **Nothing was left disturbed.**

---

# PC.6 — Go Power! configuration screens

**Captured:** 2026-08-12, 123 s
**Log:** not retained separately; no configuration traffic was generated.

The owner opened the five-page `GO POWER! CONFIGURATION` screen. With `D7` = 0 (no
controllers installed) every field read zero, and **no `1FED9` writes occurred** — so
this yields no new selectors.

It is still useful as a **known screen inventory** for the GuiData work: a fixed 5-page
layout with 21 settings and a constant 6-button action bar (`Start Maintenance Cycle`,
`Force Charge`, `Clear History`, `Set Defaults`, `Reboot Solar Controller`,
`Return to Options`).

| Page | Fields |
|---|---|
| 1/5 | Operating Status, Default State on Power-up *(both Enabled/Disabled radios)* |
| 2/5 | Battery Bank Size (Ah), Max Charge Current (A), Battery Type *(Flooded)* |
| 3/5 | Bulk-Absorption Voltage, Float Voltage, Charge Return Voltage, Under Voltage Warning |
| 4/5 | Battery High/Low Voltage Limit, High/Low Voltage Return, Low Voltage Limit Delay (Sec) |
| 5/5 | Absorption Duration (Min), Temperature Compensation Factor (°C), Maintenance Cycle Voltage / Duration (Min) / Interval (Days) |

Navigating these screens did surface **DGNs absent from the PC.1 inventory**: `0EAFF`
from the touchscreen (`9F`), and `0EBFF` / `0ECFF` from `80`, `9C` and `FD`. These are
J1939 request and transport-protocol frames — the screen requesting data from a
controller that is not present. Noted for completeness; not pursued.

---

# PC.7 — Display settings produce no bus traffic

**Captured:** 2026-08-12, 341 s, 20237 frames
**Log:** `captures/pc7-settings-2026-08-12.log`
**Method:** listen-only. Owner changed brightness 100→50→80, temperature units F→C→F,
clock 12h→24h→12h, then touched a hallway button and cancelled.

## Result — a definitive negative

**Zero `15FCE` values changed. Zero group-`0x96` writes occurred.** Across 341 seconds
and 20k frames, none of those settings touched the bus.

Display preferences — brightness, temperature units, clock format — are therefore stored
**locally on the Spyder touchscreen**, not in the G12, and never traverse RV-C.

This eliminates a whole class of hypothesis for the remaining unknown selectors. `11`
(=45), `92` (=200), `F1` (=13) and `F2` (=6) had looked like plausible timeout, brightness
and clock values; they are **not** display settings. Whatever they are, the Settings
screen does not write them.

## A second `1FED9` group exists: `0x94`

rvc2mqtt decodes `1FED9` only for **group `0x96`** (G12 configuration). Four frames from
the touchscreen (`0x9F`) used **group `0x94`**:

| t (s) | Frame | Selector | Function |
|---|---|---|---|
| 236.8 | `FF 94 FF FF FF FF F0 FF` | `FF` | `F0` |
| 244.4 | `FF 94 FF FF FF FF F0 FF` | `FF` | `F0` |
| 299.4 | `FF 94 01 FF 1C FF E5 FF` | `01` | `E5` |
| 299.6 | `FF 94 00 FF 1E FF E3 FF` | `00` | `E3` |

**Purpose unknown.** The last two are 0.2 s apart, which is *consistent with* the hallway
button press and cancel — but four frames is not enough to claim that, and the byte
layout differs from group `0x96` (values appear at byte 4 with a function code at byte 6,
but the `FF` padding pattern is different). Recorded as an observation, not a finding.

Isolating it would need a dedicated capture: press a button and let it complete, then
repeat and cancel, and diff.

## Unrelated background traffic

`1FBDA` from `0x9F` is a steady 14-second heartbeat carrying a constant
`F9BBFFFFFFFFFFFF`. Not related to any owner action.

## Selectors still unidentified

Unchanged at twelve: `00`, `04`, `06`, `11`, `2E`, `75`, `92`, `95`, `E8`, `EE`, `F1`,
`F2` — now with display settings ruled out as an explanation.

---

# PC.8 — Lighting: instance = channel = pinout, and how an option gates a load

**Captured:** 2026-08-12, ~470 s
**Log:** `captures/pc8-lighting-2026-08-12.log`
**Method:** listen-only. Owner operated interior master, each light individually, exterior
master, and switched the Bath Light option while pressing the Bath button.

Lighting rides the standard RV-C dimmer DGNs — `1FEDB` (`DC_DIMMER_COMMAND_2`) from the
touchscreen (`0x9F`) — not `15FCE`.

## Result 1 — RV-C instance == G12 channel == PDF pin number

Eight lights, operated one at a time, mapped **8 for 8** against the G12 Master sheet:

| Owner's name | Instance | PDF channel |
|---|---|---|
| Main Ceiling | 32 | `MAIN CEILING LTS` |
| Bed Lights | 24 | `BED LTS` |
| Kitchen OHC | 22 | `KITCHEN OHC` |
| Bunk Accent | 34 | `BUNK ACCENT LTS` |
| Bath | **25** | `CARGO LTS / BATH LTS` |
| Living Rm OHC | 35 | `SECURITY LT / LIVRM OHC / BENCH SEAT OHC` |
| Awning Light | 21 | `AWNING LT` |
| Step Light | 23 | `STEP LT` |

This is the join the design spec expected to have to *infer* in P4. It is now observed:
**the RV-C instance number is the G12 channel number is the number printed on the
pinout.** Any decoded GuiData channel reference can be checked against the sheet directly.

## Result 2 — master groups

| Master | Instances | Channels |
|---|---|---|
| Interior | 22, 24, 25, 26, 32, 34, 35 | all named interior lights |
| Exterior | **21, 23** | `AWNING LT`, `STEP LT` |

The two groups are disjoint and together cover every light channel on this floorplan.

## Result 3 — "memory" behaviour, explained

The owner noticed master "remembers what was on and off". The bus shows why:

| Action | Frame |
|---|---|
| Master **off** | level `0`, **command 6** (`Memory Off`) |
| Master **on** | level **251** (`0xFB`), command 0 |
| Individual light | level 250, **command 5** (`Toggle`) |

Level 251 is outside the normal 0–250 range and acts as *restore*. The state is held in
the load controller, not the display — the screen addresses all group members every time
regardless of their individual states.

## Result 4 — how the `E3`/`E4` option gates its load

**This answers design-spec open question 9.** The owner switched the Bath Light option
while operating the Bath button:

| t (s) | Event |
|---|---|
| 393.2 | `E4`=1, `E3`=0 *(option → "Bath Light")* — **and `inst 25` ← OFF (cmd 3)** |
| 397.7, 402.6 | Bath button → **`inst 26`** (toggle) |
| 414.1 | `E3`=1, `E4`=0 *(option → "Cargo / Bath Light")* — **and `inst 26` ← OFF (cmd 3)** |
| 422.2, 426.3 | Bath button → **`inst 25`** (toggle) |

So the option **rebinds the same UI control to a different physical channel** — 25 or 26
— rather than masking or enabling a fixed one. The mechanism is a *channel reference*,
which means GuiData must hold a per-option channel binding that the option selects
between.

**Unanticipated detail:** switching the option sends an explicit **OFF (command 3) to the
channel being abandoned**, so the outgoing load cannot be left energised with no control
bound to it. That is deliberate behaviour worth preserving in anything that writes these
options.

## Result 5 — the owner's automation is visible on the bus

`inst 44` (`WATER PUMP` per the sheet) was commanded from source `0x82` exactly 0.1 s
after `inst 25` every time it was switched — the owner's own Home Assistant automation,
keyed to channel 25. It did **not** fire for channel 26, consistent with an automation
bound to the specific instance.

## Corroboration from the PDF's `OPT` annotation

Channels the sheet marks `OPT` correspond to the OPTIONS-screen toggles — `25`, `26` and
`34` all carry `DIM, LM, OPT`. The pinout was flagging the option-gated channels all
along.

---

# PC.9 — Full control sweep and the second gating mechanism

**Captured:** 2026-08-12, ~1350 s (continuation of the PC.8 log)
**Method:** listen-only. Owner exercised awning, water pump, tank heaters, AES panel,
cooling, then toggled Bunk Accent and Black Tank options.

## Twelve more channels, again 12 for 12

| Instance | PDF channel |
|---|---|
| 3 / 4 | `AWNING EXT` / `AWNING RET` |
| 5, 6, 7, 8 | `TANK HEAT FRESH` / `GREY` / `BLACK` / `GREY 2` |
| 9, 43 | `A/C COOL` *(two outputs)* |
| 11, 12 | `A/C FAN HIGH` / `A/C FAN LOW` |
| 18 | `AES START/STOP` *(engine relay)* |
| 44 | `WATER PUMP` |

Combined with PC.8's eight lights, **instance == channel == pin number is now verified
across 20 channels** on floorplan `WD`. This is the validation dataset for the MCF decode:
any channel reference the parser recovers can be checked against a known-correct answer.

## The second gating mechanism — options hide controls

The owner toggled Bunk Accent (`E5`) and Black Tank (`EC`) off and reported that **the
button disappeared from the screen in both cases**.

Combined with PC.8, options gate loads **two different ways**:

| Option kind | Mechanism | Evidence |
|---|---|---|
| `E3`/`E4` — mutually exclusive pair | **rebinds** a control to a different channel (25 ↔ 26) | PC.8, observed on the bus |
| `E5`, `EC` — plain booleans | **shows/hides** the control entirely | PC.9, observed on the screen |

GuiData must therefore carry **both** a per-option channel binding *and* a per-control
visibility flag. Two concrete structures for P3 to find, where the design spec previously
had only "options gate loads somehow".

Note the asymmetry in evidence: the rebinding was proven from bus traffic, the
show/hide from the display. Hiding a control produces no CAN frames, so it can only be
observed on the screen — worth remembering when interpreting silence on the bus.

## Behavioural details visible only in the traffic

- **The awning is interlocked.** `AWNING RET` is commanded OFF in the same frame group
  before `AWNING EXT` goes on, so the pair can never drive simultaneously.
- **Momentary controls repeat.** Awning and A/C hold with `command 1` (on-duration)
  re-sent about once a second while active; the G12 refreshes its own A/C loads every
  5 s. A watchdog — a dropped bus releases the load rather than latching it on.
- **The G12 commands its own loads.** `A/C COOL`/`FAN` frames originate from `0x9C`, not
  the touchscreen: the screen sets a demand and the G12 drives the outputs.

## Tank probe diagnostics — a confirmation, not a discovery

A hidden `PROBE TANK DIAGNOSTICS` page shows per-tank ADC raw values with min/max
tracking. `DGN 0BFC1` carries them, one frame per tank:

```
03 F0 FF ...   instance 3 (BLACK),  ADC 0xFFF0 = 65520
04 73 DF ...   instance 4 (GREY2),  ADC 0xDF73 = 57203
```

**rvc2mqtt already decodes this correctly** as `G12_TANK_LEVEL_SENSOR` with instances
1–4 = fresh / gray / black / second gray. The screen independently confirms that mapping
(`BLACK` 65520, `GREY2` 57276 against 57203 on the bus, drifting in real time).

The useful new connection: the `CC`/`CD`/`CE` tank thresholds (54000 / 40000 / 25000) are
**in these same raw ADC counts**. rvc2mqtt exposes them as unitless 0–65535 numbers, which
is correct but undocumented as to what they threshold on.

## PC.10 — Threshold screen confirms the `CC`/`CD`/`CE` semantics

The hidden diagnostics page has a `Thresholds` view showing the three values as band
boundaries on the raw ADC scale:

```
0%  <-  54000  ->  33%  <-  40000  ->  67%  <-  25000  ->  100%
```

Against the values captured in PC.1 — `CC`=54000, `CD`=40000, `CE`=25000 — this confirms:

| Selector | Boundary |
|---|---|
| `CC` | 0% ↔ 33% |
| `CD` | 33% ↔ 67% |
| `CE` | 67% ↔ 100% |

**rvc2mqtt's existing labels are correct**, and the descending order (higher ADC = emptier)
matches the "lower raw value = more full" handling already present in
`g12_tank_level_sensor.py`. Screen and bus agree exactly at 54000.

Note these are the **G12's global thresholds**; the owner's rvc2mqtt floorplan override
additionally carries per-tank custom thresholds, which are a separate, bridge-side
concept.

A `Reset Default Thresholds` button exists on this page and was not pressed.

---

# PC.11 — Threshold writes are signed DELTAS, not absolute values

**Captured:** 2026-08-12, in the PC.8 log around t=1546–1570 s
**Method:** listen-only. Owner pressed the decrement and increment arrows once each for
all three thresholds on the hidden Thresholds page.

## What the arrows sent, and what happened

| t (s) | Write | Resulting broadcast |
|---|---|---|
| 1546.5 | `CC` ← **64536** | `CC` 54000 → **53000** |
| 1551.7 | `CC` ← **1000** | `CC` 53000 → **54000** |
| 1553.6 | `CD` ← 64536 | `CD` 40000 → 39000 |
| 1558.4 | `CD` ← 1000 | `CD` 39000 → 40000 |
| 1564.8 | `CE` ← 64536 | `CE` 25000 → 24000 |
| 1569.4 | `CE` ← 1000 | `CE` 25000 restored |

`64536` is `-1000` as a two's-complement `int16` (`65536 − 1000`). Every write moved the
stored value by **exactly ±1000** — it did not set it to the written number.

**For `CC`, `CD` and `CE` the `1FED9` payload is a signed 16-bit *delta*, not an absolute
value.** The frame shape is otherwise identical to an absolute set, `D1 EA` function
included, so nothing in the frame distinguishes the two semantics.

## This is a real defect in rvc2mqtt

`g12_configuration.py` writes these as absolute values:

```python
elif topic == self.threshold_cc_set_topic:
    raw_value = round(float(payload))
    frame = struct.pack("<BBBBHBB", 0xFF, 0x96, 0xCC, 0x0F, raw_value, 0xD1, 0xEA)
```

Setting `50000` from Home Assistant would therefore **add 50000** to the current
threshold rather than set it — overflowing a `uint16` and corrupting the coach's tank
calibration. The same applies to `CD` and `CE`.

The fix is to send `target − current`, which requires the current value to be known.
Applied in this session; see the rvc2mqtt working tree.

**Scope of the delta semantics is not established.** Only `CC`/`CD`/`CE` were tested this
way. Other selectors are demonstrably absolute — `16` was written as `115` and became
`115`, and the option booleans were written as `0`/`1`. Whether any *other* selector is
delta-based is unknown and should not be assumed either way.

## Correction to PC.1

PC.1 recorded `CC` as **53996**. That was an arithmetic error on my part: `0xD2F0` is
**54000**. The coach never held 53996, and the screen and bus agree exactly. `CD` and
`CE` were recorded correctly. The value has been corrected throughout this document.

The owner's thresholds are **unchanged** from the start of the session: 54000 / 40000 /
25000. Each arrow press was reversed by its opposite.

## What "Start Timer" does

The owner reported that after pressing `Start Timer`, the per-tank Minimum/Maximum
figures began tracking live readings — before it, they held stale values that were not
even internally consistent (a Maximum below its Minimum).

No `1FED9` write accompanied it, and the `0BFC1` tank broadcast rate held steady at
~240 frames/minute throughout, before and after. **Start Timer is display-side only** —
it resets and runs a min/max capture window on the screen. Like the brightness and clock
settings in PC.7, it never touches the bus.

---

# PC.12 — Per-floorplan option defaults

**Captured:** 2026-08-13, 761 s. **Sources:** the owner read the defaults off the
touchscreen for nine floorplans; the values were then independently confirmed from the
**PC.5 capture of 2026-08-12**, which already contained them.

## The complete table

Every floorplan's defaults, in `0xF5` value order. `WD` comes from the capture only —
the owner was sitting on it, so its on-screen state is their configuration rather than
the default.

| `F5` | Plan | Heat Pump `EB` | Bunk Accent `E5` | Bath Fan `E9` | Cargo/Bath `E3` | Bath Light `E4` | Black Tank `EC` | Mode `EF` | Prog Inv `E6` | GoPower `D7` | Batt `D8` |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | RT | 0 | **1** | 0 | **1** | 0 | **1** | 1 AGS | 1 | 1 | 0 |
| 2 | RA | 0 | **1** | 0 | **1** | 0 | **1** | 1 AGS | 1 | 1 | 0 |
| 3 | RD | 0 | **1** | 0 | **1** | 0 | **1** | 1 AGS | 1 | 1 | 0 |
| 4 | SY | 0 | **1** | 0 | **1** | 0 | 0 | **0 disabled** | 0 | 0 | 0 |
| 5 | TB | 0 | 0 | 0 | 0 | 0 | **1** | 3 AES Single | 0 | 0 | 0 |
| 6 | CB | 0 | 0 | 0 | 0 | 0 | **1** | 1 AGS | 1 | 1 | 0 |
| 7 | WA | 0 | **1** | 0 | **1** | 0 | **1** | 3 AES Single | 0 | 0 | 0 |
| 8 | **WD** | 0 | **1** | 0 | **1** | 0 | **1** | 3 AES Single | 0 | 0 | 0 |
| 9 | WT | 0 | **1** | 0 | **1** | 0 | **1** | 3 AES Single | 0 | 0 | 0 |
| 10 | RY | 0 | **1** | 0 | 0 | 0 | **1** | 1 AGS | 1 | 1 | 0 |

Observations that hold across the whole table:

- **`Heat Pump` and `Bath Light` default off everywhere.** No floorplan ships with either.
- **The Generator family** (RT, RA, RD, CB, RY) always defaults to `EF`=1 (AGS) with
  `E6`=`D7`=1; **the Li-AES family** (SY, TB, WA, WD, WT) never does. `E6` and `D7` are
  identical in all ten rows.
- **`SY` is the only floorplan with `EF`=0**, the "disabled" value that was absent from
  rvc2mqtt's enum until this project added it.
- Only **four distinct option patterns** exist across ten floorplans: `{E5,E3,EC}` (six
  plans), `{EC}` (TB, CB), `{E5,EC}` (RY), `{E5,E3}` (SY).

## The correction this produced

PC.5 recorded the 12-selector floorplan-selection transaction as the touchscreen
"re-asserting the entire option profile", which read as preserving the owner's settings.
**That was wrong**, and this table is what exposed it. Re-reading the PC.5 log with the
defaults in hand: selecting `TB` at t=136.1 wrote `E5=0` and `E3=0` while the owner had
both set to `1`. The transaction pushes the **selected floorplan's defaults**. The
owner's configuration appeared untouched only because `WD`'s defaults coincide with it.

The nine screen readings and the nine capture transactions agree on every one of the 90
values, which is what makes the correction safe to make.

## Where the defaults are NOT stored

With real data in hand the search became scorable, and it comes up empty. Searched
across all three MCFs:

- the `EF` sequence `1,1,1,0,3,1,3,3,3,1` in floorplan order, contiguous and at every
  stride 1–32 — **no hit**;
- the six option bits packed as one byte per floorplan, MSB-first and LSB-first, at every
  stride 1–32 — **no hit**;
- a 10-byte per-floorplan record `(EB,E5,E9,E3,E4,EC,EF,E6,D7,D8)` — matched for 1–2 of
  10 plans at scattered offsets, i.e. chance;
- `(selector, value)` pairs — **no hit**.

The apparent 9/10 and 10/10 hits on the bare 6-byte and 4-byte forms are noise: those
are short low-entropy strings like `00 01 00 01 00 01`, and RT/RA/RD "match" at one
shared offset precisely because their profiles are identical.

**Conclusion: the per-floorplan defaults are not in the MCF's data tables.** This
independently confirms the lead Task 8 recorded. `find_option_defaults` stays
unimplemented, but is now **verifiable**: the table above gives it a real oracle, which is
exactly what it lacked.

*Note: the "GuiCode bytecode" this section named as the remaining candidate is **not
bytecode** — GuiCode is native ARM Thumb-2 (`docs/GUICODE.md`). It has since been
disassembled and swept, and is eliminated; see PC.12a below and `GUICODE.md` Finding 6.*

## PC.12a — the selection burst is atomic, so the defaults are in the panel

**Source:** re-analysis of `captures/pc5-floorplans-2026-08-12.log`. No new capture.

**What was already known, stated first so this section does not appear to claim it:** PC.2
recorded the write frame layout `FF 96 <sel> 0F <val> 00 D1 EA`, and that `0x1FED9` group
`0x96` carries G12 configuration writes. PC.12 already derived the defaults table from this
capture's writes. None of that is new here, and the derivation below is a **reproduction**,
not a third source.

Three things are new.

**1. The address roles, pinned exhaustively.** Across the whole capture, `0x15FCE` has
exactly one transmitter and `0x1FED9` exactly one, and they are different nodes:

| Address | Role | Evidence |
|---|---|---|
| `0x9C` | **G12** | the *only* source of `0x15FCE` (3,397 frames), periodic ~5 s; also sources `19FECA9C` DM1 |
| `0x9F` | **Spyder panel** | the *only* source of `0x1FED9`; also sources `19FECA9F` DM1 |

So the status/write split is a fact about the bus, not a reading convention. This matters
because "the panel writes the defaults" only follows from `0x1FED9` traffic if the panel is
the sole writer and the G12 does not also emit it.

**2. `0xD8` is never written by the transaction.** The burst carries **twelve** selectors:
the floorplan `0xF5`, nine of the ten options PC.12 tabulates, and `0x15` and `0xF7`, which
are not options. `0xD8` is **absent** — its value in PC.12's table came from status, not
from a write. Anyone comparing the table against the writes must exclude it or appear to
find a mismatch.

**3. The burst is atomic — the load-bearing observation for the firmware work.** Each
selection completes in **~50 ms with no `0x9C` frame interleaved at all**. The panel issues
all twelve writes without querying anyone. **So the values are already in the panel when
the burst begins: they are in its flash.** This refutes the otherwise reasonable hypothesis
that the defaults live in the G12 and the panel merely echoes them — which matters, because
that hypothesis would have made the search in `docs/GUICODE.md` Finding 6 pointless rather
than merely unfinished.

All three, plus the ninety values, are now mechanised in `tests/test_capture_defaults.py`.
PC.12's reading was done by hand, so nothing would previously have caught a transcription
slip.

---

## PC.13–PC.15 — the remap, verified on the bus (2026-08-14)

**Logs:** `captures/pc13-remap-binding-2026-08-14.log`,
`captures/pc14-remap-status-2026-08-14.log`,
`captures/pc15-ramp-and-remap-2026-08-14.log`
**Method:** `candump -L can_rvc` on the coach bridge, listen-only. Nothing was transmitted
by the capture host. The owner flashed successive patched GuiData sections from USB and
operated the touchscreen.
**Coach:** Jayco B-Van, floorplan WD, firmware 13.6 with the `Bath Fan` → `Starlink`
patch.

### Result 1 — the panel does not cache bindings

A same-size edit to **two bytes** of GuiData (binding entries 166/167, byte 7, file
`0x02A37F` and `0x02A38B`) changed what the panel transmits:

```
19FEDB9F#26FFC805FF00FFFF     panel (0x9F) -> instance 0x26 = 38
19FEDA9C#26FFC8FCFF050400     G12  (0x9C) reports 38 at 0xC8
19FEDA9C#26FF00FCFF054000     G12 reports 38 at 0x00
```

Instance 40 appears **nowhere** in the toggle windows of any of the three captures. This
closes the patcher spec's open question 1 for the transmit path: the panel builds each
frame from the binding table it reads at boot, so `binding +7 is the transmitted instance`
is now `observed-on-hardware`, not inferred.

### Result 2 — the panel sends *toggle*; the G12 holds the state

Every command frame in PC.13–PC.15 for this control is byte-identical:
`26 FF C8 05 FF 00 FF FF`, i.e. command **5** with level `0xC8`. The panel never sends
command 3 (off). The alternation is entirely in the G12's status frames (`0xC8`, then
`0x00`).

So the on/off *pair* of byte-2 variants in a group is not the panel choosing a direction.
That is why `set_channel` must rewrite every variant: a partial remap leaves a toggle
pointing at the old pin.

### Result 3 — command and status are separate tables

Remapping only the binding produced a button that **switched the new channel while its own
indicator stayed dark**. The indicator reads a `DC_DIMMER_STATUS_3` entry in the variable
table (`+0x2A70`, instance at byte 4), which still named the old channel.

Two variables watched instance 40 — index 82 and index 184. Moving both (v9) lit the
button. Moving only 184 (v10) did nothing, so **184 is not the control's watcher** and an
edit to it should not ship. Index 82 is a member of the per-channel display list (indices
66..86 → instances 24..44, one per row), which the `G12 OUTPUTS` diagnostics screen draws.

**A control's indicator and the diagnostics row read the same variable.** Confirmed both
ways on the coach: with 82 moved, the button tracks channel 38 *and* the diagnostics row
for channel 40 reports 38's state; with 82 left alone, the row is honest and the button
never lights. No pointer or index linking a control to its variable has been located — 82
has four pointers, all in the diagnostics-screen region, and 80 and 184 have none — so the
button cannot be repointed at a different variable. `set_channel` exposes this as
`move_display_list` rather than choosing.

> **Superseded in part, 2026-09-10.** "No pointer or index linking a control to its variable"
> stands, but the control was never linked to a *variable*: its records name the **cells** the
> variable's GuiCode bridge block writes (`MCF-FORMAT.md` §5.4c, proven 2026-08-17), and those
> names can be swapped. `set_channel`'s default now does that and moves no variable, so the
> button follows the remap and every diagnostics row stays honest (hardware-unverified).
> `move_display_list` keeps v9. The v10 shape, moving 184 alone, had been the default and is
> no longer offered.

### Result 4 — press-and-hold is command 21, and the G12 interpolates

PC.15 caught a dim of channel 32 (`Main Ceiling Lts`):

```
19FEDB9F#20FFFA05FF00FFFF     cmd 5, toggle on
19FEDB9F#20FFC8150000FFFF     cmd 0x15 = 21, repeated ~1/s while the button is held
19FEDB9F#20FFFF04FF00FFFF     cmd 4 = stop, on release
```

Between them the G12 reports the level walking `C8 BE B4 AC A0 96 8C 82 78 6E 64 5A 50 46
3C 32 28 1E 14 0E`, and a later hold walks it back up. **The panel does not compute
brightness** — it asks for a ramp once per second and stops on release; the controller
interpolates. Direction alternates between holds.

> **Corrected 2026-10-21 (`observed-on-hardware`, live capture, owner ramping output 21 from the
> Firefly app, which repeats command 21 every 0.5 s):** direction does not alternate. **Every hold
> starts downward**, turns straight back up at the bottom (level ~4, below the 10 this capture's
> statuses show — statuses go out only at each 10, so the dip below 10 and back reads as
> "10, pause, 10"), and stays at 200 while held there (0.74 s observed). The "later hold walks it
> back up" here started at 14, dipped to the bottom and climbed. All six holds on record fit.
> Ramp curve: 200 → 100 in 1.10 s, → 50 at 2.35 s, → 10 at 3.69 s, → 4 at 4.02 s, symmetric up.

This is the first sighting of command 21 on the wire. It is present in the binding table
for every dimmable channel but absent from PC.8, because nobody held a button during that
capture — the stored command set is a *superset* of what any given session transmits.

### Result 5 — the G12 acts on any instance

Reported by the owner from their `rvc2mqtt` bridge, 2026-08-14: a correctly formed
`DC_DIMMER_COMMAND_2` turns on **any** output, including channels the pin sheet does not
map. The controller does not validate an instance against its configuration. Floorplan and
option state govern what the *panel* offers, not what the G12 will act on.

Consequence for `guidata.dimmable_channels`: the file's silence about an uncommanded
channel describes what the panel sends, never what the hardware can do.

---

## PC.16 — masters, the hold gesture, and the option write (2026-08-14)

**Log:** `captures/pc16-masters-and-options-2026-08-14.log`
**Method:** listen-only. The owner operated the interior light master, press-and-held the
remapped `Starlink` (latch) button, and toggled the `Starlink` option off and on.

### The light master carries memory, and the levels say how

```
+52.87  19FEDB9F#{16,18,19,1A,20,22,23} FF FB 00 ...   cmd 0, level 0xFB   master ON
+69.47  19FEDB9F#{16,18,19,1A,20,22,23} FF 00 06 ...   cmd 6, level 0x00   master OFF
```

Seven instances — **22, 24, 25, 26, 32, 34, 35** — exactly `answer_key.INTERIOR_MASTER`,
channel 35 included, and the G12 confirms 35 following.

The owner reports the master restores whatever was on before: turn the master on, switch
two lights off by hand, master off, master on, and only the lights left on come back. The
command/level pairs decode that behaviour:

| cmd | level | meaning |
|---|---|---|
| 0 | `0xFB` | master ON — restore remembered levels |
| 6 | `0x00` | master OFF — *memory off*: store, then off |
| 5 | `0xFA` | toggle to remembered level (a dimmable load) |
| 5 | `0xC8` | toggle to full (a latch load) |
| 21 | `0xC8` | ramp while held |
| 4 | `0xFF` | stop on release |

PC.8 contains 20 master-ON and 20 master-OFF frames, an exact pairing. **Levels above
`0xC8` are "use the remembered level" sentinels** — which settles the `0xFA` that
`guidata.ControlGroup` recorded as an unknown value outside the brightness range.

### A latch button sends no ramp when held

Press-and-hold on the remapped `Starlink` button produced `cmd 5` toggles and **no command
21**. The ramp is a property of the widget, not something the panel emits blindly.

### Command 21 is "act while held", not "dimmable"

File-wide, commands 0 and 6 mark the same nine channels, but 21 also covers 2, 10, 27, 36
and 86, which carry `{4, 5, 21}` — ramp, stop, toggle, no set-brightness. Those are
hold-to-*move* controls, not dimmers. **Command 0 is what marks a light.**

### The option write survives a rename and a remap

```
+122.78  19FED99F#FF96E90F 00 00 D1 EA     selector 0xE9 = 0
+128.26  19FED99F#FF96E90F 01 00 D1 EA     selector 0xE9 = 1
```

Group `0x96`, selector **`E9`** — `BUS-BASELINE`'s `bath fan` option — off then on.
Renaming every label and moving the channel left the option binding untouched. (Config
writes are DGN `1FED9`; the `195FCE` frames here are the G12's periodic broadcast at
startup, not writes.)

### Not settled: the exterior master

The exterior master press produced **no bus traffic at all** — no command to instances 21
or 23, no status for them. So whether channel 35 answers to *both* masters, as the pin
sheet's `EM/LM` note implies, is still open. `MCF-FORMAT.md` §8.2's question stands.

### A third transmitter, explained

```
+53.15  19FEDB82#2CFFFA05FF00FFFF     source 0x82 -> instance 44 (Water Pump)
```

Source `0x82` is the **owner's own automation** (`rvc2mqtt`), which turns the water pump on
when the bath light comes on. Recorded because the project had documented only `0x9F`
(panel) and `0x9C` (G12) as transmitters, and because it bounds invariant I7 in
`patch.set_channel`: that check proves no *binding in the file* commands a destination
channel, and cannot speak for another node on the bus.

## PC.17 — the "phantom" channel 40 is a display artifact, not a driven pin (2026-08-14)

**Logs:** `captures/pc13-remap-binding-*`, `pc14-remap-status-*`, `pc15-*`, `pc16-*` (post-remap);
`captures/pc12-defaults-*`, `pc5-floorplans-*` (pre-remap, 40 genuinely mapped).
**Question:** after remapping the `Starlink` button from channel 40 to 38, the G12 OUTPUTS
diagnostics row **40** still lit alongside 38. Was pin 40 actually energizing, or was the row
lying? The panel is only a bus participant — it commands and it reads status — so the bus is
the oracle. No multimeter required.

Filter every capture for `DC_DIMMER_COMMAND_2` (DGN `1FEDB`) and `DC_DIMMER_STATUS_3`
(`1FEDA`) with data byte 0 = instance `0x28` (40):

| capture | 40 mapped? | panel CMD2 inst 40 (`0x9F`) | G12 STAT3 inst 40 (`0x9C`) |
|---|---|---|---|
| `pc12`, `pc5` | yes (pre-remap) | ✅ present | ✅ present |
| `pc13`, `pc14`, `pc15`, `pc16` | no (remapped to 38) | ❌ **zero frames** | ❌ **zero frames** |

Post-remap, instance 38 flows normally (panel sends `cmd 5` toggles from `0x9F`, G12 answers
STAT3 from `0x9C`) while instance 40 is **completely silent** — not one frame, command or
status, across all four captures.

**The G12 broadcasts STAT3 for every output it drives** — `pc12`/`pc5` prove it does exactly
that for 40 when 40 is real. Its silence on 40 after the remap therefore proves **pin 40 is
not energized**. The lit diagnostics row 40 is a **panel-internal display artifact**: the
positional status-variable reuse (`move_display_list`, PC.13–15 / spec §12), the panel painting
row 40 from the `u16` status slot that now tracks 38. The bus carries no instance-40 traffic to
support it. `proven-from-bus` — this closes spec §9 item 8 and open thread 3.

---

# PC.18 — the new-HOME-button builds, on-screen (2026-08-17)

**Method:** USB flash + power cycle, one variable per build, observed **on the panel's
screen** and by the G12's response to a tap. Unlike PC.1–PC.17 these are not CAN captures —
they are the display-side oracle, the same class of evidence as PC.9's visibility result
("emits no CAN frames and was only observable on the display"). Recorded here because this
document is the project's record of what the *physical coach* did, and three of the four
results below could not have been reached any other way.

Each build cloned donor object 279 onto HOME, gave it its own command group, and pointed it
at G12 **pin 15** (uncommanded on this coach, floorplan WD).

| build | one variable changed | result on the coach |
|---|---|---|
| v6 | clone of obj **295** (Water Pump) | button appears; repaints **only on page re-entry** |
| v7 | clone of obj **279**, `value_slot` = 32 | box repaints live; **caption disappears** until a full page render |
| v8 | `fillA` index = box `value_slot` = caption `value_slot` = **145** | **works** — toggles pin 15, tracks its state, repaints in place |
| — | first *inserted* (relocating) build, before the GuiCode-pointer repair | **boot loop**; recovered by reflashing the stock GuiData+GuiCode pair |

**Result 1 — the live-redraw invariant is real, and it is three fields, not one.** v7 differs
from v8 in nothing but the two `value_slot` u16s. Splitting them (box painting from cell 145
while the slots named 32) gave a button that repainted its *box* live and lost its *caption* —
the panel repaints a box in place on a status change but re-renders text only on a full page
pass. This is the evidence behind `MCF-FORMAT.md` §6.0a; it is `inferred-from-data` rather than
`proven-from-code` because the GuiCode redraw path has still not been located.

**Result 2 — a donor must already carry `value_slot` on both its box and its caption.** Field
presence is header-gated, so a same-size edit cannot add one. Water Pump (295) carries neither
and inherits from its wt-5 parent — which a generated attach record does not supply. That is
the v6 result, and it is why donor selection is a documented step rather than an arbitrary one.

**Result 3 — a single command-5 binding is a plain toggle.** A two-binding `[toggle, off]`
group is a *smart toggle*: the panel picks off-vs-toggle from the commanded instance's tracked
state, and an instance with no status defaults to "believed on" and only ever sends OFF.
Binding *order* does not decide this — Water Pump is `[5,3]` and Tank Heater `[3,5]`, and both
work.

**Result 4 — an insertion that shifts GuiData must repair GuiCode too.** GuiCode holds twelve
absolute `ldr`-literal pointers into GuiData. The first inserted build shifted the two high
object tables and left those literals stale; the panel dereferenced shifted content at boot and
watchdog-reset in a loop. **Reflashing the stock pair recovered it fully** — which is the
practical confirmation that a bad GuiData is not a brick, provided both sections are reloaded
together. `relocate.py` now repairs the literals and has a gate that refuses a stale one.

## PC.18a — off-state colour and a grown label, confirmed (2026-08-18)

Two builds that had been outstanding at PC.18 were flashed and both work. Reported by the
owner; on-screen observation, as PC.18.

**Result 5 — the off-state colour is editable, and it lives in GuiCode.** Build **v9** = v8
plus a **two-byte** edit at GuiCode `0x002488EA`: the `ldr` that loads pin-15's *default (off)*
colour master had its `imm8` displacement repointed from the pool word holding `0x20039974`
`(39,40,98)` — the quadrant panel colour, which made the button invisible when off — to the one
holding `0x20039A58` `(65,81,163)`, the button blue every other HOME button rests at. The button
is now visible in both states.

Three things this confirms, all of which the byte-level argument predicted:

* A wtype-33 box has **no off-colour field** — all 835 of them in 13.6 carry `fillA2` mode 0.
  The off colour is the *resting value* of the mode-7 cell the box paints from, written by
  GuiCode's per-var dispatch. Editing GuiData could never have fixed this.
* **Off colours are per-var, not per-button.** The edit changes cell 145's resting default and
  nothing else: the **colour masters themselves are untouched**, so every other cell fed by
  either master keeps its colour. `theme.resting_defaults` reported exactly one changed cell
  before the build shipped, and the panel agrees.
* The **GuiCode** section must be loaded for this class of edit — it changes no GuiData byte.

**Result 6 — a stored label can be made LONGER than the field it shipped in.** The `G12 OUTPUTS`
static row `'15 - '` was grown to `'15 - Pin 15  '` — **+8 bytes**, chosen to keep the object
table's 4-byte alignment — through the relocator, and the panel draws it. This is the first
hardware confirmation of a **length-changing** GuiData edit, and it retires the standing caveat
that a rename can never add a character (`MCF-FORMAT.md` §6: *"a same-size rename **cannot add a
character**"* — still true of `patch.set_label`, no longer true of the tooling as a whole).

The mechanism detail worth carrying: `relocate.relocate()` **alone cannot do this**. It refused
the edit, correctly — with 8 NULs spliced in and the length byte still 5, the record genuinely
does not decode, and the boundary oracle said so. A length-prefixed field must have its payload
and its length byte land in the *same* operation: build the grown image, apply both edits, then
`relocate._apply_downstream_shift`, reseal, and run the gates.

**Scope note.** Row 15 is a **static** row — its whole label is stored in GuiData. The 14
*dynamic* rows (05, 06, 07, 08, 10, 18, 19, 20, 24, 25, 26, 34, 35, 40) draw their names from
GuiCode's name table, selected by the G12's config broadcast, and are not editable this way
(§6.1; and see `GUICODE.md` Finding 35 for the whitelist defect that leaves two of the four tank
heaters blank on this coach).

## PC.18b — the widened tank-heater whitelists, confirmed (2026-08-18)

**Build:** `mcf-tankheat-v10.bin` — 9 same-size GuiCode edits (26 bytes), GuiData byte-identical
to stock. Flashed by the owner; on-screen observation of the `G12 OUTPUTS` diagnostics page.

**Result 7 — all four tank-heater rows populate on WD.** Rows 05/06/07/08 all carry their names,
against 2/4 on stock (`GUICODE.md` Finding 35). This confirms the whole gate model: the four
whitelists, the floorplan register `0x2003A77A` driving them, and the emulated prediction that the
widened ranges would cover WD.

**Result 8 — row 07 tracks the Black Tank option, live.** `Tank Heat Black` is **visible when the
Black Tank option is selected and gone when it is deselected**. That is a direct hardware
confirmation of the `[0x2003A4B4] == 1` test at GuiCode `0x249528` — the firmware's own second gate
on that row, which the patch deliberately left intact. It also confirms `0x2003A4B4` is the Black
Tank option register (previously `inferred` from `options.variable_register`), and that the option
register is re-read on each page render rather than latched at boot.

**Why this validates the method, not just the patch.** The prediction came from
`src/fireflymcf/tankgates.py`, an emulator of the real instruction block — and three earlier
hand-written models of the same block produced confident, plausible, **wrong** whitelists. The
emulator was trusted only after it reproduced two independent oracles on the *stock* image (this
coach's WD = rows 05+08 only, and Finding 35's "best 3/4, none 4/4"). The coach then matched its
prediction for the patched image on the first flash.

**Result 9 — a build carries whatever its BASE carried; sections are a matched pair.** v10 was
built from **stock**, so its GuiData (888,344 B) has no Pin-15 button, and flashing it removed the
button added by v8/v9 (GuiData 888,440 B — the +96-byte inserted object). Nothing failed: the panel
booted normally, because a consistent stock-derived **pair** was loaded.

**Corrected 2026-08-18, same day:** this paragraph originally warned that loading v10's GuiCode
onto v9's GuiData would be the PC.18 Result 4 stale-pointer condition, and advised flashing both
sections together. The owner reports the menu's four entries are really **three loadable units** —
`LCD Firmware` alone, `G12` alone, and **`GuiCode`+`GuiData` as an inseparable pair**, which the
panel will not split. So that mix cannot be produced by hand, the warning is
moot, and the enforced pairing is protective: a self-consistent build cannot be split across a
reflash. (PC.18's boot loop came from a build whose *own* GuiCode literals were stale for its *own*
grown GuiData — an internally inconsistent file, not a mismatched flash.)

What survives is the *costly* half, and it is the one that bit here: since every build writes both
sections, **a build derived from stock silently replaces a customised GuiData**. "GuiCode-only"
described v10's edits, never what got written. So: **derive every new build from the image you are
actually running**, and state what a build drops when handing it over.
`mcf-pin15-tankheat-v11.bin` is v9 + the same 9 edits, which is how to have both.

---

# PC.19 — the loader's own log file, off the USB stick (2026-08-20)

**Method:** an empty `LogFile.txt` placed in `0:/SpyderConfig` on the update stick before a
normal USB-stick-plus-power-cycle flash. The Spyder Config Loader opens that path `FA_WRITE`
(existing-file-only, `cli.bin` `0x200160EC`) and, on success, enables ~60 diagnostic strings.
The panel wrote **3264 bytes**. Like PC.18 this is not a CAN capture — it is the *loader's*
account of what it saw and did, which is a class of evidence this project had never had.

The mechanism was derived from code first and predicted before the file existed
(`MCF-FORMAT.md` §4.2); this is its hardware confirmation. The file carries a FAT date of
`1979-12-31`, i.e. the panel has no RTC.

**The session, per the owner:** `mcf-13.6-Special_Edition.bin` was selected (byte-identical to
`build/mcf-from-stock.bin` — reference build v1), then the GuiCode/GuiData pair was
loaded, then G12. The stick's `cli.bin` and `mcf (original).bin` are byte-identical to
`firefly-13.6/`. **So reference build v1 is flashed as of this date** — the owner's
on-screen confirmation of what that build actually does (`MY COACH`, `Overflow Valve` on pin
28, pin 15 inert) is recorded in `CLAUDE.md`'s build entry; this section is the *loader's* account
of the same flash.

## Result 1 — one touch loads two sections: the GuiCode+GuiData pairing, from the loader

```
Touch Area 1 release
Start LCD Config Load / Upload Complete / Config Load Success
Start LCD Config Load / Upload Complete / Config Load Success
Touch Area 3 release
Start Network Config Load / Upload Complete / Config Load Success
```

Row 1 (GuiCode) produced **two** load cycles; row 3 (G12) produced one; row 0
(`LCD Firmware`) was never touched. §4.1's "four menu entries, three loadable units" was
`observed-on-hardware` by owner report; it is now also the loader's own record, and it shows
the pairing is a property of selecting *either* row of the pair.

## Result 2 — the panel's identity, and what it is running

| parameter | panel reports | where it comes from |
|---|---|---|
| `ExtFuncCode` | `0x02004665` | LCD Firmware payload `+0x410` |
| `ExtFwVer` | `0x106` (v1.06) | LCD Firmware payload `+0x40A` |
| `FwRlsLetter` | `0x72` = `'r'` | LCD Firmware payload `+0x40C` |
| `GuiRev` | `6` | installed GuiData byte `+0x06` |

The panel is already running the `LCD Firmware` that ships in this MCF (payload `+0x40C` is
`'r'`, and the panel says `'r'`), so there was no LCD update to apply. The descriptor's
`FwRlsLetter 0x70` is a **floor**, not the payload's version — see §2.

## Result 3 — the G12's flash map and MCU, over the bus

`Src Addr 9C, Num Gen Upld Status Rxd = 4` — the G12 advertises four upload targets:

| DeviceInst | UploadType | LdAddr | notes |
|---|---|---|---|
| 0 | 1 (firmware) | `0x08006000` | `ExtFwVer 0x300`, `FwRlsLetter 'd'`, `BtldrVer 0x203` |
| 0 | 2 (config) | `0x08038000` | `FuncBank 0x1F`, `FuncInst 0x16`, `ConfigRev 6`, `ConfigType 0x1234`, `ConfigID 0x16` |
| 0 | `0xFF` | — | MCU/bootloader group only |
| 1 | 1 | `0x08000000` | `ExtFwVer 0x300`, `FwRlsLetter 'b'` — the bootloader slot |

All groups report `McuDevID = 0x04351001`, `McuFlashSize = 0x100` (256 KB),
`McuRamSize = 0x40` (64 KB). Read as `DEV_ID:REV_ID`, `0x435` is an **STM32L43x/L44x** at rev
`0x1001`, and 256 KB/64 KB matches that part. **This answers `MCF-FORMAT.md` §10 item 4**: the
`G12` descriptor carries no tag `0x40` because the *device* supplies the destination. The MCF's
G12 section is `0x25A8` bytes → `0x08038000..0x0803A5A8`.

The G12's reported `ConfigRev 6` / `ConfigType 0x1234` also match the `G12` section header's
byte `+0x06` and u16 `+0x04` — the same header layout the panel reads for GuiData (§4).

## Result 4 — the target matcher, reproduced from the bytes

The `Start matching targets` block prints one line per compared parameter. Decoding the
descriptors' tagged entries (§2) reproduces **every line and every verdict**:

| desc | validate-phase entries | log verdict |
|---|---|---|
| 0 `LCD Firmware` | ExtFwVer `==`: `0x106` vs `0x106` ✓; FwRlsLetter `>`: panel `'r'` > file `'p'` ✓ | `Validated Target` |
| 1 `GuiCode` | ExtFwVer ✓; GuiRev `==`: file `0` vs panel `6` ✗ | `Not Validated` |
| 2 `GuiData` | same | `Not Validated` |
| 3 `G12` | none (its other two entries are data) | `Not Validated` |

That is what closed §2's tagged-entry decode: the log named the tags, and the named values
could then be matched against the descriptor bytes and the matcher's code.

**Operational consequence.** A stock-derived 13.6 MCF can **never** auto-validate its
GuiCode/GuiData rows on a panel that has ever had a GUI loaded: the descriptors require
`GuiRev == 0` and the panel reports `6`. MANUAL LOAD is therefore mandatory for every build
this project produces — which is what the owner has always done. Making AUTO LOAD accept a
build would be a **2-byte same-size descriptor edit** (tag `0x05`'s value `0 → 6`, or its
operator `0 → 3`) plus a descriptor reseal. **Not built, not flashed** — and it rests on
"Validated" being what gates the auto path, which has not been established.

## What the log does not say

It never records **which** MCF was selected, and stock 13.6 and reference build v1 carry
byte-identical descriptors (their `LCD Firmware` and `G12` payloads are byte-identical too;
only GuiData differs, `0xD8E18` → `0xD9944`). The identification above is the owner's, not the
log's. Touch events are logged only on the MANUAL LOAD screen, so the file-selection tap does
not appear.

# PC.20 — Driving the PD Inverter page from synthetic frames (2026-08-23)

**The first entry in this document where frames were TRANSMITTED.** Everything before PC.19 is
listen-only. Here the bus host (`can_rvc` = `can1`) injected status frames to find out
which of the panel's dormant inverter/charger subscriptions actually paint something, ahead of the
owner adding a bridge that publishes the coach's inverter/charger onto RV-C.

Safety rules held throughout, and they are worth reusing: **status DGNs only, never a command**;
source addresses `0xC0` then `0x42`, both verified unclaimed against a live capture first;
`1FF96` excluded because it is the one probe DGN a **G12 rule** keys on (table `0x61` idx 6) and
that table's semantics are undecoded; `1FECA` DM_RV excluded because vars 162-164/235 are
instance-agnostic and the alternator drives them — a synthetic fault would have landed on the
ALTERNATOR SYSTEM page. *(That rationale was wrong, although the caution cost nothing:
`GUICODE.md` Finding 77, 2026-09-25, proves from the code that nothing reads those variables,
and that the alternator page takes DM_RV from the raw frame, from address `0x80` only. This was
a rationale, not an observation. The observations in this section stand.)*

## Result 1 — six fields of the PD Inverter page, driven from the bytes

Owner-photographed, page header `PD Inverter` (the page is gated on the Progressive Inverter
option). All six are `observed-on-hardware`:

| field | DGN | byte 0 | value | scaling |
|---|---|---|---|---|
| Inverter **Enabled** + green dot | `1FFD4` INVERTER_STATUS | inst 1 | byte 1 = `01` | enum "invert" |
| DC Voltage | `1FEE8` INVERTER_DC_STATUS | inst 1 | bytes 1-2 | ×0.05 |
| DC Current | `1FEE8` | inst 1 | bytes 3-4 | ×0.05 − 1600, **then negated** |
| AC Out Volts | `1FFD7` INVERTER_AC_STATUS_1 | **`0x41`** = inst 1, line 1, *output* | bytes 1-2 | ×0.05 |
| AC Out Freq. | `1FFD7` | **`0x41`** | bytes 5-6 | ÷128 |
| AC Input Volts | `1FFD7` | **`0x01`** = inst 1, line 1, *input* | bytes 1-2 | ×0.05 |

Scalings are rvc2mqtt's (`rvc.py`), used as a prediction and confirmed by the screen.

## Result 2 — the panel NEGATES DC current

Raw 32500 = `+25.0 A` by `(x × 0.05) − 1600` drew **−25 A**; raw 31500 = `−25.0 A` drew **+25 A**.
So the panel computes `1600 − x × 0.05`. A bridge must invert DC current or the screen reports
charging while the inverter discharges. Two runs, opposite signs — a real round-trip, not one
reading.

## Result 3 — the page is fed by a dedicated parser, NOT the variable table

This overturns the working model and is the most transferable finding here. Vars 103/104 (`1FFD7`)
carry the `0xFF` sentinel, so through the filter table **one frame writes both slots regardless of
its input/output bits**. Yet the panel shows 120 V *out* and 111 V *in* simultaneously, from the
same DGN, distinguished only by byte 0's line/input-output sub-field. The filter table cannot
express that.

Consistent with it: the page is object **1419** (near-twin 1418), whose value widgets name
cell-table indices **658-664** (`0x20039eda`, `0x2003a310`, `0x20039dd6`, `0x20039e5a`,
`0x2003a424`, `0x20039c18`, `0x2003a334`) — and **none of those seven addresses appears as a
literal anywhere in GuiCode or the LCD Firmware**, searched at every byte offset. The block at
GuiCode `0x242560` that looked like the bridge is the **config-payload copier** (base
`0x20030068`, feeding cells 505-560), not this. So a received value reaches a `wt-8` value widget
by a path this project has **not located**; the LCD tail-calls GuiCode's vector entry 5 with every
frame (`MCF-FORMAT.md` §5.4a), which is where such a parser would live.

## Result 4 — the panel actively polls, and expects a device at 0x42

On the PD Inverter page the panel (`0x9F`) transmits:

```
18EAFF9F#EBFE00FFFFFFFFFF   Request PGN 0FEEB PRODUCT_IDENTIFICATION, broadcast
18EAFF9F#D8FE010000000000   Request PGN 1FED8 GENERIC_CONFIGURATION_STATUS, broadcast
18EA429F#EBFE000000000000   Request 0FEEB addressed to 0x42   <- only after we sat at 0x42
18EAC09F#EBFE000000000000   Request 0FEEB addressed to 0xC0   <- it noticed the probe appear
19FECA9F#0584FFFFFFFFFFFF   its own DM_RV instance 5, ~1 Hz
```

It does **device discovery**: an unknown source address appearing on the bus is asked to identify
itself within seconds. `0x42` is where its own `0EA42`/`0EF42` bindings expect the PD inverter,
so that is the address a bridge should claim. The `0EF42` proprietary-A binding never fired in
any capture.

## Result 5 — the PRODUCT_IDENTIFICATION format, from the three real responders

Reassembled from the archived captures' BAM sessions (`18ECFFxx` + `18EBFFxx`):

```
SA 80  'Wakespeed Offshore*WS500*926993657**'          36 B, 6 packets
SA 9C  'Spyder Controls*G12 Panel 2v2 3v0d*0000000*22*' 46 B, 7 packets
SA FD  'Firefly*Standalone Module*0001**'              32 B, 5 packets
```

`Make*Model*Serial*Unit*`, **terminated with `*`**. The first answer sent from `0x42` omitted the
terminator and was ignored; the corrected one was also ignored, so the terminator was necessary
but not sufficient. *Method note:* comparing against what real devices answer (owner's suggestion)
found a defect in our own frame that no amount of re-reading the spec would have.

Exactly one device answers the `1FED8` request — the G12:

```
19FED89C#25 B1 1F 30 06 16 34 12
         ^^ mfr LSB   ^^ firmware revision 0x30
            ^^ B1 = mfr MS bits 1, function instance 22
               ^^ function 0x1F
```

Byte 1 = `0xB1` is exactly what **var 171** compares against, so var 171 is the G12's own entry and
var 224's `0xB9` (function instance 23) is the slot left for the other expected device.

## Result 6 — `Firmware Rev.`, decoded from GuiCode and CONFIRMED (2026-08-23)

**The panel drew `1.23` from a string engineered to satisfy the instruction.** Found with Ghidra
headless (`guicode136`) after linear disassembly had failed repeatedly; the full decode is
`GUICODE.md` **Finding 48**.

Two things had to be right, and one of them is a trap this document should carry:

- **There are two twin inverter pages, 1418 and 1419, with identical labels.** 1418's eight value
  cells each have a GuiCode literal; 1419's have none. The coach draws **1418**. Result 3 above
  reasoned about 1419 and concluded "these cells have no writer" — the scan was sound, the target
  was wrong. That claim stands corrected here rather than being edited away, because the *shape*
  of the mistake is the lesson: a negative about a cell is only meaningful against a control, and
  the control is that known-good cells (AES setpoints, slots 272/274) do carry literals.
- **`Firmware Rev.` is a byte-position hack.** `FUN_0024F1FC`'s `0EBFF` arm requires TP.DT
  **packet 5** whose first two payload bytes are `05 'v'`, then reads digits at fixed offsets,
  skipping position 3 (the `.`). Packet 5 carries string bytes 28..34, so the panel wants `vN.MM`
  at **index 28 of the PRODUCT_IDENTIFICATION string** — not in a named field.

```
'Progressive Dynamics*PD-INV v1.23*0001*1*'    41 B, 6 packets
                            ^^^^^ index 28
18EBFF42#0576312E32332A30                      -> screen reads 1.23
```

Also settled: this parser **binds by source address**, unlike the receive-filter table which never
reads it. A free slot in its two-entry device table is adopted by a `1FFD4` INVERTER_STATUS frame
whose `payload[0]` is the slot index (1 or 2); the sender's SA is then stored and all its later
frames are accepted. So "claim `0x42`" was right, but for a reason none of the earlier rounds had
established.

Seven of eight fields on the page are now driven. Only `Inverter Fault` remains.

## Result 7 — `Inverter Fault`, and all eight fields driven (2026-08-23)

`19FECA42#0142010100FFFFFF` drew **`E2 - Over Voltage`**. DM_RV with `payload[1] = 'B'`,
`payload[3] =` the slot index, `payload[2] = 1`, `payload[4] = 0` → fault code 2. Decode and the
14-candidate label table are in `GUICODE.md` Finding 48's addendum.

**The concern that held this back all session was wrong, and the owner caught it.** Asked why a
DM_RV injection would disturb the ALTERNATOR SYSTEM page when DM_RV is broadcast by everything,
the answer turned out to be that it would not: that page is driven by `FUN_0024FCE8`, gated on
`(char)id == 0x80` — the Wakespeed regulator's source address, hardcoded. Three different
consumers of `1FECA` exist with three different keying rules (filter table: DGN only; PD parser:
SA after adoption; alternator parser: SA hardcoded), and the earlier caution generalised the first
one's SA-blindness to a page fed by the third. Verified after the fact: the alternator page was
unaffected throughout.

## Open — nothing on this page

All eight fields are driven. Object **922** (`Inverter/Charger Status`) was explored next and is
decoded in `GUICODE.md` **Finding 49**: its six numeric fields come straight off the
**receive-filter table** (vars 103/104 `1FFD7`, 105/106 `1FFFD` instance 1, 116/117 **`1FFCA`
CHARGER_AC_STATUS_1**), so they need no special protocol at all — but its four identity fields
(`Faults:`, `I/C Firmware Rev.`, `I/C Model`, `Bridge Firmware`) have **no writer** in 13.6 or
14.7 and are authored-but-dead.

Untested on hardware: that page was never viewed during these runs, and `1FFCA` at 111.0 V *was*
being transmitted in rounds 1-3 — so its AC Input Volts had probably been populated the whole time
while we were looking at object 1418. Worth one look if the page is reachable on this coach.

*Refuted along the way, and none of it was wasted:* `1FED8` on a guessed template; `1FED8` swept
over all 64 function codes with byte 1 = `0xB9` on the G12's own frame template; proprietary
`0EF00`. `1FED8` turned out to be the wrong DGN entirely — its vars 171/224 are the **G12's** own
config entry (`19FED89C#25B11F3006163412`, byte 1 = `0xB1`) and a second reserved slot, not this
page.

Object **922** — a *different* page, `Inverter/Charger Status`, carrying the labels
`I/C Firmware Rev.`, `I/C Model` and **`Bridge Firmware`** — is where a bridge device is probably
meant to surface. Not investigated.

---

# PC.21 — the option interlock is the panel's alone, by injection (2026-08-23)

**Captured:** 2026-08-23, live on the coach, interface **`can1`**
(altname `can_rvc`; note `can_bms` is `can0`, the 11-bit BMS bus — the RV-C bus is `can1`).
**Method:** owner-authorized **transmit**. This is the entry that closes `GUICODE.md` Finding 50.

Finding 50 proved from GuiCode that the Progressive-Inverter row's tap handler emits `E6 := 1`
**and** `EF := 1` (AGS), and showed the G12 had never been *seen* to originate an option-selection
change. What no capture could settle is the converse: every `E6 := 1` in the corpus arrives with
the panel's own `EF := 1` beside it, so nothing ruled out the G12 forcing `EF` on its own. No
screen tap can test that — a tap routes through the panel, which supplies the coupling itself.

## Method

Owner set the screen to **AGS, PI and all AES modes off**; the G12's `0x15FCE` sweep agreed —
`E6 = 0`, `EF = 0`, `D7 = 0`, `0x15 = 0x5A01`, floorplan `F5 = 8` (WD). Then **one** frame, from
source address `0xC0` (verified unclaimed against a live SA census — `9C` G12, `9F` panel, `80`,
`70`, `65`, `FD`, and now **`42`**, the owner's PD-inverter bridge):

```
19FED9C0#FF96E60F0100D1EA        E6 := 1, and nothing else
```

No `EF` frame, no read-back twin, nothing from `9F`.

## Result 1 — the write lands from an arbitrary source address (the positive control)

`E6` went `0` → `1` and held across **five consecutive G12 broadcasts (~25 s)**. This is the
control that makes the rest meaningful: had the G12 rejected a config write from a non-panel SA,
`EF` staying put would have proven nothing. It does not reject it. **The G12's config-write path
is blind to the source address**, the same way the panel's receive filter is (`MCF-FORMAT.md`
§5.4a) — two independent nodes, same property. A bridge can therefore write any option selector.

## Result 2 — the G12 does NOT force AGS

`EF` stayed **`0`** in every one of those five broadcasts. `D7` stayed `0`; `0x15` stayed
`0x5A01`. So the interlock is the **panel's alone**, in both directions, and Finding 50 is closed:

| direction | evidence |
|---|---|
| G12 does not *clear* the partner | PC.3 t=76.668: panel wrote `E6 := 0` alone, G12 held `mode = 1` for 29 s |
| G12 does not *set* the partner | **this probe**: `E6 := 1` alone, G12 held `mode = 0` for 25 s |

## Result 3 — the panel does not re-impose it on receipt either

Owner, watching the screen while `E6` was set: **"Only PI is selected."** So the panel took the
new value through the ordinary config-payload path (Finding 19's unpack → slot 113 → the checkbox
lights) and did **not** react by transmitting the `EF := 1` it emits on a tap. The coupling lives
strictly in the *tap handler*, not in any consistency check over received config.

## Result 4 — the deselect asymmetry, confirmed on the screen

Owner, independently, before the probe: *"Disabling AGS does not deselect progressive inverter;
after that, selecting either AES option does deselect it."* That is exactly Finding 50's table —
the AGS-deselect arm (`0x0024D0D8`) emits `EF := 0` alone, while both AES select arms carry
`E6 := 0`. The asymmetry moves from `proven-from-code` to **`observed-on-hardware`**, and with it
the standing warning: `mode = 0, PI = 1` is a perfectly reachable state, so nothing may assume
the pair is consistent.

Incidental confirmation: `0x15 = 0x5A01` throughout, matching the capture-derived rule that its
bit 0 tracks `EF ∈ {0,1}` — a G12-side derivation, checked live.

## Restore

`19FED9C0#FF96E60F0000D1EA` returned `E6` to `0`; the sweep then read `E6 = 0`, `EF = 0`,
`D7 = 0`, `0x15 = 0x5A01`, byte-identical to the baseline. **These are config selectors persisted
in G12 NVM, not loads** — recoverable from the screen by tapping any mode row, which is why the
probe was safe to run.

---

# PC.22 — reference build v7 on the coach (2026-08-23)

**Method:** USB flash, owner-operated; observations by eye on the panel.

Confirms, in one image, two capabilities this project had never exercised and one correction the
file could not have made on its own.

## Result 1 — a record inserted into an object's record stream renders correctly

The HOME inverter block's single 221 px hit region is now a 144 px tap and a 73 px hold strip,
built by inserting a 16-byte `wtype 12` record into object 1409 and shrinking the original. Tap
still toggles the inverter; hold opens the PD Inverter page. Nothing drawn moved — which is the
part that matters, because the first attempt placed both regions perfectly and shifted the
block's label and boxes 148 px off the panel. `CONTROL-BEHAVIOR.md` §9.

## Result 2 — authored Thumb-2 in dead space executes as written

The expired-deadline consumer's second arm is a `b.w` into a dispatch stub written into the NOP
run that removing the Progressive-Inverter interlock left behind. All three holds work:

| hold | goes to |
|---|---|
| the four HOME tanks, 5 s | PROBE TANK DIAGNOSTICS |
| the gear icon, 5 s | SERVICE MODE |
| the inverter strip, 3 s | PD Inverter |

The gear and tank holds are the *stock* behaviours, preserved through the stub rather than left
alone — the consumer arm they used to run is gone. That they still work is the evidence the stub
dispatches correctly, and that the Progressive Inverter tap handler still falls through its own
dead space is the evidence the step-over is right.

## Result 3 — the screen-id map, settled by a photograph

The owner held the gear icon for 5 s, got **SERVICE MODE**, and said so. `object − 4` — this
project's rule since 2026-08-19 — predicts AUTO ENGINE START ENABLE. The rule was wrong: screen
ids are the match values of object 1's type-6 switch, read from GuiData `+0x4562C`
(`layout.screen_object_map`). `−4` is exact only over screens 0-4, the nav rail it was derived
from; screen 6 does not exist, so everything after drifts.

**This is the third time the file has been read as contradicting the coach and been wrong**, and
the first time the contradiction was a *rule* rather than a value. It had already aimed v7's new
hold at GO POWER! CONFIGURATION (screen 34) instead of PD Inverter (screen 39), through every
structural gate, the full test suite and a commit — because each check verified the constant that
was written, not what it denoted. The fix is to name pages by **object index**, which can be
rendered and looked at.

*Not separately itemised:* selecting **Progressive Inverter and AES together** (the v6 interlock
removal) ships in this image and is expected to work, but the owner's report covered the split and
the holds. Confirmed-by-inclusion, not observed.

---

# PC.23 — The G12 is the inverter/charger's commander, and it broadcasts to **instance 1**

**Captured:** 2026-08-23, `can1` (`can_rvc`) from the bus host, probe source address `0xC0`
**Method:** transmit. Read-back requests first (non-mutating), then one signed adjust per
selector, each **immediately reversed and verified by read-back**. This document's third
transmit entry.
**Logs:** `captures/pc23-baseline-2026-08-23.log`, `…-readback-…`, `…-write-adjust-…`,
`…-write-maxchargerate-…`

## The question

Object 17 `INVERTER / CHARGER SETTINGS` (`GUICODE.md` Finding 51) writes generic G12 config
selectors, not inverter DGNs. So who actually commands the inverter/charger — and what address
does it send to? Owner's hypothesis: the G12, and he suspected `0x92` behaved like var 114.

## Answer: the G12 relays, and it addresses **nobody**

Within **2–10 ms** of each `0x1FED9` config write, the G12 (SA `0x9C`) transmits an RV-C
inverter/charger **command** DGN that appears nowhere in the 30 s baseline:

| panel selector | control | G12 emits | payload | value field |
|---|---|---|---|---|
| `0x82` | Low Batt. Cutout | **`1FFD0`** | `01 FF FF FF FF D8 00 FF` | u16 LE at byte 5 |
| `0x86` | VAC Dropout | **`1FF87`** | `01 55 FF FF FF FF FF FF` | byte 1 |
| `0x92` | Max Chrge Rate | **`1FF95`** | `01 C8 FF FF FF FF FF FF` | byte 1 |
| `0x88` | Battery Type | **`1FFC4`** | `01 FF FF FF FF FF 01 FF` | byte 6 |
| `0x99` | Shore Brkr Size | *nothing* | — | — |

**All four are PDU2 (PF ≥ `0xF0`), so they are broadcast — there is no destination address in the
CAN id.** The target is **instance `0x01`**, payload byte 0, on every single frame. "What source
id is it sending to" has no answer because the question does not apply: the G12 shouts the command
and the device that claims charger/inverter instance 1 is expected to act on it.

That **confirms on the wire** what `CLAUDE.md` had recorded as a file-derived inference — "all
seven charger/inverter command DGNs address instance 1, which nothing on this coach claims; have
the bridge claim instance 1 and the panel's existing controls drive it." Four of the seven are now
observed doing exactly that, values included.

## Four things the round trip settles beyond the map

**A read-back triggers no relay.** The read-back capture contains **zero** CAN ids absent from
baseline other than our own `19FED9C0`. Only a write makes the G12 command. So `D3 FF FFFF` is
safe to poll with, which is what made it the right first probe.

**`Shore Brkr Size` is G12-local.** Selector `0x99` stored fine (0 → 5 → 0, confirmed by
read-back) and the G12 emitted **nothing**. It is a parameter the G12 keeps for itself — load
shedding, presumably — not one it forwards. A defensible negative: the other four in the same
capture, through the same code path, all relayed.

**The periodic sweep is partial; read-back reaches more.** Of these five only `0x92` appears in
the G12's 5 s `0x15FCE` sweep. All five answer a `D3` read-back on the very next frame. So absence
from the sweep says nothing about whether the G12 holds a selector.

**Delta semantics extend to five more selectors.** PC.11 proved `D2` adjusts for `CC`/`CD`/`CE`
and explicitly left the scope open. `0x82` (±2), `0x86` (±5), `0x88` (±1), `0x92` (±20) and `0x99`
(±5) all moved by exactly the signed amount and returned exactly on the reverse. Still not a
general rule — but it is now five selectors, not none, beyond the original three.

## Finding 51's transforms, and its one inference, are confirmed

The read-back values are the panel's raw inputs, so they test the decode directly:

| selector | raw | Finding 51 transform | displayed |
|---|---|---|---|
| `0x82` | 214 | `(raw+1)>>1` | 107 → **10.7 V** |
| `0x86` | 80 | none | **80 VAC** |
| `0x92` | 200 | `>>1` | **100 %** |
| `0x99` | 0 | none | **0 A** |
| `0x88` | 0 | `>>4` | **0** |

**The one-decimal reading is no longer an inference.** Finding 51 argued it from the write gate's
admissible range and labelled it `inferred-from-code`. A Low Battery Cutout of **10.7 V** is the
correct answer for a 12 V inverter; 107 V is absurd. Upgrade to `observed-on-hardware`.

`0x92` also closes its own loop end to end: the G12 sweeps `195FCE9C#9200C8…`, the panel's
receive-filter var 114 matches `payload[0] == 0x92` and extracts `payload[2]` = 200, `>>1` = 100 %
on screen — and `1FF95` byte 1 carries that same 200, which is RV-C's 0.5 %/bit. Read path, write
path and relay all agree on one number.

## Restoration

Every adjust was reversed and a final sweep re-read all five: `0x82` = 214, `0x86` = 80,
`0x92` = 200, `0x99` = 0, `0x88` = 0 — **identical to the values before the probe**. The coach
was left as found.

---

# PC.24 — the G12 input pins on the wire, and the `0xFB` question settled (2026-08-24)

`captures/pc24-input-pins-2026-08-24.log`, owner-driven, passive `candump -L can1 | grep -i
19FBDA9C`. Ignition on, ignition off, then the water pump alone, then every physical switch at
least once. **The coach has no physical EXT/RET switches**, so pins 6 and 7 were not exercised;
pin 3 did not appear either.

This is the experiment `RVC-GAPS.md` §5f and `rvc-spec.yml` both asked for. It closes a
**disagreement between two decodes** — this project's static read of the 13.6 MCF and
`rvc2mqtt`'s handler, which was built from observed Ethos 20DL traffic — and it does not land
wholly on either side.

## Result 1 — `0x1FBDA` instance `0xA0 + pin`, `payload[1]` = state: **CONFIRMED**

Every frame is byte-for-byte what `cli g12-inputs` printed before anyone turned a key:

```
19FBDA9C#A101FFFFFFFFFFFF      pin 1 active        19FBDA9C#A100FFFFFFFFFFFF      inactive
19FBDA9C#A901FFFFFFFFFFFF      pin 9 active        19FBDA9C#A900FFFFFFFFFFFF      inactive
```

Instances seen: `A1`, `A2`, `A4`, `A9`, `AA`, plus `FB` and `BF`. **Pins 5 and 8 never appear**,
as the input dispatch table predicted (rule count 0 for both). Tail bytes are `FF` on every
input frame.

## Result 2 — `0xFB` is pin 10's second instance, NOT an idle code

The decisive pairing, and it is exact:

* **14 `FB` frames, 14 `AA` frames, and not one `FB` without a matching `AA`.** Separation is
  `0.55`–`0.59 ms`, `FB` always first, on all 14.
* In the **water-pump-only window** (t = 801–808, ignition already off) there are `A901`/`A900`
  frames and **zero `FB` frames of any kind**.

So `FB` tracks the key alone. `rvc2mqtt`'s reading of `0xFB` as the *idle* code in an
active/idle alternation is **wrong**, and this project's read — pin 10's dispatch entry fires
four rules, its own `0xAA` pair and an `0xFB` pair byte-identical apart from the instance — is
**confirmed on the wire**.

## Result 3 — `payload[1]` is the instance's own state, not a global 12V flag

`rvc-spec.yml` calls byte 1 `aux_12v_active`, "0x01 when any 12V-driven input is active". It is
not. Pins **1, 2 and 4 are GND-sense inputs**, and every one of them carries `01` while held and
`00` on release — with the **ignition off**, so no 12V input was active at all:

```
19FBDA9C#A101…  811.947   19FBDA9C#A101…  812.948   19FBDA9C#A101…  813.948
19FBDA9C#A100…  814.122
```

A GND input reporting `01` with the 12V line cold settles it. The same frames also refute
"GND inputs … deactivate via `0xFB`/aux=0": they deactivate via **their own instance** with
byte 1 = `00`, and no `FB` is involved.

## Result 4 — but there IS a 1 Hz refresh, and THIS project had that wrong

`RVC-GAPS.md` §5f said the rules "look edge-triggered … so a bridge should not expect a
heartbeat". **That is wrong.** Every instance repeats while held, at `1.000 s` — measured, not
approximate, on `FB`, `AA`, `A9`, `A1`, `A2` and `A4` alike, GND and 12V both:

| instance | consecutive ON gaps under 5 s |
|---|---|
| `FB`, `AA` | `1.000` |
| `A9` | `1.000` |
| `A1`, `A2`, `A4` | `1.000`, `1.001` |

It is a **refresh from the ON edge**, not a free-running heartbeat: eight short taps
(`82`, `120`, `134`, `174`, `312`, `616`, `668`, `872 ms`) each produced exactly one ON/OFF
pair with no repeat. Anything held past 1 s starts refreshing.

*Why the static decode missed it:* the rule table holds the ON and OFF **templates**, and a
periodic re-send is a property of the code that fires them — G12 table `0x67`, the 6,848-byte
blob that is still mostly unread. "No periodic template in the table" was true and the
conclusion drawn from it was not.

**This also explains `rvc2mqtt`'s observation rather than dismissing it.** They saw a real 1 Hz
cadence. With the ignition on *and* another input held, `FB`/`AA` and `A9` interleave once a
second — which reads exactly like "the active code alternating with an idle code" if you do not
know `FB` is pin 10.

## Result 5 — `0xBF` is real, and unexplained

```
19FBDA9C#FB00FFFFFFFFFFFF   789.442      ignition off
19FBDA9C#AA00FFFFFFFFFFFF   789.443
19FBDA9C#BF01000000000000   789.454      +11 ms
19FBDA9C#BF00000000000000   790.168      +714 ms
```

Twice only, both bracketing the ignition-off transition. Note the tail is **all zeros**, unlike
every input frame's `FF` — so it is not an input instance with a different number, it is a
different message. `0xBF` is in **no** G12 rule table and in no input dispatch entry, so this
project has no decode for it at all. `rvc2mqtt` had already recorded "code `0xBF` has also been
observed as a transition frame during some deactivation sequences" — **independently
corroborated, still unexplained by either side.** Do not guess at it.

## What this changes

| claim | source | verdict |
|---|---|---|
| instance = `0xA0 + pin`, `payload[1]` = that pin's state | this project, static | **confirmed** |
| `0xFB` is pin 10's twin instance | this project, static | **confirmed** |
| pins 5 and 8 never broadcast | this project, static | **confirmed** |
| edge-triggered, no heartbeat | this project, static | **WRONG** — 1 Hz refresh while held |
| `payload[1]` is a global 12V flag | `rvc2mqtt`, observed | **WRONG** |
| `0xFB` is the idle code | `rvc2mqtt`, observed | **WRONG** |
| a ~1 Hz cadence exists | `rvc2mqtt`, observed | **confirmed** (mis-attributed, not mis-seen) |
| `0xBF` appears during deactivation | `rvc2mqtt`, observed | **confirmed**, unexplained |

*The methodological point, and it cuts both ways.* The static decode got the frame **format**
right — every field, including two instances no guess would produce — and got the **behaviour**
wrong, because behaviour lives in code this project has not read. The observed model got the
cadence right and the format wrong, because a capture of a coach with the ignition on cannot
separate two instances that always travel together. Neither was a bad method; each was blind
where the other could see.

---

# PC.25 — `1FFB7` is TANK_STATUS. The awning ignition interlock is REFUTED (2026-08-24)

`captures/pc25-ignition-vs-ffb7-2026-08-24-EXCERPT.log` — **an excerpt**; the full log is
a capture on the bus host and should be copied here. Owner-driven, both DGNs on one trace:

```
candump -L can1 | tee ~/pc25-ignition.log | grep -E '19FBDA9C|19FFB79C'
```

107 s: ~49 s parked, ignition **on at t=428.989**, **off at t=457.275** (held **28.3 s**), then
~30 s parked. PC.24's ignition sense is the positive control on the same trace.

## Result 1 — `1FFB7` does not move with the ignition. At all.

Four instances broadcast once a second, and each has **exactly one payload for the whole
capture**:

```
19FFB79C#000003FFFFFFFFFF      instance 0
19FFB79C#010003FFFFFFFFFF      instance 1
19FFB79C#020003FFFFFFFFFF      instance 2
19FFB79C#120003FFFFFFFFFF      instance 18
```

Split the frames into before / during / after the 28.3 s ignition window and the sets of distinct
payloads are **identical in all three**. Meanwhile `19FBDA9C#FB01`/`AA01` runs for exactly that
window and stops. The key demonstrably moved; `1FFB7` demonstrably did not.

## Result 2 — the whole interlock chain was a misreading, and the file says so too

`CLAUDE.md` carried this as `inferred`: G12 rule #93 "triggers on DGN `0xFFB7` and targets
**output 3** (AWNING EXT)", the panel subscribes with five filter entries, it carries the label
`Awning Locked by Ignition`, and instance 3 "carries a bit flipping `0x50`↔`0x51`" whose "byte2
`0x64` matches the rule's constant". Every link is wrong, and reading all five `1FFB7` rules
together — rather than #93 alone — shows why:

```
t62 #45  DGN 1FFB7  inst 0x00  00__03FFFFFFFFFF  mask 02  p[1]<-0xb5
t62 #46  DGN 1FFB7  inst 0x02  02__03FFFFFFFFFF  mask 02  p[1]<-0xb6
t62 #47  DGN 1FFB7  inst 0x01  01__03FFFFFFFFFF  mask 02  p[1]<-0xb7
t62 #48  DGN 1FFB7  inst 0x12  12__03FFFFFFFFFF  mask 02  p[1]<-0xb8
t62 #93  DGN 1FFB7  inst 0x03  03__64FFFFFFFFFF  mask 02  p[1]<-0xc2
```

They are **five instances of one rule**, not four of one kind and a special one. Each broadcasts a
level in `payload[1]` substituted from a `0x15FCE` selector. That is **standard RV-C
`TANK_STATUS`**, byte for byte:

| inst | RV-C tank | byte 2 | selector | capture |
|---|---|---|---|---|
| 0 | fresh water | `03` = resolution 3 | `0xB5` | level 0 → **0 %** |
| 1 | black waste | `03` | `0xB7` | level 0 → **0 %** |
| 2 | gray waste | `03` | `0xB6` | level 0 → **0 %** |
| 18 | second gray | `03` | `0xB8` | level 0 → **0 %** |
| 3 | **LPG** | `64` = resolution **100** | `0xC2` | *absent — see below* |

So:

* **`inst 0x03` is the RV-C tank instance for LPG, not "G12 output 3".** An RV-C instance byte was
  read as a G12 output number. That is the same class of error as reading table `0x62`'s
  substitution mask into table `0x20` — a byte whose meaning comes from its record kind.
* **`0x64` is the resolution field**, 100, which is simply what an LPG sender uses instead of the
  waste tanks' 3. It "matches the rule's constant" because it *is* the rule's constant, in a field
  that has nothing to do with ignition.
* **`0x50`↔`0x51` is a propane level jittering 80↔81 out of 100** — exactly what a float sender
  does all day. The null hypothesis was correct.
* **The panel's five receive-filter entries on instances 0/1/2/3/18 are the tank display**, not an
  interlock subscription: fresh, black, gray, second gray and LPG. The four resolution-3 tanks all
  read **0 %**, which is the owner's coach as already recorded (`render.py`'s `tank_level` default
  of 0 was set from that observation).

**Why instance 3 is missing, and it confirms the reading rather than weakening it** (owner,
2026-08-24): **the lithium floorplans have no LPG tank.** WA / WD / WT / TB do not; RA / RD / RT
do (SY unknown). This coach is **WD**, so there is no propane tank to report and the G12 does not
broadcast the instance. The owner states that selecting **RD** starts it broadcasting.

That is a **prediction the tank reading makes and the interlock reading cannot**. If instance 3
were an ignition/awning flag there would be no reason for its presence to depend on which
floorplan is selected; if it is the LPG tank, absence on a coach with no propane tank is exactly
right. The `0x50`↔`0x51` values recorded earlier must therefore come from a capture taken under a
different configuration, or from a build/coach that has the tank.

**The `Awning Locked by Ignition` label is real and its source is now UNKNOWN.** Nothing here
touches the awning. The obvious candidate is the one PC.24 just proved live — G12 **input pin 10**,
the ignition sense, instance `0xAA`, var 201, slot 132 — but that is a hypothesis and it has not
been traced.

## What this costs, and the rule it earns

The interlock chain had **five** pieces of corroborating evidence: a rule on the right DGN, an
output number that matched, a panel subscription, matching labels elsewhere in the file, and a
constant that matched. It was still wrong, because the corroboration was assembled around a
conclusion rather than derived: the search began from "where is the ignition interlock?", and each
piece was accepted for fitting that question.

*The check that would have caught it costs nothing:* **read the other records of the same kind.**
Rule #93 in isolation looks like an awning rule with an ignition constant. Rules #45-#48 beside it
make all five obviously one tank-status family. The narrow listing that hid the input pins
(Finding 54's `cli g12-rules` scoping) hid this too — for the second time, *a tool scoped to one
question answered the next one wrongly.*

This is also the third bus/file disagreement in one day (PC.24's two, and this), and the pattern
is consistent: **the file is reliable about format and unreliable about purpose.** Every structural
claim it made here was right — the DGN, the instances, the substitution, the selectors. Every claim
about *what the structure is for* needed the wire.

---

# PC.26 — the awning interlock is REAL and lives in the G12; and the panel repaints live (2026-08-24)

Owner, on the coach, ignition on. Three results, and **two of them refute this project's own
claims from earlier the same day** (`GUICODE.md` Finding 56).

## Result 1 — the EXT label repaints IMMEDIATELY. Finding 56's staleness prediction was WRONG

> "Sit on HOME, turn the key, and the button should still read `EXT`."

It did not. **The HOME `EXT` button changed to `Locked by Ign` the moment the ignition came on**,
with no navigation away and back. Object 181's type-6 switch on slot 132 is evaluated **live**.

The prediction came from a scan that could not have found what it was looking for — see the
retraction in Finding 56. This is the *third* argument-from-absence this project has had to
withdraw, and the first one where the hardware caught it within hours of it being written.

## Result 2 — the wireless keypad's EXT is locked too, so the G12 enforces it

The keypad is a **separate RV-C node**: it commands the G12 directly and never consults the panel.
Its `EXT` button also did nothing with the ignition on. A panel-side inhibit could not produce
that, so **the enforcement is in the G12** — which is what the design argument predicted the day
before, from the keypad's existence alone.

That moves the interlock from *"not located, possibly nonexistent"* to **confirmed to exist,
confirmed G12-side, mechanism unread**. It is not in any of the four decoded rule tables (pin 10
fires four rules, all `0x1FBDA` broadcasts driving no output), so it is in table **`0x67`**, the
6,848-byte Thumb blob.

## Result 3 — only EXTEND is locked. RETRACT still works

The owner reports hearing the relay click for **RET** with the ignition on, and **no click for
EXT**. *(Read from a message that names RET twice; this is the only reading consistent with the
rest, and it matches the firmware's own long-form string `AWNING EXTEND LOCKED BY IGNITION` —
but confirm before building on it.)*

That is the safety-correct design and worth stating plainly: **you must always be able to retract
an awning, and you must never extend one while driving.** It also explains why the label is
attached to the EXT button alone rather than to the awning block.

## What this leaves open, sharply

Slot 132 (`0x2003A4E5`, from `0x1FBDA` instance `0xAA`) repaints live. Slot 169 (`0x2003A50C`,
from `0x1FBDA` instance `0xFB`) does **not** — `Engine Running` on ELECTRICAL is owner-reported
stale, which is what Finding 55 was written to explain. Same DGN, same input pin, same
1 Hz refresh, both type-6 switches, opposite behaviour.

So the staleness of `Engine Running` is **still a real observation with no explanation**. Finding
55's mechanism is withdrawn; its symptom stands. Candidates, none tested: the walk re-runs only
for the **active** screen and HOME (screen 0) is special; the two switches sit under different
container types with different invalidation; or something re-composes HOME on a timer. Do not
adopt one without evidence — the last explanation here was confidently wrong.

---

# PC.27 — the interlock is an RV-C **LOCK**, and the keypad is not a bus node (2026-08-24)

`captures/pc27-awning-interlock-2026-08-24.log` — **7,003 frames, 96.5 s, the whole bus, no
filter.** Captured over `ssh` while the owner ran a scripted sequence: ignition on; tap EXT on the
Lyra; press-and-hold EXT on the Lyra; tap EXT on the keypad; press-and-hold EXT on the keypad; tap
RET on the keypad; tap RET on the screen; ignition off.

This is the most informative capture in this document. It answers the interlock question outright,
confirms one claim this project inferred without proof, and **refutes the argument** that had been
used to reach the right conclusion the day before.

## Result 1 — the interlock is `DC_DIMMER_COMMAND_2` command **33 (Lock)**, sent by the G12 itself

```
29.967  19FBDA9C#AA01…                    ignition ON
29.974  19FEDB9C#03FF0003FF00FFFF         G12 -> ch 3, command  3  OFF
29.975  19FEDB9C#03FF0021FF00FFFF         G12 -> ch 3, command 33  LOCK
   …
66.932  19FBDA9C#AA00…                    ignition OFF
66.934  19FEDB9C#03FF0022FF00FFFF         G12 -> ch 3, command 34  UNLOCK
```

**8 ms after ignition-on the G12 forces channel 3 off and then locks it; 2 ms after ignition-off it
unlocks it.** There is nothing proprietary here — commands **33 = Lock** and **34 = Unlock** are
standard RV-C `DC_DIMMER_COMMAND_2`, and the G12 is the source (`19FEDB9C`, SA `0x9C`). The
mechanism was never hidden in table `0x67`; it was on the wire, in the open, in a DGN this project
had decoded a year of work ago. It had simply never been captured with the key turning.

*Note the command byte is `payload[3]`, not `payload[2]`* — `03 FF 00 21 FF 00 FF FF` is
instance 3, group FF, **level 0**, **command 0x21**.

## Result 2 — `DC_DIMMER_STATUS_3` carries the lock state, and the G12 *declines* rather than ignores

The G12's own status echoes both fields:

| | `1FEDA` byte 3 | byte 5 | brightness |
|---|---|---|---|
| ch 3, before ignition | `0xFC` unlocked | `0x03` last cmd OFF | 0 |
| ch 3, ignition on | **`0xFD` LOCKED** | `0x21` last cmd LOCK | 0 |
| ch 3, **panel commands ON while locked** | **`0xFD` LOCKED** | **`0x01` last cmd ON** | **0** |
| ch 4 (RET), commanded ON | `0xFC` unlocked | `0x01` last cmd ON | **200** |
| ch 3, after ignition off | `0xFC` unlocked | `0x22` last cmd UNLOCK | 0 |

So **byte 3 is the lock flag** (`0xFC` = unlocked, `0xFD` = locked — RV-C's 2-bit Lock Status in
bits 0-1) and **byte 5 is the last command applied**. The third row is the interesting one: the G12
**receives the ON command, records it in byte 5, and leaves brightness at 0.** It is not dropping
the frame — it is processing it and declining to act. That distinction was unavailable from a relay
that does not click.

## Result 3 — the panel transmits normally while locked. Finding 56's claim CONFIRMED

Finding 56 asserted "the panel relabels the button and still sends EXT", inferred purely from the
touch region sitting outside the type-6 switch — structure, not behaviour. It is right:

```
38.543  19FEDB9F#04FF0003FF00FFFF   panel -> ch 4  cmd  3  (kill RET)
38.544  19FEDB9F#03FFC8010200FFFF   panel -> ch 3  cmd  1  level 200
39.543  19FEDB9F#03FFC8010200FFFF   …repeat
40.543  19FEDB9F#03FFC8010200FFFF   …repeat
41.543  19FEDB9F#03FFC8010200FFFF   …repeat
42.511  19FEDB9F#03FF0003FF00FFFF   panel -> ch 3  cmd  3  (release)
```

**The `gestures` decode of group 3 is confirmed frame-for-frame** — `press → cmd 3 ch 4`,
`press+held → cmd 1 ch 3` repeating at **exactly 1.000 s**, `release → cmd 3 ch 3` — on a control
nobody had ever captured. A tap emits `cmd 1` too (phase mask `press|held` fires on press), then
`cmd 3` ~180 ms later.

## Result 4 — the wireless keypad has NO source address. The design argument was WRONG

**Only two nodes ever send `1FEDB` in the whole capture: `19FEDB9C` (G12) and `19FEDB9F` (panel).**
The keypad presses at t=47.3 and t=52.1 came out as **`19FEDB9F`** — byte-identical in form to the
on-screen presses. There is no keypad SA anywhere in 7,003 frames.

The keypad is a **wireless input to the panel**, not an RV-C node. The panel transmits on its
behalf — including its battery status: `19FFFD9F#81FF3900FFFFFFFF` is sent by **SA `0x9F`**, so the
note elsewhere in this project that "the keypad broadcasts `DGN 1FFFD`" is wrong; the panel
broadcasts it for the keypad. That is consistent with pairing living in **panel NVM** (the
2026-08-20 false alarm).

**This retracts the design argument of 2026-08-24**, which ran: *EXT/RET are on HOME and on the
keypad; the keypad commands the G12 directly and never consults the panel; therefore a panel-side
inhibit would be bypassed, therefore enforcement must be G12-side.* The premise is false. The
**conclusion is still correct** — Result 1 proves it directly — but it was reached through a
coincidence, and an argument that survives only because its conclusion happens to hold is not
evidence. *Do not reason from a device's independence without confirming it has a source address.*

## Result 5 — only EXTEND is locked

Channel 4 (RET) was never locked (`byte 3 = 0xFC` throughout) and energised to **brightness 200**
on both the keypad tap (t=58.7) and the screen tap (t=62.7). Only channel 3 is locked. This
confirms the owner's report and matches the firmware's own string
`AWNING EXTEND LOCKED BY IGNITION`: **you must always be able to retract an awning, and never
extend one while driving.**

## Consequence for the rest of this project

Commands **`0x21`/`0x22`** appear in the slide-out groups (29/30/95, and group 94 paired with pin
27), where this project's notes call them the *"act-while-held slide-out signature"*. They are
**Lock and Unlock**. A slide-out group emitting `1/3/33/34` is extending one direction while
**locking** the other, which is the same safety pattern the awning uses — not a gesture at all.
Corrected in `CLAUDE.md`; the group decodes themselves are unaffected, only the name.

---

# PC.28 — an AES engine run, end to end: the interlock confirmed by a second route (2026-08-24)

`captures/pc28-aes-engine-run-2026-08-24.log` — **24,583 frames, 365.7 s, whole bus, no filter**,
captured over `ssh` while the coach's **van-side AES** was idling the engine to charge the 12 V
battery. The owner pressed **Stop** on the ELECTRICAL page partway through. Nothing was
transmitted by this project.

Started to answer a narrow owner question — *"what device set pin 18 on? I may have a stale MQTT
topic"* — and it answered that plus three other things.

## Result 1 — nothing on the bus commanded pin 18. The G12 did it itself

**In 365 seconds there are exactly two `DC_DIMMER_COMMAND_2` frames on the entire bus:**

```
186.239  19FEDB9F#12FF0003FF00FFFF   panel -> ch 18, cmd  3 OFF   (the Stop button)
260.567  19FEDB9C#03FF0022FF00FFFF   G12   -> ch  3, cmd 34 UNLOCK
```

Pin 18 was already **on** when the capture began (`19FEDA9C#12FFC8FCFF014400`, brightness 200,
unlocked, last command 1) and no node ever commanded it on. **Pin 18 is `AES START/STOP`** in the
G12's own output config (half-bridge, 1 A, latch) and `cli pins` shows it has **no on-screen
consumer** — no button drives it. The G12 runs AES and energises its own output; there is no bus
actor to find. The owner's bridge is exonerated, and structurally so: `mqtt.register()` clears each
command topic with an empty retained publish *before* subscribing, so a stale retained command
cannot replay on restart.

## Result 2 — the Stop button is a plain command 3 on channel 18, obeyed in 1 ms

```
186.239  19FEDB9F#12FF0003FF00FFFF     panel commands OFF
186.240  19FEDA9C#12FF00FCFF030000     G12 reports brightness 0, last cmd 3
```

So the panel *can* drive channel 18 and the G12 *does* obey. That makes the owner's earlier report
— pressing **Start** on the same page did not start the engine — a live question rather than a
vague one: by symmetry Start should send command **1** on channel 18. **Untested**; see the open
item at the end.

## Result 3 — the awning interlock fires on the SENSE, not the key. PC.27 confirmed by a new route

Nobody turned a key in this capture. The AES-started engine drove the ignition sense line high, and
the G12 locked the awning anyway:

| t | event |
|---|---|
| 0.079 | channel 3 already **LOCKED** (`19FEDA9C` byte 3 = `0xFD`) |
| 0.125 | ignition sense `19FBDA9C#AA01`/`FB01` — high, engine running |
| 186.239 | Stop pressed, pin 18 → 0 |
| **260.563** | ignition sense → **`AA00`/`FB00`** (74 s after the relay opened) |
| **260.567** | G12 broadcasts **cmd 34 UNLOCK** on channel 3 |
| 260.568 | channel 3 reports `byte 3 = 0xFC` unlocked |

**Unlock follows the sense dropping by 4 ms.** PC.27 established the mechanism with a key; this
shows it keyed to the *sense line*, whatever raises it. The practical consequence is worth stating
plainly: **an AES auto-start locks the awning**, with no key turned and nobody at the panel.

It also confirms `Engine Running` end to end for the third time — the AES-run engine raises input
10, `0xFB` goes to 1, and the owner saw the text appear. **The display was never at fault.**

## Result 4 — AES state codes: 64 and 192 observed, 131 not

`rvc-spec.yml` carries `15FCE` selector `05` byte 2 as `0 disabled / 1 enabled`, with
`3 cranking / 64 monitoring / 131 running` marked **unverified** and noted as needing "a capture
with the engine actually cranking/running to confirm". This is that capture:

| t | byte 2 | state |
|---|---|---|
| 1.1 – 261.1 | **`0x40` = 64** | engine running, then stopped |
| **261.1 – 291.1** | **`0xC0` = 192** | a 30.0 s window opening 0.5 s after the ignition sense dropped |
| 291.1 onward | `0x40` = 64 | back to steady |

**`192` is undocumented anywhere in this project or in `rvc-spec.yml`.** Two things are worth
recording and nothing more: 64 is *not* "running" (it holds both while the engine runs and long
after it stops), and 192 is a **transient tied to the sense dropping, not to the relay opening** —
the relay opened 74 s earlier. The round 30.0 s duration suggests a post-run timer. `131` never
appeared. *No interpretation is offered:* one capture, with the van's AES driving rather than the
Firefly's, is not enough to name a state — and this document has withdrawn three named mechanisms
in one day for exactly that reason.

For the record, the rest of the AES profile through the run: `9B` = **0** (AES *disabled* on the
Firefly side, since the van's AES was driving), `EF` = 3 (AES Single Output), stop-at `0D` =
**54.40 V**, start-at `31` = **51.00 V**, time-at-stop `0E` = **1800 s**, max run `16` = **115 min**,
time-at-start `0C` = 60 s. Measured pack `0A` drifted **54.65 → 54.40 V** across the run.

## Closed by the owner, 2026-08-24

The thread that prompted this capture — `Engine Running` appearing not to update — is **closed
and is not a defect.** Correct behaviour is confirmed three ways (ignition on, ignition off, and
this AES auto-start), and the owner attributes the original observation to **an early build of a
bad firmware**, an image not in this corpus.

One factual question was raised and deliberately left untested: whether the ELECTRICAL `Start`
button transmits at all. By symmetry with Result 2 it should send command **1** on channel 18, and
if it does while the G12 leaves brightness at 0, `19FEDA9C#12` byte 3 would say whether the
channel is locked. Recorded so the question is not re-derived from scratch; **not** an open
investigation.

---

# PC.29 — reference build v8: screen 14 seen for the first time (2026-08-24)

**Method:** USB flash, owner-operated. Two photographs of the panel plus one `candump -L can1`
excerpt. No frames were transmitted — this is an observation entry, not an injection.

Screen 14 `INVERTER / CHARGER SETTINGS` is **orphaned in stock 13.6** (`GUICODE.md` Finding 53):
its only navigation site is guarded by an action id carried solely by two objects that are not in
the display graph. v8's hold on the HOME inverter strip writes the screen id straight to
`state+0x20A`, bypassing the action-id chain, and so gives the page its first route. Nobody had
ever seen it on this coach, and Findings 51/53 recorded its runtime behaviour as **untested**.

## Result 1 — the retarget works and the page renders

The hold opens object **17**, and the page draws correctly: header, five control blocks, the
`Inverter/Charger Status` readout block, `Faults:`, and the three identity rows. Nothing is
clipped or misplaced.

This confirms the v8 change itself (one recipe parameter, `screen_object 38 → 17`) and, with it,
that `layout.screen_for_object` picks the right object after the `state+0x20A` retraction of
PC.22 Result 3 — the bug that had aimed v7 at GO POWER! CONFIGURATION through every gate.

## Result 2 — Finding 53's predictions hold 6 for 6

Finding 53 predicted, from the file and the 30 s PC.23 baseline alone, that exactly one of the
page's control readouts would show a live value. The photograph agrees on every row:

| control | displayed | predicted | why |
|---|---|---|---|
| `Max Chrge Rate` | **100%** | live | var 114, `0x15FCE` instance `0x92` — the one selector the G12 sweeps |
| `Shore Brkr Size` | **255 A** | dead | no `1FF96` on this bus; 255 = `0xFF` unset |
| `VAC Dropout` | **255 VAC** | dead | no `1FF89`; `0xFF` unset |
| `Low Batt. Cutout` | **`---V`** | dead | no `1FFD2`; rendered as "no value" |
| `Battery Type` | **blank** | dead | no `1FFC6` |
| `Absorb` / `Float` | **not drawn** | gate closed | slot 23 reads 0, so the type-6 switch selects object 302, the empty 460×104 box |

Two display conventions are worth carrying, because they distinguish three different failures that
all look like "the field is broken": an **unset `0xFF`** renders as its literal value (`255 A`), a
**never-received** cell renders as **dashes** (`---V`), and a **gated-off** block renders as its
sibling's empty box. Only the last is intentional.

## Result 3 — the four identity fields are authored but unwired, confirmed

`Faults:` is blank, `I/C Firmware Rev.` reads `0.0`, `I/C Model` reads `UNKNOWN` and
`Bridge Firmware` reads `0.00`. Finding 49 called these four a **defensible negative** — no writer
in either image, with the six numeric fields beside them as the control proving the scan works.

`UNKNOWN` is a stored string in the image (five occurrences, first at `0x783ed`), so it is the
authored placeholder rather than a runtime value. These four will not change on any coach.

## Result 4 — `DC Current` reads 0 A because the publisher says 0 A

**The owner's report:** DC Current showed `0 A` with a load actually running. The capture:

```
(1787600561.852791) can1 19FFFD80#0178380400943577      x2/s
(1787600562.604546) can1 18FED580#FFFFFDFFFFFFFFFF      x1/s
```

`19FFFD80` — `DC_SOURCE_STATUS_1`, source address **`0x80`**, which is the **alternator DC
charger**. Payload:

| field | bytes | raw | value |
|---|---|---|---|
| instance | `[0]` | `0x01` | **1** — the instance object 17 reads |
| device priority | `[1]` | `0x78` | 120 |
| DC voltage | `[2:4]` LE | `0x0438` = 1080 | 1080 × 0.05 = **54.0 V** |
| DC current | `[4:8]` LE | `0x77359400` = 2,000,000,000 | 2e9 × 0.001 − 2,000,000 = **0.0 A** |

`0x77359400` is *exactly* RV-C's zero point for that field, not an absent one — `0xFFFFFFFF`
means "not available" and the panel draws dashes for it, as it does for `AC Input Volts`. So the
alternator is affirmatively reporting no current, the panel is displaying it faithfully, and the
fault is upstream of the MCF entirely.

**The load current is not missing from the bus, it is on a different DGN.** Object 17's DC pair is
`DC_SOURCE_STATUS_1` instance 1 — the *house battery* — while the PD Inverter page reads the
inverter's own `1FEE8`, where the same load showed **−25 A**. Finding 49 warned that the two
inverter pages do not report the same quantity; this is that warning observed.

### Correction to Finding 49 — the current field is bytes 4-7, not 4-5

Finding 49's table gives `DC Current | var 106 | 1FFFD instance 1 bytes 4-5`. RV-C puts the field
in **bytes 4-7** as a 32-bit at 1 mA with a −2,000,000 A offset, and only the 32-bit read decodes
to a sensible 0.0 A; bytes 4-5 alone give `0x9400` = 37,888, which is not a current. Corrected in
place. What the panel's own extraction mode actually grabs has **not** been re-derived, so the
var→display half of that row should be treated as unconfirmed rather than merely re-scaled.

*Cross-check on the voltage transform, and its limit.* Finding 49's `(v+1) >> 1` maps raw 1080 to
540, displayed as **54.0 V** with one decimal — and the earlier photograph of the same page read
55.2 V, which is raw 1104. Both reproduce. The photograph and the capture are **not
simultaneous**, so this corroborates the transform rather than proving it byte-for-byte.

## Result 5 — what is still untested

Finding 53's table listed two rows as untested. This entry closes the first and leaves the second:

| claim | after PC.29 |
|---|---|
| the page renders correctly at runtime | **`observed-on-hardware`** |
| its buttons emit the frames their bindings describe | **still untested** |

Nothing was pressed. The cheap closing probe is `Max Chrge Rate` `−` with `candump` running: it
should put `19FED99F#FF 96 92 0F <int16> D2 FF` on the wire plus a `D3 FF` read-back, and `0x92`
relays to `1FF95` (PC.23), so it is reversible by the same route it was set.

## A note for the bridge build-out

`AC Input Volts` reading `---V` is the same class of fact: this page takes AC in from **`1FFCA`
CHARGER_AC_STATUS_1**, not from `1FFD7` with the input nibble that lit the PD Inverter page in
PC.20. Nothing on this bus sends `1FFCA`.

Both gaps are publisher-side, and the panel already subscribes to everything needed —
`statusbind.registered_dgns` puts `1FEE8`, `1FFCA`, `1FFFD`, `1FFD7`, `1FFD2` and `1FF96` all
among the table's **66 registered DGNs**. So the missing values are missing because nobody
transmits them, never because the panel declined to listen. That also means retargeting var 106's
filter entry at `1FEE8` (`statusbind.retarget_filter`, 4 bytes) would cost nothing against the
83-DGN cap — untried, and it would still need the extraction mode and PC.20's DC-current
negation to be right.

## One loose end

`18FED580#FFFFFDFFFFFFFFFF` — DGN **`1FED5`**, same source address `0x80`, 1 Hz, every byte "not
available" except `payload[2] = 0xFD`. It appears in no capture this document has previously
decoded and in none of our tables. Recorded, not investigated.

---

# PC.30 — the write side of screen 14, and the `0x99` negative holds (2026-08-24)

**Captured:** 2026-08-24, `can1` (`can_rvc`) from the bus host, 10 min unfiltered,
`captures/pc30-screen14-writes-2026-08-24.log` (40,462 frames)
**Method:** observation only. The owner pressed four controls on the panel; nothing was
transmitted from the bus host. This closes the row PC.29 left open — *"its buttons emit the frames their
bindings describe"* — for four of object 17's seven controls.

The capture was taken unfiltered on purpose. This project has twice produced a confident wrong
answer from a listing scoped to the question asked last (Findings 52 and 54); the cost of a 2 MB
log is lower than the cost of that.

## Result 1 — all four controls transmit, and every wire delta matches Finding 51

Thirteen `19FED99F` frames in ten minutes — the four presses and nothing else.

| control | selector | wire values seen | Finding 51 predicted |
|---|---|---|---|
| `Max Chrge Rate` | `0x92` | `−20`, `+20` | ∓20 ✓ |
| `VAC Dropout` | `0x86` | `−5`, `+5` | ∓5 ✓ |
| `Low Batt. Cutout` | `0x82` | `−2`, `+2` | ∓2 ✓ |
| `Shore Brkr Size` | `0x99` | `+5` | ∓5 ✓ |

The emission shape is `FF 96 <selector> 0F <int16 LE> <function> <flag>` exactly as derived, and
the two families predicted from the bindings are both observed: `Low Batt. Cutout` and `VAC
Dropout` send **one `D2 EA` per step**, while `Max Chrge Rate` and `Shore Brkr Size` send
**`D2 FF` + a `D3 FF` read-back + a `D2 EA` with value 0** on release.

## Result 2 — the G12 relays three of the four, and Finding 52's rule table reproduces each frame

| write | relay, and its lag | rule | substitution |
|---|---|---|---|
| `0x92 −20` | `19FF959C#01B4FFFFFFFFFFFF` +10.1 ms | #53 `01__FF…` mask 02 | `p[1] ← 0xB4` = 180 |
| `0x92 +20` | `19FF959C#01C8FFFFFFFFFFFF` +8.1 ms | #53 | `p[1] ← 0xC8` = 200 |
| `0x86 −5` | `19FF879C#014BFFFFFFFFFFFF` +2.0 ms | #54 `01__FF…` mask 02 | `p[1] ← 0x4B` = 75 |
| `0x86 +5` | `19FF879C#0150FFFFFFFFFFFF` +1.9 ms | #54 | `p[1] ← 0x50` = 80 |
| `0x82 −2` | `19FFD09C#01FFFFFFFFD400FF` +1.6 ms | #52 `01FF…____FF` mask 66 | `p[5:7]` LE ← 212 |
| `0x82 +2` | `19FFD09C#01FFFFFFFFD600FF` +5.6 ms | #52 | `p[5:7]` LE ← 214 |

Two relay frames per DGN across the whole capture, one per press, all **instance 1**. PC.23
established this mapping by *injecting* `0x1FED9` from the bus host; this is the first time it has been
driven by an actual finger on the panel, and the rule table reproduces every frame byte-for-byte
with its substitution mask applied.

**The values are round-trips, not readings.** `Max Chrge Rate` went 200 → 180 → 200, and 200 under
Finding 51's `>>1` transform is **100%**, the number on the screen. `Low Batt. Cutout` went
214 → 212 → 214, and **214 is exactly what PC.23's read-back returned** for `0x82` — an
independent reproduction of that value by a different route, four days on.

## Result 3 — `0x99` relays to nobody. The falsifiable negative HOLDS

`Shore Brkr Size` transmitted normally and the G12 **stored** the value: one
`195FCE9C#9900050000000000` two milliseconds later, reporting selector `0x99` = 5. **No RV-C
command frame followed, of any DGN.** The frames arriving 44 ms later are the alternator's
unrelated periodic block (`19FFFD80`, `19FFFC80`, `19FFFB80`, `19FEC880`, `19FEA380`, all SA
`0x80`).

Finding 52's rule table has no entry for `0x99`, and Finding 53 predicted this exact outcome —
that the G12 would accept the value and forward it nowhere, while Firefly's *relaying* twin for
the same control (object 930, selector `0x90`, rule #56) sits unattached in the file. **Confirmed.**
This is the negative that gives the rule-table decode its weight: a table that reproduced only
the frames that exist would be unfalsifiable.

## Result 4 — the sweep membership is confirmed, and a read-back provokes a report

Over the full ten minutes:

| selector | `15FCE` reports | reading |
|---|---|---|
| `0x92` | **122** | in the G12's periodic sweep |
| `0x86`, `0x82`, `0x88` | **0** | not swept, and their presses carried no `D3` |
| `0x99` | **1**, at +2.7 ms | not swept — this is the `D3 FF` read-back being answered |

Finding 51 said "only `0x92` of these five is swept, yet all five answer a `D3 FF FFFF`
read-back". Both halves hold. `0x99` is the discriminating case: one report in ten minutes,
milliseconds after the only press that requested one.

*What this does **not** settle:* the `EA`/`FF` flag, which Finding 51 calls its least-settled byte.
In this dataset `D2 FF` and `D3` always travel together, so the report cannot be attributed to one
rather than the other. Unchanged, and deliberately not upgraded.

## Result 5 — a control whose write works and whose readout is dead, seen live

`Low Batt. Cutout` reads **`---V`** on screen — var 126's source `0x1FFD2` is sent by nobody — yet
pressing it moved the G12 from 214 to 212 and back, and the relay went out both times. So the
owner changed a real setting while the panel showed dashes throughout.

Finding 51 says each control is "two independent halves… joined only by sitting in the same
object". This is that claim observed rather than derived, and it is the practical form of the trap
that finding ends on: **a control that looks broken may be working, and a control that looks
right may be reporting something else entirely.**

## Result 6 — `DC_SOURCE_STATUS_1` bytes 4-7, settled beyond argument

PC.29 corrected the field from bytes 4-5 to bytes 4-7 on a single all-zero sample. This capture
has **60 distinct `19FFFD80` payloads** with the current field live, and the 32-bit read gives a
smooth −7 A … +8 A tracking 54.0–55.5 V. The same bytes read as a 16-bit at `[4:6]` give
37888, 38888, 35888 … — not a current at any scaling.

Resolution is **1 A**, not the 1 mA the field allows: every value is an exact multiple of
1,000,000 raw. So the alternator quantises to whole amps, which is why the panel shows integer
amps.

**Owner's reading, and it is the important one:** the alternator acts as a virtual BMS and reports
**net house-battery current — inclusive of every source**, not the inverter's draw. So this field
can never mean "inverter load", however well it is decoded. PC.29's suggestion of retargeting var
106's filter entry at `1FEE8` is therefore not a workaround for a broken field but the only way to
get the quantity the page's label implies.

## Result 7 — the G12 still has never sourced `0x1FED9`

Zero `19FED99C` frames in 40,462. That now stands across twelve captures, and it is the standing
premise behind the open question on G12 rule record 59 (`GUICODE.md` Finding 52 addendum), whose
whole experiment is to watch for that frame.

## Left changed on the coach — needs one press to restore

**`Shore Brkr Size` is now 5; it was 0** (PC.23 read it back as 0). Only one press landed — a
single `+5` — so unlike the other three this one is **not** a round-trip. It relays to nothing and
no device reads it, so the practical stake is nil, but the stored value differs from every prior
record of it. One press of `−` restores it.

`Battery Type` (`0x88`) was deliberately not pressed: single region, `+1` per release, no
decrement, blank readout, and it relays to `1FFC4`, a charger config command. Zero `19FFC49C`
frames confirm it was untouched.

---

# PC.31 — `Battery Type` is an ENUMERATED CYCLE, not a delta (2026-08-24)

**Captured:** 2026-08-24, `can1` from the bus host, `captures/pc31-battery-type-2026-08-24.log`
**Method:** six owner presses, then four probe transmits from SA `0xC0` to restore the value,
each verified by a `D3 FF FFFF` read-back. Completes the fourth of PC.23's four relay mappings —
the one PC.30 deliberately skipped.

## Result 1 — the read-back is non-mutating, a third time

`19FED9C0#FF96880FFFFFD3FF` was answered in **0.6 ms** by `195FCE9C#8800000000000000`
(`0x88` = 0) with **zero** `19FFC49C` frames following. PC.23 called a read-back "the safe probe";
that now holds across three sessions and both directions of this page.

## Result 2 — the correction: `+1` is a "next" command over an enumerated set

Six presses, six byte-identical writes `19FED99F#FF96880F0100D2EA` — a `+1` signed adjust, one
emission per release, no repeat, exactly as Finding 51's binding decode says. **But the values the
G12 relayed are not an arithmetic sequence:**

```
+1 -> 19FFC49C#01FFFFFFFFFF01FF   p[6] = 1
+1 -> 19FFC49C#01FFFFFFFFFF02FF   p[6] = 2
+1 -> 19FFC49C#01FFFFFFFFFF03FF   p[6] = 3
+1 -> 19FFC49C#01FFFFFFFFFF0DFF   p[6] = 13     <== not 4
+1 -> 19FFC49C#01FFFFFFFFFF00FF   p[6] = 0      <== wrapped
+1 -> 19FFC49C#01FFFFFFFFFF01FF   p[6] = 1
```

`3 → 13 → 0` is not addition and not a clamp. The G12 holds an **enumerated set of five battery
type codes `{0, 1, 2, 3, 13}`** and the panel's `+1` advances to the next one. Every other control
on this page sends a genuine signed adjust that the G12 applies arithmetically; **this one does
not**, and Finding 51 assumed it did.

**The stored byte is the RV-C code itself, not an index.** Each read-back returned exactly the
value that had just been relayed (`0x88` = 2, 3, 13, 0), so there is no separate index→code
mapping to find — the G12 stores `0x0D` and sends `0x0D`.

*Not verified:* what the five codes denote. `0x0D` is the kind of out-of-sequence value RV-C's
battery-type enumeration uses for Lithium, but this project has no copy of that enumeration and
**no chemistry name is claimed here**. What is certain is the cycle and its membership.

## Result 3 — the restore, which is also the experiment

The value was left at 1 after the owner's presses. Four `+1` transmits from SA `0xC0`, each
followed by a read-back, walked it back through the *predicted* path and every step matched:

| advance | relayed `p[6]` | read-back `0x88` |
|---|---|---|
| 1 | `02` | 2 |
| 2 | `03` | 3 |
| 3 | `0D` | 13 |
| 4 | `00` | **0 — restored** |

Predicting `3 → 13 → 0` before sending, and getting it, is what makes this a cycle model rather
than a description of six samples. The coach is back to its original value.

## Result 4 — Finding 52 rule #55 confirmed, completing PC.23's four

`0x88` → `1FFC4`, instance 1, `p[6] ← <stored 0x88>`, lag **4-12 ms**. The rule template
`01FFFFFFFFFF__FF` with mask `40` reproduces all ten frames byte-for-byte. With PC.30's three,
every one of the four relays PC.23 established by injection has now been driven by a real press.

## Why this was safe, stated for the next person

`1FFC4` addresses **instance 1, which nothing on this coach claims**, so ten battery-type commands
went on the wire and no device acted on any of them. The only state that changed was one byte in
the G12, and the panel's own control provided the means to change it back. A page whose commands
address an unclaimed instance is the cheapest possible place to run an experiment like this — and
the reason to check the instance *before* pressing, not after.

## Standing note

The `Battery Type` readout on screen stays **blank** throughout: var 125's source `0x1FFC6` is
sent by nobody. So all sixteen state changes here were invisible on the panel — the same
write-works/read-dead split PC.30 Result 5 recorded for `Low Batt. Cutout`, and the second
instance of it on one page.

---

# PC.32 — `Shore Brkr Size`'s `−` button transmits NOTHING (2026-08-24)

**Captured:** 2026-08-24, `can1`, `captures/pc32-shore-brkr-minus-2026-08-24.log`
**Method:** owner presses with a positive control in the same window, then one probe transmit to
restore. **A new firmware defect**, found by accident while tidying up PC.30's leftover value.

## The observation

Across three sessions the owner pressed `−` on `Shore Brkr Size` **five times**, at stored values
0, 5, 35, 35 and 35. **Not one produced a frame.** In the same windows, `+` transmitted every
time.

The decisive window is this one, where the owner pressed `−`, `+`, `−`, `+`:

```
(…799.1547) 19FED99F#FF96990F0500D2FF     +5   -> 0x99 = 40
(…806.3658) 19FED99F#FF96990F0500D2FF     +5   -> 0x99 = 45
```

Four presses, **two frames, both `+5`**. The `+` presses bracket the `−` presses seconds apart on
the same block, so the page was responsive and the finger was in the right place. The panel simply
does not transmit on `−`.

## Two hypotheses killed on the way

**A range clamp at the bottom.** Attractive at first: the first two silences were at stored 0 and
stored 5, and the owner's own domain point is that a 0 A shore breaker is meaningless while RV-park
pedestals are 15/20/30/50 A — so a floor of 5 in steps of 5 would be a sensible design. **Refuted
by pressing `−` at 35**, where any such floor has ample headroom. Silent there too.

**A G12-side refusal of negative adjusts.** Refuted by the restore: `19FED9C0#FF96990FD3FFD2EA`
(−45) from SA `0xC0` was applied immediately and read back as **0**. The G12 takes a negative
delta on `0x99` without complaint.

So the fault is **panel-side, in the `−` half of object 929** — not the bus, not the G12, not a
value range.

## Why it matters beyond one control

This is the **second** defect on the same control. Finding 53 established that Firefly shipped the
**non-relaying** variant here — object 929 on selector `0x99`, which the G12 stores and forwards to
nobody — while the relaying twin (object 930 on `0x90`, rule #56) sits unattached in the file.
A control that relays nowhere *and* whose `−` never fires looks less like two coincidences and
more like one that was never finished. That is consistent with the page itself being **orphaned**
(Finding 53): nothing reachable opened it, so nothing would have exercised either half.

## The file dig: the `−` IS wired. Every link is present and correct

Asked to verify in code that the button is not wired up. **It is wired up**, and the premise is
refuted at all four links of the chain:

| link | object 929 `−` | verdict |
|---|---|---|
| hit region | `wtype 12`, `(565,165)-(640,245)`, 75×80 | present, and **shape-identical** to the working `−`s on 927/931/928 |
| overlap | the page's 16 touch regions, all coordinates walked | **nothing covers it** — the box is clean, unoccupied screen |
| touch id | **199**, against a group count of **233** | a valid group id, dispatches through the LCD phase machine |
| group | **199**, three emissions | exists |
| binding | entry **630**: `d9 fe 05 06 0f 99 96 ff ff d2 ff fb` | correct DGN `1FED9`, correct selector `0x99`, payload `ff fb` = **−5** |

Binding 630 is byte-for-byte its `+` twin (633, `… d2 00 05`) apart from the value. **The frame the
panel fails to send is fully described in the file.**

## The one anomaly, and it is not proof

Group 199's header differs from its own `+` twin in exactly **two bytes**:

```
group 200  (+)   10 00 04 00 79 02 03 00
group 199  (−)   11 80 04 00 76 02 03 00
           ^^ ^^
```

Per `CONTROL-BEHAVIOR.md` §5 that is `variant_count = 1` (byte 0 low nibble, the per-control
variant counter's wrap value) and `cycles_variant = False` (byte 1 bits 6-7 == `0x80`, which
**disables advancing that counter** on press/release). Every working half on this page is
`10 00`.

*The mechanism this suggests*, and it would explain the silence exactly: the LCD firmware matches
each binding's byte-2 high nibble against a per-control counter at `0x20006E6C + index*16 + 0x0A`
(`cmp sl,r2,lsr #4` @`0x0021B946`). All three of group 199's bindings sit at **variant 0**. If that
counter holds anything but 0, no binding matches and nothing is emitted — and because
`cycles_variant` is false, **pressing the control cannot move the counter back**; only an external
setter could.

**Why this stays a hypothesis.** The counter is runtime state and is not in the file. **14 groups**
share the broad `11 80` shape (variant_count > 0 with only variant-0 bindings) and nothing is
known about the other 13. Narrowing to the exact `11 80 04 00` leaves only group **184** — which
**no object references**, so it cannot serve as a control either.

## The LCD dig — the mechanism is now complete, the cause is not

Done 2026-08-24 (`CONTROL-BEHAVIOR.md` **§5a**). It changes what the two anomalous bytes *mean*.

**Byte 1 `0x80` is not damage — it is a design switch.** Every write to the per-control variant
counter is gated on group byte 1 bits 6-7 **== `0x80`** (`0x21B5FE`-`0x21B604`, and again at
`0x21B650`-`0x21B656`), while press-cycling *skips* those same groups. The two are mutually
exclusive by construction: `0x00` = "the press cycles this control's variant", `0x80` = "something
outside chooses it". All seven pool references to the state table were enumerated, so the three
write sites are the complete set.

So group 199 is an **externally-selected** control and group 200 is a press-cycled one. Firefly
authored that difference deliberately. And the setter has exactly **one** caller in the whole
image — `0x21B002` in `FUN_0021AB88` — which takes the variant from a runtime record field.

**The shape is not fatal on its own.** Of the 14 groups with it, four are referenced by an object:
**33** (objects 408/409/862), **86** (obj 103), **87** (obj 105) and **199** (obj 929). If those
other three work on a coach, the shape is fine and 199's problem is that nothing ever selects
*its* variant.

**What is still not established, stated plainly:** the counter's power-on value. The state table is
SRAM at `0x20006E6C`; if it is BSS-zeroed, a variant-0-only group fires normally until something
sets it, and the silence would need another cause. **One such cause has not been excluded:**
`[state+0x09]`, the phase byte the sender bails on at `0x21B8CC`, would produce identical silence
by an unrelated route. The variant model explains every observation but is **not proven to be the
one operating here**.

## Object 929's `−` is the only LIVE instance of this configuration in the build

A press test was proposed against the other three object-referenced groups in this class and
**it is not possible** — their objects are unattached. Every object was scanned for a parent of
either kind (container child, or type-6 switch candidate range):

| group | object | parent |
|---|---|---|
| 33 | 408, 409 `'Patio\nAwning'` | **none of any kind** |
| 86 | 103 | **none** |
| 87 | 105 | **none** |
| 33 | 862 | **none** |
| **199** | **929 `'Shore\nBrkr Size'`** | **child of object 17** |

They are the Finding 15 class: authored template objects nothing can display. So **object 929's
`−` is the only control in 13.6 that is simultaneously (a) externally variant-selected, (b)
carrying bindings for variant 0 only, and (c) reachable on a screen** — and it only became
reachable at all because v8 gave the page its first route.

**And the pattern done correctly exists, one group away.** Group **34** — the awning control's
other half — is also externally selected (`12 80 0c 80`), but declares `variant_count = 2` and
carries **seven bindings spanning variants 0, 1 and 2**. Whatever its counter holds, something
fires. Groups 33, 86, 87 and 199 all declare a variant range **wider than their bindings cover**,
which is the actual defect shape; three of the four sit on objects nobody can reach, and the
fourth is this button.

*Byte 2 is a red herring, checked and discarded:* `0x0c` vs `0x04` decodes as the three 2-bit
repeat-rate selectors (1000 ms vs 250 ms held-repeat), not a variant field.

## Where this leaves it

The circumstantial case is strong and coherent — an incomplete control on an orphaned page, which
is exactly the class of thing that ships unnoticed because nothing can reach it. **It is still not
proof.** The counter's power-on value is SRAM state, and the `[state+0x09]` phase-byte alternative
at `0x21B8CC` remains unexcluded. What would settle it is a live read of
`0x20006E6C + 199*16 + 0x0A`, which needs a debugger on the panel, not the file.

*Recorded trap:* `records.object_records` takes a **GuiData-relative offset plus `gs`**, not an
object index — `Screen.table[i]` is the offset and the call is `object_records(data, gs + off)`.
Two attempts in session handed it an index and got derailed decodes that looked plausible enough
to quote (115/218/145 records for 7-record objects, geometry of 31503). Nothing was concluded from
them.

## Coach state

`0x99` is back to **0**, its PC.23 value. Everything PC.30 and PC.31 disturbed is restored.

---

# PC.33 — G12 rule record 59 does NOT fire on an option toggle (2026-08-24)

`captures/pc33-bathcargo-options-2026-08-24.log` — 13,087 frames, 204.6 s, `can1`, panel and G12
both live. The owner toggled the `Cargo / Bath Light` and `Bath Light` OPTIONS rows six times,
covering both selectors in both directions.

**This is the experiment `CLAUDE.md`'s owner work item 3 specified, run as specified. The answer
is negative.**

## The result

**Zero `19FED99C` frames.** Every one of the 23 `0x1FED9` frames in the capture is sourced by the
**panel** (`19FED99F`); the G12 sourced none. Record 59 — `0x102984`,
`d9 fe c1 14 ff e3 96 ff 00 cf 00 e4`, the `1FED9` transmit template whose payload substitutes the
live values of selectors `0xE3` and `0xE4` — did not transmit. **Function `0xCF` remains
undefined**, and the G12's never-having-been-observed-sourcing-`1FED9` record now stands at twelve
captures rather than eleven.

**Scope, stated honestly.** This refutes one trigger hypothesis — the obvious one, that the rule
fires when the options whose selectors it names change. It does **not** prove the rule is dead
code. Some other trigger may exist, and nothing here bounds what it could be. What can be said is
that the cheapest and most plausible candidate has been tried and eliminated.

A whole-capture scan for frames correlated with the taps found **nothing beyond the two DGNs
below** — no CAN id appears only within ±0.5 s of a tap. So the panel's config write and its
paired dimmer command are the complete tap-time traffic.

## The taps, and what each one emitted

Six taps, each a `0x1FED9` write burst from the panel followed by read-backs (`D3 FF`):

| t (rel) | frames | meaning |
|---|---|---|
| 897.390 | `E3 := 0` | Cargo off |
| 922.035 | `E4 := 1`, `E3 := 0` | Bath on — **and Cargo forced off** |
| 932.097 | `E4 := 0` | Bath off |
| 939.628 | `E3 := 1`, `E4 := 0` | Cargo on — **and Bath forced off** |
| 953.026 | `E4 := 1`, `E3 := 0` | Bath on — Cargo forced off |
| 963.314 | `E3 := 1`, `E4 := 0` | Cargo on — Bath forced off |

The owner's narration matches frame for frame, including the two they called out unprompted
(*"bath light on which toggles cargo off"*, *"back to cargo which toggles bath light off"*).

**`E3` is `Cargo / Bath Light`, `E4` is `Bath Light`** — the `answer_key.OPTIONS` names, confirmed
here by a route that never read them: the owner said which control they pressed, and the selector
that went to 1 is the one their words name, six times out of six.

**`E3`/`E4` are a mutually exclusive pair, enforced panel-side** — a third instance of the
Finding 50 mechanism (a tap handler whose template list is longer than one). **And the deselect
asymmetry holds here too:** a plain deselect (`E3 := 0` at 897, `E4 := 0` at 932) touches neither
partner, so **both-off is a reachable state** and the coach sat in it for 25 s. In that state the
Bath control is bound to no channel at all.

## The `0x1FEDB` OFF fires — the channel binding, observed

Beside each write, and 0.6–2.0 ms after it, the panel sends a `DC_DIMMER_COMMAND_2` **command 3
(OFF)**:

```
897.390391  19FED99F#FF96E30F0000D1EA     E3 := 0
897.390962  19FEDB9F#19FF0003FF00FFFF     -> channel 0x19 = 25 OFF
932.097427  19FED99F#FF96E40F0000D1EA     E4 := 0
932.097992  19FEDB9F#1AFF0003FF00FFFF     -> channel 0x1A = 26 OFF
```

All six taps do it, and the rule is exact: **the OFF names the channel of whichever selector is
being set to 0** — `E3 → 25`, `E4 → 26`, three times each, never once crossed. Where a tap writes
both selectors, the OFF follows the `:= 0` and not the `:= 1`.

This is `MCF-FORMAT.md` §8.3 row 2's cited-but-unimplemented candidate **executing on the wire**.
That row derived `E3 → 25` / `E4 → 26` from a `0x1FEDB` command-3 template sitting *beside* the
`0x1FED9` write in each tap-handler arm, and graded it `inferred-from-code` because it assumed the
arm runs and assumed the standing instance == channel identity. Both assumptions are now
`observed-on-hardware`, and the derived channels reproduce `answer_key.OPTION_CHANNEL_BINDING`
exactly.

**It does not close the strict `xfail`.** `test_bath_light_pair_binds_to_channels_25_and_26` fails
because no extractor reads the binding out of the file — that is a tooling gap, and a capture
cannot fill it. What changed is that writing the extractor is now a transcription of a confirmed
mechanism rather than a bet on an unexecuted code path.

*Why the OFF is there at all, and it is good design:* the pair **rebinds one Bath control between
two channels**. Switching the binding without turning the old channel off would leave a light on
with nothing on screen still pointing at it.

## Coach state

**Restored, and by accident rather than by design.** The capture opened at `E3 = 1`, `E4 = 0`
(G12 sweep at t=866) and closed at `E3 = 1`, `E4 = 0` (t=963) — the owner's own last tap happened
to land the pair back on its starting values, so nothing needs undoing. Worth noting only because
it was luck: six toggles of a two-state mutually-exclusive pair return to the start on even counts,
and this run had six.

---

# PC.34 — the whole OPTIONS sweep, and the offset→selector join seen on the wire (2026-08-24)

`captures/pc34-all-options-2026-08-24.log` — 29,698 frames, 305 s, `can1`. The owner toggled
**every one of 13.6's six option rows**, each on and back off, narrating as they went, then
exercised the CLIMATE mode buttons. All six selectors ended at their opening values:

| selector | row | open | close |
|---|---|---|---|
| `0xEB` | Heat Pump | 0 | 0 |
| `0xE5` | Bunk Accent | 1 | 1 |
| `0xE9` | Bath Fan | 0 | 0 |
| `0xE3` | Cargo / Bath Light | 1 | 1 |
| `0xE4` | Bath Light | 0 | 0 |
| `0xEC` | Black Tank | 1 | 1 |

## The headline: an option tap emits its config-buffer OFFSET and its SELECTOR, 0.5 ms apart

```
613500.969673  19EE009F#110000FFFFFFFFFF     cfg offset 0x11 := 0
613500.970223  19FED99F#FF96E50F0000D1EA     selector  0xE5 := 0     (Bunk Accent off)

613518.631183  19EE009F#0D0001FFFFFFFFFF     cfg offset 0x0D := 1
613518.631722  19FED99F#FF96E90F0100D1EA     selector  0xE9 := 1     (Bath Fan on)
```

Every tap does it, and the two frames carry the **same value**. Reading the pairs off the capture
gives the complete offset→selector map for this coach:

| cfg offset | selector | row |
|---|---|---|
| `0x0A` | `0xEB` | Heat Pump |
| `0x0D` | `0xE9` | Bath Fan |
| `0x0E` | `0xEC` | Black Tank |
| `0x11` | `0xE5` | Bunk Accent |
| `0x19` | `0xE4` | Bath Light |
| `0x1A` | `0xE3` | Cargo / Bath Light |

**All six match `config.selectors_by_offset` exactly** — the file-derived map, which Finding 23
built out of GuiCode by **code adjacency** (`movs r1,#<off>` … `bl 0x00259100`, then the adjacent
`.rodata` template's selector byte). Nothing in this capture was used to derive that map, and
nothing in that derivation reads a capture.

**Why this matters more than one more confirmation.** Finding 23 calls the `handler → selector`
half *"the weak link"*, grades individual attributions A2/A1/B+, and had wire verification for
**two** rows (`E3`/`E4`, against PC.2). The join now needs no disassembly at all: two frames, half
a millisecond apart, carrying one value. Six rows for six, including every attribution that rested
on the weaker rule.

## Why eleven earlier captures could not have shown this

`0x1EE00` is not a new DGN — Finding 50 already cites `19EE009F#170001…` and `#130001…`, and the
frames appear in ten prior captures. **The pairing is what is new, and it is only visible on a
single-row tap.** In a floorplan *burst* (PC.5 t=345.6) the panel sends **twelve** `1FED9`
selector writes as one block and **five** `1EE00` frames as a separate later block, in a different
order and a shorter list:

```
345.647-.697   19FED99F#FF96{F5,E9,E4,E3,EB,EC,EF,E5,E6,D7,15,F7}...   12 selectors
345.699-.714   19EE009F#{00:=4, 1A:=1, 0E:=0, 11:=1, 13:=0}             5 offsets
```

Nothing pairs there. The two streams also mean different things: **`1FED9` writes every selector
in the profile, `1EE00` reports only the offsets whose value actually changed.** A burst is the
worst case for this analysis and a deliberate single tap is the best, which is why the material
sat unexploited in the corpus for twelve days.

**They are not 1:1 in the other direction either.** The Black Tank tap bounced — three `1EE00`
frames in 15 ms (`0E := 0`, `0E := 1`, `0E := 0`) against a **single** `1FED9`:

```
613531.760154  19EE009F#0E0000     613531.761271  19FED99F#FF96EC0F0000D1EA
613531.763597  19EE009F#0E0001     (no selector write)
613531.775056  19EE009F#0E0000     (no selector write)
```

So `1EE00` tracks the panel's internal config buffer and `1FED9` is the outbound write, and the
buffer can move without a write following it.

**Payload:** byte 0 is the offset, bytes 1-2 the value. **Byte order is undetermined** — byte 1 is
zero in every frame ever captured and no observed value exceeds `0x0A`, so BE u16 and "byte 2 is
the value" fit equally. Do not assume.

## The `1FEF9` companion — Finding 23's adjacency generalises past the light pair

The Heat Pump tap emits a **THERMOSTAT_COMMAND_1** beside its config write, in **both**
directions:

```
613276.142611  19FED99F#FF96EB0F0100D1EA    EB := 1
613276.146230  19FEF99F#000000FFFFFFFFFF    thermostat instance 0, operating mode 0 = OFF
613450.717532  19FED99F#FF96EB0F0000D1EA    EB := 0
613450.720238  19FEF99F#000000FFFFFFFFFF    the same OFF again
```

This is the same shape as the `E3`/`E4` rows' `1FEDB` command-3 OFF (PC.33): **an option tap turns
off the device it is reconfiguring, and the DGN follows the device class** — dimmer for lights,
thermostat for the heat pump. Finding 23 established the adjacency for the light pair only; a
second device class doing it makes it a rule of the tap handlers rather than a quirk of one row.

**Bunk Accent and Bath Fan emit no companion at all** — config write and read-back, nothing else.
So the companion is per-row, not universal, and its absence is as much a fact about those rows as
its presence is about Heat Pump.

## CLIMATE, and the thermostat mode enumeration

With `EB = 1` the CLIMATE mode column **gains two entries**, `Heat Pump` and `Auto` (owner photo).
So this option *reveals* controls — the mirror image of the `E5`/`EC` `hides_control` behaviour
that is still unexplained, and the first concrete example of the reveal direction.

Narrated mode taps, byte 1 low nibble:

| frame | nibble | button |
|---|---|---|
| `19FEF99F#00C2…` | 2 | Heat Pump |
| `19FEF99F#00C3…` | 3 | Auto |
| `19FEF99F#00F1…` | 1 | Cool |

0 off / 1 cool / 2 heat / 3 auto, matching the RV-C standard. **Three unnarrated frames are left
uninterpreted** (`00C0` at 436 and 491, `03C0` at 481 — the last on **instance 3**, a second
thermostat, otherwise unexplained). `0xC2`/`0xC3`/`0xC0` set bits 6-7; `0xF1` sets bits 4-7. Not
decoded.

**The Auto tap was acted on, 3.0 s later, by the G12** — a positive control the owner predicted
from the room (setpoint 71 °F against 94 °F indicated):

```
613392.919312  19FEDB9C#0BFF0003FF00FFFF   ch 11 OFF
613392.919884  19FEDB9C#0CFFC801FF00FFFF   ch 12 ON  level 200
613392.921032  19FEDB9C#2BFFC801FF00FFFF   ch 43 ON
613392.921607  19FEDB9C#09FFC801FF00FFFF   ch  9 ON   (A/C COOL)
613392.934000  19FEDB9C#0BFFC801FF00FFFF   ch 11 ON
613392.934574  19FEDB9C#0CFF0003FF00FFFF   ch 12 OFF
```

## A defect raised and withdrawn in the same session

The Heat Pump row was tapped **"on, off, on"** and produced **one** `EB := 1`; `EB` then held 1
across 34 G12 sweep reports. That is the exact signature of PC.32's silent `−`, and it was raised
as a possible third instance.

**It is not one.** A later deliberate single tap emitted `EB := 0` normally, so the OFF direction
transmits and the two missing taps simply never registered. Recorded because the wrong reading was
live for several minutes and the end state could not distinguish it: *"on, off, on"* and one *"on"*
both land on `EB = 1`. **What separated them was a deliberate, narrated, single press** — the same
control PC.32 needed and the reason that defect took three sessions to pin.

## Record 59 — the negative extends

**Zero `19FED99C` frames**, across six more taps on four further selectors (`EB`, `E5`, `E9`,
`EC`), a CLIMATE mode sequence, and an A/C start. PC.33's negative was about `E3`/`E4`; it now
covers **every option row this coach has**. The G12's never-observed-sourcing-`1FED9` record
stands at thirteen captures.

---

# PC.35 — `0xCC`/`0xCD`/`0xCE` are the TANK LEVEL THRESHOLDS (2026-08-24)

`captures/pc35-tank-thresholds-2026-08-24.log` — 11,978 frames, `can1`. The owner's hypothesis,
tested on the panel and confirmed on the wire the same minute.

## The prediction, and it held exactly

`G12-CODE.md` had just decoded the table-`0x67` block at `+0x1858` as reading config selectors
`0xCC`/`0xCD`/`0xCE` and comparing them against a register, and had found GuiCode templates giving
each a `±1000` step and a restore-to-default of 54000 / 40000 / 25000 — **without being able to say
what they were.** The owner recognised the numbers as tank thresholds and went to the screen. The
prediction on the wire was `19FED99F#FF96CC0F E803 D2EA`:

```
324.797762  19FED99F#FF96CC0FE803D2EA    selector 0xCC, +1000
324.798325  19FED99F#FF96CC0FFFFFD3FF    read-back
324.799222  195FCE9C#CC00D8D6…           G12: 54000 -> 55000   (+1.5 ms)
```

Three `+` presses walked it 54000 → 55000 → 56000 → 57000; three `−` presses returned it to
**54000**, its default. **Nothing is left changed on the coach.**

## What the screen shows, and what it settles

**`PROBE TANK DIAGNOSTICS`** (owner photo). Four tanks with a live **`ADC Raw Value`** readout, and
a threshold ladder down the right:

```
      0%
  54000   <- 0xCC
     33%
  40000   <- 0xCD
     67%
  25000   <- 0xCE
    100%          [Reset Default Thresholds]
```

| selector | cell | value | what it is |
|---|---|---|---|
| `0xCC` | `+0x198` | 54000 | the **0% / 33%** boundary |
| `0xCD` | `+0x19A` | 40000 | the **33% / 67%** boundary |
| `0xCE` | `+0x19C` | 25000 | the **67% / 100%** boundary |

They are **raw 16-bit ADC counts, and higher means emptier** — the ladder descends as the level
rises. That is why the step is 1000 and why the values are in the tens of thousands; every unit
guess floated beforehand (volts, seconds, amps) was wrong, and none of them was ever supported by
more than plausibility.

**The decode predicts the display exactly.** The photo's four raw values are FRESH **65520**, GREY
**65515**, GREY2 **57412**, BLACK **65520** — every one above the 54000 top threshold, so every
tank buckets to **0%**, which is what all four gauges read. This also explains PC.25's stray
observation that *"the four resolution-3 tanks read 0 %"* mechanically for the first time: they are
not broken and they are not empty-by-coincidence, they are above the first threshold.

**`Reset Default Thresholds` is identified without pressing it.** The three `d1 ea` absolute-set
templates at file `0x028D5D` / `0x028D69` / `0x028D75` carry exactly 54000 / 40000 / 25000.

## The block is live code, and that retires a standing "open"

`G12-CODE.md` listed `+0x1858`/`+0x192A`/`+0x1948` as *"unreached code … either dead code the
generator emitted, or reached by a control-flow form the walker does not model. Open."* It is the
**tank level bucketing ladder**: `cmp r0,r7 / bhi` against each threshold in turn, unsigned, which
is exactly right for a 16-bit ADC compare. The recursive-descent walker simply cannot reach it.

## A difference from PC.33/PC.34 worth carrying

**No `19EE009F` frame accompanied any of these six presses.** Every OPTIONS toggle emits the
config-buffer offset on `0x1EE00` beside its `0x1FED9` selector write — that pairing is what made
the offset→selector join directly observable. A **± adjust control emits only the selector.**

So PC.34's technique is specific to **option rows**, not to adjust controls. Anyone repeating it on
a threshold screen will see no `1EE00` and should not conclude the mechanism has changed.

---

# PC.36 — SERVICE MODE: a third config-write shape, and a new DGN instance (2026-08-24)

`captures/pc36-service-mode-2026-08-24.log` — 12,050 frames, `can1`. The owner asked whether
enabling and disabling Service Mode puts anything on the wire. It does, and not what was predicted.

## Two complete cycles, and the coach ends where it started

```
889.703466  19EE009F#120001FFFFFFFFFF    cfg offset 0x12 := 1   ENABLE
889.704026  19FBDA9F#B001FFFFFFFFFFFF    +0.56 ms
915.346462  19EE009F#120000FFFFFFFFFF    cfg offset 0x12 := 0   DISABLE
915.347031  19FBDA9F#B000FFFFFFFFFFFF    +0.57 ms
972.093397 / .093962   the same pair again (ENABLE)
988.826972 / .827541   the same pair again (DISABLE)
```

Owner photos confirm both states — `Service Mode is currently ENABLED` with a **yellow wrench in
the header**, then `… currently DISABLED` with the wrench gone. **Left disabled.**

## Offset `0x12` confirmed — a static trace predicting a live frame

`CONTROL-BEHAVIOR.md` §5 had traced the panel storing **config payload byte `+0x12`** into
`0x2003A528` (`ldrb r1,[r3,#0x12]` @`0x00247C72`, `strb` @`0x00247C76`). The panel writes
**offset `0x12`** on the wire. That is the offset predicted from the disassembly, unchanged.

## But there is NO `0x1FED9` selector write — and that is the point

**No `0x1FED9` frame within 2 s of any of the four toggles**, and **none of the 42 selectors in the
G12's sweep moves across them**. So `0x12` is **not** a selector-addressed value: `config.selectors_by_offset`'s silence
about offset `0x12` was **correct and complete**, not a gap in the extractor.

**Three distinct config-write shapes are now observed, one per control class:**

| control | `0x1EE00` offset | `0x1FED9` selector | example |
|---|---|---|---|
| OPTIONS row | **yes** | **yes** | PC.33/PC.34, `E3`/`E4`/`EB`… |
| ± adjust control | no | **yes** | PC.35, the tank thresholds |
| **Service Mode** | **yes** | **no** | this entry |

*The prediction this entry was written to test was wrong, and specifically so.* It said "enabling
Service Mode should transmit, and the selector it uses is currently unknown — this test names it."
It transmits, and **there is no selector to name**. The premise was that every config value has
one; it does not.

## `0x1FBDA` instance `0xB0` — a Service Mode indicator, never seen before

Panel-sourced (`19FBDA9F`), `payload[1]` = 1 enabled / 0 disabled, **0.56 ms after** each offset
write. It appears in **no earlier capture** — zero frames across all twelve.

This widens Finding 54's picture of `0x1FBDA`. That finding decoded instance `0xA0 + N` as the
**G12's** input pins (source `9C`). `0xB0` is **panel**-sourced (`9F`), as is `0xF9` (ten frames
here, still undecoded). So one DGN carries two unrelated instance families from two devices, and
"`1FBDA` instance means input pin N" is only true for the G12's range.

## The toggle does NOT round-trip through the G12 — refuting a documented claim

`CONTROL-BEHAVIOR.md` §5 concluded *"Service Mode is **bus-delivered**, not set locally"*, reasoning
that the flag's only writer is the received-config-payload unpack.

**Nothing answers the panel's write.** The G12's `1FED8` is periodic at 14 s and its payload is
byte-identical before and after both toggles (`25B11F3006163412`, including the sample 0.9 s after
a disable). No config payload arrives, no selector moves — **and the screen changes anyway**.

So whatever the `+0x12` unpack path is for (a boot-time or requested config load), **the user
toggle does not go through it**. The panel changes its own state and *broadcasts* the change; it
does not wait to be told. "Bus-delivered, not set locally" is refuted as an account of the toggle.

*Why the original reasoning failed, and it is the same trap as defect 0a:* it rested on a
**pool-literal scan** finding exactly one writer of `0x2003A528`. A write through a computed
address never appears as an immediate, so that scan could not have found one. **An argument from
"only one writer" is only as good as the guarantee that a second would have been visible.**

## Safety check, done before the test rather than after

A **third, previously undocumented** reference to `0x2003A528` sits at `0x0024EBC0` — `0x94` bytes
from the CLEAR EEPROM timer at `0x0024EC54`, which is exactly where an un-guarded destructive
action would hide. It is **not** one: the branch swaps an argument from `0` to `1` on two calls
(ids `0xAA` and `0`) and rejoins the common path at `0x0024EBDC`; the 3000 ms arm is reached
identically either way, with no Service Mode gate near it. **Service Mode does not turn CLEAR
EEPROM into a tap.** The 5000 ms arm at `0x0024EBB6` *is* short-circuited, and both known
protected areas are navigations.

## Two settings left changed — by later activity, not by the toggles

*A first draft of this entry said "zero `1FED9` frames in the whole capture". That was wrong, and
the test written to pin it caught it.* The capture runs on for another minute after the last
toggle, and at **t+3053** — 65 s later, with Service Mode already off — the panel emits a burst of
**eight absolute `D1 EA` sets** re-asserting the AGS/AES parameter profile (`2B`, `2C`, `31`, `0C`,
`0D`, `0E`, `0F`, `16`), followed at t+3057 by two `−300` adjusts on `0E`.

Six of the eight matched what the G12 already held. **Two did not**, and the coach is left with the
panel's values:

| selector | before | after | reading |
|---|---|---|---|
| `0x0D` stop-at voltage | 1088 | **1060** | 54.4 V → **53.0 V** (0.05 V/unit) |
| `0x0E` time at stop volts | 900 | **1200** | 15 min → **20 min** (1 s/unit) |

**Nothing else moved** — `EF` stayed 3 (AES Single Output), `E6` 1, `D7` 0, `F5` 8 (WD), `0x15`
23040. So this was **not** a mode change.

**It was the ELECTRICAL page's `Reset to Defaults` button** (owner, immediately after). ~~it reads
as a settings screen pushing the panel's own copy of the profile and finding the G12 had drifted~~
— that guess is withdrawn; the owner said what they pressed and it is a better explanation than
mine, because it predicts the shape as well as the values.

### The burst is a template run, and all eight are in the file

Each of the eight absolute sets matches a stored `D1 EA` template exactly, at a **regular 24-byte
stride** (12-byte set + 12-byte read-back, which is why every set on the wire is followed by a
`D3 FF`):

| selector | value | template |
|---|---|---|
| `0x2B` | 5632 | file `+0x29221` → GuiCode `0x002599A1` |
| `0x2C` | 2048 | `+0x29239` |
| **`0x31`** | **1020** | `+0x29251` → GuiCode **`0x002599D0`** |
| `0x0C` | 60 | `+0x29269` |
| `0x0D` | 1060 | `+0x29281` |
| `0x0E` | 1800 | `+0x29299` |
| `0x0F` | 1800 | `+0x292B1` |
| `0x16` | 115 | `+0x292C9` |

That is exactly the tap-handler shape Findings 42/50 describe — a fixed list of templates copied
out of GuiCode's pool — so **these eight values are the factory defaults for the AGS/AES parameter
profile**, and the button that emits them is decoded.

### It closes `CLAUDE.md`'s owner work item 1 on hardware

That item asks where the **AES "Start At" default voltage** comes from and whether it can be made
51.0 V. `GUICODE.md` **Finding 43** answered it `proven-from-code`: already 51.0 V, from the
template at `0x002599D0` carrying `fc 03` = 1020 × 0.05 V.

**Selector `0x31 := 1020` is in this burst, and its template is that exact address.** So a live
`Reset to Defaults` press put Finding 43's constant on the wire — the same answer by a route that
reads no disassembly at all. `observed-on-hardware`.

### What is left changed, and why it is arguably better than before

`0x0D` was **1088** and the reset restored it to its default **1060** (53.0 V), which is the value
`CLAUDE.md` records for this coach — so the pre-reset 1088 (54.4 V) was the modified one, not this.
`0x0E` is the exception: the reset set it to its default 1800 and two deliberate `−300` presses
took it to **1200** (20 min against a 30 min default). Not reverted — it is the owner's setting to
choose.

---

# PC.37 — why AES never triggers, and the panel's own AES-stop handler (2026-08-24)

`captures/pc37-aes-trigger-attempt-2026-08-24.log` — 89,796 frames, 1,532 s, `can1`. The owner set
out to make the table-`0x67` AES watchdog fire. **It never fired, and the reason is the finding.**

## The attempt

Start-at was walked up past the house battery (53.3 V against 53.15 V), `time at start volts` down
to 10, mode `EF = 3` (AES Single Output), and a load run to drop the voltage. **No start attempt,
ever** — no channel-18 ON, no `19FEDB9C#12…`, no ignition sense.

## The reason: in Single Output mode, channel 18 is a LEVEL, not a pulse

Across all thirteen captures:

| | |
|---|---|
| `1FEDA` status frames for channel 18 showing **ON at level 200** | **96 of 99** |
| channel-18 **ON** commands, from either node, ever | **1** |
| channel-18 **OFF** commands | 7 |
| G12-sourced channel-18 commands (frame-table record 3) | **0** |

**The owner's account of the mechanism, which corrects the reading above** — *"turning on 18
triggers engine start on the AES; it is held on; turning it off stops the engine. This can be
observed from the engine start and stop buttons."* So channel 18 is a **run command held as a
level**: asserting it starts, de-asserting stops. It is not that there is no start *event* — it is
that **AES never automatically produced one** during this session, which is the actual null result.

That reframes the corpus counts rather than explaining them away. The single
`19FEDB9F#12FFC801FF00FFFF` on record is a **start**; the seven OFFs are **stops**; and the line
reading ON in 96 of 99 status frames means the G12 has been asserting "run" throughout, with the
owner's AES hardware disconnected so nothing acted on it.

**And it names a far cheaper test than anything attempted here:** the ELECTRICAL page carries
**engine Start and Stop buttons** that drive this channel directly, so the path can be exercised
with one press instead of waiting on battery voltage, thresholds and timers.

## The panel has its own AES-stop handler, and it duplicates the G12's

Five times in this capture, byte-identically, the panel emitted:

```
19FED99F#FF96010F0000D1EA    selector 0x01 := 0
19FED99F#FF96060F0000D1EA    selector 0x06 := 0
19FED99F#FF96580F0000D1EA    selector 0x58 := 0
19FED99F#FF964F0F0000D1EA    selector 0x4F := 0
19FED99F#FF96B80F0000D1EA    selector 0xB8 := 0
19FEDB9F#12FF0003FF00FFFF    channel 18 OFF
19FED99F#FF969B0F0000D1FF    selector 0x9B := 0
```

**All seven are stored templates** in GuiCode's pool (`+0x28CB1`…`+0x28DF5`), so this is a
Finding 23 tap-handler run, not computed.

Now compare the **G12's** table-`0x67` watchdog block A (`docs/G12-CODE.md`, `+0x0CC4`), which
zeroes cells `+0x02`, `+0x0C`, `+0x9E` then sends record 3. Under `offset = 2 x selector` those are
selectors **`0x01`, `0x06`, `0x4F`** — **three of the six the panel zeroes, plus the same channel-18
OFF.**

**So both devices implement the AES stop, independently.** That is the same shape as the EXT/RET
interlock (`GUICODE.md` Finding 54: GuiData bindings *and* G12 table `0x20`, each unaware of the
other), and it is the best available explanation for why **frame-table record 3 has never fired in
thirteen captures: the panel gets there first.** The G12's watchdog is the fallback for a stop
nobody else performs.

*The panel also zeroes `0x58`, `0xB8`, `0x9B`, which the G12 block does not — the two
implementations overlap, they are not identical.*

## Selector `0x01` — the AES-ENABLED flag — has its OFF template beside the channel-18 OFF

*(This heading read "is the generator-run state" as first written. **PC.38 corrects it**:
Stop clears `0x01`, Enable sets it, so it is an enable flag. `GUICODE.md` Finding 55 says the
`Engine Running` display is *gated on* `0x01 == 1`, which is not the same as the selector being
the run state — that elision is where the error came from.)*

`GUICODE.md` **Finding 55** established that the `Engine Running` display's generator branch is
gated on selector `0x01 == 1`, and that it reads 0 in 910 frames across every capture. Here the
panel writes **`01 := 0`** as the first act of the stop — PC.38 identifies it as the **AES-enabled**
flag, so this burst is the AES *disable*.

Its template is at file `+0x28CD5`; the **channel-18 OFF is at `+0x28CE1`** — the *very next
12-byte record*. That is the **third instance** of the config-write-plus-companion-command adjacency
this project has now seen: `1FEDB` beside `1FED9` for the light options (PC.33), `1FEF9` beside
`1FED9` for Heat Pump (PC.34), and `1FEDB` beside `1FED9` here. **An option or state write turns off
the device it describes, and the two templates are neighbours in the pool.**

## The flash is NOT visible on the bus

***CORRECTED 2026-09-08 — true of the LCD pair, false as a general claim.** The loader's own bus
traffic IS in this capture, 22 s before the address-claim below, and the conclusion drawn at the
end of this section does not hold for the G12 row. See "The G12 network-upload protocol, on the
wire" later in this file.*

The owner flashed stock 13.6 mid-capture. The bus shows **one panel address-claim** (`18EEFF9F`) at
+614 s and **no bulk transfer at all** — the busiest second carries 129 frames against a median of
49. A firmware image would be tens of thousands.

So the panel update is **USB-local**, exactly as `MCF-FORMAT.md` §4.1 says. **This closes a route
proposed earlier the same day:** capturing a flash will *not* yield the G12 bootloader protocol, so
it is not a way to reach the G12 application image at `0x08006000`. That ceiling stands.

## The flash restored the AGS/AES profile to its defaults

After the flash the G12 reported `0x31` = 1020 (51.0 V), `0x0D` = 1060 (53.0 V), `0x0C` = 60,
`0x0E` = 1800 — **exactly the eight-template `Reset to Defaults` values decoded in PC.36**. So the
panel pushes that profile on boot, and G12 config that had been adjusted did **not** survive the
panel flash. *Corrects an assumption stated during PC.36 that the G12's NVM would be untouched by a
panel-side flash.*

## AES + Progressive Inverter coexisted after the flash — v6/v7's interlock removal outliving its firmware

Owner-reported immediately after: the coach came up with **both AES and Progressive Inverter
selected**, a combination stock firmware's interlocks are designed to prevent
(`GUICODE.md` Finding 50 — selecting PI emits `EF := 1`, which deselects AES).

reference build v6/v7 removed those cross-writes (`interlocks.uncouple`). The resulting
*state* lives in G12 NVM, so it **outlived the firmware that permitted it**: flashing stock restored
the interlock logic, and stock then found the coach in a configuration it cannot itself produce.

`CLAUDE.md` warned that the edit "makes the two options *independent*, it does not make them
*consistent*", and that the interlock fires **on selection only**. This is the first hardware
instance of that biting. **Anyone flashing stock over an uncoupled build should expect to re-pick
the option pair by hand.**

## Two corrections to claims made earlier the same day

**`0xBF` DOES appear in PC.28.** It was reported absent from the AES engine run; that came from
grepping `19FBDA9C` and the frames are `19FBDA9F` — **panel**-sourced. Both nodes source instance
`0xBF`, and **`BF 02` has been observed** (three times, from the panel), contrary to the earlier
"state 2 has never been observed". *A negative scoped to one source address is not a negative.*

**Cell `0x0C` is not clamped to 60.** `docs/G12-CODE.md` claimed `+0x14C6` clamps it, so it could
not be set above 60 and stick. In this capture the owner walked it **120 → 310 → 10**, every step
holding. The reading assumed `r3` held the config-struct base in that function without verifying
it, unlike every other site quoted there.

---

# PC.38 — `0xBF` decoded: the engine start/stop indicator (2026-08-24)

`captures/pc38-engine-buttons-2026-08-24.log` — 94,348 frames, `can1`. The owner pressed the
ELECTRICAL page's engine **Stop / Start / Stop** buttons. One capture settles what `0xBF` is, what
channel 18 does, and what the AES enable/disable controls write — and leaves one defect open.

## `BF 02` = start, `BF 01` = stop, `BF 00` = the timed end

```
+52.485  19FBDA9F#BF01…  +  ch18 OFF  +  0x01 := 0      STOP
+57.898  19FBDA9F#BF02…  +  ch18 ON @200                START
+71.684  19FBDA9F#BF01…  +  ch18 OFF  +  0x01 := 0      STOP
```

**Owner-verified on the panel: `G12 OUTPUTS` showed pin 18 ON at the Start press.** So the chain is
confirmed end to end — finger, panel frame, G12 status, physical output.

This gives meaning to the state machine decoded from the table-`0x67` blob the same day
(`docs/G12-CODE.md`): trigger cell `0x7D` value 1 → one frame, value 2 → another, timer expiry →
`BF 00`. The mechanism was decoded from the file with **no idea what the event was**; it is the
engine start/stop indicator.

**`BF 02` is no longer unobserved.** It had been recorded as a genuine prediction sitting in the
file — a state the code could emit that no capture contained. It is the *start* indicator, and it
took a button press to produce one.

## The pulse widths are the tick-rate dataset

| pulse | width |
|---|---|
| `BF 01` @ +52.485 | **484 ms** |
| `BF 02` @ +57.898 | **410 ms** |
| `BF 01` @ +71.684 | **474 ms** |
| PC.24's pair | 714 ms |

The timer is armed to **1**, so at 1 Hz with arbitrary phase every width should fall in (0, 1000]
ms. **Four samples, all under a second, none over.** At 10 Hz they would all be ≤100 ms. This
upgrades the `~1 Hz` tick from a single measurement to a distribution.

## Two cadences in the same device

Channel 18's `1FEDA` status repeats every **2.000 s** while the output is on — distinct from the
**1.000 s** input refresh PC.24 measured. Do not generalise one to the other.

## The AES enable/disable controls

**Disable (the `Stop` button — owner: *"pressing stop disables AES"*)** emits a stored seven-template
run, byte-identical every time, and it is the same burst that appeared five times unattributed in
PC.37:

```
01:=0  06:=0  58:=0  4F:=0  B8:=0   +  channel 18 OFF  +  9B:=0
```

**Enable** emits `00:=1`, twelve AGS-parameter read-backs (`2F 2E 33 2B 2C 31 0C 0D 0E 0F 16 15`),
then `01:=1` and `9B:=1`.

**So `0x01` is the AES-ENABLED flag.** *This corrects a label used earlier the same day, including
in PC.37's first draft, which called it the generator-run state.* The error came from reading
`GUICODE.md` Finding 55 — which says the `Engine Running` display's generator branch is **gated on**
`0x01 == 1` — as though the selector *were* the run state. Enable sets it, Stop clears it; that is
an enable flag. `0x9B` tracks it exactly, three seconds behind.

A subsequent `Stop` while already disabled emits only the short `01:=0` form, which is why the
first press of a session looks different from the rest.

## OPEN: AES does not auto-start with every readable condition met

**The reason this session was run, and it is unresolved.** For the first 50 s of this capture:

| gate | value |
|---|---|
| `0xEF` AGS/AES mode | **3** (AES Single Output) |
| `0x01` AES enabled | **1** |
| house battery | **below 52.70 V continuously from +0 s to +107.5 s** |
| `0x31` start-at | 52.70 V |
| `0x0C` time at start volts | **30 s** |
| quiet time (`0x2B`/`0x2C`) | 02:00–06:00, and the clock read ~20:45 |

Every gate readable from the bus was satisfied for **50 seconds**, against a 30-second requirement,
and **no start command was sent**. `0xEF` was never written during the capture, so nothing changed
the mode underneath it.

**Two explanations were offered during the session and both were killed by the data** — first that
channel 18 has no start event (it does; asserting the level *is* the start), then that the panel
believed the engine was already running (it did not; `0x01` is an enable flag). No third is offered
here.

*Where to look next, and it is the ceiling this project already hit:* the G12's AES watchdog reads
cell **`+0x50`** — selector `0x28` — which **nothing in the MCF ever writes** and which has never
appeared in any `0x15FCE` sweep. Its writer is in the G12 application at `0x08006000`, outside the
container. If a gate is holding AES off, that is where it would be.

*The cleanest untried experiment:* raise start-at above the resting battery voltage and then **do
not touch the panel for two minutes**. Every attempt so far had a Stop, a Start or a settings change
inside the 30-second window.


## PC.39 — AES auto-start, CAPTURED. Defect 0c is closed (2026-08-25)

`captures/pc39-low-volts-trigger-2026-08-25.log`, 10,340 frames. **The G12 started the engine on
its own for the first time in this project**, and the thing that unblocked it was enabling the
`Low Volts` trigger on the AGS page. Times below are relative to the capture's first `0x15FCE`
sweep frame.

| t | frame | what |
|---|---|---|
| 21.2 | `19FED99F#FF96EF0F0100D1EA` + `D7:=0, E6:=1, 15:=0x2801, F7:=0x3C` | switch to **AGS** — Finding 50's handler, frame for frame |
| 25.0 | sweep | the G12 re-derives the **12 V** profile: `0x31` 1054 → **244** (12.20 V), `0x0A` → 247 |
| 26.4 / 31.5 / 35.3 | `19FED99F#FF962F0F` `01` / `00` / `01` | `Low Volts` **on, off, on** — left enabled |
| 57.4 | `EF:=3`, `E6:=0`, … | back to **AES Single Output**; profile flips to the 48 V set, `0x31` → 1020 |
| 63–75 | `0x31 += 2` ×19, then `0x0C` 50→10 | owner raises start-at to **1058** (52.90 V), shortens the timer to **10 s** |
| 89.5 | sweep | `0x01` (AES enable) → **1** |
| ~105 | sweep | battery `0x0A` **1057** (52.85 V) — first sample **below** 1058 |
| **117.7** | **`19FEDB9C#12FFC801FF00FFFF`** | **the G12 commands channel 18 ON, level 200** |
| +1.2 ms | `19FEDB9C#14FF0003FF00FFFF` | channel **20 = GENERATOR STOP** driven off |
| +6.3 ms | `19FEDA9C#12FFC8FCFF010400` | channel 18 status: on, last command 1 |
| +10.6 ms | `19FBDA9C#BF02000000000000` | the start indicator |
| +353 ms | `19FBDA9C#BF00…` | the indicator's timed end |
| 145.2 | `12FF0003` from `9F`, `0x01` → 0 | owner stops it from the panel |

**The source address is the whole point.** Every previous channel-18 ON in this corpus was
`19FEDB9**F**` — the panel, i.e. the `Start` button's GuiData binding. This one is
`19FEDB9**C**`, the **G12**, unprompted, with nobody at the panel. That is the auto-start.

### It confirms the `G12-CODE.md` arming mechanism, and the timing is quantitative

The blob writes `config[0x0B] = config[0x2F] ? config[0x31] : 0`. `0x2F` had been **0 in all 28
prior captures**; setting it to 1 is the only change made to the coach, and the auto-start that
never fired across a whole evening of attempts fired **within one battery sag**. The delay is
right too: the battery crossed the threshold somewhere in `(100, 105]` s (the sweep samples each
selector about every 5 s) and the command went out at **117.7 s**, against `time at start volts`
= **10 s**.

### The G12 sourced `0x1FED9` for the first time — and it is not record 59

Two frames, 2.3 ms and 3.4 ms after the start command:

```
398.253212  19FEDB9C#12FFC801FF00FFFF   channel 18 ON, level 200
398.254374  19FEDB9C#14FF0003FF00FFFF   channel 20 = GENERATOR STOP, off
398.255511  19FED99C#FF96790F0100D1FF   config 0x79 := 1
398.256641  19FED99C#FF967D0F0200D1FF   config 0x7D := 2
398.263775  19FBDA9C#BF02000000000000   the start indicator
```

`tests/test_g12rules.py` carried an assertion that the G12 has never sourced `1FED9`, put there so
that if it ever did, record 59 would be caught. **It fired, and the answer is no**: these are plain
`D1` sets of `0x79` and `0x7D`, and function `0xCF` is still absent from every capture. Record 59
remains unfired; the guard now pins these two frames instead.

**What they do settle is `0x7D`.** `G12-CODE.md` decoded the `0xBF` indicator as "trigger cell
`0x7D`, value 1 or 2 selects the start frame", with the writer unfindable because nothing in the
blob writes it. Here the **application** writes `0x7D := 2` and the blob emits `BF 02` **7 ms
later** — the decode confirmed, and the event named: it is the AES auto-start. `0x79 := 1` beside
it is undecoded.

### Two things seen for the first time

**`19FBDA9C#BF02` had never appeared** — `G12-CODE.md` recorded state 2 as emitted from the frame
table but never observed. It is the **start** indicator, and it took an auto-start to produce one,
because a panel-driven start sends `19FBDA9**F**#BF02` from the panel instead.

**The ON command is not in the frame table.** Records 0-13 hold `BF 01/02/00`, channel 18 **OFF**,
a `1FED9` read and the light/awning templates — there is **no channel-18 ON record**, and
`19FEDB9C#12FFC801FF00FFFF` still went out. So the `0x67` blob is the configuration layer and the
**application** at `0x08006000` owns the command, exactly as the arming write-up assumed. The
indicator beside it *is* a frame-table record, so the application calls into blob code for that.

### And it refutes one of the three predictions made beforehand

`GUICODE.md` Finding 57 predicted the button would write `1` on every press, because its variant
is externally selected from `config[0x2A]` and `0x2A` never appears on the bus. **It alternated —
`1, 0, 1` — exactly as a working toggle should**, and `0x2A` is still absent from this capture
(zero frames, against 1,427 sweep frames from the G12). So the variant *is* tracking the live
value and the `[r4,#0x54] = selector 0x2A` attribution is wrong. See Finding 57's retraction.

*The prediction was worth making anyway*: it was cheap, it was falsifiable, and being wrong about
it cost one press and located a specific error. The two predictions that did hold — that the
button transmits, and that `0x2F := 1` is what arms the start — are the ones the experiment was
for.

### The floorplan gate at `+0x18C8` splits on FAMILY, and that was missed

The test just above the arming block admits floorplans **{1, 2, 3, 6, 10}** — and the floorplan
table reads `RT=1, RA=2, RD=3, CB=6, RY=10`, i.e. **exactly the five `generator` floorplans**, with
`SY=4, TB=5, WA=7, WD=8, WT=9` — the five `li-aes` ones — taking the other arm. The write-up of
this block called it "only chooses between an edge-detect and a notify call", which is true and
missed what the set was. The `family` column it matches was assigned from unrelated evidence, so
this is a cross-check, not a fit. Both arms still reach `+0x18FE`, so **the arming gate applies to
AES floorplans too** — which is the whole reason this coach was affected.

---

# The AES "Start At" bus-write recipe — how to set `0x31` over the bus (2026-08-25)

Closes step (b) of the owner's AES "Start At" question. `GUICODE.md` Finding 59 established that
the panel only *decodes and displays* the setpoint — an MCF patch cannot change it — so the value
is set over the bus. This entry pins the frame down from the captures. Selector is `0x31`;
encoding is **0.05 V per raw LSB** (little-endian `int16`), verified three ways in PC.39: `FC03`=1020
= 51.0 V, `F400`=244 = 12.20 V, `2204`=1058 = 52.90 V.

## The proven shape — a signed `int16` DELTA in the `D2` handshake

Every start-at arrow press in PC.39 emitted the **same three-frame trio**, source SA `0x9F` (the
panel), DGN `0x1FED9`:

```
19FED99F#FF96310F 0200 D2FF     ; selector 0x31, value 0x0002 = +2  (the delta), fn D2 FF  -- write
19FED99F#FF96310F 0000 D2EA     ; selector 0x31, value 0          , fn D2 EA               -- commit
19FED99F#FF96310F FFFF D3FF     ; selector 0x31, value 0xFFFF     , fn D3 FF               -- read-back
```

This is the `D2 FF`+`D2 EA`+`D3 FF` family PC.30 Result 1 identified for `Max Chrge Rate`/`Shore
Brkr Size`. The delta lives in the **first** frame's value word; the commit carries 0 and the
read-back carries `FFFF`. The G12 **accumulates**: across nineteen `+0002` trios the `195FCE9C#31`
sweep walked `FC03 → FE03 → 0004 → 0204 → … → 2204`, i.e. **1020 → 1058, +2 each**, frame for frame
and sweep-report for sweep-report. Decrement is `FE FF` (−2 as `int16`).

**So to set start-at to a target from the bus, replay the panel: send N of these trios of the right
sign, N = |target − current| / 2, then confirm the `195FCE9C#31` sweep reads the target's LE
bytes.** 51.0 V is `FC 03`.

## Confidence, and the trap that still applies

- **Proven, frame-exact (PC.39):** a `D2 FF` trio carrying `±0002` moves `0x31` by exactly ±2 raw,
  and the G12 accumulates. Stepping to a target and watching the sweep is the safe path.
- **Inferred, not observed for `0x31`:** that a *single* trio carrying a larger delta (e.g. `2600`
  = +38) jumps in one shot. PC.11 proved arbitrary-magnitude deltas for `CC/CD/CE` (±1000 via
  `64536`/`1000`), so it is likely — but every `0x31` press we have carries exactly `±0002`. If you
  send a bulk delta, verify against the sweep; do not assume.
- **Unconfirmed — do NOT rely on an absolute set.** PC.36 carried `19FED99F#FF96310FFC03D1EA`
  (selector `0x31` := absolute 1020, fn `D1 EA`), but `0x31` was **already 1020** at the time, so
  the frame was a no-op: it proves only that `0x31` is *not* a pure delta that would have added 1020
  — it does **not** prove a `D1 EA` frame sets an absolute. This is the PC.11 trap restated:
  rvc2mqtt's `g12_configuration.py` sends config selectors as absolutes, which for a delta selector
  **adds** instead of sets. Treat `0x31` as delta-only until an absolute set is observed to change
  the value.

## The clean way to force exactly 51.0 V — a mode reset (proven, but not surgical)

Switching to **AES Single Output** (`0xEF := 3`) makes the G12 re-derive the 48 V profile and write
`0x31 → 1020` (51.0 V) **absolutely** — observed at PC.39 t=57.4. That is the most robust "return
start-at to 51.0 V" action, but it is a whole-profile reset (it also rewrites `0x0A`, the batteries
block, etc.), not a targeted edit. Finding 43 already noted 51.0 V *is* the AES default, so on a
coach sitting in AES mode start-at is 51.0 V without any write at all.

## The caveat that makes this matter — start-at is inert without `Low Volts`

`g12code` (`config[0x0B] = config[0x2F] ? config[0x31] : 0`): the auto-start watchdog compares the
measured voltage against cell `0x0B`, which equals `0x31` **only when `0x2F` (`Low Volts` enable) is
non-zero** — otherwise it is forced to 0, a floor the voltage can never fall below. PC.39 is the
proof: enabling `Low Volts` is exactly what unblocked the first observed auto-start. **Setting
start-at over the bus does nothing unless `Low Volts` (`0x2F`) is also enabled.**

## Bounds

The G12 clamps `0x31` to **[1000, 1080] = [50.0, 54.0 V]** (`g12code.AGS_LIMITS`) and forces
`stop_at (0x0D) >= start_at + 8` raw (0.4 V), rewriting `stop_at` if violated. 51.0 V (1020) is
mid-range and safe; the coach's stop-at of 53.0 V (1060) already satisfies the gap.

# PC.40 — the coach ran 11.23: floorplan slot 7, the true value→code map, and Service Mode's cross-version join (2026-08-30)

`captures/pc-1123-session-2026-08-30.log` — 160,799 frames, `can1`, one continuous capture
spanning the whole session (the bus host rode through every panel power cycle). Baseline snapshot:
`captures/pc-1123-preflash-baseline-2026-08-30.log`. The owner flashed **stock 11.23's
GuiCode+GuiData pair only** — G12 and LCD Firmware stayed at current — and the mixed panel ran
fine (NETWORK DIAGNOSTICS: `7IN COLOR LCD CONFIG: 11.23`, `G12 CONFIG: 13.6`, both green).
Everything below is `observed-on-hardware`; it closed CLAUDE.md owner item 5.

## Result 1 — the floorplan variable slot is 7, photo-confirmed

`config.floorplan_slot`'s static derivation (config-unpack cfg-`0x00` register `0x20039A8E` →
unique variable-table index 7) was made before the flash; the flash **validated a prediction**.
With `0xF5 = 6` on the wire, the owner's NETWORK DIAGNOSTICS photo matches
`render.screen_placements(data, 17, slot_values={7: 6})` on every floorplan-driven row
(TANK HEATER FRESH/BLACK/GREY, BENCH OHC LTS, the 3-entry input list); rows differing are
option-driven (Bath Fan), which the render was not given. SLIDES/AWNINGS draws a different
floorplan outline per value — a second gated page following slot 7.

## Result 2 — the value→code map, pinned by stated-order taps + photographed highlights

The picker lives on **ADDITIONAL OPTIONS** ("Coach Model Selection" grid + per-floorplan
"Option Selection" rows). Stated-order taps put `0xF5` writes on the wire; photographed
highlights anchor three pairs independently:

| value | code | how pinned |
|---|---|---|
| 0 | Ship Mode | elimination (8 buttons, 8 values) |
| 1 | 3RT / 4RT | photo (highlight) + wire |
| 2 | 3RA / 4RA | stated-order tap 11:13:19 |
| 3 | 3WT / 4WT | photo + wire |
| 4 | 3WA / 4WA | stated-order tap 11:13:36 |
| 5 | 3WD / 4WD | photo + wire |
| 6 | 3CB / 4CB | stated-order tap 11:13:53 |
| 7 | 3RD / 4RD | stated-order tap 11:14:09 |

**The anchor-indexed label run counts from 0 on 11.23, not 13.6's 1** — the recovered table had
mis-named every floorplan by one position (`guidata._floorplan_table` now aligns the run's base
to the tap-handler oracle). **Value 8 is out of range on 11.23**: no button, no handler, and the
panel shows *"WARNING! No floorplan selected"* while the G12 sweep still says `F5 = 8` — exactly
what a 13.6 coach (WD = 8) looks like to an 11.23 panel. The reverse is also on the wire: after
flashing back to 13.6 with the G12 holding 5 (3WD/4WD), the panel showed **TB** — 13.6's value 5.
**The G12 stores a number; each build's panel maps it to its own list.**

## Result 3 — all 8 tap handlers validated; the set helper is change-gated

Every tap's `0x1FED9` burst is a **subset of its `floorplandefaults` handler templates with zero
extra frames** (repeat taps of one value emit identical bursts; each `D1 EA` set is paired with a
`D3 FF` read-back request). The missing members are values already held — live proof the
config-set helper skips an unchanged value (Finding 20/23's `cmp` gate). Two taps matched their
handler **exactly** (value 1, three times). Confirm Changes emitted **nothing** — taps write
immediately; the button is panel-local.

## Result 4 — 11.23 re-asserts stored config at boot, `F5` included

At each boot the 11.23 panel emitted an unprompted `F5:=<stored>` + defaults-shaped burst
(`EB/EC/ED/A0`), including `F5:=8` — a value **no handler serves** — so this is a boot
re-assertion path reading the stored config, not a tap handler. The 13.6 panel did the same
once (11:06:37, `F5:=8` from its own NVM after a reflash).

## Result 5 — Service Mode is selector `0x93` on 11.23, and it closes PC.36's receive-path question

The 11.23 panel asserted `19FED99F#FF96930F0100D1FF` (selector `0x93 := 1`, function **`D1 FF`**,
a fourth function byte) every ~1.1 s — **826 times** — and the G12 **swept `93 = 1`**
(`195FCE9C#93000100…`), a selector absent from the baseline sweep. The stored flag survived the
reflash: the 13.6 panel **booted with Service Mode ENABLED** having never been toggled — the
`+0x12` config-payload unpack (`0x00247C72`, PC.36's "boot-time or requested config load")
observed doing its job live. The owner's disable then emitted PC.36's exact 13.6 shape
(`19EE009F#120000…` + `19FBDA9F#B000…`, 11:21:24) and `0x93` left the sweep. So **11.23
selector `0x93` ↔ 13.6 cfg offset `0x12` ↔ one G12-stored Service Mode flag** — 13.6 dropped
the selector write but kept the storage.

## Result 6 — the restore, and what floorplan churn actually costs

`livecfg` diffs against the morning baseline named every casualty of the defaults churn:
reselecting WD set `EF := 3` (AES Single, the WD default) and cleared `E6` (Progressive
Inverter), and the mode change re-derived the whole AES parameter profile (`0x31 → 1020` etc. —
PC.39's mode-reset behaviour, reconfirmed). The owner re-set `E6 := 1` at 11:21:17 — **with no
coupled `EF` write, the uncoupled reference build v8 interlock edit confirmed on hardware again** — and
deliberately kept AES Single with the 51.0 V profile. Residuals worth carrying: `0xD8`
(batteries count) went 0 → 1 during option toggling, and `0x15` dropped one LSB
(23041 → 23040), meaning unknown. Selectors 11.23 wrote that 13.6 never uses (`A0/ED/F0/A1-A3`)
remain in G12 storage, unswept and inert under 13.6.

## Result 7 (desk-side, same day) — the hardware map unmasked a floorplan-name switch

Correcting the table's base to the wire-true map let `options._find_floorplan_switch`'s oracle
match a switch the 2026-08-29 scan had declared absent: **objects 852/853, group 235** — a
type-6 name switch on **slot 7** whose labels are the full map verbatim (`0='Ship Mode' …
7='3RD / 4RD'`), with default arm `'No Floorplan Selected'` — the very text the panel drew at
out-of-range value 8. The "no name switch" conclusion was circular: the off-by-one table made
the oracle unmatchable and the failed match read as absence. One inversion to carry: 13.6/14.7
put the anchor text at **mv 0** (0 = none) and `Ship Mode` as the *default* arm; 11.23 puts
`Ship Mode` at mv 0 and the anchor as default — so the mv-0 arm is now skipped by its **label**,
never by the bare value. Slot 7 thus has two independent mechanisms agreeing: the name switch
and the config-unpack derivation (`config.floorplan_slot`).

# PC.41 — the coach ran 14.7: the map, both interlock arms, and the Lithionics-era selector family (2026-08-30)

`captures/pc-147-session-2026-08-30.log` — 182,184 frames, one capture spanning v14 → stock 13.6 →
reference build v8. Baseline: `captures/pc-147-preflash-baseline-2026-08-30.log`. Same
method as PC.40: GuiCode+GuiData pair only, G12/LCD Firmware untouched (`GUI Version: 14.7 /
Logic Controller Version: 13.6` on SETTINGS, both healthy). Every claim below is
`observed-on-hardware`.

## Result 1 — the 14.7 value→code map, confirmed at every value, plus Ship Mode = 0

Boot prediction registered in advance and hit: the stored value 8 (13.6's WD) displayed as **RY**
— in range on 14.7, no warning. Reading-order taps on the real FLOORPLAN SELECTION screen
(families "Generator Models" / "Li-AES Models") came through as 2,9,6 / 1,7 / 5,4 / 8,3 / 0:
**1=RT 2=RA 3=SY 4=TB 5=CB 6=WA 7=WT 8=RY 9=RE**, the structural name-switch map exactly — and
**Ship Mode IS a button** (against the prediction from its default-arm status), writing **0**,
same as 11.23. `find_floorplan_table`'s 14.7 result omits Ship Mode (it has no label-run entry);
a known, documented gap in the Studio picker, not in the map.

Two mechanism differences from the sibling builds: 14.7's floorplan bursts are **NOT
change-gated** (~13 selectors re-emitted wholesale per tap, incl. `0x15 := 10241` and
`0xF7 := 60`), and each tap also broadcasts an **OFF sweep** — command-3 dimmer frames to a
per-plan channel set (5–10, 43; some plans 8, 24) — the panel shutting down outputs the incoming
floorplan does not own.

## Result 2 — Finding 23's two no-oracle rows closed, and the interlock's real shape

- **Starlink Power = `0xE4`** (12:32:15 `:=1`, 12:32:30 `:=0`), and its HOME tile commands
  **channel 17** (`instance 17, level 0xFA, command 5` — the 13.6-G12 drove the physical pin,
  cross-version). The tile and the diagnostics row are both gated on the option.
- **Lithionics = `0xE7`** (multiple toggles).
- **The E7↔mode interlock is CONDITIONAL, both arms now characterized.** Select AGS →
  `EF:=1` + `E7:=0`, every time (two occurrences). Select Lithionics → `E7:=1` alone when the
  mode is AES/AES-Single (twice), but `E7:=1` + **`EF:=0`** when the mode is AGS (12:44:59) —
  the 2026-08-29 detector's template is real and **gated on mode == AGS**. Lithionics and
  AES/AES-Single coexist by design (WA's own defaults ship `EF=3, E7=1`).
- **Progressive Inverter is DECOUPLED on 14.7, proven**: `E6` toggled both ways with **no**
  mode write (12:34:20/33) — where stock 13.6, re-verified the same day at 12:53:55, still
  emits `E6:=1` + `EF:=1` together.

## Result 3 — every Main Options row and the whole AES parameter family, selector-pinned

Stated-direction toggles: **Heat Pump `0xEB`**, **Bunk Accent `0xE5`**, **Cargo Light `0xE3`**,
**Black Tank `0xEC`** — all direction-matched; the 13.6↔14.7 selector schema is build-stable as
Finding 20 claimed. Counts: **Go Power `0xD7`** (absolute), **Batteries `0xD8` = displayed
count − 1** (radio "1" → 0, "2" → 1 — which retroactively explains PC.40's restore diff).

With Lithionics on, the AUTO ENGINE START page parameterizes in **SoC percent** and its ± fields
write the *same* selectors as 13.6's voltage-era profile, as `D2 FF` delta trios (PC.11's shape):
**`0x0C`** time-at-start (±5 s), **`0x0E`** time-at-stop (±300 s), **`0x16`** max-run (±5 min —
and a `+` at 115 visibly reverted: the G12's `[60,115]` clamp from `g12code.AGS_LIMITS`,
observed live), **`0x2B`** quiet-start and **`0x2C`** quiet-stop — whose stored values decode at
last: `5632 = 0x1600` = **22:00**, `2048 = 0x0800` = **08:00**; the encoding is
`hour<<8 | minute`. Two new SoC-specific selectors: **`0xDB`/`0xDC`** = start-at/stop-at in
**half-percent**, delta-written, with the panel maintaining stop ≥ start by sending paired
deltas itself.

## Result 4 — `0xDA`: the panel bridges live SoC to the G12

From the Lithionics pages onward the 14.7 panel asserted **`0xDA := 174`** every ~3 s
(fn `D1 FF`, the assert class 11.23 uses for service mode) — and 174 = **87 × 2**, the battery's
SoC in half-percent. The panel appears to relay live SoC over the config channel so a
(14.x-era) G12 can evaluate %-based AES thresholds. On this mixed rig the 13.6 G12 stores and
ignores it — the %-AES family (`0xDA/DB/DC`) is **inert under a 13.6 G12**, whose watchdog reads
only `0x2F`/`0x31`. Untested prediction worth one future check: `0xDA` tracks SoC as it drains.

## Result 5 — smaller facts worth keeping

- 14.7 **never asserts service-mode `0x93`** — that assert is 11.23-only. No leak to clean up.
- The 14.7 **Lithionics detail page fills from generic RV-C battery DGNs** (volts/current/W/SoC/
  temp all matched the live Renogy; vendor fields — modules, BMS codes, firmware, serial — empty),
  and it pages up to **4 battery instances**. Both facts bear on the PD-inverter bridge brief.
- HOME is floorplan-gated on 14.7 (slot 10): three photographed variants — RY (LP tank + voltage
  house battery + Star Link tile), WA (GREY2 tank + SoC battery block), CB (GEN start/stop +
  AGS block).
- Stock 13.6, reflashed afterward, **re-asserted `F5:=8` (WD) from panel NVM** at boot — no
  manual reselect was needed; restore closed with only `E6`/`D8` to re-set and the `0x0A`
  one-LSB creep (also seen in PC.40's diffs; a drifting derived value, not a setting).

# PC.42 — selector `0xEE` is writable, echoed, and inert: the Pump Link gate candidate passes (2026-08-31)

**The question.** Phase A of the Pump-Link-as-new-option design
picked selector **`0xEE`**
as the borrowed state for a genuinely new OPTIONS row, on file evidence: swept every 5 s by the
13.6 G12 section; options-family cell (`E3..EF` block); **unseeded** by the table-`0x67`
config-defaults routine (like Heat Pump `0xEB`); zero panel consumers in 13.6 (one receive var —
**289**, slot `0x2000820A` — read by nothing; no OPTIONS row; no write template); 11.23's three
`FF 96 EE` write templates (`0x257370/7C/88`) are pool-unreferenced dead vendor stock; 14.7
dropped its sweep record entirely; no G12 MCF rule beyond its own sweep record; no blob
reference (offset-`0x1DC`/`movs #0xEE` scan — the usual Thumb-1 weak-negative caveat applies).
The one thing the file cannot show is the G12 **app**'s behaviour, hence this test.

**The test, all from the bus host (`cansend`/`candump` on can1, SA `0xC0`), coach on the
`Experimental` v7 image:**

1. *Baseline:* `195FCE9C#EE00FFFF00000000` every ~5.0 s — value `0xFFFF`, unset, matching the
   file's unseeded-cell prediction.
2. *Write:* `19FED9C0#FF96EE0F0100D1EA` (`0xEE := 1`). The next sweep, ~1.1 s later, echoed
   **`EE000100…`** and held it on every subsequent sweep — the G12 stores the value.
3. *Quiet:* zero `DC_DIMMER_STATUS_3` frames in the 20 s test window (no output moved) and a
   60 s soak's DGN census shows only known background traffic. (`19EF6565`, unfamiliar at first
   glance, appears 231× in the pc39 capture from 2026-08-25 — pre-existing, unrelated.)
4. *Restore:* `19FED9C0#FF96EE0FFFFFD1EA` put the cell back to **`0xFFFF`** and the sweep
   returned byte-identical to baseline. So the write path is a **raw u16 store, no clamp** — and
   any consumer of this cell must treat `0xFFFF` as OFF (the follower's existing sentinel
   discipline already does).

**Residual, stated:** NVM persistence across a **G12 power cycle** was not tested (not reachable
remotely). The cell lives in the same config store as the options that demonstrably survive
power cycles, so persistence is expected but is `inferred`, not observed. Check it the next time
coach power is cycled: `0xEE`'s swept value surviving is the confirmation.

# PC.43 — the config-toggle read-back reply is ~1 ms, not the 5 s sweep (2026-08-31)

**Why this was tested.** The Pump-Link design briefly assumed a new external-variant OPTIONS
toggle would show a ~1 s display lag, on the theory that the panel learns the new state only
from the G12's periodic `0x15FCE` sweep (every 5 s). The owner corrected it — options never lag
on the coach — so the mechanism was traced and then measured.

**The mechanism (proven-from-code, `extractions/13.6/GuiCode.bin`).** A config toggle's tap
emits a **write** (`19FED99F#FF96<sel>0F<val>00D1EA`) immediately followed by a **`D3`
read-back** (`19FED99F#FF96<sel>0FFFFFD3FF`) — group 129 (`0x2F`, Low Volts) carries both as
bindings 448/450. The panel's display is then driven every paint tick by a hard-coded bridge
block inside `FUN_00247BF8` (the per-tick paint fn, the same one our follower routine hooks):
`0x0024A176`–`0x0024A1A6` reads the config value, calls the **group-variant setter**
`[0x20039908]` inverted (`setVariant(129, state?0:1)`), and writes the display colour cell
(`str r2,[0x2003A814]`, slot 45, sourced from ON master `[0x2003A1A0]` / OFF master
`[0x20039A58]`). So the update path is **read-back → receive slot → per-tick bridge block →
widget**, and the 5 s sweep only refreshes a value already known.

**Measured on the coach (SA 0xC0 injection on `can1`), for selector `0xEE`:**

```
619.701773  19FED99F#FF96EE0F0100D1EA    write EE := 1
619.704074  19FED99F#FF96EE0FFFFFD3FF    read-back request
619.704746  195FCE9C#EE00010000000000    G12 reply  (+0.7 ms after read-back, +3 ms after write)
621.711525  19FED99F#FF96EE0F0000D1EA    write EE := 0
621.714249  19FED99F#FF96EE0FFFFFD3FF    read-back request
621.715219  195FCE9C#EE00000000000000    G12 reply  (+1.0 ms)
```

The G12 answers the read-back in ~1 ms both ways (the same latency `GUICODE.md`'s Low Volts
retraction block recorded for `0x2F`). One paint tick (~16 ms) later the bridge block has the
new value, so a config toggle's on-screen state updates in **~18 ms** — imperceptible. There is
no 1 s lag. **Design consequence:** a new OPTIONS toggle built on the external-variant +
read-back pattern (group-129 clone) plus an authored per-tick bridge block (our follower routine,
extended to `setVariant` + write a display cell for `0xEE`) is lag-free and is the firmware's own
existing mechanism, not a new one.

# PC.44 — Pump Link (new OPTIONS row, v8) flashed: row renders, but the follower is dead because the panel never surfaces `0xEE` where the routine reads it (2026-08-31)

**The build.** `build/mcf-experiment-v8.bin` — the `add_option_follower` recipe op: a genuinely
new "Pump Link" OPTIONS row (a cloned `wtype-6` checkbox on object 46, sharing the check-glyph
child 394) that toggles config selector **`0xEE`**, plus the per-tick follower routine re-gated
to read `0xEE`'s var-table status slot (`0x2000820A`) and act as a bridge block (setVariant on
the cloned group 164 + a display cell `0x20039F60`). Built from stock, all gates green, merged to
main (`c73d112`).

**What the coach showed (owner, on the panel + `candump`/`cansend` on the bus):**

- **The row RENDERS correctly** — the OPTIONS page draws "Pump Link" at the bottom of the left
  column, exactly as the in-repo compositor previewed, with Heat Pump restored to stock beside it.
  (So the append-as-child-object-via-wt5-attach path *does* draw on hardware; the earlier "did not
  appear" was a user-error flash of reference build v8, not this image.)
- **The tap DOES write `0xEE`** — each tap emits `19FED99F#FF96EE0F0100D1EA` + the `D3` read-back,
  and the G12 stores it and sweeps `195FCE9C#EE0001`. The row's tap→G12 path works.
- **The checkbox never fills, and the follower never fires** — with `0xEE` armed on the bus
  (`EE0001` on every sweep) and the bathroom light (ch25) cycled cleanly on/off (twice, deliberately),
  **no follower frame** (`19FEDB9F#2C…`) was ever transmitted, and the checkbox stayed empty.
- **The decisive probe:** five deliberate taps, with `0xEE` already armed, each emitted `0xEE:=`
  **`1`** — never `:=0`. In the external-variant design the bridge sets the variant *inverted* from
  the live `0xEE` value, so a tap should send `:=0` when `0xEE` is 1. It sent `:=1` every time.

**Root cause (confirmed, not inferred).** The routine **is running** — the only thing that drives
group 164's variant is our bridge block, and taps consistently emitting `:=1` means the bridge is
reading the gate slot as **0** and setting the "off" variant every tick. So the panel's receive path
is **not** writing `0xEE`'s value into the var-table status slot `0x2000820A` that the routine (and,
transitively, the checkbox and the transmit gate, which both read the same display cell) depends on
— even though the G12 broadcasts `EE0001` continuously. Static checks all passed and were verified
against the built image: var 289 intact (`ce 5f 1d 02 ee 00 00 00`, mode 1/n 2, filter payload[0]==0xEE),
class 3 → deadline 0 (**never stale**, so not a staleness effect), detour applied (`0af0c6fe`,
`bl 0x259bcc`), routine byte-correct, watch slot correct (var 67, ch25 `DC_DIMMER_STATUS_3`,
`0x2000804e`), display cell == the checkbox's value-slot cell. The `§5.4a` code reading predicted the
`0x15FCE` status slot would be written; the coach says it is not, at least for these mode-1/class-3
config-option selectors.

**Why v7 worked and v8 does not.** The v7 light→pump follower gated on the Heat Pump config
**register** (`0x2003A296`), which the panel *does* populate (the config-buffer→register path,
`FUN_00247BF8`). v8 moved the gate to a `0x15FCE` status **slot**, which the panel does not populate
for config selectors. The working readback path for config selectors is the config buffer/register,
not the status slot.

**Consequence for the design.** `0xEE` was chosen precisely because it had no consumers — but that is
exactly why the panel stores it nowhere the routine can read. A new OPTIONS-row follower must gate on a
config **register**, which means the new option needs a selector whose config **offset** unpacks into a
panel register (the deferred "quiet config offset" recovery from the Pump Link Phase A). Arming over
RV-C cannot work around this: the panel never surfaces `0xEE` in routine-readable SRAM. **Open:** trace
the `0x15FCE`→config-buffer receive path to find a selector that is register-backed, row-less, and
side-effect-free, then re-gate the follower on that register. `0xEE` remains valid as the G12-stored
state; what it lacks is a panel-side readback.

## PC.44 — CORRECTED 2026-08-31

**The root cause in PC.44 above is WRONG and is retracted.** The `0xEE` config-selector status
slot **is** written by the receive filter every sweep — the follower simply read the **wrong
address**. `FUN_0021C760` stores status slots with an **index-split base** (`cmp r5,#0xff` on the
var index, then `strhls r2,[r1,#0x10c]` for index ≤ 255 vs `strhhi r2,[r1,#0x144]` for index > 255,
`0x0021C9B0`–`0x0021C9C4`; `r1 = r4 + idx*2`, `r4 = 0x20007EBC`). So a **low** var writes
`0x20007FC8 + idx*2` and a **high** var writes `0x20008000 + idx*2`. Var 289 (`0xEE`) is index
**289 > 255**, so its true slot is **`0x20008242`**, not the `0x2000820A` the follower (and
`followchannel.STATUS_SLOT_BASE + var*2`) computed — 56 bytes into unwritten space, hence the
constant 0 that made the gate never open. The watched light (var 67, ≤ 255) landed in the low array
and worked, which is why the *watch* half was fine and only the *gate* half was dead.

**Consequences:** (a) config-selector status slots ARE readable — the earlier "config selectors
reach only the config buffer / must gate on a register" conclusion is withdrawn; (b) a **var-table
entry is a general "create a new register" mechanism** for any DGN the filter processes, read at the
correct index-split address, and our own routine reading the slot sidesteps the 46-bridge-block
paint limit; (c) **persistent Pump Link is a ~2-byte fix** — re-gate the v8 follower on `0x20008242`
(the whole v8 design then works, persistent via the G12's stored/swept `0xEE`, no boot-reset, no
config patch, no Approach B). The tooling (`followchannel`) must compute the slot with the
index-split base. Decisive confirmation is one diagnostic flash (gate on `0x20008242`, arm `0xEE`,
toggle the light → pump follows + checkbox fills).

# PC.45 — Pump Link (new OPTIONS row, v9) WORKS on the coach: the index-split slot fix closes PC.44 (2026-08-31)

**`build/mcf-experiment-v9.bin`** — v8 with one correction: the follower's gate/bridge reads the
`0xEE` status slot at its true **index-split** address **`0x20008242`** (var 289 > 255 → high slot
array), not the flat `0x2000820A` the PC.44 build used. Owner flashed it and confirmed on the panel:

- **The Pump Link checkbox fills on tap** — the bridge now reads the live `0xEE` value, sets the
  display cell, and the wtype-6 switch draws checked.
- **The pump follows the bathroom light** while Pump Link is on — the follower gate opens and it
  transmits `19FEDB9F#2C…` on each light edge.
- **Persistent** — `0xEE` is G12-stored and re-broadcast, and the panel now reads it back correctly,
  so the row survives a reboot (no boot-reset).

**This confirms three things on hardware:** (1) the PC.44 failure was purely the wrong slot address
(the index-split base), now fixed in `followchannel.status_slot_addr`; (2) `0x15FCE` config-selector
status slots ARE written by the receive filter and are readable by authored code — the earlier
"config selectors have no panel-side readback" conclusion is fully retracted; (3) **the var-table
entry is a working, general "create a new register" mechanism**
— a genuinely new, side-effect-free, persistent OPTIONS row driven by a chosen config selector,
built from stock, running on the coach. This is the project's first new *option* (as opposed to a
repurposed row) proven on hardware.

---

# PC.46 — `Battery Type` reads the WRONG CHARGER: the open `1FFC6` filter, observed with two devices on the bus (2026-09-02)

**Captured:** 2026-09-02, `can1` — a 15 s census, a 60 s window and a
600 s window (44,797 frames). The long capture is `build/captures-scratch/1ffc6-contention-2026-09-02.log`
(gitignored scratch, not promoted into `captures/`).
**Method:** passive `candump -L` only. **No frame was transmitted for this entry.**

**The owner's report:** the `INVERTER / CHARGER SETTINGS` page (screen 14, object 17) showed
`Battery Type` = **`Flooded`** on a coach whose battery is lithium. PC.29 and PC.31 had both
recorded that field as **blank**, with "no `1FFC6` on this bus" as the reason.

## Result 1 — the panel is showing the ALTERNATOR REGULATOR's battery type

Two devices now answer `0x1FFC6` CHARGER_CONFIGURATION_STATUS on this bus, and the panel accepts
both:

| source | frame | payload[3] | type nibble | draws |
|---|---|---|---|---|
| Wakespeed WS500, SA `0x80`, charger instance **`0x31`** | `19FFC680#3100000DD200FFFF` | `0x0D` | **0** | **`Flooded`** |
| the owner's rvc2mqtt virtual inverter, SA `0x42`, instance **`0x01`** | `19FFC642#01FFFF331500FFFF` | `0x33` | **3** | `Lithium` |

`0x0D` decodes as battery sensor present = 1, installation line = 3, **battery type = 0** — and the
panel's block at `0x0024A7E2` does `ldrsh.w r3,[r4,#0xfa]` (var 125) / `asrs r3,r3,#4` / `strb`,
unconditionally, every tick. So `0x0D >> 4` = 0 = the switch's match-value-0 arm, `Flooded`.

**Var 125's receive-filter entry is OPEN:** `c6 ff 1d 03 ff 00 00 00` — `0xFF` at `+4` is the
sentinel `FUN_0021C760` tests at `0x0021C93E`, which skips the payload compare entirely. The filter
never reads source address either (`ubfx r2,r2,#8,#0x12` @`0x0021C930` — the 18-bit DGN and nothing
else). So instance `0x31` from a device that is not the inverter drives a readout on the inverter's
page, and the last frame to arrive wins.

**The coach's battery really is lithium**, from two independent places: `modbus/inverter/state/battery_type`
= 6 (SRNE LiFePO4 16s) and the bridge's own mirror `rvc/state/inverter/battery_type` =
`Lithium Iron Phosphate`. Nothing was wrong with the battery, the panel or our builds.

## Result 2 — the WS500's frame is REQUEST-DRIVEN, which is why it stuck

In the 600 s window the WS500 sent `1FFC6` **exactly once**, 1.16 s after
`(…407.202545) 18EA8082#C6FF01FF00000000` — a Request-PGN for `1FFC6`, addressed to `0x80`, from
SA `0x82` (rvc2mqtt's own address), six seconds into that container's startup. It is the only
`18EA80xx` in the whole capture.

So the chain is: rvc2mqtt polls the alternator regulator for its charger configuration once at
boot → the WS500 answers with a **broadcast** → the panel's open filter latches it → and nothing
ever overwrote it, because until the bridge's own `1FFC6` went live nothing else on this bus sent
that DGN at all. A latched slot with no competing writer is indistinguishable from a configured
value.

## Result 3 — it self-corrected mid-capture, and that is the tell

The rvc2mqtt container restarted at 22:00:01Z (during this session) onto a build whose virtual
inverter includes `1FFC6` in its periodic set. From `…408.35` onward the capture carries **561**
`19FFC642` frames at ~1 Hz against the WS500's **1**. So the page reads `Lithium` continuously and
flips to `Flooded` for under a second each time rvc2mqtt restarts and re-polls `0x80`.

That intermittency is the signature to recognise: a field that is right almost always and wrong
right after something on the bus restarts is an open filter, not a bad value.

## Result 4 — Finding 51's prediction, confirmed on hardware

`GUICODE.md` Finding 51 said of this page's four device-sourced readouts: *"the other four read
genuine RV-C charger/inverter status DGNs straight from the device through an **open** filter
(`0xFF` at `+4`), so on a coach with two chargers the last transmitter wins — the same hazard v5
pinned for `1FFC7`/`1FEA3`/`1FF99`."* This is that, on a fourth DGN, observed. Reference build v5
pinned three DGNs against a hazard that had not yet materialised; this is the first
time one actually did.

**The fix is the counterpart op, and it is one byte.** `statusbind.pin_filter_instance(var=125,
instance=1)` writes `+4` from `ff` to `01`; entry `+5` is already `0x00`, so the compare tests
payload[0] against 1 — admitting the inverter's instance 1 and rejecting the WS500's `0x31`.
Instance 1 is the right target: every charger/inverter command DGN this page emits addresses
instance 1 (`HARDWARE-VERIFIED.md`, PC.23). Shipped as a recipe op on that pin
in a research build. **Built, not yet flashed.**

*The trade, stated because it is a choice:* a pinned entry whose device changes instance goes
**blank**, where the open one showed the wrong device. Silent-and-correct beats loud-and-wrong here
— the failure this replaces was a confident, plausible, wrong reading that survived two prior
captures as "blank, dead field".

## Residuals

- **`1FFCA` (vars 116/117, `AC Input Volts`/`Freq.`) is open too** and is *not* pinned. Only SA
  `0x42` sends it on this bus today, so it is uncontended — the same latent shape, no observation
  behind it. Pin it when a second speaker appears, not before.
- **The WS500 NACKs the bridge's `1FFC6`**: 169 `18E84280#01FFFFFFFFC6FF01` in the window, one per
  `19FFC642`. Noted, not pursued; it does not affect the panel, which never reads `0xE800`.
- **The bridge reports battery bank size 21 Ah** (`1500` LE) where the WS500 (`D200` LE = 210) and
  Modbus (`BattCapacity 210`) both say 210 — a suspected ×10 in the bridge's amp-hour encoder.
  Reported to the owner; no MCF field on screen 14 displays bank size, so it is out of scope here.

---

# PC.47 — selector `0xEE` takes signed `D2` DELTAS, and the store is raw u16 arithmetic that BORROWS across the byte boundary (2026-09-03)

**Why this was tested.** The Auto Off design wants `0xEE` to carry *two* values in one u16 — a
Pump Link flag and an auto-off minute count — so that the new feature reuses the register the
hardware-verified Pump Link build already persists, rather than claiming a second selector. Two
things had to be true and neither was established: that the G12 applies **`D2` signed deltas** to
`0xEE` (PC.42 tested `0xEE` only with absolute `D1` writes, and PC.11 explicitly left delta scope
open — it is now eleven selectors across four families, but never this one), and that the two
byte-fields do not interfere. The stock ± arrows are delta emitters (group 103/104 for selector
`0x0E`), so a delta-capable `0xEE` is what lets the new stepper be **stock bindings with a
different selector byte** instead of authored code.

**Method.** From the bus host (`cansend`/`candump` on can1, SA `0xC0`), coach on the `Experimental` v12
image, Pump Link ON. Every step read back with the stock `D3` frame; the sequence ends with an
absolute restore.

```
baseline    195FCE9C#EE00010000000000                          0xEE = 0x0001
+0x0500     19FED9C0#FF96EE0F0005D2EA  -> EE00010500000000     0x0001 + 0x0500 = 0x0501   ✓
-0x0001     19FED9C0#FF96EE0FFFFFD2EA  -> EE00000500000000     0x0501 - 1      = 0x0500   ✓
-0x0001     19FED9C0#FF96EE0FFFFFD2EA  -> EE00FF0400000000     0x0500 - 1      = 0x04FF   ← BORROW
restore     19FED9C0#FF96EE0F0100D1EA  -> EE00010000000000     back to baseline exactly
```

**Result 1 — `D2` deltas work on `0xEE`, both directions.** The G12 added `+0x0500` and
subtracted `0x0001`, echoing the arithmetic result on the very next sweep, and the `D3` read-back
answered in **under 1 ms** (PC.43's latency, reconfirmed on this selector). So a new ± stepper on
this selector is **data-only**: clone the stock arrow groups' six `0x1FED9` bindings, change the
selector byte and the delta literal, and the G12 does the arithmetic — the same mechanism the AES
setpoint arrows have always used. Nothing about the panel-side stepper needs authored code.

**Result 2 — the store is one u16 with NO field protection, and it borrows.** `0x0500 − 1` gave
`0x04FF`, not `0x0500` or a clamp: the G12 treats the cell as a plain 16-bit integer, exactly as
PC.42's "raw u16 store, no clamp" predicted, and a delta that underflows a low byte **eats the
high byte**. This is the finding that shapes the design, and it settles a field-layout question
that file evidence could not:

- **Minutes go in the LOW byte, the link flag in the HIGH byte.** Then the minute steps (±5,
  clamped to [5,60]) can never cross 0 or 255, so they never touch the flag; and a link-toggle
  borrow (`−0x0100` when the flag is already 0) lands as `0xFF` **in the flag byte while the
  user's minute setting survives intact**. Corruption is confined to the field that is trivially
  re-derivable, and a borrow can only occur on a "turn off" that was already off, so recovering
  the flag as 0 is unambiguous.
- The reverse layout is strictly worse: with minutes high, `0x0500 − 1` corrupts **both** fields
  at once — the minutes silently drop 5 → 4 (an off-grid value the user never chose) and the flag
  reads `0xFF`. The capture above *is* that failure, which is why it is recorded rather than
  discarded as a mis-step.
- Because the G12 clamps nothing here, **the clamp must be ours**: the appended per-tick routine
  corrects an out-of-range field before it evaluates the follower gate, so any transient
  excursion self-heals within one paint tick (~16 ms) and the follower never acts on a corrupted
  word.

**Result 3 — the low byte is what the existing subscription already sees.** Var 289's entry is
extraction mode 1 of `payload[2]`, i.e. the **low** byte, so it reads the minute field under the
chosen layout and cannot see the flag. The design therefore still needs the one-nibble mode
1 → 2 edit (`payload[2] | payload[3]<<8`, `MCF-FORMAT.md` §5.4a) to surface the whole u16 in the
slot; `statusbind.retarget_filter` already writes that field. This is a data-only edit, and it is
the only change the register itself requires.

**Residual, stated:** NVM persistence of `0xEE` across a **G12 power cycle** is still `inferred`
(PC.42's open residual, not reachable remotely) — it now matters more, because a lost word costs
the user a setting and not just a checkbox. ~~The routine's unset-sentinel seed (`0xFFFF` → the
30-minute default) covers a cleared cell either way.~~ **CORRECTED 2026-09-03 — there is no seed.**
That sentence was written before the routine existed, and the behaviour it describes was removed
during implementation because it was a defect: the panel's own parser init writes `0xFFFF` to every
receive slot (`MCF-FORMAT.md`, `FUN_0021BE50`) and the G12 sweeps this selector only every ~5.0 s,
while the routine runs every paint tick — so seeding on the sentinel would have overwritten the
owner's stored `0xEE`, Pump Link flag included, on the first tick of every power-up. The routine
now writes **nothing to the bus** while the slot reads `0xFFFF`; it sets the display byte and
disarms its own countdown, both panel-local. A genuinely cleared cell is bootstrapped by the
owner's first tap plus the clamp, not by a seed.
**The coach was left as found:** `0xEE` = `0x0001`, Pump Link ON, verified by read-back.

---

# PC.48 — the awning ignition interlock is BUS-FED, and the panel's lock label reads a DIFFERENT instance than the G12's lock (2026-09-03)

`captures/pc48-awning-lock-injection-2026-09-03.log`. Ignition off, engine off, coach on
`Experimental` v12. From the bus host (SA `0xC0`, `can1`) we replayed the G12's own engine-running
indicator byte-for-byte — `19FBDAC0#FB01FFFFFFFFFFFF` — at 1 Hz, and cleared it with `FB00`.

**Result 1 — a table-`0x61` receive record really does write its cell** (`MCF-FORMAT.md` §9a.3,
which had it as `inferred`). 4 ms after the first injected frame the G12 emitted PC.27's exact
signature, and 2 ms after the clear it undid it:

```
701.540  19FBDAC0#FB01FFFFFFFFFFFF     our injection
701.544  19FEDB9C#03FF0003FF00FFFF     G12 -> ch 3 OFF
701.545  19FEDB9C#03FF0021FF00FFFF     G12 -> ch 3 command 33 LOCK
701.550  19FEDA9C#03FF00FDFF2100FF     ch 3 status: lock flag FD
713.602  19FBDAC0#FB00FFFFFFFFFFFF     our clear
713.604  19FEDB9C#03FF0022FF00FFFF     G12 -> ch 3 command 34 UNLOCK
```

The 12 s baseline immediately before contains **zero** `19FEDB` frames from any node, so every
frame here is ours to account for. **Config cell `0x28` (struct `+0x50`) is written from the bus**,
which is why `G12-CODE.md` could never find a store to it in the blob — that entry is corrected in
place. Selector `0x06` also went `0 -> 3` for the duration and back, confirming it as the run-state
cell (it had only ever read 3 in the engine-running captures pc27/pc28).

**Result 2 — the lock is enforced at the OUTPUT, not just drawn.** The owner pressed `EXT` in both
states; the panel emitted the identical command both times and the G12 answered differently:

| condition | panel command | G12 channel-3 status | output level |
|---|---|---|---|
| locked | `19FEDB9F#03FFC8010200FFFF` | `03FF00FDFF0100FF` | **`00`** — never energised |
| unlocked | the same frame, byte for byte | `03FFC8FC020104FF` | **`C8`** = 200, motor ran |

Byte 2 is the level and byte 3 the lock flag (`FD` locked / `FC` unlocked). So the G12 accepts the
command and declines to drive the pin. This is the first direct measurement of the interlock's
*effect*; PC.27 only established that the LOCK frame is sent.

**Result 3 — the panel's `Locked by Ign` label and the G12's lock read DIFFERENT instances, and
this cost a run.** The first attempt injected `FB` (engine running) alone: the G12 locked the
awning, but the owner watching the panel saw `Engine Running` change and **no lock indication on
the EXT button**. `MCF-FORMAT.md` §9a.2 records that input pin 10 fires four rules — the `0xAA`
input-indicator pair and the `0xFB` engine-running pair — but not that the two halves feed
different consumers. They do: the **G12's** awning lock keys on `FB` (table `0x61` #0 -> cell
`0x28`), while the **panel's** slot 132 (`GUICODE.md` Finding 56, object 181's `Locked by Ign`
arm) is fed by its own receive filter on `0xAA`, the pin-10 indicator. Injecting both made the
label appear and the lock behave together. **A coach could therefore show the label without the
lock, or lock without showing it**, if only one of the two pairs reached the bus.

*Method note worth carrying:* the first run held the injected state for only 12 s and the owner's
button presses landed 2.5 s **after** the clear, so the awning answered normally and the run read
as a failure. A 75 s hold made it obvious. Size the window for the human observer, not the bus.

---

# PC.49 — the config read-back is a GENERAL read primitive: all 256 G12 config cells dumped (2026-09-03)

`captures/pc49-config-array-readback-2026-09-03.log`. The read-back request documented above
(`FF 96 <sel> 0F FF FF D3 FF`) was thought to be a paired half of the panel's write idiom. It is
more than that: **it forces a one-shot `195FCE9C` report for any selector, including the ones the
periodic 5 s sweep never carries.** Sending it for `0x00`–`0xFF` at 4 Hz from SA `0xC0` returned
**256 of 256 cells**; 100 read the `0xFFFF` unset sentinel and 156 hold values.

This defeats the limitation `G12-CODE.md` records as "the sweep is a subset, not a dump" and
"three quarters of the config cells the G12 defaults are invisible on the wire". They are invisible
*to the sweep*; they are readable on demand. Read-backs trigger no relay (established earlier in
this document and re-confirmed here — the capture contains no CAN id absent from baseline other
than our own `19FED9C0`), so this is a safe primitive.

**Live values confirming code-only readings** (`G12-CODE.md`, "The config struct named"):

| cell | live | confirms |
|---|---|---|
| `0x50` | 255 | the generator-STOP channel; AES-Single writes 255 = none — and the frame the G12 emitted in PC.48 was addressed to instance `0xFF` |
| `0x73` | 18 | the auto-start channel the AES arm writes at `+0x0A06` — and the G12 commanded channel `0x12` = 18 in PC.48 |
| `0x7A` | 3 | the last-mode latch, equal to `0xEF` = 3 |
| `0x7B` / `0x7C` | 255 / 1 | byte 4 of rules #0/#4 — the emitted frame carried `01` |
| `0x83`/`0x87`/`0x89`/`0x91` | 214 / 80 / 13 / 30 | every relay **shadow** equals its principal (`0x82`/`0x86`/`0x88`/`0x90`), a mechanism that was code-only |
| `0x6F`/`0x70`/`0x71` | 255 / 80 / 200 | the (255, 80, 200) triple sits at `6F/70/71` on 13.6, confirming the cross-build renumbering inference |
| `0x94` | **65535** | the relay gate is unset, exactly as predicted from it having no default and no report |
| `0x95`/`0x96` | 0 / 0 | the "parameters at fresh-seed values" detector reads false, correct for a coach with tuned values |

**Newly visible, still unnamed** (a live value is not a name): `0x1D`/`0x1E` = 14, `0x60` = 100,
`0xB1` = 65516, `0xB3` = 58061, `0xC4` = 25681, `0xDA` = 174, `0xDB`/`0xDC` = 29, `0xF0` = 1,
`0xF3`/`0xF4` = 8 / 13 (the pair the `1FACF` frame carries). The full dump is in the capture.

---

# PC.50 — the four cross-selector relays FIRE, and they carry the RECORD's cell, not the clamped one (2026-09-03)

`captures/pc50-charger-relay-gate-2026-09-03.log`. `G12-CODE.md` left four relays unexplained:
the code clamps `0x9C`/`0xBA`/`0xBC`/`0xBE` and then fires rules that substitute `0x8E`/`0xB0`/
`0xB2`/`0xB4`, with nothing copying one to the other, and offered "either the rule engine reads the
+10 cell or these are two copies of one value". All four are gated on `config[0x94] == 1`, which
is unset, which is why PC.30 saw none of these frames.

**Safety first, and it mattered.** Both relay targets are *command* DGNs —
`1FECB` = `CHARGER_CONFIGURATION_COMMAND_3`, `1FF97` = `CHARGER_EQUALIZATION_CONFIGURATION_COMMAND`
— and the owner's PD-inverter bridge was live at SA `0x42`. The owner stopped that container before
the write; the bus was confirmed silent at `0x42` first.

We set `0x94 := 1`, nudged each setpoint by a `D2` delta of +1 to trip the change detector, then
restored every value and the gate. The G12 emitted six frames it has never been observed to emit:

```
19FECB9C#01FFFFF0FFFFFFFF     19FECB9C#01FFFFFFFFF0FFFF
19FECB9C#02FFFFF0FFFFFFFF     19FECB9C#02FFFFFFFFF0FFFF
19FF979C#01FFFFFFFFFFFFFF     19FF979C#02FFFFFFFFFFFFFF
```

**The payload settles it.** The substituted u16 is `F0 FF` = **65520**, which is the live value of
cells `0xB0` and `0xB2`. The clamped cells `0xBA`/`0xBC` hold **292** and **274** and appear
nowhere in the frames. So the rule engine reads **the cell its record names**, and the
"two copies of one value" reading is **refuted** — they hold different values. `1FF97` went out
all-`FF` because `0xB4` is unset.

Consequences. The relay family is **unprovisioned on this coach**: enabling it would command both
charger instances with placeholder values and send an equalization configuration carrying nothing.
That is a coherent reason for the gate to ship unset, and it is a caution for anyone tempted to set
`0x94` on a coach whose charger listens. Restored and verified: `0x94` = 65535, setpoints back at
292 / 274 / 292, no further relay frames.

---

# PC.51 — a mode change SWAPS a stored per-mode parameter profile in G12 NVM; the panel sends only four selectors (2026-09-03)

`captures/pc51-mode-switch-profile-swap-2026-09-03.log`. The open question was where **1020**
comes from: `GUICODE.md` **Finding 60** reads the panel's GuiCode template as *establishing* the
AES `Start At` default, and `G12-CODE.md` observed that the blob's mode path clamps `0x31` into
`[1000, 1080]` — which would give 1000, not 1020 — and concluded PC.39's frameless `244 -> 1020`
had no blob mechanism. The owner switched AGS then back to AES Single Output on the panel while
the whole bus was captured.

**Result 1 — the panel's mode-selection burst is FOUR selectors, and `0x31` is not one of them.**
The complete set of panel-sourced (`19FED99F`) config writes across both switches:

```
AGS:            EF:=1    D7:=0    15:=0x2801    F7:=60      (+ a D3 read-back after each)
AES Single:     EF:=3    D7:=0    15:=0x2801    F7:=60
```

No `FF96310F…` frame exists in the capture. **The panel did not send 1020.**

**Result 2 — the G12 restores a whole profile by itself.** Within two sweeps of each mode write,
with no further frames from anyone:

| selector | AES (before) | after `EF:=1` (AGS) | after `EF:=3` (AES) |
|---|---|---|---|
| `0x31` start at | 1020 | **244** | **1020** |
| `0x0D` stop at | 1096 | 270 | 1060 |
| `0x0E` time at stop | 900 | 7200 | 1800 |
| `0x0F` min run | 1800 | 900 | 1800 |
| `0x0C` time at start | 30 | 60 | 60 |
| `0x16` max run | 70 | 720 | 115 |

**These are not clamp results.** A clamp of 1020 into the AGS range would give 250; the observed
value is **244**, which is 11.23/14.7's *default* for `0x31` and 12.20 V. A clamp of 244 into
`[1000, 1080]` would give 1000; the observed value is **1020**. So the G12 holds **two complete
parameter profiles, one per mode, in its own NVM**, and swaps them on a mode change. The blob's
clamp arms are a floor and ceiling applied on top, not the source of the values.

**What this corrects.** PC.39's frameless `244 -> 1020` is fully explained: a profile swap.
`GUICODE.md` Finding 60's MCF template remains the source for the **panel-driven** defaults burst
(the `Reset to Defaults` button's eight `D1 EA` sets, PC.36) — that is a different path and this
capture does not touch it — but the claim that the panel is *the* origin of the running 1020 is
**too strong**: on a mode change the G12 supplies it with the panel silent. `G12-CODE.md`'s "no
1020 anywhere in the blob" stands as a statement about the blob and is now explained rather than
puzzling: 1020 lives in G12 NVM, not in any image we hold.

*Cost, recorded because it is a real one:* the round trip overwrote four of the owner's tuned AES
parameters (`0x0C` 30->60, `0x0D` 1096->1060, `0x0E` 900->1800, `0x16` 70->115). All four were
restored by `D2` delta writes and verified; `0x31` and `0x0F` came back correct on their own. Any
future mode-switch test must budget for this.

---

# PC.52 — `0x1EBFF` decoded: it is TP.DT carrying `PRODUCT_IDENTIFICATION`, and the coach names its own devices (2026-09-03)

`captures/pc52-device-identification-2026-09-03.log`, 8,508 frames over 114 s, taken across a
restart of the owner's rvc2mqtt bridge. `DGN-MAP.md` lists `0x1EBFF` as **(undecoded)** with 8
variable-table entries. It is the **J1939/RV-C transport protocol data-transfer** channel, and on
this coach it carries one thing: the `PRODUCT_IDENTIFICATION` string of every node.

The pairing is explicit in the capture. `18ECFFxx` is the BAM announcement — `20 <size u16>
<packets> FF <dgn u24>` — and every one of them announces **DGN `0x0FEEB`** (`1FEEB`,
`PRODUCT_IDENTIFICATION`), followed by `18EBFFxx` data packets whose first byte is the sequence
number and whose remaining seven bytes are the payload:

| SA | announced | reassembled string |
|---|---|---|
| `0x80` | 36 B / 6 pkts | `Wakespeed Offshore*WS500*926993657**` |
| `0x9C` | 46 B / 7 pkts | `Spyder Controls*G12 Panel 2v2 3v0d*0000000*22*` |
| `0x9F` | 45 B / 7 pkts | `Spyder Controls*7in Lite LCD 1v6r*0000000*20*` |
| `0xFD` | 32 B / 5 pkts | `Firefly*Standalone Module*0001**` |

Fields are `*`-separated: make, model, serial, then a numeric field.

**Two cross-confirmations that cost nothing.** The panel identifies itself as `7in Lite LCD
**1v6r**` — matching `MCF-FORMAT.md` §4's decode of the `LCD Firmware` version block, which reads
`ExtFwVer 0x0106` = v1.06 and `FwRlsLetter 0x72` = `'r'` out of the image at `+0x400`. A string on
the wire and a field parsed from the file agree, by routes sharing no code. And `0x80` is the
**Wakespeed WS500** alternator regulator, which is the device `BUS-BASELINE.md` PC.46 caught
answering `1FFC6` alongside the inverter and drawing the wrong `Battery Type`.

**The panel asks, on a schedule.** It emits `18EAFF9F#EBFE00…` (a global request for `1FEEB`) and
`18EAFF9F#D8FE01…` (for `0x01FED8`) eight times each in 114 s, plus **directed** requests to a
specific address once it has adopted one — see PC.53.

---

# PC.53 — the panel adopts an inverter on `1FFD4` ALONE, and the bridge never answers the identification request (2026-09-03)

`captures/pc53-inverter-adoption-replay-2026-09-03.log`. Asked because the owner reported the
`Absorb` and `Float` rows missing from `INVERTER / CHARGER SETTINGS` while the bridge was stopped,
and recalled having seen them after a device announced itself. `GUICODE.md` records that
`FUN_0024F1FC` binds the page's device **by source address, after adoption via `1FFD4` at
instance 1 or 2**; this tests that from the bus.

**Method.** With the owner's bridge stopped and `0x42` confirmed silent, we replayed its own
frames from the bus host as SA `0x42`. Phase 1, 45 s: **only** `19FFD442#01025FFFFFFFFFFF`. Phase 2,
45 s: that plus the five other DGNs the bridge sources (`1FFC6`, `1FEE8`, `1FECA`, `1FFD7`,
`1FFCA`).

**Result — `1FFD4` alone is sufficient to trigger adoption.** 8.9 s into phase 1, with nothing
else from that address on the bus, the panel began emitting a **directed** request to `0x42`:

```
18EA429F#EBFE000000000000      panel -> 0x42: "identify yourself" (DGN 1FEEB)
```

It repeated at 8.9 s, 25.8 s and 42.9 s — all within phase 1 — and continued unchanged through
phase 2, so none of the other five DGNs is required and adding them changed nothing about the
adoption itself. The cadence is ~17 s.

**Result — the bridge does not answer.** There is **no `18EBFF42` / `18ECFF42`** anywhere in
either capture: `0x42` sources no `PRODUCT_IDENTIFICATION`, while `0x80`, `0x9C`, `0x9F` and
`0xFD` all do (PC.52). That is a sufficient explanation for the owner's photo of the page showing
`I/C Firmware Rev. ----` and `I/C Model` blank, and it is **actionable on the bridge rather than
on the MCF**: a node that answered the directed `1FEEB` request would fill those fields.
(`GUICODE.md` Finding 58's note that `UNKNOWN` is a stored string reached by a 0-arm is the
display side of the same thing.)

**What is NOT established here.** Whether adoption alone makes the `Absorb`/`Float` rows appear.
The rows were observed present in an owner photo taken with the bridge running (values reading
`25.5 V`, i.e. raw 255 = unset, so presence does not require valid data) and observed absent with
the bridge stopped — but nobody was watching the screen during the two replay phases, so the
clean discriminator was not read. `render.py --root 17` draws the page without them at the
default config. Re-running the phase-1 replay with an observer settles it in 45 s.

---

# PC.54 — the `Absorb`/`Float` gate is NOT driven by anything on the bus: five stimuli, five negatives (2026-09-03)

`GUICODE.md` **Finding 63** proves the rows are gated by a type-6 switch on value slot 23 and that
no instruction in either image reaches that slot's register. This entry is the hardware half: the
owner reproduced the rows and we tried every plausible bus cause against a live panel. **All five
failed**, and three of them moved something else on the same page, so the panel was demonstrably
listening each time.

**The reproduction.** With the virtual inverter stopped, a **cold boot of the screen alone** (Pi,
bridge and G12 left running) makes `Absorb` and `Float` appear, reliably. They then **persist**.
This refutes the stale-RAM reading offered a day earlier, which predicted the opposite — a cold
boot clearing the byte and a warm reflash showing the rows.

| stimulus | injected as | effect on the page | rows |
|---|---|---|---|
| `DC_DIMMER_STATUS_3` inst 6, the DGN of the same-numbered receive-filter entry | 50 s byte 6 set, then 50 s **every** byte set | none | unchanged |
| `CHARGER_CONFIGURATION_STATUS` (`1FFC6`) | 45 s, the bridge's own payload | **`Battery Type` → `Lithium`** | **still visible** |
| `INVERTER_STATUS` (`1FFD4`), the adoption frame | 60 s | panel sent **4 directed `1FEEB` requests** to our address | **still visible** |
| the bridge's other four DGNs (`1FFD7`, `1FFCA`, `1FEE8`, `1FECA`) | 50 s, together | none observed | **still visible** |
| the real bridge restarted, first without and then with the virtual inverter | the genuine article | its own DGNs return | **still visible** |
| **a UI mode change**, AES Single Output → AGS, on the panel | the owner, on the screen | `EF` 3 → 1 on the wire, the four-selector burst of PC.51 | **still visible** |

The bridge-restart capture adds only `18E84280` (an acknowledgement from the Wakespeed to `0x42`)
and the panel's directed request beyond what we had already replayed, so nothing was missed by
replaying rather than restarting.

**What this eliminates.** Not charger-configuration presence (the injection drove `Battery Type`
and left the rows alone). Not device adoption (the panel began requesting identification from our
spare address and the rows stayed). Not the inverter's data DGNs. Not the receive-filter entry that
shares the slot's index — which the file already predicted, since a received value reaches a widget
only through a bridge block needing the destination as a literal, and there is none.

**Where that leaves it.** The byte is set at panel power-on and nothing observed on the bus clears
it; whatever hid the rows during the previous long uptime was not a frame. Combined with Finding
63's instruction-level negative — GuiCode writes 58 distinct addresses in the 0x120-byte window
around it and skips this byte exactly — the remaining candidates are a write from outside both
images or a byte with no owner at all. **The practical consequence for the owner is that the gate
is not controllable from the bus, so making the rows permanent is a one-record GuiData edit, not a
configuration change.**

**The mode switch was the owner's own lead** ("switching to AES seemed to remove it last time") and
it did not reproduce: the panel emitted its `EF`/`D7`/`0x15`/`F7` burst, the G12 swapped profiles,
and the rows stayed. So the clearing event is not the mode change either.

**A patch attempt that did NOT work, recorded so it is not tried again.** The obvious cheap fix is
to edit the match table (`GuiData +0x4562C`) so object 303 is the switch's default arm — setting
object 302's match value to an unreachable `0x00FE` and 303's to `0xFFFF`. Resealed cleanly (one
checksum, `Lyra 13v6 GuiData`) and **the rows still do not draw**, with or without
`--match-off 4562c`. So `0xFFFF` in the match table is not "draw when nothing else matches" in the
way Finding 61's Bunk Accent arm suggested, at least not as `layout.Screen` replays it. The robust
edit is instead to replace the wt-6 switch record with an unconditional wt-5 container child of
object 303 via `relocate.replace_object_records` — **not built or verified here.**

*One field still unexplained, recorded because it moved:* `Bridge Firmware` read `0.99` in an
earlier photo and `1.07` after our injections, although the `1FFD4` payload we sent is byte-identical
to the bridge's own. Its source is not `1FFD4` and is not identified.

---

# PC.55 — `Absorb` and `Float` are driven by `0x1FECC`, and the decode is HARD-LIMITED to a 12 V band (2026-09-03)

**`observed-on-hardware`, injected and read off the panel.** Closes the readout half of
`GUICODE.md` Finding 63 and hands the owner's bridge something it can actually publish.

**The chain, every joint cited.** `FUN_00247BF8`'s parameter is the receive-slot array base, so its
`ldrsh.w [r4,#0x19C]` / `[r4,#0x19E]` are **vars 206 and 207**. Their receive-filter entries, read
with the field semantics decompiled from `FUN_0021C760` rather than guessed:

| var | DGN | extract | filter |
|---|---|---|---|
| 206 `Absorb` | **`0x1FECC`** | mode 2 = u16 LE at payload **[3],[4]** | `0xFF` sentinel — **open, any source address** |
| 207 `Float` | **`0x1FECC`** | mode 2 = u16 LE at payload **[5],[6]** | open |

`0x1FECC` is `CHARGER_CONFIGURATION_STATUS_3` (the status counterpart of `1FECB`
`CHARGER_CONFIGURATION_COMMAND_3`). Units are **0.05 V**; the panel displays `(raw + 1) >> 1`
tenths of a volt.

**Proven on the coach.** Nothing on this bus sends `0x1FECC` — hence the 25.5 V, which is the
config-byte writer's `0xFF` sentinel rendered literally. Injecting

```
19FECCC0#01FFFF20011001FF      absorb raw 288, float raw 272
```

put **14.4 V and 13.6 V** on the panel, exactly as predicted.

**The guard, and it is the finding that matters.** A first injection at 28.8 V / 27.2 V changed
**nothing**, and the code says why — the whole decode block at `0x0024A80E` is fenced by a range
test on *Float*:

```asm
0024A80E  ldrsh.w r3,[r4,#0x19e]     ; Float raw
0024A812  cmp.w   r3,#-1 / beq       ; unset -> skip
0024A818  adds    r2,r3,#1
0024A81A  cmp     r2,#0xef / ble     ; float+1 <= 239  -> SKIP
0024A824  adds    r3,#1
0024A826  cmp.w   r3,#0x16a / bge    ; float+1 >= 362  -> SKIP
0024A82C  ldrsh.w r2,[r4,#0x19c]     ; only now is Absorb read
```

So the decode runs **only for Float raw 239-361, i.e. 12.0 V to 18.0 V displayed**. Anything
outside is discarded silently and both readouts keep whatever they had.

**The consequence for this coach, and a strong candidate explanation for the gate.** This is a
**48 V** system: a real float setpoint near 54 V is raw ~1080, far above the ceiling, so
`Absorb`/`Float` on screen 14 **can never show a true value here** no matter what any device
publishes. A page region that cannot work on this floorplan is exactly the kind of thing a
type-6 gate exists to hide — which would make the switch on slot 23 (Finding 63) a *deliberate*
suppression rather than the anomaly it has looked like, and would reframe "why do they sometimes
appear" as "why is the suppression sometimes not applied". **Stated as a hypothesis:** it is
consistent with everything observed and nothing here proves the gate's intent.

**Shipped so the class is not missed again — `src/fireflymcf/decodeguards.py`.** It scans the
per-tick decoder for stores fenced by a magnitude test and reports the cell, the variables read and
the fences. Seven sites on 13.6; `Absorb`/`Float` is the only **two-sided** one. The scanning trap
is recorded in the module: a fence belongs to the **block**, not the last field loaded, and the
first cut reset its guard list on every `[r4,#imm]` load — which, because `Absorb`'s load sits
between the fence and the store, dropped the fence and reported this very site as unguarded.
`tests/test_decodeguards.py` pins the fence, its inheritance by the second field, and that enum
tests are not miscounted as ranges; reintroducing the reset bug breaks three of them.

**Actionable for the PD-inverter bridge:** publishing `0x1FECC` with absorb at payload[3..4] and
float at payload[5..6] drives these rows from any source address — but only within the 12 V
window, so on this coach it is a capability without a use.

---

# PC.56 — the `Absorb`/`Float` gate reads UNINITIALISED SRAM: ~10 cold boots always show, a flash always hides (2026-09-03)

`observed-on-hardware` for the behaviour, **`inferred`** for the mechanism. This closes the
question `GUICODE.md` Finding 63 opened, and the answer is that **nothing writes the byte, by
design or otherwise** — the ten exhausted search avenues in that finding were not missing a
mechanism, they were describing one.

**The observations, from the owner:**

| event | rows |
|---|---|
| cold boot of the screen (disconnect/reconnect), **~10 times** | **present, every time** |
| flash to v12 | **missing** |
| cycle panel power after that flash | **present** |

**Why this settles it.** Two theories survived PC.54's six bus negatives, and this separates them
cleanly:

- **A bootloader or ROM writes it on start.** *Refuted.* A post-flash restart runs the same boot
  path, so the byte would come up the same way; it does not.
- **The byte is uninitialised SRAM.** *Fits every observation.* SRAM power-on state is not random
  — each cell has a fabrication bias, so a given device comes up the same way every time, which is
  exactly the 10-for-10 result. A **flash** is the one event that dirties that memory wholesale,
  because the loader buffers a ~1 MB image through RAM, leaving this cell at 0 and the rows hidden
  until the next true power cycle restores the bias pattern.

That also explains, in retrospect, every earlier observation: why the rows were missing when the
owner first asked (the last event had been a flash, not a power cycle); why the owner had once
seen them and could not reproduce it; why **no** frame moves them (PC.54, six stimuli, three of
which visibly changed other things on the same page); and why the gate slot is one of the **210**
widget slots `slotsource` classifies `unreferenced` while its own page's readouts have four
literal writers between them.

**So it is a firmware defect, and a familiar one.** The panel draws a control block whose
visibility is decided by memory nothing initialises — this project's standing rule that *an unset
switch is not a safe default*, seen from the firmware's side rather than our tooling's. It is
latent rather than harmful here: the region it exposes is the `Absorb`/`Float` pair, whose decode
is fenced to 12.0-18.0 V (**PC.55**) and therefore cannot show a true value on this 48 V coach
either way. The two findings are consistent with each other and with the gate being *intended* to
suppress the block — the intent simply has no implementation behind it.

**Practical, and it needs no build:** the controls are reliably present after any cold power cycle
of the screen and reliably absent immediately after a flash. If they are wanted, power-cycle the
panel after flashing. The one-record GuiData edit remains the deterministic route.

**One prediction tested and NOT confirmed, recorded because it was a real risk to the reading.**
If the rule were "any boot path that works RAM hard clears the byte", then a boot with USB attached
should behave like a flash — the panel spends **33.8 s** on it against 8-11 s normally (measured by
its silence on `can1`), which looks like OTG host-mode probing for a firmware image. The owner ran
exactly that boot and **the rows were still present**. So the USB path leaves this region alone and
the refinement is withdrawn; the rule stays the narrower one, that only a **flash** clears it, which
is also the only event that streams a ~1 MB image through RAM. The core reading is untouched and
the test could have falsified it.

**The code side is now settled — `GUICODE.md`, "The 210 explained", 2026-09-03.** Prompted by the
owner's point that Firefly's own documentation tells you to power-cycle after a firmware update,
the LCD firmware's reset handler was decoded (`raminit.ram_init`): it zeroes `.bss` over
`0x20000228`-`0x2000AD98` only, against a stack top of `0x20050000`, so **276 KB above `_ebss` is
never initialised** — and every GuiCode-RAM widget slot on all three builds is in that tail.
`0x20039999` is therefore established by nothing, which is what makes it hold SRAM bias across a
cold boot and stale content across the warm reset a flash ends with. That converts this entry's
reading from inference to `proven-from-code` on the *mechanism*; what is still inferred is only
that this particular die biases to non-zero.

**What would still prove it directly** rather than infer it: a RAM read of `0x20039999` at boot,
which needs debug access this project does not have. Short of that, a falsifying observation would be a
cold boot that comes up *without* the rows on a coach whose SRAM biases the other way — i.e. the
behaviour should be **per-device**, not universal, and a second panel showing the opposite would
confirm the reading rather than contradict it.

---

## PC.57 — the levelling page ignores its own subscriptions — `observed-on-hardware`, 2026-09-03

**Captured:** `captures/pc57-leveling-status-injection-2026-09-03.log`, `can1` via
6,337 frames. Probe SA **`0xC0`** (census that day: `9C` G12, `9F` panel,
`80` Wakespeed, `42` the owner's bridge, `70`, `65`, `FD`).

**Why.** `docs/LEVELING-PAGE.md` §3 says all 18 of screen 29's state-driven cells have no writer, so
data arriving on the DGNs the page subscribes to cannot reach the display. That is an argument from
absence, and this project's standing rule is to test those.

**Method.** With the owner on screen 29 for the whole window, three 25-second bursts at 1 Hz:

| burst | frame | meaning |
|---|---|---|
| 21:43:14 | `19FFEDC0#0800000000000000` | LEVELING_CONTROL_STATUS, Operating mode = automatic hydraulic/electric levelling started |
| 21:43:39 | `19FFEDC0#0000110000000000` | idle + excessive-slope initial warning + master warning (byte 2 = `0x11`) |
| 21:44:04 | `19FFECC0#4000000000000000` | LEVELING_JACK_STATUS, 4 jacks, straight acting |

All 75 frames are in the capture. Payloads were chosen from the official layouts the owner added to
`rvc-spec.yml`, so each targets a field the page visibly has a row for.

**Result: nothing moved.** No `Status` dot changed, no arrow changed colour, the buttons did not
flip. The owner watched the page for the full 75 s. No other node emitted on any `19FFE*` DGN — the
only levelling traffic in the window is ours — so there is no levelling controller on this coach
whose absence could explain it.

**What it settles.** Bus data on `0x1FFED` / `0x1FFEC` does not reach screen 29, confirming the
static negative. The panel's filters do store the values (vars 232/234/236 land in
`0x20008198`/`0x2000819C`/`0x200081A0`); nothing carries them to the display cells.

**And it closes the §4 caveat by a different route.** That caveat was that a cell can be written
*by index* through `setVariant`, which no literal scan sees. The two v14 photos already rule that
out: the eight arrow cells came up in eight **different, arbitrary colours** after a power cycle and
stayed that way. Any writer on the paint path — indexed or not — would have overwritten that garbage
within one tick. Uninitialised values that persist while the page is displayed are proof that
nothing writes them at all, which no scan of the code could have established on its own.

**Consequence for the project:** the display path is live (the cells are read and painted every
tick) and the writer is genuinely absent, so `docs/LEVELING-PAGE.md` §5's appended per-tick routine
is not merely the cheapest route — it is the only one.

---

## PC.58 — a `wtype-8` readout repaints from a cell an appended routine writes; the Pump Link button was reading the wrong cell — `observed-on-hardware`, 2026-09-04

`mcf-experiment-v15.bin` on the coach, flashed and verified. Three
observations that settle things the file could not:

1. **Live repaint.** The `Auto Off` minutes readout (a `wtype-8` on spare cell 215, written by the
   pump routine's `strb` every paint tick) changed **immediately** on an arrow press, with the page
   left open. The widget redraw dispatch is still unlocated in GuiCode, but the behaviour it
   produces is now known: a value widget draws the cell's current contents every tick. This is the
   premise of the register-readout design, and it holds.
2. **The sentinel path is visible.** On power-up the readout showed `30` for a moment, then the
   stored value — the routine writing the default to the display byte while the `0xEE` slot still
   reads `0xFFFF`, until the G12's first sweep (~5 s, PC.42).
3. **A recolour box follows its fill index, not its touch.** The Pump Link button drew the Low
   Volts arming colour because its cloned box kept fill index 45. Confirmed by the timing: it
   read "disabled" only before the first `0x2F` sweep after a power cycle. The standing rule
   again — a control is three independent records, and cloning one carries its cell with it.

---

## PC.59 — the first on-screen register readout, and a byte cell's sentinel draws dashes — observed-on-hardware, 2026-09-04

`mcf-experiment-v17.bin` on the coach, flashed and verified
2026-09-04. Setup: v17's `minutes-home` op (`add_register_readout`) put
the Auto Off minute byte of `DGN_CONFIG` selector `0xEE` on HOME (object 4) at `(300, 357)`, on
display cell **219** (SRAM `0x20039f51`, tier 2 — an odd spare byte, `fresh=False`), driven by a
44-byte per-tick copy routine chained onto the hook ahead of the AES Low Volts bridge, the pump
routine and the Pump Link follower: `0x259e50 (minutes-home) -> 0x259df4 (aeslowvolts) ->
0x259ca8 (pumpauto) -> 0x259bcc (pumplink) -> 0x247bf8 (firmware)`.

Five observations, mapped to that entry's checklist:

1. **The HOME number equals the AES page's own `Auto Off` minutes.** The copy routine correctly
   mirrors the same `0xEE` register the pump section already reads (PC.58).
2. **Changing the minutes on the AES page and returning to HOME shows the updated value.** The
   copy is per-tick, not latched at page-enter — the same live-cell discipline PC.58 established
   for the pump section's own readout, now confirmed for a second, independent widget reading
   the same register.
3. **Right after a power cycle, HOME reads dashes, then `15`** (the stored Auto Off minutes).
4. **Nothing else on HOME visibly moved** — the new object does not disturb the page's existing
   layout.
5. **Everything the v16 checklist covers still holds** on the combined v16+v17 run (Pump Link,
   Low Volts, the minute clamp, the bath light follower, the timer, power-cycle persistence).

**The sentinel-to-dashes mechanism, observed on hardware for the first time, on a BYTE cell.**
`minutes-home` copies the `0xEE` slot's raw byte through unmodified — no default-writing logic
like `pumpauto`'s own routine (PC.58 item 2, which is a different mechanism: that routine
explicitly writes `MINUTES_DEFAULT` (30) to its display cell while the slot still reads
`0xFFFF`). Here the slot reads `0xFF` (255) until the G12's first sweep (~5 s, PC.42), and this
readout's directive has field width 2, so `GUICODE.md` Finding 65's width-overflow rule applies:
a value wider than its field draws as dashes (`cmp r1,r5 / blo` `0x002528A4`, `movs r4,#0x2d`),
not the raw three digits and not a blank. That is exactly what the coach showed — dashes, then
`15` once the G12's sweep landed. Finding 65 was `proven-from-code` only, corroborated so far by
stock's own `I/C Firmware Rev. ----`; PC.59 is its first `observed-on-hardware` confirmation from
a build this project authored, and the first for a cell this narrow (a single byte, tier 2).


## PC.63 — a G12 update captured on the wire: the upload protocol decoded, and the flashed section reconstructed byte-for-byte — observed-on-hardware, 2026-09-08

**Method:** `candump -L can1` running on the bus host for 259 s while the owner performed a normal
USB-stick update and selected **row 3 (`G12`)** on the MANUAL LOAD screen — the row every previous
captured flash had skipped. An empty `LogFile.txt` was placed in `0:/SpyderConfig` first, so the
loader's own account of the same event is beside the capture.
`captures/pc63-g12-network-upload-2026-09-08.log` (19,353 frames),
`captures/pc63-loader-logfile-2026-09-08.txt`. Decoder: `src/fireflymcf/upload.py`,
pinned by `tests/test_upload.py`.

This is the experiment the section above ("The G12 network-upload protocol, on the wire") named as
the one thing missing, and it closes it.

### Result 1 — the transfer, and it is a device-driven pull

The loader entered at t≈196 as source address **`0xF9`**, ran the enumeration already decoded
(broadcast then directed `1FED8` request at t≈220.7/221.7, four `0FF01`, the `0FF02` dump), and at
**t=226.857** began the upload. It ended at **t=228.652**: **1.79 s, 2,410 frames, 1,343 frames/s**
— about 69% of a 250 kbit/s bus, which is why it needs the priority it takes.

The channel is **PROPRIETARY_A at priority 4**: `10EF9CF9` loader→G12, `10EFF99C` G12→loader —
id bits 28-26, so `0x10……` is 4, where the coach's ordinary traffic is 6 and even the loader's own
directed request (`14EA9CF9`) is 5. The priority is not incidental: it is the highest thing on this
bus, so the coach's 1 Hz traffic keeps running underneath the transfer without breaking it up.
*(This paragraph read "priority 0" until 2026-09-08 — a slip, not a decode error; nothing depends
on it, and `upload.py` matches on `PF == 0xEF` and the source address, never the priority.)*

Every one of the 1,204 G12 frames has the same form:

```
10EFF99C#C1FF000000FFFFFF      C1 FF <offset u24 LE> FF FF FF
10EF9CF9#071620660B061234      the 8 bytes at that offset
10EFF99C#C1FF080000FFFFFF      next offset
10EF9CF9#0A00001000500060
…
10EFF99C#C1FFA02500FFFFFF      last offset, 0x25A0
10EF9CF9#000000000000BB74
```

Offsets run `0x0000` → `0x25A0` in steps of 8 with no repeats and exactly one gap, at `0x418`,
where the capture lost the request and its data together. **So the device asks and the loader
answers** — `MCF-FORMAT.md` §4.4 derived that
from `cli.bin`'s receive-then-transmit shape at `0x20015aae`/`0x20015b68` and labelled it
`inferred-from-code`; it is now `observed-on-hardware`.

**And both opcodes are in the MCF, not the firmware.** The `G12` descriptor's tagged entries
(`MCF-FORMAT.md` §2) carry `0x41` `ConfigCmd` = **`0xC0`** and `0x42` `DataReqCmd` = **`0xC1`** —
the two bytes on the wire — read by the loader at `0x2001565a` and defaulting to `0xFF` when a
descriptor omits them. **All three builds carry the same pair, and none carries `0x43`
`ConfigCmd2`**, so the third command the loader can send (`0x20015934`) has never run on this
corpus. *An MCF therefore tells the loader how to talk to the device it is about to write.*

### Result 2 — each 32-bit word is sent byte-reversed, and the payload is the flashed image

The wire's first eight bytes are `07 16 20 66 0B 06 12 34`. The G12 section's first eight are
`66 20 16 07 34 12 06 0B`. That is **per-32-bit-word byte reversal**, which is the `strb` branch
pair at `cli.bin` `0x20015b46` versus `0x20015bba` — the loader has both orders and uses this one.
*The same byte-order trap `docs/G12-CODE.md` records for the `0x67` frame table, in a third place.*

Undo it and the transfer reconstructs to **`0x25A8` bytes — the exact length of an MCF `G12`
section** — of which **9,632 arrived**; one chunk, at `0x418`, lost both its request and its data
and is reported rather than silently zeroed. Compared against the section of the image the owner
actually flashed:

| candidate | differing bytes |
|---|---|
| `mcf-experiment-v22-notankbcast.bin` G12 | **0** |
| stock `firefly-13.6/mcf.bin` G12 | 10 |
| `mcf-experiment-v21.bin` G12 | 10 |

**Byte-for-byte, across every one of the 9,632 bytes the capture saw.** And the ten that separate
it from stock are exactly PC.62's edit: the four `blx r3` → `nop` tank-broadcast sites plus the
section checksum word. **The bus carries enough to reconstruct the flashed section in full** — the
test rebuilds v22's G12 from stock with `tankbroadcast.suppress` and compares, so neither side of
that comparison ever reads the other.

### Result 3 — the loader's log and the bus agree, and the G12 reboots afterwards

The stick's `LogFile.txt` records `Touch Area 3 release / Start Network Config Load / Upload
Complete / Config Load Success`, and its enumeration block **names every ParamID this project had
only inferred from the ordered string table** — `0x0C` = `HwVer/LcdDescType`, `0x0D` = `HwOpt`,
`0x0E` = `BtldrVer`, `0x40` = `LdAddr`, `0x80`-`0x82` = the `Mcu*` triple. The per-group counts it
lists are **10 / 7 / 6 / 6**, exactly the byte-7 counts declared in the four `0FF01` frames. Two
sources, neither derived from the other, and the `0FF01` count field is now confirmed by name.

At **t=229.4**, 0.8 s after the last data frame, the G12 requests its own address claim
(`18EA9CFE#00EE00`) and re-claims at 229.565 (`18EEFF9C#0000A024F81F0080`): **the device restarts
itself when the config load completes.** No explicit success frame is sent — every one of the
1,204 device frames is a `C1` request, so the loader's `Upload Complete` is inferred from the last
offset, not from an acknowledgement.

### A correction the decoder earned, and what is still undecoded

**The first cut of this entry claimed three control frames and two dropped chunks. Both were
artifacts of the decoder, not of the capture.** `10EF9CF9#04FFC8010200FFFF` at 227.144 is not a
command at all: it is the **data for offset `0x1B0`**, which the log shows *ahead of* the
`C1FFB00100` that asked for it. Pairing strictly request-then-data mislaid it, reported its bytes
as a mystery command, and left `0x1B0` looking dropped. `upload.py` now pairs the two streams
**FIFO** — which is the true wire order, since the protocol is strictly alternating and only the
log reorders — and flags `ambiguous` if the counts ever disagree. The recovered chunk unswaps to
the section's own bytes at `0x1B0`, which is what makes the recovery a fact rather than a guess.

*The shape: a decode that reports an anomaly should be suspected before the evidence is. Two
"dropped" frames and an undecodable command were one ordering artifact.*

Two loader frames really are control, both before the first request:

```
226.857  002501AD0369CF01     [0]=0, [1:3]=0x125 — an 11-bit field from a 0x2c-stride
                              table (0x20015786, ubfx #0,#0xb); [3:8]=AD 03 69 CF 01,
                              fixed literals from `movs`. What 0x125 selects is UNKNOWN.
226.949  C0000000A425B0CE     ConfigCmd (descriptor tag 0x41). [1] and [4:6] are the
                              length 0x25A4 — the section less its 4-byte stored
                              checksum — split >>16 / low / >>8; [6:8] is a BYTE SUM of
                              the payload mod 2^16, LITTLE-ENDIAN (decoded 2026-09-08).
```

**The check word is decoded, and that is what makes a write possible at all.** The loop at
`0x20015880` reads **one byte** (`0x20015890` `ldrb.w r3,[sp,#0x24]`, after a `movs r2,#1`
length-1 read) and accumulates it **inline** at `0x200158b4`-`0x200158bc`: `ldrh.w r2,[sp,#0x1a]`
/ `add r3,r2` / `uxth r3,r3` / `strh.w r3,[sp,#0x1a]`. And `sum(section[:0x25A4]) & 0xFFFF ==
0xCEB0`, which is the observed `B0 CE` little-endian — arithmetic and code agreeing exactly.
*This block previously said "accumulated 4 bytes at a time by `0x20000e2c`", which was wrong on
both counts:* `0x20000e2c` is the **other** checksum mode — what looks like the STM32 CRC unit,
selected by `cbz r7` at `0x20015894`, its `uxth`'d return landing in the same `[sp,#0x1a]` slot —
and it is not the path this frame took. **What sets `r7` is unidentified**, so a generator must
prove itself against this captured frame rather than assume the mode.

So of the header's eight bytes only the 11-bit field in the opening frame is still unaccounted
for. `upload.Upload.control` surfaces both.

### Result 4 — the full DGN inventory, and it is five DGNs

Everything the update itself uses, counted over the capture. **Five DGNs**, and only two of them
carry the config:

| DGN | id | dir | n | role |
|---|---|---|---|---|
| `0EA00` REQUEST | `18EAFFF9` | loader → all | 1 | asks for `1FED8`, broadcast, t=220.7 |
| `0EA00` REQUEST | `14EA9CF9` | loader → G12 | 1 | the same ask, **directed and priority 5**, t=221.7 |
| `0FF01` GENERIC_UPLOAD_STATUS | `18FF019C` | G12 → all | 12 | four per request — one per upload target, each declaring its parameter count in byte 7 |
| `0FF02` (parameter report) | `18FF029C` | G12 → all | 29 | one per parameter, 10+7+6+6 as declared |
| `0EF00` PROPRIETARY_A | `10EFF99C` | G12 → loader | 1204 | every one a `C1` chunk request |
| `0EF00` PROPRIETARY_A | `10EF9CF9` | loader → G12 | 1206 | 2 control + 1204 chunks of 8 bytes |
| `0EE00` ADDRESS_CLAIMED | `18EEFF9C` | G12 → all | 1 | the reboot, **0.913 s** after the last chunk |

Two asymmetries worth carrying. **`0FF01` is periodic and `0FF02` is not:** the G12 broadcasts its
four `0FF01` frames about every 14 s whether or not anyone is updating it (67 in this capture, 12
of them inside the window), so a passive listener can read a G12's firmware and config versions off
any coach without touching it. `0FF02` appears **only** in answer to the `1FED8` request — 29 in the
capture, all 29 in the window. And **the transfer never uses transport protocol**: no BAM, no
`0EC00`, no `1FED8` beyond the request. It is 1,204 single-frame round trips.

The loader also sources two DGNs that are not part of the protocol: `19FECAF9#0584FFFFFFFFFFFF`
**DM_RV instance 5 at 1 Hz** for as long as it is on the bus (56 frames) — byte-identical to the
panel's own `19FECA9F#0584FF…`, which is the clearest single sign that the loader *is* the panel
running other code — and one `18EBFFF9#0000FFFFFFFFFFFF`, a lone TP.DT-shaped frame with sequence
byte 0 and no BAM before it, as the loader's very first frame. It is malformed as transport and
nothing answers it; **recorded, not explained.**

Everything else on the wire during those 59 s is the G12's ordinary traffic continuing underneath
(`15FCE` sweep, `1FFB7`, `1AAxx`, `1FEDA`, `0BFC1` …) — untouched by the update, which is what
priority 4 buys.

### Three things this changes

- **`MCF-FORMAT.md` §4.4 moves from `proven-from-code` to `observed-on-hardware`**, with the
  device-pull direction, the framing and the word swap all confirmed. Nothing in the code
  derivation had to be withdrawn.
- **The G12 config region is readable off the bus during any update.** Anyone who can sniff CAN
  while a coach is flashed recovers the exact `G12` section, checksum included. Worth knowing
  before handing an image to someone.
- **The application at `0x08006000` is still out of reach, and this capture says why.** It is
  `UploadType 1`, advertised by the same device on the same protocol — but the protocol only ever
  *writes*, and no MCF carries a payload for that target. The standing ceiling behind
  `docs/G12-CODE.md`'s open items is unmoved: this gives a write path, not a read one.


## PC.64 — the G12 enumerated passively from a third node, no frame transmitted — observed-on-hardware, 2026-09-08

`fireflymcf-g12flash probe --interface can1` on the bus host, listening 20 s and **transmitting
nothing at all**. It reported all four upload targets, and every field matches the `0FF01` frames
of PC.63 exactly:

| DeviceInst / UploadType | version | release | bank | declared params |
|---|---|---|---|---|
| 0 / `0x01` application | `0x0300` | `0x64` | `0x1F` | 10 |
| 0 / `0x02` **config — an MCF's `G12` section** | `0xFFFF` | `0xFF` | `0x1F` | 7 |
| 0 / `0xFF` device/bootloader | `0x0203` | `0xFF` | `0x1F` | 6 |
| 1 / `0x01` application | `0x0300` | `0x62` | `0x1F` | 6 |

**Three things this settles.**

1. ~~**`0FF01` really is unprompted and periodic**, not a response to the loader.~~
   **WRONG, corrected the same day — see PC.66.** `0FF01` is not a device broadcast at all: the
   **panel** polls `1FED8` every 14.0 s and the G12 answers within ~6 ms. What is true is the
   narrow claim — the frames do not depend on the *loader*, and this run had none. The regularity
   belongs to the poller.
2. **A third node can enumerate a G12 without transmitting.** Anyone on the bus can read which
   firmware and which config a Firefly G12 is holding, passively. Worth knowing in both
   directions: it is how you check an MCF matches a coach, and it is one more thing an RV-C bus
   discloses to anything plugged into it.
3. **The device is unchanged since PC.63.** Same versions, same counts, one day and one flash
   later — so the probe is reading the device, not replaying anything.

**And it found a defect in our own reporting, which is the useful part.** Every target printed
`[INCOMPLETE: declared 10, heard 0]`. That is not a fault: `0FF02` answers a request and nothing
else, so a passive listen can *never* carry parameters. The label would have fired on every
passive run and trained the reader to ignore the one case where it means something. `_describe`
now says `(10 parameters, not requested)` when nothing was transmitted and reserves `INCOMPLETE`
for a dump that actually came up short after a request. *An alarm that always fires is worse than
no alarm.*

**Still open — rung 2.** Whether the G12 answers a `1FED8` REQUEST from a source address that is
not the panel's loader (`0xF9`) is untested; that is the one question the passive rung cannot
reach, and the whole write path rests on it.


## PC.65 — the G12 answers a stranger, and hands over its whole flash map — observed-on-hardware, 2026-09-08

`g12flash.py probe --interface can1 --request --target 0x9C` from the bus host: **one** 8-byte
`1FED8` REQUEST, sent from source address `0xC0`, after a 20 s listen confirming `0xC0` was free.

**Result 1 — the G12 answers a requester that is not the panel's loader.** All 29 parameters came
back, every value identical to the ones Firefly's own loader logged in PC.63. The enumeration
half of this protocol is **not gated on source address `0xF9`**, so nothing has to impersonate
the loader to read a G12. The probe's third rung (`--as-loader`) is therefore a rung that never
needs climbing, and it stays only for the case where some other device does gate.

*This does not license the write side.* `C0` `ConfigCmd` may still be gated on `0xF9`; a
permissive **read** says nothing about a **write**, and stage 2 must establish that separately.

**Result 2 — the device's whole flash map, off the bus.** Three targets name a `LdAddr` and two
name `McuFlashSize = 0x100`. The units are stated nowhere, but **KB is the only reading on which
all three load addresses land inside the part** — and on that reading the three regions tile it
exactly, no gap and no overlap:

| region | extent | size | upload target |
|---|---|---|---|
| **bootloader** | `0x08000000`-`0x08006000` | 24 KB | DeviceInst **1** / UploadType `0x01` |
| application | `0x08006000`-`0x08038000` | 200 KB | DeviceInst 0 / UploadType `0x01` |
| config — *an MCF's `G12` section* | `0x08038000`-`0x08040000` | 32 KB | DeviceInst 0 / UploadType `0x02` |

256 KB flash, 64 KB RAM (`McuRamSize = 0x40`), `McuDevID 0x04351001` — one MCU, reported
identically by both device instances, which is what makes "DeviceInst 1" a second *target* on the
same chip rather than a second chip. `tests/test_upload.py` pins the tiling.

**Result 3 — and this is the dangerous one. The bootloader is an advertised, writable target.**
DeviceInst 1 / UploadType `0x01` is `LdAddr 0x08000000`: flash base. The device offers its own
bootloader for upload on the same protocol, by the same commands, distinguished from the
application only by a device-instance byte. Since the USB path is this same protocol
(PC.63), a write that lands there has **no recovery route at all** — the stick could not fix it
either. Stage 2 must address `UploadType 2` on DeviceInst 0 and refuse everything else by
construction, not by care.

**Result 4 — growth is not what constrains a G12 edit.** The `G12` section is `0x25A8` = 9,640
bytes in a 32 KB region: **29% used**. Same-size editing is this project's discipline for reasons
that have nothing to do with the device running out of room.

*Standing shape worth carrying: a permissive read path is not evidence of a permissive write
path, and a device that advertises a target is not inviting you to write it.*


## PC.66 — `0FF01` is an answer, not a broadcast: the 14-second beat is the PANEL's — observed-on-hardware, 2026-09-08

Raised by the owner asking whether the probe was worth pointing at the Lyra panel. Answering it
from the corpus first (the standing rule) overturned something written the same day.

**Across 45 capture files and 1,069,982 frames**, `0FF01`/`0FF02` come from **`0x9C` alone** —
4,815 and 290 frames, never one from the panel. What the panel does instead is **ask**: 1,187
broadcast `1FED8` REQUESTs from `0x9F`, against 10 from the loader `0xF9`.

In PC.63's capture the panel polls **14 times on an exact 14.0 s beat**, and **55 of the 67
`0FF01` frames land within 100 ms of a poll — median 5.7 ms**. The remaining 12 answer the
loader's own two requests inside the update window.

**So PC.64 Result 1 was wrong.** `0FF01` is not periodic on the device's initiative; the G12 is
mute until asked. The ~14 s cadence that made it look self-driven is the **panel's polling
cadence**, and the passive rung of `g12flash.py probe` works only because that poller is present.
On a bus with no panel, a passive listen hears nothing — and the silence would mean *no poller*,
not *no device*. Corrected in `upload.py`, `g12net.py`, `g12flash.py`, `MCF-FORMAT.md` §4.4 and
the spec, and pinned by `test_the_status_frames_are_answers_to_the_panels_poll_not_a_device_broadcast`.

*This is the project's own recorded failure shape, in a new place: **an observation of a system
with one input describes the pair, not the consumer** (`render.py`'s tank quarters, PC.62). A
regular beat was attributed to the sender without looking for a poller. The cheap guard is to ask
"what else is on this bus, and is any of it asking?" before calling a rhythm intrinsic.*

**And it answers the owner's question.** The panel is a *requester*, not an upload target: it has
never once answered, and it is the thing doing the asking. Pointing the probe at `0x9F` is
therefore expected to return nothing. The one thing the corpus cannot rule out is a **directed**
request — no one has ever sent `0x9F` one, and the G12 is known to treat directed and broadcast
differently (broadcast draws status only; directed draws the full parameter dump). So a single
directed frame at `0x9F` remains the only way to close it, and the loader's own log points the
same way: its `Desc Index 0/1/2` LCD targets were matched **locally**, against the panel itself,
while only `Desc Index 3` carried a `Target src addr`.


## PC.67 — the Lyra panel is not an upload target, asked directly — observed-on-hardware, 2026-09-08

`g12flash.py probe --interface can1 --request --target 0x9F`: one `1FED8` REQUEST directed at the
**panel**, from `0xC0`, listening 20 s afterwards — longer than the panel's own 14.0 s poll, so a
reply would have been caught.

**`0x9F` did not answer.** Nothing. The only `0FF01` frames in the window came from `0x9C`,
answering the panel's own poll (PC.66), and no `0FF02` appeared at all.

**So the panel is a requester, not a network upload target — closed.** Three independent legs now
say the same thing:

1. **1,069,982 frames, 45 captures, zero `0FF01`/`0FF02` from `0x9F`** — and 1,187 `1FED8`
   requests *from* it.
2. **The directed form, which was the one gap**, since the G12 treats directed and broadcast
   differently. Asked, declined.
3. **The loader's own log**: `Desc Index 0/1/2` are `Type LCD` and were matched **locally**,
   against the panel itself, with no `Target src addr` line; only `Desc Index 3` carried one
   (`9C`). The MCF's LCD targets are matched against the local device, never enumerated over CAN.

Consistent with the architecture: `LCD Firmware` and the `GuiCode`+`GuiData` pair are written into
the panel's own flash from a USB stick and never touch the bus, while the `G12` row is a network
load. **There is no bus path to the panel's firmware, and this run is the direct test of that.**

**It also caught the second form of a reporting defect.** All four `0x9C` targets printed
`[INCOMPLETE: declared 10, heard 0]` — but the request went to `0x9F`. Those devices were never
asked, so they had no reason to send parameters, and calling them incomplete is an alarm about
nothing. PC.64 fixed the passive case by consulting `transmitted`; the real predicate is
**whether *this device* was addressed**, which `Enumeration.was_asked` now decides. The tool also
says outright when the device it asked did not reply, so a reader is not left inferring it from
an absence. *The first fix was right about the symptom and wrong about the rule.*


## PC.69 — the loader demands `mcf.bin`: the QUADSPI store is unreachable without it, and the loophole is CLOSED — observed-on-hardware, 2026-09-08

The refined test of PC.68, run with the bus host's USB gadget carrying **`SpyderConfig/cli.bin` and no
`mcf*.bin`**. The panel booted into the loader and stopped at:

```
Master Config File Missing

Try regenerating config on USB flash drive

Remove USB Flash Drive to resume normal operation
```

No menu, no targets, nothing offered. `cli.bin`'s resource blob carries the three tiers of that
check — `'Error accessing Master Config File'` (`0xC040188D`), `'Master Config File Missing'`
(`0xC0401935`) and `'Master Config File Invalid'` (`0xC04019D4`) — so a missing, unreadable and
corrupt `mcf.bin` are distinguished, and all three are dead ends.

**So the loophole is closed, in both directions.** PC.68: no stick, no loader. PC.69: loader but
no `mcf.bin`, and it refuses before offering anything. **There is no route to a `G12` section that
does not begin with an MCF file you already have.**

**What this does and does not retract.** The QUADSPI findings of `MCF-FORMAT.md` §4.4a stand as
code: the container format, the two source types, and the fact that `cli.bin` *programs* the NOR
(Quad Page Program at `0x20006900`) are all still read off the instructions. What is retracted is
the *practical* reading — the store is **not** an independent image bank you can upload out of. It
is reachable only on a path that begins by loading an MCF, which is consistent with it being where
the loader **puts** GuiData rather than somewhere it keeps a spare copy for you.

**The consequence for the write path is the one that matters, and it is unchanged and now firm:**
there is no read-back in this protocol and no way to make the device hand its config back, so
**the MCF you are running is the only backup that exists**.
That limit is no longer "probably" — it has been
tested from both ends.

*Shape worth carrying: a capability read out of code is not a capability you can reach. The
QUADSPI source path is real, decoded and correct — and two boots established that nothing a user
can do gets to it.*


## PC.68 — no USB stick means no loader: the QUADSPI source is unreachable from a cold boot — observed-on-hardware, 2026-09-08

The test proposed by `MCF-FORMAT.md` §4.4a. The owner booted the panel **with no USB stick
inserted**, and it came up in the **normal GUI** — no boot menu, no loader.

**So `cli.bin` does not run unless a stick is present**, and the QUADSPI-sourced upload path
(source types 4/5, §4.4a) cannot be reached from a cold boot however capable it is. The "recover
your own `G12` section with the boot menu and a sniffer, no file" version of the loophole is
**closed, negative**.

**Two things it does establish, both useful.**

1. **SA `0xF9` is genuinely free during normal operation.** The loader is the only thing that
   claims it and it is not running, which is why the corpus shows `0xF9` only inside stick-boot
   sessions, and why `g12flash.py`'s `--as-loader` rung is safe to reach for.
2. **The refined question is now the interesting one, and it is cheap.** The loader needs *a
   stick* — not necessarily *the right file*. If it will run from an empty volume and still offer
   a QUADSPI-sourced `G12` load, then an owner needs no MCF file to recover their section, which
   is nearly the whole benefit of the closed version.

**The volume must carry `cli.bin` and nothing else — an EMPTY volume is a null test.** Caught by
the owner asking "empty folder or only `cli.bin`?", which is the right question: **`cli.bin` *is*
the loader and it ships on the stick** (§4.1 — `mcf.bin` + `cli.bin` in a directory named
`SpyderConfig`; the owner's own stick carried `SpyderConfig/LogFile.txt`). An empty volume has no
loader to run and reproduces this entry's result exactly, teaching nothing.

The test is therefore **`SpyderConfig/cli.bin`, no `mcf*.bin`**: the loader runs, finds no image
file, and we learn whether it can still enumerate and offer a QUADSPI-sourced load. The bus host's
`~/firefly/usbstick.sh` provides the gadget; the volume needs that one file added. Its safety
argument survives unchanged — the panel flashes files named `mcf-*.bin` and there are none.

**Bonus oracle:** the loader writes `SpyderConfig/LogFile.txt` to the stick, so even if the menu
offers nothing, its own account of what it looked for and found is readable afterwards.

*Prior evidence that the two are separable: PC.63's loader log opens `No Configurator files found`
and then enumerates and matches four descriptors anyway — so "found a file" and "has targets to
offer" are already known not to be the same condition on this loader.*


## The `1FED8` config identity, and the loader's node cache — proven-from-code, 2026-09-08

The last undecoded field of the G12 upload protocol is decoded, and it turned out to name a DGN
this project had never read.

**`0x125` is the RV-C NAME manufacturer code — 293, Firefly Integrations.** It is bits 0-10 of
`1FED8` payload `[0:2]`, and the loader echoes it in the opening frame of a config upload.

**The `0x2c`-stride table at `0x20044554` is the loader's per-source-address node cache** (SA
`0x00`-`0xFC`), and every field of it is read off its writers:

| offset | contents | writer |
|---|---|---|
| `+0x00`-`0x08` | the node's **`1FED8`** payload | `FUN_20014ef4` (`0x20014f1a`) |
| `+0x08`-`0x28` | up to four **`0FF01`** payloads | `FUN_200174fc` (`0x20017544`) |
| `+0x28` | `FwRlsLetter`, only from `DeviceInst 0 / UploadType 1` (`cmp r2,#0x100`) | `0x20017516` |
| `+0x29` | present flag | `0x20014f10` |
| `+0x2a` | count of stored `0FF01`s — **logged as `Num Gen Upld Status Rxd = 4`** | `0x20017558` |
| `+0x2b` | running sum of declared parameter counts — 10+7+6+6 = **29** | `0x20017564` |

The dispatch at `0x20017896` settles which DGN lands where: `r7 = 0x03FFFF00` masks the id, then
`0x01FED800` → `FUN_20014ef4` (slot 0), `0x00FF0100` → `FUN_200174fc` (slots 1-4), `0x00FF0200` →
`FUN_20017584`. Exactly the three DGNs subscribed at `0x20017828`/`0x2001782e`/`0x20017836`.
`FUN_20014634` is the matching accessor — a `tbb` switch whose nine arms each read one field.

**`1FED8` is a one-frame config identity, and the G12 broadcasts it unprompted.**
`19FED89C#25B11F3006163412`, one distinct payload, 1,204 frames across the corpus. Every field
checks against Firefly's own log, which prints five of them by name:

| bytes | value | the loader's log |
|---|---|---|
| `[0:2]` bits 0-10 | 293 | — (manufacturer code) |
| `[0:2]` bits 11-15, = `[1] >> 3` | `0x16` | `FuncInst = 16` |
| `[2]` | `0x1F` | `FuncBank = 1F` |
| `[3]` | `0x30` | **undecoded**, read as two nibbles at `0x20014316` |
| `[4]` | `6` | `ConfigRev = 6` |
| `[5]` | `0x16` | `ConfigID = 16` |
| `[6:8]` | `0x1234` | `ConfigType = 1234` |

**Corroborated off the wire.** 293 is also bits 21-31 of the NAME in the `ADDRESS_CLAIMED` of
**both** Firefly devices — G12 `0x9C` (`0000A024F81F0080`) and panel `0x9F` (`0000A024A0650080`) —
while a third-party node `0xDF` claims 1036. Eleven bits is the width of exactly one NAME field,
so the width, the value and its variation across vendors all agree.

**Two consequences.**

1. **The upload's opening frame is computable, not replayable.** `upload.opening_frame_payload`
   builds `00 <mfr u16 LE> AD 03 69 CF 01` from any device's own `1FED8`. The stage-2 design's
   assumption that a constant had to be replayed blind is retired.
2. **`1FED8` is a free passive read of which config a device holds** — manufacturer, `FuncBank`,
   `FuncInst`, `ConfigRev`, `ConfigID`, `ConfigType` — in one unprompted frame, without the 29
   `0FF02` frames and without transmitting. `g12flash.py probe` now reports it.

*One residual: byte `[3]` (`0x30`), split into two nibbles and copied into the loader's device
table at `+0x4CCC`/`+0x4CCD`. Not decoded, and not guessed at.*


## The G12 network-upload protocol, on the wire — `0FF01`/`0FF02` decoded, and nine loader entries with no transfer (2026-09-08)

**No new coach time.** This is a re-read of captures already in `captures/`, prompted by the
owner's question *"how does the G12 firmware update over RV-C?"*. It closes the enumeration half
of that question from the bus, and it **corrects an over-generalisation in PC.37** (below).

### The panel enters loader mode as source address `0xF9`, not `0x9F`

Nine times across four captures the Spyder Config Loader runs and claims **`0xF9`**. Every frame
it ever sends is one of four shapes, and the whole census is nine of each:

```
589.20  18EAF9FE#00EE00              Request ADDRESS_CLAIMED, directed at F9 by the null SA
589.75  18EBFFF9#0000FFFFFFFFFFFF    TP.DT, broadcast
590-611 19FECAF9#0584FFFFFFFFFFFF    DM_RV heartbeat, 1 Hz, code 0x0584 — the loader's liveness
591.57  18EAFFF9#D8FE01FFFFFFFFFF    Request PGN 1FED8, broadcast
591.57  18E8F980#01FFFFFFFFD8FE01    ACKNOWLEDGEMENT: 0x80 NACKs it (byte 0 = 1)
592.57  14EA9CF9#D8FE01FFFFFFFFFF    Request PGN 1FED8, DIRECTED at the G12, priority 5
592.57  19FED89C#25B11F3006163412    the G12 answers
592.57  18FF019C#… ×4                four upload targets
592.57  18FF029C#… ×29               the full parameter dump
```

*(timestamps from `pc37-aes-trigger-attempt-2026-08-24.log`, t relative to capture start; the
panel's normal address-claim `18EEFF9F` follows at +614 s, which is what PC.37 saw.)*

The loader entries are `pc13` (1), `pc37` (1), `pc-1123-session-2026-08-30` (4) and
`pc-147-session-2026-08-30` (3). The broadcast request is NACKed by `0x80` and answered by nobody;
**the directed request one second later is what works**, which is why the loader sends both.

### `0FF01` — GENERIC_UPLOAD_STATUS, one frame per upload target

Four frames, byte-identical in every capture, and they are PC.19's `Src Addr 9C, Num Gen Upld
Status Rxd = 4` seen from the bus instead of from the loader's log file:

| frame | DevInst | UpldType | FuncBank | ver (BE) | FwRlsLetter | params |
|---|---|---|---|---|---|---|
| `18FF019C#00011F010300640A` | 0 | 1 firmware | `0x1F` | `0x300` | `0x64` `'d'` | **10** |
| `18FF019C#00021F01FFFFFF07` | 0 | 2 config | `0x1F` | — | — | **7** |
| `18FF019C#00FF1F010203FF06` | 0 | `0xFF` MCU | `0x1F` | `0x203` | — | **6** |
| `18FF019C#01011F0103006206` | 1 | 1 bootloader | `0x1F` | `0x300` | `0x62` `'b'` | **6** |

Layout: `[0]` DeviceInst, `[1]` UploadType, `[2]` FuncBank, `[3]` `0x01`, `[4:6]` version **big-endian**,
`[6]` FwRlsLetter, `[7]` **parameter count**.

**Byte 7 is the count and it is checked, not fitted**: the four groups declare 10/7/6/6 and the
`0FF02` stream that follows carries exactly 10/7/6/6 parameters for those groups. That is what
drives the loader's `All params received for src addr %X` versus `Missing params for src addr %X`
(`cli.bin` region 2, `0xC0400847`/`0xC040086C`).

### `0FF02` — one parameter per frame, and every PC.19 value reproduced

Layout: `[0]` DeviceInst, `[1]` UploadType, `[2]` **ParamID**, `[3]` `0x00`, `[4:8]` value **little-endian**.
Names come from the loader's own table at `cli.bin` region 2 `0xC04009E1`–`0xC0400AB0`
(`FuncBank`, `FuncInst`, `ConfigRev`, `GuiRev`, `MfgCode`, `ConfigType`, `ConfigID`, `ExtFwVer`,
`FwRlsLetter`, `ExtFuncCode`, `HwVer/LcdDescType`, `HwOpt`, `BtldrVer`, `DevInst`, `UpldType`,
`LdAddr`, `ConfigCmd`, `DataReqCmd`, `ConfigCmd2`, `McuDevID`, `McuFlashSize`, `McuRamSize`).

| frame | ParamID | value | name | PC.19 said |
|---|---|---|---|---|
| `18FF029C#0001400000600008` | `0x40` | `0x08006000` | LdAddr | `0x08006000` ✓ |
| `18FF029C#0002400000800308` | `0x40` | `0x08038000` | LdAddr | `0x08038000` ✓ |
| `18FF029C#0101400000000008` | `0x40` | `0x08000000` | LdAddr | `0x08000000` ✓ |
| `18FF029C#xxxx800001103504` | `0x80` | `0x04351001` | McuDevID | `0x04351001` ✓ |
| `18FF029C#xxxx810000010000` | `0x81` | `0x100` | McuFlashSize | 256 KB ✓ |
| `18FF029C#xxxx820040000000` | `0x82` | `0x40` | McuRamSize | 64 KB ✓ |
| `18FF029C#000201001F000000` | `0x01` | `0x1F` | FuncBank | `0x1F` ✓ |
| `18FF029C#0002020016000000` | `0x02` | `0x16` | FuncInst | `0x16` ✓ |
| `18FF029C#0002040006000000` | `0x04` | `6` | ConfigRev | `6` ✓ |
| `18FF029C#0002070034120000` | `0x07` | `0x1234` | ConfigType | `0x1234` ✓ |
| `18FF029C#0002080016000000` | `0x08` | `0x16` | ConfigID | `0x16` ✓ |
| `18FF029C#0001090000030000` | `0x09` | `0x300` | ExtFwVer | `0x300` ✓ |
| `18FF029C#00010A0064000000` | `0x0A` | `0x64` `'d'` | FwRlsLetter | `'d'` ✓ |
| `18FF029C#00010E0003020000` | `0x0E` | `0x203` | BtldrVer | `0x203` ✓ |

**Every parameter PC.19 read out of `LogFile.txt` is reproduced from the bus, from a capture taken
four days earlier.** Two independent sources, neither derived from the other. `0x0B` reads
`0xFFFFFFFF` (ExtFuncCode, unset on the G12) and `0x0C`/`0x0D` read `0x202`/`1`.

**The enumeration is available on demand and costs nothing.** In normal running the panel polls
`18EAFF9F#D8FE010000000000` (Request `1FED8`) every **14 s** and the G12 answers with the four
`0FF01` — 4,748 of them across the corpus. `0FF02` is the loader-only follow-up: 261 frames, nine
bursts of 29, one per loader entry.

### What is NOT in any capture: the transfer

***SUPERSEDED 2026-09-08 by PC.63 above — the transfer is now captured and decoded.** What follows
remains true of every capture taken before that date, and the census is why PC.63 was worth the
coach time.*

**Nine loader entries, zero network config loads.** In all of them the owner selected the LCD row
(GuiCode+GuiData, which is USB-local), never row 3. The evidence that no transfer occurred:

- PROPRIETARY_A traffic in the entire corpus is `18EF7080` (125,303), `18EF8070` (92,453),
  `19EF6565` (24,087) and one `18EFFFDF`. **Nothing to or from `0x9C` or `0xF9`** — and
  PROPRIETARY_A directed at the loader is the data channel (see `MCF-FORMAT.md` §4.4).
- The busiest second anywhere in `captures/` is 473 frames (`pc52`'s identification storm) against
  a ~50 median. The G12 config section is `0x25A8` = 9,640 bytes at 8 bytes a frame, so a load is
  **≥2,400 frames** of request/response. Nothing of that shape exists.
- The same holds for `~/rvclogs` and the 2025 `GenRVC_FULL_BUS_TRACE` set: ordinary running
  traffic, peak 126/s.

### Correction to PC.37 — "the flash is NOT visible on the bus" is true only of the LCD pair

PC.37's section of that name is right about what it observed and **wrong as stated in general**.
Two things it did not have:

1. The flash **was** visible on the bus, 22 seconds before the address-claim it noticed: the
   loader's `0xF9` entry and the enumeration above are in that same capture at t≈589–593.
2. Its conclusion — *"capturing a flash will not yield the G12 bootloader protocol, so it is not a
   way to reach the G12 application image at `0x08006000`"* — does not follow. It generalised from
   a flash of the **LCD pair**, which is USB-local, to the **G12 row**, which is not: the G12
   section reaches the device over RV-C, and the loader has a whole protocol for it. **Capturing a
   Network Config Load is exactly the way to see that protocol**, and it has never been done.

*The shape to carry: a negative about a mechanism is only as broad as the operation that was
actually performed. The owner pressed one row of a four-row menu.*


## PC.62 — the breaker `−` transmits, the tank broadcast is gone, and a foreign sender drives the stock gauges — observed-on-hardware, 2026-09-07

**`build/mcf-experiment-v22-notankbcast.bin`** (sha256 `008a9f7d…`), flashed by the owner.
Three results, two of which close long-standing items, plus three corrections to things this
project believed.

### Result 1 — `Shore Brkr Size`'s `−` transmits. Defect 0b is CLOSED.

The button that sent nothing in five presses across three sessions (PC.32) put this on the bus,
byte-exact against the prediction made from the disassembly:

```
19FED99F#FF96990FFBFFD2FF     the -   : a D2 FF delta of 0xFFFB = -5
19FED99F#FF96990F0000D2EA     the echo
19FED99F#FF96990FFFFFD3FF     the read-back
19FED99F#FF96990F0500D2FF     the +   : +5, mirrored
```

`GUICODE.md` Finding 68's one-byte fix (`fix_variant_gate`, var 213's compare byte `0x95` →
`0x99`) works. `shore-brkr-minus` is **HARDWARE-VERIFIED**.

**And the floor works too, by a readback that distinguishes the two outcomes.** From a stored 5 A
the owner pressed `−`, `−`, `+`. The value came back **5**, which only one path produces:

| | press 1 `−` | press 2 `−` | press 3 `+` | final |
|---|---|---|---|---|
| gate fires | 5 → 0 | **silent** | 0 → 5 | **5** ← observed |
| gate fails | 5 → 0 | 0 → −5 | −5 → 0 | 0 |

So the control is disabled at exactly 0 A and re-enabled by `+` — 11.23's design, restored.

### Result 2 — v22 silences `1FFB7` and nothing else

A 12-second capture carries **zero** `19FFB7` frames. Every earlier capture carries ~48 (four
instances × 1 Hz). The controls hold exactly as the edit predicted: `18BFC19C` still sends all
four raw tank ADCs (48 frames in the same window) and `19AAB79C` still sends the LPG mirror. So
the four 2-byte NOPs switched off four rules and touched nothing else. `suppress_tank_status` is
**HARDWARE-VERIFIED**.

### Result 3 — a foreign RV-C sender drives the stock gauges, with no MCF edit in the path

With instance 0 unclaimed, `19FFB7AF#002238FFFFFFFFFF` was injected from SA `0xAF` at 2 Hz.
HOME's **FRESH gauge read 61%** — and GREY, GREY2 and BLACK read `--%`. A second injection,
`19FFB7AF#000D64FFFFFFFFFF`, read **13%**.

This was inference an hour earlier ("the receive filter is source-blind, so it should work") and
is now observed. It is the whole answer to the owner's question: silence the G12 on an instance,
publish it from anywhere, and the panel's own gauges follow.

### Three corrections this session earned

**a. The PANEL DOES NOT QUANTISE tank level.** `render.py` recorded "levels are quantised
0/33/67/100% on hardware (owner-confirmed)". The panel computes `relative_level / resolution` and
shows the percent: 34/56 → 61%, 13/100 → 13%. The quarters were an artefact of the only source
that had ever fed it — the G12's `CC`/`CD`/`CE` ladder emits nothing else, so no intermediate
value had ever reached the panel. The 2026-08-15 observation was honest; it just could not
distinguish the panel from its only publisher. Also settled: an unclaimed tank reads the `--%`
no-data sentinel, **not** a stale 0%.

**b. The G12 does NOT clamp selector `0x99` at zero, and the "floor" skips values off the
multiple-of-5 grid.** During the test the stored size was found at **−245 A**, read back from the
G12 as `195FCE9C#99000BFF…` (`0xFF0B` int16). The gate disables the control when the slot reads
**exactly** 0 (`ldrsh.w`, signed compare); the step is 5; so a value not divisible by 5 walks
straight through — `21 → 16 → 11 → 6 → 1 → −4 → …` never lands on 0 and never disables. That is
11.23's design faithfully reproduced, and it is a *disable-at-zero*, not a clamp.

An earlier note that an injected −45 "was applied instantly and read back as 0" is **not**
evidence of a clamp: the value was 45 at the time, so −45 lands on 0 by arithmetic. −245 sitting
in G12 storage is the actual answer.

**c. `0x99` is not in the periodic `15FCE` sweep.** 43 selectors sweep every 5 s and this is not
one of them — consistent with it being the non-relaying selector (`g12rules`' docstring). It
appears only as an echo of a write, which is why a 30-second capture of an idle bus shows none.
**To read a non-swept selector, provoke it:** `cansend can1 19FED9C0#FF96990FFFFFD3FF` and catch
the `195FCE9C#99…` reply. That is how −245 was found and how every value in Result 1 was
confirmed.

---

## PC.61 — an Auto Off timer indicator, and a byte borrow recovered on the wire — observed-on-hardware, 2026-09-06

Two images across three rounds: **`build/mcf-experiment-v20.bin`** (sha256 `b90c3e14…`) and
**`build/mcf-experiment-v21.bin`** (sha256 `4b639367…`). v21 is **FLASHED AND VERIFIED**.

### The headline: PC.47's borrow model holds in the other direction

`add_pump_auto_off(allow_disable=True)` lowers the Auto Off minute floor from 5 to 0 so that 0
disables the feature. Minutes share their u16 with the Pump Link flag and the G12 protects neither
field, so `-` at 0 subtracts 5 from the WHOLE word and borrows out of the low byte. With **Pump
Link ON at 0 minutes**, one `-` press put this on the bus:

```
19FED99F#FF96EE0FFBFFD2FF     the - button: a D2 delta of 0xFFFB = -5
19FED99F#FF96EE0F0000D2EA     the G12's echo of the delta
19FED99F#FF96EE0FFFFFD3FF     the read-back
19FED99F#FF96EE0F0001D1EA     <- the panel's own appended routine: an absolute D1 write of 0x0100
```

`0x0100` is 0 minutes with the link flag ON — the word the borrow ate, recovered exactly by
`pumpauto.clamp`'s rule (a minute byte of `0x100 - STEP` is unreachable from the grid, so it can
only be a one-step underflow; add the step back to the whole u16). The owner saw Pump Link "flip
off briefly, but it immediately returns to on", which is the one-tick window between the G12
storing `0x00FB` and the routine correcting it. `+` from 0 sends `0500D2FF` and needs no
correction at all.

**So a delta that underflows the minute byte into the flag byte is recoverable on this G12**, and
the field order PC.47 argued for (minutes LOW) is what makes the recovery exact rather than a
guess.

### Three defects the coach found, none of which the tooling could

| round | what the panel did | cause |
|---|---|---|
| 1 | the timer glyph drew **one column of pixels** | its record declared no width or height, so the layout gave it a degenerate box `(571,218)-(571,218)` and the panel clipped the glyph to it. `render.py` does not clip text to the record box, so the preview was identical to a correct one |
| 1 | `Off` and `5 Min` drew **at the same time** on the AES page | the panel does not clear a wtype-6 switch's region on an arm change, and a TEXT-ONLY arm has nothing to paint over the arm it replaced. Stock's arms all carry a box, which is what erases |
| 2 | a 0-minute setting **did not stop a countdown already running** | `arm` re-reads the minute count only at `armit`, reached only when the countdown is already disarmed. A countdown armed before the user stepped to 0 kept decrementing and would have fired |

Round 2's is the one that mattered functionally: the pump would have been switched off after the
timer was disabled. Round 1's first is the one worth carrying as a rule — **a visible label MUST
declare a box**; the two-coordinate shape is for records that never draw.

### v21, verified 2026-09-06

| item | result |
|---|---|
| 1. pump on, Auto Off 5+: the whole timer face draws in the button's lower-right corner | **as expected** |
| 2. pump off while on HOME: the mark goes | **as expected** |
| 3. Auto Off to 0 while the pump runs: the mark goes immediately, pump never switched off after | **as expected** |
| 4. AES page reads `0 Min` at zero, no `Off` ghost | **as expected** |
| 5. HOME no longer shows v17's bare minute count | **as expected** |

White on the button's lit cyan is legible — the owner's call, after round 1's first read of
"barely visible" turned out to be clipping rather than contrast.

---

## PC.60 — a stock readout fed past its decode fence, and a switch pinned through a flash — observed-on-hardware, 2026-09-05

**`build/mcf-experiment-v18.bin`** (sha256 `5340767d…`).
The owner flashed it and reported against the checklist:

| item | result |
|---|---|
| 1. `Absorb`/`Float` rows present immediately after the flash, before any power cycle | **present** |
| 2. rows read dashes, not `25.5 V`, with nothing sending `0x1FECC` | **dashes** |
| 3. `cansend can1 19FECCC0#01FFFF80043804FF` (absorb raw 1152, float raw 1080) | **rows show the values** (57.6 / 54.0) |
| 4. PC.55's in-band frame `19FECCC0#01FFFF20011001FF` (14.4 / 13.6) | **confirmed** (run after the rest, 2026-09-05) |
| 5. after a power cycle | rows present, **dashes until the next frame** |
| 6. v17's features (HOME minute readout, Pump Link, Auto Off, six-icon rail) | unchanged |

**What it settles, each for the first time on this coach.**

- **The `0x1FECC` fence is bypassed.** PC.55 proved the stock decode discards any value outside
  12.0–18.0 V. Item 3 puts 57.6 V and 54.0 V on the rows — values no stock path can draw — from a
  40-byte per-tick copy routine writing the rows' own cells (162/163) with the panel's
  `(raw+1)>>1` rounding. The panel's own widgets, formatting and steppers are untouched.
- **Silencing a stock per-tick store works and is necessary.** Item 2's dashes (not `25.5 V`)
  are the `0xFFFF` sentinel passing through our routine into a cell that `FUN_00247BF8` used to
  overwrite with a config byte every tick; the two `strh` → `nop` edits at `0x247C34`/`0x247C3A`
  took, and the hook-chain-before-`FUN_00247BF8` ordering is confirmed the
  hard way — had the stores still run, the rows would have read `25.5 V`.
- **`pin_switch_arm` holds across a flash and a power cycle.** Stock hid the rows after every
  flash (PC.55: the slot-23 gate is power-up SRAM). Items 1 and 5 show them in both states, so
  the firmware's default-arm rule — draw the base when its match value is `0xFFFF` and nothing
  matched — behaves on hardware exactly as `layout.Screen._switch_child` replays it.
- **A receive slot survives a power cycle as `0xFFFF`, not `0`.** Item 5's dashes-until-frame
  means var 206/207's slots come up as the sentinel after a cold boot even though `0x20008164`
  is inside the LCD's `.bss` zero range: the receive-filter init writes the sentinel after the
  zero loop. Consistent with `followchannel.STALE_SENTINEL` and the fenced decode's own
  `cmp r3,#-1` unset test.

Both `add_register_readout`'s `cell_slot` mode and `pin_switch_arm` are `hardware_verified`
from this entry. Item 4, run afterwards, confirmed the in-band path too: 14.4 / 13.6 as on stock, so where both
writers fire — ours every tick and the fenced decode when in band — they agree, and the rows are
right across the whole range.
