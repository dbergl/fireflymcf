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


# --- the per-tick routine ---------------------------------------------------------------------

import struct
from dataclasses import dataclass

from . import followchannel, guidata, newbutton, routinekit, thumb

BUTTONS = ("heater", "alarms")       # low byte, high byte of the cell
EF70 = 0xEF70
START_FRAME_ID = bytes.fromhex("0070ef18")   # 0x18EF70xx LE: priority 6, DGN 0EF70 to SA 0x70;
                                             # the host sender ORs in the panel's own SA
LATCH_NONE = UNSET                   # the correction latch's "nothing corrected" value


class BattDurError(routinekit.RoutineKitError):
    pass


@dataclass(frozen=True)
class BattDurPlan:
    config_slot: int                 # &(selector 0x72's receive slot, var 280)
    groups: dict                     # {"heater": group, "alarms": group}
    prefixes: dict                   # {"heater": wire bytes 0-5, "alarms": ...}
    display: dict                    # {"heater": (cell_index, addr), "alarms": (...)}
    scratch: int                     # 8 bytes: h code, a code, h prev phase, a prev phase,
                                     # pad[2], correction latch (u16)
    selector: int = SELECTOR


def _enable_binding(data: bytes, group: int):
    groups, bindings = guidata.group_table(data), guidata.binding_table(data)
    found = [b for b in guidata.group_bindings(groups, bindings, group)
             if b.dgn_low == EF70 and b.raw[3] & 0x04]
    if len(found) != 1:
        raise BattDurError("NO_BUTTON", f"group {group} has {len(found)} 0xEF70 held-phase "
                           "bindings, not one: is it an add_aes_batt_heat button?")
    return found[0]


def resolve(data: bytes, *, groups: dict, config_slot: int) -> BattDurPlan:
    """The plan for `groups` (`{"heater": g, "alarms": g}`), each an `add_aes_batt_heat`
    group whose held-phase binding carries the command's fixed bytes 0-5. Reads only."""
    if set(groups) != set(BUTTONS):
        raise BattDurError("BAD_PARAMS", f"groups must name {BUTTONS}, got {sorted(groups)}")
    prefixes = {k: newbutton.binding_payload(_enable_binding(data, g).raw)[:6]
                for k, g in groups.items()}
    cells, taken = {}, ()
    for k in BUTTONS:
        cells[k] = followchannel.scan_display_cell(data, exclude=taken)
        taken += (cells[k][0],)
    avoid = {a + i for _c, a in cells.values() for i in range(2)}
    _d, scratch = routinekit.fresh_sram(data, 8, taken=avoid)
    return BattDurPlan(config_slot, dict(groups), prefixes, cells, scratch)


def _clamp(r, src: int, default_min: int, tag: str) -> None:
    """r0 := canonical code of the byte in r0 (`decode_byte` then `encode`), using r2."""
    lo, hi = encode(MINUTES_MIN), encode(MINUTES_MAX)
    e = r.emit
    e(2, lambda h, s: (thumb.cmp_imm8(0, UNDER_LOW), f"cmp r0, {thumb.imm_text(UNDER_LOW)}"))
    r.bcc("lo", f"{tag}_default")                    # 0x00
    e(2, lambda h, s: (thumb.cmp_imm8(0, lo), f"cmp r0, {thumb.imm_text(lo)}"))
    r.bcc("lo", f"{tag}_floor")                      # 0x01..0x10
    e(2, lambda h, s: (thumb.cmp_imm8(0, hi), f"cmp r0, {thumb.imm_text(hi)}"))
    r.bcc("ls", f"{tag}_done")                       # on the grid
    e(2, lambda h, s: (thumb.cmp_imm8(0, OVER_HIGH), f"cmp r0, {thumb.imm_text(OVER_HIGH)}"))
    r.bcc("hi", f"{tag}_default")                    # 0x80..0xFF
    e(2, lambda h, s: (thumb.movs_imm(0, hi), f"movs r0, {thumb.imm_text(hi)}"))
    r.b(f"{tag}_done")
    r.label(f"{tag}_floor")
    e(2, lambda h, s: (thumb.movs_imm(0, lo), f"movs r0, {thumb.imm_text(lo)}"))
    r.b(f"{tag}_done")
    r.label(f"{tag}_default")
    d = encode(default_min)
    e(2, lambda h, s: (thumb.movs_imm(0, d), f"movs r0, {thumb.imm_text(d)}"))
    r.label(f"{tag}_done")


