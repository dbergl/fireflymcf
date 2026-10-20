import hashlib
import json
from pathlib import Path

import pytest

from fireflymcf import recipe, validate

REPO_ROOT = Path(__file__).resolve().parents[1]
STOCK_136 = REPO_ROOT / "firefly-13.6" / "mcf.bin"


def _project(_=None):
    """A one-op project on stock 13.6: a verified rename that compiles and validates clean."""
    doc = {"schema": recipe.SCHEMA,
           "base_sha256": hashlib.sha256(STOCK_136.read_bytes()).hexdigest(),
           "build_name": "Jayco B-Van V13 validate test",
           "ops": [{"id": "pump", "type": "rename_label",
                    "params": {"object": 295, "old": "Water\nPump", "new": "Pump",
                               "align": "center"}}]}
    return recipe.Project.from_json(json.dumps(doc))


def test_validate_passes_a_known_good_build(mcf_136):
    res = validate.validate(_project(), mcf_136)
    assert res.passed, res.report
    assert res.errors == []
    assert "PASS" in res.report


def test_validate_fails_on_base_mismatch(mcf_1123):
    # the project is pinned to 13.6; handing it 11.23 must FAIL, not raise.
    res = validate.validate(_project(), mcf_1123)
    assert not res.passed
    assert "FAIL" in res.report
    assert res.errors


def test_require_verified_fails_when_an_op_is_unverified(mcf_136, monkeypatch):
    # Force a non-empty unverified list without needing an actually-unverified build.
    real = recipe.compile_project

    def fake(project, stock):
        r = real(project, stock)
        return recipe.CompileResult(r.ok, r.data, r.errors, r.manifest, ["some-op"])

    monkeypatch.setattr(recipe, "compile_project", fake)
    res = validate.validate(_project(), mcf_136, require_verified=True)
    assert not res.passed
    assert "some-op" in res.report

    res2 = validate.validate(_project(), mcf_136, require_verified=False)
    assert res2.passed  # unverified is reported, not fatal, by default
    assert res2.unverified == ["some-op"]


def test_resolve_stock_finds_the_pinned_corpus_image():
    stock = validate.resolve_stock(_project(), REPO_ROOT)
    assert hashlib.sha256(stock).hexdigest() == _project().base_sha256


def test_resolve_stock_raises_when_no_corpus_matches(tmp_path):
    with pytest.raises(recipe.RecipeError) as exc:
        validate.resolve_stock(_project(), tmp_path)
    assert exc.value.code == "BASE_MISMATCH"
