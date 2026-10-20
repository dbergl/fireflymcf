import pytest

from fireflymcf import coverage, guidata, newbutton, records, relocate

BUILD = "firefly-13.6/mcf.bin"


def _d():
    with open(BUILD, "rb") as fh:
        return fh.read()


def test_unused_groups_excludes_every_located_control_group():
    data = _d()
    unused = newbutton.unused_command_groups(data)
    control_group_ids = {c.group for c in guidata.find_control_groups(data)}
    # A group referenced by a located control is NEVER "unused".
    assert control_group_ids.isdisjoint(unused), (
        f"unused set wrongly includes live control groups "
        f"{control_group_ids & set(unused)}")
    # It must also be non-empty and each entry Water-Pump-shaped (DC_DIMMER_COMMAND_2, count 2-3).
    bt = guidata.binding_table(data); gt = guidata.group_table(data)
    assert unused, "expected at least one Water-Pump-shaped unused group"
    for gid in unused:
        g = gt[gid]
        ents = [bt[j] for j in range(g.start, g.start + g.count)]
        assert ents and all(e.dgn_low == guidata.DGN_DC_DIMMER_COMMAND_2 for e in ents)


def test_allocate_returns_a_clean_single_use_group():
    data = _d()
    gid, edits, route = newbutton.allocate_command_group(data)
    assert route in ("repurpose", "grow")
    # gid is referenced by no located control ...
    assert gid not in {c.group for c in guidata.find_control_groups(data)}
    # ... and by no object's IMAGE_OPCODE group-reference tail.
    assert gid in newbutton.unused_command_groups(data)
    if route == "repurpose":
        assert edits == []
        assert gid == min(newbutton.unused_command_groups(data))


def test_clone_onto_home_adds_a_decodable_top_index_object():
    data = _d()
    before = len(guidata.object_tables(data)[1])
    new_data, idx, edits = newbutton.clone_onto_home(data)
    tabs = guidata.object_tables(new_data)[1]
    assert len(tabs) == before + 1 and idx == len(tabs) - 1     # appended at top index
    gs, _ = guidata.guidata_extent(new_data)
    recs = records.object_records(new_data, gs + tabs[idx])     # decodes (boundary oracle shape)
    assert [r.wtype for r in recs] == [33, 11, 12]              # box, caption, touch -- as the donor


# --------------------------------------------------------------------------------------
# Task 3: the clone gets its own group, commanding RV-C instance 15
# --------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def retargeted():
    """Clone the donor button onto HOME, then retarget its command group to instance 15."""
    data = _d()
    cloned, idx, _clone_edits = newbutton.clone_onto_home(data)
    out, edits = newbutton.retarget_command(cloned, idx)
    return out, idx, edits


def test_object_group_reads_the_clones_group_reference_tail():
    data = _d()
    # The donor (obj 279, "Fresh Tank Heater") references its own group, 5 -- sanity-check the
    # decoder against a known-good object before trusting it on the freshly-cloned one.
    assert newbutton.object_group(data, newbutton.DONOR_OBJ) == 5


def test_retarget_command_points_clone_group_at_channel_15_with_a_toggle(retargeted):
    out, idx, edits = retargeted
    gid = newbutton.object_group(out, idx)
    gt = guidata.group_table(out)
    bt = guidata.binding_table(out)
    group = gt[gid]
    bindings = [bt[j] for j in range(group.start, group.start + group.count)]
    # A single-press toggle is ONE DC_DIMMER_COMMAND_2 binding on channel 15 carrying command 5.
    # Every tap sends command 5 and the G12 alternates the output -- the proven single-command-5
    # light-button pattern, with no state-dependent off-vs-toggle selection (the Water Pump's
    # two-binding smart toggle sent only OFF on the coach because instance 15 has no tracked
    # status; CAN capture v5, 2026-08-17). The wire command is binding byte[4] (byte[3]=0x0a is
    # metadata). (Hardcoded 15/5 to catch a constant regression, not track it.)
    assert group.count == 1, f"single command-5 toggle is one binding, got {group.count}"
    assert bindings[0].channel == 15, (
        f"expected the binding to command channel 15, got {bindings[0].channel}")
    assert bindings[0].raw[4] == 5, (
        f"expected command 5 (toggle), got {bindings[0].raw[4]}")
    assert bindings[0].raw == newbutton._TOGGLE_ONLY[:7] + bytes([15]) + newbutton._TOGGLE_ONLY[8:]


