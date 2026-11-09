"""`own_colour_cell`: a private run-time colour cell for one control's border or fill.

Every call goes through `userop.Editor.call`, so each test also proves the call replays. The tank
gauges are found from the bytes (the receive map's TANK_STATUS entries and the objects whose
percent readout they paint), never by object number. The emulator test boots the built image in
`Scenario` (about 20 s of wall time).
"""
import struct

import numpy as np
import pytest

from fireflymcf import (busmap, celltables, colourcell, guidata, readout, records, relocate,
                        routinekit, sramreach, tablegrow, theme, thumb, userop)

NEEDS_RESEARCH_DATA = "gives the 13.6 corpus image's tank gauges colour cells, boots it"

TANK_STATUS = 0x1FFB7
RED = 0xFF0000
GARBAGE = 0x00FF00FF                    # magenta: what an uninitialised cell might hold
GARBAGE_RGB = (255, 0, 255)


@pytest.fixture(scope="module")
def gauges(corpus):
    """{TANK_STATUS instance: object index} of every gauge on stock 13.6: the object whose wt-8
    readout the instance's receive entry paints, and whose first record is a wt-32 tank well."""
    data = corpus["13.6"]["mcf"]
    gs, _ = guidata.guidata_extent(data)
    tab = guidata.object_tables(data)[1]
    out = {}
    for e in busmap.receive_map(data, frozenset({TANK_STATUS})):
        objs = {w.obj for w in e.widgets if w.wtype == 8}
        wells = [o for o in objs if records.object_records(data, gs + tab[o])[0].wtype == 32]
        if wells:
            (out[e.instance],) = wells
    return out


def _recs(data, oi):
    gs, _ = guidata.guidata_extent(data)
    return records.object_records(data, gs + guidata.object_tables(data)[1][oi])


def _row(data, idx):
    gs, _ = guidata.guidata_extent(data)
    addr = struct.unpack_from("<I", data, gs + celltables.cell_table_off(data) + 4 * idx)[0]
    return addr, data[gs + celltables.type_table_off(data) + idx]


def _objects(data):
    """Every table-1 object's record bytes, or None where an entry does not decode."""
    gs, _ = guidata.guidata_extent(data)
    out = []
    for rel in guidata.object_tables(data)[1]:
        try:
            out.append(tuple(bytes(data[r.offset:r.end])
                             for r in records.object_records(data, gs + rel)))
        except Exception:
            out.append(None)
    return out


@pytest.mark.slow
def test_tank_gauges_found_from_the_bytes(gauges, mcf_136):
    assert gauges == {0: 721, 1: 719, 2: 723, 18: 725}       # fresh, black, grey, grey 2
    for oi in gauges.values():
        well, level = _recs(mcf_136, oi)[:2]
        assert (well.wtype, level.wtype) == (32, 33)
        assert ("fillA", 2, None) in well.colors and ("fillB", 7, 161) in well.colors


def test_primitive_registered():
    assert routinekit.PRIMITIVES["own_colour_cell"] is routinekit.own_colour_cell


def test_border_of_the_fresh_gauge(gauges, mcf_136):
    fresh = gauges[0]
    _cb, _tb, rows = readout.resolve_cell_tables(mcf_136)
    ed = userop.Editor(mcf_136)
    cell = ed.call("own_colour_cell", fresh, "border")
    out = ed.data

    assert (cell.object_index, cell.record, cell.role) == (fresh, 0, "fillA")
    assert (cell.width, cell.encoding, cell.initialised_at_boot) == (4, "rgb888", False)
    assert (cell.before, cell.before_word, cell.before_mode) == ((255, 255, 255), 0xFFFFFF, 2)
    assert cell.before_cell_addr is None
    assert cell.cell_index == rows                              # a new row, never a stock one
    assert cell.addr % 4 == 0 and 0x2000AD98 < cell.addr < theme.SRAM_HIGH
    assert _row(out, cell.cell_index) == (cell.addr, 10)
    assert userop.replay(mcf_136, ed.ledger) == out
    assert userop.touched_sections(mcf_136, out) == {"guicode", "guidata"}

    # the well's border now names the row; every other byte of every record is stock's
    before, after = _recs(mcf_136, fresh), _recs(out, fresh)
    assert ("fillA", 7, cell.cell_index) in after[0].colors
    assert after[0].size == before[0].size + 2                  # mode 2 had no inline value
    assert [r.wtype for r in after] == [r.wtype for r in before]
    for a, b in zip(after[1:], before[1:]):
        assert out[a.offset:a.end] == mcf_136[b.offset:b.end]
    objs_before, objs_after = _objects(mcf_136), _objects(out)
    assert len(objs_before) == len(objs_after)
    assert [i for i, (a, b) in enumerate(zip(objs_before, objs_after)) if a != b] == [fresh]
    # the stock rows are copied verbatim into the grown table; the new row's 4 bytes are taken
    for i in range(rows):
        assert _row(out, i) == _row(mcf_136, i)
    assert sramreach.reach(out).referenced(cell.addr, 4)


