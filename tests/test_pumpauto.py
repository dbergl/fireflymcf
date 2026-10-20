"""The 0xEE packed register: minutes in the LOW byte, link flag in the HIGH byte.

The layout is forced by hardware: BUS-BASELINE.md PC.47 showed the G12 stores this
selector as a raw u16 and BORROWS across the byte boundary (0x0500 - 1 = 0x04FF), so
the field that must survive a borrow is the one the user set.
"""

import pytest

from tests.thumbsim import Sim as _Sim
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

import hashlib
import json
import struct
from pathlib import Path

from tests.conftest import compiled_image

REPO = Path(__file__).resolve().parent.parent
STOCK = REPO / "firefly-13.6" / "mcf.bin"
PROJECT = REPO / "builds" / "golden-v22-anchor.ffproj.json"

# The INPUT image the allocation is derived from -- v12, the build currently on the coach,
# which is the state BEFORE the Auto Off routine is appended. This matters:
# `_guicode_references` is a literal scan, so once 8b's routine reaches these cells through
# `ldr rX,[pc,#imm]` pool words they become GuiCode-referenced BY THE ROUTINE ITSELF and
# `scan_display_cell` will move on to another cell. That is correct (a claimed cell is no
# longer spare), but it means the allocation must never be re-derived from the append's own
# output -- deriving it from today's FULL recipe would silently produce a different and
# unvalidated allocation. See spec Sec 3.4a.
#
# So `_v12()` RECONSTRUCTS that image in-test from tracked inputs: `golden-v22-anchor.ffproj.json`
# compiled against `firefly-13.6/mcf.bin` with the post-v12 ops filtered out, the same
# technique `tests/test_registers.py` uses for its v9 oracle. It used to read
# `build/mcf-experiment-v12.bin`, which is gitignored, untracked and produced by no script or
# recipe in this tree, behind a `pytest.skip` -- so on a clean checkout 28 of the tests in
# this file silently vanished, including the sentinel-disarm gate for the bug that could send
# an unsolicited pump-off command to a moving vehicle.

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


# --- the pre-append image, reconstructed from tracked inputs -----------------------------

# The ops that postdate v12 and must therefore be OUT of the reconstruction. Recipe order:
#
#   `pin125`  (pin_filter_instance)  -- v13, the `1FFC6` receive-filter pin. Not on the coach
#       and not part of the append, but v12 is the image the spike measured, so it is dropped
#       to reconstruct exactly that image rather than an approximation of it.
#   `pumprow-switch` / `pumprow-label` / `pumprow-touch` (move_element) -- Task 11 / v14: they
#       park the old Pump Link OPTIONS row off-canvas because the Auto Off feature moves that
#       control to the AES Water Pump section. They exist only because the routine exists.
#   `pumpauto` (add_pump_auto_off) -- THE APPEND ITSELF. This is the one that must not be in
#       the image the allocation is scanned from: its pool words are what would make cells 215
#       and 217 look GuiCode-referenced and push `scan_display_cell` onto different cells.
#
#   `navslides` (show_nav_slides_icon) -- v15, added on main after this feature branched. It has
#       nothing to do with the Auto Off routine; it is here because the sha gate below caught it
#       leaking into the pre-append image the first time the two lines were rebased together.
#       That is this blocklist's known weakness working as designed: it fails OPEN, so the gate,
#       not the list, is what notices. Any future op lands here the same way.
#   `minutes-home` (add_register_readout) -- v17, Task 6 of the register-readout plan. Same
#       story as `navslides`: unrelated to the Auto Off routine, and the sha gate below is what
#       caught it leaking into the pre-append image when this op landed at the end of the recipe.
#
# `aesswap` and `aeslowvolts` stay in: both predate the routine (v10, HARDWARE-VERIFIED) and
# both are in v12. `invhold` likewise.
#   `no-tank-bcast` (suppress_tank_status) -- v22. Same story again, and the sha gate caught it
#       the same way. It also RENAMED the build, which the op filter cannot undo, so `_v12`
#       restores `build_name` as well: the name lands in the header at 0x24 and moves the header
#       checksum, and a reconstruction carrying v22's name is not v12 no matter which ops ran.
POST_V12_OPS = ("pin125", "pumprow-switch", "pumprow-label", "pumprow-touch", "pumpauto",
                "navslides", "minutes-home", "rows-permanent", "absorb-rows", "float-rows",
                "shore-brkr-minus", "timer-glyph", "pump-timer-mark", "no-tank-bcast")
# The build name as it stood through v21. See the note above.
PRE_V22_BUILD_NAME = "Jayco B-Van V13 (Experimental)"

# Two ops that ARE in v12 gained params for v14, which must be reset the way test_registers.py
# resets `pumplink`'s for the v9 oracle:
#   `pumplink`.gate_mask / gate_on_value -- added for v14 (PC.47) so the follower's gate
#       survives the 0xEE slot widening that `pumpauto` performs. v12 gates on the whole
#       halfword, unmasked.
#   `aesswap`.pump_section -- `{"ref": "pumpauto"}`, the handle the Water Pump section reads.
#       It cannot survive the drop of `pumpauto` (the compile refuses BAD_REF), and v12's AES
#       page carries no Water Pump section at all.
#   `aesswap`.off_at_zero -- added for v20; it needs `pump_section` too (there is no Auto Off
#       readout without the Water Pump section), so it goes with the pair above.
POST_V12_PARAMS = {"pumplink": ("gate_mask", "gate_on_value"),
                   "aesswap": ("pump_section", "link_indicator", "off_at_zero")}
# v16 (Task 5) added `link_indicator` to `aesswap`, wiring the Pump Link switch to the
# `pumplink` op's own display cell; it needs `pump_section` (both are stripped together here,
# or `swap_aes_electrical.validate` raises BAD_PARAMS -- `link_indicator needs pump_section` --
# on the pre-v12 reconstruction, which carries neither).

