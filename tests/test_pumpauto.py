"""The 0xEE packed register: minutes in the LOW byte, link flag in the HIGH byte.

The layout is forced by hardware: BUS-BASELINE.md PC.47 showed the G12 stores this
selector as a raw u16 and BORROWS across the byte boundary (0x0500 - 1 = 0x04FF), so
the field that must survive a borrow is the one the user set.
"""

import pytest

from fireflymcf import pumpauto


def test_pack_puts_minutes_low_and_flag_high():
    assert pumpauto.pack(30, False) == 0x001E
    assert pumpauto.pack(30, True) == 0x011E
    assert pumpauto.pack(5, True) == 0x0105
    assert pumpauto.pack(60, False) == 0x003C


def test_unpack_is_the_inverse():
    for minutes in range(5, 65, 5):
        for link in (False, True):
            assert pumpauto.unpack(pumpauto.pack(minutes, link)) == (minutes, link)


def test_pack_refuses_out_of_range_minutes():
    for bad in (0, 4, 61, 65, -5):
        with pytest.raises(pumpauto.PumpAutoError):
            pumpauto.pack(bad, False)


def test_pack_accepts_zero_only_at_the_lowered_floor():
    """v20's `allow_disable`. 0 is the disabled setting, and it is legal only for a build that
    asked for the lowered floor -- the default stays the shipped 5, which is what keeps every
    flashed image (v14-v19) compiling byte-identically."""
    with pytest.raises(pumpauto.PumpAutoError):
        pumpauto.pack(0, False)
    assert pumpauto.pack(0, False, minutes_min=0) == 0x0000
    assert pumpauto.pack(0, True, minutes_min=0) == 0x0100


def test_pack_refuses_off_grid_minutes():
    with pytest.raises(pumpauto.PumpAutoError):
        pumpauto.pack(7, False)


def test_clamp_passes_a_valid_word_unchanged():
    assert pumpauto.clamp(0x011E) is None
    assert pumpauto.clamp(0x003C) is None
    assert pumpauto.clamp(0x0105) is None


def test_clamp_does_not_correct_the_unset_sentinel():
    """SUPERSEDES Task 1's `test_clamp_seeds_the_unset_sentinel`, which asserted
    `clamp(0xFFFF) == pack(30, False)`. Task 8b fix round 1 ruled that the panel must never
    write while the slot reads 0xFFFF -- the slot reads it from boot until the G12's first
    sweep ~5.0 s later (BUS-BASELINE PC.42), so a "correction" there overwrites the user's
    stored minutes AND Pump Link flag on the first tick of every power-up. The ruling outranks
    the earlier assertion, and `clamp` is the model the authored routine is checked against, so
    it must not still encode the seed: `None` means "no correction, do nothing", which is
    exactly what the machine code does. See spec Sec 3.4 step 1."""
    assert pumpauto.clamp(0xFFFF) is None
    # and the bootstrap the ruling relies on: what the G12 produces from 0xFFFF on a first
    # touch is NOT the sentinel, so these are corrected normally.
    assert pumpauto.clamp(0x00FF) == pumpauto.pack(30, False)     # the toggle's +0x0100
    assert pumpauto.clamp(0x0004) == pumpauto.pack(5, False)      # an arrow's +5


def test_clamp_recovers_the_minute_underflow_borrow():
    """The exact word the coach produced: 0x0500 - 1. Under the REJECTED layout
    (minutes high) this is minutes 5 -> 4 and flag 0xFF, both corrupted. Under the
    chosen layout 0x04FF is flag 4 / minutes 255 -- nonsense either way, so it
    clamps to the default rather than inventing a value."""
    assert pumpauto.clamp(0x04FF) == pumpauto.pack(30, False)


def test_clamp_recovers_a_flag_borrow_and_KEEPS_the_minutes():
    """This is the payoff of the layout: -0x0100 while the flag is already 0 gives
    0xFF1E. The flag byte is garbage, the user's 30 minutes are intact, and a borrow
    can only come from a 'turn off' that was already off -- so the flag recovers to
    OFF and the setting survives."""
    assert pumpauto.clamp(0xFF1E) == pumpauto.pack(30, False)
    assert pumpauto.clamp(0xFF05) == pumpauto.pack(5, False)


def test_clamp_pins_minutes_to_the_range_ends():
    assert pumpauto.clamp(0x0002) == pumpauto.pack(5, False)     # below the floor
    assert pumpauto.clamp(0x0050) == pumpauto.pack(60, False)    # above the ceiling


