"""The write-new-op skill's worked example is a working op, end to end through the CLI.

An agent copies `plugin/skills/write-new-op/examples/counter-op/` as the shape of its own op, so
the example must load, compile on stock 13.6 with `--allow-user-ops`, pass `verify`, and pass its
own emulator test through the gate (`verify --emulate --project --allow-user-ops`). Its test must
also be able to fail: on stock (no counter) it does.
"""
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from fireflymcf import recipe, userop
from fireflymcf.cli import main

NEEDS_RESEARCH_DATA = "compiles the skill's example op on the 13.6 corpus image and emulates it"

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "plugin" / "skills" / "write-new-op" / "examples" / "counter-op"
STOCK = "firefly-13.6/mcf.bin"


def _project(tmp_path: Path, stock: bytes) -> Path:
    shutil.copytree(EXAMPLE, tmp_path / "ops" / "counter-op",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    pf = tmp_path / "counter.ffproj.json"
    pf.write_text(json.dumps({
        "schema": recipe.SCHEMA, "base_sha256": hashlib.sha256(stock).hexdigest(),
        "build_name": "Jayco B-Van V13 counter example", "preview": {},
        "ops": [{"id": "count", "type": "user:tick-counter", "params": {"step": 3}}]}))
    return pf


def test_skill_example_manifest_is_complete():
    m = json.loads((EXAMPLE / "manifest.json").read_text())
    assert m["type"].startswith("user:") and m["touches"] == ["guicode"]
    assert {"build_name", "base_sha256", "recipe_ops_required"} <= set(m["written_against"][0])
    assert {"kind", "base_sha256", "date", "by", "note"} <= set(m["provenance"][0])
    assert {p.name for p in EXAMPLE.iterdir() if not p.name.startswith("__")} == {
        "manifest.json", "op.py", "emu_test.py", "README.md"}
    # discovery reads the manifest the way the engine does, without importing op.py
    d = userop._read_manifest(EXAMPLE)
    assert d["type"] == "user:tick-counter"


@pytest.mark.slow
def test_skill_example_compiles_and_emulates(tmp_path, mcf_136, capsys):
    pytest.importorskip("unicorn")
    pf = _project(tmp_path, mcf_136)
    out = tmp_path / "mcf-counter.bin"
    assert main(["compile", str(pf), STOCK, "-o", str(out), "--allow-user-ops"]) == 0, \
        capsys.readouterr()
    capsys.readouterr()
    side = json.loads(Path(str(out) + ".json").read_text())
    assert [u["type"] for u in side["user_ops"]] == ["user:tick-counter"]
    assert main(["verify", str(out), STOCK, "--no-render", "--emulate",
                 "--project", str(pf), "--allow-user-ops"]) == 0, capsys.readouterr()
    emu = json.loads(Path(str(out) + ".json").read_text())["emulation"]
    assert emu["user:tick-counter"]["status"] == "passed", emu


def test_skill_example_test_fails_on_stock(tmp_path, mcf_136):
    pytest.importorskip("unicorn")
    from fireflymcf.emu import gate
    res = gate.run_scenario_file(mcf_136, EXAMPLE / "emu_test.py", timeout_s=240)
    assert res.status == "failed" and "no counter" in res.reason, res
