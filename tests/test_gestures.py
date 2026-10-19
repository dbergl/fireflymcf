"""The control-interaction model: tap, hold, ramp, repeat, momentary.

These tests defend a decode of two previously-`unknown` fields -- group entry bytes 0/2 and
binding entry byte 3 -- against the LCD Firmware code that consumes them
(`docs/CONTROL-BEHAVIOR.md`). Two kinds of test appear here, and the second kind is the one
that matters:

  * transcription tests, which check the module reproduces the bytes;
  * **oracle tests**, which check the decode against something written independently of it --
    a screen's own caption saying "hold for 3 seconds", and the RV-C command semantics. Those
    are the tests that could have caught a wrong bit-to-phase assignment, and they are the
    reason this decode is not a fit.
"""
import pytest

from fireflymcf import gestures, guidata, records


def mcf_136_bytes():
    import pathlib
    return pathlib.Path("firefly-13.6/mcf.bin").read_bytes()

BUILDS = ("mcf_1123", "mcf_136", "mcf_147")


@pytest.fixture(params=BUILDS)
def build(request):
    return request.getfixturevalue(request.param)


# ---------------------------------------------------------------------------
# Oracle 1: the firmware's own captions
# ---------------------------------------------------------------------------

def _labels(data, obj_index):
    gs, _ = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    return [r.label for r in records.object_records(data, gs + table[obj_index])
            if getattr(r, "label", None)]


def test_the_aes_enable_screen_says_three_seconds_and_its_group_says_three_seconds(mcf_136):
    """Object 9's caption and group 64's byte 0 agree, and neither was derived from the other.

    The caption is a string in a screen record; the hold threshold is the high nibble of a
    byte in a different table entirely. This is the single strongest check on the decode: a
    wrong scale (ticks instead of seconds, or the low nibble instead of the high) fails here.
    """
    text = " ".join(_labels(mcf_136, 9))
    assert "AUTO ENGINE START ENABLE" in text
    assert "3 seconds" in text, (
        "the oracle caption changed; re-read the screen before trusting this")

    ids = gestures.touch_ids(mcf_136, 9)
    assert ids == [64], f"the enable button's touch id moved to {ids}"
    behaviour = gestures.describe_group(mcf_136, 64)
    assert behaviour.hold_ms == 3000
    assert behaviour.kind == "hold-only", (
        "the caption says hold-then-release, so nothing may fire on press or on a short tap")


def test_the_pairing_clear_button_says_three_seconds_and_its_group_agrees(mcf_136):
    """A second, independent caption/byte pair: 'HOLD THE CLEAR BUTTON FOR 3 SECONDS'."""
    procedure = " ".join(_labels(mcf_136, 769))
    assert "HOLD THE CLEAR BUTTON FOR 3 SECONDS" in procedure

    for obj in (10, 301, 361):
        assert gestures.touch_ids(mcf_136, obj) == [85]
    assert gestures.describe_group(mcf_136, 85).hold_ms == 3000


# ---------------------------------------------------------------------------
# Oracle 2: RV-C command semantics constrain which phase a command may fire on
# ---------------------------------------------------------------------------

def test_stop_is_only_ever_sent_on_release_after_hold(build):
    """DC_DIMMER_COMMAND_2 command 4 ends a ramp, so it can only fire when a hold ends.

    This is the load-bearing test for the bit-to-phase assignment. Permute the four phase
    bits any way at all and this fails, because `stop` would land on press, on a short tap,
    or during the hold -- none of which is a coherent place to stop a ramp that has not
    started or has not finished.
    """
    seen = set()
    for group in gestures.all_groups(build):
        for e in group.emissions:
            if e.dgn == guidata.DGN_DC_DIMMER_COMMAND_2 and e.command == 4:
                seen.add(e.phases)
    assert seen, "no stop command found at all -- the scan is broken, not the claim"
    assert seen == {gestures.PHASE_RELEASE_HELD}, (
        f"stop fires on phase mask(s) {sorted(seen)}, not release-after-hold alone")


def test_ramp_is_only_ever_sent_while_held(build):
    """Command 21 is the ramp; it may only fire on the held phase."""
    seen = {e.phases for g in gestures.all_groups(build) for e in g.emissions
            if e.dgn == guidata.DGN_DC_DIMMER_COMMAND_2 and e.command == 21}
    assert seen == {gestures.PHASE_HELD}, f"ramp fires on {sorted(seen)}"


def test_a_ramping_control_repeats_while_held(build):
    """A ramp is a continuous action, so its entry must carry the repeat bit and its group a
    non-zero held-phase interval. A one-shot ramp would stop after a single frame."""
    for group in gestures.all_groups(build):
        ramps = [e for e in group.emissions
                 if e.dgn == guidata.DGN_DC_DIMMER_COMMAND_2 and e.command == 21]
        if not ramps:
            continue
        assert group.repeat_held_ms > 0, f"group {group.group} ramps but never repeats"
        assert all(e.repeats for e in ramps), (
            f"group {group.group}'s ramp entry lacks binding byte-2 bit 2")


# ---------------------------------------------------------------------------
# The named patterns
# ---------------------------------------------------------------------------