# The sha256 the coach image v12 was recorded with is pinned below as `V12_SHA256`, and ASSERTED
# in `_v12()` alongside the allocation gate.
#
# The allocation gate alone is not enough, because `POST_V12_OPS` is a BLOCKLIST and so fails
# open: every op added to the recipe in future is silently *included* in this "pre-append"
# image, and the gate does not catch that. Measured -- keeping `pin125` in yields v13's bytes
# (43c6677b...) and keeping `pumplink`'s v14 mask params yields a 12-byte-longer image
# (c519f7d0...), and the gate passes on BOTH. It proves the routine is absent; it does not
# prove the image is v12, and "the recipe grew an op" is the drift that will actually happen.
#
# If a future recipe or tools change legitimately moves what an earlier op emits (as v10's pill
# shift did), this pin breaks -- which is the point: the reconstruction has stopped being the
# coach image, 28 safety tests are about to re-point at something else, and a human must decide
# rather than find out silently. `test_registers.py` accepts the same cost for v9 and v14.
V12_SHA256 = "4cb8ad1230e76f603a0da35b472634fd1fbe5d453341366018d7ee342a87e67c"

_PRE_APPEND: list = []


def _guicode(data):
    from fireflymcf import theme
    sec = theme.guicode_section(data)
    return data[sec.offset:sec.offset + sec.length], theme.guicode_base(sec)


def _assert_pre_append_allocation(data):
    """THE GATE ON THE RECONSTRUCTION. Every test below inherits it, because `_v12()` runs it
    before it will hand the image out.

    A reconstruction that quietly produced a *different but plausible* image would re-derive
    some other allocation and then cheerfully verify that -- strictly worse than the skip this
    replaces, because it would look green. The `V12_SHA256` pin beside this call is the first
    line of defence; this is the second, and it is the one that states the property in the terms
    the tests below use. The image is only accepted if it reproduces the allocation Task 8a
    settled from hardware evidence: 15 spare +0x44BC8 cells, of which
    exactly index 215 -> 0x20039F5E and index 217 -> 0x20039F58 pass the GuiCode-reference scan
    as a whole 2-aligned halfword, and 215 is already variable type 1 (the single-byte load
    that leaves its high byte free for the tick anchor).
    """
    from fireflymcf import followchannel, guidata, layout, statusbind

    display = (215, SCRATCH_SPANS["display_minutes"][0])
    countdown = (217, SCRATCH_SPANS["seconds_countdown"][0])

    gs, _ = guidata.guidata_extent(data)
    section, base = _guicode(data)

    def safe(addr):
        return (followchannel.SRAM_LOW <= addr < followchannel.SRAM_HIGH
                and not followchannel._guicode_references(section, base, addr))

    spares = statusbind.spare_cell_indices(data)
    ok = []
    for idx in spares:
        cand = struct.unpack_from("<I", data, gs + statusbind.CELL_TABLE_OFF + idx * 4)[0]
        if cand % 2 == 0 and safe(cand) and safe(cand + 1):
            ok.append((idx, cand))

    detail = (f"got {len(spares)} spare cells and safe halfwords "
              f"{[(i, hex(a)) for i, a in ok]}; expected 15 and "
              f"{[(display[0], hex(display[1])), (countdown[0], hex(countdown[1]))]}. "
              "The RECONSTRUCTION is what is wrong -- do not adjust the documented constants "
              "to match it; they are the outcome of a hardware-evidence spike (spec Sec 3.4a). "
              "The usual cause is an op that appends code or claims a cell leaking into "
              "POST_V12_OPS' complement.")
    assert len(spares) == 15, "reconstructed image is not the pre-append build: " + detail
    assert ok == [display, countdown], \
        "reconstructed image does not reproduce the Task 8a allocation: " + detail
    assert followchannel.scan_display_cell(data) == display, \
        "scan_display_cell disagrees with the documented display cell: " + detail
    assert layout.variable_register(data, display[0]) == (display[1], 1), \
        "the display cell is not already the single-byte type: " + detail


def _v12():
    """v12 -- the coach image, before the Auto Off routine is appended -- compiled from
    tracked inputs, pinned to v12's recorded bytes, and gated on the allocation it is used to
    verify. The two checks catch different things: the sha says this really is the coach image
    (which the allocation gate cannot, `POST_V12_OPS` being a blocklist), and the allocation
    gate says what these tests actually depend on still holds.

    Cached per process here, and across processes by `conftest.compiled_image`, so a 16-way
    `pytest -n auto` run compiles this image once rather than once per worker. The local cache
    is populated only AFTER the gate passes, so a bad reconstruction can never be handed out.
    """
    if not _PRE_APPEND:
        doc = json.loads(PROJECT.read_text())
        doc["build_name"] = PRE_V22_BUILD_NAME
        doc["ops"] = [o for o in doc["ops"] if o["id"] not in POST_V12_OPS]
        for op in doc["ops"]:
            for name in POST_V12_PARAMS.get(op["id"], ()):
                op["params"].pop(name, None)
        data = compiled_image(json.dumps(doc), STOCK.read_bytes()).data
        assert hashlib.sha256(data).hexdigest() == V12_SHA256, (
            "the reconstruction is no longer the coach image: it does not match v12's recorded "
            f"sha256 {V12_SHA256} (got "
            f"{hashlib.sha256(data).hexdigest()}, {len(data)} bytes). POST_V12_OPS is a "
            "blocklist, so the usual cause is a new recipe op that this filter does not know to "
            "drop and that therefore leaked into the pre-append image; the other cause is a "
            "tools change that moved what an earlier op emits. Either way a human must decide "
            "which image these tests should be derived from -- do NOT just update this constant "
            "to whatever came out.")
        _assert_pre_append_allocation(data)
        _PRE_APPEND.append(data)
    return _PRE_APPEND[0]


