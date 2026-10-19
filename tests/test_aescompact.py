"""Task 2 -- compact AES ELECTRICAL frame + in-place replace of object 829.

The compact frame is built from byte-verbatim clones of stock donor records; the AGS branch is
never touched (its render must stay pixel-identical stock -> built).
"""

from fireflymcf import aescompact, guidata, records


def _recs(data, i):
    gs, _ = guidata.guidata_extent(data)
    return records.object_records(data, gs + guidata.object_tables(data)[1][i])


def test_clone_all_in_one_matches_stock_829():
    data = open("firefly-13.6/mcf.bin", "rb").read()
    stock_829 = _recs(data, 829)
    out, body_idx = aescompact.clone_all_in_one(data)
    assert [r.wtype for r in _recs(out, body_idx)] == [r.wtype for r in stock_829]
    # original 829 still intact at this stage
    assert [r.wtype for r in _recs(out, 829)] == [r.wtype for r in stock_829]


def test_swap_builds_faithful_frame_and_leaves_ags_golden():
    """rev4: object 829 becomes the 7-record faithful mirror of the AGS compact frame 830, and
    the AGS-mode render stays pixel-identical (donors are cloned, never written)."""
    from fireflymcf import render
    data = open("firefly-13.6/mcf.bin", "rb").read()
    out, _body_idx, _cluster_idx = aescompact.swap_electrical_to_compact(data)
    # AES mode now draws the compact frame (far fewer records than the 52-record body)
    assert len(_recs(out, 829)) < len(_recs(data, 829))
    assert [r.wtype for r in _recs(out, 829)] == [33, 11, 5, 11, 5, 5, 6]
    # AGS-mode render is pixel-identical stock -> built
    a = render.render_screen_image(data, 5, slot_values={7: 1, 37: 1})
    b = render.render_screen_image(out,  5, slot_values={7: 1, 37: 1})
    assert list(a.getdata()) == list(b.getdata())


def test_compact_frame_mirrors_ags_830():
    """The faithful frame carries the House/Chassis voltage containers (value_slots 209/205), the
    compact-cluster container drawn UNCONDITIONALLY (no value_slot) and the slot-113-gated inverter
    switch -- the AGS 830 structure, minus the slot-37 switch and solar."""
    data = open("firefly-13.6/mcf.bin", "rb").read()
    out, _body_idx, cluster_idx = aescompact.swap_electrical_to_compact(data)
    recs = _recs(out, 829)
    vslots = [r.value_slot for r in recs if r.wtype == 5 and r.value_slot is not None]
    assert 209 in vslots and 205 in vslots, "missing the House/Chassis voltage containers"
    cont = recs[5]
    assert cont.wtype == 5 and cont.value_slot is None and cont.child_index == cluster_idx, \
        "cluster container does not draw the cloned cluster unconditionally"
    assert recs[6].wtype == 6 and recs[6].value_slot == 113, "inverter switch not slot-113-gated"
    # The voltage gauges (obj 443/440) render, mirroring the AGS placement.
    from fireflymcf import layout
    placed = layout.Screen(out).walk(5, slot_values={7: 0, 37: 3, 113: 0})
    assert any(p.obj_index == 443 for p in placed) and any(p.obj_index == 440 for p in placed)


def test_cluster_engine_is_the_aes_engine_control():
    """rev5: the cloned cluster's engine (via 315[0]) is a CLONE of the real AES 'Engine' control
    (object 353) -- label 'Engine', Start/Stop touch ids 0x0d/0x0e (groups 13/14, channel 18), the
    526 status band, indicator value_slots 167/168 -- carrying NONE of the AGS GEN control's (352)
    extras: no generator-hours numeric line (value_slot 46) and no stray center-touch (group 170).
    It is 353's 7 records + ONE appended invisible flow spacer (an empty wtype-11 label) = 8."""
    from fireflymcf import gestures
    data = open("firefly-13.6/mcf.bin", "rb").read()
    out, _body_idx, cluster_idx = aescompact.swap_electrical_to_compact(data)
    crecs = _recs(out, cluster_idx)
    # rev5: the container points at the appended engine clone (NOT stock 353 itself).
    eng_idx = crecs[aescompact.CLUSTER_ENGINE_CONTAINER_REC].child_index
    assert eng_idx != aescompact.ENGINE_CONTENT_DONOR, "engine must be a clone, not stock 353"
    eng = _recs(out, eng_idx)
    assert len(eng) == 8, "engine clone should be 353's 7 records + 1 flow spacer"
    assert "Engine" in [r.label for r in eng if r.label]
    ids = [out[r.end - 2] | (out[r.end - 1] << 8) for r in eng if r.wtype == 12]
    assert 0x0d in ids and 0x0e in ids, "engine missing Start/Stop touch ids 0x0d/0x0e"
    status = next((r for r in eng if r.wtype == 6 and r.value_slot == 169), None)
    assert status is not None and status.child_index == 526, \
        "engine status switch not on slot 169 / the 526 band"
    # The Start/Stop indicator containers carry the Engine value_slots 167/168.
    ind = [r.value_slot for r in eng if r.wtype == 5 and r.value_slot is not None]
    assert 167 in ind and 168 in ind
    # FIX #4: none of 352's GEN-only extras leaked in.
    assert 46 not in [r.value_slot for r in eng], "engine still carries the gen-hours line (slot 46)"
    assert 170 not in gestures.touch_ids(out, eng_idx), \
        "engine still carries the stray center-touch (group 170)"
    # The appended flow spacer is an empty, flow-gated wtype-11 label (invisible, no gen data).
    from fireflymcf import layout
    spacer = eng[-1]
    assert spacer.wtype == 11 and not spacer.label and layout._in_flow_gate(spacer.wtype), \
        "engine flow spacer is not an empty flow-gated label"


