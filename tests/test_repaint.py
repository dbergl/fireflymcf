"""`repaint_on_cell`, `own_repaint_trigger` and `own_colour_cell(..., repaint=True)`: a tick
routine's store repaints a control in place, without a page draw.

The emulator test is the owner's request as a user op: the FRESH gauge's outline turns red once
the G12's raw fresh ADC reading has stayed above 62000 for 10 s, and back once it has stayed
below 58000 for 10 s. On the coach the gauge already reads 0 % from 54000 up (`BUS-BASELINE`
PC.10), so no percentage change ever repaints it: only the repaint primitive can. Both runs keep
the gauge at 0 % throughout and never leave HOME; the run without the repaint is the proof that
the repaint is what draws the red (each emulator run is about a minute of wall time).
"""
import hashlib
import json
import struct
import time

import numpy as np
import pytest

from fireflymcf import celltables, coverage, guidata, records, repaint, routinekit, userop

NEEDS_RESEARCH_DATA = "binds the 13.6 corpus image's tank gauges for repaint, boots it"

FRESH = 721                     # stock 13.6: the FRESH gauge (a wt-32 well first), test_colourcell
TANK_STATUS = 0x1FFB7
ADC_DGN = 0x0BFC1               # G12_TANK_LEVEL_SENSOR: instance 1 fresh, raw u16 at byte 1
RED = 0xFF0000


def _recs(data, oi):
    gs, _ = guidata.guidata_extent(data)
    return records.object_records(data, gs + guidata.object_tables(data)[1][oi])


def _objects(data):
    gs, _ = guidata.guidata_extent(data)
    out = []
    for rel in guidata.object_tables(data)[1]:
        try:
            out.append(tuple(bytes(data[r.offset:r.end])
                             for r in records.object_records(data, gs + rel)))
        except Exception:
            out.append(None)
    return out


def _row_addr(data, idx):
    gs, _ = guidata.guidata_extent(data)
    return struct.unpack_from("<I", data, gs + celltables.cell_table_off(data) + 4 * idx)[0]


# --- the bind, from the bytes -------------------------------------------------------------------

def test_primitives_registered():
    assert routinekit.PRIMITIVES["repaint_on_cell"] is routinekit.repaint_on_cell
    assert routinekit.PRIMITIVES["own_repaint_trigger"] is routinekit.own_repaint_trigger


def test_the_fresh_gauge_is_drawn_by_enrolled_containers(mcf_136):
    """Every placement of the gauge is a wt-5 container carrying the enrol bit and watching the
    level cell (value_slot 255): the live-redraw list already repaints it on a level change."""
    found = repaint.containers(mcf_136, FRESH)
    assert len(found) == 13 and all(e for _p, _k, e in found)
    for p, k, _e in found:
        rec = _recs(mcf_136, p)[k]
        assert (rec.wtype, rec.child_index, rec.value_slot) == (5, FRESH, 255)
        assert rec.header[4] & repaint.ENROLL_BIT
        assert all(m == 0 for _r, m, _v in rec.colors)          # it sets no colour itself


def test_repaint_on_cell_binds_only_the_containers(mcf_136):
    ed = userop.Editor(mcf_136)
    cell = ed.call("own_colour_cell", FRESH, "border")
    mid = ed.data
    rp = ed.call("repaint_on_cell", FRESH, cell.cell_index)
    out = ed.data
    assert userop.replay(mcf_136, ed.ledger) == out
    assert userop.touched_sections(mid, out) == {"guicode", "guidata"}
    assert (rp.object_index, rp.cell_index, rp.addr, rp.skipped) == (
        FRESH, cell.cell_index, cell.addr, ())
    parents = {w.parent for w in rp.watchers}
    assert len(rp.watchers) == len(parents) == 13
    assert {w.role for w in rp.watchers} == {"fillA"}             # the field 721[0] sets itself
    a, b = _objects(mid), _objects(out)
    assert [i for i, (x, y) in enumerate(zip(a, b)) if x != y] == sorted(parents)
    for w in rp.watchers:
        before, after = _recs(mid, w.parent), _recs(out, w.parent)
        for k, (x, y) in enumerate(zip(before, after)):
            if k != w.record:
                assert out[y.offset:y.end] == mid[x.offset:x.end]
        x, y = before[w.record], after[w.record]
        assert y.size == x.size + 2 and y.header[4] == x.header[4]
        assert (y.wtype, y.child_index, y.value_slot) == (x.wtype, x.child_index, x.value_slot)
        assert y.colors == [("fillA", 7, cell.cell_index)] + x.colors[1:]
    # the watched row is the cell, nothing else moved in the table, and coverage still holds
    assert _row_addr(out, cell.cell_index) == cell.addr
    assert coverage.analyse(out, deep=True).unclaimed == []


