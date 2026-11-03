"""Re-encode one GuiData record with a field ADDED or changed, and swap it into its object.

Two edits the same-size ops cannot make, because the field they need is not on the record:

- **A colour field's mode and value** (`with_color`). A colour field is 3 mode bits in the header
  (fillA: byte 3 bits 0-2, fillB: byte 3 bits 3-5, fillA2: byte 5 bits 1-3, fillB2: byte 5
  bits 4-6) and, for modes 3/6/7, an inline value the decoder `FUN_00252CB0` reads at the colour
  block's cursor, in field order (`records.decode_record`'s `colour()`): mode 3 a BGR u24
  (bytes B, G, R), mode 6 a palette u8, mode 7 a colour-table u16. Modes 0/1/2/5 carry no value
  (GUICODE Findings 28/38: 0 inherit, 1 the literal at GuiCode `0x2535AC`, 2 white, 5
  transparent). Changing between valueless modes is a header edit; to a valued mode, the value's
  bytes are inserted at the field's cursor.
- **A rounded shape from a plain fill** (`with_wtype_radius`). wtype 4 (fill) and 0x21 (capsule)
  decode the same fields (both in colour block A only, neither in block B; every tail gate in
  `decode_record` treats them alike), so the type byte can change. The capsule's corner radius is
  geometry slot 4, gated by header byte 6 bit `0x10` and read after slots 0-3 (`_PEN_BITS`);
  when absent it is inserted there, base mode 0 (header byte 7 bits 1-2 clear: absolute).

Every result is re-decoded and compared field by field with the original (`_check`): only what
was asked changes. `replace_records` then swaps the object's record stream through
`relocate.replace_object_records`, which shifts every downstream reference and reruns the
relocation gates.
"""
from __future__ import annotations

import struct

from . import guidata, patch, records, relocate, theme

ROLES = ("fillA", "fillA2", "fillB", "fillB2")
VALUE_SIZE = {3: 3, 6: 1, 7: 2}
# role -> (header byte, shift) of its 3 mode bits
_MODE_BITS = {"fillA": (3, 0), "fillB": (3, 3), "fillA2": (5, 1), "fillB2": (5, 4)}


class RecordEditError(ValueError):
    pass


class PointerInside(RecordEditError):
    pass


def _colour_block_start(data: bytes, rec: records.WRecord) -> int:
    """The decoder's cursor where the colour fields begin: after the 8-byte header, the R9-R10
    clip geometry (header byte 7 bit 0x20) and the R11-R12 colour-mode bytes (bit 0x40)."""
    p = rec.offset + 8
    b7 = rec.header[7]
    if b7 & 0x20:
        m = data[p]
        p += 1 + 2 * bin(m & 0xF).count("1")
    if b7 & 0x40:
        p += 2
    return p


def color_cursor(data: bytes, rec: records.WRecord, role: str) -> tuple[int, int]:
    """(file offset, current value size) of `role`'s inline value on `rec`, present or not."""
    names = [c[0] for c in rec.colors]
    if role not in names:
        raise RecordEditError("wtype %d record @ %#x has no %s field (the type or its colour-mode "
                              "bytes gate it out)" % (rec.wtype, rec.offset, role))
    p = _colour_block_start(data, rec)
    for (name, mode, _), off in zip(rec.colors, rec.color_off):
        if off is not None and off != p:
            raise RecordEditError("colour cursor replay disagrees with the decoder at %#x" % p)
        if name == role:
            return p, VALUE_SIZE.get(mode, 0)
        p += VALUE_SIZE.get(mode, 0)
    raise AssertionError("unreachable")


def encode_value(mode: int, value) -> bytes:
    if mode == 3:
        r, g, b = value
        return bytes([b, g, r])
    if mode == 6:
        return bytes([value])
    if mode == 7:
        return struct.pack("<H", value)
    if value is not None:
        raise RecordEditError("mode %d carries no value" % mode)
    return b""


def with_color(data: bytes, rec: records.WRecord, role: str, mode: int, value=None) -> bytes:
    """`rec`'s bytes with `role` set to `mode` (and its inline `value`)."""
    if role not in _MODE_BITS:
        raise RecordEditError("role %r is not one of %s" % (role, ROLES))
    if mode not in (0, 1, 2, 3, 5, 6, 7):
        raise RecordEditError("colour mode %r is not one the decoder reads" % mode)
    at, old_size = color_cursor(data, rec, role)
    raw = bytearray(data[rec.offset:rec.end])
    byte, shift = _MODE_BITS[role]
    raw[byte] = (raw[byte] & ~(7 << shift)) | (mode << shift)
    rel = at - rec.offset
    out = bytes(raw[:rel]) + encode_value(mode, value) + bytes(raw[rel + old_size:])
    _check(data, rec, out, colors={role: (mode, _decoded_value(mode, value))})
    return out


