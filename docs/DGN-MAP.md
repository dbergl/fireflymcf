# DGN map — what the panel listens to and what it (and the G12) send

A consolidated list of the RV-C DGNs this coach's firmware **receives** and **transmits**, for
13.6. There was no single list before; this is generated from the tables, and the bottom of the
file says how to regenerate it for any build. Counts are entries/bindings, not distinct — one DGN
can have many entries (different instances/selectors).

DGNs are shown as the full 18-bit value (`0x1xxxx`). "low 16 bits" is how they are stored in the
tables (`0x15FCE` stores as `CE 5F` etc.).

## 1. Panel RECEIVES — the variable-table receive filter (§5.4a)

**66 distinct DGNs, 298 entries.** `FUN_0021C760` scans the variable table for each incoming
frame, matches the 18-bit DGN (+ an optional instance/selector byte filter), extracts a byte or
u16, and writes it to a per-entry **status slot**. Each entry is effectively a register minted
from the bus. **Slot address is index-split** (proven-from-code, and the PC.44 lesson): entry
index ≤ 255 → `0x20007FC8 + idx*2`; index > 255 → `0x20008000 + idx*2`
(`followchannel.status_slot_addr`). Adding an entry is data-only and is the general "create a new
register" mechanism; the ~83 distinct-DGN cap only bites a *new* DGN (a new selector on an
already-listed DGN like `0x15FCE` is free).

The heavily-used subscriptions. **Every entry, with what the panel does with it and what it calls
it, is `fireflymcf bus-map MCF`** (`busmap.py`, `GUICODE.md` Finding 82); this table is a summary
of its 13.6 output. Of the 298 entries, 128 reach a painted cell, 11 reach the config-set helper,
13 reach logic only, 132 are **written and never read** and 14 are inert.

| DGN | entries | what it carries (RV-C name; the panel's own words in quotes) |
|---|---|---|
| `0x1FEDA` | 75 | `DC_DIMMER_STATUS_3` — per-output live state (this is what the follower watches for the light); 45 painted, 30 write-only |
| `0x15FCE` | 53 | `DGN_CONFIG` — the G12's config broadcast, one **selector** per entry (options, AES params, floorplan…); the eight OPTIONS selectors reach the config-set helper (Finding 82 §3) |
| `0x1FBDA` | 27 | **G12/G9 input status**, instance `0xA0 + pin` — the input rows `'1 - SHORE SENSE (12V)'` … (corrected 2026-10-19: this row said "G12 output / keypad status") |
| `0x1FFE2` | 15 | `THERMOSTAT_STATUS_1`; instance 0 painted on the climate clusters, instances 1-6 write-only (corrected 2026-10-19: this row said "DC source status") |
| `0x1FFFD` | 11 | `DC_SOURCE_STATUS_1`: instance 1 is `'DC Current'` / `'I/C Amps'` and the `'House'` gauge; `0x83`-`0x87` write-only |
| `0x1FFBF` | 10 | `AC_LOAD_STATUS`: all ten write-only |
| `0x1EBFF` | 8 | **TP.DT — transport-protocol data transfer, 2026-09-03** (`BUS-BASELINE.md` PC.52). Announced by `18ECFFxx` BAM, carries `PRODUCT_IDENTIFICATION` (`1FEEB`) strings: `Wakespeed Offshore*WS500*…` from `0x80`, `Spyder Controls*G12 Panel…` from `0x9C`, `Spyder Controls*7in Lite LCD 1v6r…` from `0x9F`, `Firefly*Standalone Module*…` from `0xFD` |
| `0x1FACE` | 7 | (no RV-C name) the `'Mira PIN:'` / `'Eclipse PIN:'` and `'Mira ID:'` digits of the pairing page |
| `0x1FFAD` | 6 | `ATS_AC_STATUS_1`: `'LINE 1'` / `'LINE 2'` volts, amps, hertz |
| `0x1FFB7`,`0x1FECA` | 5 each | `0x1FECA` = `DM_RV` diagnostics. **Its five filter vars (162-164, 235, 277) are write-only**; DM_RV is consumed from the raw frame by three source-address-keyed parsers (alternator `0x80`, adopted inverter, adopted solar controller) — `GUICODE.md` Finding 77 |
| … 55 more DGNs at 1–4 entries each | | (`0x1FF9C`,`0x1BFC1`,`0x1FFD7`,`0x1FEE8`, the `0x1FE8x` family, `0x1FDFx`, `0x1FEC7-9`, `0x1FFCx`, `0x1FFDx`, `0x1AADC/AAFD` prop, `0x1EF00` prop A, `0x1FACF`, …) |

Note: 14 entries are **mode 0** (match a DGN but write no slot — inert subscriptions); don't read
those as "the panel uses this DGN." `cli`/`guidata.variable_table` gives the per-entry detail
(mode, instance filter, extracted field).

**Tooling entry points:** `register.resolve_register` turns a listed subscription into a slot handle
(a `{"ref"}` consumed by later ops); `register.follow_register` reads that handle to watch the slot
for edges and drive a channel with gate conditions (see its design note (not published)).

**Two `0x15FCE` selectors worth naming individually (13.6):**

- **`0xEF` — AES/AGS mode** (1 = AGS, 2 = AES, 3 = AES-Single). This is the value behind slot 37
  (`0x2003A38C`), the wt-6 match value that selects object 829 (AES) vs 830 (AGS) on the
  Electrical page and gates the Low Volts freeze below. `docs/OBJECT-HIERARCHY.md` §7 and
  its design note (not published) walk the switch and
  the freeze in full. **The AES enable/disable affordance is a GuiCode action, not this DGN
  directly:** touch id `0x7E2` (handler `0x0024C72A`, gate `0x20039F19`) — disabled state routes
  through a "Notice:" interstitial (screen 18 / object 21) before a 3-second-hold Warning screen;
  the AGS mode pill (binding group `0x0080`) jumps straight to that same Warning screen, skipping
  the Notice. Only after the hold does the panel emit the `0x1FED9` selector-`0xEF` config write
  this bullet describes. The compact AES page's own enable pill is stamped with `0x7E2` (not
  `0x0080`) so it gets the Notice flow too — see `docs/OBJECT-HIERARCHY.md` §7.
