"""`add_button_status`: buttons lit from receive slots through ONE per-tick routine.

Corpus-reading: compiles against stock 13.6. Three `add_rvc_button` taps go on HOME's root
object 4 (two captions, one with a glyph above its caption), then one `add_button_status`
lights each from var 13's low nibble (eq 1, 3 and 6).
"""
import hashlib
import json
import struct
from pathlib import Path

import pytest

from fireflymcf import (followchannel, guidata, recipe, recipe_ops, records, register,
                        status_map, theme, thumb)
from tests.thumbsim import Sim

REPO = Path(__file__).resolve().parents[1]
STOCK = REPO / "firefly-13.6/mcf.bin"
SHA = "d66b41ffafaae4f5ec47cf90714f14bb1954fc3a5ae38defa98a94cd9643d382"
HOME = 4
STALE = followchannel.STALE_SENTINEL
INSET = 2
# a 16 x 16 ring for icon font 24 (cell 48), installed earlier in the project
RING = ["0" * 4 + "f" * 8 + "0" * 4] * 2 + ["00" + "f" * 12 + "00"] * 2 + \
       ["f" * 4 + "0" * 8 + "f" * 4] * 8 + ["00" + "f" * 12 + "00"] * 2 + \
       ["0" * 4 + "f" * 8 + "0" * 4] * 2
GLYPH_OP = {"id": "ring", "type": "install_glyph", "enabled": True,
            "params": {"slot": 24, "char": "Y", "y": 16, "rows": RING, "claim_block": True}}
BUTTONS = [("b1", 210, 332, 80, 44, "Fan", None),
           ("b2", 300, 332, 80, 44, "Cool", None),
           ("b3", 440, 400, 70, 60, "Dry", {"slot": 24, "char": "Y"})]
EQ = (1, 3, 6)


def _button_op(oid, x, y, w, h, text, glyph):
    return {"id": oid, "type": "add_rvc_button", "enabled": True,
            "params": {"parent": HOME, "x": x, "y": y, "w": w, "h": h, "text": text,
                       "glyph": glyph, "dgn": 0x1FEF9, "tap": {"payload": "07f6ffffffffffff"}}}


BASE = [GLYPH_OP] + [_button_op(*b) for b in BUTTONS]


def _entry(ref, value, **over):
    e = {"button": {"ref": ref}, "source": {"var": 13}, "mask": 0x0F, "compare": {"eq": value}}
    e.update(over)
    return e


def _status(entries=None, oid="st", **over):
    params = {"buttons": entries if entries is not None
              else [_entry(b[0], v) for b, v in zip(BUTTONS, EQ)]}
    params.update(over)
    return {"id": oid, "type": "add_button_status", "enabled": True, "params": params}


def _stock():
    try:
        return STOCK.read_bytes()
    except FileNotFoundError:
        pytest.skip("needs the stock 13.6 image")


def _compile(ops, name="button-status-test"):
    p = {"schema": 1, "base_sha256": SHA, "build_name": name, "ops": ops}
    return recipe.compile_project(recipe.Project.from_json(json.dumps(p)), _stock())


def _ok(ops, name="button-status-test"):
    res = _compile(ops, name)
    assert res.ok, res.errors
    return res.data


def _errors(ops):
    res = _compile(ops)
    assert not res.ok
    return " ".join(str(e) for e in res.errors)


@pytest.fixture(scope="module")
def base():
    return _ok(BASE)


@pytest.fixture(scope="module")
def built():
    return _ok(BASE + [_status()])


def _gs_tab(data):
    return guidata.guidata_extent(data)[0], guidata.object_tables(data)[1]


def _button_objects(base_data):
    """The three button objects: the last three in table 1 of the build without the status op."""
    n = len(_gs_tab(base_data)[1])
    return [n - 3, n - 2, n - 1]


def _switch(data, obj):
    gs, tab = _gs_tab(data)
    sws = [r for r in records.object_records(data, gs + tab[obj]) if r.wtype == 6]
    assert len(sws) == 1, sws
    return sws[0]


def test_batch_one_routine(base, built):
    assert len(followchannel.chain_from_hook(built)) == len(followchannel.chain_from_hook(base)) + 1
    sws = [_switch(built, o) for o in _button_objects(base)]
    assert len({s.value_slot for s in sws}) == 3
    for s in sws:
        assert s.count == 2 and s.header[4] & 0x20, "switch not enrolled for live redraw"


