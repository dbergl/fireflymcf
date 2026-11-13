"""`add_popup_page` in the emulator: the gear on ELECTRICAL opens the dialog on the new screen,
Close returns to ELECTRICAL, and the dialog takes `set_theme`'s colours when the theme runs after
it and the stock ones when it does not."""
import json

import pytest

pytest.importorskip("unicorn")

from fireflymcf import recipe  # noqa: E402
from fireflymcf.emu.scenario import Scenario  # noqa: E402

STOCK_SHA = "d66b41ffafaae4f5ec47cf90714f14bb1954fc3a5ae38defa98a94cd9643d382"
ELECTRICAL = 1
NEW = 35
AT = [600, 40]                      # clear on stock ELECTRICAL (tests/test_popup_page.py)
DIALOG_INSIDE = (300, 250)          # (x, y) inside the dialog, below the title, no content
BODY = (38, 44, 96)                 # levelpage.BODY, the dialog fill
THEMED_BODY = (46, 31, 39)
THEME = {"colours": {"bg": [32, 21, 27], "panel": [46, 31, 39], "button": [133, 77, 39],
                     "header": [221, 114, 48], "rail": [92, 52, 27], "active": [214, 160, 64]},
         "extra": [[list(BODY), list(THEMED_BODY)]]}


# Over a background object 829 paints, the arm ELECTRICAL's slot-7 switch (root 5 record 5)
# selects. The switch repaints its arm on its own after the page is entered; a gear drawn after
# the switch in the root is painted over then (found 2026-11-13 on the card layout).
AT_ON_ARM = [110, 280]


def _build(mcf_136, theme=False, at=AT):
    ops = [{"id": "screen", "type": "add_screen", "params": {"title": "BATTERY SETUP"},
            "enabled": True},
           {"id": "popup", "type": "add_popup_page", "enabled": True,
            "params": {"screen": {"ref": "screen"}, "title": "BATTERY SETUP",
                       "return_to": "ELECTRICAL", "opener": {"page": "ELECTRICAL", "at": at}}}]
    if theme:
        ops.append({"id": "theme", "type": "set_theme", "params": THEME, "enabled": True})
    project = recipe.Project.from_json(json.dumps({
        "schema": 1, "base_sha256": STOCK_SHA, "build_name": "popup-emu", "ops": ops}))
    res = recipe.compile_project(project, mcf_136)
    assert res.ok, res.errors
    return res.data


@pytest.fixture(scope="module")
def booted(mcf_136):
    s = Scenario(_build(mcf_136), timeout_s=600)
    s.boot()
    return s


def test_gear_opens_popup_and_close_returns(booted, tmp_path):
    booted.goto("ELECTRICAL")
    assert booted.screen == ELECTRICAL
    booted.tap_at(AT[0] + 22, AT[1] + 19)        # the 44x38 gear's centre
    booted.run(1.0)
    assert booted.screen == NEW
    booted.screenshot(tmp_path / "popup.png")
    booted.tap("Close")
    booted.run(1.0)
    assert booted.screen == ELECTRICAL


@pytest.mark.parametrize("theme, want", [(False, BODY), (True, THEMED_BODY)])
def test_dialog_takes_the_theme(mcf_136, theme, want):
    s = Scenario(_build(mcf_136, theme=theme), timeout_s=600)
    s.boot()
    s.show_screen(NEW)
    x, y = DIALOG_INSIDE
    assert tuple(int(v) for v in s.screenshot()[y, x]) == want


def test_gear_survives_the_arm_repaint(mcf_136):
    s = Scenario(_build(mcf_136, at=AT_ON_ARM), timeout_s=600)
    s.boot()
    s.goto("ELECTRICAL")
    x, y = AT_ON_ARM
    s.run(3.0)                                         # past the slot-7 arm's repaint
    gear = s.screenshot()[y:y + 38, x:x + 44]
    assert int((gear == 255).all(axis=2).sum()) > 200  # the white gear is still drawn