def test_guicode_changes_are_only_the_table_grow_and_shifted_pointers(gauges, mcf_136):
    """Diff the whole GuiCode section: every differing word is a cell/type table base literal, an
    index-bound `cmp.w`, or a GuiData pointer the object swap moved by its size change."""
    ed = userop.Editor(mcf_136)
    ed.call("own_colour_cell", gauges[0], "border")
    out = ed.data
    gc0, gc1 = theme.guicode_section(mcf_136), theme.guicode_section(out)
    a = mcf_136[gc0.offset:gc0.offset + gc0.length - 4]
    b = out[gc1.offset:gc1.offset + gc1.length - 4]
    assert len(a) == len(b)
    cell_base, type_base, rows = readout.resolve_cell_tables(mcf_136)
    base = theme.guicode_base(gc0)
    literals = {r - base for r in tablegrow.find_base_refs(mcf_136, cell_base)
                + tablegrow.find_base_refs(mcf_136, type_base)}
    bounds = {r - base for r in tablegrow.find_bound_sites(mcf_136, rows - 1)}
    gs, _ = guidata.guidata_extent(mcf_136)
    obj_end = gs + guidata.object_tables(mcf_136)[1][gauges[0]]
    diffs = sorted({i & ~3 if (i & ~3) in literals else i for i in range(len(a)) if a[i] != b[i]})
    unexplained = []
    for i in diffs:
        word = lambda d, k=i: struct.unpack_from("<I", d, k)[0]
        if i in literals or any(s <= i < s + 4 for s in bounds):
            continue
        if i % 4 == 0 and 0x90000000 + (obj_end - gs) < word(a) < 0x9F000000 \
                and word(b) - word(a) == 4:
            continue
        unexplained.append(hex(base + i))
    assert not unexplained
    assert literals and bounds


@pytest.mark.slow
def test_fill_of_a_gauge(gauges, mcf_136):
    """The level fill: an inline (mode-3) colour on fresh, a shared row (mode 7) on black."""
    ed = userop.Editor(mcf_136)
    fresh = ed.call("own_colour_cell", gauges[0], "fill", record=1)
    assert (fresh.record, fresh.role, fresh.before_mode) == (1, "fillA", 3)
    assert fresh.before == (0x00, 0xA7, 0x9D)                   # the fresh teal, inline
    assert ("fillA", 7, fresh.cell_index) in _recs(ed.data, gauges[0])[1].colors

    black_idx = next(v for r, m, v in _recs(mcf_136, gauges[1])[1].colors if r == "fillA")
    black = ed.call("own_colour_cell", gauges[1], "fill", record=1)
    assert black.before_mode == 7
    assert black.before_cell_addr == _row(mcf_136, black_idx)[0]
    assert black.before == theme.mode7_palette(mcf_136)[black_idx]
    assert ("fillA", 7, black.cell_index) in _recs(ed.data, gauges[1])[1].colors
    assert _row(ed.data, black_idx) == _row(mcf_136, black_idx)  # the shared row is untouched
    assert userop.replay(mcf_136, ed.ledger) == ed.data


