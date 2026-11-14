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

# --- the step code, its clamp, and the start frame ------------------------------------------
#
# Each byte of cell 0x72 is a STEP CODE, `minutes / 5 + CODE_BASE` (0x11 .. 0x1C for 5 .. 60):
# heater in the low byte, Ignore Alarms in the high. The steppers write deltas (+-1 low,
# +-0x100 high) and the G12 stores the raw u16 with no clamp (PC.42, PC.70). The base keeps
# every legal byte >= 0x10, so a step never borrows across the byte boundary, and it keeps a
# wrap from the unset 0xFFFF (0x0000, 0xFFFE, 0x00FF, 0xFEFF) out of the legal range. The arrows
# repeat while held, so an overrun can be more than one step before the routine corrects it: a
# code below the grid (0x01..0x10) clamps to 5 and one above it (0x1D..0x7F) to 60. 0x00 and
# 0x80..0xFF -- where every wrap from 0xFFFF lands -- are that byte's default. Raw minutes could not do this: 0xFFFF + 5 leaves 0x00 in the high byte, which a
# minute count cannot tell from "Ignore Alarms stepped below 5".

MINUTES_MIN = 5
MINUTES_MAX = 60
MINUTES_STEP = 5
CODE_BASE = 0x10
DEFAULTS = {"heater": 60, "alarms": 10}
UNSET = 0xFFFF
UNDER_LOW = 0x01             # lowest code read as "stepped below the grid"
OVER_HIGH = 0x7F             # highest code read as "stepped above the grid"
TICKS_PER_MINUTE = 600       # the BMS duration field counts 100 ms


def _check(minutes: int) -> None:
    if (not isinstance(minutes, int) or isinstance(minutes, bool)
            or not MINUTES_MIN <= minutes <= MINUTES_MAX or minutes % MINUTES_STEP):
        raise ValueError(f"minutes {minutes!r} is not on the grid {MINUTES_MIN}..{MINUTES_MAX} "
                         f"step {MINUTES_STEP}")


def encode(minutes: int) -> int:
    """The step code for a grid minute count."""
    _check(minutes)
    return minutes // MINUTES_STEP + CODE_BASE


def decode_byte(code: int, default_min: int) -> int:
    """The minutes a stored byte means: on the grid as stored; below it (0x01..0x10) 5; above
    it (0x1D..0x7F) 60; 0x00 and 0x80..0xFF, `default_min`."""
    if encode(MINUTES_MIN) <= code <= encode(MINUTES_MAX):
        return (code - CODE_BASE) * MINUTES_STEP
    if UNDER_LOW <= code < encode(MINUTES_MIN):
        return MINUTES_MIN
    if encode(MINUTES_MAX) < code <= OVER_HIGH:
        return MINUTES_MAX
    return default_min


def clamp_code(code: int, default_min: int) -> int:
    """The canonical code for a stored byte."""
    return encode(decode_byte(code, default_min))


def pack(heater_min: int, alarms_min: int) -> int:
    return encode(alarms_min) << 8 | encode(heater_min)


def decode_word(word: int) -> tuple[int, int]:
    """`(heater minutes, alarms minutes)` for the cell's u16."""
    return (decode_byte(word & 0xFF, DEFAULTS["heater"]),
            decode_byte(word >> 8 & 0xFF, DEFAULTS["alarms"]))


def canonical_word(word: int) -> int | None:
    """The word the routine writes back, or None when there is nothing to write: the word is
    already canonical, or it is `UNSET` -- which also reads before the first sweep is heard, so
    writing then could overwrite a stored setting with the defaults."""
    if word == UNSET:
        return None
    want = pack(*decode_word(word))
    return None if want == word else want


def start_wire(prefix6: bytes, minutes: int) -> bytes:
    """The 8 wire bytes of a start frame: the command's fixed bytes 0-5, then the duration in
    100 ms ticks, big-endian (60 min -> 8CA0, as the heater's binding has always sent)."""
    _check(minutes)
    if len(prefix6) != 6:
        raise ValueError(f"prefix must be 6 bytes, got {len(prefix6)}")
    return bytes(prefix6) + (minutes * TICKS_PER_MINUTE).to_bytes(2, "big")
