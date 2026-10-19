"""Repointing a control's G12 channel.

Phase 2 of its design note (not published). The edit itself is
one byte per binding entry, so the same-size invariant is trivially satisfied; everything
worth testing is what `set_channel` *refuses*.

The four checks, and the failure each prevents:

    I5  a group holding a non-DC_DIMMER_COMMAND_2 entry -- byte 7 is not known to be the
        channel there, so rewriting it would corrupt a message of unknown shape
    I6  a group no located control references, or more than one -- a remap would be a
        side effect on a button the caller never named
    I7  a destination another binding already commands -- two controls, one pin
    I8  a move between capability classes (`guidata.dimmable_channels`)

Every literal is a measurement against 13.6.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from fireflymcf import guidata, patch, records, status_map, statusbind


IMAGE = "firefly-13.6/mcf.bin"

# 'Bath Fan', group 40. The spec named these two offsets before the primitive existed and
# they are reproduced here so a change of decode is caught, not silently accepted.
BATH_FAN_GROUP = 40
BATH_FAN_CHANNEL = 40
BATH_FAN_EDIT_OFFSETS = (0x02A37F, 0x02A38B)

# The status side. Variable 82 is a member of the per-channel display list -- the G12
# OUTPUTS screen's row for channel 40 -- and 184 is not. Both watch instance 40; moving
# 82 is what makes the button's indicator follow the remap, and it is also what makes the
# diagnostics row for the old channel report the new one. Observed on the coach.
BATH_FAN_STATUS_LIST = 0x02C13C        # variable 82, display-list member
BATH_FAN_STATUS_OTHER = 0x02C46C       # variable 184, not in any list

# The control's own object, and the first +0x44BC8 index no record names.
BATH_FAN_OBJECT = 247
FIRST_SPARE_INDEX = 203

# In no binding entry, latch, and both of its status cells already indexed -- a destination
# whose remap needs no cell-table entry, so it inverts exactly.
FULLY_INDEXED_FREE_CHANNEL = 9

# Commanded by nothing in the file, and a high/low current output on the pin sheet.
FREE_CHANNEL = 38

# 13.6's dimmable set, reproducing the pin sheet's DIM notes exactly.
DIMMABLE_136 = {21, 22, 23, 24, 25, 26, 32, 34, 35}

GROUP_WITH_FOREIGN_DGN = 10        # 0xFEF9 entries
GROUP_NO_CONTROL_REFERENCES = 3


@pytest.fixture(scope="module")
def image() -> bytes:
    with open(IMAGE, "rb") as handle:
        return handle.read()


@pytest.fixture(scope="module")
def bath_fan(image: bytes) -> guidata.ControlGroup:
    found = [c for c in guidata.find_control_groups(image) if c.group == BATH_FAN_GROUP]
    assert len(found) == 1, "the fixture's premise -- one control on group 40 -- broke"
    return found[0]


def test_dimmable_channels_reproduce_the_pin_sheet(image: bytes) -> None:
    """Nine DIM channels, and none of the six LATCH ones.

    The PDF is independent evidence, not a fixture: this is the check that
    `dimmable_channels` describes the hardware and not just the file's own habits.
    """
    dimmable = guidata.dimmable_channels(image)
    assert dimmable == DIMMABLE_136
    for latch in (5, 6, 7, 8, 40, 44):
        assert latch not in dimmable


def test_set_channel_rewrites_every_entry_in_the_group(
        image: bytes, bath_fan: guidata.ControlGroup) -> None:
    """Both variant copies, at the offsets the spec predicted."""
    patcher = patch.McfPatcher(image)
    patcher.set_channel(bath_fan, FREE_CHANNEL)
    edits = patcher.edits()
    bindings = [e for e in edits if e.note.startswith("binding")]
    assert tuple(sorted(e.file_offset for e in bindings)) == BATH_FAN_EDIT_OFFSETS
    assert all(edit.old == bytes([BATH_FAN_CHANNEL]) for edit in bindings)
    assert all(edit.new == bytes([FREE_CHANNEL]) for edit in bindings)


def _indicator_cells(data: bytes, obj: int) -> set[int]:
    """Every SRAM cell object `obj`'s records name, through a mode-7 fill or a value_slot."""
    gs, _ = guidata.guidata_extent(data)
    table = status_map.cell_table(data)
    recs = records.object_records(data, gs + guidata.object_tables(data)[1][obj])
    cells = {table[i] for r in recs for (_role, mode, i) in r.colors if mode == 7}
    cells |= {table[r.value_slot] for r in recs if r.value_slot is not None}
    return cells