def test_engine_status_does_not_overlap_the_enable_pill():
    """rev5 regression: the engine control's 2-line 'Not Running' status band (obj 526) and the
    enable pill / DISABLED readout below it (in the cluster) must sit on SEPARATE rows -- the
    engine status must end ABOVE where the pill capsule begins. Without the flow spacer the pill
    flowed up into the status band (~y160-230 overlap, the coach-reported regression)."""
    from fireflymcf import layout
    data = open("firefly-13.6/mcf.bin", "rb").read()
    out, _body_idx, cluster_idx = aescompact.swap_electrical_to_compact(data)
    placed = layout.Screen(out).walk(5, slot_values={7: 0, 37: 2, 113: 1})
    status_bottom = max(p.y1 for p in placed if p.obj_index == 526)
    pill_top = min(p.y0 for p in placed if p.obj_index == cluster_idx and p.wtype == 33)
    assert pill_top >= status_bottom, \
        "enable pill (top %d) overlaps the engine status band (bottom %d)" % (pill_top, status_bottom)
    # And nothing clips past the 480px panel bottom.
    assert max(p.y1 for p in placed) <= 480, "cluster clips past the 480px panel bottom"


def test_pill_touch_routes_through_aes_notice():
    """FIX #5: the compact enable pill's touch (cloned from AGS 315[2], wtype-12) no longer carries
    group 128 (0x0080 -> the Warning/3-sec-hold path); it is retargeted to action 0x07E2, the stock
    AES enable that shows the 'Notice:' page first. A same-size 2-byte edit inside the cloned
    cluster (object 1424); the stock 315 is untouched."""
    from fireflymcf import gestures
    data = open("firefly-13.6/mcf.bin", "rb").read()
    out, _body_idx, cluster_idx = aescompact.swap_electrical_to_compact(data)
    crecs = _recs(out, cluster_idx)
    pill = crecs[aescompact.PILL_TOUCH_REC]
    assert pill.wtype == 12
    assert (out[pill.end - 2] | (out[pill.end - 1] << 8)) == aescompact.AES_NOTICE_ACTION == 0x07E2
    cluster_ids = gestures.touch_ids(out, cluster_idx)
    assert 0x07E2 in cluster_ids, "cluster does not carry the AES Notice action 0x7E2"
    assert aescompact.PILL_STOCK_ID not in cluster_ids, \
        "cluster still carries the stock enable-pill group 0x0080"
    # Stock 315 is untouched (byte-golden): its pill still carries 0x0080.
    assert aescompact.PILL_STOCK_ID in gestures.touch_ids(data, aescompact.CLUSTER_DONOR_OBJECT)


def test_gear_touch_overlaps_its_capsule():
    """The FIX for the coach-reported dead gear: the gear nav touch (0x7DE) lives in the cloned
    cluster and, because its donor geometry is preserved off the cluster origin, composes onto the
    SAME box as its visible capsule (315[10]) -- so a tap now lands on the button."""
    from fireflymcf import layout
    data = open("firefly-13.6/mcf.bin", "rb").read()
    out, _body_idx, cluster_idx = aescompact.swap_electrical_to_compact(data)
    crecs = _recs(out, cluster_idx)
    touch = crecs[aescompact.GEAR_TOUCH_REC]
    assert touch.wtype == 12
    assert (out[touch.end - 2] | (out[touch.end - 1] << 8)) == aescompact.SETTINGS_NAV_TOUCH_ID
    capsule = crecs[10]
    assert capsule.wtype == 33
    placed = layout.Screen(out).walk(5, slot_values={7: 0, 37: 3, 113: 0})
    box = {p.record_offset: (p.x0, p.y0, p.x1, p.y1) for p in placed if p.obj_index == cluster_idx}
    tb, cb = box[touch.offset], box[capsule.offset]
    # The touch box overlaps the capsule box on the vast majority of its area (here: identical).
    ox = max(0, min(tb[2], cb[2]) - max(tb[0], cb[0]))
    oy = max(0, min(tb[3], cb[3]) - max(tb[1], cb[1]))
    assert ox * oy > 0.9 * (cb[2] - cb[0]) * (cb[3] - cb[1]), \
        "gear touch %s does not overlap its capsule %s" % (tb, cb)
    # And the gear is fully on the 480px panel (the coach's other complaint was a broken button).
    assert cb[3] <= 480, "gear capsule bottom %d clips the panel" % cb[3]


