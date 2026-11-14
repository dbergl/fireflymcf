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


# --- button_time: the chosen minutes on the ELECTRICAL buttons ---------------------------------

STATUS_OPS = [
    {"id": "status", "type": "add_aes_batt_heat_status", "params": {"button": {"ref": "heat"}},
     "enabled": True},
    {"id": "alarms-status", "type": "add_aes_bms_button_status",
     "params": {"button": {"ref": "alarms"}, "status": {"ref": "status"}, "mask": 2},
     "enabled": True},
]


@pytest.fixture(scope="module")
def timed():
    ops = BASE_OPS[:3] + STATUS_OPS + BASE_OPS[3:]
    dur = dict(DUR, params=dict(DUR["params"], button_time=True))
    before, after = _compile(ops + [DUR]), _compile(ops + [dur])
    assert before.ok, before.errors
    assert after.ok, after.errors
    return before.data, after.data


def _switch_arms(data, text):
    from fireflymcf import records
    gs, _ = guidata.guidata_extent(data)
    tab = guidata.object_tables(data)[1]
    out = []
    for r in records.object_records(data, gs + tab[829]):
        if r.wtype != 6:
            continue
        arms = [records.object_records(data, gs + tab[c])
                for c in range(r.child_index, r.child_index + r.count)]
        if any(x.wtype == 11 and x.label == text for a in arms for x in a):
            out.append(arms)
    return out


def test_button_time_in_every_arm(timed):
    _b, after = timed
    for text, cell_key in (("Batt Heat", "heater"), ("Alarms", "alarms")):
        (arms,) = _switch_arms(after, text)
        for arm in arms:
            assert [x.label for x in arm if x.wtype == 11 and x.label
                    and x.font != 23] == [text, "Min"]
            readouts = [x for x in arm if x.wtype == 8]
            assert len(readouts) == 1
            cap = arm[0]
            assert cap.wtype == 33
            fill = [c for c in cap.colors if c[0] == "fillA"][0]
            assert ("fillB", 7, fill[2]) in readouts[0].colors
            glyph = [j for j, x in enumerate(arm) if x.wtype == 11 and x.font == 23]
            if glyph:                        # placed from the caption's pen: right after it
                assert glyph == [2] and arm[3].wtype == 11 and not arm[3].label


@pytest.mark.slow
def test_button_time_follows_setting(timed):
    from fireflymcf.emu.scenario import Scenario
    _b, after = timed
    s = Scenario(after, timeout_s=900)
    s.set_g12_selector(battdur.SELECTOR, battdur.pack(25, 45))
    s.boot()
    s.goto("ELECTRICAL")
    s.run(7.0)
    shown = sorted(q.label for q in s._placed(s._page_root(None)) if q.label == "Min")
    assert len(shown) == 2


def test_check_installed_catches_a_dropped_part(built):
    """verify's check: the G12 sweep byte, the routine on the hook chain, and the steppers on
    0x72. Reverting any one must fail it."""
    from fireflymcf import tankbroadcast
    from fireflymcf import followchannel, layout, records
    _b, after, _ = built
    root = layout.screen_object_map(after)[35]
    gs, _ = guidata.guidata_extent(after)
    recs = records.object_records(after, gs + guidata.object_tables(after)[1][root])
    ids = [int.from_bytes(after[r.end - guidata.GROUP_REFERENCE_TAIL:
                                r.end - guidata.GROUP_REFERENCE_TAIL + 2], "little")
           for r in recs if r.wtype == 12][-5:-1]
    handle = {"entry": followchannel.chain_from_hook(after)[0],
              "touch": {"heater": tuple(ids[0:2]), "alarms": tuple(ids[2:4])}}
    battdur.check_installed(after, handle)
    off = tankbroadcast.blob_file_offset(after) + battdur.G12_PATCH_SITE
    reverted = after[:off] + bytes([43]) + after[off + 1:]
    with pytest.raises(AssertionError, match="sweep"):
        battdur.check_installed(reverted, handle)
    with pytest.raises(AssertionError, match="chain"):
        battdur.check_installed(after, dict(handle, entry=handle["entry"] + 2))
    with pytest.raises(AssertionError, match="stepper"):
        battdur.check_installed(after, dict(handle, touch={"heater": (1, 2),
                                                           "alarms": handle["touch"]["alarms"]}))
