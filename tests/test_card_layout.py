"""aes_card_layout: the v48 four-card ELECTRICAL page (spec 2026-10-15)."""
import json
from pathlib import Path

import pytest

from fireflymcf import cardlayout, guidata, layout, options, recipe, records

REPO = Path(__file__).resolve().parents[1]
STOCK = REPO / "firefly-13.6/mcf.bin"
V47_RECIPE = REPO / "builds/mcf-13.6-batt-outline.ffproj.json"
COACH = options.CoachConfig(
    floorplan="WD",
    options=["AES Single Output", "Bunk Accent", "Cargo / Bath Light\n    (CH. 25)",
             "Black Tank", "Bath Fan"],
    extra_slots={6: 1, 37: 3, 113: 1, 512: 1, 38: 0, 39: 0})


def _v48_project():
    p = json.loads(V47_RECIPE.read_text())
    p["build_name"] = "card-layout-test"
    for op in p["ops"]:
        if op["id"] == "battheat":
            op["params"] = {"x": 84, "y": 156, "w": 328, "h": 54, "text": "Battery Heater"}
        if op["id"] == "status":
            op["params"] = {"button": {"ref": "battheat"}}
    p["ops"].append({"id": "cards", "type": "aes_card_layout", "params": {}, "enabled": True})
    return p


@pytest.fixture(scope="module")
def built():
    stock = STOCK.read_bytes()
    res = recipe.compile_project(recipe.Project.from_json(json.dumps(_v48_project())), stock)
    assert res.ok, res.errors
    v47 = recipe.compile_project(recipe.Project.from_json(V47_RECIPE.read_text()), stock)
    assert v47.ok, v47.errors
    return res, v47.data


def _walk(data):
    sv = dict(options.slot_values(data, COACH))
    sv.update(options.derived_slots(data, sv))
    sv[113] = 1
    return layout.Screen(data).walk(5, slot_values=sv)


def _inside(p, box):
    x0, y0, x1, y1 = box
    return x0 <= p.x0 and y0 <= p.y0 and p.x1 <= x1 and p.y1 <= y1


CARD_BOXES = {t: (x, y, x + w, y + h) for t, (x, y, w, h) in cardlayout.CARDS}


def test_four_cards(built):
    res, _ = built
    placed = [p for p in _walk(res.data) if p.obj_index == 829 and p.wtype in (32, 33)]
    boxes = [(p.x0, p.y0, p.x1, p.y1) for p in placed]
    for title, box in CARD_BOXES.items():
        assert box in boxes, (title, boxes)


def test_titles(built):
    res, _ = built
    labels = {p.label: p for p in _walk(res.data) if p.obj_index == 829 and p.wtype == 11}
    for title, (x, y, _w, _h) in cardlayout.CARDS:
        p = labels[title]
        assert (p.x0, p.y0) == (x + cardlayout.TITLE_DX, y + cardlayout.TITLE_DY)
        assert p.x1 > p.x0 and p.y1 > p.y0                       # a declared box (Finding 69)


def test_every_control_inside_its_card(built):
    res, _ = built
    pl = _walk(res.data)
    where = {   # object -> card title
        1437: "Batteries", 440: "Batteries", 1433: "Batteries", 1434: "Batteries",
        1426: "Auto Engine Start", 249: "Auto Engine Start", 251: "Auto Engine Start",
        154: "Auto Engine Start", 285: "Auto Engine Start", 1427: "Auto Engine Start",
        1409: "Inverter", 1421: "Inverter",
        1428: "Water Pump", 393: "Water Pump"}
    for p in pl:
        card = where.get(p.obj_index)
        if card and p.x1 > p.x0 and p.y1 > p.y0 and p.wtype not in (8, 0):
            assert _inside(p, CARD_BOXES[card]), (p.obj_index, p.wtype, p.label,
                                                  (p.x0, p.y0, p.x1, p.y1), card)