def _pin_cells(data: bytes, pin: int) -> set[int]:
    b = statusbind.pin_bindings(data)[pin]
    return {b.colour_cell, b.bool_cell}


def test_default_leaves_the_variable_table_alone(
        image: bytes, bath_fan: guidata.ControlGroup) -> None:
    """Neither watcher moves: PC.14 showed moving 184 alone does nothing (v10), and moving 82
    makes the old channel's G12 OUTPUTS row lie. The indicator is retargeted instead."""
    patcher = patch.McfPatcher(image)
    patcher.set_channel(bath_fan, FREE_CHANNEL)
    touched = {e.file_offset for e in patcher.edits()}
    assert BATH_FAN_STATUS_OTHER not in touched
    assert BATH_FAN_STATUS_LIST not in touched


def test_default_retargets_the_indicator_to_the_new_pins_cells(
        image: bytes, bath_fan: guidata.ControlGroup) -> None:
    """Read back through the decoder: object 247 now names pin 38's colour and bool cells, and
    none of pin 40's. Stock names 144 (pin 40's colour cell) in fillA and 101 (its bool cell) in
    every value_slot."""
    assert _pin_cells(image, BATH_FAN_CHANNEL) <= _indicator_cells(image, BATH_FAN_OBJECT)
    patcher = patch.McfPatcher(image)
    patcher.set_channel(bath_fan, FREE_CHANNEL)
    out, _ = patcher.build()
    cells = _indicator_cells(out, BATH_FAN_OBJECT)
    assert _pin_cells(out, FREE_CHANNEL) <= cells
    assert not _pin_cells(out, BATH_FAN_CHANNEL) & cells


def test_default_indexes_an_unindexed_destination_cell(
        image: bytes, bath_fan: guidata.ControlGroup) -> None:
    """Pin 38's colour cell has no +0x44BC8 index in stock; the remap points the first spare at
    it, exactly as `statusbind.bind_pin` does for a new button."""
    assert statusbind.pin_bindings(image)[FREE_CHANNEL].colour_index is None
    patcher = patch.McfPatcher(image)
    patcher.set_channel(bath_fan, FREE_CHANNEL)
    out, _ = patcher.build()
    assert statusbind.pin_bindings(out)[FREE_CHANNEL].colour_index == FIRST_SPARE_INDEX


def test_default_edits_only_bindings_the_control_and_one_cell_entry(
        image: bytes, bath_fan: guidata.ControlGroup) -> None:
    """Whole-image diff, every run classified. The G12 OUTPUTS row objects that share pin 40's
    bool cell (35, 975) must not move."""
    patcher = patch.McfPatcher(image)
    patcher.set_channel(bath_fan, FREE_CHANNEL)
    out, _ = patcher.build()
    gs, _ = guidata.guidata_extent(image)
    tab = guidata.object_tables(image)[1]
    obj_span = (gs + tab[BATH_FAN_OBJECT], gs + tab[BATH_FAN_OBJECT - 1])
    cell_entry = (gs + statusbind.CELL_TABLE_OFF + FIRST_SPARE_INDEX * 4,
                  gs + statusbind.CELL_TABLE_OFF + FIRST_SPARE_INDEX * 4 + 4)
    checksums = [(f.position, f.position + 4) for f in patch.checksum_fields(image)]
    allowed = [(o, o + 1) for o in BATH_FAN_EDIT_OFFSETS] + [obj_span, cell_entry] + checksums
    for start, length in patch.diff_ranges(image, out):
        assert any(lo <= start and start + length <= hi for lo, hi in allowed), \
            f"unexpected change at {start:#x}+{length}"
    for other in (35, 975):
        span = slice(gs + tab[other], gs + tab[other - 1])
        assert image[span] == out[span]


def test_move_display_list_is_the_v9_shape(
        image: bytes, bath_fan: guidata.ControlGroup) -> None:
    """With the flag, both watchers move -- PC.14's v9, the coach-proven build -- and the
    control's own records are left as they are."""
    patcher = patch.McfPatcher(image)
    patcher.set_channel(bath_fan, FREE_CHANNEL, move_display_list=True)
    touched = {e.file_offset for e in patcher.edits()}
    assert {BATH_FAN_STATUS_LIST, BATH_FAN_STATUS_OTHER} <= touched
    out, _ = patcher.build()
    assert _indicator_cells(out, BATH_FAN_OBJECT) == _indicator_cells(image, BATH_FAN_OBJECT)


