"""The LEVEL SETUP popup: screen 12 redrawn as a dialog over the LEVEL page (owner, 2026-09-29).

The owner asked for block height and tolerance controls, and for Zero Level and the Blocks-carried
stepper to move off the LEVEL page, all in a popup, so they never feel they left the page. The
panel has no overlay mechanism, but it does not need one:

* GuiCode draws into ONE framebuffer, `0xC0000000`: the state pointer `+0x6BA8` is set from that
  constant at one site (`0x2516F4`), and it is the LTDC layer's `FBStartAdress`
  (`LCD-FIRMWARE.md` Finding 7). What is drawn stays until something draws over it.
* Every stock screen root paints its own full-screen background first (its first record is a
  799x479 wt-4 fill), so a screen change evidently does not clear the display (INFERENCE from the
  corpus; the first flash of this page confirms it).

So screen 12's root (object 15, the stranded SLIDES/AWNINGS page, reached by nothing since the rail
icon moved to screen 29) is rebuilt WITHOUT a background: the header widgets (892) and the nav rail
(974), which redraw identically over the LEVEL page's own, then the dialog. The LEVEL page stays
visible around it, frozen until Close (touch id `0x7DC`, the rail icon's leaf, which goes to screen
29) repaints it. The LEVEL page's Setup button carries `0x7ED`, a leaf no stock region uses,
retargeted from screen 29 to 12 (`navicon.retarget_action_leaf`).

Inside the dialog, top to bottom:

    LEVEL SETUP
    Block height   [-]  1.00 in  [+]
    Tolerance      [-]  0.50 in  [+]  [AUTO]
    Carried        [-]    10     [+]
    [Zero Level, hold 3 s]              [Close]

Every value is a live readout (`levelfeed` cells), drawn BEFORE any label beside it: v37-v40
showed a readout repainting about 50 px of background left of its pen, whatever its field width,
which wipes whatever was drawn there first (`readoutfill`). The AUTO button is a type-6 switch on
the auto cell (lit green while the tolerance is automatic). Every touch region is appended last in
the root, never inside a switch arm (the LEVEL controls' rule).
"""
from __future__ import annotations

from . import levelpage as lp
from . import metrics, pagedraw as pd, records

POPUP_ROOT = 15                      # screen 12's root object
SETUP_ACTION = 0x7ED                 # LEVEL page Setup button -> screen 12 (retargeted leaf)
CLOSE_ACTION = 0x7DC                 # the rail icon's leaf -> screen 29, the LEVEL page
KEPT_ROOT_RECORDS = (3, 4)           # root 15's wt-5 references to 892 (header widgets), 974 (rail)
ROOT_REFS = (892, 974)
# v43 (`popup_background`): v42 on the coach showed a screen change DOES clear the display (to
# white), refuting the no-clear inference above. So root 15 keeps its own background, header bar
# and title (records 0-2, retitled), then paints the LEVEL page's navy page area, then the dialog.
BACKGROUND_RECORDS = (0, 1, 2)
TITLE_RECORD = 2
TITLE_STOCK, TITLE = "   SLIDES/AWNINGS", "   LEVEL SETUP   "

DIALOG = (150, 60, 580, 360, 10)     # x, y, w, h, radius: x 150..730, y 60..420, inside the page
FILL, BORDER = lp.BODY, lp.WHITE
TITLE_Y = 72
LABEL_X = 172
ROW_Y = {"height": 118, "tol": 190, "carried": 262}      # each row's button tops; 46 px tall
BTN = 46
MINUS_X, PANEL_X, PANEL_W, PLUS_X = 356, 408, 150, 564   # stepper columns: - | panel | +
AUTO = (626, None, 88, BTN)          # AUTO button x, (row y), w, h
BOTTOM_Y, BOTTOM_H = 340, 60
ZERO = (172, BOTTOM_Y, 200, BOTTOM_H)
CLOSE = (570, BOTTOM_Y, 140, BOTTOM_H)
RADIUS = 6
UNIT_GAP = 4

# (cell, directive width, places, unit): height 0.25..12.50 in is at most five characters.
VALUES = {"height": ("height", 5, 2, "in"), "tol": ("tol", 4, 2, "in"),
          "carried": ("carried", 3, 0, None)}
LABELS = {"height": "Block height", "tol": "Tolerance", "carried": "Carried"}
# The touch ids: carried's - / + and Zero Level reuse the LEVEL controls' groups (their frames are
# unchanged); height and tolerance use the five setup groups (`levelcontrols.SETUP_CONTROLS`).
ROW_CONTROLS = {"height": ("h_down", "h_up"), "tol": ("t_down", "t_up"),
                "carried": ("minus", "plus")}
TOUCH_NAMES = ("h_down", "h_up", "t_down", "t_up", "t_auto", "minus", "plus", "zero", "close")