def test_arms_draw_whole_button(base, built):
    gs, tab = _gs_tab(built)
    off = theme.resolve_fill_index(built, color=(0x41, 0x51, 0xA3))
    on = theme.resolve_fill_index(built, color=(0x00, 0xAE, 0xEF))
    for (_id, _x, _y, w, h, text, glyph), obj in zip(BUTTONS, _button_objects(base)):
        sw = _switch(built, obj)
        cands = guidata.switch_candidates(built, sw, guidata.find_match_table(built))
        assert [v for v, _o in cands] == [0xFFFF, 1], "base arm 0xFFFF (off), then 1 (on)"
        for (_v, arm_obj), fill in zip(cands, (off, on)):
            arm = records.object_records(built, gs + tab[arm_obj])
            assert arm[0].wtype == 33 and arm[0].colors[0] == ("fillA", 7, fill)
            assert arm[0].geom[2:4] == [w - 2 * INSET, h - 2 * INSET]
            assert [r.label for r in arm if r.wtype == 11 and r.label] == [text]
            want = [] if glyph is None else [(glyph["slot"], glyph["char"])]
            assert [(r.font, r.label) for r in arm if r.wtype == 0] == want
        # the static body is gone from the button object: only the switch draws a capsule
        recs = records.object_records(built, gs + tab[obj])
        assert not any(r.wtype == 33 for r in recs)
        assert not any(r.wtype == 11 and r.label for r in recs)
        assert [r.wtype for r in recs].count(12) == 1, "the touch region stays"


def _routine(data):
    """The appended routine (its entry is the hook's first link) and its disassembly up to and
    including its tail `b.w`; the literal pool follows."""
    gc = theme.guicode_section(data)
    code = data[gc.offset:gc.offset + gc.length]
    entry = followchannel.chain_from_hook(data)[0]
    base = theme.GUICODE_BASE_DEFAULT
    blob = bytes(code[entry - base:gc.length - 4])
    dis = []
    for a, t in thumb.disassemble(blob, entry):
        dis.append((a, t))
        if t.startswith("b.w #"):
            break
    return blob, entry, dis


@pytest.mark.parametrize("word,lit", [
    (1, (1, 0, 0)), (3, (0, 1, 0)), (6, (0, 0, 1)), (4, (0, 0, 0)), (0xF3, (0, 1, 0)),
    (STALE, (0, 0, 0)), (0, (0, 0, 0))])
def test_fold_semantics_in_image(base, built, word, lit):
    blob, entry, dis = _routine(built)
    slot = register.status_slot_addr(13)
    cells = [status_map.cell_table(built)[_switch(built, o).value_slot]
             for o in _button_objects(base)]
    sim = Sim(blob, entry, dis)
    sim.wr(slot, word, 2)
    for c in cells:
        sim.wr(c, 0xAAAA, 2)
    sim.run()
    assert tuple(sim.rd(c, 2) for c in cells) == lit
    assert sim.calls == [], "no toggle: no setVariant call"


def test_routine_names_each_slot_cell_pair(base, built):
    gc = theme.guicode_section(built)
    code = built[gc.offset:gc.offset + gc.length]
    slot = register.status_slot_addr(13)
    for o in _button_objects(base):
        cell = status_map.cell_table(built)[_switch(built, o).value_slot]
        assert struct.pack("<I", cell) in code


def test_cells_are_freshly_grown(base, built):
    """Every switch reads a freshly grown cell index, never a reused stock one."""
    stock = _stock()
    stock_rows = len(status_map.cell_table(stock))
    slots = [_switch(built, o).value_slot for o in _button_objects(base)]
    assert len(slots) == 3
    assert all(s >= stock_rows for s in slots), (slots, stock_rows)
    assert not set(status_map.cell_table(built)[s] for s in slots) & set(
        status_map.cell_table(stock))


def _spy_apply(monkeypatch):
    seen = {}
    real = recipe_ops.AddButtonStatus.apply

    def spy(self, data, ctx):
        out = real(self, data, ctx)
        seen.update(op=self, ctx=ctx, cells=set(ctx.authored_cells))
        return out
    monkeypatch.setattr(recipe_ops.AddButtonStatus, "apply", spy)
    return seen


