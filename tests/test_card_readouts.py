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

import pytest

from fireflymcf import layout, options, readout, readoutfill, records

CARDS_RECIPE = REPO / "builds/mcf-13.6-electrical-cards.ffproj.json"
V47 = REPO / "builds/mcf-13.6-BattOutline-v47.bin"
INV_COLUMN = (676, 76, 786, 216)          # right of the inverter controls, inside the card
CAPTIONS = ["AC In", "In Amps", "Out Amps", "Batt Amps"]


def _filter(op_id, payload_byte, instance):
    return {"id": op_id, "type": "append_filter", "enabled": True,
            "params": {"dgn": 0x1FFD7, "mode": 2, "payload_byte": payload_byte,
                       "instance": instance, "compare_byte": 0}}


def _readout(op_id, ref, **arith):
    return {"id": op_id, "type": "add_register_readout", "enabled": True,
            "params": dict({"register": {"ref": ref}, "draw": False}, **arith)}


READOUT_OPS = [
    _filter("acin-v-f", 1, 0x01), _filter("acin-a-f", 3, 0x01), _filter("acout-a-f", 3, 0x41),
    _readout("acin-v", "acin-v-f", add=1, shift=1),
    _readout("acin-a", "acin-a-f", sub=32000, shift=1),
    _readout("acout-a", "acout-a-f", sub=32000, shift=1),
    {"id": "invrows", "type": "add_card_readouts", "enabled": True, "params": {"rows": [
        {"caption": "AC In", "value": {"ref": "acin-v"}, "format": {"width": 5, "places": 1},
         "unit": "V"},
        {"caption": "In Amps", "value": {"ref": "acin-a"}, "format": {"width": 4, "places": 1},
         "unit": "A"},
        {"caption": "Out Amps", "value": {"ref": "acout-a"}, "format": {"width": 4, "places": 1},
         "unit": "A"},
        {"caption": "Batt Amps", "value": {"slot": 232}, "format": {"width": 4, "places": 0},
         "unit": "A"}]}},
]


def _v49_project():
    p = json.loads(CARDS_RECIPE.read_text())
    p["build_name"] = "card-readouts-test"
    p["ops"] += json.loads(json.dumps(READOUT_OPS))
    return p


@pytest.fixture(scope="module")
def v49():
    res = recipe.compile_project(recipe.Project.from_json(json.dumps(_v49_project())),
                                 STOCK.read_bytes())
    assert res.ok, res.errors
    return res.data


def _gs_tab(data):
    return guidata.guidata_extent(data)[0], guidata.object_tables(data)[1]


def _readouts_object(data):
    gs, tab = _gs_tab(data)
    frame = records.object_records(data, gs + tab[829])
    return frame[8].child_index


def _walk(data, root=5):
    from tests.test_card_layout import COACH
    sv = dict(options.slot_values(data, COACH))
    sv.update(options.derived_slots(data, sv))
    sv[113] = 1
    return layout.Screen(data).walk(root, slot_values=sv)


def test_draw_false_cells_are_distinct_and_still_map_to_their_routines():
    """Three draw:false readouts: each gets its own display slot, and the cell table still maps
    every slot to the address its own routine writes (tier 3 once repointed an index an earlier
    draw:false readout owned -- nothing named it yet)."""
    from fireflymcf import status_map
    p = _v49_project()
    p["ops"] = p["ops"][:-1]
    res = recipe.compile_project(recipe.Project.from_json(json.dumps(p)), STOCK.read_bytes())
    assert res.ok, res.errors
    lines = [m for m in res.manifest if "no widget" in m]
    assert len(lines) == 3
    import re
    got = [re.search(r"cell (\d+) @ (0x[0-9a-f]+)", m).groups() for m in lines]
    slots = [int(s) for s, _a in got]
    assert len(set(slots)) == 3, got
    cells = status_map.cell_table(res.data)
    assert [hex(cells[s]) for s in slots] == [a for _s, a in got]


