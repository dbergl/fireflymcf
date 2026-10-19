import hashlib
from pathlib import Path

import pytest

from fireflymcf import recipe, validate

REPO_ROOT = Path(__file__).resolve().parents[1]
V26 = REPO_ROOT / "builds" / "experiment-v26-coach48v.ffproj.json"


def _project(path):
    return recipe.Project.from_json(Path(path).read_text())


def test_validate_passes_a_known_good_build(mcf_136):
    res = validate.validate(_project(V26), mcf_136)
    assert res.passed, res.report
    assert res.errors == []
    assert "PASS" in res.report


def test_validate_fails_on_base_mismatch(mcf_1123):
    # v26 is pinned to 13.6; handing it 11.23 must FAIL, not raise.
    res = validate.validate(_project(V26), mcf_1123)
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
    res = validate.validate(_project(V26), mcf_136, require_verified=True)
    assert not res.passed
    assert "some-op" in res.report

    res2 = validate.validate(_project(V26), mcf_136, require_verified=False)
    assert res2.passed  # unverified is reported, not fatal, by default
    assert res2.unverified == ["some-op"]


def test_resolve_stock_finds_the_pinned_corpus_image():
    stock = validate.resolve_stock(_project(V26), REPO_ROOT)
    assert hashlib.sha256(stock).hexdigest() == _project(V26).base_sha256


def test_resolve_stock_raises_when_no_corpus_matches(tmp_path):
    with pytest.raises(recipe.RecipeError) as exc:
        validate.resolve_stock(_project(V26), tmp_path)
    assert exc.value.code == "BASE_MISMATCH"
