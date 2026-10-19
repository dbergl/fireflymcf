"""Splitting a hit region to add a HOLD action (`fireflymcf.holdregion`).

`docs/CONTROL-BEHAVIOR.md` §1/§7. A control's tap and a hold cannot share one region when the
tap has a side effect — the dispatcher is edge-triggered on the touch id, so the tap fires the
instant the finger lands. The way out is two non-overlapping regions.

Two things here are load-bearing beyond "the bytes came out right":

* **the record count.** An object's first byte is its record count; inserting a record without
  bumping it makes the decoder stop one record early, which is exactly how the first attempt
  failed the boundary oracle. `test_count_byte_is_bumped` pins it directly.
* **flow-relative geometry.** A touch record carries a pen offset, not an absolute box, so where
  it lands is a property of the layout replay. Every geometry assertion here therefore re-walks
  the finished image rather than reading back the u16 that was written.
"""
from __future__ import annotations

import struct

import pytest

from fireflymcf import gestures, guidata, holdregion, patch, records, relocate, theme, thumb

capstone = pytest.importorskip("capstone")

INV_OBJ = 1409
SLOTS = {5: 8, 112: 1, 113: 1, 37: 2}
STOCK_BOX = (197, 382, 417, 439)
TAP_BOX = (197, 382, 340, 439)
HOLD_BOX = (345, 382, 417, 439)
SPLIT = dict(tap_width=143, hold_x=148, hold_width=72)


def _elements(img, obj=INV_OBJ):
    from fireflymcf import layout
    scr = layout.Screen(img, match_table_off=layout.MATCH_TABLE_136)
    return [(q.wtype, q.x0, q.y0, q.x1, q.y1, q.label)
            for q in scr.walk(4, slot_values=SLOTS) if q.obj_index == obj]


@pytest.fixture(scope="module")
def split(mcf_136):
    data, edits = holdregion.split_touch_region(
        mcf_136, INV_OBJ, expect=(TAP_BOX, HOLD_BOX), slot_values=SLOTS, **SPLIT)
    return data, edits


def test_stock_has_one_region_covering_the_block(mcf_136):
    assert holdregion.region_boxes(mcf_136, INV_OBJ, slot_values=SLOTS) == [STOCK_BOX]
    assert gestures.touch_ids(mcf_136, INV_OBJ) == [2400]


def test_split_lands_where_intended(split):
    data, _ = split
    assert holdregion.region_boxes(data, INV_OBJ, slot_values=SLOTS) == [TAP_BOX, HOLD_BOX]


def test_regions_do_not_overlap(split):
    """The hit test returns the LOWEST matching id, so an overlap would hand every touch to the
    tap region and the hold would never fire — a silent failure, not a visible one."""
    data, _ = split
    tap, hold = holdregion.region_boxes(data, INV_OBJ, slot_values=SLOTS)
    assert tap[2] < hold[0], f"tap ends at {tap[2]}, hold starts at {hold[0]}"


def test_the_new_region_carries_the_dormant_arm_id(split):
    data, _ = split
    assert gestures.touch_ids(data, INV_OBJ) == [2400, holdregion.DORMANT_HOLD_ID]


def test_split_grows_the_image_by_exactly_one_record(mcf_136, split):
    data, _ = split
    assert len(data) - len(mcf_136) == 16


def test_count_byte_is_bumped(mcf_136, split):
    """The failure mode this pins is silent: without the bump the decoder returns one record
    fewer and the object appears to end early."""
    data, _ = split
    def count(img):
        gs, _ = guidata.guidata_extent(img)
        tab = guidata.object_tables(img)[relocate.OBJECT_TABLE_INDEX]
        base = gs + tab[INV_OBJ]
        return img[base], len(records.object_records(img, base))
    before_byte, before_n = count(mcf_136)
    after_byte, after_n = count(data)
    assert (after_byte, after_n) == (before_byte + 1, before_n + 1)