def test_rows_inside_the_inverter_card_right_of_the_controls(v49):
    obj = _readouts_object(v49)
    mine = [p for p in _walk(v49) if p.obj_index == obj and p.wtype != 9]
    assert len(mine) == 12                     # 4 x (caption, readout, unit)
    x0, y0, x1, y1 = INV_COLUMN
    for p in mine:
        assert x0 <= p.x0 <= p.x1 <= x1 and y0 <= p.y0 <= p.y1 <= y1, (p.wtype, p.label,
                                                                       (p.x0, p.y0, p.x1, p.y1))


def test_captions_and_units_read_back(v49):
    gs, tab = _gs_tab(v49)
    recs = records.object_records(v49, gs + tab[_readouts_object(v49)])
    labels = [r.label for r in recs if r.wtype == 11]
    assert labels[0::2] == CAPTIONS and labels[1::2] == ["V", "A", "A", "A"]


def test_values_name_the_routine_cells_and_slot_232(v49):
    gs, tab = _gs_tab(v49)
    recs = records.object_records(v49, gs + tab[_readouts_object(v49)])
    slots = [r.value_slot for r in recs if r.wtype == 8]
    assert len(slots) == 4 and slots[3] == 232 and len(set(slots)) == 4
    assert all(r.font == 12 for r in recs if r.wtype == 8)


def test_no_stock_directive_changes(v49):
    v47 = V47.read_bytes()
    obj = _readouts_object(v49)
    for root in (5, 39):
        old = {(e[1], e[2]): e[3] for e in readout.directive_in_force(v47, root)
               if e[0] == "readout"}
        new = {(e[1], e[2]): e[3] for e in readout.directive_in_force(v49, root)
               if e[0] == "readout" and e[1] != obj}
        changed = sorted(k for k in old if k in new and old[k] != new[k])
        assert not changed, (root, changed)


def test_v48_invariants_hold(v49):
    from tests import test_card_layout as T
    v47 = V47.read_bytes()
    assert T._tail_state(v49) == T._tail_state(v47)
    obj = _readouts_object(v49)
    gs, tab = _gs_tab(v49)
    band = records.object_records(v49, gs + tab[1427])[6].child_index
    skip = ({829, 1427, obj} | set(range(1433, 1437)) | set(range(154, 162))
            | set(range(band, band + 8)))

    def key(pl):
        return [(p.obj_index, p.wtype, p.label, p.font, p.align, p.h_align, p.colors)
                for p in pl if p.obj_index not in skip]
    assert key(_walk(v49)) == key(_walk(v47))


def test_no_fill_wipes_a_neighbour(v49):
    gs, tab = _gs_tab(v49)
    assert readoutfill.conflicts(v49, gs, tab, [_readouts_object(v49)]) == []


def test_refuses_without_card_layout():
    p = json.loads((REPO / "builds/mcf-13.6-batt-outline.ffproj.json").read_text())
    p["ops"] += json.loads(json.dumps(READOUT_OPS))
    res = recipe.compile_project(recipe.Project.from_json(json.dumps(p)), STOCK.read_bytes())
    assert not res.ok
    assert any("WRONG_SHAPE" in str(e) for e in res.errors), res.errors


def test_every_caption_and_unit_character_is_in_its_font(v49):
    """Font 5 lacks lowercase: the first v49 render drew the captions as fallback boxes."""
    from fireflymcf import glyphs
    fonts = {f.slot: f for f in glyphs.fonts(v49)}
    gs, tab = _gs_tab(v49)
    for r in records.object_records(v49, gs + tab[_readouts_object(v49)]):
        if r.wtype == 11:
            f = fonts[r.font]
            assert all(f.first <= ord(c) <= f.last for c in r.label), (r.label, r.font)