def test_the_light_button_is_tap_toggle_hold_ramp_release_stop(mcf_136):
    """Group 17 is the canonical dimmable-light gesture."""
    b = gestures.describe_group(mcf_136, 17)
    assert b.kind == "tap-or-hold"
    assert [e.command for e in b.on(gestures.PHASE_TAP)] == [5]           # toggle
    assert [e.command for e in b.on(gestures.PHASE_HELD)] == [21]         # ramp
    assert [e.command for e in b.on(gestures.PHASE_RELEASE_HELD)] == [4]  # stop
    assert not b.on(gestures.PHASE_PRESS), "a light must not act on press"


def test_the_awning_is_momentary_and_kills_the_other_direction_on_press(mcf_136):
    """Group 3: RET off on press, EXT on while held, EXT off on any release.

    The mutual exclusion `MCF-FORMAT.md` §11 records as a group property is specifically a
    *press-phase* emission, which is what makes it safe: the opposite direction is commanded
    off before this one is commanded on.
    """
    b = gestures.describe_group(mcf_136, 3)
    assert b.kind == "momentary"

    press = b.on(gestures.PHASE_PRESS)
    assert [(e.channel, e.command) for e in press if e.channel == 4] == [(4, 3)], (
        "RET (channel 4) must be commanded OFF on press")

    held = [e for e in b.on(gestures.PHASE_HELD) if e.channel == 3]
    assert held and all(e.command == 1 and e.repeats for e in held), (
        "EXT (channel 3) must be commanded ON, repeating, while held")

    released = [e for e in b.on(gestures.PHASE_RELEASE_HELD) if e.channel == 3]
    assert released and all(e.command == 3 for e in released), (
        "EXT must be commanded OFF when released")
    # ...and it must also stop if you only tapped it, never latching on.
    assert any(e.channel == 3 and e.command == 3 for e in b.on(gestures.PHASE_TAP))


def test_the_water_pump_is_a_latch(mcf_136):
    """Group 18 toggles on release and does nothing on hold -- no ramp, no momentary run."""
    b = gestures.describe_group(mcf_136, 18)
    assert b.kind == "latch"
    assert [e.command for e in b.on(gestures.PHASE_TAP)] == [5]
    assert not b.on(gestures.PHASE_PRESS)
    assert not b.on(gestures.PHASE_HELD)


# ---------------------------------------------------------------------------
# Structural invariants
# ---------------------------------------------------------------------------

def test_the_phase_mask_is_four_bits(build):
    """Byte 3's high nibble is unused except on the all-0xFF stub entries.

    The sender only ever tests bits 0-3 (`tst #1`, `tst #2`, `lsls #0x1d`, `tst #8`), so a
    set high bit would be a field this decode does not model. There is none.
    """
    highs = {b.raw[3] >> 4 for b in guidata.binding_table(build) if b.raw[:2] != b"\xff\xff"}
    assert highs == {0}, f"binding byte 3 carries unmodelled high bits: {sorted(highs)}"


def test_hold_thresholds_are_whole_seconds_from_a_small_set(build):
    """Every group holds for 1, 3 or 5 seconds -- no other value is used by any build."""
    holds = {g.hold_ms for g in gestures.all_groups(build) if g.emissions}
    assert holds <= {1000, 3000, 5000}, f"unexpected hold thresholds {sorted(holds)}"
    assert 1000 in holds and 3000 in holds


def test_repeat_intervals_come_from_the_firmware_table(build):
    """The three repeat rates are 2-bit indices into `REPEAT_MS`, so nothing else can appear."""
    for g in gestures.all_groups(build):
        for ms in (g.repeat_press_ms, g.repeat_held_ms, g.repeat_release_ms):
            assert ms in gestures.REPEAT_MS


def test_a_touch_id_at_or_above_the_group_count_is_not_a_binding_group(mcf_136):
    """CLEAR EEPROM's button is id 0xB5F, far above the 233 groups.

    It is a hold-to-confirm control all the same -- but its timer lives in GuiCode
    (`addw r3,r3,#0xbb8` at 0x0024EC54), not in the group table. Conflating the two classes
    would make `describe_group` answer for a control it knows nothing about.
    """
    ids = gestures.touch_ids(mcf_136, 22)
    assert ids == [0xB5F]
    assert not gestures.is_group_id(mcf_136, 0xB5F)
    assert gestures.is_group_id(mcf_136, 64)


def test_every_group_decodes_on_every_build(build):
    """No group raises, and every bound group matches a named pattern.

    "other" is the classifier's escape hatch. If it ever fires again there is a gesture shape
    this document does not describe, which is worth knowing about rather than tolerating.
    """
    groups = [g for g in gestures.all_groups(build) if g.emissions]
    assert len(groups) > 150
    unnamed = [g.group for g in groups if "other" in g.kinds.values()]
    assert not unnamed, f"groups {unnamed} fall outside every named pattern"


def test_the_cli_kind_choices_cover_every_name_the_classifier_returns(build):
    """`cli gestures --kind` hardcodes the list so `--help` needs no image; keep them in sync."""
    from fireflymcf import cli
    produced = {k for g in gestures.all_groups(build) for k in g.kinds.values()} | {"unbound"}
    assert produced <= set(cli._GESTURE_KINDS), (
        f"classifier returns {sorted(produced - set(cli._GESTURE_KINDS))}, which --kind rejects")


def test_an_unbound_group_is_reported_as_such_not_guessed_at(mcf_136):
    """13.6's group 5 is the `ff ff ff …` stub -- 14.7 gives it real entries."""
    stub = gestures.describe_group(mcf_136, 5)
    assert stub.emissions == ()
    assert stub.kind == "unbound"


