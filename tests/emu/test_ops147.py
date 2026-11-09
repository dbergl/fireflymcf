"""The ops enabled on 14.7, each exercised in the emulator on a build of 14.7 stock.

An op joins 14.7's `firmwares` (and `emulated_on`, never `verified_on`) only with a test here
that compiles it against 14.7 stock, on a target found in 14.7's own records, and shows on
the emulated panel that it does what it does on 13.6 (design: docs/superpowers/specs/
2026-11-06-firmware-14x-support-design.md, goal 3). Every object number, touch id and pixel
box below is a 14.7 test expectation read from 14.7's records and walk (`fireflymcf pages`,
`records.object_records`, the live `layout.Screen.walk`), never a 13.6 number and never a
locator.

**HOME settles late on 14.7.** For the first ~5 s HOME draws its boot arms (the tank card
with a Water Pump icon button, the battery card with Star Link); once the G12's
configuration arrives its switches pick the coach's arms: the tank card with Water Pump and
Tank Heater buttons (objects 260, 816), the battery card House | Chassis (object 765). Every
screenshot is taken at `SETTLE_S` of simulated time, stock and build alike: the emulator is
deterministic, so two boots of one image draw the same pixels at the same simulated time. The
one input from outside, the clock the demo network broadcasts (the image-driven G12's
`clock`, else `peers.Clock`: the host's local time), is pinned to `CLOCK` for the module, or a
minute boundary between two boots repaints the header.

Each test fails when its build is replaced by the stock image unchanged (checked 2026-11-07).
"""
import hashlib
import time

import numpy as np
import pytest

pytest.importorskip("unicorn")

from fireflymcf import (aescompact, buildcheck, celltables, container,  # noqa: E402
                        coverage, donors, followchannel, glyphs, guidata, insert, layout,
                        lcdgroups, metrics, newbutton, pagewalk, patch, recipe, records, rvcbutton,
                        status_map, themeswap)
from fireflymcf.emu import g12, peers  # noqa: E402
from fireflymcf.emu.canbus import Peer  # noqa: E402
from fireflymcf.emu.scenario import PANEL_SA, Scenario  # noqa: E402

NEEDS_RESEARCH_DATA = "builds the 14.7 corpus image and boots it in the emulator"

SETTLE_S = 12.0
DC_DIMMER_COMMAND_2 = 0x1FEDB
TOGGLE = 5

# 14.7 HOME after it settles (live walk at SETTLE_S); test expectations only.
BATTERY_CARD = 765              # the battery card: 'House' (record 1), 'Chassis' (record 3)
WATER_PUMP = 260                # wt-0x21 box (radius 5, fill index 145), label, region 0x2C
TANK_HEATER_ARM = 816           # attaches 260, then its own Tank Heater box (record 1, x 140)
TANK_CARD = 781                 # attaches the LP gauge (object 665, record 7, x 5)
LP_GAUGE = 665
AWNING = 150                    # EXT | Awning | RET, attached by card 764 at (32, 17)
CLIMATE_ICON = 94               # the snowflake: font 24 'H'
STAR_LINK = 240                 # 'Star\nLink', group 17: one 1FEDB instance 17 toggle
STARLINK_POWER_SELECTOR = 0xE4  # `config.option_selector(cfg 0x1B)` on 14.7 (inferred-from-code)
STAR_LINK_OUTPUT = 17
LIGHT_MASTER_ON = {22, 24, 25, 32, 34, 35}   # bus-map group 1 on 14.7
GREEN = (43, 182, 115)          # 14.7's static palette: mode-7 index 194 (and 503)
NAVY = (39, 40, 98)             # 14.7's static palette: mode-7 index 1, the card body
YELLOW = (255, 255, 0)
CLOCK = time.struct_time((2026, 11, 7, 11, 0, 0, 5, 311, 0))

# Every op whose `firmwares` lists 14.7, each with its `test_<name>` below. An op joins 14.7 with
# a test here and an entry in this set; `test_every_147_op_is_tested_here` holds both in step.
TESTED_147 = frozenset({
    "rename_label", "set_record_color", "recolor", "move_element", "move_attach",
    "set_corner_radius", "fill_to_capsule", "remove_records", "remove_control", "set_icon",
    "install_glyph", "append_filter", "drop_from_master", "set_theme", "add_text_label",
    "add_register_readout", "add_enum_label", "follow_register", "add_rvc_button",
    "add_button_status", "add_button_states", "add_glyph_button", "wire_button"})


class _PinnedTime:
    """`peers`' view of the `time` module: a fixed local time."""

    @staticmethod
    def localtime(*_):
        return CLOCK


@pytest.fixture(scope="module", autouse=True)
def _pinned_clock():
    built = g12.from_mcf

    def from_mcf(mcf_data, **kwargs):
        return built(mcf_data, **{**kwargs, "clock": _PinnedTime.localtime})

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(g12, "from_mcf", from_mcf)
        mp.setattr(peers, "time", _PinnedTime)
        yield


def _build(stock: bytes, specs) -> bytes:
    project = recipe.Project(base_sha256=hashlib.sha256(stock).hexdigest(), build_name=None,
                             ops=[recipe.OpSpec(id=i, type=t, params=p) for i, t, p in specs])
    res = recipe.compile_project(project, stock)
    assert res.ok, res.errors
    assert res.firmware == "14.7"
    return res.data


def _settled(image: bytes, *, presets: dict | None = None) -> Scenario:
    s = Scenario(image, timeout_s=300)
    for sel, value in (presets or {}).items():
        s.set_g12_selector(sel, value)
    s.boot()
    s.run(SETTLE_S - s.now)
    return s


def _shot(s: Scenario, tmp_path, name: str) -> np.ndarray:
    return s.screenshot(tmp_path / f"{name}.png").astype(int)


def _diff_bbox(a: np.ndarray, b: np.ndarray):
    """(x0, y0, x1, y1) inclusive of every pixel that differs, or None."""
    ys, xs = np.nonzero((a != b).any(axis=2))
    if not len(xs):
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def _inside(bbox, box) -> bool:
    """`bbox` (inclusive) lies within the placement `box` (x1/y1 drawn inclusively)."""
    return (bbox is not None and box[0] <= bbox[0] and box[1] <= bbox[1]
            and bbox[2] <= box[2] and bbox[3] <= box[3])


def _crop(img, box):
    x0, y0, x1, y1 = box
    return img[y0:y1, x0:x1]


def _near(crop: np.ndarray, rgb, tol: int = 8) -> int:
    return int((np.abs(crop - np.array(rgb)) <= tol).all(axis=2).sum())


def _panel_sends(s: Scenario, dgn: int, since: float) -> list:
    return s.frames(dgn=dgn, sa=PANEL_SA, since=since)


def _press_at(s: Scenario, x: int, y: int, hold_s: float = 0.0) -> float:
    """Press the panel at (x, y): a point, not a control, because the control is gone."""
    t = s.now
    s._press(x, y, hold_s)
    return t


def _draws_glyph(crop: np.ndarray, rows, visible: float = 0.9) -> bool:
    """Whether `rows` (0..15 coverage) is drawn in `crop`, the box its record clips to: at one
    offset every full-coverage pixel inside the box is ink, every zero-coverage pixel inside it
    is the box's background, no ink lies outside the glyph's rectangle, and at least `visible`
    of the glyph's full-coverage pixels fall inside the box (a glyph wider than its advance can
    overhang the box edge, and the panel clips it there)."""
    cov = np.array(rows)
    h, w = cov.shape
    flat = crop.reshape(-1, 3)
    vals, counts = np.unique(flat, axis=0, return_counts=True)
    bg = vals[counts.argmax()]
    ink = (crop != bg).any(axis=2)
    H, W = ink.shape
    full = int((cov == 15).sum())
    for oy in range(-h + 1, H):
        for ox in range(-w + 1, W):
            cy0, cx0 = max(oy, 0), max(ox, 0)
            cy1, cx1 = min(oy + h, H), min(ox + w, W)
            if cy1 <= cy0 or cx1 <= cx0:
                continue
            part = cov[cy0 - oy:cy1 - oy, cx0 - ox:cx1 - ox]
            if (part == 15).sum() < visible * full:
                continue
            win = ink[cy0:cy1, cx0:cx1]
            if not win[part == 15].all() or win[part == 0].any():
                continue
            outside = ink.copy()
            outside[cy0:cy1, cx0:cx1] = False
            if not outside.any():
                return True
    return False