def test_split_does_not_move_anything_the_object_DRAWS(mcf_136, split):
    """The defect this pins shipped once and every other check passed.

    An inserted record advances the layout pen by its own x, so without compensating the next
    record the block's label and boxes slide right by `hold_x` — 148 px, off the panel — while
    both hit regions still land exactly where they were asked to. Verifying only what you set
    is how that survived; this asserts what was *not* meant to change.
    """
    data, _ = split
    def drawn(img):
        return [e for e in _elements(img) if e[0] != 12]
    assert drawn(data) == drawn(mcf_136)


def test_split_compensates_the_pen_on_the_following_record(mcf_136, split):
    """The compensation is a real edit, not a no-op: the next record's x must have moved by
    exactly -hold_x."""
    data, _ = split
    def first_box_x(img):
        # the record that absorbs the pen advance is the object's first wtype-4, which follows
        # the hit region(s). Keyed by type, not by position: in the split image the record
        # immediately after the original hit region is the NEW one, not the compensated one.
        gs, _g = guidata.guidata_extent(img)
        tab = guidata.object_tables(img)[relocate.OBJECT_TABLE_INDEX]
        base = gs + tab[INV_OBJ]
        return [r for r in records.object_records(img, base) if r.wtype == 4][0].geom[0]
    before, after = first_box_x(mcf_136), first_box_x(data)
    assert (before - after) % 0x10000 == SPLIT["hold_x"], (before, after)


def test_split_passes_the_boundary_oracle(split):
    data, _ = split
    ok, why = relocate._boundary_oracle(data)
    assert ok, why


def test_split_refuses_an_object_without_exactly_one_region(mcf_136):
    """Object 385 is the nav rail: five hit regions, so "the" region is ambiguous and the
    module refuses rather than picking one. (Object 4 has exactly one — the tank cluster — so
    it is *not* a counter-example, which is why the rail is used here.)"""
    assert len(gestures.touch_ids(mcf_136, 385)) == 5
    with pytest.raises(ValueError, match="touch records, expected exactly 1"):
        holdregion.touch_record(mcf_136, 385)
    with pytest.raises(ValueError, match="touch records, expected exactly 1"):
        holdregion.split_touch_region(mcf_136, 385, expect=None, **SPLIT)


def test_split_refuses_a_tap_width_that_is_not_a_shrink(mcf_136):
    with pytest.raises(ValueError, match="must be between 1 and"):
        holdregion.split_touch_region(mcf_136, INV_OBJ, tap_width=999, hold_x=148, hold_width=72)


def test_split_refuses_when_it_does_not_land_on_expect(mcf_136):
    """`expect` is the only thing that turns `hold_x` from a guess about the layout pen into a
    checked fact, so it has to actually fire."""
    with pytest.raises(ValueError, match="split landed at"):
        holdregion.split_touch_region(
            mcf_136, INV_OBJ, expect=(TAP_BOX, (999, 0, 1000, 1)), slot_values=SLOTS, **SPLIT)


# --- the GuiCode half ---------------------------------------------------------------------

def test_encoders_reproduce_the_stock_instructions():
    """The encoders are checked against the bytes already in the image, so a wrong encoding
    cannot pass: `movw r2,#0x838` and `movs r3,#0x11` are what the consumer ships with."""
    assert holdregion.encode_movw(2, 0x838) == holdregion.CONSUMER_ID_OLD
    assert holdregion.encode_movs(3, 0x11) == holdregion.CONSUMER_SCREEN_OLD


@pytest.mark.parametrize("enc,text", [
    (holdregion.encode_movw(2, 0x1795), "movw r2, #0x1795"),
    (holdregion.encode_movs(3, 0x22), "movs r3, #0x22"),
])
def test_encoders_disassemble_to_what_they_claim(enc, text):
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    insn = next(md.disasm(enc, 0x1000))
    assert f"{insn.mnemonic} {insn.op_str}" == text