def test_aes_mode_renders_cleanly():
    """The faithful frame renders in AES mode (root 5, slot 7 = AES) without error, and the
    conditional inverter block draws only when its switch (slot 113) is on."""
    from fireflymcf import render
    data = open("firefly-13.6/mcf.bin", "rb").read()
    out, _body_idx, _cluster_idx = aescompact.swap_electrical_to_compact(data)
    off = render.render_screen_image(out, 5, slot_values={7: 0, 37: 3, 113: 0})
    on = render.render_screen_image(out, 5, slot_values={7: 0, 37: 3, 113: 1})
    assert off.size == (800, 480) and on.size == (800, 480)
    # inverter off vs on differ (the conditional block appears only when on)
    assert list(off.getdata()) != list(on.getdata())


def test_screen_28_maps_to_object_31_and_is_reusable():
    from fireflymcf import layout
    data = open("firefly-13.6/mcf.bin", "rb").read()
    assert layout.screen_object_map(data)[28] == 31


def test_settings_screen_renders_all_in_one_body():
    from fireflymcf import layout, render
    data = open("firefly-13.6/mcf.bin", "rb").read()
    out, body_idx = aescompact.clone_all_in_one(data)
    out, screen = aescompact.build_settings_screen(out, body_idx)
    assert screen == 28
    root = layout.screen_object_map(out)[screen]
    img = render.render_screen_image(out, root, slot_values={37: 2})
    assert img.size == (800, 480)      # renders as a full screen, no walk error


def test_settings_screen_root_still_maps_to_object_31():
    """The rebuild is in-place at object index 31, so screen 28 still maps to it -- no
    screen-map or match-table edit is needed."""
    from fireflymcf import layout
    data = open("firefly-13.6/mcf.bin", "rb").read()
    out, body_idx = aescompact.clone_all_in_one(data)
    out, _ = aescompact.build_settings_screen(out, body_idx)
    assert layout.screen_object_map(out)[28] == 31


# --------------------------------------------------------------------------------------------
# Task 4 -- native settings-button navigation for the compact AES page.
# --------------------------------------------------------------------------------------------


def _leaf_bytes(data):
    """The 2 bytes of the 0x7DE action leaf's screen constant, located the holdregion way."""
    from fireflymcf import theme
    section = theme.guicode_section(data)
    base = theme.guicode_base(section)
    off = section.offset + (aescompact.SETTINGS_LEAF_SITE - base)
    return off, data[off:off + 2]


def test_leaf_file_offset_and_stock_bytes():
    """The 0x7DE leaf's screen constant lives at runtime 0x0024B96A == file 0x1B1EA and is stock
    `0e 23` (movs r3,#0xe, screen 14) -- verify the mapping against the image, not a raw number."""
    data = open("firefly-13.6/mcf.bin", "rb").read()
    off, b = _leaf_bytes(data)
    assert off == 0x1B1EA
    assert b == aescompact.SETTINGS_LEAF_STOCK == bytes.fromhex("0e23")


def test_chosen_touch_id_is_dispatchable_and_uncollided():
    """0x7DE must be an ACTION id (>= group count 233) and carried by NO displayed region, so
    repurposing its tap leaf cannot collide with a live control."""
    from fireflymcf import guidata
    data = open("firefly-13.6/mcf.bin", "rb").read()
    assert aescompact.SETTINGS_NAV_TOUCH_ID == 0x7DE
    assert aescompact.SETTINGS_NAV_TOUCH_ID >= len(guidata.group_table(data))
    assert aescompact.displayed_carriers(data, aescompact.SETTINGS_NAV_TOUCH_ID) == []


def test_settings_button_leaf_retargets_to_screen_28():
    """After wire_settings_nav the 0x7DE leaf's screen constant is `1c 23` (movs r3,#0x1c = screen
    28); it was `0e 23` (screen 14) before. This is the tap-dispatch oracle."""
    data = open("firefly-13.6/mcf.bin", "rb").read()
    _off, before = _leaf_bytes(data)
    assert before == bytes.fromhex("0e23")
    out, edits = aescompact.wire_settings_nav(data, aescompact.SETTINGS_NAV_TOUCH_ID)
    _off, after = _leaf_bytes(out)
    assert after == bytes.fromhex("1c23")     # movs r3,#0x1c -> state+0x20a = screen 28
    assert len(edits) == 1


def test_wire_refuses_wrong_touch_id():
    """The route repurposes the single 0x7DE leaf, so it refuses any other id rather than silently
    doing nothing."""
    import pytest
    data = open("firefly-13.6/mcf.bin", "rb").read()
    with pytest.raises(ValueError):
        aescompact.wire_settings_nav(data, 0x801)