def test_variants_of_one_group_can_implement_different_gestures(mcf_136):
    """Group 0 fires on `held` in variant 0 and on `press` in variant 1.

    A single `kind` for the whole group would be a fiction, which is why `kind` reports
    variant 0 and `kinds` reports them all. This test exists because an earlier version of
    the classifier ORed the variants together and called group 0 "press-only", hiding the
    `held` arm entirely.
    """
    b = gestures.describe_group(mcf_136, 0)
    assert b.variants == (0, 1)
    assert b.kinds == {0: "hold-only", 1: "press-only"}
    assert not b.variants_agree
    assert b.kind == "hold-only", "kind must report variant 0, not a merge"


def test_a_light_group_carries_the_dimmer_gesture_in_one_variant_only(mcf_136):
    """Group 22 (channel 22): variant 1 is the dimmer, variant 0 a plain off-on-release.

    And its byte 1 disables press-driven cycling, so which one is live is chosen outside this
    subsystem. Pinning that here stops a future reader assuming variant 0 wins by default.
    """
    b = gestures.describe_group(mcf_136, 22)
    assert b.kinds == {0: "latch", 1: "tap-or-hold"}
    assert not b.cycles_variant
    assert [e.command for e in b.on(gestures.PHASE_HELD, variant=1)] == [21]
    assert not b.on(gestures.PHASE_HELD, variant=0)


def test_the_five_second_pairing_groups_fire_only_when_a_completed_hold_ends(mcf_136):
    """Groups 39/41/42/45/46 are `hold-release`: nothing happens until you let go."""
    for group in (39, 41, 42, 45, 46):
        b = gestures.describe_group(mcf_136, group)
        assert b.hold_ms == 5000, f"group {group} holds {b.hold_ms} ms"
        assert b.kind == "hold-release"
        assert not b.on(gestures.PHASE_PRESS) and not b.on(gestures.PHASE_TAP)


# ---------------------------------------------------------------------------
# Protected touch areas (docs/CONTROL-BEHAVIOR.md section 7)
# ---------------------------------------------------------------------------

def _screen_header(data, screen_id):
    gs, _ = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    recs = records.object_records(data, gs + table[gestures.screen_object(screen_id)])
    labels = [r.label.strip() for r in recs if getattr(r, "label", None)]
    return labels[0] if labels else None


def test_the_screen_id_offset_is_pinned_by_the_nav_rail(mcf_136):
    """Screens 0-4 are HOME / ELECTRICAL / LIGHTS / CLIMATE / SETTINGS, in rail order.

    This is the whole basis for `SCREEN_ID_TO_OBJECT`. An off-by-anything here silently
    renames every screen in the build, which is exactly the error this pins against.
    """
    assert [_screen_header(mcf_136, n) for n in range(5)] == [
        "HOME", "ELECTRICAL", "LIGHTS", "CLIMATE CONTROL", "SETTINGS"]


def test_the_home_tank_quadrant_is_one_hit_region_covering_all_four_gauges(mcf_136):
    """Owner, 2026-08-19: the probe-tank page opens by holding *anywhere* in the tank area,
    "not tied to a specific tank icon". The file agrees: HOME registers exactly one hit region
    over the whole top-right quadrant, and the individual gauges register none.
    """
    from fireflymcf import layout, options
    slot_values = options.slot_values(
        mcf_136, options.CoachConfig.from_json("build/coach-wd.json"))
    placed = layout.Screen(mcf_136).walk(4, slot_values=slot_values)

    regions = []
    for p in placed:
        if p.wtype != 12:
            continue
        rec = records.decode_record(mcf_136, p.record_offset)
        tid = mcf_136[rec.end - 2] | (mcf_136[rec.end - 1] << 8)
        regions.append((tid, p.x0, p.y0, p.x1, p.y1))

    tank = [r for r in regions if r[0] == 0x7E8]
    assert len(tank) == 1, f"expected one 0x7E8 region on HOME, got {tank}"
    _, x0, y0, x1, y1 = tank[0]
    assert (x0, y0, x1, y1) == (485, 49, 745, 179)

    # ...and it really is one region, not four abutting ones: nothing else overlaps its box.
    overlapping = [r for r in regions
                   if r[0] != 0x7E8 and r[1] <= x1 and r[3] >= x0 and r[2] <= y1 and r[4] >= y0]
    assert not overlapping, f"other hit regions share the tank quadrant: {overlapping}"

    # It hugs the WELL CLUSTER, not the panel behind it. The owner reports that holding the
    # panel left or right of the tanks does nothing, and the geometry has to agree: the first
    # draft of this decode called the region "the whole top-right quadrant", which is wrong by
    # ~50px on the left and ~45px on the right.
    panel = [p for p in placed if p.obj_index == 919 and p.wtype == 33]
    assert panel, "the quadrant panel object moved"
    px0, py0, px1, py1 = panel[0].x0, panel[0].y0, panel[0].x1, panel[0].y1
    assert px0 < x0 - 20 and px1 > x1 + 20, (
        f"hit region {(x0, x1)} should sit well inside panel {(px0, px1)} horizontally")
    assert py1 > y1 + 50, f"hit region bottom {y1} should stop well above panel bottom {py1}"

    wells = [p for p in placed if p.wtype == 32 and p.obj_index in (719, 721, 723, 725)]
    assert len(wells) == 4, f"expected four tank wells, got {len(wells)}"
    assert x0 <= min(w.x0 for w in wells) + 4 and x1 >= max(w.x1 for w in wells) - 4, (
        "the hit region should span the four wells")