def test_cells_are_recorded_for_the_aliasing_gate(monkeypatch):
    seen = _spy_apply(monkeypatch)
    _ok(BASE + [_status()])
    slots = [b["slot"] for b in seen["ctx"].handles["st"]["buttons"]]
    assert len(slots) == 3 and set(slots) <= seen["cells"]


def test_verify_catches_a_tampered_routine(monkeypatch):
    seen = _spy_apply(monkeypatch)
    data = _ok(BASE + [_status()])
    op, ctx = seen["op"], seen["ctx"]
    op.verify(data, ctx)                                   # the untouched image passes
    h = ctx.handles["st"]
    gc = theme.guicode_section(data)
    at = gc.offset + h["code_load_addr"] - theme.GUICODE_BASE_DEFAULT
    for k in (h["blob_len"] // 2, h["blob_len"] - 1):      # an instruction byte, a pool byte
        bad = bytearray(data)
        bad[at + k] ^= 0x01
        with pytest.raises(AssertionError, match="is not the one its 3 entries build"):
            op.verify(bytes(bad), ctx)


def test_double_wrap_refuses():
    assert "BAD_PARAMS" in _errors(BASE + [_status([_entry("b1", 1)]),
                                           _status([_entry("b1", 3)], oid="st2")])


def test_two_entries_one_button_refuses():
    assert "BAD_PARAMS" in _errors(BASE + [_status([_entry("b1", 1), _entry("b1", 3)])])


ALARMS_BASE = [
    {"id": "aesswap", "type": "swap_aes_electrical", "params": {}, "enabled": True},
    {"id": "battheat", "type": "add_aes_batt_heat", "enabled": True,
     "params": {"x": 84, "y": 156, "w": 163, "h": 54, "text": "Battery\nHeater"}},
    {"id": "status", "type": "add_aes_batt_heat_status", "enabled": True,
     "params": {"button": {"ref": "battheat"}}},
    {"id": "alarms", "type": "add_aes_batt_heat", "enabled": True,
     "params": {"x": 249, "y": 156, "w": 163, "h": 54, "text": "Ignore\nAlarms",
                "enable_wire": "4d53000000001770", "off_wire": "4d53000000000000",
                "group_pool": "hold_safe"}},
]
ALARMS_STATUS = {"id": "alarms-status", "type": "add_aes_bms_button_status", "enabled": True,
                 "params": {"button": {"ref": "alarms"}, "status": {"ref": "status"},
                            "mask": 2}}


def test_double_wrap_after_bms_status_refuses():
    assert "BAD_PARAMS" in _errors(ALARMS_BASE + [ALARMS_STATUS,
                                                  _status([_entry("alarms", 1)])])


def test_preset_button_wraps():
    """A preset button (records appended to 829 among the compact frame's) is found by its
    touch id and wrapped; the heater's own switch is left alone."""
    data = _ok(ALARMS_BASE + [_status([_entry("alarms", 1, compare={"bit": 2})])])
    gs, tab = _gs_tab(data)
    frame = records.object_records(data, gs + tab[829])
    mine = [r for r in frame if r.wtype == 6 and r.count == 2
            and any(x.label == "Ignore\nAlarms"
                    for x in records.object_records(data, gs + tab[r.child_index]))]
    assert len(mine) == 1
    arm = records.object_records(data, gs + tab[mine[0].child_index])
    assert arm[0].wtype == 33 and arm[1].label == "Ignore\nAlarms"
    assert not any(r.wtype == 11 and r.label == "Ignore\nAlarms" for r in frame), \
        "the caption moved into the arms"
    assert len([r for r in frame if r.wtype == 6 and r.count == 4]) == 1, "heater switch kept"


def test_ge_requires_valid_max():
    assert "BAD_PARAMS" in _errors(BASE + [_status([_entry("b1", 2, compare={"ge": 2})])])
    assert "BAD_PARAMS" in _errors(BASE + [_status([_entry("b1", 2, compare={"ne": 2})])])
    _ok(BASE + [_status([_entry("b1", 2, compare={"ge": 2}, valid_max=0xFA)])])


@pytest.mark.parametrize("entry", [
    _entry("b1", 1, compare={}),
    _entry("b1", 1, compare={"eq": 1, "ge": 2}),
    _entry("b1", 1, compare={"lt": 1}),
    _entry("b1", 1, compare={"bit": 0}),
    _entry("b1", 1, mask=0),
    _entry("b1", 1, shift=16),
    _entry("b1", 1, valid_max=0x10000),
])
def test_bad_params(entry):
    assert "BAD_PARAMS" in _errors(BASE + [_status([entry])])


def test_no_buttons_is_bad_params():
    assert "BAD_PARAMS" in _errors(BASE + [_status([])])


def test_bad_ref():
    assert "BAD_REF" in _errors(BASE + [_status([_entry("nope", 1)])])
    assert "BAD_REF" in _errors(BASE + [_status([_entry("b1", 1, source={"ref": "nope"})])])
    assert "BAD_REF" in _errors(BASE + [_status([_entry("ring", 1)])])


def test_no_body_colour():
    assert "NO_BODY_COLOUR" in _errors(BASE + [_status(on_colour=[1, 2, 3])])
    assert "NO_BODY_COLOUR" in _errors(
        BASE + [_status([_entry("b1", 1, off_colour=[1, 2, 3])])])


# `tests/test_batt_heat_status.py`'s and `tests/test_bms_button.py`'s builds, recorded at
# 824a8cd, before `add_batt_heat_state_switch` delegated to `statelights.wrap_button`.
HEATER_SHA = "ee97433b393f0464e2a0a2f4821f6527c32d80ee8c5b0a74bad7e829d9d69782"
BMS_SHA = "4f272c6d3b53cb3b86b37499b1e04d864603a402933bf6644c9db0941fdfa296"


def test_heater_switch_unchanged():
    heater = _ok(ALARMS_BASE[:1] + [
        {"id": "battheat", "type": "add_aes_batt_heat", "params": {}, "enabled": True},
        {"id": "status", "type": "add_aes_batt_heat_status", "params": {}, "enabled": True}],
        "batt-heat-status-test")
    assert hashlib.sha256(heater).hexdigest() == HEATER_SHA
    bms = _ok(ALARMS_BASE + [ALARMS_STATUS], "bms-button-test")
    assert hashlib.sha256(bms).hexdigest() == BMS_SHA


def test_inert_source_refuses():
    """Var 185's extraction mode is 0: it matches its DGN and writes no slot
    (`statusbind.LIVE_MODES`), so a light on it would never change."""
    msg = _errors(BASE + [_status([_entry("b1", 1, source={"var": 185})])])
    assert "NO_SUCH_REGISTER" in msg and "mode 0" in msg


def test_preset_handle_has_the_rvc_button_keys(monkeypatch):
    """A status op reads its button's handle with `.get`: the `add_aes_batt_heat` preset's must
    carry every key an `add_rvc_button`'s does, with the values the preset really builds (fill
    index 35's colour, DGN 0xEF70, the tap-or-hold form on the donor's 1-s hold nibble, off on
    tap and enable on hold)."""
    from fireflymcf import aescompact
    seen = {}
    real = recipe._finish

    def spy(project, stock, data, ctx, ops):
        seen["ctx"], seen["data"] = ctx, data
        return real(project, stock, data, ctx, ops)
    monkeypatch.setattr(recipe, "_finish", spy)
    _ok(ALARMS_BASE[:2] + BASE[1:2])
    preset, button = seen["ctx"].handles["battheat"], seen["ctx"].handles["b1"]
    assert set(button) - set(preset) == set(), sorted(set(button) - set(preset))
    palette = theme.mode7_palette(_stock())
    assert preset["colour"] == list(palette[aescompact.BATT_HEAT_FILL_INDEX]) == [0, 174, 239]
    assert (preset["dgn"], preset["form"], preset["seconds"]) == (aescompact.BATT_HEAT_DGN,
                                                                   "tap_hold", 1)
    assert (preset["tap"], preset["hold"]) == (aescompact.BATT_HEAT_OFF_WIRE,
                                               aescompact.BATT_HEAT_ENABLE_WIRE)
    g = guidata.group_table(seen["data"])[preset["group"]]
    bt = guidata.binding_table(seen["data"])
    assert b"".join(bytes(bt[g.start + k].raw) for k in range(g.count)) == \
        recipe_ops._rvc_button_bindings(preset), "the handle does not rebuild the bindings"