def _emit_start_send(r, template: str, sender: str) -> None:
    """Copy the 12-byte start template to the stack, put r3 (100 ms ticks) big-endian at
    payload bytes 6-7 (frame offsets 10, 11), and send: `routinekit.emit_send_frame`'s
    sequence with the two byte stores in place of its halfword store."""
    e = r.emit
    e(2, lambda h, s: (thumb.sub_sp_imm(20), "sub sp, #0x14"))
    r.ldr_lit(0, template)
    e(2, lambda h, s: (thumb.ldr_imm(1, 0, 0), "ldr r1, [r0]"))
    e(2, lambda h, s: (thumb.ldr_imm(2, 0, 4), "ldr r2, [r0, #4]"))
    e(2, lambda h, s: (thumb.str_sp_imm(1, 0), "str r1, [sp]"))
    e(2, lambda h, s: (thumb.str_sp_imm(2, 4), "str r2, [sp, #4]"))
    e(2, lambda h, s: (thumb.ldr_imm(1, 0, 8), "ldr r1, [r0, #8]"))
    e(2, lambda h, s: (thumb.str_sp_imm(1, 8), "str r1, [sp, #8]"))
    e(2, lambda h, s: (thumb.mov_reg(0, 13), "mov r0, sp"))
    e(2, lambda h, s: (thumb.strb_imm(3, 0, 11), "strb r3, [r0, #0xb]"))
    e(2, lambda h, s: (thumb.lsrs_imm(3, 3, 8), "lsrs r3, r3, #8"))
    e(2, lambda h, s: (thumb.strb_imm(3, 0, 10), "strb r3, [r0, #0xa]"))
    r.ldr_lit(2, sender)
    e(2, lambda h, s: (thumb.ldr_imm(2, 2, 0), "ldr r2, [r2]"))
    e(2, lambda h, s: (thumb.blx_reg(2), "blx r2"))
    e(2, lambda h, s: (thumb.add_sp_imm(20), "add sp, #0x14"))


def templates(plan: BattDurPlan) -> bytes:
    """The prefix: heater start, alarms start, the D1 write, the D3 read-back (12 bytes each)."""
    out = b""
    for k in BUTTONS:
        out += START_FRAME_ID + plan.prefixes[k] + b"\x00\x00"
    out += routinekit.config_frame(plan.selector, 0, routinekit.FUNCTION_WRITE)
    out += routinekit.config_frame(plan.selector, UNSET, routinekit.FUNCTION_READBACK)
    return out