def test_the_tank_quadrant_id_is_a_guicode_action_not_a_binding_group(mcf_136):
    """0x7E8 is far above the group count, so the LCD phase machine never sees it."""
    assert not gestures.is_group_id(mcf_136, 0x7E8)


def test_holding_the_tank_quadrant_reaches_probe_tank_diagnostics(mcf_136):
    """Screen 0x18 -- what the hold-expiry handler writes for id 0x7E8 -- is that page.

    The instruction chain is cited in `docs/CONTROL-BEHAVIOR.md` section 7; what this test
    pins is the destination, which is the half the owner observed on the coach.
    """
    assert _screen_header(mcf_136, 0x18) == "PROBE TANK DIAGNOSTICS"


def test_clear_eeprom_and_probe_tank_are_reachable_screens(mcf_136):
    """Both were on an earlier, wrong list of "screens with no navigation write".

    That list came from reading `state+0x20A` as an object index. Screens 18 and 23-25 are
    written by GuiCode and map to CLEAR EEPROM and the three PROBE TANK pages.
    """
    assert _screen_header(mcf_136, 18) == "CLEAR EEPROM"
    assert [_screen_header(mcf_136, n) for n in (23, 24, 25)] == ["PROBE TANK DIAGNOSTICS"] * 3


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------

def test_hold_to_toggle_is_three_same_size_bytes(mcf_136):
    """Group 18 (a single-entry command-5 latch) becomes a 3-second hold-to-toggle."""
    out, edits = gestures.make_hold_to_toggle(mcf_136, 18, seconds=3)
    assert len(out) == len(mcf_136), "the edit must not resize the image"
    assert sum(1 for a, b in zip(out, mcf_136) if a != b) == 3
    assert all(len(e.old) == len(e.new) == 1 for e in edits)

    before, after = gestures.describe_group(mcf_136, 18), gestures.describe_group(out, 18)
    assert before.kind == "latch" and before.hold_ms == 1000
    assert after.kind == "hold-only" and after.hold_ms == 3000
    assert [(e.command, e.channel) for e in after.on(gestures.PHASE_HELD)] == [(5, 89)]
    # A tap must do nothing at all -- that is the entire point of the change.
    for dead in (gestures.PHASE_PRESS, gestures.PHASE_TAP, gestures.PHASE_RELEASE_HELD):
        assert not after.on(dead)


def test_hold_to_toggle_fires_once_not_once_per_repeat_tick(mcf_136):
    """Both guards must hold: the binding lacks the repeat bit AND the rate is zeroed.

    The second is defence in depth. If the byte-2-bit-2 reading were wrong, a live repeat rate
    would toggle a real output once a second for as long as a finger rests on the button.
    """
    out, _ = gestures.make_hold_to_toggle(mcf_136, 18, seconds=3)
    after = gestures.describe_group(out, 18)
    assert after.repeat_held_ms == 0
    assert not any(e.repeats for e in after.on(gestures.PHASE_HELD))


def test_hold_to_toggle_refuses_a_multi_entry_group(mcf_136):
    """The variant trap: in a multi-entry group a runtime counter picks which copy fires, so
    editing "the" binding may edit one that never runs. Refuse rather than guess."""
    for group in (1, 3, 216):          # masters and the awning
        with pytest.raises(gestures.GestureEditError, match="live binding"):
            gestures.make_hold_to_toggle(mcf_136, group)


def test_hold_to_toggle_refuses_a_group_that_is_not_a_plain_toggle(mcf_136):
    """Only command 5 gives the same gesture for on and off; anything else is refused.

    13.6 has no single-entry non-toggle dimmer group to point at, so the case is built by
    flipping group 18's command byte in a copy. That tests the guard rather than the corpus.
    """
    gs, _ = guidata.guidata_extent(mcf_136)
    entry = guidata.binding_table(mcf_136)[guidata.group_table(mcf_136)[18].start]
    off = gs + entry.offset + 4
    assert mcf_136[off] == 5, "group 18's binding is no longer a toggle; re-pick the target"
    mutated = mcf_136[:off] + bytes([3]) + mcf_136[off + 1:]      # 3 = off, not a toggle
    with pytest.raises(gestures.GestureEditError, match="not 5"):
        gestures.make_hold_to_toggle(mutated, 18)


def test_hold_to_toggle_refuses_a_binding_that_repeats(mcf_136):
    """A repeating entry would toggle on every tick of the hold, not once."""
    gs, _ = guidata.guidata_extent(mcf_136)
    entry = guidata.binding_table(mcf_136)[guidata.group_table(mcf_136)[18].start]
    off = gs + entry.offset + 2
    mutated = mcf_136[:off] + bytes([mcf_136[off] | gestures.BINDING_REPEATS]) + mcf_136[off + 1:]
    with pytest.raises(gestures.GestureEditError, match="repeat bit"):
        gestures.make_hold_to_toggle(mutated, 18)


def _poke(data, off, value):
    return data[:off] + bytes([value]) + data[off + 1:]


def _binding_off(data, index, byte):
    gs, _ = guidata.guidata_extent(data)
    return gs + guidata.binding_table(data)[index].offset + byte


def _low_volts_bindings(data):
    g = guidata.group_table(data)[_LOW_VOLTS_GROUP]
    return list(range(g.start, g.start + g.count))