def test_wire_hold_writes_three_same_size_edits(mcf_136):
    data, edits = holdregion.wire_hold_to_screen(
        mcf_136, touch_id=holdregion.DORMANT_HOLD_ID, screen=34)
    assert len(data) == len(mcf_136)
    assert len(edits) == 3
    assert all(len(old) == len(new) for _o, old, new, _w in edits)
    sec = theme.guicode_section(data); base = theme.guicode_base(sec)
    at = lambda v, n: data[sec.offset + (v - base):][:n]
    assert at(holdregion.ARM_DELAY_SITE, 4) == holdregion.ARM_DELAY_3000
    assert at(holdregion.CONSUMER_ID_SITE, 4) == holdregion.encode_movw(2, 0x1795)
    assert at(holdregion.CONSUMER_SCREEN_SITE, 2) == holdregion.encode_movs(3, 34)


def test_wire_hold_leaves_the_tank_branch_alone(mcf_136):
    """This build trades away the 0x838 shortcut and nothing else. The 0x7E8 branch is the
    owner's PROBE TANK DIAGNOSTICS hold and must survive untouched."""
    data, _ = holdregion.wire_hold_to_screen(
        mcf_136, touch_id=holdregion.DORMANT_HOLD_ID, screen=34)
    sec = theme.guicode_section(mcf_136); base = theme.guicode_base(sec)
    lo = sec.offset + (0x0024E906 - base)
    assert data[lo:lo + 8] == mcf_136[lo:lo + 8]


def test_wire_hold_refuses_a_screen_that_does_not_fit_the_byte_store(mcf_136):
    with pytest.raises(ValueError, match="does not fit the byte store"):
        holdregion.wire_hold_to_screen(mcf_136, touch_id=0x1795, screen=300)


def test_wire_hold_refuses_an_unsupported_delay(mcf_136):
    """Only 3000 ms is offered, because the replacement is the firmware's own encoding lifted
    from the CLEAR EEPROM timer rather than an assembled immediate."""
    with pytest.raises(ValueError, match="only 3000 ms"):
        holdregion.wire_hold_to_screen(mcf_136, touch_id=0x1795, screen=34, delay_ms=5000)


def test_wire_hold_refuses_a_build_whose_sites_differ(mcf_136):
    data, _ = holdregion.wire_hold_to_screen(mcf_136, touch_id=0x1795, screen=34)
    with pytest.raises(ValueError, match="expected .* found"):
        holdregion.wire_hold_to_screen(data, touch_id=0x1795, screen=34)


def test_full_split_reseals_and_holds_both_gates(mcf_136):
    data, _ = holdregion.split_touch_region(
        mcf_136, INV_OBJ, expect=(TAP_BOX, HOLD_BOX), slot_values=SLOTS, **SPLIT)
    data, _ = patch.reseal(data)
    data, _ = holdregion.wire_hold_to_screen(data, touch_id=0x1795, screen=34)
    data, changed = patch.reseal(data)
    assert changed == ["section-checksum:Lyra 13v6 GuiCode"], changed
    ok, why = relocate._boundary_oracle(data)
    assert ok, why
    assert holdregion.region_boxes(data, INV_OBJ, slot_values=SLOTS) == [TAP_BOX, HOLD_BOX]


# --- the dispatch stub: a third destination without giving one up -------------------------

STUB_ENTRIES = [(0x838, 17), (holdregion.DORMANT_HOLD_ID, 39)]


@pytest.fixture(scope="module")
def stubbed(mcf_136):
    """Stock + the interlocks removed (which frees the dead space) + the stub."""
    from fireflymcf import interlocks
    data, _ = interlocks.uncouple(mcf_136)
    data, _ = patch.reseal(data)
    data, _ = holdregion.set_hold_delay(data, 3000)
    data, edits = holdregion.install_hold_dispatch(data, STUB_ENTRIES)
    return patch.reseal(data)[0], edits


