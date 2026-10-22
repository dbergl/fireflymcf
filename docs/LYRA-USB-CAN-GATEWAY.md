# Lyra touchscreen USB port = live CAN gateway

Findings from inspecting the Lyra touchscreen over its micro-USB port (2026-08-30),
with a Linux host on the coach's RV-C bus (`can1`).

## What the port is

Plugged into the Lyra's micro-USB, the touchscreen enumerates as an **STM32 USB CDC
Virtual COM Port** (`0483:5740`, `/dev/ttyACM0`, `cdc_acm`). It is **not** a DFU/bootloader
interface (`0483:df11`) and cannot read or dump flash. Firmware is loaded from a USB flash
drive, not over this port and not over CAN.

Instead the port continuously streams the coach's RV-C traffic as a **binary CAN gateway** —
it mirrors the bus (`can1`) out USB. So it is a second, read-oriented monitor of the bus;
for anything you already do on `can1` it adds nothing, but it confirms the touchscreen sees
the same bus.

## Frame format

Fixed 20-byte frames:

```
5A C3 | 05 | 0E | ID[4] (LE, 29-bit) | 02 | DLC | data[8] | trailer[2]
sync    type len   CAN identifier     flag  len   payload    ??
```

- `5A C3` sync, then constant `type=0x05`, `len=0x0E` (14 = bytes from ID through data).
- 4-byte CAN identifier, little-endian (e.g. bytes `9C CE 5F 19` = `0x195FCE9C`).
- `flag=0x02`, then DLC (`0x08`), then 8 data bytes.
- 2-byte trailer: **the panel's Fletcher-16, solved 2026-09-10** (`LCD-FIRMWARE.md` Finding 14).
  It is not a CRC, which is why eight CRC-16 variants found nothing. The frame builder pre-sets the
  trailer to `FF FF`, then runs a ones'-complement Fletcher-16 (folded every 21 bytes) over 18 bytes
  from the type byte, a span that includes those two `FF`s. The result is stored little-endian.
  It reproduces all 11,326 frames in the three captures; `tools/lyra_usbcan.py` checks it
  (`trailer_ok`) and counts failures in `--summary`.

Decoder: `tools/lyra_usbcan.py` (reads raw bytes on stdin, e.g.
`cat /dev/ttyACM0 | python3 tools/lyra_usbcan.py` on the host the gateway is plugged into; `--summary` aggregates
by ID for baseline-vs-event diffing).

## The port also takes commands — read from code, never exercised

The panel firmware parses packets arriving from the host (`FUN_0021E9E0`, `LCD-FIRMWARE.md`
Finding 14). The framing is `5A C3`, a type byte, a length byte, then twice-the-length payload
bytes, then a Fletcher-16 over type, length and payload (initialised to `0xFFFF`, no `FF FF`
padding on this side). The types it acts on:

- `0x81`: an identify reply, carrying the GuiData header's build and version fields.
- `0x82`: an identify variant that also stops the display refresh.
- `0x84`: a u16 command. **`0x0101` reboots the panel** (a `SYSRESETREQ` write to the Cortex-M
  `AIRCR` register); `0x0201` stops the mirror stream; `0x0202` enables transmit.
- `0x85`: in mode 2 only (after `0x0202`), queue a CAN frame — an id word (source address, 18-bit
  DGN, priority) then eight data bytes — onto the RV-C bus.

The framing differs from the mirror's: an inbound frame's byte 3 is `payload ÷ 2`, whereas an
outbound mirror frame carries a constant `05 0E` there. They are two grammars sharing the `5A C3`
sync, which is why a mirror frame fed back would fail the panel's own checksum — the earlier note
that the length "did not fit" is resolved (`LCD-FIRMWARE.md` Finding 14).

**None of this inbound path has been tried.** No packet has ever been sent to the port, and a
transmit through it reaches the live coach bus, so it is not something to experiment with without
the owner. `can1` + `cansend` stays the known-good path.

## What is and isn't visible

- **Navigation (Home/Electrical/Lights/Climate/Settings) is NOT on the bus.** Page switching is
  local to the display; the frame rate during nav presses matched idle and no field tracked the
  page changes. Nothing to monitor or replay for navigation.
- **Actuator presses ARE visible.** Toggling the main light produced a clean `DC_DIMMER_STATUS_3`
  (`19FEDA9C`, instance 32) flipping brightness `C8`(on)/`00`(off), plus the proprietary
  `196F849C` level report.

## Control path (simulate a press)

The panel drives its loads internally, so no `DC_DIMMER_COMMAND` frame appears on the bus during
a touch. But the G12 obeys standard **DC_DIMMER_COMMAND_2** (DGN `1FEDB`) from the bus — this is
what `rvc2mqtt` uses. Proven on `can1`:

