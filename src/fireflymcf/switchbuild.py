"""switchbuild -- the shared n-arm type-6 state switch author, absolute or flow-relative.

A type-6 switch is one record: a value slot, a `child_index` (the arms band's base object index,
`records.WRecord.child_index`) and a `count` (`records.WRecord.count`). The renderer's candidate
scan (`layout.Screen._switch_child`, `layout.py` ~434) reads a FIXED-POSITION band
`[arms_base, arms_base+count)` off the match table -- "a switch's candidates are a fixed-position
band read off the match table" (`CLAUDE.md`) -- and returns the first entry in the band whose match
value equals the read slot's value; on no match it falls back to `arms_base` itself ONLY IF THAT
ENTRY'S OWN match value is `0xFFFF` (there is no scan-the-band-for-0xFFFF fallback). So the arm
that is meant to be the switch's default must be placed FIRST in the band -- at `arms_base`, i.e.
`arm_objects[0]` / `match_values[0]` in `append_arms` -- or an unmatched value renders nothing
instead of the intended default.

`author_switch` builds the switch record itself, either flow-relative (the original v14-v32 shape:
header byte 1 `0x55`, base modes `[1,1,1,1]`, geometry `(0, 0)`) or absolute (header byte 1 `0x50`,
base modes `[0,0,1,1]`, geometry `(x, y)`). `append_arms` grows the match table and appends the arm
objects that make up the band, in one call, returning the base index `author_switch`'s `arms_base`
argument expects.

The donor (object 9, record 9 -- the AES enable-pill switch, first repurposed for the Pump Link
button in v14) and its stock bytes are pinned here; `aescompact` re-exports the names so existing
imports keep working.
"""

from __future__ import annotations

import struct

from . import guidata, insert, records, relocate

# Object indices (object-table 1). The AES enable-pill switch -- stock donor for every authored
# n-arm switch in this project (v14 Pump Link onward).
LINK_SWITCH_DONOR = 9
LINK_SWITCH_REC = 9                         # 9[9]: wtype-6, slot 11, band 173+6
LINK_SWITCH_STOCK = bytes.fromhex("0650030020000000f4015e01ff0b000600ad00")


def _object_records(data: bytes, index: int) -> list[records.WRecord]:
    """Decode object `index`'s record stream via the standard accessor recipe."""
    gs, _ = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    return records.object_records(data, gs + table[index])


def _record_bytes(data: bytes, index: int, j: int) -> bytes:
    """The raw byte span of record `j` of object `index`."""
    r = _object_records(data, index)[j]
    return bytes(data[r.offset:r.offset + r.size])


def author_switch(data: bytes, *, value_slot: int, arms_base: int, count: int,
                  at: "tuple[int, int] | None" = None) -> bytes:
    """A `count`-arm type-6 state switch: the stock enable-pill switch (9[9]), reading
    `value_slot` and selecting the `count`-object band at `arms_base`. The record shape is
    identical for any arm count; only the R45 count field, and (for an absolute placement) the
    geometry, differ. The match-table values the arms were appended with live in the match table
    (`append_arms`), not here.

    `at=None` (the default): flow-relative at the flow origin -- header byte 1 `0x55` (base modes
    `[1,1,1,1]`), x = y = 0, i.e. the box the donor replaces drew at the flow origin. This is
    byte-identical to every switch this project authored before this module existed (v14 Pump Link
    through v32 Batt Heat state) -- `aescompact._author_n_arm_switch` is now a thin wrapper over
    this branch.

    `at=(x, y)`: absolute placement -- header byte 1 `0x50` (base modes `[0,0,1,1]`: x/y absolute,
    w/h relative, matching the donor's own w/h mode), geom[0..1] = x, y.
    """
    rec = _object_records(data, LINK_SWITCH_DONOR)[LINK_SWITCH_REC]
    raw = _record_bytes(data, LINK_SWITCH_DONOR, LINK_SWITCH_REC)
    if raw != LINK_SWITCH_STOCK:
        raise ValueError(
            f"object {LINK_SWITCH_DONOR} record {LINK_SWITCH_REC} is {raw.hex()}, not the stock "
            "enable-pill switch this route was derived against")
    frag = bytearray(raw)
    x, y = (0, 0) if at is None else at
    frag[1] = 0x55 if at is None else 0x50
    struct.pack_into("<H", frag, records.geom_offset(data, rec, 0) - rec.offset, x)
    struct.pack_into("<H", frag, records.geom_offset(data, rec, 1) - rec.offset, y)
    struct.pack_into("<H", frag, records.value_slot_offset(data, rec) - rec.offset, value_slot)
    ci = records.child_index_offset(data, rec) - rec.offset
    struct.pack_into("<H", frag, ci, arms_base)
    struct.pack_into("<H", frag, ci - 2, count)            # R45 count precedes R48 child for wtype 6
    chk = records.decode_record(bytes(frag), 0)
    want_modes = [1, 1, 1, 1] if at is None else [0, 0, 1, 1]
    if not (chk.wtype == 6 and chk.value_slot == value_slot and chk.child_index == arms_base
            and chk.count == count and chk.base_modes[:4] == want_modes
            and chk.geom[0] == x and chk.geom[1] == y and chk.size == len(frag)
            and chk.header[4] & 0x20):
        raise ValueError(
            f"authored switch failed its self-decode (wt={chk.wtype} slot={chk.value_slot} "
            f"child={chk.child_index} count={chk.count} modes={chk.base_modes[:4]} "
            f"geom={chk.geom[:2]})")
    return bytes(frag)


def append_arms(data: bytes, arm_objects: list[bytes], match_values: list[int]) -> tuple[bytes, int]:
    """Grow the match table with `match_values` and append `arm_objects` as one contiguous band,
    returning `(image, arms_base)` for `author_switch`'s `arms_base` argument.

    `match_values[i]` is the match value for `arm_objects[i]`. The arm whose match value is
    `0xFFFF` -- the switch's default -- MUST be `arm_objects[0]` / `match_values[0]`, landing at
    `arms_base` itself: `layout.Screen._switch_child` only falls back to the base entry when ITS
    OWN match value is `0xFFFF` (it never scans the rest of the band looking for one). Order the
    two lists with that arm first.

    `match_values` is padded to an even length with a trailing `0xFFFF` sentinel before growing the
    table -- `relocate.grow_match_table` requires an even, non-empty count (object offsets are
    4-aligned) -- and that padding entry covers no arm and costs nothing (`grow_match_table`'s own
    docstring: an uncovered index is either the format's own "no match" sentinel or, if an object
    ever lands there, its own default-arm marker).
    """
    values = list(match_values)
    if len(values) % 2:
        values.append(0xFFFF)
    out, _edits = relocate.grow_match_table(data, values)
    idxs = []
    for obj in arm_objects:
        out, _edits, i = insert._append_object(out, obj)
        idxs.append(i)
    if idxs != list(range(idxs[0], idxs[0] + len(arm_objects))):
        raise ValueError("switch arms did not land contiguously (%s)" % idxs)
    return out, idxs[0]
