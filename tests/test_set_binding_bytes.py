"""`set_binding_bytes`: same-size payload edits to existing bindings (corpus 13.6)."""
import hashlib

import pytest

from fireflymcf import guidata, newbutton, recipe

STOCK_PATH = "firefly-13.6/mcf.bin"
ARROW_DGN = 0x1FEF9


@pytest.fixture(scope="module")
def stock():
    with open(STOCK_PATH, "rb") as f:
        return f.read()


def _compile(stock, *ops):
    p = recipe.Project(
        base_sha256=hashlib.sha256(stock).hexdigest(), build_name=None,
        ops=[recipe.OpSpec(id="b%d" % i, type="set_binding_bytes", params=params)
             for i, params in enumerate(ops)])
    return recipe.compile_project(p, stock)


def _arrows(group, **over):
    params = {"group": group, "dgn": ARROW_DGN, "bindings": "all",
              "set": {"0": {"from": 0, "to": 7}}}
    params.update(over)
    return params


def _frame(data, i):
    return newbutton.binding_wire_frame(guidata.binding_table(data)[i].raw)


def test_arrows_to_instance_7(stock):
    r = _compile(stock, _arrows(130), _arrows(131))
    assert r.ok, r.errors
    steps = {451: 0xFC, 452: 0xFA, 453: 0xFB, 454: 0xF9}
    for i, step in steps.items():
        can_id, payload = _frame(r.data, i)
        assert payload == bytes([7, 0xFF, 0xFF, 0xFF, 0xFF, step, 0xFF, 0xFF])
        assert can_id == _frame(stock, i)[0]
        assert guidata.binding_table(r.data)[i].raw[:4] == guidata.binding_table(stock)[i].raw[:4]


def test_byte_position_is_wire_order(stock):
    r = _compile(stock, _arrows(130, bindings=[0], set={"5": {"from": 0xFC, "to": 0xFD}}))
    assert r.ok, r.errors
    old = guidata.binding_table(stock)[451].raw
    new = guidata.binding_table(r.data)[451].raw
    changed = [i for i in range(12) if old[i] != new[i]]
    assert changed == [newbutton._PAYLOAD_ORDER[5]]
    assert _frame(r.data, 451)[1][5] == 0xFD


def test_refuses_wrong_from(stock):
    r = _compile(stock, _arrows(130, set={"0": {"from": 3, "to": 7}}))
    assert not r.ok
    assert r.errors[0]["code"] == "EXPECT_MISMATCH"
    assert "00fffffffffcffff" in r.errors[0]["message"]


def test_refuses_wrong_dgn(stock):
    r = _compile(stock, _arrows(130, dgn=0x1FEDB))
    assert not r.ok
    assert r.errors[0]["code"] == "EXPECT_MISMATCH"


def test_bindings_subset(stock):
    r = _compile(stock, _arrows(130, bindings=[0]))
    assert r.ok, r.errors
    assert _frame(r.data, 451)[1][0] == 7
    assert _frame(r.data, 452) == _frame(stock, 452)


def test_no_such_group_and_bad_params(stock):
    assert _compile(stock, _arrows(9999)).errors[0]["code"] == "NO_SUCH_GROUP"
    assert _compile(stock, _arrows(130, bindings=[5])).errors[0]["code"] == "BAD_PARAMS"
    assert _compile(stock, _arrows(130, set={"8": {"from": 0, "to": 1}})
                    ).errors[0]["code"] == "BAD_PARAMS"
    assert _compile(stock, _arrows(130, set={})).errors[0]["code"] == "BAD_PARAMS"


def test_is_reported_unverified(stock):
    r = _compile(stock, _arrows(130))
    assert r.ok and r.unverified
