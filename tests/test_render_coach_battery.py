"""Task 6 (2026-09-11 home-coach-48v-battery): the render's coach 48V pack battery colour+level.

Unit-tests the two pure helpers per task-6-brief.md, then an integration check: build the
`add_voltage_battery` op's output (the HOME object-902 clone) and render HOME's AES variant,
asserting the clone's fill draws green/full at a high pack voltage and red/low at a low one --
the validation render's job before a build is flashed. Also covers the Task 6 review fix
(2026-09-11): the coach identification in `_battery_fill` is scoped to HOME (`root == 4`), not
just "unresolved mode-7, not 202/206" -- `test_coach_pack_voltage_does_not_leak_onto_other_pages`
pins that a coach-voltage preview never paints unrelated mode-7 gauges on another page (reproduced
pre-fix on the stock LEVELING page, root 32).

`_img`/`render.render_page` from the brief's own draft does not exist (api-notes.md's Task 6
correction): the integration check below uses `render.screen_placements` instead, and inspects
the resulting `Prim`s directly rather than decoding pixels.
"""
import hashlib
import json
from pathlib import Path

from fireflymcf import recipe, render

ROOT = Path(__file__).resolve().parents[1]
STOCK = (ROOT / "firefly-13.6" / "mcf.bin").read_bytes()


def test_coach_battery_green_above_threshold_red_below():
    assert render.coach_battery_color(volts=54.0) == render._BATTERY_GREEN_RGB
    assert render.coach_battery_color(volts=49.0) == render._BATTERY_RED_RGB


def test_coach_battery_levels_over_window():
    assert render.coach_battery_fraction(54.8) == 1.0
    assert render.coach_battery_fraction(48.0) == 0.0
    assert 0.0 < render.coach_battery_fraction(51.4) < 1.0


def test_coach_battery_fraction_clamps_outside_window():
    assert render.coach_battery_fraction(60.0) == 1.0
    assert render.coach_battery_fraction(40.0) == 0.0


def _build():
    proj = {"schema": 1, "base_sha256": hashlib.sha256(STOCK).hexdigest(),
            "build_name": "t6",
            "ops": [{"id": "batt", "type": "add_voltage_battery",
                     "params": {"x": 615, "y": 334}}]}
    res = recipe.compile_project(recipe.Project.from_json(json.dumps(proj)), STOCK)
    assert res.ok, res.errors
    return res.data


def _voltage_state_prims(data, volts):
    placements = render.screen_placements(
        data, root=4, slot_values={6: 1, 37: 2},
        preview={"coach_pack_voltage": volts})
    prims = placements[0].prims
    return [p for p in prims if p.color_src == "voltage-state"]


def test_coach_battery_fill_is_green_and_full_at_high_voltage():
    data = _build()
    hits = _voltage_state_prims(data, 54.0)
    assert len(hits) == 1, hits
    p = hits[0]
    assert p.color == render._BATTERY_GREEN_RGB
    assert p.h > 0


def test_coach_battery_fill_is_red_and_shorter_at_low_voltage():
    # exactly 48.0V is fraction 0.0, which -- like the tank/stock-battery gauges -- draws no
    # fill Prim at all (`if fh > 0:` in screen_placements); use a low-but-nonzero voltage to
    # see the fill itself, and check the true-empty case separately below.
    data = _build()
    full = _voltage_state_prims(data, 54.8)[0]
    low = _voltage_state_prims(data, 49.0)[0]
    assert low.color == render._BATTERY_RED_RGB
    assert full.color == render._BATTERY_GREEN_RGB
    assert low.h < full.h


def test_coach_battery_fill_absent_when_exactly_empty():
    # fraction 0.0 at 48.0V draws no fill Prim -- matching the tank/stock-battery gauge
    # convention (drawn only "up to" the fraction; 0 means nothing to draw).
    data = _build()
    assert _voltage_state_prims(data, 48.0) == []


def test_no_voltage_state_prims_without_preview_key():
    # the coach fill's colour cell is unresolved (fresh, no theme-init store) -- absent the
    # preview key it must stay unpainted (heuristic), not silently guessed.
    data = _build()
    placements = render.screen_placements(data, root=4, slot_values={6: 1, 37: 2})
    hits = [p for p in placements[0].prims if p.color_src == "voltage-state"]
    assert hits == []


def test_coach_battery_fill_near_empty_does_not_crash_on_corner_radius():
    # Task 9 (owner validation renders, 2026-09-11): rendering the coach battery at 48.5V (near
    # the 48.0V empty floor -- one of the four owner-requested validation voltages) raised
    # `ValueError: y1 must be greater than or equal to y0` from PIL's `rounded_rectangle`, not
    # from this module's own geometry. Root cause: the fill's `fh` (a couple of px at this
    # voltage) was shorter than the donor's corner radius (`p.coord4`), and PIL's
    # `rounded_rectangle` only clamps/collapses the radius via its `full_x`/`full_y` shortcut
    # when ALL FOUR corners are requested (`if all(corners):`) -- with our two-corner mask
    # (`(False, False, True, True)`, flat top / rounded bottom) it always takes the "four
    # separate corners" branch, which insets the requested edge by `radius + 1` with no bound of
    # its own, flipping y1 below y0 when the fill is shorter than that inset. The fix clamps the
    # Prim's radius to `fh - 1` so the inset always fits; below that a square corner is the
    # correct degrade for a sliver too thin to show curvature, not a crash. A regression here
    # means the clamp in `screen_placements`'s voltage-state fill (around `fill_radius`) was
    # lost.
    data = _build()
    hits = _voltage_state_prims(data, 48.5)
    assert len(hits) == 1, hits
    p = hits[0]
    assert p.color == render._BATTERY_RED_RGB
    assert 0 < p.h < 10                      # a thin near-empty sliver, but still drawn
    assert p.radius <= p.h - 1 if p.h > 0 else True


def test_coach_pack_voltage_does_not_leak_onto_other_pages():
    # Task 6 review (2026-09-11): "unresolved mode-7 fillA that isn't stock idx 202/206" is NOT
    # by itself page-scoped -- every page has its own unresolved mode-7 gauge fills. Reproduced on
    # a STOCK render (no coach clone present at all) of the LEVELING page (root 32, mode-7 indices
    # 424-431): passing coach_pack_voltage painted 8 spurious green "voltage-state" prims there
    # before the `root == 4` guard was added. This must stay zero on every non-HOME page, stock or
    # built.
    placements = render.screen_placements(STOCK, root=32, preview={"coach_pack_voltage": 54.0})
    hits = [p for p in placements[0].prims if p.color_src == "voltage-state"]
    assert hits == []

    # same check on the built image (clone present, but this is HOME's page, not root 32)
    data = _build()
    placements = render.screen_placements(data, root=32, preview={"coach_pack_voltage": 54.0})
    hits = [p for p in placements[0].prims if p.color_src == "voltage-state"]
    assert hits == []


def test_coach_voltage_number_drawn_on_home():
    """The clone's value widget reads slot 209 (the live cell ELECTRICAL reads), so the House
    NUMBER is drawn by the standard 'V' path from `preview["coach_voltage"]` -- no clone-specific
    value injection. On HOME the House battery must show the coach voltage."""
    data = _build()
    placements = render.screen_placements(
        data, root=4, slot_values={6: 1, 37: 2},
        preview={"coach_voltage": 54.0, "coach_pack_voltage": 54.0})
    texts = [p.text for p in placements[0].prims
             if p.src == "preview" and getattr(p, "text", None)]
    assert "54.0" in texts, "coach 48V number not drawn on HOME: %r" % texts
