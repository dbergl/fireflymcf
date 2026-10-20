import hashlib
import json
from pathlib import Path

import pytest

from fireflymcf import (guidata, insert, layout, readout, recipe, recipe_ops, records,
                              status_map, statusbind, tablegrow)

ROOT = Path(__file__).resolve().parents[1]
STOCK = (ROOT / "firefly-13.6" / "mcf.bin").read_bytes()


def _apply(params):
    proj = {"schema": 1, "base_sha256": hashlib.sha256(STOCK).hexdigest(),
            "build_name": "t",
            "ops": [{"id": "batt", "type": "add_voltage_battery", "params": params}]}
    return recipe.compile_project(recipe.Project.from_json(json.dumps(proj)), STOCK)


def _labels(data, gs, tab, idx):
    recs = records.object_records(data, gs + tab[idx])
    return [records.label_text(r.label) for r in recs if getattr(r, "label", None)]


def _object_bytes(data, obj):
    gs, tab = guidata.guidata_extent(data)[0], guidata.object_tables(data)[insert.OBJECT_TABLE_INDEX]
    recs = records.object_records(data, gs + tab[obj])
    start = gs + tab[obj]
    end = recs[-1].end
    return data[start:end]


# The stock chassis (object 440) and house/coach (object 443, the clone's donor) battery
# cells, by their
# pinned field map: fillA mode-7 colour index, wt-8 value_slot index.
STOCK_COLOR_202 = 0x20039D5C
STOCK_COLOR_206 = 0x2003A664
STOCK_VALUE_205 = 0x2003A272
STOCK_VALUE_209 = 0x2003A594


def _clone_cells(data, idx):
    """(color_addr, level_addr, value_addr) the clone at object `idx` actually reads --
    decoded straight from its own records, not trusted from a handle (`CompileResult` has
    no `.handles`; `recipe.py`'s `ctx.handles` is an internal, in-pipeline mechanism)."""
    gs, tab = guidata.guidata_extent(data)[0], guidata.object_tables(data)[insert.OBJECT_TABLE_INDEX]
    recs = records.object_records(data, gs + tab[idx])
    fillrec = next(r for r in recs if r.wtype == 33 and r.colors
                  and r.colors[0][:2] == ("fillA", 7) and r.colors[0][2] != 3)
    valuerec = next(r for r in recs if r.wtype == 8 and r.value_slot is not None)
    level_off = fillrec.offset + fillrec.size - 2
    level_idx = int.from_bytes(data[level_off:level_off + 2], "little")
    # The clone's cells are freshly-grown indices in the RELOCATED table; resolve them at the
    # table's CURRENT base (the GuiCode literal), not status_map.cell_table's structural scan --
    # which still finds the pre-grow 665-entry copy and IndexErrors on index >= 665.
    cell_base = tablegrow.read_guicode_word(data, readout._CELL_BASE_REF)
    cell_off = gs + (cell_base - 0x90000000)

    def _addr(index):
        return int.from_bytes(data[cell_off + index * 4:cell_off + index * 4 + 4], "little")

    return _addr(fillrec.colors[0][2]), _addr(level_idx)   # (color, level) -- the two fresh cells


def test_clone_uses_fresh_cells_not_stock():
    """The clone's CUSTOM colour and level cells are fresh (the value widget instead reads the live
    stock cell 209, see test_clone_value_reads_the_live_stock_cell_209)."""
    before = len(guidata.object_tables(STOCK)[insert.OBJECT_TABLE_INDEX])
    res = _apply({"donor": 443, "parent": 902, "x": 556, "y": 334})
    assert res.ok, res.errors
    color_addr, level_addr = _clone_cells(res.data, before)
    for label, addr in (("color", color_addr), ("level", level_addr)):
        assert addr % 4 == 0, "%s cell %#x is not 4-aligned" % (label, addr)
    addrs = {color_addr, level_addr}
    assert len(addrs) == 2                      # two distinct fresh cells
    # The stock chassis/house cells are untouched -- neither fresh address lands on one.
    stock_cells = {STOCK_COLOR_202, STOCK_COLOR_206, STOCK_VALUE_205, STOCK_VALUE_209}
    assert addrs.isdisjoint(stock_cells)


def test_clone_value_reads_the_live_stock_cell_209():
    """The value widget keeps the donor's value_slot 209 -- the live, receive-filter-fed decoded
    coach-48V cell the ELECTRICAL page reads -- so the number repaints live (a fresh cell is off
    the panel's repaint watch-list and shows a stale number; coach test 2026-09-12)."""
    before = len(guidata.object_tables(STOCK)[insert.OBJECT_TABLE_INDEX])
    res = _apply({"donor": 443, "parent": 902, "x": 556, "y": 334})
    assert res.ok, res.errors
    gs = guidata.guidata_extent(res.data)[0]
    tab = guidata.object_tables(res.data)[insert.OBJECT_TABLE_INDEX]
    recs = records.object_records(res.data, gs + tab[before])
    valuerec = next(r for r in recs if r.wtype == 8 and r.value_slot is not None)
    assert valuerec.value_slot == 209


