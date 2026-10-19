"""The battery-heater contactor glyph on the House battery icons.

While the battery's internal heater CONTACTOR is closed, the House battery on HOME (the coach-48V
clone) and on the compact ELECTRICAL frame (object 443) draw a WHITE wavy-lines glyph -- the same
font slot 23 char '2' the "Batt Heat" button shows in red -- in the bottom-right corner of the
body. The voltage number sits `NUMBER_RAISE` px higher in EVERY state: the body is ~50 px tall and
the number is anchored 2 px above its bottom, so without the raise the glyph runs through the
number (owner chose bottom-right + raised number from mockups, 2026-09-24).

**Why the whole battery goes inside a switch.** A battery is drawn by an enrolled wt5 container
that watches the voltage cell and repaints its WHOLE child -- number and fill -- on every change
(v25, the reference coach's build log (not published)). A glyph laid over it as a sibling would be wiped on the next voltage
tick and not come back until its own cell changed. So the battery object itself becomes ONE
enrolled wt6 switch on the Batt Heat state cell (`add_aes_batt_heat_status`'s fold: bit 0 heater,
bit 1 contactor), and each of its four arms draws the whole battery, the contactor arms adding the
glyph. A voltage repaint walks the switch and picks the current arm; a contactor change repaints
the switch, and the selected arm's opaque body covers whatever the previous arm drew. That is the
v32 one-switch rule applied to the battery, and stock uses the same object shape (the container on
slot 419 whose child 465 is a single enrolled switch on slot 225).

The containers are not touched, so the v25 live-repaint path is byte-identical.

**Record order inside an arm** (the battery is seven flow records: two terminals, body, level fill,
the slot-208 switch, the number, 'V'): the glyph goes straight after the level fill, so it draws
over the green fill, and BEFORE the number, because the font selector is persistent and the number
sets its own font (15), which the 'V' then inherits -- the glyph's font 23 never leaks. The glyph
is flow-relative to the pen the level fill committed (body x0 + 2, body y1 - 2) and is followed by
an empty record that cancels its offset, so every later record lands exactly where it did.
"""
from __future__ import annotations

import struct

from . import aescompact, cornermark, guidata, insert, layout, pagedraw, records, relocate

NUMBER_RAISE = 11               # px the voltage number moves up, in every state
GLYPH_FONT_SLOT = aescompact.CONTACTOR_GLYPH_FONT_SLOT
GLYPH_CHAR = aescompact.CONTACTOR_GLYPH_CHAR
GLYPH_WHITE_BGR = (0xFF, 0xFF, 0xFF)
GLYPH_MARGIN = 3                # px of body between the glyph's ink and the body's right/bottom edge
# The glyph box, relative to the pen the level fill leaves (body x0 + 2, body y1 - 2). Tuned
# against the render so the ink lands GLYPH_MARGIN px inside the body's bottom-right corner;
# `tests/test_batt_heat_battery_glyph.py` asserts the margin on both pages.
GLYPH_DX = 64
GLYPH_DY = -23
GLYPH_W = 24
GLYPH_H = 24

# The `outline` style (owner, 2026-10-05, replacing the glyph and the raised number): while the
# contactor is closed the battery's silhouette turns ORANGE. The first terminal's fillA becomes a
# mode-3 inline orange, which the second terminal and the body inherit (both mode 0), and a dark
# capsule in the first terminal's own stock colour (mode 7, the same colour-table cell) is laid
# OUTLINE_INSET px inside the body, leaving an orange ring. The inset equals the level fill's own
# (fill record x = 2, y = 48, h = -46 against the body's 50), so the ring reads the same width
# whether the fill is full or empty. The inset capsule sits between the body and the level fill,
# which writes its own mode-7 colour, so nothing after it inherits the dark or the orange; an empty
# record cancels its flow offset. The number stays where stock put it.
STYLES = ("glyph", "outline")
OUTLINE_RGB = (0xFF, 0x8C, 0x00)
OUTLINE_INSET = 2
_TERM_REC = 0
_BODY_REC = 2

