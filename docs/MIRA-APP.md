# The VegaTouch Mira phone app and its config

How the **VegaTouch Mira** phone app (Firefly's RV control app, `com.fireflyint.mira`)
is configured, and how to read and write that config ourselves over Bluetooth.

**This is a separate surface from the Spyder panel `mcf.bin`.** The app does not read the
panel firmware; editing `mcf.bin` never changes the app, and none of this project's panel
customisations appear in the app. Proven by capture + code (below). Everything here was
derived from the shipped Android APK (`apk/com.firefly.mira_2.18.15…apkm`, a
Capacitor/Cordova/Ionic/AngularJS web app — all logic is readable JS under
`assets/public/`) and confirmed end-to-end on the reference coach on 2026-10-14.

## 1. The config pipeline

The app's entire UI (which pages, lights, tanks, labels) is a **JSON config**. Source of
truth is **AWS S3**; the Mira module holds a copy; the app renders whatever the module
holds.

- **S3 index**: `https://internal-vegatouch-configs.s3.amazonaws.com/files/floorPlan.json`
  (dev builds use a `dev-` prefix) — `configLoader.svc.js:2604`, `:311`. It lists every
  make/model/floorplan and the S3 `url` of each config.
- **Per-coach config**: `…amazonaws.com/<url>`, where `<url>` from the index is
  **lowercased and spaces→`_`** before the fetch (`configLoader.svc.js:316`). Our coach is
  `jayco/b_van/v13/config.json` (floorplan WD, version `JBv13-042525`).
- **"Update Config"** (Settings → the `downloadUpdate()` button, `settings.ctrl.js:83` →
  `downloadRemoteConfig`, `configLoader.svc.js:2583`): download the latest config from S3,
  render it (`populateData`), **and write it into the Mira module**. In-app help:
  *"retrieve the latest configuration file for the coach … install the file into itself and
  the module it is connected to."*
- The module stores the config and hands it back (`requestConfigFromMira`), so the app
  renders the module's copy. **A customised module config therefore drives the app with no
  S3 involvement** — until someone taps "Update Config", which overwrites it from S3.

## 2. BLE interface  `proven-from-code` + `observed-on-hardware`

The module (advertises as `Mira-<id>`, e.g. `Mira-225523`) exposes a **Nordic UART
Service** `6e400001-b5a3-f393-e0a9-e50e24dcca9e` (`bleSocket.svc.js:61-67`):

| Characteristic | Props | Role |
|---|---|---|
| `6e400002` | notify | module→app text/status (`PIN_AUTH_PASS;`, `RX:` CAN heartbeat ~2 s, `WRITE_LOCK_GRANTED;`, …) |
| `6e400003` | write | app→module text commands (`AUTH_PIN:`, `WRITE_LOCK;`, `GET_PIN;`, …) |
| `6e400004` | write + indicate | bulk **command** channel (ERASE/WRITE/READ + status replies) |
| `6e400005` | write + notify | bulk **data** channel |

The link is **unencrypted** (no bonding) — a central reads the GATT in the clear.

### Authentication — a 6-digit PIN

Commands that matter require `AUTH_PIN:<pin>;` → `PIN_AUTH_PASS;` / `PIN_AUTH_FAIL;`
(`bleSocket.svc.js:914-917`, `1154-1157`). The PIN is 4–6 digits, first digit 1–9,
zero-padded to 6 for transmit; `777777` is the reserved factory default ("unset"). It is
**not** a secret: the panel shows it on the **MOBILE APP** screen (screen 8 / root 11), and
the module broadcasts it on **RV-C DGN `0x1FACE`** from SA `0xFD`, BCD-encoded, every ~14 s
(mux byte `F0` = PIN, `F1` = ID; `19FACEFD#F0001611AAAAAAAA` = PIN `001611`). That decodes a
DGN previously logged "undecoded" in `docs/DGN-MAP.md`. "Reset PIN to Default" on that
screen resets it to `777777` (`inferred`).

### Bulk read/write — two files

The module stores two files: **config (id 1)** = zlib-deflated JSON (`pako.deflate`), and
**filter (id 2)** = raw CSV of `dgn,circuit` pairs (the RV-C receive filter;
`choppedPgn = pgn[2:3]+pgn[4:8]`). Both carry a **version string that must match**
(`requestConfigVersion`, `bleSocket.svc.js:1735-1757`) — a mismatch makes the app treat the
board as blank and push its cached config back, overwriting yours.

Command frame on `6e400004` is `[fileId, cmd, …]`; status replies come back on the same
char; data flows on `6e400005`. Commands (`bleSocket.svc.js:43-54`): `READ=1`, `READ_CRC=2`,
`READ_VERSION=3`, `ERASE=4`, `WRITE=5`, `WRITE_DONE=6`; `STATUS_OK=0`.

- **Read** (`bleReadBulkData`, `:2057`): `[id,READ_CRC]` → `[id,2,OK,crc32(BE4),len(BE4)]`;
  then loop `[id,READ,offset(BE4)]`, collecting data notifications, until `len` bytes;
  verify CRC32; `pako.inflate` → JSON.
- **Write** (`bleWriteBulkData`, `:1844`, inside `writeConfig` `:1244`): acquire lock =
  send **`WRITE_LOCK;`** → `WRITE_LOCK_GRANTED;`; then per file `[id,ERASE]` →
  `[id,WRITE,offset(BE4),blocksize(BE4)]` + ≤512 B of data in 20-byte packets on `6e400005`
  → `[id,WRITE_DONE,crc32(BE4),len(BE4)]` + the version string on `6e400005`; release lock =
  send **`WRITE_UNLOCK;`** → `WRITE_UNLOCKED;`. The lock is a plain token handshake, **not
  crypto**.

Python `zlib.compress(json, 6)` with compact separators reproduces `pako.deflate`
byte-for-byte (verified against the module's reported length).

## 3. Caching — how to surface an edit

The app **caches** the config and only re-reads a file from the module when the module's
**version DATE is newer** than the app's cached date (`checkMiraConfigVersion` →
*"Newer config on board, read from board"* → `requestConfigFromMira`,
`configLoader.svc.js:753-827`). `version_date_int("JBv13-042525")` = `250425` (the date is
`MMDDYY`, reordered to `YYMMDD`; model `JBv13` must stay the same).

So to make an edit show **without** triggering an S3 re-download: **bump the version date and
rewrite both files** (config + filter) with that same version. Equal versions → the app keeps
its cache. Reopening the app then reads the board. Tapping "Update Config" re-downloads stock
from S3; reinstalling the app also forces a fresh board read.

## 4. Tooling — `tools/mira_config.py`

Pure helpers (version maths, zlib pack/unpack, bulk framing, filter derivation) import
without `bleak` and are pinned by `tests/test_mira_config.py`. The BLE transport needs
`bleak` and a BlueZ host in range (e.g. a Raspberry Pi in the coach; the module is single-link, so close the
phone app first).

```
# read the module's current config and filter (read-only)
python3 tools/mira_config.py read --pin <PIN> --file 1 --out mira_config.json
python3 tools/mira_config.py read --pin <PIN> --file 2 --out mira_filter.bin

# edit mira_config.json (rename a circuit's name/buttonName, flip a views flag, etc.)
# and BUMP its "version" date so the app re-reads it, then:
python3 tools/mira_config.py write --config mira_config.json --filter mira_filter.bin \
        --pin <PIN>            # dry run
python3 tools/mira_config.py write --config mira_config.json --filter mira_filter.bin \
        --pin <PIN> --commit   # actually write (close the phone app first)
```

Recovery: re-write the original config, or tap "Update Config" in the app to restore stock
from S3.

## 5. Editable surface

Top-level config keys: `version`, `views` (which pages show: `home/lights/hvac/exterior/
shades/powerPage/solarSettings/settings/…`), `tanks` + per-floorplan `*tanks` (titles,
`present`), `lightButtons`, `circuits`, `power`, `Hvac`, `shades`, `DGN`. The user-facing
control labels live in `circuits.dcDim.circuits[*].name` / `.buttonName` (`<br>` = line
break). A relabel changes only the display string; the filter is derived from
`refName`/`statusPGN`, so a relabel does not change filter id 2.

## 6. Verified on the coach — 2026-10-14

Read `jayco/b_van/v13` config off the module (6394 B stored / 39 KB JSON, CRC32 verified) →
relabelled `circuits.dcDim.circuits["22"]` (`refName: kitchenOHCLight`) name
`"Kitchen<br>OHC"` → `"Galley<br>OHC"` → bumped version `JBv13-042525` → `JBv13-042625` →
rewrote config id 1 + filter id 2 → reopened the app → the Lights page showed **"Galley
OHC"**. Reverted the same way. Panel `mcf.bin` untouched throughout. PIN for the reference
coach is `001611`.

## 7. Adding a received status to the app (and a worked leveling example)

To make the app display a value from a **new received DGN**, the config needs **three** parts, not
two — missing the third fails silently (the frame relays, nothing shows):

1. a circuit node under `circuits` with its `statusPGN` (so the module relays the DGN — see below);
2. the **filter** entry: append `<choppedPGN>,-1` to filter id 2 (`choppedPGN = pgn[2:3]+pgn[4:8]`);
3. an entry in the top-level **`DGN.status`** map, keyed `NAME -> "<PF><PS>"` (the 4-char DGN, e.g.
   `"LEVELING_SENSOR_STATUS":"FFEB"`). The app does `dgnSvc.status = config.DGN.status`
   (`configLoader.svc.js:1384`) and keys its receive dispatch on it
   (`socketCan.svc.js` `dgnFunctions[dgnSvc.status.X]`). The runtime DGN key is the **4-char PF+PS**
   from the relayed frame (`buildCanData`, `bleSocket.svc.js:1012`, drops priority/data-page), so
   `0x1FFEB` dispatches as `"FFEB"`. Omit part 3 and the handler registers under `undefined`.

**A new `DGN.status` entry only takes effect after the app is FORCE-CLOSED and relaunched** — the
dispatch table is built once in `init()`; a soft reopen re-reads the config but does not rebuild it.
A plain relabel or `views` toggle shows on a normal reopen.

**Worked example — a live tilt indicator (flashed + verified on the reference coach 2026-10-14).**
The app's **HWH** leveling type (`HwhLeveling.cls.js:118`) reads standard `LEVELING_SENSOR_STATUS`
(`0x1FFEB`) byte 0 and lights a 3x3 grid: `bits[0]`=driver, `bits[2]`=front, `bits[4]`=passenger,
`bits[6]`=rear (`byteToBit` is LSB-first). The coach's level-sensor (`../level-sensor`) broadcasts
`0x1FFEB` at priority 3, byte 0 = bits 0-1 driver / 2-3 front / 4-5 passenger / 6-7 rear low
(`01`=low) — an exact match. Config edits applied:
- `views.leveling: true`;
- a `leveling` block, `type: "HWH"`, copied from a donor (Winnebago Journey), with `utilityButtons`
  and jack-button `hide:true`, corner grid cells `hide:true`, and `additionalStatusBox: []` — leaving
  only the four directional cells (the control/jack status boxes are static defaults here, since we
  do not feed `0x1FFED`);
- `circuits.levelingSensorStatus` (statusPGN `0x19FFEB00`);
- `DGN.status.LEVELING_SENSOR_STATUS: "FFEB"`;
- filter: append `1FFEB,-1`.
Result after force-close/relaunch: the Leveling page lights the low side (rear low verified: byte 0
`0x40` -> bottom-center cell). Quadrant resolution only — the app ignores the numeric pitch/roll the
sensor also carries in bytes 1-4. Reproduce the config from `builds`/scratchpad tooling; the panel
`mcf.bin` is untouched.

## 8. Adding a custom button (and a worked hold-to-toggle example)

To make the app **send** a frame from a new control, add a circuit under `circuits.dcDim.circuits`
keyed by its **instance byte** (decimal), `type: "light"`, and the action arrays the Lights tile
wires. The tile (`templates/lights/lights.html:244+`) binds `ng-holdstart → 'hold'` and
`ng-holdend → 'holdRelease'` to `lightsTouchEvent → dgnSvc.configCan(light, action)`, which sends
`circuits[refName][action]` **verbatim** (`dgn.svc.js:245`); an action string is
`can,<PGN>,<byte0>,<byte1>,…`. `type: "light"` is **required** — the status handler only tracks a
circuit's `state` when `type === 'light'` (`light.svc.js:381`). A received `DC_DIMMER_STATUS` on the
circuit's instance drives its on/off dot.

The gesture arrays are the five phases the tile exposes: `press`, `pressRelease`, `hold`,
`holdRelease`. The hold threshold is `holdStartDefaultTime = 1000` ms (`ngTouch.js:33`), but the
Lights tile's `ng-interval="500"` makes `hold` **fire and repeat every 500 ms**; `holdRelease` fires
once on release, and only if the hold threshold was met (`ngTouch.js:295`). So for a **hold-to-toggle**
button that must not fire on a tap and must not repeat, put the frame in **`holdRelease` only** and
leave `press`/`pressRelease`/`hold` **absent** — a tap then does nothing, a ~0.5 s hold fires exactly
once on release.

One command-5 (toggle) binding suffices; the panel only sends toggle and the **G12 holds the state**,
and the G12 acts on any instance regardless of the pin sheet (see CLAUDE.md). The app's `dcDim` group
uses command PGN `0x19FEDB00` and status PGN `0x19FEDA00`; the frame mirrors the app's own light
toggle (e.g. `awningLight`) with only the instance byte changed.

**Worked example — Starlink + Overflow Valve (written + owner-confirmed on the reference coach).**
Both are existing G12 channels on this coach: **Overflow Valve = instance 28** (`0x1C`), **Starlink =
instance 29** (`0x1D`) (`wire_button` channels 28/29 in the reference build). Config edits
applied, one circuit each under `circuits.dcDim.circuits`:
- key `"28"` / `"29"`, `type: "light"`, `dimmable: false`, `room: "exterior"`;
- `holdRelease: ["can,0x19FEDB00,0x1C,0xFF,0xFA,0x05,0xFF,0x00,0xFF,0xFF"]` (byte 2 = `0x05` =
  command-5 toggle; instance byte `0x1C` for Overflow, `0x1D` for Starlink); no `press`/`pressRelease`/
  `hold` keys;
- filter: append `1FEDA,28` and `1FEDA,29` (the `DC_DIMMER_STATUS` receive subscriptions for the two
  instances, previously absent) so the on/off dots update.

These two circuits then also appear on the **Circuit Status** diagnostic page
(`templates/diagnostics/circuitStatus.html`) automatically: it lists every circuit whose group is in
the whitelist `circuitStatusSections = {dcDim, dcDim1, M1DcLoad}` (`home.ctrl.js:544`) and draws a
green/black dot from `state`. Version bumped `JBv13-043125 → JBv13-050125`; config id 1 CRC32
`0x45b09e5b` (6958 B), filter id 2 CRC32 `0xdc19dbce` (1237 B). Owner confirmed the buttons work.
Caveats: the tile draws the generic bulb glyph (the APK's icon set is fixed; a new glyph cannot be
added from config), and the hold is ~0.5 s, not 3 s.

## 9. Showing the 48 V house battery as a state-of-charge tile

On this coach the Power → batteries **House** tile read a 12 V value and the **Chassis** tile was not
a battery at all, while the real 48 V house bank was ignored. The fix is a one-key config change that
switches the app to its **Renogy** battery system.

What the tiles do by default (`templates/power/batteries.html`): `powerSvc.batteries.house.outVoltage`
and `.chassis.outVoltage`, both scaled by `setBatteryLevelByVoltage(…, 12)` (12 V nominal, hardcoded
in the template). Routing (`power.svc.js`): `DC_POWER_STATUS` instance 1 → house, instance 2 → chassis;
`DC_SOURCE_STATUS_1` instance 1 → house. On a live can1 capture the **48 V bank is
`DC_SOURCE_STATUS_1` (`0x1FFFD`) instance 1 from SA `0x80` = 53.15 V**, while `DC_POWER_STATUS`
(`0x1AAFD`, SA `0x9C`) carries 12.35 V / 12.30 V — so the 12 V `DC_POWER` frame **masked** the 53 V
`DC_SOURCE` frame on the House tile, and Chassis showed `DC_POWER` instance 2. (`circuits.chassis`,
`0x19FFF400`, is `CHASSIS_MOBILITY_STATUS`, not a battery.)

The coach's 48 V hardware is **Renogy** (the APS-500 alternator-charger at SA `0x80`; see
`docs/SETTLED-FINDINGS.md`). The app's `Renogy.cls.js` is hardwired to
`this._sourceAddress = 0x80` and reads `DC_SOURCE_STATUS_1/2/11` + `CHARGER_STATUS` gated on SA `0x80`.
State-of-charge comes from `DC_SOURCE_STATUS_2` (`0x1FFFC`): `SOC = ceil(can[4] / 2)`, treated invalid
if `can[4] > 200`. Our captured `0x1FFFC` payload `0178c024c2ffffff` → `can[4] = 0xC2 = 194` →
**SOC 97 %, valid**. `voltageReadings` is cleared on each `DC_SOURCE_STATUS_2` (a 30 s revert-to-voltage
timeout); the bus sends `0x1FFFC` ~2×/s, so the SOC stays live.

