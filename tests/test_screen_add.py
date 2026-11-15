"""`add_screen`: a new screen id and root object, by rebuilding the screen switch's band at the
top of object table 1 (GUICODE Finding 93). Every stock screen keeps its root object; the band
holds one wt-5 trampoline per stock screen, then the new root."""
import json
import struct

import numpy as np
import pytest

from fireflymcf import guidata, layout, recipe, records, render, screenadd

STOCK = "firefly-13.6/mcf.bin"
STOCK_SHA = "d66b41ffafaae4f5ec47cf90714f14bb1954fc3a5ae38defa98a94cd9643d382"


def _switch(data):
    gs, _ = guidata.guidata_extent(data)
    return layout._screen_switch_record(data, guidata.find_match_table(data), gs)


@pytest.fixture(scope="module")
def stock():
    return open(STOCK, "rb").read()


@pytest.fixture(scope="module")
def added(stock):
    return screenadd.add_screen(stock, screenadd.blank_root(stock, "BATTERY SETUP"))


def test_returns_new_id_and_root(stock, added):
    data, sid, root = added
    assert sid == 35
    assert root == len(guidata.object_tables(data)[1]) - 1     # the last object appended


def test_band_moved(stock, added):
    data, _sid, _root = added
    sw = _switch(data)
    assert sw.child_index == len(guidata.object_tables(stock)[1])
    assert sw.count == 48


def test_trampolines_preserve_map(stock, added):
    data, sid, root = added
    want = dict(layout.screen_object_map(stock))
    want[sid] = root
    assert layout.screen_object_map(data) == want


def test_band_entries_are_trampolines(stock, added):
    data, sid, root = added
    sw = _switch(data)
    band = guidata.switch_candidates(data, sw)
    stock_band = guidata.switch_candidates(stock, _switch(stock))
    assert [v for v, _ in band] == [v for v, _ in stock_band] + [sid]
    for (_v, obj), (_sv, sobj) in zip(band, stock_band):
        assert screenadd.original_root(data, obj) == sobj
        assert obj != sobj                                   # a trampoline, not the root itself
    assert band[-1][1] == root


def test_default_first(stock, added):
    data, _sid, _root = added
    value, obj = guidata.switch_candidates(data, _switch(data))[0]
    assert value == 0xFFFF
    assert screenadd.original_root(data, obj) == 3


def test_twice(stock, added):
    data, sid1, root1 = added
    data2, sid2, root2 = screenadd.add_screen(data, screenadd.blank_root(data, "SECOND"))
    assert (sid1, sid2) == (35, 36)
    sw = _switch(data2)
    first_base = _switch(data).child_index
    for _v, obj in guidata.switch_candidates(data2, sw)[:-1]:
        target = screenadd.original_root(data2, obj)
        assert target < first_base or target == root1       # never a trampoline of the old band
    m = layout.screen_object_map(data2)
    assert (m[sid1], m[sid2]) == (root1, root2)


def test_static_render_unchanged(stock, added):
    data, _sid, _root = added
    for sid, root in layout.screen_object_map(stock).items():
        a = render.render_screen_image(stock, root)
        b = render.render_screen_image(data, root)
        assert np.array_equal(np.asarray(a), np.asarray(b)), f"screen {sid} root {root} differs"


def test_blank_root_title(stock, added):
    data, _sid, root = added
    gs, _ = guidata.guidata_extent(data)
    labels = [r.label for r in records.object_records(data, gs + guidata.object_tables(data)[1][root])
              if getattr(r, "label", None)]
    assert any(lab.strip() == "BATTERY SETUP" for lab in labels)


def test_title_too_long(stock):
    with pytest.raises(screenadd.ScreenAddError):
        screenadd.blank_root(stock, "A TITLE FAR TOO LONG FOR THE HEADER")


def _project(params):
    return recipe.Project.from_json(json.dumps({
        "schema": 1, "base_sha256": STOCK_SHA, "build_name": "add-screen-test",
        "ops": [{"id": "screen", "type": "add_screen", "params": params, "enabled": True}],
    }))


def test_op_compiles_coach_verified(stock):
    res = recipe.compile_project(_project({"title": "BATTERY SETUP"}), stock)
    assert res.ok, res.errors
    assert "screen" not in res.unverified                  # coach-verified 2026-11-14
    assert layout.screen_object_map(res.data)[35] == len(guidata.object_tables(res.data)[1]) - 1
