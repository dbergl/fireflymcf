"""Firefly Studio server tests. Everything runs through FastAPI's TestClient —
no live server, no sockets. The stock image is the read-only base; each test
module gets a throwaway projects directory."""
import hashlib
import json
import os

import pytest
from fastapi.testclient import TestClient

from fireflymcf import recipe

STOCK_PATH = "firefly-13.6/mcf.bin"


@pytest.fixture(scope="module")
def stock():
    with open(STOCK_PATH, "rb") as f:
        return f.read()


@pytest.fixture()
def client(tmp_path):
    from studio.server.app import create_app
    app = create_app(stock_path=STOCK_PATH, projects_root=str(tmp_path / "projects"),
                     build_root=str(tmp_path / "build"))
    return TestClient(app, base_url="http://localhost")


def test_a_rebound_hostname_is_refused(client, tmp_path):
    # DNS rebinding: the attacker's page reaches 127.0.0.1 under its own hostname, so the
    # request carries that Host. Reads and writes must both be refused, and nothing written.
    evil = {"host": "attacker.example:8676"}
    assert client.get("/api/health", headers=evil).status_code == 400
    r = client.post("/api/projects", json={"name": "pwned"}, headers=evil)
    assert r.status_code == 400
    assert not list((tmp_path / "projects").glob("pwned*"))
    for host in ("127.0.0.1:8676", "localhost:5173", "localhost"):
        assert client.get("/api/health", headers={"host": host}).status_code == 200, host


def test_health(client, stock):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["stock_build_name"] == "Jayco B-Van V13"
    assert body["base_sha256"] == hashlib.sha256(stock).hexdigest()


def test_project_crud_round_trip(client):
    assert client.get("/api/projects").json() == []

    r = client.post("/api/projects", json={"name": "mybuild"})
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["name"] == "mybuild"
    assert doc["project"]["ops"] == [] and doc["project"]["schema"] == 1
    base = doc["project"]["base_sha256"]
    assert len(base) == 64

    assert client.post("/api/projects", json={"name": "mybuild"}).status_code == 409

    got = client.get("/api/projects/mybuild")
    assert got.status_code == 200 and got.json()["project"] == doc["project"]

    proj = got.json()["project"]
    proj["build_name"] = "Jayco B-Van V13 (T)"
    r = client.put("/api/projects/mybuild",
                   json={"project": proj, "expected_mtime": got.json()["mtime"]})
    assert r.status_code == 200
    assert client.get("/api/projects/mybuild").json()["project"]["build_name"] == \
        "Jayco B-Van V13 (T)"
    names = [p["name"] for p in client.get("/api/projects").json()]
    assert names == ["mybuild"]


def test_project_save_mtime_conflict(client):
    client.post("/api/projects", json={"name": "p"})
    got = client.get("/api/projects/p").json()
    stale = got["mtime"] - 10
    r = client.put("/api/projects/p",
                   json={"project": got["project"], "expected_mtime": stale})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "STALE_SAVE"


def test_project_name_validation(client):
    for bad in ("../evil", "a/b", ".hidden", "", "x" * 65, "sp ace"):
        r = client.post("/api/projects", json={"name": bad})
        assert r.status_code == 422, bad
    assert client.get("/api/projects/%2E%2E%2Fevil").status_code in (404, 422)


def test_project_save_rejects_invalid_project(client):
    client.post("/api/projects", json={"name": "q"})
    got = client.get("/api/projects/q").json()
    bad = dict(got["project"], schema=999)
    r = client.put("/api/projects/q",
                   json={"project": bad, "expected_mtime": got["mtime"]})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "BAD_SCHEMA"


def test_project_save_rejects_ops_not_a_list(client):
    # IMPORTANT 2 covering test: {"ops": 5} used to reach enumerate(5) inside
    # Project.from_json and raise a bare TypeError past every handler (a plain-text
    # 500), contradicting "corrupt files are refused whole". Must be a structured
    # 422 BAD_PROJECT instead.
    client.post("/api/projects", json={"name": "opsint"})
    got = client.get("/api/projects/opsint").json()
    bad = dict(got["project"], ops=5)
    r = client.put("/api/projects/opsint",
                   json={"project": bad, "expected_mtime": got["mtime"]})
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "BAD_PROJECT"


def test_project_save_rejects_op_entry_not_an_object(client):
    # Second malformed shape from the same finding: {"ops": [1]} reaches
    # `field not in o` with o=1, another bare TypeError. Also must be 422 BAD_PROJECT.
    client.post("/api/projects", json={"name": "opselem"})
    got = client.get("/api/projects/opselem").json()
    bad = dict(got["project"], ops=[1])
    r = client.put("/api/projects/opselem",
                   json={"project": bad, "expected_mtime": got["mtime"]})
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "BAD_PROJECT"


def test_project_save_writes_bak(client, tmp_path):
    client.post("/api/projects", json={"name": "b"})
    got = client.get("/api/projects/b").json()
    pre_save_project = got["project"]
    proj = dict(got["project"], build_name="X")
    client.put("/api/projects/b", json={"project": proj,
                                        "expected_mtime": got["mtime"]})
    root = tmp_path / "projects"
    assert (root / "b.ffproj.json").exists()
    bak_path = root / "b.ffproj.json.bak"
    assert bak_path.exists()

    bak_text = bak_path.read_text()
    recipe.Project.from_json(bak_text)  # must parse as a valid project
    assert json.loads(bak_text) == pre_save_project


def test_schemas_endpoint_covers_registry(client):
    from fireflymcf import recipe
    r = client.get("/api/schemas")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == set(recipe.REGISTRY)
    for name, entry in body.items():
        assert "properties" in entry["params"], name
        assert isinstance(entry["doc"], str) and entry["doc"], name
        assert entry["doc"] != name, \
            "%s has no real docstring -- doc fell back to the bare op name" % name
        assert isinstance(entry["hardware_verified"], bool)
    assert body["remove_control"]["hardware_verified"] is False
    for field in ("slot", "char", "blank_caption"):
        desc = body["set_icon"]["params"]["properties"][field]["description"]
        assert "slot, char and/or blank_caption" in desc, (field, desc)


def _make(client, name, ops=(), build_name=None):
    client.post("/api/projects", json={"name": name})
    got = client.get("/api/projects/%s" % name).json()
    proj = got["project"]
    proj["ops"] = list(ops)
    proj["build_name"] = build_name
    client.put("/api/projects/%s" % name,
               json={"project": proj, "expected_mtime": got["mtime"]})
    return proj


def test_compile_empty_project(client, stock):
    _make(client, "empty")
    r = client.post("/api/projects/empty/compile")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["errors"] == []
    assert body["size"] == len(stock)
    assert body["manifest"] == [] and body["unverified"] == []
    assert isinstance(body["etag"], str) and len(body["etag"]) == 64
    # deterministic etag on recompile
    assert client.post("/api/projects/empty/compile").json()["etag"] == body["etag"]


def test_compile_op_error_is_data(client):
    _make(client, "bad", ops=[{"id": "a", "type": "rename_label", "enabled": True,
                               "note": "",
                               "params": {"object": 35, "old": "28 - ",
                                          "new": "28 - Overflow Valve"}}])
    body = client.post("/api/projects/bad/compile").json()
    assert body["ok"] is False
    assert body["errors"][0]["code"] == "LABEL_TOO_LONG"
    assert body["errors"][0]["offers"] == ["grow", "shorten"]


def test_compile_rename_changes_etag_and_grows(client, stock):
    # Self-contained: both projects are created inside this test, since each test
    # gets a fresh client/tmp projects dir (no "empty" project carries over from
    # test_compile_empty_project).
    op = {"id": "a", "type": "rename_label", "enabled": True, "note": "",
          "params": {"object": 35, "old": "28 - ", "new": "28 - Overflow Valve",
                     "allow_grow": True}}
    _make(client, "grow", ops=[op])
    body = client.post("/api/projects/grow/compile").json()
    assert body["ok"] and body["size"] > len(stock)

    _make(client, "empty2")
    empty_etag = client.post("/api/projects/empty2/compile").json()["etag"]
    assert body["etag"] != empty_etag