def build(plan: BattDurPlan, code_load_addr: int, tail_target: int,
          sender_addr: int) -> tuple[bytes, int]:
    """`(blob, entry_offset)`. Per tick:

    1. Clamp each byte of the cell to its canonical code (`clamp_code`), keep the codes in
       scratch, and write each button's minutes to its display cell.
    2. If the word is not `UNSET`, not canonical, and not the word last corrected, write the
       canonical word (D1) and request a read-back (D3); remember the word. A canonical word
       clears that latch.
    3. For each button: on the tick its gesture phase becomes `PHASE_HELD`, send its start
       frame with `minutes * 600` in payload bytes 6-7, big-endian.
    """
    prefix = templates(plan)
    tpl = {"heater": code_load_addr, "alarms": code_load_addr + 12,
           "write": code_load_addr + 24, "readback": code_load_addr + 36}
    pool = {"slot": plan.config_slot, "scratch": plan.scratch,
            "cell_heater": plan.display["heater"][1], "cell_alarms": plan.display["alarms"][1],
            "phase_heater": GESTURE_BASE + GESTURE_STRIDE * plan.groups["heater"] + GESTURE_PHASE,
            "phase_alarms": GESTURE_BASE + GESTURE_STRIDE * plan.groups["alarms"] + GESTURE_PHASE,
            "t_heater": tpl["heater"], "t_alarms": tpl["alarms"],
            "_sender": sender_addr}
    r = thumb.Routine(pool, error=lambda m: BattDurError("BAD_ROUTINE", m))
    e = r.emit
    routinekit.emit_prologue(r)
    # 1. clamp both bytes
    for i, k in enumerate(BUTTONS):
        r.ldr_lit(1, "slot")
        e(2, lambda h, s: (thumb.ldrh_imm(1, 1, 0), "ldrh r1, [r1]"))
        if i == 0:
            e(2, lambda h, s: (thumb.lsls_imm(0, 1, 24), "lsls r0, r1, #0x18"))
            e(2, lambda h, s: (thumb.lsrs_imm(0, 0, 24), "lsrs r0, r0, #0x18"))
        else:
            e(2, lambda h, s: (thumb.lsrs_imm(0, 1, 8), "lsrs r0, r1, #8"))
        _clamp(r, 0, DEFAULTS[k], k)
        r.ldr_lit(2, "scratch")
        e(2, lambda h, s, i=i: (thumb.strb_imm(0, 2, i), f"strb r0, [r2{', #%d' % i if i else ''}]"))
        e(2, lambda h, s: (thumb.subs_imm8(0, CODE_BASE), f"subs r0, {thumb.imm_text(CODE_BASE)}"))
        e(2, lambda h, s: (thumb.movs_imm(2, MINUTES_STEP), f"movs r2, #{MINUTES_STEP}"))
        e(2, lambda h, s: (thumb.muls(0, 2), "muls r0, r2, r0"))
        r.ldr_lit(2, f"cell_{k}")
        e(2, lambda h, s: (thumb.strb_imm(0, 2, 0), "strb r0, [r2]"))
    # 2. correct a non-canonical stored word
    r.ldr_lit(2, "scratch")
    e(2, lambda h, s: (thumb.ldrb_imm(3, 2, 1), "ldrb r3, [r2, #1]"))
    e(2, lambda h, s: (thumb.lsls_imm(3, 3, 8), "lsls r3, r3, #8"))
    e(2, lambda h, s: (thumb.ldrb_imm(0, 2, 0), "ldrb r0, [r2]"))
    e(2, lambda h, s: (thumb.adds_reg(3, 3, 0), "adds r3, r3, r0"))      # r3 = canonical word
    r.ldr_lit(1, "slot")
    e(2, lambda h, s: (thumb.ldrh_imm(1, 1, 0), "ldrh r1, [r1]"))         # r1 = stored word
    e(2, lambda h, s: (thumb.cmp_reg(1, 3), "cmp r1, r3"))
    r.bcc("eq", "canonical")
    e(4, lambda h, s: (thumb.movw(0, UNSET), f"movw r0, #{UNSET:#x}"))
    e(2, lambda h, s: (thumb.cmp_reg(1, 0), "cmp r1, r0"))
    r.bcc("eq", "holds")
    e(2, lambda h, s: (thumb.ldrh_imm(0, 2, 6), "ldrh r0, [r2, #6]"))
    e(2, lambda h, s: (thumb.cmp_reg(1, 0), "cmp r1, r0"))
    r.bcc("eq", "holds")
    e(2, lambda h, s: (thumb.strh_imm(1, 2, 6), "strh r1, [r2, #6]"))
    routinekit.emit_send_frame(r, tpl["write"], value_in_r3=True, sender_addr=sender_addr)
    routinekit.emit_send_frame(r, tpl["readback"], sender_addr=sender_addr)
    r.b("holds")
    r.label("canonical")
    e(4, lambda h, s: (thumb.movw(0, LATCH_NONE), f"movw r0, #{LATCH_NONE:#x}"))
    e(2, lambda h, s: (thumb.strh_imm(0, 2, 6), "strh r0, [r2, #6]"))
    # 3. the hold edges
    r.label("holds")
    for i, k in enumerate(BUTTONS):
        r.ldr_lit(0, f"phase_{k}")
        e(2, lambda h, s: (thumb.ldrb_imm(1, 0, 0), "ldrb r1, [r0]"))
        r.ldr_lit(0, "scratch")
        e(2, lambda h, s, i=i: (thumb.ldrb_imm(2, 0, 2 + i), f"ldrb r2, [r0, #{2 + i}]"))
        e(2, lambda h, s, i=i: (thumb.strb_imm(1, 0, 2 + i), f"strb r1, [r0, #{2 + i}]"))
        e(2, lambda h, s: (thumb.cmp_imm8(1, PHASE_HELD), f"cmp r1, #{PHASE_HELD}"))
        r.bcc("ne", f"next_{k}")
        e(2, lambda h, s: (thumb.cmp_imm8(2, PHASE_HELD), f"cmp r2, #{PHASE_HELD}"))
        r.bcc("eq", f"next_{k}")
        e(2, lambda h, s, i=i: (thumb.ldrb_imm(3, 0, i), f"ldrb r3, [r0{', #%d' % i if i else ''}]"))
        e(2, lambda h, s: (thumb.subs_imm8(3, CODE_BASE), f"subs r3, {thumb.imm_text(CODE_BASE)}"))
        ticks = MINUTES_STEP * TICKS_PER_MINUTE
        e(4, lambda h, s: (thumb.movw(2, ticks), f"movw r2, {thumb.imm_text(ticks)}"))
        e(2, lambda h, s: (thumb.muls(3, 2), "muls r3, r2, r3"))
        _emit_start_send(r, f"t_{k}", "_sender")
        r.label(f"next_{k}")
    routinekit.emit_tail(r, tail_target)
    return routinekit.assemble_tick(r, code_load_addr, prefix=prefix)