def test_stub_needs_the_dead_space_and_refuses_without_it(mcf_136):
    """On stock there are no NOP runs, so there is nowhere to put a stub. Refusing is the
    honest answer -- the alternative would be overwriting live code."""
    with pytest.raises(ValueError, match="no scratch run"):
        holdregion.install_hold_dispatch(mcf_136, STUB_ENTRIES)


def test_stub_dispatches_every_id_including_the_preserved_one(stubbed):
    data, _ = stubbed
    assert holdregion.hold_dispatch_map(data) == {
        0x7E8: 24, 0x838: 17, holdregion.DORMANT_HOLD_ID: 39}


def test_stub_costs_nothing(mcf_136, stubbed):
    """The point of the stub: every id the stock consumer resolved must still resolve, to the
    same screen. A version that dropped one would still 'work' for the new hold."""
    data, _ = stubbed
    before = holdregion.hold_dispatch_map(mcf_136)
    after = holdregion.hold_dispatch_map(data)
    for tid, screen in before.items():
        assert after.get(tid) == screen, "id %#x was %s, now %s" % (tid, screen, after.get(tid))


def test_stub_leaves_its_host_handler_falling_through(mcf_136, stubbed):
    """The stub lives inside the Progressive Inverter tap handler's dead space, so the first
    instruction must step over it. Without that, tapping that row would execute the stub with
    whatever happened to be in r3 and write a garbage screen id."""
    data, _ = stubbed
    sec = theme.guicode_section(data); base = theme.guicode_base(sec)
    run_at, run_len = holdregion.SCRATCH_RUNS[0]
    first = thumb.disassemble(data[sec.offset + (run_at - base):][:4], run_at)[0][1]
    assert first == "b.w #%#x" % (run_at + run_len), first
    tail = sec.offset + (run_at + run_len - base)
    assert data[tail:tail + 4] == mcf_136[tail:tail + 4]


def test_stub_does_not_touch_the_tank_arm(mcf_136, stubbed):
    data, _ = stubbed
    sec = theme.guicode_section(mcf_136); base = theme.guicode_base(sec)
    lo = sec.offset + (0x0024E906 - base)
    assert data[lo:lo + 8] == mcf_136[lo:lo + 8]


def test_stub_is_same_size(mcf_136, stubbed):
    data, _ = stubbed
    assert len(data) == len(mcf_136)


def _uncoupled(mcf):
    from fireflymcf import interlocks
    data, _ = interlocks.uncouple(mcf)
    return patch.reseal(data)[0]


def test_stub_refuses_a_screen_that_does_not_fit_a_byte(mcf_136):
    with pytest.raises(ValueError, match="does not fit the byte"):
        holdregion.install_hold_dispatch(_uncoupled(mcf_136), [(0x838, 17), (0x1795, 300)])


def test_stub_refuses_when_it_will_not_fit(mcf_136):
    """Six entries need more than the 32 usable bytes of a run; better to refuse than to run
    off the end of the dead space into live code."""
    with pytest.raises(ValueError, match="only .* available"):
        holdregion.install_hold_dispatch(_uncoupled(mcf_136),
                                         [(0x800 + i, i) for i in range(6)])


def test_built_stub_disassembles_to_the_intended_listing():
    """`build_dispatch_stub` checks its own output, so this asserts the listing is what a
    reader would expect rather than merely self-consistent."""
    code, listing = holdregion.build_dispatch_stub(0x24CD28, STUB_ENTRIES, 0x24E91C)
    assert listing == [
        "movw r2, #0x838", "cmp r3, r2", "bne #0x24cd34", "movs r3, #0x11", "b #0x24cd3e",
        "movw r2, #0x1795", "cmp r3, r2", "bne #0x24cd42", "movs r3, #0x27",
        "strb.w r3, [r4, #0x20a]", "b.w #0x24e91c"]
    assert len(code) == 30 == holdregion.stub_size(2)
