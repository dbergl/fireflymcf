"""The LEVEL page controls: what the Blocks-carried stepper (- / +) and Zero Level send, the
bindings that send it, their group-table entries, and a decoder of one whole press.

This module holds
data and a model; it writes nothing into an image (the page op does, via
`optionrow._grow_bindings` and `newbutton.free_group_slots`).

**The frames** (`FRAMES`) are the spec's table verbatim. Every binding is composed with
`newbutton.binding_raw`, and `newbutton.binding_wire_frame` reconstructs the wire frame the way
the sender does (`FUN_0021B8B4`: the 18-bit DGN inserted at id bit 8, priority 6 hard-wired,
`bfi r2,r1,#8,#0x12` / `movs r3,#6; bfi r2,r3,#2,#3` @0x21B9B2-0x21B9C0; SA 0x9F).

**The - / + gesture: prefix `10 00 00 00`, mask `0x06` (tap-release + held), repeat bit clear.**

Choice (1) of the plan -- the stock stepper arrows' own shape with repeat removed. Stock groups
103/104 (the `0x0E` -/+300 arrows `aescompact.PUMP_STEPPER_*` reuses, coach-proven on the Water
Pump Auto Off stepper) are `10 00 04 80`: hold 1 s, held-phase repeat index 1 (250 ms), byte 3
bit 7 set. Their delta binding (385 / 388) carries mask `0x06` with the repeat bit set; a
release zero-delta (`0x0A`) and a `D3` read-back (`0x0E`) follow. Keeping the delta's mask and
clearing every repeat gives one frame for a press of any length, because the phase machine takes
exactly one of two paths (`docs/CONTROL-BEHAVIOR.md` section 2, "The phases", lines 168-187):

* released before the hold deadline: phase 1 -> 2 (`0x21BC58`), and bit `0x02` fires once
  (arm `0x21B9F6` `tst r2,#2`, section 3 lines 230-237);
* held past it: phase 1 -> 3 (`0x21BDB0`), bit `0x04` fires once (arm `0x21B974`), and the
  release goes 3 -> 4 (`0x21BCB6`), NOT to 2 -- so bit `0x02` cannot fire a second time and
  bit `0x08` is not set.

Once, not a stream: the held rate index is 0 (group byte 2 bits 2-3, `REPEAT_MS[0]` = "send
once and stop", section 2 "Repeat" lines 209-224; `0x21BDEE` zeroes the next-send slot), and the
binding's byte 2 bit 2 is clear, so the sender skips it after the phase's first send
(`0x21B94C`-`0x21B952`). Either alone suffices; both are cleared, the belt-and-braces
`gestures.make_hold_to_toggle` uses on hardware.

**Byte 3 bit 7 is cleared too, which departs from the arrows' own prefix.** With it set the
sender runs a second loop over the global binding list (`ldrsb [r0,#3]; bge` @0x21B90C-0x21B912,
loop 0x21BA06-0x21BA3A), which the spec forbids for Zero because it can emit extra frames; the
same argument applies here, and the spec's acceptance check is "exactly the frames in section 3
and nothing else from SA 9F". The result, `10 00 00 00`, is a prefix eight stock 13.6 groups
already carry (229 among them) -- and it is group 17's `10 00 0c 00` with its held rate cleared.
*Inference, labelled:* on the arrows the bit is evidently harmless (the pump stepper is
coach-proven with it set), so clearing it removes a loop, it does not remove anything the
stepper needs.

Why not mask `0x02` alone (Batt Heat's off): past the 1-s hold the release is phase 4, so a slow
press sends NOTHING. Why not `0x01` on touch-down (choice 2): it fires once too, but the plan
prefers the proven arrow shape.

**Zero Level: prefix `30 00 00 00`, mask `0x04`, repeat clear, both frames in table order.**
Group 17 (`10 00 0c 00`) with the hold nibble set to 3 and the held rate cleared -- exactly the
hardware-verified `gestures.make_hold_to_toggle` edit, and byte-identical to v34b's group 229.
Built from group 17 rather than read from group 229 so it does not depend on the base image
(stock 229 is `10 00 00 00`). Hold seconds = byte 0 high nibble (`mla r0,fp,r3,r0`, fp = 1000,
@0x21BD86; section 2 "The hold threshold" lines 197-207). Both bindings match phase 3 in one
send call, table order (`bl 0x21825C` @0x21B9DE, `adds r4,#0xc`, `b 0x21B970`). Never cloned
from groups 64 / 85 (`30 00 00 80`, `30 00 0c 80`): byte 3 bit 7.
"""
from __future__ import annotations

from . import gestures, guidata, newbutton, optionrow


