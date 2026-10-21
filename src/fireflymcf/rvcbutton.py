"""rvcbutton -- a button that sends RV-C frames: its records, gesture group, bindings, group
and placement. `add_rvc_button` (`recipe_ops.AddRvcButton`) composes these; `add_aes_batt_heat`
is the same body in its own place (`aescompact.build_batt_heat_button` delegates here).

**Sending.** A button sends through 12-byte bindings in its touch id's group
(`newbutton.binding_raw`): an 18-bit DGN, a gesture mask, a variant nibble and a fixed 8-byte
payload. The LCD sender hard-wires priority 6 and source address 0x9F (`FUN_0021B8B4`), so a
binding on DGN `d` puts `(6 << 26) | (d << 8) | 0x9F` on the wire (`newbutton.binding_wire_frame`).

**Gestures** (`gesture_group`, `bindings`), each a coach-proven shape:

- ``tap``: group prefix `10 00 00 00` (the LEVEL stepper's: stock group 103 `10 00 04 80` with
  byte 2 and byte 3 bit 7 cleared, `levelcontrols.group_entry`), one binding on mask `0x06`.
- ``tap_hold``: Batt Heat's group-17 clone (`10 00 0c 00` on 13.6), the hold nibble (byte 0 bits
  4-7) set to the seconds; the hold binding on mask `0x04` FIRST, then the tap on `0x02`.
- ``hold``: the same clone with byte 2's held-phase repeat index (bits 2-3) cleared, one binding
  on `0x04` (`altreboot.group_entry`'s shape).
- ``toggle``: the tap prefix with byte 1 `0x80` (no variant cycle on release) and byte 0's low
  nibble 1; variant 0 sends `on`, variant 1 sends `off`, both on `0x06`. Something else must
  drive the variant: the setter `FUN_0021B5E8` writes it only while the variant is at most
  that nibble (`and r3,#0xf` / `cmp r3,r4` / `blo` @`0x21B60A`-`0x21B610`), so the tap donor's
  0 would refuse variant 1. Stock's setter-driven two-variant groups carry 1 (129, the Low
  Volts toggle, `11 80 0c 80`; 143 and 146).

**Records** (`button_records`), from the stock donors `aescompact` names, in flow order:

  [0] 829[7], an absolute (base-mode 0) filled box at `(x, y)` `w` x `h`, align 1;
  [1] a flow anchor insetting the origin by `PUMP_OUTLINE_INSET`;
  [2] the Low Volts capsule 285[0], inner size, its mode-7 fillA set to `fill_index`;
  [3] with a glyph AND a caption, an empty record dropping the origin to the caption strip;
  [4] the caption 285[1] relabelled (inner width; the strip's height under a glyph);
  [5] the glyph, a wtype-0 single character (`glyph_record`), and an empty record cancelling
      its offset so the touch region lands on the inner box again;
  [6] the touch region 315[9], inner size, carrying the group id.

`text`, `glyph`, `font` None and `fill_index` None give exactly the Batt Heat body.

**Placement** (`place`). ``attach`` pastes the records as one new object under a byte clone of
`parent`'s first wtype-5 attach (`insert.insert_object`, `move_control`'s paste path). The box is
absolute, so the attach's own pen does not place it. The caption and the glyph write the R38
font and the align byte, which the layout engine does not restore after a child
(`enumlabel` module docstring), so the object ends with `enumlabel.restore_record` writing back
what the walk carried in, measured over every page root reaching `parent`. ``append`` appends
the records to `parent`'s own stream (`relocate.replace_object_records`); only Batt Heat's
preset uses it.
"""
from __future__ import annotations

from . import (aescompact, cornermark, enumlabel, glyphs, guidata, insert, layout, levelcontrols,
               metrics, newbutton, optionrow, pagewalk, records, relocate)

FORMS = ("tap", "hold", "tap_hold", "toggle")
POOLS = ("free", "hold_safe")

