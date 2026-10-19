"""levelpage -- the LEVEL page body (screen 29, object 367): preview and live layouts.

Every coordinate comes from the approved mockup (its design note (not published), drawn by the session script `mock2.py`), whose van and tile geometry is
`X(v) = round(cx + (v - 260) * sc)`, `Y(v) = round(top + (v - 40) * sc)` with `cx = 300`,
`top = 84`, `sc = 0.70` (Python's round, i.e. half to even). `LAYOUT` is the one source of those
numbers; `tests/test_level_page.py` recomputes the tiles from the mockup math so they cannot drift.

Boxes follow `pagedraw`: `(x, y, w, h)` is the inclusive box `(x, y)-(x+w, y+h)`, the same reading
as PIL's `rectangle([x0, y0, x1, y1])` with `w = x1 - x0`. A text box is exactly one font cell tall
(`h = cell - 1`), so its top is the mockup's text y; a right-aligned box ends at the mockup's
anchor (`x + w = anchor`: the renderer and panel right-justify to `x1 - width`), and a centred box
is centred on the mockup's anchor.

`build_body(data, cells=None, ...)` (preview) draws the mockup's top, "example" state inline --
static text for every number and word, no switch and no readout. With `cells` it draws the live
page: the static frame, four readouts (pitch, roll, needed, carried) and eight type-6 switches whose
arms are appended as ONE band group (`switchbuild.append_arms`, so the match table grows once).
Within each switch's band the `0xFFFF` default arm is FIRST (`layout.Screen._switch_child` falls
back only to the band's base entry, and only when its own match value is `0xFFFF`), and every arm
paints every pixel of its area: its full capsule or navy fill first (spec section 5.2).

"blocks" is always plural: a live switch on `layers == 1` is not worth a fifth arm.

Orientation (`ORIENTATIONS`, owner 2026-09-26, spec `2026-09-26-level-controls-design.md` §1.3):
`"rear"` (the default from v35 on) draws the page as seen by someone facing the van's rear -- the
van rotated 180 degrees in place (`_rot`: about the body's own centre, `x -> 2*cx - x`,
`y -> body_top + body_bot - y`), "FRONT" below it, and each wheel's tile at the position nearest
its rotated tyre (`tile_slot`: FL bottom-right, FR bottom-left, RL top-right, RR top-left). Text
stays upright; the attitude words and the sensor contract do not change. `"front"` is the original
drawing (v34a/v34b), which v34b's project pins so it still rebuilds to its committed image. Every
tile placement, preview and live, goes through `tile_slot`, so the two cannot disagree.
"""
from __future__ import annotations

from . import metrics, pagedraw as pd, switchbuild

NAVY, BODY, GREY, WHITE = (0x15, 0x18, 0x3A), (0x26, 0x2C, 0x60), (0x80, 0x82, 0x85), (0xFF, 0xFF, 0xFF)
GREEN, AMBER, RED, DIV = (0x2B, 0xB6, 0x73), (0xE6, 0xA0, 0x28), (0xBE, 0x1E, 0x2D), (0x3C, 0x40, 0x6E)
# The "needs blocks" accent (wheel tiles and SHORT ON BLOCKS). AMBER is what v34a flashed with (it
# was this module's only default at the time); on the panel it reads as a pale yellow and white text
# on it washes out (owner photo, 2026-09-26). ORANGE is the static palette's own orange (mode-6 index
# 7, #F15A29) and is the default from v34b on. `builds/mcf-13.6-level-preview.ffproj.json` (v34a's
# project) pins neither `accent` nor `orientation`, so it reproduces the flashed v34a image only at
# commit `3f24759`, before this default and the SHORT text's y and colour changed.
ORANGE = (0xF1, 0x5A, 0x29)
ACCENT = ORANGE

# The controls (spec 2026-09-26-level-controls-design.md section 2): the stock AES stepper's look,
# measured from its render (root 16): dark-red buttons, a navy-blue centre panel, white bars.
MAROON, CENTRE = (0x7C, 0x15, 0x1D), (0x27, 0x28, 0x62)

# The mockup's van transform (mock2.py): cx, top, sc.
_CX, _TOP, _SC = 300, 84, 0.70


def _X(v: float) -> int:
    return round(_CX + (v - 260) * _SC)


def _Y(v: float) -> int:
    return round(_TOP + (v - 40) * _SC)


_TW, _TH = 130, 74                       # tile box (mock2 `bw, bh`)

# ---- orientation ----------------------------------------------------------------------------
ORIENTATIONS = ("front", "rear", "rear-low")
# "rear-low" is v35's rear-facing layout exactly (the van group sitting low, FRONT 7 px off the
# page bottom). "rear" (default) is the same drawing with the whole left group -- van, tiles, Zero
# Level and FRONT -- raised 20 px so the space above the van and below FRONT are even (26/27 px;
# owner-approved mockup, 2026-09-27). The right-hand column never moves.
_ROTATED = frozenset({"rear", "rear-low"})
SHIFT_Y = {"front": 0, "rear": -20, "rear-low": 0}
# Rear-facing: wheel -> the tile position (a key of LAYOUT["tiles"]) nearest its rotated tyre.
_REAR = {"fl": "rr", "fr": "rl", "rl": "fr", "rr": "fl"}
# The van body's vertical extent (mock2 `rounded_rectangle([.., Y(40), .., Y(560)])`); the
# rotation's centre is (cx, (top + bottom) / 2), the body's own centre (X(175) + X(345) == 2*cx).
_BODY_TOP, _BODY_BOT = _Y(40), _Y(560)


def _check_orientation(orientation: str) -> None:
    if orientation not in ORIENTATIONS:
        raise LevelPageError(f"orientation {orientation!r} is not one of {ORIENTATIONS}")


def tile_slot(key: str, orientation: str) -> str:
    """The `LAYOUT["tiles"]` position ("fl", "fr", "rl" or "rr") where wheel `key`'s tile is
    drawn: its own for `"front"`, the diagonal opposite for `"rear"`."""
    _check_orientation(orientation)
    if key not in _REAR:
        raise LevelPageError(f"no wheel {key!r}")
    return _REAR[key] if orientation in _ROTATED else key


