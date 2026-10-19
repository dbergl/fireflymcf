"""The project file's contract: unknown op params are refused, and `fireflymcf schema` emits a
JSON Schema that every committed recipe satisfies."""
import json

import pytest

from fireflymcf import cli, recipe


def test_an_unknown_param_is_refused_not_ignored():
    # A typo ("allow_grwo") used to be silently dropped, building something else.
    spec = recipe.OpSpec(id="r", type="rename_label",
                         params={"object": 4, "old": "a", "new": "b", "allow_grwo": True})
    with pytest.raises(recipe.RecipeError) as exc:
        recipe.REGISTRY["rename_label"](spec)
    assert exc.value.code == "BAD_PARAMS" and "allow_grwo" in str(exc.value)
    assert "allow_grow" in str(exc.value)          # names what it does take


jsonschema = pytest.importorskip("jsonschema")

from fireflymcf import ops_doc  # noqa: E402
from pathlib import Path  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def validator():
    schema = ops_doc.project_schema()
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


def _doc(*ops):
    return {"schema": 1, "base_sha256": "0" * 64, "build_name": "x", "ops": list(ops)}


def test_committed_schema_matches_the_generator():
    committed = json.loads((ROOT / "schema" / "ffproj.schema.json").read_text())
    assert committed == ops_doc.project_schema(), \
        "schema/ffproj.schema.json is stale: run python3 -m fireflymcf.ops_doc"


def test_cli_prints_the_schema(capsys):
    assert cli.main(["schema"]) == 0
    assert json.loads(capsys.readouterr().out) == ops_doc.project_schema()


def test_a_good_project_passes(validator):
    doc = _doc({"id": "p", "type": "rename_label",
                "params": {"object": 295, "old": "Water\nPump", "new": "Pump"},
                "note": "n", "enabled": True})
    assert list(validator.iter_errors(doc)) == []


@pytest.mark.parametrize("op, why", [
    ({"id": "p", "type": "rename_label",
      "params": {"object": 1, "old": "a", "new": "b", "allow_grwo": True}}, "allow_grwo"),
    ({"id": "p", "type": "rename_label", "params": {"object": 1, "old": "a"}}, "'new'"),
    ({"id": "p", "type": "no_such_op", "params": {}}, "no_such_op"),
    ({"id": "p", "type": "rename_label", "params": {"object": "x", "old": "a", "new": "b"}},
     "'x'"),
])
def test_what_the_engine_refuses_the_schema_refuses(validator, op, why):
    errors = list(validator.iter_errors(_doc(op)))
    assert errors and any(why in e.message for e in errors), [e.message for e in errors]