def test_pump_auto_scratch_allocation_is_safe_and_distinct():
    """THE GATE. Every byte of every scratch span must be in SRAM and unreferenced by
    GuiCode -- the same discipline that excludes the trap cell 203, which is
    widget-spare but is live working memory for FUN_00247BF8, the very per-tick function
    this routine chains onto."""
    from fireflymcf import followchannel

    data = _v12()
    section, base = _guicode(data)

    def safe(addr):
        return (followchannel.SRAM_LOW <= addr < followchannel.SRAM_HIGH
                and not followchannel._guicode_references(section, base, addr))

    seen = set()
    for name, (addr, width) in SCRATCH_SPANS.items():
        for i in range(width):
            byte = addr + i
            assert safe(byte), f"{name} byte {byte:#x} is GuiCode-referenced or out of SRAM"
            assert byte not in seen, f"{name} overlaps another span at {byte:#x}"
            seen.add(byte)
    assert SCRATCH_SPANS["seconds_countdown"][0] % 2 == 0, \
        "a halfword countdown must be 2-aligned (strh/ldrh)"
    assert len(seen) == sum(w for _, w in SCRATCH_SPANS.values())


def test_the_display_cell_and_the_tick_anchor_are_the_two_halves_of_one_cell():
    """The anchor is only free because it is the high byte of the display cell, and the
    display cell is read one byte at a time. Pin that adjacency so a future edit cannot
    silently move one without the other."""
    disp = SCRATCH_SPANS["display_minutes"][0]
    assert SCRATCH_SPANS["tick_anchor"][0] == disp + 1


def test_display_cell_is_the_one_scan_display_cell_picks_and_is_already_byte_typed():
    from fireflymcf import followchannel, layout

    data = _v12()
    idx, addr = followchannel.scan_display_cell(data)
    assert (idx, addr) == (215, SCRATCH_SPANS["display_minutes"][0])
    cell_addr, vtype = layout.variable_register(data, idx)
    assert cell_addr == addr
    assert vtype == 1, "slot 215 must already be the single-byte type; no retype needed"


def test_countdown_cell_is_the_second_and_last_safe_spare():
    """217 is the only other spare whose halfword is entirely safe. If this ever changes,
    the allocation must be re-derived rather than assumed."""
    from fireflymcf import followchannel, guidata, statusbind

    data = _v12()
    gs, _ = guidata.guidata_extent(data)
    section, base = _guicode(data)

    def safe(addr):
        return (followchannel.SRAM_LOW <= addr < followchannel.SRAM_HIGH
                and not followchannel._guicode_references(section, base, addr))

    spares = statusbind.spare_cell_indices(data)
    assert len(spares) == 15
    ok = []
    for idx in spares:
        cand = struct.unpack_from("<I", data, gs + statusbind.CELL_TABLE_OFF + idx * 4)[0]
        if cand % 2 == 0 and safe(cand) and safe(cand + 1):
            ok.append((idx, cand))
    assert ok == [(215, 0x20039F5E), (217, 0x20039F58)]
    assert (217, SCRATCH_SPANS["seconds_countdown"][0]) in ok


def test_no_spare_cell_offers_a_safe_four_aligned_word():
    """The measured negative that killed the u32 millisecond deadline and forced the
    seconds countdown. 0x20039F58 is 4-aligned but 0x20039F5A IS GuiCode-referenced."""
    from fireflymcf import followchannel, guidata, statusbind

    data = _v12()
    gs, _ = guidata.guidata_extent(data)
    section, base = _guicode(data)

    def safe(addr):
        return (followchannel.SRAM_LOW <= addr < followchannel.SRAM_HIGH
                and not followchannel._guicode_references(section, base, addr))

    for idx in statusbind.spare_cell_indices(data):
        cand = struct.unpack_from("<I", data, gs + statusbind.CELL_TABLE_OFF + idx * 4)[0]
        if cand % 4:
            continue
        assert not all(safe(cand + i) for i in range(4)), \
            f"cell {idx} at {cand:#x} IS a safe word -- the ms-deadline design is back on"
    # the specific byte that costs us the word at 0x20039F58
    assert not safe(0x20039F5A)


def test_no_other_variable_slot_reads_any_byte_of_the_scratch_spans():
    """`_guicode_references` only rules out CODE references. A second widget reading the
    same cell would be just as fatal, so sweep the whole 0x44BC8 / 0x4492C variable table
    (Finding 17) and confirm the only slots touching our bytes are 215 and 217 themselves."""
    from fireflymcf import guidata

    data = _v12()
    gs, _ = guidata.guidata_extent(data)
    dptr_off, type_off = guidata.find_variable_tables(data)
    widths = {0: 1, 1: 1, 2: 1, 3: 2, 4: 2, 5: 4, 6: 4, 7: 4, 8: 8, 9: 4, 10: 4}

    targets = set()
    for addr, width in SCRATCH_SPANS.values():
        targets.update(range(addr, addr + width))

    touching = {}
    slot = 0
    while True:
        addr = struct.unpack_from("<I", data, gs + dptr_off + slot * 4)[0]
        if addr and not (0x20000000 <= addr < 0x20080000):
            break
        vtype = data[gs + type_off + slot]
        if set(range(addr, addr + widths.get(vtype, 4))) & targets:
            touching[slot] = addr
        slot += 1
    assert slot == 666, f"variable table length changed ({slot}); re-derive the allocation"
    assert touching == {215: 0x20039F5E, 217: 0x20039F58}


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