def test_own_colour_cell_repaint_is_the_two_calls(mcf_136):
    """`repaint=True` is `own_colour_cell` then `repaint_on_cell` on its row; without it the
    containers keep their stock bytes."""
    ed = userop.Editor(mcf_136)
    cell = ed.call("own_colour_cell", FRESH, "border", repaint=True)
    assert cell.repaint is not None and cell.repaint.cell_index == cell.cell_index
    assert cell.repaint.addr == cell.addr and len(cell.repaint.watchers) == 13
    assert userop.replay(mcf_136, ed.ledger) == ed.data
    two = userop.Editor(mcf_136)
    c2 = two.call("own_colour_cell", FRESH, "border")
    two.call("repaint_on_cell", FRESH, c2.cell_index)
    assert two.data == ed.data

    plain = userop.Editor(mcf_136)
    c3 = plain.call("own_colour_cell", FRESH, "border")
    assert c3.repaint is None
    for p, k, _e in repaint.containers(mcf_136, FRESH):
        assert _recs(plain.data, p)[k].colors == _recs(mcf_136, p)[k].colors


def test_batched_cells_repaint_per_request(mcf_136):
    """A batch binds only the requests that ask; any one cell of an object can be the watched
    one (the container watches it through fillA whatever field the cell colours)."""
    ed = userop.Editor(mcf_136)
    fill, grey = ed.call("own_colour_cells", [
        {"object": FRESH, "field": "fill", "record": 1, "repaint": True},
        [723, "border"]])
    assert {w.role for w in fill.repaint.watchers} == {"fillA"}
    assert grey.repaint is None
    for p, k, _e in repaint.containers(mcf_136, FRESH):
        colors = dict((r, (m, v)) for r, m, v in _recs(ed.data, p)[k].colors)
        assert colors["fillA"] == (7, fill.cell_index) and colors["fillB"] == (0, None)
    for p, k, _e in repaint.containers(mcf_136, 723):
        assert _recs(ed.data, p)[k].colors == _recs(mcf_136, p)[k].colors
    assert userop.replay(mcf_136, ed.ledger) == ed.data


def test_fillb_is_never_watched(mcf_136):
    """A container's fillB/fillB2 word is painted over the container's box when the renderer's
    +0x704C flag is set (`0x257AFC bl 0x255F44`, r0 = r7 = fillB/fillB2), before the child's
    first record resets anything. So once fillA is taken, the gauge's only other field its first
    record sets (fillB, mode 7) is refused, in a batch, a second bind and a trigger alike."""
    assert repaint.ROLES == ("fillA", "fillA2")
    with pytest.raises(routinekit.RoutineKitError) as e:
        routinekit.own_colour_cells(mcf_136, [
            {"object": FRESH, "field": "border", "repaint": True},
            {"object": FRESH, "field": "fill", "record": 1, "repaint": True}])
    assert e.value.code == "UNSUPPORTED_CONTAINER"
    ed = userop.Editor(mcf_136)
    border = ed.call("own_colour_cell", FRESH, "border", repaint=True)
    for name, args in (("repaint_on_cell", (FRESH, border.cell_index)),
                       ("own_repaint_trigger", (FRESH,))):
        with pytest.raises(routinekit.RoutineKitError) as e:
            ed.call(name, *args)
        assert e.value.code == "UNSUPPORTED_CONTAINER", name
        assert "fillB" not in str(e.value).split("among")[1].split("(")[0]


