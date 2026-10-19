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

## 2. Panel TRANSMITS — bindings, GuiCode, and the LCD's own

**Every way the panel transmits, with what the panel calls each message, is `fireflymcf bus-map
MCF`'s SEND section** (`sendmap.py`, `GUICODE.md` Finding 83); this is a summary of its 13.6
output. Three paths, one queue: every frame goes through the LCD's 64-entry transmit ring
`FUN_0021825C`.

- **Binding groups** (GuiData `+0x778`, 741 live entries in 233 groups). A touch region whose id
  is below the group count selects group `id`; the LCD's sender `FUN_0021B8B4` sends the entries
  whose variant and gesture phase match. The entry's DGN and its payload order are
  `proven-from-code` (Finding 83, `MCF-FORMAT.md` §5.3). 101 groups are reached from a page,
  65 only from objects no page draws, 62 from no touch region at all, 5 are `ff ff` stubs.
- **GuiCode** (701 `blx` sites through the sender slot `0x2003986C`, the host API table's
  `+0x08` word): 554 copy a 12-byte `.rodata` template, the rest build the frame on the stack.
  526 are reached from a touch-dispatcher handler (22 on press, 108 ids on release), 9 sit after
  a touch-id compare (hold consumers), 1 is the config-set helper's `0x1EE00`, 165 are per-tick
  or parser code: 134 in `FUN_00242B70`, which sends what RV-C names
  SOLAR_CONTROLLER_(CONFIGURATION_)COMMAND and SOLAR_EQUALIZATION_CONFIGURATION_COMMAND among
  others (a solar-controller set-up, by those names: inference), 10 of them with a DGN the
  back-walk does not resolve.
- **The LCD firmware's own** (6 call sites of `FUN_0021825C` outside the binding sender
  `FUN_0021B8B4`, in `FUN_0021A114`, `FUN_0021A540`, `FUN_0021CE28` and the USB port's command
  parser `FUN_0021E9E0`; not decoded). Every panel frame in the 13.6 captures that neither of the first two explains is one
  of `1FECA` DM_RV, `1FFFD` DC_SOURCE_STATUS_1 (instance `0x81`, every 10 s), `0EEFF` address
  claim, `0ECFF`/`0EBFF` transport protocol.

| DGN | bindings | reached / unattached / dead | GuiCode sites | what the panel calls it (examples) |
|---|---|---|---|---|
| `0x1FEDB` | 337 | 159 / 75 / 103 | 100 | DC_DIMMER_COMMAND_2 to a G12 pin: the OUTPUTS-page name (`'Water Pump'`, group 44), the control (`'ON'` in `'Light Master'`, group 1; `'Tank Heater'`, GuiCode id `0x851`) |
| `0x1FED9` | 224 | 129 / 26 / 69 | 432 | config write, `FF 96 <sel> 0F <lo> <hi> D1/D2/D3`: the OPTIONS row (`'Bath Fan'` writes `0xE9` on release of id `0x844`), `'Low Volts'` (group 129, `0x2F`) |
| `0x1FEF9` | 100 | 24 / 61 / 15 | 12 | RV-C THERMOSTAT_COMMAND_1 (climate tiles) |
| `0x1FBDA` | 24 | 24 / 0 / 0 | 5 | (Firefly's) the AES `'Start'`/`'Stop'` keys (groups 13/14), `'FRONT'`/`'REAR'`/`'LEFT'`/`'Right'` (groups 221-228); Service Mode `'ENABLE'`/`'DISABLE'` (GuiCode) |
| `0x1FFEE` | 22 | 22 / 0 / 0 | 0 | RV-C LEVELING_CONTROL_COMMAND |
| `0x1EE00` | 0 | | 5 | the config-set helper's `<offset> <value>` write (Finding 23); 33 call sites, 17 from touch handlers, 11 from the receive side's compare block |
| `0x1FEB0`/`1FEB1`/`1FEAD`/`1FDF9`/`1FDFB`/`1FDFD` | 0 | | 112 | built in place by `FUN_00242B70`, no control |
| `0x0EAxx` requests | 4 (`0EA42`, dead) | 0 / 0 / 4 | 21 | J1939 Request: `0EAFF` (6 after a hold-consumer's touch-id compare), `0EA8D`, and 11 to `0EA00 + [cell]`, a destination held in SRAM (`FUN_00242B70`, `FUN_0024F104`) |
| 14 more | 30 | 4 / 8 / 18 | 4 | `1FFBE`, `1FFD3`, `1FFFE`, `1FFBC`, `1FF87`, `1FF95`, `1FFC4/C5`, `1FFD0`, `1FEBE`, `1FACD/CE/CF`, `0EF42` |

The panel never transmits `0x15FCE` — that DGN is the G12's to broadcast.

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
- **Panel sends:** `fireflymcf bus-map MCF` (`busmap.send_map`): every binding group with its
  controls and class, every GuiCode send with what drives it. Raw: `guidata.binding_table(data)`
  → the first u32's low 18 bits are the DGN, `newbutton.binding_payload(raw)` the wire bytes.
- **G12 sends:** `g12rules.all_rules(data)` → `.dgn`; or `cli g12-rules <mcf>` for the decoded
  command/selector detail, `cli g12-inputs` for the input-pin rules.

Names marked "(undecoded)" are well-formed RV-C DGNs whose role on this coach hasn't been pinned;
the numeric value is authoritative.
