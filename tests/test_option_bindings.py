"""What an OPTIONS entry binds to, and how GuiData names a G12 channel.

The task brief for this work assumed each OPTIONS row carried its own 15FCE selector
byte in its binary fields. It does not -- see `test_the_options_screen_holds_no_selector`
and `guidata.find_option_bindings`. These tests assert what the bytes actually support:
the group/binding chain that ties a labelled control to a set of channels, and the
channel references themselves.

The `name -> selector` half was recovered on 2026-08-15 by a different route entirely --
code adjacency in GuiCode, not a GuiData field (`src/fireflymcf/config.py`'s selector
join, `tests/test_config_offsets.py`) -- so the negatives below stand as written and the
first two of the brief's four tests now pass.
"""
import struct

import pytest

from tests import answer_key
from fireflymcf import guidata


# ---------------------------------------------------------------------------
# The three header tables
# ---------------------------------------------------------------------------


def test_header_tables_resolve_to_whole_numbers_of_entries(mcf_136):
    """The GuiData header's three table descriptors, read off 13.6's bytes.

    header +0x08 `10 00 e9 00 30 00 48 07` -> group table at 0x0030, 0x0748 = 233*8
    header +0x10 `20 00 00 00 78 07 f8 22` -> binding table at 0x0778, 0x22F8 = 746*12
    header +0x20 `41 01 00 2a 70 2a 50 09` -> variable table at 0x2A70, 0x0950 = 298*8
    """
    groups = guidata.group_table(mcf_136)
    bindings = guidata.binding_table(mcf_136)
    variables = guidata.variable_table(mcf_136)
    assert (len(groups), len(bindings), len(variables)) == (233, 746, 298)
    assert (groups[0].offset, bindings[0].offset, variables[0].offset) == (
        0x0030, 0x0778, 0x2A70)


@pytest.mark.parametrize("version,counts", [
    ("11.23", (230, 732, 291)),
    ("13.6", (233, 746, 298)),
    ("14.7", (234, 747, 310)),
])
def test_every_build_carries_the_same_three_tables(corpus, version, counts):
    data = corpus[version]["mcf"]
    got = (len(guidata.group_table(data)),
           len(guidata.binding_table(data)),
           len(guidata.variable_table(data)))
    assert got == counts


def test_binding_entries_two_to_seven_are_the_interior_lights(mcf_136):
    """The six entries the module docstring quotes, asserted byte for byte.

    Each is `db fe 01 02 00 fb ff <instance>` + `ff ff 00 ff`: DGN low 0xFEDB
    (DC_DIMMER_COMMAND_2) with the channel in byte 7.
    """
    entries = guidata.binding_table(mcf_136)[2:8]
    assert [e.raw.hex(" ") for e in entries] == [
        "db fe 01 02 00 fb ff 16 ff ff 00 ff",
        "db fe 01 02 00 fb ff 18 ff ff 00 ff",
        "db fe 01 02 00 fb ff 19 ff ff 00 ff",
        "db fe 01 02 00 fb ff 1a ff ff 00 ff",
        "db fe 01 02 00 fb ff 20 ff ff 00 ff",
        "db fe 01 02 00 fb ff 22 ff ff 00 ff",
    ]
    assert [e.channel for e in entries] == [22, 24, 25, 26, 32, 34]
    assert all(e.dgn_low == guidata.DGN_DC_DIMMER_COMMAND_2 for e in entries)


def test_group_one_selects_the_first_twenty_six_bindings(mcf_136):
    """Group table entry 1 is `11 80 0c 80 02 00 1a 00`: start 2, count 26."""
    group = guidata.group_table(mcf_136)[1]
    assert (group.start, group.count) == (2, 26)


# ---------------------------------------------------------------------------
# The join: a labelled control -> group -> binding entries -> channels
# ---------------------------------------------------------------------------


def _by_screen(data):
    out = {}
    for control in guidata.find_control_groups(data):
        out.setdefault(control.screen_title.strip(), {})[control.label.strip()] = control
    return out