# v45 `geometry`: Wheelbase and Track rows under Carried (set once, so last), -/+ 0.5 in on
# level-sensor ops 0Bh-0Eh, each value in 0.1 in shown with one place. Five rows need a taller
# dialog (y 46..464, still inside the page area below the 36-px header) and a 54-px row pitch
# (46-px buttons, 8-px gaps). Without `geometry` every constant above is used unchanged, so
# v42-v44 rebuild byte-identically.
GEOMETRY_DIALOG = (150, 46, 580, 418, 10)
GEOMETRY_TITLE_Y = 56
GEOMETRY_ROW_Y = {"height": 94, "tol": 148, "carried": 202, "wb": 256, "track": 310}
GEOMETRY_BOTTOM_Y = 372
GEOMETRY_ZERO = (172, GEOMETRY_BOTTOM_Y, 200, BOTTOM_H)
GEOMETRY_CLOSE = (570, GEOMETRY_BOTTOM_Y, 140, BOTTOM_H)
VALUES.update(wb=("wb", 5, 1, "in"), track=("track", 5, 1, "in"))
LABELS.update(wb="Wheelbase", track="Track")
ROW_CONTROLS.update(wb=("w_down", "w_up"), track=("k_down", "k_up"))
GEOMETRY_TOUCH_NAMES = TOUCH_NAMES[:-2] + ("w_down", "w_up", "k_down", "k_up") + TOUCH_NAMES[-2:]


def layout(geometry: bool = False) -> dict:
    """The popup's geometry: dialog, title y, each row's button top, Zero, Close, the rows in
    draw order and the touch names in append order."""
    if geometry:
        return {"dialog": GEOMETRY_DIALOG, "title_y": GEOMETRY_TITLE_Y, "row_y": GEOMETRY_ROW_Y,
                "zero": GEOMETRY_ZERO, "close": GEOMETRY_CLOSE,
                "rows": ("height", "tol", "carried", "wb", "track"),
                "touch_names": GEOMETRY_TOUCH_NAMES}
    return {"dialog": DIALOG, "title_y": TITLE_Y, "row_y": ROW_Y, "zero": ZERO, "close": CLOSE,
            "rows": ("height", "tol", "carried"), "touch_names": TOUCH_NAMES}


class LevelSetupError(ValueError):
    """The popup could not be drawn (a text does not fit, a cell or touch id is missing)."""


def rects(geometry: bool = False) -> dict:
    """Every touch rectangle `(x, y, w, h)`, inclusive like every pagedraw box."""
    lay = layout(geometry)
    out = {}
    for row in lay["rows"]:
        down, up = ROW_CONTROLS[row]
        y = lay["row_y"][row]
        out[down] = (MINUS_X, y, BTN, BTN)
        out[up] = (PLUS_X, y, BTN, BTN)
    out["t_auto"] = (AUTO[0], lay["row_y"]["tol"], AUTO[2], AUTO[3])
    out["zero"] = lay["zero"]
    out["close"] = lay["close"]
    return out