@pytest.fixture(scope="module")
def stock(mcf_147, tmp_path_factory):
    """Stock 14.7's settled HOME, its controls' touch boxes, and what a press at Tank Heater's
    centre sends (taken last: the press toggles the heater)."""
    tmp = tmp_path_factory.mktemp("stock147")
    s = _settled(mcf_147)
    out = {"home": _shot(s, tmp, "home"),
           "chassis": s.control_box("Chassis"),
           "water_pump": s.control_box("Water Pump"),
           "ext": s.control_box("EXT"),
           "ret": s.control_box("RET"),
           "awning": s.control_box("Awning"),
           "tank_heater": s.control_box("Tank Heater")}
    _revisit_home(s)
    out["home_again"] = _shot(s, tmp, "home_again")
    t = _press_at(s, *_centre(out["tank_heater"]))
    s.run(1.0)
    out["tank_heater_sends"] = [f.data for f in _panel_sends(s, DC_DIMMER_COMMAND_2, t)]
    return out


def _revisit_home(s: Scenario) -> None:
    """Leave HOME and come back: the page is drawn whole. At `SETTLE_S` an object attached to
    HOME's root inside a card is not on screen: the card's switch picks its arm once the G12's
    configuration arrives (~5 s) and repaints the card over it, and the attached object is
    redrawn only when its own cell changes (2026-11-08; the same on 13.6)."""
    s.goto("CLIMATE CONTROL")
    s.run(1.0)
    s.goto("HOME")
    s.run(2.0)


def _centre(box):
    return (box[0] + box[2]) // 2, (box[1] + box[3]) // 2


# --- labels and colours ----------------------------------------------------------------------

def test_rename_label(mcf_147, stock, tmp_path):
    img = _build(mcf_147, [("r", "rename_label",
                            {"object": BATTERY_CARD, "old": "Chassis", "new": "Chassix"})])
    s = _settled(img)
    box = stock["chassis"]
    bbox = _diff_bbox(stock["home"], _shot(s, tmp_path, "b"))
    assert bbox is not None, "the renamed label drew nothing new"
    assert _inside(bbox, box), (bbox, box)
    assert s.control_box("Chassix") == box


def test_set_record_color(mcf_147, stock, tmp_path):
    img = _build(mcf_147, [("c", "set_record_color",
                            {"target": BATTERY_CARD, "selector": {"wtype": 11, "label": "House"},
                             "color": list(YELLOW)})])
    s = _settled(img)
    box = s.control_box("House")
    shot = _shot(s, tmp_path, "b")
    assert _near(_crop(stock["home"], box), YELLOW) == 0
    assert _near(_crop(shot, box), YELLOW, tol=0) > 50, "the House caption is not yellow"
    assert _inside(_diff_bbox(stock["home"], shot), box)


def test_recolor(mcf_147, stock, tmp_path):
    img = _build(mcf_147, [("c", "recolor", {"object": WATER_PUMP, "color": list(GREEN)})])
    s = _settled(img)
    box = s.control_box("Water Pump")
    shot = _shot(s, tmp_path, "b")
    area = (box[2] - box[0]) * (box[3] - box[1])
    assert _near(_crop(stock["home"], box), GREEN) == 0
    assert _near(_crop(shot, box), GREEN) > area // 2, "the Water Pump box is not green"
    assert _inside(_diff_bbox(stock["home"], shot), box)


def test_recolor_index1(mcf_147, stock, tmp_path):
    """Index 1, the navy body 628 of 14.7's fields use, recolours once the type table is read
    from the code (`guidata.variable_table_literals`); under the old scan it was type 0 and
    `recolor` refused it as NO_SUCH_COLOR (2026-11-07)."""
    img = _build(mcf_147, [("c", "recolor", {"object": WATER_PUMP, "color": {"index": 1}})])
    s = _settled(img)
    box = s.control_box("Water Pump")
    shot = _shot(s, tmp_path, "b")
    area = (box[2] - box[0]) * (box[3] - box[1])
    assert _near(_crop(stock["home"], box), NAVY, tol=0) < area // 4
    assert _near(_crop(shot, box), NAVY, tol=0) > area // 2, "the Water Pump box is not navy"
    assert _inside(_diff_bbox(stock["home"], shot), box)


# CLIMATE CONTROL's root on 14.7 (`fireflymcf pages`: screen 3, root 12), and a label in the
# empty band left of the fan grid; test expectations only.
CLIMATE_ROOT = 12
LABEL = {"x": 90, "y": 320, "w": 180, "h": 30, "font": 16, "text": "FAN SPEED"}
WHITE = (255, 255, 255)


def _text_coverage(img: bytes, slot: int, text: str) -> np.ndarray:
    """`text`'s full coverage (0..15) set in font `slot`, each glyph's bitmap at its kerned
    advance (`metrics.glyph_positions`) and its own bearing, cropped to the ink: the picture
    the panel must draw. A blank (the space: height 0) adds no ink."""
    font = glyphs.font_for_slot(img, slot)
    bpp = glyphs.font_bpp(img, font)
    pos = metrics.glyph_positions(img, slot, text)
    assert pos is not None, "a glyph of the label is missing from the font"
    placed = []
    for ch, x in zip(text, pos):
        try:
            g = glyphs.decode_glyph(img, font.glyph_index(ord(ch)), bpp=bpp)
        except glyphs.GlyphError:
            assert ch == " ", ch
            continue
        placed.append((x + g.x, g.y, np.array(g.rows)))
    top = min(y for _x, y, _r in placed)
    h = max(y + r.shape[0] for _x, y, r in placed) - top
    w = max(x + r.shape[1] for x, _y, r in placed)
    cov = np.zeros((h, w), int)
    for x, y, r in placed:
        sub = cov[y - top:y - top + r.shape[0], x:x + r.shape[1]]
        np.maximum(sub, r, out=sub)
    return cov


def test_add_text_label(mcf_147, tmp_path):
    """White text on CLIMATE CONTROL. 14.7's text drawers are not twins of 13.6's
    (`grammar.prove`), so this is the pixel evidence `donors` defers to: every glyph of the
    text is drawn in the declared box (each full-coverage pixel exactly `ffffff`, each empty
    one the box's background), and no pixel outside the box changes on the page that reaches
    the parent; the other rail pages do not change at all (2026-11-08)."""
    img = _build(mcf_147, [("t", "add_text_label", {"parent": CLIMATE_ROOT, **LABEL})])
    assert pagewalk.reaching_roots(img, CLIMATE_ROOT) == [CLIMATE_ROOT]
    box = (LABEL["x"], LABEL["y"], LABEL["x"] + LABEL["w"], LABEL["y"] + LABEL["h"])
    s = _settled(img)
    assert s._roots_for("CLIMATE CONTROL") == {CLIMATE_ROOT}
    stock = _rail_shots(_settled(mcf_147), tmp_path, "stock")
    shots = _rail_shots(s, tmp_path, "build")
    for page in RAIL_PAGES:
        bbox = _diff_bbox(stock[page], shots[page])
        if page == "CLIMATE":
            assert bbox is not None, "the label drew nothing"
            assert _inside(bbox, box), (bbox, box)
        else:
            assert bbox is None, (page, bbox)
    cov = _text_coverage(img, LABEL["font"], LABEL["text"])
    x0, y0, x1, y1 = box
    crop = shots["CLIMATE"][y0:y1 + 1, x0:x1 + 1]
    assert not _draws_glyph(stock["CLIMATE"][y0:y1 + 1, x0:x1 + 1], cov)
    assert _draws_glyph(crop, cov, visible=1.0), "the text is not drawn in the box"
    assert _exact(crop, WHITE) == int((cov == 15).sum()), "the full-coverage ink is not white"


# --- geometry -----------------------------------------------------------------------------------

