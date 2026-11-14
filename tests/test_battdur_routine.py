"""The battdur per-tick routine in the emulator: the readouts follow cell 0x72, a hold sends the
shown duration once, a tap sends one stop, and a non-canonical stored word is corrected."""
import json

import pytest

from fireflymcf import battdur, guidata, newbutton, patch, readout, recipe, register, routinekit

STOCK = "firefly-13.6/mcf.bin"
STOCK_SHA = "d66b41ffafaae4f5ec47cf90714f14bb1954fc3a5ae38defa98a94cd9643d382"
HEATER, ALARMS = bytes.fromhex("aa5500000000"), bytes.fromhex("4d5300000000")

pytestmark = pytest.mark.slow


def _project():
    return recipe.Project.from_json(json.dumps({
        "schema": 1, "base_sha256": STOCK_SHA, "build_name": "battdur-routine",
        "ops": [
            {"id": "aesswap", "type": "swap_aes_electrical", "params": {}, "enabled": True},
            {"id": "heat", "type": "add_aes_batt_heat", "params": {"w": 140}, "enabled": True},
            {"id": "alarms", "type": "add_aes_batt_heat",
             "params": {"text": "Alarms", "x": 230, "w": 140,
                        "enable_wire": "4d53000000001770", "off_wire": "4d53000000000000",
                        "group_pool": "hold_safe"}, "enabled": True},
        ]}))


def held_groups(data):
    groups, bindings = guidata.group_table(data), guidata.binding_table(data)
    out = {}
    for g in range(len(groups)):
        for b in guidata.group_bindings(groups, bindings, g):
            if b.dgn_low == battdur.EF70 and b.raw[3] & 0x04:
                p = newbutton.binding_payload(b.raw)[:6]
                out["heater" if p == HEATER else "alarms" if p == ALARMS else p.hex()] = g
    return out


def install(data):
    """What add_bms_duration will do, by hand: sweep 0x72, add the routine, drop the holds."""
    data, _ = battdur.activate_sweep(data)
    groups = held_groups(data)
    assert set(groups) == {"heater", "alarms"}, groups
    slot = register.resolve_register(data, dgn="DGN_CONFIG", selector=battdur.SELECTOR).slot_addr
    data, cells, _e = readout.allocate_fresh_cells(data, [(1, 1), (1, 1)])
    plan = battdur.resolve(data, groups=groups, config_slot=slot,
                           cells=dict(zip(battdur.BUTTONS, cells)))
    _d, tp = routinekit.tick_install_plan(data)
    blob, entry = battdur.build(plan, tp.code_load_addr, tp.tail_target, tp.sender_addr)
    data, _ = routinekit.install_tick_routine(data, blob, entry, "battdur", 
                                              code_load_addr=tp.code_load_addr,
                                              tail_target=tp.tail_target)
    for g in groups.values():
        data, _ = battdur.disable_enable_binding(data, g)
    data, _ = patch.reseal(data)
    return data, plan, blob


@pytest.fixture(scope="module")
def image():
    stock = open(STOCK, "rb").read()
    res = recipe.compile_project(_project(), stock)
    assert res.ok, res.errors
    return install(res.data)


def _scenario(data, word=None):
    from fireflymcf.emu.scenario import Scenario
    s = Scenario(data, timeout_s=600)
    if word is not None:
        s.set_g12_selector(battdur.SELECTOR, word)
    s.boot()
    s.goto("ELECTRICAL")
    s.run(7.0)
    return s


def _point(s, plan, key):
    root = s._page_root(None)
    regions = s._regions(s._placed(root))
    mine = [r for r in regions if r[1] == plan.groups[key]]
    assert mine, f"no {key} touch region on ELECTRICAL"
    return s._tap_point(mine[0][0], plan.groups[key], regions)


def _press(s, xy, hold_s):
    t0 = s.now
    s._m.touch.touch([xy])
    s.run(hold_s)
    s._m.touch.touch([])
    s.run(1.5)
    return [f.data.hex() for f in s.frames(dgn=battdur.EF70, since=t0)]


def _cells(s, plan):
    return tuple(s.read_mem(plan.display[k][1], 1)[0] for k in battdur.BUTTONS)


def test_routine_disassembles(image):
    data, plan, blob = image
    _d, tp = routinekit.tick_install_plan(data)
    assert blob[:12] == battdur.START_FRAME_ID + HEATER + b"\0\0"


def test_routine_sends_shown_minutes(image):
    data, plan, _ = image
    s = _scenario(data, battdur.pack(25, 45))
    assert _cells(s, plan) == (25, 45)
    assert _press(s, _point(s, plan, "heater"), 1.5) == ["aa55000000003a98"]
    assert _press(s, _point(s, plan, "alarms"), 1.5) == ["4d53000000006978"]


def test_long_hold_sends_one_start_no_stop(image):
    data, plan, _ = image
    s = _scenario(data, battdur.pack(25, 45))
    assert _press(s, _point(s, plan, "heater"), 5.0) == ["aa55000000003a98"]


def test_tap_sends_one_stop(image):
    data, plan, _ = image
    s = _scenario(data, battdur.pack(25, 45))
    assert _press(s, _point(s, plan, "heater"), 0.3) == ["aa55000000000000"]


def test_each_button_uses_its_own_cell(image):
    data, plan, _ = image
    s = _scenario(data, battdur.pack(15, 50))
    assert _press(s, _point(s, plan, "heater"), 1.5) == ["aa55000000002328"]


def test_g12_unset_uses_defaults(image):
    data, plan, _ = image
    s = _scenario(data)
    assert _cells(s, plan) == (60, 10)
    assert _press(s, _point(s, plan, "heater"), 1.5) == ["aa55000000008ca0"]
    assert _press(s, _point(s, plan, "alarms"), 1.5) == ["4d53000000001770"]
    assert s.g12_selector(battdur.SELECTOR) is None       # unset is never overwritten


def test_non_canonical_word_is_corrected(image):
    data, plan, _ = image
    s = _scenario(data, 0x1E1C)                            # alarms two past the top
    assert s.g12_selector(battdur.SELECTOR) == 0x1C1C
    assert _cells(s, plan) == (60, 60)
    writes = [f for f in s.frames(dgn=0x1FED9) if f.data[2] == battdur.SELECTOR
              and f.data[6] == 0xD1]
    assert len(writes) == 1


def test_chain_preserved(image):
    data, _plan, _ = image
    from fireflymcf import followchannel
    chain = followchannel.chain_from_hook(data)
    assert len(chain) >= 2
