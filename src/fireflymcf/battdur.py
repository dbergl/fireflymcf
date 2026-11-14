"""Settable BMS command durations: the Battery Heater and Ignore Alarms hold buttons send a
duration the user picks, stored in the G12 and swept back to the panel.

**Where the value lives (proven 2026-11-13).** G12 config cell `0x72` holds both durations, one per
byte. Cell `0x72` has a `0x15FCE` sweep record (#85) that the G12 program never activates; it has no
default (`g12code.config_defaults_by_selector`), no command rule (`g12rules.rule_for_selector`), no
direct read in the G12 program, and it read unset on a coach (`BUS-BASELINE.md` PC.49). The panel
already subscribes to it (receive var 280) and no GuiCode load covers that slot (`busmap`:
write-only). A bus test (write, echo, 60 s quiet, restore) found it inert: `BUS-BASELINE.md` "Cell
`0x72` is writable, echoed and inert".

**How it reaches the sweep.** The G12 boot list activates two byte-identical `0xE8` sweep records,
#43 and #79 (`+0x888 movs r0,#0x2b` and `+0x83a movs r0,#0x4f`, each followed by
`ldr r3,[r4,#8]; blx r3`), so a coach sweeps `0xE8` twice. Retargeting #43's `movs` to #85 sweeps
`0x72` in the duplicate's place; `0xE8` is still swept once.

**The hold.** The LCD firmware's per-group gesture record, `GESTURE_BASE + GESTURE_STRIDE * group`,
byte `GESTURE_PHASE`, goes to `PHASE_HELD` when the hold threshold passes (`0x21BDB0`,
`CONTROL-BEHAVIOR.md` section 2), on the same tick the enable binding fires.
"""
from __future__ import annotations

SELECTOR = 0x72              # G12 config cell holding both durations
SWEEP_RECORD = 85            # its 0x15FCE sweep template's rule index
G12_DUPLICATE = (43, 79)     # byte-identical 0xE8 sweep records: (retarget this, keep this)
G12_PATCH_SITE = 0x888       # G12 program offset of #43's boot `movs r0,#imm8`
CONFIG_VAR = 280             # the panel's receive var for selector 0x72

GESTURE_BASE = 0x20006E6C    # LCD per-group gesture records
GESTURE_STRIDE = 16
GESTURE_PHASE = 0x09
PHASE_HELD = 3