def test_wire_guard_refuses_non_stock_leaf():
    """The guarded poke refuses when the leaf's stock bytes do not match (a drifted build)."""
    import pytest
    data = bytearray(open("firefly-13.6/mcf.bin", "rb").read())
    off, _ = _leaf_bytes(data)
    data[off:off + 2] = bytes.fromhex("1023")     # movs r3,#0x10 -- not the stock 0x0e
    with pytest.raises(ValueError):
        aescompact.wire_settings_nav(bytes(data), aescompact.SETTINGS_NAV_TOUCH_ID)


def test_ags_action_leaves_and_rest_of_image_untouched():
    """wire_settings_nav changes EXACTLY the 2-byte 0x7DE leaf constant and nothing else, so every
    other action leaf (0x7FE->46, 0x7D1->1, ...) and all GuiData is byte-identical."""
    data = open("firefly-13.6/mcf.bin", "rb").read()
    out, _ = aescompact.wire_settings_nav(data, aescompact.SETTINGS_NAV_TOUCH_ID)
    off, _ = _leaf_bytes(data)
    assert len(out) == len(data)
    diffs = [i for i in range(len(data)) if out[i] != data[i]]
    # only the movs immediate's low byte changes (0x0e -> 0x1c); the high byte (0x23) is identical
    assert diffs == [off]


def test_gear_stamped_with_nav_id_during_build_not_stock():
    """rev4: the gear touch is stamped with the nav id 0x7DE DURING the build (inside the cloned
    cluster, so it stays aligned with its capsule) -- object 829 no longer carries a wtype-12
    settings region, and the stock 0x7FE is gone from the AES page subtree."""
    from fireflymcf import gestures
    data = open("firefly-13.6/mcf.bin", "rb").read()
    out, _body_idx, cluster_idx = aescompact.swap_electrical_to_compact(data)
    # The gear nav id is on the cloned cluster, not on 829's own records.
    cluster_ids = gestures.touch_ids(out, cluster_idx)
    assert aescompact.SETTINGS_NAV_TOUCH_ID in cluster_ids
    assert aescompact.SETTINGS_BUTTON_STOCK_ID not in cluster_ids
    assert 12 not in [r.wtype for r in _recs(out, 829)]


def test_compact_settings_button_reaches_aes_settings_not_ags():
    """The full connection via the TAP path: build the compact frame (whose gear already carries
    0x7DE), repoint the 0x7DE action leaf, and prove a tap on the gear resolves to the AES settings
    screen (28), not AGS's (46 via stock 0x7FE). The collision guard tolerates the gear's own
    carrier (the cloned cluster in the AES subtree)."""
    data = open("firefly-13.6/mcf.bin", "rb").read()
    TOUCH = aescompact.SETTINGS_NAV_TOUCH_ID
    out, _body_idx, cluster_idx = aescompact.swap_electrical_to_compact(data)
    out, edits = aescompact.wire_settings_nav(out, TOUCH)
    assert len(edits) == 1
    # the gear's tap leaf now writes screen 28 (movs r3,#0x1c), not 14 or 46
    _off, leaf = _leaf_bytes(out)
    assert leaf == bytes.fromhex("1c23")
    assert aescompact.SETTINGS_SCREEN_ID == 28


def test_settings_frame_body_container_points_at_the_aes_body():
    """The built frame mirrors object 45: header + nav rail + a wtype-5 container whose
    child_index resolves to the appended AES body clone."""
    data = open("firefly-13.6/mcf.bin", "rb").read()
    out, body_idx = aescompact.clone_all_in_one(data)
    out, _ = aescompact.build_settings_screen(out, body_idx)
    recs = _recs(out, 31)
    assert [r.wtype for r in recs] == [4, 4, 6, 5, 5, 5]
    container = recs[aescompact.CONTAINER_RECORD_INDEX]
    assert container.wtype == 5
    assert container.child_index == body_idx


# ==========================================================================================
# Task 7 -- the outlined "Water Pump" section on the compact AES frame.
# ==========================================================================================
#
# The section's fragments are decoded by splicing them into a THROWAWAY appended object (they
# are record bytes, not an object, and a record carries no absolute self-pointer -- so it
# decodes identically wherever it is placed). `insert._append_object` returns
# `(out, edits, index)` and refuses a body whose length is not 4-aligned, so the helper below
# pads; `build_pump_section` itself returns the raw fragments, and the real caller
# (`build_compact_frame`) hands them to `replace_object_records`, which re-pads.

PUMP_IDS = dict(link_touch_id=164, plus_touch_id=15, minus_touch_id=36, readout_slot=215)


def _decode_fragments(data, frags):
    """Splice `frags` into a throwaway appended object and return its decoded records."""
    from fireflymcf import insert
    body = bytes([len(frags)]) + b"".join(frags)
    body += b"\x00" * ((-len(body)) % 4)
    out, _edits, idx = insert._append_object(data, body)
    return out, _recs(out, idx)