TAP_MASK = 0x06                  # short-tap release + held: one frame per press of any length
TAP_WITH_HOLD_MASK = 0x02        # short-tap release only, beside a hold
HOLD_MASK = 0x04                 # held, past the threshold; repeat clear, so it fires once

TAP_DONOR_GROUP = levelcontrols.STEPPER_DONOR_GROUP          # stock 103, `10 00 04 80`
TAP_DONOR_PREFIX = bytes.fromhex("10000480")
HOLD_DONOR_GROUP = aescompact.BATT_HEAT_HOLD_DONOR_GROUP      # stock 17, `10 00 0c 00`
NO_CYCLE = 0x80                  # group byte 1: the variant is not cycled on release
TOGGLE_VARIANT_MAX = 1           # group byte 0 low nibble: the highest variant the setter takes
HELD_REPEAT_INDEX = 0x0C         # group byte 2 bits 2-3: the held-phase repeat-rate index
MAX_HOLD_SECONDS = 15            # one nibble


class RvcButtonError(ValueError):
    """A button this module refuses to build or place."""


# --- gestures ----------------------------------------------------------------------------------

def gesture_group(data: bytes, *, form: str, hold_seconds: int | None,
                  binding_start: int, count: int) -> bytes:
    """The group-table entry (`guidata.GROUP_ENTRY_SIZE` bytes) for `form` whose slice is
    `count` bindings from `binding_start`. The prefix is the named stock donor's, edited
    (`optionrow._clone_group_entry`: a prefix is not a generic constant); the donor's bytes are
    asserted so another base image cannot silently give another gesture. `hold_seconds` None
    keeps the donor's 1-s hold nibble (Batt Heat's, byte for byte)."""
    gt = guidata.group_table(data)
    if form in ("tap", "toggle"):
        raw = bytearray(gt[TAP_DONOR_GROUP].raw)
        if bytes(raw[:4]) != TAP_DONOR_PREFIX:
            raise RvcButtonError(f"tap donor group {TAP_DONOR_GROUP} prefix "
                                 f"{bytes(raw[:4]).hex(' ')} is not the stock 10 00 04 80")
        raw[2] = 0x00
        raw[3] &= 0x7F
        if form == "toggle":
            raw[0] = (raw[0] & 0xF0) | TOGGLE_VARIANT_MAX
            raw[1] = NO_CYCLE
    elif form in ("tap_hold", "hold"):
        raw = bytearray(gt[HOLD_DONOR_GROUP].raw)
        if raw[0] >> 4 < 1:
            raise RvcButtonError(f"hold-donor group {HOLD_DONOR_GROUP} has a 0-second hold "
                                 f"nibble; a hold could not register")
        if hold_seconds is not None:
            if not (isinstance(hold_seconds, int) and 1 <= hold_seconds <= MAX_HOLD_SECONDS):
                raise RvcButtonError(f"hold seconds {hold_seconds!r} is not 1..{MAX_HOLD_SECONDS}")
            raw[0] = (hold_seconds << 4) | (raw[0] & 0x0F)
        if form == "hold":
            raw[2] &= ~HELD_REPEAT_INDEX & 0xFF
    else:
        raise RvcButtonError(f"form {form!r} is not one of {FORMS}")
    return optionrow._clone_group_entry(bytes(raw), binding_start, count)


def _wire(name: str, payload) -> bytes:
    if not isinstance(payload, (bytes, bytearray)) or len(payload) != 8:
        raise RvcButtonError(f"{name} must be an 8-byte payload, got {payload!r}")
    return bytes(payload)


