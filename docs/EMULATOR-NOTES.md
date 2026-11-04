# Emulator findings ledger

The emulator's notebook (`fireflymcf.emu`, [EMULATOR.md](EMULATOR.md)), carried over from its
private development. Capture names (`pc23-baseline`, `pc28`, ...) are the research repository's
coach captures, which are not published; [BUS-BASELINE.md](BUS-BASELINE.md) transcribes them.

Observations come from REA/Ghidra output (Evidence IDs `ev_...`),
from emulator traces, or from plain shell inspection (marked *shell*).
Inferences are marked as such.

## Firmware package (`firefly-13.6/`)

| File | Contents |
| --- | --- |
| `mcf.bin` | MCF package "Jayco B-Van V13" (*shell*, `python -m fireflymcf.emu.mcf info`) |
| &nbsp; [0] LCD Firmware | Cortex-M app, 62,920 B, linked at 0x00210000 (ITCM alias of flash 0x08010000), SP 0x20050000 |
| &nbsp; [1] Lyra 13v6 GuiCode | GUI module, 105,400 B at 0x00240000 (flash 0x08040000). Header magic 0x1A2B3C4D + 5 exports |
| &nbsp; [2] Lyra 13v6 GuiData | 888,344 B for QSPI flash at 0x90000000 (memory-mapped) |
| &nbsp; [3] G12 | 9,640 B config for another network device; addr field 0x180000 is not LCD memory (inference: uploaded over CAN by the Config Loader) |
| `cli.bin` | RAM image (magic 0xC11B102E) loaded at 0x20000000: the USB-stick **Config Loader** (USB host + FAT + MCF upload; error strings like "Unlock Command Timeout", "Device GUI Config Invalid") |

Not present: the bootloader at 0x08000000-0x0800FFFF. `main` sets VTOR to
0x08010000 (ev_825eb961). The app looks for a bootloader info block through
the pointer at 0x20000154 (magic 0xD15B10C4, CRC16 checked) and copes when it
is absent (ev_24ec78da).

## Hardware (from `main` ev_825eb961 and emulator MMIO traces)

* STM32F7 (320 KiB SRAM, SP 0x20050000), SYSCLK 160 MHz (SysTick reload
  159999, TIM2 PSC 79), caches + MPU enabled.
* SDRAM via FMC at 0xC0000000; framebuffer 800x480 RGB888 at 0xC0000000.
* QSPI flash memory-mapped at 0x90000000 (GuiData).
* LTDC + DMA2D (DMA2D used by GuiCode; not yet exercised).
* I2C2: **GT911** touch at 0x14 (product ID read, 186-byte config written,
  INT on EXTI line 5).
* I2C4: EEPROM at 0x50, 2-byte addressing. Reads seen: 0x0004(2), 0x0008(1),
  0x0009(8, passed to GUI init), 0x0012(1), 0x0014, 0x0054(128), 0x00D4(128).
  Contents unknown; emulated as blank 0xFF.
* GPIOF pin 10 selects between two LCD timing sets (ev_24ec78da).
* CAN1 (bxCAN) at 250 kbit/s: RV-C. TIM2 = HAL timebase (1 kHz). TIM1/TIM3 PWM
  (inference: backlight / buzzer). UART4 initialised (no output seen yet).
  USB OTG FS device (STM32 Virtual ComPort strings). ADC, SPI2, CRC
  (CRC-16/CCITT, init 0x1D0F) used. IWDG enabled.

## Software

* CubeMX HAL + FreeRTOS (CMSIS-RTOS v1). Tasks: `defaultTask`, `GuiUpdateTask`,
  `GuiUpdateSuspendTask`, `IDLE` (ev_825eb961).
* `can1_init` requires MSR.INAK to *still* read 1 right after leaving init mode
  (true on real silicon for 11 bit times); otherwise main hangs (ev_e259d96f).
* RV-C: claims source address **0x9F**, sends Address Claim (18EEFF9F),
  request-for-address-claim (18EA9FFE), DM_RV (19FECA9F, about 1/s) and
  19FFFD9F. Listens for proprietary-A PGN 0xEF9F (config protocol) in
  `defaultTask_loop` (ev_97df0036).
* Display loop: LTDC line interrupt -> callback `FUN_0021cac8` alternates line
  29/512 and signals `GuiUpdateTask` (ev_75f9069b) -> `GuiUpdateTask` calls GUI
  export1 `0x24ee29` with the state block at 0x20007fc8 (ev_1d7bdc4d).
* GUI export4 `0x247801` is the RV-C frame handler (PGNs 0x1FEDA, 0x1FACF,
  0x1FBDA, 0x1FFDC, 0x1FFFF ...) (ev_b942dca7). export0 = init with callback
  table; export3 = fallback when init fails.
* Config flags 0x20000130/0x20000131 both 1 (configured), so
  `FUN_0021bab4` runs: it walks a table of expected network nodes with
  timeouts (ev_8ae15827).

## Why the screen was white (resolved)

The earlier hypothesis (missing network data) was wrong. Causes, in order:
1. `GuiUpdateSuspendTask` blocks in `osSignalWait(forever)` (ev_2bacb95e) via
   xTaskNotifyWait (ev_8b0944e8), which pends PendSV inside a critical section.
   The emulator only took PendSV at slice boundaries, so the wait returned
   at once and the task suspended `GuiUpdateTask` 68,000 times a second; the GUI
   task never ran. Fixed by taking pending exceptions right after
   `msr basepri/primask`/`cpsie`.
2. Touch reads use `HAL_I2C_Mem_Read_IT` (ev_65e99578, from the EXTI5 handler
   ev_b650a48f/ev_e4ec4236), so the I2C model must raise its event interrupt.
3. The first page is a terms-of-use screen; **I ACCEPT** (400, 425) leads to HOME.

## RV-C behaviour

* CAN RX goes through the bxCAN filters, and the firmware dispatches on the
  filter match index (ev_35bb9149, ev_9b7829f7). Filters as programmed:

  | Bank | ID / mask | FIFO | Meaning |
  | --- | --- | --- | --- |
  | 0 | exact 0x00EEFF9F | 0 | someone else claims our SA 0x9F: **reset** (NVIC_SystemReset) |
  | 1 | 0x00EAFF00 / ...00 | 0 | requests to global |
  | 2 | 0x00EA9F00 / ...00 | 0 | requests to us |
  | 3 | 0x00E8FF00 / ...00 | 0 | ACK |
  | 4 | all extended | 1 | everything else |

* **The panel never moves off 0x9F**, although its NAME (`18EEFF9F#0000A024A0650080`,
  byte 7 bit 7) says "arbitrary address capable". `0x217BCE movs r3,#0x9f /
  strb r3,[r5]` is the only store to the SA byte at `0x20000018` (12 references,
  all others loads) and runs in the RV-C init on every start; the filter-0
  handler (`can_rx_enqueue` `0x218434 tbb`, FMI 0 -> `0x21843E bl
  NVIC_SystemReset`) resets without comparing NAMEs. So a node claiming 0x9F
  sends the panel into reset, and it comes back claiming 0x9F again; two copies
  of this firmware on one bus would reset each other indefinitely. Hence the
  emulator's link to a real coach is listen-only.
* GuiData section 'A' (offset 0x2A70, 298 entries) maps (PGN, match byte,
  field) to 16-bit GUI values; decoder in `fireflymcf/emu/guidata.py`
  (`python -m fireflymcf.emu.guidata`). Values expire (shown as `---`) after 2/10/30 s
  depending on the entry.
* Identified widgets (HOME page), observed by probing and confirmed with frames:

  | Widget | Data point | Frame |
  | --- | --- | --- |
  | Clock | 17 | DATE_TIME_STATUS 1FFFF, hour/minute bytes 4-5 |
  | CLIMATE | 9 | THERMOSTAT_AMBIENT_STATUS 1FF9C inst 0, 0.03125 K |
  | House / Chassis | 4 / 5 | **1AAFD** (non-standard) inst 1 / 2, u16 byte 2, 0.05 V |
  | Main Ceiling etc. | 74, ... | DC_DIMMER_STATUS_3 1FEDA, byte 6 bits 2-3 = load on, bits 4-5 = fault (red) |

