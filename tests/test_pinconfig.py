"""`pinconfig.describe_pin` -- the whole configuration of one G12 output pin.

Written after a build silently deleted the AWNING EXT command (2026-08-19). The cause was that
a pin's configuration is spread across several structures in two directions, and the tooling
only ever looked at one at a time. These tests pin the parts that mattered.
"""
import pytest

from fireflymcf import guidata, newbutton, pinconfig


def test_object_scan_sees_the_groups_the_old_screen_walk_missed(mcf_136):
    """The root cause, pinned.

    `_object_referenced_groups` used to walk `guidata.iter_screens` (the boundary-fitted
    positional scan `records.py` replaced) and reported ~21 of the ~230 groups. Objects 108 and
    186 both name group 65 in stock 13.6 and it saw neither, so `unused_command_groups` offered
    live groups for repurposing.
    """
    referenced = newbutton._object_referenced_groups(mcf_136)
    assert len(referenced) > 100, (
        f"only {len(referenced)} groups seen as referenced -- the scan is walking the old "
        f"positional screen list again")
    assert 65 in referenced, "group 65 is named by objects 108 and 186 and must be seen"


def test_awning_pin_reports_its_commands_groups_and_buttons(mcf_136):
    """Pin 3 is AWNING EXT: two groups, a sole-producer pair, and locatable buttons."""
    cfg = pinconfig.describe_pin(mcf_136, 3)
    assert cfg.commanded
    assert set(cfg.groups) == {3, 4}
    assert cfg.sole_pairs.get(3) == [(3, 1)], (
        "group 3 is the only source of AWNING EXT 'on'; losing that is the regression")
    assert any("Awning" in (lab or "") for _idx, _gid, labels in cfg.objects for lab in labels)
    assert cfg.outputs_row and cfg.outputs_row[0].startswith("03 -")


def test_status_side_is_reported(mcf_136):
    """var -> colour cell -> table index -> consuming widgets, plus the per-var state colours."""
    cfg = pinconfig.describe_pin(mcf_136, 3)
    assert cfg.bridged and cfg.var is not None
    assert cfg.colour_cell is not None and cfg.colour_index is not None
    assert cfg.consumers, "a displayable pin should have at least one consuming widget"
    assert cfg.on_rgb == (0, 174, 239), "every ON branch loads the same cyan"


def test_sole_produced_pairs_is_symmetric_with_the_tables(mcf_136):
    """A pair reported as sole-produced must genuinely appear in exactly one group."""
    gt = guidata.group_table(mcf_136)
    bt = guidata.binding_table(mcf_136)

    def producers(pair):
        n = 0
        for g in gt:
            if pair in {(bt[j].channel, bt[j].raw[4])
                        for j in range(g.start, g.start + g.count) if 0 <= j < len(bt)}:
                n += 1
        return n

    for gid in (3, 4):
        for pair in newbutton.sole_produced_pairs(mcf_136, gid):
            assert producers(pair) == 1, f"{pair} claimed sole for group {gid} but has others"


def test_an_unconfigured_pin_reports_empty_rather_than_raising(mcf_136):
    """Pin 42 is the one pin `statusbind` calls unreachable; describing it must still work."""
    cfg = pinconfig.describe_pin(mcf_136, 42)
    assert cfg.pin == 42
    assert isinstance(cfg.bindings, list) and isinstance(cfg.consumers, list)


@pytest.mark.parametrize("pin", [3, 15, 29, 44])
def test_format_pin_renders_without_error(mcf_136, pin):
    text = pinconfig.format_pin(pinconfig.describe_pin(mcf_136, pin))
    assert f"G12 output pin {pin}" in text
    assert "COMMAND" in text and "STATUS" in text and "DIAGNOSTICS" in text
