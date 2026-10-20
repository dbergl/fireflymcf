"""Re-encode a GuiData record at an absolute position, its inherited state made explicit.

`move_control` pastes a control's records onto another page as a new child object. There a
record no longer has the predecessors it laid out from (layout is flow-relative,
`FUN_00257BFC`), nor the font, align and value slot it inherited (`layout._State`). `absolute`
rebuilds one record so it stands alone:

- **Geometry.** Base modes `[0,0,1,1]` (header byte 1 = `0x50`): x, y absolute, and w, h
  pen-relative, added to the record's own x, y (`layout._base_ref` mode 1 for coords 2/3 is
  `c[0]`/`c[1]`). This is the coach-confirmed shape of `AddLowVoltsControl`'s `_title_record`,
  `_box_record`, `_label_record` and `_attach_record`; `[0,0,0,0]` drew nothing on the coach.
  geom[0..1] are (x, y); geom[2..3] are the walked box's width and height; geom[4..5] (radius,
  style) are kept.
- **Registers.** The R18-R20 register-index bytes are dropped and their header gates cleared,
  so the record neither reads nor writes `regx`/`regy`.
- **State.** `explicit_font` writes the walked font into R38; `reset_align` writes the walked
  align into the align bits; `value_slot` overwrites R41 or adds/overwrites R42.

`x, y` are the record's PEN, geom[0..1]. For most records that is the walked box's top-left;
a path-1 record (`layout._align_path1`) under a persistent align of 2 or 3, or a labelled
wtype 0 under one, has its box shifted from the pen by the align block
(`layout._placed_label_box`), and the caller places the pen accordingly.

Colour fields are copied as they are. A mode-0/5 field still inherits at the destination.

Every header bit used here is a gate in `records.decode_record` (`FUN_00252CB0`), cited
inline; the result is re-decoded and checked before it is returned.
"""
from __future__ import annotations

import struct

from . import records

SUPPORTED = frozenset({0, 4, 5, 6, 8, 11, 12, 13, 32, 33})


class Unsupported(ValueError):
    """The record's wtype is not re-encodable, or its header cannot carry a requested field."""


def _type_words(t: int) -> tuple[int, int]:
    # the decoder's type words: aed = 1<<t for t<32, aee = 1<<(t-32) otherwise
    return ((1 << t) if t < 32 else 0), ((1 << (t - 32)) if t >= 32 else 0)


def _u16(v: int, what: str) -> bytes:
    if not -0x8000 <= v <= 0xFFFF:
        raise ValueError("%s %d does not fit a u16 coordinate" % (what, v))
    return struct.pack("<H", v & 0xFFFF)


def carries_value_slot(wtype: int) -> bool:
    """Whether the decoder (`FUN_00252CB0`) gives `wtype` a value-slot field: R41 (always read
    when the type allows it) or R42 (header-gated), the gates `absolute` checks."""
    aed, aee = _type_words(wtype)
    return bool((aed & 0x2140) or (aee & 0x4000) or (aed & 0x00070C3F) or (aee & 0xF))


