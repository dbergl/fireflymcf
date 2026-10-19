"""The option -> gated-widget mechanism (`optiongates.py`): the `hides_control` and
`channel` joins that closed the last two strict xfails in `test_option_bindings.py`.

Every assertion here names object indices, so the tests pin the STRUCTURE the derivation
reads, not just the numbers the answer key already holds. Mutation-checked 2026-09-02:
stubbing `optiongates.gated_channel` to None / `hides_control` to False fails
`test_option_bindings.py`'s two former xfails, and mutating `OptionGate.reveals` to True
fails `test_heat_pump_modifies_rather_than_hides`.
"""
from __future__ import annotations

import pytest

from tests import answer_key
from fireflymcf import config, guidata, optiongates, options

pytest.importorskip("capstone")


def _slot(data: bytes, name: str) -> int:
    return next(b.value_slot for b in options.option_bindings(data)
                if b.name.splitlines()[0].strip() == name)


# ---------------------------------------------------------------------------
# 13.6: the LIGHTS pages' three option switches and the HOME tank cluster
# ---------------------------------------------------------------------------

LIGHTS_PAGES_136 = set(range(940, 950))   # per-floorplan LIGHTS bodies under root object 6


def test_bath_cargo_pair_is_two_switches_revealing_two_controls(mcf_136):
    """0xE3 (slot 110) and 0xE4 (slot 111) each key their OWN switch on every LIGHTS page;
    the ON arm is a 'Bath'/'Cargo' control whose touch id is group 25 / group 26."""
    cargo = optiongates.option_gates(mcf_136, _slot(mcf_136, "Cargo / Bath Light"))
    bath = optiongates.option_gates(mcf_136, _slot(mcf_136, "Bath Light"))

    assert {g.switch_obj for g in cargo} == LIGHTS_PAGES_136 | {46}
    assert {g.switch_obj for g in bath} == LIGHTS_PAGES_136 | {46}

    on_pages = [g for g in cargo if g.switch_obj in LIGHTS_PAGES_136]
    assert all(g.reveals for g in on_pages)
    assert {g.arm_on for g in on_pages} == {370, 372}          # 'Bath' / 'Cargo' (943, 944)
    assert {g.arm_off for g in on_pages} == {369, 371}         # a wt-0 '.' at x = -95
    assert all(g.on_touch == {25} for g in on_pages)
    assert all(g.channels == {25} for g in on_pages)

    on_pages = [g for g in bath if g.switch_obj in LIGHTS_PAGES_136]
    assert all(g.reveals for g in on_pages)
    assert {g.arm_on for g in on_pages} == {374, 619}          # 943 uses base 618, count 3
    assert all(g.on_touch == {26} for g in on_pages)
    assert all(g.channels == {26} for g in on_pages)


def test_bunk_accent_reveals_object_376_on_every_lights_page(mcf_136):
    gates = [g for g in optiongates.option_gates(mcf_136, _slot(mcf_136, "Bunk Accent"))
             if g.switch_obj in LIGHTS_PAGES_136]
    assert len(gates) == 10
    assert all(g.reveals for g in gates)
    assert {g.base for g in gates} == {375}
    assert {g.arm_off for g in gates} == {375}                 # explicit 0xffff default, empty
    assert {g.arm_on for g in gates} == {376}                  # 'Bunk\nAccent', touch id 34
    assert all(g.on_touch == {34} for g in gates)
    # group 34 carries variant-2 entries on channel 23 beside its channel-34 ones
    # (CONTROL-BEHAVIOR.md section 5), so the channel set is NOT a singleton and `channel`
    # must stay None rather than be picked from it.
    assert all(g.channels == {23, 34} for g in gates)
    assert optiongates.gated_channel(mcf_136, _slot(mcf_136, "Bunk Accent")) is None


def test_black_tank_reveals_the_home_black_gauge(mcf_136):
    """0xEC gates a GAUGE, not a touch control: object 802 ('BLACK' + wt-5 child 719)
    against 801 (a wt-0 '.' at x = -20), on the ten HOME tank clusters 912-921."""
    gates = [g for g in optiongates.option_gates(mcf_136, _slot(mcf_136, "Black Tank"))
             if g.base == 801]
    assert {g.switch_obj for g in gates} == set(range(912, 922))
    assert all(g.reveals for g in gates)
    assert all((g.arm_off, g.arm_on) == (801, 802) for g in gates)
    assert all(g.on_children == {719} and not g.on_touch for g in gates)
    assert optiongates.gated_channel(mcf_136, _slot(mcf_136, "Black Tank")) is None


