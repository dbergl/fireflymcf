"""`docs/CHANGE-CATALOG.md` is the owner-facing, intent-grouped catalogue of changes.

Unlike `docs/RECIPE-OPS.md` it is hand-written (it folds multi-op recipes into one entry and
frames each change in plain language), so nothing regenerates it. This test is the guard that
keeps it from silently falling behind the code: every op in the registry must be named in it,
as a code span, so a newly added op cannot ship without an owner-facing catalogue entry.
"""
from pathlib import Path

from fireflymcf import recipe

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "CHANGE-CATALOG.md"


def _uncatalogued(text: str, registry: dict) -> set[str]:
    """Op names from `registry` that never appear in `text` as a `code span`."""
    return {name for name in registry if "`%s`" % name not in text}


def test_every_registered_op_is_in_the_catalogue():
    text = DOC.read_text()
    missing = _uncatalogued(text, recipe.REGISTRY)
    assert not missing, (
        "docs/CHANGE-CATALOG.md does not mention these ops: %s. Add an owner-facing entry "
        "(intent + choices + status) for each, referencing it as `op_name`." % sorted(missing))


def test_the_coverage_check_can_fail():
    """Guard the guard: a bogus op absent from the doc must be reported."""
    text = DOC.read_text()
    missing = _uncatalogued(text, dict(recipe.REGISTRY, not_a_real_op=object))
    assert missing == {"not_a_real_op"}


def test_catalogue_links_to_the_op_reference():
    """The catalogue is a discovery layer over RECIPE-OPS.md; it must point there."""
    text = DOC.read_text()
    assert "RECIPE-OPS.md#" in text