* **G12 logic controller protocol** (from rvc2mqtt's rvc-spec.yml, written from
  coach captures; reproduced in the emulator):
  * Panel writes selectors with 1FED9 group 0x96 `FF 96 <sel> 0F <u16 LE> D1|D2 EA`
    (SET) / `FF 96 <sel> 0F FF FF D3 FF` (QUERY). The boot-time `19FED99F ff 96 9b 0f
    00 00 d1 ff` is SET 9B (AES enabled) = 0. Some selectors take signed deltas
    (CC/CD/CE/82/86/88/92/99).
  * G12 = source **0x9C**; answers and sweeps on 15FCE `<sel> 00 <u16 LE> 00..`.
  * 1FACF (G12 rule #44) carries F1 / F5 / F2 at payload bytes 2 / 5 / 7. Without a
    G12 on the bus the panel never sends floorplan selections.
  * **Floorplan selection** is a hidden technician page: SETTINGS, *hold* the
    Floorplan button ~3.2 s, Proceed. Selecting a model sends the spec's 12-selector
    transaction F5, D7, E5, E9, E4, EF, E6, EC, E3, EB, 15, F7 with values equal to
    rvc2mqtt's FLOORPLAN_OPTION_DEFAULTS (checked for RD), and turns off nine output
    channels. F5 codes (13.6 package): 1 RT, 2 RA, 3 RD, 4 SY, 5 TB, 6 CB, 7 WA, 8 WD,
    9 WT, 10 RY. LIGHTS/ELECTRICAL then show that floorplan's layout.
  * F1/F2: the spec calls them unidentified floorplan-adjacent values (coach 0x0D,
    0x06). The panel displays them as "Logic Controller Version: F1.F2"
    (data points 51/52), i.e. 13.6, which equals this MCF's version. Suggestive, not
    proven.
* **Panel write semantics, observed in the emulator** (useful for rvc-spec.yml,
  where 0x31 is listed as untested and 0x16 as absolute):
  * Absolute writes use function **D1** (`... <value> D1 EA`): the whole floorplan
    transaction, each followed by a D3 read-back.
  * The +/- steppers send **D2** with a signed delta and byte 7 = FF, then a zero
    D2 with EA, then D3. Seen for 31 (+2 = +0.1 V), 0D (-2), 16 (+5 min), 0C (+10 s).
    So the panel writes 0x16 and 0x31 as deltas via D2. Whether the real G12 also
    treats some D1 writes as deltas (as the spec's probes found for CC/CD/CE, 82,
    86, 88, 92, 99) is not visible from the panel side; the emulated G12 applies
    D2 as delta and D1 as absolute except for those.
* Li-AES floorplans (WA/WD/WT/TB/SY) give ELECTRICAL an AES page showing G12
  selectors 31, 0C, 0D, 0E, 16, 2B, 2C (encodings as in the spec: volts 0.05 V,
  quiet times minute/hour bytes). Its "Battery Voltage" is not selector 0A
  Generator floorplans show GEN start/stop, AGS and the solar box.
* AES "Battery Voltage" = **DC_SOURCE_STATUS_1 (1FFFD) instance 1** (52.8 V shown
  from 0x0420). Per the owner it comes from the alternator regulator / virtual BMS
  at SA 0x80 (APS-500 in the spec); the panel does not filter on source address,
  and instance 0x81 does not work. Not a data-point-table value (poking state does
  nothing), so GuiCode decodes the frame itself. Emulated by the APS500 peer
  (also DC_SOURCE_STATUS_2 SOC and J1939 alternator info 0FED5).
* Tank gauges (Li-AES HOME page): the panel shows standard **TANK_STATUS 1FFB7**
  instance 0 fresh, 1 black, 2 gray and **18 gray 2** (owner's hint, confirmed;
  instance 0x18 does not work). The G12's raw 0BFC1 levels (instances 1 fresh,
  2 gray, 3 black, 4 gray 2) appear as "ADC Raw Value" on PROBE TANK DIAGNOSTICS
  (HOME, hold a tank gauge ~5.5 s) but do not drive the
  gauges, so the G12 converts with the CC/CD/CE thresholds and relays
  TANK_STATUS. *Superseded by running the G12 logic (below):* the bucketer is
  descending (raw >= CC 54000 = empty ... < CE 25000 = full) and TANK_STATUS
  carries level 0-3 with **resolution 3**, as on the coach (`00 00 03`).
  The fallback `peers.ProtocolG12` (used only when the MCF's G12 logic cannot run) buckets
  the same way (descending, a reading at a threshold is the emptier bucket, thresholds
  defaulting to the coach's 54000/40000/25000, each tank's 0BFC1 and 1FFB7 at 1 Hz) but encodes
  TANK_STATUS as **level/100** (0, 33, 67, 100 of resolution 100), where the real G12 sends
  level/3, so a test that reads the TANK_STATUS slot's bytes sees different values under the
  two models.
* **Inverter** (GuiCode FUN_0024f1fc ev_113acf0b, toggle FUN_0024f6d8 ev_1848006f):
  binds up to two inverters by the source address of the first INVERTER_STATUS
  1FFD4 for instance 1/2; widget follows status byte 1 (0 disabled, 1 invert...).
  Tapping sends INVERTER_COMMAND 1FFD3 `01 11` (enable) when status is 0, else
  `01 10`. Also decodes 1FFD7 AC, 1FEBD DC, 1FEE8 temperature, DM_RV faults, and
  requests Product ID (FEEB); eleven freshness counters expire fields.
* More widgets (probed with `fireflymcf.emu.tools.probe_widgets`):

  | Widget | Data point | Frame |
  | --- | --- | --- |
  | CLIMATE CONTROL fan mode | 13 | THERMOSTAT_STATUS_1 1FFE2 inst 0, u16 byte 1 |
  | CLIMATE CONTROL big temperature | 85 | DC_DIMMER_STATUS_3 inst 0x2B byte 6 |
  | Bath Fan | 82 | DC_DIMMER_STATUS_3 inst 0x28 byte 6 |
  | Logic Controller Version | 51 / 52 | **15FCE** selectors F1 / F2 (shown as F1.F2) |
  | Floorplan | 294 | 15FCE var 0xF5 (read when the page is entered) |

  15FCE is the logic controller's variable broadcast (byte 0 = variable id,
  byte 2 = u8 or u16 value); 53 data points use it. The climate page drives
  logic-controller outputs (dimmer instances 0x29-0x2B, also handled in
  gui_export4) rather than an RV-C thermostat.
* ELECTRICAL solar box: needs DGN **1FDFF plus one of 1FE82 / 1FE83 / 1FE85**.
  1FDFF = SOLAR_CONTROLLER_SOLAR_ARRAY_STATUS (spec): array volts bytes 1-2
  (0.05 V), current bytes 3-4 (0.05 A, offset -1600 A); the panel shows W = V x A.
  The earlier "4966.1 A" came from a test payload that was not a valid current.
* Inverter: resolved, see above (earlier tests bound the inverter only after
  entering the page and without a G12/floorplan).
* Settings persist in the I2C EEPROM only (flash is never programmed). First
  boot initialises 0x60=01, 0x7D=1A, 0x7E=0A; I ACCEPT writes 0x7F=00.
  Anomaly (seen once, not reproduced in 6 attempts): a run rewrote 0x7F=01,
  re-arming the terms page on the next boot.
* Hold controls depend on the GT911 re-reporting every scan (~10 ms) while
  touched; a read of status 0x00 is taken as release. Awning EXT: instance 4
  OFF (stop retract), instance 3 "on for 2 s" (cmd 1, duration byte 2) repeated
  every 1 s while held, instance 3 OFF on release.