def test_own_repaint_trigger(mcf_136):
    ed = userop.Editor(mcf_136)
    trig = ed.call("own_repaint_trigger", FRESH)
    assert trig.width == 4 and trig.addr % 4 == 0 and trig.initialised_at_boot is False
    assert _row_addr(ed.data, trig.cell_index) == trig.addr
    assert trig.repaint.cell_index == trig.cell_index and len(trig.repaint.watchers) == 13
    assert userop.replay(mcf_136, ed.ledger) == ed.data
    assert userop.touched_sections(mcf_136, ed.data) == {"guicode", "guidata"}


def _first(data, code):
    """The first object `repaint.plan` refuses with `code`, found from the bytes."""
    for oi in range(len(guidata.object_tables(data)[1])):
        try:
            repaint.plan(data, oi)
        except repaint.RepaintError as e:
            if e.code == code:
                return oi
    raise AssertionError(f"no object refuses {code}")


@pytest.mark.parametrize("code", ["NO_LIVE_CONTAINER", "CHILD_INHERITS", "UNSUPPORTED_CONTAINER"])
def test_refusals_from_the_bytes(mcf_136, code):
    oi = _first(mcf_136, code)
    with pytest.raises(routinekit.RoutineKitError) as e:
        routinekit.repaint_on_cell(mcf_136, oi, 8)
    assert e.value.code == code
    with pytest.raises(routinekit.RoutineKitError) as e:     # refused before the cell grows
        routinekit.own_repaint_trigger(mcf_136, oi)
    assert e.value.code == code


@pytest.mark.parametrize("args,code", [
    ((-1, 8), "BAD_OBJECT"), ((10 ** 6, 8), "BAD_OBJECT"), ((True, 8), "BAD_OBJECT"),
    ((FRESH, -1), "BAD_CELL"), ((FRESH, 10 ** 6), "BAD_CELL"), ((FRESH, "8"), "BAD_CELL"),
])
def test_bad_arguments(mcf_136, args, code):
    with pytest.raises(routinekit.RoutineKitError) as e:
        routinekit.repaint_on_cell(mcf_136, *args)
    assert e.value.code == code


def test_a_cell_request_that_cannot_repaint_refuses(mcf_136):
    """own_colour_cell(repaint=True) on a field own_colour_cell accepts, of an object repaint
    cannot bind, refuses with repaint's code (and gives back no image)."""
    from fireflymcf import colourcell
    rows = celltables.row_count(mcf_136)
    for oi in range(len(guidata.object_tables(mcf_136)[1])):
        try:
            colourcell._plan(mcf_136, oi, "border", None, None, rows)
            repaint.plan(mcf_136, oi)
        except colourcell.ColourCellError:
            continue
        except repaint.RepaintError as e:
            code = e.code
            break
    else:
        raise AssertionError("no colourable object that repaint refuses")
    with pytest.raises(routinekit.RoutineKitError) as e:
        routinekit.own_colour_cell(mcf_136, oi, "border", repaint=True)
    assert e.value.code == code
    routinekit.own_colour_cell(mcf_136, oi, "border")           # and without repaint it binds


# --- emulator: the owner's request as a user op -------------------------------------------------

