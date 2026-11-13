"""popuppage -- a LEVEL-SETUP-style dialog on a screen `screenadd.add_screen` added.

The dialog is the one the LEVEL SETUP popup draws with Wheelbase and Track
(`levelsetup.GEOMETRY_DIALOG`, `GEOMETRY_TITLE_Y`, `GEOMETRY_CLOSE`), so the two popups are a
matched pair: a white-bordered `levelpage.BODY` box, the title centred at the top, and a Close
button bottom right. Close carries the nav rail's own leaf for the page it returns to, so it
navigates exactly as the rail icon does. Every colour is a theme role or `levelpage.BODY`, all
remapped by `set_theme`. The touch region is appended last in the root (the LEVEL controls' rule).

`content_y` (the first row top, `levelsetup.GEOMETRY_ROW_Y["height"]`) is where later content
starts.
"""
from __future__ import annotations

import struct

from . import guidata, layout, metrics, navmap, options, records
from . import levelpage as lp
from . import levelsetup as ls
from . import pagedraw as pd

DIALOG = ls.GEOMETRY_DIALOG
TITLE_Y = ls.GEOMETRY_TITLE_Y
CLOSE = ls.GEOMETRY_CLOSE
CONTENT_Y = ls.GEOMETRY_ROW_Y["height"]
RAIL_OBJECT = ls.ROOT_REFS[1]          # 974: the nav rail every root references


class PopupPageError(ValueError):
    """The dialog could not be drawn or wired."""


def _norm(text: str) -> str:
    return " ".join((text or "").split())


def _subtree_touch_ids(data: bytes, obj: int, seen: set | None = None) -> set:
    gs, ge = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    seen = set() if seen is None else seen
    if obj in seen or not 0 <= obj < len(table):
        return set()
    seen.add(obj)
    out = set()
    for rec in options._own_records(data, gs, ge, table, obj):
        if rec.wtype == 12:
            out.add(struct.unpack_from("<H", data, rec.end - guidata.GROUP_REFERENCE_TAIL)[0])
        elif rec.wtype == 5 and rec.child_index is not None:
            out |= _subtree_touch_ids(data, rec.child_index, seen)
        elif rec.wtype in (6, 0x2E):
            for _v, child in guidata.switch_candidates(data, rec):
                out |= _subtree_touch_ids(data, child, seen)
    return out


def return_leaf(data: bytes, header: str) -> tuple[int, int]:
    """`(screen_id, touch_id)`: the screen whose page header is `header` that the nav rail
    reaches, and the rail's leaf for it. Refuses unless exactly one rail leaf routes to a screen
    with that header."""
    gs, ge = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    want = _norm(header)
    screens = set()
    for sid, root in layout.screen_object_map(data).items():
        try:
            lab = options._first_label(options._own_records(data, gs, ge, table, root))
        except Exception:                       # a root without a readable header is not it
            continue
        if _norm(lab) == want:
            screens.add(sid)
    rail = _subtree_touch_ids(data, RAIL_OBJECT)
    hits = sorted({(r.screen, r.touch_id) for r in navmap.routes(data)
                   if r.screen in screens and r.touch_id in rail})
    if len(hits) != 1:
        raise PopupPageError(f"page {header!r}: {len(hits)} nav-rail leaves reach it "
                             f"(screens {sorted(screens)}), expected exactly 1")
    return hits[0]


def shell_records(data: bytes, title: str) -> list[bytes]:
    """The dialog frame, its centred title and the Close button, in draw order."""
    x, y, w, h, r = DIALOG
    if metrics.text_width(data, lp.F_CAPTION, title) > w - 2 * (ls.LABEL_X - x):
        raise PopupPageError(f"title {title!r} is wider than the dialog")
    return ([pd.rbox(data, x, y, w, h, r, ls.BORDER, ls.FILL),
             lp._centre(data, title, x, w, TITLE_Y, font=lp.F_CAPTION, rgb=lp.WHITE)]
            + ls._button(data, CLOSE, "Close"))


def close_region(data: bytes, touch_id: int) -> bytes:
    return pd.touch(data, *CLOSE, touch_id)


def root_body(data: bytes, root: int, extra: list[bytes]) -> bytes:
    """`root`'s payload (`<count><records>`, unpadded) with `extra` appended after its records."""
    gs, _ = guidata.guidata_extent(data)
    start = gs + guidata.object_tables(data)[1][root]
    recs = records.object_records(data, start)
    if len(recs) + len(extra) > 0xFF:
        raise PopupPageError(f"root {root} would carry {len(recs) + len(extra)} records (max 255)")
    return bytes([len(recs) + len(extra)]) + bytes(data[start + 1:recs[-1].end]) + b"".join(extra)


# ---- the opener: a gear button on a host page --------------------------------------------------
# The LEVEL page's Setup button shape without its caption: a MAROON capsule (a theme role) holding
# the nav rail's gear, font 24 'L' (37x37), centred in a 40-px box (`levelpage.SETUP_GEAR*`).
GEAR_W, GEAR_H = 44, 38
FILL_REACH = 60          # a readout's fill reaches ~50 px left of its pen on the coach (readoutfill)


def gear_records(data: bytes, x: int, y: int, touch_id: int) -> list[bytes]:
    """The gear button at `(x, y)`: capsule, gear glyph, touch region carrying `touch_id`."""
    font, char = lp.SETUP_GEAR
    gw, gh = lp.SETUP_GEAR_BOX, metrics.font_metrics(data, font).height - 1
    return [pd.capsule(data, x, y, GEAR_W, GEAR_H, ls.RADIUS, lp.MAROON),
            pd.glyph(data, char, x + (GEAR_W - gw) // 2, y + (GEAR_H - gh) // 2, gw, gh,
                     font=font, rgb=lp.WHITE),
            pd.touch(data, x, y, GEAR_W, GEAR_H, touch_id)]


def _hit(a: tuple, b: tuple) -> bool:
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def opener_conflicts(data: bytes, root: int, x: int, y: int) -> list[str]:
    """What the gear box at `(x, y)` would collide with on the page `root` draws: a touch region
    (a tap would be ambiguous) or a readout's fill band (a live redraw could wipe the gear: the
    rows the readout's font covers, from `FILL_REACH` left of its pen to its widest fill right)."""
    box = (x, y, x + GEAR_W, y + GEAR_H)
    out = []
    for q in layout.Screen(data).walk(root):
        if q.x0 is None or q.y0 is None:
            continue
        if q.wtype == 12 and _hit(box, (q.x0, q.y0, q.x1, q.y1)):
            out.append(f"touch region {(q.x0, q.y0, q.x1, q.y1)} in object {q.obj_index}")
        elif q.wtype == 8 and getattr(q, "font", None) is not None:
            m = metrics.font_metrics(data, q.font)
            band = (q.x0 - FILL_REACH, q.y0 - m.top, q.x0 + metrics.worst_fill_width(data, q.font, 8),
                    q.y0 - m.top + m.height - 1)
            if _hit(box, band):
                out.append(f"readout fill {band} in object {q.obj_index}")
    return out


def open_leaf(data: bytes) -> tuple[int, int]:
    """`(touch_id, current_screen)`: the lowest free ACTION leaf (`screenadd.free_action_leaves`)
    and the screen its dispatcher stores now."""
    from . import screenadd
    free = screenadd.free_action_leaves(data)
    if not free:
        raise PopupPageError("no free ACTION leaf to open the popup with")
    leaf = free[0]
    route = next(r for r in navmap.routes(data) if r.touch_id == leaf)
    return leaf, route.screen
