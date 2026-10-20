"""The live display-cell/type tables, stock or relocated, and a guard that no module indexes the
fixed stock offset directly (the 2026-09-25 dead-copy defect)."""
import ast
from pathlib import Path

import pytest

from fireflymcf import celltables, tablegrow

STOCK = "firefly-13.6/mcf.bin"


def test_stock_offsets_are_the_known_constants():
    d = open(STOCK, "rb").read()
    assert celltables.cell_table_off(d) == 0x44BC8
    assert celltables.type_table_off(d) == 0x4492C
    assert celltables.row_count(d) == 665


@pytest.mark.parametrize("path", [STOCK])
def test_the_live_table_is_the_one_a_gui_code_base_literal_actually_loads(path):
    """Spec §8's other half of "resolve the live table from the descriptor": refuse to have
    resolved a table copy no GuiCode reader/writer literal actually points at. On stock and on
    v33 (tables relocated), `0x90000000 + celltables.cell_table_off/type_table_off` must each be
    the target of at least one GuiCode base literal (`tablegrow.find_base_refs`, the same
    reference-finder `allocate_fresh_cells` uses to find the surfaces it must repoint when it
    grows the table)."""
    d = open(path, "rb").read()
    cell_base = 0x90000000 + celltables.cell_table_off(d)
    type_base = 0x90000000 + celltables.type_table_off(d)
    assert tablegrow.find_base_refs(d, cell_base), (
        "no GuiCode literal loads %#x -- celltables resolved a cell-table address nothing reads"
        % cell_base)
    assert tablegrow.find_base_refs(d, type_base), (
        "no GuiCode literal loads %#x -- celltables resolved a type-table address nothing reads"
        % type_base)


# Guard fix-round-1 (2026-09-25 review): the original guard matched only the literal shape
# `gs + (statusbind.|status_map.)?(CELL_TABLE_OFF|VAR_TYPE_TABLE_OFF)`, which missed
# `readout.VAR_TYPE_TABLE_OFF` (the exact form the recipe_ops.py:4209/4256 defect took),
# `layout.VAR_TYPE_TABLE_136`/`VAR_DPTR_TABLE_136`, a bare constant with no `gs +`, reversed
# operand order, and raw `0x44BC8`/`0x4492C` literals (the sramreach.py defect this round found).
# An `ast`-based scan instead flags every real (non-comment, non-docstring, non-string-literal)
# appearance of the flagged identifiers or integer literals, anywhere in an expression, and is
# blind to source formatting.
_FLAGGED_NAMES = {"CELL_TABLE_OFF", "VAR_TYPE_TABLE_OFF", "VAR_DPTR_TABLE_136", "VAR_TYPE_TABLE_136"}
_FLAGGED_INTS = {0x44BC8, 0x4492C}

# (path, lineno): one-line reason a hit here is not the dead-copy defect. Only a constant's own
# definition/re-export, or a genuine relocation-detecting identity check, belongs here -- never
# an offset computation (`gs + ...`, or any arithmetic/indexing use).
_ALLOW = {
    ("src/fireflymcf/layout.py", 266): "VAR_DPTR_TABLE_136's own definition (the stock constant)",
    ("src/fireflymcf/layout.py", 267): "VAR_TYPE_TABLE_136's own definition (the stock constant)",
    ("src/fireflymcf/status_map.py", 37): "CELL_TABLE_OFF's own definition (the stock constant)",
    ("src/fireflymcf/readout.py", 38):
        "VAR_TYPE_TABLE_OFF re-exports layout.VAR_TYPE_TABLE_136 -- kept, per the task brief, "
        "because tests/docs cite it as the stock value; not used to index a table",
    ("src/fireflymcf/statusbind.py", 50):
        "CELL_TABLE_OFF re-exports status_map.CELL_TABLE_OFF -- kept, per the task brief, because "
        "tests/docs cite it as the stock value; not used to index a table",
    ("src/fireflymcf/guidata.py", 1893):
        "genuine identity check in _relocated_variable_tables: is the live GuiCode literal STILL "
        "the stock cell base (i.e. the table has NOT been grown) -- a comparison, not an offset "
        "used to read or write the table",
}


def _flagged_lines(path: Path) -> list:
    tree = ast.parse(path.read_text(), filename=str(path))
    hits = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in _FLAGGED_NAMES:
            hits.add(node.lineno)
        elif isinstance(node, ast.Attribute) and node.attr in _FLAGGED_NAMES:
            hits.add(node.lineno)
        elif (isinstance(node, ast.Constant) and isinstance(node.value, int)
              and not isinstance(node.value, bool) and node.value in _FLAGGED_INTS):
            hits.add(node.lineno)
    return sorted(hits)


def test_no_module_indexes_the_fixed_cell_table_offset():
    """Guard: any expression outside celltables.py naming `CELL_TABLE_OFF`, `VAR_TYPE_TABLE_OFF`,
    `VAR_DPTR_TABLE_136`, `VAR_TYPE_TABLE_136`, or the raw `0x44BC8`/`0x4492C` literals reads or
    writes the dead copy on a relocated image, unless it is one of the two allow-listed shapes
    (a constant's own definition, or `guidata._relocated_variable_tables`'s identity check)."""
    root = Path("src/fireflymcf")
    hits = []
    for p in sorted(root.glob("*.py")):
        if p.name == "celltables.py":
            continue
        for lineno in _flagged_lines(p):
            key = (str(p), lineno)
            if key in _ALLOW:
                continue
            hits.append(f"{p}:{lineno}")
    assert not hits, hits


def test_the_allow_list_stays_exact():
    """Every allow-listed line must still exist and still contain a flagged name/literal -- a
    guard against the allow-list quietly outliving the code it excuses (e.g. after a refactor
    moves or removes the identity check or a re-export)."""
    for path_str, lineno in _ALLOW:
        p = Path(path_str)
        assert lineno in _flagged_lines(p), (
            "%s:%d is allow-listed but no longer contains a flagged name/literal -- remove the "
            "stale allow-list entry" % (path_str, lineno))