def test_clamp_rounds_onto_the_lowered_floor_when_it_is_zero():
    assert pumpauto.clamp(0x0002, minutes_min=0) == 0x0000       # 2 rounds down to 0, not up to 5
    assert pumpauto.clamp(0x0050, minutes_min=0) == pumpauto.pack(60, False)


def test_clamp_pins_a_zero_minute_byte_to_the_floor_and_KEEPS_the_flag():
    """v15 defect 2. `-` at 5 sends -5, the G12 stores 0, and the clamp used to
    treat 0 as "never configured" and rewrite the DEFAULT with the link flag cleared. Zero IS a
    value the arrows produce; it pins to the floor like any other underflow, flag intact."""
    from fireflymcf import pumpauto
    assert pumpauto.clamp(0x0000) == pumpauto.pack(5, False)
    assert pumpauto.clamp(0x0100) == pumpauto.pack(5, True)
    # the borrow signature is still the default: 0x00FF cannot come from an arrow
    assert pumpauto.clamp(0x00FF) == pumpauto.pack(30, False)


def test_clamp_leaves_a_zero_minute_byte_ALONE_at_the_lowered_floor():
    """The v15 word, read by a v20 build: 0 is the disabled setting, so it needs no correction
    at all -- and the Pump Link flag still rides through untouched, which is the part both the
    v15 fix and this one are about."""
    from fireflymcf import pumpauto
    assert pumpauto.clamp(0x0000, minutes_min=0) is None
    assert pumpauto.clamp(0x0100, minutes_min=0) is None
    assert pumpauto.clamp(0x00FF, minutes_min=0) == pumpauto.pack(30, False)


def test_clamp_undoes_the_borrow_a_minus_press_at_zero_leaves():
    """v20. With the floor at 0, `-` subtracts 5 from a word whose low byte is already 0 and
    borrows out of it -- into the Pump Link flag. Both fields must come back exactly, because
    the alternative is a press that jumps the setting to an hour and silently clears the link.

    `0x0000 - 5 = 0xFFFB` (link off) and `0x0100 - 5 = 0x00FB` (link on) are the only two words
    the arrows can put this byte in, and `BORROW_MINUTES` is unreachable from the grid any
    other way, so adding the step back is a recovery rather than a guess."""
    from fireflymcf import pumpauto
    z = dict(minutes_min=0)
    assert pumpauto.clamp(0xFFFB, **z) == pumpauto.pack(0, False, 0)   # link off, word wrapped
    assert pumpauto.clamp(0x00FB, **z) == pumpauto.pack(0, True, 0)    # link on, flag borrowed
    # and it is a real correction, not a no-op: the stored word is not the recovered one
    assert pumpauto.clamp(0xFFFB, **z) != 0xFFFB and pumpauto.clamp(0x00FB, **z) != 0x00FB
    # at the SHIPPED floor the rule is off entirely -- that is what keeps v14-v19 reproducible
    assert pumpauto.clamp(0x00FB) == pumpauto.pack(60, False)


def test_clamp_rounds_off_grid_minutes_to_nearest_ties_up():
    assert pumpauto.clamp(0x0007) == pumpauto.pack(5, False)     # 7 -> 5
    assert pumpauto.clamp(0x0008) == pumpauto.pack(10, False)    # 8 -> 10 (tie up)
    assert pumpauto.clamp(0x0111) == pumpauto.pack(15, True)     # 17 -> 15, flag kept


def test_clamp_normalises_a_nonzero_flag_byte_to_one():
    assert pumpauto.clamp(0x021E) == pumpauto.pack(30, True)


# ---------------------------------------------------------------------------
# Task 8a: the scratch-space allocation for the per-tick Auto Off routine.
#
# Measured on v12, and it is tighter than the design assumed: 15 spare +0x44BC8
# cells, of which exactly TWO pass the GuiCode-reference safety scan as a whole
# 2-aligned halfword (215 -> 0x20039F5E, 217 -> 0x20039F58), and NO cell offers a
# safe 4-aligned word. So the routine cannot hold a u32 millisecond deadline.
#
# The resolution (spec Sec 3.4a):
#   * the EDGE LATCH is eliminated -- the countdown is its own edge memory;
#   * the deadline becomes a u16 SECONDS countdown (3600 fits a u16, 3_600_000 ms
#     does not), driven by a one-byte coarse-tick anchor;
#   * the anchor lives in the display cell's HIGH byte, which is free because the
#     readout is variable type 1 and type 1 is a SINGLE-BYTE load.
#
# Type 1 = ldrb is proven here from the tbh dispatch of FUN_00251E38.
# ---------------------------------------------------------------------------