def test_render_home_png_with_etag(client):
    _make(client, "r1")
    r = client.get("/api/projects/r1/render/home")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
    etag = r.headers["etag"]
    r2 = client.get("/api/projects/r1/render/home",
                    headers={"If-None-Match": etag})
    assert r2.status_code == 304


def test_render_unknown_page_is_404_with_choices(client):
    _make(client, "r2")
    r = client.get("/api/projects/r2/render/nonsense")
    assert r.status_code == 404
    assert "home" in r.json()["detail"]["choices"]


def test_render_with_knobs(client):
    _make(client, "r3")
    r = client.get("/api/projects/r3/render/home",
                   params={"tank_level": 0.5, "slots": "5:8"})
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"


def test_render_bad_query_payload_is_a_message_not_errors_list(client):
    # IMPORTANT 3 covering test: parse_pairs used to raise EngineError with a bare
    # string payload, which the handler's non-dict arm puts under detail.errors --
    # a LIST of op-error dicts on every other route, so detail["errors"][0]["code"]
    # silently returned a single character instead of erroring. Must take the dict
    # arm: {"detail": {"code": "BAD_QUERY", "message": <str>}}, no "errors" key.
    _make(client, "badquery")
    r = client.get("/api/projects/badquery/render/home", params={"slots": "abc"})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "BAD_QUERY"
    assert isinstance(detail["message"], str) and detail["message"]
    assert "errors" not in detail


def test_render_root_override(client, stock):
    from fireflymcf import cli
    _make(client, "r4")
    root = cli._resolve_page_root(stock, "home")
    r = client.get("/api/projects/r4/render/home", params={"root": root})
    assert r.status_code == 200


def test_render_304_response_carries_etag_header(client):
    # RFC 7232 4.1: a 304 must repeat the validator a 200 would have sent, so a
    # client relying on the 304's own ETag (not just its cached copy) still works.
    _make(client, "r5")
    r = client.get("/api/projects/r5/render/home")
    etag = r.headers["etag"]
    r2 = client.get("/api/projects/r5/render/home",
                    headers={"If-None-Match": etag})
    assert r2.status_code == 304
    assert r2.headers["etag"] == etag


def test_render_different_pages_get_different_etags(client):
    # Two different pages of the same (unrendered, root=None) project must not
    # collide on one ETag -- and replaying one page's tag as If-None-Match against
    # the other page's URL must be a real cache miss (200), not a false 304.
    _make(client, "r6")
    home = client.get("/api/projects/r6/render/home")
    lights = client.get("/api/projects/r6/render/lights")
    assert home.status_code == 200 and lights.status_code == 200
    assert home.content != lights.content
    assert home.headers["etag"] != lights.headers["etag"]

    cross = client.get("/api/projects/r6/render/lights",
                       headers={"If-None-Match": home.headers["etag"]})
    assert cross.status_code == 200


def test_render_of_broken_project_is_409_with_errors(client):
    _make(client, "r7", ops=[{"id": "a", "type": "rename_label", "enabled": True,
                              "note": "",
                              "params": {"object": 35, "old": "zzz", "new": "yyy"}}])
    r = client.get("/api/projects/r7/render/home")
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["errors"][0]["code"] == "NO_SUCH_LABEL"


def test_placements_home_coherent_with_render(client):
    _make(client, "pl")
    pr = client.get("/api/projects/pl/placements/home")
    assert pr.status_code == 200, pr.text
    body = pr.json()
    rr = client.get("/api/projects/pl/render/home")
    assert body["etag"] == rr.headers["etag"]      # same inputs, same tag
    assert body["canvas"] == [800, 480]
    assert len(body["placements"]) > 10
    from fireflymcf import recipe
    known_ops = set(recipe.REGISTRY)
    for p in body["placements"]:
        x0, y0, x1, y1 = p["box"]
        assert 0 <= x0 <= x1 <= 800 and 0 <= y0 <= y1 <= 480, p
        assert isinstance(p["obj_index"], int)
        assert set(p["editable"]) <= known_ops, p["editable"]
    # The button's caption is stored byte-exact as "Water\nPump" (a literal 0x0A, same
    # convention as CLAUDE.md's 'G12\nOUTPUTS'), so normalize before substring-matching --
    # the placements payload itself keeps the raw label, this is a test-only comparison.
    labels = {(p["label"] or "").replace("\n", " ")
             for p in body["placements"] if p.get("label")}
    assert any("Water Pump" in l for l in labels)


def test_placements_geom_is_raw_not_resolved(client):
    # CRITICAL 1 review finding: the client's drag-to-move must anchor its delta on the
    # record's RAW stored (x, y) -- the coordinate space move_element writes into via
    # records.geom_offset -- not on `box`, which is the layout-RESOLVED absolute
    # placement (geom + register + container offset). For a record nested inside a
    # container those differ, so `geom` has to be a genuinely different field, not a
    # copy of box[0]/box[1].
    _make(client, "pg")
    body = client.get("/api/projects/pg/placements/home").json()
    placements = body["placements"]
    assert placements and "geom" in placements[0], "every placement must carry the key"
    for p in placements:
        g = p["geom"]
        # All four dims, each INDEPENDENTLY nullable. Record geometry is presence-masked
        # and genuinely ragged -- donor 247's box has w/h but no x/y, donor 279's has x,
        # w, h but no y -- so a client that resizes must be told which fields exist. This
        # used to be reported as {x, y} only, and only when BOTH were present, which hid
        # w/h from every client and reported "no geometry" for most records on the page.
        assert g is None or (set(g) == {"x", "y", "w", "h"}
                             and all(v is None or isinstance(v, int)
                                     for v in g.values())), p
        assert g is None or any(v is not None for v in g.values()), p
    # FRESH sits nested inside HOME's tank-well container: its raw stored geometry (30, 7)
    # is byte-derived (records.geom_offset on the stock image) and differs from its
    # layout-resolved box origin -- the walkthrough this test pins.
    by_label = {(p["label"] or "").replace("\n", " "): p
               for p in placements if p.get("label")}
    fresh = by_label["FRESH"]
    assert fresh["geom"] == {"x": 30, "y": 7, "w": 55, "h": 20}
    assert (fresh["geom"]["x"], fresh["geom"]["y"]) != (fresh["box"][0], fresh["box"][1])
    # And null where the record carries NO geometry field at all -- not merely no x/y.
    assert any(p["geom"] is None for p in placements)
    # A record carrying a size but no position must still report its size: this is the
    # case the old {x, y}-only contract dropped on the floor.
    assert any(g and g["x"] is None and g["w"] is not None
               for g in (p["geom"] for p in placements)), \
        "a size-without-position record must report its w/h"


def test_text_widgets_are_renameable_and_clickable(client):
    # The G12 OUTPUTS rows are wtype-0 text widgets, not wtype-11 labels. They were
    # classified as bare "widget" (move_element only) and reported a DEGENERATE box --
    # [325, 286, 325, 286] -- so they could be neither selected nor renamed, though
    # renaming two of them is exactly what reference build v1 does.
    _make(client, "txt")
    body = client.get("/api/projects/txt/placements/outputs").json()
    rows = [p for p in body["placements"] if (p["label"] or "").strip() == "28 -"]
    assert rows, "the '28 - ' row must be placed"
    row = rows[0]
    assert row["kind"] == "text"
    assert "rename_label" in row["editable"]
    assert row["box"][2] > row["box"][0] and row["box"][3] > row["box"][1], \
        "a zero-area box is not clickable, so the row could never be selected"


def test_dynamic_rows_are_not_offered_for_rename(client):
    # The 14 wtype-13 rows are filled by GuiCode over the bus (MCF-FORMAT.md 6.1).
    # Renaming one edits a string the panel overwrites at runtime, so it must not be
    # offered -- the text classification is deliberately wtype 0 only.
    _make(client, "dyn")
    body = client.get("/api/projects/dyn/placements/outputs").json()
    dynamic = [p for p in body["placements"] if p["wtype"] == 13]
    assert dynamic, "the outputs page must place its dynamic rows"
    for p in dynamic:
        assert "rename_label" not in p["editable"], p