def test_heat_pump_modifies_rather_than_hides(mcf_136):
    """0xEB's arms 872/873/874 ALL carry touch regions -- the option adds mode buttons
    (PC.34's 'reveals Heat Pump / Auto'), it does not hide a control. The predicate must
    say so; if it did not, `hides_control` would be a tautology over boolean rows."""
    gates = [g for g in optiongates.option_gates(mcf_136, _slot(mcf_136, "Heat Pump"))
             if g.base == 872]
    assert gates
    assert all((g.arm_off, g.arm_on) == (872, 873) for g in gates)
    assert all(g.off_touch and g.on_touch for g in gates)
    assert not any(g.reveals for g in gates)
    assert not optiongates.hides_control(mcf_136, _slot(mcf_136, "Heat Pump"))


def test_bath_fan_is_finding_16s_worked_example(mcf_136):
    """Climate object 247 ('Bath Fan', group 40) only under slot 109 = 1 -- the case
    GUICODE.md Finding 16 worked by hand, reproduced by the general derivation."""
    gates = [g for g in optiongates.option_gates(mcf_136, _slot(mcf_136, "Bath Fan"))
             if g.base == 246]
    assert gates and all(g.reveals and g.arm_on == 247 and g.on_touch == {40} for g in gates)
    assert optiongates.gated_channel(mcf_136, _slot(mcf_136, "Bath Fan")) == 40


# ---------------------------------------------------------------------------
# The two routes to `channel` agree, and the answer key is reproduced
# ---------------------------------------------------------------------------

def test_guidata_and_guicode_routes_agree_on_136(mcf_136):
    """GuiData (switch -> control -> group -> instance) and GuiCode (the tap handler's
    companion OFF template, Finding 23) are derived from disjoint bytes; where both speak
    they must say the same channel, and on 13.6 both speak for exactly E3 and E4."""
    by_sel = {b.selector: b for b in guidata.find_option_bindings(mcf_136)}
    offs = config.off_channels_by_selector(mcf_136)
    assert set(offs) == {0xE3, 0xE4}
    for selector, channel in offs.items():
        assert by_sel[selector].channel == channel
    assert offs == answer_key.OPTION_CHANNEL_BINDING


def test_off_template_bytes_are_the_cited_ones(mcf_136):
    """Finding 23's two `0x1FEDB` templates, by address: `0x00259820` instance 0x1A and
    `0x00259838` instance 0x19, both command 3 (OFF)."""
    templates = {(f.template, f.instance, f.command)
                 for sites in config.selector_sites(mcf_136).values()
                 for site in sites for f in site.dimmer_frames}
    assert templates == {(0x00259820, 26, 3), (0x00259838, 25, 3)}


# ---------------------------------------------------------------------------
# 14.7: same shape, its own indices; no rebinding pair, so no OFF companions
# ---------------------------------------------------------------------------

def test_147_gates_have_the_same_shape(mcf_147):
    by_name = {b.name: b for b in guidata.find_option_bindings(mcf_147)}
    assert by_name["Cargo Light"].channel == 25 and by_name["Cargo Light"].hides_control
    assert by_name["Starlink Power"].channel == 17 and by_name["Starlink Power"].hides_control
    assert by_name["Bunk Accent"].hides_control and by_name["Bunk Accent"].channel is None
    assert by_name["Black Tank"].hides_control and by_name["Black Tank"].channel is None
    assert not by_name["Heat Pump"].hides_control

    cargo = [g for g in optiongates.option_gates(mcf_147, _slot(mcf_147, "Cargo Light"))
             if g.reveals]
    assert {g.arm_on for g in cargo} == {328, 330} and all(g.on_touch == {25} for g in cargo)
    star = [g for g in optiongates.option_gates(mcf_147, _slot(mcf_147, "Starlink Power"))
            if g.reveals]
    assert {g.arm_on for g in star} == {240} and all(g.on_touch == {17} for g in star)
    black = [g for g in optiongates.option_gates(mcf_147, _slot(mcf_147, "Black Tank"))
             if g.base == 737]
    assert len(black) == 10 and all((g.arm_off, g.arm_on) == (737, 738) for g in black)

    # Cargo Light has no partner on 14.7 (Starlink replaced Bath Light), so no handler
    # sends a rebinding OFF -- the GuiCode route is correctly empty, not wrong.
    assert config.off_channels_by_selector(mcf_147) == {}


def test_1123_has_no_gated_light_rows(mcf_1123):
    """11.23's OPTIONS pages carry no Cargo/Bath/Bunk/Black-Tank rows at all
    (`options.option_bindings` finds Heat Pump, Bath Fan, Progressive Inverter, the defaults
    buttons and Renogy Battery), so there is nothing for the join to reach -- the
    derivation returns nothing rather than something invented."""
    names = {b.name.splitlines()[0].strip() for b in options.option_bindings(mcf_1123)}
    assert not names & {"Cargo / Bath Light", "Bath Light", "Bunk Accent", "Black Tank"}
    assert config.off_channels_by_selector(mcf_1123) == {}