def test_two_calls_give_two_independent_cells(gauges, mcf_136):
    ed = userop.Editor(mcf_136)
    a = ed.call("own_colour_cell", gauges[0], "border")
    b = ed.call("own_colour_cell", gauges[2], "border")
    out = ed.data
    assert a.cell_index != b.cell_index
    assert not set(range(a.addr, a.addr + 4)) & set(range(b.addr, b.addr + 4))
    assert _row(out, a.cell_index) == (a.addr, 10) and _row(out, b.cell_index) == (b.addr, 10)
    assert ("fillA", 7, a.cell_index) in _recs(out, gauges[0])[0].colors
    assert ("fillA", 7, b.cell_index) in _recs(out, gauges[2])[0].colors
    for oi in (gauges[1], gauges[18]):                          # the other wells keep white
        assert ("fillA", 2, None) in _recs(out, oi)[0].colors
    free = ed.call("fresh_sram", 4)
    assert not set(range(free, free + 4)) & (set(range(a.addr, a.addr + 4))
                                             | set(range(b.addr, b.addr + 4)))
    assert userop.replay(mcf_136, ed.ledger) == out


@pytest.mark.parametrize("args,kw,code", [
    ((721, "border"), {"record": 3}, "UNSUPPORTED_FIELD"),      # a wt-8 readout has no border
    ((721, "glow"), {}, "UNSUPPORTED_FIELD"),
    ((0, "border"), {}, "NO_COLOUR_FIELD"),                     # a wt-6 switch
    ((721, "fill"), {}, "AMBIGUOUS_RECORD"),                    # the well's interior, the level
    ((194, "fill"), {}, "STATEFUL_RECORD"),                     # a button box on its value slot
    ((31, "fill"), {"record": 19}, "UNSUPPORTED_MODE"),         # an inheriting fillA
    ((16, "fill"), {"record": 5}, "INHERITED_COLOUR"),          # a later record inherits it
    ((246, "fill"), {}, "INHERITED_COLOUR"),                    # the object ends unreset
    ((99999, "border"), {}, "BAD_OBJECT"),
    ((721, "border"), {"expect": b"\x00" * 16}, "FIRMWARE_MISMATCH"),
])
def test_refusals(mcf_136, args, kw, code):
    ed = userop.Editor(mcf_136)
    with pytest.raises(routinekit.RoutineKitError) as e:
        ed.call("own_colour_cell", *args, **kw)
    assert e.value.code == code, str(e.value)
    assert ed.data == mcf_136 and ed.ledger == ()


def test_expect_matches_the_stock_record(gauges, mcf_136):
    well = _recs(mcf_136, gauges[0])[0]
    ed = userop.Editor(mcf_136)
    cell = ed.call("own_colour_cell", gauges[0], "border",
                   expect=mcf_136[well.offset:well.end])
    assert cell.record == 0


# --- emulator ---------------------------------------------------------------------------------

def _alarm_build(slot: int, cell: colourcell.ColourCell):
    """A tick routine in the documented shape: store the field's old colour into the cell at
    entry, every tick, then overwrite it with red while the tank's level is at or below 10 % of
    its resolution (TANK_STATUS byte 1 / byte 2, the receive slot's low and high byte)."""
    def build(code_load_addr, tail_target):
        r = thumb.Routine({"slot": slot, "cell": cell.addr, "red": RED,
                           "restore": cell.before_word}, error=routinekit.RoutineKitError)
        routinekit.emit_prologue(r)
        r.ldr_lit(0, "cell")
        r.ldr_lit(1, "restore")
        r.emit(2, lambda h, s: (thumb.str_imm(1, 0, 0), "str r1, [r0]"))      # unconditional
        r.ldr_lit(0, "slot")
        r.emit(2, lambda h, s: (thumb.ldrh_imm(1, 0, 0), "ldrh r1, [r0]"))
        r.emit(2, lambda h, s: (thumb.lsls_imm(2, 1, 24), "lsls r2, r1, #0x18"))
        r.emit(2, lambda h, s: (thumb.lsrs_imm(2, 2, 24), "lsrs r2, r2, #0x18"))   # level
        r.emit(2, lambda h, s: (thumb.lsrs_imm(3, 1, 8), "lsrs r3, r1, #8"))       # resolution
        r.emit(2, lambda h, s: (thumb.movs_imm(0, 10), "movs r0, #0xa"))
        r.emit(2, lambda h, s: (thumb.muls(2, 0), "muls r2, r0, r2"))
        r.emit(2, lambda h, s: (thumb.cmp_reg(2, 3), "cmp r2, r3"))
        r.bcc("hi", "done")                                     # 10 * level > resolution
        r.ldr_lit(0, "cell")
        r.ldr_lit(1, "red")
        r.emit(2, lambda h, s: (thumb.str_imm(1, 0, 0), "str r1, [r0]"))
        r.label("done")
        routinekit.emit_tail(r, tail_target)
        return routinekit.assemble_tick(r, code_load_addr)
    return build