def test_the_formatter_and_renderer_are_byte_identical_on_the_v12_build(mcf_136):
    """v12 appends routines; it touches neither the formatter nor the digit renderer. The
    type tables decided on 13.6 therefore apply to the image on the coach."""
    data = _v12()
    v12_section, v12_base = _guicode(data)
    stock_section, stock_base = _guicode(mcf_136)
    assert v12_base == stock_base
    # the formatter, and the renderer's dispatch plus every one of its arms
    for lo_addr, hi_addr in ((FORMATTER, FORMATTER_RETURN + 0x10),
                             (0x002521C8, 0x00252300)):
        lo, hi = lo_addr - stock_base, hi_addr - stock_base
        assert v12_section[lo:hi] == stock_section[lo:hi], f"{lo_addr:#x} differs on v12"

    # Across the renderer's FULL span the only differences are two literal-pool GuiData
    # pointers that v12's relocation moved (0x90074D70 -> 0x90074D00). No instruction of
    # FUN_002521C8 changed, so the type dispatch decided on 13.6 holds on the coach image.
    lo, hi = 0x002521C8 - stock_base, 0x00252B08 - stock_base
    differing = [0x002521C8 + i - lo for i in range(lo, hi)
                 if v12_section[i] != stock_section[i]]
    assert differing == [0x002527D0, 0x00252ABC]
    for addr in (0x002527D0 & ~3, 0x00252ABC & ~3):
        off = addr - stock_base
        assert struct.unpack_from("<I", stock_section, off)[0] == 0x90074D70
        assert struct.unpack_from("<I", v12_section, off)[0] == 0x90074D00


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

CODE_LOAD = 0x00259C00        # a plausible append point; the routine is position-independent
                              # apart from its own pool, which is built from this address.


def _regs(data):
    from fireflymcf import register
    cfg = register.resolve_register(data, dgn="DGN_CONFIG", selector=0xEE)
    pump = register.resolve_register(data, dgn="DC_DIMMER_STATUS_3", instance=44)
    return cfg, pump


def _plan(data=None, minutes_min=None):
    from fireflymcf import pumpauto
    data = _v12() if data is None else data
    cfg, pump = _regs(data)
    kw = {} if minutes_min is None else {"minutes_min": minutes_min}
    return pumpauto.resolve(data, config=cfg, pump=pump, **kw)


def test_resolve_picks_distinct_safe_cells():
    data = _v12()
    cfg, pump = _regs(data)
    plan = _plan(data)

    assert plan.config_slot == cfg.slot_addr
    assert plan.pump_slot == pump.slot_addr
    # Task 8a's allocation: display byte, tick anchor byte, countdown halfword -- all
    # distinct, all in SRAM, and the countdown 2-aligned.
    cells = [plan.display_addr, plan.tick_anchor_addr, plan.countdown_addr]
    assert len(set(cells)) == 3
    for c in cells:
        assert 0x20030000 <= c < 0x20040000
    assert plan.countdown_addr % 2 == 0
    assert plan.countdown_addr + 1 != plan.display_addr, "the countdown must not overlap"
    assert plan.default_word == pumpauto.pack(30, False)


def test_resolve_matches_the_allocation_task_8a_settled():
    plan = _plan()
    assert plan.display_addr == SCRATCH_SPANS["display_minutes"][0]
    assert plan.tick_anchor_addr == SCRATCH_SPANS["tick_anchor"][0]
    assert plan.countdown_addr == SCRATCH_SPANS["seconds_countdown"][0]
    assert plan.tick_anchor_addr == plan.display_addr + 1


def test_resolve_refuses_a_guicode_live_cell():
    """The trap: cell 203 (0x2003A2F8) is loaded by the very per-tick function
    we hook, so 'widget-spare' is not enough -- the GuiCode-reference scan is."""
    plan = _plan()
    assert 0x2003A2F8 not in (plan.display_addr, plan.tick_anchor_addr,
                              plan.countdown_addr)


def test_build_round_trips_every_instruction():
    from fireflymcf import pumpauto
    plan = _plan()
    blob, disasm = pumpauto.build(plan, CODE_LOAD)
    assert len(blob) % 4 == 0
    assert disasm, "build must return the disassembly it verified"
    # the contract that makes chaining transparent
    text = " ; ".join(d for _a, d in disasm)
    assert text.startswith("push {r0, r1, r2, r3, lr}")
    assert "b.w" in text.split(";")[-1] or "b.w" in text
    assert "pop {pc}" not in text


def test_build_never_touches_r4_to_r7():
    """Every link in the chain must be transparent to the next; r4-r7 belong to the
    displaced paint function."""
    import re
    from fireflymcf import pumpauto
    plan = _plan()
    _blob, disasm = pumpauto.build(plan, CODE_LOAD)
    for _addr, line in disasm:
        # reading r4 as the state base is allowed; WRITING r4-r7 is not
        m = re.match(r"(movs?|str\w*|ldr\w*|adds?|subs?|ands?|muls|lsrs)\s+(r[4-7])\b", line)
        if m and m.group(1).startswith(("mov", "add", "sub", "and", "mul", "ldr", "lsr")):
            raise AssertionError(f"routine writes {m.group(2)}: {line}")
    # and the push/pop pair is the r0-r3+lr contract, nothing wider
    texts = [d for _a, d in disasm]
    assert texts[0] == "push {r0, r1, r2, r3, lr}"
    assert texts[-2] == "pop.w {r0, r1, r2, r3, lr}"
    assert texts[-1].startswith("b.w ")


def test_build_reads_the_millisecond_clock_at_0x204():
    from fireflymcf import pumpauto
    plan = _plan()
    _blob, disasm = pumpauto.build(plan, CODE_LOAD)
    assert any("[r4, #0x204]" in d for _a, d in disasm), \
        "the timer must count on the same ms clock every hold in this firmware uses"