def _rot(x: int, y: int, orientation: str) -> tuple[int, int]:
    """One point of the van drawing, rotated 180 degrees about the body's centre for "rear"."""
    if orientation in _ROTATED:
        return 2 * _CX - x, _BODY_TOP + _BODY_BOT - y + SHIFT_Y[orientation]
    return x, y


def _rot_box(x: int, y: int, w: int, h: int, orientation: str) -> tuple[int, int, int, int]:
    """An inclusive box `(x, y)-(x+w, y+h)` through `_rot`, by its opposite corners, normalised."""
    ax, ay = _rot(x, y, orientation)
    bx, by = _rot(x + w, y + h, orientation)
    return min(ax, bx), min(ay, by), abs(bx - ax), abs(by - ay)

LAYOUT = {
    # The page area below the header and right of the nav rail (mock2 `rectangle([62,38,799,479])`).
    "page": (62, 38, 737, 441),
    "van": {
        # body: rounded_rectangle([X(175), Y(40), X(345), Y(560)], radius=16)
        "body": (_X(175), _Y(40), _X(345) - _X(175), _Y(560) - _Y(40), 16),
        # tyres: rounded_rectangle([X(wx), Y(wy), X(wx+28), Y(wy+70)])
        "tyres": [(_X(wx), _Y(wy), _X(wx + 28) - _X(wx), _Y(wy + 70) - _Y(wy))
                  for wx, wy in ((161, 95), (331, 95), (161, 430), (331, 430))],
        # windshield: line([X(182), Y(128), X(338), Y(128)], width=3) -> rows Y-1..Y+1
        "windshield": (_X(182), _Y(128) - 1, _X(338) - _X(182), 2),
        # cab line: 5 px dashes every 10 px from X(175)+4 to X(345)-4 at Y(205), width 2
        "cab_y": _Y(205) - 1,
        "cab_x": (_X(175) + 4, _X(345) - 4),
        # mirrors: rectangle outlines [X(160), Y(178), X(175), Y(188)] and [X(345) .. X(360)]
        "mirrors": [(_X(160), _Y(178), _X(175) - _X(160), _Y(188) - _Y(178)),
                    (_X(345), _Y(178), _X(360) - _X(345), _Y(188) - _Y(178))],
        # "FRONT", font 15, centred on cx at y 56
        "front": (_CX - 60, 56, 120, 25),
    },
    # tile top-left: left tiles end 22 px left of X(161); right tiles start 22 px right of X(359);
    # the tile is centred on Y(130) (fronts) / Y(465) (rears): top = Y - bh // 2.
    "tiles": {"fl": (_X(161) - 22 - _TW, _Y(130) - _TH // 2),
              "fr": (_X(359) + 22, _Y(130) - _TH // 2),
              "rl": (_X(161) - 22 - _TW, _Y(465) - _TH // 2),
              "rr": (_X(359) + 22, _Y(465) - _TH // 2),
              "w": _TW, "h": _TH, "r": 6},
    # group boxes: (x, y, w, h, radius); title = text top-left; notch = the navy fill behind it
    "attitude": {"box": (540, 56, 245, 244, 8), "title": (554, 48), "notch": (548, 50, 76, 12)},
    "blocks": {"box": (540, 318, 245, 102, 8), "title": (554, 310), "notch": (548, 312, 62, 12)},
    # row captions (font 15 GREY, text top-left) and values (right edge 765 = rx + 225)
    "captions": {"PITCH": (560, 80), "ROLL": (560, 160), "Needed": (560, 336),
                 "Carried": (560, 374)},
    "value_right": 765,
    "value_y": {"pitch": 76, "roll": 156, "total": 332, "carried": 370},
    "word_y": {"pitch_word": 108, "roll_word": 188},
    "word_box_w": 180,                   # fits "passenger side high" (173 px, font 13)
    "divider": (556, 140, 213),          # line([rx+16, 140, 769, 140])
    "banner": (556, 228, 213, 52, 6),
    "banner_text_y": 241,
    "short": (540, 432, 245, 38, 6),
    "short_text_y": 436,       # caps ink (rows 5..23 of font 16) centred in the 38 px strip
}

# The three touch rectangles `(x, y, w, h)`, inclusive like every pagedraw box: the Blocks-carried
# stepper's - and + (46x46, flanking the centre panel, inside the Blocks box) and Zero Level in the
# left gap between the two left-hand tiles. The hit test returns the lowest matching id, so they
# must not overlap (>= 4 px apart; pinned by the tests).
CONTROLS = {"minus": (552, 368, 46, 46), "plus": (727, 368, 46, 46), "zero": (79, 230, 130, 64)}
CONTROL_NAMES = ("minus", "plus", "zero")          # the touch records' order, appended last


def controls_rects(orientation: str) -> dict:
    """`CONTROLS` as drawn for `orientation`: Zero Level moves with the van group (`SHIFT_Y`);
    the stepper, in the right-hand column, never moves."""
    zx, zy, zw, zh = CONTROLS["zero"]
    return dict(CONTROLS, zero=(zx, zy + SHIFT_Y[orientation], zw, zh))
ZERO_RADIUS = 6
SETUP_GEAR = (24, "L")                   # the nav rail's settings gear, 37x37 in the icon font
SETUP_GEAR_BOX, SETUP_GEAR_PAD = 40, 2   # the gear's box width, and its gap to the button's edge
# Inside the stepper: each button's bar is 18x4 px (stock AES: 336..353 x 97..100), the + adds a
# 4x18 px upright; fills are inclusive, so an 18 px bar is `w = 17`.
BAR_LONG, BAR_THICK = 18, 4
# "Carried" (font 13) and the carried value's cell tops, from the panel's top. The value's cell is
# 31 px (font 17) and must end inside the 47-row panel, so it starts at +16. Its background is
# opaque (the redraw erase); INFERENCE, not traced in `FUN_002557C0`: the erase may cover the full
# cell height, so the caption's lowest ink row (its baseline, cell top + font[0x0b] = 16) is kept
# above that cell. The caption therefore starts 1 px above the panel (its box is transparent;
# `_controls` refuses an overlap).
STEPPER_CAPTION_DY = -1
STEPPER_VALUE_DY = 16
ZERO_TEXT_Y = (9, 35)                    # "Zero Level" (font 15) and "hold 3 s" (font 12) tops

# Fonts (the brief's; every text measured against metrics before it is drawn).
F_TITLE, F_CAPTION, F_VALUE, F_WORD, F_BANNER, F_SHORT = 12, 15, 17, 13, 17, 16
F_STEPPER_CAPTION, F_ZERO, F_ZERO_HINT = 13, 15, 12
F_TILE_LEVEL, F_TILE_LAYERS, F_TILE_BLOCKS, F_TILE_LIFT, F_TILE_NA = 16, 17, 13, 15, 17

# Tile interior, relative to the tile's (x0, yc) = (left, vertical centre); from mock2.py.
TILE = {
    "bar": (-14, -18, 28, 4),            # rectangle([mx-14, y-18, mx+14, y-14]), mx-relative
    "level_y": -4,                       # "LEVEL" top, centred on mx
    # The arrow glyph's box (x0+8..x0+27, yc-26..yc-6), one font-13 cell tall. The mockup's
    # triangle sat at x0+16..x0+38, where a two-digit layers value (right edge x0+63; the widest,
    # "44", is 32 px in font 17 and starts at x0+32) would draw over it; moved left so every value
    # 10..99 clears the arrow by >= 2 px (`ARROW_CLEARANCE`, pinned by the tests).
    "arrow": (8, -26, 19, 20),
    "layers_right": 63,                  # layers value right edge (one digit at x0+48)
    "layers_y": -30,
    # "blocks" text top-left. At x0+66 the widest two-digit layers value (last column x0+63)
    # left a 2 px gap and read as "44blocks" (owner's render review); moved right so the gap is
    # >= BLOCKS_GAP. Font 13's "blocks" is 56 px, so it ends at x0+124, inside the 130 px tile.
    "blocks": (68, -24),
    "lift_y": 8,                         # "2.8 in" top, centred on mx
}

# The preview's example: pitch -1.0 (front low), roll +0.4 (driver side high), 5 blocks carried,
# as mock2.py's solve() computes it.
EXAMPLE = {
    "pitch": "1.0°", "roll": "0.4°", "pitch_word": "nose down", "roll_word": "driver side high",
    "total": "8", "carried": "5", "banner": 0, "short": 1,
    "tiles": {"fl": (3, "2.8"), "fr": (4, "3.3"), "rl": None, "rr": (1, "0.5")},
}

BANNER_ARMS = ((0xFFFF, GREY, "NO SENSOR"), (0, RED, "NOT LEVEL"), (1, GREEN, "LEVEL"),
               (2, RED, "SENSOR FAULT"))
WORDS = {"pitch_word": ("nose up", "nose down"),
         "roll_word": ("driver side high", "passenger side high")}


# The up arrow: a 20x14 triangle installed by the recipe's `install_glyph` op (font slot 13, a
# character whose stub falls back to the placeholder on 13.6 and v33; slot 23, the brief's first
# choice, has no free character). Font 13's cell is 21 px, so the bearing is (21 - 14) // 2 = 3.
ARROW = (13, "\x8f")
ARROW_W, ARROW_H = 20, 14
ARROW_CELL = 21
# The wheel tiles' readout field widths, in characters. The panel fills a readout's background
# across its MEASURED text, and the measuring class varies at runtime (v37 on the coach: the
# two-character layers field " 7" filled from x0+14 -- a fixed 25 px per character, font 17's
# `font[6]` -- and wiped the arrow). `readoutfill` bounds every class (`metrics.worst_fill_width`):
#   * layers, font 17: 1 character fills at most 25 px, from x0+39, clear of the arrow
#     (x0+8..27); 2 characters reach x0+14;
#   * lift, font 15: 3 characters fill at most 66 px, from x0+1, inside the tile; 4 reach x0-21.
# A value wider than its field is drawn as that many dashes (`render.format_cell_value`, the
# width-dash loop at 0x002529A4), so `fill_safe` shows 10+ layers as "-" and 10.0+ in as "---".
# v34b-v37 were built with (2, 4); `add_level_page` keeps that as the default so their projects
# rebuild byte-identically.
FIELDS_V37 = {"layers": 2, "lift": 4}
FIELDS_FILL_SAFE = {"layers": 1, "lift": 3}
ARROW_CLEARANCE = 2                     # px between the arrow's box and any two-digit layers value
BLOCKS_GAP = 4                          # px between any two-digit layers value and "blocks"


def arrow_rows() -> list[str]:
    """The arrow's pixels as `install_glyph` rows: row i has `2*i+1` pixels scaled across the
    width (so the last row is the full 20), centred, `f` inside and `0` outside."""
    rows = []
    for i in range(ARROW_H):
        n = max(1, round((2 * i + 1) * ARROW_W / (2 * ARROW_H - 1)))
        left = (ARROW_W - n) // 2
        rows.append("0" * left + "f" * n + "0" * (ARROW_W - n - left))
    return rows


# The nav rail's LEVEL icon (owner, 2026-09-28): a spirit level -- a solid bar with a dark vial
# window, the bubble centred between two ticks, and a hole at each end -- in the rail's icon font
# 24 at the unused placeholder 'Y' (a stock 48x48 block that no record draws). The rail's slide-out
# icon is 'S', drawn only by the rail's two six-icon arms (objects 389 and 390), which the recipe
# retargets S -> Y; 'S' itself stays in the font. 44x19, one hex digit (4bpp coverage) per pixel,
# drawn at 8x and box-filtered down; the cell is 48 rows, so y = (48 - 19) // 2 = 14.
NAV_ICON = (24, "Y")
NAV_ICON_OBJECTS = (389, 390)
NAV_ICON_ROWS = (
    "02aefffffffffffffffffffffffffffffffffffffb30",
    "2effffffffffffffffffffffffffffffffffffffffe3",
    "affffffffffffffffffffffffffffffffffffffffffc",
    "efffffffffffffffffffffffffffffffffffffffffff",
    "ffffffffffffb40f900000000008f24affffffffffff",
    "fffffffffff8000f900000000008f2006fffffffffff",
    "ffffffffffb0000f9007cffc7108f20009ffffffffff",
    "fffb009fff40000f90bffffffd18f20002fffb009fff",
    "fff8006fff00000f96ffffffff88f20000eff8006fff",
    "fff8006fff00000f96ffffffff88f20000dff8006fff",
    "fffa008fff40000f91dffffffe28f20002fffa008fff",
    "ffffedffffa0000f9018effe9208f20008ffffedffff",
    "fffffffffff6000f900002200008f2004fffffffffff",
    "ffffffffffff920f900000000008f228ffffffffffff",
    "ffffffffffffffefeddddddddddefeffffffffffffff",
    "bffffffffffffffffffffffffffffffffffffffffffd",
    "3efffffffffffffffffffffffffffffffffffffffff4",
    "03cffffffffffffffffffffffffffffffffffffffd40",
    "00012222222222222222222222222222222222221000",
)


def nav_icon_ops() -> list[dict]:
    """The three ops that put the spirit level on the nav rail: the glyph, then the rail's two
    six-icon arms retargeted from the slide-out 'S' to it."""
    slot, char = NAV_ICON
    ops = [{"id": "nav-level-glyph", "type": "install_glyph", "enabled": True,
            "params": {"slot": slot, "char": char, "y": (48 - len(NAV_ICON_ROWS)) // 2,
                       "rows": list(NAV_ICON_ROWS), "claim_block": True},
            "note": "nav rail: spirit-level icon at font 24 'Y' (an unused placeholder)"}]
    ops += [{"id": f"nav-level-{obj}", "type": "set_icon", "enabled": True,
             "params": {"target": obj, "current": "S", "char": char},
             "note": "nav rail: the LEVEL icon replaces the slide-out icon"}
            for obj in NAV_ICON_OBJECTS]
    return ops


def recipe_ops(preview: bool, accent=ACCENT, orientation="rear",
               controls=False, dgn=None, stepper_dgn=None, fill_safe=False,
               arrow_probe=False, arrow_probe2=False, arrow_last=False,
               setup=False, refresh=False) -> list[dict]:
    """The three ops a LEVEL build appends to v33's recipe, in order: the title rename (root 32's
    own wt-11 title, record 32[3], so screens 12 and 27 keep theirs), the arrow glyph, and the page.
    Task 9's build projects are generated from this, so tests and builds cannot diverge.
    `controls` true adds the Blocks-carried stepper and Zero Level (`add_level_page`'s
    `controls` param); false leaves the param out, so v34a/v34b's projects are unchanged.
    `dgn` (the sensor's status DGN) and `stepper_dgn` (the - / + DGN) are emitted only when
    given, so v34a-v36's projects are unchanged; v37 passes RV-C's custom DGN 0x1EFBF for
    both."""
    _check_orientation(orientation)
    slot, char = ARROW
    params = {"preview": bool(preview), "arrow": {"slot": slot, "char": char},
              "accent": list(accent), "orientation": orientation}
    if controls:
        params["controls"] = True
    if dgn is not None:
        params["dgn"] = dgn
    if stepper_dgn is not None:
        params["stepper_dgn"] = stepper_dgn
    if fill_safe:
        params["fill_safe"] = True
    if arrow_probe:
        params["arrow_probe"] = True
    if arrow_probe2:
        params["arrow_probe2"] = True
    if arrow_last:
        params["arrow_last"] = True
    if setup:
        params["setup"] = True
    if refresh:
        params["repaint"] = True
    return [
        {"id": "level-title", "type": "rename_label", "enabled": True,
         "params": {"object": 32, "old": "SLIDES/AWNINGS", "new": "LEVEL"},
         "note": "LEVEL page: screen 29's own title (record 32[3]); same-size edit"},
        {"id": "level-arrow", "type": "install_glyph", "enabled": True,
         "params": {"slot": slot, "char": char, "y": (ARROW_CELL - ARROW_H) // 2,
                    "rows": arrow_rows()},
         "note": "LEVEL page: the wheel tiles' 20x14 up arrow"},
        {"id": "level", "type": "add_level_page", "enabled": True,
         "params": params,
         "note": "LEVEL page on screen 29 (object 367 replaced), fed by the level sensor's "
                 "0x%05X records" % (0x0FFB0 if dgn is None else dgn) + (" -- PREVIEW: static example, no receive side" if preview
                                      else "")},
    ]


class LevelPageError(ValueError):
    """The layout could not be drawn (a text does not fit, a cell is missing)."""


# ---- text helpers ------------------------------------------------------------------------------

def _cell_h(data: bytes, font: int) -> int:
    return metrics.font_metrics(data, font).height - 1


def _width(data: bytes, font: int, text: str) -> int:
    w = metrics.text_width(data, font, text)
    if w is None:
        raise LevelPageError(f"font {font} cannot draw {text!r}")
    return w


def _left(data, text, x, y, *, font, rgb, bg=None):
    """Text whose top-left is (x, y), as mock2's `text(..., anchor='l')`."""
    return pd.label(data, text, x, y, _width(data, font, text) + 2, _cell_h(data, font),
                    font=font, rgb=rgb, h_align=1, v_align=2, bg=bg)


def _right(data, text, right, y, *, font, rgb, bg=None, w=None):
    """Text right-justified to end before `right` (mock2 anchor 'r'): box `x + w == right`."""
    w = w if w is not None else _width(data, font, text) + 2
    return pd.label(data, text, right - w, y, w, _cell_h(data, font), font=font, rgb=rgb,
                    h_align=3, v_align=2, bg=bg)


def _centre(data, text, x, w, y, *, font, rgb, bg=None):
    """Text centred in the column `x .. x+w` with its top at y."""
    return pd.label(data, text, x, y, w, _cell_h(data, font), font=font, rgb=rgb,
                    h_align=2, v_align=2, bg=bg)


# ---- static frame ------------------------------------------------------------------------------

def van(orientation: str) -> dict:
    """The van drawing's boxes in screen position for `orientation`: `LAYOUT["van"]` with every
    box through `_rot_box` ("tyres" in wheel order fl, fr, rl, rr; "dashes" the cab line's
    segments). "front" is the "FRONT" label's column and top: above the body facing front,
    `body_bot + 4` below it facing rear (y ~ 452, the rear-facing mockup)."""
    _check_orientation(orientation)
    v = LAYOUT["van"]
    rb = lambda box: _rot_box(*box, orientation)
    bx, by, bw, bh, br = v["body"]
    x0, x1 = v["cab_x"]
    fx, fy, fw, fh = v["front"]
    if orientation in _ROTATED:
        fy = _BODY_BOT + 4 + SHIFT_Y[orientation]
    return {"body": rb((bx, by, bw, bh)) + (br,),
            "tyres": [rb(t) for t in v["tyres"]],
            "windshield": rb(v["windshield"]),
            "dashes": [rb((x, v["cab_y"], 5, 1)) for x in range(x0, x1, 10)],
            "mirrors": [rb(m) for m in v["mirrors"]],
            "front": (fx, fy, fw, fh)}


def _frame(data: bytes, orientation: str = "rear", controls: bool = False) -> list[bytes]:
    L, v = LAYOUT, van(orientation)
    recs = [pd.fill(data, *L["page"], NAVY)]
    for t in v["tyres"]:
        recs.append(pd.fill(data, *t, GREY))
    bx, by, bw, bh, br = v["body"]
    recs.append(pd.rbox(data, bx, by, bw, bh, br, GREY, BODY))
    recs.append(pd.fill(data, *v["windshield"], GREY))
    for d in v["dashes"]:
        recs.append(pd.fill(data, *d, GREY))
    for m in v["mirrors"]:
        recs.append(pd.rbox(data, *m, 2, GREY, NAVY))
    fx, fy, fw, _fh = v["front"]
    px, py, pw, ph = L["page"]
    if fy + _cell_h(data, F_CAPTION) > py + ph:
        raise LevelPageError(f"FRONT at y {fy} runs below the page (y {py + ph})")
    recs.append(_centre(data, "FRONT", fx, fw, fy, font=F_CAPTION, rgb=GREY))
    for key, title in (("attitude", "Attitude"), ("blocks", "Blocks")):
        g = L[key]
        x, y, w, h, r = g["box"]
        recs.append(pd.rbox(data, x, y, w, h, r, GREY, NAVY))
        recs.append(pd.fill(data, *g["notch"], NAVY))
        recs.append(_left(data, title, *g["title"], font=F_TITLE, rgb=WHITE))
    for text, (x, y) in L["captions"].items():
        if controls and text == "Carried":         # moves into the stepper's centre panel
            continue
        recs.append(_left(data, text, x, y, font=F_CAPTION, rgb=GREY))
    recs.append(pd.hline(data, *L["divider"], DIV))
    return recs


# ---- the parts that are live (switch arms) or example text (preview) ----------------------------

def _tile_at(key: str, orientation: str) -> tuple[int, int]:
    """Wheel `key`'s tile top-left, through `tile_slot` (the one place a tile is positioned),
    moved with the van by `SHIFT_Y`."""
    x, y = LAYOUT["tiles"][tile_slot(key, orientation)]
    return x, y + SHIFT_Y[orientation]


tile_pos = _tile_at      # public: where a wheel's tile is drawn for an orientation (tests, tools)


def _tile_xy(key: str, orientation: str) -> tuple[int, int]:
    x0, top = _tile_at(key, orientation)
    return x0, top + _TH // 2


def _tile_capsule(data, key, rgb, orientation):
    x0, top = _tile_at(key, orientation)
    t = LAYOUT["tiles"]
    return pd.capsule(data, x0, top, t["w"], t["h"], t["r"], rgb)


def _tile_green(data, key, orientation) -> list[bytes]:
    x0, yc = _tile_xy(key, orientation)
    mx = x0 + _TW // 2
    bx, by, bw, bh = TILE["bar"]
    return [_tile_capsule(data, key, GREEN, orientation),
            pd.fill(data, mx + bx, yc + by, bw, bh, WHITE),
            _centre(data, "LEVEL", x0, _TW, yc + TILE["level_y"], font=F_TILE_LEVEL, rgb=WHITE)]


def _tile_grey(data, key, orientation) -> list[bytes]:
    x0, yc = _tile_xy(key, orientation)
    h = _cell_h(data, F_TILE_NA)
    return [_tile_capsule(data, key, GREY, orientation),
            _centre(data, "--", x0, _TW, yc - (h + 1) // 2, font=F_TILE_NA, rgb=WHITE)]


# `arrow_probe` (v39, a diagnostic): v37 and v38 drew the arrow as its leftmost six columns on the
# coach, cut at x0+13, which neither the text drawer's clip nor the renderer explains
# (`GUICODE.md` Finding 78). One flash separates the hypotheses. Keys are the rear-facing tiles the
# owner's 2026-09-28 photo shows amber: rr (top-left) keeps the arrow as it is, the control; rl
# (top-right) widens the box to 30 px, still centred; fr (bottom-left) keeps the box but aligns
# left. `ARROW_PROBE_STATIC` puts one more arrow in the static body, outside every arm and not
# after a capsule, in the empty gap left of the van between Zero Level and the lower tiles.
ARROW_PROBE = {"rl": ("wide", 4, 30, 2), "fr": ("left", 8, 19, 1)}   # (name, x offset, w, h-align)
ARROW_PROBE_STATIC = (135, 288, 19, 20)

# `arrow_probe2` (v40, a diagnostic): v39 showed only the static arrow whole, so the cut is inside
# the tile arm, and the banners (a label right after a capsule) show it is not the capsule. The
# suspect is the layers readout's background, whose ~50 px width did not follow the field width.
# rr (top-left) is the control; rl (top-right) draws the arrow LAST in the arm; fr (bottom-left)
# paints the layers readout's background green and the lift's blue, so the photo shows each fill.
ARROW_PROBE2_LAST = "rl"
ARROW_PROBE2_COLOURS = ("fr", (0x00, 0xE0, 0x00), (0x00, 0x60, 0xFF))   # key, layers bg, lift bg


def _arrow(data, key, arrow, orientation, probe=False) -> bytes:
    """The up-arrow glyph, centred in `TILE["arrow"]` (pagedraw refuses a glyph wider than it).
    `probe` applies `ARROW_PROBE`'s variant for `key`, if it has one."""
    font, char = arrow
    x0, yc = _tile_xy(key, orientation)
    ax, ay, aw, ah = TILE["arrow"]
    if probe == 1 and key in ARROW_PROBE:
        _name, ax, aw, h_align = ARROW_PROBE[key]
        return pd.label(data, char, x0 + ax, yc + ay, aw, ah, font=font, rgb=WHITE,
                        h_align=h_align, v_align=2)
    return pd.glyph(data, char, x0 + ax, yc + ay, aw, ah, font=font, rgb=WHITE)


def _lift_in_x(data, x0) -> tuple[int, int]:
    """(value right edge, "in" left) for the tile's "2.8 in" line, centred as mock2 draws it."""
    mx = x0 + _TW // 2
    whole = _width(data, F_TILE_LIFT, "2.8 in")
    start = mx - whole // 2
    in_left = start + whole - _width(data, F_TILE_LIFT, "in")
    num_right = start + _width(data, F_TILE_LIFT, "2.8")
    return num_right, in_left


def _tile_amber(data, key, arrow, *, layers, lift, orientation, accent=ACCENT,
                fields=FIELDS_V37, probe=False) -> list[bytes]:
    """`layers`/`lift`: a display-cell index (live readout) or a str (preview text). `probe` 1 is
    v39's `ARROW_PROBE`, 2 is v40's `ARROW_PROBE2_*`, 3 is `arrow_last`: the arrow drawn after
    both readouts, whose backgrounds otherwise paint over it (v40 on the coach)."""
    x0, yc = _tile_xy(key, orientation)
    last = probe == 3 or (probe == 2 and key == ARROW_PROBE2_LAST)
    paint = probe == 2 and key == ARROW_PROBE2_COLOURS[0]
    layers_bg = ARROW_PROBE2_COLOURS[1] if paint else accent
    lift_bg = ARROW_PROBE2_COLOURS[2] if paint else accent
    arrow_rec = _arrow(data, key, arrow, orientation, probe)
    recs = [_tile_capsule(data, key, accent, orientation)] + ([] if last else [arrow_rec])
    lr, ly = x0 + TILE["layers_right"], yc + TILE["layers_y"]
    lh = _cell_h(data, F_TILE_LAYERS)
    if isinstance(layers, str):
        recs.append(_right(data, layers, lr, ly, font=F_TILE_LAYERS, rgb=WHITE))
    else:
        w = TILE["layers_right"] - (TILE["arrow"][0] + TILE["arrow"][2]) - ARROW_CLEARANCE
        recs.extend(pd.readout(data, lr - w, ly, w, lh, value_slot=layers,
                               width=fields["layers"], places=0, font=F_TILE_LAYERS, rgb=WHITE,
                               bg=layers_bg, align=3))
    bx, by = TILE["blocks"]
    recs.append(_left(data, "blocks", x0 + bx, yc + by, font=F_TILE_BLOCKS, rgb=WHITE))
    num_right, in_left = _lift_in_x(data, x0)
    fy = yc + TILE["lift_y"]
    fh = _cell_h(data, F_TILE_LIFT)
    if isinstance(lift, str):
        recs.append(_right(data, lift, num_right, fy, font=F_TILE_LIFT, rgb=WHITE))
    else:
        w = num_right - (x0 + 8)
        recs.extend(pd.readout(data, num_right - w, fy, w, fh, value_slot=lift,
                               width=fields["lift"], places=1, font=F_TILE_LIFT, rgb=WHITE,
                               bg=lift_bg, align=3))
    recs.append(_left(data, "in", in_left, fy, font=F_TILE_LIFT, rgb=WHITE))
    return recs + ([arrow_rec] if last else [])


def _banner(data, rgb, text) -> list[bytes]:
    x, y, w, h, r = LAYOUT["banner"]
    return [pd.capsule(data, x, y, w, h, r, rgb),
            _centre(data, text, x, w, LAYOUT["banner_text_y"], font=F_BANNER, rgb=WHITE)]


def _short_on(data, accent=ACCENT) -> list[bytes]:
    x, y, w, h, r = LAYOUT["short"]
    return [pd.capsule(data, x, y, w, h, r, accent),
            _centre(data, "SHORT ON BLOCKS", x, w, LAYOUT["short_text_y"], font=F_SHORT,
                    rgb=WHITE)]


def _short_off(data) -> list[bytes]:
    x, y, w, h, _r = LAYOUT["short"]
    return [pd.fill(data, x, y, w, h, NAVY)]


def _word_box(data, name) -> tuple[int, int, int, int]:
    w = LAYOUT["word_box_w"]
    return (LAYOUT["value_right"] - w, LAYOUT["word_y"][name], w, _cell_h(data, F_WORD))


def _word(data, name, text) -> list[bytes]:
    x, y, w, _h = _word_box(data, name)
    return [_right(data, text, x + w, y, font=F_WORD, rgb=GREY, bg=NAVY, w=w)]


def _word_blank(data, name) -> list[bytes]:
    return [pd.fill(data, *_word_box(data, name), NAVY)]


def _value_parts(data, name, cell, *, width, places, unit) -> tuple[list, list, tuple]:
    """`(static, live, clear)`: the unit label (if any), the value readout records, and the box
    a repaint-on-change arm clears -- from `REFRESH_CLEAR_X[name]` (right of the row's caption)
    to the value's last column, the value's rows."""
    right, y = LAYOUT["value_right"], LAYOUT["value_y"][name]
    h = _cell_h(data, F_VALUE)
    static = []
    if unit:
        uw = _width(data, F_VALUE, unit)
        static.append(_right(data, unit, right, y, font=F_VALUE, rgb=WHITE, bg=NAVY, w=uw + 1))
        right -= uw + 1
    w = width * metrics.font_metrics(data, F_VALUE).spacing
    live = pd.readout(data, right - w, y, w, h, value_slot=cell, width=width,
                      places=places, font=F_VALUE, rgb=WHITE, bg=NAVY, align=3)
    cx = REFRESH_CLEAR_X[name]
    return static, list(live), (cx, y, right - 1 - cx, h)


def _value_readout(data, name, cell, *, width, places, unit) -> list[bytes]:
    """A right-aligned value ending at `value_right` (the unit, if any, after it)."""
    static, live, _clear = _value_parts(data, name, cell, width=width, places=places, unit=unit)
    return static + live


def refresh_group(data, name: str, live: list, clear: tuple, bg) -> tuple:
    """A repaint-on-change switch group `(toggle cell, at, arms, match values)`: two identical
    arms, each clearing `clear` to `bg` and then drawing the value, selected by the value's toggle
    cell (`levelfeed.REFRESH_*`). Toggle 1 matches the second arm; 0, and the junk SRAM holds
    before the first tick, fall back to the default first."""
    arm = [pd.fill(data, *clear, bg)] + list(live)
    return (f"{name}_t", clear[:2], [arm, list(arm)], [0xFFFF, 1])


# The left edge of each LEVEL value's repaint box: right of its caption's ink ("PITCH"/"ROLL" end
# before 640, "Needed"/"Carried" before 650), so the arm never paints over a caption.
REFRESH_CLEAR_X = {"pitch": 640, "roll": 640, "total": 650, "carried": 650}


# ---- the controls (static body) ----------------------------------------------------------------

def _centre_panel() -> tuple[int, int, int, int]:
    """The stepper's centre panel: the columns between - and + (inclusive), their rows."""
    mx, my, mw, mh = CONTROLS["minus"]
    px, _py, _pw, _ph = CONTROLS["plus"]
    return mx + mw + 1, my, px - 1 - (mx + mw + 1), mh


def _bar(data, bx, by, bw, bh, upright=False) -> bytes:
    """A white bar centred in button `(bx, by, bw, bh)`: BAR_LONG x BAR_THICK px, or upright."""
    lw, lh = (BAR_THICK, BAR_LONG) if upright else (BAR_LONG, BAR_THICK)
    return pd.fill(data, bx + (bw + 1 - lw) // 2, by + (bh + 1 - lh) // 2, lw - 1, lh - 1, WHITE)


def _controls(data: bytes, carried, orientation: str = "rear") -> list[bytes]:
    """The stepper (- and + buttons, the centre panel with "Carried" and the carried value) and
    the Zero Level button. `carried` is a display cell (live readout) or a str (preview)."""
    mx, my, mw, mh = CONTROLS["minus"]
    px, py, pw, ph = CONTROLS["plus"]
    cx, cy, cw, ch = _centre_panel()
    recs = [pd.fill(data, mx, my, mw, mh, MAROON), _bar(data, mx, my, mw, mh),
            pd.fill(data, px, py, pw, ph, MAROON), _bar(data, px, py, pw, ph),
            _bar(data, px, py, pw, ph, upright=True),
            pd.fill(data, cx, cy, cw, ch, CENTRE),
            _centre(data, "Carried", cx, cw, cy + STEPPER_CAPTION_DY, font=F_STEPPER_CAPTION,
                    rgb=WHITE)]
    vy, vh = cy + STEPPER_VALUE_DY, _cell_h(data, F_VALUE)
    if vy + vh > cy + ch:
        raise LevelPageError(f"the carried value (y {vy}..{vy + vh}) runs out of its panel")
    base = cy + STEPPER_CAPTION_DY + metrics.font_metrics(data, F_STEPPER_CAPTION).top
    if base >= vy:
        raise LevelPageError(f"'Carried' ink reaches row {base}, inside the value's cell (y {vy})")
    if isinstance(carried, str):
        recs.append(_centre(data, carried, cx, cw, vy, font=F_VALUE, rgb=WHITE))
    else:
        w = 3 * metrics.font_metrics(data, F_VALUE).spacing
        recs.extend(pd.readout(data, cx + (cw - w) // 2, vy, w, vh, value_slot=carried,
                               width=3, places=0, font=F_VALUE, rgb=WHITE, bg=CENTRE, align=2,
                               justify="centre"))
    zx, zy, zw, zh = controls_rects(orientation)["zero"]
    ty, hy = (zy + d for d in ZERO_TEXT_Y)
    if hy + _cell_h(data, F_ZERO_HINT) > zy + zh:
        raise LevelPageError("Zero Level's hint runs out of its button")
    recs += [pd.capsule(data, zx, zy, zw, zh, ZERO_RADIUS, MAROON),
             _centre(data, "Zero Level", zx, zw, ty, font=F_ZERO, rgb=WHITE),
             _centre(data, "hold 3 s", zx, zw, hy, font=F_ZERO_HINT, rgb=WHITE)]
    return recs


def _check_controls(controls) -> None:
    if not isinstance(controls, dict) or set(controls) != set(CONTROL_NAMES):
        raise LevelPageError(f"controls must map exactly {CONTROL_NAMES} to touch ids, "
                             f"got {controls!r}")
    for name, tid in controls.items():
        if not isinstance(tid, int) or isinstance(tid, bool) or not 0 <= tid <= 0xFFFF:
            raise LevelPageError(f"controls[{name!r}] must be a u16 touch id, got {tid!r}")


# ---- the page ----------------------------------------------------------------------------------

def _preview(data: bytes, arrow, accent=ACCENT, orientation="rear",
             controls: bool = False) -> list[bytes]:
    E = EXAMPLE
    recs = []
    for key in ("fl", "fr", "rl", "rr"):
        t = E["tiles"][key]
        recs += _tile_green(data, key, orientation) if t is None else \
            _tile_amber(data, key, arrow, layers=str(t[0]), lift=t[1], accent=accent,
                        orientation=orientation)
    right = LAYOUT["value_right"]
    for name in ("pitch", "roll", "total") + (() if controls else ("carried",)):
        recs.append(_right(data, E[name], right, LAYOUT["value_y"][name], font=F_VALUE,
                           rgb=WHITE))
    for name in ("pitch_word", "roll_word"):
        recs += _word(data, name, E[name])
    _v, rgb, text = [a for a in BANNER_ARMS if a[0] == E["banner"]][0]
    recs += _banner(data, rgb, text)
    recs += _short_on(data, accent) if E["short"] else _short_off(data)
    return recs


def _live_arms(data: bytes, cells: dict, arrow, accent=ACCENT, orientation="rear",
               fields=FIELDS_V37, probe=False) -> list[tuple[str, int, list, list]]:
    """[(cell name, switch placement key, arm record lists, match values)], default arm first."""
    groups = []
    for key in ("fl", "fr", "rl", "rr"):
        amber = _tile_amber(data, key, arrow, layers=cells[f"lay_{key}"],
                            lift=cells[f"lift_{key}"], accent=accent, orientation=orientation,
                            fields=fields, probe=probe)
        groups.append((f"wheel_{key}", _tile_at(key, orientation),
                       [_tile_grey(data, key, orientation), _tile_green(data, key, orientation),
                        amber], [0xFFFF, 0, 1]))
    groups.append(("banner", LAYOUT["banner"][:2],
                   [_banner(data, rgb, text) for _v, rgb, text in BANNER_ARMS],
                   [v for v, _rgb, _t in BANNER_ARMS]))
    groups.append(("short", LAYOUT["short"][:2], [_short_off(data), _short_on(data, accent)],
                   [0xFFFF, 1]))
    for name, (one, two) in WORDS.items():
        groups.append((name, _word_box(data, name)[:2],
                       [_word_blank(data, name), _word(data, name, one), _word(data, name, two)],
                       [0xFFFF, 1, 2]))
    return groups


def build_body(data: bytes, *, cells: "dict[str, int] | None",
               arrow: tuple[int, str], accent=ACCENT,
               orientation: str = "rear",
               controls: "dict[str, int] | None" = None,
               fill_safe: bool = False, arrow_probe: bool = False,
               arrow_probe2: bool = False, arrow_last: bool = False,
               setup: bool = False, refresh: bool = False) -> tuple[bytes, dict]:
    """`(image, info)`: `info["body"]` is object 367's new record stream. With `cells=None` the
    image is `data` unchanged and the body draws the mockup's example state; with `cells` the
    image carries the switch arms appended (one match-table grow) and the body the live page.
    `orientation` (`ORIENTATIONS`) turns the van, places the tiles and sets the group height.

    `controls` (`{"minus": id, "plus": id, "zero": id}`, touch ids) draws the Blocks-carried
    stepper in place of the Carried row (the carried value moves into its centre panel) and the
    Zero Level button, and appends the three wt-12 touch regions (`CONTROLS`) LAST in the static
    body, in `CONTROL_NAMES` order -- never inside a switch arm. `None` draws exactly the body
    without them (v34b's bytes). `fill_safe` narrows the wheel tiles' readout fields
    (`FIELDS_FILL_SAFE`) so no background fill can cover the arrow or leave the tile.

    `setup` (with `controls`) moves the stepper and Zero Level into the LEVEL SETUP popup
    (`levelsetup`): the Blocks box shows Carried read-only, and a Setup button takes Zero Level's
    place, its touch region (`levelsetup.SETUP_ACTION`) last in the body. The popup's own records
    are built by the caller."""
    _check_orientation(orientation)
    if controls is not None:
        _check_controls(controls)
    if setup and (controls is None or cells is None):
        raise LevelPageError("setup needs the live page with its controls")
    stepper = controls is not None and not setup
    recs = _frame(data, orientation, stepper)
    info: dict = {"switches": {}, "arm_objects": 0, "match_values": []}
    out = data
    if cells is None:
        recs += _preview(data, arrow, accent, orientation, controls is not None)
    else:
        missing = [n for n in ("pitch", "roll", "total", "carried", "banner", "short",
                               "pitch_word", "roll_word")
                   + tuple(f"{p}_{k}" for p in ("wheel", "lay", "lift")
                           for k in ("fl", "fr", "rl", "rr")) if n not in cells]
        if missing:
            raise LevelPageError(f"cells missing {missing}")
        values = [("pitch", 5, 1, "°"), ("roll", 5, 1, "°"), ("total", 3, 0, None)]
        if not stepper:
            values.append(("carried", 3, 0, None))
        wrapped = []
        for name, width, places, unit in values:
            static, live, clear = _value_parts(data, name, cells[name], width=width,
                                               places=places, unit=unit)
            recs += static
            if refresh:
                wrapped.append(refresh_group(data, name, live, clear, NAVY))
            else:
                recs += live
        groups = _live_arms(data, cells, arrow, accent, orientation,
                            FIELDS_FILL_SAFE if fill_safe else FIELDS_V37,
                            3 if arrow_last else 2 if arrow_probe2 else 1 if arrow_probe else 0)
        groups += wrapped
        if arrow_probe:
            x, y, w, h = ARROW_PROBE_STATIC
            recs.append(pd.glyph(data, arrow[1], x, y, w, h, font=arrow[0], rgb=WHITE))
        arm_objects = [pd.stream(arm) for _n, _at, arms, _mv in groups for arm in arms]
        values = [v for _n, _at, _arms, mv in groups for v in mv]
        out, base = switchbuild.append_arms(data, arm_objects, values)
        for name, at, arms, mv in groups:
            recs.append(switchbuild.author_switch(out, value_slot=cells[name], arms_base=base,
                                                  count=len(arms), at=tuple(at)))
            info["switches"][name] = {"arms_base": base, "count": len(arms), "match": list(mv)}
            base += len(arms)
        info["arm_objects"] = len(arm_objects)
        info["match_values"] = values
    if stepper:
        recs += _controls(data, EXAMPLE["carried"] if cells is None else cells["carried"],
                          orientation)
        rects = controls_rects(orientation)
        recs += [pd.touch(data, *rects[n], controls[n]) for n in CONTROL_NAMES]
    elif setup:
        from . import levelsetup
        zx, zy, zw, zh = controls_rects(orientation)["zero"]
        # The stock setup buttons' shape (object 427, "Xantrex Controls"): the caption centred in
        # the left part, the nav rail's gear (font 24 'L', 37x37) centred in its own box on the
        # right. The gear's box is one font-24 cell tall, centred on the button.
        gw, gh = SETUP_GEAR_BOX, metrics.font_metrics(data, SETUP_GEAR[0]).height - 1
        tw = zw - gw - SETUP_GEAR_PAD
        recs += [pd.capsule(data, zx, zy, zw, zh, ZERO_RADIUS, MAROON),
                 _centre(data, "Setup", zx, tw, zy + (zh - _cell_h(data, F_ZERO)) // 2,
                         font=F_ZERO, rgb=WHITE),
                 pd.glyph(data, SETUP_GEAR[1], zx + tw, zy + (zh - gh) // 2, gw, gh,
                          font=SETUP_GEAR[0], rgb=WHITE),
                 pd.touch(data, zx, zy, zw, zh, levelsetup.SETUP_ACTION)]
    info["records"] = len(recs)
    info["body"] = pd.stream(recs)
    return out, info
