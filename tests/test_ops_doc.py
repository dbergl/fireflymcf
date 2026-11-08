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


def test_ops_name_header_says_verified_per_firmware(capsys):
    """The header follows `verified_on` (empty when hardware_verified is False), not the class
    default: an op enabled on 14.7 by the emulator alone is verified on 13.6 only."""
    for name, want in [("rename_label", "verified: 13.6"), ("remove_control", "verified: none")]:
        assert cli.main(["ops", name]) == 0
        head = capsys.readouterr().out.splitlines()[0]
        assert head == f"{name}  ({ops_doc.OP_NOTES[name][0]}; {want})"
    assert "14.7" in recipe.REGISTRY["append_filter"].firmwares
    assert cli.main(["ops", "append_filter"]) == 0
    assert "verified: 13.6)" in capsys.readouterr().out.splitlines()[0]


def test_ops_json_is_the_entries(capsys):
    assert cli.main(["ops", "--json", "remove_control"]) == 0
    [entry] = json.loads(capsys.readouterr().out)
    assert entry["name"] == "remove_control" and entry["verified"] is False
    assert entry["params"] == recipe.REGISTRY["remove_control"].PARAMS


def test_ops_refuses_an_unknown_name(capsys):
    assert cli.main(["ops", "no_such_op"]) == 1
    assert "no such op" in capsys.readouterr().err


def test_entries_carry_firmware_support():
    e = {x["name"]: x for x in ops_doc.entries(recipe.REGISTRY)}
    assert e["set_theme"]["firmwares"] == ["13.6", "14.7"]
    assert e["set_theme"]["verified_on"] == ["13.6"] and e["set_theme"]["emulated_on"] == ["14.7"]
    assert e["set_gesture"]["firmwares"] == ["13.6"] and e["set_gesture"]["emulated_on"] == []
    assert "| Firmware |" in ops_doc.render(recipe.REGISTRY)


def test_an_unverified_op_is_verified_on_no_firmware():
    e = {x["name"]: x for x in ops_doc.entries(recipe.REGISTRY)}
    unverified = [n for n, x in e.items() if not x["verified"]]
    assert unverified
    for n in unverified:
        assert e[n]["verified_on"] == [], n


def _firmware_set_problems(registry):
    """Every op's verification and emulation lie inside the firmwares it may compile on. An op
    whose class default is unverified is verified nowhere (`Op.is_verified`), so its effective
    verified set is empty; its `verified_on` must still name only listed firmwares."""
    out = []
    for name, cls in sorted(registry.items()):
        verified = set(cls.verified_on) if getattr(cls, "hardware_verified", True) else set()
        for label, have in (("verified_on", set(cls.verified_on)), ("verified", verified),
                            ("emulated_on", set(cls.emulated_on))):
            if not have <= set(cls.firmwares):
                out.append(f"{name}: {label} {sorted(have)} not within "
                           f"firmwares {sorted(cls.firmwares)}")
    return out


def test_every_op_verifies_and_emulates_only_where_it_compiles():
    assert _firmware_set_problems(recipe.REGISTRY) == []


def test_the_firmware_set_check_can_fail():
    class Stray(recipe.Op):
        firmwares = frozenset({"13.6"})
        emulated_on = frozenset({"14.7"})

    class StrayVerified(recipe.Op):
        verified_on = frozenset({"13.6", "14.7"})
    assert _firmware_set_problems({"stray": Stray}) == [
        "stray: emulated_on ['14.7'] not within firmwares ['13.6']"]
    assert len(_firmware_set_problems({"sv": StrayVerified})) == 2
