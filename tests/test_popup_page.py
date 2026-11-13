"""`add_popup_page`: a LEVEL-SETUP-style dialog on a screen `add_screen` added -- the taller
dialog the LEVEL SETUP popup draws with Wheelbase and Track, its title, and Close returning to
the page named by `return_to`."""
import json
import struct

import pytest

from fireflymcf import guidata, layout, recipe, records, themeswap
from fireflymcf import levelpage as lp
from fireflymcf import levelsetup as ls
from fireflymcf import pagedraw as pd

STOCK = "firefly-13.6/mcf.bin"
STOCK_SHA = "d66b41ffafaae4f5ec47cf90714f14bb1954fc3a5ae38defa98a94cd9643d382"
ELECTRICAL_RAIL_LEAF = 0x7D1


def _project(popup_params):
    return recipe.Project.from_json(json.dumps({
        "schema": 1, "base_sha256": STOCK_SHA, "build_name": "popup-test",
        "ops": [{"id": "screen", "type": "add_screen", "params": {"title": "BATTERY SETUP"},
                 "enabled": True},
                {"id": "popup", "type": "add_popup_page", "params": popup_params,
                 "enabled": True}],
    }))


PARAMS = {"screen": {"ref": "screen"}, "title": "BATTERY SETUP", "return_to": "ELECTRICAL"}


@pytest.fixture(scope="module")
def built():
    stock = open(STOCK, "rb").read()
    res = recipe.compile_project(_project(PARAMS), stock)
    assert res.ok, res.errors
    data = res.data
    root = layout.screen_object_map(data)[35]
    gs, _ = guidata.guidata_extent(data)
    start = gs + guidata.object_tables(data)[1][root]
    recs = records.object_records(data, start)
    return data, root, recs, bytes(data[start:recs[-1].end])


def test_dialog_frame(built):
    data, _root, _recs, span = built
    x, y, w, h, r = ls.GEOMETRY_DIALOG
    assert pd.rbox(data, x, y, w, h, r, ls.BORDER, ls.FILL) in span


def test_title(built):
    data, _root, _recs, span = built
    x, _y, w, _h, _r = ls.GEOMETRY_DIALOG
    assert lp._centre(data, "BATTERY SETUP", x, w, ls.GEOMETRY_TITLE_Y, font=lp.F_CAPTION,
                      rgb=lp.WHITE) in span


def test_close_button_and_region(built):
    data, _root, recs, span = built
    for frag in ls._button(data, ls.GEOMETRY_CLOSE, "Close"):
        assert frag in span
    last = recs[-1]
    assert last.wtype == 12
    assert struct.unpack_from("<H", data, last.end - guidata.GROUP_REFERENCE_TAIL)[0] \
        == ELECTRICAL_RAIL_LEAF
    assert bytes(data[last.offset:last.end]) == pd.touch(data, *ls.GEOMETRY_CLOSE,
                                                         ELECTRICAL_RAIL_LEAF)


def test_colours_themeable(built):
    data, root, _recs, _span = built
    allowed = set(themeswap.STOCK.values()) | {0x262C60, 0xFFFFFF}     # + levelpage.BODY, white
    used = {val for _off, val, obj in themeswap.inline_fields(data) if obj == root}
    assert used and used <= allowed, sorted(hex(v) for v in used - allowed)


def test_handle(built):
    stock = open(STOCK, "rb").read()
    res = recipe.compile_project(_project(PARAMS), stock)
    assert "popup" in res.unverified


def test_unknown_return_page_refused():
    stock = open(STOCK, "rb").read()
    res = recipe.compile_project(_project(dict(PARAMS, return_to="NO SUCH PAGE")), stock)
    assert not res.ok
    assert [e["code"] for e in res.errors] == ["NO_RETURN"], res.errors


# ---- the opener: a gear on a host page that opens the popup ---------------------------------
from fireflymcf import navmap  # noqa: E402

GEAR_LEAF = 0x7D5
AT = [600, 40]           # stock ELECTRICAL (root 5): clear of every touch region and readout


@pytest.fixture(scope="module")
def opened():
    stock = open(STOCK, "rb").read()
    res = recipe.compile_project(
        _project(dict(PARAMS, opener={"page": "ELECTRICAL", "at": AT})), stock)
    assert res.ok, res.errors
    return res.data


def test_gear_on_host_page(opened):
    from fireflymcf import popuppage
    data = opened
    host = layout.screen_object_map(data)[1]
    gs, _ = guidata.guidata_extent(data)
    recs = records.object_records(data, gs + guidata.object_tables(data)[1][host])
    tail = [bytes(data[r.offset:r.end]) for r in recs[-3:]]
    assert tail == popuppage.gear_records(data, *AT, GEAR_LEAF)
    assert [r.wtype for r in recs[-3:]] == [33, 11, 12]


def test_leaf_retargeted(opened):
    hits = [r for r in navmap.routes(opened) if r.touch_id == GEAR_LEAF]
    assert [h.screen for h in hits] == [35]


def test_opener_overlap_refused():
    stock = open(STOCK, "rb").read()
    # on the nav rail's ELECTRICAL icon: its touch region
    res = recipe.compile_project(
        _project(dict(PARAMS, opener={"page": "ELECTRICAL", "at": [8, 70]})), stock)
    assert not res.ok
    assert [e["code"] for e in res.errors] == ["OPENER_OVERLAP"], res.errors
