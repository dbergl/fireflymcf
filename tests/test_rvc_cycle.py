"""`add_rvc_button`'s cycle and display forms, drawn and driven by `add_button_states`.

Corpus-reading: compiles against stock 13.6. A three-state cycle button goes on HOME's root
object 4 at (210, 332) 80 x 44, sending one of three payloads on DGN 0x1FFF6; a display tile
sits beside it. `add_button_states` reads var 86 (the Water Pump's DC dimmer status) as
`(slot & 0x0C) >> 2`: 0, 1 and 2 are the three states, anything else is unknown. The routine is
run in `tests.thumbsim.Sim` for every reported value: the cell names the state, and the cycle's
variant is the state AFTER it, so a tap sends one step on from what the unit reports.
"""
import json
from pathlib import Path

import pytest

from fireflymcf import (followchannel, gestures, guidata, newbutton, recipe, records, register,
                        status_map, theme, thumb)
from tests.thumbsim import Sim

pytestmark = pytest.mark.slow  # module-scoped emulation fixtures: every worker pays the setup

REPO = Path(__file__).resolve().parents[1]
STOCK = REPO / "firefly-13.6/mcf.bin"
SHA = "d66b41ffafaae4f5ec47cf90714f14bb1954fc3a5ae38defa98a94cd9643d382"
HOME = 4
DGN = 0x1FFF6
VAR = 86
PAYLOADS = ["00ffffffffffffff", "01ffffffffffffff", "02ffffffffffffff"]
STALE = followchannel.STALE_SENTINEL
TEXTS = ["Alpha", "Bravo", "Charlie"]


def _stock():
    try:
        return STOCK.read_bytes()
    except FileNotFoundError:
        pytest.skip("needs the stock 13.6 image")


def _button(oid="mode", **over):
    params = {"parent": HOME, "x": 210, "y": 332, "w": 80, "h": 44, "text": "Mode", "dgn": DGN,
              "cycle": {"payloads": PAYLOADS}}
    params.update(over)
    return {"id": oid, "type": "add_rvc_button", "enabled": True,
            "params": {k: v for k, v in params.items() if v is not ...}}


def _tile(oid="tile"):
    return {"id": oid, "type": "add_rvc_button", "enabled": True,
            "params": {"parent": HOME, "x": 300, "y": 332, "w": 80, "h": 44, "text": "Pump",
                       "display": True}}


def _match(v, **over):
    m = {"source": {"var": VAR}, "mask": 0x0C, "shift": 2, "eq": v}
    m.update(over)
    return m


def _entry(ref="mode", n=3, **over):
    e = {"button": {"ref": ref},
         "states": [{"match": _match(k), "text": TEXTS[k], "lit": k > 0} for k in range(n)],
         "unknown": {"text": "?"}}
    e.update(over)
    return e


def _states(*entries, oid="st", enabled=True):
    return {"id": oid, "type": "add_button_states", "enabled": enabled,
            "params": {"buttons": list(entries) or [_entry()]}}


def _project(ops):
    p = {"schema": 1, "base_sha256": SHA, "build_name": "rvc-cycle-test", "ops": ops}
    return recipe.Project.from_json(json.dumps(p))


def _compile(ops):
    return recipe.compile_project(_project(ops), _stock())


def _codes(res):
    assert not res.ok
    return [e["code"] for e in res.errors]


GATE = _match(1, mask=0x04, shift=2)          # the pump's own bit: lit only while it is on
OPS = [_button(), _tile(),
       _states(_entry(), _entry("tile", n=2, lit_when=GATE,
                                states=[{"match": _match(0), "text": "Off", "lit": True},
                                        {"match": _match(1), "text": "On", "lit": True}]))]


@pytest.fixture(scope="module")
def run():
    cache = {}
    res = recipe.compile_cached(_project(OPS), _stock(), cache)
    assert res.ok, res.errors
    final = cache[recipe._prefix_key(SHA, _project(OPS).ops)][1][2]
    return res.data, final


@pytest.fixture(scope="module")
def built(run):
    return run[0]


@pytest.fixture(scope="module")
def handles(run):
    return run[1]


def _routine(data):
    gc = theme.guicode_section(data)
    code = data[gc.offset:gc.offset + gc.length]
    entry = followchannel.chain_from_hook(data)[0]
    blob = bytes(code[entry - theme.GUICODE_BASE_DEFAULT:gc.length - 4])
    dis = []
    for a, t in thumb.disassemble(blob, entry):
        dis.append((a, t))
        if t.startswith("b.w #"):
            break
    return blob, entry, dis


