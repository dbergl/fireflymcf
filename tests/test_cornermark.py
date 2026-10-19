"""`cornermark.py` -- a state-gated mark in a button's corner.

The three properties worth pinning are the three the first cut got wrong or nearly wrong:

1. the arms are FLOW-NEUTRAL, so the switch cannot move the host's siblings;
2. the `0xFFFF` DEFAULT arm is at the BASE, so the mark draws on a no-match rather than nothing;
3. the mark is placed relative to the host's own origin, so it lands where the caller meant.

Property 1 has a deliberate negative test (`..._catches_an_absolute_mark`): it builds the arm the
broken way and asserts the layout re-walk sees the damage. This project's standing rule is "check
your tests can fail", and a flow-neutrality assertion that never sees a non-neutral arm is exactly
the kind that passes on an empty set.
"""
import hashlib
import struct
from pathlib import Path

import pytest

from fireflymcf import cornermark, guidata, layout, records, recipe

REPO = Path(__file__).resolve().parent.parent
PUMP_BUTTON = 295                    # HOME 'Water Pump' -- the shared child of 989/990
HOME_ROOT = 4
# What the walk needs to REACH the pump button: the coach's floorplan and the option slots the
# HOME quadrants gate on (`build/coach-wd.json` resolved through `options.slot_values`).
HOME_SLOTS = {5: 8, 6: 1, 37: 3, 112: 1, 113: 0}
MARK_CHAR = chr(141)


@pytest.fixture(scope="module")
def stock():
    return (REPO / "firefly-13.6" / "mcf.bin").read_bytes()


def _walk(data, cell, value):
    return list(layout.Screen(data).walk(HOME_ROOT, slot_values={**HOME_SLOTS, cell: value}))


def _placements(data, cell, value):
    return {(p.obj_index, p.record_offset, p.x0, p.y0) for p in _walk(data, cell, value)}


def test_the_records_self_decode(stock):
    mark = records.decode_record(cornermark.build_mark_record(80, 26, 20, 21, 13, MARK_CHAR), 0)
    assert mark.wtype == 11 and mark.label == MARK_CHAR and mark.font == 13
    assert mark.base_modes[:4] == [1, 1, 1, 1], "the mark must ride the host's flow origin"
    assert [layout._s16(mark.geom[0]), layout._s16(mark.geom[1])] == [80, 26]
    assert [mark.geom[2], mark.geom[3]] == [20, 21], \
        "a VISIBLE label needs a box; without one the panel draws a single column (v20)"

    empty = records.decode_record(cornermark.build_empty_record(-80, -26), 0)
    assert empty.wtype == 11 and (empty.label or "") == ""
    assert [layout._s16(empty.geom[0]), layout._s16(empty.geom[1])] == [-80, -26]

    sw = records.decode_record(cornermark.build_switch_record(217, 1433), 0)
    assert sw.wtype == 6 and sw.value_slot == 217 and sw.child_index == 1433 and sw.count == 2


def test_a_box_of_zero_is_refused():
    """The v20 defect at its source. The mark record was copied from `_flow_anchor`, which
    declares no width or height because it never draws; the panel clipped the glyph to that
    degenerate box and drew one column of it."""
    for w, h in ((0, 21), (20, 0), (-1, 21)):
        with pytest.raises(cornermark.CornerMarkError):
            cornermark.build_mark_record(80, 26, w, h, 13, MARK_CHAR)


def test_the_placed_box_is_big_enough_for_the_glyph(stock):
    """What `AddCornerMark.verify` now asserts, at the layout level: the box the mark is PLACED
    in must be at least as big as the glyph going into it. v20 placed it in (571,218)-(571,218)
    and the re-walk, which only checked the origin, passed anyway."""
    out, info = cornermark.add_mark(stock, object_index=PUMP_BUTTON, value_slot=217,
                                    dx=80, dy=26, w=20, h=21, font_slot=13, char=MARK_CHAR)
    placed = [p for p in _walk(out, 217, 900) if p.obj_index == info["mark_object"]
              and p.label == MARK_CHAR]
    assert len(placed) == 1
    p0 = placed[0]
    assert (p0.x1 - p0.x0, p0.y1 - p0.y0) == (20, 21), \
        f"the mark is placed in a {p0.x1 - p0.x0}x{p0.y1 - p0.y0} box"