def test_retarget_indicator_satisfies_the_live_redraw_invariant():
    """The box's mode-7 fillA index, the box's value_slot and the caption's value_slot must all
    name the SAME cell (MCF-FORMAT.md Sec.6.0a). Every live button on 13.6 obeys this; the donor
    (a template object) does not, so retarget_indicator has to establish it on all three fields.

    This is the assertion that would have caught v7, whose box painted from cell 145 while both
    value_slots said 32 -- on the coach the box repainted in place but the caption vanished until
    the page was re-entered."""
    from fireflymcf import insert
    with open("firefly-13.6/mcf.bin", "rb") as fh:
        data = fh.read()
    cloned, idx, _ = newbutton.clone_onto_home(data)

    gs0, _ = guidata.guidata_extent(cloned)
    tbl0 = guidata.object_tables(cloned)[insert.OBJECT_TABLE_INDEX]
    before = records.object_records(cloned, gs0 + tbl0[idx])
    b33, b11 = (next(r for r in before if r.wtype == t) for t in (33, 11))
    # Guard the premise: the donor really does carry both value_slot fields (they cannot be
    # added by a same-size edit) and they really do disagree with the fill index to start with.
    assert b33.value_slot is not None and b11.value_slot is not None, (
        "donor must carry a value_slot on box AND caption -- a same-size edit cannot add one")
    assert b33.value_slot != newbutton.INDICATOR_COLOUR_INDEX, (
        "donor already satisfies the invariant; this test would pass vacuously")

    out, _edits = newbutton.retarget_indicator(cloned, idx)
    gs, _ = guidata.guidata_extent(out)
    tbl = guidata.object_tables(out)[insert.OBJECT_TABLE_INDEX]
    recs = records.object_records(out, gs + tbl[idx])
    w33, w11 = (next(r for r in recs if r.wtype == t) for t in (33, 11))

    fillA = next(c for c in w33.colors if c[0] == "fillA")
    assert fillA[1] == 7, f"fillA must stay a mode-7 colour-table index, got mode {fillA[1]}"
    assert fillA[2] == newbutton.INDICATOR_COLOUR_INDEX
    assert w33.value_slot == fillA[2], (
        f"box value_slot {w33.value_slot} must equal its fillA index {fillA[2]}")
    assert w11.value_slot == fillA[2], (
        f"caption value_slot {w11.value_slot} must equal the box's cell {fillA[2]}")


def test_retarget_command_leaves_donor_bindings_untouched(retargeted):
    out, idx, edits = retargeted
    bt = guidata.binding_table(out)
    # The clone gets its OWN spare group (allocate_command_group), so the donor's group-7
    # bindings (indices 50/51, channel 7) are undisturbed -- and Water Pump's (174/175) too.
    assert bt[50].channel == 7
    assert bt[51].channel == 7
    assert bt[174].channel == 44
    assert bt[175].channel == 44


def test_retarget_command_reseals_and_passes_the_gates(retargeted):
    out, idx, edits = retargeted
    ok, reason = relocate._boundary_oracle(out)
    assert ok, reason
    report = coverage.analyse(out, deep=True)
    assert report.unclaimed == []


# --------------------------------------------------------------------------------------
# Task 4: the clone's indicator points at instance 15's colour cell
# --------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def indicator_retargeted():
    """Clone Water Pump onto HOME, then retarget its indicator colour cell to 145
    (instance 15), directly on the clone -- independent of the command retarget."""
    data = _d()
    cloned, idx, _clone_edits = newbutton.clone_onto_home(data)
    out, edits = newbutton.retarget_indicator(cloned, idx)
    return out, idx, edits