class LevelControlsError(ValueError):
    """A press the model does not settle, or an unknown control."""


# DGN 0x0EFFF: J1939 proprietary-A, destination FF -- what v35/v36 send, and the default, so
# their projects rebuild byte-identically. RV-C puts custom messages on 1EF00h with DGN-low the
# destination: `STEPPER_DGN_RVC` = 0x1EFBF addresses the sensor at BFh, its default and first
# claimed address. A binding's frame is fixed, so the sensor accepts BFh-addressed commands
# whatever it claimed (the 2026-09-28 sensor request, section 3). DGNs 0x1FDE8 / 0x1FDEA: the
# sensor's zero pair.
_DGN_PROP_A = 0x0EFFF
STEPPER_DGN_DEFAULT = _DGN_PROP_A
STEPPER_DGN_RVC = 0x1EFBF
_DGN_ZERO_A = 0x1FDE8
_DGN_ZERO_B = 0x1FDEA
_ZERO_PAYLOAD = bytes.fromhex("00FFFFFFFFFDFFFF")   # instance 0, byte 5 bits 0-1 = 01 set zero

FRAMES = {
    "minus": [(0x18EFFF9F, bytes.fromhex("4C5604FFFFFFFFFF"))],
    "plus": [(0x18EFFF9F, bytes.fromhex("4C5603FFFFFFFFFF"))],
    "zero": [(0x19FDE89F, _ZERO_PAYLOAD), (0x19FDEA9F, _ZERO_PAYLOAD)],
}

STEPPER_MASK = gestures.PHASE_TAP | gestures.PHASE_HELD      # 0x06
ZERO_MASK = gestures.PHASE_HELD                              # 0x04
ZERO_HOLD_SECONDS = 3

STEPPER_DONOR_GROUP = 103          # the stock -300 arrow (104 is the +300; same prefix)
ZERO_DONOR_GROUP = 17              # the stock light group make_hold_to_toggle edits
FORBIDDEN_DONORS = (64, 85)        # byte 3 bit 7: the second loop over the global list

# Groups the LEVEL controls must never claim, though `newbutton.free_group_slots` lists them.
# 68 and 69 hold the stock `1FFD3` / `1FFC5` inverter/charger enable/disable templates (two-variant
# toggles: variant 1 enable, variant 0 disable, tap-release); `docs/HARDWARE-VERIFIED.md`'s
# PD-inverter bridge brief ("Commands need no edit at all", the paragraph ending "Groups 68/69 are
# two-variant toggles") names them as the route that bridge will drive. Of v34b's 43 free slots
# only group 61 is an empty stub, so every other claim overwrites an unreferenced stock template;
# these two are the ones with a documented purpose (controller ruling, 2026-09-26).
RESERVED_GROUPS = frozenset({68, 69})    # `recipe.RESERVED_GROUPS` gates them at compile

_BINDINGS = {
    "minus": [(_DGN_PROP_A, FRAMES["minus"][0][1], STEPPER_MASK)],
    "plus": [(_DGN_PROP_A, FRAMES["plus"][0][1], STEPPER_MASK)],
    "zero": [(_DGN_ZERO_A, _ZERO_PAYLOAD, ZERO_MASK), (_DGN_ZERO_B, _ZERO_PAYLOAD, ZERO_MASK)],
}

# The LEVEL SETUP popup's five step controls (sensor request 2026-09-29; opcodes agreed with
# level-sensor): each is one fixed frame on the stepper's DGN with the "LV" signature, sent once
# per press exactly like - / +. The sensor answers each with record 04h.
SETUP_OPCODES = {"h_down": 0x07, "h_up": 0x06, "t_down": 0x09, "t_up": 0x08, "t_auto": 0x0A}
SETUP_CONTROLS = tuple(SETUP_OPCODES)
for _name, _op in SETUP_OPCODES.items():
    _BINDINGS[_name] = [(_DGN_PROP_A, bytes([0x4C, 0x56, _op]) + b"\xff" * 5, STEPPER_MASK)]

