"""The selector-0xEE packed register, and the Auto Off routine that services it.

`0xEE` carries BOTH the Pump Link flag and the Auto Off minute count in one u16:

    bits 15..8   link flag   (0 = off, 1 = on)
    bits  7..0   minutes     (0, then 5..60 in steps of 5; **0 disables Auto Off**)

**The layout is hardware-forced, not a preference.** `BUS-BASELINE.md` PC.47: the G12
stores this selector as a plain 16-bit integer with no clamp and no field protection, so
a delta that underflows one byte eats the other (`0x0500 - 1 = 0x04FF`, observed). With
minutes LOW, a flag borrow (`-0x0100` while already 0) lands as 0xFF in the FLAG byte
while the user's minutes survive. The reverse layout corrupts both fields at once and
silently moves the minutes off-grid. Do not swap them.

**The minute steps DO cross the byte boundary at the floor, and that is new in v20.** This
paragraph used to end "the ±5 minute steps can never cross 0 or 255, so they cannot disturb
the flag", which was true only while the floor was 5: `-` from 5 landed exactly on 0. Making
0 the disabled setting puts a `-` press at the floor, and `0x0000 - 5` borrows straight into
the flag byte. `clamp` undoes that borrow exactly rather than the layout preventing it; the
argument for this field order is now the *reverse* case (a flag borrow leaves the minutes
alone), which still holds.

**0 disables Auto Off, and it takes ONE branch rather than none.** `armit` stores
`minutes * 60`, so a count of 0 arms to `DISARMED` and a timer that is not running never
starts -- that part is arithmetic. But `arm` re-reads the minute count only at `armit`, which
is reached only when the countdown is already 0, so a countdown ALREADY RUNNING when the user
stepped down to 0 kept counting and would have fired. Observed on the coach (v20 round 2), and
closed by a minutes-is-zero test at the top of `arm` that disarms whatever the pump is doing.

Because the G12 clamps nothing here, `clamp` is the whole guard, and the per-tick routine
applies it BEFORE anything reads the value (see `build`).
"""

from __future__ import annotations

MINUTES_MIN = 5                # the SHIPPED floor (v14-v19). `minutes_min=0` -- what the
                               # `allow_disable` op param passes -- makes 0 a legal setting that
                               # disables Auto Off; see `clamp`'s borrow rule for its one cost.
MINUTES_MAX = 60
MINUTES_STEP = 5
MINUTES_DEFAULT = 30

# The minute byte a `-` press produces from the floor once the floor is 0: `0 - STEP` borrows
# out of the low byte and leaves `0x100 - STEP`. No value on the grid (0, 5 .. 60) can put this
# byte there any other way, which is what makes it a safe signature to act on -- see `clamp`.
BORROW_MINUTES = 0x100 - MINUTES_STEP

FLAG_SHIFT = 8
FLAG_MASK = 0xFF00
MINUTES_MASK = 0x00FF

# The value the receive slot reads before the G12 has ever stored anything (PC.42: the
# unseeded config cell sweeps as 0xFFFF, and the write path is a raw u16 store).
UNSET = 0xFFFF


class PumpAutoError(Exception):
    """A minute count or packed word that this module refuses to construct."""


def _check_minutes(minutes: int, minutes_min: int = MINUTES_MIN) -> None:
    if not isinstance(minutes, int) or isinstance(minutes, bool):
        raise PumpAutoError(f"minutes {minutes!r} is not an int")
    if not minutes_min <= minutes <= MINUTES_MAX:
        raise PumpAutoError(
            f"minutes {minutes} outside [{minutes_min}, {MINUTES_MAX}]")
    if minutes % MINUTES_STEP:
        raise PumpAutoError(
            f"minutes {minutes} is not a multiple of {MINUTES_STEP}")


def pack(minutes: int, link: bool, minutes_min: int = MINUTES_MIN) -> int:
    """The u16 to store in selector 0xEE. Refuses an out-of-range or off-grid count."""
    _check_minutes(minutes, minutes_min)
    return ((1 if link else 0) << FLAG_SHIFT) | minutes


def unpack(word: int) -> tuple[int, bool]:
    """`(minutes, link)` as the bytes literally read — no correction. Use `clamp` to
    decide whether the word needs fixing first."""
    if not 0 <= word <= 0xFFFF:
        raise PumpAutoError(f"word {word!r} is not a u16")
    return word & MINUTES_MASK, bool((word & FLAG_MASK) >> FLAG_SHIFT)