import struct


# The allocation this spike decided. Every byte of every span is asserted safe below.
SCRATCH_SPANS = {
    "display_minutes": (0x20039F5E, 1),   # spare cell index 215, variable type 1 (ldrb)
    "tick_anchor":     (0x20039F5F, 1),   # the byte the type-1 readout does NOT read
    "seconds_countdown": (0x20039F58, 2),  # spare cell index 217, 2-aligned halfword
}

# FUN_00251E38, the value formatter. `tbh [pc, r1, lsl #1]` at 0x00251E58 with the
# table based at 0x00251E5C; r1 is the variable type, range-checked `cmp r1,#0xa`.
FORMATTER = 0x00251E38
TBH = 0x00251E58
TBH_TABLE = 0x00251E5C
FORMATTER_RETURN = 0x002520CE          # mov r0,r4 ; vpop ; pop  -- the numeric sink

# FUN_002521C8, the glyph/digit renderer. The call site hands it the type code a SECOND
# time (`ldrb.w r1,[r3,#0x91]` at 0x00256F6C, between `bl 0x251E38` and `bl 0x2521C8`),
# so the shared formatter return sink alone does NOT prove type 1 renders like type 3 --
# the renderer could have distinguished them. Its own dispatch is what settles it.
RENDER_TBB = 0x002521E2                # tbb [pc, r8]  (r8 = the variable type)
RENDER_TBB_TABLE = 0x002521E6
RENDER_COMMON = 0x00252242             # every arm falls through here
RENDER_ARMS = {
    0: 0x002521F2,   # cmp r6,#0xff / cmp r6,#0        -- unsigned BYTE clamp
    1: 0x002521F2,   # the SAME arm as type 0
    2: 0x00252200,
    3: 0x00252214,   # cmp.w r6,#0x10000 / cmp r6,#0   -- the identical clamp, 16-bit
    4: 0x00252224,
    5: 0x002522A4,
    6: 0x0025223A,
    7: 0x0025223A,
    8: 0x0025223A,
    9: 0x002522A4,
    10: 0x00252294,
}
TYPE_ARMS = {
    0: 0x00251E90,   # ldrb r3,[r5] ; cmp r3,#0        -> BOOL (collapses to 0/1)
    1: 0x00251E9C,   # ldrb r4,[r5]                    -> u8, the full 0..255 value
    2: 0x00251EA0,   # ldrsb.w r4,[r5]                 -> s8
    3: 0x00251EA6,   # ldrh r4,[r5]                    -> u16 (Time at Stop Volts)
    4: 0x00251EAA,   # ldrsh.w r4,[r5]                 -> s16
    5: 0x00251EB0,   # ldr r4,[r5]
    6: 0x00251EB4,   # ldr r4,[r5]
    7: 0x00251EBC,   # vldr s16,[r5]                   -> float, decimal-place path
    8: 0x00251FB4,   # ldrd r6,r7,[r5]                 -> 64-bit
    9: 0x002520D8,   # movs r4,#0                      -- same sink as the range reject
    10: 0x00251EB8,  # ldr r4,[r5]                     -> colour cell (Finding 18)
}


def _guicode(data):
    from fireflymcf import theme
    sec = theme.guicode_section(data)
    return data[sec.offset:sec.offset + sec.length], theme.guicode_base(sec)


def test_the_display_cell_and_the_tick_anchor_are_the_two_halves_of_one_cell():
    """The anchor is only free because it is the high byte of the display cell, and the
    display cell is read one byte at a time. Pin that adjacency so a future edit cannot
    silently move one without the other."""
    disp = SCRATCH_SPANS["display_minutes"][0]
    assert SCRATCH_SPANS["tick_anchor"][0] == disp + 1


# --- the type dispatch of FUN_00251E38 (13.6, byte-identical on v12) ---------