def test_build_pump_section_emits_the_expected_record_shape():
    data = open("firefly-13.6/mcf.bin", "rb").read()
    frags = aescompact.build_pump_section(data, **PUMP_IDS)
    _out, recs = _decode_fragments(data, frags)
    wtypes = [r.wtype for r in recs]
    assert wtypes[:3] == [32, 32, 11], f"box, outline, heading; got {wtypes[:3]}"
    assert 12 in wtypes, "the Pump Link touch region is missing"
    assert 8 in wtypes, "the Auto Off numeric readout is missing"


# The section's fragment indices, in emission order (see `build_pump_section`'s docstring).
# Asserted BY INDEX rather than as a set: a set comparison passes just as happily with the
# plus and minus arrows swapped, which is precisely the mistake worth catching -- the arrows
# are visually interchangeable but drive opposite-signed config writes on the bus.
# This set describes the v14/v15 shape: 15 fragments, box+label at [4]/[5] (PUMP_IDS, no arm
# keys -- link_display_slot/link_arms_base left None).
PUMP_FRAG_LINK_TOUCH = 6
PUMP_FRAG_PLUS_TOUCH = 10
PUMP_FRAG_MINUS_TOUCH = 11
PUMP_FRAG_READOUT = 13
# The seven mirrored frame records (830[0..4] + cluster container + inverter switch) precede
# the section in the rebuilt object 829, so a fragment index maps to a frame record index by
# this offset.
PUMP_FRAME_PREFIX = 7

# The v16 shape (LINK_IDS, both arm keys given): 14 fragments, the box+label pair collapsed into
# one wtype-6 switch at [4], so every index from there on shifts down by one from the set above.
LINK_FRAG_LINK_TOUCH = 5
LINK_FRAG_PLUS_TOUCH = 9
LINK_FRAG_MINUS_TOUCH = 10
LINK_FRAG_READOUT = 12


def _touch_id(data, rec):
    return data[rec.end - 2] | (data[rec.end - 1] << 8)


def test_build_pump_section_carries_the_three_touch_ids_in_emission_order():
    """Each id must land on ITS OWN record: the link on the button's region, the plus on the
    right-hand arrow (donor id 104) and the minus on the left-hand one (donor id 103)."""
    data = open("firefly-13.6/mcf.bin", "rb").read()
    frags = aescompact.build_pump_section(data, **PUMP_IDS)
    out, recs = _decode_fragments(data, frags)

    # ordered, not sorted -- a plus/minus swap must fail here
    assert [_touch_id(out, r) for r in recs if r.wtype == 12] == [164, 15, 36]

    for name, frag, want in (("link", PUMP_FRAG_LINK_TOUCH, 164),
                             ("plus", PUMP_FRAG_PLUS_TOUCH, 15),
                             ("minus", PUMP_FRAG_MINUS_TOUCH, 36)):
        rec = recs[frag]
        assert rec.wtype == 12, f"fragment {frag} ({name}) is wtype {rec.wtype}, expected 12"
        got = _touch_id(out, rec)
        assert got == want, f"the {name} region carries id {got}, expected {want}"


def test_swap_lands_each_pump_section_id_on_its_intended_record():
    """The path Task 9 actually uses. `pump_section_ids` is POSITIONAL --
    `(link_touch_id, plus_touch_id, minus_touch_id, readout_slot)` -- so this drives the whole
    `swap_electrical_to_compact` keyword with four distinguishable values and checks each one
    reached the record it names. Any reordering of the unpack fails here."""
    data = open("firefly-13.6/mcf.bin", "rb").read()
    link, plus, minus, slot = 164, 15, 36, 215
    assert len({link, plus, minus, slot}) == 4, "the fixture values must be distinguishable"
    out, _b, _c = aescompact.swap_electrical_to_compact(
        data, pump_section_ids=(link, plus, minus, slot))
    frame = _recs(out, 829)
    assert len(frame) == PUMP_FRAME_PREFIX + 15

    def rec(frag):
        return frame[PUMP_FRAME_PREFIX + frag]

    assert _touch_id(out, rec(PUMP_FRAG_LINK_TOUCH)) == link, "link id on the wrong record"
    assert _touch_id(out, rec(PUMP_FRAG_PLUS_TOUCH)) == plus, "plus id on the wrong record"
    assert _touch_id(out, rec(PUMP_FRAG_MINUS_TOUCH)) == minus, "minus id on the wrong record"
    assert rec(PUMP_FRAG_READOUT).value_slot == slot, "readout slot on the wrong record"
    # and the arrows kept the donor sides: 793[2] (stock id 104 = the +300 group) is the
    # right-hand box, 793[3] (stock 103 = -300) the left-hand one.
    from fireflymcf import layout
    placed = {p.record_offset: p for p in layout.Screen(out).walk(
        5, slot_values={7: 0, 37: 3, 113: 1})}
    p_plus = placed[rec(PUMP_FRAG_PLUS_TOUCH).offset]
    p_minus = placed[rec(PUMP_FRAG_MINUS_TOUCH).offset]
    assert p_minus.x0 < p_plus.x0, \
        f"the minus region ({p_minus.x0}) is not left of the plus region ({p_plus.x0})"


