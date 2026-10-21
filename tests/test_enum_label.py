"""`add_enum_label`: a value shown as text from a table, on any page.

Corpus-reading: compiles against stock 13.6 and places a thermostat-mode label inside HOME's
CLIMATE box (object 539, attached under its parent 899; the box is (82, 346)-(192, 466)).
"""
import json
import struct
from pathlib import Path

import pytest

from fireflymcf import (followchannel, guidata, pagewalk, recipe, records, register, status_map,
                        theme, thumb)
from tests.thumbsim import Sim

REPO = Path(__file__).resolve().parents[1]
STOCK = REPO / "firefly-13.6/mcf.bin"
SHA = "d66b41ffafaae4f5ec47cf90714f14bb1954fc3a5ae38defa98a94cd9643d382"
HOME_ROOT = 4
CLIMATE_PARENT = 899
VALUES = {"0": "Off", "1": "Cool", "3": "Auto", "4": "Fan", "6": "Dry"}
TEXTS = ["Off", "Cool", "Auto", "Fan", "Dry", "--"]
STALE = followchannel.STALE_SENTINEL


def _op(**over):
    params = {"parent": CLIMATE_PARENT, "x": 88, "y": 372, "w": 98, "h": 16, "font": 11,
              "align": "centre", "source": {"var": 13, "mask": 0x0F}, "values": VALUES,
              "default": "--"}
    params.update(over)
    return {"id": "mode", "type": "add_enum_label", "enabled": True, "params": params}


def _stock():
    try:
        return STOCK.read_bytes()
    except FileNotFoundError:
        pytest.skip("needs the stock 13.6 image")


def _compile(ops):
    p = {"schema": 1, "base_sha256": SHA, "build_name": "enum-label-test", "ops": ops}
    return recipe.compile_project(recipe.Project.from_json(json.dumps(p)), _stock())


@pytest.fixture(scope="module")
def built():
    res = _compile([_op()])
    assert res.ok, res.errors
    return res.data


@pytest.fixture(scope="module")
def with_bg():
    res = _compile([_op(background=[65, 81, 163])])
    assert res.ok, res.errors
    return res.data


def _gs_tab(data):
    return guidata.guidata_extent(data)[0], guidata.object_tables(data)[1]


def _label_switch(data):
    """The new object (the last in table 1) and its one switch record."""
    gs, tab = _gs_tab(data)
    obj = len(tab) - 1
    recs = records.object_records(data, gs + tab[obj])
    assert [r.wtype for r in recs][0] == 6, recs
    return obj, recs[0]


def _arms(data, sw):
    """The arms in match-value order (the band itself is laid out descending)."""
    gs, tab = _gs_tab(data)
    cands = dict(guidata.switch_candidates(data, sw, guidata.find_match_table(data)))
    return [records.object_records(data, gs + tab[cands[v]]) for v in sorted(cands)]


def test_mode_label_on_home(built):
    gs, tab = _gs_tab(built)
    obj, sw = _label_switch(built)
    parent = records.object_records(built, gs + tab[CLIMATE_PARENT])
    assert any(r.wtype == 5 and r.child_index == obj for r in parent), "not attached to 899"
    assert sw.count == 6
    assert sw.header[4] & 0x20, "switch not enrolled for live redraw"
    assert sw.base_modes[:4] == [0, 0, 1, 1] and sw.geom[:2] == [88, 372]
    cands = guidata.switch_candidates(built, sw, guidata.find_match_table(built))
    assert sorted(v for v, _o in cands) == [0, 1, 2, 3, 4, 5]
    assert [v for v, _o in cands] == [5, 4, 3, 2, 1, 0], "the band is laid out descending"
    labels = []
    for arm in _arms(built, sw):
        lab = [r for r in arm if r.wtype == 11]
        assert len(lab) == 1 and arm[-1].wtype == 11
        assert lab[0].geom[2] > 0 and lab[0].geom[3] > 0, "a wtype-11 label needs a box"
        assert lab[0].geom[2:4] == [98, 16] and lab[0].font == 11 and lab[0].h_align == 2
        assert not any(r.wtype == 33 for r in arm), "no background was asked for"
        labels.append(lab[0].label)
    assert labels == TEXTS
    white = theme.resolve_fill_index(built, color=(255, 255, 255))
    assert all(("fillA", 7, white) in a[-1].colors for a in _arms(built, sw))


