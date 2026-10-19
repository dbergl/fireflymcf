"""`fireflymcf coach-config`: the floorplan + options file render and verify take."""
import json
from pathlib import Path

import pytest

from fireflymcf import cli, options

STOCK = "firefly-13.6/mcf.bin"
CAPTURE = Path(__file__).parent / "fixtures" / "livecfg-sample.log"


def test_from_a_capture_is_the_coach_state(tmp_path):
    out = tmp_path / "coach.json"
    assert cli.main(["coach-config", STOCK, "-o", str(out), "--capture", str(CAPTURE)]) == 0
    body = json.loads(out.read_text())
    assert body["floorplan"] == "WD" and "Black Tank" in body["options"]
    options.CoachConfig.from_json(out)          # loads as render --config does


def test_from_names(tmp_path):
    out = tmp_path / "coach.json"
    assert cli.main(["coach-config", STOCK, "-o", str(out), "--floorplan", "RT",
                     "--option", "Heat Pump"]) == 0
    assert json.loads(out.read_text()) == {"floorplan": "RT", "options": ["Heat Pump"]}


@pytest.mark.parametrize("argv, says", [
    (["--floorplan", "XX"], "no floorplan 'XX'"),
    (["--floorplan", "WD", "--option", "Hot Tub"], "no option 'Hot Tub'"),
    ([], "name --floorplan"),
])
def test_refuses_what_the_build_does_not_have(tmp_path, capsys, argv, says):
    out = tmp_path / "coach.json"
    assert cli.main(["coach-config", STOCK, "-o", str(out), *argv]) == 2
    assert says in capsys.readouterr().err and not out.exists()


def test_a_capture_without_config_frames_is_refused(tmp_path, capsys):
    log = tmp_path / "other.log"
    log.write_text("(1.0) can1 19FFD780#0102030405060708\n")
    assert cli.main(["coach-config", STOCK, "-o", str(tmp_path / "c.json"),
                     "--capture", str(log)]) == 2
    assert "no configuration frames" in capsys.readouterr().err