def test_build_pump_section_points_the_readout_at_the_given_slot():
    data = open("firefly-13.6/mcf.bin", "rb").read()
    frags = aescompact.build_pump_section(data, **PUMP_IDS)
    _out, recs = _decode_fragments(data, frags)
    assert [r.value_slot for r in recs if r.wtype == 8] == [215]


def test_build_pump_section_labels_read_water_pump_and_auto_off():
    data = open("firefly-13.6/mcf.bin", "rb").read()
    frags = aescompact.build_pump_section(data, **PUMP_IDS)
    _out, recs = _decode_fragments(data, frags)
    labels = [records.label_text(r.label) for r in recs if r.label]
    assert "Water Pump" in labels
    assert "Pump Link" in labels
    assert "Auto Off" in labels
    assert "Min" in labels


# The sha256 of `swap_electrical_to_compact(stock 13.6)` as it stood BEFORE Task 7 added the
# `pump_section_ids` keyword -- taken from commit 74fe757's `aescompact.py`, run against
# `firefly-13.6/mcf.bin`. Pinning the digest is the only way to assert "unchanged from before"
# from inside the tree that already contains the change: comparing the defaulted call with an
# explicit `pump_section_ids=None` is the same code path twice and cannot fail.
#
# If a later task deliberately changes what the compact frame emits, this literal must be
# updated in the same commit, consciously -- that is the point of it.
PRE_TASK7_COMPACT_SHA256 = \
    "e818c340d193701c8c6976820756a7d75ac8a15a3696875061cc9885a76f79f2"


def test_pump_section_ids_none_reproduces_the_pre_task7_frame_byte_for_byte():
    """The new keyword is INERT by default: with `pump_section_ids` unset (and set to `None`)
    the swap must rebuild the exact image it built before Task 7 existed, byte for byte."""
    import hashlib
    data = open("firefly-13.6/mcf.bin", "rb").read()
    default, ab, ac = aescompact.swap_electrical_to_compact(data)
    explicit, bb, bc = aescompact.swap_electrical_to_compact(data, pump_section_ids=None)
    assert (ab, ac) == (bb, bc) and default == explicit, \
        "an explicit None does not match the defaulted call"
    assert hashlib.sha256(default).hexdigest() == PRE_TASK7_COMPACT_SHA256, \
        "the None build no longer reproduces the pre-Task-7 image byte for byte"


def test_pump_section_lands_inside_its_own_outlined_box():
    """Every drawn part of the section sits inside (PUMP_SECTION_X, PUMP_SECTION_Y,
    PUMP_SECTION_W, 173) and on the panel -- the geometry gate for the render step."""
    from fireflymcf import layout
    data = open("firefly-13.6/mcf.bin", "rb").read()
    with_section, _b2, _c2 = aescompact.swap_electrical_to_compact(
        data, pump_section_ids=(164, 15, 36, 215))
    # The section is object 829's records from index 7 on (the seven mirrored frame records
    # come first), plus everything the stepper's container draws out of object 393.
    frame = _recs(with_section, 829)
    assert len(frame) == 7 + 15, f"the frame carries {len(frame)} records, expected 22"
    section_offsets = {r.offset for r in frame[7:]}
    placed = layout.Screen(with_section).walk(5, slot_values={7: 0, 37: 3, 113: 1})
    added = [p for p in placed
             if (p.obj_index == 829 and p.record_offset in section_offsets)
             or p.obj_index == 393]
    assert len(added) >= 10, f"the section drew only {len(added)} things"
    x0, y0 = aescompact.PUMP_SECTION_X, aescompact.PUMP_SECTION_Y
    x1 = x0 + aescompact.PUMP_SECTION_W
    y1 = y0 + aescompact.PUMP_SECTION_H
    for p in added:
        if p.x0 == p.x1 == 0 and p.y0 == p.y1 == 0:
            continue                      # degenerate spacers draw nothing
        assert x0 <= p.x0 <= p.x1 <= x1, f"{p.label!r} {p.wtype} spills horizontally: {p}"
        assert y0 <= p.y0 <= p.y1 <= y1, f"{p.label!r} {p.wtype} spills vertically: {p}"