* Panel commands: lights send DC_DIMMER_COMMAND_2 (1FEDB) with byte 3 = command
  (5 = toggle, 0 = set level) and byte 2 = level; 0xFA/0xFB mean "on at last
  level". Light Master ON targets instances 0x16, 0x18-0x1A, 0x20, 0x22, 0x23.
  Light Master OFF / Exterior Master OFF send command **6 (memory off)** with
  level 0 to the same channels (Exterior Master on RD: 0x15, 0x17); the next
  "on at last level" restores the remembered brightness.
* PGN 0x1FED9 addressed to the panel's instance (0x14 here) with command 0xE0 and
  byte 6 = 0xF0/0xF1 steps the GUI page forward/back (ev_8a5f3378).

## G12 logic from the MCF (`fireflymcf/emu/g12.py`)

The G12 section's code blob (table 0x67, decoded in
[G12-CODE.md](G12-CODE.md)) runs unmodified in a second Unicorn (Cortex-M4, mapped at
the inferred 0x08038AE4; it is position independent). Calling convention, as
established by fireflymcf and confirmed by executing it: r0 = context
`{u32 cells, u16 count, u8 ?, u8 extra_seconds, fn activate(n), fn
deactivate(n), fn is_active(n), fn log(str), fn send(record)}`; init at +0x0C,
tick at the header's +0x08 pointer. **The fifth slot (+0x18) was found by
executing the blob:** the awning interlock calls it with a 12-byte frame-table
record copied to the stack (pad, DGN low u16, CAN-id high byte, payload in wire
order; G12-CODE.md "The frame table"), so it is the G12's send-a-frame service. Cells are u16, selector N at index N; 0xFFFF = unset;
count 0x200 (the timer bank sits at the top).

**The tick runs every 10 ms, not every second** (corrected 2026-10-21; the
emulator ticked at 1 Hz until then). Init arms cell[count-1] = 100; each tick
counts it down by ctx[7]+1 and only when it wraps (13.6 +0x0968, 14.7 +0x1FA2,
`cell - extra + 99`) does the 16-timer bank drop by 1 -- so the bank is the
1 s clock and cell[count-1] its 100-tick divider. The first two timers drop by
ctx[7]+1 every tick, i.e. they count 10 ms (ctx[7] = missed ticks, 0 passed).
The evidence fits: the coach relays a config write in 2-10 ms (PC.23), locks
the awning 4 ms after ignition-off (pc28), and clears BF 714 ms / 343 ms after
setting it (pc24 / pc39: a bank timer armed to 1 fires at the next wrap). 14.7
settles it: a held button's ramp is re-sent when a 300-tick counter is a
multiple of 100, which at 100 Hz is the panel's once-a-second ramp re-send and
at 1 Hz would never happen. fireflymcf's "the tick is ~1 Hz" read the 714 ms
as one tick without the divider. The emulator's former extra tick after config
writes and input changes was a stand-in for this and is gone.