- **`0x2F` — Low Volts arming.** Var-table entry **42**, receive slot **`0x2000801C`**
  (`0x20007FC8 + 42*2`, the ≤255 index-split base). `FUN_0021C760` writes this slot from the G12
  config sweep **in every mode, AES included** — the subscription itself is never gated. What
  *is* gated is display: stock GuiCode's per-tick paint fn (`FUN_00247BF8`) freezes the one block
  that copies this slot to recolor cell 45 (`0x2003A814`) and update the group-129 variant counter
  whenever mode is AES (`bls.w` at `0x0024A172`, gated on the `0xEF` slot above), so the AES page
  never shows the armed state live even though the register updates normally. The `aeslowvolts`
  recipe op (`add_aes_low_volts_trigger`) reads this already-subscribed slot from an appended,
  un-gated bridge (hook `0x0024EE3C` → routine `0x00259BB4` → tail-call `FUN_00247BF8`) that is a
  **faithful un-gated replica of the frozen block itself** — it calls the firmware's own
  `setVariant` via fn-pointer `[0x20039908]` (r0=0x81, r1=`!armed`) and writes cell 45 directly,
  rather than adding a new receive-filter entry, a spare display cell, or touching the freeze gate.
  A new *consumer* of an existing register, not a new subscription. **Hardware-verified,
  firefly-13.6, 2026-09-01** — toggles both ways in AES. See
  its design note (not published), §3.3 and
  its design note (not published).

## 2. Panel TRANSMITS — the GuiData binding table

**20 distinct DGNs, 746 bindings.** A control's binding emits one of these when touched.

| DGN | bindings | what it is |
|---|---|---|
| `0x1FEDB` | 337 | `DC_DIMMER_COMMAND_2` — command a G12 output/channel (on/off/toggle/level) |
| `0x1FED9` | 224 | config write — set a **selector** on the G12 (options, AES params); this is what an OPTIONS tap sends |
| `0x1FEF9` | 100 | (channel command family) |
| `0x1FBDA` | 24 | G12 output/keypad |
| `0x1FFEE` | 22 | (undecoded) |
| `0x1EA42` | 4 | RV-C request (`0xEA00`) to a specific address |
| … 14 more at 1–6 bindings | | `0x1FF95/87/97`,`0x1FFC4/D0`,`0x1FEBE`,`0x1FACF/CD`,`0x1FFBE/BC/C5/D3`,`0x1FFFE/FF` |

Plus, from **GuiCode** (not bindings): the config-set helper `FUN_00259100` broadcasts **`0x1EE00`**
(config **offset** write) alongside the `0x1FED9` selector write on an OPTIONS tap (PC.34). The
panel never transmits `0x15FCE` — that DGN is the G12's to broadcast.

## 3. G12 TRANSMITS — the rule tables (`0x20`/`0x30`/`0x61`/`0x62`)

Decoded by `g12rules` / `cli g12-rules`. Highlights:

| DGN | records | what it is |
|---|---|---|
| `0x15FCE` | 52 | the periodic **config sweep** — one template per stored selector, broadcast ~every 5 s (this is how the panel learns the G12's config; it re-delivers stored state, which is why options persist across reboot) |
| `0x1FEDB` | 30 | `DC_DIMMER_COMMAND_2` — output commands the G12 originates |
| `0x1FBDA` | 21 | output/keypad status |
| `0x1FEDA` | 9 | `DC_DIMMER_STATUS_3` — output status the G12 reports |
| `0x1FED9` | 5 | config writes (incl. the `0xCF` light-relay record 59, gated on a device this coach lacks) |
| `0x1FACF` | 1 | the F1–F5 synth (floorplan etc.), every 5 s |
| … ~20 more at 1–5 records | | `0x1FFB7`,`0x1FECB`,`0x1AADC/AAFD/AAB7`,`0x1FF95/96/97/9C`,`0x1FFC4/D0`,`0x1FF87`,`0x0BFC1`,`0x1FEBE`/`0x2FEBE`,`0x1FECA`,`0x1FFxx` |

## Regenerate

- **Panel receives:** `fireflymcf bus-map MCF [--dgn X] [--json]` (`busmap.receive_map`): every
  entry with its trace, class and the panel's name. Raw: `guidata.variable_table(data)` → group by
  `.dgn_low`; per-entry mode is `raw[2] >> 4` (0 = inert), instance filter is `raw[4]`/`raw[5]`,
  extracted field `raw[3] & 7`.
- **Panel sends:** `guidata.binding_table(data)` → `raw[0] | raw[1]<<8` is the DGN low word.
- **G12 sends:** `g12rules.all_rules(data)` → `.dgn`; or `cli g12-rules <mcf>` for the decoded
  command/selector detail, `cli g12-inputs` for the input-pin rules.

Names marked "(undecoded)" are well-formed RV-C DGNs whose role on this coach hasn't been pinned;
the numeric value is authoritative.
