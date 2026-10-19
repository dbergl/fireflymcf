"""A state-gated mark in the corner of an existing button.

The mechanism is the firmware's own, not a new one: a **wtype-6 value-match switch** appended
to a button object's record stream, selecting between an object that draws one character and an
object that draws nothing. Stock uses exactly this shape for the HOME pump/heater pair
(`OBJECT-HIERARCHY.md` §3), the Pump Link button (`aescompact`) and the `Absorb`/`Float` rows.

Three things here are deliberate and each avoids a trap this project has already paid for.

**Each arm is FLOW-NEUTRAL, and this is the part that was wrong first.** Every text widget type
is in the layout flow gate (`layout._in_flow_gate`: types 0, 8, 11 and 13 all commit their own
coordinates as the new flow origin), and the flow a child object leaves carries back out to the
parent. The first cut here placed the mark at ABSOLUTE screen coordinates, reasoning that a
corner has a position of its own; the layout re-walk showed the consequence immediately -- HOME's
`Tank Heater`, which is the PARENT object's next record, moved to `(164, 23)` with the empty arm
drawn and `(735, 241)` with the mark drawn, i.e. it was reading the arm's coordinates as its
origin.

So the mark is placed **relative to the host's own flow origin** (`dx`, `dy`), and each arm ends
with an empty label that puts the origin back exactly where it was: `(-dx, -dy)` after the mark,
`(0, 0)` in the arm that draws nothing. Both arms therefore leave the flow byte-identical to a
build without the switch, whichever one is selected -- which is what makes this edit safe to
attach to a SHARED child object like HOME's 295. Relative placement is also the better answer on
its own terms: move the button and the mark follows it.

This is the trap `CLAUDE.md` states as "an inserted record advances the pen by its own x, and the
only trustworthy check is a layout re-walk of the finished image". It was stated, and it still
had to be paid for a second time; the re-walk is in `recipe_ops.AddCornerMark.verify`.

**The DEFAULT ARM IS THE BASE.** On no match, `layout._resolve_switch` (transcribing GUICODE
Finding 16) renders the base only when `match[base] == 0xFFFF`, and otherwise renders NOTHING.
The gate here is "draw when the cell is anything but 0", which has far too many values to
enumerate, so the mark MUST be the `0xFFFF` default at the base and the empty object the
`match == 0` arm after it. Built the other way round the mark simply never appears.

**The mark inherits the host caption's font rather than setting its own.** The R38 font selector
is persistent -- it changes what every later record in the walk inherits until something sets it
again (`CLAUDE.md`, the same-size-edit table). A mark that switched to its own font would leak
that font into whatever the walk reaches next, and would leak it *conditionally*, since the arm
only draws sometimes. Installing the glyph into the font the host caption already uses makes the
selector a no-op write of the value already in force.
"""
from __future__ import annotations

import struct

from . import guidata, insert, layout, records, relocate

# In band order. The mark is the base so it is the `0xFFFF` no-match default; the empty arm
# takes the one value that means "off". See the module docstring.
MATCH_VALUES = [0xFFFF, 0]


class CornerMarkError(ValueError):
    """A mark this module refuses to build or attach."""


