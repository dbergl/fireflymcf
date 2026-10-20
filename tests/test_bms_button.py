"""The Ignore Alarms button (v49): `add_aes_batt_heat` as a generic BMS command button --
`enable_wire` / `off_wire` / `text` / `group_pool` -- and `add_aes_bms_button_status`."""
import json
from pathlib import Path

import pytest

from fireflymcf import guidata, levelcontrols, newbutton, options, recipe

REPO = Path(__file__).resolve().parents[1]
STOCK = REPO / "firefly-13.6/mcf.bin"
SHA = "d66b41ffafaae4f5ec47cf90714f14bb1954fc3a5ae38defa98a94cd9643d382"

ALARMS = {"id": "alarms", "type": "add_aes_batt_heat", "enabled": True,
          "params": {"x": 249, "y": 156, "w": 163, "h": 54, "text": "Ignore\nAlarms",
                     "enable_wire": "4d53000000001770", "off_wire": "4d53000000000000",
                     "group_pool": "hold_safe"}}
BASE_OPS = [
    {"id": "aesswap", "type": "swap_aes_electrical", "params": {}, "enabled": True},
    {"id": "battheat", "type": "add_aes_batt_heat", "enabled": True,
     "params": {"x": 84, "y": 156, "w": 163, "h": 54, "text": "Battery\nHeater"}},
    {"id": "status", "type": "add_aes_batt_heat_status", "enabled": True,
     "params": {"button": {"ref": "battheat"}}},
]


def _compile(ops):
    p = {"schema": 1, "base_sha256": SHA, "build_name": "bms-button-test", "ops": ops}
    return recipe.compile_project(recipe.Project.from_json(json.dumps(p)), STOCK.read_bytes())


@pytest.fixture(scope="module")
def built():
    res = _compile(BASE_OPS + [ALARMS])
    assert res.ok, res.errors
    return res


def _group_frames(data, group):
    g = guidata.group_table(data)[group]
    bt = guidata.binding_table(data)
    return [(newbutton.binding_wire_frame(bt[g.start + k].raw), bt[g.start + k].raw[3])
            for k in range(g.count)]


def _alarms_group(res):
    line = next(m for m in res.manifest if "Ignore" in m or "4d53" in m.lower())
    import re
    return int(re.search(r"new group (\d+)", line).group(1))


def test_alarms_binds_hold_and_tap_frames(built):
    frames = _group_frames(built.data, _alarms_group(built))
    assert frames == [((0x18EF709F, bytes.fromhex("4d53000000001770")), 0x04),
                      ((0x18EF709F, bytes.fromhex("4d53000000000000")), 0x02)]


def test_alarms_group_is_hold_safe(built):
    stock = STOCK.read_bytes()
    assert _alarms_group(built) in newbutton.hold_safe_groups(stock, levelcontrols.RESERVED_GROUPS)


def test_bad_wire_refused():
    bad = json.loads(json.dumps(ALARMS))
    bad["params"]["enable_wire"] = "4d53"
    res = _compile(BASE_OPS + [bad])
    assert not res.ok and any("BAD_PARAMS" in str(e) for e in res.errors), res.errors


# --- add_aes_bms_button_status ------------------------------------------------------------------

import struct

from fireflymcf import bitcombine, layout, readout, records, register, status_map, theme
from tests.thumbsim import Sim

ALARMS_STATUS = {"id": "alarms-status", "type": "add_aes_bms_button_status", "enabled": True,
                 "params": {"button": {"ref": "alarms"}, "status": {"ref": "status"},
                            "mask": 2}}
SIM_LOAD = 0x00300000


@pytest.fixture(scope="module")
def with_status():
    res = _compile(BASE_OPS + [ALARMS, ALARMS_STATUS])
    assert res.ok, res.errors
    return res.data


def _frame(data):
    gs, tab = guidata.guidata_extent(data)[0], guidata.object_tables(data)[1]
    return gs, tab, records.object_records(data, gs + tab[829])


