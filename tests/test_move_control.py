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