def bindings(dgn: int, *, form: str, tap: bytes | None, hold: bytes | None,
             off: bytes | None, on: bytes | None) -> bytes:
    """The bindings of `form` on `dgn`, concatenated in table order (module docstring). Exactly
    the payloads `form` uses must be given."""
    want = {"tap": ("tap",), "hold": ("hold",), "tap_hold": ("hold", "tap"),
            "toggle": ("on", "off")}
    if form not in want:
        raise RvcButtonError(f"form {form!r} is not one of {FORMS}")
    if not isinstance(dgn, int) or not 0 <= dgn <= 0x3FFFF:
        raise RvcButtonError(f"DGN {dgn!r} is not an 18-bit value")
    given = {k: v for k, v in (("tap", tap), ("hold", hold), ("off", off), ("on", on))
             if v is not None}
    if set(given) != set(want[form]):
        raise RvcButtonError(f"form {form!r} takes {sorted(want[form])}, got {sorted(given)}")
    shape = {"tap": {"tap": (TAP_MASK, 0)},
             "hold": {"hold": (HOLD_MASK, 0)},
             "tap_hold": {"hold": (HOLD_MASK, 0), "tap": (TAP_WITH_HOLD_MASK, 0)},
             "toggle": {"on": (TAP_MASK, 0), "off": (TAP_MASK, 1)}}[form]
    out = b""
    for name in want[form]:
        mask, variant = shape[name]
        out += newbutton.binding_raw(dgn, _wire(name, given[name]), gesture_mask=mask,
                                     variant=variant)
    return out


def allocate(data: bytes, *, pool: str, need: int = 1) -> list[int]:
    """The first `need` group slots of `pool`: ``free`` (`newbutton.free_group_slots`) or
    ``hold_safe`` (`newbutton.hold_safe_groups`, a group GuiCode does not drive). Never
    `levelcontrols.RESERVED_GROUPS`. Raises when the pool holds fewer than `need`."""
    if pool == "free":
        slots = newbutton.free_group_slots(data)
    elif pool == "hold_safe":
        slots = newbutton.hold_safe_groups(data, levelcontrols.RESERVED_GROUPS)
    else:
        raise RvcButtonError(f"group pool {pool!r} is not one of {POOLS}")
    slots = [g for g in slots if g not in levelcontrols.RESERVED_GROUPS]
    if len(slots) < need:
        raise RvcButtonError(f"the {pool!r} group pool holds {len(slots)} slot(s), "
                             f"{need} needed")
    return slots[:need]


# --- records -----------------------------------------------------------------------------------

def glyph_missing(data: bytes, slot: int, char: str) -> bool:
    """`char` in font `slot` draws a fallback: it is outside the font's range (the font's
    fallback glyph, `glyphs.Font.glyph_index`), resolves to the shared placeholder
    (`glyphs.decode_glyph` raises, `AddCornerMark`'s check), or to a solid full-coverage block
    another character shares (icon font 24 marks its unused characters that way,
    `InstallGlyph._unused_block`), or the slot has no font at all."""
    try:
        font = glyphs.font_for_slot(data, slot)
    except glyphs.GlyphError:             # no font descriptor for the slot: nothing to draw
        return True
    if not font.covers(ord(char)):
        return True
    gi = font.glyph_index(ord(char))
    try:
        g = glyphs.decode_glyph(data, gi)
    except glyphs.GlyphError:
        return True
    if not all(v == 0xF for row in g.rows for v in row):
        return False
    at = glyphs.glyph_extent(data, gi)[0]
    return any(glyphs.glyph_extent(data, font.glyph_index(c))[0] == at
               for c in range(font.first, font.first + font.count) if c != ord(char))