def test_the_light_master_button_selects_exactly_the_interior_master(mcf_136):
    """`Light Master` / `ON` -> group 1 -> binding entries 2..27 -> seven channels.

    The seven are the UNION over the group's entries: byte 2 splits them into variant
    copies and no single copy holds all seven (0x01 gives six, 0x11 adds 35). Nothing
    in this chain matches on channel numbers or on the answer key -- the label comes
    from the display list, the group index from the button's 0x0C record, the channels
    from the binding entries the group selects.
    """
    control = _by_screen(mcf_136)["Light Master"]["ON"]
    assert control.group == 1
    assert control.has_full_level
    assert control.channels == sorted(answer_key.INTERIOR_MASTER)


def test_the_exterior_master_button_covers_the_exterior_master(mcf_136):
    """`Exterior Master` / `ON` -> group 216 -> union of channels 21, 23 and 35.

    21 and 23 are `answer_key.EXTERIOR_MASTER`; 35 arrives from one of the group's
    variant copies. A superset, not equality, is asserted because the union is all
    GuiData supports -- see `test_byte_two_variants_do_not_settle_channel_35`.
    """
    control = _by_screen(mcf_136)["Exterior Master"]["ON"]
    assert control.group == 216
    assert set(control.channels) >= set(answer_key.EXTERIOR_MASTER)
    assert control.channels == [21, 23, 35]


def test_byte_two_variants_do_not_settle_channel_35(corpus):
    """Byte 2 splits a group into variant copies, and its meaning is NOT known.

    Channel 35 appears in the union of both master groups, which invites "GuiData
    contradicts the answer key's disjoint masters". It does not. Read per variant,
    13.6 has 0x11 as the copy carrying 35 on both sides, but 11.23 REVERSES the
    polarity on the exterior side and adds a third variant on the interior one -- so
    no consistent rule assigns 35 to a master, and under either 11.23 variant the two
    masters are disjoint. The answer key came off a live CAN capture and stands; this
    test exists to stop the wrong reading being drawn again.
    """
    def variants(data, group_index):
        groups, bindings = guidata.group_table(data), guidata.binding_table(data)
        out = {}
        for entry in guidata.group_bindings(groups, bindings, group_index):
            if entry.channel is not None:
                out.setdefault(entry.raw[2], set()).add(entry.channel)
        return out

    interior_136 = variants(corpus["13.6"]["mcf"], 1)
    exterior_136 = variants(corpus["13.6"]["mcf"], 216)
    assert interior_136 == {0x01: {22, 24, 25, 26, 32, 34},
                            0x11: {22, 24, 25, 26, 32, 34, 35}}
    assert exterior_136 == {0x01: {21, 23}, 0x11: {21, 23, 35}}

    interior_1123 = variants(corpus["11.23"]["mcf"], 61)
    exterior_1123 = variants(corpus["11.23"]["mcf"], 216)
    assert interior_1123 == {0x01: {22, 24, 25, 32, 34},
                             0x11: {22, 24, 25, 32, 34, 35},
                             0x21: {22, 24, 32, 35}}
    # The reversal: on 11.23's exterior side it is 0x01, not 0x11, that carries 35.
    assert exterior_1123 == {0x01: {21, 23, 35}, 0x11: {21, 23}}
    assert 35 in exterior_1123[0x01] and 35 not in interior_1123[0x01]
    assert 35 in interior_1123[0x11] and 35 not in exterior_1123[0x11]
    # ... so each 11.23 variant keeps the two masters disjoint, as the coach recorded.
    for variant in (0x01, 0x11):
        assert not interior_1123[variant] & exterior_1123[variant]


@pytest.mark.parametrize("version", ["11.23", "13.6", "14.7"])
@pytest.mark.parametrize("screen", ["Light Master", "Exterior Master"])
def test_on_and_off_buttons_pair_up_on_the_same_channels(corpus, version, screen):
    """The falsifiable half of the group hypothesis, checked on every build.

    A master's ON and OFF buttons carry consecutive group indices. The ON group's
    dimmer entries reach 0xC8 (200 = 100% in RV-C half-percent units) and every one of
    the OFF group's is 0x00 -- and both groups name the same channels. Twelve
    button/group pairs across three builds, no exceptions.
    """
    controls = _by_screen(corpus[version]["mcf"])[screen]
    on, off = controls["ON"], controls["OFF"]
    assert off.group == on.group + 1
    assert on.has_full_level and not off.has_full_level
    assert off.all_levels_zero and not on.all_levels_zero
    assert on.channels == off.channels
    assert on.channels