def test_build_scales_minutes_to_seconds_with_muls():
    """Task 8a's unit decision: the countdown is minutes * 60 seconds, so 60 is an
    imm8 and the multiply is the new muls encoder -- not a 60000 pool word."""
    from fireflymcf import pumpauto
    plan = _plan()
    _blob, disasm = pumpauto.build(plan, CODE_LOAD)
    assert any(d.startswith("muls ") for _a, d in disasm)
    assert any("#0x3c" in d or "#60" in d for _a, d in disasm), "the 60 s/min factor"


def test_build_disarms_with_zero_not_a_sentinel():
    """The countdown IS the edge memory (Task 8a eliminated the latch): 0 means 'not
    running', so nothing may write 0xFFFF into it."""
    from fireflymcf import pumpauto
    plan = _plan()
    _blob, disasm = pumpauto.build(plan, CODE_LOAD)
    joined = " ; ".join(d for _a, d in disasm)
    assert "0xffff" not in joined.lower(), \
        "the countdown disarms with 0; a 0xFFFF sentinel would look like ~18 hours"


def test_build_carries_the_channel_44_off_template():
    from fireflymcf import followchannel, pumpauto
    plan = _plan()
    assert plan.off_template == followchannel._template(44, 0, 3)
    blob, _d = pumpauto.build(plan, CODE_LOAD)
    assert plan.off_template in blob, "the template must be in the appended blob"


def test_detour_edit_targets_the_current_hook():
    from fireflymcf import followchannel, pumpauto
    data = _v12()
    plan = _plan(data)
    hook_old, tail = followchannel.hook_state(data)
    assert plan.hook_old == hook_old
    assert plan.tail_target == tail, "the new link tail-calls what the hook reaches NOW"
    off, old, new = pumpauto.detour_edit(plan, CODE_LOAD)
    assert old == hook_old and len(new) == len(old)
    assert off == followchannel.HOOK_OFF
    text = thumb_disasm(new, 0x00240000 + off)
    assert text == [f"bl #{CODE_LOAD + pumpauto.ENTRY_OFF:#x}"]


def thumb_disasm(code, at):
    from fireflymcf import thumb
    return [t for _a, t in thumb.disassemble(code, at)]


def test_build_carries_the_config_write_and_readback_frames():
    """Steps 1 and 2 put a `D1` absolute write and its `D3` read-back on the bus. Both are
    Finding-7 frames in the pool's own wire order: a little-endian u32 CAN id followed by the
    8-byte payload. 13.6's own pool records are the shape reference --
    `00 d9 fe 19 | ff 96 cc 0f e8 03 d2 ea` (a D2 delta) and
    `00 d9 fe 19 | ff 96 cc 0f ff ff d3 ff` (its read-back)."""
    from fireflymcf import pumpauto
    data = _v12()
    plan = _plan(data)
    assert plan.write_template == bytes.fromhex("00d9fe19") + bytes(
        [0xFF, 0x96, 0xEE, 0x0F, 0x00, 0x00, 0xD1, 0xEA])
    assert plan.readback_template == bytes.fromhex("00d9fe19") + bytes(
        [0xFF, 0x96, 0xEE, 0x0F, 0xFF, 0xFF, 0xD3, 0xFF])
    # the shape is the image's own, not this module's invention
    assert bytes.fromhex("00d9fe19ff96cc0fffffd3ff") in data
    blob, _d = pumpauto.build(plan, CODE_LOAD)
    assert plan.write_template in blob and plan.readback_template in blob


def test_resolve_refuses_the_wrong_registers():
    from fireflymcf import pumpauto, register
    data = _v12()
    cfg, pump = _regs(data)
    with pytest.raises(pumpauto.PumpAutoError):
        pumpauto.resolve(data, config=pump, pump=pump)      # config must be DGN_CONFIG
    with pytest.raises(pumpauto.PumpAutoError):
        pumpauto.resolve(data, config=cfg, pump=cfg)        # pump must be a dimmer status


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


SIM_LOAD = 0x00300000        # where the sim maps the blob; nothing in the image is at stake


def _sim_setup(minutes_min=None):
    from fireflymcf import pumpauto
    plan = _plan(minutes_min=minutes_min)
    blob, disasm = pumpauto.build(plan, SIM_LOAD)
    return plan, blob, disasm


def _tick(plan, blob, disasm, *, word=0x001E, pump_on=False, countdown=0, anchor=0,
          display=0, clock=0):
    """Run the routine once and report every cell it could have touched."""
    s = _Sim(blob, SIM_LOAD, disasm)
    s.clock = clock
    s.wr(plan.config_slot, word, 2)
    s.wr(plan.pump_slot, 0x04 if pump_on else 0x40, 2)   # the firmware's own on/off bytes
    s.wr(plan.display_addr, display, 1)
    s.wr(plan.tick_anchor_addr, anchor, 1)
    s.wr(plan.countdown_addr, countdown, 2)
    s.run()
    assert s.tail == plan.tail_target, "the link must tail-branch to what the hook reaches"
    assert s.sp_at_exit == _Sim.STACK_TOP, "the routine leaked stack"
    assert s.regs[4:8] == [_Sim.STATE_BASE, 0, 0, 0], "the routine wrote r4-r7"
    return {"display": s.rd(plan.display_addr, 1), "anchor": s.rd(plan.tick_anchor_addr, 1),
            "countdown": s.rd(plan.countdown_addr, 2), "sent": s.sent}


def _write_frame(plan, value):
    return plan.write_template[:8] + struct.pack("<H", value) + plan.write_template[10:]