_OP = '''"""user:fresh-low-outline-test: FRESH's outline red after > HIGH for HOLD ms, back after
< LOW for HOLD ms (a test fixture, never flash it)."""
from fireflymcf import recipe, routinekit, thumb
from fireflymcf.userop import UserOp

TAG = b"uRPT"
FRESH = 721


@recipe.op("user:fresh-low-outline-test")
class FreshLowOutline(UserOp):
    PARAMS = {{"properties": {{}}, "required": []}}

    def build(self, ed, ctx):
        reg = ed.call("resolve_register", dgn=0x0BFC1, instance=1)
        cell = ed.call("own_colour_cell", FRESH, "border", repaint={repaint!r})
        st = ed.call("fresh_sram", 8, taken=list(range(cell.addr, cell.addr + 4)))
        pool = {{"slot": reg.slot_addr, "cell": cell.addr, "st": st, "red": 0xFF0000,
                "restore": cell.before_word, "high": 62000, "low": 58000, "hold": 10000,
                "ffff": 0xFFFF}}

        def e(r, code, text):
            r.emit(len(code), lambda h, s, c=code, t=text: (c, t))

        def build_routine(load, tail):
            r = thumb.Routine(dict(pool), error=routinekit.RoutineKitError)
            routinekit.emit_prologue(r)
            r.ldr_lit(0, "cell")                       # 1. every tick: the normal colour
            r.ldr_lit(1, "restore")
            e(r, thumb.str_imm(1, 0, 0), "str r1, [r0]")
            e(r, thumb.ldr_imm12(3, 4, 0x204), "ldr.w r3, [r4, #0x204]")   # ms clock
            r.ldr_lit(2, "st")
            e(r, thumb.ldrb_imm(1, 2, 5), "ldrb r1, [r2, #5]")
            e(r, thumb.cmp_imm8(1, 0x5A), "cmp r1, #0x5a")
            r.bcc("eq", "inited")
            e(r, thumb.movs_imm(1, 0x5A), "movs r1, #0x5a")
            e(r, thumb.strb_imm(1, 2, 5), "strb r1, [r2, #5]")
            e(r, thumb.movs_imm(1, 0), "movs r1, #0")
            e(r, thumb.strb_imm(1, 2, 4), "strb r1, [r2, #4]")
            e(r, thumb.str_imm(3, 2, 0), "str r3, [r2]")
            r.b("paint")
            r.label("inited")
            r.ldr_lit(0, "slot")
            e(r, thumb.ldrh_imm(0, 0, 0), "ldrh r0, [r0]")
            r.ldr_lit(1, "ffff")
            e(r, thumb.cmp_reg(0, 1), "cmp r0, r1")
            r.bcc("eq", "rearm")
            e(r, thumb.ldrb_imm(1, 2, 4), "ldrb r1, [r2, #4]")
            e(r, thumb.cmp_imm8(1, 1), "cmp r1, #1")
            r.bcc("eq", "isred")
            r.ldr_lit(1, "high")
            e(r, thumb.cmp_reg(0, 1), "cmp r0, r1")
            r.bcc("ls", "rearm")
            r.b("timed")
            r.label("isred")
            r.ldr_lit(1, "low")
            e(r, thumb.cmp_reg(0, 1), "cmp r0, r1")
            r.bcc("hs", "rearm")
            r.label("timed")
            e(r, thumb.ldr_imm(0, 2, 0), "ldr r0, [r2]")
            e(r, thumb.subs_reg(0, 3, 0), "subs r0, r3, r0")
            r.ldr_lit(1, "hold")
            e(r, thumb.cmp_reg(0, 1), "cmp r0, r1")
            r.bcc("lo", "paint")
            e(r, thumb.ldrb_imm(1, 2, 4), "ldrb r1, [r2, #4]")
            e(r, thumb.movs_imm(0, 1), "movs r0, #1")
            e(r, thumb.subs_reg(1, 0, 1), "subs r1, r0, r1")
            e(r, thumb.strb_imm(1, 2, 4), "strb r1, [r2, #4]")
            r.label("rearm")
            e(r, thumb.str_imm(3, 2, 0), "str r3, [r2]")
            r.label("paint")
            e(r, thumb.ldrb_imm(1, 2, 4), "ldrb r1, [r2, #4]")
            e(r, thumb.cmp_imm8(1, 1), "cmp r1, #1")
            r.bcc("ne", "done")
            r.ldr_lit(0, "cell")                       # 2. alarm: red over it
            r.ldr_lit(1, "red")
            e(r, thumb.str_imm(1, 0, 0), "str r1, [r0]")
            r.label("done")
            routinekit.emit_tail(r, tail)
            return routinekit.assemble_tick(r, load, prefix=TAG + cell.addr.to_bytes(4, "little"))

        routinekit.install_tick(ed, build_routine, "fresh low outline")

    def check(self, data, ctx):
        return "fresh low outline"
'''