def test_the_level_flags_do_not_claim_a_control_switches_off(mcf_136):
    """`has_full_level` / `all_levels_zero` describe bytes, not behaviour.

    Most groups hold both a zero and a full-level entry, so they report
    `has_full_level` True and `all_levels_zero` False together -- "Water Pump" is one.
    Anyone reading those as "turns on" / "does not turn off" would be wrong about the
    file, which is why the fields are not named that way.
    """
    controls = {c.group: c for c in guidata.find_control_groups(mcf_136)}
    water_pump = controls[44]
    assert water_pump.channels == [44]
    assert water_pump.levels == (0x00, 0xC8)
    assert water_pump.has_full_level and not water_pump.all_levels_zero
    # and one that reaches has_full_level on a value that is not a brightness at all
    assert controls[80].levels == (0x00, 0xFA)
    assert controls[80].has_full_level


@pytest.mark.parametrize("version,expected", [("13.6", None), ("14.7", 5)])
def test_group_five_is_a_stub_in_the_build_without_that_screen(corpus, version,
                                                               expected):
    """13.6 has no Fresh Tank Heater screen and its group 5 is one `ff ff ...` entry
    with DGN 0xFFFF and no channel; 14.7 has the screen and group 5 names channel 5.
    The group table tracks what the build actually offers."""
    data = corpus[version]["mcf"]
    groups, bindings = guidata.group_table(data), guidata.binding_table(data)
    entries = guidata.group_bindings(groups, bindings, 5)
    channels = {e.channel for e in entries if e.channel is not None}
    if expected is None:
        assert [e.raw.hex(" ") for e in entries] == ["ff " * 11 + "ff"]
        assert not channels
    else:
        assert channels == {expected}


# GuiData's own label for a control, against the coach's name for the channel that
# control's group commands. The left half is read out of the display list, the right
# half was read off the vehicle; neither knows about the other.
NAMED_CONTROLS = {
    "Fresh Tank Heater": "TANK HEAT FRESH",
    "Grey Tank Heater": "TANK HEAT GREY",
    "Black Tank Heater": "TANK HEAT BLACK",
    "Grey 2 Tank Heater": "TANK HEAT GREY 2",
    "Water\nPump": "WATER PUMP",
}


def test_labelled_controls_name_the_channel_the_pinout_names(mcf_147):
    """14.7 is the build that carries all five: 13.6 has no Fresh Tank Heater screen.

    Labels are not unique -- "E" labels three single-channel controls in 14.7, on
    channels 33, 22 and 2 -- so every control under a label is collected and required
    to agree before it is used. A silent `{label: channel}` collapse would keep only
    the last and hide any disagreement.
    """
    single = {}
    for control in guidata.find_control_groups(mcf_147):
        if len(control.channels) == 1:
            single.setdefault(control.label, []).append(control.channels[0])
    missing = [label for label in NAMED_CONTROLS if label not in single]
    assert not missing, f"labels not found as single-channel controls: {missing}"
    for label, pin_name in NAMED_CONTROLS.items():
        channels = set(single[label])
        assert len(channels) == 1, (
            f"{label!r} labels controls on disagreeing channels: {sorted(channels)}")
        channel = channels.pop()
        assert answer_key.CHANNELS[channel] == pin_name, (
            f"{label!r} resolves to channel {channel}, which the coach calls "
            f"{answer_key.CHANNELS.get(channel)!r}, not {pin_name!r}")
    # the collision that motivates the agreement check above
    assert sorted(set(single["E"])) == [2, 22, 33]


# ---------------------------------------------------------------------------
# Channel references
# ---------------------------------------------------------------------------


def test_channel_references_cover_the_known_channels(mcf_136):
    """Channel numbers in GuiData are the same numbers as the RV-C instances and the
    PDF pins. Every channel exercised on the coach must appear somewhere."""
    refs = guidata.find_channel_references(mcf_136)
    missing = [ch for ch in answer_key.CHANNELS if ch not in refs]
    assert not missing, f"channels never referenced in GuiData: {sorted(missing)}"