def test_a_zero_minute_count_never_arms_the_countdown():
    """v20's whole disabling mechanism, and it is arithmetic rather than a branch.

    `armit` stores `minutes * 60`, so a count of 0 arms to `DISARMED`. The countdown therefore
    never leaves 0, never decrements, and the pump-OFF frame -- which is only reachable from a
    decrement that lands on 0 -- can never be sent. Run with the pump ON and the clock advanced,
    the two conditions that arm and tick a live timer."""
    from fireflymcf import pumpauto
    plan, blob, disasm = _sim_setup(0)
    for clock in (0, 1 << 10, 5 << 10):
        out = _tick(plan, blob, disasm, word=0x0000, display=0, pump_on=True,
                    countdown=0, anchor=0xFF, clock=clock)
        assert out["countdown"] == 0, f"clock {clock}: a 0-minute setting armed the countdown"
        assert out["sent"] == [], f"clock {clock}: a disabled timer put a frame on the bus"
    # and with the link flag on, the same -- the flag must not look like a minute count
    out = _tick(plan, blob, disasm, word=0x0100, display=0, pump_on=True, countdown=0,
                anchor=0xFF, clock=1 << 10)
    assert out["countdown"] == 0 and out["sent"] == []
    # the contrast: a real 5-minute setting DOES arm, so the test above is not vacuous
    out = _tick(plan, blob, disasm, word=0x0005, display=5, pump_on=True, countdown=0,
                anchor=0xFF, clock=1 << 10)
    assert out["countdown"] == 5 * pumpauto.SECONDS_PER_MINUTE


def test_setting_zero_minutes_disarms_a_RUNNING_countdown():
    """v20 round 2, found on the coach. `armit` stores `minutes * 60`, which stops a timer that
    is not running from ever starting -- but `arm` re-reads the minute count only at `armit`, and
    `armit` is reached only when the countdown is already 0. So a countdown already running when
    the user stepped down to 0 kept counting, and would have fired and switched the pump off
    after the feature was disabled.

    The owner saw the mark stay lit across a page change with the setting at 0, which is not a
    repaint artifact: leaving HOME and returning repaints, so the countdown really was still live.
    """
    plan, blob, disasm = _sim_setup(0)
    out = _tick(plan, blob, disasm, word=0x0000, display=0, pump_on=True,
                countdown=240, anchor=0xFF, clock=1 << 10)
    assert out["countdown"] == 0, "a running countdown survived a 0-minute setting"
    assert out["sent"] == [], "disarming must put nothing on the bus"
    # it disarms on the very next tick, not only when the second rolls over
    out = _tick(plan, blob, disasm, word=0x0000, display=0, pump_on=True,
                countdown=240, anchor=0, clock=0)
    assert out["countdown"] == 0

    # and with Pump Link on, since the flag shares the word the minute count comes from
    out = _tick(plan, blob, disasm, word=0x0100, display=0, pump_on=True,
                countdown=240, anchor=0xFF, clock=1 << 10)
    assert out["countdown"] == 0 and out["sent"] == []

    # THE CONTRAST, so this is not vacuous: a real setting leaves a running countdown alone
    out = _tick(plan, blob, disasm, word=0x001E, display=30, pump_on=True,
                countdown=240, anchor=0xFF, clock=1 << 10)
    assert out["countdown"] == 239, "a live countdown must keep counting at a real setting"

    # ...and at the SHIPPED floor the branch does not exist at all, which is what keeps v14-v19
    # byte-identical. A 0 display byte there is a value the clamp corrects, not a disable.
    plan5, blob5, disasm5 = _sim_setup()
    out = _tick(plan5, blob5, disasm5, word=0x0005, display=5, pump_on=True,
                countdown=240, anchor=0xFF, clock=1 << 10)
    assert out["countdown"] == 239


@pytest.mark.parametrize("minutes_min", [None, 0])
def test_the_emitted_code_computes_exactly_the_python_clamp(minutes_min):
    """Every corruption `clamp()` knows how to recover from, executed through the real bytes.

    The curated words are the ones PC.47 and `clamp` care about; the swept ones cover every
    minute byte against the flag bytes that can actually occur (0/1, the 0xFF borrow, and a
    couple of foreign values). The FULL 65536-word sweep was run once against this same blob
    and matched on every word -- see the task report; it is 35 s, which is too slow to run on
    every commit, so the shape of the space is covered here instead."""
    import random
    from fireflymcf import pumpauto
    plan, blob, disasm = _sim_setup(minutes_min)
    floor = {} if minutes_min is None else {"minutes_min": minutes_min}

    words = {0x001E, 0x011E, 0x0105, 0x003C, 0x0500, 0x04FF, 0xFF1E, 0xFF05, 0x0002, 0x0050,
             0x0007, 0x0008, 0x0111, 0x021E, 0x0000, 0x00FF, 0xFF00}
    for flag in (0x00, 0x01, 0x02, 0x7F, 0xFE, 0xFF):
        words.update((flag << 8) | m for m in range(0x100))
    rng = random.Random(8)
    words.update(rng.randrange(0x10000) for _ in range(400))

    assert 0xFFFF in words, "the sentinel is IN the sweep now -- the two models agree on it"
    for w in sorted(words):
        out = _tick(plan, blob, disasm, word=w, display=0xAA)
        want = pumpauto.clamp(w, **floor)
        corrected = w if want is None else want
        # The readout is the one place the sentinel differs, and only because it is panel-local:
        # `clamp` has nothing to say about 0xFFFF, while the routine shows the default minute
        # count so the display is not boot garbage. Nothing leaves the panel either way.
        expect_display = pumpauto.MINUTES_DEFAULT if w == 0xFFFF else corrected & 0xFF
        assert out["display"] == expect_display, f"{w:#06x} displayed {out['display']:#x}"
        if want is None:
            assert out["sent"] == [], f"{w:#06x} is already valid; nothing may go on the bus"
        else:
            assert out["sent"] == [_write_frame(plan, want), plan.readback_template], \
                f"{w:#06x} must be corrected to {want:#06x} with a read-back"