def test_spec_anchor_positions(built):
    res, _ = built
    pl = _walk(res.data)

    def first(obj, wt):
        return next(p for p in pl if p.obj_index == obj and p.wtype == wt)

    assert (first(1426, 33).x0, first(1426, 33).y0) == (138, 266)
    assert (first(285, 33).x0, first(285, 33).y0) == (138, 400)
    assert (first(1409, 33).x0, first(1409, 33).y0) == (452, 82)
    assert (first(1421, 33).x0, first(1421, 33).y0) == (452, 156)
    assert (first(1428, 33).x0, first(1428, 33).y0) == (503, 286)
    heater = [p for p in pl if p.obj_index in range(1433, 1437) and p.wtype == 33][0]
    assert (heater.x0, heater.y0, heater.x1, heater.y1) == (86, 158, 410, 208)
    assert [p for p in pl if p.label == "Triggers:"] == []


def test_only_private_objects_change(built):
    res, v47 = built
    gs8, t8 = guidata.guidata_extent(res.data)[0], guidata.object_tables(res.data)[1]
    gs7, t7 = guidata.guidata_extent(v47)[0], guidata.object_tables(v47)[1]
    # the only new objects are the private AES-pill band, appended after every v47 object
    # (cards runs last, so nothing else renumbers)
    cluster = records.object_records(res.data, gs8 + t8[1427])
    band = set(range(cluster[6].child_index, cluster[6].child_index + 8))
    assert min(band) >= len([i for i in range(len(t7)) if t7[i] > 0]) - 1, sorted(band)
    sw = [r for r in records.object_records(v47, gs7 + t7[829]) if r.wtype == 6 and r.count == 4]
    arms = set(range(sw[0].child_index, sw[0].child_index + 4))
    allowed = {829, 1427} | arms | band
    changed = []
    for i in range(min(len(t7), len(t8))):
        if i in band:
            continue
        a = records.object_records(v47, gs7 + t7[i]) if t7[i] > 0 else []
        b = records.object_records(res.data, gs8 + t8[i]) if t8[i] > 0 else []
        if [v47[r.offset:r.end] for r in a] != [res.data[r.offset:r.end] for r in b]:
            changed.append(i)
    assert set(changed) <= allowed, sorted(set(changed) - allowed)
    assert 829 in changed and 1427 in changed


def test_refuses_without_batt_heat():
    p = _v48_project()
    p["ops"] = [o for o in p["ops"] if o["id"] not in ("battheat", "status", "battglyph")]
    res = recipe.compile_project(recipe.Project.from_json(json.dumps(p)), STOCK.read_bytes())
    assert not res.ok
    assert any("WRONG_SHAPE" in str(e) for e in res.errors), res.errors


def test_refuses_default_size_heater():
    """v47's recipe as-is plus `cards`: the heater is still 122x173 at 672,290, which would land
    on the Water Pump card -- the op must refuse rather than ship overlapping controls."""
    p = json.loads(V47_RECIPE.read_text())
    p["ops"].append({"id": "cards", "type": "aes_card_layout", "params": {}, "enabled": True})
    res = recipe.compile_project(recipe.Project.from_json(json.dumps(p)), STOCK.read_bytes())
    assert not res.ok
    assert any("WRONG_SHAPE" in str(e) for e in res.errors), res.errors


def _tail_state(data):
    sv = dict(options.slot_values(data, COACH))
    sv.update(options.derived_slots(data, sv))
    sv[113] = 1
    st = layout._State()
    layout.Screen(data).walk(829, state=st, slot_values=sv)      # the frame's OWN tail
    return st.font, st.align, dict(st.fill_slots), dict(st.fill_transparent)


def test_page_ends_in_v47_render_state(built):
    """Enrolled switches repaint live from a state path GUICODE Finding 78 has not located, so the
    page must END in exactly v47's persistent font/align/colour state: nothing the cards or titles
    set may be what a repaint inherits."""
    res, v47 = built
    assert _tail_state(res.data) == _tail_state(v47)


def test_stock_controls_inherit_v47_state(built):
    """Every placement outside 829, the cluster and the heater arms draws with v47's font, align
    and effective colours: the new records leak no persistent state into the shared controls."""
    res, v47 = built
    # the AES pill band is compared in test_disabled_half_is_engine_navy (a private recoloured
    # copy replaces stock 154-161 on this page by design)
    skip = {829, 1427} | set(range(1433, 1437)) | set(range(154, 162)) | {
        r.child_index + k for r in records.object_records(
            res.data, guidata.guidata_extent(res.data)[0]
            + guidata.object_tables(res.data)[1][1427])[6:7] for k in range(8)}

    def key(pl):
        return [(p.obj_index, p.wtype, p.label, p.font, p.align, p.h_align, p.colors)
                for p in pl if p.obj_index not in skip]
    assert key(_walk(res.data)) == key(_walk(v47))


