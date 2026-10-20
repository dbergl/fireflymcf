"""`fireflymcf verify`: the whole-build comparison against stock (`buildcheck`)."""
import hashlib
import json
from pathlib import Path

import pytest

from fireflymcf import buildcheck, cli, options, patch, recipe

STOCK = Path("firefly-13.6/mcf.bin")
WD = options.CoachConfig(floorplan="WD")
HOME = 4


@pytest.fixture(scope="module")
def stock():
    return STOCK.read_bytes()


@pytest.fixture(scope="module")
def renamed(stock):
    project = recipe.Project.from_json(json.dumps({
        "schema": 1, "base_sha256": hashlib.sha256(stock).hexdigest(),
        "build_name": "Jayco B-Van V13 verify test",
        "ops": [{"id": "pump", "type": "rename_label",
                 "params": {"object": 295, "old": "Water\nPump", "new": "Pump",
                            "align": "center"}}]}))
    result = recipe.compile_project(project, stock)
    assert result.ok, result.errors
    return result.data


def test_stock_against_itself_differs_nowhere(stock):
    r = buildcheck.compare(stock, stock, {"WD": WD}, [HOME])
    assert r.runs == [] and all(s.status == "same" for s in r.sections)
    assert not any(o.changed or o.added or o.removed for o in r.objects)
    assert [p.bbox for p in r.pages] == [None]


def test_a_rename_lands_on_its_object_and_its_page(stock, renamed):
    r = buildcheck.compare(stock, renamed, {"WD": WD}, [HOME, 5])
    assert [run.region for run in r.runs] == [
        "header (build name)", "header checksum", "GuiData object",
        "section checksum (Lyra 13v6 GuiData)"]
    assert r.runs[2].object == 295 and r.unexplained == []
    assert [(o.table, o.changed) for o in r.objects if o.changed] == [(1, [295])]
    home, electrical = r.pages
    assert home.bbox is not None and 491 <= home.bbox[0] and home.bbox[2] <= 595
    assert electrical.bbox is None
    assert {s.name: s.status for s in r.sections}["G12"] == "same"


def test_object_zero_is_not_blamed_for_the_section_checksum(stock, renamed):
    # Object 0 of each table used to run to the section end, checksum included.
    r = buildcheck.compare(stock, renamed, render_pages=False)
    assert all(0 not in o.changed for o in r.objects)


def test_a_change_outside_guidata_is_reported_in_its_section(stock):
    g12 = next(s for s in __import__("fireflymcf.container").container.parse_container(
        stock).sections if s.name == "G12")
    data = bytearray(stock)
    data[g12.offset + 0x100] ^= 0xFF
    resealed, _ = patch.reseal(bytes(data))
    r = buildcheck.compare(stock, resealed, render_pages=False)
    assert {s.name: s.status for s in r.sections}["G12"] == "changed"
    assert [run.region for run in r.runs if run.offset == g12.offset + 0x100] == ["section"]
    assert all(run.object is None for run in r.runs)

    # Not resealed: the panel would reject it, and verify says so instead of crashing.
    r = buildcheck.compare(stock, bytes(data), render_pages=False)
    assert any("checksum" in p for p in r.problems)


def test_pages_include_the_outputs_list(stock):
    roots = [root for _, root in buildcheck.pages(stock)]
    assert HOME in roots and cli._resolve_page_root(stock, "outputs") in roots


def test_cli_exits_1_when_the_build_name_is_stocks(tmp_path, stock, capsys):
    assert cli.main(["verify", str(STOCK), str(STOCK), "--no-render"]) == 1
    assert "boot menu cannot tell them apart" in capsys.readouterr().out


def test_cli_json(tmp_path, renamed, capsys):
    build = tmp_path / "mcf-t.bin"
    build.write_bytes(renamed)
    cfg = tmp_path / "wd.json"
    cfg.write_text('{"floorplan": "WD"}')
    assert cli.main(["verify", str(build), str(STOCK), "--config", str(cfg),
                     "--page", "home", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["build_name"] == "Jayco B-Van V13 verify test"
    assert out["pages"][0]["config"] == "wd" and out["pages"][0]["bbox"]