def test_the_unset_slot_puts_nothing_on_the_bus():
    """Step 1, as ruled in fix round 1. 0xFFFF cannot mean "never written": the panel
    initialises every receive slot to 0xFFFF at boot and the G12 sweeps this selector only
    every ~5.0 s (BUS-BASELINE PC.42), so for up to five seconds of every power-up -- while
    this routine runs every paint tick -- "unset" and "not swept yet" are the same bytes. A
    write there would destroy the user's stored word (minutes AND the Pump Link flag, which
    share the selector). So the routine is SILENT on the bus and touches only PANEL-LOCAL
    cells: the default minute count on the readout, and 0 in the countdown."""
    plan, blob, disasm = _sim_setup()
    out = _tick(plan, blob, disasm, word=0xFFFF, pump_on=True, display=0xAA, anchor=0x5A,
                countdown=7, clock=99 << 10)
    assert out["sent"] == [], "nothing may go on the bus while the slot reads 0xFFFF"
    assert out["display"] == 30, "the readout shows the default minute count, not boot garbage"
    assert out["display"] == plan.default_word & 0xFF == pumpauto.MINUTES_DEFAULT
    assert out["countdown"] == 0, "the countdown is disarmed, not armed or decremented"
    assert out["anchor"] == 0x5A, "and the tick anchor is not advanced either"
    # ... with the pump OFF too, and from a disarmed timer: still nothing, still no arm
    quiet = _tick(plan, blob, disasm, word=0xFFFF, pump_on=False, display=0, countdown=0)
    assert quiet["sent"] == [] and quiet["countdown"] == 0 and quiet["display"] == 30


def test_the_sentinel_path_disarms_the_uninitialised_countdown():
    """The boot leg of the phantom-countdown mitigation, and the ONLY leg that runs.

    The countdown cell lives above `_ebss`, which the panel's C runtime never establishes
    (`raminit.py`), so at power-on it holds garbage. The arm step's impossible-value guard
    only catches garbage ABOVE `MINUTES_MAX * 60`; a phantom in [1, 3601] is indistinguishable
    from a live countdown, and a coach that boots with the pump already running would count it
    down and emit an unsolicited channel-44 OFF frame. It cannot be left to "any tick with the
    pump off zeroes the cell" either: the pump's status DGN repeats every 2.0 s while an output
    is on (BUS-BASELINE, DC_DIMMER_STATUS_3) while the config sweep is every ~5.0 s (PC.42), so
    by the time the config slot first goes valid the pump slot has very likely already been
    written with a real ON.

    So the sentinel path zeroes it. Safe because var 289's refresh class is 3 = "none" (byte 2
    is 0x2D after the widening, 0x1D before -- the class bits are untouched), so a populated
    slot never reverts to 0xFFFF: reading the sentinel means the G12 has not spoken since boot,
    and no legitimate countdown can be running."""
    plan, blob, disasm = _sim_setup()
    # every value the >3601 guard cannot reach, with the pump reading ON -- the exposure
    for phantom in (1, 2, 59, 600, 1799, 3599, 3600, 3601):
        out = _tick(plan, blob, disasm, word=0xFFFF, pump_on=True, countdown=phantom,
                    anchor=0x11, clock=7 << 10)
        assert out["countdown"] == 0, f"phantom {phantom} survived the sentinel tick"
        assert out["sent"] == [], f"phantom {phantom} put a frame on the bus"
    # and the countdown stays at 0 across the whole ~5 s sentinel window, so the first valid
    # sweep arms from a clean cell rather than resuming a phantom
    state = 2500
    for second in range(6):
        state = _tick(plan, blob, disasm, word=0xFFFF, pump_on=True, countdown=state,
                      anchor=second, clock=second << 10)["countdown"]
    assert state == 0


def test_a_never_configured_cell_bootstraps_from_the_users_first_touch():
    """The cost of never seeding, and the reason nobody should later "fix" it. From 0xFFFF the
    G12's own arithmetic produces a word that is NOT the sentinel -- the link toggle's +0x0100
    gives 0x00FF, an arrow's +5 gives 0x0004 -- so the clamp corrects it on the very next tick.
    One extra tap on a never-configured coach; no data-loss path anywhere."""
    plan, blob, disasm = _sim_setup()
    # the toggle's +0x0100 on 0xFFFF
    toggled = _tick(plan, blob, disasm, word=0x00FF)
    assert toggled["sent"] == [_write_frame(plan, pumpauto.pack(30, False)),
                               plan.readback_template]
    assert toggled["display"] == 30
    # an arrow's +5 on 0xFFFF
    stepped = _tick(plan, blob, disasm, word=0x0004)
    assert stepped["sent"] == [_write_frame(plan, pumpauto.pack(5, False)),
                               plan.readback_template]
    assert stepped["display"] == 5
    # and both corrections are words the clamp then leaves alone
    for settled in (pumpauto.pack(30, False), pumpauto.pack(5, False)):
        assert pumpauto.clamp(settled) is None
        assert _tick(plan, blob, disasm, word=settled)["sent"] == []


def test_no_frame_is_ever_authored_for_the_sentinel_path():
    """A structural guard beside the executed one: the only `blx` reachable without first
    passing the `cmp r3, r1` correction test is the timer's OFF frame, which needs an armed
    countdown -- and the sentinel path cannot arm one."""
    from fireflymcf import pumpauto as pa
    plan = _plan()
    _blob, disasm = pa.build(plan, CODE_LOAD)
    texts = [t for _a, t in disasm]
    assert texts.count("blx r2") == 3, "one write, one read-back, one OFF frame -- no more"