def _band(img, box, width=3):
    """The pixels of the `width`-px band just inside `box` (inclusive): the outline."""
    x0, y0, x1, y1 = box
    sub = img[y0:y1 + 1, x0:x1 + 1]
    mask = np.ones(sub.shape[:2], bool)
    mask[width:-width, width:-width] = False
    return sub[mask]


def _count(px, rgb):
    return int(np.all(px == np.array(rgb, np.uint8), axis=1).sum())


@pytest.mark.slow
def test_emulated_outline_is_red_only_when_low(gauges, mcf_136, tmp_path):
    pytest.importorskip("unicorn")
    from fireflymcf.emu.scenario import Scenario

    ed = userop.Editor(mcf_136)
    cell = ed.call("own_colour_cell", gauges[0], "border")
    reg = ed.call("resolve_register", dgn=TANK_STATUS, instance=0)
    routinekit.install_tick(ed, _alarm_build(reg.slot_addr, cell), "fresh low alarm")
    assert userop.replay(mcf_136, ed.ledger) == ed.data

    s = Scenario(ed.data, timeout_s=400)
    # power-on garbage in the cell, before the first tick (nothing initialises it)
    s._m.mem(cell.addr, 4)[:] = GARBAGE.to_bytes(4, "little")
    s.set_tank_raw(1, 50)                                       # fresh full at power-on
    s.boot()

    def wells():
        return {q.obj_index: (q.x0, q.y0, q.x1, q.y1)
                for q in s._placed(s._root()) if q.wtype == 32}

    def level():
        return s.read_mem(reg.slot_addr, 2)[0]

    def until(cond, limit):
        end = s.now + limit
        while not cond():
            assert s.now < end, "timed out"
            s.run(1.0)
        s.run(1.0)                                              # the repaint after the change

    until(lambda: set(gauges.values()) <= set(wells()), 20)
    boxes = wells()
    others = [gauges[i] for i in (1, 2, 18)]
    shots = []
    for raw, low in ((None, False), (63000, True), (50, False)):
        if raw is not None:
            s.set_tank_raw(1, raw)
        until(lambda: (level() == 0) == low, 45)
        img = s.screenshot(tmp_path / f"home-{len(shots)}.png")
        shots.append(img)
        mine = _band(img, boxes[gauges[0]])
        assert _count(mine, GARBAGE_RGB) == 0
        word = int.from_bytes(s.read_mem(cell.addr, 4), "little")
        if low:
            assert word == RED
            assert _count(mine, (255, 0, 0)) > 150 and _count(mine, (255, 255, 255)) == 0
        else:
            assert word == cell.before_word
            assert _count(mine, (255, 255, 255)) > 150 and _count(mine, (255, 0, 0)) == 0
        for oi in others:
            band = _band(img, boxes[oi])
            assert _count(band, (255, 255, 255)) > 150 and _count(band, (255, 0, 0)) == 0
    for oi in others:                                           # the neighbours never changed
        assert all(np.array_equal(_band(x, boxes[oi]), _band(shots[0], boxes[oi]))
                   for x in shots[1:])


# --- the batched form ---------------------------------------------------------------------------