def test_placements_binding_enrichment(client):
    _make(client, "pl2")
    body = client.get("/api/projects/pl2/placements/home").json()
    bindings = [o["binding"] for o in body["objects"].values()
                if o.get("binding")]
    assert bindings, "no control on HOME had a decodable binding"
    assert any(b["gesture"] == "latch" and b["channels"] for b in bindings)


def test_placements_excludes_offcanvas_parked_control(client, stock):
    # Regression: Engine.placements used to clamp x0/y0/x1/y1 to the canvas BEFORE testing
    # for off-canvas exclusion. Clamping is monotonic, so it cannot un-invert a well-ordered
    # box -- the old "if x1 < x0 or y1 < y0" check only ever caught boxes that were ALREADY
    # inverted (the legitimate gauge/level-fill convention). A box merely TRANSLATED off-
    # canvas (remove_control's park_y >= 480, or an out-of-bounds move_element) clamped to a
    # degenerate zero-height box pinned at the canvas edge and was kept -- a phantom hit
    # target. remove_control is a real, supported op reachable from the project pipeline, so
    # drive the regression through it rather than asserting on the predicate in isolation.
    from fireflymcf import cli as C, insert, records as R, guidata

    gs, _ = guidata.guidata_extent(stock)
    tab = guidata.object_tables(stock)[1]
    home = C._resolve_page_root(stock, "home")
    graph = insert.object_child_graph(stock)

    def _has_y_everywhere(child):
        # Same selection test_recipe_ops.py's remove_control test uses: pick a HOME child
        # whose wtype-5 attach record carries a real y field in every parent that attaches
        # it, so the op has something concrete to park and nothing to refuse on.
        parents = [p for p, kids in graph.items() if child in kids]
        recs = [pr for p in parents
                for pr in R.object_records(stock, gs + tab[p])
                if pr.wtype == 5 and pr.child_index == child]
        return bool(recs) and all(R.geom_offset(stock, pr, 1) is not None
                                  for pr in recs)

    child = next(r.child_index for r in R.object_records(stock, gs + tab[home])
                 if r.wtype == 5 and r.child_index is not None
                 and _has_y_everywhere(r.child_index))

    op = {"id": "rm", "type": "remove_control", "enabled": True, "note": "",
          "params": {"object": child, "park_y": 900}}
    _make(client, "parked", ops=[op])
    body = client.get("/api/projects/parked/placements/home").json()

    assert child not in {p["obj_index"] for p in body["placements"]}, \
        "the parked object's own placement must not survive clamp-then-keep"
    for p in body["placements"]:
        x0, y0, x1, y1 = p["box"]
        assert not (x1 < 0 or x0 > 800 or y1 < 0 or y0 > 480), \
            "no placement's box may lie wholly outside the 800x480 canvas: %r" % (p,)


def test_placements_predicate_excludes_translated_offcanvas_boxes(stock, monkeypatch):
    # Direct check of the exclusion predicate, using the review's own numeric example (a
    # well-ordered box translated to x0=900, x1=950 -- moved bodily off the right edge, not
    # resized/inverted) plus the other three directions and the pre-existing legitimate
    # inverted-box convention. Bypasses HTTP/compile: patches layout.Screen.walk to return
    # synthetic Placed boxes so Engine.placements runs its real exclusion/clip logic against
    # exact coordinates, rather than reproducing the arithmetic in the test.
    from studio.server.engine import Engine
    from fireflymcf import layout

    synthetic = [
        layout.Placed(wtype=33, label=None, x0=900, y0=100, x1=950, y1=150,
                     depth=0, obj_index=1, record_offset=0),   # off-canvas: past the right
        layout.Placed(wtype=33, label=None, x0=-200, y0=100, x1=-100, y1=150,
                     depth=0, obj_index=2, record_offset=0),   # off-canvas: past the left
        layout.Placed(wtype=33, label=None, x0=10, y0=900, x1=60, y1=950,
                     depth=0, obj_index=3, record_offset=0),   # off-canvas: past the bottom
                                                               # (remove_control's park_y shape)
        layout.Placed(wtype=33, label=None, x0=10, y0=-200, x1=60, y1=-100,
                     depth=0, obj_index=4, record_offset=0),   # off-canvas: past the top
        layout.Placed(wtype=33, label=None, x0=750, y0=100, x1=850, y1=150,
                     depth=0, obj_index=5, record_offset=0),   # genuinely partial: must be
                                                               # CLIPPED and KEPT, not dropped
        layout.Placed(wtype=0x20, label=None, x0=10, y0=150, x1=60, y1=100,
                     depth=0, obj_index=6, record_offset=0),   # legit inverted gauge/level
                                                               # fill (y1 < y0 by convention)
    ]
    monkeypatch.setattr(layout.Screen, "walk", lambda self, *a, **k: synthetic)
    eng = Engine(stock=stock, base_sha256="x")
    monkeypatch.setattr(eng, "_binding", lambda data, oi, table=None: None)

    body = eng.placements(stock, 0, {}, {})
    kept = {p["obj_index"]: p["box"] for p in body["placements"]}
    assert set(kept) == {5}, "only the genuinely partially-visible box should survive"
    assert kept[5] == [750, 100, 800, 150], "the survivor must be CLIPPED to the edge"


def test_reference_pins(client):
    r = client.get("/api/reference/pins")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) >= 40                       # all 44 pins are watched on 13.6
    by_pin = {row["pin"]: row for row in rows}
    assert by_pin[26]["commanded"] is True       # commanded and watched (not Water Pump -- that's channel 44)
    assert any(row["warnings"] for row in rows if row["commanded"])
    assert all(set(row) >= {"pin", "var", "bridged", "colour_index",
                            "commanded", "dimmable", "warnings"} for row in rows)


def test_reference_colors(client):
    rows = client.get("/api/reference/colors").json()
    assert rows and all(len(row["rgb"]) == 3 for row in rows)
    assert any(row["static"] for row in rows)


def test_reference_fonts_and_specimen(client):
    rows = client.get("/api/reference/fonts").json()
    slots = {row["slot"] for row in rows}
    assert 13 in slots and 27 in slots
    r = client.get("/api/reference/fonts/13/specimen")
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
    assert client.get("/api/reference/fonts/999/specimen").status_code == 404


def test_reference_glyph_exports_reinstallable_parameters(client, stock):
    """The copy-a-glyph route hands back exactly what `install_glyph` consumes, and the
    record it produces is byte-identical to the one it came from -- which needs the
    kerning bytes and the flag bit, not just the pixels (`GUICODE.md` Finding 46)."""
    from fireflymcf import glyphenc, glyphs

    r = client.get("/api/reference/glyph", params={"slot": 27, "char": "A"})
    assert r.status_code == 200, r.text
    spec = r.json()
    assert set(spec) == {"slot", "char", "x", "y", "rows", "profiles", "flag"}
    assert len(spec["profiles"]) == 10

    off, _ = glyphs.glyph_extent(stock, glyphs.font_for_slot(stock, 27).glyph_index(ord("A")))
    rec = glyphenc.encode_glyph([tuple(int(c, 16) for c in row) for row in spec["rows"]], 4,
                                x=spec["x"], y=spec["y"], profiles=spec["profiles"],
                                flag=spec["flag"])
    assert rec == stock[off:off + len(rec)]


def test_reference_glyph_refuses_what_has_no_record_of_its_own(client):
    """Two near-misses, both of which would export the wrong thing.

    Slot 27 'E' is COVERED but is the shared placeholder -- it decodes to nothing, which
    is exactly why the Starlink logo could claim it. Slot 27 '3' is NOT covered, so it
    resolves to the font's fallback (`Font.glyph_index` mirrors FUN_00251AA0): exporting
    it would return another character's artwork labelled '3', and installing that back
    would repoint the fallback entry every uncovered character draws."""
    assert client.get("/api/reference/glyph",
                      params={"slot": 27, "char": "E"}).status_code == 404
    assert client.get("/api/reference/glyph",
                      params={"slot": 27, "char": "3"}).status_code == 404
    assert client.get("/api/reference/glyph",
                      params={"slot": 999, "char": "A"}).status_code == 404


