"""The cross-table cell-aliasing gate.

Two halves:
  * `status_map.foreign_table_refs(data, cells)` is the cross-table view the renderer's page walk
    cannot produce (it traverses only object table 1). On the exact cells the coach-48V build
    aliased -- 254/256/258 -- it names the 3295-entry-table objects that read them and painted the
    background bars. This is the tooling that CAN see what the render cannot.
  * `recipe._assert_authored_cells_fresh` is the runtime guard: every display cell an op mints must
    be a freshly-grown index (>= the stock row count), never a reused stock index. That invariant
    makes the aliasing class impossible and is what would have failed the original coach-48V build,
    which reused 254/256/258 (< 665).
"""
import dataclasses
from pathlib import Path

import pytest

from fireflymcf import readout, recipe, status_map, tablegrow

ROOT = Path(__file__).resolve().parents[1]
STOCK = (ROOT / "firefly-13.6" / "mcf.bin").read_bytes()


def _ctx(authored):
    return recipe.Ctx(stock=STOCK, edits=[], insertions=[], handles={},
                      authored_cells=set(authored))


def test_foreign_refs_detect_the_historical_alias():
    """The cells the coach-48V bug reused are really read by objects in tables 0/2/3 -- the tables
    the render cannot see and the old spare scan ignored."""
    foreign = status_map.foreign_table_refs(STOCK, {254, 256, 258})
    assert {254, 256, 258} & set(foreign), "known aliased cells not detected: %r" % foreign
    assert all(ti in (0, 2, 3) for refs in foreign.values() for ti, _obj in refs)


def test_foreign_refs_empty_for_empty_set():
    assert status_map.foreign_table_refs(STOCK, set()) == {}


def test_gate_passes_for_freshly_grown_cells():
    """Cells minted by allocate_fresh_cells are indices >= the stock row count, so the freshness
    gate passes."""
    _image, cells, _edits = readout.allocate_fresh_cells(STOCK, [(4, None), (1, 1), (2, 3)])
    stock_len = tablegrow.read_cmpw_bound(STOCK, readout._CELL_BOUND_SITE) + 1
    assert all(c.slot >= stock_len for c in cells)
    recipe._assert_authored_cells_fresh(_ctx(c.slot for c in cells))    # does not raise


def test_gate_is_a_noop_when_nothing_authored():
    recipe._assert_authored_cells_fresh(_ctx(set()))                    # does not raise


def test_gate_fails_on_a_reused_stock_index():
    """Authoring a reused stock index -- exactly what the original coach-48V op did with 256 --
    fails the gate."""
    with pytest.raises(AssertionError, match="below the stock cell-table row count"):
        recipe._assert_authored_cells_fresh(_ctx({256}))