def test_titles_drawn_after_every_fill_under_them(built):
    """The panel paints in stream order (v48 on the coach lost 'Water Pump' under its own card,
    which the renderer -- fills before text -- did not show). Every card/box fill a title
    overlaps must come BEFORE that title in the walk."""
    res, _ = built
    pl = [p for p in _walk(res.data) if p.obj_index == 829]
    for i, t in enumerate(pl):
        if t.wtype != 11 or t.label not in cardlayout.TITLE_TEXTS:
            continue
        for j, f in enumerate(pl):
            invisible = (f.x0 == f.x1 and f.y0 == f.y1    # a 0x0 anchor in the card colour
                         and ("fillA", 7, cardlayout.CARD_FILL_INDEX) in f.colors)
            if (f.wtype in (32, 33) and not invisible and f.x0 <= t.x1 and t.x0 <= f.x1
                    and f.y0 <= t.y1 and t.y0 <= f.y1):
                assert j < i, ("fill %s drawn after title %r" % ((f.x0, f.y0, f.x1, f.y1), t.label))


def test_no_white_pixel_left_exposed(built):
    """An empty label with an explicit white (mode-2) background paints its 0x0 box as one
    pixel (v48 on the coach: a white dot on the Battery Heater's corner). Any such record in 829
    must sit inside a capsule drawn after it."""
    res, _ = built
    pl = _walk(res.data)
    for i, p in enumerate(pl):
        if (p.obj_index == 829 and p.wtype == 11 and not p.label
                and ("fillB", 2, None) in p.colors):
            assert any(q.wtype == 33 and q.x0 < p.x0 < q.x1 and q.y0 < p.y0 < q.y1
                       for q in pl[i + 1:]), ("exposed white pixel at", (p.x0, p.y0))


def test_disabled_half_is_engine_navy(built):
    """The AES pill's DISABLED half fills with the engine-status navy (index 3, as 1426[0]),
    through a private copy of the shared band 154-161; ENABLED keeps its cyan."""
    res, _ = built
    gs, tab = guidata.guidata_extent(res.data)[0], guidata.object_tables(res.data)[1]
    sw = records.object_records(res.data, gs + tab[1427])[6]
    assert sw.child_index != 154 and sw.count == 8
    fills = {}
    for k in range(8):
        arm = records.object_records(res.data, gs + tab[sw.child_index + k])
        fills.setdefault(arm[2].label, set()).add(arm[0].colors[0])
    assert fills["DISABLED"] == {("fillA", 7, 3)}
    assert fills["ENABLED"] == {("fillA", 7, 35)}


# --- two BMS buttons in the Batteries card (v49: Battery Heater + Ignore Alarms) --------------

def _two_button_project():
    p = _v48_project()
    ids = [o["id"] for o in p["ops"]]
    for op in p["ops"]:
        if op["id"] == "battheat":
            op["params"] = {"x": 84, "y": 156, "w": 163, "h": 54, "text": "Battery\nHeater"}
    alarms = [
        {"id": "alarms", "type": "add_aes_batt_heat", "enabled": True,
         "params": {"x": 249, "y": 156, "w": 163, "h": 54, "text": "Ignore\nAlarms",
                    "enable_wire": "4d53000000001770", "off_wire": "4d53000000000000",
                    "group_pool": "hold_safe"}},
        {"id": "alarms-status", "type": "add_aes_bms_button_status", "enabled": True,
         "params": {"button": {"ref": "alarms"}, "status": {"ref": "status"}, "mask": 2}}]
    at = ids.index("cards")
    p["ops"][at:at] = alarms
    return p


@pytest.fixture(scope="module")
def built2():
    res = recipe.compile_project(recipe.Project.from_json(json.dumps(_two_button_project())),
                                 STOCK.read_bytes())
    assert res.ok, res.errors
    return res.data