def test_batched_cells_take_one_grow(gauges, mcf_136):
    from fireflymcf import coverage
    _cb, _tb, rows = readout.resolve_cell_tables(mcf_136)
    ed = userop.Editor(mcf_136)
    objs = [gauges[i] for i in (0, 1, 2, 18)]
    cells = ed.call("own_colour_cells", [[oi, "border"] for oi in objs])
    out = ed.data
    assert [c.cell_index for c in cells] == list(range(rows, rows + 4))
    assert [c.object_index for c in cells] == objs
    addrs = [a for c in cells for a in range(c.addr, c.addr + 4)]
    assert len(set(addrs)) == 16
    for oi, c in zip(objs, cells):
        assert ("fillA", 7, c.cell_index) in _recs(out, oi)[0].colors
        assert _row(out, c.cell_index) == (c.addr, 10)
        assert c.before == (255, 255, 255)
    report = coverage.analyse(out, deep=True)
    labels = [label.split(":")[0] for _s, _n, label in report.spans]
    assert labels.count("variable-cell-table") == 1
    assert labels.count("variable-cell-table-dead") == 0          # one grow, no dead copy
    assert userop.replay(mcf_136, ed.ledger) == out
    assert userop.touched_sections(mcf_136, out) == {"guicode", "guidata"}


def test_batched_two_fields_of_one_record_compose(gauges, mcf_136):
    ed = userop.Editor(mcf_136)
    border, fill = ed.call("own_colour_cells", [[gauges[0], "border"],
                                                 {"object": gauges[0], "field": "fill",
                                                  "record": 0}])
    well = _recs(ed.data, gauges[0])[0]
    assert ("fillA", 7, border.cell_index) in well.colors
    assert ("fillB", 7, fill.cell_index) in well.colors
    assert fill.before_mode == 7 and fill.before_cell_addr == _row(mcf_136, 161)[0]


@pytest.mark.parametrize("reqs,code", [
    ([[721, "border"], [721, "border"]], "BAD_EDIT"),                  # the same field twice
    ([], "BAD_EDIT"),
    ([[721]], "BAD_EDIT"),
    ([[721, "border"], [246, "fill"]], "INHERITED_COLOUR"),            # one bad refuses all
])
def test_batched_refusals(mcf_136, reqs, code):
    ed = userop.Editor(mcf_136)
    with pytest.raises(routinekit.RoutineKitError) as e:
        ed.call("own_colour_cells", reqs)
    assert e.value.code == code, str(e.value)
    assert ed.data == mcf_136 and ed.ledger == ()


# --- a relocation failure is a coded refusal that names the headroom ----------------------------

@pytest.mark.parametrize("where,code", [("readout.allocate_fresh_cells", "NO_DISPLAY_CELL"),
                                        ("relocate.replace_object_records", "BAD_EDIT")])
def test_relocation_failure_is_coded(mcf_136, monkeypatch, where, code):
    mod, fn = where.split(".")

    def boom(*a, **k):
        raise relocate.RelocationError("the trailing object ... is absorbing something else")
    monkeypatch.setattr({"readout": readout, "relocate": relocate}[mod], fn, boom)
    with pytest.raises(routinekit.RoutineKitError) as e:
        userop.Editor(mcf_136).call("own_colour_cell", 721, "border")
    assert e.value.code == code
    assert "tail headroom is" in str(e.value)


# --- a build whose GuiData tail is nearly spent -------------------------------------------------

# Five AC readouts, each growing the cell table: the last object table's tail headroom falls
# below one grow of the table, as on the reference coach's running builds (2026-11-03).
def _crowded_ops():
    ops = []
    for i, (inst, byte) in enumerate([(1, 1), (1, 3), (65, 3), (2, 1), (2, 3)]):
        ops.append({"id": f"f{i}", "type": "append_filter",
                    "params": {"dgn": 0x1FFD7, "mode": 2, "payload_byte": byte,
                               "instance": inst, "compare_byte": 0}})
        ops.append({"id": f"r{i}", "type": "add_register_readout",
                    "params": {"register": {"ref": f"f{i}"}, "draw": False, "shift": 1}})
    return ops