# The House battery's record signature: terminal, terminal, body, level fill, slot-208 switch,
# number, 'V'. The op refuses any object that does not match, rather than guessing positions.
BATTERY_WTYPES = [33, 33, 33, 33, 6, 8, 0]
_FILL_REC = 3
_NUMBER_REC = 5


class BattGlyphError(ValueError):
    """A battery object this module refuses to rewrite."""


def _object_records(data: bytes, oi: int):
    gs, tab = guidata.guidata_extent(data)[0], guidata.object_tables(data)[1]
    return records.object_records(data, gs + tab[oi])


def battery_records(data: bytes, oi: int):
    """The battery's own seven records, whether or not this module has wrapped it: an object
    that is ONE wt6 switch is read through its first (default) arm, minus the glyph records.
    `add_voltage_battery.verify` reads the clone through this, so it still sees its cells."""
    recs = _object_records(data, oi)
    if len(recs) == 1 and recs[0].wtype == 6:
        recs = [r for r in _object_records(data, recs[0].child_index) if r.wtype != 11]
    return recs


def _check_battery(recs, oi: int) -> None:
    got = [r.wtype for r in recs]
    if got != BATTERY_WTYPES:
        raise BattGlyphError("object %d is not a House battery (record types %s, expected %s)"
                             % (oi, got, BATTERY_WTYPES))
    if recs[_NUMBER_REC].value_slot is None:
        raise BattGlyphError("object %d's number record has no value slot" % oi)


def _inset_record(data: bytes, recs) -> bytes:
    """The outline style's dark inner capsule: the body record moved OUTLINE_INSET px in on every
    side, coloured with the first terminal's own stock fillA (mode 7 cell), radius 2."""
    body, term = recs[_BODY_REC], recs[_TERM_REC]
    mode, value = next((m, v) for role, m, v in term.colors if role == "fillA")
    if mode != 7:
        raise BattGlyphError("battery terminal fillA is mode %d, not a mode-7 cell" % mode)
    frag = bytes(data[body.offset:body.offset + body.size])
    w, h = body.geom[2] - 2 * OUTLINE_INSET, body.geom[3] - 2 * OUTLINE_INSET
    for i, v in enumerate((OUTLINE_INSET, OUTLINE_INSET, w, h, 2)):
        frag = pagedraw._set_geom(frag, i, v)
    frag = pagedraw._set_mode(frag, "fillA", 7, struct.pack("<H", value))
    dec = records.decode_record(frag, 0)
    if not (dec.wtype == 33 and dec.size == len(frag) and dec.header[1] == 0x55
            and [layout._s16(g) for g in dec.geom[:5]] == [OUTLINE_INSET, OUTLINE_INSET, w, h, 2]
            and ("fillA", 7, value) in dec.colors):
        raise BattGlyphError("outline inset capsule failed its self-decode: %s" % frag.hex())
    return frag


def build_arm(data: bytes, recs, *, glyph: bool, style: str = "glyph",
              outline_rgb=OUTLINE_RGB) -> bytes:
    """One arm. `glyph` is the contactor state (the arm carries the mark).

    style "glyph": the battery's seven records verbatim, the number raised by NUMBER_RAISE, and --
    when `glyph` -- the white glyph plus its cancelling empty record after the level fill.
    style "outline": the seven records verbatim (number not raised) and -- when `glyph` -- the
    first terminal recoloured `outline_rgb` plus the inset dark capsule and its cancelling empty
    record after the body."""
    if style not in STYLES:
        raise BattGlyphError("unknown battery mark style %r" % (style,))
    outline = style == "outline"
    parts = []
    for j, r in enumerate(recs):
        raw = bytearray(data[r.offset:r.offset + r.size])
        if j == _NUMBER_REC and not outline:
            at = records.geom_offset(data, r, 1) - r.offset
            dy = layout._s16(r.geom[1]) - NUMBER_RAISE
            struct.pack_into("<h", raw, at, dy)
        if j == _TERM_REC and outline and glyph:
            raw = bytearray(pagedraw._set_colour(bytes(raw), "fillA", outline_rgb))
        parts.append(bytes(raw))
        if j == _BODY_REC and outline and glyph:
            parts.append(_inset_record(data, recs))
            parts.append(cornermark.build_empty_record(-OUTLINE_INSET, -OUTLINE_INSET))
        if j == _FILL_REC and glyph and not outline:
            parts.append(aescompact._red_glyph_record(GLYPH_DX, GLYPH_DY, GLYPH_W, GLYPH_H,
                                                      bgr=GLYPH_WHITE_BGR))
            parts.append(cornermark.build_empty_record(-GLYPH_DX, -GLYPH_DY))
    payload = cornermark._object(parts)
    chk = records.object_records(payload, 0)
    if not arm_ok(chk, recs, contactor=glyph, style=style, outline_rgb=outline_rgb):
        raise BattGlyphError("battery arm (%s, mark %s) failed its self-decode" % (style, glyph))
    return payload


