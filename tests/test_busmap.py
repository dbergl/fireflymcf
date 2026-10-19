"""`busmap` / `slottrace` -- every receive-filter entry, what the panel does with it, and what the
panel calls it (`GUICODE.md` Finding 82).

Pinned on 13.6 (the coverage table and a set of names), spot-checked on 11.23 and 14.7, and held
to three independent oracles: Ghidra's slot-bridge dump (`data/slot_bridge_136.txt`), the tank
names (`tanknames`), and the send side's selector join (`config.option_selector`). The stub tests
show the pins can fail.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from fireflymcf import (busmap, cli, config, g12rules, outputs, register, slottrace, status_map,
                        tanknames)

REPO = Path(__file__).resolve().parents[1]
B136 = "firefly-13.6/mcf.bin"
B1123 = "firefly-11.23/mcf.bin"
B147 = "firefly-14.7 (Terrain)/mcf.bin"


def image(name):
    return (REPO / name).read_bytes()


def _clear():
    for mod in (busmap, slottrace):
        for v in vars(mod).values():
            if callable(getattr(v, "cache_clear", None)):
                v.cache_clear()


@pytest.fixture
def clean_caches():
    _clear()
    yield
    _clear()


def by_var(data):
    return {e.var: e for e in busmap.receive_map(data)}


# ------------------------------------------------------------------------------ the pins

COVERAGE_136 = {
    0x1FEDA: {"entries": 75, "named": 41, "drawn": 45, "drawn-unnamed": 7, "write-only": 30},
    0x15FCE: {"entries": 53, "named": 27, "drawn": 24, "drawn-unnamed": 6, "config": 11,
              "logic-only": 4, "write-only": 14},
    0x1FBDA: {"entries": 27, "named": 11, "drawn": 11, "logic-only": 1, "write-only": 15},
    0x1FFB7: {"entries": 5, "named": 5, "drawn": 5},
    0x1FECA: {"entries": 5, "write-only": 4, "inert": 1},
    0x1FFFD: {"entries": 11, "named": 3, "drawn": 4, "drawn-unnamed": 1, "logic-only": 2,
              "write-only": 5},
    0x1FFE2: {"entries": 15, "drawn": 3, "drawn-unnamed": 3, "write-only": 12},
}


def test_coverage_136():
    m = busmap.receive_map(image(B136))
    cov = busmap.coverage(m)
    for dgn, want in COVERAGE_136.items():
        assert dict(cov[dgn]) == want, hex(dgn)
    classes = {c: sum(1 for e in m if e.cls == c) for c in busmap.CLASSES}
    assert classes == {"drawn": 128, "config": 11, "logic-only": 13, "write-only": 132,
                       "untraced": 0, "inert": 14}
    assert len(m) == 298 and sum(e.named for e in m) == 111


NAMES_136 = {   # var: (name, source)
    0: ("FRESH", "gauge"), 138: ("GREY2", "gauge"),
    6: ("Gen Hrs", "readout"),                       # 1FFDC GENERATOR_STATUS_1
    42: ("Low Volts", "control"),                    # 15FCE selector 0x2F (DGN-MAP)
    43: ("HVAC Load", "control"),
    44: ("Start at", "readout"),                     # selector 0x31, AES start-at voltage
    103: ("AC Out Volts", "readout"), 104: ("AC Out Freq.", "readout"),     # 1FFD7
    116: ("AC Input Volts", "readout"), 117: ("AC Input Freq.", "readout"),  # 1FFCA
    106: ("DC Current / I/C AMPS / I/C Amps", "readout"),                    # 1FFFD inst 1
    119: ("LINE 1", "readout"), 120: ("LINE 2", "readout"),                 # 1FFAD
    126: ("Low Batt. Cutout", "readout"), 127: ("VAC Dropout", "readout"),
    206: ("Absorb", "readout"), 230: ("Absorb Hours", "readout"),
    283: ("Bath Fan", "option"), 292: ("AGS / AES / AES Single Output", "option"),
    294: ("floorplan", "option"),
    86: ("Water Pump", "outputs-page"), 82: ("Bath Fan", "outputs-page"),
}


def test_names_136():
    m = by_var(image(B136))
    for var, (name, source) in NAMES_136.items():
        e = m[var]
        assert (e.name, e.names[0].source) == (name, source), var


def test_the_cited_chains_136():
    """Var 42 (15FCE selector 0x2F, the Low Volts arming): read at 0x24A176, stored as a bool to
    0x20039A79, which the block re-reads to pick cell 45's colour (0x2003A814, DGN-MAP); the
    setVariant pointer call (r0=0x81) sits in the decided arm. Var 283 (selector 0xE9) reaches
    the config-set helper with config offset 0xD in r1."""
    m = by_var(image(B136))
    e42 = m[42]
    assert [r.at for r in e42.reads] == [0x24A176]
    cells = {x.sink.target for x in e42.effects if x.sink.kind == "cell"}
    assert cells == {0x20039A79, 0x2003A814}
    calls = [x.sink for x in e42.effects if x.sink.kind == "call"]
    assert {c.via for c in calls} == {0x20039908}
    assert all(("r0", "abs", 0x81) in c.args for c in calls)
    e283 = m[283]
    (x,) = [x for x in e283.effects if x.config_offset is not None]
    assert x.sink.target == config.config_set_helper(image(B136)) == 0x259100
    assert (x.config_offset, x.register) == (0xD, 0x20039B49)


# ------------------------------------------------------------------------------ the oracles

def test_ghidra_bridge_dump_reproduced():
    """Every clean colour/bool block Ghidra's script extracted (`status_map.load_bridge`) is found
    by the tracer, cell for cell, but one: var 124's dump lists 0x20039E24, whose store at
    0x24A7E0 writes `r3 = r2 >> 1` (`mov r3,r2` at 0x24A7D4) -- and `r2` was set before var 124's
    slot load at 0x24A7BC and is not written between. The dump attributes by position, not by register; the tracer is
    right there (and on vars 3 and 206/207, where the dump puts a neighbour's stores)."""
    data = image(B136)
    bridge = status_map.load_bridge("136")
    tr = slottrace._tracer(data)
    for var, b in bridge.items():
        reads = [r for r in slottrace.slot_reads(data, var) if r.at == int(b["read_addr"], 16)]
        got = {s.target for r in reads for s in r.sinks if s.kind == "cell"}
        want = {b["colour_cell"], b["bool_cell"]}
        if var == 124:
            assert want - got == {0x20039E24} and got == {0x20039E92}
            mov = tr.ins[tr.at[0x24A7D4]]
            assert (mov.mnemonic, mov.op_str) == ("mov", "r3, r2")
            seg = tr.ins[tr.at[0x24A7BC]:tr.at[0x24A7D4]]
            assert not any("r2" in tanknames._writes(i) for i in seg)
            continue
        assert got == want, var


@pytest.mark.parametrize("name", [B136, B1123, B147])
def test_tanks_through_the_generic_path(name):
    """The tank names come out of the generic trace and caption rules exactly as `tanknames`
    gives them, and the generic trace finds every store `tanknames.level_stores` does."""
    data = image(name)
    want = {i: n for i, (n, _s) in tanknames.tank_names(data).items()}
    m = busmap.receive_map(data, frozenset({busmap.DGN_TANK_STATUS}))
    assert {e.instance: e.name for e in m} == want
    for e in m:
        _loads, stores = tanknames.level_stores(data, e.var)
        got = {(s.at, s.target) for r in e.reads for s in r.sinks if s.kind == "cell"}
        assert set(stores) <= got, e.var


def test_dc_dimmer_names_are_phase_ones():
    data = image(B136)
    variants = outputs.output_name_variants(data)
    m = busmap.receive_map(data, frozenset({busmap.DGN_DC_DIMMER_STATUS_3}))
    seen = 0
    for e in m:
        page = {n.name for n in variants.get(e.instance, ()) if n.name}
        got = {n.text for n in e.names if n.source == "outputs-page"}
        assert got == page, e.var
        seen += bool(page)
    assert seen >= 20


@pytest.mark.parametrize("name,expect", [
    (B136, {0xE6: "Progressive Inverter", 0xE9: "Bath Fan", 0xEF: "AGS / AES / AES Single Output"}),
    (B147, {0xE4: "Starlink Power", 0xE7: "Lithionics Batteries"}),
])
def test_received_selector_agrees_with_the_sent_one(name, expect):
    """The receive side joins a 15FCE selector to a config offset through the compare block's
    config-set call (`r1` immediate); the send side joins them through the OPTIONS tap handlers
    (`config.option_selector`, Rules A/B). Two code paths, no shared instruction: they agree on
    every option, which is what settles 14.7's E4 = Starlink Power / E7 = Lithionics."""
    data = image(name)
    m = busmap.receive_map(data, frozenset({busmap.DGN_CONFIG}))
    checked = [x for e in m for x in e.effects if x.selector_check]
    assert checked and all(x.selector_check.endswith("agrees") for x in checked)
    got = {e.filter_value: e.name for e in m if e.cls == "config"}
    for sel, row in expect.items():
        assert got[sel] == row


def test_input_rows_match_g12_inputs():
    """1FBDA instances 0xA1-0xAA are the G12 input rows; every label `g12rules.input_status`
    reads (GUICODE Finding 54) is among the row names the generic rule finds."""
    data = image(B136)
    m = by_var(data)
    n = 0
    for st in g12rules.input_status(data):
        if st.label and re.match(r"^\d+\s*-\s*\S", st.label):
            assert st.label in {x.text for x in m[st.var].names}, st.var
            n += 1
    assert n >= 6


# ------------------------------------------------------------------------------ the negatives

@pytest.mark.parametrize("name", [B136, B1123, B147])
def test_the_base_never_escapes(name):
    sr = slottrace.state_reach(image(name))
    assert sr.escapes == ()
    assert len(sr.functions) >= 8


def test_state_reach_finds_the_known_reads():
    """Positive controls: var 138's `ldrsh.w r6,[r4,#0x114]` at 0x2484D2 (Finding 77's) and the
    config-buffer pointer load `[r0,#0x214]` inside the config-set helper (0x259104)."""
    sr = slottrace.state_reach(image(B136))
    assert (0x2484D2, "r4", 0) in sr.loads[slottrace.slot_disp(138)]
    assert any(a == 0x259104 for a, _r, _o in sr.loads[config.CONFIG_BUFFER_OFFSET])
    assert slottrace.slot_disp(277) == 0x262 and slottrace.slot_disp(255) == 0x1FE


def test_dm_rv_is_write_only_and_frame_parsed():
    """Finding 77, reproduced generically: the four live DM_RV vars are write-only with the
    guarantee, the fifth is inert, and the solar parser `FUN_00240D20` holds the DGN."""
    m = by_var(image(B136))
    for var in (162, 163, 164, 235):
        assert m[var].cls == "write-only" and "no load through it" in m[var].evidence
        assert 0x240D20 in m[var].frame_parsers
    assert m[277].cls == "inert"


def test_inert_is_the_mode():
    for e in busmap.receive_map(image(B136)):
        assert (e.cls == "inert") == (e.mode not in busmap.WRITING_MODES)
        assert e.slot == register.status_slot_addr(e.var)


def test_spec_names_are_labelled_and_never_a_name():
    assert busmap.spec_name(0x1FFB7) == "RV-C TANK_STATUS"
    assert busmap.spec_name(0x0EFDF).startswith("J1939 Proprietary A")
    assert busmap.spec_name(0x1FACE) is None
    for e in busmap.receive_map(image(B136)):
        for n in e.names:
            assert n.source in ("outputs-page", "output-caption", "option", *busmap.RULE_ORDER)


# ------------------------------------------------------------------------------ the pins can fail

def test_without_reads_nothing_is_drawn(clean_caches, monkeypatch):
    monkeypatch.setattr(slottrace, "slot_reads", lambda data, var: ())
    m = busmap.receive_map(image(B136))
    assert not any(e.cls == "drawn" for e in m)
    assert not any(e.name for e in m if e.dgn == busmap.DGN_TANK_STATUS)


def test_without_control_flow_the_decided_stores_vanish(clean_caches, monkeypatch):
    """Var 80's colour store (0x248DA6, cell chosen by `cmp r2,#1 ; beq.w`) is a control sink;
    with decided branches treated as undecided it is lost and the bridge oracle breaks."""
    monkeypatch.setattr(slottrace, "MAX_DEPTH", 0)
    data = image(B136)
    got = {s.target for r in slottrace.slot_reads(data, 80) for s in r.sinks}
    assert status_map.load_bridge("136")[80]["colour_cell"] not in got


def test_an_escape_voids_every_negative(clean_caches, monkeypatch):
    real = slottrace.state_reach

    def leaky(data):
        sr = real(data)
        return slottrace.StateReach(sr.functions, sr.loads, ((0x240000, "test"),))
    monkeypatch.setattr(slottrace, "state_reach", leaky)
    m = busmap.receive_map(image(B136))
    assert not any(e.cls == "write-only" for e in m)


# ------------------------------------------------------------------------------ spot checks

def test_other_builds():
    m1123 = busmap.receive_map(image(B1123))
    m147 = busmap.receive_map(image(B147))
    assert (len(m1123), len(m147)) == (291, 310)
    for m in (m1123, m147):
        assert sum(e.cls == "inert" for e in m) == sum(e.mode not in busmap.WRITING_MODES
                                                       for e in m)
    b147 = {e.var: e for e in m147}
    assert b147[296].name == "Starlink Power"


# ------------------------------------------------------------------------------ the command

def test_cli_bus_map(capsys):
    assert cli.main(["bus-map", str(REPO / B136), "--dgn", "1FFB7"]) == 0
    out = capsys.readouterr().out
    assert "FRESH (gauge)" in out and "RV-C TANK_STATUS" in out
    assert cli.main(["bus-map", str(REPO / B136), "--dgn", "1ffd7", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert {r["name"] for r in payload["receive"]} >= {"AC Out Volts", "AC Out Freq."}
    assert payload["coverage"]["1FFD7"]["entries"] == 3
