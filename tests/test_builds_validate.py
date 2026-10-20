# tests/test_builds_validate.py
"""Every shipping recipe in builds/ compiles green through every universal gate.

This is the build-agnostic replacement for hand-written per-build test modules: a new
experimental build is validated simply by existing in builds/. No geometry is pinned here, so
nothing rots as a build iterates -- compilation itself runs the structural gates, the capability
gate, and each op's verify(); this asserts only that it passes and that no op silently drifts
into `unverified`.
"""
from pathlib import Path

import pytest

from fireflymcf import recipe, validate

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILDS = sorted((REPO_ROOT / "builds").glob("*.ffproj.json"))

# Ops a shipping build in builds/ is knowingly carrying UNVERIFIED. Keep it empty if every build
# is fully hardware-verified; add an op id here ONLY with a comment saying which build and why.
# The implementer sets this from the actual state (see Step 2) -- do not guess.
# (battheat removed 2026-09-21: the "Batt Heat" button is now coach-verified -- hold->enable /
# tap->off frames were captured on can1 and the heater engaged, so add_aes_batt_heat reports
# itself hardware_verified and no longer belongs here.)
UNVERIFIED_ALLOWLIST: set[str] = {
    # mcf-13.6-level(-preview) (v34a/v34b): add_level_page. v34a (static preview) flashed 2026-09-26
    # and its drawing confirmed; the live half (v34b) is not yet flashed. Coach test: spec §7.
    # mcf-13.6-level-controls (v35, supersedes the unflashed v34b): the same op with the rear-facing
    # van and the Blocks-carried stepper and Zero Level controls; not yet flashed. Coach test:
    # the 2026-09-26 level-controls spec §6.
    # mcf-13.6-level-centred (v36): v35 with the rear van group raised 20 px (orientation "rear";
    # v35 pins "rear-low"). Coach test: the page looks evenly spaced, and everything v35 verified.
    # mcf-13.6-level-rvc-dgn (v37): v36 on RV-C's custom DGN 1EF00h -- status 0x1EFBF, - / +
    # 0x1EFBF (addressed to the sensor). Coach test: with the sensor sending 19EFBF<SA>,
    # everything v35 verified. (Flashed 2026-09-28; the live page is verified.)
    # mcf-13.6-level-refresh (v43): repaint-on-change values, popup background.
    # mcf-13.6-hold-fix (v46): flashed 2026-10-05; every geometry stepper's tap and 250 ms hold
    # repeat verified. `level` stays here for Zero Level, never checked on the coach.
    # mcf-13.6-level-geometry (v45): v44 with Wheelbase/Track rows in the popup. Coach test: with
    # level-sensor d466a5b, each -/+ steps 0.5 in, a hold repeats, the value follows record 04h.
    # mcf-13.6-level-setup (v42): the LEVEL SETUP popup. Coach test: its spec, section 7.
    # mcf-13.6-level-arrow-icon (v38): v37 with fill_safe and the rail's spirit-level icon.
    # Coach test: the whole arrow on every amber tile, the new icon opens the LEVEL page.
    "level",
    # mcf-13.6-electrical-cards (v48): aes_card_layout -- ELECTRICAL as four cards, built
    # 2026-10-15, not flashed. Coach test: the four cards draw; every control still works by
    # touch (engine Start/Stop, the AES pill's Notice, Low Volts, AES and Inverter gears open
    # their pages, Pump Link, Auto Off -/+, Battery Heater hold/tap); the heater colours and
    # contactor glyph still track the BMS.
    "cards",
    # mcf-13.6-inverter-readouts (v49): built 2026-10-15, not flashed. Coach test: the build log v49.
    #   alarms / alarms-status -- the Ignore Alarms button (18EF709F#4D53..1770 on a hold, the
    #     zeroed cancel on a tap) and its byte-2-bit-1 status;
    #   acin-v / acin-a / acout-a -- draw:false readouts on fresh cells (the amps with the new
    #     subtract-and-clamp routine); invrows -- the captioned rows in the Inverter card.
    "alarms", "alarms-status", "acin-v", "acin-a", "acout-a", "invrows",
    # the move_control proof build (LV-mc): built 2026-10-20, not flashed. Coach test: the
    # compact AES card draws without Low Volts and the gear where LV-move has it; Low Volts on
    # the AES settings page arms and disarms on a 3 s hold and repaints with its status.
    "mv",
}
# (battglyph removed again 2026-10-10: v47 was flashed, its numbers sit at the stock position and
# the owner saw the orange outline with the heater running, so the outline style reports itself
# hardware_verified.)
# (altreboot removed 2026-10-05: v46 was flashed; a 3-s hold put one 197F809F#0100FFFFFFFFFFFF on
# can1 and the APS-500 re-claimed 0x80 and sent its boot query, so add_alt_reboot reports itself
# hardware_verified.)
# (battglyph removed 2026-09-26: v34a, which carries v33's recipe, was flashed and the owner
# confirmed the white contactor glyph on the House batteries; add_batt_heat_battery_glyph now
# reports itself hardware_verified.)
# (status removed again 2026-09-22: v32 was flashed and the owner confirmed the DRAWING half on the
# panel -- injected 18FF70C0 frames transitioned through every edge; the caption survived an enable
# and the glyph cleared on a disable. The RECEIVE half was already verified on v31.)
# (probe-dgn removed 2026-09-22: the append-new probe was flashed and passed -- the HOME readout
# followed injected 0x1FEA0 frames (42, then 7) -- so `append_filter` reports itself
# hardware_verified and no build carries it unverified.)


def test_there_are_builds_to_validate():
    # Guards against a vacuous pass if the glob ever matches nothing.
    assert BUILDS, "no builds/*.ffproj.json found"


@pytest.mark.parametrize("path", BUILDS, ids=[p.name for p in BUILDS])
def test_build_validates(path):
    project = recipe.Project.from_json(path.read_text())
    stock = validate.resolve_stock(project, REPO_ROOT)
    res = validate.validate(project, stock)
    assert res.passed, "%s did not validate:\n%s" % (path.name, res.report)
    drifted = set(res.unverified) - UNVERIFIED_ALLOWLIST
    assert not drifted, (
        "%s carries unverified op(s) %s not in UNVERIFIED_ALLOWLIST -- either verify them on "
        "hardware or add them to the allowlist with a reason" % (path.name, sorted(drifted)))
