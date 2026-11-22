"""`reattach_control`: a control attached over a switched card is re-attached inside the card's
arms, drawn exactly where it was, and is no longer painted over by the card's live redraw.

`add_button`'s default spot on 13.6 (HOME, 435,404) attaches the new button to the HOME root
AFTER the type-6 switch at the root's record 9 (`value_slot` 6), whose arms 901-904 are the
battery card: the shape the 2026-11-22 emulation found wiped by the card's re-draw
(`repaintcheck`).
"""
import hashlib

import pytest

from fireflymcf import guidata, pagewalk, recipe, recipe_ops, records, render, repaintcheck

HOME, SWITCH_REC, ARMS = 4, 9, [901, 902, 903, 904]
BUTTON = {"channel": 15, "label": "Pin 15", "off_colour": [65, 81, 163]}
INTO = {"switch": {"object": HOME, "record": SWITCH_REC, "slot": 6}}


def _compile(stock, *extra):
    ops = [recipe.OpSpec(id="b", type="add_button", params=dict(BUTTON))]
    ops += [recipe.OpSpec(id=i, type=t, params=p) for i, t, p in extra]
    p = recipe.Project(base_sha256=hashlib.sha256(stock).hexdigest(), build_name=None, ops=ops)
    return recipe.compile_project(p, stock)


@pytest.fixture(scope="module")
def built(mcf_136):
    plain = _compile(mcf_136)
    moved = _compile(mcf_136, ("r", "reattach_control", {"controls": [{"ref": "b"}],
                                                          "into": INTO}))
    assert plain.ok, plain.errors
    assert moved.ok, moved.errors
    return plain, moved


def _attaching(data, child):
    gs, _ = guidata.guidata_extent(data)
    tab = guidata.object_tables(data)[1]
    out = []
    for o in range(len(tab)):
        try:
            recs = records.object_records(data, gs + tab[o])
        except (IndexError, ValueError):
            continue
        out += [(o, i) for i, r in enumerate(recs) if r.wtype == 5 and r.child_index == child]
    return out


def _button(data):
    gs, _ = guidata.guidata_extent(data)
    tab = guidata.object_tables(data)[1]
    return len(tab) - 1          # add_button appends the button as the top object


def test_the_attach_moves_from_the_root_into_every_arm(built):
    plain, moved = built
    child = _button(plain.data)
    assert _attaching(plain.data, child) == [(HOME, SWITCH_REC + 1)]
    where = _attaching(moved.data, child)
    assert sorted(o for o, _ in where) == ARMS
    assert moved.manifest[-1] == ("reattached object(s) [%d] from object %d into %s"
                                  % (child, HOME, ARMS))


@pytest.mark.parametrize("value", [0, 1, 2, 3])
def test_every_arm_draws_home_pixel_for_pixel_as_before(built, value):
    plain, moved = built
    sv = {901: value}
    a = render.render_screen_image(plain.data, HOME, switch_values=sv)
    b = render.render_screen_image(moved.data, HOME, switch_values=sv)
    assert a.tobytes() == b.tobytes()


@pytest.mark.parametrize("value", [0, 1, 2, 3])
def test_the_card_no_longer_redraws_over_the_button(built, value):
    """Before: the root switch (record 9) re-draws over the button. After: no unit outside the
    button's own arm does that it did not before; and the switch itself never does."""
    plain, moved = built
    child = _button(plain.data)

    def found(data):
        walk = pagewalk.walk_records(data, HOME, switch_values={901: value})
        return repaintcheck.overlaps(walk, lambda v: any(o == child for o, _ in v.key))

    before, after = found(plain.data), found(moved.data)
    assert any(o.unit.key == ((HOME, SWITCH_REC),) for o in before)
    assert not any(o.unit.key == ((HOME, SWITCH_REC),) for o in after)
    sig = {(o.unit.obj, o.unit.index, o.victim.box) for o in before}
    assert all((o.unit.obj, o.unit.index, o.victim.box) in sig for o in after)


def test_the_handle_records_the_move(built):
    plain, moved = built
    # the compile result exposes no ctx; re-apply the op on the plain build to read its handle
    ctx = recipe.Ctx(stock=plain.data, edits=[], insertions=[], handles={"b": None})
    op = recipe.REGISTRY["reattach_control"](recipe.OpSpec(
        id="r", type="reattach_control",
        params={"controls": [_button(plain.data)], "into": INTO}))
    op.validate()
    out = op.apply(plain.data, ctx)
    h = ctx.handles["r"]
    assert h["from"] == HOME and h["into"] == ARMS and h["dropped"] == []
    assert ctx.reattached == {(HOME, _button(plain.data)): ARMS}
    assert op.verify(out, ctx).startswith("reattached object(s)")


def _refused(mcf_136, params):
    r = _compile(mcf_136, ("r", "reattach_control", {"controls": [{"ref": "b"}], **params}))
    assert not r.ok
    return r.errors


def _codes(errors):
    """Each error as text (the compile result's errors carry the op's code in their text)."""
    return {str(e) for e in errors}


def test_a_single_arm_is_refused_unless_drops_are_accepted(mcf_136):
    one = {"switch": dict(INTO["switch"]), "arms": [1]}
    errs = _refused(mcf_136, {"into": one})
    assert any("CONTROL_DROPPED" in s for s in _codes(errs))
    r = _compile(mcf_136, ("r", "reattach_control", {"controls": [{"ref": "b"}], "into": one,
                                                      "drop_where_absent": True}))
    assert r.ok, r.errors
    assert sorted(o for o, _ in _attaching(r.data, _button(r.data))) == [902]
    assert "no longer drawn in" in r.manifest[-1]