```
cansend can1 19FEDB44#20FFFA05FF00FFFF   # instance 32, command 05 = toggle
cansend can1 19FEDB44#20FF64 00FF00FFFF  # command 00 = set brightness (0x64 = 50%)
```

- Source address does **not** appear to matter: both an unused `0x44` and the panel's own `0x9C`
  worked. One early failure sourced from `0xFD` was most likely a timing artifact (the light had
  just been toggled by hand), not an address conflict — an earlier claim that it was a conflict was
  not supported.
- Verify state by reading `DC_DIMMER_STATUS_3` (`19FEDA9C`). **A load broadcasts `1FEDA` only while
  its output is ON**; an absent status means off, not missing.

## Proprietary load-current report: DGN 16F00 (new; corrected 2026-10-21)

`196F849C` decodes as priority 6, DP=1, **PF=0x6F, dest=0x84, src=0x9C** — a directed proprietary
message from the G12 Controller (`0x9C`) to the load-driver module (`0x84`). Fields:

```
FF | instance | FF FF | load current | 7D (FF on outputs 9-20) | active(05=on/00=off) | 00
```

**Correction, 2026-10-21 — byte 4 is the output's load current (unit unknown), not a 0–15 level.**
This section first read it as the dimmer level at 0–15 resolution. Across the coach captures
(`captures/pc*.log`), with the same commanded brightness (`1FEDA` byte 2 = 200, output on), outputs
report different and stable values — `observed-on-hardware`:

| output | byte 4 at brightness 200 | frames (captures) |
|---|---|---|
| 24 | `0x0A` | 567 (5) |
| 43 | `0x02` | 358 (3) |
| 32 | `0x0F` | 60 (3) |
| 22 / 23 / 21 | `0x0A`-`0x0B` / `0x16` / `0x07` | |
| 26, 38 | `0x00` — on, nothing measured | 62 (2), 78 (4) |
| 3, 4 (awning motors) | `0x37` … `0x79`, varying at a fixed level | |
| 9–20 (half-bridge) | `FF FF` in bytes 4-5 — no current sense | 1,223 |

For a dimmable load the current follows the delivered brightness, so on output 32 — whose full-load
value happens to be `0x0F` — **`floor(brightness * 15 / 200)` holds**: the brightness sweep
(`captures/lyra-brightness-sweep.log`, output 32 only) fits it at 24 of 25 levels (120 → 8, not 9).
The sweep exercising a single output is why the value was taken for a level. It follows the
*actual* output (fading in over ~0.7 s while `1FEDA` already reports the target), at 1 Hz while on,
plus a zero frame with byte 6 = `00` when the output switches on and when toggle (5) or memory-off
(6) switches it off; nothing while off. Still a *report* directed at the driver — consistent with,
but not proven to be, an internal command; use `1FEDB` for control. Documented as
`DC_DIMMER_LEVEL_16_SPYDER` in the rvc2mqtt fork's `rvc-spec.yml` (branch `unknown-dgns`, corrected
there in the same terms; the names are kept for MQTT compatibility). The emulator models it per
output (`firefly-emulator` `firefly_emu/g12out.py`).

## Load / instance map

From the coach's live rvc2mqtt floorplan (its `rvc2mqtt` configuration,
base + `.override.yml`), cross-checked against a non-actuating status poll
(`cansend can1 18EAFF44#DAFE01FFFFFFFFFF`, a J1939 Request for `1FEDA`) that made the G12
Controller (`0x9C`) report all 44 instances without energizing anything:

| Inst | Load | | Inst | Load |
|---|---|---|---|---|
| 1 | spotlight | | 32 | main lights (the "ceiling light") |
| 21 | awning lights | | 34 | bunk accent lights |
| 22 | kitchen OHC lights | | 35 | living room OHC lights |
| 23 | step light | | 40 | bath fan |
| 24 | bed light | | **44** | **water pump** ⚠️ |
| **25** | **bathroom light** ⚠️ | | **29** | **starlink** ⚠️ (override) |

Node identities (override): `0x9C` = G12 Controller (drives loads), `0x9F` = Touchscreen,
`0xFD` = Vegatouch Mira.

### Safety
- **Instance 25 (bathroom light)** is tied to the **water pump** by an external automation —
  toggling it runs the pump.
- **Instance 44** is the water pump directly.
- **Instance 29 (starlink)** likely powers the Starlink dish; actuating it may drop the bus host's own
  backhaul and your SSH/Tailscale session.
- Safe-to-actuate lights: 1, 21, 22, 23, 24, 32, 34, 35. After any actuation test, return all
  outputs to off.

## Captures (this round)

