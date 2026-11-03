# Firefly / Spyder Controls LCD emulator

Emulates the **Spyder Controls 7in Lite LCD** (Firefly Integrated RV control
panel, STM32F7) from its `.mcf` firmware package: FreeRTOS, the GUI on an
800x480 LTDC display with GT911 touch, and RV-C on a modelled CAN bus with
simulated devices (clock, thermostat, batteries, dimmer channels, inverter,
and the G12 logic controller with floorplan/option selectors). Settings persist in a file-backed EEPROM (`--eeprom`, default
`build/eeprom.bin`; `--eeprom none` for a fresh panel each run).

```
pip install "fireflymcf[emu] @ git+https://github.com/dbergl/fireflymcf.git"   # [emu,emu-gui] for the window

python -m fireflymcf.emu MCF --gui -q                                  # window, mouse = touch
python -m fireflymcf.emu MCF --seconds 5 --screenshot build/shot.png -q # headless
python -m pytest tests/emu                                             # its tests (from a checkout)
```

`MCF` is a stock or built `mcf.bin` (the emulator is developed against 13.6). The tests that
boot a firmware image need the research corpus (`scripts/link-research.sh`); without it they are
not collected.

Status: boots to the terms-of-use page; tap **I ACCEPT** to reach HOME, where
the clock, climate, House/Chassis voltages and lights respond to the simulated
RV-C devices. Runs at about 2x real time (1.3x while booting/redrawing, 2.7x idle on
HOME; `python -m fireflymcf.emu.tools.bench` measures it), and the GUI is paced to real time.
See [EMULATOR-NOTES.md](EMULATOR-NOTES.md).

### RV-C network

`--network demo` (default) puts simulated devices on the bus; `--network none`
leaves the panel alone. `--log-can` logs frames delivered to the panel (its own
transmissions are always logged). To connect real tools, bridge to Linux
SocketCAN:

```
sudo modprobe vcan && sudo ip link add dev vcan0 type vcan && sudo ip link set up vcan0
python -m fireflymcf.emu MCF --gui --can socketcan:vcan0
candump vcan0                                   # in another terminal
```

Any python-can interface works (`virtual:<name>` for in-process tests).

To mirror a real coach, listen to its bus over SSH (needs key login and
can-utils on the remote host; `--network none` so no simulated devices
answer for the coach's own):

```
python -m fireflymcf.emu MCF --gui --network none --can ssh:USER@HOST:CHANNEL
```

This link is **listen-only**: the emulated panel claims the real panel's
address (0x9F), so its frames never reach the coach, and the real panel's
frames are dropped on the way in (the firmware resets on an address conflict).

To drive the panel with a recorded coach instead of simulated devices, replay a
`candump -l` log (frames from the recorded panel, SA 0x9F, are skipped):

```
python -m fireflymcf.emu MCF --gui --replay CAPTURE.log --replay-loop
```

### G12 logic controller

The emulated G12 (SA 0x9C) **runs the logic shipped in the MCF's G12 section**:
the section's Thumb code (init + 1 Hz tick: AES/AGS timers and limits, mode
switch, interlocks, quiet hours, tank bucketing, rule gating) executes in its own
Unicorn instance, and the rules it switches on are sent from the section's
transmit templates. So the G12 behaves like the build you load, including edited
G12 sections. It is also the load driver for outputs 1-44 (status, ramps,
locks, ACKs, level reports; modelled on captures, `fireflymcf.emu.tools.g12_outputs_check`
compares it with one), and its input pins (wall switches, ignition) can be driven
with `G12.set_input(pin, active)`. What the MCF does not describe is modelled on coach captures: the
`1FED9` config protocol, the rule repeat rates, NVM, product ID / DM_RV, and the
sensors (simulated in the demo network: tanks, house/chassis volts, ambient
temperature, LPG; the G12 is also the bus clock). The section decoders are
`fireflymcf.g12code` and `fireflymcf.g12rules`; for an MCF with no G12 logic, or a context
ABI the emulator does not know, a protocol-only G12 is used.

The G12 keeps its config cells in `build/g12.json` next to the EEPROM file (keyed
to the G12 section, so another build starts fresh). The demo starts as the
reference coach: floorplan WD, AES mode. To pick a floorplan: SETTINGS, hold the
Floorplan button ~3.2 s, Proceed, choose a model. Holding a tank gauge on HOME
~5.5 s opens the raw tank-sensor diagnostics. Sources: [G12-CODE.md](G12-CODE.md),
[BUS-BASELINE.md](BUS-BASELINE.md) and rvc2mqtt's `rvc-spec.yml`.