def _routine(data, slot_addr, cell_addr):
    """The appended routine: from the stock append point to the end of its literal pool."""
    gc = theme.guicode_section(data)
    code = data[gc.offset:gc.offset + gc.length]
    pair = struct.pack("<II", slot_addr, cell_addr)
    assert code.count(pair) == 1, "no single routine folds the slot into the cell"
    start = followchannel.STOCK_APPEND_POINT - theme.GUICODE_BASE_DEFAULT
    end = code.index(pair) + len(pair)
    blob = bytes(code[start:end])
    dis = thumb.disassemble(blob[:end - start - len(pair)], followchannel.STOCK_APPEND_POINT)
    return blob, dis


@pytest.mark.parametrize("word,expect", [(0, 0), (3, 2), (6, 4), (2, 5), (STALE, 5),
                                         (0xF1, 1)])
def test_routine_maps_values(built, word, expect):
    _obj, sw = _label_switch(built)
    cell_addr = status_map.cell_table(built)[sw.value_slot]
    slot_addr = register.status_slot_addr(13)
    blob, dis = _routine(built, slot_addr, cell_addr)
    sim = Sim(blob, followchannel.STOCK_APPEND_POINT, dis)
    sim.wr(slot_addr, word, 2)
    sim.wr(cell_addr, 0xAAAA, 2)
    sim.run()
    assert sim.rd(cell_addr, 2) == expect


def test_hook_reaches_the_routine(built):
    gc = theme.guicode_section(built)
    hook = built[gc.offset + followchannel.HOOK_OFF:gc.offset + followchannel.HOOK_OFF + 4]
    want = thumb.bl(theme.GUICODE_BASE_DEFAULT + followchannel.HOOK_OFF,
                    followchannel.STOCK_APPEND_POINT)
    assert hook == want


def test_background_arm(with_bg):
    _obj, sw = _label_switch(with_bg)
    bg = theme.resolve_fill_index(with_bg, color=(65, 81, 163))
    for arm in _arms(with_bg, sw):
        assert [r.wtype for r in arm] == [33, 11]
        assert arm[0].geom[2:4] == [98, 16]
        assert arm[0].colors[0] == ("fillA", 7, bg)


def _errors(op):
    res = _compile([op])
    assert not res.ok
    return " ".join(str(e) for e in res.errors)


@pytest.mark.parametrize("over", [
    {"values": {}},
    {"values": {str(k): "v%d" % k for k in range(17)}},
    {"values": {"0": "Off", "one": "Cool"}},
    {"values": {"0": "Off", "00": "Cool"}},
    {"values": {"0": ""}},
    {"align": "middle"},
    {"source": {"var": 13, "mask": 0}},
    {"source": {"var": 13, "shift": 16}},
    {"source": {"var": 13, "dgn": 0x1FFE2}},
    {"w": 0},
])
def test_bad_params(over):
    assert "BAD_PARAMS" in _errors(_op(**over))


def test_no_colour():
    assert "NO_COLOUR" in _errors(_op(colour=[1, 2, 3]))
    assert "NO_COLOUR" in _errors(_op(background=[1, 2, 3]))


def test_off_canvas():
    assert "OFF_CANVAS" in _errors(_op(x=750))
    assert "OFF_CANVAS" in _errors(_op(y=470))


def test_label_refused_when_the_text_does_not_fit():
    assert "LABEL_REFUSED" in _errors(_op(w=10))


def test_source_refusals():
    assert "BAD_REF" in _errors(_op(source={"ref": "nope"}))
    assert "NO_SUCH_REGISTER" in _errors(_op(source={"var": 100000}))
    assert "NO_SUCH_REGISTER" in _errors(_op(source={"dgn": 0x1FFE2, "instance": 99}))


def test_dgn_source_matches_var(built):
    res = _compile([_op(source={"dgn": 0x1FFE2, "instance": 0, "index": 13, "mask": 0x0F})])
    assert res.ok, res.errors
    assert res.data == built