def test_the_countdown_arms_when_the_pump_is_on_and_disarms_when_it_goes_off():
    plan, blob, disasm = _sim_setup()
    armed = _tick(plan, blob, disasm, word=0x001E, pump_on=True, countdown=0)
    assert armed["countdown"] == 30 * 60, "30 minutes, in seconds"
    assert armed["sent"] == []
    # the link flag is irrelevant to the timer: Auto Off watches the pump, not the follower
    linked = _tick(plan, blob, disasm, word=0x0105, pump_on=True, countdown=0)
    assert linked["countdown"] == 5 * 60
    # armed, then the pump goes off: disarm, no frame
    off = _tick(plan, blob, disasm, word=0x001E, pump_on=False, countdown=1234)
    assert off["countdown"] == 0 and off["sent"] == []
    # already running with the pump on: untouched
    running = _tick(plan, blob, disasm, word=0x001E, pump_on=True, countdown=1234, anchor=0)
    assert running["countdown"] == 1234 and running["sent"] == []


def test_an_impossible_countdown_is_treated_as_power_on_garbage():
    """Nothing establishes the countdown cell's value at power-up -- `ldrh` reads whatever the
    panel left there -- and a nonzero one with the pump reading on would count a phantom
    interval down and send an unsolicited OFF frame at an arbitrary moment. `MINUTES_MAX * 60`
    is the largest value the arm step can ever write, so anything above it did not come from
    this routine: it is re-armed over, and no frame is emitted while that happens."""
    from fireflymcf import pumpauto as pa
    plan, blob, disasm = _sim_setup()
    ceiling = pa.MINUTES_MAX * pa.SECONDS_PER_MINUTE
    assert ceiling == 3600

    for garbage in (ceiling + 1, 0x4000, 0xBEEF, 0xFFFF):
        out = _tick(plan, blob, disasm, word=0x001E, pump_on=True, countdown=garbage)
        assert out["countdown"] == 30 * 60, f"{garbage:#06x} must be re-armed over"
        assert out["sent"] == [], f"{garbage:#06x} must not put a frame on the bus"
        # with the pump off it disarms, as any nonzero countdown already did
        off = _tick(plan, blob, disasm, word=0x001E, pump_on=False, countdown=garbage)
        assert off["countdown"] == 0 and off["sent"] == []

    # the boundary is inclusive on the legal side: a full 60-minute countdown is left alone
    legal = _tick(plan, blob, disasm, word=0x003C, pump_on=True, countdown=ceiling)
    assert legal["countdown"] == ceiling and legal["sent"] == []
    # and one second into it, still running
    running = _tick(plan, blob, disasm, word=0x003C, pump_on=True, countdown=ceiling - 1)
    assert running["countdown"] == ceiling - 1 and running["sent"] == []

    # the guard cannot manufacture a frame: even the tick that re-arms sends nothing, and the
    # decrement path can only fire from a value at or below the ceiling.
    assert _tick(plan, blob, disasm, word=0x001E, pump_on=True, countdown=1, anchor=0,
                 clock=1 << 10)["sent"] == [plan.off_template]


def test_the_countdown_advances_once_per_second_not_once_per_paint_tick():
    """Step 5. The anchor is the display cell's high byte and holds `(ms >> 10) & 0xFF`; a
    paint tick that lands inside the same 1.024 s advances nothing."""
    plan, blob, disasm = _sim_setup()
    clock = 100 << 10
    same = _tick(plan, blob, disasm, pump_on=True, countdown=600, anchor=100, clock=clock + 5)
    assert same["countdown"] == 600 and same["anchor"] == 100
    nxt = _tick(plan, blob, disasm, pump_on=True, countdown=600, anchor=100, clock=clock + 1024)
    assert nxt["countdown"] == 599 and nxt["anchor"] == 101
    # and the anchor is a byte, so it wraps every 256 units (~4.4 min) without losing a tick
    wrap = _tick(plan, blob, disasm, pump_on=True, countdown=600, anchor=255,
                 clock=(256 << 10))
    assert wrap["anchor"] == 0 and wrap["countdown"] == 599


def test_the_timer_fires_the_off_frame_when_it_reaches_zero_with_the_pump_still_on():
    plan, blob, disasm = _sim_setup()
    fired = _tick(plan, blob, disasm, pump_on=True, countdown=1, anchor=0, clock=1 << 10)
    assert fired["countdown"] == 0, "it stays disarmed after firing"
    assert fired["sent"] == [plan.off_template]
    # the same expiry with the pump already off sends nothing
    quiet = _tick(plan, blob, disasm, pump_on=False, countdown=1, anchor=0, clock=1 << 10)
    assert quiet["countdown"] == 0 and quiet["sent"] == []


def test_a_full_five_minute_run_fires_once_at_the_right_second():
    """End to end, second by second: arm from a `5 minute` setting, count down, fire exactly
    once, and stay disarmed while the pump status has not caught up yet."""
    plan, blob, disasm = _sim_setup()
    state = {"display": 0, "anchor": 0, "countdown": 0}
    frames = []
    for second in range(0, 5 * 60 + 3):
        out = _tick(plan, blob, disasm, word=0x0005, pump_on=True,
                    countdown=state["countdown"], anchor=state["anchor"],
                    display=state["display"], clock=second << 10)
        state = {k: out[k] for k in ("display", "anchor", "countdown")}
        if out["sent"]:
            frames.append((second, out["sent"]))
        assert out["display"] == 5, "the readout mirrors the clamped minute count every tick"
    assert [s for s, _f in frames] == [5 * 60], f"fired at {[s for s, _f in frames]}"
    assert frames[0][1] == [plan.off_template]
    # after the fire the pump STILL reads on (the G12 has not answered yet), so on the NEXT
    # second the timer re-arms for another interval -- and the second after that it is already
    # counting down again. That is the accepted consequence of having no latch: the disarm
    # arrives with the status, not with the frame.
    assert state["countdown"] == 5 * 60 - 1, "re-armed at s+1, decremented at s+2"
    after = _tick(plan, blob, disasm, word=0x0005, pump_on=False,
                  countdown=state["countdown"], anchor=state["anchor"], display=5,
                  clock=(5 * 60 + 4) << 10)
    assert after["countdown"] == 0 and after["sent"] == []


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