def test_clone_cells_are_freshly_grown_indices():
    """The coach-48V background-bar fix: the clone's three cells must be freshly GROWN +0x44BC8
    indices (>= the stock row count), never reused stock indices that an object in another GuiData
    table also reads. (`res.ok` already proves the compile's cell-aliasing gate accepted them; this
    reads the clone's own records to pin the indices explicitly.)"""
    res = _apply({"donor": 443, "parent": 902, "x": 556, "y": 334})
    assert res.ok, res.errors

    stock_len = tablegrow.read_cmpw_bound(STOCK, readout._CELL_BOUND_SITE) + 1
    before = len(guidata.object_tables(STOCK)[insert.OBJECT_TABLE_INDEX])
    gs = guidata.guidata_extent(res.data)[0]
    tab = guidata.object_tables(res.data)[insert.OBJECT_TABLE_INDEX]
    recs = records.object_records(res.data, gs + tab[before])
    fillrec = next(r for r in recs if r.wtype == 33 and r.colors
                   and r.colors[0][:2] == ("fillA", 7) and r.colors[0][2] != 3)
    valuerec = next(r for r in recs if r.wtype == 8 and r.value_slot is not None)
    level_off = fillrec.offset + fillrec.size - 2
    level_idx = int.from_bytes(res.data[level_off:level_off + 2], "little")

    idxs = [fillrec.colors[0][2], level_idx]     # the two CUSTOM cells: colour, level
    assert all(i >= stock_len for i in idxs), \
        "clone colour/level indices %r are not all freshly grown (>= stock row count %d)" % (
            idxs, stock_len)
    assert len(set(idxs)) == 2
    # the value widget reads the live stock cell 209, not a fresh one
    assert valuerec.value_slot == 209

    # the grown table's bound was bumped so the panel accepts the fresh indices
    assert tablegrow.read_cmpw_bound(res.data, readout._CELL_BOUND_SITE) + 1 > max(idxs)


def test_chassis_object_440_is_unchanged():
    """Isolating the clone (object 443's donor copy) must never touch the ORIGINAL chassis
    battery object 440 -- its own colour/level/value cells (202/203/205) stay exactly as
    stock wrote them."""
    res = _apply({"donor": 443, "parent": 902, "x": 556, "y": 334})
    assert res.ok, res.errors
    assert _object_bytes(res.data, 440) == _object_bytes(STOCK, 440)


def test_gauge_level_indices_finds_known_pairs_and_excludes_id_tags():
    """Regression test for the `_gauge_level_indices` fix: an earlier hand-rolled formula
    (colour width + present-geom widths) mislocated the trailing field on 276 of the 13.6
    corpus's 840 wt-33 records, including object 803 (off by one byte) -- it now uses the
    decoder's own record end (`_trailing_gap`), which is correct for every record shape
    `records.decode_record` handles."""
    used = recipe_ops._gauge_level_indices(STOCK)
    # chassis (440) and house/donor (443) level indices, the pair this op must protect
    assert {203, 207}.issubset(used)
    # object 803's mode-7 record: the hand-rolled formula put its tail one byte short and
    # missed it entirely; the decoder-end fix finds it at the corrected offset (value 279)
    assert 279 in used
    # objects 721-727's mode-3 (not mode-7) capsule trailing values are a layout/ID pairing
    # with their own sibling wt-5 container's `value_slot` (already `spare_cell_indices`-
    # tracked), not an independent +0x44BC8 index -- see this function's own docstring
    assert not ({254, 256, 258, 260} & used)


def test_isolate_cell_never_returns_a_misaligned_address(monkeypatch):
    """Regression test for the `_isolate_cell` alignment bug: an earlier version fell back to
    a free run's own (not necessarily 4-aligned) `start` when the 4-aligned address inside
    that run didn't leave room for `width` bytes. Stub a first run too tight to hold a
    4-aligned width-4 cell (0x20038001, length 5 -- the 4-aligned address inside it,
    0x20038004, leaves only 1 byte) and a second, roomy run; the fix must skip the first run
    rather than returning its misaligned `start`."""
    monkeypatch.setattr(recipe_ops.sramreach, "free_runs",
                        lambda data, min_bytes=8: [(0x20038001, 5), (0x20038100, 16)])
    ctx = recipe.Ctx(stock=STOCK, edits=[], insertions=[], handles={})
    idx = statusbind.spare_cell_indices(STOCK)[0]
    _out, addr = recipe_ops._isolate_cell(STOCK, ctx, idx, 4, None, "test")
    assert addr % 4 == 0
    assert addr == 0x20038100                  # the roomy second run, not the tight first one