def glyph_record(dx: int, dy: int, slot: int, char: str) -> bytes:
    """A wtype-0 single character `char` in font `slot` at `(dx, dy)` from the flow origin,
    align 1 (left: the pen is the cell's left edge). The shape of stock's single-character
    records (HOME's `:`: header, declared x/y, `01 <font> <len> <text> 00`) with base modes
    `[1,1,1,1]`; a wtype-0 is drawn at the pen and needs no box (`cornermark.build_mark_record`'s
    docstring)."""
    if len(char) != 1:
        raise RvcButtonError(f"a glyph is one character, got {char!r}")
    if not 0 <= slot <= 0xFE:
        raise RvcButtonError(f"font slot {slot} is not a slot byte")
    try:
        raw = char.encode("latin-1")
    except UnicodeEncodeError:
        raise RvcButtonError(f"glyph char {char!r} is not a single byte") from None
    rec = (bytes([0x00, 0x55, 0x03, 0x40, 0x00, 0x00, 0x00, 0x00])
           + int(dx).to_bytes(2, "little", signed=True) + int(dy).to_bytes(2, "little", signed=True)
           + bytes([0x01, slot, len(raw)]) + raw + b"\x00")
    dec = records.decode_record(rec, 0)
    if not (dec.wtype == 0 and dec.label == char and dec.font == slot and dec.size == len(rec)
            and dec.align == 1 and dec.base_modes[:4] == [1, 1, 1, 1]
            and layout._s16(dec.geom[0]) == dx and layout._s16(dec.geom[1]) == dy):
        raise RvcButtonError(f"authored glyph record failed its self-decode: {rec.hex()}")
    return rec


def button_records(donors: bytes, *, touch_id: int, w: int, h: int, text: str | None,
                   glyph: tuple[int, str] | None, font: int | None,
                   fill_index: int | None, x: int = 0, y: int = 0,
                   glyph_image: bytes | None = None) -> list[bytes]:
    """The button's record fragments, in flow order (module docstring), cloned from the stock
    donors in `donors`. `x, y` place the absolute box; `fill_index` None keeps Batt Heat's
    static colour (`aescompact.BATT_HEAT_FILL_INDEX`). `glyph` is `(font slot, char)`, its
    metrics read from `glyph_image` (the image it is installed in; default `donors`)."""
    a = aescompact
    inset = a.PUMP_OUTLINE_INSET
    iw, ih = w - 2 * inset, h - 2 * inset
    if iw <= 0 or ih <= 0:
        raise RvcButtonError(f"a {w} x {h} button has no inner box inside its {inset}-px inset")
    box = a._clone_with_geom(donors, a.PUMP_FRAME_DONOR, a.PUMP_BOX_REC,
                             {0: x, 1: y, 2: w, 3: h})
    a._set_align(box, 1)
    anchor = a._flow_anchor(inset, inset)
    btn = a._clone_with_geom(donors, a.PUMP_BUTTON_BODY_DONOR, a.PUMP_BUTTON_BOX_REC, {2: iw, 3: ih})
    box_rec = a._object_records(donors, a.PUMP_BUTTON_BODY_DONOR)[a.PUMP_BUTTON_BOX_REC]
    fill_off = box_rec.color_off[0]
    if fill_off is None:
        raise ValueError("Low Volts box donor 285[0] has no inline fillA colour to repoint")
    btn[fill_off - box_rec.offset:fill_off - box_rec.offset + 2] = (
        a.BATT_HEAT_FILL_INDEX if fill_index is None else fill_index).to_bytes(2, "little")
    frags = [bytes(box), anchor, bytes(btn)]

    label_rec = a._object_records(donors, a.PUMP_BUTTON_BODY_DONOR)[a.PUMP_BUTTON_LABEL_REC]
    cap_font = label_rec.font if font is None else font
    strip = 0                                # the caption's y inside the inner box
    if glyph is not None:
        slot, char = glyph
        img = donors if glyph_image is None else glyph_image
        g = glyphs.decode_glyph(img, glyphs.font_for_slot(img, slot).glyph_index(ord(char)))
        area = ih
        if text is not None:
            if cap_font is None:
                raise RvcButtonError("the caption donor carries no font to measure")
            cap_h = metrics.font_metrics(donors, cap_font).height * (text.count("\n") + 1)
            area = ih - cap_h
            strip = area
        if g.width > iw or g.height > area:
            raise RvcButtonError(f"the {g.width} x {g.height} glyph does not fit the "
                                 f"{iw} x {area} area above the caption")
        gx = (iw - g.width) // 2 - g.x
        gy = (area - g.height) // 2 - g.y
    if text is not None:
        if strip:
            frags.append(cornermark.build_empty_record(0, strip))
        label = a._clone_with_geom(donors, a.PUMP_BUTTON_BODY_DONOR, a.PUMP_BUTTON_LABEL_REC,
                                   {2: iw, 3: ih - strip})
        label = bytearray(a._relabel(donors, a.PUMP_BUTTON_BODY_DONOR, a.PUMP_BUTTON_LABEL_REC,
                                     text, label))
        if font is not None:
            off = records.font_offset(bytes(label), records.decode_record(bytes(label), 0))
            if off is None:
                raise RvcButtonError("the caption donor carries no font field to set")
            label[off] = font
        frags.append(bytes(label))
    if glyph is not None:
        frags.append(glyph_record(gx, gy - strip, slot, char))
        frags.append(cornermark.build_empty_record(-gx, -gy))
    touch = a._clone_with_geom(donors, a.CLUSTER_DONOR_OBJECT, a.PUMP_BUTTON_TOUCH_REC,
                               {2: iw, 3: ih})
    a._set_touch_id(touch, touch_id)
    frags.append(bytes(touch))
    return frags