`python -m fireflymcf.emu.tools.fidelity MCF CAPTURE.log --g12` replays a coach capture without its G12,
runs the emulated one seeded from what the real one broadcast, and compares both
nodes' traffic with the capture.

## Layout

Everything is under `src/fireflymcf/emu/`.

| Path | What |
| --- | --- |
| `mcf.py` | MCF package / RAM-image parser, ELF export with symbols (`python -m fireflymcf.emu.mcf info\|extract`) |
| `machine.py` | Memory map, image loading, run loop, idle fast-forward |
| `cortexm.py` | Exception entry/return (incl. FP frames), NVIC, SCB, SysTick, DWT |
| `periph/` | STM32F7 peripherals: RCC, PWR, FLASH, GPIO, EXTI, TIMx, USART, I2C, bxCAN (with acceptance filters), LTDC, DMA2D, CRC, QSPI, FMC, SYSCFG, USB OTG stub; board devices GT911 touch + EEPROM |
| `fastreg.py` | Direct libunicorn register access, MMIO and interrupt-hook callbacks (skipping the binding's wrappers) |
| `native/` | Optional C block hook bounding each slice (compiled on first use into `~/.cache/fireflymcf-emu`; `FIREFLY_NO_NATIVE=1` disables it) |
| `canbus.py`, `rvc.py`, `peers.py` | CAN bus with simulated RV-C devices and optional python-can bridge |
| `g12.py`, `g12out.py`, `g12thermo.py` | G12 node running the MCF's G12 code blob and rule tables; its output driver and thermostat |
| `guidata.py` | GuiData decoder: the data-point table mapping RV-C frames to GUI values (`python -m fireflymcf.emu.guidata <GuiData.bin>`) |
| `symbols/lcd-13.6.sym` | Names for identified functions (fed into the exported ELF for Ghidra) |
| `tools/probe_widgets.py` | Finds which data points drive a screen region |
| `tools/drive.py` | Scripted taps/holds with screenshots and CAN log (`python -m fireflymcf.emu.tools.drive MCF --demo name:x,y[,hold] ...`) |
| `tools/bench.py` | Speed benchmark (boot / settle / HOME idle), optional cProfile output |
| `tools/fidelity.py`, `tools/g12_outputs_check.py`, `tools/g12_readback.py` | Compare the emulated panel and G12 with a coach capture |

Run a tool with `python -m fireflymcf.emu.tools.NAME`.

## How it works

* **CPU**: [Unicorn](https://www.unicorn-engine.org/) in Cortex-M7 mode. Unicorn
  does not take M-profile exceptions, so `cortexm.Core` does stacking,
  EXC_RETURN handling and NVIC priority/preemption itself. SVC and exception
  returns arrive through `UC_HOOK_INTR` (2 and 8).
* **Time**: the machine counts CPU cycles (160 MHz, as the firmware programs
  it), executing in slices that end at the next timer event. A native
  `UC_HOOK_BLOCK` callback (`native/slicer.c`) charges each block one cycle per
  Thumb halfword and stops at the budget. That is 3.7x cheaper than Unicorn's
  per-instruction `count` limit and exact even when a hook stops a slice early.
  When the FreeRTOS idle task is seen spinning on `PENDSVSET`, time skips to the
  next event (like WFI).
* **Interrupt latency**: FreeRTOS pends PendSV inside critical sections and
  relies on it firing the moment BASEPRI/PRIMASK drops. Every `cpsie` and
  `msr basepri/primask` that can unmask is patched (in emulated memory) to
  `svc #0xFF`; the interrupt hook performs the write and takes the exception
  right there. SVC and exception return are handled inside Unicorn's interrupt
  hook without stopping emulation.
* **MMIO**: libunicorn MMIO callbacks route to Python peripheral objects at
  1 KiB granularity. Unknown registers become logging `Generic` peripherals, so
  the log shows exactly which hardware the firmware touches next. Pages with no
  useful behaviour (IWDG) are plain RAM to keep hot loops fast.
* **Stopping from MMIO**: Unicorn replays the store if emulation is stopped
  inside its MMIO write callback, so `Machine.stop_slice()` only stops on a plain
  store and steps the PC past it (see `test_mmio_stop_does_not_replay_store`).

## Exporting an image for a disassembler

```
python -m fireflymcf.emu.mcf extract MCF build/mcf --symbols src/fireflymcf/emu/symbols/lcd-13.6.sym
```

writes each component as a raw `.bin` and an ELF carrying the symbol file's names, for Ghidra or
any disassembler.