def test_reference_pages(client, stock):
    """Every advertised page must resolve to the root it advertises.

    Two resolvers, because the OPTIONS pages all share the header label "OPTIONS" and
    `cli._resolve_page_root` matches on the header -- so it can only ever return the
    first. The extras come from `reference.options_page_names`, and the route's
    `_page_root` consults both (proved end to end by the options-electrical render in
    test_preview_options_reach_the_render)."""
    rows = client.get("/api/reference/pages").json()
    names = {row["name"] for row in rows}
    assert {"home", "lights", "climate"} <= names
    from fireflymcf import cli
    from studio.server import reference
    extra = reference.options_page_names(stock)
    for row in rows:
        if row["name"] in extra:
            assert row["root"] == extra[row["name"]]
        else:
            assert row["root"] == cli._resolve_page_root(stock, row["name"])


def test_reference_pins_commanded_covers_every_engine_refused_channel(client, stock):
    # Regression for a desync between two independently-written predicates: reference.pins
    # computes "commanded" from recipe.capabilities (the same gate builds/from_stock.py uses
    # to refuse silently repurposing a channel already driven by a control group), while
    # guidata.channel_commands walks the binding table on its own, independently, to the same
    # end. If they ever drift, the GUI could offer a pin as "free" that the engine would
    # actually refuse, inviting a double-drive of an existing group -- so pin their agreement
    # directly (this is the finding's "cheapest version": the two lower-level predicates,
    # not routed through HTTP).
    #
    # recipe.capabilities is scoped to channels reachable from the group table;
    # guidata.channel_commands scans the raw binding table directly, with no group-table
    # filter. In principle channel_commands's set could be the larger of the two (a binding
    # a group doesn't point at would still show up there but not in capabilities) -- on
    # stock they are, empirically, exactly equal (every bound channel is reachable from some
    # group), so pin containment rather than equality: it holds either way and states the
    # actual guarantee ("capabilities never claims a channel absent from the wire-level
    # scan") without being fragile to that empirical coincidence.
    from fireflymcf import guidata, recipe

    engine_commanded = {c for c, _cmd in recipe.capabilities(stock)}
    binding_table_commanded = set(guidata.channel_commands(stock))
    assert engine_commanded <= binding_table_commanded

    # Then pin the route's own wiring: reference.pins() only describes WATCHED pins (it has
    # no var/colour_index/bridged data to report for a channel with no status binding), so
    # restrict the ground truth to the pins this route can even list before comparing --
    # every watched pin the engine would refuse must be flagged commanded=True in the
    # picker; none may be silently missed as "free".
    rows = client.get("/api/reference/pins").json()
    reference_commanded = {row["pin"] for row in rows if row["commanded"]}
    watched = {row["pin"] for row in rows}
    assert (engine_commanded & watched) <= reference_commanded


def test_reference_channels_includes_unwatched_commanded(client, stock):
    from fireflymcf import recipe, statusbind
    r = client.get("/api/reference/channels")
    assert r.status_code == 200
    rows = r.json()
    by = {row["channel"]: row for row in rows}
    watched = set(statusbind.pin_bindings(stock))
    commanded = {c for c, _cmd in recipe.capabilities(stock)}
    # every commanded channel appears, watched or not
    assert commanded <= set(by), sorted(commanded - set(by))
    # and the unwatched ones are marked as such
    unwatched = sorted(commanded - watched)
    assert unwatched, "corpus premise: some commanded channel is unwatched"
    for ch in unwatched:
        assert by[ch]["commanded"] is True
        assert by[ch]["watched"] is False
    # watched pins are present and marked watched
    for pin in sorted(watched)[:5]:
        assert by[pin]["watched"] is True
    assert rows == sorted(rows, key=lambda x: x["channel"])


def test_export_writes_mcf_prefixed_file(client, stock, tmp_path):
    _make(client, "exp", build_name="Jayco B-Van V13 (Studio T)")
    r = client.post("/api/projects/exp/export")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["filename"] == "mcf-exp.bin"
    # Written under the per-test build_root (tmp_path/"build"), never the repo's
    # real build/ directory -- FINDING 1's whole point.
    assert body["path"] == str(tmp_path / "build" / "mcf-exp.bin")
    assert body["size"] == len(stock)
    import os
    assert os.path.exists(body["path"])
    dl = client.get(body["download"])
    assert dl.status_code == 200
    assert len(dl.content) == body["size"]
    import hashlib as h
    assert h.sha256(dl.content).hexdigest() == body["sha256"]


def test_export_failed_compile_is_409(client):
    _make(client, "expbad", ops=[{"id": "a", "type": "rename_label",
                                  "enabled": True, "note": "",
                                  "params": {"object": 35, "old": "zzz",
                                             "new": "yyy"}}])
    r = client.post("/api/projects/expbad/export")
    assert r.status_code == 409
    assert r.json()["detail"]["errors"][0]["code"] == "NO_SUCH_LABEL"


def test_export_file_before_export_is_404(client):
    _make(client, "expnone")
    assert client.get("/api/projects/expnone/export/file").status_code == 404


def test_export_atomic_write_leaves_no_tmp_and_is_stable_across_reexport(client, tmp_path):
    # FINDING 1 covering test: two sequential exports of the same project must both
    # promote to the exact same bytes (the atomic-write contract), and neither
    # export's tempfile.mkstemp-based temp file may survive into build/ -- a true
    # concurrency race isn't worth the machinery here, so this pins the promote
    # contract and the cleanup instead. Lists the per-test build_root (tmp_path /
    # "build"), never the repo's real build/ directory.
    _make(client, "expatomic", build_name="Jayco B-Van V13 (Studio T)")
    r1 = client.post("/api/projects/expatomic/export")
    assert r1.status_code == 200, r1.text
    body1 = r1.json()
    r2 = client.post("/api/projects/expatomic/export")
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert body1["sha256"] == body2["sha256"]

    import hashlib as h
    with open(body2["path"], "rb") as f:
        on_disk = f.read()
    assert h.sha256(on_disk).hexdigest() == body2["sha256"]

    import os
    leftovers = [n for n in os.listdir(str(tmp_path / "build")) if n.endswith(".tmp")]
    assert leftovers == [], leftovers


def test_export_file_not_stale_right_after_export(client):
    # FINDING 2(a): a freshly exported file must report itself current, and its
    # ETag must match the etag the export response reported (now also carried in
    # the JSON body per the finding, so a client can compare without a HEAD).
    _make(client, "expfresh")
    body = client.post("/api/projects/expfresh/export").json()
    assert isinstance(body["etag"], str) and len(body["etag"]) == 64

    dl = client.get(body["download"])
    assert dl.status_code == 200
    assert dl.headers["x-firefly-stale"] == "false"
    assert dl.headers["etag"] == body["etag"]


def test_export_file_flags_stale_after_project_changes_without_reexport(client):
    # FINDING 2(b): editing the project so it would compile DIFFERENTLY, without
    # re-exporting, must make the still-served file self-report as stale -- and
    # its ETag must keep naming the OLD build, not silently track the new one.
    _make(client, "expstale")
    body = client.post("/api/projects/expstale/export").json()

    got = client.get("/api/projects/expstale").json()
    proj = got["project"]
    proj["ops"] = [{"id": "a", "type": "rename_label", "enabled": True, "note": "",
                    "params": {"object": 35, "old": "28 - ", "new": "29 - "}}]
    put = client.put("/api/projects/expstale",
                     json={"project": proj, "expected_mtime": got["mtime"]})
    assert put.status_code == 200, put.text

    dl = client.get(body["download"])
    assert dl.status_code == 200
    assert dl.headers["x-firefly-stale"] == "true"
    assert dl.headers["etag"] == body["etag"]


