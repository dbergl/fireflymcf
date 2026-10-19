"""Where a number readout's background fill can land, and what it can paint over.

A wt-8 readout draws on an opaque background that `FUN_002557C0` fills across the text extent it
has just measured (`FUN_00251C10`, then `FUN_0025572C`). The measuring class is a render-state
byte that varies at runtime (`metrics.worst_fill_width`), so the same readout can fill a
different width on different redraws. And a readout that is enrolled for live redraw repaints on
its own, without the records drawn before it in the same object. So any label or glyph drawn
earlier in the object that lies inside the readout's widest possible fill can be wiped at any
later redraw.

v37 shipped exactly that: the wheel tiles' layers readout filled from about x0+12 on the coach and
wiped the up arrow drawn before it, while the renderer's kerned model placed the fill at x0+43.
v38 narrowed the field to one character, which bounds the fill at 25 px in every class this
module could model, and the arrow was still wiped. v40 painted the fills green and blue: both tile
readouts filled about 50 px left of their pens, whatever the field width. So the width model
below is NOT a bound on the coach. Inside a capsule tile, the fill is instead taken as able to
reach anywhere from the tile's left edge to the pen (`observed-on-hardware`, v40). The rule it
leaves is to draw a tile's decorations after its readouts (`add_level_page` `arrow_last`).
`conflicts` finds this class of defect from the bytes, before a flash.

What is checked, per object, walking its records in order:

* the number-format directive (wt-9) persists along the walk (GUICODE Finding 65), and gives the
  field width;
* each wt-8's worst-case fill: `metrics.worst_fill_width` columns, anchored at its pen by its
  header align (`metrics.aligned_x0`), rows from `pen_y - font[0x0b]` for `font[0x07]` rows;
* when the object's first record is a capsule (wt-33, a tile), the fill is widened to the band
  from the capsule's left edge to the pen (v40);
* it must not intersect an earlier label/glyph (wt 0/11/13: its measured text's columns, its
  glyphs' inked rows) or the worst-case fill of an earlier readout in the same object.

A directive width of 0 (unbounded) cannot be bounded and is reported as such.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import glyphs, metrics, records

TEXT_WTYPES = (0, 11, 13)
READOUT_WTYPE = 8
DIRECTIVE_WTYPE = 9
CAPSULE_WTYPE = 33


@dataclass(frozen=True)
class Conflict:
    obj: int
    record: int
    kind: str                 # "overlap" or "unbounded"
    fill: tuple               # (x0, y0, x1, y1), inclusive
    other: tuple | None       # the box it hits, or the capsule it leaves
    detail: str

    def __str__(self) -> str:
        return (f"object {self.obj} record {self.record}: {self.kind} -- worst fill {self.fill}"
                f"{'' if self.other is None else f' vs {self.other}'} ({self.detail})")


def _ink_rows(data: bytes, r, box: tuple) -> tuple:
    """`box` narrowed vertically to the rows the label's glyphs ink. `FUN_002559A0` places the
    one-line cell at the box top (v-align 1), centred (2: `y0 + ((y1 - y0 + 1) - cell) / 2`,
    `0x255B9C`) or bottom-aligned (3); each glyph then inks rows `y..y+height-1` of the cell.
    Columns are the measured text (`metrics.text_width`) placed by the label's h-align inside the
    box (`FUN_002559A0` `0x255C94`: left, centred `x0 + (w - tw) / 2`, right `x0 + w - tw`); labels
    draw during a full paint, which resets the measuring class to kerned (`0x258716`). A text the
    measurer cannot size keeps the whole box."""
    if r.font is None or not r.label:
        return box
    x0, x1 = box[0], box[2]
    tw = metrics.text_width(data, r.font, r.label)
    if tw is not None and "\n" not in r.label:
        w = x1 - x0 + 1
        left = x0 + ((w - tw) // 2 if r.h_align == 2 else (w - tw) if r.h_align == 3 else 0)
        x0, x1 = max(x0, left), min(x1, left + tw - 1)
    m = metrics.font_metrics(data, r.font)
    h = box[3] - box[1] + 1
    top = box[1] + ((h - m.height) // 2 if r.v_align == 2
                    else (h - m.height) if r.v_align == 3 else 0)
    font = glyphs.font_for_slot(data, r.font)
    spans = []
    for ch in r.label:
        try:
            g = glyphs.decode_glyph(data, font.glyph_index(ord(ch)))
        except glyphs.GlyphError:
            continue                                   # a blank (height-0) glyph inks nothing
        spans.append((g.y, g.y + g.height - 1))
    if not spans:
        return box
    return (x0, top + min(a for a, _ in spans), x1, top + max(b for _, b in spans))


def _box(r) -> tuple | None:
    x, y, w, h = r.geom[:4]
    if None in (x, y, w, h):
        return None
    return (x, y, x + w, y + h)                      # inclusive, as the panel clips


def _hit(a: tuple, b: tuple) -> bool:
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def worst_fill(data: bytes, r, field: int) -> tuple:
    """The inclusive rectangle wt-8 record `r` can fill with a `field`-character directive."""
    w = metrics.worst_fill_width(data, r.font, field)
    m = metrics.font_metrics(data, r.font)
    x0 = metrics.aligned_x0(r.geom[0], w, r.align)
    y0 = r.geom[1] - m.top
    return (x0, y0, x0 + w - 1, y0 + m.height - 1)


def conflicts(data: bytes, gs: int, table, objects) -> list[Conflict]:
    """Every readout-fill conflict in `objects` (object indices into `table`)."""
    out: list[Conflict] = []
    for obj in objects:
        recs = records.object_records(data, gs + table[obj])
        capsule = _box(recs[0]) if recs and recs[0].wtype == CAPSULE_WTYPE else None
        field = None
        drawn: list[tuple[int, tuple, str]] = []
        for i, r in enumerate(recs):
            if r.wtype == DIRECTIVE_WTYPE and r.fmt is not None:
                field = r.fmt[2]
            elif r.wtype in TEXT_WTYPES:
                b = _box(r)
                if b is not None:
                    drawn.append((i, _ink_rows(data, r, b), f"wt-{r.wtype} {r.label!r}"))
            elif r.wtype == READOUT_WTYPE and r.value_slot is not None and r.font is not None:
                if not field:
                    out.append(Conflict(obj, i, "unbounded", (), None,
                                        "directive width 0: the fill cannot be bounded"))
                    continue
                fill = worst_fill(data, r, field)
                if capsule is not None:
                    fill = (min(fill[0], capsule[0]),) + fill[1:]
                for j, b, what in drawn:
                    if _hit(fill, b):
                        out.append(Conflict(obj, i, "overlap", fill, b,
                                            f"field {field}, font {r.font}; covers record {j} "
                                            f"{what}"))
                drawn.append((i, fill, f"readout slot {r.value_slot}"))
    return out


def body_and_arms(data: bytes, gs: int, table, body: int) -> list[int]:
    """`body` and every arm its wt-6 switches can select, in table order."""
    arms = set()
    for r in records.object_records(data, gs + table[body]):
        if r.wtype == 6 and r.child_index is not None and r.count:
            arms.update(range(r.child_index, r.child_index + r.count))
    return [body] + sorted(arms)