def test_a_non_single_byte_char_is_refused():
    with pytest.raises(cornermark.CornerMarkError):
        cornermark.build_mark_record(0, 0, 20, 21, 13, "ab")
    with pytest.raises(cornermark.CornerMarkError):
        cornermark.build_mark_record(0, 0, 20, 21, 13, "✓")     # a checkmark: two bytes, no glyph


def test_the_default_arm_is_the_base(stock):
    """The firmware renders the base on a no-match only when `match[base] == 0xFFFF`, and renders
    NOTHING otherwise (`layout._resolve_switch`). The gate here is "anything but 0", so the mark
    has to BE the default; built as `[0, 0xFFFF]` it would never appear."""
    assert cornermark.MATCH_VALUES[0] == 0xFFFF
    out, base = cornermark.append_arms(stock, dx=80, dy=26, w=20, h=21, font_slot=13,
                                       char=MARK_CHAR)
    gs, _ = guidata.guidata_extent(out)
    mt = guidata.find_match_table(out)
    mvs = [struct.unpack_from("<H", out, gs + mt + i * 2)[0] for i in (base, base + 1)]
    assert mvs == [0xFFFF, 0]
    assert records.object_records(out, gs + guidata.object_tables(out)[1][base])[0].label \
        == MARK_CHAR, "the base arm must be the one that draws"


def test_the_switch_lands_last_and_leaves_the_host_records_alone(stock):
    gs, _ = guidata.guidata_extent(stock)
    before = records.object_records(stock, gs + guidata.object_tables(stock)[1][PUMP_BUTTON])
    out, info = cornermark.add_mark(stock, object_index=PUMP_BUTTON, value_slot=217,
                                    dx=80, dy=26, w=20, h=21, font_slot=13, char=MARK_CHAR)
    gs2, _ = guidata.guidata_extent(out)
    after = records.object_records(out, gs2 + guidata.object_tables(out)[1][PUMP_BUTTON])
    assert [r.wtype for r in after] == [r.wtype for r in before] + [6]
    assert after[-1].value_slot == 217 and after[-1].child_index == info["arms_base"]


def test_a_second_mark_on_the_same_cell_is_refused(stock):
    out, _ = cornermark.add_mark(stock, object_index=PUMP_BUTTON, value_slot=217,
                                 dx=80, dy=26, w=20, h=21, font_slot=13, char=MARK_CHAR)
    with pytest.raises(cornermark.CornerMarkError):
        cornermark.add_mark(out, object_index=PUMP_BUTTON, value_slot=217,
                            dx=80, dy=26, w=20, h=21, font_slot=13, char=MARK_CHAR)


def test_the_arms_are_flow_neutral_so_nothing_else_moves(stock):
    """Toggling the gate may change ONLY the mark's own placements.

    The host is `295`, a shared child: HOME's `Tank Heater` is the PARENT's next record, and the
    flow a child leaves carries back out to it. That is how the first build of this broke.
    """
    out, info = cornermark.add_mark(stock, object_index=PUMP_BUTTON, value_slot=217,
                                    dx=80, dy=26, w=20, h=21, font_slot=13, char=MARK_CHAR)
    idle, running = _placements(out, 217, 0), _placements(out, 217, 900)
    arms = {info["mark_object"], info["empty_object"]}
    moved = {(o, r) for o, r, _, _ in idle ^ running} - {(o, r) for o, r, _, _ in idle | running
                                                         if o in arms}
    assert not moved, f"toggling the gate moved placements outside the arms: {sorted(moved)[:6]}"

    # and the specific sibling that caught it, by name, in both states
    for value in (0, 900):
        heater = [p for p in _walk(out, 217, value) if p.label == "Tank\nHeater"]
        assert len(heater) == 1 and (heater[0].x0, heater[0].y0) == (631, 192), \
            f"Tank Heater is at {(heater[0].x0, heater[0].y0)} with the gate at {value}"