def test_export_file_serves_last_good_build_flagged_stale_after_failed_reexport(client):
    # FINDING 2(c): break the project, re-export (409, no new file written), then
    # confirm the file route still serves the last GOOD build rather than 404ing --
    # and flags it stale, since a failed re-export must never silently un-flag the
    # file it left behind.
    _make(client, "expbroken")
    body = client.post("/api/projects/expbroken/export").json()

    got = client.get("/api/projects/expbroken").json()
    proj = got["project"]
    proj["ops"] = [{"id": "a", "type": "rename_label", "enabled": True, "note": "",
                    "params": {"object": 35, "old": "zzz", "new": "yyy"}}]
    put = client.put("/api/projects/expbroken",
                     json={"project": proj, "expected_mtime": got["mtime"]})
    assert put.status_code == 200, put.text

    r2 = client.post("/api/projects/expbroken/export")
    assert r2.status_code == 409

    dl = client.get(body["download"])
    assert dl.status_code == 200
    assert dl.headers["x-firefly-stale"] == "true"
    assert dl.headers["etag"] == body["etag"]


def test_export_file_corrupt_sidecar_is_treated_as_stale_not_500(client):
    # Round-2 FINDING: a sidecar that exists but holds invalid JSON (truncated
    # write, manual edit, a future format change) must degrade exactly like a
    # missing sidecar -- reported stale, file still served -- never an unhandled
    # 500 from json.JSONDecodeError.
    _make(client, "expcorrupt")
    body = client.post("/api/projects/expcorrupt/export").json()

    sidecar_path = body["path"] + ".json"
    with open(sidecar_path, "wb") as f:
        f.write(b"{not json")

    dl = client.get(body["download"])
    assert dl.status_code == 200
    assert dl.headers["x-firefly-stale"] == "true"
    import hashlib as h
    assert h.sha256(dl.content).hexdigest() == body["sha256"]


def test_export_file_missing_sidecar_is_treated_as_stale(client):
    # Cheap companion to the corrupt-sidecar case: a sidecar that was never
    # written (or was removed) reports stale too, never a false "fresh".
    _make(client, "expnosidecar")
    body = client.post("/api/projects/expnosidecar/export").json()

    import os
    os.remove(body["path"] + ".json")

    dl = client.get(body["download"])
    assert dl.status_code == 200
    assert dl.headers["x-firefly-stale"] == "true"


def test_api_routes_work_without_a_built_bundle(client):
    # The static mount is optional: with no studio/web/dist the API still serves.
    assert client.get("/api/health").status_code == 200


def test_static_mount_never_shadows_api(tmp_path):
    # Build a fake dist with an index.html, mount it, and confirm /api still wins.
    import studio.server.app as appmod
    dist = os.path.join(os.path.dirname(appmod.__file__), "..", "web", "dist")
    dist = os.path.abspath(dist)
    made = False
    if not os.path.isdir(dist):
        os.makedirs(dist, exist_ok=True)
        with open(os.path.join(dist, "index.html"), "w") as f:
            f.write("<!doctype html><title>x</title>")
        made = True
    try:
        from fastapi.testclient import TestClient
        app = appmod.create_app(stock_path=STOCK_PATH,
                                projects_root=str(tmp_path / "projects"),
                                build_root=str(tmp_path / "build"))
        c = TestClient(app, base_url="http://localhost")
        assert c.get("/api/health").json()["status"] == "ok"
        assert c.get("/").status_code == 200
    finally:
        if made:
            import shutil
            shutil.rmtree(dist, ignore_errors=True)


def test_placements_advertise_set_icon_on_the_box(client):
    # `set_icon` targets the OBJECT (the engine locates the icon record itself), so it
    # belongs on the box placement beside the other object-level ops — recolor and
    # remove_control. Without this the UI has nothing honest to gate an icon control on.
    _make(client, "icon")
    body = client.get("/api/projects/icon/placements/home").json()
    boxes = [p for p in body["placements"] if p["kind"] == "box"]
    assert boxes, "HOME has no box placements to check"
    assert all("set_icon" in p["editable"] for p in boxes)
    # and it stays off the kinds it cannot apply to
    for p in body["placements"]:
        if p["kind"] in ("touch", "attach"):
            assert "set_icon" not in p["editable"]


# --- coach config: floorplans, options, per-floorplan defaults ---------------

def test_reference_config_lists_every_floorplan(client):
    body = client.get("/api/reference/config").json()
    codes = {f["code"]: f["value"] for f in body["floorplans"]}
    assert codes["WD"] == 8 and codes["WA"] == 7 and codes["WT"] == 9
    assert len(codes) == 14

def test_reference_config_lists_options_with_their_slots(client):
    body = client.get("/api/reference/config").json()
    by_name = {o["name"]: o for o in body["options"]}
    assert by_name["Bath Fan"]["slot"] == 109
    # the generator mode is a RADIO on one slot, not three booleans -- rendering it as
    # checkboxes is what silently turned "AES Single Output" into AGS (options._option_value).
    # Radio-ness is structural: >1 option sharing a slot, each with its own written value.
    gen = [o for o in body["options"] if o["slot"] == 37]
    assert {o["name"] for o in gen} == {"AGS", "AES", "AES Single Output"}
    assert {o["name"]: o["value"] for o in gen} == {"AGS": 1, "AES": 2, "AES Single Output": 3}
    assert by_name["Bath Fan"]["value"] == 1          # a plain boolean writes 1

def test_reference_config_defaults_name_options_the_picker_shows(client):
    """Every default must name an option in the list, or the UI drops it silently.

    This is the 'Cargo / Bath Light\n    (CH. 25)' join: the two engine sources spell
    that option differently, and without canonicalising they never meet."""
    body = client.get("/api/reference/config").json()
    names = {o["name"] for o in body["options"]}
    assert "Cargo / Bath Light" in names
    for value, d in body["defaults"].items():
        assert set(d) <= names, (value, set(d) - names)
        assert "Cargo / Bath Light" in d

def test_reference_config_omits_floorplans_that_have_no_defaults(client):
    """Only 11 of 14 floorplans have a defaults handler (GUICODE.md Finding 42).

    4NJ/3NG/3NK/3NJ have none, so the endpoint must omit them rather than invent a
    default set -- the UI leaves the options untouched when one is picked."""
    body = client.get("/api/reference/config").json()
    by_code = {f["code"]: str(f["value"]) for f in body["floorplans"]}
    for code in ("WA", "WD", "WT", "SY", "TB"):
        assert by_code[code] in body["defaults"], code
    for code in ("4NJ", "3NG", "3NK", "3NJ"):
        assert by_code[code] not in body["defaults"], code

def test_reference_config_wd_defaults_match_the_engine(client, stock):
    from fireflymcf import guidata
    body = client.get("/api/reference/config").json()
    wd = body["defaults"]["8"]
    assert wd["Black Tank"] is True and wd["Bath Fan"] is False
    assert len(wd) == len(guidata.find_option_defaults(stock, 8))


# --- the three OPTIONS pages, and preview-derived slots ----------------------

def test_pages_expose_electrical_options_separately(client):
    """All three OPTIONS objects share the header 'OPTIONS', so header-matching only
    ever finds the first (46). Electrical Options is object 47 and had no name at all."""
    pages = {p["name"]: p["root"] for p in client.get("/api/reference/pages").json()}
    assert pages["options"] == 46
    assert pages["options-electrical"] == 47


def _save_preview(client, name, preview):
    client.post("/api/projects", json={"name": name})
    got = client.get("/api/projects/%s" % name).json()
    proj = got["project"]
    proj["preview"] = preview
    r = client.put("/api/projects/%s" % name,
                   json={"project": proj, "expected_mtime": got["mtime"]})
    assert r.status_code == 200, r.text


