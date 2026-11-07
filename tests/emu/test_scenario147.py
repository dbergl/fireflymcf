"""The Scenario API on stock 14.7: boot, the five rail pages, a tap that sends, a status repaint.

14.7 runs the LCD firmware 11.23 also runs (sha256 cbed1028...), not 13.6's. These are the
minimum the design asks before the emulator accepts it (docs/superpowers/specs/
2026-11-06-firmware-14x-support-design.md, "The emulator"): not only that the screen drew,
but that a tap reaches the bus and a peer's status reaches the screen.
"""
import numpy as np
import pytest

pytest.importorskip("unicorn")

from fireflymcf import register  # noqa: E402
from fireflymcf.emu.scenario import PANEL_SA, Scenario, ScenarioError  # noqa: E402

NEEDS_RESEARCH_DATA = "boots the 14.7 corpus image in the emulator"

DC_DIMMER_COMMAND_2 = 0x1FEDB
# `fireflymcf bus-map --name "Light Master"` on 14.7: group 1 ("ON [in 'Light Master']")
# sends 1FEDB set-brightness to these instances. A test expectation, not a locator.
LIGHT_MASTER_ON_INSTANCES = {22, 24, 25, 32, 34, 35}
# `bus-map --name "Main Ceiling"` on 14.7: var 74, 1FEDA instance 0x20, drawn on HOME.
MAIN_CEILING = 0x20
RAIL_NAMES = {0: "HOME", 1: "ELECTRICAL", 2: "LIGHTS", 3: "CLIMATE", 4: "SETTINGS"}


@pytest.fixture
def booted(mcf_147):
    s = Scenario(mcf_147, timeout_s=240)
    s.boot()
    return s


def test_boots_to_home(booted):
    assert booted.screen == 0
    assert booted.page == "HOME"


@pytest.mark.parametrize("screen", [0, 1, 2, 3, 4])
def test_rail_reaches_each_page(booted, screen):
    # By screen number: 14.7's screen 1 draws a blank header, so its name is only the alias.
    booted.goto(RAIL_NAMES[screen])
    assert booted.screen == screen
    # and away again from that page's own rail (the live rail, not HOME's)
    nxt = (screen + 1) % 5
    booted.goto(RAIL_NAMES[nxt])
    assert booted.screen == nxt


def test_tap_sends_on_the_bus(booted):
    booted.run(2.0)
    t0 = booted.now
    booted.run(2.0)
    assert booted.frames(dgn=DC_DIMMER_COMMAND_2, sa=PANEL_SA, since=t0) == [], \
        "the panel sent 1FEDB without a tap"
    t1 = booted.now
    booted.tap("ON")                            # Light Master's ON, HOME's only "ON"
    booted.run(2.0 - (booted.now - t1))
    sent = booted.frames(dgn=DC_DIMMER_COMMAND_2, sa=PANEL_SA, since=t1)
    assert sent, "tapping Light Master ON sent no 1FEDB"
    assert {f.data[0] for f in sent} == LIGHT_MASTER_ON_INSTANCES
    assert {f.data[3] for f in sent} == {0}     # command 0, set brightness


def test_status_repaints(booted, tmp_path):
    """The G12 switches Main Ceiling on by itself (no panel tap): its 1FEDA status lands in
    the variable's slot and the control's indicator is repainted; while nothing reports, the
    indicator stays as it is."""
    slot = register.resolve_register(booted.image, dgn="DC_DIMMER_STATUS_3",
                                     instance=MAIN_CEILING).slot_addr
    x0, y0, x1, y1 = booted.control_box("Main Ceiling")
    booted.run(2.0)
    before = booted.screenshot(tmp_path / "a.png")[y0:y1, x0:x1]
    booted.run(2.0)
    still = booted.screenshot(tmp_path / "b.png")[y0:y1, x0:x1]
    assert np.array_equal(before, still), "the indicator changed with no status"
    slot_off = booted.read_mem(slot, 2)

    booted.set_g12_output(MAIN_CEILING, 200)
    booted.run(2.0)
    on = booted.screenshot(tmp_path / "c.png")[y0:y1, x0:x1]
    assert booted.read_mem(slot, 2) != slot_off, "the 1FEDA status did not reach its slot"
    assert not np.array_equal(before, on), "Main Ceiling's indicator did not repaint"
    assert not any(f.dgn == DC_DIMMER_COMMAND_2 for f in booted.frames(sa=PANEL_SA)), \
        "the repaint came from a panel send, not the G12's status"

    booted.set_g12_output(MAIN_CEILING, 0)
    booted.run(2.0)
    off = booted.screenshot(tmp_path / "d.png")[y0:y1, x0:x1]
    assert np.array_equal(before, off), "Main Ceiling did not repaint back to off"


def test_set_g12_output_refuses_an_unknown_output(booted):
    with pytest.raises(ScenarioError, match="output"):
        booted.set_g12_output(0x7F, 200)


def test_goto_options(booted):
    # SETTINGS, hold Floorplan, Proceed, Main Options: the same touch ids as 13.6 (PROCEED_ID,
    # MAIN_OPTIONS_ID), which `navmap` routes to screen 47 on 14.7 as well.
    booted.goto("OPTIONS")
    assert booted.screen == 47 and booted.page == "OPTIONS"
