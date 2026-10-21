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
    {"toggle": {"off_payload": TAP.hex(), "on_payload": HOLD.hex()}},           # with tap
    {"toggle": {"off_payload": TAP.hex(), "on_payload": HOLD.hex()}, "tap": None,
     "hold": {"payload": HOLD.hex(), "seconds": 2}},
    {"toggle": {"off_payload": TAP.hex(), "on_payload": HOLD.hex()}, "tap": None,
     "group_pool": "free"},
    {"toggle": {"off_payload": TAP.hex()}, "tap": None},
    {"toggle": {"off_payload": TAP.hex(), "on_payload": "07f6"}, "tap": None},
    {"toggle": "on", "tap": None},
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


def test_toggle_constructs():
    op = recipe.REGISTRY["add_rvc_button"](_spec(
        tap=None, toggle={"off_payload": TAP.hex(), "on_payload": HOLD.hex()}))
    assert op.form == "toggle" and op.group_pool == "hold_safe"
    assert (op.off, op.on) == (TAP, HOLD)


# --- `recipe._check_toggles_driven`: a static check over the enabled specs ----------------------

def _toggle(oid="eco", enabled=True):
    return recipe.OpSpec(id=oid, type="add_rvc_button", enabled=enabled,
                         params={"toggle": {"off_payload": TAP.hex(), "on_payload": HOLD.hex()}})


def _lights(*refs, oid="st", enabled=True):
    return recipe.OpSpec(id=oid, type="add_button_status", enabled=enabled,
                         params={"buttons": [{"button": {"ref": r}} for r in refs]})


def _undriven(specs):
    errs = recipe._check_toggles_driven(specs)
    assert all(e.code == "TOGGLE_UNDRIVEN" for e in errs)
    return [e.op_id for e in errs]


def test_check_driven_passes():
    assert _undriven([_toggle(), _lights("other", "eco")]) == []
    assert _undriven([recipe.OpSpec(id="b", type="add_rvc_button",
                                    params={"tap": {"payload": TAP.hex()}})]) == []


def test_check_undriven():
    assert _undriven([_toggle()]) == ["eco"]
    assert _undriven([_toggle("a"), _toggle("b"), _lights("b")]) == ["a"]


def test_check_disabled_status_does_not_count():
    assert _undriven([_toggle(), _lights("eco", enabled=False)]) == ["eco"]
    assert _undriven([_toggle(enabled=False)]) == []


def test_check_status_before_does_not_count():
    assert _undriven([_lights("eco"), _toggle()]) == ["eco"]


def test_check_tolerates_malformed_status():
    bad = recipe.OpSpec(id="st", type="add_button_status",
                        params={"buttons": ["x", {"button": "eco"}, {}]})
    assert _undriven([_toggle(), bad]) == ["eco"]
    assert _undriven([_toggle(), recipe.OpSpec(id="st", type="add_button_status",
                                               params={"buttons": 3})]) == ["eco"]
