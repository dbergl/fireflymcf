"""scripts/golden.py: record and check the compiled-image hash of every project."""
import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STOCK = ROOT / "firefly-13.6" / "mcf.bin"


def _golden():
    spec = importlib.util.spec_from_file_location("golden", ROOT / "scripts" / "golden.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def world(tmp_path):
    stock_dir = tmp_path / "stocks" / "a"
    stock_dir.mkdir(parents=True)
    shutil.copy(STOCK, stock_dir / "mcf.bin")
    sha = hashlib.sha256(STOCK.read_bytes()).hexdigest()
    proj = tmp_path / "empty.ffproj.json"
    proj.write_text(json.dumps({"schema": 1, "base_sha256": sha,
                                "build_name": "golden test", "ops": []}), encoding="utf-8")
    return tmp_path, stock_dir.parent, proj


def test_record_then_check_passes(world):
    tmp, stocks, proj = world
    table = tmp / "t.json"
    g = _golden()
    assert g.main(["record", str(table), str(stocks), str(proj)]) == 0
    assert g.main(["check", str(table), str(stocks), str(proj)]) == 0


def test_check_reports_mismatch(world, capsys):
    tmp, stocks, proj = world
    table = tmp / "t.json"
    g = _golden()
    g.main(["record", str(table), str(stocks), str(proj)])
    doc = json.loads(table.read_text(encoding="utf-8"))
    doc[str(proj)] = "0" * 64
    table.write_text(json.dumps(doc), encoding="utf-8")
    assert g.main(["check", str(table), str(stocks), str(proj)]) == 1
    assert str(proj) in capsys.readouterr().out


def test_unknown_stock_names_project(world):
    tmp, stocks, proj = world
    other = tmp / "none"
    other.mkdir()
    with pytest.raises(SystemExit) as e:
        _golden().compile_sha(proj, other)
    assert "empty.ffproj.json" in str(e.value)