# The popup's wheelbase and track steppers (v45; level-sensor ops 0Bh-0Eh, +/-0.5 in). Unlike the
# rest they REPEAT while held, because 0.5 in steps across an 80-300 in range are too many taps:
# the stock arrows' own shape (group 103 `10 00 04 80`: 1-s hold, held-phase repeat index 1 =
# 250 ms; the delta binding's repeat bit set), coach-proven on the Water Pump Auto Off stepper,
# with byte 3 bit 7 cleared as for every LEVEL control. A tap sends one frame (phase 2); a press
# past 1 s sends one on entering phase 3 and one every 250 ms after; the release (phase 4) sends
# nothing, since bit 0x08 is clear.
GEOMETRY_OPCODES = {"w_down": 0x0C, "w_up": 0x0B, "k_down": 0x0E, "k_up": 0x0D}
GEOMETRY_CONTROLS = tuple(GEOMETRY_OPCODES)
GEOMETRY_HELD_RATE = 0x04          # group byte 2 bits 2-3 = 1: REPEAT_MS[1], 250 ms
for _name, _op in GEOMETRY_OPCODES.items():
    _BINDINGS[_name] = [(_DGN_PROP_A, bytes([0x4C, 0x56, _op]) + b"\xff" * 5, STEPPER_MASK)]

_SECOND_LOOP = 0x80                # group byte 3 bit 7
_SA_PANEL = 0x9F
_PRIORITY = 6                      # hard-wired by the sender (`movs r3,#6` @0x21B9BE)


def _check(control: str) -> None:
    if control not in _BINDINGS:
        raise LevelControlsError(f"unknown control {control!r}; expected one of "
                                 f"{sorted(_BINDINGS)}")


def _check_stepper_dgn(stepper_dgn: int) -> None:
    if not isinstance(stepper_dgn, int) or not 0 <= stepper_dgn <= 0x3FFFF:
        raise LevelControlsError(f"stepper DGN {stepper_dgn!r} is not an 18-bit DGN")


def _entries(control: str, stepper_dgn: int) -> list:
    _check(control)
    _check_stepper_dgn(stepper_dgn)
    return [((stepper_dgn if dgn == _DGN_PROP_A else dgn), payload, mask)
            for dgn, payload, mask in _BINDINGS[control]]


def frames(control: str, stepper_dgn: int = STEPPER_DGN_DEFAULT) -> list:
    """The `(can_id, data)` frames `control` sends, with the - / + on `stepper_dgn`.
    `frames(c)` is `FRAMES[c]`."""
    return [((_PRIORITY << 26) | (dgn << 8) | _SA_PANEL, payload)
            for dgn, payload, _mask in _entries(control, stepper_dgn)]


def bindings(control: str, stepper_dgn: int = STEPPER_DGN_DEFAULT) -> list[bytes]:
    """The 12-byte binding entries for `control`, in send order; repeat bit clear on all but the
    geometry steppers. `stepper_dgn` is the DGN the - / + send on (Zero's pair is fixed)."""
    repeat = control in GEOMETRY_OPCODES
    return [newbutton.binding_raw(dgn, payload, gesture_mask=mask, repeat=repeat)
            for dgn, payload, mask in _entries(control, stepper_dgn)]


def group_entry(data: bytes, control: str, binding_start: int) -> bytes:
    """The 8-byte group-table entry for `control` whose slice starts at `binding_start`.

    The prefix is derived from the named stock donor in `data` and then edited, never typed
    in (a group prefix is not a generic constant: `optionrow._clone_group_entry`). The
    stepper: group 103 with byte 2 (all repeat rates) and byte 3 bit 7 cleared; the geometry
    steppers keep its 250 ms held rate (`GEOMETRY_HELD_RATE`). Zero: group 17
    with hold nibble 3 and byte 2 cleared. The donor's bytes are asserted so a different base
    image cannot silently produce a different gesture.
    """
    _check(control)
    gt = guidata.group_table(data)
    donor = ZERO_DONOR_GROUP if control == "zero" else STEPPER_DONOR_GROUP
    assert donor not in FORBIDDEN_DONORS
    raw = bytearray(gt[donor].raw)
    if control == "zero":
        if bytes(raw[:4]) != bytes.fromhex("10000c00"):
            raise LevelControlsError(f"group {donor} prefix {bytes(raw[:4]).hex(' ')} is not "
                                     f"the stock 10 00 0c 00")
        raw[0] = (ZERO_HOLD_SECONDS << 4) | (raw[0] & 0x0F)
    else:
        if bytes(raw[:4]) != bytes.fromhex("10000480"):
            raise LevelControlsError(f"group {donor} prefix {bytes(raw[:4]).hex(' ')} is not "
                                     f"the stock arrow 10 00 04 80")
    raw[2] = GEOMETRY_HELD_RATE if control in GEOMETRY_OPCODES else 0x00
    raw[3] &= ~_SECOND_LOOP & 0xFF
    entry = optionrow._clone_group_entry(bytes(raw), binding_start, len(_BINDINGS[control]))
    assert not entry[3] & _SECOND_LOOP
    return entry


