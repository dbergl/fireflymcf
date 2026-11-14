"""The two facts the settable BMS durations rest on (2026-11-13).

1. **The hold is visible to GuiCode.** The LCD firmware keeps a 16-byte gesture record per
   group at SRAM `0x20006E6C` (`CONTROL-BEHAVIOR.md` section 2); byte `+0x09` is the phase,
   set to 3 when the hold threshold passes (`0x21BDB0`). A per-tick routine can watch it.
2. **Cell `0x72` can be swept for free.** The G12 boot list activates two byte-identical
   `0xE8` sweep records (43 and 79); `0x72`'s record (85) is activated nowhere, so retargeting
   the duplicate's one `movs r0,#imm8` sweeps `0x72` and still sweeps `0xE8` once.
"""
import json

import pytest

from fireflymcf import battdur, busmap, g12code, g12rules, guidata, recipe, register
from fireflymcf import g12code as gc
from fireflymcf import tankbroadcast as tb

STOCK = "firefly-13.6/mcf.bin"
STOCK_SHA = "d66b41ffafaae4f5ec47cf90714f14bb1954fc3a5ae38defa98a94cd9643d382"


def _sweep_records(data):
    return {r.index: r for r in g12rules.sweep_templates(data)}


def _activations(data):
    calls = tb._engine_calls(g12code.blob(data), g12code.analyse(data).starts)
    return [(addr, rule) for addr, slot, rule in calls if slot == 8]


def test_sweep_record_facts(mcf_136):
    recs = _sweep_records(mcf_136)
    dup, keep = battdur.G12_DUPLICATE
    assert recs[dup].raw == recs[keep].raw and recs[dup].payload[0] == 0xE8
    assert recs[battdur.SWEEP_RECORD].payload[0] == battdur.SELECTOR == 0x72
    acts = _activations(mcf_136)
    # One activation sets r0 too far back for the finder: `movs r0,#9` @+0x796, then only
    # strh/ldr/subs on other registers up to the `blx` @+0x7c6. Pin that, so every other
    # activation's record is known.
    assert [a for a, rule in acts if rule is None] == [0x7C6]
    ins = tb._decode(g12code.blob(mcf_136), g12code.analyse(mcf_136).starts)
    assert ins[0x796][:2] == ("movs", "r0, #9")
    assert not [a for a in ins if 0x796 < a < 0x7C6 and ins[a][1].startswith("r0")]
    assert [a for a, rule in acts if rule == dup] == [battdur.G12_PATCH_SITE + 4]
    assert not [a for a, rule in acts if rule == battdur.SWEEP_RECORD]
    assert battdur.SELECTOR not in gc.config_defaults_by_selector(mcf_136)
    assert not g12rules.rule_for_selector(mcf_136, battdur.SELECTOR)
    reg = register.resolve_register(mcf_136, dgn="DGN_CONFIG", selector=battdur.SELECTOR)
    assert reg.var_index == battdur.CONFIG_VAR == 280


def test_config_var_has_no_guicode_reader(mcf_136):
    """bus-map's 'write-only' verdict for var 280: no GuiCode load covers its slot."""
    entries = [e for e in busmap.receive_map(mcf_136) if e.var == battdur.CONFIG_VAR]
    assert entries and all(e.cls == "write-only" for e in entries)


def _heat_project():
    return recipe.Project.from_json(json.dumps({
        "schema": 1, "base_sha256": STOCK_SHA, "build_name": "battdur-feasibility",
        "ops": [
            {"id": "aesswap", "type": "swap_aes_electrical", "params": {}, "enabled": True},
            {"id": "heat", "type": "add_aes_batt_heat", "params": {}, "enabled": True},
        ]}))


@pytest.mark.slow
def test_hold_trigger_visible_in_emulator():
    from fireflymcf.emu.scenario import Scenario
    stock = open(STOCK, "rb").read()
    res = recipe.compile_project(_heat_project(), stock)
    assert res.ok, res.errors
    s = Scenario(res.data, timeout_s=400)
    s.boot()
    s.goto("ELECTRICAL")
    s.run(6.0)
    root = s._page_root(None)
    all_regions = s._regions(s._placed(root))
    groups, bindings = guidata.group_table(res.data), guidata.binding_table(res.data)
    heat = [r for r in all_regions if r[1] < len(groups) and any(
        b.dgn_low == 0xEF70 and b.raw[3] & 0x04
        for b in guidata.group_bindings(groups, bindings, r[1]))]
    assert len(heat) == 1, "exactly one Batt Heat touch region on ELECTRICAL"
    group = heat[0][1]
    x, y = s._tap_point(heat[0][0], group, all_regions)
    phase_at = battdur.GESTURE_BASE + battdur.GESTURE_STRIDE * group + battdur.GESTURE_PHASE

    def press(hold_s):
        s._m.touch.touch([(x, y)])
        seen, t0 = [], s.now
        while s.now - t0 < hold_s:
            s.run(0.05)
            seen.append(s.read_mem(phase_at, 1)[0])
        s._m.touch.touch([])
        for _ in range(6):
            s.run(0.05)
            seen.append(s.read_mem(phase_at, 1)[0])
        s.run(1.0)
        return [p for i, p in enumerate(seen) if i == 0 or p != seen[i - 1]]

    assert press(1.5) == [1, battdur.PHASE_HELD, 4]
    assert press(0.3) == [1, 2]