def test_value_formatter_dispatch_table_is_the_ten_way_switch(mcf_136):
    """`tbh [pc, r1, lsl #1]` at 0x00251E58 over an 11-entry halfword table based at
    0x00251E5C, guarded by `cmp r1,#0xa` / `bhi.w`. Each entry is a halfword count of
    instruction halfwords from the table base."""
    section, base = _guicode(mcf_136)
    assert section[TBH - base:TBH - base + 4] == bytes.fromhex("dfe811f0")  # tbh [pc,r1,lsl#1]
    for vtype, arm in TYPE_ARMS.items():
        entry = struct.unpack_from("<H", section, TBH_TABLE - base + vtype * 2)[0]
        assert TBH_TABLE + 2 * entry == arm, f"type {vtype} arm moved"


def test_variable_type_1_is_a_single_byte_load_that_returns_the_raw_value(mcf_136):
    """Route 1's load-bearing fact. Type 1's arm at 0x00251E9C is `ldrb r4,[r5]`
    (bytes `2c 78`) followed by `b 0x2520CE` (`16 e1`) -- the SAME return sink the u16
    type 3 uses -- so the formatter hands the caller the full 0..255 byte, exactly the
    way it hands over a halfword. It reads ONE byte, so the cell's high byte is free."""
    from capstone import CS_ARCH_ARM, CS_MODE_THUMB, Cs

    section, base = _guicode(mcf_136)
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)

    arm = TYPE_ARMS[1]
    assert section[arm - base:arm - base + 4] == bytes.fromhex("2c7816e1")
    ins = list(md.disasm(section[arm - base:arm - base + 4], arm))
    assert (ins[0].mnemonic, ins[0].op_str) == ("ldrb", "r4, [r5]")
    assert ins[1].mnemonic == "b" and int(ins[1].op_str.lstrip("#"), 16) == FORMATTER_RETURN

    # the sink itself: r0 = r4, then pop. No bool collapse, no formatting.
    assert section[FORMATTER_RETURN - base:FORMATTER_RETURN - base + 2] == bytes.fromhex("2046")

    # type 3, the u16 the stock `Time at Stop Volts` readout uses, reaches the same sink.
    three = TYPE_ARMS[3]
    assert section[three - base:three - base + 4] == bytes.fromhex("2c8811e1")
    i3 = list(md.disasm(section[three - base:three - base + 4], three))
    assert (i3[0].mnemonic, i3[0].op_str) == ("ldrh", "r4, [r5]")
    assert int(i3[1].op_str.lstrip("#"), 16) == FORMATTER_RETURN


def test_variable_type_0_is_the_boolean_and_type_1_is_not(mcf_136):
    """Type 0 also loads a byte, but immediately compares it to zero and returns a
    literal 0 or 1 (`movs r4,#1` at 0x002520DC / `movs r4,#0` at 0x00251E98). That is the
    bool. Type 1 does none of it -- which is why the readout can show 5..60, not 0/1."""
    from capstone import CS_ARCH_ARM, CS_MODE_THUMB, Cs

    section, base = _guicode(mcf_136)
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    arm = TYPE_ARMS[0]
    ins = list(md.disasm(section[arm - base:arm - base + 8], arm))
    assert (ins[0].mnemonic, ins[0].op_str) == ("ldrb", "r3, [r5]")
    assert (ins[1].mnemonic, ins[1].op_str) == ("cmp", "r3, #0")
    assert ins[2].mnemonic == "bne.w"
    assert section[0x002520DC - base:0x002520DC - base + 2] == bytes.fromhex("0124")  # movs r4,#1


def test_stock_byte_typed_readouts_already_render_decimal_numbers(mcf_136):
    """The data-side proof that a type-1 cell renders as a NUMBER, not a bool: 13.6 has
    127 wtype-8 (single-line numeric text) records reading type-1 slots, including
      * object 13 record 4, slot 13 -> 0x20039A6E: the CLEANING MODE
        'WILL REVERT ... IN __ SECONDS' countdown, and
      * object 16 records 15/18, slots 16/17 -> the AES 'Quiet Time Start' HH:MM pair,
        a stepper of exactly the shape the Auto Off control clones.
    A bool cell could not render either."""
    from fireflymcf import guidata, layout, records

    gs, _ = guidata.guidata_extent(mcf_136)
    table = guidata.object_tables(mcf_136)[1]

    def rec(obj, j):
        return records.object_records(mcf_136, gs + table[obj])[j]

    cleaning = rec(13, 4)
    assert cleaning.wtype == 8 and cleaning.value_slot == 13
    assert layout.variable_register(mcf_136, 13) == (0x20039A6E, 1)

    for j, slot in ((15, 16), (18, 17)):
        r = rec(16, j)
        assert r.wtype == 8 and r.value_slot == slot
        assert layout.variable_register(mcf_136, slot)[1] == 1

    n = 0
    for obj_off in table:
        if not obj_off:
            continue
        try:
            recs = records.object_records(mcf_136, gs + obj_off)
        except Exception:
            continue
        for r in recs:
            if r.wtype == 8 and r.value_slot is not None:
                try:
                    if layout.variable_register(mcf_136, r.value_slot)[1] == 1:
                        n += 1
                except Exception:
                    pass
    assert n == 127, f"count of byte-typed numeric readouts changed: {n}"