def with_wtype_radius(data: bytes, rec: records.WRecord, wtype: int, radius: int) -> bytes:
    """`rec`'s bytes as `wtype` with corner radius `radius` (geometry slot 4)."""
    if {rec.wtype, wtype} - {0x04, 0x21}:
        raise RecordEditError("only fill (4) <-> capsule (0x21) is checked to decode alike; "
                              "got %#x -> %#x" % (rec.wtype, wtype))
    if not 0 <= radius <= 0xFFFF:
        raise RecordEditError("radius %r out of range" % radius)
    raw = bytearray(data[rec.offset:rec.end])
    raw[0] = (raw[0] & 0xC0) | wtype
    if rec.geom[4] is None:
        if rec.header[7] & 0x06:
            raise RecordEditError("header byte 7 gives geometry slot 4 a base mode; refusing")
        raw[6] |= 0x10
        # slot 4 follows slots 0-3 in the R23-R28 loop
        after = [o for o in rec.geom_off[:4] if o is not None]
        at = (max(after) + 2 if after else rec.geom_base_off) - rec.offset
        raw = raw[:at] + struct.pack("<H", radius) + raw[at:]
        if rec.geom[5] is not None:
            raise RecordEditError("record carries geometry slot 5; insert order unverified")
    else:
        off = rec.geom_off[4] - rec.offset
        struct.pack_into("<H", raw, off, radius)
    out = bytes(raw)
    _check(data, rec, out, wtype=wtype, radius=radius)
    return out


def _decoded_value(mode, value):
    """The value `records.decode_record` reports for an encoded field: mode 3 reads the bytes
    B, G, R as a little-endian u24, i.e. 0xRRGGBB."""
    if mode == 3:
        r, g, b = value
        return r << 16 | g << 8 | b
    return value if mode in (6, 7) else None


def _check(data, rec, out, colors=None, wtype=None, radius=None):
    """Decode `out` standalone and require it equal to `rec` except for the asked change."""
    buf = bytes(data[:rec.offset]) + out + bytes(data[rec.end:])
    new = records.decode_record(buf, rec.offset)
    if new.size != len(out):
        raise RecordEditError("re-encoded record decodes to %d bytes, wrote %d"
                              % (new.size, len(out)))
    want_colors = [(r, *(colors[r] if colors and r in colors else (m, v)))
                   for r, m, v in rec.colors]
    want_geom = list(rec.geom)
    if radius is not None:
        want_geom[4] = radius
    checks = {
        "wtype": (new.wtype, wtype if wtype is not None else rec.wtype),
        "colors": (list(new.colors), want_colors),
        "geom": (list(new.geom), want_geom),
        "label": (new.label, rec.label),
        "font": (new.font, rec.font),
        "value_slot": (new.value_slot, rec.value_slot),
        "child_index": (new.child_index, rec.child_index),
        "base_modes": (list(new.base_modes), list(rec.base_modes)),
        "align": (new.align, rec.align),
        "count": (new.count, rec.count),
        "fmt": (new.fmt, rec.fmt),
        "h_align": (new.h_align, rec.h_align),
        "v_align": (new.v_align, rec.v_align),
        "line_height": (new.line_height, rec.line_height),
        "corner_mask": (new.corner_mask, rec.corner_mask),
    }
    bad = {k: v for k, v in checks.items() if v[0] != v[1]}
    if bad:
        raise RecordEditError("re-encoded record @ %#x differs beyond the edit: %r"
                              % (rec.offset, bad))


def replace_records(data: bytes, obj: int, new: dict[int, bytes]):
    """Swap records of object `obj` (index -> new bytes) and relocate. Returns (sealed, edits)."""
    gs, _ = guidata.guidata_extent(data)
    tab = guidata.object_tables(data)[1]
    start = gs + tab[obj]
    recs = records.object_records(data, start)
    body = bytearray(data[start:recs[0].offset])          # the count byte
    for i, r in enumerate(recs):
        body += new.get(i, data[r.offset:r.end])
    unknown = set(new) - set(range(len(recs)))
    if unknown:
        raise RecordEditError("object %d has no record(s) %s" % (obj, sorted(unknown)))
    if all(len(b) == recs[i].size for i, b in new.items()):
        # same size: nothing moves, so no relocation and no pointer hazard
        buf = bytearray(data)
        edits = []
        for i, b in new.items():
            r = recs[i]
            edits.append(relocate.Edit(r.offset, bytes(data[r.offset:r.end]), b, "recipe",
                                       "object %d record %d re-encoded" % (obj, i)))
            buf[r.offset:r.end] = b
        out, _ = patch.reseal(bytes(buf))
        return out, edits
    # A literal into the object past the first changed record would name a moved position:
    # `replace_object_records` shifts references at or after the object BOUNDARY only.
    first = min(recs[i].offset for i in new) - gs
    obj_end = recs[-1].end - gs
    for section in ("GuiCode", "LCD"):
        for pos, rel in theme.section_guidata_pointers(data, section):
            if first < rel < obj_end:
                raise PointerInside("%s literal @ %#x points at GuiData+%#x, inside object %d "
                                    "after its first edited record" % (section, pos, rel, obj))
    out, edits = relocate.replace_object_records(data, obj, bytes(body))
    got = records.object_records(out, gs + guidata.object_tables(out)[1][obj])
    if len(got) != len(recs):
        raise RecordEditError("object %d decodes to %d records after the swap, was %d"
                              % (obj, len(got), len(recs)))
    for i, b in new.items():
        if out[got[i].offset:got[i].end] != b:
            raise RecordEditError("object %d record %d did not land as written" % (obj, i))
    return out, edits
