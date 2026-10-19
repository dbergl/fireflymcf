"""`cli options` -- the human table and `--json` form of the OPTIONS-page binding map
(`src/fireflymcf/options.py`'s `option_bindings`/`floorplan_options`), Task 4.

Known-truth values reused from `tests/test_options.py`: 13.6's Bath Fan is value_slot 109,
register 0x20039B49; floorplan value 8 is "WD" (`guidata.find_floorplan_table`, cited there
too). This file only exercises the CLI's formatting/plumbing, not the binding logic itself.
"""
from __future__ import annotations

import json

from fireflymcf.cli import main


def test_options_command_lists_bindings_and_floorplans(capsys):
    rc = main(["options", "firefly-13.6/mcf.bin"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Bath Fan" in out
    assert "0x20039B49" in out
    assert "WD" in out
    # Falsifiability: a name that is not an OPTIONS-page binding on 13.6 (it's a
    # floorplan CODE, not an option name) must not appear as if it were one.
    assert "Starlink" not in out


def test_options_command_json_matches_known_values(capsys):
    rc = main(["options", "firefly-13.6/mcf.bin", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)

    by_name = {o["name"]: o for o in payload["options"]}
    assert by_name["Bath Fan"]["value_slot"] == 109
    assert by_name["Bath Fan"]["register"] == "0x20039B49"

    assert payload["floorplans"]["8"] == "WD"

    # Falsifiability: this build has no option literally named "Starlink Power" (that
    # is 14.7's example from CLAUDE.md) -- a fitted/stubbed implementation that always
    # reported every known label regardless of build would wrongly include it here.
    assert "Starlink Power" not in by_name


def test_options_command_json_prints_nothing_else(capsys):
    """`--json` mode must print ONLY the JSON object -- no table, no banner text that
    would break a naive `json.loads(stdout)` caller."""
    rc = main(["options", "firefly-13.6/mcf.bin", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    # The whole of stdout must parse as exactly one JSON value.
    json.loads(out)
    assert "option binding(s)" not in out


def test_options_command_recovers_the_floorplan_map_on_1123(capsys):
    """11.23's value->code map comes off its floorplan-name switch (objects 852/853 --
    hidden until 2026-08-30, when the hardware-confirmed 0-based map let the oracle match
    it; see BUS-BASELINE PC.40), so the command lists the floorplans instead of reporting
    the map unavailable -- while still reporting the option bindings it has. The values
    are the wire-confirmed ones: 0=Ship Mode, 5=3WD/4WD."""
    rc = main(["options", "firefly-11.23/mcf.bin"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Renogy Battery" in out
    assert "3WD / 4WD" in out and "Ship Mode" in out

    rc = main(["options", "firefly-11.23/mcf.bin", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["floorplans"]["5"] == "3WD / 4WD"
    assert payload["floorplans"]["0"] == "Ship Mode"
    assert any(o["name"] == "Renogy Battery" for o in payload["options"])


def test_options_command_sorted_by_name(capsys):
    rc = main(["options", "firefly-13.6/mcf.bin", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    names = [o["name"] for o in payload["options"]]
    assert names == sorted(names)