def test_low_volts_hold_to_toggle_edits_every_live_copy_and_nothing_else(mcf_136):
    """Group 129: one hold byte, one repeat byte, and the phase mask of all four bindings.

    Editing only some copies is the anti-goal: the variant counter picks which copy runs, so a
    copy left on tap-release would arm by tap in one direction and by hold in the other.
    """
    out, edits = gestures.make_hold_to_toggle(mcf_136, _LOW_VOLTS_GROUP, seconds=3)
    assert len(out) == len(mcf_136)
    diff = [i for i, (a, b) in enumerate(zip(out, mcf_136)) if a != b]
    assert len(diff) == len(edits) == 6
    assert sorted(e.file_offset for e in edits) == diff

    gs, _ = guidata.guidata_extent(mcf_136)
    group_off = gs + guidata.group_table(mcf_136)[_LOW_VOLTS_GROUP].offset
    phase_offs = {_binding_off(mcf_136, i, 3) for i in _low_volts_bindings(mcf_136)}
    assert set(diff) == {group_off, group_off + 2} | phase_offs

    after = gestures.describe_group(out, _LOW_VOLTS_GROUP)
    assert after.hold_ms == 3000 and after.repeat_held_ms == 0
    assert after.kinds == {0: "hold-only", 1: "hold-only"}
    assert len(after.emissions) == 4
    assert all(e.phases == gestures.PHASE_HELD and not e.repeats for e in after.emissions)
    assert not after.cycles_variant
    # the variant wrap nibble survives the hold edit
    assert out[group_off] & 0x0F == mcf_136[group_off] & 0x0F


def test_the_single_binding_path_is_unchanged(mcf_136):
    """Group 18 still takes exactly the three-byte path the coach verified on pins 28/29."""
    out, edits = gestures.make_hold_to_toggle(mcf_136, 18, seconds=3)
    want, _ = gestures.set_hold_seconds(mcf_136, 18, 3)
    want, _ = gestures.set_phases(want, guidata.group_table(mcf_136)[18].start,
                                  gestures.PHASE_HELD)
    want, _ = gestures.set_repeat_index(want, 18, "held", 0)
    assert out == want and [e.structure for e in edits] == [
        "group-hold", "binding-phases", "group-repeat"]


def test_multi_binding_refuses_a_copy_that_repeats(mcf_136):
    b = _low_volts_bindings(mcf_136)[2]
    off = _binding_off(mcf_136, b, 2)
    mutated = _poke(mcf_136, off, mcf_136[off] | gestures.BINDING_REPEATS)
    with pytest.raises(gestures.GestureEditError, match="repeat bit"):
        gestures.make_hold_to_toggle(mutated, _LOW_VOLTS_GROUP)


def test_multi_binding_refuses_two_targets_in_one_group(mcf_136):
    b = _low_volts_bindings(mcf_136)[3]
    mutated = _poke(mcf_136, _binding_off(mcf_136, b, 5), 0x2E)      # another selector
    with pytest.raises(gestures.GestureEditError, match="one target"):
        gestures.make_hold_to_toggle(mutated, _LOW_VOLTS_GROUP)


def test_multi_binding_refuses_variants_with_different_gestures(mcf_136):
    mutated = mcf_136
    for b in _low_volts_bindings(mcf_136)[1::2]:                     # variant 1's copies
        mutated = _poke(mutated, _binding_off(mcf_136, b, 3), gestures.PHASE_TAP)
    assert gestures.describe_group(mutated, _LOW_VOLTS_GROUP).kinds == {
        0: "latch", 1: "tap-only"}
    with pytest.raises(gestures.GestureEditError, match="variants"):
        gestures.make_hold_to_toggle(mutated, _LOW_VOLTS_GROUP)


def test_multi_binding_refuses_a_gesture_that_is_not_a_release_toggle(mcf_136):
    mutated = mcf_136
    for b in _low_volts_bindings(mcf_136):
        mutated = _poke(mutated, _binding_off(mcf_136, b, 3), gestures.PHASE_PRESS)
    with pytest.raises(gestures.GestureEditError, match="press-only"):
        gestures.make_hold_to_toggle(mutated, _LOW_VOLTS_GROUP)


def test_multi_binding_refuses_variants_that_do_not_write_zero_and_one(mcf_136):
    set_v1 = _low_volts_bindings(mcf_136)[1]
    assert mcf_136[_binding_off(mcf_136, set_v1, 11)] == 1
    mutated = _poke(mcf_136, _binding_off(mcf_136, set_v1, 11), 0)   # both copies write 0
    with pytest.raises(gestures.GestureEditError, match="two-state"):
        gestures.make_hold_to_toggle(mutated, _LOW_VOLTS_GROUP)


def test_multi_binding_refuses_paired_copies_that_differ_elsewhere(mcf_136):
    read_v1 = _low_volts_bindings(mcf_136)[3]
    off = _binding_off(mcf_136, read_v1, 9)
    mutated = _poke(mcf_136, off, 0xD1)                              # read-back -> set
    with pytest.raises(gestures.GestureEditError, match="differ"):
        gestures.make_hold_to_toggle(mutated, _LOW_VOLTS_GROUP)


def test_multi_binding_refuses_a_group_that_cycles_its_variant(mcf_136):
    gs, _ = guidata.guidata_extent(mcf_136)
    off = gs + guidata.group_table(mcf_136)[_LOW_VOLTS_GROUP].offset + 1
    mutated = _poke(mcf_136, off, 0x00)
    assert gestures.describe_group(mutated, _LOW_VOLTS_GROUP).cycles_variant
    with pytest.raises(gestures.GestureEditError, match="cycles"):
        gestures.make_hold_to_toggle(mutated, _LOW_VOLTS_GROUP)


