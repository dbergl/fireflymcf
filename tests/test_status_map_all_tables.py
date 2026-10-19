"""`status_map.object_consumers`/`all_table_cell_consumers` must scan ALL FOUR of GuiData's
object-pointer tables (`guidata.object_tables`), not just the 1422-entry table (index 1).

Cells 254/256/258/260 are referenced by objects living in the 3295-entry table (table index 3),
so a scan restricted to table 1 wrongly reports them as spare -- handing them out to a fresh
allocation, which then corrupts whatever drawn object in table 3 actually reads them. This is
the regression this module guards.
"""
from pathlib import Path

from fireflymcf import guidata, records, recipe_ops, status_map

ROOT = Path(__file__).resolve().parents[1]
STOCK = (ROOT / "firefly-13.6" / "mcf.bin").read_bytes()

REGRESSION_CELLS = {254, 256, 258, 260}


def _table_only_consumed(data: bytes, table_index: int) -> set:
    """Reproduce the OLD (pre-fix) single-table scan inline, to document the bug: which cell
    indices are consumed if only `guidata.object_tables(data)[table_index]` is scanned."""
    gs, _ = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[table_index]
    used: set = set()
    for off in table:
        try:
            recs = records.object_records(data, gs + off)
        except Exception:
            continue
        for r in recs:
            vs = getattr(r, "value_slot", None)
            if isinstance(vs, int) and 0 <= vs < status_map.CELL_TABLE_LEN:
                used.add(vs)
            for entry in (getattr(r, "colors", None) or []):
                if len(entry) > 2 and entry[1] in (6, 7) and isinstance(entry[2], int) \
                        and 0 <= entry[2] < status_map.CELL_TABLE_LEN:
                    used.add(entry[2])
    return used


def test_254_256_258_are_consumed():
    all_consumed = status_map.all_table_cell_consumers(STOCK)

    # The whole point: these cells are read by objects living OUTSIDE table 1, so a scan of
    # all four tables must find them.
    assert REGRESSION_CELLS <= all_consumed

    # Document the bug directly: the table-1-only scan (what `object_consumers` used to do)
    # does NOT contain them -- that is the mechanism this task fixes, not a coincidence.
    table1_only = _table_only_consumed(STOCK, 1)
    assert not (REGRESSION_CELLS & table1_only), (
        "table-1-only scan unexpectedly already covers the regression cells; "
        "the bug this test documents may have moved")

    # Scanning all four tables should account for nearly the whole cell table -- the only
    # legitimate gap is the undecoded gauge/capsule "empty offset" trailing field, which
    # `object_records`/`WRecord` does not expose at all (recipe_ops._gauge_level_indices
    # locates it by a byte-level scan `object_consumers` cannot see).
    assert len(all_consumed) > 600

    gauge_levels = recipe_ops._gauge_level_indices(STOCK)
    complement = set(range(status_map.CELL_TABLE_LEN)) - all_consumed
    assert complement - gauge_levels == set()