def test_status_list_membership(image: bytes) -> None:
    """Variable 82 sits in the run of indices 66..86 mapping to instances 24..44."""
    listed = guidata.status_list_variables(image)
    assert 82 in listed
    assert 184 not in listed
    assert 80 in listed


def test_remap_passes_the_ladder(image: bytes, bath_fan: guidata.ControlGroup) -> None:
    patcher = patch.McfPatcher(image)
    patcher.set_channel(bath_fan, FREE_CHANNEL)
    out, changed = patcher.build()
    patch.verify(image, out, patcher.edits(), changed)
    assert changed == ["section-checksum:Lyra 13v6 GuiData"]
    assert len(out) == len(image)


def test_remap_is_invertible(image: bytes, bath_fan: guidata.ControlGroup) -> None:
    """On a destination whose cells are already indexed. A spare index pointed at pin 38's cell
    stays pointed there after the way back, which is harmless (nothing names it) but not a
    byte-identical round trip."""
    patcher = patch.McfPatcher(image)
    patcher.set_channel(bath_fan, FULLY_INDEXED_FREE_CHANNEL)
    out, _ = patcher.build()

    back = patch.McfPatcher(out)
    moved = [c for c in guidata.find_control_groups(out) if c.group == BATH_FAN_GROUP][0]
    back.set_channel(moved, BATH_FAN_CHANNEL)
    restored, _ = back.build()
    assert restored == image


def test_the_remapped_channel_is_what_the_reader_sees(
        image: bytes, bath_fan: guidata.ControlGroup) -> None:
    """Read it back through the ordinary decoder rather than trusting the writer."""
    patcher = patch.McfPatcher(image)
    patcher.set_channel(bath_fan, FREE_CHANNEL)
    out, _ = patcher.build()
    moved = [c for c in guidata.find_control_groups(out) if c.group == BATH_FAN_GROUP][0]
    assert moved.channels == [FREE_CHANNEL]
    assert BATH_FAN_CHANNEL not in {b.channel for b in guidata.binding_table(out)}


# --------------------------------------------------------------------- the refusals


def test_i5_refuses_a_group_with_a_foreign_dgn(
        image: bytes, bath_fan: guidata.ControlGroup) -> None:
    control = replace(bath_fan, group=GROUP_WITH_FOREIGN_DGN)
    with pytest.raises(patch.PatchError, match="non-DC_DIMMER_COMMAND_2"):
        patch.McfPatcher(image).set_channel(control, FREE_CHANNEL)


def test_i6_refuses_a_group_no_control_references(
        image: bytes, bath_fan: guidata.ControlGroup) -> None:
    control = replace(bath_fan, group=GROUP_NO_CONTROL_REFERENCES)
    with pytest.raises(patch.PatchError, match="referenced by 0 located controls"):
        patch.McfPatcher(image).set_channel(control, FREE_CHANNEL)


def test_i7_refuses_an_occupied_destination(
        image: bytes, bath_fan: guidata.ControlGroup) -> None:
    with pytest.raises(patch.PatchError, match="already commanded"):
        patch.McfPatcher(image).set_channel(bath_fan, 25)


def test_i8_refuses_moving_a_dimmer_onto_an_uncommanded_pin(image: bytes) -> None:
    """The reachable direction of I8 -- see the comment in `set_channel`."""
    dimmer = [c for c in guidata.find_control_groups(image) if c.group == 22][0]
    assert set(dimmer.channels) & DIMMABLE_136
    with pytest.raises(patch.PatchError, match="allow_capability_change"):
        patch.McfPatcher(image).set_channel(dimmer, FREE_CHANNEL)


def test_i8_can_be_overridden_deliberately(image: bytes) -> None:
    dimmer = [c for c in guidata.find_control_groups(image) if c.group == 22][0]
    patcher = patch.McfPatcher(image)
    patcher.set_channel(dimmer, FREE_CHANNEL, allow_capability_change=True,
                        move_status=False)
    assert patcher.edits()


def test_i8_does_not_fire_for_a_latch_control(
        image: bytes, bath_fan: guidata.ControlGroup) -> None:
    """Bath Fan is latch-only and 38 is uncommanded -- same class, so no override needed."""
    patcher = patch.McfPatcher(image)
    patcher.set_channel(bath_fan, FREE_CHANNEL, move_status=False)
    assert len(patcher.edits()) == 2