def _project(tmp_path, stock, repaint_on):
    from fireflymcf import recipe
    folder = tmp_path / "ops" / "flo"
    folder.mkdir(parents=True)
    (folder / "op.py").write_text(_OP.format(repaint=repaint_on))
    (folder / "manifest.json").write_text(json.dumps({
        "name": "flo", "type": "user:fresh-low-outline-test", "version": "1.0.0",
        "author": "tests", "description": "repaint test fixture, never flash it",
        "touches": ["guicode", "guidata"], "written_against": [], "provenance": []}))
    pf = tmp_path / "p.ffproj.json"
    pf.write_text(json.dumps({
        "schema": 1, "base_sha256": hashlib.sha256(stock).hexdigest(),
        "build_name": "Jayco B-Van V13 repaint test",
        "ops": [{"id": "flo", "type": "user:fresh-low-outline-test", "params": {}}]}))
    proj = recipe.Project.from_json(pf.read_text())
    with userop.registered(userop.load_user_ops(pf, proj)):
        res = recipe.compile_project(proj, stock)
    assert res.ok, res.errors
    return res.data


def _band(img, box, width=3):
    x0, y0, x1, y1 = box
    sub = img[y0:y1 + 1, x0:x1 + 1]
    mask = np.ones(sub.shape[:2], bool)
    mask[width:-width, width:-width] = False
    return sub[mask]


def _count(px, rgb):
    return int(np.all(px == np.array(rgb, np.uint8), axis=1).sum())


def _run(image, tmp_path):
    """Boot with fresh at 56000 (0 %), never leave HOME: above 62000 for 14 s, then 57000 (still
    0 %) for 14 s. Returns what was seen at each step."""
    from fireflymcf.emu.scenario import Scenario
    cell = routinekit.guicode(image).u32(routinekit.find_tag(image, b"uRPT") + 4)
    level_slot = routinekit.resolve_register(image, dgn=TANK_STATUS, instance=0)[1].slot_addr
    adc_slot = routinekit.resolve_register(image, dgn=ADC_DGN, instance=1)[1].slot_addr
    s = Scenario(image, timeout_s=900)
    s.set_g12_selector(0xF5, 8)                              # floorplan WD
    s.set_tank_raw(1, 56000)
    s.boot()
    fixed = time.strptime("2026-06-01 12:00:00", "%Y-%m-%d %H:%M:%S")
    s._g12().clock = lambda: fixed                           # no clock readout ticks over
    s.run(4.0)
    assert s.page == "HOME", s.page
    screen = s.screen
    box = next((q.x0, q.y0, q.x1, q.y1) for q in s._placed(s._root())
               if q.wtype == 32 and q.obj_index == FRESH)
    steps = []

    def look(name):
        word = int.from_bytes(s.read_mem(cell, 4), "little")
        level, res = s.read_mem(level_slot, 2)
        adc = int.from_bytes(s.read_mem(adc_slot, 2), "little")
        img = s.screenshot(tmp_path / f"{name}.png")
        steps.append({"name": name, "word": word, "level": level, "res": res, "adc": adc,
                      "img": img, "page": s.page, "screen": s.screen})

    look("start")
    s.set_tank_raw(1, 63000)
    s.run(7.0)
    look("above-7s")
    s.run(7.0)
    look("above-14s")
    s.set_tank_raw(1, 57000)
    s.run(7.0)
    look("below-7s")
    s.run(7.0)
    look("below-14s")
    s.set_tank_raw(1, 63000)                                 # red again, then a full compose
    s.run(14.0)
    look("red-again")
    s.goto("SETTINGS")
    s.goto("HOME")
    s.run(2.0)
    look("composed-red")
    return {"box": box, "screen": screen, "steps": {x["name"]: x for x in steps}}


@pytest.fixture(scope="module")
def runs(mcf_136, tmp_path_factory):
    pytest.importorskip("unicorn")
    out = {}
    for on in (True, False):
        image = _project(tmp_path_factory.mktemp(f"p{int(on)}"), mcf_136, on)
        out[on] = _run(image, tmp_path_factory.mktemp(f"shots{int(on)}"))
    return out