def test_indicator_points_at_instance_15_colour_cell(indicator_retargeted):
    out, idx, edits = indicator_retargeted
    gs, _ = guidata.guidata_extent(out)
    recs = records.object_records(out, gs + guidata.object_tables(out)[1][idx])
    w33 = next(r for r in recs if r.wtype == 33)
    assert ('fillA', 7, 145) in w33.colors        # mode-7 index now 145 (was 155)
    # donor 278 unchanged (its own indicator cell stays 155)
    d33 = next(r for r in records.object_records(out, gs + guidata.object_tables(out)[1][278])
               if r.wtype == 33)
    assert ('fillA', 7, 155) in d33.colors


def test_retarget_indicator_reseals_and_passes_the_gates(indicator_retargeted):
    out, idx, edits = indicator_retargeted
    ok, reason = relocate._boundary_oracle(out)
    assert ok, reason
    report = coverage.analyse(out, deep=True)
    assert report.unclaimed == []


# --------------------------------------------------------------------------------------
# Task 5: the full end-to-end build
# --------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def built():
    """The complete build_home_button(data) result, computed once for this module's tests."""
    data = _d()
    out, edits = newbutton.build_home_button(data)
    return out, edits


def test_build_home_button_passes_the_structural_gates(built):
    out, edits = built
    ok, reason = relocate._boundary_oracle(out)
    assert ok, reason
    report = coverage.analyse(out, deep=True)
    assert report.unclaimed == []


def test_build_home_button_clone_commands_channel_15(built):
    out, edits = built
    idx = newbutton.last_object_index(out)
    gid = newbutton.object_group(out, idx)
    gt = guidata.group_table(out)
    bt = guidata.binding_table(out)
    group = gt[gid]
    # Read the binding/group tables DIRECTLY -- find_control_groups' positional screen scan
    # cannot see a standalone object attached only by child-pointer (the inserted clone).
    bindings = [bt[j] for j in range(group.start, group.start + group.count)]
    assert bindings, "expected the clone's group to have bindings"
    assert all(b.channel == 15 for b in bindings), (
        f"expected every binding to command channel 15, got "
        f"{[b.channel for b in bindings]}")


def test_build_home_button_clone_is_a_boxed_button_with_colour_and_label(built):
    out, edits = built
    idx = newbutton.last_object_index(out)
    gs, _ = guidata.guidata_extent(out)
    recs = records.object_records(out, gs + guidata.object_tables(out)[1][idx])
    w33 = next(r for r in recs if r.wtype == 33)
    # a REAL box (donor 279's 84x50 box: width at geom[2], height geom[3]), not a box-less
    # text fragment
    assert w33.geom[2] and w33.geom[2] > 0, f"expected a box width, got geom {w33.geom}"
    assert w33.geom[3] and w33.geom[3] > 0, f"expected a box height, got geom {w33.geom}"
    assert ('fillA', 7, 145) in w33.colors          # instance-15 indicator cell
    w11 = next(r for r in recs if r.wtype == 11)
    assert w11.label.strip() == "Pin 15"


def test_build_home_button_leaves_the_donor_untouched(built):
    """The clone is a new object; every retarget must land on it and never on obj 279 itself
    (nor on its neighbours, which share the tank-heater group)."""
    out, edits = built
    gs, _ = guidata.guidata_extent(out)
    d_recs = records.object_records(out, gs + guidata.object_tables(out)[1][newbutton.DONOR_OBJ])
    d33 = next(r for r in d_recs if r.wtype == 33)
    assert ('fillA', 7, 138) in d33.colors          # donor keeps its own indicator cell
    assert d33.value_slot == 53                     # and its own type-1 config gate
    d11 = next(r for r in d_recs if r.wtype == 11)
    assert d11.label == "Fresh Tank Heater"
    assert d11.value_slot == 59
    bt = guidata.binding_table(out)
    # Water Pump (obj 295, group 44) is untouched too
    assert bt[174].channel == 44 and bt[175].channel == 44


