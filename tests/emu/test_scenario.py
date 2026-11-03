"""The Scenario API: drive the emulated panel by control name and simulated bus value.

Each test boots stock 13.6 on the emulator with the demo network, so each costs a few seconds
of wall time; every scenario runs only as long as its assertion needs.
"""
import numpy as np
import pytest

pytest.importorskip("unicorn")

from fireflymcf.emu.scenario import Frame, Scenario, ScenarioError  # noqa: E402

NEEDS_RESEARCH_DATA = "boots the 13.6 corpus image in the emulator"

DC_DIMMER_COMMAND_2 = 0x1FEDB
G12_TANK_LEVEL_SENSOR = 0x0BFC1
G12_SA = 0x9C
HEAT_PUMP_SELECTOR = 0xEB      # `fireflymcf bus-map --name "Heat Pump"`: touch 0x843 -> 1FED9 sel EB


@pytest.fixture
def booted(mcf_136):
    s = Scenario(mcf_136, timeout_s=240)
    s.boot()
    return s


def test_boot_reaches_home(mcf_136, booted, tmp_path):
    terms = Scenario(mcf_136, timeout_s=120)
    terms.boot(accept_terms=False)
    assert terms.page.startswith("Thank you for being part of the Firefly family!")
    terms_img = terms.screenshot(tmp_path / "terms.png")
    with pytest.raises(ScenarioError):          # HOME's header is not on the terms page
        terms.control_box("HOME")

    assert booted.page == "HOME"
    x0, y0, x1, y1 = booted.control_box("HOME")
    assert x1 > x0 and y1 > y0
    home_img = booted.screenshot(tmp_path / "home.png")
    assert home_img.shape == terms_img.shape == (480, 800, 3)
    assert (tmp_path / "home.png").stat().st_size > 0
    # The terms page's I ACCEPT button region, compared pixel for pixel: HOME draws something else.
    bx0, by0, bx1, by1 = terms.control_box("I ACCEPT")
    assert not np.array_equal(home_img[by0:by1, bx0:bx1], terms_img[by0:by1, bx0:bx1])


def test_tap_light_sends_dimmer_command(booted):
    t0 = booted.now
    assert booted.frames(dgn=DC_DIMMER_COMMAND_2, since=t0) == []
    booted.tap("Main Ceiling")                 # HOME's 'Main\nCeiling', whitespace-normalised
    booted.run(2.0 - (booted.now - t0))
    sent = booted.frames(dgn=DC_DIMMER_COMMAND_2, since=t0)
    assert sent, "tapping Main Ceiling sent no DC_DIMMER_COMMAND_2"
    assert all(isinstance(f, Frame) and f.sa == 0x9F and f.t - t0 <= 2.0 for f in sent)


def test_tank_raw_reaches_g12_frames(booted):
    t0 = booted.now
    booted.set_tank_raw(1, 63000)
    booted.run(5.0)
    raw = (63000).to_bytes(2, "little")
    hits = [f for f in booted.frames(sa=None, dgn=G12_TANK_LEVEL_SENSOR, since=t0)
            if f.data[0] == 1 and f.data[1:3] == raw]
    assert hits and all(f.sa == G12_SA for f in hits)
    # the panel itself never sends that DGN: the default sa filter (0x9F) sees none
    assert booted.frames(dgn=G12_TANK_LEVEL_SENSOR, since=t0) == []


def test_option_tap_updates_g12_selector(booted):
    booted.goto("OPTIONS")
    assert booted.page == "OPTIONS"
    before = booted.g12_selector(HEAT_PUMP_SELECTOR)
    t0 = booted.now
    booted.tap("Heat Pump")
    booted.run(1.0)
    after = booted.g12_selector(HEAT_PUMP_SELECTOR)
    assert after is not None and after != before
    writes = [f for f in booted.frames(dgn=0x1FED9, since=t0) if f.data[1:3] == b"\x96\xeb"]
    assert writes, "no 1FED9 group-0x96 write to selector EB from the panel"