# held_3s (3000 ms) sits exactly on Zero's 3-s hold deadline, so a press of that length only
# tests held vs. tap through the `>=` inference (see the docstring below); held_3_5s (3500 ms) is
# unambiguously past every donor's hold deadline used here and is what the Zero "held" assertions
# use instead.
PRESS_MS = {"tap": 300, "held_2s": 2000, "held_3s": 3000, "held_3_5s": 3500}


def presses(group_entry: bytes, binding_list: list[bytes], *,
            allow_second_loop: bool = False) -> dict:
    """The frames ONE whole press sends, touch-down to release, for a tap (300 ms), a 2-s and
    a 3-s press: `{"tap": [...], "held_2s": [...], "held_3s": [...]}` of `(can_id, data)`.

    The tests' model of the LCD Firmware's phase machine (`FUN_0021BAB4`) and sender
    (`FUN_0021B8B4`). Each rule, with its citation in `docs/CONTROL-BEHAVIOR.md`:

    * touch-down enters phase 1 (`0x21BD72`; section 2 "The phases", lines 168-185) and sends
      every entry with mask bit `0x01` (arm `0x21B968`; section 3, lines 230-237);
    * the hold deadline is now + 1000 * (group byte 0 >> 4) ms (`0x21BD7C`-`0x21BD86`; lines
      197-207); reaching it enters phase 3 (`0x21BDB0`), sending bit `0x04` (arm `0x21B974`);
    * a release from 1 is phase 2 (`0x21BC58`), sending bit `0x02` (arm `0x21B9F6`); a release
      from 3 is phase 4 (`0x21BCB6`), sending bit `0x08` (arm `0x21B9FE`) -- never bit `0x02`;
    * within a phase, a repeat is scheduled every `REPEAT_MS[idx]` ms, idx from group byte 2
      bits 0-1 (phase 1), 2-3 (phase 3), 4-5 (release phases); idx 0 = send once and stop
      (`0x21F2A2`, selected `0x21BBAC`-`0x21BBD2`; lines 209-224); a repeat re-sends only
      entries whose byte 2 bit 2 is set (`0x21B94C`-`0x21B952`);
    * matching entries go out in table order within one call (`adds r4,#0xc` / `b 0x21B970`);
      only variant-0 entries (byte 2 high nibble, `cmp sl,r2,lsr #4` @`0x21B946`; section 5),
      the per-control counter's state at the first press.

    *Inference, labelled:* the deadline boundary -- a press of exactly the hold time counts as
    held here (`>=`); the doc does not cite the comparison. The 3-s key relies on it only for
    Zero; the stepper is one frame either side of any boundary. A repeat tick lands strictly
    before release.

    NOT settled by the doc, so refused rather than guessed: a repeat on a release phase (what
    ends phase 2/4's repeating is not documented), a zero-second hold (whether phase 1's send
    precedes an already-expired deadline), and group byte 3 bit 7's second loop over the
    global binding list (`0x21BA06`-`0x21BA3A`), which this models only by leaving it out --
    `allow_second_loop=True` accepts that omission for stock groups.
    """
    if group_entry[3] & _SECOND_LOOP and not allow_second_loop:
        raise LevelControlsError("group byte 3 bit 7 set: the sender's second loop over the "
                                 "global binding list is not modelled")
    hold_ms = (group_entry[0] >> 4) * gestures.HOLD_MS_PER_UNIT
    if hold_ms == 0:
        raise LevelControlsError("a zero-second hold is not settled by CONTROL-BEHAVIOR.md")
    rates = [gestures.REPEAT_MS[(group_entry[2] >> s) & 3] for s in (0, 2, 4)]
    live = [raw for raw in binding_list if raw[2] >> 4 == 0]

    def send(mask: int, first: bool) -> list:
        return [newbutton.binding_wire_frame(raw) for raw in live
                if raw[3] & mask and (first or raw[2] & gestures.BINDING_REPEATS)]

    def phase(mask: int, start: int, end: int, rate: int) -> list:
        out = send(mask, True)
        if rate:
            t = start + rate
            while t < end:
                out += send(mask, False)
                t += rate
        return out

    result = {}
    for key, length in PRESS_MS.items():
        frames = phase(gestures.PHASE_PRESS, 0, min(length, hold_ms), rates[0])
        if length >= hold_ms:
            frames += phase(gestures.PHASE_HELD, hold_ms, length, rates[1])
            release = gestures.PHASE_RELEASE_HELD
        else:
            release = gestures.PHASE_TAP
        if rates[2] and any(raw[3] & release and raw[2] & gestures.BINDING_REPEATS
                            for raw in live):
            raise LevelControlsError("a repeating release phase is not settled by "
                                     "CONTROL-BEHAVIOR.md")
        frames += send(release, True)
        result[key] = frames
    return result
