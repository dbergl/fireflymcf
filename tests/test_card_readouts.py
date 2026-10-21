"""v49: captioned readouts in the ELECTRICAL Inverter card (spec 2026-10-15-inverter-card-readouts).

`add_register_readout`'s `sub` / `draw: false` (a cell and routine with no widget of its own) and
`add_card_readouts` (captions, values and units drawn together inside cardlayout's frame)."""
import json
from pathlib import Path

from fireflymcf import guidata, recipe

REPO = Path(__file__).resolve().parents[1]
STOCK = REPO / "firefly-13.6/mcf.bin"
SHA = "d66b41ffafaae4f5ec47cf90714f14bb1954fc3a5ae38defa98a94cd9643d382"


def _compile(ops):
    p = {"schema": 1, "base_sha256": SHA, "build_name": "card-readouts-test", "ops": ops}
    return recipe.compile_project(recipe.Project.from_json(json.dumps(p)), STOCK.read_bytes())


def test_draw_false_allocates_a_cell_and_routine_but_no_object():
    base = _compile([])
    res = _compile([
        {"id": "f", "type": "append_filter", "enabled": True,
         "params": {"dgn": 0x1FFD7, "mode": 2, "payload_byte": 3, "instance": 0x41,
                    "compare_byte": 0}},
        {"id": "r", "type": "add_register_readout", "enabled": True,
         "params": {"register": {"ref": "f"}, "sub": 32000, "shift": 1, "draw": False}}])
    assert res.ok, res.errors
    assert len(guidata.object_tables(res.data)[1]) == len(guidata.object_tables(base.data)[1])
    assert "r" in res.unverified


# --- add_card_readouts on the v48 card layout ------------------------------------------------


# --- v50: inverter DC current replaces Batt Amps ----------------------------------------------


# --- add_card_readouts `parent` ----------------------------------------------------------------

def _rows():
    return [{"caption": "DC Amps", "value": {"slot": 232}, "format": {"width": 4, "places": 1},
             "unit": "A"}]


def _with_readouts(extra=None):
    """Straight onto stock: a non-829 parent needs no card layout."""
    return _compile([{"id": "cr", "type": "add_card_readouts", "enabled": True,
                      "params": {"rows": _rows(), **(extra or {})}}])


def test_parent_default_is_byte_identical(monkeypatch):
    """`parent` omitted and `parent=829` take the same, unchanged path: the frame check, never
    `insert.insert_object`. (Stock 829 is not a card layout, so both refuse identically here;
    the byte-for-byte compile of real card-layout projects is the golden table's check.)"""
    import pytest
    from fireflymcf import cardreadouts, insert
    def no_insert(*a, **k):
        raise AssertionError("parent 829 must not use insert.insert_object")
    monkeypatch.setattr(insert, "insert_object", no_insert)
    data = STOCK.read_bytes()
    obj = cardreadouts.build_object(data, [cardreadouts.Row("DC Amps", 232, 4, 1, "A")])
    msgs = []
    for args in ((), (829,)):
        with pytest.raises(cardreadouts.CardReadoutsError) as e:
            cardreadouts.attach(data, obj, *args)
        msgs.append(str(e.value))
    assert msgs[0] == msgs[1] and "aes_card_layout" in msgs[0]


def test_parent_other_object():
    from fireflymcf import records
    res = _with_readouts({"parent": 902})
    assert res.ok, res.errors
    gs, tab = guidata.guidata_extent(res.data)[0], guidata.object_tables(res.data)[1]
    new = len(tab) - 1
    assert any(r.wtype == 5 and r.child_index == new
               for r in records.object_records(res.data, gs + tab[902]))
    labels = [r.label for r in records.object_records(res.data, gs + tab[new])
              if r.wtype == 11]
    assert labels == ["DC Amps", "A"]


def test_parent_without_template_refuses():
    res = _with_readouts({"parent": 13})
    assert not res.ok
    assert res.errors[0]["code"] == "WRONG_SHAPE"
