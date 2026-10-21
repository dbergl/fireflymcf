"""`pin_filter_instance`: the narrow path, and the `from_instance` re-pin (corpus 13.6)."""
import hashlib

import pytest

from fireflymcf import guidata, recipe

STOCK_PATH = "firefly-13.6/mcf.bin"


@pytest.fixture(scope="module")
def stock():
    with open(STOCK_PATH, "rb") as f:
        return f.read()


def _compile(stock, **params):
    p = recipe.Project(
        base_sha256=hashlib.sha256(stock).hexdigest(), build_name=None,
        ops=[recipe.OpSpec(id="pin", type="pin_filter_instance", params=params)])
    return recipe.compile_project(p, stock)


def test_repin_var13_from_0_to_3(stock):
    r = _compile(stock, var=13, instance=3, dgn=0x1FFE2, compare=0, from_instance=0)
    assert r.ok, r.errors
    old = guidata.variable_table(stock)[13].raw
    new = guidata.variable_table(r.data)[13].raw
    assert old[4] == 0 and new[4] == 3
    assert new[:4] + new[5:] == old[:4] + old[5:]


def test_repin_refuses_wrong_from(stock):
    r = _compile(stock, var=13, instance=3, dgn=0x1FFE2, compare=0, from_instance=1)
    assert not r.ok
    assert r.errors[0]["code"] == "FILTER_REFUSED"
    assert "0x00" in r.errors[0]["message"]


def test_narrow_path_unchanged(stock):
    r = _compile(stock, var=13, instance=3, dgn=0x1FFE2, compare=0)
    assert not r.ok
    assert r.errors[0]["code"] == "FILTER_REFUSED"
    assert "already compares" in r.errors[0]["message"]


@pytest.mark.parametrize("var,dgn", [(9, 0x1FF9C), (14, 0x1FFE2)])
def test_repin_rooms_and_setpoint(stock, var, dgn):
    r = _compile(stock, var=var, instance=3, dgn=dgn, compare=0, from_instance=0)
    assert r.ok, r.errors
    assert guidata.variable_table(r.data)[var].raw[4] == 3
