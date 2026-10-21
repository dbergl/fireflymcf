"""`add_rvc_button`'s toggle form, driven by `add_button_status` through setVariant.

Corpus-reading: compiles against stock 13.6. A toggle button goes on HOME's root object 4 at
(210, 332) 80 x 44 with Eco's on/off payloads, and one `add_button_status` lights it from var
13's low nibble (eq 1); the routine calls `setVariant(group, lit)` every tick, so the button
sends "on" (variant 0) while its status reads off and "off" (variant 1) while it reads on.
"""
import json
from pathlib import Path

import pytest

from fireflymcf import (followchannel, gestures, guidata, newbutton, recipe, register, theme,
                        thumb)
from tests.thumbsim import Sim

REPO = Path(__file__).resolve().parents[1]
STOCK = REPO / "firefly-13.6/mcf.bin"
SHA = "d66b41ffafaae4f5ec47cf90714f14bb1954fc3a5ae38defa98a94cd9643d382"
HOME = 4
DGN = 0x1FEF9
ON = "07fff7ffffffffff"           # the spec's motivating map: Eco on
OFF = "07fff3ffffffffff"          # Eco off
STALE = followchannel.STALE_SENTINEL


def _stock():
    try:
        return STOCK.read_bytes()
    except FileNotFoundError:
        pytest.skip("needs the stock 13.6 image")


def _button(oid="eco", **over):
    params = {"parent": HOME, "x": 210, "y": 332, "w": 80, "h": 44, "text": "Eco", "dgn": DGN,
              "toggle": {"off_payload": OFF, "on_payload": ON}}
    params.update(over)
    return {"id": oid, "type": "add_rvc_button", "enabled": True,
            "params": {k: v for k, v in params.items() if v is not ...}}


def _status(ref="eco", oid="st", enabled=True, value=1):
    return {"id": oid, "type": "add_button_status", "enabled": enabled,
            "params": {"buttons": [{"button": {"ref": ref}, "source": {"var": 13},
                                    "mask": 0x0F, "compare": {"eq": value}}]}}


def _project(ops):
    p = {"schema": 1, "base_sha256": SHA, "build_name": "rvc-toggle-test", "ops": ops}
    return recipe.Project.from_json(json.dumps(p))


def _compile(ops):
    return recipe.compile_project(_project(ops), _stock())


def _codes(res):
    assert not res.ok
    return [e["code"] for e in res.errors]


DRIVEN = [_button(), _status()]


@pytest.fixture(scope="module")
def run():
    """(the finished image, the image after the button op alone, the handles), from one
    `compile_cached` over a fresh cache: its one-op prefix entry is the button op's output."""
    cache = {}
    res = recipe.compile_cached(_project(DRIVEN), _stock(), cache)
    assert res.ok, res.errors
    after_button, snap = cache[recipe._prefix_key(SHA, _project(DRIVEN).ops[:1])]
    final = cache[recipe._prefix_key(SHA, _project(DRIVEN).ops)][1][2]
    return res.data, after_button, final


@pytest.fixture(scope="module")
def built(run):
    return run[0]


@pytest.fixture(scope="module")
def group(run):
    return run[2]["eco"]["group"]


def _touch_ids(data):
    """The touch ids of the last object in table 1 (the button, before any wrap)."""
    return gestures.touch_ids(data, len(guidata.object_tables(data)[1]) - 1)


def test_toggle_bindings(built, group):
    g = guidata.group_table(built)[group]
    assert g.raw[1] == 0x80, "byte 1 0x80: the variant is not cycled on release"
    assert bytes(g.raw[:4]) == bytes.fromhex("11800000")
    beh = gestures.describe_group(built, group)
    assert not beh.cycles_variant
    # FUN_0021B5E8 writes a variant only while it is <= byte 0's low nibble (@0x21B60A-0x21B610):
    # at 0 the status routine's setVariant(group, 1) would be refused and "off" never sent.
    assert beh.variant_count == 1
    by_variant = {e.variant: e for e in beh.emissions}
    assert sorted(by_variant) == [0, 1] and len(beh.emissions) == 2
    bt = guidata.binding_table(built)
    for variant, payload in ((0, ON), (1, OFF)):
        e = by_variant[variant]
        assert e.phases == 0x06 and e.dgn == DGN & 0xFFFF
        assert newbutton.binding_wire_frame(bt[e.binding_index].raw) == \
            ((6 << 26) | (DGN << 8) | 0x9F, bytes.fromhex(payload))


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


@pytest.mark.parametrize("word,variant", [(0, 0), (4, 0), (1, 1), (0xF1, 1), (STALE, 0)])
def test_toggle_variant_driven(built, group, word, variant):
    blob, entry, dis = _routine(built)
    sim = Sim(blob, entry, dis)
    sim.wr(register.status_slot_addr(13), word, 2)
    sim.run()
    assert sim.calls == [(group, variant)]


def test_undriven_refuses():
    res = _compile([_button()])
    assert _codes(res) == ["TOGGLE_UNDRIVEN"]
    assert res.errors[0]["op_id"] == "eco"


def test_driven_by_disabled_refuses():
    assert _codes(_compile([_button(), _status(enabled=False)])) == ["TOGGLE_UNDRIVEN"]


def test_driven_before_refuses():
    """A status op before the button cannot drive it: the static check refuses before any op
    applies, so the code is TOGGLE_UNDRIVEN (not the status op's BAD_REF)."""
    assert _codes(_compile([_status(), _button()])) == ["TOGGLE_UNDRIVEN"]


def test_cached_matches(built):
    stock = _stock()
    cache = {}
    fresh = recipe.compile_cached(_project(DRIVEN), stock, cache)
    assert fresh.ok and fresh.data == built == _compile(DRIVEN).data
    edited = [_button(), _status(value=3)]
    warm = recipe.compile_cached(_project(edited), stock, cache)
    assert warm.ok and warm.data == _compile(edited).data
    assert _codes(recipe.compile_cached(_project([_button()]), stock, {})) == ["TOGGLE_UNDRIVEN"]
    assert _codes(recipe.compile_cached(_project([_button()]), stock, cache)) == \
        ["TOGGLE_UNDRIVEN"]


def test_toggle_free_pool_refuses():
    assert _codes(_compile([_button(group_pool="free"), _status()])) == ["BAD_PARAMS"]


def test_toggle_group_not_guicode_driven(run, group):
    """The button op's own output (the cached one-op prefix) has no GuiCode setter for its
    group. The finished image does: the status routine's `movs r0,#group` / `blx` through
    `0x20039908` is exactly what `guicode_selected_groups` scans for, so it sees the drive."""
    built, after_button, _handles = run
    stock = _stock()
    assert _touch_ids(after_button)[-1] == group
    assert group not in newbutton.guicode_selected_groups(after_button)
    assert group not in newbutton.guicode_selected_groups(stock)
    assert group in newbutton.hold_safe_groups(stock)
    assert group in newbutton.guicode_selected_groups(built), "the status routine drives it"