def test_frame_with_the_pump_section_keeps_every_other_element_in_place():
    """The v7 discipline: verifying only what you set is not verification.

    The v7 defect moved an unrelated label and its boxes 148 px off the panel while placing
    exactly the two regions it was asked to place, and every gate passed because each gate only
    looked at the two regions. So this looks at EVERYTHING: every pixel of the frame, in both
    inverter states, with the ONLY exemption being the section's own declared box. The House/
    Chassis row, the cluster, 'Triggers:'/Low Volts, the AES gear, the Inverter status block and
    the Inverter capsule directly ABOVE the section, and the whole band BELOW it, are all
    covered -- none of which the old `x < PUMP_SECTION_X` loop touched.
    """
    from fireflymcf import render
    data = open("firefly-13.6/mcf.bin", "rb").read()
    without, _b, _c = aescompact.swap_electrical_to_compact(data)
    with_section, _b2, _c2 = aescompact.swap_electrical_to_compact(
        data, pump_section_ids=(164, 15, 36, 215))

    sx0, sy0 = aescompact.PUMP_SECTION_X, aescompact.PUMP_SECTION_Y
    sx1 = sx0 + aescompact.PUMP_SECTION_W
    sy1 = sy0 + aescompact.PUMP_SECTION_H

    for vals in ({7: 0, 37: 3, 113: 1}, {7: 0, 37: 3, 113: 0}):
        a = render.render_screen_image(without, 5, slot_values=vals)
        b = render.render_screen_image(with_section, 5, slot_values=vals)
        assert a.size == b.size == (800, 480)
        ax, bx = a.load(), b.load()
        for y in range(a.height):
            inside_rows = sy0 <= y <= sy1
            for x in range(a.width):
                if inside_rows and sx0 <= x <= sx1:
                    continue              # the section's own box -- the only thing allowed to change
                assert ax[x, y] == bx[x, y], \
                    f"pixel moved at ({x},{y}) with {vals}: {ax[x, y]} -> {bx[x, y]}"
        # ...and the section must actually have drawn something inside its box, or the loop
        # above would pass on a build that emitted nothing at all.
        assert list(a.getdata()) != list(b.getdata()), f"the section drew nothing with {vals}"


def test_ags_render_stays_golden_with_the_pump_section():
    from fireflymcf import render
    data = open("firefly-13.6/mcf.bin", "rb").read()
    out, _b, _c = aescompact.swap_electrical_to_compact(
        data, pump_section_ids=(164, 15, 36, 215))
    a = render.render_screen_image(data, 5, slot_values={7: 1, 37: 1})
    b = render.render_screen_image(out, 5, slot_values={7: 1, 37: 1})
    assert list(a.getdata()) == list(b.getdata())


def test_build_link_arm_clones_the_enable_pill_arm_widened_with_our_caption():
    from fireflymcf import aescompact, records
    data = open("firefly-13.6/mcf.bin", "rb").read()
    for donor, fill in ((aescompact.LINK_ARM_OFF_DONOR, 10), (aescompact.LINK_ARM_ON_DONOR, 35)):
        obj = aescompact.build_link_arm(data, donor, "Pump Link")
        assert len(obj) % 4 == 0 and obj[0] == 2
        recs = records.object_records(obj, 0)
        assert [r.wtype for r in recs] == [33, 11]
        assert recs[0].colors[0] == ("fillA", 7, fill), "the arm keeps its donor's static fill"
        assert recs[0].geom[2:4] == [aescompact.PUMP_CONTENT_W, aescompact.PUMP_BUTTON_H]
        assert recs[1].geom[2:4] == [aescompact.PUMP_CONTENT_W, aescompact.PUMP_BUTTON_H]
        assert recs[1].label == "Pump Link"
        assert recs[0].geom[0] is None and recs[0].geom[1] is None, "flow-placed, like the donor"


def test_append_link_arms_lands_two_objects_with_match_values_0_and_1():
    import struct
    from fireflymcf import aescompact, guidata, records
    data = open("firefly-13.6/mcf.bin", "rb").read()
    n = len(guidata.object_tables(data)[1])
    out, base = aescompact.append_link_arms(data)
    assert base == n
    gs, _ = guidata.guidata_extent(out)
    tbl = guidata.object_tables(out)[1]
    assert len(tbl) == n + 2
    mt = guidata.find_match_table(out)
    assert [struct.unpack_from("<H", out, gs + mt + i * 2)[0] for i in (base, base + 1)] == [0, 1]
    off = records.object_records(out, gs + tbl[base])
    on = records.object_records(out, gs + tbl[base + 1])
    assert off[0].colors[0] == ("fillA", 7, 10) and on[0].colors[0] == ("fillA", 7, 35)
    assert off[1].label == on[1].label == "Pump Link"
    # every stock object still decodes to its boundary (the relocate gates ran, but check the
    # decode too): the enable pill donors are untouched
    for i in (173, 174):
        assert records.object_records(out, gs + tbl[i])[1].label == \
            records.object_records(data, guidata.guidata_extent(data)[0] + guidata.object_tables(data)[1][i])[1].label


# ==========================================================================================
# Task 4 -- the Pump Link box+label pair collapses into one wtype-6 switch over its own arms.
# ==========================================================================================

