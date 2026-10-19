"""`mastergroup.drop_channel`: remove one G12 channel from a master control.

A "master" is one on-screen control whose ON and OFF buttons drive several channels at
once (`guidata.find_control_groups`, screen title "Light Master" / "Exterior Master").
Dropping a channel neutralises every binding entry that names it in BOTH the ON and the
OFF group, across every variant copy, so the master neither turns that load on nor off.

The neutralisation is two same-size edits per entry: the DGN u16 -> 0xFFFF (the inert
form Firefly itself ships in group 5, and the `raw[:2] == FFFF` liveness convention
`gestures.py` already uses) AND the phase mask (byte 3) -> 0x00, which
`docs/CONTROL-BEHAVIOR.md` section 3 proves makes the entry emit on no gesture phase.
"""
import pathlib

import pytest

from fireflymcf import guidata, mastergroup


def _light_master(data):
    return {c.label: c for c in guidata.find_control_groups(data)
            if c.screen_title == "Light Master"}


def test_drops_bath_channel_from_both_light_master_groups(mcf_136):
    out, _edits = mastergroup.drop_channel(mcf_136, "Light Master", 25)
    groups = _light_master(out)
    assert set(groups) == {"ON", "OFF"}, "the ON/OFF pair moved"
    for label, control in groups.items():
        assert 25 not in control.channels, f"ch25 still in Light Master {label}"
    assert groups["ON"].channels == [22, 24, 26, 32, 34, 35], "an innocent channel changed"
    assert groups["OFF"].channels == [22, 24, 26, 32, 34, 35], "an innocent channel changed"


def _diff_runs(a, b):
    assert len(a) == len(b), "edit was not size-neutral"
    return [i for i in range(len(a)) if a[i] != b[i]]


def test_only_the_six_bath_entries_change_and_each_becomes_inert(mcf_136):
    out, edits = mastergroup.drop_channel(mcf_136, "Light Master", 25)
    gs, _ = guidata.guidata_extent(mcf_136)
    groups = guidata.group_table(mcf_136)
    bindings = guidata.binding_table(mcf_136)

    # The exact entries that name ch25 in the ON (1) and OFF (2) groups.
    ch25 = [b for gi in (1, 2) for b in bindings[groups[gi].start:
            groups[gi].start + groups[gi].count] if b.channel == 25]
    assert len(ch25) == 6, "the stock fixture no longer has six ch25 master bindings"

    # Every changed byte lies inside one of those six entries -- nothing else moved.
    changed = set(_diff_runs(mcf_136, out))
    allowed = {gs + b.offset + k for b in ch25 for k in range(12)}
    assert changed <= allowed, "an edit landed outside the ch25 bindings"

    # Each entry is now a dead, inert binding: DGN 0xFFFF and an empty phase mask.
    for b in ch25:
        base = gs + b.offset
        assert out[base:base + 2] == b"\xff\xff", "DGN not neutralised"
        assert out[base + 3] == 0x00, "phase mask not cleared"

    # Two edits per entry (DGN, phase mask), all logged.
    assert len(edits) == 12


def test_refuses_a_channel_that_is_not_a_member(mcf_136):
    with pytest.raises(mastergroup.MasterEditError) as e:
        mastergroup.drop_channel(mcf_136, "Light Master", 99)
    assert "not a member" in str(e.value)


def test_refuses_an_unknown_master_title(mcf_136):
    with pytest.raises(mastergroup.MasterEditError) as e:
        mastergroup.drop_channel(mcf_136, "Bedroom Master", 25)
    assert "no master control" in str(e.value)


def test_dropping_from_light_master_leaves_exterior_master_untouched(mcf_136):
    out, _ = mastergroup.drop_channel(mcf_136, "Light Master", 25)
    ext = {c.label: c.channels for c in guidata.find_control_groups(out)
           if c.screen_title == "Exterior Master"}
    assert ext == {"ON": [21, 23, 35], "OFF": [21, 23, 35]}, "Exterior Master changed"
