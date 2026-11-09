"""Two 13.6 readings refuse on an image they cannot read, instead of answering with 13.6's
values or with nothing (2026-11-08):

- `statusbind.bind_pin` names a pin's colour cell from the committed 13.6 GuiCode bridge dump
  (`status_map.load_bridge("136")`). On another firmware those cells are not that image's, so
  it refuses BRIDGE_UNKNOWN, and so do `add_button` and `wire_button` through it.
- `newbutton.guicode_selected_groups` refuses VARIANT_SETTER_UNKNOWN where the setter cell's
  uses do not resolve (it read only 13.6's cell until 2026-11-08; it now reads the located cell
  on every image, `tests/test_groups_locate.py`). The finish gate that reads it on every compile
  refuses, on such an image, only a build that reuses a group; a build that moves no group
  slice is not gated.
"""
import dataclasses
import hashlib

import pytest

from fireflymcf import guidata, hooklocate, newbutton, recipe, statusbind

NEEDS_RESEARCH_DATA = "reads the 13.6 and 14.7 corpus images"


# --- bind_pin ----------------------------------------------------------------------------------

def test_bind_pin_refuses_an_image_the_bridge_dump_is_not(mcf_147):
    with pytest.raises(statusbind.BridgeUnknown) as ei:
        statusbind.bind_pin(mcf_147, 3)
    assert ei.value.code == "BRIDGE_UNKNOWN"
    assert "13.6" in str(ei.value)
    assert isinstance(ei.value, statusbind.StatusBindError)


def test_bind_pin_still_binds_on_13_6(mcf_136):
    pin = statusbind.free_pins(mcf_136)[0]
    try:
        out, index, _edits = statusbind.bind_pin(mcf_136, pin)
    except statusbind.BridgeUnknown:                       # pragma: no cover
        pytest.fail("13.6 is the bridge dump's own image")
    except statusbind.StatusBindError:
        return                                             # a pin-specific refusal, not ours
    assert isinstance(index, int)


def _build(stock, specs, monkeypatch, admit):
    for name in admit:
        monkeypatch.setattr(recipe.REGISTRY[name], "firmwares", frozenset({"13.6", "14.7"}))
    project = recipe.Project(base_sha256=hashlib.sha256(stock).hexdigest(), build_name=None,
                             ops=[recipe.OpSpec(id=i, type=t, params=p) for i, t, p in specs])
    return recipe.compile_project(project, stock)


def test_add_button_refuses_bridge_unknown_on_147(mcf_147, monkeypatch):
    """`add_button` admitted on 14.7 for this test only: it reaches `bind_pin` and refuses
    under the bridge's own code."""
    res = _build(mcf_147, [("b", "add_button", {"channel": 3, "label": "X",
                                                 "off_colour": [65, 81, 163],
                                                 "allow_named_output": True})],
                 monkeypatch, ["add_button"])
    assert not res.ok
    assert [e["code"] for e in res.errors] == ["BRIDGE_UNKNOWN"], res.errors


# --- guicode_selected_groups --------------------------------------------------------------------

def test_selected_groups_read_the_located_cell_on_147(mcf_147):
    """14.7's cell is not 13.6's, and the scan reads it rather than refusing (2026-11-08)."""
    assert hooklocate.host_api_cells(mcf_147)[0x04] != newbutton.SET_VARIANT_PTR
    assert newbutton.guicode_selected_groups(mcf_147)
    for reader in (newbutton.hold_safe_groups, newbutton.assignable_groups):
        reader(mcf_147)


def test_selected_groups_on_13_6_unchanged(mcf_136):
    assert hooklocate.host_api_cells(mcf_136)[0x04] == newbutton.SET_VARIANT_PTR
    assert set(range(130, 140)) <= newbutton.guicode_selected_groups(mcf_136)


def test_selected_groups_refuse_a_13_6_image_whose_setter_moved(mcf_136, monkeypatch):
    """The check can fail: 13.6 with its located setter cell stubbed to another word refuses
    (no store to that word is entry0's copy)."""
    cells = dict(hooklocate.host_api_cells(mcf_136))
    cells[0x04] += 4
    monkeypatch.setattr(hooklocate, "host_api_cells", lambda _d: cells)
    with pytest.raises(newbutton.VariantSetterUnknown):
        newbutton.guicode_selected_groups(mcf_136)


def test_gate_skips_a_147_build_that_reuses_no_group(mcf_147):
    """A 14.7 compile of an op that allocates no group passes the reuse gate."""
    project = recipe.Project(
        base_sha256=hashlib.sha256(mcf_147).hexdigest(), build_name=None,
        ops=[recipe.OpSpec(id="r", type="rename_label",
                           params={"object": 765, "old": "Chassis", "new": "Chassix"})])
    assert recipe.compile_project(project, mcf_147).ok
    recipe._assert_no_guicode_selected_group_reused(mcf_147, mcf_147)


def test_gate_refuses_a_group_reuse_where_the_driven_groups_are_unknown(mcf_147, monkeypatch):
    """Where `guicode_selected_groups` refuses, a build that moves a group slice is refused
    VARIANT_SETTER_UNKNOWN (14.7 itself now locates: `test_groups_locate.py`)."""
    real = guidata.group_table
    stock = mcf_147
    built = bytearray(mcf_147)
    built[-1] ^= 1                    # another image; only its group table is read

    def moved(data):
        gt = real(data)
        if data != stock:
            gt = list(gt)
            gt[5] = dataclasses.replace(gt[5], start=gt[5].start + 1)
        return gt

    def unknown(_data):
        raise newbutton.VariantSetterUnknown("stubbed: not known")

    monkeypatch.setattr(guidata, "group_table", moved)
    monkeypatch.setattr(newbutton, "guicode_selected_groups", unknown)
    with pytest.raises(recipe.GateError) as ei:
        recipe._assert_no_guicode_selected_group_reused(mcf_147, bytes(built))
    assert ei.value.code == "VARIANT_SETTER_UNKNOWN"
    assert "5" in str(ei.value)