def test_build_home_button_channel_15_assertion_can_fail(built):
    """Prove-can-fail: the channel-15 check in `test_build_home_button_clone_commands_channel_15`
    is load-bearing -- corrupting one of the clone's group bindings to a different channel makes
    the same all-channels-are-15 predicate go False, so a passing build passes on merit."""
    import struct
    out, edits = built
    idx = newbutton.last_object_index(out)
    gid = newbutton.object_group(out, idx)
    gt = guidata.group_table(out)
    bt = guidata.binding_table(out)
    group = gt[gid]
    channels = [bt[j].channel for j in range(group.start, group.start + group.count)]
    assert all(c == 15 for c in channels)               # holds on the real build

    gs, _ = guidata.guidata_extent(out)
    entry = bt[group.start]
    corrupt = bytearray(out)
    corrupt[gs + entry.offset + guidata.BINDING_CHANNEL_FIELD] = 99
    ct = guidata.binding_table(bytes(corrupt))
    channels2 = [ct[j].channel for j in range(group.start, group.start + group.count)]
    assert not all(c == 15 for c in channels2)          # the predicate really can fail


def test_inserted_build_still_locates_the_match_table(built):
    """Regression: insert.py grows the object count without extending the parallel
    per-object match-value table, which used to make `guidata.find_match_table` raise
    (breaking `render --config`). The width-fallback locator must still find the
    (one-shorter) table, and a match-table-aware `layout.Screen` must resolve switches."""
    from fireflymcf import layout
    out, _ = built
    data = _d()
    assert len(guidata.object_tables(out)[1]) == len(guidata.object_tables(data)[1]) + 1
    # Would raise MatchTableNotFound without the fallback:
    off = guidata.find_match_table(out)
    assert off == guidata.find_match_table(data)          # table precedes the insert on 13.6
    # A Screen auto-locates it and resolves a floorplan-gated switch without raising.
    scr = layout.Screen(out)
    assert scr.match_table_off == off


def test_build_home_button_reproduces_the_hardware_verified_v8(built):
    """The whole pipeline must still produce the exact image that was flashed and confirmed
    working on the coach (2026-08-17): toggles instance 15, caption stays visible, and the
    button repaints in place on a status change without leaving HOME.

    Rather than pin a hash of a file that is not in the repo, this re-derives the build and
    asserts the properties that were verified on hardware, all read back from the output bytes.
    """
    from fireflymcf import insert, relocate, coverage, theme
    import struct
    out, _edits = built
    idx = newbutton.last_object_index(out)

    # structural gates the flashed image passed
    ok, reason = relocate._boundary_oracle(out)
    assert ok, reason
    assert coverage.analyse(out, deep=True).unclaimed == []
    for lit_off, tgt in theme.guicode_guidata_pointers(out):
        assert struct.unpack_from("<I", out, lit_off)[0] == 0x90000000 + tgt, (
            f"stale GuiCode->GuiData pointer at {lit_off:#x} (this is the boot-loop failure)")

    gs, _ = guidata.guidata_extent(out)
    recs = records.object_records(out, gs + guidata.object_tables(out)[insert.OBJECT_TABLE_INDEX][idx])
    w33, w11, w12 = (next(r for r in recs if r.wtype == t) for t in (33, 11, 12))

    # the live-redraw invariant, on the shipped build
    cell = next(c for c in w33.colors if c[0] == "fillA")[2]
    assert cell == w33.value_slot == w11.value_slot == newbutton.INDICATOR_COLOUR_INDEX

    # one command-5 binding on instance 15 -- no state-dependent off/toggle selection
    gid = struct.unpack_from("<H", out, w12.end - guidata.GROUP_REFERENCE_TAIL)[0]
    group = guidata.group_table(out)[gid]
    bt = guidata.binding_table(out)
    assert group.count == 1
    assert bt[group.start].channel == newbutton.TARGET_CHANNEL
    assert bt[group.start].raw[4] == 5

    assert w11.label.strip() == "Pin 15"