def _alarm_switch(data):
    gs, tab, frame = _frame(data)
    sws = [r for r in frame if r.wtype == 6 and r.count == 2
           and any(x.label == "Ignore\nAlarms"
                   for x in records.object_records(data, gs + tab[r.child_index]))]
    assert len(sws) == 1, sws
    return sws[0]


def _heater_var(data):
    hits = [v for v in guidata.variable_table(data)
            if v.dgn_low == 0xFF70 and v.raw[2] >> 4 == 4]
    assert len(hits) == 1
    return hits[0].index


def test_one_two_arm_switch_with_whole_button_arms(with_status):
    gs, tab, _f = _frame(with_status)
    sw = _alarm_switch(with_status)
    assert sw.header[4] & 0x20, "not enrolled for live redraw"
    for k in range(2):
        arm = records.object_records(with_status, gs + tab[sw.child_index + k])
        assert arm[0].wtype == 33 and arm[0].geom[2:4] == [159, 50]
        assert arm[1].label == "Ignore\nAlarms"
        assert not any(r.font == 23 for r in arm), "no contactor glyph on Ignore Alarms"


def test_routine_folds_the_heater_var_bit_1_into_the_switch_cell(with_status):
    sw = _alarm_switch(with_status)
    cell_addr = status_map.cell_table(with_status)[sw.value_slot]
    gc = theme.guicode_section(with_status)
    code = with_status[gc.offset:gc.offset + gc.length]
    pair = struct.pack("<II", register.status_slot_addr(_heater_var(with_status)), cell_addr)
    assert pair in code, "no routine folds the heater var into the alarm cell"


@pytest.mark.parametrize("word,expect", [(0, 0), (1, 0), (2, 1), (3, 1), (0xFFFF, 0)])
def test_mask_2_fold_reads_bit_1(word, expect):
    stock = STOCK.read_bytes()
    _img, cell, _e = readout.allocate_display_cell(stock, 2)
    src = 0x20008100
    plan = bitcombine.resolve(stock, sources=(bitcombine.BitSource(src, 2, 1),), cell=cell)
    blob, disasm = bitcombine.build(plan, SIM_LOAD)
    s = Sim(blob, SIM_LOAD, disasm)
    s.wr(src, word, 2)
    s.wr(cell.addr, 0xA5A5, 2)
    s.run()
    assert s.rd(cell.addr, 2) == expect


def test_cell_1_selects_the_cyan_arm(with_status):
    from tests.test_card_layout import COACH
    sw = _alarm_switch(with_status)
    sv = dict(options.slot_values(with_status, COACH))
    sv.update(options.derived_slots(with_status, sv))
    fills = {}
    for v in (0, 1):
        sv[sw.value_slot] = v
        pl = layout.Screen(with_status).walk(5, slot_values=sv)
        arm = [p for p in pl if sw.child_index <= p.obj_index < sw.child_index + 2
               and p.wtype == 33]
        fills[v] = arm[0].colors[0]
    pal = theme.mode7_palette(with_status)
    assert pal[fills[0][2]] == (65, 81, 163) and pal[fills[1][2]] == (0, 174, 239), fills


def test_a_new_command_reports_itself_unverified(built):
    """The class is coach-verified for the heater's frames; a different command is not."""
    assert "alarms" in built.unverified and "battheat" not in built.unverified


def test_manifest_describes_each_button_by_its_own_command(built):
    lines = [m for m in built.manifest if m.startswith("add_aes_batt_heat:")]
    heater = next(m for m in lines if "Battery\\nHeater" in m or "Battery\nHeater" in m)
    alarms = next(m for m in lines if "Ignore" in m)
    assert "4d53000000001770" in alarms.lower() and "COACH-VERIFIED" not in alarms
    assert "60-minute" not in alarms
    assert "COACH-VERIFIED" in heater