def test_preview_floorplan_drives_the_render(client):
    """Without a floorplan LIGHTS is the panel's own NO FLOORPLAN SELECTED screen."""
    _save_preview(client, "nofp", {})
    _save_preview(client, "wd", {"floorplan": "WD"})
    sha = lambda r: hashlib.sha256(r.content).hexdigest()
    bare = client.get("/api/projects/nofp/render/lights")
    withfp = client.get("/api/projects/wd/render/lights")
    assert bare.status_code == 200 and withfp.status_code == 200
    assert sha(bare) != sha(withfp)
    # and an explicit ?slots= must reproduce exactly what the preview produced
    explicit = client.get("/api/projects/nofp/render/lights?slots=5:8")
    assert sha(explicit) == sha(withfp)


def test_preview_options_reach_the_render(client):
    """AGS/AES/AES Single Output share slot 37 by VALUE, so they must not be booleans.

    Asserted on the Electrical Options page because that is where the distinction is
    *drawn*: each row's slot-37 switch turns its dot cyan at exactly one value
    (`options._option_value`). HOME and ELECTRICAL do not vary with slot 37 in this
    renderer at all -- checked, and the reason this test names page 47."""
    shots = {}
    for name, opt in (("ags", "AGS"), ("aes", "AES"), ("aesso", "AES Single Output")):
        _save_preview(client, name, {"floorplan": "WD", "options": [opt]})
        r = client.get("/api/projects/%s/render/options-electrical" % name)
        assert r.status_code == 200, r.text
        shots[opt] = hashlib.sha256(r.content).hexdigest()
    assert len(set(shots.values())) == 3, shots


def test_preview_change_changes_the_render_etag_but_not_the_compile_etag(client):
    """The compile etag deliberately excludes preview, so the render etag must include
    it explicitly -- otherwise two floorplans serve each other's cached PNG."""
    _save_preview(client, "e", {"floorplan": "WA"})
    first = client.get("/api/projects/e/render/lights")
    compile_a = client.post("/api/projects/e/compile").json()["etag"]

    got = client.get("/api/projects/e").json()
    proj = got["project"]
    proj["preview"] = {"floorplan": "WD"}
    client.put("/api/projects/e", json={"project": proj, "expected_mtime": got["mtime"]})

    second = client.get("/api/projects/e/render/lights")
    compile_b = client.post("/api/projects/e/compile").json()["etag"]
    assert first.headers["etag"] != second.headers["etag"]
    assert compile_a == compile_b


def test_unknown_preview_floorplan_is_a_clean_error_not_a_500(client):
    _save_preview(client, "bogus", {"floorplan": "ZZ"})
    r = client.get("/api/projects/bogus/render/lights")
    assert r.status_code == 422, r.text


def test_radio_defaults_pick_one_mode_not_all_three(client):
    """A radio's default is the VALUE its slot is given, not a bool per name.

    All three generator names share one selector (0xEF), so `find_option_defaults`
    reports AGS/AES/AES Single Output all True for WD -- lossy, because the selector's
    value is 3, i.e. exactly AES Single Output. Feeding all three into a radio is
    meaningless, and feeding them to `options.slot_values` is a conflict."""
    body = client.get("/api/reference/config").json()
    wd = body["defaults"]["8"]        # EF = 3
    assert wd["AES Single Output"] is True
    assert wd["AGS"] is False and wd["AES"] is False
    rt = body["defaults"]["1"]        # EF = 1 -> AGS
    assert rt["AGS"] is True and rt["AES Single Output"] is False
    sy = body["defaults"]["4"]        # EF = 0 -> no generator mode at all
    assert sy["AGS"] is False and sy["AES"] is False
    assert sy["AES Single Output"] is False


def test_a_floorplans_own_defaults_render_without_a_422(client):
    """End-to-end: load a floorplan's defaults exactly as the UI does, and render.

    Catches two joins at once -- the canonical option name has to survive the round
    trip back into `options.slot_values` (which knows only the verbatim OPTIONS-row
    label, 'Cargo / Bath Light\\n    (CH. 25)'), and the radio default has to be one
    mode rather than three."""
    cfg = client.get("/api/reference/config").json()
    for code, value in (("WD", "8"), ("SY", "4"), ("RT", "1")):
        d = cfg["defaults"][value]
        opts = [o["name"] for o in cfg["options"] if d.get(o["name"])]
        _save_preview(client, "fp%s" % value, {"floorplan": code, "options": opts})
        r = client.get("/api/projects/fp%s/render/lights" % value)
        assert r.status_code == 200, (code, r.text)


# --- owner-supplied per-floorplan extra slots (the Batteries widget) ---------

def test_the_battery_gate_is_on_for_every_floorplan(client):
    """Slot 512 is panel-local RUNTIME state, not a floorplan default.

    It is written 1 by tap handlers (GuiCode 0x00242F86 / 0x002466AA), is absent from
    the config-payload block (+0x04..+0x1A), and toggling the widget on the coach
    produced no 1FED9 traffic for it at all (capture 2026-08-20). So the panel sets it
    on entering the page and Studio applies it unconditionally, so the canvas draws what
    the panel draws. An earlier version gated this on an owner-supplied WA/WD/WT list --
    that was inventing a floorplan rule to explain a runtime behaviour."""
    body = client.get("/api/reference/config").json()
    assert "floorplan_slots" not in body

    sha = lambda r: hashlib.sha256(r.content).hexdigest()
    for code in ("WD", "SY", "RT"):
        _save_preview(client, "g%s" % code, {"floorplan": code})
        shown = client.get("/api/projects/g%s/render/options-electrical" % code)
        assert shown.status_code == 200, (code, shown.text)
        # explicitly forcing the gate off must differ -- proving the group really is drawn
        _save_preview(client, "n%s" % code,
                      {"floorplan": code, "extra_slots": {"512": 0}})
        hidden = client.get("/api/projects/n%s/render/options-electrical" % code)
        assert sha(shown) != sha(hidden), code


def test_battery_group_is_described_for_the_picker(client):
    """Slot 512 fronts TWO groups -- object 1121 ('Battery', inner slot 509) and object
    1235 (' Batteries:', inner slot 599). Both are described, identified by their content
    object, because picking by position would silently have chosen 1121, which is
    reachable from no page root this project can render."""
    body = client.get("/api/reference/config").json()
    by_obj = {g["object"]: g for g in body["slot_groups"]}
    assert set(by_obj) == {1121, 1235}
    g = by_obj[1235]
    assert g["gate_slot"] == 512 and g["slot"] == 599
    assert g["label"] == "Batteries"
    assert [c["label"] for c in g["choices"]] == ["1", "2"]
    assert [c["value"] for c in g["choices"]] == [0, 1]


def test_preview_extra_slots_reach_the_render(client):
    """extra_slots overrides a runtime gate, so it must beat RUNTIME_GATE_SLOTS."""
    sha = lambda r: hashlib.sha256(r.content).hexdigest()
    _save_preview(client, "gated", {"floorplan": "WD"})
    _save_preview(client, "ungated", {"floorplan": "WD", "extra_slots": {"512": 0}})
    on = client.get("/api/projects/gated/render/options-electrical")
    off = client.get("/api/projects/ungated/render/options-electrical")
    assert on.status_code == 200 and off.status_code == 200
    assert sha(on) != sha(off)
    explicit = client.get(
        "/api/projects/gated/render/options-electrical?slots=5:8,512:0")
    assert sha(explicit) == sha(off)


def test_battery_count_default_is_one(client):
    """Slot 599 is ZERO-BASED: 0 shows '1', 1 shows '2'. Established by rendering
    all three values and comparing the dots, not by reading a match value -- both
    switches in object 1235 match on 1."""
    sha = lambda r: hashlib.sha256(r.content).hexdigest()
    _save_preview(client, "b1", {"floorplan": "WD"})
    _save_preview(client, "b0", {"floorplan": "WD", "extra_slots": {"599": 0}})
    _save_preview(client, "b2", {"floorplan": "WD", "extra_slots": {"599": 1}})
    one = client.get("/api/projects/b1/render/options-electrical")
    zero = client.get("/api/projects/b0/render/options-electrical")
    two = client.get("/api/projects/b2/render/options-electrical")
    assert sha(one) == sha(zero), "unset must be the same as 0, i.e. '1' selected"
    assert sha(one) != sha(two)