def _sim(data, word):
    blob, entry, dis = _routine(data)
    sim = Sim(blob, entry, dis)
    sim.wr(register.status_slot_addr(VAR), word, 2)
    sim.run()
    return sim


def test_cycle_bindings(built, handles):
    group = handles["mode"]["group"]
    g = guidata.group_table(built)[group]
    assert bytes(g.raw[:4]) == bytes.fromhex("12800000"), "nibble N-1 = 2, no cycle on release"
    beh = gestures.describe_group(built, group)
    assert not beh.cycles_variant and beh.variant_count == 2
    by_variant = {e.variant: e for e in beh.emissions}
    assert sorted(by_variant) == [0, 1, 2]
    bt = guidata.binding_table(built)
    for k, payload in enumerate(PAYLOADS):
        e = by_variant[k]
        assert e.phases == 0x06 and e.dgn == DGN & 0xFFFF
        assert newbutton.binding_wire_frame(bt[e.binding_index].raw) == \
            ((6 << 26) | (DGN << 8) | 0x9F, bytes.fromhex(payload))
    assert group in newbutton.hold_safe_groups(_stock())


# (reported var-86 word, the state the cell names (3 = unknown), the variant: the next state)
CASES = [(STALE, 3, 0), (0x40, 0, 1), (0x44, 1, 2), (0x48, 2, 0), (0x4C, 3, 0)]


@pytest.mark.parametrize("word,state,variant", CASES)
def test_cycle_follows_the_report(built, handles, word, state, variant):
    sim = _sim(built, word)
    b = handles["st"]["buttons"][0]
    assert sim.rd(b["cell_addr"], 2) == state
    assert sim.calls == [(handles["mode"]["group"], variant)], "one setVariant, the tile none"


# the tile: off/on by the pump bit, gated on the same bit, so "Off" is drawn unlit (cell 0 + 3);
# a stale slot fails the gate too (unknown 2 + 3 = 5, which no arm names: the default arm)
@pytest.mark.parametrize("word,cell", [(STALE, 5), (0x40, 3), (0x44, 1), (0x4C, 2)])
def test_gate_offsets_the_cell(built, handles, word, cell):
    b = handles["st"]["buttons"][1]
    assert _sim(built, word).rd(b["cell_addr"], 2) == cell


def test_arms_draw_every_state(built, handles):
    gs, tab = guidata.guidata_extent(built)[0], guidata.object_tables(built)[1]
    for b, want in zip(handles["st"]["buttons"],
                       ([(0xFFFF, "?"), (2, "Charlie"), (1, "Bravo"), (0, "Alpha")],
                        [(0xFFFF, "?"), (4, "On"), (3, "Off"), (1, "On"), (0, "Off")])):
        recs = records.object_records(built, gs + tab[b["object"]])
        (sw,) = [r for r in recs if r.wtype == 6]
        assert sw.value_slot == b["slot"]
        assert status_map.cell_table(built)[b["slot"]] == b["cell_addr"]
        cands = guidata.switch_candidates(built, sw, guidata.find_match_table(built))
        assert [v for v, _o in cands] == [v for v, _t in want]
        for (_v, text), (_m, obj) in zip(want, cands):
            arm = records.object_records(built, gs + tab[obj])
            assert [r.label for r in arm if r.wtype == 11 and r.label] == [text]
    # the gated arms (3, 4) draw the off fill whatever the state's `lit`
    tile = handles["st"]["buttons"][1]
    fills = {v: look["fill"] for v, look in tile["arms"]}
    assert fills[3] == fills[4] == fills[0xFFFF] != fills[1] == fills[0]


def test_display_tile_sends_nothing(built, handles):
    h = handles["tile"]
    assert h["group"] is None and h["form"] == "display"
    assert gestures.touch_ids(built, h["object"]) == []


def test_undriven_cycle_refuses():
    res = _compile([_button()])
    assert _codes(res) == ["CYCLE_UNDRIVEN"]
    assert _codes(_compile([_button(), _states(enabled=False)])) == ["CYCLE_UNDRIVEN"]
    assert _codes(_compile([_states(), _button()])) == ["CYCLE_UNDRIVEN"]


def test_cycle_free_pool_refuses():
    assert _codes(_compile([_button(group_pool="free"), _states()])) == ["BAD_PARAMS"]


