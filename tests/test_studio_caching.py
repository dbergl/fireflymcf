"""The studio server's per-request and per-etag caching — the page-switch latency fix.

A placements request used to re-derive every expensive structure per OBJECT: the
object-pointer-table scan once per control (via gestures.touch_ids) and the glyph-table
scan once per text record (via metrics.text_width). These tests pin the fix at three
levels: the thread-through hook on touch_ids, the per-request resolve-once behaviour of
Engine.placements, and the per-knob-tag memo that makes a revisited page free.

Counting monkeypatches wrap the REAL functions (results still come from the bytes), so
each test still checks the output is right, not just that something was cached.
"""
import pytest
from fastapi.testclient import TestClient

from studio.server.engine import Engine
from fireflymcf import cli, gestures, guidata

STOCK_PATH = "firefly-13.6/mcf.bin"


@pytest.fixture(scope="module")
def stock():
    with open(STOCK_PATH, "rb") as f:
        return f.read()


@pytest.fixture(scope="module")
def home_root(stock):
    return cli._resolve_page_root(stock, "home")


@pytest.fixture()
def client(tmp_path):
    from studio.server.app import create_app
    app = create_app(stock_path=STOCK_PATH, projects_root=str(tmp_path / "projects"),
                     build_root=str(tmp_path / "build"))
    return TestClient(app, base_url="http://localhost")


def _count_calls(monkeypatch, module, name):
    """Wrap module.name so calls are counted but still answered by the real function."""
    real = getattr(module, name)
    counter = {"n": 0}

    def wrapper(*args, **kwargs):
        counter["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(module, name, wrapper)
    return counter


def test_touch_ids_accepts_a_prewalked_object_table(stock, home_root):
    # The resolve-once-and-thread-through hook metrics/glyphs already have: a caller
    # holding the object table (Engine.placements walks it anyway) can pass it in and
    # skip the ~40 ms full-section rescan -- with the result byte-identical.
    table = guidata.object_tables(stock)[1]
    assert gestures.touch_ids(stock, home_root, table=table) == \
        gestures.touch_ids(stock, home_root)


def test_placements_resolves_expensive_tables_once_per_request(stock, home_root,
                                                               monkeypatch):
    from fireflymcf import glyphs
    tables = _count_calls(monkeypatch, guidata, "object_tables")
    glyph = _count_calls(monkeypatch, glyphs, "find_glyph_table")
    eng = Engine(stock, "x")
    body = eng.placements(stock, home_root, {}, {})
    assert len(body["placements"]) > 50          # the walk itself still worked
    # layout.Screen resolves the object table once for the walk; placements may
    # resolve it once more for its own per-object lookups. What it must NOT do is
    # rescan per object (HOME has ~30 controls) or per text record.
    assert tables["n"] <= 2
    assert glyph["n"] <= 1


def test_engine_memoizes_placements_by_tag(stock, home_root, monkeypatch):
    from fireflymcf import layout
    walks = _count_calls(monkeypatch, layout.Screen, "walk")
    eng = Engine(stock, "x")
    first = eng.placements(stock, home_root, {}, {}, tag="tag-a")
    cold = walks["n"]                 # Screen.walk recurses, so this is > 1
    assert cold >= 1
    again = eng.placements(stock, home_root, {}, {}, tag="tag-a")
    assert walks["n"] == cold         # served from the memo: no new walking
    assert first == again
    # The memo hands out copies: a route mutating its response (placements does,
    # body.update(etag=...)) must not poison the cache.
    again["etag"] = "route-added"
    assert "etag" not in eng.placements(stock, home_root, {}, {}, tag="tag-a")
    # A different tag is a different compile/knobs: never served from the memo.
    eng.placements(stock, home_root, {}, {}, tag="tag-b")
    assert walks["n"] == 2 * cold


def test_engine_memoizes_png_by_tag(stock, home_root, monkeypatch):
    from fireflymcf import render
    renders = _count_calls(monkeypatch, render, "render_screen_image")
    eng = Engine(stock, "x")
    first = eng.render_png(stock, home_root, 0.0, {}, {}, tag="tag-a")
    again = eng.render_png(stock, home_root, 0.0, {}, {}, tag="tag-a")
    assert renders["n"] == 1
    assert first == again and first[:8] == b"\x89PNG\r\n\x1a\n"
    eng.render_png(stock, home_root, 0.0, {}, {}, tag="tag-b")
    assert renders["n"] == 2


def test_placements_route_returns_304_on_matching_etag(client):
    client.post("/api/projects", json={"name": "p"})
    r = client.get("/api/projects/p/placements/home")
    assert r.status_code == 200
    tag = r.json()["etag"]
    r304 = client.get("/api/projects/p/placements/home",
                      headers={"if-none-match": tag})
    assert r304.status_code == 304
    # RFC 7232 4.1: the 304 repeats the validator the 200 would have sent.
    assert r304.headers["etag"] == tag


def test_options_page_root_resolved_once_per_etag(client, monkeypatch):
    # The three OPTIONS pages share one header label, so _page_root falls back to
    # reference.options_page_names -- a 0.36 s full-object-table walk that used to run
    # on EVERY render/placements request for those pages, 304s included.
    from studio.server import reference
    walks = _count_calls(monkeypatch, reference, "options_page_names")
    client.post("/api/projects", json={"name": "p"})
    r = client.get("/api/projects/p/placements/options-electrical")
    assert r.status_code == 200
    client.get("/api/projects/p/placements/options-electrical")
    client.get("/api/projects/p/render/options-electrical")
    assert walks["n"] == 1


def test_placements_objects_carry_attach_geometry(client, stock, home_root):
    # Box-dragging a whole control needs the attach record's RAW stored (x, y) -- the
    # coordinate space move_attach writes -- which the walk itself never surfaces.
    # Compare against the file directly: the root's own wtype-5 records.
    from fireflymcf import records
    gs, _ = guidata.guidata_extent(stock)
    table = guidata.object_tables(stock)[1]
    import struct
    expected = {}
    for rec in records.object_records(stock, gs + table[home_root]):
        if rec.wtype != 5 or rec.child_index is None:
            continue
        geom = {}
        for key, dim in (("x", 0), ("y", 1)):
            off = records.geom_offset(stock, rec, dim)
            if off is not None:
                geom[key] = struct.unpack_from("<H", stock, off)[0]
        expected.setdefault(rec.child_index, geom)
    assert expected, "home root carries attach records"

    client.post("/api/projects", json={"name": "p"})
    body = client.get("/api/projects/p/placements/home").json()
    for child, geom in expected.items():
        obj = body["objects"].get(str(child))
        if obj is None:
            continue                      # child renders nothing placeable: no entry
        assert obj["attach"] == geom
    # An object that is placed but not attached to the page root reports None, so the
    # client can tell "not box-draggable" from "server too old to say".
    unattached = [k for k in body["objects"] if int(k) not in expected]
    assert unattached, "home places objects that are not root-attached"
    assert all(body["objects"][k]["attach"] is None for k in unattached)