def absolute(data, rec: records.WRecord, placed, x: int, y: int, explicit_font: bool,
             reset_align: bool, value_slot: int | None) -> bytes:
    """The bytes of `rec` re-encoded with its pen at absolute (x, y), its box the size of
    `placed.box` (a `pagewalk.WalkRec` of `rec`), no register reads or writes, and the
    requested state explicit. wtype 5/6 keep their `child_index`.

    Raises `Unsupported` for a wtype outside `SUPPORTED`, for `explicit_font` on a wtype the
    decoder gives no R38 font field, and for `value_slot` on one with no R41/R42 field."""
    t = rec.wtype
    if t not in SUPPORTED:
        raise Unsupported("wtype %d is not re-encodable (supported: %s)"
                          % (t, sorted(SUPPORTED)))
    if placed.wtype != t:
        raise ValueError("placed record is wtype %d, the record wtype %d" % (placed.wtype, t))
    fresh = records.decode_record(data, rec.offset)
    if fresh.size != rec.size or fresh.header != rec.header:
        raise ValueError("record @ %#x does not decode as given" % rec.offset)
    aed, aee = _type_words(t)
    # R38 is read iff `(0x2000a961 & aed) or (aee & 0x6cc0)` (FUN_00252CB0)
    if explicit_font and fresh.font_off is None:
        raise Unsupported("wtype %d has no R38 font field (gate 0x2000a961/0x6cc0)" % t)
    # R41 is read iff `(aed & 0x2140) or (aee & 0x4000)`, always when the type allows it; R42
    # iff header b4 & 0x20 and `(aed & 0x70c3f) or (aee & 0xf)`. R40 (`aee & 0x2cf0`) gates
    # no supported wtype.
    has_r41 = bool((aed & 0x2140) or (aee & 0x4000))
    can_r42 = bool((aed & 0x00070C3F) or (aee & 0xF))
    if value_slot is not None and not (has_r41 or can_r42):
        raise Unsupported("wtype %d has no value_slot field (R41 gate 0x2140/0x4000, R42 gate "
                          "0x70c3f/0xf)" % t)

    o = rec.offset
    raw = bytes(data[o:o + rec.size])
    b0, b1, b2, b3, b4, b5, b6, b7 = raw[:8]

    # R18/R19/R20: one byte each, gated by b0 & 0x40, b0 & 0x80, b7 & 0x01
    n_aux = bool(b0 & 0x40) + bool(b0 & 0x80) + bool(b7 & 0x01)
    pre = raw[8:fresh.aux_off - o]
    mid = raw[fresh.aux_off - o + n_aux:fresh.geom_base_off - o]      # R21, R22
    # R23-R28: geom slot i is present iff its bit in `records._PEN_BITS` is set (b2 0x01-0x08,
    # b6 0x10/0x20), read in slot order, so the present slots are contiguous
    n_geom = sum(g is not None for g in fresh.geom)
    geom_end = fresh.geom_base_off - o + 2 * n_geom
    w = placed.box[2] - placed.box[0]
    h = placed.box[3] - placed.box[1]
    geom = _u16(x, "x") + _u16(y, "y")
    present = 0x03
    # an absent coord reads as 0 (`layout._s16(None)`): w/h are written when the record had
    # the slot or the size is not 0, so a size-0 attach keeps the template's `05 50 03` shape
    for i, v in ((2, w), (3, h)):
        if fresh.geom[i] is not None or v != 0:
            if v < 0:
                raise ValueError("placed box %r has a negative size" % (placed.box,))
            geom += _u16(v, "size")
            present |= 1 << i
    for i in (4, 5):
        if fresh.geom[i] is not None:
            geom += struct.pack("<H", fresh.geom[i])
    r42_present = bool(b4 & 0x20) and can_r42
    post = raw[geom_end:fresh.r42_off - o]
    r42 = raw[fresh.r42_off - o:fresh.r42_off - o + 2] if r42_present else b""
    tail = raw[fresh.r42_off - o + len(r42):]
    if value_slot is not None and can_r42:
        r42 = struct.pack("<H", value_slot)
        b4 |= 0x20                                    # R42 gate

    b0 &= ~0xC0 & 0xFF                                # drop R18/R19
    b7 &= ~0x01 & 0xFF                                # drop R20 (b7 bits 1-4: coord 4/5 modes)
    b1 = 0x50                                         # modes [0,0,1,1]: b1 & 3, >>2, >>4, >>6
    b2 = (b2 & 0xF0) | present                        # geom 0-3 present; R29-R32 bits kept
    if reset_align:
        if not 0 <= placed.align <= 3:
            raise ValueError("align %r is not 0-3" % (placed.align,))
        b3 = (b3 & 0x3F) | (placed.align << 6)        # align = b3 >> 6; b3 & 0x3f is colours
    out = bytearray(bytes([b0, b1, b2, b3, b4, b5, b6, b7]) + pre + mid + geom + post
                    + r42 + tail)

    dec = records.decode_record(out, 0)
    if explicit_font:
        out[dec.font_off] = 0xFF if placed.font is None else placed.font
    if value_slot is not None and has_r41:
        struct.pack_into("<H", out, dec.value_slot_off, value_slot)
    out = bytes(out)
    _check(out, rec, placed, x, y, w if present & 4 else 0, h if present & 8 else 0,
           explicit_font, reset_align, value_slot)
    return out


def _check(out: bytes, rec: records.WRecord, placed, x, y, w, h, explicit_font, reset_align,
           value_slot) -> None:
    """Re-decode `out` and confirm every field the re-encoding promised."""
    dec = records.decode_record(out, 0)
    want_font = (None if placed.font is None else placed.font) if explicit_font else rec.font
    problems = [name for name, ok in (
        ("size", dec.size == len(out)),
        ("wtype", dec.wtype == rec.wtype),
        ("base modes", dec.base_modes[:4] == [0, 0, 1, 1]
         and dec.base_modes[4:] == rec.base_modes[4:]),
        ("registers", not any(dec.in_regs) and not any(dec.out_regs)),
        ("geometry", [g or 0 for g in dec.geom[:4]] == [x & 0xFFFF, y & 0xFFFF, w, h]
         and dec.geom[4:] == rec.geom[4:]),
        ("font", dec.font == want_font),
        ("align", dec.align == (placed.align if reset_align else rec.align)),
        ("value_slot", dec.value_slot == (rec.value_slot if value_slot is None
                                          else value_slot)),
        ("child_index", dec.child_index == rec.child_index),
        ("label", dec.label == rec.label),
        ("colours", dec.colors == rec.colors),
        ("count", dec.count == rec.count),
    ) if not ok]
    if problems:
        raise ValueError("re-encoded record @ %#x fails its own decode: %s"
                         % (rec.offset, ", ".join(problems)))
