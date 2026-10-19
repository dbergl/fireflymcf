"""On-screen content whose visibility rides on SRAM nothing initialises."""
from pathlib import Path

import pytest

from fireflymcf import bootlottery, guidata

ROOT = Path(__file__).resolve().parents[1]
BUILDS = {
    "13.6": ROOT / "firefly-13.6" / "mcf.bin",
    "11.23": ROOT / "firefly-11.23" / "mcf.bin",
    "14.7": ROOT / "firefly-14.7 (Terrain)" / "mcf.bin",
}


@pytest.fixture(scope="module")
def images():
    return {k: v.read_bytes() for k, v in BUILDS.items()}


def test_the_audit_rediscovers_the_absorb_float_gate_unprompted(images):
    """The oracle: Finding 63 was found by hand from a coach observation. A correct audit must
    surface the same gate from the bytes, with no knowledge of slot 23."""
    gates = [g for g in bootlottery.at_risk(images["13.6"]) if g.value_slot == 23]
    assert len(gates) == 1
    g = gates[0]
    assert g.switch_obj == 17          # screen 14's root
    assert g.screens == frozenset({14})
    assert g.has_default is False      # no 0xFFFF arm -> a non-matching value draws nothing
    assert [a.obj for a in g.arms] == [302, 303]
    assert [a.draws_content for a in g.arms] == [False, True]
    assert g.controls == frozenset({186, 187, 188, 189})


def test_reachability_filters_and_is_reported_not_silently_dropped(images):
    all_gates = bootlottery.lottery_gates(images["13.6"])
    reachable = [g for g in all_gates if g.reachable]
    risky = bootlottery.at_risk(images["13.6"])
    assert len(all_gates) == 329
    assert len(reachable) == 151
    assert len(risky) == 122
    # the unreachable ones are still present in the full list, so the filter is visible
    assert any(not g.reachable for g in all_gates)
    assert set(risky) <= set(reachable) <= set(all_gates)


def test_only_three_slots_put_real_controls_at_stake(images):
    by_slot = {}
    for g in bootlottery.at_risk(images["13.6"]):
        by_slot.setdefault(g.value_slot, set()).update(g.controls)
    with_controls = {s: c for s, c in by_slot.items() if c}
    assert set(with_controls) == {23, 174, 175}
    assert with_controls[174] == {218, 220}   # 'Auto Level'  <-> 'Cancel'
    assert with_controls[175] == {219, 220}   # 'All Retract' <-> 'Cancel'


def test_the_control_action_split_is_not_vacuous(images):
    """Slot 529's twelve touch ids are ACTION leaves, not controls; slot 174's are controls.
    A classifier that called everything one or the other would pass a weaker test."""
    count = len(guidata.group_table(images["13.6"]))
    by_slot = {}
    for g in bootlottery.at_risk(images["13.6"]):
        by_slot.setdefault(g.value_slot, []).append(g)
    g529 = by_slot[529][0]
    assert g529.controls == frozenset()
    assert len(g529.actions) == 12
    assert all(a >= count for a in g529.actions)
    g174 = by_slot[174][0]
    assert g174.actions == frozenset()
    assert all(c < count for c in g174.controls)


def test_a_gate_with_a_default_arm_is_recorded_as_such(images):
    """`has_default` decides whether a garbage value degrades gracefully, so it must discriminate."""
    gates = bootlottery.lottery_gates(images["13.6"])
    assert any(g.has_default for g in gates)
    assert any(not g.has_default for g in gates)


def test_the_audit_runs_on_every_build(images):
    """Object numbering and slot allocation differ per build; the audit must not be 13.6-shaped."""
    for name, data in images.items():
        gates = bootlottery.lottery_gates(data)
        assert gates, f"{name} produced no gates at all"
        assert all(g.count > 0 for g in gates)
        # every reachable gate names at least one real screen id from the match table
        assert all(g.screens for g in gates if g.reachable)
