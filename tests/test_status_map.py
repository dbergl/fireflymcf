"""Regression tests for status_map module: RV-C instance → on-screen indicator bridging.

Tests validate the backbone (bridged/indexed/cell/consumer) analysis and the
high-confidence idle_safe_targets set derivation. The module generates
docs/STATUS-INDICATOR-MAP.md, which pairs RV-C status instances with GuiCode's
FUN_00247BF8 bridge to enable lit status indicators on new cloned buttons.
"""
import struct

import pytest

from fireflymcf import guidata, status_map


def test_idle_safe_targets_high_confidence_set(mcf_136):
    """The high-confidence, floorplan-independent Tier-1 set.

    These instances are free (not commanded by any control), bridged in GuiCode,
    have an indexed colour cell, and are not displayed on any floorplan —
    safe for same-size edit across all 12 floorplans.
    """
    rows = status_map.backbone(mcf_136)
    views, visible_union = status_map.per_floorplan(mcf_136, rows)
    grids = status_map.grid_objects(mcf_136, rows)
    displayed_any = set()
    for v in views:
        displayed_any |= v.displayed

    idle_safe = status_map.idle_safe_targets(rows, grids, visible_union, displayed_any)
    instances = {r.instance for r in idle_safe}

    assert instances == {15, 19, 20, 27, 28, 29, 30, 31}, \
        f"idle_safe_targets returned {sorted(instances)}, expected {{15, 19, 20, 27, 28, 29, 30, 31}}"


def test_backbone_instance_17_unindexed_tier2_candidate(mcf_136):
    """Instance 17 (var 34): Tier-2 candidate (free + bridged but unindexed).

    This instance is not commanded, has a live bridge in GuiCode, but its colour
    cell is NOT indexed in the GuiData 0x44BC8 table — requires a relocator
    pointer-table insert, not a same-size edit.
    """
    rows = status_map.backbone(mcf_136)
    r17 = next((r for r in rows if r.instance == 17), None)

    assert r17 is not None, "instance 17 not found in backbone"
    assert not r17.commanded, "instance 17 should not be commanded"
    assert r17.bridged, "instance 17 should be bridged"
    assert r17.colour_idx == [], "instance 17 colour cell should be UNINDEXED (colour_idx empty)"
    assert r17.colour_cell is not None, "instance 17 should have a colour_cell address"


def test_backbone_instance_40_bridged_with_colour_index_144(mcf_136):
    """Instance 40 (var 82, Bath Fan selector): bridged and indexed in colour table.

    This is the Bath Fan / Starlink option toggle. It is commanded (by the OPTIONS
    checkbox), bridged in GuiCode, and has a colour index of 144 in the 0x44BC8 table.
    """
    rows = status_map.backbone(mcf_136)
    r40 = next((r for r in rows if r.instance == 40 and r.var == 82), None)

    assert r40 is not None, "instance 40 (var 82) not found in backbone"
    assert r40.bridged, "instance 40 should be bridged"
    assert r40.colour_idx == [144], f"instance 40 colour_idx should be [144], got {r40.colour_idx}"
    assert r40.colour_cell is not None, "instance 40 should have a colour_cell address"


def test_backbone_contains_dc_dimmer_status_3_variables_only(mcf_136):
    """The backbone contains only DC_DIMMER_STATUS_3 (0xF1) variables.

    All rows in the backbone are status indicators derived from the receive-filter
    variable table, specifically those with DGN low byte 0xF1.
    """
    rows = status_map.backbone(mcf_136)
    vt = __import__('fireflymcf.guidata', fromlist=['guidata']).variable_table(mcf_136)

    # Collect all DC_DIMMER_STATUS_3 variables
    DGN_DC_DIMMER_STATUS_3 = __import__('fireflymcf.guidata', fromlist=['guidata']).DGN_DC_DIMMER_STATUS_3
    expected_vars = {v.index for v in vt if v.dgn_low == DGN_DC_DIMMER_STATUS_3}

    # Backbone vars should match
    backbone_vars = {r.var for r in rows}
    assert backbone_vars == expected_vars, \
        f"backbone vars {backbone_vars} don't match expected {expected_vars}"