def build_mark_record(dx: int, dy: int, w: int, h: int, font_slot: int, char: str) -> bytes:
    """One wtype-11 label drawing `char` in a `w` x `h` box at `(dx, dy)` from the host's flow
    origin.

    The byte layout is `aescompact._flow_anchor`'s with two differences: the length-prefixed label
    carries one byte instead of none, and header byte 2 declares **all four** coordinates (`0x0f`)
    rather than just the two.

    **THE BOX IS NOT OPTIONAL, and leaving it out is how the first flashed build failed.** The
    anchor declares coordinates 0 and 1 only, because an invisible record needs no box; copying
    that shape for a VISIBLE one gave the mark a degenerate box -- the layout walk reported
    `(571, 218, 571, 218)`, x0 == x1 -- and the panel drew a single column of the glyph. The
    owner's second look ("only the very edge, next to the r in Water, one column of pixels") is
    what named it. `AddCornerMark.verify` checked WHERE the mark landed and never that it had a
    width to land in; it now asserts the box too.

    **What that does and does not establish** -- sharpened 2026-09-06 by measuring the corpus,
    because the first write-up of it said "the panel clips text to the record box", which is
    false. A **wtype-11/13** record is laid out INSIDE a declared box, and one carrying text with
    no width is a shape stock never ships: across the three builds 5,209 declare a width and the
    only two that do not are the empty flow anchors. A **wtype-0** record (a single character
    drawn at the pen) needs no box at all -- HOME's `V` and `°` have 0x0 placed boxes and draw in
    full on the coach. So the rule is about the record TYPE's layout model, not about clipping in
    general; `render._boxless_text_record` carries the same statement.
    """
    if len(char) != 1:
        raise CornerMarkError(f"char must be exactly one character, got {char!r}")
    if not 0 <= font_slot <= 0xFE:
        raise CornerMarkError(f"font slot {font_slot} is not a slot byte")
    try:
        raw = char.encode("latin-1")
    except UnicodeEncodeError:
        raise CornerMarkError(
            f"char {char!r} is not a single byte; a label is a byte string the text drawer "
            f"indexes the font with, so only U+0000..U+00FF can name a glyph") from None

    if w <= 0 or h <= 0:
        raise CornerMarkError(f"the mark's box must be positive, got {w} x {h}")
    rec = (bytes([0x0B, 0x55, 0x0F, 0x00, 0x00, 0x00, 0x00, 0x00])
           + struct.pack("<hhhh", dx, dy, w, h)
           + bytes([0x01, font_slot, 0xFF, 0x02, 0x02, 0x0A, 0x00])
           + bytes([len(raw)]) + raw + b"\x00")
    dec = records.decode_record(rec, 0)
    if not (dec.wtype == 11 and dec.label == char and dec.size == len(rec)
            and dec.font == font_slot and dec.base_modes[:4] == [1, 1, 1, 1]
            and layout._s16(dec.geom[0]) == dx and layout._s16(dec.geom[1]) == dy
            and dec.geom[2] == w and dec.geom[3] == h):
        raise CornerMarkError(
            f"authored mark record failed its self-decode (wt={dec.wtype} label={dec.label!r} "
            f"font={dec.font} modes={dec.base_modes[:2]} geom={dec.geom[:2]} "
            f"size={dec.size}/{len(rec)})")
    return rec


def build_empty_record(dx: int = 0, dy: int = 0) -> bytes:
    """A record that draws nothing and moves the flow origin by `(dx, dy)`: an empty wtype-11
    label, exactly `aescompact._flow_anchor`. Zero-length text draws no pixels, but it IS in the
    flow gate, which is the whole point -- `(-dx, -dy)` after a mark cancels the origin the mark
    committed, and `(0, 0)` leaves the origin alone in the arm that draws nothing."""
    rec = (bytes([0x0B, 0x55, 0x03, 0x00, 0x00, 0x00, 0x00, 0x00])
           + struct.pack("<hh", dx, dy)
           + bytes([0x01, 0xFF, 0xFF, 0x02, 0x02, 0x0A, 0x00])
           + bytes([0x00, 0x00]))
    dec = records.decode_record(rec, 0)
    if not (dec.wtype == 11 and (dec.label or "") == "" and dec.size == len(rec)
            and dec.font is None and dec.base_modes[:4] == [1, 1, 1, 1]
            and layout._s16(dec.geom[0]) == dx and layout._s16(dec.geom[1]) == dy
            and layout._in_flow_gate(dec.wtype)):
        raise CornerMarkError("authored empty record failed its self-decode")
    return rec


def _object(parts: list[bytes]) -> bytes:
    """`<count>` + records, padded to the 4-byte object alignment."""
    payload = bytes([len(parts)]) + b"".join(parts)
    payload += b"\x00" * (-len(payload) % 4)
    if len(records.object_records(payload, 0)) != len(parts):
        raise CornerMarkError("authored arm object failed its self-decode")
    return payload


def append_arms(data: bytes, *, dx: int, dy: int, w: int, h: int, font_slot: int,
                char: str) -> tuple[bytes, int]:
    """Grow the match table by `MATCH_VALUES` and append the mark arm then the empty arm.

    Each arm ends with the flow restore that makes it neutral: the mark arm cancels its own
    `(dx, dy)`, the empty arm moves nothing. Returns `(out, arms_base)`: the MARK is object
    `arms_base` (the `0xFFFF` default, which the firmware only honours at the base) and the empty
    arm is `arms_base + 1`.
    """
    out, _edits = relocate.grow_match_table(data, MATCH_VALUES)
    out, _edits, mark_idx = insert._append_object(
        out, _object([build_mark_record(dx, dy, w, h, font_slot, char),
                      build_empty_record(-dx, -dy)]))
    out, _edits, empty_idx = insert._append_object(out, _object([build_empty_record()]))
    if empty_idx != mark_idx + 1:
        raise CornerMarkError(f"mark arms did not land contiguously ({mark_idx}, {empty_idx})")
    return out, mark_idx