def test_clone_appends_one_object_attached_to_902():
    before = len(guidata.object_tables(STOCK)[insert.OBJECT_TABLE_INDEX])
    res = _apply({"donor": 443, "parent": 902, "x": 556, "y": 334})
    assert res.ok, res.errors
    after = len(guidata.object_tables(res.data)[insert.OBJECT_TABLE_INDEX])
    assert after == before + 1                      # exactly one new object
    # boundary oracle + coverage.analyse(deep=True) already ran inside compile_project
    # (via _run_structural_gates) -- a derail would have raised.

    # The new object (at the top index, `before`) is a verbatim clone of donor 443:
    # same label content as the donor carries on its own records (donor 443's own
    # records carry the 'V' unit caption directly; 'House' lives on 443's *parent*
    # objects 901/903, not on 443 itself -- confirmed via
    # `cli labels firefly-13.6/mcf.bin --containing House` and
    # `insert.object_child_graph`, so this asserts against 443's real content rather
    # than the donor's neighbourhood).
    gs, tab = guidata.guidata_extent(STOCK)[0], guidata.object_tables(STOCK)[insert.OBJECT_TABLE_INDEX]
    donor_labels = _labels(STOCK, gs, tab, 443)
    gs2, tab2 = guidata.guidata_extent(res.data)[0], guidata.object_tables(res.data)[insert.OBJECT_TABLE_INDEX]
    clone_labels = _labels(res.data, gs2, tab2, before)
    assert clone_labels == donor_labels == ["V"]


def _battery_bodies(data, root=4, slot_values=None):
    """Every battery's OUTER SILHOUETTE box (~90px wide) on the given screen, in the
    lower-right quadrant (x0 >= 400) -- one per battery graphic. `layout.Screen.walk`
    (not `render.screen_placements`, which returns already-composited `Placement`/
    `Prim` objects with no per-record `box`/`wtype`/`obj_index`) is the primitive that
    actually carries per-record placement -- `Placed.x0/y0/x1/y1`, `.wtype`,
    `.obj_index` -- so this walks the page directly.

    A battery graphic draws TWO overlapping wt-33 fillA records at this width: the
    silhouette (mode-0, inheriting colour index 3 from an earlier terminal record --
    `Placed.colors` carries the resolved EFFECTIVE colour, `layout._apply_colors`) in
    NORMAL y-order (y0 < y1), and the runtime level FILL (mode-7, the driven colour
    cell -- index 202/206 on stock, a fresh index on the clone) drawn bottom-up, so
    its box is Y-INVERTED (y0 > y1) -- `y0 < y1` is what picks the silhouette, not
    the fill, so this returns exactly one box per battery regardless of which colour
    cell is driving it."""
    placed = layout.Screen(data).walk(root, slot_values=slot_values or {6: 1, 37: 2})
    return [p for p in placed
           if p.wtype == 33 and p.x0 is not None and p.x0 >= 400
           and p.colors and p.colors[0][0] == "fillA" and p.y0 < p.y1
           and 50 < (p.x1 - p.x0) < 150]


