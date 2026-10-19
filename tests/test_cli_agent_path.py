"""The two CLI readouts an agent needs to author a recipe from the command line:
`labels --containing` names the OBJECT holding each match (what `rename_label` takes), and
`render --placements FILE` writes the page's hit map (object index per placed record)."""
from __future__ import annotations

import json

from fireflymcf import layout
from fireflymcf.cli import _object_of, main

STOCK = "firefly-13.6/mcf.bin"


def test_object_of_maps_a_text_offset_to_the_object_holding_it(mcf_136):
    screen = layout.Screen(mcf_136)
    # HOME root is object 4 on 13.6; its first record byte is at gs + table[4].
    start = screen.gs + screen.table[4]
    assert _object_of(screen, start) == 4
    assert _object_of(screen, start + 1) == 4
    # One byte before the object belongs to whichever object precedes it, never to 4.
    assert _object_of(screen, start - 1) != 4


def test_labels_prints_the_object_index(capsys):
    assert main(["labels", STOCK, "--containing", "Water Pump"]) == 0
    out = capsys.readouterr().out
    assert "object" in out.splitlines()[1]
    rows = [l for l in out.splitlines()[2:] if l.strip()]
    assert rows, out
    for row in rows:
        cols = row.split()
        assert cols[2].isdigit(), row      # text off, len, object, field...


def test_render_placements_writes_the_hit_map(tmp_path, mcf_136):
    png, js = tmp_path / "home.png", tmp_path / "home.json"
    # Floorplan slot 5 = 1: HOME's Water Pump / Tank Heater pair is switch-selected on the
    # floorplan, so an unconfigured render places neither (OBJECT-HIERARCHY.md §3).
    assert main(["render", STOCK, str(png), "--root", "4", "--slot", "5=1",
                 "--placements", str(js)]) == 0
    body = json.loads(js.read_text())
    assert body["root"] == 4 and body["canvas"] == [800, 480]
    assert body["slot_values"]["5"] == 1
    labels = {p["label"] for p in body["placements"] if p["label"]}
    assert "Water\nPump" in labels, labels
    p = next(p for p in body["placements"] if p["label"] == "Water\nPump")
    # Two objects carry this caption (295 and 296, two switch arms); the hit map names the
    # one the walk placed, and `_object_of` agrees with it from the label bytes alone.
    assert p["obj_index"] == _object_of(layout.Screen(mcf_136), p["label_offset"])
    assert p["label_offset"] is not None
    assert set(p) >= {"obj_index", "record_offset", "wtype", "box", "label", "label_offset",
                      "font", "value_slot"}
    x0, y0, x1, y1 = p["box"]
    assert 0 <= x0 <= x1 <= 800 and 0 <= y0 <= y1 <= 480
