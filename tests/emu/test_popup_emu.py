"""`add_popup_page` in the emulator: the dialog draws on the new screen and Close returns to the
page it names."""
import json

import pytest

pytest.importorskip("unicorn")

from fireflymcf import recipe  # noqa: E402
from fireflymcf.emu.scenario import Scenario  # noqa: E402

STOCK_SHA = "d66b41ffafaae4f5ec47cf90714f14bb1954fc3a5ae38defa98a94cd9643d382"
ELECTRICAL = 1
NEW = 35


def _build(mcf_136):
    project = recipe.Project.from_json(json.dumps({
        "schema": 1, "base_sha256": STOCK_SHA, "build_name": "popup-emu",
        "ops": [{"id": "screen", "type": "add_screen", "params": {"title": "BATTERY SETUP"},
                 "enabled": True},
                {"id": "popup", "type": "add_popup_page", "enabled": True,
                 "params": {"screen": {"ref": "screen"}, "title": "BATTERY SETUP",
                            "return_to": "ELECTRICAL"}}],
    }))
    res = recipe.compile_project(project, mcf_136)
    assert res.ok, res.errors
    return res.data


@pytest.fixture(scope="module")
def booted(mcf_136):
    s = Scenario(_build(mcf_136), timeout_s=600)
    s.boot()
    return s


def test_close_returns_to_electrical(booted, tmp_path):
    booted.show_screen(NEW)
    assert booted.screen == NEW
    booted.screenshot(tmp_path / "popup.png")
    booted.tap("Close")
    booted.run(1.0)
    assert booted.screen == ELECTRICAL