LINK_IDS = dict(PUMP_IDS, link_display_slot=211, link_arms_base=1422)


def test_pump_section_with_arms_replaces_the_box_and_label_by_one_switch():
    from fireflymcf import aescompact
    data = open("firefly-13.6/mcf.bin", "rb").read()
    frags = aescompact.build_pump_section(data, **LINK_IDS)
    _out, recs = _decode_fragments(data, frags)
    wtypes = [r.wtype for r in recs]
    assert len(frags) == 14
    assert wtypes[3:6] == [11, 6, 12], f"anchor, switch, touch; got {wtypes[3:6]}"
    sw = recs[4]
    assert sw.value_slot == 211 and sw.child_index == 1422 and sw.count == 2
    assert sw.base_modes[:4] == [1, 1, 1, 1] and sw.geom[0] == 0 and sw.geom[1] == 0
    assert "Pump Link" not in [r.label for r in recs], "the arms carry the caption now"

    # every touch id / value slot lands on its own record, at the v16 (14-fragment) indices.
    link_rec = recs[LINK_FRAG_LINK_TOUCH]
    assert link_rec.wtype == 12 and _touch_id(_out, link_rec) == PUMP_IDS["link_touch_id"]
    plus_rec = recs[LINK_FRAG_PLUS_TOUCH]
    assert plus_rec.wtype == 12 and _touch_id(_out, plus_rec) == PUMP_IDS["plus_touch_id"]
    minus_rec = recs[LINK_FRAG_MINUS_TOUCH]
    assert minus_rec.wtype == 12 and _touch_id(_out, minus_rec) == PUMP_IDS["minus_touch_id"]
    readout_rec = recs[LINK_FRAG_READOUT]
    assert readout_rec.wtype == 8 and readout_rec.value_slot == PUMP_IDS["readout_slot"]


def test_pump_section_without_arms_is_byte_identical_to_v15():
    """Pinned digest of `build_pump_section(**PUMP_IDS)`'s 15 fragments as they stood at 3e144fe
    (the commit immediately before this task), computed with this file's own PUMP_IDS -- so a
    changed byte anywhere in the pre-v16 shape breaks this, rather than comparing f(x) == f(x)."""
    import hashlib
    from fireflymcf import aescompact
    data = open("firefly-13.6/mcf.bin", "rb").read()
    frags = aescompact.build_pump_section(data, **PUMP_IDS)
    assert len(frags) == 15
    assert hashlib.sha256(b"".join(frags)).hexdigest() == \
        "6db5739a8889ab949e2d39f9a77fd147d1b5b72a439708d60967ca6797210d15"   # 15 fragments, pre-v16 shape, at 3e144fe


def test_the_switch_draws_the_dark_arm_at_0_and_the_lit_arm_at_1_where_the_box_was():
    """The layout re-walk is the only trustworthy check (positions are flow-relative).

    `layout.screen_for_object` cannot name a root here -- object 829 is not a direct candidate
    of the top-level screen switch, it is the AES arm of object 5's own slot-7 switch (count 2,
    child_index 829: value 0 -> 829, value 1 -> 830). So this walks from object 5 with the same
    slot_values shape `test_frame_with_the_pump_section_keeps_every_other_element_in_place` uses
    to reach into the rebuilt 829, adding our own switch's slot 211.
    """
    from fireflymcf import aescompact, layout, relocate
    data = open("firefly-13.6/mcf.bin", "rb").read()
    out, base = aescompact.append_link_arms(data)
    ids = dict(PUMP_IDS, link_display_slot=211, link_arms_base=base)
    section = aescompact.build_pump_section(data, **ids)
    frame = aescompact.build_compact_frame(data, aescompact.CLUSTER_DONOR_OBJECT,
                                           pump_section=section)
    # build_compact_frame wants the cluster index; the stock cluster donor stands in for a
    # placement check, exactly as test_pump_section_lands_inside_its_own_outlined_box does.
    img, _ = relocate.replace_object_records(out, aescompact.ELECTRICAL_AES_BODY, frame)
    placed = {}
    for value, want in ((0, base), (1, base + 1)):
        arms = [p for p in layout.Screen(img).walk(
                    5, slot_values={7: 0, 37: 3, 113: 1, 211: value})
                if p.obj_index in (base, base + 1)]
        assert arms and {p.obj_index for p in arms} == {want}, (value, arms)
        placed[value] = arms[0]
    # both arms land on the button row inside the section's content column
    button_row_y = aescompact.PUMP_SECTION_Y + aescompact.PUMP_OUTLINE_INSET + aescompact.PUMP_BUTTON_DY
    for p in placed.values():
        assert aescompact.PUMP_SECTION_X < p.x0 < p.x1 <= aescompact.PUMP_SECTION_X + aescompact.PUMP_SECTION_W
        assert abs(p.y0 - button_row_y) <= 8, p
    assert placed[0].x0 == placed[1].x0 and placed[0].y0 == placed[1].y0