def _round_to_step(minutes: int, minutes_min: int = MINUTES_MIN) -> int:
    """Nearest multiple of `MINUTES_STEP`, ties up, then pinned to the range."""
    stepped = ((minutes + MINUTES_STEP // 2) // MINUTES_STEP) * MINUTES_STEP
    return max(minutes_min, min(MINUTES_MAX, stepped))


def clamp(word: int, minutes_min: int = MINUTES_MIN) -> int | None:
    """The corrected word, or `None` when `word` is already valid.

    `minutes_min` is the floor the caller's build ships. At the shipped 5 this is the v14-v19
    function unchanged, byte for byte in the emitted code too. At 0 it additionally undoes the
    byte borrow a `-` press at the floor now produces -- see the block below.

    Returning `None` for "no change needed" is what lets the routine skip the write on
    the overwhelmingly common tick — an unconditional rewrite every tick would put a
    `D1` frame on the bus 60 times a second.
    """
    if not 0 <= word <= 0xFFFF:
        raise PumpAutoError(f"word {word!r} is not a u16")
    if word == UNSET:
        # NO CORRECTION -- and this is a ruling, not an oversight (Task 8b fix round 1, spec
        # Sec 3.4 step 1). `UNSET` is what the RECEIVE SLOT reads from boot until the G12's
        # first sweep of this selector, ~5.0 s later (BUS-BASELINE PC.42), so it cannot be
        # read as "the cell has never been written": correcting it would put a `D1` write of
        # the default over the user's stored word -- minutes AND the Pump Link flag, which
        # share the selector -- on the first paint tick of every power-up. The panel stays
        # silent instead, and a genuinely never-configured cell is bootstrapped by the user's
        # first touch: the G12's own arithmetic on 0xFFFF yields 0x00FF (the toggle) or 0x0004
        # (an arrow), neither of which is the sentinel, so the rules below correct those on the
        # very next tick. This function is the model the authored routine is checked against,
        # so it must not encode the seed the ruling struck.
        return None

    minutes, link = unpack(word)
    flag_byte = (word & FLAG_MASK) >> FLAG_SHIFT

    # THE BORROW, and it is the price of `MINUTES_MIN = 0`. The minutes share their u16 with
    # the Pump Link flag and the G12 protects neither field (PC.47), so `-` at 0 subtracts 5
    # from the WHOLE word and borrows out of the low byte: link off gives `0x0000 - 5 =
    # 0xFFFB`, link on gives `0x0100 - 5 = 0x00FB`. Left alone, the first reads as 251 minutes
    # (pinned to 60, flag decoded from a byte that is really a borrow marker) and the second
    # as 251 minutes with the flag CLEARED -- a `-` press would jump the setting to an hour
    # and silently turn Pump Link off.
    #
    # The recovery is exact, not a guess: a minute byte of `BORROW_MINUTES` is unreachable from
    # the grid (max 60), so it can only be the low byte of a word that underflowed by one step.
    # Adding the step back to the WHOLE word undoes the borrow and both fields come back
    # exactly -- `0xFFFB + 5 = 0x0000` (0 minutes, link off), `0x00FB + 5 = 0x0100` (0 minutes,
    # link on). The authored routine reaches the same two answers by compare rather than by a
    # 16-bit add, because it holds the byte and the flag in separate registers.
    #
    # With the floor at 5 this could not arise -- `-` from 5 landed exactly on 0 with no borrow
    # -- which is why the rule appears with the floor change and not before it.
    if minutes_min == 0 and minutes == BORROW_MINUTES:
        # NB the recovered word is a local: `word` stays the value actually stored, because the
        # return below compares against it to decide whether a write is needed at all. Folding
        # the recovery into `word` would make every borrow compare equal to itself and return
        # "no correction", leaving the borrowed word on the G12 for good.
        undone = (word + MINUTES_STEP) & 0xFFFF
        minutes, link = unpack(undone)
        flag_byte = (undone & FLAG_MASK) >> FLAG_SHIFT

    # 0xFF is a byte no arrow can produce even at the floor (`-` at 0 gives `BORROW_MINUTES`,
    # `-` at 5 gives 0), so the word is CORRUPT -> the default. 0x00 is NOT corrupt: it is the
    # disabled setting itself, and since v20 a resting value rather than an underflow.
    if minutes == 0xFF:
        default = pack(MINUTES_DEFAULT, False)
        return None if word == default else default

    # The minute byte is plausible, so the word is merely off-grid or the flag took a
    # borrow. A flag byte above 1 recovers as OFF when it is the 0xFF borrow signature,
    # otherwise as ON (a real nonzero someone wrote).
    if flag_byte > 1:
        link = flag_byte != 0xFF
    minutes = _round_to_step(minutes, minutes_min)

    corrected = pack(minutes, link, minutes_min)
    return None if corrected == word else corrected


# ======================================================================================
# Task 8b -- the per-tick routine: clamp, display, auto-off timer
# ======================================================================================
#
# A fourth link on the per-tick chain `followchannel.hook_state` already maintains. It runs
# inside the detoured `bl` at GuiCode+0xEE3C, obeys the same transparency contract every
# other link obeys (`push {r0-r3,lr}` / never write r4-r7 / `pop.w {r0-r3,lr}` + `b.w
# <whatever the hook reaches now>`), and every instruction it emits is re-read with capstone
# by `thumb.assert_disassembles_to` before it can reach an image.
#
# WHAT IT DOES, in the order it does it -- the order is the design:
#
#   1. Read the `0xEE` receive slot as a halfword. If it is 0xFFFF the G12 has not spoken
#      about this selector yet -- at boot that is true for up to ~5 s (PC.42's sweep period)
#      and is indistinguishable from a genuinely unset cell -- so the routine touches only
#      PANEL-LOCAL cells and ends the tick: the default minute count to the readout byte, and
#      0 to the countdown, which is the one moment the uninitialised countdown can safely be
#      established (var 289's refresh class is "none", so a populated slot never reverts to
#      the sentinel -- no live countdown can exist here). It puts NOTHING on the bus while the
#      slot reads 0xFFFF; a write there would clobber the user's stored word on the first tick
#      of every power-up.
#   2. Compute `clamp()` in machine code. If a correction is needed, write the corrected
#      word absolutely (+ read-back). This runs BEFORE anything reads the value, so a word
#      the G12's unguarded u16 arithmetic has borrowed across (BUS-BASELINE PC.47) self-heals
#      within one paint tick and nothing downstream ever acts on it.
#   3. Mirror the corrected minute byte to the readout cell (Task 6's slot, Task 8a's width:
#      variable type 1 is a single `ldrb`, so only the low byte is the readout).
#   4. Decode the pump's status the firmware's own way: `(byte & 0x1C) == 0x04` is "on".
#   5. Advance the countdown at most once per second with NO extra state: `t =
#      [r4,#0x204] >> 10` (1.024 s units) compared against a one-byte anchor -- the display
#      cell's HIGH byte, free because the readout is a byte load. `t`'s low byte wraps every
#      ~4.4 min, far longer than a paint tick, so a wrap cannot be missed.
#   6. Arm / disarm / fire. There is NO latch (Task 8a eliminated it): the countdown is its
#      own edge memory and 0 means disarmed.
#        minutes == 0 (only when the floor is 0) -> 0 (disarm), running or not
#        countdown == 0 and pump on              -> arm to minutes * 60
#        countdown > MINUTES_MAX*60 and pump on  -> the same: a value this routine can never
#                                                   have written is power-on garbage, not a
#                                                   countdown, so re-arm over it
#        countdown != 0 and pump off             -> 0 (disarm)
#        countdown reaches 0 by decrement, on    -> send channel 44's OFF frame, stay disarmed
#   7. Tail-branch to whatever the hook currently reaches.
#
# THE FRAMES. Steps 1/2 emit `0x1FED9` config frames in Finding 7's pool wire order -- a
# little-endian u32 CAN id followed by the 8-byte payload. 13.6's own pool records fix both
# the id and the payload shape, e.g. at file offset 0x28cec/0x28cf8:
#
#     00 d9 fe 19 | ff 96 cc 0f e8 03 d2 ea     a D2 delta of +1000 on selector 0xCC
#     00 d9 fe 19 | ff 96 cc 0f ff ff d3 ff     its D3 read-back
#
# and PC.47 wrote `0xEE` from the bus host with exactly `FF96EE0F0100D1EA`, so the D1 payload is
# hardware-observed on this selector. The value is little-endian at payload[4..5] (PC.47:
# `0005` added 0x0500), i.e. frame offset 8 -- which is why the routine copies the template
# to the stack and then `strh`s the runtime-computed word over that halfword.
#
# THE CLAMP IN MACHINE CODE is `clamp()` instruction for instruction, including the
# ties-up rounding, which is done by repeated subtraction (at most 12 iterations for a
# minute byte in 5..60) because this firmware's Thumb subset has no divide here.
# `tests/test_pumpauto.py` executes the emitted BYTES against `clamp()` for all 65536
# possible words rather than trusting that claim.

import struct
from dataclasses import dataclass

from . import followchannel, guidata, register as _register, theme, thumb

# Blob layout, offsets from `code_load_addr`. The three 12-byte frames sit at the front so
# their absolute addresses are known at build time and go straight into the literal pool.
OFF_TEMPLATE_OFF = 0x00        # channel 44 OFF (the follower's own `_template(44, 0, 3)`)
WRITE_TEMPLATE_OFF = 0x0C      # the D1 absolute write; its value halfword is patched at runtime
READBACK_TEMPLATE_OFF = 0x18   # the D3 read-back that follows every write
ENTRY_OFF = 0x24               # the routine itself

# The `0x1FED9` config frame (Finding 7). `0x19FED900` little-endian, then `FF 96 <sel> 0F
# <value u16 LE> <function> <trailer>`.
CONFIG_FRAME_ID = bytes.fromhex("00d9fe19")
CONFIG_GROUP = 0x96
CONFIG_PAD = 0x0F
FUNCTION_WRITE = 0xD1          # absolute set
FUNCTION_READBACK = 0xD3       # read-back request
WRITE_TRAILER = 0xEA           # what PC.47's own successful `0xEE` write carried
READBACK_TRAILER = 0xFF        # what 13.6's own read-back pool records carry
READBACK_VALUE = 0xFFFF        # the read-back's don't-care value field (pool records again)

# The pump's operating-status decode, unchanged from the follower: the light/pump is on iff
# the 3-bit field at bits 2..4 equals 1.
PUMP_ON_MASK = 0x1C
PUMP_ON_VALUE = 0x04

CLOCK_OFFSET = 0x204           # GuiCodeState + 0x204, the millisecond clock every hold uses
TICK_SHIFT = 10                # >> 10 -> 1.024-second units (a shift, not a divide)
SECONDS_PER_MINUTE = 60        # fits `movs rN,#imm8`, so no pool word for the factor
DISARMED = 0                   # the countdown's "not running" value. NOT 0xFFFF -- that would
                               # read as ~18 hours of countdown, and there is no latch to
                               # distinguish it from a real one.


@dataclass
class PumpAutoPlan:
    """Every address the routine needs, derived from the image or refused.

    Derived from the **pre-append** image, always: `followchannel._guicode_references` is a
    literal scan, so once this routine's own pool words point at the scratch cells they are
    "GuiCode-referenced" and `scan_display_cell` moves on. The allocation is computed once,
    on the input image, and carried in this plan.
    """
    config_slot: int            # &(selector 0xEE receive slot halfword)
    pump_slot: int              # &(channel 44 DC_DIMMER_STATUS_3 slot halfword)
    display_addr: int           # &(minutes readout cell) -- a single byte, variable type 1
    tick_anchor_addr: int       # &(coarse-tick anchor) == display_addr + 1, the cell's high byte
    countdown_addr: int         # &(seconds countdown halfword); 0 == disarmed
    hook_at: int                # GuiCode-section offset of the detoured `bl`
    hook_old: bytes             # its current 4 bytes (stock, or a bl into an earlier routine)
    tail_target: int            # what this link tail-branches to (`followchannel.hook_state`)
    sender_ptr: int             # &(fn-ptr to the host CAN sender)
    off_template: bytes         # the 12-byte channel-44 OFF frame
    default_word: int           # pack(MINUTES_DEFAULT, False), the seed and the clamp fallback
    write_template: bytes       # the D1 absolute config write (value patched at runtime)
    readback_template: bytes    # its D3 read-back
    config_selector: int        # 0xEE
    display_slot: int           # the +0x44BC8 cell index behind display_addr (Task 9 types it)
    countdown_slot: int         # the cell index behind countdown_addr
    minutes_min: int = MINUTES_MIN   # the floor this build ships; 0 makes 0 the disabled setting


def _config_frame(selector: int, value: int, function: int, trailer: int) -> bytes:
    """A 12-byte `0x1FED9` config frame in the pool's wire order."""
    if not 0 <= selector <= 0xFF:
        raise PumpAutoError(f"selector {selector!r} is not a byte")
    if not 0 <= value <= 0xFFFF:
        raise PumpAutoError(f"value {value!r} is not a u16")
    return (CONFIG_FRAME_ID
            + bytes([0xFF, CONFIG_GROUP, selector, CONFIG_PAD])
            + struct.pack("<H", value)
            + bytes([function, trailer]))


def resolve(data: bytes, *, config: "_register.Register",
            pump: "_register.Register",
            minutes_min: int = MINUTES_MIN) -> PumpAutoPlan:
    """Derive the plan from `data` (the image the routine will be appended to), or refuse.

    `config` is the `0xEE` DGN_CONFIG register and `pump` the channel-44 DC_DIMMER_STATUS_3
    register, both from `register.resolve_register`. Everything else -- the two scratch cells,
    the hook and its current tail -- comes out of the image.

    `minutes_min` is the minute floor the built routine will enforce: `MINUTES_MIN` (5, the
    v14-v19 behaviour and the default) or 0, which makes 0 a legal setting that disables Auto
    Off and turns on the borrow recovery `clamp` documents. Nothing else is accepted -- the
    grid below 5 has no other legal point, and a floor of, say, 10 would silently make the
    stored 5 unreachable.
    """
    if minutes_min not in (0, MINUTES_MIN):
        raise PumpAutoError(
            f"minutes_min {minutes_min} is neither 0 (Auto Off disablable) nor the shipped "
            f"floor {MINUTES_MIN}")
    if config.dgn_low != guidata.DGN_CONFIG:
        raise PumpAutoError(
            f"config register subscribes to DGN {config.dgn_low:#06x}, not DGN_CONFIG "
            f"({guidata.DGN_CONFIG:#06x})")
    if pump.dgn_low != guidata.DGN_DC_DIMMER_STATUS_3:
        raise PumpAutoError(
            f"pump register subscribes to DGN {pump.dgn_low:#06x}, not DC_DIMMER_STATUS_3 "
            f"({guidata.DGN_DC_DIMMER_STATUS_3:#06x})")
    for name, addr in (("config_slot", config.slot_addr), ("pump_slot", pump.slot_addr)):
        if addr % 2:
            raise PumpAutoError(f"{name} {addr:#x} must be 2-aligned (ldrh)")
        if not (theme.SRAM_LOW <= addr < theme.SRAM_HIGH):
            raise PumpAutoError(f"{name} {addr:#x} is outside SRAM")

    # The two scratch cells, in `scan_display_cell`'s own order and under its own discipline
    # (2-aligned, both bytes in SRAM and GuiCode-unreferenced -- the scan that excludes the
    # trap cell 203, which is widget-spare but is live working memory for the very
    # per-tick function this routine chains onto).
    display_slot, display_addr = followchannel.scan_display_cell(data)
    countdown_slot, countdown_addr = followchannel.scan_display_cell(
        data, exclude=(display_slot,))
    tick_anchor_addr = display_addr + 1
    if countdown_addr % 2:
        raise PumpAutoError(f"countdown {countdown_addr:#x} must be 2-aligned (ldrh/strh)")
    spans = {display_addr, tick_anchor_addr, countdown_addr, countdown_addr + 1}
    if len(spans) != 4:
        raise PumpAutoError(
            f"the scratch spans overlap: display {display_addr:#x}+2, countdown "
            f"{countdown_addr:#x}+2")

    hook_old, tail_target = followchannel.hook_state(data)

    return PumpAutoPlan(
        config_slot=config.slot_addr,
        pump_slot=pump.slot_addr,
        display_addr=display_addr,
        tick_anchor_addr=tick_anchor_addr,
        countdown_addr=countdown_addr,
        hook_at=followchannel.HOOK_OFF,
        hook_old=hook_old,
        tail_target=tail_target,
        sender_ptr=followchannel.SENDER_PTR_ADDR,
        off_template=followchannel._template(pump.value, 0, 3),
        default_word=pack(MINUTES_DEFAULT, False),
        write_template=_config_frame(config.value, 0, FUNCTION_WRITE, WRITE_TRAILER),
        readback_template=_config_frame(config.value, READBACK_VALUE, FUNCTION_READBACK,
                                        READBACK_TRAILER),
        config_selector=config.value,
        display_slot=display_slot,
        countdown_slot=countdown_slot,
        minutes_min=minutes_min,
    )


def build(plan: PumpAutoPlan, code_load_addr: int) -> tuple[bytes, list]:
    """Author the appended blob (three frames + routine + literal pool) and verify it.

    Returns `(appended_bytes, disassembly)`. The disassembly is what
    `thumb.assert_disassembles_to` has just confirmed, instruction for instruction, against
    the text this builder claims it authored -- the same no-trust-on-inspection contract as
    `followchannel.build`.
    """
    minutes_min = plan.minutes_min
    if minutes_min not in (0, MINUTES_MIN):
        raise PumpAutoError(
            f"plan.minutes_min {minutes_min} is neither 0 nor the shipped floor {MINUTES_MIN}")
    for name, tpl in (("off_template", plan.off_template),
                      ("write_template", plan.write_template),
                      ("readback_template", plan.readback_template)):
        if len(tpl) != 12:
            raise PumpAutoError(f"{name} must be 12 bytes, got {len(tpl)}")
    if not (0 <= plan.default_word <= 0xFF):
        # Step 1 shows `default_word`'s MINUTE byte with `movs rN,#imm8`. A default carrying a
        # set link flag (>= 0x0100) would put a flag the user never chose behind that readout,
        # so refuse it rather than silently display the low byte.
        raise PumpAutoError(
            f"default_word {plan.default_word:#x} must fit `movs rN,#imm8` (link flag clear); "
            "a default with the link flag set is not something this routine may display")
    if plan.tick_anchor_addr != plan.display_addr + 1:
        raise PumpAutoError("the tick anchor must be the display cell's high byte")

    # capstone prints small immediates in decimal and >= 10 in hex; match it so the
    # round-trip check passes for every immediate this routine emits.
    def imm(v: int) -> str:
        return f"{v:#x}" if v >= 10 else str(v)

    pool_order = ["cfg", "pump", "disp", "cd", "sender", "offtpl", "wrtpl", "rbtpl"]
    pool_val = {
        "cfg": plan.config_slot,
        "pump": plan.pump_slot,
        "disp": plan.display_addr,          # its high byte is the tick anchor
        "cd": plan.countdown_addr,
        "sender": plan.sender_ptr,
        "offtpl": code_load_addr + OFF_TEMPLATE_OFF,
        "wrtpl": code_load_addr + WRITE_TEMPLATE_OFF,
        "rbtpl": code_load_addr + READBACK_TEMPLATE_OFF,
    }

    stream: list = []

    def emit(size: int, fn) -> None:
        stream.append(("ins", size, fn))

    def label(name: str) -> None:
        stream.append(("lbl", name))

    def ldrlit_text(rt: int, here: int, target: int) -> str:
        off = target - ((here + 4) & ~3)
        return f"ldr r{rt}, [pc, #{off:#x}]" if off else f"ldr r{rt}, [pc]"

    def send(tmpl: str, patch: bool) -> None:
        """Copy a 12-byte frame to the stack and hand it to the host sender.

        The Finding-7 send primitive, in the form `followchannel.build` already proves on
        hardware: `sub sp,#0x14` keeps sp 8-byte aligned at the `blx` (5 pushed words + 20 =
        40), the frame is copied a word at a time, and `r0` is the buffer.

        `patch` writes r3 over the frame's value halfword (payload[4..5], frame offset 8).
        The copy therefore uses only r0-r2, leaving r3 -- the runtime-computed config word --
        untouched right up to the store. Everything after a `blx` reloads what it needs; the
        host sender is a normal AAPCS call and may clobber r0-r3.
        """
        emit(2, lambda h, s: (thumb.sub_sp_imm(20), "sub sp, #0x14"))
        emit(2, lambda h, s, n=tmpl: (thumb.ldr_literal(0, h, s(n)), ldrlit_text(0, h, s(n))))
        emit(2, lambda h, s: (thumb.ldr_imm(1, 0, 0), "ldr r1, [r0]"))
        emit(2, lambda h, s: (thumb.ldr_imm(2, 0, 4), "ldr r2, [r0, #4]"))
        emit(2, lambda h, s: (thumb.str_sp_imm(1, 0), "str r1, [sp]"))
        emit(2, lambda h, s: (thumb.str_sp_imm(2, 4), "str r2, [sp, #4]"))
        emit(2, lambda h, s: (thumb.ldr_imm(1, 0, 8), "ldr r1, [r0, #8]"))
        emit(2, lambda h, s: (thumb.str_sp_imm(1, 8), "str r1, [sp, #8]"))
        emit(2, lambda h, s: (thumb.mov_reg(0, 13), "mov r0, sp"))
        if patch:
            emit(2, lambda h, s: (thumb.strh_imm(3, 0, 8), "strh r3, [r0, #8]"))
        emit(2, lambda h, s: (thumb.ldr_literal(2, h, s("sender")),
                              ldrlit_text(2, h, s("sender"))))
        emit(2, lambda h, s: (thumb.ldr_imm(2, 2, 0), "ldr r2, [r2]"))
        emit(2, lambda h, s: (thumb.blx_reg(2), "blx r2"))
        emit(2, lambda h, s: (thumb.add_sp_imm(20), "add sp, #0x14"))

    # --- entry ------------------------------------------------------------------------
    emit(2, lambda h, s: (thumb.push_reglist([0, 1, 2, 3, 14]), "push {r0, r1, r2, r3, lr}"))

    # --- steps 1 & 2: read the config word, split it, and compute the clamp -------------
    # r1 = the stored word, r2 = its minute byte, r3 = its flag byte.
    emit(2, lambda h, s: (thumb.ldr_literal(0, h, s("cfg")), ldrlit_text(0, h, s("cfg"))))
    emit(2, lambda h, s: (thumb.ldrh_imm(1, 0, 0), "ldrh r1, [r0]"))
    emit(2, lambda h, s: (thumb.movs_imm(2, MINUTES_MASK), f"movs r2, #{MINUTES_MASK:#x}"))
    emit(2, lambda h, s: (thumb.ands_reg(2, 1), "ands r2, r1"))
    emit(2, lambda h, s: (thumb.lsrs_imm(3, 1, FLAG_SHIFT), f"lsrs r3, r1, #{FLAG_SHIFT}"))
    # UNSET is 0xFFFF, tested as two BYTE compares -- deliberately, so that no 0xFFFF literal
    # exists anywhere in this routine (the countdown disarms with 0, and a stray 0xFFFF would
    # be ~18 hours of countdown).
    emit(2, lambda h, s: (thumb.cmp_imm8(2, 0xFF), "cmp r2, #0xff"))
    emit(2, lambda h, s: (thumb.bcc_n(h, s("notunset"), "ne"), f"bne #{s('notunset'):#x}"))
    emit(2, lambda h, s: (thumb.cmp_imm8(3, 0xFF), "cmp r3, #0xff"))
    emit(2, lambda h, s: (thumb.bcc_n(h, s("unset"), "eq"), f"beq #{s('unset'):#x}"))
    label("notunset")
    # THE BORROW -- `clamp`'s rule, done in registers. A minute byte of `BORROW_MINUTES` is
    # unreachable from the grid, so it can only be a `-` press from 0.
    #
    # `clamp` recovers by adding the step back to the whole u16. Here the word is already split
    # across r2 (minutes) and r3 (flag byte), and that same 16-bit add is exactly: minutes
    # become 0, and the flag byte carries UP by one -- 0xFF wrapping to 0. So two instructions
    # reproduce it with no 16-bit add and no `uxth` (which this routine's encoder set does not
    # carry). The adjusted flag byte then goes through the ORDINARY decode below rather than a
    # special case of its own, which is what keeps the two models equal for a garbage flag byte
    # as well as for the two the arrows produce: stored 0x00 -> 0x01 -> ON (link was on, its
    # byte borrowed down), stored 0xFF -> 0x00 -> OFF (the whole word wrapped), stored 0xFE ->
    # 0xFF -> OFF by the borrow-signature rule, which is what `0xFEFB + 5 = 0xFF00` means too.
    # ...and only when the floor is 0. At the shipped floor of 5 no arrow press can put
    # `BORROW_MINUTES` in the byte, so the branch would be unreachable -- and emitting it anyway
    # would move every instruction after it, changing the bytes of v14-v19's routine and breaking
    # the byte-stability pins that make those flashed images reproducible. The feature is opt-in
    # in the machine code exactly as it is in `clamp`.
    if minutes_min == 0:
        emit(2, lambda h, s: (thumb.cmp_imm8(2, BORROW_MINUTES),
                              f"cmp r2, #{imm(BORROW_MINUTES)}"))
        emit(2, lambda h, s: (thumb.bcc_n(h, s("notborrow"), "ne"),
                              f"bne #{s('notborrow'):#x}"))
        emit(2, lambda h, s: (thumb.movs_imm(2, 0), "movs r2, #0"))
        emit(2, lambda h, s: (thumb.cmp_imm8(3, 0xFF), "cmp r3, #0xff"))
        emit(2, lambda h, s: (thumb.bcc_n(h, s("flagoff"), "eq"), f"beq #{s('flagoff'):#x}"))
        emit(2, lambda h, s: (thumb.adds_imm8(3, 1), "adds r3, #1"))
        emit(2, lambda h, s: (thumb.b_n(h, s("flagdecode")), f"b #{s('flagdecode'):#x}"))
        label("notborrow")
    # A minute byte of 0xFF is not a value the arrows can produce -- `-` at 0 gives
    # `BORROW_MINUTES` and `-` at 5 gives 0 -- so the word is CORRUPT: seed the whole default
    # rather than keep a flag decoded from bytes we have just declared untrustworthy (`clamp`'s
    # own rule). A minute byte of 0x00 IS producible and is now the disabled setting itself; it
    # falls through with its flag intact -- v15 defect 2, and v20's Off state.
    emit(2, lambda h, s: (thumb.cmp_imm8(2, 0xFF), "cmp r2, #0xff"))
    emit(2, lambda h, s: (thumb.bcc_n(h, s("default"), "eq"), f"beq #{s('default'):#x}"))
    # The flag byte: 0 or the 0xFF borrow signature recovers as OFF, anything else as ON. The
    # borrow path above joins here, having already carried its byte up by one.
    label("flagdecode")
    emit(2, lambda h, s: (thumb.cmp_imm8(3, 0), "cmp r3, #0"))
    emit(2, lambda h, s: (thumb.bcc_n(h, s("flagoff"), "eq"), f"beq #{s('flagoff'):#x}"))
    emit(2, lambda h, s: (thumb.cmp_imm8(3, 0xFF), "cmp r3, #0xff"))
    emit(2, lambda h, s: (thumb.bcc_n(h, s("flagoff"), "eq"), f"beq #{s('flagoff'):#x}"))
    emit(4, lambda h, s: (thumb.movw(3, 1 << FLAG_SHIFT), f"movw r3, #{1 << FLAG_SHIFT:#x}"))
    emit(2, lambda h, s: (thumb.b_n(h, s("minutes")), f"b #{s('minutes'):#x}"))
    label("flagoff")
    emit(2, lambda h, s: (thumb.movs_imm(3, 0), "movs r3, #0"))
    label("minutes")
    # `_round_to_step`: pin below the floor and above the ceiling, then round the rest to the
    # nearest multiple of 5 with ties up. `((m + 2) // 5) * 5` by repeated subtraction -- at
    # most 12 iterations for a minute byte in 5..60, and there is no divide in this subset.
    emit(2, lambda h, s: (thumb.cmp_imm8(2, minutes_min), f"cmp r2, #{imm(minutes_min)}"))
    emit(2, lambda h, s: (thumb.bcc_n(h, s("pinlow"), "cc"), f"blo #{s('pinlow'):#x}"))
    emit(2, lambda h, s: (thumb.cmp_imm8(2, MINUTES_MAX + 1), f"cmp r2, #{imm(MINUTES_MAX + 1)}"))
    emit(2, lambda h, s: (thumb.bcc_n(h, s("pinhigh"), "cs"), f"bhs #{s('pinhigh'):#x}"))
    emit(2, lambda h, s: (thumb.adds_imm8(2, MINUTES_STEP // 2), f"adds r2, #{MINUTES_STEP // 2}"))
    emit(2, lambda h, s: (thumb.movs_imm(0, 0), "movs r0, #0"))
    label("round")
    emit(2, lambda h, s: (thumb.cmp_imm8(2, MINUTES_STEP), f"cmp r2, #{imm(MINUTES_STEP)}"))
    emit(2, lambda h, s: (thumb.bcc_n(h, s("rounded"), "cc"), f"blo #{s('rounded'):#x}"))
    emit(2, lambda h, s: (thumb.subs_imm8(2, MINUTES_STEP), f"subs r2, #{imm(MINUTES_STEP)}"))
    emit(2, lambda h, s: (thumb.adds_imm8(0, MINUTES_STEP), f"adds r0, #{imm(MINUTES_STEP)}"))
    emit(2, lambda h, s: (thumb.b_n(h, s("round")), f"b #{s('round'):#x}"))
    label("rounded")
    emit(2, lambda h, s: (thumb.mov_reg(2, 0), "mov r2, r0"))
    emit(2, lambda h, s: (thumb.b_n(h, s("combine")), f"b #{s('combine'):#x}"))
    label("pinlow")
    emit(2, lambda h, s: (thumb.movs_imm(2, minutes_min), f"movs r2, #{imm(minutes_min)}"))
    emit(2, lambda h, s: (thumb.b_n(h, s("combine")), f"b #{s('combine'):#x}"))
    label("pinhigh")
    emit(2, lambda h, s: (thumb.movs_imm(2, MINUTES_MAX), f"movs r2, #{imm(MINUTES_MAX)}"))
    emit(2, lambda h, s: (thumb.b_n(h, s("combine")), f"b #{s('combine'):#x}"))
    label("default")
    emit(2, lambda h, s: (thumb.movs_imm(2, MINUTES_DEFAULT), f"movs r2, #{imm(MINUTES_DEFAULT)}"))
    emit(2, lambda h, s: (thumb.movs_imm(3, 0), "movs r3, #0"))
    label("combine")
    # r3 = the corrected word, r2 = the corrected minute count.
    emit(2, lambda h, s: (thumb.adds_reg(3, 3, 2), "adds r3, r3, r2"))

    # --- step 3: mirror the minute byte to the readout cell ----------------------------
    # Done from the register the clamp just produced, before any bus traffic: the readout
    # shows the corrected value on the very tick the correction is decided, and the byte is
    # also where the arm step (below) re-reads the minute count from, since a `blx` in
    # between would clobber any register holding it.
    emit(2, lambda h, s: (thumb.ldr_literal(0, h, s("disp")), ldrlit_text(0, h, s("disp"))))
    emit(4, lambda h, s: (thumb.strb_imm12(2, 0, 0), "strb.w r2, [r0]"))

    # --- step 2 (the write): only when the stored word is not already correct ----------
    emit(2, lambda h, s: (thumb.cmp_reg(3, 1), "cmp r3, r1"))
    emit(2, lambda h, s: (thumb.bcc_n(h, s("decode"), "eq"), f"beq #{s('decode'):#x}"))
    send("wrtpl", patch=True)
    send("rbtpl", patch=False)
    emit(2, lambda h, s: (thumb.b_n(h, s("decode")), f"b #{s('decode'):#x}"))

    # --- step 1: the slot the G12 has not spoken about yet ------------------------------
    # 0xFFFF, and the routine is SILENT ON THE BUS. It cannot mean "the cell is unset": the
    # panel initialises every receive slot to 0xFFFF at boot and the G12 sweeps this selector
    # only every ~5.0 s (BUS-BASELINE PC.42), so for up to five seconds of every boot -- while
    # this routine runs every paint tick -- "unset" and "not swept yet" are the same bytes. A
    # write here would overwrite the user's stored word, minutes AND the Pump Link flag, on the
    # first tick of every power-up. So: put the default minute count on the READOUT (a panel-
    # local byte, no bus traffic, nothing destroyed), ZERO THE COUNTDOWN (panel-local too --
    # see the note at the store) and end the tick. Nothing arms and nothing decrements from a
    # word we have not actually seen.
    #
    # A genuinely never-configured cell is bootstrapped by the user's first touch, not by us:
    # from 0xFFFF the toggle's +0x0100 gives 0x00FF and an arrow's +5 gives 0x0004, neither of
    # which is the sentinel, so step 2's clamp corrects it on the very next tick. One extra tap
    # on a never-configured coach, and no path that can destroy a setting. Do not "fix" the
    # missing seed -- spec Sec 3.4 step 1 records the ruling.
    label("unset")
    emit(2, lambda h, s: (thumb.movs_imm(2, plan.default_word & MINUTES_MASK),
                          f"movs r2, #{imm(plan.default_word & MINUTES_MASK)}"))
    emit(2, lambda h, s: (thumb.ldr_literal(0, h, s("disp")), ldrlit_text(0, h, s("disp"))))
    emit(4, lambda h, s: (thumb.strb_imm12(2, 0, 0), "strb.w r2, [r0]"))
    # AND DISARM THE COUNTDOWN. This is the boot-time zeroing of the uninitialised cell, and
    # it is the ONLY place it can happen: the countdown lives above `_ebss`, which the panel's
    # C runtime never establishes (`raminit.py`), so at power-on it holds garbage. A garbage
    # value at or below `MINUTES_MAX * 60` is indistinguishable from a live countdown -- the
    # arm step's impossible-value guard sits above that range by construction -- so a coach
    # that boots with the pump already running would count a phantom interval down and put an
    # unsolicited channel-44 OFF frame on the bus.
    #
    # This path is the one moment where zeroing is provably safe, and the proof is var 289's
    # refresh class: 3 = "none" (`MCF-FORMAT.md` Sec 5.4a; byte 2 is 0x2D after the widening,
    # 0x1D before -- the low nibble the class lives in is untouched), so a slot that has been
    # populated NEVER reverts to 0xFFFF. Reading the sentinel therefore means the G12 has not
    # spoken since boot, and no legitimate countdown can be running.
    #
    # It also does not weaken the standing boot ruling above: a store to a panel-local scratch
    # halfword puts nothing on the bus and cannot destroy a user setting.
    emit(2, lambda h, s: (thumb.movs_imm(1, DISARMED), f"movs r1, #{imm(DISARMED)}"))
    emit(2, lambda h, s: (thumb.ldr_literal(0, h, s("cd")), ldrlit_text(0, h, s("cd"))))
    emit(2, lambda h, s: (thumb.strh_imm(1, 0, 0), "strh r1, [r0]"))
    emit(2, lambda h, s: (thumb.b_n(h, s("done")), f"b #{s('done'):#x}"))

    # --- step 4: is the pump on? -------------------------------------------------------
    # The follower's own decode, unchanged: the 3-bit operating-status field (bits 2..4) is 1.
    # The stale/never-seen sentinel 0xFFFF gives field 7, i.e. OFF -- which is what it means.
    label("decode")
    emit(2, lambda h, s: (thumb.ldr_literal(0, h, s("pump")), ldrlit_text(0, h, s("pump"))))
    emit(2, lambda h, s: (thumb.ldrh_imm(0, 0, 0), "ldrh r0, [r0]"))
    emit(2, lambda h, s: (thumb.movs_imm(1, PUMP_ON_MASK), f"movs r1, #{PUMP_ON_MASK:#x}"))
    emit(2, lambda h, s: (thumb.ands_reg(0, 1), "ands r0, r1"))
    emit(2, lambda h, s: (thumb.cmp_imm8(0, PUMP_ON_VALUE), f"cmp r0, #{imm(PUMP_ON_VALUE)}"))
    emit(2, lambda h, s: (thumb.bcc_n(h, s("pumpoff"), "ne"), f"bne #{s('pumpoff'):#x}"))
    emit(2, lambda h, s: (thumb.movs_imm(3, 1), "movs r3, #1"))
    emit(2, lambda h, s: (thumb.b_n(h, s("clock")), f"b #{s('clock'):#x}"))
    label("pumpoff")
    emit(2, lambda h, s: (thumb.movs_imm(3, 0), "movs r3, #0"))
    label("clock")
    # r3 now carries the on-state through the whole timer section (nothing below writes it
    # until the fire path, which ends the tick).

    # --- step 5: advance the countdown at most once per 1.024 s ------------------------
    emit(4, lambda h, s: (thumb.ldr_imm12(0, 4, CLOCK_OFFSET),
                          f"ldr.w r0, [r4, #{CLOCK_OFFSET:#x}]"))
    emit(2, lambda h, s: (thumb.lsrs_imm(0, 0, TICK_SHIFT), f"lsrs r0, r0, #{TICK_SHIFT:#x}"))
    emit(2, lambda h, s: (thumb.movs_imm(1, 0xFF), "movs r1, #0xff"))
    emit(2, lambda h, s: (thumb.ands_reg(0, 1), "ands r0, r1"))
    emit(2, lambda h, s: (thumb.ldr_literal(1, h, s("disp")), ldrlit_text(1, h, s("disp"))))
    emit(2, lambda h, s: (thumb.ldrb_imm(2, 1, 1), "ldrb r2, [r1, #1]"))
    emit(2, lambda h, s: (thumb.cmp_reg(0, 2), "cmp r0, r2"))
    emit(2, lambda h, s: (thumb.bcc_n(h, s("arm"), "eq"), f"beq #{s('arm'):#x}"))
    emit(4, lambda h, s: (thumb.strb_imm12(0, 1, 1), "strb.w r0, [r1, #1]"))
    emit(2, lambda h, s: (thumb.ldr_literal(0, h, s("cd")), ldrlit_text(0, h, s("cd"))))
    emit(2, lambda h, s: (thumb.ldrh_imm(1, 0, 0), "ldrh r1, [r0]"))
    emit(2, lambda h, s: (thumb.cmp_imm8(1, DISARMED), f"cmp r1, #{imm(DISARMED)}"))
    emit(2, lambda h, s: (thumb.bcc_n(h, s("arm"), "eq"), f"beq #{s('arm'):#x}"))
    emit(2, lambda h, s: (thumb.subs_imm8(1, 1), "subs r1, #1"))
    emit(2, lambda h, s: (thumb.strh_imm(1, 0, 0), "strh r1, [r0]"))
    emit(2, lambda h, s: (thumb.cmp_imm8(1, DISARMED), f"cmp r1, #{imm(DISARMED)}"))
    emit(2, lambda h, s: (thumb.bcc_n(h, s("arm"), "ne"), f"bne #{s('arm'):#x}"))
    emit(2, lambda h, s: (thumb.cmp_imm8(3, 0), "cmp r3, #0"))
    emit(2, lambda h, s: (thumb.bcc_n(h, s("arm"), "eq"), f"beq #{s('arm'):#x}"))

    # --- step 6 (fire): the interval elapsed with the pump still on --------------------
    send("offtpl", patch=False)
    emit(2, lambda h, s: (thumb.b_n(h, s("done")), f"b #{s('done'):#x}"))

    # --- step 6 (arm / disarm) ---------------------------------------------------------
    label("arm")
    emit(2, lambda h, s: (thumb.ldr_literal(0, h, s("cd")), ldrlit_text(0, h, s("cd"))))
    emit(2, lambda h, s: (thumb.ldrh_imm(1, 0, 0), "ldrh r1, [r0]"))
    # A 0-MINUTE SETTING DISARMS A RUNNING COUNTDOWN, not just a stopped one -- and this is the
    # v20 round-2 fix, found on the coach. Everything else about "0 disables" is arithmetic:
    # `armit` stores `minutes * 60`, so 0 arms to DISARMED and a timer that is not running never
    # starts. But `arm` re-reads the minute count ONLY at `armit`, which is reached only when the
    # countdown is already 0 -- so a countdown that was ALREADY running when the user stepped down
    # to 0 kept counting, and would have fired and switched the pump off after the feature was
    # disabled. The owner saw the mark stay lit across a page change with the setting at 0, which
    # is the panel telling the truth: the countdown really was still live.
    #
    # Reached on every tick, so the disarm is immediate rather than waiting for the next second.
    # r2 is dead here (the impossible-value guard below loads it fresh).
    if minutes_min == 0:
        emit(2, lambda h, s: (thumb.ldr_literal(2, h, s("disp")), ldrlit_text(2, h, s("disp"))))
        emit(2, lambda h, s: (thumb.ldrb_imm(2, 2, 0), "ldrb r2, [r2]"))
        emit(2, lambda h, s: (thumb.cmp_imm8(2, 0), "cmp r2, #0"))
        emit(2, lambda h, s: (thumb.bcc_n(h, s("disarm"), "eq"), f"beq #{s('disarm'):#x}"))
    emit(2, lambda h, s: (thumb.cmp_imm8(3, 0), "cmp r3, #0"))
    emit(2, lambda h, s: (thumb.bcc_n(h, s("disarm"), "eq"), f"beq #{s('disarm'):#x}"))
    emit(2, lambda h, s: (thumb.cmp_imm8(1, DISARMED), f"cmp r1, #{imm(DISARMED)}"))
    emit(2, lambda h, s: (thumb.bcc_n(h, s("armit"), "eq"), f"beq #{s('armit'):#x}"))
    # IMPOSSIBLE-VALUE GUARD. Nothing establishes the countdown cell's power-on value: `ldrh`
    # reads whatever the panel left in that byte pair, and if it happened to be nonzero with
    # the pump reading on, the routine would count a phantom interval down and send an
    # unsolicited OFF frame at an arbitrary moment. `MINUTES_MAX * 60` is the largest value the
    # arm step can ever write, so anything above it cannot have come from this routine: treat
    # it as disarmed and re-arm over it. Costs nothing when the cell really is zeroed, and does
    # not depend on being right about the panel's reset behaviour.
    #
    # A phantom AT OR BELOW 3600 is indistinguishable from a live countdown, so this guard
    # cannot catch it -- it is closed on the sentinel path instead, which zeroes the cell
    # before the G12 has ever spoken about this selector (see the store under `unset`). That is
    # what makes the two guards together cover the whole range; neither does on its own.
    emit(4, lambda h, s: (thumb.movw(2, MINUTES_MAX * SECONDS_PER_MINUTE),
                          f"movw r2, #{MINUTES_MAX * SECONDS_PER_MINUTE:#x}"))
    emit(2, lambda h, s: (thumb.cmp_reg(1, 2), "cmp r1, r2"))
    emit(2, lambda h, s: (thumb.bcc_n(h, s("done"), "ls"), f"bls #{s('done'):#x}"))
    label("armit")
    emit(2, lambda h, s: (thumb.ldr_literal(1, h, s("disp")), ldrlit_text(1, h, s("disp"))))
    emit(2, lambda h, s: (thumb.ldrb_imm(1, 1, 0), "ldrb r1, [r1]"))
    emit(2, lambda h, s: (thumb.movs_imm(2, SECONDS_PER_MINUTE),
                          f"movs r2, #{SECONDS_PER_MINUTE:#x}"))
    emit(2, lambda h, s: (thumb.muls(1, 2), "muls r1, r2, r1"))
    emit(2, lambda h, s: (thumb.strh_imm(1, 0, 0), "strh r1, [r0]"))
    emit(2, lambda h, s: (thumb.b_n(h, s("done")), f"b #{s('done'):#x}"))
    label("disarm")
    emit(2, lambda h, s: (thumb.cmp_imm8(1, DISARMED), f"cmp r1, #{imm(DISARMED)}"))
    emit(2, lambda h, s: (thumb.bcc_n(h, s("done"), "eq"), f"beq #{s('done'):#x}"))
    emit(2, lambda h, s: (thumb.movs_imm(1, DISARMED), f"movs r1, #{imm(DISARMED)}"))
    emit(2, lambda h, s: (thumb.strh_imm(1, 0, 0), "strh r1, [r0]"))

    # --- step 7: restore and tail-branch to whatever the hook reaches now ---------------
    label("done")
    emit(4, lambda h, s: (thumb.pop_w_reglist([0, 1, 2, 3, 14]), "pop.w {r0, r1, r2, r3, lr}"))
    emit(4, lambda h, s: (thumb.b_w(h, plan.tail_target), f"b.w #{plan.tail_target:#x}"))

    # Size pass: every emission is fixed-size, so one pass fixes every label offset.
    labels: dict[str, int] = {}
    code_len = 0
    for item in stream:
        if item[0] == "lbl":
            labels[item[1]] = code_len
        else:
            code_len += item[1]

    entry = code_load_addr + ENTRY_OFF
    pool_blob_off = (ENTRY_OFF + code_len + 3) & ~3          # 4-align the pool
    pool_abs = {name: code_load_addr + pool_blob_off + i * 4
                for i, name in enumerate(pool_order)}
    clash = set(labels) & set(pool_abs)
    if clash:
        # `sym` resolves labels first, so a label sharing a pool word's name would silently
        # turn a pool load into a code address. Refuse instead of emitting it.
        raise PumpAutoError(f"label/pool name collision: {sorted(clash)}")

    def sym(name: str) -> int:
        if name in labels:
            return entry + labels[name]
        return pool_abs[name]

    # Encode pass.
    code = bytearray()
    expected: list[str] = []
    for item in stream:
        if item[0] == "lbl":
            continue
        _kind, size, fn = item
        here = entry + len(code)
        raw, text = fn(here, sym)
        if len(raw) != size:
            raise PumpAutoError(f"instruction size mismatch at {here:#x}: {text!r}")
        code += raw
        expected.append(text)

    # Verify: re-read every emitted instruction and require it to say what we claimed.
    thumb.assert_disassembles_to(bytes(code), entry, expected)

    blob = bytearray()
    blob += plan.off_template
    blob += plan.write_template
    blob += plan.readback_template
    assert len(blob) == ENTRY_OFF
    blob += code
    while len(blob) < pool_blob_off:
        blob += b"\x00"
    for name in pool_order:
        blob += struct.pack("<I", pool_val[name])
    assert len(blob) % 4 == 0
    return bytes(blob), thumb.disassemble(bytes(code), entry)


def detour_edit(plan: PumpAutoPlan, code_load_addr: int) -> tuple[int, bytes, bytes]:
    """Retarget the per-tick hook's `bl` to this routine's entry.

    Returns `(GuiCode-section offset, old 4 bytes, new 4 bytes)`. The old bytes are refused
    unless they are the stock `bl` or a `bl` to exactly this plan's tail target -- i.e. the
    routine this new link is chaining in front of.
    """
    followchannel._check_plan_hook(plan)
    hook_runtime = theme.GUICODE_BASE_DEFAULT + plan.hook_at
    new = thumb.bl(hook_runtime, code_load_addr + ENTRY_OFF)
    return plan.hook_at, plan.hook_old, new
