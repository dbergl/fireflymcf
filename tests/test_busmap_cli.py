"""`fireflymcf bus-map`'s text output: a summary by default, a name search, everything on --all.

An agent reading the default output on a whole file could not use 800 lines; the summary has
to fit, and `--name` has to find a control by what the panel calls it."""
from fireflymcf import cli

STOCK = "firefly-13.6/mcf.bin"


def out_of(capsys, *argv):
    assert cli.main(["bus-map", STOCK, *argv]) == 0
    return capsys.readouterr().out


def test_the_default_is_a_summary_that_says_how_to_see_more(capsys):
    out = out_of(capsys)
    assert len(out.splitlines()) < 150
    assert "RECEIVE" in out and "SEND" in out
    assert "--name" in out and "--all" in out
    assert "Binding groups" not in out


def test_name_finds_what_the_panel_calls_water_pump_on_both_sides(capsys):
    out = out_of(capsys, "--name", "water pump")
    assert "Water Pump" in out
    rows = [l for l in out.splitlines() if l.lstrip().startswith(("44 ", "86 "))]
    assert any("1FEDA" in l for l in rows)               # receive: its status, var 86
    assert any("reached" in l for l in rows)             # send: binding group 44
    assert "Fresh" not in out and "Awning" not in out    # nothing that is not a water pump


def test_all_lists_every_entry_and_group(capsys):
    out = out_of(capsys, "--all")
    assert "Binding groups" in out and "GuiCode sends" in out
    assert len(out.splitlines()) > 500
