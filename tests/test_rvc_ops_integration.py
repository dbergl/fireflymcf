"""The generic RV-C control ops together on one page, against stock 13.6.

Corpus-reading (needs the research data; not collected without it). One project puts an
`add_rvc_button` toggle on HOME (root object 4), lights and drives it with an
`add_button_status` entry, shows a value as words with an `add_enum_label` on the same page,
captions it with an `add_text_label`, and carries a `set_binding_bytes` edit (the stock arrow
group 130's instance byte) and a `pin_filter_instance` narrowing (var 115 to instance 2). It asserts that the project compiles
with only the ops no coach has run listed unverified, that the cell-aliasing gate saw the fresh
cells and passed, that every op's verify passed, and that nothing on HOME moved but the new
records.
"""
import json
from pathlib import Path

import pytest

from fireflymcf import guidata, pagewalk, readout, recipe, records

REPO = Path(__file__).resolve().parents[1]
STOCK = REPO / "firefly-13.6/mcf.bin"
SHA = "d66b41ffafaae4f5ec47cf90714f14bb1954fc3a5ae38defa98a94cd9643d382"
HOME = 4

OPS = [
    {"id": "toggle", "type": "add_rvc_button", "enabled": True,
     "params": {"parent": HOME, "x": 210, "y": 332, "w": 80, "h": 44, "text": "Mode",
                "dgn": 0x1FEF9, "toggle": {"off_payload": "07fff3ffffffffff",
                                           "on_payload": "07fff7ffffffffff"}}},
    {"id": "lit", "type": "add_button_status", "enabled": True,
     "params": {"buttons": [{"button": {"ref": "toggle"}, "source": {"var": 13},
                             "mask": 0x0F, "compare": {"eq": 1}}]}},
    {"id": "words", "type": "add_enum_label", "enabled": True,
     "params": {"parent": HOME, "x": 88, "y": 372, "w": 98, "h": 16, "font": 11,
                "align": "centre", "source": {"var": 13, "mask": 0x0F},
                "values": {"0": "Off", "1": "On", "2": "Auto"}, "default": "--"}},
    {"id": "caption", "type": "add_text_label", "enabled": True,
     "params": {"parent": HOME, "x": 88, "y": 352, "w": 98, "h": 18, "font": 11,
                "align": "centre", "text": "Thermostat"}},
    {"id": "bytes", "type": "set_binding_bytes", "enabled": True,
     "params": {"group": 130, "dgn": 0x1FEF9, "bindings": "all",
                "set": {"0": {"from": 0, "to": 5}}}},
    {"id": "pin", "type": "pin_filter_instance", "enabled": True,
     "params": {"var": 115, "instance": 2, "dgn": 0x1FFC7, "compare": 0}},
]
UNVERIFIED = ["toggle", "lit", "words", "caption", "bytes"]      # pin_filter_instance's narrowing is flashed


def _stock():
    try:
        return STOCK.read_bytes()
    except FileNotFoundError:
        pytest.skip("needs the stock 13.6 image")


@pytest.fixture(scope="module")
def run():
    seen = {}
    real = recipe._finish

    def spy(project, stock, data, ctx, ops):
        res = real(project, stock, data, ctx, ops)
        seen.update(ctx=ctx, ops=ops)
        return res
    p = {"schema": 1, "base_sha256": SHA, "build_name": "rvc-ops-integration", "ops": OPS}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(recipe, "_finish", spy)
        res = recipe.compile_project(recipe.Project.from_json(json.dumps(p)), _stock())
    return res, seen


def test_compiles_with_only_the_expected_unverified(run):
    res, _seen = run
    assert res.ok, res.errors
    assert res.unverified == UNVERIFIED


def test_the_aliasing_gate_saw_fresh_cells(run):
    """The gate ran on a non-empty set (it is a no-op on an empty one) and every cell is grown."""
    res, seen = run
    assert res.ok, res.errors
    ctx = seen["ctx"]
    mine = {b["slot"] for b in ctx.handles["lit"]["buttons"]} | {ctx.handles["words"]["slot"]}
    assert mine and mine <= ctx.authored_cells
    _base, _types, stock_rows = readout.resolve_cell_tables(_stock())
    assert min(ctx.authored_cells) >= stock_rows
    recipe._assert_authored_cells_fresh(ctx)              # passes on its own, too


def test_every_op_verifies(run):
    res, seen = run
    assert res.ok, res.errors
    assert len(res.manifest) == len(OPS)
    for op in seen["ops"]:                                # again, on the finished image
        assert op.verify(res.data, seen["ctx"])


def _walk_sets(stock, data, roots):
    """Stock's arm sets, plus one per non-base arm of every switch the ops added."""
    n_stock = len(guidata.object_tables(stock)[1])
    sets = pagewalk.switch_arm_sets(stock, roots)
    sets += [v for v in pagewalk.switch_arm_sets(data, roots)
             if v and min(v) >= n_stock]
    return sets


def test_nothing_else_on_the_page_moved(run):
    res, _seen = run
    assert res.ok, res.errors
    stock, data = _stock(), res.data
    n_stock = len(guidata.object_tables(stock)[1])
    gs = guidata.guidata_extent(stock)[0]
    home_recs = len(records.object_records(stock, gs + guidata.object_tables(stock)[1][HOME]))

    def ours(r):
        # a record of an object the ops added, or an attach they appended to HOME
        return r.obj >= n_stock or (r.obj == HOME and r.index >= home_recs)
    sets = _walk_sets(stock, data, [HOME])
    assert len(sets) > 1, "no switch arm was walked"
    appeared = set()
    for values in sets:
        deltas = pagewalk.diff(pagewalk.walk_records(stock, HOME, switch_values=values),
                               pagewalk.walk_records(data, HOME, switch_values=values))
        foreign = [d for d in deltas if d.kind != "appeared" or not ours(d.after)]
        assert not foreign, pagewalk.describe(foreign[:20])
        appeared |= {d.after.label for d in deltas if d.after.label}
    assert {"Mode", "Off", "On", "Auto", "--", "Thermostat"} <= appeared