def test_the_hold_threshold_must_fit_the_nibble(mcf_136):
    with pytest.raises(gestures.GestureEditError, match="nibble"):
        gestures.set_hold_seconds(mcf_136, 18, 16)


def test_set_phases_refuses_a_stub_entry(mcf_136):
    """13.6's group 5 is the all-0xFF stub; writing a phase mask into it would invent a
    binding out of padding."""
    stub = guidata.group_table(mcf_136)[5]
    with pytest.raises(gestures.GestureEditError, match="stub"):
        gestures.set_phases(mcf_136, stub.start, gestures.PHASE_HELD)


# ---------------------------------------------------------------------------
# Glyph vertical placement (docs/CONTROL-BEHAVIOR.md section 7a)
# ---------------------------------------------------------------------------

def _glyph_top(box_y0, box_y1, cell, bearing):
    """Where the panel puts a v-align-2 label's glyph, transcribed from GuiCode.

    Block height for one line is the FONT CELL (`ldrb r1,[r3,#7]` @0x00255C30); the centre arm
    @0x00255D3C computes `y0 + ((y1-y0+1 - block) >> 1)` with a `bmi` that adds 1 first when the
    difference is negative (@0x00255D5C); the blitter then adds the glyph's y-bearing
    (`ldrb r1,[r3,#0xc]` / `add r2,r1` @0x00251138).
    """
    d = (box_y1 - box_y0) + 1 - cell
    if d < 0:
        d += 1
    return box_y0 + (d >> 1) + bearing