def test_the_flow_neutrality_check_catches_an_absolute_mark(stock):
    """The negative. Build the arms the broken way -- a single absolutely-placed label with no
    flow restore, which is exactly what the first cut emitted -- and confirm the re-walk above
    actually sees the damage.

    Without this, `test_the_arms_are_flow_neutral_so_nothing_else_moves` would pass just as
    happily on a mark that cannot move anything because it is never drawn. Assembled here from
    the same primitives `add_mark` uses rather than by monkeypatching a record builder: the
    restore record is half the fix, so a patch that leaves it in place accidentally repairs the
    defect (`571 - 80, 218 - 26` is the very origin it was meant to lose).
    """
    from fireflymcf import insert, relocate

    def absolute_label(x, y, text=MARK_CHAR):
        raw = text.encode("latin-1")
        rec = (bytes([0x0B, 0x50, 0x03, 0x00, 0x00, 0x00, 0x00, 0x00])
               + struct.pack("<hh", x, y)
               + bytes([0x01, 13, 0xFF, 0x02, 0x02, 0x0A, 0x00])
               + bytes([len(raw)]) + raw + b"\x00")
        assert records.decode_record(rec, 0).base_modes[:2] == [0, 0]
        return rec

    def obj(parts):
        payload = bytes([len(parts)]) + b"".join(parts)
        return payload + b"\x00" * (-len(payload) % 4)

    out, _ = relocate.grow_match_table(stock, cornermark.MATCH_VALUES)
    out, _, base = insert._append_object(out, obj([absolute_label(571, 218)]))
    out, _, second = insert._append_object(out, obj([absolute_label(0, 0, "")]))
    assert second == base + 1
    gs, _ = guidata.guidata_extent(out)
    recs = records.object_records(out, gs + guidata.object_tables(out)[1][PUMP_BUTTON])
    stream = [out[r.offset:r.offset + r.size] for r in recs]
    stream.append(cornermark.build_switch_record(217, base))
    out, _ = relocate.replace_object_records(out, PUMP_BUTTON, obj(stream))

    idle, running = _placements(out, 217, 0), _placements(out, 217, 900)
    arms = {base, base + 1}
    moved = {(o, r) for o, r, _, _ in idle ^ running} - {(o, r) for o, r, _, _ in idle | running
                                                         if o in arms}
    assert moved, "the broken arms moved nothing -- the neutrality check cannot fail, so it is " \
                  "not testing anything"
    heater = [p for p in _walk(out, 217, 900) if p.label == "Tank\nHeater"]
    assert heater and (heater[0].x0, heater[0].y0) != (631, 192), \
        "the absolutely-placed mark did not drag Tank Heater, so the defect is not reproduced"


def test_the_op_refuses_a_character_with_no_glyph(stock):
    """`add_corner_mark` draws a character; it does not install one. Pointed at a code point the
    font falls back on, it must refuse rather than ship the placeholder block in a corner."""
    p = recipe.Project(base_sha256=hashlib.sha256(stock).hexdigest(), build_name=None,
                       ops=[recipe.OpSpec(id="m", type="add_corner_mark",
                                          params={"object": PUMP_BUTTON, "value_slot": 217,
                                                  "dx": 80, "dy": 26, "page_root": HOME_ROOT,
                                                  "expect": [571, 218], "font": 13,
                                                  "char": MARK_CHAR,
                                                  "preview_slots": {str(k): v for k, v
                                                                    in HOME_SLOTS.items()}})])
    r = recipe.compile_project(p, stock)
    assert not r.ok and r.errors[0]["code"] == "NO_GLYPH", r.errors