def test_a_glyph_button_can_be_cloned_resized_and_disarmed() -> None:
    """The HOME glyph-button capability (`build/build-home-glyph-v12.py`): clone the Bath Fan
    button (obj 247 -- box + icon + caption + touch), resize it to the Water Pump / Tank Heater
    box with same-size geometry edits, and repoint its command group so it commands nothing.

    Two things this pins, both learned the hard way:

    * **Disarming is required.** Obj 247's touch record carries command group **40**, a live
      binding to channel 40. Cloning it unchanged would ship a button that commands someone
      else's pin the first time it is tapped.
    * **The clone must satisfy the live-redraw invariant.** 247 is one of the unattached template
      objects (MCF-FORMAT §6.0a) whose `value_slot` is a config gate, not a status cell -- its box
      carries `fillA` idx 144 against `value_slot` 101. Left alone that is the v7 caption-vanishing
      bug, so all three fields are rewritten to one cell.
    """
    import struct
    from fireflymcf import guidata, insert, records, relocate, newbutton, cli

    data = _d()
    gs, _ = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]

    # the donor really is the shape we think, and really is armed
    donor = records.object_records(data, gs + table[247])
    assert [r.wtype for r in donor] == [33, 11, 11, 12]
    assert any(r.font == 24 and r.label == "E" for r in donor), "no icon glyph record"
    assert newbutton.object_group(data, 247) == 40, "donor is expected to carry a live group"
    box = donor[0]
    fill = next(v for role, m, v in box.colors if role == "fillA" and m == 7)
    assert fill != box.value_slot, "247 should violate the invariant (it is a template object)"

    # clone + attach, then resize and disarm with same-size edits
    clone = insert.clone_object(data, 247)
    out, _ = insert.insert_object(data, clone, cli._resolve_page_root(data, "home"),
                                  position=(631, 404))
    gs2, _ = guidata.guidata_extent(out)
    table2 = guidata.object_tables(out)[1]
    new_idx = len(table2) - 1
    buf = bytearray(out)
    recs = records.object_records(out, gs2 + table2[new_idx])
    for rec, w in ((recs[0], 104), (recs[3], 104)):
        struct.pack_into("<H", buf, records.geom_offset(out, rec, 2), w)
        struct.pack_into("<H", buf, records.geom_offset(out, rec, 3), 50)
    touch = recs[3]
    free = newbutton.unused_command_groups(out)
    assert free
    struct.pack_into("<H", buf, touch.offset + touch.size - 2, free[0])
    out = bytes(buf)

    # the result still decodes, and the button now commands nothing
    ok, reason = relocate._boundary_oracle(out)
    assert ok, reason
    assert newbutton.object_group(out, new_idx) == free[0] != 40
    resized = records.object_records(out, gs2 + table2[new_idx])
    assert resized[0].geom[2] == 104 and resized[0].geom[3] == 50
    assert any(r.font == 24 and r.label == "E" for r in resized), "glyph lost in the resize"


# --------------------------------------------------------------------------- parameterisation
# `retarget_command`/`retarget_indicator` were pin-15-only until the Starlink button needed
# pin 29. The parameters must change what they claim to change and nothing else.


def test_retarget_command_honours_an_explicit_channel():
    """`channel=` writes that instance into the binding, leaving every other byte alone."""
    data = _d()
    cloned, idx, _ = newbutton.clone_onto_home(data)
    default, _ = newbutton.retarget_command(cloned, idx)
    other, _ = newbutton.retarget_command(cloned, idx, channel=29,
                                          allow_commanded_channel=True)

    gid = newbutton.object_group(default, idx)
    assert newbutton.object_group(other, idx) == gid, "both should allocate the same group"
    grp = guidata.group_table(default)[gid]
    a = guidata.binding_table(default)[grp.start]
    b = guidata.binding_table(other)[grp.start]
    assert a.channel == newbutton.TARGET_CHANNEL and b.channel == 29
    differing = [i for i in range(len(a.raw)) if a.raw[i] != b.raw[i]]
    assert differing == [guidata.BINDING_CHANNEL_FIELD], (
        f"channel= changed bytes {differing}, expected only the instance field")
    assert b.raw[4] == 5, "the wire command must stay 5 (toggle)"


