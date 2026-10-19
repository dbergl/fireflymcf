import pytest

from fireflymcf import guidata, levelcontrols, newbutton

V34B = "builds/mcf-13.6-Level-v34b.bin"
D = open(V34B, "rb").read()
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


@pytest.mark.parametrize("control", ["minus", "plus"])
def test_stepper_fires_once_whatever_the_press_length(control):
    entry = levelcontrols.group_entry(D, control, 0)
    p = levelcontrols.presses(entry, levelcontrols.bindings(control))
    # every key is "the frames one whole press of that length sends": exactly one, always
    assert p["tap"] == EXPECT[control]
    assert p["held_2s"] == EXPECT[control]
    assert p["held_3s"] == EXPECT[control]
    assert p["held_3_5s"] == EXPECT[control]


@pytest.mark.parametrize("control", ["minus", "plus"])
def test_stepper_schedules_no_repeat_at_all(control):
    """Belt and braces for the model: with every repeat rate at 0 a repeat bit would be
    inert (`0x21BDEE`), so the stepper must carry neither -- a live rate plus a set bit
    would stream steps while a finger rests on the button."""
    entry = levelcontrols.group_entry(D, control, 0)
    assert entry[2] == 0x00
    assert not entry[3] & 0x80
    for raw in levelcontrols.bindings(control):
        assert not raw[2] & 0x04


def test_zero_needs_the_full_hold():
    entry = levelcontrols.group_entry(D, "zero", 0)
    p = levelcontrols.presses(entry, levelcontrols.bindings("zero"))
    assert p["tap"] == [] and p["held_2s"] == []
    # held_3s sits exactly on the 3-s deadline, which only resolves through the `>=` inference
    # (levelcontrols.presses' docstring); the unambiguous assertion is held_3_5s, well past it.
    assert p["held_3_5s"] == EXPECT["zero"]
    assert entry[0] >> 4 == 3 and not entry[3] & 0x80


def test_zero_prefix_is_not_a_second_loop_donor():
    gt = guidata.group_table(D)
    for bad in (64, 85):
        assert levelcontrols.group_entry(D, "zero", 0)[:4] != gt[bad].raw[:4]


@pytest.mark.parametrize("control", ["minus", "plus", "zero"])
def test_group_entry_splices_start_and_count(control):
    entry = levelcontrols.group_entry(D, control, 0x1234)
    assert len(entry) == guidata.GROUP_ENTRY_SIZE
    assert entry[4:6] == b"\x34\x12"
    assert entry[6] == len(levelcontrols.bindings(control))


def test_presses_models_repeat_on_the_stock_arrow():
    """The model is not blind to repeat: stock group 103 (the -300 arrow, `10 00 04 80`,
    held rate index 1 = 250 ms) streams its repeat-bit delta while held."""
    gt = guidata.group_table(D)
    bt = guidata.binding_table(D)
    g = gt[103]
    slice_ = [b.raw for b in bt[g.start:g.start + g.count]]
    delta = newbutton.binding_wire_frame(slice_[0])
    p = levelcontrols.presses(g.raw, slice_, allow_second_loop=True)
    # tap: delta on tap-release, then the 0x0A and 0x0E release frames
    assert p["tap"].count(delta) == 1
    # held 2 s: the held phase starts at 1000 ms and repeats at 1250, 1500, 1750
    assert p["held_2s"].count(delta) == 4
    assert p["held_3s"].count(delta) == 8


def test_presses_refuses_an_unmodelled_release_repeat():
    entry = bytes([0x10, 0x00, 0x10, 0x00, 0, 0, 1, 0])        # release rate index 1
    raw = newbutton.binding_raw(0x1FEDB, bytes(8), gesture_mask=0x02, repeat=True)
    with pytest.raises(levelcontrols.LevelControlsError):
        levelcontrols.presses(entry, [raw])


def test_presses_refuses_the_unmodelled_second_loop():
    g = guidata.group_table(D)[103]                            # 10 00 04 80: byte 3 bit 7
    with pytest.raises(levelcontrols.LevelControlsError):
        levelcontrols.presses(g.raw, [])


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
