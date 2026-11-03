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


def test_colour_state_is_shared_past_a_container(mcf_136):
    """GUICODE Finding 29: the decoder's colour state is one state for the whole walk -- a
    child's colour writes reach its container's later siblings (`FUN_00258330`'s snapshot is
    gated off, `0x257ac2 movs r2,#0`). On ELECTRICAL (root 5) 'Engine' (353:5) inherits its
    fillA. The last record to write fillA before it is the 'Stop' caption (251:2), white, inside
    the container 353:3 recursed into just before. The 13.6 panel in firefly-emulator draws
    'Engine' white. A walk that restored the colour slots after each container gave it the
    Stop button's idx 168 instead."""
    from fireflymcf import guidata, records
    walk = pagewalk.walk_records(mcf_136, 5)
    i = next(i for i, r in enumerate(walk) if (r.obj, r.index) == (353, 5))
    assert dict((role, (m, v)) for role, m, v in walk[i].colors)["fillA"] == (2, None)
    gs, tab = guidata.guidata_extent(mcf_136)[0], guidata.object_tables(mcf_136)[1]
    own = records.object_records(mcf_136, gs + tab[353])[5]
    assert dict((role, m) for role, m, _ in own.colors)["fillA"] == 0      # it inherits
    writer = max(j for j in range(i) if dict(walk[j].color_state)["fillA"]
                 != (dict(walk[j - 1].color_state)["fillA"] if j else None))
    assert (walk[writer].obj, walk[writer].index, walk[writer].label) == (251, 2, "Stop")
    assert (353, 3) in walk[writer].key, "the writer sits inside a container Engine follows"
