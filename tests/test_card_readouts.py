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


PASTE_PARENT = 793     # roots 5, 16, 39, 45: a slot-free attach, and the restore is needed


def test_parent_other_object():
    from fireflymcf import records
    res = _with_readouts({"parent": PASTE_PARENT})
    assert res.ok, res.errors
    gs, tab = guidata.guidata_extent(res.data)[0], guidata.object_tables(res.data)[1]
    new = len(tab) - 1
    assert any(r.wtype == 5 and r.child_index == new
               for r in records.object_records(res.data, gs + tab[PASTE_PARENT]))
    recs = records.object_records(res.data, gs + tab[new])
    assert [r.label for r in recs if r.wtype == 11] == ["DC Amps", "A", ""]
    assert recs[-1].wtype == 11 and not recs[-1].label      # the font/align restore
    assert "cr" in res.unverified


def test_paste_needs_the_restore(monkeypatch):
    """Without the restore record the rows' font and align reach every later record on the
    pages reaching 793 (538 deltas): the whole-page gate refuses it."""
    from fireflymcf import enumlabel
    monkeypatch.setattr(enumlabel, "restore_state", lambda *a, **k: None)
    res = _with_readouts({"parent": PASTE_PARENT})
    assert not res.ok
    assert res.errors[0]["code"] == "LAYOUT_REFUSED" and "changes" in res.errors[0]["message"]


def test_parent_whose_attach_carries_a_slot_refuses():
    """902's first wtype-5 attach carries value_slot 205: the captions would inherit it."""
    res = _with_readouts({"parent": 902})
    assert not res.ok
    assert res.errors[0]["code"] == "LAYOUT_REFUSED"
    assert "inherits value_slot 205" in res.errors[0]["message"]


def test_parent_without_template_refuses():
    res = _with_readouts({"parent": 13})
    assert not res.ok
    assert res.errors[0]["code"] == "WRONG_SHAPE"


def test_parent_that_moves_other_controls_refuses():
    """Attached under 199 (root 15), the readouts' flow shifted every later control on the page,
    touch regions included (`15:5/846:4 wtype 12 moved (453,-295)`). The whole-page gate
    (`enumlabel.foreign_deltas`) refuses it."""
    res = _with_readouts({"parent": 199})
    assert not res.ok
    assert res.errors[0]["code"] == "LAYOUT_REFUSED", res.errors
    assert "changes" in res.errors[0]["message"]


def test_parent_other_than_829_is_unverified():
    """Only 829's path was flashed; any other index is unverified from validate, and a ref is
    unverified until it resolves (`test_parent_ref_resolving_to_829_is_verified`)."""
    from fireflymcf import recipe_ops
    from fireflymcf.recipe import OpSpec
    def made(extra):
        return recipe_ops.AddCardReadouts(OpSpec(id="cr", type="add_card_readouts",
                                                 params={"rows": _rows(), **extra}))
    assert made({}).hardware_verified is True
    assert made({"parent": 829}).hardware_verified is True
    assert made({"parent": 902}).hardware_verified is False
    assert made({"parent": {"ref": "x"}}).hardware_verified is False


# The compact AES frame `aes_card_layout` expects, built from stock (one full-width BMS button).
_CARD_FRAME = [
    {"id": "link", "type": "add_option_follower", "enabled": True,
     "params": {"watch_channel": 25, "target_channel": 44, "gate_mask": 0xFF00,
                "gate_on_value": 0x100}},
    {"id": "pump", "type": "add_pump_auto_off", "enabled": True, "params": {"allow_disable": True}},
    {"id": "swap", "type": "swap_aes_electrical", "enabled": True,
     "params": {"pump_section": {"ref": "pump"}, "link_indicator": {"ref": "link"}}},
    {"id": "heat", "type": "add_aes_batt_heat", "enabled": True,
     "params": {"x": 84, "y": 156, "w": 328, "h": 54, "text": "Heater"}},
    {"id": "heat-status", "type": "add_aes_batt_heat_status", "enabled": True,
     "params": {"button": {"ref": "heat"}}},
    {"id": "cards", "type": "aes_card_layout", "enabled": True, "params": {}},
]


def test_parent_ref_resolving_to_829_is_verified():
    """`parent` given as a ref whose object is 829 takes 829's flashed path and is reported
    verified, exactly as the literal 829; only a resolved parent other than 829 is not."""
    def build(extra):
        res = _compile(_CARD_FRAME + [{"id": "cr", "type": "add_card_readouts", "enabled": True,
                                       "params": {"rows": _rows(), **extra}}])
        assert res.ok, res.errors
        return res
    literal, by_ref = build({}), build({"parent": {"ref": "heat"}})
    assert by_ref.data == literal.data
    assert "cr" not in literal.unverified
    assert "cr" not in by_ref.unverified


def test_parent_829_never_widens_an_unverified_status(monkeypatch):
    """verify settles the parent half of the status and nothing else: an op validate() left
    unverified for another reason stays unverified on 829."""
    from fireflymcf import recipe_ops
    monkeypatch.setattr(recipe_ops.AddCardReadouts, "hardware_verified", False)
    res = _compile(_CARD_FRAME + [{"id": "cr", "type": "add_card_readouts", "enabled": True,
                                   "params": {"rows": _rows()}}])
    assert res.ok, res.errors
    assert "cr" in res.unverified


def test_paste_gate_without_a_match_table_is_a_structured_refusal(monkeypatch):
    """A grown match table the whole-page gate cannot locate refuses LAYOUT_REFUSED, not a raw
    `MatchTableNotFound` out of compile_project."""
    from fireflymcf import enumlabel
    def lost(*a, **k):
        raise guidata.MatchTableNotFound("no match table")
    monkeypatch.setattr(enumlabel, "foreign_deltas", lost)
    res = _with_readouts({"parent": PASTE_PARENT})
    assert not res.ok
    assert res.errors[0]["code"] == "LAYOUT_REFUSED", res.errors