def test_channel_references_point_at_the_bytes_that_name_the_channel(mcf_136):
    """Not just "channel 22 is mentioned" -- the offsets are checkable.

    Binding entry 2 sits at section 0x0778 + 2*12 = 0x0790 and its channel byte is at
    +7, so 0x0797 must be one of channel 22's references, and the byte there must be 22.
    """
    refs = guidata.find_channel_references(mcf_136)
    at = 0x0778 + 2 * 12 + guidata.BINDING_CHANNEL_FIELD
    assert at in refs[22]
    start, _ = guidata.guidata_extent(mcf_136)
    assert mcf_136[start + at] == 22
    assert refs[22] == sorted(refs[22])
    # and every offset really does hold the channel it is filed under
    for channel, offsets in refs.items():
        assert {mcf_136[start + off] for off in offsets} == {channel}


def test_master_group_channels_are_all_referenced(mcf_136):
    refs = guidata.find_channel_references(mcf_136)
    for ch in answer_key.INTERIOR_MASTER + answer_key.EXTERIOR_MASTER:
        assert ch in refs, f"master-group channel {ch} not referenced"


def test_channel_reference_coverage_is_not_a_free_pass(mcf_136):
    """The variable table enumerates DC_DIMMER_STATUS_3 instances 1..44 exhaustively,
    so "every answer-key channel is referenced" is nearly automatic from that source
    alone. The discriminating source is the command table, whose instances are sparse:
    it names 64 of the 122 values it could, and nothing above 122.
    """
    commanded = guidata.find_channel_references(mcf_136, sources=("binding",))
    assert len(commanded) == 64
    assert max(commanded) == 122
    assert set(commanded) < set(guidata.find_channel_references(mcf_136))


@pytest.mark.parametrize("version", ["11.23", "13.6", "14.7"])
def test_the_air_conditioning_channels_are_never_commanded_as_dimmers(corpus, version):
    """Channels 9, 11, 12 and 43 have no DC_DIMMER_COMMAND_2 binding in any build.

    Those are exactly the four the coach names A/C COOL, A/C FAN HIGH, A/C FAN LOW and
    A/C COOL. INFERENCE, not proof: the panel drives them through a thermostat DGN
    rather than per-channel dimmer commands. The asserted fact is only their absence.
    """
    commanded = guidata.find_channel_references(corpus[version]["mcf"],
                                                sources=("binding",))
    assert not {9, 11, 12, 43} & set(commanded)
    # ... while the neighbouring channels the coach does switch are all present.
    assert {5, 6, 7, 8, 21, 22, 23, 24, 25, 32, 34, 35} <= set(commanded)


# ---------------------------------------------------------------------------
# The negatives, recorded so nobody repeats the search
# ---------------------------------------------------------------------------


def test_the_options_screen_holds_no_selector(mcf_136):
    """The brief's Step 3 -- scan each option widget's fields for its selector byte --
    cannot work: not one of the six bytes occurs anywhere in the screen object.
    """
    start, _ = guidata.guidata_extent(mcf_136)
    screen = mcf_136[start + 0x70AD4:start + 0x70E24]
    present = {b for b in screen} & {sel for _, sel in answer_key.OPTIONS}
    assert not present, f"selector bytes found after all: {sorted(map(hex, present))}"
    assert len(screen) == 0x350   # the span really is the OPTIONS screen object
    assert b"Black Tank" in screen


@pytest.mark.parametrize("version,entries", [
    ("11.23", 732), ("13.6", 746), ("14.7", 747),
])
def test_the_binding_table_never_carries_the_config_dgn(corpus, version, entries):
    """15FCE is declared in the variable table and commanded nowhere: no binding entry
    in any build has it, so the option selectors are not reachable by group index.

    The entry count is asserted inline so the negative stands on its own -- "none of
    them" is worthless if the table were empty.
    """
    bindings = guidata.binding_table(corpus[version]["mcf"])
    assert len(bindings) == entries
    assert not [b for b in bindings if b.dgn_low == guidata.DGN_CONFIG]
    assert [v for v in guidata.variable_table(corpus[version]["mcf"])
            if v.dgn_low == guidata.DGN_CONFIG]