def test_two_batteries_centered_in_lower_right():
    """Task 5: the chassis (object 440) moves left, the clone/'House' battery sits to its
    right, both evenly spaced and centred in HOME's lower-right quadrant -- and the clone
    gets the 'House' text donor 443 does not itself carry (it lives on 443's parent objects
    901/903, confirmed by `test_clone_appends_one_object_attached_to_902`'s own donor-label
    check)."""
    res = _apply({"donor": 443, "parent": 902, "x": 556, "y": 334})
    assert res.ok, res.errors

    bodies = _battery_bodies(res.data)
    assert len(bodies) == 2, "expected exactly 2 battery bodies, found %d" % len(bodies)
    centers = sorted((p.x0 + p.x1) / 2 for p in bodies)
    assert min(centers) < 600 < max(centers), \
        "battery centres %r are not straddling the quadrant centre" % centers
    # The pair reproduces the AGS variant's (object 903's) own two-battery spacing -- ~176px
    # apart, centre 644 -- which is as far centred as the wide 'Chassis' label lets the pair
    # sit inside object 902's box (x 435-790). 644, not 600: the label reaches ~120px left of
    # its own body, so the left battery's centre cannot drop below ~556 without the label
    # leaving the panel. Assert the pair centre sits within object 902's own box centre (612).
    pair_centre = (min(centers) + max(centers)) / 2
    assert abs(pair_centre - 612) < 40, \
        "pair centre %r is not within 40px of object 902's box centre (612)" % (pair_centre,)
    # both batteries drawn on the same row -- not wrapped onto a different line
    rows = {p.y0 for p in bodies}
    assert len(rows) == 1, "battery bodies are not on the same row: %r" % bodies

    # House (the clone) is the LEFT-hand battery, the chassis (object 440) the right-hand one
    # (owner's request: HOME matches the AES compact / AGS layout).
    left, right = sorted(bodies, key=lambda p: p.x0)
    assert right.obj_index == 440, "the chassis (object 440) is not the right-hand battery"
    assert left.obj_index != 440, "the House/clone battery is not the left-hand battery"

    # a 'House' label now exists (object 443 carries none of its own) and sits by the LEFT battery
    placed = layout.Screen(res.data).walk(4, slot_values={6: 1, 37: 2})
    house = next(p for p in placed if p.wtype == 11 and (p.label or "").strip() == "House")
    assert house.x1 <= left.x0 + 5, "'House' label is not positioned by its own (left) battery"
    assert abs(house.y0 - left.y0) < 40, "'House' label is not on the battery's own row"

    # the chassis keeps its own name label too, by the RIGHT battery
    chassis_label = next(p for p in placed if p.wtype == 11
                         and (p.label or "").strip() == "Chassis")
    assert chassis_label.x1 <= right.x0 + 5

    # REGRESSION (owner-caught on a render, 2026-09-11): neither name label may overlap the
    # NEIGHBOURING battery's body. A name label reaches ~122px left of its own body, so a
    # too-tight spacing lets a label land on the other battery's body even when the two BODIES do
    # not overlap. Each label must clear the OTHER battery's silhouette in x.
    def _x_overlap(a, b):
        return not (a.x1 <= b.x0 or a.x0 >= b.x1)
    assert not _x_overlap(house, right), \
        "'House' label %r overlaps the chassis body %r in x" % (
            (house.x0, house.x1), (right.x0, right.x1))
    assert not _x_overlap(chassis_label, left), \
        "'Chassis' label %r overlaps the House body %r in x" % (
            (chassis_label.x0, chassis_label.x1), (left.x0, left.x1))

    # rigid-unit check: the clone's own wt-8 VALUE widget (the runtime volts number) moved
    # together with its body -- not left behind at the clone's pre-reposition flow position --
    # so the whole cloned subtree, not just its outer silhouette, tracked the move. The clone is
    # the LEFT battery now.
    value = next(p for p in placed if p.obj_index == left.obj_index and p.wtype == 8)
    assert left.x0 <= value.x0 <= left.x1, \
        "clone's value widget %r is not inside its own battery box %r" % (value, left)
    assert abs(value.y0 - left.y0) < 40, \
        "clone's value widget is not on the same row as its own battery body"


def test_reposition_label_refuses_a_multi_part_label():
    """Task-5-review Important finding: `_reposition_label`'s text-retext slice must validate
    the label's on-disk framing before writing, the same guard `labels.grow_label` (this
    project's own precedent for exactly this edit) makes before its splice -- otherwise a
    donor whose label is NOT the single NUL-terminated length-prefixed shape (e.g. a multi-part
    R37 `cnt` > 1 label, several length-prefixed strings concatenated into one `rec.label` --
    see `records.py`'s final decode block) would be silently mis-written instead of refused."""
    gs, tab = guidata.guidata_extent(STOCK)[0], guidata.object_tables(STOCK)[insert.OBJECT_TABLE_INDEX]
    recs = records.object_records(STOCK, gs + tab[902])
    chassis_rec = next(r for r in recs if r.wtype == 11 and (r.label or "").strip() == "Chassis")

    # sanity: the real donor satisfies the guard and retexts cleanly
    recipe_ops._reposition_label(STOCK, chassis_rec, text="House", op_id="t")

    # corrupt the length byte that should precede the label text -- simulates a label whose
    # on-disk framing is not the single length-prefixed-string shape (e.g. a multi-part label,
    # where this byte holds only the FIRST part's own, shorter length)
    corrupted = bytearray(STOCK)
    corrupted[chassis_rec.label_offset - 1] = 1
    with pytest.raises(recipe.RecipeError) as exc:
        recipe_ops._reposition_label(bytes(corrupted), chassis_rec, text="House", op_id="t")
    assert exc.value.code == "BAD_LABEL_FRAMING"

    # corrupt the terminating NUL instead -- the other half of the same guard
    corrupted2 = bytearray(STOCK)
    corrupted2[chassis_rec.label_offset + len(chassis_rec.label)] = 0x41
    with pytest.raises(recipe.RecipeError) as exc2:
        recipe_ops._reposition_label(bytes(corrupted2), chassis_rec, text="House", op_id="t")
    assert exc2.value.code == "BAD_LABEL_FRAMING"