def test_the_renderer_clamps_by_type_and_treats_type_1_exactly_like_type_0(mcf_136):
    """The real proof that a type-1 cell renders as a number, and the reason the shared
    formatter return sink was not enough on its own: `FUN_002521C8` receives the type code
    independently (`ldrb.w r1,[r3,#0x91]` at 0x00256F6C) and dispatches on it AGAIN --
    `tbb [pc, r8]` at 0x002521E2 over the byte table at 0x002521E6. Types 0 and 1 land on
    the SAME arm (a plain unsigned-byte range clamp) and type 3 on an identical clamp
    against 0x10000; every arm falls through to the common formatting at 0x00252242 and the
    type register r8 is never referenced again. So the type buys a range clamp and nothing
    else, and type 0's bool-ness is the FORMATTER's doing, not the renderer's."""
    from capstone import CS_ARCH_ARM, CS_MODE_THUMB, Cs

    section, base = _guicode(mcf_136)
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)

    # the call site really does re-read the type between the two calls
    call = section[0x00256F64 - base:0x00256F74 - base]
    site = list(md.disasm(call, 0x00256F64))
    assert site[0].mnemonic == "bl" and int(site[0].op_str.lstrip("#"), 16) == FORMATTER
    assert (site[3].mnemonic, site[3].op_str) == ("ldrb.w", "r1, [r3, #0x91]")
    assert site[4].mnemonic == "bl" and int(site[4].op_str.lstrip("#"), 16) == 0x002521C8

    # the dispatch itself, decoded from the bytes
    assert section[RENDER_TBB - base:RENDER_TBB - base + 4] == bytes.fromhex("dfe808f0")
    table = section[RENDER_TBB_TABLE - base:RENDER_TBB_TABLE - base + 11]
    assert table == bytes.fromhex("06060d171f5f2a2a2a5f57")
    for vtype, arm in RENDER_ARMS.items():
        assert RENDER_TBB_TABLE + 2 * table[vtype] == arm, f"render arm for type {vtype} moved"

    # the load-bearing relation: 0 and 1 share an arm, and it is not type 3's
    assert RENDER_ARMS[0] == RENDER_ARMS[1]
    assert RENDER_ARMS[1] != RENDER_ARMS[3]

    # both arms are the same shape -- a range clamp ending in the common formatting
    byte_clamp = list(md.disasm(section[RENDER_ARMS[1] - base:RENDER_ARMS[1] - base + 14],
                                RENDER_ARMS[1]))
    assert (byte_clamp[0].mnemonic, byte_clamp[0].op_str) == ("cmp", "r6, #0xff")
    assert byte_clamp[-1].mnemonic == "b"
    assert int(byte_clamp[-1].op_str.lstrip("#"), 16) == RENDER_COMMON

    half_clamp = list(md.disasm(section[RENDER_ARMS[3] - base:RENDER_ARMS[3] - base + 16],
                                RENDER_ARMS[3]))
    assert (half_clamp[0].mnemonic, half_clamp[0].op_str) == ("cmp.w", "r6, #0x10000")
    assert half_clamp[-1].mnemonic == "b"
    assert int(half_clamp[-1].op_str.lstrip("#"), 16) == RENDER_COMMON

    # and the type is dead after the dispatch: r8 appears only in the prologue push, the
    # `mov r8,r1`, the range check and the tbb -- across the whole function, both alignments.
    end = base + section.index(bytes.fromhex("bde8f08f"), 0x002521C8 - base) + 4
    mentions = {}
    for align in (0, 2):
        for ins in md.disasm(section[0x002521C8 - base + align:end - base],
                             0x002521C8 + align):
            if "r8" in (ins.op_str or ""):
                mentions.setdefault(ins.address, ins.mnemonic)
    assert sorted(mentions) == [0x002521C8, 0x002521D0, RENDER_TBB - 6, RENDER_TBB]
    assert mentions[RENDER_TBB] == "tbb"