@pytest.mark.parametrize("params,code", [
    ({"into": {"switch": {"object": HOME, "record": SWITCH_REC, "slot": 5}}}, "NOT_A_SWITCH"),
    ({"into": {"switch": {"object": HOME, "record": 10}}}, "NOT_A_SWITCH"),
    ({"into": {"switch": {"object": HOME, "record": SWITCH_REC}, "arms": [9]}}, "NOT_A_SWITCH"),
    ({"into": [HOME]}, "BAD_PARAMS"),
    ({"into": [902, 902]}, "BAD_PARAMS"),
    ({"into": ARMS, "from": 5}, "NOT_ATTACHED"),
])
def test_bad_destinations_are_refused(mcf_136, params, code):
    assert any(code in s for s in _codes(_refused(mcf_136, params)))


def test_a_destination_walked_before_the_card_is_refused(mcf_136):
    """Every arm of the TANK card switch (record 6) is walked before the battery card: the
    button would inherit another font and colour there (CONTROL_CHANGED), and the battery card
    would then paint over it (Z_ORDER_CHANGED)."""
    errs = _refused(mcf_136, {"into": {"switch": {"object": HOME, "record": 6, "slot": 5}}})
    assert any("CONTROL_CHANGED" in s or "Z_ORDER_CHANGED" in s for s in _codes(errs)), errs


def test_the_stacking_gate_can_fail(mcf_136, monkeypatch):
    """Wiring check: a change in what is drawn over the control is refused (`_covers` stubbed
    to report one more covering record after the move)."""
    real = recipe_ops.ReattachControl._covers
    calls = []

    def fake(walk, ctrl):
        out = real(walk, ctrl)
        calls.append(1)
        if len(calls) % 2 == 0 and out:
            k = next(iter(out))
            out = {**out, k: out[k] | {(33, None, (0, 0, 1, 1))}}
        return out

    monkeypatch.setattr(recipe_ops.ReattachControl, "_covers", staticmethod(fake))
    errs = _refused(mcf_136, {"into": INTO})
    assert any("Z_ORDER_CHANGED" in s for s in _codes(errs)), errs


def test_the_overlap_gate_can_fail(mcf_136, monkeypatch):
    """Wiring check: an overlap the move adds is refused (`repaintcheck.overlaps` stubbed to
    report the control under a unit only after the move)."""
    real = repaintcheck.overlaps
    calls = []

    def fake(walk, victims=None):
        found = real(walk, victims)
        calls.append(1)
        if len(calls) % 2 == 0:                   # the after-walk of each pair
            mine = [r for r in walk if victims is None or victims(r)]
            paint = [r for r in mine if repaintcheck.paints(r)]
            if paint:
                found = found + [repaintcheck.Overlap(walk[0], paint[0])]
        return found

    monkeypatch.setattr(repaintcheck, "overlaps", fake)
    errs = _refused(mcf_136, {"into": INTO})
    assert any("REPAINT_OVERLAP" in s for s in _codes(errs)), errs


def _w(key, wtype=33, box=(0, 0, 10, 10), slot=None):
    return pagewalk.WalkRec(key=key, wtype=wtype, label=None, obj=key[-1][0],
                            index=key[-1][1], box=box, font=None, align=0, colors=(),
                            value_slot=slot, in_regs=(), out_regs=())


def test_overlaps_rule():
    sw = _w(((1, 0),), wtype=6, box=(0, 0, 0, 0), slot=6)
    card = _w(((1, 0), (2, 0)), box=(0, 0, 100, 100))
    inside = _w(((1, 0), (2, 1)), box=(10, 10, 20, 20))
    over = _w(((1, 1),), box=(50, 50, 60, 60))
    beside = _w(((1, 2),), box=(200, 0, 210, 10))
    plain = _w(((1, 3),), wtype=5, box=(0, 0, 0, 0), slot=0)       # slot 0: not a unit
    walk = [sw, card, inside, over, beside, plain]
    got = repaintcheck.overlaps(walk)
    assert [(o.unit.key, o.victim.key) for o in got] == [(sw.key, over.key)]
    assert repaintcheck.overlaps(walk, lambda v: v is beside) == []
    assert not repaintcheck.is_unit(plain)
    assert repaintcheck.is_unit(_w(((1, 4),), wtype=5, slot=209))


def test_a_copy_placed_in_another_frame_is_moved_back(mcf_136, monkeypatch):
    """A destination whose frame differs from the source's places the byte copy elsewhere; the
    op measures the shift and corrects the copy's x/y. Simulated by planting every copy 7 px
    right and 5 px down: the build must still draw HOME exactly as without the op."""
    import struct

    from fireflymcf import insert, patch
    real = insert.append_record

    def planted(data, parent, record_bytes):
        out, edits = real(data, parent, record_bytes)
        gs, _ = guidata.guidata_extent(out)
        rec = records.object_records(out, gs + guidata.object_tables(out)[1][parent])[-1]
        buf = bytearray(out)
        for gi, d in ((0, 7), (1, 5)):
            off = records.geom_offset(out, rec, gi)
            struct.pack_into("<H", buf, off, (struct.unpack_from("<H", buf, off)[0] + d) & 0xFFFF)
        out, _ = patch.reseal(bytes(buf))
        return out, edits

    monkeypatch.setattr(insert, "append_record", planted)
    r = _compile(mcf_136, ("r", "reattach_control", {"controls": [{"ref": "b"}], "into": INTO}))
    assert r.ok, r.errors
    plain = _compile(mcf_136)
    for value in range(4):
        sv = {901: value}
        assert render.render_screen_image(plain.data, HOME, switch_values=sv).tobytes() == \
            render.render_screen_image(r.data, HOME, switch_values=sv).tobytes()
