"""The `add_alt_reboot` recipe op: a "REBOOT APS-500" button beside CLEAR EEPROM on the
ALTERNATOR SYSTEM page that sends RV-C GENERAL_RESET to the APS-500 on a 3-second hold.

Composed standalone against stock: the button lands on object 24 at the drawn box, its touch
references a freshly-minted one-binding group with a 3 s threshold and no held repeat, and the
binding reconstructs to the exact wire frame. The op refuses a build whose object 24 is not the
page it was verified against.
"""
import json

import pytest

from fireflymcf import altreboot, guidata, layout, newbutton, recipe

STOCK = "firefly-13.6/mcf.bin"
STOCK_SHA = "d66b41ffafaae4f5ec47cf90714f14bb1954fc3a5ae38defa98a94cd9643d382"


def _project(params=None):
    return recipe.Project.from_json(json.dumps({
        "schema": 1, "base_sha256": STOCK_SHA, "build_name": "alt-reboot-test",
        "ops": [{"id": "reboot", "type": "add_alt_reboot", "params": params or {},
                 "enabled": True}],
    }))


@pytest.fixture(scope="module")
def built():
    stock = open(STOCK, "rb").read()
    res = recipe.compile_project(_project(), stock)
    assert res.ok, res.errors
    return stock, res


def _reboot_group(data):
    gt = guidata.group_table(data)
    bt = guidata.binding_table(data)
    hits = [gi for gi, g in enumerate(gt)
            if g.count and bt[g.start].dgn_low == (altreboot.GENERAL_RESET_DGN & 0xFFFF)]
    assert len(hits) == 1, "expected exactly one GENERAL_RESET group, found %s" % hits
    return gt[hits[0]], hits[0]


def test_op_is_hardware_verified(built):
    """Coach-verified on v46 (2026-10-05): the hold sent one GENERAL_RESET and the APS-500
    rebooted."""
    _stock, res = built
    assert "reboot" not in res.unverified


def test_binding_is_the_general_reset_reboot_frame_on_the_held_phase_only(built):
    _stock, res = built
    g, _gi = _reboot_group(res.data)
    assert g.count == 1
    raw = guidata.binding_table(res.data)[g.start].raw
    assert newbutton.binding_wire_frame(raw) == (0x197F809F, bytes.fromhex("0100ffffffffffff"))
    assert raw[3] == 0x04, "must fire on the held phase and nothing else"
    assert not raw[2] & 0x04, "must not re-send on the repeat ticks"


def test_group_holds_three_seconds_without_a_held_repeat(built):
    stock, res = built
    g, _gi = _reboot_group(res.data)
    donor = guidata.group_table(stock)[altreboot.HOLD_DONOR_GROUP].raw
    assert g.raw[0] >> 4 == 3
    assert g.raw[2] & 0x0C == 0
    # everything but the threshold, the repeat index, start and count is the donor's
    assert g.raw[0] & 0x0F == donor[0] & 0x0F
    assert g.raw[1] == donor[1] and g.raw[3] == donor[3] and g.raw[2] & ~0x0C == donor[2] & ~0x0C


def test_button_draws_beside_clear_eeprom_with_the_new_group_as_its_touch(built):
    _stock, res = built
    _g, gi = _reboot_group(res.data)
    box, cap, touch = altreboot.placed_button(res.data, gi)
    assert (box.x0, box.y0, box.x1, box.y1) == (507, 435, 617, 475)
    assert cap.label == "REBOOT APS-500"
    assert (touch.x0, touch.x1) == (507, 617)
    clear = [p for p in layout.Screen(res.data).walk(altreboot.PAGE_ROOT)
             if p.wtype == 11 and p.label == "CLEAR EEPROM"]
    assert len(clear) == 1 and clear[0].x0 - box.x1 == 13


def test_every_other_page_record_is_untouched(built):
    stock, res = built
    before = altreboot.check_page(stock)
    after = altreboot.check_page(res.data)
    assert len(after) == len(before) + 3
    assert [bytes(stock[r.offset:r.end]) for r in before] == \
        [bytes(res.data[r.offset:r.end]) for r in after[:len(before)]]


def test_clear_faults_sends_rvc2mqtts_reset_frame():
    res = recipe.compile_project(_project({"clear_faults": True}), open(STOCK, "rb").read())
    assert res.ok, res.errors
    g, _gi = _reboot_group(res.data)
    raw = guidata.binding_table(res.data)[g.start].raw
    assert newbutton.binding_payload(raw) == bytes.fromhex("0500ffffffffffff")


def test_refuses_a_build_whose_object_24_is_another_page():
    other = open("firefly-11.23/mcf.bin", "rb").read()
    with pytest.raises(altreboot.AltRebootError):
        altreboot.check_page(other)


def test_the_reserved_inverter_groups_gate_refuses_a_build_that_claims_them(monkeypatch):
    """v44's first build took group 68 (a stock 1FFD3 inverter template). With the op's own
    exclusion removed and 68 first in the free list (as it was on v43) it would again, and the
    compile-time gate must refuse it."""
    from fireflymcf import levelcontrols
    monkeypatch.setattr(levelcontrols, "RESERVED_GROUPS", frozenset())
    monkeypatch.setattr(newbutton, "free_group_slots", lambda data: [68])
    with pytest.raises(AssertionError, match="reserved group 68"):
        recipe.compile_project(_project(), open(STOCK, "rb").read())


def test_guicode_selected_groups_finds_13_6s_130_139_block():
    """13.6 GuiCode sets the variant of 130-139 as a block (0x24AC66-0x24ACB0); v45 reused 136/137
    and their holds failed on the coach. The scan must see the whole block."""
    sel = newbutton.guicode_selected_groups(open(STOCK, "rb").read())
    assert set(range(130, 140)) <= sel
    assert not {96, 126, 127, 46} & sel          # the slots whose holds worked on the coach
