"""`fireflymcf doctor` lists the G12's tank broadcasts, read from the file.

The skill runs `doctor` first; listing the instances there means an agent sees what this
firmware broadcasts before it can name a tank instance from memory."""
import json

from fireflymcf import cli, tankbroadcast

STOCK = "firefly-13.6/mcf.bin"


def test_json_lists_each_broadcast_from_the_file(capsys):
    assert cli.main(["doctor", STOCK, "--json"]) == 0
    tanks = json.loads(capsys.readouterr().out)["tank_broadcasts"]
    data = open(STOCK, "rb").read()
    assert [t["instance"] for t in tanks] == [b.instance for b in
                                              tankbroadcast.tank_broadcasts(data)]
    assert {t["instance"] for t in tanks} == {0, 1, 2, 3, 18}
    assert all(t["broadcasting"] for t in tanks)
    lpg = next(t for t in tanks if t["instance"] == 3)
    assert lpg["rvc_name"] == "lpg" and lpg["panel_name"] == "LP"
    assert "0x2484aa" in lpg["panel_name_source"]
    assert next(t for t in tanks if t["instance"] == 18)["panel_name"] == "GREY2"


def test_text_names_the_tool_and_the_name_source(capsys):
    assert cli.main(["doctor", STOCK]) == 0
    out = capsys.readouterr().out
    assert "instance 18" in out and "fireflymcf-tankpatch" in out and "RV-C" in out
    assert "panel: GREY2" in out


def test_without_panel_names_doctor_says_the_rvc_names_are_the_specs(capsys, monkeypatch):
    from fireflymcf import tanknames
    assert cli.main(["doctor", STOCK]) == 0
    assert "may not match" not in capsys.readouterr().out
    monkeypatch.setattr(tanknames, "tank_names", lambda data: {})
    assert cli.main(["doctor", STOCK]) == 0
    out = " ".join(capsys.readouterr().out.split())
    assert "RV-C specification's name and may not match what Firefly calls that tank" in out