# --- the G12 sweep: activate a selector's sweep record in a duplicate's place -----------------

def _activation_sites(data: bytes) -> list:
    """`(blob offset of the movs, rule)` for every `movs r0,#N; ldr r3,[r4,#8]; blx r3`."""
    from . import g12code, tankbroadcast
    body = g12code.blob(data)
    an = g12code.analyse(data)
    ins = tankbroadcast._decode(body, an.starts)
    out = []
    for addr, slot, rule in tankbroadcast._engine_calls(body, an.starts):
        if slot != 8 or rule is None:
            continue
        movs = addr - 4
        if ins.get(movs, ("",))[0] == "movs" and ins[movs][1] == f"r0, #{thumb._imm(rule)}" \
                and ins.get(addr - 2, ("", ""))[1] == "r3, [r4, #8]":
            out.append((movs, rule))
    return out


def activate_sweep(data: bytes, selector: int = SELECTOR) -> tuple[bytes, list]:
    """Retarget one boot activation of a duplicated sweep record to `selector`'s never-activated
    sweep record: one byte, the `movs r0,#imm8` immediate. Returns `(image, edits)`; not resealed.

    Refuses unless `selector` has exactly one sweep record and nothing activates it, and some
    sweep record is activated exactly once, at a boot `movs`, while a byte-identical record is
    also activated (so its selector is still swept)."""
    from . import g12rules, tankbroadcast
    recs = {r.index: r for r in g12rules.sweep_templates(data)}
    mine = [i for i, r in recs.items() if r.payload[0] == selector]
    if len(mine) != 1:
        raise BattDurError("NO_SWEEP_RECORD",
                           f"selector {selector:#04x} has {len(mine)} sweep records, not one")
    target = mine[0]
    if target > 0xFF:
        raise BattDurError("NO_SWEEP_RECORD", f"record #{target} does not fit a movs imm8")
    sites = _activation_sites(data)
    rules = [rule for _a, rule in sites]
    if target in rules:
        raise BattDurError("ALREADY_SWEPT", f"record #{target} is already activated")
    for movs, rule in reversed(sites):        # the later twin: the first keeps its place
        if rule not in recs or rules.count(rule) != 1:
            continue
        twins = [o for o in recs if o != rule and recs[o].raw == recs[rule].raw and o in rules]
        if not twins:
            continue
        off = tankbroadcast.blob_file_offset(data) + movs
        old = bytes(data[off:off + 2])
        new = thumb.movs_imm(0, target)
        thumb.assert_disassembles_to(old, movs, [f"movs r0, #{thumb._imm(rule)}"])
        out = bytearray(data)
        out[off:off + 2] = new
        return bytes(out), [(off, old, new,
                             f"G12 boot list: activate sweep record #{target} (selector "
                             f"{selector:#04x}) in place of #{rule}, a byte-identical twin of "
                             f"#{twins[0]} (selector {recs[rule].payload[0]:#04x}, still swept)")]
    raise BattDurError("NO_DUPLICATE", "no boot activation of a duplicated sweep record to "
                       "retarget")


def disable_enable_binding(data: bytes, group: int) -> tuple[bytes, list]:
    """Clear the held-phase bit (`0x04`) of `group`'s start binding, so the routine's frame is
    the only start a hold sends; the tap's stop binding (`0x02`) is untouched. One byte, in
    place: the table keeps its size and every index. Returns `(image, edits)`; not resealed."""
    b = _enable_binding(data, group)
    gs, _ = guidata.guidata_extent(data)
    off = gs + b.offset + 3
    old = data[off]
    if old & ~0x0F or old != b.raw[3]:
        raise BattDurError("NO_BUTTON", f"binding {b.index}'s phase mask {old:#04x} is not a "
                           "plain phase mask")
    out = bytearray(data)
    out[off] = old & ~0x04
    return bytes(out), [(off, bytes([old]), bytes([out[off]]),
                         f"binding {b.index} (group {group}): drop the held phase; the battdur "
                         "routine sends the start frame")]
