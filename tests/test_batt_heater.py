"""The `add_aes_batt_heat` recipe op: a "Batt Heat" command button on the compact AES ELECTRICAL
page that transmits the BMS proprietary-A "enable battery heater" frame on a press-and-hold and
the "off" frame on a tap.

The transmit path (a novel proprietary-A DGN emitted from a binding, priority 6, SA 0x9F) is
proven separately in `tests/test_binding_frame.py`; this exercises the op composed on the compact
frame: the button records land on object 829, its touch references a freshly-minted command group,
that group's two bindings decode to the exact wire frames, and the capability gate sees no delta.
"""
import json

from fireflymcf import aescompact, guidata, newbutton, records, recipe

STOCK = "firefly-13.6/mcf.bin"

# swap_aes_electrical (builds the compact frame) + add_aes_batt_heat, standalone against stock.
RECIPE = json.dumps({
    "schema": 1,
    "base_sha256": "d66b41ffafaae4f5ec47cf90714f14bb1954fc3a5ae38defa98a94cd9643d382",
    "build_name": "batt-heat-test",
    "ops": [
        {"id": "aesswap", "type": "swap_aes_electrical", "params": {}, "enabled": True},
        {"id": "battheat", "type": "add_aes_batt_heat", "params": {}, "enabled": True},
    ],
})


def _build():
    stock = open(STOCK, "rb").read()
    res = recipe.compile_project(recipe.Project.from_json(RECIPE), stock)
    assert res.ok, res.errors
    return stock, res


def _frame(data, group):
    gt = guidata.group_table(data)
    bt = guidata.binding_table(data)
    g = gt[group]
    return [newbutton.binding_wire_frame(bt[j].raw) for j in range(g.start, g.start + g.count)], \
        [bt[j].raw[3] for j in range(g.start, g.start + g.count)]


def _group(res):
    """Find the minted Batt Heat group -- the one whose bindings carry the EF70 DGN."""
    data = res.data
    gt = guidata.group_table(data)
    bt = guidata.binding_table(data)
    hits = [gi for gi, g in enumerate(gt)
            if g.count and bt[g.start].dgn_low == (aescompact.BATT_HEAT_DGN & 0xFFFF)]
    assert len(hits) == 1, "expected exactly one EF70 command group, found %s" % hits
    return data, hits[0]


def test_op_is_hardware_verified():
    # Coach-verified 2026-09-21: hold->enable / tap->off frames captured on can1, heater engaged.
    _stock, res = _build()
    assert "battheat" not in res.unverified, "add_aes_batt_heat is coach-verified; must not be flagged unverified"


def test_button_records_land_on_the_compact_frame():
    _stock, res = _build()
    data, group = _group(res)
    gs, _ = guidata.guidata_extent(data)
    tab = guidata.object_tables(data)[1]
    recs = records.object_records(data, gs + tab[aescompact.ELECTRICAL_AES_BODY])
    labels = [r.label for r in recs if r.wtype == 11 and r.label]
    assert aescompact.BATT_HEAT_TEXT in labels, labels
    touch_ids = [data[r.end - 2] | (data[r.end - 1] << 8) for r in recs if r.wtype == 12]
    assert group in touch_ids, (group, touch_ids)


def test_bindings_emit_the_exact_wire_frames():
    _stock, res = _build()
    data, group = _group(res)
    frames, masks = _frame(data, group)
    assert frames == [
        (aescompact.BATT_HEAT_ENABLE_ID, aescompact.BATT_HEAT_ENABLE_WIRE),
        (aescompact.BATT_HEAT_OFF_ID, aescompact.BATT_HEAT_OFF_WIRE),
    ], frames
    # press-and-hold arms (mask 0x04), a quick tap turns off (mask 0x02).
    assert masks == [aescompact.BATT_HEAT_ENABLE_MASK, aescompact.BATT_HEAT_OFF_MASK], masks


def test_enable_frame_is_the_60_minute_arm_not_empty():
    """Guard against a stub/empty payload silently passing: the tick count must be 0x8CA0."""
    _stock, res = _build()
    data, group = _group(res)
    frames, _ = _frame(data, group)
    (enable_id, enable_wire), (off_id, off_wire) = frames
    assert enable_id == 0x18EF709F and enable_wire.hex() == "aa55000000008ca0"
    assert enable_wire[6:] == bytes.fromhex("8ca0")          # 36000 x 100ms = 60 min
    assert off_wire[6:] == bytes.fromhex("0000")             # off


def test_capability_gate_sees_no_delta():
    stock, res = _build()
    assert recipe.capabilities(res.data) == recipe.capabilities(stock)