def test_the_variable_table_declares_the_six_option_selectors(mcf_136):
    """13.6 entries 283..288 read `ce 5f 1d 02 {e9,e4,e5,e3,eb,ec}` -- the six
    selectors of `answer_key.OPTIONS`, contiguous, but in an order that is neither the
    screen's nor stable across builds, and with no name attached.
    """
    run = guidata.variable_table(mcf_136)[283:289]
    assert [v.raw.hex(" ") for v in run] == [
        "ce 5f 1d 02 e9 00 00 00",
        "ce 5f 1d 02 e4 00 00 00",
        "ce 5f 1d 02 e5 00 00 00",
        "ce 5f 1d 02 e3 00 00 00",
        "ce 5f 1d 02 eb 00 00 00",
        "ce 5f 1d 02 ec 00 00 00",
    ]
    assert {v.value for v in run} == {sel for _, sel in answer_key.OPTIONS}
    assert [v.value for v in run] != [sel for _, sel in answer_key.OPTIONS]


def test_the_option_run_is_not_a_stable_key(mcf_147):
    """14.7 shows five options but keeps six entries in the same slots, so position in
    the run cannot be the thing that names an option."""
    run = guidata.variable_table(mcf_147)[283:289]
    assert [v.value for v in run] == [0xE7, 0xE4, 0xE5, 0xE3, 0xEB, 0xEC]
    screen = guidata.find_screens_by_title(mcf_147, "OPTIONS")[-1]
    assert [w.label.strip() for w in screen.widgets] == [
        "Heat Pump", "Bunk Accent", "Cargo Light", "Starlink Power", "Black Tank"]


# ---------------------------------------------------------------------------
# The brief's own tests. The first two pass since 2026-08-15 (`config.py`'s selector
# join); the last two stay strict xfails, one per remaining gap, so those gaps stay
# visible -- `channel` and `hides_control` are not in the file and are not invented.
# ---------------------------------------------------------------------------


def test_every_option_has_a_binding(mcf_136):
    """Recovered 2026-08-15 (see `guidata.find_option_bindings`), so no longer an xfail.

    A **superset**, not an equality: `answer_key.OPTIONS` is the subject coach's OPTIONS
    *screen* -- its Main Options tab -- while the extractor walks every OPTIONS-labelled tab
    in the file and so also returns the Electrical Options rows (AGS/AES/AES Single Output,
    Progressive Inverter). Asserting equality would mean deleting real rows to match the
    answer key.
    """
    bindings = guidata.find_option_bindings(mcf_136)
    assert {name for name, _ in answer_key.OPTIONS} <= {b.name for b in bindings}


def test_selectors_match_the_live_protocol(mcf_136):
    bindings = {b.name: b.selector for b in guidata.find_option_bindings(mcf_136)}
    for name, selector in answer_key.OPTIONS:
        assert bindings[name] == selector, f"{name}: expected {selector:#04x}"


def test_bath_light_pair_binds_to_channels_25_and_26(mcf_136):
    """RECOVERED 2026-09-02 (`optiongates.py`; was `xfail(strict)` from 2026-08-15). There is
    no stored channel: each of 0xE3/0xE4 keys a type-6 switch on every LIGHTS page whose ON
    arm is a 'Bath' control commanding channel 25 (object 370, group 25) or 26 (object 374,
    group 26). The tap handlers' companion OFF templates (Finding 23, PC.33) give the same
    two numbers from GuiCode; `tests/test_option_gates.py` pins both routes."""
    by_sel = {b.selector: b for b in guidata.find_option_bindings(mcf_136)}
    for selector, channel in answer_key.OPTION_CHANNEL_BINDING.items():
        assert by_sel[selector].channel == channel


def test_boolean_options_hide_their_control(mcf_136):
    """RECOVERED 2026-09-02 (`optiongates.py`; was `xfail(strict)`). There is no visibility
    flag: 0xE5 keys switches whose ON arm is object 376 ('Bunk Accent') and whose 0 arm is
    the empty default 375; 0xEC keys the HOME tank switches 912-921 (802 'BLACK' gauge vs
    801 empty). `hides_control` is that structural shape, read by `optiongates.hides_control`."""
    by_sel = {b.selector: b for b in guidata.find_option_bindings(mcf_136)}
    for selector in answer_key.OPTION_HIDES_CONTROL:
        assert by_sel[selector].hides_control