def _the_routine_ran_and_nothing_else_moved(run):
    """What holds with or without the repaint: the routine's colour word follows the request,
    the gauge reads 0 % throughout, HOME is never left."""
    st = run["steps"]
    for x in st.values():
        assert (x["page"], x["screen"]) == ("HOME", run["screen"]), x["name"]
        assert (x["level"], x["res"]) == (0, 3), f"{x['name']}: the gauge is not at 0 %"
    assert [st[n]["adc"] for n in ("start", "above-7s", "below-7s")] == [56000, 63000, 57000]
    normal = st["start"]["word"]
    assert normal == 0xFFFFFF
    assert st["above-7s"]["word"] == normal                       # not before the hold
    assert st["above-14s"]["word"] == RED
    assert st["below-7s"]["word"] == RED                          # not before the hold
    assert st["below-14s"]["word"] == normal


def _outline_repainted_live(run):
    """The owner's view: red on HOME after the hold above, white again after the hold below,
    with no page draw in between; nothing outside the FRESH outline changes."""
    st, box = run["steps"], run["box"]
    x0, y0, x1, y1 = box
    start = st["start"]["img"]
    for n in ("start", "above-7s", "below-14s"):
        band = _band(st[n]["img"], box)
        assert _count(band, (255, 0, 0)) == 0 and _count(band, (255, 255, 255)) > 150, n
    red = st["above-14s"]["img"]
    band = _band(red, box)
    assert _count(band, (255, 0, 0)) > 150 and _count(band, (255, 255, 255)) == 0, \
        "the outline did not turn red on HOME without a page draw"
    assert _count(_band(st["below-7s"]["img"], box), (255, 0, 0)) > 150
    changed = np.any(red != start, axis=2)
    outside = changed.copy()
    outside[y0:y1 + 1, x0:x1 + 1] = False
    assert not outside.any(), f"{int(outside.sum())} px changed outside the FRESH gauge"
    inside = changed[y0:y1 + 1, x0:x1 + 1]
    assert np.all(np.all(red[y0:y1 + 1, x0:x1 + 1][inside] == (255, 0, 0), axis=1)), \
        "a changed pixel of the gauge is not outline red"
    for n in ("above-7s", "below-14s"):
        assert np.array_equal(st[n]["img"], start), f"{n}: the screen differs from the start"


def test_repaint_turns_the_outline_red_and_back_without_a_page_draw(runs):
    _the_routine_ran_and_nothing_else_moved(runs[True])
    _outline_repainted_live(runs[True])


def test_without_the_repaint_the_same_check_fails(runs):
    """The control: the same op without `repaint` writes the same colour word at the same
    ticks, and HOME never shows it (no page draw happens in the window), so the live check
    above fails on it."""
    _the_routine_ran_and_nothing_else_moved(runs[False])
    with pytest.raises(AssertionError, match="did not turn red"):
        _outline_repainted_live(runs[False])
    st = runs[False]["steps"]
    for x in st.values():
        if x["name"] != "composed-red":
            assert np.array_equal(x["img"], st["start"]["img"]), x["name"]


def test_a_full_compose_draws_the_same_with_and_without_the_bind(runs):
    """HOME composed with the alarm word set is pixel for pixel the unbound op's compose (which
    draws the red from the gauge's own record), and the same as the live repaint drew. This is
    consistency evidence, not a test of the leak paths: binding fillB or an inherited fillB2
    instead also passes it, because HOME's compose takes neither the `+0x704C` box fill nor a
    path that draws the inherited field. The fillB refusal is pinned by
    `test_fillb_is_never_watched` from the code."""
    bound, unbound = runs[True]["steps"], runs[False]["steps"]
    for st in (bound, unbound):
        assert st["composed-red"]["word"] == RED and st["composed-red"]["page"] == "HOME"
        assert st["red-again"]["word"] == RED
    assert np.array_equal(bound["composed-red"]["img"], unbound["composed-red"]["img"])
    assert np.array_equal(bound["composed-red"]["img"], bound["red-again"]["img"])
    assert np.array_equal(bound["red-again"]["img"], bound["above-14s"]["img"])
    assert _count(_band(unbound["composed-red"]["img"], runs[False]["box"]), (255, 0, 0)) > 150
