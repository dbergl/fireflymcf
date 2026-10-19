"""`fireflymcf compile PROJECT STOCK -o OUT` -- the command-line build of a project file.

Same engine as Studio's export (`recipe.compile_project`), so the test holds the CLI to the
engine's own bytes rather than to a second oracle. The project used is small and fast:
`tank_heater_names` is a same-size GuiCode edit with no parameters.
"""
from __future__ import annotations

import hashlib
import json

from fireflymcf import recipe
from fireflymcf.cli import main

STOCK = "firefly-13.6/mcf.bin"


def _project(mcf_136: bytes, build_name="Jayco B-Van V13 compile-test") -> dict:
    return {"schema": recipe.SCHEMA,
            "base_sha256": hashlib.sha256(mcf_136).hexdigest(),
            "build_name": build_name,
            "ops": [{"id": "tank", "type": "tank_heater_names", "params": {}}],
            "preview": {}}


def test_compile_writes_the_engines_bytes_and_a_sidecar(tmp_path, mcf_136):
    proj = tmp_path / "p.ffproj.json"
    proj.write_text(json.dumps(_project(mcf_136)))
    out = tmp_path / "mcf-test.bin"
    assert main(["compile", str(proj), STOCK, "-o", str(out)]) == 0

    expected = recipe.compile_project(recipe.Project.from_json(proj.read_text()), mcf_136)
    assert expected.ok
    assert out.read_bytes() == expected.data

    side = json.loads((tmp_path / "mcf-test.bin.json").read_text())
    assert side["sha256"] == hashlib.sha256(expected.data).hexdigest()
    assert side["size"] == len(expected.data)
    assert side["build_name"] == "Jayco B-Van V13 compile-test"
    assert side["manifest"] == expected.manifest
    assert side["unverified"] == []


def test_compile_refuses_a_name_the_bootloader_would_not_list(tmp_path, mcf_136, capsys):
    proj = tmp_path / "p.ffproj.json"
    proj.write_text(json.dumps(_project(mcf_136)))
    out = tmp_path / "custom.bin"
    assert main(["compile", str(proj), STOCK, "-o", str(out)]) == 2
    assert not out.exists()
    assert "mcf-" in capsys.readouterr().err


def test_compile_refuses_a_stock_that_is_not_the_projects_base(tmp_path, mcf_136, mcf_147,
                                                               capsys):
    proj = tmp_path / "p.ffproj.json"
    doc = _project(mcf_136)
    doc["base_sha256"] = hashlib.sha256(mcf_147).hexdigest()
    proj.write_text(json.dumps(doc))
    out = tmp_path / "mcf-test.bin"
    assert main(["compile", str(proj), STOCK, "-o", str(out)]) == 1
    assert not out.exists()
    assert "BASE_MISMATCH" in capsys.readouterr().err


def test_compile_reports_an_op_error_and_writes_nothing(tmp_path, mcf_136, capsys):
    proj = tmp_path / "p.ffproj.json"
    doc = _project(mcf_136)
    doc["ops"] = [{"id": "bad", "type": "rename_label",
                   "params": {"object": 4, "old": "no such label anywhere", "new": "x"}}]
    proj.write_text(json.dumps(doc))
    out = tmp_path / "mcf-test.bin"
    assert main(["compile", str(proj), STOCK, "-o", str(out)]) == 1
    assert not out.exists()
    assert "NO_SUCH_LABEL" in capsys.readouterr().err


def test_compile_refuses_to_overwrite(tmp_path, mcf_136):
    proj = tmp_path / "p.ffproj.json"
    proj.write_text(json.dumps(_project(mcf_136)))
    out = tmp_path / "mcf-test.bin"
    out.write_bytes(b"x")
    assert main(["compile", str(proj), STOCK, "-o", str(out)]) == 2
    assert out.read_bytes() == b"x"


def _unverified_project(mcf_136):
    doc = _project(mcf_136)
    doc["ops"] = [{"id": "gone", "type": "remove_control", "params": {"object": 293}}]
    return doc


def test_compile_refuses_an_unverified_op_by_default(tmp_path, mcf_136, capsys):
    """Conservative default: an op no coach has run is the owner's decision, so `compile`
    writes nothing until it is acknowledged with --allow-unverified."""
    proj = tmp_path / "p.ffproj.json"
    proj.write_text(json.dumps(_unverified_project(mcf_136)))
    out = tmp_path / "mcf-test.bin"
    assert main(["compile", str(proj), STOCK, "-o", str(out)]) == 1
    assert not out.exists()
    err = capsys.readouterr().err
    assert "UNVERIFIED" in err and "gone" in err and "--allow-unverified" in err


def test_allow_unverified_builds_and_records_it(tmp_path, mcf_136):
    proj = tmp_path / "p.ffproj.json"
    proj.write_text(json.dumps(_unverified_project(mcf_136)))
    out = tmp_path / "mcf-test.bin"
    assert main(["compile", str(proj), STOCK, "-o", str(out), "--allow-unverified"]) == 0
    side = json.loads((tmp_path / "mcf-test.bin.json").read_text())
    assert side["unverified"] == ["gone"]
