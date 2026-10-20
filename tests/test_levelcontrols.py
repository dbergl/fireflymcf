import pytest

from fireflymcf import levelcontrols, newbutton

EXPECT = {
    "minus": [(0x18EFFF9F, bytes.fromhex("4C5604FFFFFFFFFF"))],
    "plus": [(0x18EFFF9F, bytes.fromhex("4C5603FFFFFFFFFF"))],
    "zero": [(0x19FDE89F, bytes.fromhex("00FFFFFFFFFDFFFF")),
             (0x19FDEA9F, bytes.fromhex("00FFFFFFFFFDFFFF"))],
}


def test_frames_constant_is_the_spec_table():
    assert levelcontrols.FRAMES == EXPECT


@pytest.mark.parametrize("control", ["minus", "plus", "zero"])
def test_bindings_put_exactly_the_spec_frames_on_the_wire(control):
    frames = [newbutton.binding_wire_frame(b) for b in levelcontrols.bindings(control)]
    assert frames == EXPECT[control]


def test_presses_refuses_an_unmodelled_release_repeat():
    entry = bytes([0x10, 0x00, 0x10, 0x00, 0, 0, 1, 0])        # release rate index 1
    raw = newbutton.binding_raw(0x1FEDB, bytes(8), gesture_mask=0x02, repeat=True)
    with pytest.raises(levelcontrols.LevelControlsError):
        levelcontrols.presses(entry, [raw])


def test_frames_default_is_the_frames_constant():
    for control in ("minus", "plus", "zero"):
        assert levelcontrols.frames(control) == levelcontrols.FRAMES[control]


@pytest.mark.parametrize("control", ["minus", "plus", "zero"])
def test_rvc_custom_dgn_moves_only_the_stepper(control):
    """RV-C custom DGN 1EF00h, addressed to the sensor at BFh: the - / + go out as 19EFBF9F,
    same payload;
    Zero's standard pair is untouched."""
    frames = [newbutton.binding_wire_frame(b)
              for b in levelcontrols.bindings(control, levelcontrols.STEPPER_DGN_RVC)]
    want = [((0x19EFBF9F if i == 0x18EFFF9F else i), p) for i, p in EXPECT[control]]
    assert frames == want == levelcontrols.frames(control, levelcontrols.STEPPER_DGN_RVC)


def test_stepper_dgn_must_be_18_bit():
    with pytest.raises(levelcontrols.LevelControlsError):
        levelcontrols.bindings("plus", 0x40000)


def test_setup_controls_send_the_agreed_opcodes():
    """The popup's five controls: one LV frame each on the stepper's DGN (sensor, 2026-09-29)."""
    want = {"h_up": 0x06, "h_down": 0x07, "t_up": 0x08, "t_down": 0x09, "t_auto": 0x0A}
    for name, op in want.items():
        assert levelcontrols.frames(name, levelcontrols.STEPPER_DGN_RVC) == \
            [(0x19EFBF9F, bytes([0x4C, 0x56, op]) + b"\xff" * 5)], name
