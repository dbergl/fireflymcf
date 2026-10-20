"""v25 (2026-09-11/12): the coach-48V battery live-repaint + ELECTRICAL 48V rescale.

Covers the two coach-observed defects the v25 design fixes:

1. `live_repaint` retargets the clone's enrolled wt5 CONTAINER record (on the parent object) to
   watch the live coach cell 209 instead of the chassis cell 205 it inherited from the template.
   The panel's per-widget dispatch then repaints the WHOLE House child -- number AND fill -- on
   every coach-voltage change, exactly as the stock chassis rides object 902 rec2 (vslot 205,
   child 440). This is why v24 stayed frozen when only cell 209 was injected: the House container
   watched 205, which the injection never touched.
2. `rescale_donor` repoints the ELECTRICAL coach battery (donor object 443) fill colour (206) and
   level (207) onto the clone's fresh 48V-scaled cells, so it stops showing always-100%-green (its
   stock cells are driven by `FUN_00247BF8` on a 12V scale a ~53V pack saturates). Donor 443's own
   enrolled containers (901/903/829/830) already watch 209, so once the fill reads the 48V cell the
   ELECTRICAL fill goes live and correct with no further enrol.
"""
import hashlib
import json
from pathlib import Path

from fireflymcf import recipe, guidata, records

ROOT = Path(__file__).resolve().parents[1]
STOCK = (ROOT / "firefly-13.6" / "mcf.bin").read_bytes()

ENROLL = 0x20        # header byte-4 bit that enrolls a record in live-repaint


def _build(live_repaint=True, rescale_donor=True):
    proj = {"schema": 1, "base_sha256": hashlib.sha256(STOCK).hexdigest(),
            "build_name": "t25",
            "ops": [{"id": "batt", "type": "add_voltage_battery",
                     "params": {"x": 615, "y": 334,
                                "live_repaint": live_repaint,
                                "rescale_donor": rescale_donor}}]}
    res = recipe.compile_project(recipe.Project.from_json(json.dumps(proj)), STOCK)
    assert res.ok, res.errors
    return res.data


def _tables(data):
    gs, _ = guidata.guidata_extent(data)
    return gs, guidata.object_tables(data)[1]


def _clone_idx(data):
    _, tab = _tables(data)
    return len(tab) - 1                       # clone is the last appended object


def _container(data, parent=902):
    """The clone's enrolled wt5 container record on the parent object."""
    gs, tab = _tables(data)
    idx = _clone_idx(data)
    return next(r for r in records.object_records(data, gs + tab[parent])
                if r.wtype == 5 and r.child_index == idx)


def _level_cell(data, r):
    return int.from_bytes(data[r.offset + r.size - 2:r.offset + r.size], "little")


def test_live_repaint_retargets_container_to_coach_cell():
    data = _build(live_repaint=True)
    crec = _container(data)
    assert crec.header[4] & ENROLL, "House container is not enrolled"
    assert crec.value_slot == 209, \
        "House container watches cell %s, not the live coach cell 209" % crec.value_slot


def test_live_repaint_false_leaves_inherited_chassis_cell():
    # Backward-compat: with the flag off, the container keeps the chassis cell 205 it inherited
    # from the template (so it only repaints on chassis-voltage change -- the v24 behaviour).
    data = _build(live_repaint=False, rescale_donor=False)
    crec = _container(data)
    assert crec.value_slot == 205, \
        "expected inherited chassis cell 205, got %s" % crec.value_slot


def test_clone_fill_record_is_not_enrolled():
    # The wt33 fill must never carry the enroll bit: for a wt33 header[4] bit 0x20 activates the
    # R42 value-slot field in `records.decode_record`, growing and corrupting the record. The fill
    # goes live via the CONTAINER's whole-child repaint, not its own enrollment.
    data = _build(live_repaint=True)
    gs, tab = _tables(data)
    recs = records.object_records(data, gs + tab[_clone_idx(data)])
    fill = next(r for r in recs if r.wtype == 33 and r.colors
                and r.colors[0][:2] == ("fillA", 7) and r.colors[0][2] != 3)
    assert not (fill.header[4] & ENROLL), \
        "clone fill wrongly enrolled: header[4]=0x%02x" % fill.header[4]


def test_rescale_donor_repoints_443_fill_to_clone_cells():
    data = _build(live_repaint=True, rescale_donor=True)
    gs, tab = _tables(data)

    # the clone's fresh 48V cells
    recs = records.object_records(data, gs + tab[_clone_idx(data)])
    cfill = next(r for r in recs if r.wtype == 33 and r.colors
                 and r.colors[0][:2] == ("fillA", 7) and r.colors[0][2] != 3)
    clone_color = cfill.colors[0][2]
    clone_level = _level_cell(data, cfill)
    assert clone_color >= 665 and clone_level >= 665, \
        "clone cells not freshly grown: color=%d level=%d" % (clone_color, clone_level)

    # donor 443 now reads the SAME fresh 48V cells (was 206/207 on a 12V scale)
    donor = records.object_records(data, gs + tab[443])
    dfill = next(r for r in donor if r.wtype == 33 and r.colors
                 and r.colors[0][:2] == ("fillA", 7) and r.colors[0][2] != 3)
    assert dfill.colors[0][2] == clone_color, \
        "donor 443 fill colour cell %d != clone's %d" % (dfill.colors[0][2], clone_color)
    assert _level_cell(data, dfill) == clone_level, \
        "donor 443 level cell %d != clone's %d" % (_level_cell(data, dfill), clone_level)


def test_donor_443_has_an_enrolled_209_container_so_its_fill_repaints_live():
    # The rescale relies on 443's own enrolled containers already watching 209 (they repaint the
    # whole 443 child on every voltage change). This pins that structural assumption.
    data = _build(live_repaint=True, rescale_donor=True)
    gs, tab = _tables(data)
    watchers = [oi for oi in range(len(tab))
                for r in records.object_records(data, gs + tab[oi])
                if r.wtype == 5 and r.child_index == 443
                and (r.header[4] & ENROLL) and r.value_slot == 209]
    assert watchers, "no enrolled wt5 container watches donor 443 on cell 209"


def test_rescale_donor_false_leaves_443_on_stock_cells():
    data = _build(live_repaint=True, rescale_donor=False)
    gs, tab = _tables(data)
    donor = records.object_records(data, gs + tab[443])
    dfill = next(r for r in donor if r.wtype == 33 and r.colors
                 and r.colors[0][:2] == ("fillA", 7) and r.colors[0][2] != 3)
    assert dfill.colors[0][2] == 206, "donor 443 fill colour should stay stock 206"
    assert _level_cell(data, dfill) == 207, "donor 443 level should stay stock 207"