def test_grid_objects_returns_multi_instance_grids(mcf_136):
    """grid_objects returns objects that reference 3+ distinct status colour indices.

    These are light grids and status grids that display multiple instances.
    A consequence is that any instance consumed by a grid object is 'in use'
    and not an idle_safe candidate, regardless of floorplan visibility.
    """
    rows = status_map.backbone(mcf_136)
    grids = status_map.grid_objects(mcf_136, rows)

    assert isinstance(grids, set), "grid_objects should return a set"
    assert len(grids) > 0, "should find at least one grid object"
    # Grid objects should be object indices (integers)
    assert all(isinstance(obj, int) for obj in grids), "all grid objects should be integers"


def test_backbone_rows_sorted_by_instance_then_var(mcf_136):
    """The backbone is sorted by (instance, var), maintaining order for audit."""
    rows = status_map.backbone(mcf_136)

    instances = [r.instance for r in rows]
    vars_by_instance = [r.var for r in rows]

    # Instances should be non-decreasing
    assert instances == sorted(instances), "instances should be sorted"

    # Within each instance, vars should be sorted
    for inst in set(instances):
        inst_rows = [r for r in rows if r.instance == inst]
        inst_vars = [r.var for r in inst_rows]
        assert inst_vars == sorted(inst_vars), \
            f"vars for instance {inst} should be sorted, got {inst_vars}"


# ------------------------------------------------------------------------------------------
# `cell_table` must locate its base PER BUILD.
#
# The failure this guards against is silent, not loud: the value-slot table exists in every
# build, so using 13.6's `0x44BC8` on another one reads a real table at the wrong offset and
# every index resolves to a cell ~26 entries away -- still an SRAM address in the same region,
# still plausible. It produced a confident cross-build claim on 2026-08-23 that was only caught
# because a control cell traced back to a boolean config flag read with `cbnz`, and a DC voltage
# cannot be a boolean.
# ------------------------------------------------------------------------------------------

CORPUS_TABLE_BASES = {
    "firefly-13.6/mcf.bin": 0x44BC8,
    "firefly-11.23/mcf.bin": 0x44B60,
    "firefly-14.7 (Terrain)/mcf.bin": 0x42B74,
}


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


def test_value_slot_table_base_moves_between_builds():
    """The premise, asserted rather than assumed: all three bases differ."""
    from fireflymcf import guidata
    seen = {}
    for path, want in CORPUS_TABLE_BASES.items():
        base, _types = guidata.find_variable_tables(_read(path))
        assert base == want, "%s: base %#x, expected %#x" % (path, base, want)
        seen[base] = path
    assert len(seen) == len(CORPUS_TABLE_BASES), "bases collide: %s" % seen


def test_cell_table_uses_the_per_build_base():
    """Every entry must come from that build's own table, not 13.6's offset."""
    from fireflymcf import guidata
    for path, base in CORPUS_TABLE_BASES.items():
        data = _read(path)
        gs, _ = guidata.guidata_extent(data)
        tbl = status_map.cell_table(data)
        assert tbl, "%s: empty cell table" % path
        first = struct.unpack_from("<I", data, gs + base)[0]
        assert tbl[0] == (first if 0x20020000 <= first < 0x20040000 else 0), path
        # ...and the length is derived, not 13.6's constant carried across.
        if path != "firefly-13.6/mcf.bin":
            assert len(tbl) != status_map.CELL_TABLE_LEN, \
                "%s: length %d equals 13.6's constant -- base likely not per-build" % (
                    path, len(tbl))


def test_cell_table_is_unchanged_for_136():
    """The build every same-size edit targets must decode exactly as before the fix."""
    data = _read("firefly-13.6/mcf.bin")
    gs, _ = guidata.guidata_extent(data)
    tbl = status_map.cell_table(data)
    assert len(tbl) == status_map.CELL_TABLE_LEN == 665
    legacy = [struct.unpack_from("<I", data, gs + status_map.CELL_TABLE_OFF + i * 4)[0]
              for i in range(status_map.CELL_TABLE_LEN)]
    assert tbl == legacy