Config edit applied — **one key**: `power.dcPower.batterySystem = "renogy"`. That makes
`initializeBatterySystem('renogy')` (`power.svc.js:771`, reached via `:555`) build
`service.renogy.house`; `isBatterySystemActive('renogy')` is then false (`power.ctrl.js:337`), which
**hides the old 12 V House voltage tile**, and `isSoCInvalid('renogy')` is false, which **shows the SOC
tile** with a correct SOC-based icon. No filter change was needed — `FFFD,1`, `FFFC,1`, `FEA5` and
`FFC7` are already subscribed and `DGN.status` already maps the `DC_SOURCE` DGNs. The bogus 12 V Chassis
tile was left visible (`dcPower.chassis.hide` can flip it off as a follow-up). Version bumped
`JBv13-050125 → JBv13-050225`; config id 1 CRC32 `0x0aaebc68` (6970 B), filter id 2 unchanged
(`0xdc19dbce`, 1237 B).

**The battery system is wired in `init()`, so this only takes effect after a FORCE-CLOSE and
relaunch** (like §7), not a soft reopen. Written and read-back-verified on the module (config CRC
`0x0aaebc68`); the SOC decode is confirmed against the live can1 capture; the on-screen SOC tile awaits
the owner's visual confirmation after that relaunch.

## Evidence & confidence

`proven-from-code`: the GATT map, the bulk read/write framing, auth, the version/caching
logic — all cited to `apk/.../app/services/bleSocket.svc.js` and `configLoader.svc.js`.
`observed-on-hardware`: the read/write round-trip and the in-app result; the `0x1FACE`
PIN/ID broadcast (capture `pi:/tmp/vega_update.log`, 2026-10-14). `inferred`: "Reset PIN to
Default" sending a reset to `777777` (not captured); `make_filter` covers only the config's
own circuits (the app also folds in static/DeviceList nodes), so for a relabel read and reuse
the module's filter rather than regenerating it.