# --- placement ---------------------------------------------------------------------------------

def _object_index(data: bytes) -> int:
    return len(guidata.object_tables(data)[1]) - 1


def _states_after(data: bytes, parent: int, attach_index: int) -> set:
    """The `(font, align)` each base walk of a page reaching `parent` carries out of record
    `attach_index`'s subtree (the state after its last record)."""
    step = (parent, attach_index)
    found: set = set()
    for root in pagewalk.reaching_roots(data, parent):
        walk = pagewalk.walk_records(data, root)
        for i, r in enumerate(walk):
            inside = step in r.key
            if inside and (i + 1 == len(walk) or step not in walk[i + 1].key):
                found.add((r.font, r.align))
    return found


def place(data: bytes, *, parent: int, x: int, y: int, frags: list[bytes],
          mode: str = "attach") -> tuple[bytes, list, int]:
    """`(image, edits, object_index)`: `frags` placed on `parent` (module docstring). For
    ``attach`` the index is the new object's; for ``append`` it is `parent`. The image is not
    resealed."""
    if mode == "append":
        gs, _ = guidata.guidata_extent(data)
        obj_start = gs + guidata.object_tables(data)[1][parent]
        recs = records.object_records(data, obj_start)
        body = (bytes([data[obj_start] + len(frags)]) + bytes(data[obj_start + 1:recs[-1].end])
                + b"".join(frags))
        try:
            out, edits = relocate.replace_object_records(data, parent, body)
        except relocate.RelocationError as e:
            raise RvcButtonError(str(e)) from e
        return out, edits, parent
    if mode != "attach":
        raise RvcButtonError(f"placement {mode!r} is not 'attach' or 'append'")
    try:
        out, edits = insert.insert_object(data, cornermark._object(frags), parent)
        index = _object_index(out)
        gs, tab = guidata.guidata_extent(out)[0], guidata.object_tables(out)[1]
        attach = len(records.object_records(out, gs + tab[parent])) - 1
        leaving = _states_after(out, parent, attach)
        if len(leaving) != 1:
            raise RvcButtonError(f"the pages reaching object {parent} leave the button with "
                                 f"font/align states {sorted(leaving, key=str)}; expected one")
        restore = enumlabel.restore_state(out, parent, next(iter(leaving)))
        if restore is not None:
            out, edits = insert.insert_object(
                data, cornermark._object(frags + [enumlabel.restore_record(*restore)]), parent)
    except (ValueError, cornermark.CornerMarkError) as e:   # InsertError, RelocationError too
        raise RvcButtonError(str(e)) from e
    gs, tab = guidata.guidata_extent(out)[0], guidata.object_tables(out)[1]
    recs = records.object_records(out, gs + tab[index])
    if len(recs) != len(frags) + (restore is not None) or recs[0].geom[:2] != [x, y]:
        raise RvcButtonError(f"the button object {index} does not hold the authored records")
    return out, edits, index
