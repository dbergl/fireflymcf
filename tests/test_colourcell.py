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
    """A tick routine: red while the tank's level is at or below 10 % of its resolution
    (TANK_STATUS byte 1 / byte 2, the receive slot's low and high byte), the field's old colour
    otherwise. It writes the cell every tick, in both branches."""
    def build(code_load_addr, tail_target):
        r = thumb.Routine({"slot": slot, "cell": cell.addr, "red": RED,
                           "restore": cell.before_word}, error=routinekit.RoutineKitError)
        routinekit.emit_prologue(r)
        r.ldr_lit(0, "slot")
        r.emit(2, lambda h, s: (thumb.ldrh_imm(1, 0, 0), "ldrh r1, [r0]"))
        r.emit(2, lambda h, s: (thumb.lsls_imm(2, 1, 24), "lsls r2, r1, #0x18"))
        r.emit(2, lambda h, s: (thumb.lsrs_imm(2, 2, 24), "lsrs r2, r2, #0x18"))   # level
        r.emit(2, lambda h, s: (thumb.lsrs_imm(3, 1, 8), "lsrs r3, r1, #8"))       # resolution
        r.emit(2, lambda h, s: (thumb.movs_imm(0, 10), "movs r0, #0xa"))
        r.emit(2, lambda h, s: (thumb.muls(2, 0), "muls r2, r0, r2"))
        r.emit(2, lambda h, s: (thumb.cmp_reg(2, 3), "cmp r2, r3"))
        r.bcc("hi", "normal")                                   # 10 * level > resolution
        r.ldr_lit(1, "red")
        r.b("store")
        r.label("normal")
        r.ldr_lit(1, "restore")
        r.label("store")
        r.ldr_lit(0, "cell")
        r.emit(2, lambda h, s: (thumb.str_imm(1, 0, 0), "str r1, [r0]"))
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


def test_emulated_outline_is_red_only_when_low(gauges, mcf_136, tmp_path):
    pytest.importorskip("unicorn")
    from fireflymcf.emu.scenario import Scenario

    ed = userop.Editor(mcf_136)
    cell = ed.call("own_colour_cell", gauges[0], "border")
    reg = ed.call("resolve_register", dgn=TANK_STATUS, instance=0)
    routinekit.install_tick(ed, _alarm_build(reg.slot_addr, cell), "fresh low alarm")
    assert userop.replay(mcf_136, ed.ledger) == ed.data

    s = Scenario(ed.data, timeout_s=400)
    s.set_tank_raw(1, 63000)                                    # fresh empty at power-on
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
    for raw, low in ((None, True), (50, False), (63000, True)):
        if raw is not None:
            s.set_tank_raw(1, raw)
            until(lambda: (level() == 0) == low, 40)
        assert (level() == 0) == low
        img = s.screenshot(tmp_path / f"home-{len(shots)}.png")
        shots.append(img)
        mine = _band(img, boxes[gauges[0]])
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
