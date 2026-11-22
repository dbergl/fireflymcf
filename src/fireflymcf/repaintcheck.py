"""Records drawn over a repaint unit from outside it: the shape that leaves a control wiped.

Between page composes the panel re-draws enrolled widgets one at a time (`FUN_002588A8`, the
live redraw, called from `GuiCode_entry1` at `0x24EEB8`; GUICODE Finding 87). Re-drawing a
widget restores its saved render state and walks it again (`0x258902 bl 0x256C54`): a type-6
switch walks the arm it selects, a wtype-5 container its child (`0x257AC8 bl 0x258330`). That
walk paints the unit's own records and nothing else. So a record that the compose drew ON TOP of
a unit's pixels, but that is not inside the unit, is painted over when the unit re-draws and is
not drawn again until the next compose.

`observed-in-emulation`, 2026-11-22: HOME's battery card is the arm of the type-6 switch at
HOME's record 9 (`value_slot` 6). Two buttons attached to the HOME root after it and placed over
the card were wiped by the card's live redraw (stack `0x24EEBD` -> `0x258907` -> `0x257ACD` ->
the card's background box), while the stock Water Pump button, a record of the tank card, was
re-drawn with its card.

What this module checks is geometry, not pixels: a unit's paint is the union of the boxes of
the drawing records inside it, a victim is a drawing record walked after the unit, outside it,
whose box meets one of those boxes. It over-reports (a label's box is larger than its glyphs;
an arm the coach never selects is still walked), so callers compare two walks of the SAME page
-- a finding present after an edit and absent before is one the edit introduced -- rather than
treating a finding as a defect on its own.

Which records the panel enrols is decided per record (`FUN_00256C54` tests render-state bit
`0x20`, from header byte 4 bit `0x20`); this module treats every switch, and every wtype-5
container that names a `value_slot` other than 0, as a unit whether or not it is enrolled
(`inferred-from-data`: on 13.6 HOME every live widget carries the bit). Over-reporting units
only adds findings that a before/after comparison cancels.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import pagewalk

# Records that draw nothing themselves: containers (their children draw) and touch regions.
_NO_PAINT = (5, 6, 0x2E, 12)
_SWITCHES = (6, 0x2E)


def _area(box) -> bool:
    return box[2] > box[0] and box[3] > box[1]


def _meets(a, b) -> bool:
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def is_unit(rec: pagewalk.WalkRec) -> bool:
    """A record the live redraw can re-draw on its own: a switch, or a wtype-5 container that
    watches a value."""
    return rec.wtype in _SWITCHES or (rec.wtype == 5 and rec.value_slot not in (None, 0, 0xFFFF))


def paints(rec: pagewalk.WalkRec) -> bool:
    return rec.wtype not in _NO_PAINT and _area(rec.box)


@dataclass(frozen=True)
class Overlap:
    unit: pagewalk.WalkRec      # the unit that re-draws
    victim: pagewalk.WalkRec    # the record it paints over and does not re-draw


def overlaps(walk: list[pagewalk.WalkRec], victims=None) -> list[Overlap]:
    """Every (unit, victim) pair in one walk (`pagewalk.walk_records` order). `victims`, a
    predicate on a WalkRec, keeps only the pairs whose victim it accepts."""
    out = []
    for i, u in enumerate(walk):
        if not is_unit(u):
            continue
        n = len(u.key)
        j = i + 1
        while j < len(walk) and walk[j].key[:n] == u.key:
            j += 1
        boxes = [r.box for r in walk[i + 1:j] if paints(r)]
        if not boxes:
            continue
        for v in walk[j:]:
            if not paints(v) or (victims is not None and not victims(v)):
                continue
            if any(_meets(v.box, b) for b in boxes):
                out.append(Overlap(u, v))
    return out


def describe(found: list[Overlap]) -> str:
    def key(k):
        return "/".join("%d:%d" % s for s in k)
    return "\n".join("  %s (wtype %d, slot %s) re-draws over %s (wtype %d %r at %s)"
                     % (key(o.unit.key), o.unit.wtype, o.unit.value_slot, key(o.victim.key),
                        o.victim.wtype, o.victim.label, o.victim.box) for o in found)
