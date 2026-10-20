"""`pagewalk`: the walk diff and the key remap after a record removal.

The tests that walk the reference coach's Low Volts builds live in the private research
repository.
"""
from fireflymcf import pagewalk


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
