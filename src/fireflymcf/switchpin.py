"""Pin a type-6 switch to ONE arm, whatever its value slot holds.

Why (2026-09-04). `INVERTER / CHARGER SETTINGS`'s `Absorb`/`Float` rows (object 303) hang off
object 17 record 11, a wt-6 switch on value slot 23 -- a cell nothing writes (`GUICODE.md`
Findings 63/64/67), so whether the rows draw is decided by power-up SRAM: present after a cold
power cycle, absent after a flash (`BUS-BASELINE.md` PC.55). Making them permanent is a
record edit, and the firmware's own arm-selection rule says which one.

The rule (`layout.Screen._switch_child`, a replay of `FUN_00256C54` `0x00257B3E`-`0x00257B6E`,
`proven-from-code`): scan exactly `count` arms from `child_base` for the first whose match
value equals the slot's value; on no match, draw the BASE arm only if the base's match value is
`0xFFFF`, else draw nothing. So a switch with `child_base = arm`, `count = 1` and
`match[arm] = 0xFFFF` can never select anything but `arm`: no byte or halfword value equals
`0xFFFF` as a match, so the scan always misses and always falls back to the base, which is the
arm. Three same-size edits -- the record's `child_index` u16, its `count` u16, and one u16 in the
match table -- guarded on the stock values and verified by re-walking the screen for several slot
values afterwards.

The match table holds one u16 per OBJECT, so writing `match[arm]` is only safe when no OTHER
switch's band covers `arm` (it would change that switch's behaviour too); `pin_switch_arm`
refuses in that case rather than sharing the edit.
"""
from __future__ import annotations

import struct

from . import guidata, layout, records

MATCH_DEFAULT = 0xFFFF


class SwitchPinError(Exception):
    pass


def _record(data: bytes, obj: int, rec_index: int):
    gs, _ = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    if not 0 <= obj < len(table):
        raise SwitchPinError(f"object {obj} is outside the object table (0..{len(table) - 1})")
    recs = records.object_records(data, gs + table[obj])
    if not 0 <= rec_index < len(recs):
        raise SwitchPinError(f"object {obj} has {len(recs)} records, no record {rec_index}")
    return gs, table, recs[rec_index]


def other_switches_covering(data: bytes, arm: int, *, skip=()) -> list:
    """`[(object, record)]` of every wt-6 switch whose band includes `arm`, except `skip`."""
    gs, _ = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    hits = []
    for obj in range(len(table)):
        for i, r in enumerate(records.object_records(data, gs + table[obj])):
            if r.wtype != 6 or r.child_index is None or not r.count:
                continue
            if r.child_index <= arm < r.child_index + r.count and (obj, i) not in skip:
                hits.append((obj, i))
    return hits


def pin_switch_arm(data: bytes, obj: int, rec_index: int, arm: int) -> tuple[bytes, list]:
    """Make switch (`obj`, `rec_index`) draw `arm` for every slot value. Returns `(image,
    edits)`; edits are `(file_off, old, new, note)`; the image is NOT resealed."""
    gs, table, rec = _record(data, obj, rec_index)
    if rec.wtype != 6:
        raise SwitchPinError(f"object {obj} record {rec_index} is wtype {rec.wtype}, not a switch (6)")
    if rec.child_index is None or rec.count is None or rec.child_index_off is None \
            or rec.count_off is None:
        raise SwitchPinError(f"object {obj} record {rec_index} carries no child_index/count")
    if not rec.child_index <= arm < rec.child_index + rec.count:
        raise SwitchPinError(
            f"arm {arm} is outside the switch's band [{rec.child_index}, "
            f"{rec.child_index + rec.count}); pinning an arm the switch never offered is not this "
            "edit")
    others = other_switches_covering(data, arm, skip=((obj, rec_index),))
    if others:
        raise SwitchPinError(
            f"object {arm} is also an arm of switch(es) {others}; its match value is shared and "
            "cannot be pinned for one switch alone")
    scr = layout.Screen(data)
    if scr.match_table_off is None:
        raise SwitchPinError("no match table located in this image")
    match_off = gs + scr.match_table_off + arm * 2
    out = bytearray(data)
    edits = []

    def put_u16(off: int, value: int, note: str):
        old = bytes(out[off:off + 2])
        new = struct.pack("<H", value)
        if old != new:
            out[off:off + 2] = new
            edits.append((off, old, new, note))

    put_u16(rec.child_index_off, arm,
            f"object {obj} record {rec_index}: switch child_base {rec.child_index} -> {arm}")
    put_u16(rec.count_off, 1,
            f"object {obj} record {rec_index}: switch count {rec.count} -> 1")
    put_u16(match_off, MATCH_DEFAULT,
            f"match[{arm}] {scr._match_value(arm):#x} -> 0xFFFF: the no-match default, so the "
            f"scan always falls back to {arm}")
    return bytes(out), edits


def pinned_to(data: bytes, obj: int, rec_index: int) -> int | None:
    """The arm a switch is pinned to, or None if it still depends on its slot."""
    gs, table, rec = _record(data, obj, rec_index)
    if rec.wtype != 6 or rec.count != 1 or rec.child_index is None:
        return None
    scr = layout.Screen(data)
    if scr._match_value(rec.child_index) != MATCH_DEFAULT:
        return None
    return rec.child_index