# --- importing the coach's live config from a capture (optional, opt-in) ------

CAPTURE = "tests/fixtures/livecfg-sample.log"


def test_import_live_returns_a_preview_and_a_diff_without_saving(client):
    """Pure: it proposes, the caller applies. Nothing is written."""
    client.post("/api/projects", json={"name": "imp"})
    before = client.get("/api/projects/imp").json()["project"]
    with open(CAPTURE) as f:
        body = client.post("/api/projects/imp/preview/import",
                           json={"capture": f.read()}).json()
    assert body["preview"]["floorplan"] == "WD"
    assert body["source"] == "9C" and body["frames"] == 45
    # a fresh project has no preview, so every value is a change
    changes = {c["field"]: c for c in body["diff"]}
    assert changes["floorplan"]["from"] is None
    assert changes["floorplan"]["to"] == "WD"
    assert "Bunk Accent" in changes["options"]["to"]
    # and the project on disk is untouched
    assert client.get("/api/projects/imp").json()["project"] == before


def test_import_live_diffs_against_the_current_preview(client):
    _save_preview(client, "imp2", {"floorplan": "SY", "options": ["Bath Fan"]})
    with open(CAPTURE) as f:
        body = client.post("/api/projects/imp2/preview/import",
                           json={"capture": f.read()}).json()
    changes = {c["field"]: c for c in body["diff"]}
    assert changes["floorplan"]["from"] == "SY" and changes["floorplan"]["to"] == "WD"
    assert "Bath Fan" in changes["options"]["from"]
    assert "Bath Fan" not in changes["options"]["to"]


