"""The live display-cell/type tables, stock or relocated, and a guard that no module indexes the
fixed stock offset directly (the 2026-09-25 dead-copy defect)."""
import ast
import json
import re
from pathlib import Path

import pytest

from fireflymcf import celltables, guidata, recipe, status_map, tablegrow

STOCK = "firefly-13.6/mcf.bin"
V33 = "builds/mcf-13.6-BattHeatGlyph-v33.bin"
V33_PROJECT = "builds/mcf-13.6-batt-heat-glyph.ffproj.json"


def test_stock_offsets_are_the_known_constants():
    d = open(STOCK, "rb").read()
    assert celltables.cell_table_off(d) == 0x44BC8
    assert celltables.type_table_off(d) == 0x4492C
    assert celltables.row_count(d) == 665


def test_v33_resolves_the_relocated_table():
    d = open(V33, "rb").read()
    off = celltables.cell_table_off(d)
    assert off != 0x44BC8, "v33 relocated the table; resolving to the stock offset is the defect"
    assert off == guidata.find_variable_tables(d)[0]
    # `status_map.cell_table` already follows the relocation for its BASE the same way
    # `celltables` does (both call `guidata.find_variable_tables`) -- but its LENGTH is a
    # heuristic best-effort walk that trims trailing zero words (module docstring), and v33's
    # table has been grown TWICE (`add_voltage_battery` then `add_aes_batt_heat_status`); each
    # `tablegrow.grow_indexed_tables` call pads up to the next Thumb `cmp.w #imm` encodable bound
    # (tablegrow.py:432-507), leaving legitimate but currently-unwritten zero rows INSIDE the true
    # table (verified: indices 667/668/670/671/672 are zero, 665/666/669 are the real grown
    # cells). `status_map.cell_table`'s trailing-zero trim cannot tell those apart from padding
    # past the table's real end, so it under-counts (670) against the structurally-proven bound
    # `celltables.row_count` reads from the GuiCode `cmp.w` gate (673) -- a lower bound, not
    # equality.
    assert celltables.row_count(d) >= len(status_map.cell_table(d))
    assert celltables.row_count(d) == 673


@pytest.mark.parametrize("path", [STOCK, V33])
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


def _v33_recipe_plus(*extra_ops):
    proj = json.load(open(V33_PROJECT))
    proj["build_name"] = "celltables-test"
    proj["ops"].extend(extra_ops)
    return proj


def test_add_register_readout_tier3_on_a_relocated_table_binds_the_live_cell():
    """The defect (spec Sec8): `add_register_readout`'s tier-3 allocation
    (`statusbind.index_cell` + `readout._ensure_type`) used to write the FIXED stock table
    offsets. v33's cell/type tables are already relocated (two earlier ops in its own recipe --
    `coach48v`/`add_voltage_battery` and `status`/`add_aes_batt_heat_status` -- grow them via
    `readout.allocate_fresh_cells`), so a third op landing on tier 3 wrote a cell/type update to a
    dead copy nothing reads.

    Reproduced 2026-09-25 (pre-fix): appending a register readout with `width_bytes=2` to v33's
    own recipe (exhausting the halfword-safe and odd-byte spares so `allocate_display_cell` falls
    through to tier 3) raised `AssertionError` inside `AddRegisterReadout.verify`
    (`recipe_ops.py`'s read of the dead type-table copy did not match what `_ensure_type` had
    actually written, because `statusbind.set_variable_type`'s WRITE already resolved the type
    table structurally while the stale READ constant did not -- the two disagreed only because
    the table had moved). Fixed: both sides now resolve through `celltables`.
    """
    proj = _v33_recipe_plus(
        {"id": "probe-var", "type": "append_filter", "enabled": True,
         "params": {"dgn": 0x1FEA0, "mode": 1, "payload_byte": 1}},
        {"id": "probe-ro", "type": "add_register_readout", "enabled": True,
         "params": {"register": {"ref": "probe-var"}, "width_bytes": 2,
                    "page": 367, "x": 100, "y": 100}})
    stock = open(STOCK, "rb").read()
    res = recipe.compile_project(recipe.Project.from_json(json.dumps(proj)), stock)
    assert res.ok, res.errors

    tier3 = [m for m in res.manifest if "add_register_readout" in m and "tier 3" in m]
    assert tier3, ("expected the probe readout to land on tier 3 (every cheaper tier consumed "
                   "by v33's own recipe); it did not, so this no longer exercises the relocated-"
                   "table defect: %r" % res.manifest)
    m = re.search(r"cell (\d+) @ (0x[0-9a-f]+)", tier3[0])
    slot, addr = int(m.group(1)), int(m.group(2), 16)

    # The live +0x44BC8 table entry the widget's value_slot resolves through must now be the
    # SAME address the appended per-tick routine writes -- not a stale/dead-copy value.
    cells = status_map.cell_table(res.data)
    assert cells[slot] == addr, (
        "the live cell table still names %#x at slot %d; the tier-3 write landed on the dead "
        "stock-offset copy instead of the relocated table" % (cells[slot], slot))