def arm_ok(arm, recs, *, contactor: bool, style: str, outline_rgb=OUTLINE_RGB) -> bool:
    """Whether decoded arm `arm` is the battery `recs` drawn in `style` for this contactor state:
    the seven battery records in order, the number raised only in the glyph style, and exactly the
    mark records the state calls for."""
    if style == "glyph":
        body = [r for r in arm if r.wtype != 11]
        return ([r.wtype for r in body] == BATTERY_WTYPES
                and layout._s16(body[_NUMBER_REC].geom[1])
                == layout._s16(recs[_NUMBER_REC].geom[1]) - NUMBER_RAISE
                and sum(1 for r in arm if r.wtype == 11 and r.label == GLYPH_CHAR
                        and r.font == GLYPH_FONT_SLOT) == int(contactor))
    want = list(BATTERY_WTYPES)
    if contactor:
        want[_BODY_REC + 1:_BODY_REC + 1] = [33, 11]
    if [r.wtype for r in arm] != want:
        return False
    body = arm[:_BODY_REC + 1] + arm[_BODY_REC + 1 + 2 * contactor:]
    r, g, b = outline_rgb
    ink = ("fillA", 3, (r << 16) | (g << 8) | b) in body[_TERM_REC].colors
    return (ink == contactor
            and layout._s16(body[_NUMBER_REC].geom[1]) == layout._s16(recs[_NUMBER_REC].geom[1])
            and not any(r.wtype == 11 and r.label == GLYPH_CHAR for r in arm))


def wrap_battery(data: bytes, *, object_index: int, value_slot: int, style: str = "glyph",
                 outline_rgb=OUTLINE_RGB) -> tuple[bytes, dict]:
    """Rewrite battery object `object_index` into ONE enrolled wt6 switch on `value_slot` over
    four whole-battery arms keyed like `aescompact.BATT_HEAT_STATE_ARMS` (the contactor arms carry
    the `style` mark). Returns the new image and `{"arms_base", "arm_objects"}`."""
    recs = _object_records(data, object_index)
    _check_battery(recs, object_index)
    out, _ = relocate.grow_match_table(data, list(aescompact.BATT_HEAT_STATE_MATCH_VALUES))

    recs = _object_records(out, object_index)
    arms = [build_arm(out, recs, glyph=contactor, style=style, outline_rgb=outline_rgb)
            for _m, _h, contactor in aescompact.BATT_HEAT_STATE_ARMS]
    idxs = []
    for arm in arms:
        out, _e, i = insert._append_object(out, arm)
        idxs.append(i)
    if idxs != list(range(idxs[0], idxs[0] + len(arms))):
        raise BattGlyphError("battery arms did not land contiguously (%s)" % idxs)
    switch = aescompact._author_n_arm_switch(out, value_slot, idxs[0], len(arms))
    out, _ = relocate.replace_object_records(out, object_index, cornermark._object([switch]))
    return out, {"arms_base": idxs[0], "arm_objects": idxs}