def test_layout_unchanged_elsewhere(built):
    before = pagewalk.walk_records(_stock(), HOME_ROOT)
    after = pagewalk.walk_records(built, HOME_ROOT)
    obj, sw = _label_switch(built)
    ours = {CLIMATE_PARENT, obj, *range(sw.child_index, sw.child_index + sw.count)}
    deltas = pagewalk.diff(before, after)
    assert all(d.kind == "appeared" for d in deltas), pagewalk.describe(deltas)
    new = [d.after for d in deltas]
    assert [(r.wtype, r.obj) for r in new][:2] == [(5, CLIMATE_PARENT), (6, obj)]
    assert all(r.obj in ours for r in new), pagewalk.describe(deltas)
    # the attach, the switch, the band's base arm (the descending band starts at "--") and the
    # empty label that hands the walk back the font and align it carried in
    assert [(r.wtype, r.label or "") for r in new] == [(5, ""), (6, ""), (11, "--"), (11, "")]
    assert new[2].box == (88, 372, 186, 388) and new[2].font == 11
    assert new[3].obj == obj


def test_restore_hands_back_the_entering_state(built):
    """The empty label writes back the font/align the walk carried into the attach point."""
    stock = _stock()
    walk = pagewalk.walk_records(stock, HOME_ROOT)
    last = max(i for i, r in enumerate(walk) if r.obj == CLIMATE_PARENT or
               any(o == CLIMATE_PARENT for o, _i in r.key[:-1]))
    after = pagewalk.walk_records(built, HOME_ROOT)
    obj, _sw = _label_switch(built)
    restore = [r for r in after if r.obj == obj][-1]
    assert restore.wtype == 11 and not restore.label
    assert (restore.font, restore.align) == (walk[last].font, walk[last].align)


@pytest.mark.parametrize("value,text", list(enumerate(TEXTS)))
def test_each_cell_value_draws_its_text(built, value, text):
    _obj, sw = _label_switch(built)
    walk = pagewalk.walk_records(built, HOME_ROOT, slot_values={sw.value_slot: value})
    drawn = [r for r in walk if r.wtype == 11 and r.label
             and sw.child_index <= r.obj < sw.child_index + sw.count]
    assert [(r.label, r.box[:2]) for r in drawn] == [(text, (88, 372))]


# --- fix round 1: fresh cell (ruling R8) and the whole-page layout gate ----------------------

from fireflymcf import enumlabel, readout, recipe_ops


def test_cell_is_freshly_grown(built):
    """Never a reused stock index: the gate `recipe._assert_authored_cells_fresh` sees it."""
    _obj, sw = _label_switch(built)
    _base, _types, stock_rows = readout.resolve_cell_tables(_stock())
    assert sw.value_slot >= stock_rows
    assert status_map.cell_table(built)[sw.value_slot] not in status_map.cell_table(_stock())


def test_cell_is_recorded_for_the_aliasing_gate(monkeypatch):
    seen = {}
    real = recipe_ops.AddEnumLabel.apply

    def spy(self, data, ctx):
        out = real(self, data, ctx)
        seen["cells"] = set(ctx.authored_cells)
        seen["slot"] = ctx.handles[self.spec.id]["slot"]
        return out
    monkeypatch.setattr(recipe_ops.AddEnumLabel, "apply", spy)
    res = _compile([_op()])
    assert res.ok, res.errors
    assert seen["slot"] in seen["cells"]


@pytest.mark.parametrize("states,why", [
    (set(), "no page root reaches"),
    ({(None, 1)}, "cannot be written back"),
    ({(23, 0)}, "cannot be written back"),
    ({(23, 1), (14, 1)}, "different font/align"),
])
def test_refused_when_the_entering_state_is_unknown(monkeypatch, states, why):
    monkeypatch.setattr(enumlabel, "entering_state", lambda *_a: set(states))
    err = _errors(_op())
    assert "LABEL_REFUSED" in err and why in err, err


def test_layout_gate_refuses_a_leak(monkeypatch):
    """Without the restore record the label's font reaches HOME's next switch: refused."""
    monkeypatch.setattr(recipe_ops.AddEnumLabel, "_restore", lambda self, placed, parent: None)
    err = _errors(_op())
    assert "LABEL_REFUSED" in err and "besides its own" in err and "4:9" in err, err


def test_home_placement_passes_the_gate(built):
    stock = _stock()
    obj, _sw = _label_switch(built)
    assert enumlabel.foreign_deltas(stock, built, CLIMATE_PARENT, obj) == []
