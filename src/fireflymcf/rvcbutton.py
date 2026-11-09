"""rvcbutton -- a button that sends RV-C frames: its records, gesture group, bindings, group
and placement. `add_rvc_button` (`recipe_ops.AddRvcButton`) composes these; `add_aes_batt_heat`
is the same body in its own place (`aescompact.build_batt_heat_button` delegates here).

**Sending.** A button sends through 12-byte bindings in its touch id's group
(`newbutton.binding_raw`): an 18-bit DGN, a gesture mask, a variant nibble and a fixed 8-byte
payload. The LCD sender hard-wires priority 6 and source address 0x9F (`FUN_0021B8B4`), so a
binding on DGN `d` puts `(6 << 26) | (d << 8) | 0x9F` on the wire (`newbutton.binding_wire_frame`).

**Gestures** (`gesture_group`, `bindings`), each a coach-proven shape:

- ``tap``: group prefix `10 00 00 00` (the frozen `donors.GROUP_PREFIXES["group.tap"]`, 13.6's
  stock group 103 `10 00 04 80`, with byte 2 and byte 3 bit 7 cleared,
  `levelcontrols.group_entry`), one binding on mask `0x06`.
- ``tap_hold``: the frozen `group.hold` prefix (Batt Heat's group 17, `10 00 0c 00` on 13.6),
  the hold nibble (byte 0 bits 4-7) set to the seconds; the hold binding on mask `0x04` FIRST,
  then the tap on `0x02`.
- ``hold``: the same clone with byte 2's held-phase repeat index (bits 2-3) cleared, one binding
  on `0x04` (`altreboot.group_entry`'s shape).
- ``toggle``: the tap prefix with byte 1 `0x80` (no variant cycle on release) and byte 0's low
  nibble 1; variant 0 sends `on`, variant 1 sends `off`, both on `0x06`. Something else must
  drive the variant: GuiCode's `setVariant` (host API `+0x04`, the ungated `FUN_0021B678`,
  `lcdgroups.host_writer`). The nibble bounds only the LCD's gated setter `FUN_0021B5E8`
  (`and r3,#0xf` / `cmp r3,r4` / `blo` @`0x21B60A`-`0x21B610`), which GuiCode does not call
  (corrected 2026-11-08); it is 1 because stock's setter-driven two-variant groups carry 1
  (129, the Low Volts toggle, `11 80 0c 80`; 143 and 146).
- ``cycle``: the toggle's shape with N bindings (2-16): variant k sends payload k, every one on
  `0x06`, and byte 0's low nibble N-1 so the setter accepts every variant. `add_button_states`
  drives it (the state after the one the unit reports). *Emulator*: each variant sends its own
  payload (the 2026-11-05 port of a user op whose author reports its four- and five-state
  groups reaching an Elwell water heater on another coach); no coach of this project has run it.
- ``display``: no gesture and no group. The records carry no touch region, so the button only
  draws (a status tile for `add_button_states` or `add_button_status`).

**Records** (`button_records`), from the frozen `donors` templates (13.6's records at the
pins named here; `donors.template` gates them on the image's grammar proof), in flow order:

  [0] `rvc.box` (13.6 829[7]), an absolute (base-mode 0) filled box at `(x, y)` `w` x `h`,
      align 1;
  [1] a flow anchor insetting the origin by `PUMP_OUTLINE_INSET`;
  [2] the Low Volts capsule `rvc.capsule` (285[0]), inner size, its mode-7 fillA set to
      `fill_index` (below the image's `donors.mode7_index_limit`);
  [3] with a glyph AND a caption, an empty record dropping the origin to the caption strip;
  [4] the caption `rvc.caption` (285[1]) relabelled (inner width; the strip's height under a
      glyph);
  [5] the glyph, a wtype-0 single character (`glyph_record`), and an empty record cancelling
      its offset so the touch region lands on the inner box again. The pen is placed so the
      bitmap is centred in the area above the caption: the panel draws it `font.top - y` rows
      above the pen (`metrics.wt0_ink_offset`, GUICODE Finding 84). Across, the pen is the
      bitmap's left edge in measuring class 2 (proven given class 2; a page's class is not
      checked, so `add_rvc_button` names a glyph's nonzero x bearing in its manifest);
  [6] the touch region `rvc.touch` (315[9]), inner size, carrying the group id (none when
      `touch_id` is None: a display tile).

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

import struct

from . import (aescompact, cornermark, enumlabel, glyphs, guidata, insert, layout,
               metrics, newbutton, optionrow, pagewalk, records, relocate)
from . import donors as _donors

FORMS = ("tap", "hold", "tap_hold", "toggle", "cycle")
MAX_CYCLE_STATES = 16            # the variant is a nibble
POOLS = ("free", "hold_safe")

TAP_MASK = 0x06                  # short-tap release + held: one frame per press of any length
TAP_WITH_HOLD_MASK = 0x02        # short-tap release only, beside a hold
HOLD_MASK = 0x04                 # held, past the threshold; repeat clear, so it fires once

# The gesture prefixes are frozen (`donors.GROUP_PREFIXES`): 13.6's groups 103 and 17.
TAP_PREFIX = "group.tap"         # `10 00 04 80`
HOLD_PREFIX = "group.hold"       # `10 00 0c 00`
NO_CYCLE = 0x80                  # group byte 1: the variant is not cycled on release
# Group byte 0 low nibble: the highest variant the LCD's gated setter `FUN_0021B5E8` takes. GuiCode's
# `setVariant` is the ungated `FUN_0021B678` (host API +0x04, `lcdgroups`), which does not read it;
# 1 is what stock's setter-driven two-variant groups carry (129, 143, 146).
TOGGLE_VARIANT_MAX = 1
HELD_REPEAT_INDEX = 0x0C         # group byte 2 bits 2-3: the held-phase repeat-rate index
MAX_HOLD_SECONDS = 15            # one nibble


class RvcButtonError(ValueError):
    """A button this module refuses to build or place."""


# --- gestures ----------------------------------------------------------------------------------

def gesture_group(data: bytes, *, form: str, hold_seconds: int | None,
                  binding_start: int, count: int) -> bytes:
    """The group-table entry (`guidata.GROUP_ENTRY_SIZE` bytes) for `form` whose slice is
    `count` bindings from `binding_start`. The prefix is a frozen 13.6 group prefix
    (`donors.group_prefix`, gated on the image's grammar proof), edited and cloned with a zero pad
    byte (`optionrow._clone_group_entry`). `hold_seconds` None keeps the prefix's 1-s hold nibble
    (Batt Heat's, byte for byte)."""
    def prefix(name: str) -> bytearray:
        pre, _proof = _donors.group_prefix(data, name)
        return bytearray(pre + bytes(guidata.GROUP_ENTRY_SIZE - len(pre)))

    if form in ("tap", "toggle", "cycle"):
        raw = prefix(TAP_PREFIX)
        raw[2] = 0x00
        raw[3] &= 0x7F
        if form == "toggle":
            raw[0] = (raw[0] & 0xF0) | TOGGLE_VARIANT_MAX
            raw[1] = NO_CYCLE
        elif form == "cycle":
            if not 2 <= count <= MAX_CYCLE_STATES:
                raise RvcButtonError(f"a cycle takes 2..{MAX_CYCLE_STATES} states, got {count}")
            raw[0] = (raw[0] & 0xF0) | (count - 1)
            raw[1] = NO_CYCLE
    elif form in ("tap_hold", "hold"):
        raw = prefix(HOLD_PREFIX)
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
             off: bytes | None, on: bytes | None, cycle: list | None = None) -> bytes:
    """The bindings of `form` on `dgn`, concatenated in table order (module docstring). Exactly
    the payloads `form` uses must be given; `cycle` is the list of a cycle's payloads."""
    if form == "cycle" or cycle is not None:
        if form != "cycle" or any(v is not None for v in (tap, hold, off, on)):
            raise RvcButtonError(f"form {form!r}: cycle payloads go with form 'cycle' alone")
        if not isinstance(dgn, int) or not 0 <= dgn <= 0x3FFFF:
            raise RvcButtonError(f"DGN {dgn!r} is not an 18-bit value")
        if not isinstance(cycle, (list, tuple)) or not 2 <= len(cycle) <= MAX_CYCLE_STATES:
            raise RvcButtonError(f"a cycle takes 2..{MAX_CYCLE_STATES} payloads, got {cycle!r}")
        return b"".join(newbutton.binding_raw(dgn, _wire("cycle[%d]" % k, p),
                                              gesture_mask=TAP_MASK, variant=k)
                        for k, p in enumerate(cycle))
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
    """The first `need` group slots of `pool`: ``free`` (`newbutton.assignable_groups`) or
    ``hold_safe`` (`newbutton.hold_safe_groups`, a group GuiCode does not drive). Never a
    reserved template (`newbutton.reserved_groups`, located; refuses `ReservedGroupUnknown`).
    Raises when the pool holds fewer than `need`."""
    if pool == "free":
        slots = newbutton.assignable_groups(data)
    elif pool == "hold_safe":
        slots = newbutton.hold_safe_groups(data)
    else:
        raise RvcButtonError(f"group pool {pool!r} is not one of {POOLS}")
    reserved = newbutton.reserved_groups(data)
    slots = [g for g in slots if g not in reserved]
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


def glyph_blank(data: bytes, slot: int, char: str) -> bool:
    """`char` in font `slot` is a blank glyph: the font covers it and its glyph declares height
    0, so the blitter (`FUN_002510C0`, `glyphs.decode_glyph`) draws no rows and the text
    advances by its width. In 13.6 that is the space in fonts 0-20 and the latin-1 blanks that
    share its shape (NBSP, 0x8d, 0x8f, 0x90, 0x9d in fonts 10-17). `glyph_missing` reports
    these as missing (a decode error); a text label treats them as spaces. False when the slot
    has no font, the font does not cover `char` (a fallback), or the glyph has rows (font 25's
    space is its solid 72x72 block)."""
    try:
        font = glyphs.font_for_slot(data, slot)
    except glyphs.GlyphError:
        return False
    if not font.covers(ord(char)):
        return False
    offset, _extent = glyphs.glyph_extent(data, font.glyph_index(ord(char)))
    return data[offset + glyphs.OFF_WIDTH] >= 1 and data[offset + glyphs.OFF_HEIGHT] == 0


def glyph_record(dx: int, dy: int, slot: int, char: str) -> bytes:
    """A wtype-0 single character `char` in font `slot` at `(dx, dy)` from the flow origin,
    align 1 (left: the pen is the bitmap's left edge in class 2 and the cell's baseline row, see
    `metrics.wt0_ink_offset`). The shape of stock's single-character records (HOME's `:`:
    header, declared x/y, `01 <font> <len> <text> 00`) with base modes `[1,1,1,1]`; a wtype-0
    is drawn at the pen and needs no box (`cornermark.build_mark_record`'s docstring)."""
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


def _with_geom(raw: bytes, edits: dict) -> bytearray:
    """Template `raw` (one standalone record) with `{geom slot: value}` written in place, each a
    signed u16 on a slot the record already carries (`aescompact._clone_with_geom`'s rule)."""
    rec = records.decode_record(raw, 0)
    frag = bytearray(raw)
    for slot, value in edits.items():
        off = records.geom_offset(raw, rec, slot)
        if off is None:
            raise RvcButtonError(f"template wtype {rec.wtype} has no geom[{slot}] to set")
        if not -0x8000 <= value <= 0x7FFF:
            raise RvcButtonError(f"geom[{slot}] value {value} does not fit a signed u16")
        struct.pack_into("<h", frag, off, value)
    return frag


def _relabel(raw: bytes, text: str) -> bytes:
    """Template `raw` with its final label field rebuilt at `text`'s length
    (`aescompact._relabel`'s rule), self-decoded."""
    rec = records.decode_record(raw, 0)
    if rec.label_offset is None or rec.end != rec.label_offset + len(rec.label) + 1:
        raise RvcButtonError("the caption template's label is not its final field")
    if len(text) > 0xFE:
        raise RvcButtonError(f"label {text!r} is too long for a one-byte length prefix")
    out = bytes(raw[:rec.label_offset - 1]) + bytes([len(text)]) + text.encode("ascii") + b"\x00"
    dec = records.decode_record(out, 0)
    if not (dec.wtype == rec.wtype and dec.label == text and dec.size == len(out)):
        raise RvcButtonError(f"rebuilt label record for {text!r} failed its self-decode")
    return out


def capsule_record(donors: bytes, *, fill_index: int, iw: int, ih: int) -> bytes:
    """The `rvc.capsule` template at inner size `iw` x `ih`, its mode-7 fillA `fill_index`
    (refused at or above `donors.mode7_index_limit`)."""
    _donors.check_mode7_index(donors, fill_index, "the button capsule's fillA")
    raw, _proof = _donors.template(donors, "rvc.capsule")
    btn = _with_geom(raw, {2: iw, 3: ih})
    off = records.decode_record(raw, 0).color_off[0]
    if off is None:
        raise RvcButtonError("the capsule template has no inline fillA colour to repoint")
    struct.pack_into("<H", btn, off, fill_index)
    return bytes(btn)


def button_records(donors: bytes, *, touch_id: int | None, w: int, h: int, text: str | None,
                   glyph: tuple[int, str] | None, font: int | None,
                   fill_index: int | None, x: int = 0, y: int = 0,
                   glyph_image: bytes | None = None) -> list[bytes]:
    """The button's record fragments, in flow order (module docstring), from the frozen
    `donors` templates, gated on the image `donors`. `x, y` place the absolute box;
    `fill_index` None keeps Batt Heat's static colour (`aescompact.BATT_HEAT_FILL_INDEX`).
    `glyph` is `(font slot, char)`, its metrics read from `glyph_image` (the image it is
    installed in; default `donors`)."""
    a = aescompact
    inset = a.PUMP_OUTLINE_INSET
    iw, ih = w - 2 * inset, h - 2 * inset
    if iw <= 0 or ih <= 0:
        raise RvcButtonError(f"a {w} x {h} button has no inner box inside its {inset}-px inset")
    box_raw, _proof = _donors.template(donors, "rvc.box")
    box = _with_geom(box_raw, {0: x, 1: y, 2: w, 3: h})
    a._set_align(box, 1)
    anchor = a._flow_anchor(inset, inset)
    btn = capsule_record(donors, fill_index=a.BATT_HEAT_FILL_INDEX if fill_index is None
                         else fill_index, iw=iw, ih=ih)
    frags = [bytes(box), anchor, btn]

    label_raw = cap_font = None
    if text is not None:
        label_raw, _proof = _donors.template(donors, "rvc.caption", font=font)
        cap_font = records.decode_record(label_raw, 0).font if font is None else font
        if cap_font is None:
            raise _donors.DonorError("NO_TEMPLATE", "the caption template carries no font")
        _donors.check_text(donors, cap_font, text, "the button caption")
    strip = 0                                # the caption's y inside the inner box
    if glyph is not None:
        slot, char = glyph
        img = donors if glyph_image is None else glyph_image
        g = glyphs.decode_glyph(img, glyphs.font_for_slot(img, slot).glyph_index(ord(char)))
        area = ih
        if text is not None:
            cap_h = metrics.font_metrics(donors, cap_font).height * (text.count("\n") + 1)
            area = ih - cap_h
            strip = area
        if g.width > iw or g.height > area:
            raise RvcButtonError(f"the {g.width} x {g.height} glyph does not fit the "
                                 f"{iw} x {area} area above the caption")
        # the panel draws a wtype-0 character's bitmap at the pen plus `wt0_ink_offset`: its
        # x bearing cancelled (given measuring class 2, not checked here), and `font.top - y`
        # rows ABOVE the pen (GUICODE Finding 84), so the pen sits below the ink it centres
        ink_dx, ink_dy = metrics.wt0_ink_offset(img, slot, char)
        gx = (iw - g.width) // 2 - ink_dx
        gy = (area - g.height) // 2 - ink_dy
    if text is not None:
        if strip:
            frags.append(cornermark.build_empty_record(0, strip))
        label = bytearray(_relabel(bytes(_with_geom(label_raw, {2: iw, 3: ih - strip})), text))
        if font is not None:
            off = records.font_offset(bytes(label), records.decode_record(bytes(label), 0))
            if off is None:
                raise RvcButtonError("the caption template carries no font field to set")
            label[off] = font
        frags.append(bytes(label))
    if glyph is not None:
        frags.append(glyph_record(gx, gy - strip, slot, char))
        frags.append(cornermark.build_empty_record(-gx, -gy))
    if touch_id is None:                     # a display tile: nothing to touch
        return frags
    touch_raw, _proof = _donors.template(donors, "rvc.touch")
    touch = _with_geom(touch_raw, {2: iw, 3: ih})
    a._set_touch_id(touch, touch_id)
    frags.append(bytes(touch))
    return frags


# --- placement ---------------------------------------------------------------------------------

def _object_index(data: bytes) -> int:
    return len(guidata.object_tables(data)[1]) - 1


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
        leaving = enumlabel.leaving_state(out, parent, attach)
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
