"""Every committed reference-coach recipe satisfies the project schema (research data)."""
import glob
import json

import pytest

from fireflymcf import ops_doc

jsonschema = pytest.importorskip("jsonschema")
RECIPES = sorted(glob.glob("builds/*.ffproj.json"))


@pytest.mark.parametrize("path", RECIPES)
def test_recipe_satisfies_the_schema(path):
    validator = jsonschema.Draft202012Validator(ops_doc.project_schema())
    errors = [(list(e.absolute_path), e.message)
              for e in validator.iter_errors(json.loads(open(path).read()))]
    assert errors == []


def test_there_are_recipes_to_check():
    assert len(RECIPES) >= 20