def test_retarget_command_refuses_an_already_commanded_channel_by_default():
    """The I7 check still fires without the opt-out -- pin 29 is named by the slide groups."""
    data = _d()
    cloned, idx, _ = newbutton.clone_onto_home(data)
    with pytest.raises(ValueError, match="already commanded"):
        newbutton.retarget_command(cloned, idx, channel=29)


def test_retarget_command_rejects_a_channel_that_is_not_a_byte():
    data = _d()
    cloned, idx, _ = newbutton.clone_onto_home(data)
    with pytest.raises(ValueError, match="u8"):
        newbutton.retarget_command(cloned, idx, channel=300,
                                   allow_commanded_channel=True)


def test_retarget_indicator_honours_an_explicit_colour_index():
    """`colour_index=` lands in fillA and in every value_slot -- the live-redraw invariant."""
    data = _d()
    cloned, idx, _ = newbutton.clone_onto_home(data)
    out, _ = newbutton.retarget_indicator(cloned, idx, colour_index=312)

    gs, _ = guidata.guidata_extent(out)
    recs = records.object_records(out, gs + guidata.object_tables(out)[1][idx])
    w33 = next(r for r in recs if r.wtype == 33)
    fill_a = [c for c in w33.colors if c[0] == "fillA"][0]
    assert fill_a[1] == 7 and fill_a[2] == 312, f"fillA is {fill_a}, expected mode 7 index 312"
    slots = [r.value_slot for r in recs if r.value_slot is not None]
    assert slots and set(slots) == {312}, f"value_slots {slots} are not all 312"


def test_retarget_indicator_covers_every_text_record_not_just_the_first():
    """A button with two wtype-11 records (icon + caption) must have BOTH retargeted.

    Retargeting only the box is what v7 did: the box repainted in place on the coach and the
    caption vanished until a full page render. Leaving a *second* text record on the donor's
    config gate is the same defect one record further along.

    Object 247 (the Bath Fan / Starlink button) is the real two-text case in stock 13.6 -- an
    icon record 'E' and a caption 'Bath Fan', both carrying a value_slot, under one wtype-33
    box. The `clone_onto_home` donor carries only ONE value-slot text record, so a test built
    on the clone passes even when the loop is truncated to `[:1]` -- it must not be used here.
    """
    data = _d()
    obj = 247
    gs, _ = guidata.guidata_extent(data)
    before = records.object_records(data, gs + guidata.object_tables(data)[1][obj])
    texts = [r for r in before if r.wtype == 11 and r.value_slot is not None]
    assert len(texts) >= 2, (
        f"object {obj} carries {len(texts)} value-slot text record(s); this test needs >= 2 "
        f"or it cannot distinguish 'all' from 'the first'")

    out, edits = newbutton.retarget_indicator(data, obj, colour_index=312)

    retargeted = [e for e in edits if "wtype-11" in (e.note or "")]
    assert len(retargeted) == len(texts), (
        f"{len(texts)} value-slot text record(s) but {len(retargeted)} retargeted")

    after = records.object_records(out, gs + guidata.object_tables(out)[1][obj])
    slots = [r.value_slot for r in after
             if r.wtype == 11 and r.value_slot is not None]
    assert slots and set(slots) == {312}, (
        f"wtype-11 value_slots are {slots}; every one must be the cell the box paints from")


# ------------------------------------------------------------------- group-allocation safety
# Added 2026-08-19. `unused_command_groups` used to rest on two reachability scans that locate
# ~21 and ~17 of the ~230 groups, so groups carrying live bindings passed as "unused". On stock
# 13.6 that made 23 of 33 candidates unsafe, and `build_home_button` took group 3 -- the sole
# producer of (3, 1), AWNING EXT. Every flashed Pin-15 build lost the awning-extend command.


