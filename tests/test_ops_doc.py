"""`docs/RECIPE-OPS.md` is generated; this pins it to the generator and the generator to the registry."""
from pathlib import Path

import pytest

import json

from fireflymcf import cli, ops_doc, recipe

ROOT = Path(__file__).resolve().parents[1]


def test_every_registered_op_has_a_note():
    assert set(recipe.REGISTRY) <= set(ops_doc.OP_NOTES)
    assert set(ops_doc.OP_NOTES) <= set(recipe.REGISTRY), "note for an op that no longer exists"
    for name, (klass, purpose, status, proof) in ops_doc.OP_NOTES.items():
        assert klass in {c[0] for c in ops_doc.CLASSES}, name
        assert purpose.endswith(".") and status and proof, name


def test_committed_doc_matches_the_generator():
    committed = (ROOT / "docs" / "RECIPE-OPS.md").read_text()
    assert committed == ops_doc.render(), \
        "docs/RECIPE-OPS.md is stale: run python3 -m fireflymcf.ops_doc"


def test_generator_refuses_an_undocumented_op():
    class Fake(recipe.Op):
        pass
    with pytest.raises(KeyError, match="fake_op"):
        ops_doc.render(dict(recipe.REGISTRY, fake_op=Fake))


def test_error_codes_are_parsed_from_the_docstring():
    codes = dict(ops_doc._error_codes(recipe.REGISTRY["rename_label"]))
    assert {"NO_SUCH_LABEL", "AMBIGUOUS_LABEL", "LABEL_TOO_LONG", "GROW_REFUSED"} <= set(codes)
    assert "offers" in codes["LABEL_TOO_LONG"]


def test_the_doc_does_not_read_the_research_repo():
    # The public reference is generated from the package alone: no coach's recipe feeds it.
    assert not hasattr(ops_doc, "RUNNING_RECIPE")
    assert "builds/" not in ops_doc.render()


def test_ops_lists_every_op_with_its_verified_flag(capsys):
    assert cli.main(["ops"]) == 0
    out = capsys.readouterr().out
    for name in recipe.REGISTRY:
        assert name in out
    line = next(l for l in out.splitlines() if l.startswith("remove_control "))
    assert " NO " in line


def test_ops_name_prints_params_and_error_codes(capsys):
    assert cli.main(["ops", "rename_label"]) == 0
    out = capsys.readouterr().out
    assert "allow_grow" in out and "LABEL_TOO_LONG" in out and "(required)" in out


def test_ops_json_is_the_entries(capsys):
    assert cli.main(["ops", "--json", "remove_control"]) == 0
    [entry] = json.loads(capsys.readouterr().out)
    assert entry["name"] == "remove_control" and entry["verified"] is False
    assert entry["params"] == recipe.REGISTRY["remove_control"].PARAMS


def test_ops_refuses_an_unknown_name(capsys):
    assert cli.main(["ops", "no_such_op"]) == 1
    assert "no such op" in capsys.readouterr().err