def test_icon_fonts_set_the_y_bearing_to_centre_the_glyph_in_its_cell(build):
    """`y = (cell - height) // 2` is the dominant convention in every icon/symbol font.

    It is the compensation that makes the panel's centring maths land a glyph in the middle of
    its box: the block height is the font CELL, so the bearing has to cancel the difference.
    Authoring a glyph without it is what put the Starlink logo 33 px above its button.

    Asserted as a strong majority rather than an absolute, because it is not universal --
    slot 24 has three glyphs at -15 and slot 9 a handful of strays. Those are real exceptions in
    the file, not decode errors, and pretending otherwise would make this test a lie.
    """
    from fireflymcf import glyphs, metrics
    for slot in (23, 24, 25, 9, 19):
        try:
            font = glyphs.font_for_slot(build, slot)
            cell = metrics.font_metrics(build, slot).height
        except Exception:
            continue
        deltas = []
        for code in range(font.first, font.last + 1):
            try:
                g = glyphs.decode_glyph(build, font.glyph_index(code))
            except Exception:
                continue
            deltas.append(g.y - (cell - g.height) // 2)
        assert deltas, f"slot {slot} decoded no glyphs"
        # The claim is "this is the norm", so assert the norm: zero is the most common delta,
        # and a clear majority sits within a pixel of it. 11.23's slot 24 is the weakest case at
        # 74% (seven genuine strays out of 27), which is why the floor is 0.70 and not higher.
        assert max(set(deltas), key=deltas.count) == 0, (
            f"slot {slot}: the commonest bearing is not the centring one")
        near = sum(1 for d in deltas if abs(d) <= 1)
        assert near / len(deltas) >= 0.70, (
            f"slot {slot}: only {near}/{len(deltas)} glyphs sit within 1px of the convention")


def test_the_starlink_logo_slot_follows_the_convention_except_its_v_align_1_glyphs(mcf_136):
    """Slot 27 is where the logo lives, so its own habits are the relevant precedent.

    19 of its 23 glyphs are EXACTLY on `(cell - h) // 2`. The four that are not are the Go
    Power! logo pieces, which object 1230 draws with **v-align 1 (top)** -- there the centring
    never runs and the bearing is a plain offset from the box top, so the convention does not
    apply to them. That split is the whole reason the authored logo needed a bearing of 37
    rather than the 3-ish its neighbours use.
    """
    from fireflymcf import glyphs, metrics
    font = glyphs.font_for_slot(mcf_136, 27)
    cell = metrics.font_metrics(mcf_136, 27).height
    assert cell == 128
    exact = off = 0
    for code in range(font.first, font.last + 1):
        try:
            g = glyphs.decode_glyph(mcf_136, font.glyph_index(code))
        except Exception:
            continue
        if g.y == (cell - g.height) // 2:
            exact += 1
        else:
            off += 1
    assert exact >= 15 and off <= 5, f"{exact} conforming, {off} not"


def test_the_climate_chevron_places_where_the_photo_oracle_shows_it(mcf_136):
    """The shipped calibration: slot 25, cell 72, glyph 28, bearing 22, box 140..195.

    The transcribed formula and `render.py`'s independent bitmap-centring agree on 154, and that
    is where CLIMATE.jpg puts it. If they ever disagree, one of the two models has drifted.
    """
    from fireflymcf import glyphs, metrics
    cell = metrics.font_metrics(mcf_136, 25).height
    g = glyphs.decode_glyph(mcf_136, glyphs.font_for_slot(mcf_136, 25).glyph_index(1))
    assert (cell, g.height, g.y) == (72, 28, 22)
    assert _glyph_top(140, 195, cell, g.y) == 154
    assert 140 + (56 - g.height) // 2 == 154          # render.py's rule, same answer


# --- Finding 57: `Low Volts` writes 0x2F and displays 0x2A -------------------

_VARIANT_SETTER = 0x20039908       # GuiCode's `setVariant(group, variant)` function pointer
_LOW_VOLTS_GROUP = 129
_ARMING_SELECTOR = 0x2F
_DISPLAY_SELECTOR = 0x2A


def _guicode(data):
    from fireflymcf import container
    section = next(s for s in container.parse_container(data).sections
                   if "GuiCode" in s.name)
    return data[section.offset:section.offset + section.length], 0x00240000


def _literal_loads(body, base):
    """Every `ldr rX,[pc,#imm]`, as `(address, value)`. T1 and T2 encodings."""
    import struct
    for off in range(0, len(body) - 4, 2):
        half = struct.unpack_from("<H", body, off)[0]
        if 0x4800 <= half < 0x5000:
            imm = (half & 0xFF) * 4
        elif half == 0xF8DF:
            imm = struct.unpack_from("<H", body, off + 2)[0] & 0xFFF
        else:
            continue
        slot = ((base + off + 4) & ~3) + imm - base
        if 0 <= slot <= len(body) - 4:
            yield base + off, struct.unpack_from("<I", body, slot)[0]


def test_low_volts_is_the_control_that_writes_the_arming_selector(mcf_136):
    """Object 31's `Low Volts` button emits `0x2F := 0 | 1` through group 129."""
    gs, _ = guidata.guidata_extent(mcf_136)
    table = guidata.object_tables(mcf_136)[1]
    recs = records.object_records(mcf_136, gs + table[31])

    container_rec = recs[12]
    assert container_rec.wtype == 5 and container_rec.value_slot == 45
    child = records.object_records(mcf_136, gs + table[container_rec.child_index])
    assert [r.label for r in child if r.label] == ["Low\nVolts"]

    assert _LOW_VOLTS_GROUP in gestures.touch_ids(mcf_136, 31)

    bindings = guidata.binding_table(mcf_136)
    wrote = {}
    for emission in gestures.describe_group(mcf_136, _LOW_VOLTS_GROUP).emissions:
        raw = bindings[emission.binding_index].raw
        wire = bytes(raw[k] for k in (7, 6, 5, 4, 11, 10, 9, 8))
        assert wire[2] == _ARMING_SELECTOR, f"group writes selector {wire[2]:#04x}"
        if wire[6] == 0xD1:                      # absolute set, not the read-back
            wrote[emission.variant] = wire[4] | (wire[5] << 8)
    assert wrote == {0: 0, 1: 1}


def test_the_low_volts_variant_is_externally_selected_and_inverted(mcf_136):
    """Group 129 carries the `0x80` design switch, and GuiCode sets the OPPOSITE state.

    Inversion is what makes it a toggle: showing "on" arms the tap that writes 0. Reading it the
    other way round would predict a button that can only ever re-assert itself.
    """
    import capstone
    body, base = _guicode(mcf_136)
    groups = guidata.group_table(mcf_136)
    assert groups[_LOW_VOLTS_GROUP].raw[1] == 0x80

    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    text = {i.address: (i.mnemonic, i.op_str)
            for i in md.disasm(body[0x0A176:0x0A1A8], 0x0024A176)}
    assert text[0x0024A176] == ("ldrsh.w", "r3, [r4, #0x54]")     # 2 * 0x2A
    assert text[0x0024A17A] == ("cmp", "r3, #1")
    assert text[0x0024A188] == ("bne.w", "#0x24a4e8")             # state 1 -> variant 0
    assert text[0x0024A190] == ("movs", "r1, #1")                 # state 0 -> variant 1
    assert text[0x0024A192] == ("movs", f"r0, #{_LOW_VOLTS_GROUP:#x}")

    far = {i.address: (i.mnemonic, i.op_str)
           for i in md.disasm(body[0x0A4E8:0x0A4F4], 0x0024A4E8)}
    assert far[0x0024A4EC] == ("movs", "r1, #0")
    assert far[0x0024A4EE] == ("movs", f"r0, #{_LOW_VOLTS_GROUP:#x}")


def test_the_variant_setter_is_only_ever_called_with_externally_selected_groups(mcf_136):
    """What identifies `0x20039908` as the setter, rather than assuming it.

    96 of 233 groups carry the `0x80` switch, so hitting only those across 40-odd call sites is
    not something an unrelated `(id, flag)` callee would do.
    """
    import capstone
    body, base = _guicode(mcf_136)
    groups = guidata.group_table(mcf_136)
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)

    called = set()
    for addr, value in _literal_loads(body, base):
        if value != _VARIANT_SETTER:
            continue
        window = list(md.disasm(body[addr - base:addr - base + 12], addr))
        for i, insn in enumerate(window):
            if insn.mnemonic == "blx":
                for prior in window[:i]:
                    if prior.mnemonic == "movs" and prior.op_str.startswith("r0, #"):
                        called.add(int(prior.op_str.split("#")[1], 0))
                break
    assert len(called) > 30, f"only {len(called)} ids found -- the scan is broken"
    assert _LOW_VOLTS_GROUP in called
    external = {g.index for g in groups if g.raw[1] == 0x80}
    assert called <= external, f"not externally-selected groups: {sorted(called - external)}"