def test_appended_filters_separate_input_from_output(v49):
    """The receive filter is blind to the sender (PC.46); only byte 0's instance compare keeps
    the input-line frames (0x01) from overwriting the output-line values (0x41)."""
    vt = guidata.variable_table(v49)
    mine = [v for v in vt if v.dgn_low == 0xFFD7 and v.raw[2] >> 4 == 2 and v.raw[4] != 0xFF]
    got = sorted((v.raw[3] & 7, v.raw[4], v.raw[5]) for v in mine)
    assert got == [(1, 0x01, 0x00), (3, 0x01, 0x00), (3, 0x41, 0x00)], got


def test_no_fill_touches_any_label_in_the_object(v49):
    """An enrolled readout repaints on its own, so its fill must clear labels drawn AFTER it too
    (readoutfill.conflicts checks only earlier ones): at pitch 33 each value's fill shaved the
    top ink row of the next row's caption on every repaint (v49 final review)."""
    gs, tab = _gs_tab(v49)
    recs = records.object_records(v49, gs + tab[_readouts_object(v49)])
    field = None
    labels = [readoutfill._ink_rows(v49, r, readoutfill._box(r)) for r in recs
              if r.wtype == 11 and readoutfill._box(r) is not None]
    for r in recs:
        if r.wtype == 9 and r.fmt is not None:
            field = r.fmt[2]
        elif r.wtype == 8:
            fill = readoutfill.worst_fill(v49, r, field)
            hits = [b for b in labels if readoutfill._hit(fill, b)]
            assert not hits, (r.value_slot, fill, hits)


# --- v50: inverter DC current replaces Batt Amps ----------------------------------------------

V50_RECIPE = REPO / "builds/mcf-13.6-inverter-dc.ffproj.json"


@pytest.fixture(scope="module")
def v50():
    res = recipe.compile_project(recipe.Project.from_json(V50_RECIPE.read_text()),
                                 STOCK.read_bytes())
    assert res.ok, res.errors
    return res.data


def test_v50_fourth_row_is_the_pd_inverter_dc_current(v50):
    """Slot 652 is the PD Inverter page's DC Current cell: FUN_00247BF8 0x248064-0x248082 turns
    1FEE8 bytes 3-4 into whole amps, negated (charging positive), every tick while slot 113 == 1."""
    gs, tab = _gs_tab(v50)
    recs = records.object_records(v50, gs + tab[_readouts_object(v50)])
    labels = [r.label for r in recs if r.wtype == 11]
    assert labels[0::2] == ["AC In", "In Amps", "Out Amps", "Inv DC"]
    assert [r.value_slot for r in recs if r.wtype == 8][3] == 652
    fmts = [r.fmt for r in recs if r.wtype == 9]
    assert fmts[3][2:] == (4, 0)                 # width 4 fits "-40"


def test_v50_keeps_the_invariants(v50):
    from tests import test_card_layout as T
    v47 = V47.read_bytes()
    assert T._tail_state(v50) == T._tail_state(v47)
    gs, tab = _gs_tab(v50)
    assert readoutfill.conflicts(v50, gs, tab, [_readouts_object(v50)]) == []


def test_every_caption_fits_its_box():
    """A label clips to its box (GUICODE Finding 69): 'AC Out Current' is 100 px in font 10."""
    from fireflymcf import cardreadouts, metrics
    data = V47.read_bytes()
    rows = [cardreadouts.Row(c, 232, 4, 1, "A")
            for c in ("AC In", "AC In Current", "AC Out Current", "Inv DC")]
    obj = cardreadouts.build_object(data, rows)
    for r in records.object_records(obj, 0):
        if r.wtype == 11:
            assert metrics.text_width(data, r.font, r.label) <= r.geom[2], (r.label, r.geom[2])


def test_v51_captions():
    res = recipe.compile_project(recipe.Project.from_json(
        (REPO / "builds/mcf-13.6-inverter-captions.ffproj.json").read_text()), STOCK.read_bytes())
    assert res.ok, res.errors
    gs, tab = _gs_tab(res.data)
    recs = records.object_records(res.data, gs + tab[_readouts_object(res.data)])
    labels = [r.label for r in recs if r.wtype == 11]
    assert labels[0::2] == ["AC In", "AC In Current", "AC Out Current", "Inv DC"]
