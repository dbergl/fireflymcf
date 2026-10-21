"""`rvcbutton.bindings` and `add_rvc_button`'s parameter validation: no image needed."""
import pytest

from fireflymcf import newbutton, recipe, rvcbutton

DGN = 0x1FEF9
TAP = bytes.fromhex("07f6ffffffffffff")
HOLD = bytes.fromhex("07f7ffffffffffff")


def _split(blob):
    assert len(blob) % 12 == 0
    return [blob[i:i + 12] for i in range(0, len(blob), 12)]


def test_tap_binding():
    (b,) = _split(rvcbutton.bindings(DGN, form="tap", tap=TAP, hold=None, off=None, on=None))
    assert b == newbutton.binding_raw(DGN, TAP, gesture_mask=0x06)
    assert newbutton.binding_wire_frame(b) == (0x19FEF99F, TAP)


def test_tap_hold_bindings_hold_first():
    """Ruling R1: the hold binding, then the tap (Batt Heat's enable, off order)."""
    hold, tap = _split(rvcbutton.bindings(DGN, form="tap_hold", tap=TAP, hold=HOLD,
                                          off=None, on=None))
    assert hold == newbutton.binding_raw(DGN, HOLD, gesture_mask=0x04)
    assert tap == newbutton.binding_raw(DGN, TAP, gesture_mask=0x02)


def test_hold_binding():
    (b,) = _split(rvcbutton.bindings(DGN, form="hold", tap=None, hold=HOLD, off=None, on=None))
    assert b[3] == 0x04 and not b[2] & 0x04


def test_toggle_bindings():
    on, off = _split(rvcbutton.bindings(DGN, form="toggle", tap=None, hold=None,
                                        off=TAP, on=HOLD))
    assert on == newbutton.binding_raw(DGN, HOLD, gesture_mask=0x06, variant=0)
    assert off == newbutton.binding_raw(DGN, TAP, gesture_mask=0x06, variant=1)


@pytest.mark.parametrize("kw", [
    {"form": "tap", "tap": TAP[:7]},
    {"form": "tap", "tap": None},
    {"form": "tap", "tap": TAP, "hold": HOLD},
    {"form": "hold", "hold": None},
    {"form": "tap_hold", "tap": TAP},
    {"form": "toggle", "on": TAP},
    {"form": "spin", "tap": TAP},
])
def test_bindings_refuse(kw):
    args = {"tap": None, "hold": None, "off": None, "on": None} | kw
    with pytest.raises(rvcbutton.RvcButtonError):
        rvcbutton.bindings(DGN, **args)


def test_bindings_refuse_a_wide_dgn():
    with pytest.raises(rvcbutton.RvcButtonError):
        rvcbutton.bindings(0x40000, form="tap", tap=TAP, hold=None, off=None, on=None)


def _spec(**over):
    params = {"parent": 4, "x": 210, "y": 332, "w": 80, "h": 44, "text": "Go", "dgn": DGN,
              "tap": {"payload": TAP.hex()}}
    params.update(over)
    return recipe.OpSpec(id="btn", type="add_rvc_button",
                         params={k: v for k, v in params.items() if v is not ...})


def _code(**over):
    with pytest.raises(recipe.RecipeError) as exc:
        recipe.REGISTRY["add_rvc_button"](_spec(**over))
    return exc.value.code


def test_valid_params_construct():
    op = recipe.REGISTRY["add_rvc_button"](_spec())
    assert op.hardware_verified is False and op.group_pool == "free"
    op = recipe.REGISTRY["add_rvc_button"](_spec(hold={"payload": HOLD.hex(), "seconds": 2}))
    assert op.group_pool == "hold_safe"


@pytest.mark.parametrize("over", [
    {"tap": None},                                               # no gesture
    {"tap": ...},
    {"tap": {"payload": "07f6ffffffffff"}},                      # 7 bytes
    {"tap": {"payload": "zz"}},
    {"tap": {"payload": TAP.hex(), "extra": 1}},
    {"hold": {"payload": HOLD.hex(), "seconds": 0}},
    {"hold": {"payload": HOLD.hex(), "seconds": 16}},
    {"hold": {"payload": HOLD.hex()}},
    {"hold": {"payload": HOLD.hex(), "seconds": 3}, "group_pool": "free"},
    {"group_pool": "anywhere"},
    {"toggle": {"off_payload": TAP.hex(), "on_payload": HOLD.hex()}, "tap": None},
    {"dgn": 0x40000},
    {"w": 0},
    {"colour": [1, 2]},
    {"glyph": {"slot": 24}},
    {"glyph": {"slot": 24, "char": "YY"}},
    {"font": 0xFF},
    {"text": "café"},
])
def test_bad_params(over):
    assert _code(**over) == "BAD_PARAMS"


@pytest.mark.parametrize("over", [{"x": -1}, {"x": 760}, {"y": 450}])
def test_off_canvas(over):
    assert _code(**over) == "OFF_CANVAS"
