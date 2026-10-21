"""The shift report: what `remove_records` and `move_element` moved besides their own records.

Report only: the compile carries `CompileResult.shifts` (`{op_id: [entry, ...]}`), its text at
the end of the op's own manifest line (still one entry per op), and the CLI sidecar's `shifts`
key; the bytes and the refusals are unchanged.

The page is the compact AES card `swap_aes_electrical` builds after the Water Pump feature
(`_pump_feature_ops`): object 1425, drawn on ELECTRICAL roots 5 and 39. Its record 8 is the Low
Volts attach (child 285), 9 its touch region (id 129), 10-13 the "AES" gear (box, caption,
touch, "L" icon), each laid out from the frame the record before it left.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from fireflymcf import recipe, recipe_ops
from fireflymcf.cli import main
from tests.test_recipe_ops import _pump_feature_ops

STOCK = "firefly-13.6/mcf.bin"
CARD = 1425
LOW_VOLTS = [{"selector": {"wtype": 5, "nth": 1}, "child": 285},
             {"selector": {"wtype": 12, "nth": 1}, "touch_id": 129}]


@pytest.fixture(scope="module")
def stock():
    with open(STOCK, "rb") as f:
        return f.read()


def _compile(stock, *specs):
    p = recipe.Project(base_sha256=hashlib.sha256(stock).hexdigest(), build_name=None,
                       ops=_pump_feature_ops() + list(specs))
    r = recipe.compile_project(p, stock)
    assert r.ok, r.errors
    return r


def _rr():
    return recipe.OpSpec(id="rr", type="remove_records",
                         params={"target": CARD, "records": LOW_VOLTS})


def _move(oid, selector, set_):
    return recipe.OpSpec(id=oid, type="move_element",
                         params={"target": CARD, "selector": selector, "set": set_})


def _by(entries, root):
    return {(e["object"], e["record"]): e for e in entries if e["root"] == root}


def _line(r, n=1):
    """The shift text of the n-th op after the Water Pump feature: its manifest entry (one per
    op) after the op's own verify() line."""
    assert len(r.manifest) == len(_pump_feature_ops()) + n, r.manifest
    head, sep, tail = r.manifest[-1].partition("; shift")
    assert sep, r.manifest[-1]
    return "shift" + tail


def test_remove_records_reports_the_gear_it_lifted(stock):
    """Removing the Low Volts attach takes its frame with it: the gear's four records (after
    the removal, records 8-11 of the card) move by (-59, -29) on both ELECTRICAL roots."""
    r = _compile(stock, _rr())
    entries = r.shifts["rr"]
    for root in (5, 39):
        got = _by(entries, root)
        assert set(got) == {(CARD, 8), (CARD, 9), (CARD, 10), (CARD, 11)}, entries
        for e in got.values():
            assert (e["kind"], e["dx"], e["dy"]) == ("moved", -59, -29), e
            assert e["new"] == [e["old"][0] - 59, e["old"][1] - 29]
            assert isinstance(e["offset"], int) and e["offset"] > 0
        assert got[(CARD, 9)]["label"].strip() == "AES"
        assert got[(CARD, 9)]["wtype"] == 11
        assert got[(CARD, 8)]["old"] == [200, 419]
        assert got[(CARD, 8)]["key"] == "%d:5/829:5/1425:8" % root
    # the removed attach, its child and its touch region are the op's own: not listed
    assert all(e["kind"] == "moved" for e in entries)
    line = _line(r)
    assert r.manifest[-1].startswith("remove_records: object 1425 lost 2 record(s)")
    assert line.startswith("shifted 8 record(s)"), line
    assert "on root 5:" in line and "'  AES' (-59,-29)" in line


def test_move_element_of_a_last_record_shifts_nothing_else(stock):
    """The "L" icon is the card's last record: moving it moves nothing else, and the report
    says so explicitly."""
    r = _compile(stock, _move("mv", {"wtype": 11, "label": "L"}, {"x": 3}))
    assert r.shifts["mv"] == []
    assert _line(r) == "shifted nothing else (roots 5, 39)"