def test_each_low_volts_tap_is_write_then_readback_then_reply():
    """PC.39: what repaints the button is the control's own read-back, not selector 0x2A.

    Each variant carries a `D1` set and a `D3` read of the same selector, and the G12 answers the
    read within milliseconds. This pins the loop end to end -- the binding pair in the file, and
    the three round-trips on the wire -- because Finding 57 first attributed the repaint to a
    selector that has never been on this bus, and the coach refuted it.
    """
    import re

    bindings = guidata.binding_table(mcf_136_bytes())
    functions = set()
    for emission in gestures.describe_group(mcf_136_bytes(), _LOW_VOLTS_GROUP).emissions:
        raw = bindings[emission.binding_index].raw
        wire = bytes(raw[k] for k in (7, 6, 5, 4, 11, 10, 9, 8))
        functions.add((emission.variant, wire[6]))
    assert functions == {(0, 0xD1), (1, 0xD1), (0, 0xD3), (1, 0xD3)}, (
        f"each variant should set and read back: {sorted(functions)}")

    frames = []
    with open("captures/pc39-low-volts-trigger-2026-08-25.log", errors="ignore") as handle:
        for line in handle:
            match = re.search(r"\((\d+\.\d+)\) can1 (19FED99F|195FCE9C)#([0-9A-F]{16})", line)
            if match:
                frames.append((float(match.group(1)), match.group(2),
                               bytes.fromhex(match.group(3))))

    rounds = []
    for i, (when, src, payload) in enumerate(frames):
        if not (src == "19FED99F" and payload[2] == _ARMING_SELECTOR and payload[6] == 0xD1):
            continue
        wrote = payload[4] | (payload[5] << 8)
        reply = next(((t, p) for t, s, p in frames[i:i + 4]
                      if s == "195FCE9C" and p[0] == _ARMING_SELECTOR), None)
        assert reply is not None, f"no read-back reply after the write at {when}"
        assert reply[1][2] | (reply[1][3] << 8) == wrote, "the G12 echoed a different value"
        rounds.append((wrote, reply[0] - when))

    assert [v for v, _ in rounds] == [1, 0, 1], (
        f"the taps should alternate, proving the variant tracks the live value: {rounds}")
    assert all(0 < gap < 0.010 for _, gap in rounds), f"reply latencies: {rounds}"


def test_the_supposed_display_selector_is_still_absent_from_every_capture():
    """`0x2A` never appears, which is what made Finding 57's first read look supportable.

    Kept as the standing record of the refuted attribution: the selector really is absent, so
    absence was never the error -- reading `[r4,#0x54]` as `0x2A` was.
    """
    import collections
    import glob
    import os
    import re

    # Exclude the active-probe captures: the `D3` read-back forces a one-shot report for ANY
    # selector (`BUS-BASELINE.md` PC.49), so a hit there would mean "we asked", not "the coach
    # sent it" -- and this test's whole claim is that the coach never sends 0x2A.
    from tests.test_g12code import PROBE_CAPTURES

    counts = collections.Counter()
    for path in sorted(glob.glob("captures/*.log")):
        if os.path.basename(path) in PROBE_CAPTURES:
            continue
        with open(path, errors="ignore") as handle:
            for line in handle:
                match = re.search(r"195FCE[0-9A-F]{2}#([0-9A-F]{16})", line)
                if match:
                    counts[bytes.fromhex(match.group(1))[0]] += 1
    assert counts[_ARMING_SELECTOR] > 1000, "no 0x2F frames -- the scan is broken"
    assert counts[_DISPLAY_SELECTOR] == 0


def test_the_group_action_discriminator_is_the_lcd_scanner_bound(mcf_136):
    """The `id < group_count` split that decides whether a touch id is a binding group or a
    GuiCode action id is a real instruction, not a tooling heuristic: the LCD Firmware's
    control scanner bounds its loop with the group count at 0x21BAD4-0x21BADA
    (`docs/CONTROL-BEHAVIOR.md` §1, `docs/MCF-FORMAT.md` §10 item 8).

    Pinning the three-instruction sequence keeps the proven-from-code citation honest: if a
    build shifts it, `is_group_id`'s premise -- and every reader that separates the two uses
    of the 0x0C-record tail u16 -- is no longer anchored to code and the test says so. The
    bound is read as a *byte* count (`ldrb.w`), which is why 233 (0xE9) fits.
    """
    import capstone

    from fireflymcf import container

    c = container.parse_container(mcf_136)
    lcd = next(s for s in c.sections if s.name == "LCD Firmware")
    base = 0x00210000
    off = lcd.offset + (0x0021BAD4 - base)
    seq = mcf_136[off:off + 8]
    # ldrb.w r3,[r8] ; cmp r3,r4 ; bhi <fwd>
    assert seq == bytes.fromhex("98f80030a34202d8")

    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    ins = list(md.disasm(seq, 0x0021BAD4))
    assert [(i.mnemonic, i.op_str) for i in ins[:3]] == [
        ("ldrb.w", "r3, [r8]"),   # r3 = group count
        ("cmp", "r3, r4"),        # r4 = the current touch id
        ("bhi", "#0x21bae2"),     # process only while count > id, i.e. id < count
    ]

    # The bound is exactly the group-table length, so the tooling threshold is that bound,
    # not a fitted constant: the last group index is a group, the count itself is not.
    n = len(guidata.group_table(mcf_136))
    assert n == 233
    assert gestures.is_group_id(mcf_136, n - 1)
    assert not gestures.is_group_id(mcf_136, n)