def _button_capsules(data):
    """{caption: capsule box} for the BMS buttons' selected (default) arms."""
    out = {}
    pl = _walk(data)
    for i, p in enumerate(pl):
        if p.wtype == 33 and i + 1 < len(pl) and pl[i + 1].label in ("Battery\nHeater",
                                                                     "Ignore\nAlarms"):
            out[pl[i + 1].label] = (p.x0, p.y0, p.x1, p.y1)
    return out


def test_two_buttons_at_their_spec_boxes(built2):
    assert _button_capsules(built2) == {"Battery\nHeater": (86, 158, 245, 208),
                                        "Ignore\nAlarms": (251, 158, 410, 208)}


def test_two_buttons_no_white_pixel_left_exposed(built2):
    pl = _walk(built2)
    for i, p in enumerate(pl):
        if (p.obj_index == 829 and p.wtype == 11 and not p.label
                and ("fillB", 2, None) in p.colors):
            assert any(q.wtype == 33 and q.x0 < p.x0 < q.x1 and q.y0 < p.y0 < q.y1
                       for q in pl[i + 1:]), ("exposed white pixel at", (p.x0, p.y0))


def test_two_buttons_frame_ends_in_v47_state(built2):
    v47 = (REPO / "builds/mcf-13.6-BattOutline-v47.bin").read_bytes()
    assert _tail_state(built2) == _tail_state(v47)


def test_two_buttons_stock_controls_inherit_v47_state(built2):
    v47 = (REPO / "builds/mcf-13.6-BattOutline-v47.bin").read_bytes()
    gs, tab = guidata.guidata_extent(built2)[0], guidata.object_tables(built2)[1]
    frame = records.object_records(built2, gs + tab[829])
    arms = set()
    for r in frame:
        if r.wtype == 6 and r.count:
            arms |= set(range(r.child_index, r.child_index + r.count))
    band = records.object_records(built2, gs + tab[1427])[6].child_index
    skip = {829, 1427} | arms | set(range(154, 162)) | set(range(band, band + 8)) \
        | set(range(1433, 1437))

    def key(pl):
        return [(p.obj_index, p.wtype, p.label, p.font, p.align, p.h_align, p.colors)
                for p in pl if p.obj_index not in skip]
    assert key(_walk(built2)) == key(_walk(v47))


def test_nothing_drawn_after_a_title_touches_its_ink(built2):
    """v50 on the coach: 'Water Pump' lost the bottom of its e and r. The pump inset 829[8], kept
    as a 0x0 anchor, still carried radius 5 -- the panel draws that as a card-coloured blob, and it
    was drawn AFTER the title. A card-coloured fill is not invisible over text: any fill drawn
    after a title, its extent grown by its corner radius, must miss the title's ink rows."""
    from fireflymcf import readoutfill
    gs, tab = guidata.guidata_extent(built2)[0], guidata.object_tables(built2)[1]
    recs = records.object_records(built2, gs + tab[829])
    pl = [p for p in _walk(built2) if p.obj_index == 829]
    by_off = {r.offset: r for r in recs}
    titles = [(i, readoutfill._ink_rows(built2, by_off[p.record_offset],
                                        (p.x0, p.y0, p.x1, p.y1)))
              for i, p in enumerate(pl) if p.wtype == 11 and p.label in cardlayout.TITLE_TEXTS]
    for j, f in enumerate(pl):
        if f.wtype not in (32, 33):
            continue
        r = (f.coord4 or 0)
        grown = (f.x0 - r, f.y0 - r, f.x1 + r, f.y1 + r)
        for i, ink in titles:
            if j > i:
                assert not readoutfill._hit(grown, ink), (f.record_offset, grown, ink)


def test_authored_labels_have_a_transparent_background():
    """v50 on the coach: the 'm' of 'Amps' lost its right edge -- an inherited (opaque) fillB lets
    each glyph cell paint over its neighbour. Stock captions use fillB mode 5 (transparent)."""
    rec = records.decode_record(cardlayout.title_record(86, 50, "Amps"), 0)
    assert ("fillB", 5, None) in rec.colors, rec.colors
