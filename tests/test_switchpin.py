"""`switchpin`: pin a wt-6 switch to one arm by the firmware's own default-arm rule."""
from __future__ import annotations

import struct

import pytest

from fireflymcf import guidata, layout, patch, records, recipe, switchpin

STOCK = "firefly-13.6/mcf.bin"
OBJ, REC, ROWS, EMPTY, SLOT = 17, 11, 303, 302, 23     # INVERTER / CHARGER SETTINGS, Absorb/Float


@pytest.fixture(scope="module")
def data() -> bytes:
    with open(STOCK, "rb") as handle:
        return handle.read()


def _draws(img: bytes, obj: int, value: int) -> bool:
    return any(p.obj_index == obj for p in layout.Screen(img).walk(OBJ, slot_values={SLOT: value}))


def test_stock_rows_depend_on_slot_23(data: bytes) -> None:
    assert not _draws(data, ROWS, 0) and _draws(data, ROWS, 1) and not _draws(data, ROWS, 2)
    assert switchpin.pinned_to(data, OBJ, REC) is None


def test_pin_makes_the_rows_draw_for_every_slot_value_and_hides_the_empty_arm(data: bytes) -> None:
    out, edits = switchpin.pin_switch_arm(data, OBJ, REC, ROWS)
    assert len(edits) == 3 and all(len(e[1]) == 2 and len(e[2]) == 2 for e in edits)
    assert len(out) == len(data)                         # same-size
    assert switchpin.pinned_to(out, OBJ, REC) == ROWS
    for value in (0, 1, 2, 0xFF, 0xFFFF):
        assert _draws(out, ROWS, value), value
        assert not _draws(out, EMPTY, value), value
    # the three u16s, and nothing else, moved (302->303 and 2->1 each touch one byte of their u16)
    diff = [i for i in range(len(data)) if data[i] != out[i]]
    spans = {i for off, _, _, _ in edits for i in (off, off + 1)}
    assert len(diff) == 4 and set(diff) <= spans, (diff, spans)
    gs, _ = guidata.guidata_extent(out)
    rec = records.object_records(out, gs + guidata.object_tables(out)[1][OBJ])[REC]
    assert (rec.child_index, rec.count) == (ROWS, 1)
    assert layout.Screen(out)._match_value(ROWS) == 0xFFFF


def test_pin_refuses_an_arm_outside_the_band_and_a_non_switch(data: bytes) -> None:
    with pytest.raises(switchpin.SwitchPinError):
        switchpin.pin_switch_arm(data, OBJ, REC, 304)
    with pytest.raises(switchpin.SwitchPinError):
        switchpin.pin_switch_arm(data, OBJ, 0, ROWS)      # record 0 of the page is not a switch


def test_pin_refuses_an_arm_whose_match_value_another_switch_reads(data: bytes) -> None:
    """The match table is one u16 per object; an arm shared between two switches' bands cannot
    be pinned for one of them alone. Synthesised: point object 17 record 11's band at objects
    two other switches already scan (the nav rail's 385-388) and ask to pin 385."""
    gs, _ = guidata.guidata_extent(data)
    rec = records.object_records(data, gs + guidata.object_tables(data)[1][OBJ])[REC]
    buf = bytearray(data)
    struct.pack_into("<H", buf, rec.child_index_off, 385)
    assert switchpin.other_switches_covering(bytes(buf), 385, skip=((OBJ, REC),))
    with pytest.raises(switchpin.SwitchPinError, match="also an arm"):
        switchpin.pin_switch_arm(bytes(buf), OBJ, REC, 385)


def test_the_recipe_op_pins_and_reports_verified(data: bytes) -> None:
    import hashlib
    p = recipe.Project(base_sha256=hashlib.sha256(data).hexdigest(), build_name=None, ops=[
        recipe.OpSpec(id="rows", type="pin_switch_arm",
                      params={"object": OBJ, "record": REC, "arm": ROWS})])
    r = recipe.compile_project(p, data)
    assert r.ok, r.errors
    assert r.unverified == []          # v18 FLASHED AND VERIFIED 2026-09-05
    assert switchpin.pinned_to(r.data, OBJ, REC) == ROWS
    bad = recipe.Project(base_sha256=hashlib.sha256(data).hexdigest(), build_name=None, ops=[
        recipe.OpSpec(id="rows", type="pin_switch_arm",
                      params={"object": OBJ, "record": REC, "arm": 304})])
    r2 = recipe.compile_project(bad, data)
    assert not r2.ok and r2.errors[0]["code"] == "PIN_REFUSED"