**Context ABI 17 (14.7), mapped 2026-10-21** by disassembling both builds and
running 14.7's blob: `{cells +0, count +4, extra ticks +7, activate +8,
deactivate +0x0C, is_active +0x10, send +0x18, button(n) +0x24, notify(value)
+0x40}`; 13.6's log slot (+0x14, "G8 Test") is gone. Call counts 13.6 / 14.7:
activate 88/91, deactivate 5/10, is_active 6/11, send 14/34, +0x24 0/7, +0x40
0/3.
* `button(n)` (n = 0, 1, 3, 5, 6, 8, 9) returns push-button input n's state
  (0 idle, 1 pressed, 2 tapped, 3 held, 4 released after a hold) -- the
  table-0x20 phases, which 14.7 leaves empty. The blob keeps the last state
  per input as nibbles in cell 0x116 and on a change sends from its frame
  table, then `1FFB8` DIGITAL_INPUT_STATUS `[n+1, down, FF, 02, FF FF FF FF]`
  (down = state 1 or 3); 14.7 also feeds cell 0x28 (ignition) from 1FFB8.
  Executed: input 0 -> output 22 (tap toggle FA, hold ramp re-sent each 1 s,
  release stop), 1 and 3 -> output 35 (same), 5 / 6 -> relays 3 / 4
  momentary (press on 200, release off), 8 -> output 44 toggle on tap and on
  hold release, 9 -> output 3 off + lock (33) on press, unlock (34) on release.
  The emulator maps G12 pin p to button p-1 (`G12.buttons`).
  Corroborated by an owner's photo of a 14.12 coach (G12 firmware 4.0k):
  pins 1 Kitchen OHC, 2 Bench Seat OHC, 6/7 Awning EXT/RET, 9 Water Pump,
  10 Ignition match outputs 22, 35, 3/4, 44 and the MCF's "AWNING EXTEND
  LOCKED BY IGNITION". This coach's G12 app is 0x0300 rel 0x64 (PC.64), shown as 3.0d on its
  diagnostics page (owner's photo; panel firmware 1.6r), so
  ABI 17 likely needs a 4.x G12 application, which no MCF carries.
* `notify(value)` is called three times by a defaults routine with cells
  0x0E (300), 0x0F (1800) and 0x16 (115) just after setting them; the result
  is discarded (what the application does with them is not in the MCF).

**The 14.7 panel's SOC relay, cell 0xDA (2026-10-22).** Every 3.01 s the
panel asserts `1FED9 FF 96 DA 0F <soc u16> D1 FF`: the average of
DC_SOURCE_STATUS_2 (1FFFC) byte 4 (half-percent) over its battery slots, or
FFFF when none counts. Bisected by replaying pc-147-session into the 14.7
panel (which reproduces the coach's FFFF, FFFF, then 174 after boot) and read
from the code:
* Four slots of 100 bytes at 0x2003A3D0; instance n (1-4) lives in slot n and
  the first 1FFFC from a source claims it (SA at +0x16, SOC at +0x2A, handler
  0x24FE5E). Instance 5 is ignored.
* DM_RV (1FECA) from the same source sets the slot's state (+0x24, handler
  0x24FC90): red lamp 3, yellow lamp 2, else operating-status nibble != 0 -> 1,
  0 -> 0. A summary byte (0x2003A569, 0x24FA5C) is non-zero when any slot's
  state is; while it is 0 the SOC is FFFF (0x24F7F0). The DSA is not checked.
  So the coach's FFFF after boot is the wait for the WS500's 5 s DM_RV.
* Slots time out ~30 s after their last 1FFFC (SA back to FF), DM_RV state
  ~30 s after its last DM_RV. If the SOC stops but DM_RV continues, the state
  lingers and the average divides by zero slots: Cortex-M `sdiv` by 0 gives 0
  (CCR = 0x30000, DIV_0_TRP clear), so the panel tells the G12 **0 %**, not
  unknown. A 14.7 G12 compares 0xDA against 0xDB for its SOC start.
* The same code also handles proprietary DGNs 0xFF80-0xFF82/0xFF85 and a
  `0xEF<sa>` request `55 <slot> 01 ..` to each slot's source; not exercised.
The demo APS500 now sends the WS500's DM_RV (`05 4C FF FF FF FF 00 FF`);
without it the panel relayed FFFF forever. `tests/emu/test_panel147.py`.

Verified by execution (13.6):
* init: 128 sentinel defaults, `G8 Test` log call, 48 boot activations.
* tank bucketer: raw cells B0-B3 (= 0BFC1 instances 1-4) -> buckets 0-3 in
  B5-B8 (fresh, grey, black, grey 2), descending; TANK_STATUS #45-#48
  substitute them at resolution 3. Black (#47) follows EC; grey 2 (#48) runs
  only for floorplans RA/RD/WA/WD.
* mode switch (EF vs latch 7A): to AES sets 0D 1080, 31 1000, 16 **115** (the
  coach's value), 73 = 18; to AGS sets 50 = 20, 7B/7C = 30, 0D 270.
* relays: a config write followed by a tick relays via the change detection
  (e.g. 82 -> 1FFD0 #52), as PC.23 saw within 2-10 ms.

Findings from execution (all now in G12-CODE.md, "Executing the blob"):
* The context has a fifth callback, `[ctx+0x18](record)` = transmit a frame
  record (above). Without it the interlock jumps to address 0.
* The G12 application feeds its **own** frames back through its receive rules
  and reacts within milliseconds: in pc28 it broadcasts ignition-off
  (`1FBDA FB 00`, table 0x20 pin 10) at 260.563 s and the interlock's Unlock
  (`1FEDB 03 FF 00 22`) follows at 260.567 s, so cell 0x28 (struct +0x50) is
  written by the G12 receiving its own FB frame (table 0x61 #0) and seen by the
  next 10 ms tick.
* **Table 0x61 record #15 (1FFFD inst 1 -> cell 0x55) is a receive.** The coach's
  G12 never sends 1FFFD; its AES measured voltage 0x0A (copied from 0x55 by the
  tick) is 1077 = 53.85 V, exactly the APS-500's 1FFFD instance 1 (SA 0x80) in
  pc23. Its kind (0x28) is the same as the 1AAFD transmit record, so kind alone
  does not give the direction.
* The G12 answers the panel's 14 s broadcast REQUESTs: FED8 -> 1FED8 + four
  0FF01 frames; FEEB -> product ID by BAM, 46 bytes
  `Spyder Controls*G12 Panel 2v2 3v0d*0000000*22*`, packets 50 ms apart.
  Unprompted: DM_RV `05 84 1F 16 ..` and 1FFE2 / 17EFE every 5 s.
* The G12 is the bus clock (1FFFF at 1 Hz; table 0x61 #3 puts cell 0x2A's hour
  at byte 4; cell 0x2A = hour | minute << 8).
* The 15FCE sweep is the set of kind-0xC0 records activated since power-up, sent
  as one burst (~1 ms apart) every 5 s. Which records are in it is history: the
  coach sweeps 0x11 although a stored 45 no longer triggers its clamp relay.

Input pins (tables 0x10/0x20, `G12.set_input(pin, active)`): rules fire by
phase -- press (1), tap release (2), held (4), release after hold (8). Held
begins 1 s after the press and repeats each second (PC.24: ON frames refresh at
1.000 s from the ON edge; taps up to 872 ms never repeat). Pin 1 is the wall
switch for light 0x16 (tap toggles, hold ramps, release stops); pin 10 is the
ignition sense (FB then AA).

Modelled from captures, not the MCF: 1FED9 handling (D1 absolute, D2 and the
DELTA set signed), the 15FCE echo of writes and answers to reads, the rule
scheduler (kind 0x80 1 s, 0xC0/0xCC 5 s, 0x40 3x at 250 ms, else once), the
G12 setting its own cells from its own 1FED9 rules (loop-back), receive rules
(byte at the record's index; u16 for kinds 0x28/0x2C; DM_RV filtered by source
address), sensors, NVM (cells 0x00-0xFF).

`fireflymcf.emu.tools.fidelity --g12` on pc23-baseline: every frame of the emulated G12
matches the coach in DGN, instance, rate and payload (15FCE burst, TANK_STATUS,
1FACF, 1AADC, DM_RV, 1FED8, 0FF01, product ID, 1FFE2, 17EFE, and the sensor
broadcasts, which the tool feeds from the recorded G12 on the replay's
timeline) except the wall clock. On pc28 (AES engine run, 366 s) the emulated
G12's 0x0A follows the APS-500 through the same values as the real one, its run
state 06 goes 3 -> 0, and the awning Unlock comes at **260.569 s vs 260.567 s**
real -- from the MCF's code driven only by the replayed ignition input
(100 Hz tick: 260.564 / 260.570 s on the two replay passes; 16F84, 16C84 and
ambient now match too). With the tick at its real rate two settle-after-boot
behaviours became visible and the fidelity tools now seed past them: the
charge-rate soft start (cell 0x81 +20 every 10 s up to 0x92 with a 1FF95 each
step, 13.6 +0x0E48; seeded 0x81 = 0x92) and quiet hours (cell 04 from the clock
cell vs 2B/2C, +0x0F70; the G12 clock now runs from the capture's own 1FFFF).
The output checks also feed the G12 every foreign frame, so the WS500's DM_RV
keeps cell 0x53 unset as on the coach (init defaults it to 0, which arms the
AES watchdog's 60 s channel-18 OFF).

**BF and the auto-start (2026-10-22).** Cell 0x7D is raised only by bus
loop-back of 1FED9 rules #7 (7D := 1, BF 01 "stop") and #3 (7D := 2, BF 02
"start"). The blob activates #7 at one site (+0x15A6), executed and confirmed:
on an ignition edge (0x28 != latch 0x29) with 0x01 = 1, outside AES (EF = 7A,
EF not 2/3; that path copies 0x2D into 06 and needs 3), together with #10 (0x01
report), #4 (1FEDB) and #6 (79 := 0). The coach was in AES (EF 3) for pc24, so
pc24's G12 BF 01 at ignition off is not this path; pc24 kept only 1FBDA. The
coach tests of 2026-10-22 show it is most likely an AES stop: the G12's own
stop sends BF 01 as it drops the start channel and the ignition falls ~0.5 s
later (16:46:19.7). Nothing in the blob activates #2/#3: the application
does, as part of its low-volts start. The G12 now models that start
(`G12._watchdog`, application behaviour from pc39, the only G12 start in the
captures): on each 0x55/0x56 receive, with 0x01 = 1, 0x79 != 1 and 0x0B
non-zero, the mode's voltage below 0x0B for 0x0C + 5 s (fitted: crossed at
102.655, fired 3 ms after the 117.655 frame, 0x0C = 10) -> channel 0x73 ON 200,
channel 20 OFF, rules #2 and #3. Replaying pc39 (G12 1 s phase from its 1FFB7):
start 120.230 vs 120.235, BF 02 120.235 vs 120.244, **BF 00 120.585 vs 120.586**,
the bank timer firing at the divider wrap, an independent check of the 100 Hz
tick. The
0x0C + 5 s split has independent support now: cell 05 bit 1 (below) appears
between the 112.575 and 117.575 sweeps, i.e. 0x0C = 10 s after the crossing.

**AES start, stop and abandoned start (coach tests 2026-10-22,
run 2).** The first
captured G12 auto-stop, an abandoned start and the start inhibit; all in
`G12._watchdog` / `_auto_stop` / `_abandon_start`, checked every 10 ms tick:
* **Start timing is tick-based, from when the condition becomes true**: enabled
  with the voltage already below Start At, the start came 15.012 and 15.002 s
  after the enable (0x0C = 10), between 1FFFD frames -- not on a receive, as
  the pc39 fit had it (there the condition began at a frame, so both fit).
* **Inhibit (cell 05 bit 2, 0x04)**: AES enabled while the ignition is on
  (cell 0x28 = 1) sets it, and only disabling clears it (an ignition-off does
  not: 16:08:11). With it set the start goes pending (0x307 at 16:21:20) and
  never fires. Stop, then Enable with the ignition off: 0x381, start 15.0 s
  later (16:23:10 -> 16:23:25.501). Four enables fit (16:07:40, 16:09:17,
  16:20:00 with the ignition on; 16:15:10, 16:23:10, 16:27:17 off).
* **Stop**: running (0x79 = 1), the mode's voltage above stop volts 0x0D for
  0x0E s -> rules #4 (1FEDB FF FF C8 01 01 00), #5 (channel 0x73 OFF), #6
  (79 := 0), #7 (7D := 1 -> BF 01), AES stays enabled and cell 05 drops bit 1.
  Strictly above: a reading of exactly 0x0D (1088 at 16:36:19.220) restarted
  it, and the stop came 600.5 s later; 0x0E is read live (900 -> 600 twenty
  seconds into the run). The panel cannot set 0x0E below 600 (300 bounced
  back) and the emulated blob floors it the same way. Start At (0x31) tops out
  at 1080 (54.0 V).
* **Abandoned start**: after the start the ignition came on 57.6 s later and
  dropped 10.6 s after that with no engine; 59.7 s after the drop, on the timer
  bank's wrap, the blob's own channel-0x73 OFF (frame-table record 3,
  `12FF00030000`), then rules #4-#7 and AES disabled (05 = 0x3C0). Modelled as
  the application reacting to that record while 0x79 = 1. Without any ignition
  the blob fires it 60 s after the start. Cell 0x53 must be unset (the WS500's
  DM_RV keeps it so on the coach) or block +0x0D0C's timer fires every 60 s.
* **Two 1 s phases**: the output driver's tick (16F84, 2 s statuses) ran 0.46 s
  after the sweep, which the blob's timer bank shares (BF 00, the 60 s OFF).
  `G12(output_phase=)`; `g12_outputs_check.boot_phase` now returns both
  (the sweep's phase, the 16F84's offset from it).
Replaying run 2 into an emulated G12 (a one-off script: the
g12_outputs_check harness plus the real sensor feed): both starts 4-10 ms off,
the abandoned start 12 ms, the stop 4 ms early (16:46:19.721 vs .717), BF
pulses within 12 ms, and cell 05 identical from 16:23:35 on. Run 1 (the A/C
run, the sensor fault, Test 3) matches except as below. Not modelled:
* a panel **AES Stop** (panel BF 01, 1FEDB 12 OFF, 01 := 0): the real G12 sent
  rules #4-#7 at 16:09:06 (and again every 5 s, at .387, until re-enabled),
  16:13:43 and 16:23:03, and nothing at 16:14:53 or 16:19:48. Cell 05 went
  0x300 at 16:09:06 but 0x3C0 the other times; no rule found;
* rule #2 (79 := 1) was missing from the real 16:23:25 start but present at
  16:27:32 and in pc39;
* the immediate 15FCE 01 answer to an enable/disable write is the generic form
  (`01 00 v 00 00 00 00 00`) on the coach; the emulator sends the sweep template.

A low chassis battery also started the engine without the G12 at 16:17:40
(ignition on/off/on, no start channel, no BF): the chassis's own feature.

**Cells 03 and 05 (2026-10-22, application, `G12._aes_state`).** Swept every
5 s; the panel copies 05 as data point 231 (the spec's AES state, 64 / 192).
From every capture: 05 = 0x300 | **01** AES enabled (= cell 0x01) | **02** the
low-volts start pending, set 0x0C s below 0x0B and kept until AES is disabled
or stops the engine (pc39: 769 -> 771 -> 768; 16:46:20 0x381) | **04** AES
enabled with the ignition on, blocks the start (above) | **40** set by an
ignition change while AES is disabled or a stop that disables it, cleared when
AES is enabled or disabled (pc37 832 -> 769 -> 768; pc48 768 -> 960 -> 832) |
**80** for 30 s after such an event, an automatic stop, or the ignition coming
on during a run (pc27 has both edges, pc28, pc48 the owner's injected 1FBDA;
28.8-30.5 s from the sweeps). With AES enabled and idle an ignition change sets
nothing (2026-10-22 16:08:11, 16:17:51), nor does the ignition dropping
mid-run (16:24:33).
03 = 720 while enabled, FFFF otherwise (no other cell holds 720). Replaying
pc27, pc28, pc37, pc38, pc39 and pc48 into an emulated G12 gives the real
value sequences; only the sweep phase differs (the harness does not align it).
What 720 and the 40 bit mean is not known.

**Pin 2 and the thermostat (2026-10-22).** fireflymcf's "+0x17C0 light
dispatch" is the **pin-2 wall switch**: table 0x61 #10 loops the G12's own
1FBDA A2 into cell 0x4A, and +0x1048 change-detects it -- held 1 s (100 ticks)
sends ramp (21) and again every 0.5 s, release after a hold sends stop (04),
a tap toggles (FA 05) -- on channel 25 (Cargo / Bath Lts) or, for floorplans
4/5/6/10, 35. The owner's photo labels this coach's pin 2 "Bath Lts"; the
14.12 coach's pin 2 is "Bench Seat OHC Lts" (35). Already ran via loop-back;
now tested. 14.7 drives input 1 -> 35 on every floorplan.
Its supposed gate, cell 0x68, is unrelated: +0x1778 is the **A/C fan** arm.
Cell 0x68 = THERMOSTAT_STATUS_1 (1FFE2) bytes 1 | 2 << 8 as the G12 reports
them (mode / fan mode nibbles, fan speed): with fan mode 1 the blob runs fan
low (rules #37 on, #34 off) at speed 100 and fan high (#38, #35) at 200,
matching pc8's G12 frames at 1108.43 / 1117.82. Writing 0x68 from 1FEF9 and
the compressor (rules #30-33 and #40/#41, 2B/09/0A, which no blob site
activates; pc8: cool on 3 s after mode 1, re-sent every 5 s) are the
application's, and are pc8's whole ACK gap (68 own commands).

**Thermostat modelled (`fireflymcf/emu/g12thermo.py`, 2026-10-22).** Per the owner
the temperature sensor is wired to the G12, which is therefore the thermostat:
it broadcasts 1FF9C (cell 0x6B) and 1FFE2 and acts on 1FEF9. Modelled from pc8,
pc34, pc37, pc38 and pc63; the module docstring has the rules. In short:
1FEF9 instance 0 -> state (FF keeps a field; FFFA/FFF9 = +-1 degF, fitted to
pc8's three steps); 1FFE2 at once and every 5 s (heat follows cool at the
periodic one); cell 0x68 = status bytes 1-2 so the blob runs the fans; cool /
auto with ambient above the cool setpoint starts the compressor after 3.0 s,
re-sent every 5 s; every run lasted exactly 120 s whatever the mode did; fan
back to auto turns the running fan off; fan on or a new speed is switched by
the application with the command (old fan off, new pair) and the blob's off
rule follows on its next 10 ms pass (pc8 1108.43 / 1117.82); kinds 0x24/0x28 go out as pairs; all
off at power-up (pc63; `G12(cold_start=False)` for capture replays). Two
emulator bugs fixed on the way: `is_active` only knew periodic rules, so the
blob re-activated the fan rules every tick; and the canned 1FFE2 is gone.
Results, real / emulated A/C commands and ACKs: pc34, pc37, pc38 identical
(pc34's 5 s status phase is 1 s off, harness alignment); pc8's fan frames
identical; the compressor's last re-send is a race (pc8 has none, pc34/pc38
do), and at 1141.91 the capture holds one ACK for two commands: ACKs 164 / 167
(was 97), identical status frames 1223 of 1370
(was 611). **Heating, observed (coach tests 2026-10-22, run 3)**: the owner
turned the OPTIONS row Heat Pump (cell 0xEB) on although the coach has none
(output 10 is unwired: undercurrent). Heat, or auto, with the ambient (20 degC)
below the heat setpoint (78 degF) runs the cooling sequence on 0A ("A/C Heat
Pump") **alone** -- 2B/09 stay off: 3.0 s after the command the fan pair (0B
off, 0C on, then 0B on / 0C off 10 ms later) and #41 0A ON, re-sent every 5 s;
120 s run whatever the panel sends; #40 OFF three times 250 ms apart with the
fans' 15 s run-on. **After a run the A/C rests 120 s**: Auto 56 s after the end
started 120.00 s after it (17:39:19.04 -> 17:41:19.04), not 3 s after the
command. Replaying run 3: every 1FEDB within 7 ms; the only difference is the
known last-re-send race at the second stop.

**Thermistor faults (2026-10-22).** The panel lists "The Front thermistor has
an invalid reading." (SETTINGS > Network Diagnostics > FAULTS, with a warning
triangle in the title bar; seen on a 14.12 coach's photo) when GUI data point 9
-- 1FF9C instance 0, the G12's sensor -- is out of range or stale. Bisected in
the emulator through the G12's cell 0x6B: raw 0x1BC1-0x2C5F is fine, 0x1BC0
(222 K, -51 degC) and below or 0x2C60 (355 K, 82 degC) and above is a fault
(0x0000 and FFFD-FFFF included), and so is no 1FF9C for 30 s (the data point's
timeout); it clears with the next valid frame. Probing every data point finds
only 9 for it on this floorplan; the Rear / Bay / System texts (GuiData
0x92e84-0x9302f, two layouts each for Front and Rear) did not appear for 10-12.
**Unplugged on the coach (2026-10-22, run 1):** the G12 keeps sending 1FF9C
every second with the open-circuit reading, 0x1AF0 (-57.6 degC) at 15:56:51
settling noisily around 0x191F (-72 degC); the panel shows "Temp Sensor Error!",
"---" for the temperature and the triangle. In Cool with the setpoint stepped
to ~55 degF the G12 sent no output command for 2 min 18 s -- consistent with the
model (an ambient below the setpoint is no demand), so whether it would refuse
an open-high (shorted) reading is still unknown. Plugged back in, the reading
recovered within ~5 s (3.7, 18.0, then 22 degC), so the fault clears at once.
A normal cooling run in the same log matches the model frame for frame (start
3.0 s after Cool, 120 s regardless of Off / Cool commands, the fans' 15 s
run-on). `python -m fireflymcf.emu MCF --ambient -60` shows the fault in the demo.

**The whole FAULTS list (13.6, from the object tree).** Object 525 (the FAULTS
box) holds three type-6 switches, so this build has exactly three faults:
variable 235 G12 offline (objects 495-497), 236 Front thermistor (512-514) and
237 a wireless wall switch (504-505, an inner switch on variable 5, the
floorplan, picks one of nine names: Rear Wall, Hallway, Hallway, Bed Wall,
Entry Door, Entry Door, Hallway, Hallway, Rear Wall; WD shows "Hallway Switch").
The Aqua Hot, G12 #2, G6, PMM, transfer switch, Atwood and Rear/System/Bay
thermistor texts are in objects nothing references here. 14.7 is the same:
object 466 with variables 206 / 207 / 208 (G12 offline, Front thermistor, wall
switch with seven names on its floorplan variable 10). The writers, in
GuiCode's per-pass block at 0x24B440 and its outlined tails at 0x24E852-0x24E8DC:
* 236 = (byte 0x2003A33F == 1), evaluated only while 235 = 0. 0x2003A33F is set
  at 0x24AA98 when the converted ambient (0x2003A53E) is the 10000 (0x2710)
  sentinel, i.e. data point 9 invalid or stale.
* 237: a deadline at 0x200398B4, set once at boot to now + 600 000 ms
  (0x247F6E, among the other start-up timers). After it: 237 = (variable 480 ==
  0). Variable 480 (0x2003A6CA, written at 0x24B2FC) = FUN_0025918C((data point
  16 + 1) / 2), i.e. 0.1 V steps of 1FFFD instance 0x81, the wireless panel's
  battery that the LCD itself reports every 10 s and loops back into point 16:
  2.4-3.5 V -> 1 (fine), 0.1-2.3 V -> 2 (low, not listed), 0, >= 3.6 V or stale
  -> 0 (listed). Variable 481 is the same for instance 0x82 (no widget here).
  Checked in the emulator by shortening the deadline (`tests/emu/test_faults.py`);
  `--remote-battery 0` runs without the remote, so the fault shows 10 min in.
  **Pulling the switch's battery does not raise it** (coach, 2026-10-22, panel
  up > 10 min): the LCD keeps re-sending the last reading (2.95 V) every 10 s,
  so point 16 never goes stale; the emulator does the same when the remote goes
  silent after reporting. Only a switch never heard since boot (or a 0 / >=
  3.6 V report) lists it.
* Each listed fault also increments 0x20039CCC (the title-bar triangle's count,
  inferred).

**The panel does not use cell 05 or 03.** Data point 231 (15FCE selector 05)
is read once, at 0x24A1C6, and stored to 0x200399B6, which no instruction reads
and no widget variable maps to; 14.7 does the same (0x24A6A2 -> 0x20039682).
Cell 03 has no data point. So the panel cannot name bit 0x40 or the 720; only
cell 01 (point 41) matters to it.

Not modelled: other cells the application writes; what the application does with ABI 17's
notify values; the A/C program's inputs.

### The G12 as load driver (`fireflymcf/emu/g12out.py`)

The coach's G12 is also the load module for outputs 1-44 (no other node
reports them). Modelled from pc8/pc15/pc16/pc27/pc28, every rule checked with
`fireflymcf.emu.tools.g12_outputs_check`, which feeds a capture's 1FEDB commands (and the
real G12's input-pin edges) to an emulated G12 and compares the commanded
output's status and level report:

| capture | identical responses | notes |
| --- | --- | --- |
| pc8 lighting | 97/97 | |
| pc16 masters | 20/20 | memory off / restore |
| pc27 awning interlock | 24/24, ACKs 27/27 | channel 3 locked by the G12's own interlock code |
| pc15 ramp | 16/16 | step model (below) |
| live 2026-10-21 ramp / lights / heaters | 29/29, 21/21, 8/8 | ramp: all 137 status + current frames identical |

Behaviour (details in the module docstring): ACK `00 FF FF FF FF DB FE 01` for
every 1FEDB, **its own included** (pc8: 97 + 68 commands, 164 ACKs); 1FEDA
`[inst, FF, level, FC|lock, duration, cmd, 04/00, FF relay/00]` after each
command, at each 10 levels of a ramp and every 2 s while on or locked; 16F84 to
0x84 at 1 Hz while on, plus an edge frame (not for OFF, not for timed outputs).
**16F84 byte 4 is the load current, not the level** (unit unknown): at full
brightness output 24 reports 0A, 43 02, 32 0F, 26/38 00 while on; the awning
motors 37-79; it scales with the faded output (`full*level//200`: rvc-spec.yml's
`floor(brightness*15/200)` fits output 32 at 24 of 25 sweep levels because its
full-load value happens to be 15). The half-bridge outputs 9-20 send FF FF (no current
sense: the "odd" output-18 frames). 16C84 to 0x84 with a switch-on
count and on-time minutes at switch-on and each minute. Outputs 1-8 are relays
(command 1 switches one on whatever the level); FB restores the memory-off
state, FA/C8 toggles use the last level; lock (33) makes an output ignore all
but unlock (34); timed commands (awning EXT/RET, 2 s) switch off when they run
out. **Timed commands (2026-10-22):** the off comes exactly D s after the
command (pc34 285.821 -> 300.819, pc37 851.466 -> 866.466), not on a tick;
byte 4 reports floor(D - elapsed) with elapsed counted in the G12's 10 ms
ticks (0F 5 ms after the command in pc34 but 0E 2 ms after in pc37 -- a tick
fell in between). **OFF (3) with a duration is a delayed off**: the output
stays on (status level 200, cmd 03, the countdown) and goes off at D; with the
output already off it reports D and still sends the expiry status (pc8
1117.830 -> 1132.829). That is the A/C fans' 15 s run-on (#34/#35 are `OFF
0F`). A plain OFF, SET or toggle cancels it, and 16F84 keeps reporting through
a timed command (pc8 output 3, the fans in pc34/37/38). **Ramp** (command 21, re-sent while held -- every 1 s from the panel,
0.5 s from the Firefly app) moves in **steps of 1 % (2 levels)**: the level
is always even, and the G12 waits `78.64 - 0.375 * level` ms at each level
before the next step (3.6 ms at full, 77 ms at the bottom; least squares over
all 38 ten-level crossings of pc15 and the live test, worst error 1.1 ms,
model step times within 0.4 ms of the real statuses). One dwell rule in both
directions is why each 10 levels take ~3.7 ms longer going up (dwells at
L..L+8 instead of L+2..L+10) and why pc15's up ramp and the live one agree to
1 ms from "10 on the way up". Re-sent commands do not restart the dwell.
**Every hold starts downward**, turns back up at the bottom (4; the turn costs
one more dwell, which is what makes a hold from 6 reach 10 only at +0.383 s)
and stays at full while held; command 4 stops it. PC.15's "direction
alternates between holds" is what holds ending near the bottom look like.
1FEDA duration is 02 answering a ramp command and 01 on ramp steps and on the
2 s periodic statuses while a hold is running. The 16F84 current lags the
level by ~0.27 s while ramping (live: every moving sample allows exactly 0.27;
pc15 0.22-0.34, i.e. a count of noise; a sliding-window average fits worse).
The 2 s statuses go out 0.5 s after the G12's 1 s tick (live: 16F84 at x.356,
statuses at x.859); `g12_outputs_check.py` now boots the emulated G12 in that
phase, so periodic frames are compared too.

**When the undercurrent bit latches** (2026-10-22, 999 statuses after a
switch-on across the captures and live logs): at a check on the G12's
half-second grid -- its 1 s tick and the status tick 0.5 s later -- once the
output has been on for 0.23 s (fit 0.222-0.236); statuses answering a command
report the bit but do not check. That is why 0.225 s is clear and 0.254 s
latched at the same status phase, and why a switch-off 0.378 s after switch-on
(pc13, no check in between) is clear while 0.42-0.48 s are latched. Not fully
determined: output 38 latched at 0.226 s in pc14 but was clear at 0.251 s in
pc15 with the same command and tick phase, so something off the bus also
decides (a sampling clock of its own?). On the half-bridge outputs 9-20 a
command's own status never shows the bit, but the latch stays: output 9's ON
re-sent every 5 s while the compressor runs answers 04, and the periodic status
3 ms later is 44 (pc38 874.346 / 874.349); outputs 21-44 show it throughout
(output 43's re-sends, output 21's level changes and ramps live). With the
timed-command fixes and the load seed taken as the most common full-level
reading (heaters flicker 2B/2C, so the maximum overstated them), identical
status/level frames: pc8 1223 -> 1335 of 1370, pc34 582 -> 631 of 632, pc38
604 -> 653 of 658, pc16 240 -> 254 of 260, pc27 49 / 49, pc13 86 / 86; pc15
104 -> 103 (the case above). What remains is current noise (heaters, the
water pump and awning motors) and the compressor's end-of-run race. Also not modelled: the
loads' real currents beyond the coach's measured table (`COACH_LOADS`; the
fidelity tools seed loads and lifetime counters from the capture).
`fidelity.py --g12` on pc28 with the driver: output statuses follow the same
2 s rhythm (131 locked channel-3 statuses until the 260.569 s UNLOCK); the rate
difference is channel 18, which the coach's panel switched off at 186 s (a
person pressed AES Stop) and the replayed panel did not. The demo's DimmerModule keeps the instances the
G12 does not own (59-71, 90-104).

## Joining the coach bus as the panel (2026-10-22, `--can-tx`)

With the real panel unplugged from the coach's RV-C bus there is no address
conflict, so `--can ssh:USER@HOST:CHANNEL --can-tx dry|live` joins the bus as
the panel: one SSH session runs a standard-library bridge on the remote host (a raw
SocketCAN socket; `canbus.BRIDGE`), received frames in as `candump -L` lines,
transmissions out. `--network none` is forced and simulated time is paced to
the wall clock. **dry** starts the remote end receive-only and logs what the
panel would send (`--tx-log`, default `build/live/panel-tx.log`); **live**
transmits, holding settings writes unless `--allow-config`; any SA 9F frame on
the bus (a real panel) stops transmission. Dry runs on the coach, 90 s and 40 s,
nothing on the bus from 9F: the emulated panel's traffic is the real one's
(address claim 18EEFF9F `0000A024A0650080` after a request from FE, DM_RV
19FECA9F `05 84` each second, the 14 s requests for 1FED8 / product ID with
1FBDA `F9 00`, 1FFFD `81` every 10 s -- `81 FF FF FF` with `--remote-battery 0`,
the real panel sends the wall switch's battery) plus settings writes from its
own EEPROM at boot: 1FED9 `FF 96 9B 0F 00 00 D1` (selector 0x9B := 0, the AES
enabled mirror the real panel writes with every enable / disable) and, in one
run, 1EE00 `0D 00 00` and `18 00 00` (the real panel's option broadcast:
`0A 00 01` turned the Heat Pump option and G12 cell 0xEB on). Those are what
live mode holds. The coach's wireless wall switch talks to the panel by radio,
so it does nothing while the real panel is unplugged.

## Live validation on the coach (2026-10-21, listen-only over SSH)

Live captures (not published; the owner operating loads remotely via SA 0x82):
* `fidelity.py --g12` on 10 live minutes: every G12 DGN matches the coach in
  rate and timing; payload differences only in live current jitter, the wall
  clock, and the RF remote's battery (coach 2.70 V vs the simulated 2.80 V).
* Output driver (`g12_outputs_check.py`): tank heaters 8/8, lights 19/21 (the
  other two are the coach's 1 Hz report landing a few ms after the command).
* Learned live: load current proportional to level on a second output (21:
  200 -> 07, 100 -> 03, 50 -> 01); level changes while on are immediate (soft
  start only from off; relays none); relays measure current (no undercurrent
  detection); no off-edge 16F84 for relays; the undercurrent latch (half-second
  checks, see above); 16C84 counts switch-ons and on-minutes.
* **Tank heaters 6 and 7 draw no current** (5 = 2C, 8 = 41) while the G12 and
  the panel report all four on: the owner's two failed heaters, visible only in
  16F84.
* **Config read-back** (`fireflymcf.emu.tools.g12_readback`, PC.49's D3 queries from 0xC0,
  approved by the owner): 256/256 answered, no CAN id outside the live baseline
  except our own. Cell 0x55 = the WS500's 1FFFD instance 1 (1071) and 0x0A steps
  1072 -> 1071 with it; 0x56 = the G12's own house reading (248). The emulated
  G12, seeded only with what the real one broadcasts, matches **231/256 cells
  (189 of the 214 never broadcast)**. The rest: the owner's settings that are
  stored but never swept (82/83, 86/87, 88/89, 99, BA, BC), cells the 13.6 code
  never writes (12 19 1A 1D 1E A0 DB DC E7 ED F0), and time/live drift (81 soft
  start, 2A, 56, 6B, B3).
* Table 0x61 record params are the **accepted value range**: a received value
  outside it leaves the cell unset (FFFF). The WS500 sends DM_RV with byte 0 = 5,
  below #14's 80, so cell 0x53 (AES watchdog block C's guard) reads FFFF, and
  block C's status 0x77 reads 0 -- both reproduced once modelled.
* Cell 0xC2 (LPG) holds level | resolution << 8 (0x6450 = 80 % of 100).

## Nodes on the coach's RV-C bus (can1, live 2026-10-21)

| SA | Node | Evidence |
| --- | --- | --- |
| 0x9F | Spyder/Firefly LCD panel (this firmware) | address claim; hard-coded SA |
| 0x9C | G12 logic controller and load driver | product ID `Spyder Controls*G12 Panel 2v2 3v0d*0000000*22*` |
| 0x80 | Wakespeed WS500 alternator regulator / virtual BMS (APS-500 is its older name; the `APS500` peer) | product ID `Wakespeed Offshore*WS500*<serial>**`; 1FFFD inst 1 |
| 0x42 | Inverter/charger | 1FFD4/1FFD7 |
| 0x70 | Renogy BMS (proprietary, not RV-C) | 0EF80 every 50 ms, 0FF70 every 2 s (owner) |
| 0x65 | Elwell Timberline heater | 1FFE2/1FFE4/1FFF7/1FE97/1FE99/1FEFA/1FF9C every 5 s, 1EF65 proprietary (owner) |
| 0xFD | VegaTouch Bluetooth bridge | product ID `Firefly*Standalone Module*0001**`; DM_RV every 0.5 s (owner) |
| 0xBF | Level sensor | LEVELING_SENSOR_STATUS 1FFEB at 1 Hz, status records on 1EFBF |

## Coach replay

`--replay` plays coach captures (candump -l). With
pc23-baseline the panel comes up on the coach's floorplan (WD, from the G12's
F5 broadcast) and shows the coach's real values: Inverter "Passthrough",
Chassis 11.8 V (red), AES Battery Voltage 53.9 V, AES settings 51.0 V / 60 s /
115 min / 53.0 V / 30 min, quiet hours 22:00-08:00, CLIMATE 80. Tanks read 0 %.
Frames from the recorded panel (SA 0x9F) are skipped.

## Fidelity check

`python -m fireflymcf.emu.tools.fidelity MCF CAPTURE.log` replays a capture and compares what the
emulated firmware transmits with what the real panel (SA 0x9F) sent. On
pc23-baseline every message the real panel sends is reproduced with identical
payloads and periods: DM_RV (1 s), requests for FED8/FEEB (8/min), 1FBDA F9
(14 s), DC_SOURCE_STATUS_1 inst 0x81 (10 s, `81 FF 38 00`). The Product ID
request to the inverter (EA42 EBFE) was every 20 s emulated vs 16.9 s real;
the 366 s pc28 capture showed it was systematic. Cause: LTDC timing. The firmware programs a 32 MHz pixel clock
(HSE 12 MHz: PLLSAI N=96 R=3 DIVR=2) and a 908 x 992 total frame, i.e. **35.5 Hz**,
and the line-event callback alternates lines 29/512 for **two line interrupts per
frame (71 Hz)**. GuiCode's inverter counters need 80 x 15 = 1200 ticks ->
16.9 s. The LTDC model now derives frame timing from these registers and fires
the line interrupt at the LIPCR line; the period matches to the millisecond.

pc28 (AES engine run) also shows real-panel frames the emulator does not send:
1FBDA `BF 00/01 ..`, a G12 selector-01 set/query and a dimmer command to
instance 0x12 -- presumably reactions to the AES engine run or someone using the
panel; not investigated.

Hold gestures (measured with the corrected timing): SETTINGS > Floorplan opens
the technician page from ~3.2 s (3.0 s does not); a HOME tank gauge opens
PROBE TANK DIAGNOSTICS from ~5.5 s (5.0 s does not).

**Wireless control panel** (owner: DC_SOURCE_STATUS_1 instance 0x81 is its
battery). It reaches the LCD through an RF module on **UART4 at 50 kbaud**
(UART4 ISR FUN_00216b1c ev_1e27027b fills a 128-byte RX ring; receiver
FUN_0021ce28 ev_4dfe4d57). Frames: `F0 0F` + 10 bytes: [0] ?, [1-4] remote ID
(u32 LE, paired IDs in EEPROM), [5] type (A5 status / FA buttons), status:
[6-7] temperature (signed 12-bit, 1/16 C), [8-9] battery mV. Every 10 s the LCD
reports battery as (mV + 25) / 50 in 0.05 V on 1FFFD (instance from config,
0x81) and the temperature on 1FF9C if configured. Button packets carry four
bitmaps dispatched to FUN_0021cdf8(remote, button, kind). Emulated by
`periph/rfremote.py` (status packets only; a blank EEPROM pairs ID FFFFFFFF).

## Performance

`fireflymcf.emu.tools.bench` (boot 3 s, accept + settle 3 s, HOME idle 10 s, demo network):

| change | overall | HOME idle |
| --- | --- | --- |
| before | 0.44x | 0.48x |
| timers without update IRQ no longer schedule events | 1.32x | 1.99x |
| DMA2D zero-copy + scalar path for tiny transfers | 1.50x | 2.0x |
| direct libunicorn callbacks, leaner bus dispatch | 1.55x | 2.0x |
| no trap for `msr basepri` right after `cpsid i` (68 -> 38 sites) | 1.68x | 2.2x |
| native block-hook slicer instead of `count` | ~1.9-2.0x | 2.7x |

**Page changes (2026-10-22).** Switching pages the firmware keeps the screen dark
for ~0.1 s of simulated time while it redraws, and in that window programs DMA2D
~24,600 times -- 16,600 single-pixel A8-over-RGB888 blends (anti-aliasing) and
~7,300 small register fills -- with ~325,000 register writes. The emulator ran
that 4-10x slower than real time, so the dark gap lasted 0.5-1.1 s of wall time.
Now DMA2D is remapped with its own write callback (`FAST_MMIO`,
`fastreg.mmio_map_offsets` -> `DMA2D.fast_write`: whole-word writes stored
directly, only CR START / CLUT START / IFCR dispatched), one-pixel blends take
`DMA2D._blend1` and fills write guest RAM directly (both bit-exact,
`tests/emu/test_dma2d.py`): dark gap ELECTRICAL 1.12 -> 0.76 s, LIGHTS 0.87 -> 0.60 s,
CLIMATE 0.48 -> 0.34 s. What is left is mostly executing the firmware and one
Python callback per register write; mapping the registers as RAM would remove
the latter, but the START bit then stays set in memory where the firmware reads
CR after each transfer. DMA2D writes are no longer counted by `bus.stats`.

Findings:
* **TIM3 runs at ~20 kHz** (8160 CPU cycles; backlight PWM) and TIM1 also
  free-runs, both with the update interrupt disabled. Every overflow still
  ended an idle skip, so the idle task was re-entered 23,000 times per second.
  Now an overflow only sets UIF, caught up lazily when SR is read.
* **The GUI blends anti-aliased edges one pixel at a time** with DMA2D (mode 2,
  A8 over RGB888, w x h = 1): ~30,000 transfers in the first 6 s, each 14
  register writes. The numpy setup cost ~50 us per pixel; transfers of 64 pixels or fewer
  now take a scalar path (bit-exact with the numpy one, `tests/emu/test_dma2d.py`).
* **LTDC render** for the GUI window was 34 ms per frame (a full Porter-Duff
  blend of an opaque RGB888 layer); now 2.3 ms via a zero-copy BGR->RGB copy.
* **Real CPU load on HOME is ~7%** (~8M instructions/s, mostly a 1 ms task
  iterating a table at 0x21bac0). The old flat 50-cycle charge for
  early-stopped slices hid this and reported 100% idle. With exact accounting
  the pc23 fidelity check still matches the real panel in every period.
* Unicorn's `count` limit costs 7.2 ns per instruction vs 1.9 ns without it
  (per-instruction helper call); the C block hook costs ~2.0 ns.

## Emulator limitations

* The simulated CAN bus delivers one frame per frame time (~130 bit times);
  before this, bursts overflowed the 3-deep RX FIFOs (60 drops in 8 s) and
  frames silently went missing.

* Interrupts unmasked by an exception return or by code outside the scanned
  images (none known) are taken at the next slice boundary.
* CPU time is approximate: one cycle per Thumb halfword executed (the M7
  dual-issues and has caches/wait states). Without the native slicer,
  early-stopped slices are charged a nominal 50 cycles instead.
* `Machine.cycles` only advances between slices, so timers read inside a slice
  see the time at its start.
* No flash programming (not used by this firmware), USB enumeration, ADC values,
  QSPI indirect mode, DMA streams, RTC, or bxCAN error states/overrun flags.
* Clock tree is fixed (160 MHz) rather than derived from RCC.
* Speed: about 2x real time overall (see Performance).
* Interrupt-unmasking instructions are patched in emulated memory (SVC traps),
  so code at those 38 addresses differs from the image if inspected at runtime.
