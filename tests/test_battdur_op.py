"""`add_bms_duration`: BATTERY SETUP's two duration steppers on cell 0x72, the G12 sweep
retarget, and the buttons' holds sending the chosen minutes."""
import json

import pytest

from fireflymcf import battdur, g12_config, guidata, newbutton, recipe

STOCK = "firefly-13.6/mcf.bin"
STOCK_SHA = "d66b41ffafaae4f5ec47cf90714f14bb1954fc3a5ae38defa98a94cd9643d382"

BASE_OPS = [
    {"id": "aesswap", "type": "swap_aes_electrical", "params": {}, "enabled": True},
    {"id": "heat", "type": "add_aes_batt_heat", "params": {"w": 140}, "enabled": True},
    {"id": "alarms", "type": "add_aes_batt_heat",
     "params": {"text": "Alarms", "x": 230, "w": 140, "enable_wire": "4d53000000001770",
                "off_wire": "4d53000000000000", "group_pool": "hold_safe"}, "enabled": True},
    {"id": "screen", "type": "add_screen", "params": {"title": "BATTERY SETUP"}, "enabled": True},
    {"id": "popup", "type": "add_popup_page",
     "params": {"screen": {"ref": "screen"}, "title": "BATTERY SETUP",
                "return_to": "ELECTRICAL"}, "enabled": True},
]
DUR = {"id": "dur", "type": "add_bms_duration",
       "params": {"popup": {"ref": "popup"}, "heater": {"ref": "heat"},
                  "alarms": {"ref": "alarms"}}, "enabled": True}


def _compile(ops):
    stock = open(STOCK, "rb").read()
    return recipe.compile_project(recipe.Project.from_json(json.dumps({
        "schema": 1, "base_sha256": STOCK_SHA, "build_name": "battdur-op", "ops": ops})), stock)


@pytest.fixture(scope="module")
def built():
    before = _compile(BASE_OPS)
    after = _compile(BASE_OPS + [DUR])
    assert before.ok, before.errors
    assert after.ok, after.errors
    return before.data, after.data, after


def test_op_refuses_without_popup():
    bad = dict(DUR, params=dict(DUR["params"], popup={"ref": "nope"}))
    res = _compile(BASE_OPS + [bad])
    assert not res.ok and res.errors[0]["code"] == "BAD_REF"


def test_unverified(built):
    assert "dur" in built[2].unverified


def _ef70(data):
    groups, bindings = guidata.group_table(data), guidata.binding_table(data)
    out = {}
    for g in range(len(groups)):
        for b in guidata.group_bindings(groups, bindings, g):
            if b.dgn_low == battdur.EF70:
                out.setdefault(g, []).append(b)
    return out


def test_enable_bindings_removed_stop_kept(built):
    before, after, _ = built
    b0, b1 = _ef70(before), _ef70(after)
    assert set(b0) == set(b1) and len(b0) == 2
    for g in b0:
        held0 = [b for b in b0[g] if b.raw[3] & 0x04]
        assert len(held0) == 1
        assert not [b for b in b1[g] if b.raw[3] & 0x04]
        stop0 = [b.raw for b in b0[g] if b.raw[3] & 0x02]
        stop1 = [b.raw for b in b1[g] if b.raw[3] & 0x02]
        assert stop0 == stop1 and newbutton.binding_payload(stop0[0])[6:] == b"\0\0"


def test_activate_sweep_one_byte_in_g12(built):
    before, after, _ = built
    g0, g1 = g12_config.parse_g12(before), g12_config.parse_g12(after)
    a = before[g0.file_offset:g0.file_offset + g0.length]
    b = after[g1.file_offset:g1.file_offset + g1.length]
    diff = [i for i in range(len(a) - 4) if a[i] != b[i]]      # the last 4: its checksum
    assert len(a) == len(b) and len(diff) == 1
    assert (a[diff[0]], b[diff[0]]) == (43, 85)


@pytest.mark.slow
def test_emulated_g12_sweeps_72_once_and_e8_once(built):
    from fireflymcf.emu.scenario import Scenario
    _b, after, _ = built
    s = Scenario(after, timeout_s=600)
    s.set_g12_selector(battdur.SELECTOR, battdur.pack(30, 15))
    s.boot()
    t0 = s.now
    s.run(16.0)
    sweep = [f for f in s.frames(dgn=0x15FCE, sa=0x9C, since=t0)]
    n72 = sum(1 for f in sweep if f.data[0] == 0x72)
    ne8 = sum(1 for f in sweep if f.data[0] == 0xE8)
    nef = sum(1 for f in sweep if f.data[0] == 0xEF)
    assert n72 >= 3 and n72 == ne8 == nef
    want = battdur.pack(30, 15).to_bytes(2, "little")
    assert any(f.data[2:4] == want for f in sweep if f.data[0] == 0x72)


@pytest.mark.slow
def test_steppers_independent(built):
    from fireflymcf.emu.scenario import Scenario
    _b, after, res = built
    s = Scenario(after, timeout_s=900)
    s.boot()
    s.show_screen(35)
    s.run(6.0)
    rects = battdur.row_rects()

    def tap(key, which):
        x, y, w, h = rects[key][0 if which == "-" else 1]
        s.tap_at(x + w // 2, y + h // 2)
        s.run(1.0)

    def minutes():
        return battdur.decode_word(s.g12_selector(battdur.SELECTOR) or battdur.UNSET)

    tap("heater", "+")                       # from unset: the wrap gives the defaults
    assert minutes() == (60, 10)
    for _ in range(2):
        tap("heater", "+")
    assert minutes() == (60, 10) and s.g12_selector(battdur.SELECTOR) == 0x121C
    tap("alarms", "-")
    assert minutes() == (60, 5)
    tap("alarms", "-")
    assert minutes() == (60, 5) and s.g12_selector(battdur.SELECTOR) == 0x111C
    tap("heater", "-")
    tap("heater", "-")
    assert minutes() == (50, 5)
    tap("alarms", "+")
    assert minutes() == (50, 10)