- `captures/lyra-usb-canstream-2026-08-30.bin` — 15 s idle USB stream
- `captures/lyra-press-test.bin` — nav-bar presses (shows nav is not on the bus)
- `captures/lyra-light-test.bin` (+ `.decoded.log`) — main-light toggles
- `captures/lyra-brightness-sweep.log` — commanded brightness sweep (the `16F00` quantization)

## No boot-time output — checked across a power cycle, 2026-09-03

Asked while hunting for a way to read panel RAM at boot (`BUS-BASELINE.md` PC.56 needs exactly
that to turn an inference into a proof). The port was left connected and logged read-only while the
owner power-cycled the screen. **There is no banner, no boot log and no debug text**: the device
disconnects, re-enumerates about a second later, and the very first byte after it appears is a
`5A C3` frame sync. Every printable run in a 126 KB capture is RV-C payload — the
`PRODUCT_IDENTIFICATION` strings (`Firefly`, `Spyder`, `Wakespeed`, `Standalone Module`) carried
inside transport-protocol frames, not console output.

So the gateway is a bus mirror from the instant it enumerates, and this port **cannot** answer a
memory question. The 2026-08-30 decode above reproduces unchanged nine days later, and
`tools/lyra_usbcan.py` parses a fresh capture cleanly (4,582 frames, top talker `11FEC880`, the
Wakespeed — the same busiest source `can1` shows).

*Useful side effect:* the enumerate/disconnect pair in `dmesg` is an independent witness that a
"power cycle" really removed panel power, rather than being a warm restart. That is what confirmed
the boot series behind PC.56.

## No DFU interface, ever — so no `dfu-util` route, 2026-09-03

Asked whether an STM32 command-line tool could attach during the boot window. **It cannot, and the
reason is the device, not the tooling.** Every enumeration in the bus host's kernel log across more than
ten boots — including the whole PC.56 cold-boot series — is `0483:5740`, this CDC port. DFU would
enumerate as **`0483:df11`** and never has, not even momentarily, and a brief appearance would still
be logged because it would have to enumerate to exist.

That is expected: ST's ROM bootloader exposes USB DFU only when the part boots from system memory,
which needs **BOOT0 asserted at reset** — a hardware condition a host cannot request over the
cable. This panel boots straight into its application, which brings up CDC. `dfu-util` and
STM32CubeProgrammer would find nothing to talk to, so installing them is wasted effort.

**Booting with the bus host attached takes ~4x longer, and the cause is NOT host-mode probing.**
Measured on `can1` by the panel's own silence: eleven ordinary cold boots put `0x9F` off the bus for
**8-11 s**, while the boot with the bus host attached took **33.8 s**. ~~The panel is probably probing in
OTG host mode for a firmware image and timing out.~~ **Refuted the same day by the owner:** with an
**OTG adapter and a real USB stick** on that port — the panel genuinely acting as host — it
**booted quickly and did not show the flash menu**. So host-mode probing is fast, and the delay
belongs to the case where the panel is a USB **device** attached to a host. What actually costs the
33.8 s is **`unknown`**; do not repeat the host-probe guess.

**And the micro-USB is NOT an update source at all.** ~~The flash menu depends on finding matching
content.~~ Withdrawn: the owner clarified that the stick he used is **the same one he flashes with**
on the panel's **separate USB-A port**, so it carries valid `mcf-*.bin` images. It still booted
straight through with no menu. The menu's absence is therefore about the **port**, not the content:
this panel takes firmware on its **USB-A** connector, and the micro-USB does not serve that role
even with an OTG adapter and a known-good image present.

*Consequence for the emulated-stick experiment below, and it makes it simpler:* it should target the
panel's **USB-A** port, which is already a host, so **no OTG adapter is needed** — a USB-C-to-USB-A
cable from the bus host in gadget mode is a plain device-to-host connection. Whether that port accepts an
emulated volume is untested and is the thing to find out.

**An emulated stick is prepared but not yet run** (a `usbstick.sh` gadget script on the bus host,
a 64 MB FAT32 volume labelled `FIREFLY`, deliberately **empty** so it cannot trigger an update).
The bus host (a Raspberry Pi 4) has a UDC and `dwc2` enabled, so it can present mass storage on its USB-C port, which
cables straight into the panel's **USB-A** flash port; the owner reports its USB-C is
physically awkward to reach, so this is deferred. It would trace what the
panel reads — filesystem expectations, directory walk, filename matching — turning the update path
from behavioural knowledge into a read trace, and could stage builds without writing a physical
stick each time.

**The remaining route to a live RAM read is SWD**: debug pads, an ST-Link, OpenOCD. That would turn
`BUS-BASELINE.md` PC.56's inference into a measurement, at the cost of opening a working panel.

