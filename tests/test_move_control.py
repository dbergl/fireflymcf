"""`move_control`: the compensation-target rule over the source page's per-arm deltas.

The tests that compile the op against the reference coach's Low Volts builds live in the
private research repository.
"""
import pytest

from fireflymcf import pagewalk, recipe, recipe_ops

SRC, DST, AES_SETTINGS_ROOT = 1427, 1425, 31


def _delta(obj, index, dx, dy, root=5):
    rec = pagewalk.WalkRec(key=((root, 0), (obj, index)), wtype=33, label=None, obj=obj,
                           index=index, box=(0, 0, 10, 10), font=None, align=0, colors=(),
                           value_slot=None, in_regs=(), out_regs=())
    return pagewalk.Delta(rec.key, 33, None, "moved", rec, rec, dx, dy)


def test_arms_that_disagree_are_refused():
    with pytest.raises(recipe.RecipeError) as e:
        recipe_ops._compensation_target([[_delta(SRC, 8, 0, -29)], [_delta(SRC, 8, 0, -30)]],
                                        SRC)
    assert e.value.code == "ARM_DISAGREES"
    with pytest.raises(recipe.RecipeError) as e:
        recipe_ops._compensation_target([[_delta(SRC, 8, 0, -29)], [_delta(SRC, 9, 0, -29)]],
                                        SRC)
    assert e.value.code == "ARM_DISAGREES"
    assert recipe_ops._compensation_target([[_delta(SRC, 8, 0, -29)], [],
                                            [_delta(SRC, 8, 0, -29, root=39)]],
                                           SRC) == (8, 0, -29)
    assert recipe_ops._compensation_target([[], []], SRC) is None


UNSET = (("fillA", 2, None), ("fillB", 0, None))          # fillB never written on the path
CLEAR = (("fillA", 2, None), ("fillB", 5, None))          # fillB transparent


def _rec(wtype=11, colors=UNSET, font=15, align=1, slot=None):
    return pagewalk.WalkRec(key=((7, 0),), wtype=wtype, label=None, obj=7, index=0,
                            box=(0, 0, 10, 10), font=font, align=align, colors=colors,
                            value_slot=slot, in_regs=(), out_regs=())


def test_an_unwritten_colour_may_arrive_transparent():
    """The Bath Fan's labels walk CLIMATE with fillB never written and land on LIGHTS after a
    transparent one: neither paints a background, so it is not a restyle."""
    assert not recipe_ops._paste_restyled(_rec(colors=UNSET), _rec(colors=CLEAR), 101, 101)
    assert not recipe_ops._paste_restyled(_rec(colors=CLEAR), _rec(colors=CLEAR), 101, 101)


@pytest.mark.parametrize("got,src_slot,got_slot", [
    (_rec(colors=UNSET), 101, 101),                                       # transparent -> unset
    (_rec(colors=(("fillA", 2, None), ("fillB", 7, 8))), 101, 101),     # a painted background
    (_rec(colors=(("fillA", 7, 3), ("fillB", 5, None))), 101, 101),     # another ink
    (_rec(colors=CLEAR, font=14), 101, 101),
    (_rec(colors=CLEAR, align=2), 101, 101),
    (_rec(colors=CLEAR), 101, 0),                                         # another live cell
])
def test_a_drawn_record_is_restyled(got, src_slot, got_slot):
    assert recipe_ops._paste_restyled(_rec(colors=CLEAR), got, src_slot, got_slot)


@pytest.mark.parametrize("wtype", [5, 6, 0x2E, 12])
def test_a_record_that_draws_nothing_keeps_font_align_and_its_own_slot(wtype):
    """A switch's inherited colours (CLIMATE's fillA 7/3) and a touch region's inherited slot
    (CLIMATE's page attach carries 0) reach nothing; its own selector cell does."""
    src = _rec(wtype, colors=(("fillA", 7, 3),), slot=109)
    assert not recipe_ops._paste_restyled(src, _rec(wtype, colors=(("fillA", 2, None),),
                                                    slot=109), 0, None)
    assert recipe_ops._paste_restyled(src, _rec(wtype, slot=110), 0, 0)
    assert recipe_ops._paste_restyled(src, _rec(wtype, slot=109, font=14), 0, 0)