def test_no_allocatable_group_is_the_sole_producer_of_a_command():
    """The property that matters: repurposing a candidate must not delete a capability."""
    data = _d()
    offenders = {gid: sorted(newbutton.sole_produced_pairs(data, gid))
                 for gid in newbutton.unused_command_groups(data)
                 if newbutton.sole_produced_pairs(data, gid)}
    assert not offenders, (
        "allocatable group(s) are the only source of a (channel, command) pair; "
        f"repurposing them removes a control: {offenders}")


def test_the_awning_groups_are_not_allocatable():
    """The specific regression, pinned by name.

    Group 3 is the only producer of (3, 1) and group 4 of (4, 1) -- the awning EXT and RET
    'on' commands. Both must be excluded, and the allocator must hand back something else.
    """
    data = _d()
    assert newbutton.sole_produced_pairs(data, 3) == {(3, 1)}
    assert newbutton.sole_produced_pairs(data, 4) == {(4, 1)}
    allocatable = newbutton.unused_command_groups(data)
    assert 3 not in allocatable and 4 not in allocatable
    gid, _edits, _route = newbutton.allocate_command_group(data)
    assert gid not in (3, 4)


def test_free_group_slots_keeps_every_real_exclusion():
    """Relax only the DGN-shape restriction. A slot that is carried by a record, named
    by a located control, or the sole producer of a (channel, command) pair must still
    be excluded -- criterion 3 is the one that caught the awning-extend regression."""
    from fireflymcf import records
    data = _d()
    free = newbutton.free_group_slots(data)

    gs, _ = guidata.guidata_extent(data)
    tab = guidata.object_tables(data)[1]
    gt = guidata.group_table(data)
    carried = set()
    for i in range(len(tab)):
        try:
            recs = records.object_records(data, gs + tab[i])
        except (IndexError, ValueError):
            continue
        for r in recs:
            if r.wtype == 12:
                tid = data[r.end - 2] | (data[r.end - 1] << 8)
                if tid < len(gt):
                    carried.add(tid)
    controls = {c.group for c in guidata.find_control_groups(data)}

    assert not (set(free) & carried)
    assert not (set(free) & controls)
    for g in free:
        assert not newbutton.sole_produced_pairs(data, g), (
            f"group {g} is the sole producer of {newbutton.sole_produced_pairs(data, g)}")


def test_free_group_slots_is_a_superset_of_unused_command_groups():
    """It relaxes a restriction, so it can only add candidates -- never drop one the
    narrower function already blessed."""
    data = _d()
    assert set(newbutton.unused_command_groups(data)) <= set(
        newbutton.free_group_slots(data))


def test_free_group_slots_is_sorted_and_unique():
    data = _d()
    free = newbutton.free_group_slots(data)
    assert free == sorted(set(free))


def test_build_home_button_loses_no_command_capability():
    """End to end: the finished build must still send everything stock could.

    This is the gate that would have caught the awning loss at build time rather than on the
    coach. It compares (channel, wire-command) pairs, computed from the group and binding
    tables alone -- no reachability analysis, which is what failed here.
    """
    data = _d()

    def caps(img):
        gt, bt = guidata.group_table(img), guidata.binding_table(img)
        return {(bt[j].channel, bt[j].raw[4]) for g in gt
                for j in range(g.start, g.start + g.count)
                if 0 <= j < len(bt) and bt[j].channel is not None}

    out, _ = newbutton.build_home_button(data)
    lost = sorted(caps(data) - caps(out))
    assert not lost, f"the build can no longer send {lost}"
    assert sorted(caps(out) - caps(data)) == [(newbutton.TARGET_CHANNEL, 5)]


