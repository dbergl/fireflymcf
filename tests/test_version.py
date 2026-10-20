"""`scripts/check_version.py`: the release gate on the tag and the two version fields."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_version",
                                               ROOT / "scripts" / "check_version.py")
cv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cv)


def test_the_package_and_the_plugin_name_one_version():
    found = cv.versions()
    assert cv.problems(None, found) == [], found


def test_a_matching_tag_passes_and_others_fail():
    found = {"pyproject.toml": "0.2.0", "plugin/.claude-plugin/plugin.json": "0.2.0"}
    assert cv.problems("v0.2.0", found) == []
    assert "does not match" in cv.problems("v0.2.1", found)[0]
    assert "does not match" in cv.problems("0.2.0", found)[0]


def test_a_bump_in_one_file_only_fails():
    found = {"pyproject.toml": "0.2.0", "plugin/.claude-plugin/plugin.json": "0.1.0"}
    assert "disagree" in cv.problems(None, found)[0]


def test_main_exit_codes(capsys):
    version = cv.versions()["pyproject.toml"]
    assert cv.main(["v" + version]) == 0
    assert cv.main(["v999.0.0"]) == 1
    assert "does not match" in capsys.readouterr().err