def test_channel_must_fit_one_byte(image: bytes, bath_fan: guidata.ControlGroup) -> None:
    for bad in (-1, 256, 300):
        with pytest.raises(patch.PatchError, match="one-byte"):
            patch.McfPatcher(image).set_channel(bath_fan, bad)


def test_a_no_op_remap_produces_nothing_to_build(
        image: bytes, bath_fan: guidata.ControlGroup) -> None:
    """I4: remapping a channel onto itself must not silently emit an identical file."""
    patcher = patch.McfPatcher(image)
    patcher.set_channel(bath_fan, BATH_FAN_CHANNEL)
    with pytest.raises(patch.PatchError, match="no edits"):
        patcher.build()


def test_group_index_must_be_in_range(
        image: bytes, bath_fan: guidata.ControlGroup) -> None:
    with pytest.raises(patch.PatchError, match="outside the"):
        patch.McfPatcher(image).set_channel(replace(bath_fan, group=9999), FREE_CHANNEL)


def test_remap_touches_only_the_guidata_section(
        image: bytes, bath_fan: guidata.ControlGroup) -> None:
    from fireflymcf import container
    patcher = patch.McfPatcher(image)
    patcher.set_channel(bath_fan, FREE_CHANNEL)
    out, _ = patcher.build()
    for section in container.parse_container(image).sections:
        span = slice(section.offset, section.offset + section.length)
        if "GuiData" in section.name:
            assert image[span] != out[span]
        else:
            assert image[span] == out[span]


# --------------------------------------------------------------------- the indicator retarget


def test_refuses_a_destination_with_no_bridge_block(
        image: bytes, bath_fan: guidata.ControlGroup) -> None:
    """Pin 42 (var 84) has no GuiCode bridge block, so no cell ever shows its state."""
    with pytest.raises(patch.PatchError, match="bridge block"):
        patch.McfPatcher(image).set_channel(bath_fan, 42)


def test_refuses_on_a_build_with_no_bridge_dump() -> None:
    """The bridge dump is 13.6's alone; 14.7 must refuse rather than borrow 13.6's cells."""
    with open("firefly-14.7 (Terrain)/mcf.bin", "rb") as handle:
        other = handle.read()
    fan = [c for c in guidata.find_control_groups(other) if c.group == BATH_FAN_GROUP][0]
    with pytest.raises(patch.PatchError, match="13.6"):
        patch.McfPatcher(other).set_channel(fan, FREE_CHANNEL)
    patcher = patch.McfPatcher(other)
    patcher.set_channel(fan, FREE_CHANNEL, move_display_list=True)
    assert patcher.edits()


# Every single-channel control on 13.6, by group: does its indicator name its own pin's cells
# (so it can follow a remap), or something else? Measured 2026-09-10. The four refusals paint
# pin 5's colour cell (C, ch1), pin 3's (ch26), or cells no status var writes (ch2, and the
# Water Pump, whose pin 44 is unbridged).
FOLLOWS_ITS_PIN = {40, 8, 6, 7, 205, 22}
DOES_NOT = {44, 28, 26, 80}


@pytest.mark.parametrize("group", sorted(FOLLOWS_ITS_PIN | DOES_NOT))
def test_every_control_either_retargets_or_refuses_by_name(image: bytes, group: int) -> None:
    control = [c for c in guidata.find_control_groups(image) if c.group == group][0]
    obj = status_map._control_object(image, control)
    patcher = patch.McfPatcher(image)
    if group in DOES_NOT:
        with pytest.raises(patch.PatchError, match="does not follow"):
            patcher.set_channel(control, FULLY_INDEXED_FREE_CHANNEL,
                                allow_capability_change=True)
        return
    patcher.set_channel(control, FULLY_INDEXED_FREE_CHANNEL, allow_capability_change=True)
    out, _ = patcher.build()
    (was,) = control.channels
    cells = _indicator_cells(out, obj)
    assert _pin_cells(out, FULLY_INDEXED_FREE_CHANNEL) & cells
    assert not _pin_cells(out, was) & cells


def test_the_sweep_covers_every_single_channel_control(image: bytes) -> None:
    groups = {c.group for c in guidata.find_control_groups(image) if len(c.channels) == 1}
    assert groups == FOLLOWS_ITS_PIN | DOES_NOT