def test_import_live_with_no_status_frames_is_a_clean_message(client):
    """No bridge, wrong log, a log from another bus -- all the same quiet outcome."""
    client.post("/api/projects", json={"name": "imp3"})
    r = client.post("/api/projects/imp3/preview/import",
                    json={"capture": "(1.0) can1 19FEDB9F#01FC00FFFFFFFFFF\n"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["frames"] == 0
    assert body["diff"] == []
    assert "no" in body["message"].lower()


def test_import_live_rejects_nothing_and_never_500s(client):
    client.post("/api/projects", json={"name": "imp4"})
    for junk in ("", "hello", "\x00\x01\x02", "19FED9"):
        r = client.post("/api/projects/imp4/preview/import", json={"capture": junk})
        assert r.status_code == 200, (junk, r.text)
        assert r.json()["frames"] == 0


def test_page_stays_findable_after_its_header_is_renamed(client):
    """Renaming a page header must not make the page unreachable in the editor.

    Pages resolve by header LABEL (`cli._resolve_page_root`), so renaming HOME's header
    to '   MY COACH' -- which is one of reference build v1's own ops -- used to
    404 every render and placements request for `home`. The editor broke the page the
    user was editing, mid-edit, and the canvas went blank. Object indices never move, so
    the name is resolved against stock as a fallback.
    """
    _make(client, "hdr")
    doc = client.get("/api/projects/hdr").json()
    project = doc["project"]
    root = client.get("/api/reference/pages").json()
    home_root = [p["root"] for p in root if p["name"] == "home"][0]
    project["ops"] = [{"id": "r", "type": "rename_label", "enabled": True, "note": "",
                       "params": {"object": home_root, "old": "   HOME",
                                  "new": "   MY COACH", "allow_grow": True}}]
    saved = client.put("/api/projects/hdr",
                       json={"project": project, "expected_mtime": doc["mtime"]})
    assert saved.status_code == 200, saved.text
    assert client.post("/api/projects/hdr/compile").json()["ok"]

    body = client.get("/api/projects/hdr/placements/home")
    assert body.status_code == 200, body.text
    labels = {(p["label"] or "") for p in body.json()["placements"]}
    assert any("MY COACH" in l for l in labels), "should be the renamed page itself"
    assert client.get("/api/projects/hdr/render/home").status_code == 200


def test_battery_block_follows_the_generator_setting(client):
    """The HOME battery block is DERIVED, not picked.

    Object 4's type-6 switch on slot 6 chooses between four battery blocks
    (901/902/903/904). Slot 6 is not a config field and no tap handler writes it:
    `FUN_00247BF8` computes it from the AGS register (0x2003A38C) -- AGS in {2,3}
    (AES, AES Single Output) gives 1, AGS == 1 gives 2, anything else 0
    (see reference.derived_slots for the traced branches).

    Before this, reaching object 902 -- the block reference build v1's own
    `battery` op edits -- meant typing `6:1` into the raw-slots escape hatch, which no
    owner would guess. Picking their coach's floorplan does it now.
    """
    from studio.server import reference
    stock = open("firefly-13.6/mcf.bin", "rb").read()

    opts = reference.config(stock)["options"]
    ags_slot = next(o["slot"] for o in opts if o["name"] == "AGS")
    for ags, want in ((0, 0), (1, 2), (2, 1), (3, 1)):
        assert reference.derived_slots(stock, {ags_slot: ags})[6] == want, ags
    # Nothing is invented for a preview that never chose a generator mode: slot 6 stays
    # absent, and the canvas draws the same default block the panel does. (The nav rail
    # beside it IS derived from an absent AGS -- see its own test for why they differ.)
    assert 6 not in reference.derived_slots(stock, {5: 8})


def test_derived_slot_reaches_the_render_and_can_be_overridden(client):
    from studio.server import engine
    stock = open("firefly-13.6/mcf.bin", "rb").read()

    def slot6(preview):
        pairs = dict(p.split(":")
                     for p in engine.preview_slots(stock, preview).split(",") if p)
        return pairs.get("6")

    assert slot6({"floorplan": "WD", "options": ["AES"]}) == "1"
    assert slot6({"floorplan": "WD", "options": ["AGS"]}) == "2"
    # A WD coach's own defaults select AES Single Output, so the owner's floorplan alone
    # puts the right battery block on the canvas.
    cfg = client.get("/api/reference/config").json()
    wd = next(f for f in cfg["floorplans"] if f["code"] == "WD")
    defaults = [o["name"] for o in cfg["options"]
                if cfg["defaults"][str(wd["value"])].get(o["name"])]
    assert slot6({"floorplan": "WD", "options": defaults}) == "1"
    # And the escape hatch still wins, exactly as it does for the runtime gates.
    assert slot6({"floorplan": "WD", "options": ["AES"], "extra_slots": {"6": 0}}) == "0"


def test_derived_battery_block_is_the_one_actually_rendered(client):
    """Not just the slot string: the object the walk places must change with it."""
    _make(client, "batt")
    doc = client.get("/api/projects/batt").json()
    project = doc["project"]

    def placed(options):
        project["preview"] = {"floorplan": "WD", "options": options}
        mtime = client.get("/api/projects/batt").json()["mtime"]
        r = client.put("/api/projects/batt",
                       json={"project": project, "expected_mtime": mtime})
        assert r.status_code == 200, r.text
        body = client.get("/api/projects/batt/placements/home").json()
        return {p["obj_index"] for p in body["placements"]}

    assert 901 in placed([]) and 902 not in placed([])
    aes = placed(["AES"])
    assert 902 in aes and 901 not in aes, "AES must show the inverter/AGS battery block"
    ags = placed(["AGS"])
    assert 903 in ags and 902 not in ags


def test_nav_rail_drops_the_electrical_icon_when_nothing_electrical_is_fitted(client):
    """The left icon column is DERIVED too, and its default is the wrong picture.

    Object 974's type-6 switch (child_base 385, count 4) picks the rail from slot 453,
    and GuiCode computes that slot rather than receiving it -- `0x00249D3E`-`0x00249D60`
    clears it iff AGS == 0 && Go Power! count == 0 && Progressive Inverter == 0, and
    `0x00249F02` sets it 1 otherwise. Value 0 selects object 386, a four-icon rail with
    no Electrical bolt and a wider gap; every other value falls through to the 0xffff
    default candidate 385, the five-icon rail.

    Undrived, the switch takes that default -- so a coach with no generator, no solar
    controller and no Progressive Inverter previewed with an Electrical icon the panel
    does not draw. Owner-reported from the coach, 2026-08-21.
    """
    from studio.server import reference
    stock = open("firefly-13.6/mcf.bin", "rb").read()

    opts = reference.config(stock)["options"]
    ags = next(o["slot"] for o in opts if o["name"] == "AGS")
    pi = next(o["slot"] for o in opts if o["name"] == "Progressive Inverter")

    # Any one of the three sources on is enough to show it.
    assert reference.derived_slots(stock, {5: 8, ags: 3})[453] == 1
    assert reference.derived_slots(stock, {5: 8, ags: 1})[453] == 1
    assert reference.derived_slots(stock, {5: 8, pi: 1})[453] == 1
    assert reference.derived_slots(stock, {5: 8, reference.GO_POWER_SLOT: 2})[453] == 1
    # All three off -- the owner's case.
    assert reference.derived_slots(stock, {5: 8, ags: 0, pi: 0})[453] == 0
    assert reference.derived_slots(stock, {5: 8})[453] == 0
    # And nothing is derived for a preview that expressed no config at all.
    assert reference.derived_slots(stock, {}) == {}


def test_derived_nav_rail_is_the_one_actually_rendered(client):
    """Not just the slot string: the rail object the walk places must change with it."""
    _make(client, "rail")
    doc = client.get("/api/projects/rail").json()
    project = doc["project"]

    def placed(options):
        project["preview"] = {"floorplan": "WD", "options": options}
        mtime = client.get("/api/projects/rail").json()["mtime"]
        r = client.put("/api/projects/rail",
                       json={"project": project, "expected_mtime": mtime})
        assert r.status_code == 200, r.text
        body = client.get("/api/projects/rail/placements/home").json()
        return {p["obj_index"] for p in body["placements"]}

    aes = placed(["AES Single Output"])
    assert 385 in aes and 386 not in aes, "AES fitted -> the five-icon rail"
    none = placed([])
    assert 386 in none and 385 not in none, "nothing electrical -> the four-icon rail"
    pi = placed(["Progressive Inverter"])
    assert 385 in pi and 386 not in pi, "the inverter alone brings the icon back"


def test_config_reports_what_a_setting_derives_without_the_arithmetic(client):
    body = client.get("/api/reference/config").json()
    derives = body["derives"]
    assert derives, "the picker needs the linkage to explain the coupling"
    ags = next(o["slot"] for o in body["options"] if o["name"] == "AGS")
    pi = next(o["slot"] for o in body["options"] if o["name"] == "Progressive Inverter")
    by_slot = {}
    for d in derives:
        by_slot.setdefault(d["source_slot"], []).append(d["what"])
    assert set(by_slot) == {ags, pi}
    assert any("battery" in w for w in by_slot[ags])
    # Both settings feed the nav rail, so the note has to sit beside each of them.
    assert any("icon column" in w for w in by_slot[ags])
    assert any("icon column" in w for w in by_slot[pi])
    # The mapping itself must NOT be shipped to the client: one copy of a rule read out
    # of GuiCode, applied server-side, is the whole point.
    for d in derives:
        assert "values" not in d and "mapping" not in d


def test_slot_groups_say_which_page_places_them(client):
    """A pickable control the owner cannot find the purpose of is a bug in the picker.

    13.6 has one of each kind: 'Batteries' (object 1235, slot 599) is placed on the
    options-electrical page and changing its slot changes that page, while 'Battery'
    (object 1121, slot 509) is placed by NO root in the entire object table -- an
    unattached template, like 247/278-281/1038. Both are still listed (hiding one only
    moves the question), but the payload has to say which is which.

    `page` is deliberately "no page THIS renderer draws places it", not "the panel never
    draws it": object 247 is attached by none of the graph mechanisms either
    (`GUICODE.md` Finding 15) and is on screen via the option -> placement binding this
    project has not located.
    """
    from fireflymcf import cli, layout
    from studio.server import reference

    stock = open("firefly-13.6/mcf.bin", "rb").read()
    groups = {g["label"]: g for g in client.get("/api/reference/config").json()
              ["slot_groups"]}
    assert groups["Batteries"]["page"] == "options-electrical"
    assert groups["Battery"]["page"] is None

    # And the reachability claim itself, checked against the walk rather than trusted:
    # toggling the placed group changes its page, and the unplaced one changes nothing.
    screen = layout.Screen(stock)
    roots = {n: cli._resolve_page_root(stock, n) for n in ("home", "electrical")}
    roots.update(reference.options_page_names(stock))
    for label, expect_change in (("Batteries", True), ("Battery", False)):
        slot = groups[label]["slot"]
        changed = set()
        for name, root in roots.items():
            a = {p.obj_index for p in screen.walk(root, slot_values={512: 1, slot: 0})}
            b = {p.obj_index for p in screen.walk(root, slot_values={512: 1, slot: 1})}
            if a != b:
                changed.add(name)
        assert bool(changed) is expect_change, (label, changed)


# --- Cross-coach: the server must work when pointed at a non-13.6 stock ------------------
# Studio takes `--stock`, so an owner on any build runs it against their own firmware. These
# exercise the whole path (reference data, compile, render, export) on 11.23 and 14.7, and pin the
# graceful degradation where a build lacks a structure (11.23 has no decodable floorplan table).
OTHER_BUILDS = ["firefly-11.23/mcf.bin", "firefly-14.7 (Terrain)/mcf.bin"]


@pytest.fixture(params=OTHER_BUILDS)
def other_client(request, tmp_path):
    from studio.server.app import create_app
    app = create_app(stock_path=request.param,
                     projects_root=str(tmp_path / "projects"),
                     build_root=str(tmp_path / "build"))
    c = TestClient(app, base_url="http://localhost")
    c.stock_path = request.param
    return c


def test_reference_endpoints_work_on_other_builds(other_client):
    for path in ("/api/reference/pins", "/api/reference/colors", "/api/reference/channels",
                 "/api/reference/fonts", "/api/reference/pages", "/api/reference/config"):
        r = other_client.get(path)
        assert r.status_code == 200, f"{path} on {other_client.stock_path}: {r.text[:120]}"


def test_config_recovers_the_floorplan_picker_on_1123(tmp_path):
    """11.23 has no FLOORPLAN SELECTION screen and no name switch, but its value->code map is
    recovered from the anchor-indexed label run and corroborated by the tap-handler oracle
    (`guidata.find_floorplan_table`), so the config endpoint now serves a real picker there --
    eight floorplans led by 'Ship Mode' (value 0; hardware-confirmed map 2026-08-30) -- rather
    than degrading to none. The endpoint must not 500, and options are still served."""
    from studio.server.app import create_app
    app = create_app(stock_path="firefly-11.23/mcf.bin",
                     projects_root=str(tmp_path / "p"), build_root=str(tmp_path / "b"))
    r = TestClient(app, base_url="http://localhost").get("/api/reference/config")
    assert r.status_code == 200
    body = r.json()
    codes = {f["code"] for f in body["floorplans"]}
    assert "3WD / 4WD" in codes and "Ship Mode" in codes
    assert set(body["defaults"]) == {str(f["value"]) for f in body["floorplans"]}
    assert isinstance(body["options"], list)          # options are still served


def test_compile_render_export_round_trip_on_other_builds(other_client):
    assert other_client.post("/api/projects", json={"name": "t", "ops": []}).status_code == 201
    assert other_client.post("/api/projects/t/compile").status_code == 200
    assert other_client.get("/api/projects/t/render/home").status_code == 200
    pl = other_client.get("/api/projects/t/placements/home")
    assert pl.status_code == 200 and len(pl.json()["placements"]) > 0
    ex = other_client.post("/api/projects/t/export")
    assert ex.status_code == 200
    assert other_client.get("/api/projects/t/export/file").status_code == 200
