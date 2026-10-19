"""Captioned live readouts drawn inside a cardlayout card (v49: the ELECTRICAL Inverter card --
spec its design note (not published)).

One private object holds, per row: a white caption (`cardlayout.title_record`, font 5), a number
directive and a wtype-8 readout (`readout.author_readout_object`'s two records, the readout's font
set to 12), and a white unit label. Every record is absolute, so the rows sit where they are
authored whatever the flow origin.

The object is attached to 829 by a plain wtype-5 container inserted DIRECTLY AFTER cardlayout's
four titles: after every card fill (the panel paints in stream order, so nothing covers the rows)
and before every v47 record. Its persistent state (font, align, colour, the directive) is
therefore followed by v47's own records; tests/test_card_readouts.py pins that no stock control or
readout draws differently and that the frame still ends in v47's state.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from . import aescompact, cardlayout, guidata, insert, readout, records, relocate

CAPTION_FONT, CAPTION_H, CAPTION_W = 10, 17, 102    # font 10: full ASCII (5 lacks lowercase)
VALUE_FONT = 12
VALUE_TOP = 16             # font 12's cell top: a wtype-8's y is its baseline pen
UNIT_W, UNIT_H = 16, 19     # 19, not the font's 21: keeps the 4th row inside the card at pitch 34
ATTACH_AT = 8              # after cardlayout's 4 cards + 4 titles
PLAIN_CONTAINER_CHILD = 393   # the Auto Off chrome's container: a plain flow-relative wtype-5


class CardReadoutsError(ValueError):
    pass


@dataclass(frozen=True)
class Row:
    caption: str
    slot: int
    width: int
    places: int
    unit: str


def _gs_tab(data: bytes):
    return guidata.guidata_extent(data)[0], guidata.object_tables(data)[1]


def _value_records(data: bytes, x: int, y: int, slot: int, width: int, places: int) -> list[bytes]:
    """The (directive, readout) pair, the readout's font set to VALUE_FONT."""
    payload = readout.author_readout_object(data, x=x, y=y, display_slot=slot,
                                            directive=readout.directive_bytes(width, places))
    d, r = records.object_records(payload, 0)
    rfrag = bytearray(payload[r.offset:r.end])
    off = records.font_offset(payload, r)
    if off is None:
        raise CardReadoutsError("the readout donor carries no font selector to set")
    rfrag[off - r.offset] = VALUE_FONT
    chk = records.decode_record(bytes(rfrag), 0)
    if not (chk.wtype == 8 and chk.font == VALUE_FONT and chk.value_slot == slot):
        raise CardReadoutsError("readout record failed its self-decode after the font change")
    return [bytes(payload[d.offset:d.end]), bytes(rfrag)]


def build_object(data: bytes, rows: list[Row], *, x_caption: int = 684, pen_x: int = 760,
                 unit_x: int = 763, y0: int = 78, pitch: int = 34) -> bytes:
    """`<count>` + per row (caption, directive, readout, unit), padded to 4.

    Pitch 34 is the minimum: a font-12 value fills `pen_y-16 .. pen_y+4`, and an enrolled readout
    repaints on its own, so that fill must clear the NEXT row's caption ink too (at 33 it shaved
    the caption's top row on every repaint)."""
    parts: list[bytes] = []
    for k, row in enumerate(rows):
        top = y0 + k * pitch
        value_top = top + CAPTION_H
        parts.append(cardlayout.title_record(x_caption, top, row.caption, font=CAPTION_FONT,
                                             w=CAPTION_W, h=CAPTION_H))
        parts += _value_records(data, pen_x, value_top + VALUE_TOP, row.slot, row.width,
                                row.places)
        parts.append(cardlayout.title_record(unit_x, value_top, row.unit, font=VALUE_FONT,
                                             w=UNIT_W, h=UNIT_H))
    payload = bytes([len(parts)]) + b"".join(parts)
    payload += b"\x00" * (-len(payload) % 4)
    if len(records.object_records(payload, 0)) != len(parts):
        raise CardReadoutsError("authored readouts object failed its self-decode")
    return payload


def check_frame(data: bytes) -> None:
    """829 must be cardlayout's frame: four absolute cards, then the four titles."""
    gs, tab = _gs_tab(data)
    frame = records.object_records(data, gs + tab[aescompact.ELECTRICAL_AES_BODY])
    cards = frame[:4]
    titles = [r.label for r in frame[4:8]]
    if not (len(frame) > ATTACH_AT
            and all(r.wtype == 33 and r.colors and
                    r.colors[0] == ("fillA", 7, cardlayout.CARD_FILL_INDEX) for r in cards)
            and titles == list(cardlayout.TITLE_TEXTS)):
        raise CardReadoutsError(
            "object %d is not aes_card_layout's frame (cards then titles); run aes_card_layout "
            "first" % aescompact.ELECTRICAL_AES_BODY)


def attach(data: bytes, obj_bytes: bytes) -> tuple[bytes, int]:
    """Append the object and insert a plain wtype-5 attach for it at 829[ATTACH_AT]."""
    check_frame(data)
    out, _edits, index = insert._append_object(data, obj_bytes)
    gs, tab = _gs_tab(out)
    body = aescompact.ELECTRICAL_AES_BODY
    frame = records.object_records(out, gs + tab[body])
    plain = [r for r in frame if r.wtype == 5 and r.child_index == PLAIN_CONTAINER_CHILD]
    if len(plain) != 1 or plain[0].value_slot is not None:
        raise CardReadoutsError("no plain wtype-5 template (the Auto Off chrome container) in 829")
    t = plain[0]
    rec = bytearray(out[t.offset:t.end])
    struct.pack_into("<H", rec, records.child_index_offset(out, t) - t.offset, index)
    chk = records.decode_record(bytes(rec), 0)
    if not (chk.wtype == 5 and chk.child_index == index and chk.value_slot is None
            and all(g is None for g in chk.geom[:4])):
        raise CardReadoutsError("authored attach record failed its self-decode")
    stream = [out[r.offset:r.end] for r in frame]
    stream.insert(ATTACH_AT, bytes(rec))
    out, _ = relocate.replace_object_records(out, body, bytes([len(stream)]) + b"".join(stream))
    return out, index