def _stepper(data: bytes, row: str, cell: int, refresh: bool = False,
             row_y: dict = ROW_Y) -> tuple[list, tuple]:
    """`(records, group)`: - and + buttons, the centre panel, the value readout, then its unit
    (after the readout). With `refresh` the readout moves into a repaint-on-change switch group
    (`levelpage.refresh_group`) clearing the panel left of the unit; else `group` is None."""
    y = row_y[row]
    name, width, places, unit = VALUES[row]
    recs = [pd.fill(data, MINUS_X, y, BTN, BTN, lp.MAROON), lp._bar(data, MINUS_X, y, BTN, BTN),
            pd.fill(data, PLUS_X, y, BTN, BTN, lp.MAROON), lp._bar(data, PLUS_X, y, BTN, BTN),
            lp._bar(data, PLUS_X, y, BTN, BTN, upright=True),
            pd.fill(data, PANEL_X, y, PANEL_W, BTN, lp.CENTRE)]
    vh = lp._cell_h(data, lp.F_VALUE)
    vy = y + (BTN - vh) // 2
    uw = lp._width(data, lp.F_CAPTION, unit) + 2 if unit else 0
    field = width * metrics.font_metrics(data, lp.F_VALUE).spacing
    right = PANEL_X + PANEL_W - 8 - (uw + UNIT_GAP if unit else (PANEL_W - 16 - field) // 2)
    live = pd.readout(data, right - field, vy, field, vh, value_slot=cell, width=width,
                      places=places, font=lp.F_VALUE, rgb=lp.WHITE, bg=lp.CENTRE, align=3)
    group = None
    if refresh:
        group = lp.refresh_group(data, name, live, (PANEL_X, y, right - PANEL_X, BTN), lp.CENTRE)
    else:
        recs += live
    if unit:
        uy = y + (BTN - lp._cell_h(data, lp.F_CAPTION)) // 2
        recs.append(lp._left(data, unit, right + UNIT_GAP, uy, font=lp.F_CAPTION, rgb=lp.WHITE))
    return recs, group


def _auto_arm(data: bytes, rgb, row_y: dict = ROW_Y) -> list[bytes]:
    x, _y, w, h = AUTO
    y = row_y["tol"]
    ty = y + (h - lp._cell_h(data, lp.F_ZERO)) // 2
    return [pd.capsule(data, x, y, w, h, RADIUS, rgb),
            lp._centre(data, "AUTO", x, w, ty, font=lp.F_ZERO, rgb=lp.WHITE)]


def auto_arms(data: bytes, geometry: bool = False) -> tuple[list, list]:
    """The AUTO switch's arms and match values, default (n/a) first: dark unless auto is 1."""
    ry = layout(geometry)["row_y"]
    return [_auto_arm(data, lp.MAROON, ry), _auto_arm(data, lp.MAROON, ry),
            _auto_arm(data, lp.GREEN, ry)], [0xFFFF, 0, 1]


def _button(data: bytes, rect, text: str, hint: str | None = None) -> list[bytes]:
    x, y, w, h = rect
    recs = [pd.capsule(data, x, y, w, h, RADIUS, lp.MAROON)]
    if hint is None:
        recs.append(lp._centre(data, text, x, w, y + (h - lp._cell_h(data, lp.F_ZERO)) // 2,
                               font=lp.F_ZERO, rgb=lp.WHITE))
    else:
        ty, hy = (y + d for d in lp.ZERO_TEXT_Y)
        recs += [lp._centre(data, text, x, w, ty, font=lp.F_ZERO, rgb=lp.WHITE),
                 lp._centre(data, hint, x, w, hy, font=lp.F_ZERO_HINT, rgb=lp.WHITE)]
    return recs


def kept_root_records(data: bytes, gs: int, table, background: bool = False) -> list[bytes]:
    """Root 15's own references to the header widgets and the nav rail, byte for byte. With
    `background`, preceded by its full-screen fill, header bar and title (retitled `TITLE`, a
    same-length edit), then the LEVEL page's navy page area."""
    recs = records.object_records(data, gs + table[POPUP_ROOT])
    kept = [recs[i] for i in KEPT_ROOT_RECORDS]
    if [r.child_index for r in kept] != list(ROOT_REFS) or any(r.wtype != 5 for r in kept):
        raise LevelSetupError(f"root {POPUP_ROOT} records {KEPT_ROOT_RECORDS} are not the wt-5 "
                              f"references to {ROOT_REFS}")
    out = [bytes(data[r.offset:r.end]) for r in kept]
    if not background:
        return out
    bg = []
    for i in BACKGROUND_RECORDS:
        r = recs[i]
        raw = bytearray(data[r.offset:r.end])
        if i == TITLE_RECORD:
            if r.label != TITLE_STOCK or len(TITLE) != len(TITLE_STOCK):
                raise LevelSetupError(f"root {POPUP_ROOT} title is {r.label!r}, not "
                                      f"{TITLE_STOCK!r}")
            at = r.label_offset - r.offset
            raw[at:at + len(TITLE)] = TITLE.encode("ascii")
        bg.append(bytes(raw))
    return bg + out + [pd.fill(data, *lp.LAYOUT["page"], lp.NAVY)]


def body_records(data: bytes, cells: dict, refresh: bool = False,
                 geometry: bool = False) -> tuple[list, list]:
    """`(records, groups)`: the dialog's static records (frame, title, row labels, steppers, Zero
    Level, Close), in draw order, and with `refresh` the values' repaint-on-change switch groups;
    the AUTO switch, those switches and the touch regions are added by the caller."""
    lay = layout(geometry)
    missing = [n for n in tuple(VALUES[r][0] for r in lay["rows"]) + ("auto",) if n not in cells]
    if missing:
        raise LevelSetupError(f"cells missing {missing}")
    x, y, w, h, r = lay["dialog"]
    recs = [pd.rbox(data, x, y, w, h, r, BORDER, FILL),
            lp._centre(data, "LEVEL SETUP", x, w, lay["title_y"], font=lp.F_CAPTION,
                       rgb=lp.WHITE)]
    groups = []
    for row in lay["rows"]:
        srecs, group = _stepper(data, row, cells[VALUES[row][0]], refresh, lay["row_y"])
        recs += srecs
        if group:
            groups.append(group)
        ly = lay["row_y"][row] + (BTN - lp._cell_h(data, lp.F_CAPTION)) // 2
        recs.append(lp._left(data, LABELS[row], LABEL_X, ly, font=lp.F_CAPTION, rgb=lp.WHITE))
    recs += _button(data, lay["zero"], "Zero Level", "hold 3 s")
    recs += _button(data, lay["close"], "Close")
    return recs, groups


def touch_records(data: bytes, touch_ids: dict, geometry: bool = False) -> list[bytes]:
    """The touch regions, in `layout(geometry)["touch_names"]` order, appended last."""
    names = layout(geometry)["touch_names"]
    missing = [n for n in names if n not in touch_ids]
    if missing:
        raise LevelSetupError(f"touch ids missing {missing}")
    rr = rects(geometry)
    return [pd.touch(data, *rr[n], touch_ids[n]) for n in names]
