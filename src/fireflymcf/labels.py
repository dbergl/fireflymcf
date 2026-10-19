"""Grow one static length-prefixed label in place, repairing everything downstream.

`relocate()` alone cannot do this: the payload and its length byte have to land in the
SAME operation or the boundary oracle correctly refuses the result. So splice the bytes
by hand, rewrite the length byte and the text, then let `_apply_downstream_shift` fix
the four object tables, the header directory, the container offsets and GuiCode's
ldr-literals.

The insert length must be a multiple of 4 (all object offsets are 4-aligned), so `new`
is padded with TRAILING spaces up to the next boundary — invisible on these left-aligned
labels. Offsets are re-derived from `data` on every call, so growths compose. The result
is returned UNSEALED; the caller reseals (callers batch several growths per reseal).

Extracted from builds/from_stock.py (hardware-confirmed path: BUS-BASELINE PC.18).
"""
from fireflymcf import guidata, records, relocate


def grow_label(data: bytes, obj_index: int, old: str, new: str):
    """Returns (new_data_unsealed, padded_new_text, edits, at)."""
    gs, _ = guidata.guidata_extent(data)
    tab = guidata.object_tables(data)[1]
    rec = next(r for r in records.object_records(data, gs + tab[obj_index])
               if getattr(r, "label", None) == old)
    len_off = rec.label_offset - 1
    assert data[len_off] == len(old) and data[rec.label_offset + len(old)] == 0, \
        "%r at %#x is not a NUL-terminated length-prefixed field" % (old, rec.label_offset)
    assert len(new) > len(old), "%r does not grow %r -- use a same-size edit" % (new, old)
    new = new + " " * (-(len(new) - len(old)) % 4)
    grow = len(new) - len(old)
    assert grow > 0 and grow % 4 == 0, "grow of %d breaks 4-alignment" % grow
    ins_abs = rec.label_offset + len(old)
    at = ins_abs - gs
    buf = bytearray(data[:ins_abs] + b"\x00" * grow + data[ins_abs:])
    buf[len_off] = len(new)
    buf[rec.label_offset:rec.label_offset + len(new)] = new.encode("ascii")
    buf[rec.label_offset + len(new)] = 0
    edits = relocate._apply_downstream_shift(data, buf, at, grow)
    return bytes(buf), new, edits, at
