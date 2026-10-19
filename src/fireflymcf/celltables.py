"""The live display-cell table (`DAT_00253D04`, stock GuiData+0x44BC8) and its parallel type table
(stock +0x4492C), wherever they are on THIS image.

`tablegrow` relocates both when a recipe grows them (`readout.allocate_fresh_cells`), leaving the
stock copy in place but unread. Every reader and writer resolves through here; a fixed
`gs + CELL_TABLE_OFF` indexes the dead copy (2026-09-25, the LEVEL page's design note §8; guarded by
tests/test_celltables.py).
"""
from __future__ import annotations

from . import memo


@memo.content_cached(copy=None)
def _resolve(data: bytes) -> tuple[int, int, int]:
    from . import readout            # local: readout imports statusbind imports us
    cell_base, type_base, rows = readout.resolve_cell_tables(data)
    return cell_base - 0x90000000, type_base - 0x90000000, rows


def cell_table_off(data: bytes) -> int:
    return _resolve(data)[0]


def type_table_off(data: bytes) -> int:
    return _resolve(data)[1]


def row_count(data: bytes) -> int:
    return _resolve(data)[2]
