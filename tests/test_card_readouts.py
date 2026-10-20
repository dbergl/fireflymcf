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