def _assert_translated(before, after, box, dx):
    """The pixels of `box` in `before` are drawn `dx` to the right in `after`, the strip they
    left is the plain background, and nothing else changed."""
    x0, y0, x1, y1 = box
    moved = after[y0:y1 + 1, x0 + dx:x1 + 1 + dx]
    assert np.array_equal(moved, before[y0:y1 + 1, x0:x1 + 1]), "not drawn at its new place"
    left = after[y0:y1 + 1, x0:x0 + dx]
    assert (left == left[0, 0]).all() and not np.array_equal(left, before[y0:y1 + 1, x0:x0 + dx]), \
        "still drawn at its old place"
    assert _inside(_diff_bbox(before, after), (x0, y0, x1 + dx, y1))


def test_move_element(mcf_147, stock, tmp_path):
    """Tank Heater's box (record 1 of 816, x 140) to x 180; its label and region (no x of
    their own) follow it. A control is three records: it must draw at its new place and
    answer a press there, and a press where it was must reach nothing."""
    x_was, x_now = 140, 180
    dx = x_now - x_was
    img = _build(mcf_147, [("m", "move_element",
                            {"target": TANK_HEATER_ARM, "selector": {"wtype": 0x21},
                             "set": {"x": x_now}, "expect": {"x": x_was}})])
    s = _settled(img)
    old = stock["tank_heater"]
    new = s.control_box("Tank Heater")
    assert new == (old[0] + dx, old[1], old[2] + dx, old[3]), (old, new)
    _assert_translated(stock["home"], _shot(s, tmp_path, "b"), old, dx)

    assert stock["tank_heater_sends"], "stock's Tank Heater press sent no 1FEDB"
    cx, cy = _centre(new)
    t = _press_at(s, cx, cy)
    s.run(1.0)
    sent = [f.data for f in _panel_sends(s, DC_DIMMER_COMMAND_2, t)]
    assert sent == stock["tank_heater_sends"], f"a press at its new centre: {sent}"
    # Tank Heater toggles (on, then off), so the second press is judged by whom it addresses.
    t = _press_at(s, (old[2] + new[2]) // 2, cy)        # where only the new box is
    s.run(1.0)
    sent = [f.data for f in _panel_sends(s, DC_DIMMER_COMMAND_2, t)]
    assert sent and [d[0] for d in sent] == [d[0] for d in stock["tank_heater_sends"]], \
        f"a press where only the new box is: {sent}"
    t = _press_at(s, (old[0] + new[0]) // 2, cy)        # where only the old box was
    s.run(1.0)
    assert _panel_sends(s, DC_DIMMER_COMMAND_2, t) == [], "the old place still answers"


def test_move_attach(mcf_147, stock, tmp_path):
    # The LP gauge (well and reading), attached by the tank card at x 5: 40 px right. The band
    # checked runs from the gauge's top to the top of the Tank Heater button below it, and
    # from the gauge's left edge to the card's right edge less the move.
    img = _build(mcf_147, [("m", "move_attach", {"page": TANK_CARD, "child": LP_GAUGE, "x": 45})])
    s = _settled(img)
    placed = s._placed(s._root())
    gauge = [q for q in placed if q.obj_index == LP_GAUGE]
    [card] = [q for q in placed if q.obj_index == TANK_CARD and q.wtype == 0x21]
    assert gauge, "the LP gauge is not on HOME"
    below = s.control_box("Tank Heater")[1]
    old = (min(q.x0 for q in gauge) - 40, min(q.y0 for q in gauge), card.x1 - 1 - 40, below - 1)
    _assert_translated(stock["home"], _shot(s, tmp_path, "b"), old, 40)


def test_set_corner_radius(mcf_147, stock, tmp_path):
    img = _build(mcf_147, [("r", "set_corner_radius",
                            {"target": WATER_PUMP, "selector": {"wtype": 0x21}, "radius": 0,
                             "expect": 5})])
    s = _settled(img)
    x0, y0, x1, y1 = box = s.control_box("Water Pump")
    shot = _shot(s, tmp_path, "b")
    for x, y in ((x0, y0), (x1 - 1, y0), (x0, y1 - 1), (x1 - 1, y1 - 1)):
        assert not np.array_equal(shot[y, x], stock["home"][y, x]), ("corner unchanged", x, y)
    inner = (x0 + 6, y0 + 6, x1 - 6, y1 - 6)
    assert np.array_equal(_crop(shot, inner), _crop(stock["home"], inner)), "interior changed"
    assert _inside(_diff_bbox(stock["home"], shot), box)


def test_fill_to_capsule(mcf_147, stock, tmp_path):
    # HOME's header bar: object 9's second wt-4 fill, (0, 0)-(799, 36).
    img = _build(mcf_147, [("c", "fill_to_capsule",
                            {"target": 9, "selector": {"wtype": 4, "nth": 1}, "radius": 18})])
    s = _settled(img)
    shot = _shot(s, tmp_path, "b")
    for x, y in ((0, 0), (799, 0), (0, 35), (799, 35)):
        assert not np.array_equal(shot[y, x], stock["home"][y, x]), ("corner unchanged", x, y)
    inner = (20, 0, 780, 36)
    assert np.array_equal(_crop(shot, inner), _crop(stock["home"], inner)), "interior changed"
    assert _inside(_diff_bbox(stock["home"], shot), (0, 0, 799, 36))


# --- removal -------------------------------------------------------------------------------------

def _assert_gone(stock_home, shot, box):
    gone = _crop(shot, box)
    assert (gone == gone[0, 0]).all(), "the control still draws"
    assert not np.array_equal(gone, _crop(stock_home, box))


def test_remove_records(mcf_147, stock, tmp_path):
    # Water Pump: object 816's attach of 260 (its record 0), which draws the whole button.
    img = _build(mcf_147, [("x", "remove_records", {"target": TANK_HEATER_ARM, "records": [
        {"selector": {"wtype": 5, "nth": 0}, "child": WATER_PUMP}]})])
    s = _settled(img)
    shot = _shot(s, tmp_path, "b")
    box = stock["water_pump"]
    _assert_gone(stock["home"], shot, box)
    assert _inside(_diff_bbox(stock["home"], shot), box)
    t = _press_at(s, *_centre(box))
    s.run(1.0)
    assert _panel_sends(s, DC_DIMMER_COMMAND_2, t) == [], "the removed region still sends"


def test_remove_control(mcf_147, stock, tmp_path):
    img = _build(mcf_147, [("x", "remove_control", {"object": AWNING})])
    s = _settled(img)
    shot = _shot(s, tmp_path, "b")
    _assert_gone(stock["home"], shot, stock["awning"])
    whole = (stock["ext"][0], stock["ext"][1], stock["ret"][2], stock["ret"][3])
    assert _inside(_diff_bbox(stock["home"], shot), whole), "something else moved"
    t = _press_at(s, *_centre(stock["ext"]))
    s.run(1.0)
    assert _panel_sends(s, DC_DIMMER_COMMAND_2, t) == [], "the parked region still sends"


# --- icons ---------------------------------------------------------------------------------------

def test_set_icon(mcf_147, stock, tmp_path):
    img = _build(mcf_147, [("i", "set_icon", {"target": CLIMATE_ICON, "char": "P"})])
    s = _settled(img)
    box = _icon_box(s)
    house = glyphs.decode_font(img, 24)["P"].rows
    snow = glyphs.decode_font(img, 24)["H"].rows
    assert not _draws_glyph(_crop(stock["home"], box), house)
    shot = _shot(s, tmp_path, "b")
    assert _draws_glyph(_crop(shot, box), house), "the icon does not draw the new glyph"
    assert not _draws_glyph(_crop(shot, box), snow)


def _icon_box(s: Scenario):
    [q] = [q for q in s._placed(s._root()) if q.obj_index == CLIMATE_ICON and q.wtype == 11]
    return q.x0, q.y0, q.x1, q.y1


# A 20 x 20 mark nothing in 14.7 draws: a ring with a centre dot.
MARK = ["0" * 20] + ["0" + "f" * 18 + "0"] * 2 + \
       ["0ff" + "0" * 14 + "ff0"] * 6 + ["0ff000000ff000000ff0"] * 2 + \
       ["0ff" + "0" * 14 + "ff0"] * 6 + ["0" + "f" * 18 + "0"] * 2 + ["0" * 20]
FREE_CHAR = "K"                 # font 24 draws the solid placeholder block for it on 14.7


def test_install_glyph(mcf_147, stock, tmp_path):
    cell = 48                   # font 24's cell height on 14.7 (install_glyph's bearing check)
    img = _build(mcf_147, [
        ("g", "install_glyph", {"slot": 24, "char": FREE_CHAR, "y": (cell - len(MARK)) // 2,
                                "rows": MARK, "claim_block": True}),
        ("i", "set_icon", {"target": CLIMATE_ICON, "char": FREE_CHAR})])
    s = _settled(img)
    box = _icon_box(s)
    rows = [[int(c, 16) for c in r] for r in MARK]
    assert not _draws_glyph(_crop(stock["home"], box), rows)
    assert _draws_glyph(_crop(_shot(s, tmp_path, "b"), box), rows), "the new glyph is not drawn"


def test_every_147_op_is_tested_here():
    enabled = {n for n, cls in recipe.REGISTRY.items() if "14.7" in cls.firmwares}
    assert enabled == TESTED_147
    assert {n for n in TESTED_147 if not callable(globals().get(f"test_{n}"))} == set()
    # The emulator is the only 14.7 evidence: each is emulated there and verified nowhere.
    for n in TESTED_147:
        cls = recipe.REGISTRY[n]
        assert "14.7" in cls.emulated_on and "14.7" not in cls.verified_on, n


# --- a new button -------------------------------------------------------------------------------

GLYPH_DONOR = 214               # `donors.glyph_button` on 14.7: the Bath Fan button
DONOR_GROUP = 40                # its group: two 1FEDB bindings on output 40 (commands 5 and 3)
WIRE_PIN = 3                    # a free output, bridged on 14.7 (var 20)
OFF = (65, 81, 163)
SHELL = {"page": "climate", "x": 100, "y": 380, "w": 120, "h": 60, "fill_cell": 164}


def test_unwired_glyph_button_clone_carries_the_donors_live_group(mcf_147):
    """The 14.7 donor's clone carries the donor's own command group, which drives a live
    output (Bath Fan, output 40), so `add_glyph_button` must refuse a shell no `wire_button`
    rewired (2026-11-08)."""
    donor, _proof = donors.glyph_button(mcf_147)
    assert donor == GLYPH_DONOR
    assert newbutton.object_group(mcf_147, donor) == DONOR_GROUP
    grp = guidata.group_table(mcf_147)[DONOR_GROUP]
    bt = guidata.binding_table(mcf_147)
    assert [(bt[j].channel, bt[j].raw[4]) for j in range(grp.start, grp.start + grp.count)] \
        == [(40, 5), (40, 3)]
    clone = insert.clone_object(mcf_147, donor)
    placed, _e = insert.insert_object(mcf_147, clone, CLIMATE_ROOT, position=(100, 380))
    assert newbutton.object_group(placed, newbutton.last_object_index(placed)) == DONOR_GROUP

    with pytest.raises(AssertionError, match="was never wired"):
        _build(mcf_147, [("shell", "add_glyph_button", SHELL)])


@pytest.fixture(scope="module")
def wired(mcf_147, tmp_path_factory):
    """A glyph button cloned from 14.7's donor onto CLIMATE CONTROL and wired to output 3, on
    the emulated panel: the page drawn, then with output 3 set on and off again, then what a tap
    at its centre sends and the page after the G12 echoes the toggle. Until 2026-11-08
    `bind_pin` read the committed 13.6 status-bridge dump, binding pin 3's var 20 to 13.6's
    cell 0x20039BB0, which stays 0x004151A3 on 14.7, so the button never recoloured. The bridge
    is now derived per image (`status_map.derived_bridge`: 0x20039854/0x20039D50 on 14.7,
    tests/test_groups_locate.py)."""
    tmp = tmp_path_factory.mktemp("wired147")
    img = _build(mcf_147, [
        ("shell", "add_glyph_button", SHELL),
        ("wire", "wire_button", {"target": {"ref": "shell"}, "channel": WIRE_PIN,
                                 "off_colour": list(OFF), "allow_commanded_channel": True,
                                 "allow_named_output": True})])
    s = _settled(img)
    s.goto("CLIMATE CONTROL")
    s.run(2.0)
    obj = len(guidata.object_tables(img)[1]) - 1
    assert obj == len(guidata.object_tables(mcf_147)[1])
    out = {"img": img, "off": _shot(s, tmp, "off"),
           "footprint": _footprint(img, CLIMATE_ROOT, obj),
           "caption": _footprint(img, CLIMATE_ROOT, obj, label="Bath Fan")}
    s.set_g12_output(WIRE_PIN, 200)
    s.run(2.0)
    out["on"] = _shot(s, tmp, "on")
    s.set_g12_output(WIRE_PIN, 0)
    s.run(2.0)
    out["off_again"] = _shot(s, tmp, "off_again")
    t = _press_at(s, *_centre(GLYPH_BOX))
    s.run(1.0)
    out["sent"] = [bytes(f.data) for f in _panel_sends(s, DC_DIMMER_COMMAND_2, t)]
    out["after_tap"] = _shot(s, tmp, "after_tap")
    stock = _settled(mcf_147)
    stock.goto("CLIMATE CONTROL")
    stock.run(2.0)
    out["stock"] = _shot(stock, tmp, "stock")
    return out


GLYPH_BOX = (SHELL["x"], SHELL["y"], SHELL["x"] + SHELL["w"], SHELL["y"] + SHELL["h"])


def _footprint(img: bytes, root: int, obj: int, label: str | None = None):
    """The union of `obj`'s placed records under `root` (x1/y1 inclusive): the clone's box,
    icon, caption and touch region, read from the build's own walk (only the record labelled
    `label`, if given)."""
    qs = [q for q in layout.Screen(img).walk(root)
          if q.obj_index == obj and label in (None, q.label)]
    assert qs, obj
    return (min(q.x0 for q in qs), min(q.y0 for q in qs),
            max(q.x1 for q in qs), max(q.y1 for q in qs))


def test_add_glyph_button(wired):
    """The clone draws its body in its declared box on CLIMATE CONTROL and changes no pixel
    outside its own records (the donor's caption, "Bath Fan", is carried right of the box), and
    a tap at its centre sends exactly the wired output's toggle (1FEDB pin 3, command 5),
    nothing to the donor's output 40 (2026-11-08)."""
    area = SHELL["w"] * SHELL["h"]
    assert _near(_crop(wired["off"], GLYPH_BOX), OFF) > area // 2, \
        "the clone does not draw at its new place"
    assert _near(_crop(wired["stock"], GLYPH_BOX), OFF) < area // 4
    bbox = _diff_bbox(wired["stock"], wired["off"])
    assert _inside(bbox, wired["footprint"]), (bbox, wired["footprint"])
    assert [(d[0], d[3]) for d in wired["sent"]] == [(WIRE_PIN, TOGGLE)], wired["sent"]


def test_wire_button(wired):
    """`set_g12_output` on the wired output recolours the button in place: on, the off body is
    gone from its box; off again, its box is the off box pixel for pixel. The tap's toggle,
    echoed by the emulated G12, lights it the same way. No change reaches a pixel outside the
    clone's records. Off again, the carried caption's anti-aliased edges differ (193 pixels):
    the hollow caption is drawn over itself on the repaint, and the same build on 13.6 differs
    by the same 193 pixels in the same box (2026-11-08)."""
    area = SHELL["w"] * SHELL["h"]
    assert _near(_crop(wired["on"], GLYPH_BOX), OFF) < area // 4, "the button did not recolour"
    bbox = _diff_bbox(wired["off"], wired["on"])
    assert bbox is not None and _inside(bbox, wired["footprint"]), (bbox, wired["footprint"])
    _same_but_caption(wired, "off", "off_again", max_pixels=193)
    _same_but_caption(wired, "on", "after_tap")


def _same_but_caption(wired: dict, a: str, b: str, max_pixels: int | None = None) -> None:
    """Shots `a` and `b` agree pixel for pixel in the button's box, and differ, if at all, only
    in the carried caption's record, by at most `max_pixels` pixels when given."""
    if max_pixels is not None:
        n = int((wired[a] != wired[b]).any(axis=2).sum())
        assert n <= max_pixels, (a, b, n)
    bbox = _diff_bbox(wired[a], wired[b])
    assert bbox is None or _inside(bbox, wired["caption"]), (a, b, bbox)
    assert _diff_bbox(_crop(wired[a], GLYPH_BOX), _crop(wired[b], GLYPH_BOX)) is None, (a, b)


# --- the theme ----------------------------------------------------------------------------------

# Six distinct role colours (the same six as tests/test_themeswap.py's THEME_SIX, the 13.6 test's).
THEME = {"bg": (33, 22, 17), "panel": (61, 40, 29), "button": (166, 86, 38),
         "header": (176, 58, 28), "rail": (112, 40, 22), "active": (200, 118, 22)}
RAIL_PAGES = ("HOME", "ELECTRICAL", "LIGHTS", "CLIMATE", "SETTINGS")
# Each edit `themeswap.plan` makes, by its note, and the region `coverage` claims it in.
_THEME_KINDS = (("theme inline fill", "inline", "GuiData object"),
                ("tank well", "wells", "GuiData object"),
                ("theme mode-6 palette", "palette", "image-container-header"))


def _rail_shots(s: Scenario, tmp_path, tag: str) -> dict:
    out = {}
    for page in RAIL_PAGES:
        s.goto(page)
        s.run(2.0)
        out[page] = _shot(s, tmp_path, f"{tag}-{page}")
    return out


def _exact(img: np.ndarray, rgb) -> int:
    return int((img == np.array(rgb)).all(axis=2).sum())


def test_set_theme(mcf_147, tmp_path):
    """The six chrome roles re-themed on 14.7: the edits land where `themeswap` locates them (6
    in GuiCode; in GuiData 1 inline field, 7 mode-6 palette entries, 5 tank wells), each role's
    SRAM cell holds its colour after boot, and no rail page draws a pixel of a stock role colour
    (2026-11-07)."""
    params = {"colours": {r: list(c) for r, c in THEME.items()}, "tank_wells": "panel"}
    img = _build(mcf_147, [("t", "set_theme", params)])

    # 1-2. The compiled image is `plan`'s edits, resealed; every differing run (checksums
    # aside) lies in one edit, classified by section and by the region coverage claims it in.
    p = themeswap.plan(mcf_147, THEME, "panel")
    pt = patch.McfPatcher(mcf_147)
    for e in p.edits:
        pt.set_bytes(*e)
    assert pt.build()[0] == img
    sections = container.parse_container(img).sections
    runs = buildcheck._runs(mcf_147, img, coverage.analyse(img, deep=True).spans)
    hit = {}
    for r in runs:
        if "checksum" in r.region:
            continue
        [k] = [k for k, (off, old, _n, _note) in enumerate(p.edits)
               if off <= r.offset and r.offset + r.length <= off + len(old)]
        [sec] = [x.name for x in sections if x.offset <= r.offset < x.offset + x.length]
        hit.setdefault(k, set()).add((sec.split()[-1], r.region))
    assert set(hit) == set(range(len(p.edits))), "an edit changed no byte"
    by = {}
    for k, where in hit.items():
        [(sec, region)] = where
        note = p.edits[k][3]
        kind = next((name for prefix, name, _r in _THEME_KINDS if note.startswith(prefix)), sec)
        assert kind == "GuiCode" or (sec, region) == (
            "GuiData", dict((n, rg) for _p, n, rg in _THEME_KINDS)[kind]), (note, sec, region)
        by[kind] = by.get(kind, 0) + 1
    assert by == {"GuiCode": 6, "inline": 1, "palette": 7, "wells": 5}, by
    assert (p.inline, p.palette, p.wells) == (1, 7, 5)

    # 3. Each role cell, as the build's own theme-init is located, holds its applied colour.
    ti = themeswap.locate_theme_init(img)
    assert ti.form == "rethemed"
    s = _settled(img)
    for role, cells in ti.cells.items():
        for cell in cells:
            got = int.from_bytes(s.read_mem(cell, 4), "little")
            assert got == p.applied[role], (role, hex(cell), hex(got))

    # 4. Stock draws every stock role colour on the rail pages; the build draws none of them.
    stock = _rail_shots(_settled(mcf_147), tmp_path, "stock")
    for role, v in themeswap.STOCK.items():
        assert sum(_exact(a, themeswap.int_rgb(v)) for a in stock.values()) > 0, role
    for page, a in stock.items():
        for role in ("bg", "panel", "button", "header", "rail"):
            assert _exact(a, themeswap.int_rgb(themeswap.STOCK[role])) > 0, (page, role)
    shots = _rail_shots(s, tmp_path, "build")
    left = {(page, role): n for page, a in shots.items()
            for role, v in themeswap.STOCK.items()
            if (n := _exact(a, themeswap.int_rgb(v)))}
    assert left == {}, f"stock role colours still drawn: {left}"


# --- the bus -------------------------------------------------------------------------------------

def test_append_filter(mcf_147):
    dgn, value = 0x1FEA0, 42
    img = _build(mcf_147, [("f", "append_filter", {"dgn": dgn, "mode": 1, "payload_byte": 0})])
    var = len(guidata.variable_table(img)) - 1
    assert var == len(guidata.variable_table(mcf_147))
    slot = followchannel.status_slot_addr(var)
    s = _settled(img)

    class Host(Peer):
        sa = 0x42

    host = s._m.canbus.add_peer(Host())
    before = s.read_mem(slot, 2)
    s.run(1.0)
    assert s.read_mem(slot, 2) == before, "the slot changed with no frame"
    for v in (value, 7):
        host.send(dgn, bytes([v, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]))
        s.run(1.0)
        assert s.read_mem(slot, 1)[0] == v, (hex(slot), s.read_mem(slot, 2))


def test_drop_from_master(mcf_147):
    img = _build(mcf_147, [("d", "drop_from_master", {"master": "Light Master", "channel": 22})])
    s = _settled(img)
    t = s.now
    s.tap("ON")
    s.run(1.0)
    sent = _panel_sends(s, DC_DIMMER_COMMAND_2, t)
    assert sent, "Light Master ON sent nothing"
    assert {f.data[0] for f in sent} == LIGHT_MASTER_ON - {22}


@pytest.mark.xfail(strict=True, reason=(
    "observed 2026-11-07: on 14.7 a hold-to-toggle binding never sends. After set_gesture, "
    "group 17 (Star Link) holds 3 s with binding 79's phase mask 0x04 (held) and no held "
    "repeat; a tap sends nothing (as intended) and holds of 1.5, 3.5 and 6 s send nothing "
    "either. Stock 14.7 sends Star Link's toggle on a tap, and a stock held-repeat binding "
    "(Awning EXT, group 3) sends on hold, so the held phase itself runs; a held-only mask "
    "without the repeat bit does not, with the hold threshold left at 1 s as well. Group 17 "
    "is GuiCode-driven on 14.7 (newbutton.guicode_selected_groups, 2026-11-08), and 14.7's "
    "LCD Firmware (sha256 cbed1028...) is proven a relocation twin of 13.6's group handling, "
    "its sender FUN_0021B8B4 included (grammar.prove_lcd). Inference: the same symptom as the "
    "hold on driven group 136 (test_add_rvc_button_on_driven_group_136_sends_nothing), not an "
    "LCD difference. Driven is not sufficient by itself: Awning EXT's group 3 is driven on "
    "14.7 and its stock held repeat sends, and a hold on driven 129 works on a coach "
    "(2026-10-19). Whether 13.6's emulator fires the same edit was not checked (13.6 stock "
    "reaches no single-toggle group)."))
def test_set_gesture(mcf_147):
    """Star Link (shown with the Starlink Power option on): a tap no longer toggles it, a 3 s
    hold does."""
    img = _build(mcf_147, [("h", "set_gesture",
                            {"target": STAR_LINK, "kind": "hold_to_toggle", "seconds": 3})])
    s = _settled(img, presets={STARLINK_POWER_SELECTOR: 1})

    def toggles(since):
        return [f for f in _panel_sends(s, DC_DIMMER_COMMAND_2, since)
                if f.data[0] == STAR_LINK_OUTPUT and f.data[3] == TOGGLE]

    t = s.now
    s.tap("Star Link")
    s.run(1.0)
    assert toggles(t) == [], "a tap still toggles Star Link"
    t = s.now
    s.tap("Star Link", hold_s=3.5)
    s.run(1.0)
    assert len(toggles(t)) == 1, "a 3 s hold did not toggle Star Link"


# --- live status ----------------------------------------------------------------------------------

# A stock 14.7 register no peer of the demo network publishes: var 232, DGN 0x1FFED, payload
# byte 0, open filter (mode 1). Its slot is the low array's (`register.status_slot_addr`), read
# back from the emulator below. Test expectations only.
REG = {"dgn": 0x1FFED, "index": 232}
REG_DGN = 0x1FFED
HOME_ROOT = 9
READOUT = {"page": HOME_ROOT, "x": 740, "y": 440, "format": {"width": 3}}
READOUT_BOX = (690, 425, 745, 456)  # the wtype-8's fill and digits (right edge x), on HOME's
                                    # empty lower battery-card band; checked against stock


class _Host(Peer):
    sa = 0x42


def _publish(s: Scenario, host, value: int) -> None:
    host.send(REG_DGN, bytes([value, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]))
    s.run(1.0)


def _readout_cell(img: bytes, stock: bytes) -> int:
    """The readout's display cell: the cell its new wtype-8 names, read from the build."""
    obj = len(guidata.object_tables(img)[1]) - 1
    assert obj == len(guidata.object_tables(stock)[1])
    [q] = [q for q in layout_walk(img, HOME_ROOT) if q.obj_index == obj and q.wtype == 8]
    return status_map.cell_table(img)[q.value_slot]


def layout_walk(img: bytes, root: int):
    return layout.Screen(img).walk(root)


def test_add_register_readout(mcf_147, stock, tmp_path):
    """A live number on HOME from stock register var 232: stale it reads the panel's dashes; a
    published 42, then 7, each repaint the digits in the readout's box and nowhere else, and
    the cell holds the value (2026-11-08)."""
    img = _build(mcf_147, [("r", "add_register_readout", {"register": REG, **READOUT})])
    cell = _readout_cell(img, mcf_147)
    s = _settled(img)
    host = s._m.canbus.add_peer(_Host())
    assert s.read_mem(cell, 2) == b"\xff\xff", "nothing published, yet the cell is not stale"
    _revisit_home(s)
    stale = _shot(s, tmp_path, "stale")
    assert _inside(_diff_bbox(stock["home_again"], stale), READOUT_BOX)
    shots = [stale]
    for v in (42, 7):
        _publish(s, host, v)
        assert s.read_mem(cell, 2) == v.to_bytes(2, "little"), (v, s.read_mem(cell, 2))
        shot = _shot(s, tmp_path, f"v{v}")
        assert _inside(_diff_bbox(stock["home_again"], shot), READOUT_BOX)
        for prev in shots:
            assert not np.array_equal(_crop(shot, READOUT_BOX), _crop(prev, READOUT_BOX)), \
                f"{v}: the digits did not change"
        shots.append(shot)


def test_add_register_readout_draw_false(mcf_147, stock, tmp_path):
    """draw false: a fresh cell (the first grown index) follows the register, and nothing is
    drawn."""
    img = _build(mcf_147, [("r", "add_register_readout", {"register": REG, "draw": False})])
    idx = celltables.locate(mcf_147).rows
    cell = status_map.cell_table(img)[idx]
    s = _settled(img)
    host = s._m.canbus.add_peer(_Host())
    assert s.read_mem(cell, 2) == b"\xff\xff"
    assert _diff_bbox(stock["home"], _shot(s, tmp_path, "b")) is None
    for v in (42, 7):
        _publish(s, host, v)
        assert s.read_mem(cell, 2) == v.to_bytes(2, "little")
    _revisit_home(s)
    assert _diff_bbox(stock["home_again"], _shot(s, tmp_path, "c")) is None


POISON = 0x3039                     # 12345: what the cell might hold after a reset
DIGITS = (700, 426, 742, 452)       # the readout's digit band (x1/y1 exclusive), inside its box
STEP_S = 0.01


def _boot_sampled(img: bytes, cell: int, poison: int | None) -> list:
    """Boot `img` (the cell set to `poison` before reset, unless None) and settle, sampling
    every `STEP_S` of simulated time: `[(t, cell value, screen id, digit band or None)]`."""
    s = Scenario(img, timeout_s=600)
    if poison is not None:
        s._m.mem(cell, 2)[:] = poison.to_bytes(2, "little")
    real = s.run
    samples = []

    def stepped(seconds):
        end = s.now + seconds
        while s.now < end - 1e-4:
            before = s.now
            real(min(STEP_S, end - s.now))
            if s.now == before:
                break
            frame = s._m.periph["LTDC"].render()
            band = None if frame is None else frame[DIGITS[1]:DIGITS[3], DIGITS[0]:DIGITS[2]]
            samples.append((round(s.now, 4), int.from_bytes(s.read_mem(cell, 2), "little"),
                            s._screen_id(), None if band is None else band.copy()))

    s.run = stepped
    s.boot()
    s.run(SETTLE_S - s.now)
    return samples


def test_add_register_readout_first_frame(mcf_147):
    """Before its first write the readout's cell is never drawn (2026-11-08). The image boots
    twice, its cell poisoned with 12345 before reset and not (the emulator's SRAM starts 0),
    each sampled every 10 ms of simulated time from reset to `SETTLE_S`. Poisoned, the cell
    holds 12345 until the copy routine's first write, the stale 0xFFFF (0.76 s, under the
    splash); HOME first draws the readout at ~3.4 s, as dashes. Every sampled frame of the
    readout's digit band is the same pixels in both boots: what the cell held before its first
    write is never drawn."""
    img = _build(mcf_147, [("r", "add_register_readout", {"register": REG, **READOUT})])
    cell = _readout_cell(img, mcf_147)
    poisoned = _boot_sampled(img, cell, POISON)
    clean = _boot_sampled(img, cell, None)
    values = [v for _t, v, _s, _b in poisoned]
    first = values.index(0xFFFF)
    assert set(values[:first]) == {POISON} and set(values[first:]) == {0xFFFF}, \
        sorted(set(values))
    assert [t for t, *_r in poisoned] == [t for t, *_r in clean]
    drew = [t for t, _v, sc, band in poisoned
            if band is not None and sc == 0 and (band >= 200).all(axis=2).any()]
    assert drew, "HOME never drew the readout during boot: the check would be vacuous"
    assert poisoned[first][0] < drew[0], (poisoned[first][0], drew[0])
    differ = [t for (t, _v, _s, a), (_t, _w, _c, b) in zip(poisoned, clean)
              if (a is None) != (b is None) or (a is not None and not np.array_equal(a, b))]
    assert differ == [], f"the poison is drawn at {differ[:5]}"


CHASSIS_VOLTS_SLOT = 176            # HOME's Chassis readout (object 374) value_slot on 14.7


def test_add_register_readout_existing_cell_compiles(mcf_147):
    """cell_slot gates on the per-tick decoder's range, located on 14.7 since 2026-11-08
    (`decodeguards.decoder_range`, GUICODE Finding 91): the op now compiles there. Static only;
    the emulator proof that the cell repaints is the next step of the 14.x decoder-range plan."""
    project = recipe.Project(
        base_sha256=hashlib.sha256(mcf_147).hexdigest(), build_name=None,
        ops=[recipe.OpSpec(id="r", type="add_register_readout",
                           params={"register": REG, "cell_slot": CHASSIS_VOLTS_SLOT})])
    res = recipe.compile_project(project, mcf_147)
    assert res.ok, res.errors


ENUM = {"parent": HOME_ROOT, "x": 560, "y": 420, "w": 180, "h": 30, "font": 16,
        "background": [39, 40, 98], "source": {"var": 232},
        "values": {"0": "Off", "1": "Cool", "2": "Heat"}}


def test_add_enum_label(mcf_147, stock, tmp_path):
    """Each published value draws its text in the box, a value not listed (and stale) draws
    the default, and nothing outside the box changes (2026-11-08). The background is the card's
    navy: without one, the switch's arms draw over each other on 14.7 and on 13.6 alike in the
    emulator (2026-11-08)."""
    img = _build(mcf_147, [("e", "add_enum_label", ENUM)])
    x0, y0 = ENUM["x"], ENUM["y"]
    box = (x0, y0, x0 + ENUM["w"], y0 + ENUM["h"])
    s = _settled(img)
    host = s._m.canbus.add_peer(_Host())

    def draws(shot, text):
        crop = shot[y0:box[3] + 1, x0:box[2] + 1]
        cov = _text_coverage(img, ENUM["font"], text)
        return _draws_glyph(crop, cov, visible=1.0) and _exact(crop, WHITE) == int(
            (cov == 15).sum())

    _revisit_home(s)
    shot = _shot(s, tmp_path, "stale")
    assert draws(shot, "--"), "stale does not draw the default"
    assert _inside(_diff_bbox(stock["home_again"], shot), box)
    for v, text in ((0, "Off"), (1, "Cool"), (2, "Heat"), (9, "--"), (1, "Cool")):
        _publish(s, host, v)
        shot = _shot(s, tmp_path, f"v{v}")
        assert draws(shot, text), (v, text)
        assert _inside(_diff_bbox(stock["home_again"], shot), box), v


WATCH_TARGET = 3                    # a G12 output nothing on the emulated panel sends to
GATE = {"dgn": 0x1FFEE, "index": 240, "mask": 0xFF, "value": 1}   # stock var 240, byte 0


def test_follow_register(mcf_147):
    """Watch var 232 bit 0, gated on var 240 == 1: the first report is the silent baseline, each
    later edge sends output 3 on (level 200, command 1) or off (command 3) while the gate holds,
    and nothing while it does not (2026-11-08)."""
    img = _build(mcf_147, [("f", "follow_register", {
        "watch": {**REG, "mask": 1, "value": 1}, "target_channel": WATCH_TARGET,
        "gates": [GATE]})])
    s = _settled(img)
    host = s._m.canbus.add_peer(_Host())

    def sends(since):
        return [bytes(f.data) for f in _panel_sends(s, DC_DIMMER_COMMAND_2, since)
                if f.data[0] == WATCH_TARGET]

    assert sends(0.0) == [], "the follower sent at boot"
    on = bytes([WATCH_TARGET, 0xFF, 200, 1, 0xFF, 0x00, 0xFF, 0xFF])
    off = bytes([WATCH_TARGET, 0xFF, 0, 3, 0xFF, 0x00, 0xFF, 0xFF])

    def gate(v):
        host.send(0x1FFEE, bytes([v, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]))
        s.run(0.5)

    gate(1)
    for v, want in ((1, []), (0, [off]), (1, [on])):
        t = s.now
        _publish(s, host, v)
        assert sends(t) == want, (v, sends(t))
    gate(0)
    for v in (0, 1):
        t = s.now
        _publish(s, host, v)
        assert sends(t) == [], ("the closed gate let an edge through", v)


def _build_refused_as(stock: bytes, specs, code: str) -> bytes:
    """`_build`, for a strict xfail: the refusal must be `code` (else RuntimeError, which the
    xfail does not catch, so a different failure fails the test)."""
    project = recipe.Project(base_sha256=hashlib.sha256(stock).hexdigest(), build_name=None,
                             ops=[recipe.OpSpec(id=i, type=t, params=p) for i, t, p in specs])
    res = recipe.compile_project(project, stock)
    if not res.ok and [e["code"] for e in res.errors] != [code]:
        raise RuntimeError("refused otherwise than %s: %s" % (code, res.errors))
    assert res.ok, res.errors
    return res.data


def _admit(monkeypatch, *names):
    for n in names:
        monkeypatch.setattr(recipe.REGISTRY[n], "firmwares", frozenset({"13.6", "14.7"}))


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=(
    "follow_channel carries 13.6 pins: GATE_OBJECT = 46 is 13.6's OPTIONS page, whose 'Heat "
    "Pump' row it renames and whose option register it gates on. Observed 2026-11-08 with the "
    "op admitted on 14.7: NO_SUCH_LABEL, object 46 has no label field containing 'Heat Pump'. "
    "It stays off 14.7; follow_register covers the follower without the row."))
def test_follow_channel(mcf_147, monkeypatch):
    _admit(monkeypatch, "follow_channel")
    _build_refused_as(mcf_147, [("f", "follow_channel",
                                 {"watch_channel": 3, "target_channel": 4})], "NO_SUCH_LABEL")


# --- add_rvc_button and its status ops -----------------------------------------------------------

# A new button on CLIMATE CONTROL's empty band (the glyph button's box), sending 1FEDB pin 3
# frames; test expectations only. 14.7's first hold-safe slot is group 12, which GuiCode does not
# drive; 136 is a group it drives (`newbutton.guicode_selected_groups` on 14.7, 2026-11-08).
RVC_BOX = GLYPH_BOX
RVC_AT = {"parent": CLIMATE_ROOT, "x": SHELL["x"], "y": SHELL["y"], "w": SHELL["w"],
          "h": SHELL["h"], "text": "Go", "dgn": DC_DIMMER_COMMAND_2}
PIN3_TOGGLE = "03ffc805ff00ffff"
PIN3_ON = "03ffc801ff00ffff"
PIN3_OFF = "03ff0003ff00ffff"
HOLD_S = 3
FIRST_HOLD_SAFE = 12
DRIVEN_GROUP = 136
HOLD_BUTTON = ("b", "add_rvc_button",
               {**RVC_AT, "hold": {"payload": PIN3_TOGGLE, "seconds": HOLD_S}})


def _new_groups(stock: bytes, img: bytes) -> list[int]:
    """The groups the build changed, read from the two group tables."""
    sg, dg = guidata.group_table(stock), guidata.group_table(img)
    return [g.index for g in sg if g.index < len(dg) and dg[g.index].raw != g.raw]


def _rvc_sends(s: Scenario, since: float) -> list:
    return [(round(f.t - since, 3), bytes(f.data).hex())
            for f in _panel_sends(s, DC_DIMMER_COMMAND_2, since)]


def test_add_rvc_button(mcf_147):
    """The hold form on 14.7's first hold-safe group: a 4 s hold sends exactly one frame, the
    binding's DGN and payload, once the 3 s threshold has passed; a 0.2 s tap sends nothing
    (2026-11-08)."""
    assert rvcbutton.allocate(mcf_147, pool="hold_safe") == [FIRST_HOLD_SAFE]
    assert FIRST_HOLD_SAFE not in newbutton.guicode_selected_groups(mcf_147)
    img = _build(mcf_147, [HOLD_BUTTON])
    assert _new_groups(mcf_147, img) == [FIRST_HOLD_SAFE]
    s = _settled(img)
    s.goto("CLIMATE CONTROL")
    s.run(2.0)
    assert _inside(s.control_box("Go"), RVC_BOX)
    for _ in range(2):
        t = s.now
        s.tap("Go", hold_s=0.2)
        s.run(1.0)
        assert _rvc_sends(s, t) == [], "a short tap sent the hold's frame"
        t = s.now
        s.tap("Go", hold_s=4.0)
        s.run(1.0)
        sent = _rvc_sends(s, t)
        assert [p for _dt, p in sent] == [PIN3_TOGGLE], sent
        assert HOLD_S <= sent[0][0] < 4.0, ("not sent after the hold threshold", sent)


def test_add_rvc_button_tap(mcf_147):
    """The tap form sends its payload once per press, short or long (2026-11-08)."""
    img = _build(mcf_147, [("b", "add_rvc_button", {**RVC_AT, "tap": {"payload": PIN3_TOGGLE}})])
    [grp] = _new_groups(mcf_147, img)
    assert grp not in newbutton.guicode_selected_groups(mcf_147)
    s = _settled(img)
    s.goto("CLIMATE CONTROL")
    s.run(2.0)
    for hold in (0.0, 0.2, 2.0):
        t = s.now
        s.tap("Go", hold_s=hold)
        s.run(1.0)
        assert [p for _dt, p in _rvc_sends(s, t)] == [PIN3_TOGGLE], hold


def test_add_rvc_button_never_on_a_driven_group(mcf_147, monkeypatch):
    """Negative control: neither pool offers a group GuiCode drives on 14.7, and the hold
    button forced onto one (136, by a pool patched to offer it) is refused by the finish gate
    (`recipe._assert_no_guicode_selected_group_reused`), not built (2026-11-08)."""
    driven = newbutton.guicode_selected_groups(mcf_147)
    assert DRIVEN_GROUP in driven and DRIVEN_GROUP not in newbutton.reserved_groups(mcf_147)
    for pool in rvcbutton.POOLS:
        offered = rvcbutton.allocate(mcf_147, pool=pool, need=1)
        assert not set(offered) & driven, pool
    assert not set(newbutton.hold_safe_groups(mcf_147)) & driven
    assert not set(newbutton.assignable_groups(mcf_147)) & driven
    monkeypatch.setattr(newbutton, "hold_safe_groups", lambda _data: [DRIVEN_GROUP])
    project = recipe.Project(base_sha256=hashlib.sha256(mcf_147).hexdigest(), build_name=None,
                             ops=[recipe.OpSpec(id="b", type=HOLD_BUTTON[1],
                                                params=HOLD_BUTTON[2])])
    with pytest.raises(recipe.GateError, match=r"group\(s\) \[%d\] were reused" % DRIVEN_GROUP) \
            as ei:
        recipe.compile_project(project, mcf_147)
    assert ei.value.code == "STRUCTURAL_GATE"


def test_add_rvc_button_on_driven_group_136_sends_nothing(mcf_147, monkeypatch):
    """A hold button on 14.7's driven group 136, with the gate stubbed out, builds, and neither
    a short tap nor a 4 or 6 s hold sends anything in the emulator (2026-11-08), as 13.6's holds
    on its driven 136/137 did on the coach (2026-09-30). This shows group 136, not every driven
    group: a hold on driven group 129 works on the coach (2026-10-19)."""
    monkeypatch.setattr(newbutton, "hold_safe_groups", lambda _data: [DRIVEN_GROUP])
    monkeypatch.setattr(recipe, "_assert_no_guicode_selected_group_reused", lambda *_a: None)
    img = _build(mcf_147, [HOLD_BUTTON])
    assert _new_groups(mcf_147, img) == [DRIVEN_GROUP]
    s = _settled(img)
    s.goto("CLIMATE CONTROL")
    s.run(2.0)
    for hold in (0.2, 4.0, 6.0):
        t = s.now
        s.tap("Go", hold_s=hold)
        s.run(1.0)
        assert _rvc_sends(s, t) == [], hold


def _status_run(img: bytes, stock: bytes, tmp_path) -> dict:
    """Boot `img`, open CLIMATE CONTROL, and walk var 232 through stale, 1, 0, 1: at each step
    the switch's cell, the button group's LCD runtime record (`lcdgroups.host_writer`, 16
    bytes per group, variant counters at +0x0A/+0x0B), a screenshot, and what a tap sends."""
    [grp] = _new_groups(stock, img)
    assert grp == FIRST_HOLD_SAFE
    gs, tab = guidata.guidata_extent(img)[0], guidata.object_tables(img)[1]
    first = len(guidata.object_tables(stock)[1])
    [sw] = [r for o in range(first, len(tab)) for r in records.object_records(img, gs + tab[o])
            if r.wtype == 6]
    cell = status_map.cell_table(img)[sw.value_slot]
    rec = lcdgroups.host_writer(img).records + 16 * grp
    s = _settled(img)
    host = s._m.canbus.add_peer(_Host())
    s.goto("CLIMATE CONTROL")
    s.run(2.0)
    steps = []
    for v in (None, 1, 0, 1):
        if v is not None:
            _publish(s, host, v)
        r = s.read_mem(rec, 16)
        step = {"v": v, "cell": int.from_bytes(s.read_mem(cell, 2), "little"),
                "variant": (r[0x0A], r[0x0B]),
                "shot": _shot(s, tmp_path, "v%s" % v)}
        t = s.now
        s.tap("Go")
        s.run(1.0)
        step["sent"] = [p for _dt, p in _rvc_sends(s, t)]
        steps.append(step)
    stock_s = _settled(stock)
    stock_s.goto("CLIMATE CONTROL")
    stock_s.run(2.0)
    return {"steps": steps, "stock": _shot(stock_s, tmp_path, "stock")}


def _assert_status_steps(run: dict, cells: dict) -> None:
    """Var 232 = 1 lights the button (`cells[1]`, variant 1, the on colour in its box) and a
    tap sends "off"; 0 and stale leave it off (variant 0) and a tap sends "on"; every pixel the
    button changes, against stock or between states, lies in its box."""
    steps = run["steps"]
    area = SHELL["w"] * SHELL["h"]
    on_rgb = tuple(aescompact.BATT_HEAT_ON_RGB)
    for st in steps:
        lit = st["v"] == 1
        assert st["cell"] == cells[st["v"]], (st["v"], st["cell"])
        assert st["variant"] == (int(lit), int(lit)), (st["v"], st["variant"])
        assert st["sent"] == [PIN3_OFF if lit else PIN3_ON], (st["v"], st["sent"])
        body = _near(_crop(st["shot"], RVC_BOX), on_rgb)
        assert (body > area // 2) if lit else (body == 0), (st["v"], body)
        assert _inside(_diff_bbox(run["stock"], st["shot"]), RVC_BOX), st["v"]
    for a, b in zip(steps, steps[1:]):
        bbox = _diff_bbox(a["shot"], b["shot"])
        assert bbox is None or _inside(bbox, RVC_BOX), (a["v"], b["v"], bbox)
    assert _diff_bbox(steps[0]["shot"], steps[1]["shot"]) is not None, "lit drew nothing"


def test_add_button_status(mcf_147, tmp_path):
    """A toggle driven by var 232 (eq 1): the routine writes the switch's cell 1 or 0 and calls
    setVariant on the toggle's group, so its LCD record holds variant = lit, the button draws
    lit or not in its box only, and a tap sends the opposite of what is reported (2026-11-08)."""
    img = _build(mcf_147, [
        ("b", "add_rvc_button", {**RVC_AT, "toggle": {"on_payload": PIN3_ON,
                                                      "off_payload": PIN3_OFF}}),
        ("s", "add_button_status", {"buttons": [
            {"button": {"ref": "b"}, "source": {"var": REG["index"]}, "compare": {"eq": 1}}]})])
    _assert_status_steps(_status_run(img, mcf_147, tmp_path), {None: 0, 1: 1, 0: 0})


def test_add_button_states(mcf_147, tmp_path):
    """A two-state cycle driven by var 232: state 0 "on" (eq 1, lit) sends payload 0 "on",
    state 1 "off" (eq 0) payload 1 "off". The routine writes the matched state (2 = unknown
    while stale) and calls setVariant with the next state, so with this order the variant is
    the lit flag, as for the toggle: the button draws the reported state in its box only and a
    tap sends the opposite command (2026-11-08)."""
    img = _build(mcf_147, [
        ("b", "add_rvc_button", {**RVC_AT, "cycle": {"payloads": [PIN3_ON, PIN3_OFF]}}),
        ("s", "add_button_states", {"buttons": [{"button": {"ref": "b"}, "states": [
            {"match": {"source": {"var": REG["index"]}, "eq": 1}, "lit": True},
            {"match": {"source": {"var": REG["index"]}, "eq": 0}}]}]})])
    _assert_status_steps(_status_run(img, mcf_147, tmp_path), {None: 2, 1: 0, 0: 1})