def test_ambiguous_label_refused(booted):
    # HOME draws a "V" unit after both the House (object 443) and Chassis (440) voltages.
    with pytest.raises(ScenarioError, match="ambiguous") as e:
        booted.tap("V")
    assert "440" in str(e.value) and "443" in str(e.value)
    t0 = booted.now
    with pytest.raises(ScenarioError, match="no control labelled"):
        booted.tap("No Such Control")
    assert booted.now == t0 and booted.frames(since=t0, sa=None) == []   # refused before tapping


def test_unknown_page_refused(booted):
    with pytest.raises(ScenarioError, match="no known path to page"):
        booted.goto("CLEAR EEPROM")


def test_read_mem_and_timeout(mcf_136):
    s = Scenario(mcf_136, timeout_s=0.5)
    with pytest.raises(ScenarioError, match="timeout"):
        s.boot()
    assert len(s.read_mem(0x20000000, 16)) == 16


def test_cpu_fault_raises(mcf_136):
    s = Scenario(mcf_136)
    s._booted = True                                 # fault injection: no boot needed
    s._m.run = lambda cycles: "unmapped read 0x00000000 (size 4) at pc=0x00000000"
    with pytest.raises(ScenarioError, match="fault: unmapped read"):
        s.run(0.1)


def test_renamed_home_header_still_home(mcf_136):
    """Pages are screen ids: a build whose HOME header is renamed (a-reference-build's `hdr` op, one
    `rename_label` on object 4) still boots to HOME, and goto finds HOME and the rail by it."""
    import hashlib

    from fireflymcf import recipe

    project = recipe.Project(
        base_sha256=hashlib.sha256(mcf_136).hexdigest(), build_name=None,
        ops=[recipe.OpSpec(id="hdr", type="rename_label",
                           params={"object": 4, "old": "   HOME", "new": "   MYCOACHNAME",
                                   "allow_grow": True})])
    built = recipe.compile_project(project, mcf_136)
    assert built.ok, built.errors
    s = Scenario(built.data, timeout_s=240)
    s.boot()
    assert s.screen == 0 and s.page == "MYCOACHNAME"
    s.goto("LIGHTS")
    assert s.screen == 2
    s.goto("HOME")
    assert s.screen == 0
    s.goto("MYCOACHNAME")                       # the drawn header is an alias of screen 0
    assert s.screen == 0


# --- support detection -----------------------------------------------------------------------

def _compiled_saffron_substitute(stock):
    """v54-Saffron stand-in (`builds/` is not linked): option follower, pump auto off, readout."""
    import hashlib
    import json

    from fireflymcf import recipe
    ops = [
        {"id": "link", "type": "add_option_follower", "enabled": True,
         "params": {"watch_channel": 25, "target_channel": 44, "gate_mask": 0xFF00,
                    "gate_on_value": 0x100}},
        {"id": "pump", "type": "add_pump_auto_off", "enabled": True,
         "params": {"allow_disable": True}},
        {"id": "f", "type": "append_filter", "enabled": True,
         "params": {"dgn": 0x1FFD7, "mode": 2, "payload_byte": 3, "instance": 0x41,
                    "compare_byte": 0}},
        {"id": "r", "type": "add_register_readout", "enabled": True,
         "params": {"register": {"ref": "f"}, "sub": 32000, "shift": 1, "draw": False}},
    ]
    p = {"schema": 1, "base_sha256": hashlib.sha256(stock).hexdigest(),
         "build_name": "support-test", "ops": ops}
    res = recipe.compile_project(recipe.Project.from_json(json.dumps(p)), stock)
    assert res.ok, res.errors
    return res.data


