"""`--json` on the commands an agent drives: stdout parses, failures included."""
import hashlib
import json
from pathlib import Path

import pytest

from fireflymcf import cli

STOCK = Path("firefly-13.6/mcf.bin")


def _run(capsys, *argv):
    rc = cli.main([*argv, "--json"])
    return rc, json.loads(capsys.readouterr().out)


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "p.ffproj.json"
    p.write_text(json.dumps({
        "schema": 1, "base_sha256": hashlib.sha256(STOCK.read_bytes()).hexdigest(),
        "build_name": "Jayco B-Van V13 json test",
        "ops": [{"id": "pump", "type": "rename_label",
                 "params": {"object": 295, "old": "Water\nPump", "new": "Pump"}}]}))
    return p


def test_labels(capsys):
    rc, out = _run(capsys, "labels", str(STOCK), "--containing", "Water\nPump")
    assert rc == 0 and 295 in {r["object"] for r in out}
    assert all(r["text"] == "Water\nPump" for r in out)


def test_labels_without_a_filter_lists_screens(capsys):
    rc, out = _run(capsys, "labels", str(STOCK))
    assert rc == 0 and out and {"start", "title", "rows"} <= set(out[0])


def test_validate_pass_and_fail(capsys, project, tmp_path):
    rc, out = _run(capsys, "validate", str(project), str(STOCK))
    assert rc == 0 and out["passed"] is True and out["unverified"] == []
    bad = tmp_path / "bad.ffproj.json"
    bad.write_text(project.read_text().replace('"object": 295', '"object": 4'))
    rc, out = _run(capsys, "validate", str(bad), str(STOCK))
    assert rc == 1 and out["passed"] is False and out["errors"]


def test_compile_success_and_errors(capsys, project, tmp_path):
    out_bin = tmp_path / "mcf-json.bin"
    rc, out = _run(capsys, "compile", str(project), str(STOCK), "-o", str(out_bin))
    assert rc == 0 and out["ok"] is True and out["build_name"] == "Jayco B-Van V13 json test"
    assert out["sha256"] == hashlib.sha256(out_bin.read_bytes()).hexdigest()
    # A refused write (the file exists) is still JSON on stdout.
    rc, out = _run(capsys, "compile", str(project), str(STOCK), "-o", str(out_bin))
    assert rc == 2 and out["ok"] is False and "overwrite" in out["errors"][0]["message"]
    # A failing op carries its code and op id.
    bad = tmp_path / "bad.ffproj.json"
    bad.write_text(project.read_text().replace('"new": "Pump"', '"new": "' + "P" * 40 + '"'))
    rc, out = _run(capsys, "compile", str(bad), str(STOCK), "-o", str(tmp_path / "mcf-b.bin"))
    assert rc == 1 and out["errors"][0]["code"] == "LABEL_TOO_LONG"


def test_coverage_identify_image_export(capsys):
    rc, out = _run(capsys, "coverage", str(STOCK))
    assert rc == 0 and out["unclaimed"] == [] and out["claimed"] == out["total"]
    rc, out = _run(capsys, "identify", str(STOCK))
    assert rc == 0 and {s["section_name"] for s in out["sections"]} >= {"G12"}
    rc, out = _run(capsys, "image-export", str(STOCK))
    assert rc == 0 and out[0]["index"] == 0 and out[0]["width"] > 0