def build_switch_record(value_slot: int, arms_base: int) -> bytes:
    """The wtype-6 switch: read `value_slot`, select the two-object band at `arms_base`.

    Authored rather than cloned, because the two switch donors this project already uses
    (`aescompact._author_two_arm_switch`'s enable pill, and the stock pump/heater pair) both
    carry absolute geometry composed for their own containers. This one has nothing to place --
    its arms position themselves -- so it sits at the flow origin and moves nothing.
    """
    # Field order read off stock's own switches (object 9 record 9, object 4 record 6): header,
    # the two coordinates header[2]=0x03 declares, the R38 font byte, then value_slot, count and
    # child index as u16s -- with header[4]=0x20, the bit that carries the value_slot field.
    # Header byte 1 is 0x55 (all four coordinates flow-relative) with x = y = 0, i.e. "at the
    # flow origin", the neutral placement `aescompact._author_two_arm_switch` also uses.
    rec = (bytes([0x06, 0x55, 0x03, 0x00, 0x20, 0x00, 0x00, 0x00])
           + struct.pack("<hh", 0, 0)
           + bytes([0xFF])
           + struct.pack("<H", value_slot)
           + struct.pack("<H", len(MATCH_VALUES))
           + struct.pack("<H", arms_base))
    dec = records.decode_record(rec, 0)
    if not (dec.wtype == 6 and dec.value_slot == value_slot and dec.child_index == arms_base
            and dec.count == len(MATCH_VALUES) and dec.size == len(rec)):
        raise CornerMarkError(
            f"authored switch record failed its self-decode (wt={dec.wtype} "
            f"slot={dec.value_slot} child={dec.child_index} count={dec.count} "
            f"size={dec.size}/{len(rec)})")
    return rec


def add_mark(data: bytes, *, object_index: int, value_slot: int, dx: int, dy: int,
             w: int, h: int, font_slot: int, char: str) -> tuple[bytes, dict]:
    """Attach a state-gated corner mark to object `object_index`.

    The switch record is appended as the object's LAST record, which is what makes the edit
    layout-neutral: an inserted record advances the pen by its own x, so a record placed anywhere
    else would displace everything after it. Nothing follows the last one.

    Returns `(out, info)` with the arm indices and the switch's own record index, for a caller
    that wants to assert on them.
    """
    gs, _ = guidata.guidata_extent(data)
    tab = guidata.object_tables(data)[1]
    if not 0 <= object_index < len(tab):
        raise CornerMarkError(f"object index {object_index} out of range 0..{len(tab) - 1}")
    before = records.object_records(data, gs + tab[object_index])
    if any(r.wtype == 6 and r.value_slot == value_slot for r in before):
        raise CornerMarkError(
            f"object {object_index} already carries a wtype-6 switch on slot {value_slot}; "
            f"adding a second would draw the mark twice")

    out, arms_base = append_arms(data, dx=dx, dy=dy, w=w, h=h, font_slot=font_slot, char=char)
    gs2, _ = guidata.guidata_extent(out)
    tab2 = guidata.object_tables(out)[1]
    recs = records.object_records(out, gs2 + tab2[object_index])
    stream = [out[r.offset:r.offset + r.size] for r in recs]
    stream.append(build_switch_record(value_slot, arms_base))
    out, _edits = relocate.replace_object_records(out, object_index, _object(stream))

    gs3, _ = guidata.guidata_extent(out)
    tab3 = guidata.object_tables(out)[1]
    after = records.object_records(out, gs3 + tab3[object_index])
    if len(after) != len(before) + 1 or after[-1].wtype != 6:
        raise CornerMarkError(
            f"object {object_index} carries {len(after)} records ending in wtype "
            f"{after[-1].wtype}, expected {len(before) + 1} ending in the switch")
    if [r.wtype for r in after[:-1]] != [r.wtype for r in before]:
        raise CornerMarkError("attaching the mark changed the host object's existing records")
    return out, {"arms_base": arms_base, "mark_object": arms_base,
                 "empty_object": arms_base + 1, "switch_record": len(after) - 1,
                 "value_slot": value_slot, "dx": dx, "dy": dy, "w": w, "h": h,
                 "font_slot": font_slot, "char": char}