@pytest.mark.parametrize("bad", [
    {"cycle": {"payloads": PAYLOADS[:1]}},                         # one state
    {"cycle": {"payloads": PAYLOADS}, "tap": {"payload": PAYLOADS[0]}},
    {"display": True},                                             # display with a dgn
    {"dgn": ...},                                                  # no dgn
])
def test_button_params_refuse(bad):
    assert _codes(_compile([_button(**bad), _states()])) == ["BAD_PARAMS"]


@pytest.mark.parametrize("entry", [
    _entry(n=2),                                                   # 2 states, 3 payloads
    _entry(check_order=[0, 0, 1]),
    _entry(states=[{"match": _match(k), "text": "Two\nlines"} for k in range(3)]),
    _entry(states=[{"match": _match(k), "glyph": {"slot": 24, "char": "E"}} for k in range(3)]),
    _entry(states=[{"match": {"source": {"var": VAR}, "range": [3, 1]}} for _k in range(3)]),
    _entry(states=[{"match": _match(k), "colour": "red"} for k in range(3)]),
    _entry(unknown={"variant": 3}),
    _entry(lit_when={"source": {"var": VAR}}),
])
def test_states_params_refuse(entry):
    assert _codes(_compile([_button(), _states(entry)])) == ["BAD_PARAMS"]


def test_toggle_is_not_driven_here():
    """A toggle is `add_button_status`'s: one driven by a status op after it (so the static
    gate passes) is still refused by an `add_button_states` entry naming it."""
    toggle = _button(cycle=..., toggle={"off_payload": PAYLOADS[0], "on_payload": PAYLOADS[1]})
    status = {"id": "lit", "type": "add_button_status", "enabled": True,
              "params": {"buttons": [{"button": {"ref": "mode"}, "source": {"var": VAR},
                                      "compare": {"bit": 4}}]}}
    res = _compile([toggle, _states(_entry(n=2)), status])
    assert _codes(res) == ["BAD_PARAMS"] and "toggle" in res.errors[0]["message"]


def test_check_order_picks_the_first_listed_match():
    """Overlapping matches: state 1 (range 0..3) listed first takes every report state 0's
    `eq 0` would have taken."""
    states = [{"match": _match(0)}, {"match": _match(0, eq=..., range=[0, 3])},
              {"match": _match(2)}]
    for st in states:
        st["match"] = {k: v for k, v in st["match"].items() if v is not ...}
    res = _compile([_button(), _states(_entry(states=states, check_order=[1, 0, 2]))])
    assert res.ok, res.errors
    sim = _sim(res.data, 0x40)
    assert sim.calls[-1][1] == 2, "state 1 matched first: the next is 2"


def test_wrap_false_stops_at_the_last_state():
    res = _compile([_button(), _states(_entry(wrap=False))])
    assert res.ok, res.errors
    assert _sim(res.data, 0x48).calls[-1][1] == 2


def test_test_can_fail(built, handles, monkeypatch):
    """The cycle case fails when the routine names the state, not the next one: build the
    same project with the variant map replaced by the identity."""
    from fireflymcf import statusfold
    real = statusfold.States.__init__

    def ident(self, *a, **kw):
        real(self, *a, **kw)
        if self.variant_group is not None:
            object.__setattr__(self, "variants", tuple(range(len(self.variants) - 1)) + (0,))
    monkeypatch.setattr(statusfold.States, "__init__", ident)
    res = _compile([_button(), _states()])
    assert res.ok, res.errors
    assert _sim(res.data, 0x40).calls[-1][1] == 0, "identity map: state 0 -> variant 0"


def test_arm_gate_sees_a_misplaced_arm(built, handles):
    """`_arm_deltas` walks every arm: clean on the build, and a caption box narrowed in one
    arm (as a state arm drawn in another shape would be) is reported."""
    import struct
    from fireflymcf.recipe_ops import AddButtonStates
    b = handles["st"]["buttons"][0]
    gs, tab = guidata.guidata_extent(built)[0], guidata.object_tables(built)[1]
    recs = records.object_records(built, gs + tab[b["object"]])
    (cap,) = [i for i, r in enumerate(recs) if r.wtype == 6]
    info = {"arms_base": b["arms_base"], "switch_index": cap}
    values = [v for v, _l in b["arms"][1:]]
    assert AddButtonStates._arm_deltas(built, b["object"], info, values) == []
    arm = b["arms_base"] + [v for v, _l in b["arms"]].index(1)
    (lab,) = [r for r in records.object_records(built, gs + tab[arm]) if r.wtype == 11 and r.label]
    bad = bytearray(built)
    struct.pack_into("<H", bad, lab.geom_off[2], lab.geom[2] - 16)
    assert AddButtonStates._arm_deltas(bytes(bad), b["object"], info, values)