def test_move_element_reports_the_siblings_that_follow(stock):
    """The gear's caption, touch region and icon lay out from the frame the gear box left, so
    moving the box moves them by the same delta; the box itself is the op's own."""
    r = _compile(stock, _move("gear", {"wtype": 33, "nth": 1}, {"x": -50, "y": -20}))
    for root in (5, 39):
        got = _by(r.shifts["gear"], root)
        assert set(got) == {(CARD, 11), (CARD, 12), (CARD, 13)}, r.shifts["gear"]
        assert {(e["dx"], e["dy"]) for e in got.values()} == {(9, -124)}
    assert _line(r).startswith("shifted 6 record(s)")


def test_the_report_changes_no_bytes(stock, monkeypatch):
    with_report = _compile(stock, _rr(), _move("mv", {"wtype": 11, "label": "L"}, {"x": 3}))
    monkeypatch.setattr(recipe_ops, "_side_shifts", lambda *a, **k: ([], []))
    without = _compile(stock, _rr(), _move("mv", {"wtype": 11, "label": "L"}, {"x": 3}))
    assert with_report.data == without.data
    # one entry per op either way, and each op's own verify() line is unchanged before "; "
    assert len(with_report.manifest) == len(without.manifest) == len(_pump_feature_ops()) + 2
    assert ([m.split("; shift")[0] for m in with_report.manifest]
            == [m.split("; shift")[0] for m in without.manifest])
    assert with_report.unverified == without.unverified


def test_a_failing_report_never_refuses(stock, monkeypatch):
    def boom(*a, **k):
        raise ValueError("walk failed")
    monkeypatch.setattr(recipe_ops, "_side_shifts", boom)
    r = _compile(stock, _rr())
    assert "rr" not in r.shifts
    assert r.manifest[-1].endswith("; shift report unavailable: ValueError: walk failed")


def test_the_shift_line_is_capped():
    entries = [{"root": 5, "kind": "moved", "label": "r%d" % i, "wtype": 11, "dx": 0,
                "dy": -1} for i in range(12)]
    line = recipe_ops._shift_line(entries, [5])
    assert line.startswith("shifted 12 record(s) on root 5: 'r0' (0,-1), ")
    assert line.endswith("... and 6 more")


def test_compile_sidecar_and_json_carry_shifts(tmp_path, stock, capsys):
    ops = _pump_feature_ops() + [_rr()]
    doc = {"schema": recipe.SCHEMA, "base_sha256": hashlib.sha256(stock).hexdigest(),
           "build_name": "Jayco B-Van V13 shift-test",
           "ops": [{"id": s.id, "type": s.type, "params": s.params} for s in ops],
           "preview": {}}
    proj = tmp_path / "p.ffproj.json"
    proj.write_text(json.dumps(doc))
    out = tmp_path / "mcf-test.bin"
    assert main(["compile", str(proj), STOCK, "-o", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "now 12; shifted 8 record(s) on root 5:" in printed
    side = json.loads((tmp_path / "mcf-test.bin.json").read_text())
    assert len(side["shifts"]["rr"]) == 8
    assert side["shifts"]["rr"][0]["dy"] == -29

    out2 = tmp_path / "mcf-test2.bin"
    assert main(["compile", str(proj), STOCK, "-o", str(out2), "--json"]) == 0
    js = json.loads(capsys.readouterr().out)
    assert js["shifts"] == side["shifts"]
    assert out2.read_bytes() == out.read_bytes()


def test_the_arm_filter_loses_no_shift(stock, monkeypatch):
    """`switch_arm_sets(touching=...)` walks only the arms an edit inside the card can show a
    different shift under; walking every arm set reports the same records."""
    from fireflymcf import pagewalk
    filtered = _compile(stock, _rr()).shifts["rr"]
    real = pagewalk.switch_arm_sets
    monkeypatch.setattr(pagewalk, "switch_arm_sets",
                        lambda data, roots, touching=None: real(data, roots))
    every = recipe.compile_project(
        recipe.Project(base_sha256=hashlib.sha256(stock).hexdigest(), build_name=None,
                       ops=_pump_feature_ops() + [_rr()]), stock).shifts["rr"]
    assert len(real(stock, [5])) > len(real(stock, [5], touching=0))
    assert ({(e["root"], e["key"], e["dx"], e["dy"]) for e in every}
            == {(e["root"], e["key"], e["dx"], e["dy"]) for e in filtered})