def test_supported_136_stock_and_builds(mcf_136):
    from fireflymcf.emu.scenario import supported
    assert supported(mcf_136) == (True, "supported: 13.6")
    ok, reason = supported(_compiled_saffron_substitute(mcf_136))
    assert ok and reason == "supported: 13.6"


def test_unsupported_build_not_emulated(mcf_1123, mcf_147):
    from fireflymcf.emu.scenario import supported
    for img in (mcf_1123, mcf_147):
        ok, reason = supported(img)
        assert not ok
        assert reason.startswith("not emulated: LCD firmware ")
        assert "13.6 only" in reason
    with pytest.raises(ScenarioError, match="not emulated"):
        Scenario(mcf_1123)
    ok, reason = supported(b"not an mcf")
    assert not ok and "not emulated" in reason


# --- navigation on owners' builds ------------------------------------------------------------

def _compile(stock, ops):
    import hashlib

    from fireflymcf import recipe
    project = recipe.Project(base_sha256=hashlib.sha256(stock).hexdigest(), build_name=None,
                             ops=[recipe.OpSpec(id=i, type=t, params=p) for i, t, p in ops])
    built = recipe.compile_project(project, stock)
    assert built.ok, built.errors
    return built.data


def test_goto_on_a_six_icon_rail(mcf_136):
    """`show_nav_slides_icon` gives the rail a sixth icon under a variant switch: the static
    walk shows a rail without ELECTRICAL and the live one has six regions. goto resolves the
    rail from the live walk, by route, with HOME on top and SETTINGS at the bottom."""
    img = _compile(mcf_136, [("nav", "show_nav_slides_icon", {"screen": 29})])
    s = Scenario(img, timeout_s=300)
    s.boot()
    for page, screen in (("ELECTRICAL", 1), ("LIGHTS", 2), ("CLIMATE", 3), ("SETTINGS", 4),
                         ("HOME", 0)):
        s.goto(page)
        assert s.screen == screen, (page, s.screen)


def test_goto_options_once_the_keypad_tile_draws(booted):
    """After a few seconds on the network SETTINGS draws the wireless-keypad tile, a second
    switch keyed on the floorplan slot; goto still finds the Floorplan button, by the
    floorplan name it draws."""
    booted.run(8.0)
    booted.goto("ELECTRICAL")
    booted.goto("OPTIONS")
    assert booted.page == "OPTIONS"


def test_set_g12_selector_before_and_after_boot(mcf_136):
    """A preset reaches the G12's store and, through the G12's report, the panel's receive
    register for that selector; a change after boot does too."""
    from fireflymcf import register
    slot = register.resolve_register(mcf_136, dgn="DGN_CONFIG",
                                     selector=HEAT_PUMP_SELECTOR).slot_addr
    s = Scenario(mcf_136, timeout_s=240)
    s.set_g12_selector(HEAT_PUMP_SELECTOR, 1)
    s.boot()
    assert s.g12_selector(HEAT_PUMP_SELECTOR) == 1
    s.run(12.0)
    assert int.from_bytes(s.read_mem(slot, 2), "little") & 0xFF == 1
    s.set_g12_selector(HEAT_PUMP_SELECTOR, 0)
    assert s.g12_selector(HEAT_PUMP_SELECTOR) == 0
    s.run(12.0)
    assert int.from_bytes(s.read_mem(slot, 2), "little") & 0xFF == 0
    with pytest.raises(ScenarioError, match="u16"):
        s.set_g12_selector(HEAT_PUMP_SELECTOR, 0x10000)


def test_set_tank_raw_before_boot(mcf_136):
    """A tank reading set before boot is what the G12 reports from its first frame on."""
    s = Scenario(mcf_136, timeout_s=240)
    s.set_tank_raw(1, 63000)
    s.boot()
    fresh = [f for f in s.frames(sa=None, dgn=G12_TANK_LEVEL_SENSOR) if f.data[0] == 1]
    assert fresh and all(f.data[1:3] == (63000).to_bytes(2, "little") for f in fresh)
