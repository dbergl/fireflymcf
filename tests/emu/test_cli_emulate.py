"""`fireflymcf emulate`: the emulator from the main CLI."""
import json
import sys

import pytest

from fireflymcf import cli

NEEDS_RESEARCH_DATA = "boots the 13.6 corpus image"


def test_emulate_headless_screenshot(tmp_path, capsys):
    pytest.importorskip("unicorn")
    from PIL import Image

    png = tmp_path / "shot.png"
    rc = cli.main(["emulate", "firefly-13.6/mcf.bin", "--seconds", "5", "--eeprom", "none",
                   "--screenshot", str(png), "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    summary = json.loads(out)
    assert summary["ok"] is True and summary["screenshot"] == str(png)
    assert Image.open(png).size == (800, 480)


def test_emulate_without_extra_explains(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "fireflymcf.emu.machine", None)
    rc = cli.main(["emulate", "firefly-13.6/mcf.bin", "--seconds", "1"])
    assert rc == 2
    err = capsys.readouterr().err
    assert cli.EMU_MISSING in err                    # the extra, from this repository by URL
    assert 'fireflymcf[full,emu] @ git+https://github.com/dbergl/fireflymcf.git' in err
