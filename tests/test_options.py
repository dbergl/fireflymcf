"""The option -> value-slot -> register binding map (GUICODE.md Findings 16-17).

Known-truth values below are cited in the task-2 brief and traced independently in
`tests/test_layout.py` (Findings 16-17): Bath Fan is slot 109 / SRAM 0x20039B49, AGS is
slot 37 / 0x2003A38C, Progressive Inverter is slot 113 / 0x2003A62B, and the fourth
("black") tank option is slot 112 / 0x2003A4B4. This file's own contribution is the
NAME each register belongs to, found by pairing OPTIONS-page checkboxes to their
switches rather than assuming the pairing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from fireflymcf import options


def _by_name(bindings: list[options.OptionBinding]) -> dict[str, options.OptionBinding]:
    """{name: binding}, asserting the task's de-dup contract holds (no name collides
    across two different slots -- if it did, this dict would silently keep only one)."""
    out: dict[str, options.OptionBinding] = {}
    for b in bindings:
        if b.name in out:
            assert out[b.name].value_slot == b.value_slot, (
                f"{b.name!r} bound to two different slots: "
                f"{out[b.name].value_slot} and {b.value_slot}")
        out[b.name] = b
    return out


def test_option_bindings_reproduces_the_known_registers(mcf_136):
    """The four cited (name -> (slot, register)) pairs, looked up BY NAME -- not by
    position or by assuming a particular OPTIONS object. A stubbed `option_bindings`
    returning `[]` fails every one of these (verified during development, not
    reproduced here as a test since gutting the real function is not something a
    permanent test should do)."""
    by_name = _by_name(options.option_bindings(mcf_136))

    bath_fan = by_name["Bath Fan"]
    assert (bath_fan.value_slot, bath_fan.register) == (109, 0x20039B49)

    ags = by_name["AGS"]
    assert (ags.value_slot, ags.register) == (37, 0x2003A38C)

    inverter = by_name["Progressive Inverter"]
    assert (inverter.value_slot, inverter.register) == (113, 0x2003A62B)

    # The brief flags that the black-tank option's on-screen label may not literally
    # read "black-tank" -- it reads "Black Tank" on 13.6's OPTIONS page.
    black_tank = by_name["Black Tank"]
    assert (black_tank.value_slot, black_tank.register) == (112, 0x2003A4B4)


def test_option_bindings_black_tank_is_findable_by_register_alone(mcf_136):
    """Belt-and-braces per the brief: even if a future build spells the label
    differently, the (slot, register) pair is what actually identifies the option."""
    bindings = options.option_bindings(mcf_136)
    matches = [b for b in bindings if (b.value_slot, b.register) == (112, 0x2003A4B4)]
    assert matches, "no binding resolves to the black-tank slot/register pair"


def test_option_bindings_vtype_matches_variable_register(mcf_136):
    """`vtype` is whatever `layout.variable_register` reports for that slot -- not
    assumed to be boolean for every option. Bath Fan, Progressive Inverter and Black
    Tank are booleans (type 0); AGS is type 1, since its slot (37) is shared by a
    three-way AGS/AES/AES Single Output selection, not a plain on/off flag
    (`tests/test_layout.py::test_ags_generator_section_is_option_gated` pins the same
    (0x2003A38C, 1) pair independently)."""
    from fireflymcf import layout
    by_name = _by_name(options.option_bindings(mcf_136))
    for name in ("Bath Fan", "Progressive Inverter", "Black Tank"):
        assert by_name[name].vtype == 0, f"{name} has vtype {by_name[name].vtype}, not bool"
    assert by_name["AGS"].vtype == 1
    for name, slot in (("Bath Fan", 109), ("AGS", 37),
                       ("Progressive Inverter", 113), ("Black Tank", 112)):
        _, vtype = layout.variable_register(mcf_136, slot)
        assert by_name[name].vtype == vtype


def test_option_bindings_tags_the_options_object_it_came_from(mcf_136):
    """`options_obj` is a real object-table index that itself carries the OPTIONS
    header, not an arbitrary number -- ties each binding back to the page it renders
    on for anything that wants to walk that page."""
    from fireflymcf import guidata
    gs, ge = guidata.guidata_extent(mcf_136)
    table = guidata.object_tables(mcf_136)[1]
    by_name = _by_name(options.option_bindings(mcf_136))
    for name, obj in (("Bath Fan", None), ("AGS", None)):
        b = by_name[name]
        header = options._first_label(options._own_records(mcf_136, gs, ge, table,
                                                            b.options_obj))
        assert header is not None and header.strip() == "OPTIONS"


# The tab/section labels on obj 47 (13.6): none of these are options -- they are the
# nav buttons ("Floorplans", "Main\nOptions", "Electrical Options", "Go Power!\n
# Settings") that follow the LAST real row (Progressive Inverter, slot 113) with no
# further switch between them and the end of the object. A pairing scan that does not
# stop at the first label found after a switch -- e.g. "attach every label to the last
# type-6 switch seen anywhere before it", which has no upper bound at all -- would
# walk straight through the hit-region/divider records after Progressive Inverter and
# misattach one or more of these tab labels to slot 113 as if they were options. On
# this corpus every REAL row's label happens to sit immediately after its own switch,
# which is why a plain "did the four known options come out right" check cannot
# distinguish correct bounded pairing from that bug -- only these never-real-options
# tab labels can, since they are the ones a naive scan would wrongly pull in.
OPTIONS_TAB_LABELS = ("Floorplans", "Main\nOptions", "Electrical Options",
                     "Go Power!\nSettings")


def test_option_bindings_pairing_is_not_the_running_last_switch(mcf_136):
    """The trap the brief calls out by name: obj 47's AGS/AES/AES Single Output rows
    are three separate switches that all happen to read the SAME slot (37) -- a
    'nearest preceding switch' bug would instead show up as two or more DIFFERENT
    names collapsing onto slots that don't belong to them (e.g. Progressive Inverter,
    the row right after, grabbing 37 instead of its own 113). Checking Progressive
    Inverter's slot is 113 and not 37 is exactly the regression a mis-pairing
    heuristic would fail."""
    by_name = _by_name(options.option_bindings(mcf_136))
    assert by_name["AGS"].value_slot == 37
    assert by_name["Progressive Inverter"].value_slot == 113
    assert by_name["Progressive Inverter"].value_slot != by_name["AGS"].value_slot


def test_option_bindings_does_not_bind_the_tab_selector_labels(mcf_136):
    """A naive 'first label anywhere below the switch, unbounded' scan would walk past
    Progressive Inverter (obj 47's last real switch) all the way to the tab buttons at
    the bottom of the object and misattach one of them to slot 113. None of the four
    nav-tab labels are options and none may appear as an `OptionBinding.name` -- this
    is the assertion that actually distinguishes bounded own-row pairing from an
    unbounded last-switch scan (see `OPTIONS_TAB_LABELS`'s comment for why the four
    known-truth checks alone cannot tell the two apart)."""
    names = {b.name for b in options.option_bindings(mcf_136)}
    present = names & set(OPTIONS_TAB_LABELS)
    assert not present, f"tab/section labels wrongly bound as options: {sorted(present)}"


def test_option_bindings_returns_dataclass_instances(mcf_136):
    bindings = options.option_bindings(mcf_136)
    assert bindings, "no OPTIONS-page bindings found at all"
    for b in bindings:
        assert isinstance(b, options.OptionBinding)
        assert isinstance(b.name, str) and b.name
        assert isinstance(b.value_slot, int)
        assert isinstance(b.register, int) and b.register != 0
        assert isinstance(b.vtype, int)
        assert isinstance(b.options_obj, int)


def test_option_bindings_runs_on_1123(corpus):
    """11.23 ships only the Renogy Battery option, on its own OPTIONS-labelled page."""
    bindings = options.option_bindings(corpus["11.23"]["mcf"])
    names = {b.name for b in bindings}
    assert names == {"Renogy Battery"}


def test_option_bindings_runs_on_147(corpus):
    """14.7 ships the shipped 'Starlink Power' example CLAUDE.md points at, and no
    'Renogy Battery' page (that is 13.6/11.23's Go Power! tab; 14.7's equivalent tab
    offers Lithionics Batteries instead)."""
    data = corpus["14.7"]["mcf"]
    bindings = options.option_bindings(data)
    names = {b.name for b in bindings}
    assert "Starlink Power" in names
    assert "Black Tank" in names
    assert "Renogy Battery" not in names
    # the false-positive trap this module's docstring documents: neither of the
    # decorative-container labels a recursive pairing once produced here
    assert not ({"1", "2", "D"} & names)


def test_floorplan_options_known_truth(mcf_136):
    floorplans = options.floorplan_options(mcf_136)
    assert floorplans[8] == "WD"
    assert floorplans[1] == "RT"


def test_floorplan_options_matches_the_independent_object_table_oracle(mcf_136):
    """Cross-checks the switch-based result against `guidata.find_floorplan_table` (a
    completely different mechanism -- an object-table anchor scan) for every
    confirmed entry, not just the two the brief names."""
    from fireflymcf import guidata
    floorplans = options.floorplan_options(mcf_136)
    for fp in guidata.find_floorplan_table(mcf_136):
        if fp.confirmed:
            assert floorplans.get(fp.value) == fp.code


def test_floorplan_options_is_a_plain_value_to_name_dict(mcf_136):
    floorplans = options.floorplan_options(mcf_136)
    assert isinstance(floorplans, dict)
    assert all(isinstance(k, int) for k in floorplans)
    assert all(isinstance(v, str) and v for v in floorplans.values())


def test_floorplan_options_on_1123_agrees_with_the_handler_aligned_table(mcf_1123):
    """11.23's names now come off its floorplan-name switch (objects 852/853, unmasked
    2026-08-30 when the corrected 0-based map let the oracle match it), and that result
    must agree with `guidata.find_floorplan_table`'s handler-aligned label run -- two
    independent structures, one wire-confirmed map (BUS-BASELINE PC.40): 0=Ship Mode,
    5=3WD/4WD."""
    from fireflymcf import guidata
    floorplans = options.floorplan_options(mcf_1123)
    assert floorplans[0] == "Ship Mode"
    assert floorplans[5] == "3WD / 4WD"
    expected = {fp.value: fp.code
                for fp in guidata.find_floorplan_table(mcf_1123) if fp.confirmed}
    assert floorplans == expected


def test_slot_values_floorplan_on_1123_writes_slot7(mcf_1123):
    """A picked floorplan drives 11.23's floorplan-gated pages: the name switch (objects
    852/853) yields slot 7, the config-unpack derivation (`config.floorplan_slot`)
    independently agrees, and the value is the wire-confirmed one (BUS-BASELINE PC.40:
    3WD/4WD = 5). An unknown name still raises so a typo is not silently ignored."""
    got = options.slot_values(mcf_1123, options.CoachConfig(floorplan="3WD / 4WD"))
    assert got == {7: 5}
    with pytest.raises(KeyError):
        options.slot_values(mcf_1123, options.CoachConfig(floorplan="NOPE"))


def test_floorplan_options_runs_on_147(mcf_147):
    """14.7 uses a different value_slot number than 13.6 for the same mechanism --
    nothing here is hardcoded to slot 5."""
    floorplans = options.floorplan_options(mcf_147)
    assert floorplans[1] == "RT"


def test_widgets_gated_by_bath_fan_finds_the_climate_button(mcf_136):
    """GUICODE Finding 16's worked example: obj 247 ("Bath Fan") on the Climate page
    is the value-1 candidate of the obj-881 switch, which reads slot 109 -- the same
    slot the OPTIONS checkbox reads. It must not be reachable from the switch's
    default (value-0) candidate."""
    gated = options.widgets_gated_by(mcf_136, 109)
    assert "Bath Fan" in gated


def test_widgets_gated_by_black_tank_finds_the_home_gauge(mcf_136):
    """The black-tank option (slot 112) gates the BLACK tank gauge on HOME
    (`test_layout.py::test_black_tank_option_adds_the_fourth_gauge`)."""
    gated = options.widgets_gated_by(mcf_136, 112)
    assert "BLACK" in gated


def test_widgets_gated_by_returns_empty_for_an_unused_slot(mcf_136):
    """No type-6 switch anywhere in 13.6 reads a value-slot this large (the variable
    table has under 700 entries) -- best-effort means an empty list, not an error."""
    assert options.widgets_gated_by(mcf_136, 999_999) == []


def test_widgets_gated_by_returns_a_sorted_list_of_str(mcf_136):
    gated = options.widgets_gated_by(mcf_136, 109)
    assert isinstance(gated, list)
    assert gated == sorted(gated)
    assert all(isinstance(x, str) for x in gated)


# --- CoachConfig / slot_values (Task 3) --------------------------------------------
#
# Known-truth (cited, not fitted): floorplan WD = slot 5 -> value 8; Black Tank =
# slot 112 -> 1 (`test_option_bindings_reproduces_the_known_registers` pins the same
# (slot, register) pair independently). `PROBE5_CONFIG_PATH` is the coach in the
# HOME-INVERTER-AGS photo the brief names: floorplan WD, Black Tank + AGS options on,
# plus a raw extra_slots override.

PROBE5_CONFIG_PATH = Path(__file__).resolve().parent.parent / "configs" / "probe5.json"


def test_slot_values_floorplan_and_one_option(mcf_136):
    config = options.CoachConfig(floorplan="WD", options=["Black Tank"])
    values = options.slot_values(mcf_136, config)
    assert values[5] == 8
    assert values[112] == 1


def test_slot_values_from_probe5_json(mcf_136):
    config = options.CoachConfig.from_json(PROBE5_CONFIG_PATH)
    values = options.slot_values(mcf_136, config)
    assert values == {5: 8, 112: 1, 37: 1, 6: 2}


def test_coach_config_from_json_parses_extra_slots_keys_to_int():
    """JSON object keys are always strings -- `extra_slots`' `"6"` must come back as
    the int 6, since it is used as a dict key alongside the int value_slots
    `option_bindings`/`_find_floorplan_switch` return."""
    config = options.CoachConfig.from_json(PROBE5_CONFIG_PATH)
    assert config.extra_slots == {6: 2}
    assert all(isinstance(k, int) for k in config.extra_slots)


def test_option_value_maps_aes_ags_radio_to_byte_grounded_values():
    """The generator mode is a RADIO on one slot (GUICODE Finding 31): the firmware picks the
    choice by the VALUE written, not a per-name bool. `_option_value` maps the name to the value
    that turns that row's OPTIONS-page checkbox dot cyan -- AGS=1, AES=2, AES Single Output=3
    (obj-47 switch match values, proven-from-code). Every other (boolean) option stays 1."""
    assert options._option_value("AGS") == 1
    assert options._option_value("AES") == 2
    assert options._option_value("AES Single Output") == 3
    for boolean in ("Black Tank", "Bath Fan", "Progressive Inverter", "Heat Pump"):
        assert options._option_value(boolean) == 1


def test_slot_values_aes_single_output_is_3(mcf_136):
    """"AES Single Output" as a plain list option must drive slot 37 to 3 -- the value that checks
    its OWN OPTIONS-page row (obj 47 base-144 cand-145 match 3), not 2 (which checks the plain "AES"
    row) nor the blanket 1 (which reads as AGS)."""
    values = options.slot_values(mcf_136, options.CoachConfig(options=["AES Single Output"]))
    assert values[37] == 3
    assert options.slot_values(mcf_136, options.CoachConfig(options=["AES"]))[37] == 2
    assert options.slot_values(mcf_136, options.CoachConfig(options=["AGS"]))[37] == 1


def test_slot_values_options_dict_form_uses_the_given_value_not_1(mcf_136):
    """The spec's other accepted form: `options` as a `{name: value}` dict, for a
    non-boolean option (AGS's slot is a 3-way selector) -- driven to whatever value
    is given, not always 1."""
    config = options.CoachConfig(options={"AGS": 2})
    values = options.slot_values(mcf_136, config)
    assert values[37] == 2


def test_slot_values_unknown_option_name_raises_with_available_names(mcf_136):
    config = options.CoachConfig(options=["Nonexistent"])
    with pytest.raises(KeyError) as excinfo:
        options.slot_values(mcf_136, config)
    assert "Nonexistent" in str(excinfo.value)
    assert "Bath Fan" in str(excinfo.value)


def test_slot_values_unknown_floorplan_name_raises_with_available_names(mcf_136):
    config = options.CoachConfig(floorplan="Nonexistent")
    with pytest.raises(KeyError) as excinfo:
        options.slot_values(mcf_136, config)
    assert "Nonexistent" in str(excinfo.value)
    assert "WD" in str(excinfo.value)


def test_slot_values_extra_slots_only(mcf_136):
    """No floorplan, no named options -- `extra_slots` alone must still come through,
    confirming it isn't silently dropped when the other two fields are unset."""
    config = options.CoachConfig(extra_slots={6: 2})
    values = options.slot_values(mcf_136, config)
    assert values == {6: 2}


def test_slot_values_raises_on_option_name_slot_collision(monkeypatch, mcf_136):
    """If two OPTIONS-page rows ever shared a name but read different slots,
    `slot_values` must raise loudly instead of silently keeping whichever binding
    `option_bindings` happens to yield last (the plain `{b.name: b.value_slot for b
    in ...}` dict comprehension this replaces would do exactly that). Cannot happen
    on the three current builds, so the collision is manufactured here by
    monkeypatching `option_bindings` to return two bindings that share a name on
    different slots -- this is RED against the old comprehension (it would silently
    return `{998: 1}` or `{999: 1}` with no error) and GREEN after the fix."""
    fake_bindings = [
        options.OptionBinding(name="Dup", value_slot=998, register=0x1, vtype=0,
                              options_obj=1),
        options.OptionBinding(name="Dup", value_slot=999, register=0x2, vtype=0,
                              options_obj=1),
    ]
    monkeypatch.setattr(options, "option_bindings", lambda data: fake_bindings)
    config = options.CoachConfig(options=["Dup"])
    with pytest.raises(ValueError, match="Dup"):
        options.slot_values(mcf_136, config)


def test_coach_config_from_json_parses_preview_block(tmp_path):
    """The optional `preview` block carries RUNTIME sample values for rendering (tank fill %,
    battery voltage) -- explicitly NOT byte-derived (these are bus-delivered, open thread 5).
    from_json loads it verbatim; absent -> empty dict."""
    import json
    p = tmp_path / "coach.json"
    p.write_text(json.dumps({"floorplan": "WD", "preview": {"tank_level": 33, "battery_voltage": 12.2}}))
    config = options.CoachConfig.from_json(p)
    assert config.preview == {"tank_level": 33, "battery_voltage": 12.2}

    p2 = tmp_path / "bare.json"
    p2.write_text(json.dumps({"floorplan": "WD"}))
    assert options.CoachConfig.from_json(p2).preview == {}
