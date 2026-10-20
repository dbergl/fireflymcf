"""`pagewalk`: keyed page walks (containers included) and their diff.

Measured on the Low Volts hold image (`lv_hold`: `builds/mcf-13.6-low-volts-move.ffproj.json`
without its last three ops): the compact AES ELECTRICAL card is object 1427, drawn by the two
ELECTRICAL roots 5 and 39, and its AES gear is the card's second wtype-33 box (the selector the
recipe's `lv-gear` op uses).
"""
import struct

import pytest

from fireflymcf import guidata, layout, pagewalk, records
from fireflymcf.recipe import OpSpec
from tests import lvbuilds

NEEDS_RESEARCH_DATA = "builds/mcf-13.6-low-volts-move.ffproj.json against firefly-13.6/mcf.bin"

GEAR = {"wtype": 33, "nth": 1}
# The gear is record 10 of 1427 (its second wtype-33 record; record 1 is the Low Volts box).
# Its absolute box starts at (248, 400); (110, 134) is its offset from the card attach,
# record 0, at (138, 266).
GEAR_INDEX = 10


def _gear_y_field(data: bytes) -> int:
    gs, _ = guidata.guidata_extent(data)
    tab = guidata.object_tables(data)[1]
    gear = [r for r in records.object_records(data, gs + tab[1427]) if r.wtype == 33][1]
    return struct.unpack_from("<H", data, records.geom_offset(data, gear, 1))[0]


@pytest.mark.parametrize("root", [5, 39])
def test_walk_has_the_gear_with_its_box(lv_hold, root):
    recs = pagewalk.walk_records(lv_hold, root)
    gear = [r for r in recs if r.obj == 1427 and r.index == GEAR_INDEX]
    assert len(gear) == 1 and gear[0].wtype == 33
    assert gear[0].box[:2] == (248, 400)


def test_switch_arm_sets_never_repeat_the_base_walk(lv_hold):
    base = [r.key for r in pagewalk.walk_records(lv_hold, 5)]
    for s in pagewalk.switch_arm_sets(lv_hold, [5])[1:]:
        assert [r.key for r in pagewalk.walk_records(lv_hold, 5, switch_values=s)] != base, s


def test_walk_lists_containers(lv_hold):
    recs = pagewalk.walk_records(lv_hold, 5)
    assert any(r.obj == 1427 and r.wtype == 5 and r.label is None for r in recs)


def test_keys_are_unique_and_end_at_the_record(lv_hold):
    recs = pagewalk.walk_records(lv_hold, 5)
    assert len({r.key for r in recs}) == len(recs)
    assert all(r.key[0][0] == 5 and r.key[-1] == (r.obj, r.index) for r in recs)


def test_reaching_roots_of_the_compact_card(lv_hold):
    assert pagewalk.reaching_roots(lv_hold, 1427) == [5, 39]


def test_switch_arm_sets_start_at_base_and_set_one_switch_each(lv_hold):
    sets = pagewalk.switch_arm_sets(lv_hold, [5])
    assert sets[0] == {} and len(sets) > 1
    assert all(len(s) == 1 for s in sets[1:])
    assert len({tuple(s.items()) for s in sets}) == len(sets)


def test_identical_walks_diff_empty(lv_hold):
    a = pagewalk.walk_records(lv_hold, 5)
    assert pagewalk.diff(a, pagewalk.walk_records(lv_hold, 5)) == []


def test_a_move_element_is_one_moved_delta(lv_hold):
    y = _gear_y_field(lv_hold)
    out = lvbuilds.image(lvbuilds.LOW_VOLTS_MOVE, OpSpec(
        "gear-y", "move_element",
        {"target": 1427, "selector": GEAR, "set": {"y": y + 1}, "expect": {"y": y}}),
        drop=lvbuilds.HOLD_DROP)
    d = pagewalk.diff(pagewalk.walk_records(lv_hold, 5), pagewalk.walk_records(out, 5))
    assert d and d[0].kind == "moved" and (d[0].dx, d[0].dy) == (0, 1)
    assert d[0].before.obj == 1427 and d[0].before.wtype == 33
    assert "moved" in pagewalk.describe(d).splitlines()[0]


def test_diff_ignores_and_remaps():
    rec = pagewalk.WalkRec(key=((5, 0), (9, 2)), wtype=11, label="A", obj=9, index=2,
                           box=(0, 0, 10, 10), font=1, align=0, colors=(), value_slot=None,
                           in_regs=(), out_regs=())
    moved = pagewalk.WalkRec(**{**rec.__dict__, "key": ((5, 0), (9, 1)), "index": 1,
                                "box": (2, 3, 12, 13)})
    styled = pagewalk.WalkRec(**{**rec.__dict__, "font": 2})
    assert [(d.kind, d.dx, d.dy) for d in pagewalk.diff(
        [rec], [moved], remap={rec.key: moved.key})] == [("moved", 2, 3)]
    assert [d.kind for d in pagewalk.diff([rec], [styled])] == ["state"]
    assert [d.kind for d in pagewalk.diff([rec], [moved])] == ["gone", "appeared"]
    assert pagewalk.diff([rec], [], remap={rec.key: None}) == []
    assert pagewalk.diff([rec], [moved], ignore=frozenset({rec.key, moved.key})) == []


def test_remap_drops_removed_and_shifts_later():
    assert pagewalk.remap_after_removal(((5, 0), (1427, 7)), 1427, {3, 4}) == ((5, 0), (1427, 5))
    assert pagewalk.remap_after_removal(((5, 0), (1427, 3), (285, 0)), 1427, {3}) is None


def test_trace_off_leaves_walk_unchanged(lv_hold):
    a = layout.Screen(lv_hold).walk(5)
    b = layout.Screen(lv_hold)
    b.walk(5, trace=[])
    assert [p.__dict__ for p in a] == [p.__dict__ for p in b.placed]