_CELLS_OP = '''from fireflymcf import recipe
from fireflymcf.userop import UserOp


@recipe.op("user:cells")
class Cells(UserOp):
    PARAMS = {{"properties": {{}}, "required": []}}

    def build(self, ed, ctx):
        objs = {objs!r}
        if {batched!r}:
            cells = ed.call("own_colour_cells", [[oi, "border"] for oi in objs])
        else:
            cells = [ed.call("own_colour_cell", oi, "border") for oi in objs]
        ctx.handles[self.spec.id] = [c.cell_index for c in cells]

    def check(self, data, ctx):
        return "cells"
'''


def _cells_project(tmp_path, stock, objs, batched, touches=("guicode", "guidata"), ops=()):
    import hashlib
    import json
    from fireflymcf import recipe
    folder = tmp_path / "ops" / "cells"
    folder.mkdir(parents=True)
    (folder / "op.py").write_text(_CELLS_OP.format(objs=list(objs), batched=batched))
    (folder / "manifest.json").write_text(json.dumps({
        "name": "cells", "type": "user:cells", "version": "1.0.0", "author": "tests",
        "description": "colour cells (a test fixture, never flash it)",
        "touches": list(touches), "written_against": [], "provenance": []}))
    pf = tmp_path / "p.ffproj.json"
    p = {"schema": 1, "base_sha256": hashlib.sha256(stock).hexdigest(),
         "build_name": "Jayco B-Van V13 colour cell test",
         "ops": list(ops) + [{"id": "cells", "type": "user:cells", "params": {}}]}
    pf.write_text(json.dumps(p))
    proj = recipe.Project.from_json(pf.read_text())
    return pf, proj


def _compile(pf, proj, stock):
    from fireflymcf import recipe
    with userop.registered(userop.load_user_ops(pf, proj)):
        return recipe.compile_project(proj, stock)


@pytest.fixture(scope="module")
def crowded(mcf_136):
    import hashlib
    import json
    from fireflymcf import recipe
    p = {"schema": 1, "base_sha256": hashlib.sha256(mcf_136).hexdigest(),
         "build_name": "Jayco B-Van V13 colour cell test", "ops": _crowded_ops()}
    res = recipe.compile_project(recipe.Project.from_json(json.dumps(p)), mcf_136)
    assert res.ok, res.errors
    return res.data


@pytest.mark.slow
@pytest.mark.parametrize("batched", [True, False])
def test_four_cells_on_a_crowded_build(gauges, mcf_136, crowded, tmp_path, batched):
    """Four gauge outlines on a build whose tail headroom is below one table grow compile and
    pass every structural gate, in one batched call and in four single calls."""
    from fireflymcf import coverage
    # Counted the old way, as one trailing object running to GuiData's checksum, the bytes
    # after the last object leave less headroom than one grow of the table needs.
    gs, ge = guidata.guidata_extent(crowded)
    _a, _b, ptrs = coverage._pointer_tables(crowded, gs, ge - 4)[-1]
    distinct = sorted(set(ptrs))
    largest = max(b - a for a, b in zip(distinct, distinct[1:]))
    _cb, _tb, rows = readout.resolve_cell_tables(crowded)
    assert largest - (ge - 4 - distinct[-1]) < 5 * rows
    room = coverage.analyse(crowded, deep=True).tail_headroom
    objs = [gauges[i] for i in (0, 1, 2, 18)]
    pf, proj = _cells_project(tmp_path, mcf_136, objs, batched, ops=_crowded_ops())
    res = _compile(pf, proj, mcf_136)
    assert res.ok, res.errors
    for oi in objs:
        assert any(r == "fillA" and m == 7 for r, m, _v in _recs(res.data, oi)[0].colors)
    after = coverage.analyse(res.data, deep=True)
    assert after.unclaimed == [] and after.tail_headroom == room


def test_own_colour_cell_needs_guicode_declared(gauges, mcf_136, tmp_path):
    """The grow repoints GuiCode literals: an op that declares only guidata is refused."""
    pf, proj = _cells_project(tmp_path, mcf_136, [gauges[0]], True, touches=("guidata",))
    res = _compile(pf, proj, mcf_136)
    assert not res.ok
    [err] = res.errors
    assert err["code"] == "USER_OP_UNDECLARED_TOUCH" and "guicode" in err["message"]