# ---------------------------------------------------------------------------
# Task 8b: the per-tick routine -- clamp, seed, display, timer.
#
# Everything below derives its allocation from the PRE-APPEND image (V12). That is
# not a convenience: `_guicode_references` is a literal scan, so the moment this
# routine reaches those cells through its own pool words they become "referenced"
# by the routine itself. The allocation is computed once, on the input.
# ---------------------------------------------------------------------------

                              # apart from its own pool, which is built from this address.


# ---------------------------------------------------------------------------
# Task 8b, the behaviour gate: EXECUTE the emitted bytes.
#
# `assert_disassembles_to` proves each instruction says what the builder claimed. It does
# not prove the routine DOES the right thing. There is no emulator in this project's
# dependency set, so the check below is a ~200-line interpreter for exactly the instruction
# forms this routine emits, driven by CAPSTONE's decode of the built blob (`build`'s returned
# disassembly) rather than by re-reading this project's own encoders -- so an encoding error
# surfaces as an unsupported form or wrong behaviour, never as agreement by construction.
#
# It models the flags as unsigned compares, the stack (push/pop move sp, so the AAPCS
# 8-byte alignment at every `blx` is checked for real), and treats each `blx` as the host CAN
# sender: it captures the 12 bytes at r0 and then clobbers r0-r3 and lr, which is what an
# AAPCS callee is allowed to do. r4-r7 are asserted unchanged at exit.
# ---------------------------------------------------------------------------


def test_r4_really_is_the_guicode_state_base_at_the_hook(mcf_136):
    """The one register the routine READS without owning. `ldr.w r0,[r4,#0x204]` is only the
    millisecond clock if r4 is the GuiCode state pointer when the detoured `bl` executes, so
    read it out of the caller rather than assume it: at GuiCode+0xEE32, three instructions
    before the hook, the caller does `mov r4, r0` (r0 is the state pointer -- FUN_00247BF8's
    own prologue does the identical `mov r4, r0` and then reads `[r4,#0x214]`), and the
    instruction AFTER the hook reads `[r4,#0x219]`, so r4 is live across the call."""
    from capstone import CS_ARCH_ARM, CS_MODE_THUMB, Cs
    from fireflymcf import followchannel, theme

    section, base = _guicode(mcf_136)
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    hook = theme.GUICODE_BASE_DEFAULT + followchannel.HOOK_OFF
    pre = list(md.disasm(section[hook - 10 - base:hook - base + 8], hook - 10))
    texts = [f"{i.mnemonic} {i.op_str}".strip() for i in pre]
    assert texts[0] == "mov r4, r0", texts
    assert texts[-2] == f"bl #{followchannel.TAIL_TARGET:#x}", texts
    assert texts[-1] == "ldrb.w r3, [r4, #0x219]", texts
    # the callee agrees about what r0 is
    callee = [f"{i.mnemonic} {i.op_str}".strip()
              for i in md.disasm(section[followchannel.TAIL_TARGET - base:
                                         followchannel.TAIL_TARGET - base + 0x24],
                                 followchannel.TAIL_TARGET)]
    assert callee[0] == "push {r4, r5, r6, r7, lr}"
    assert callee[2] == "mov r4, r0"
    assert "ldr.w r3, [r4, #0x214]" in callee


def test_the_clock_at_0x204_counts_milliseconds(mcf_136):
    """And the unit: the CLEAR EEPROM hold reads `[r4,#0x204]` and arms its 3-second deadline
    with `addw r3, r3, #0xbb8` -- 3000. So `>> 10` is 1.024-second units, which is why the
    countdown is in seconds and why a 60-minute setting expires at ~61.4 minutes."""
    from capstone import CS_ARCH_ARM, CS_MODE_THUMB, Cs

    section, base = _guicode(mcf_136)
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    ins = [f"{i.mnemonic} {i.op_str}".strip()
           for i in md.disasm(section[0x0024EC48 - base:0x0024EC58 - base], 0x0024EC48)]
    assert ins[0] == "ldr.w r3, [r4, #0x204]"
    assert ins[-1] == "addw r3, r3, #0xbb8"
    assert 0xBB8 == 3000