def _caps(img):
    """Every (channel, wire-command) pair the image can transmit, from the two tables alone."""
    gt, bt = guidata.group_table(img), guidata.binding_table(img)
    return {(bt[j].channel, bt[j].raw[4]) for g in gt
            for j in range(g.start, g.start + g.count)
            if 0 <= j < len(bt) and bt[j].channel is not None}


def test_building_on_a_channel_leaves_no_orphan_group_on_the_default_one():
    """Build on the destination pin; never build on 15 and retarget.

    `retarget_command` allocates a FRESH group every call, so build-then-retarget leaves the
    first group -- still carrying a command-5 binding on the abandoned channel -- orphaned in
    the table. Nothing references it, but the image can still express that capability, which
    is exactly what the reference build script's capability gate refuses.

    Found 2026-08-19 building 'Overflow Valve' on pin 28: the gate reported a gained (15,5).
    """
    data = _d()
    direct, _ = newbutton.build_home_button(data, channel=28, allow_commanded_channel=True)
    gained = sorted(_caps(direct) - _caps(data))
    assert gained == [(28, 5)], f"building on 28 should add only (28,5), got {gained}"

    # ...and the shape that motivated the parameter really does strand the old channel, so this
    # test fails if `build_home_button` ever silently goes back to building on TARGET_CHANNEL.
    staged, _ = newbutton.build_home_button(data)
    idx = newbutton.last_object_index(staged)
    staged, _ = newbutton.retarget_command(staged, idx, channel=28,
                                           allow_commanded_channel=True)
    stranded = sorted(_caps(staged) - _caps(data))
    assert stranded == [(newbutton.TARGET_CHANNEL, 5), (28, 5)], (
        f"expected the retarget shape to strand (15,5); got {stranded}")


def test_build_home_button_honours_an_explicit_label_and_refuses_a_longer_one():
    """The caption is a same-size field edit: it pads, it never grows."""
    data = _d()
    out, _ = newbutton.build_home_button(data, label="Overflow\nValve")
    gs, _ = guidata.guidata_extent(out)
    table = guidata.object_tables(out)[1]
    idx = newbutton.last_object_index(out)
    labels = [r.label for r in records.object_records(out, gs + table[idx]) if r.label]
    # Compare line by line: the pad is distributed per line now (see the centred-caption test
    # below), so a whole-field .strip() no longer sees the text on its own.
    stripped = ["\n".join(ln.strip() for ln in lbl.split("\n")) for lbl in labels]
    assert "Overflow\nValve" in stripped, labels
    # the field is the donor's width, so the written label is padded to exactly that
    assert any(len(lbl) == len(newbutton.TARGET_LABEL_RAW) for lbl in labels), labels

    too_long = "X" * (len(newbutton.TARGET_LABEL_RAW) + 1)
    with pytest.raises(ValueError, match="relocating edit"):
        newbutton.build_home_button(data, label=too_long)


def test_build_home_button_balances_the_pad_on_a_centred_caption():
    """The donor's caption is centre-aligned, so where the pad goes is visible on hardware.

    Padding on the right (what this did until 2026-08-21) puts all the slack inside the LAST
    drawn line, and the panel centres that line by its padded width -- so 'Overflow\\nValve'
    drew its second line 9 px left of centre on the coach while its first line looked right.
    The pad is now split around that line, which lands the word within half a space of centre.

    Asserted through the decoded record's own h_align rather than by assuming the donor is
    centred: if a future donor is left-aligned, right-padding is correct again and this test
    should be the thing that says so.
    """
    data = _d()
    out, _ = newbutton.build_home_button(data, label="Overflow\nValve")
    gs, _ = guidata.guidata_extent(out)
    table = guidata.object_tables(out)[1]
    idx = newbutton.last_object_index(out)
    cap = next(r for r in records.object_records(out, gs + table[idx])
               if r.wtype == 11 and r.label and "Valve" in r.label)
    assert cap.h_align == 2, "the donor caption is no longer centred; re-check the pad rule"
    assert cap.label == "Overflow\n  Valve ", repr(cap.label)
    assert len(cap.label) == len(newbutton.TARGET_LABEL_RAW)
