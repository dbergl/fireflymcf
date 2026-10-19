"""pagedraw -- absolute, explicit-colour widget-record primitives for authored pages.

Each primitive returns ONE widget record's bytes. The record decodes standalone at offset 0
(`records.decode_record(raw, 0).size == len(raw)`), so a caller composes a page body by
concatenating primitives and wrapping them with `stream`. Every record is:

* **absolute** -- header byte 1 = `0x50`, base modes `[0,0,1,1]`: geom[0]/[1] are the screen
  x/y (mode 0) and geom[2]/[3] are added to the record's own placed x/y (mode 1), so they read as
  a WIDTH and HEIGHT and the box is `(x, y)-(x+w, y+h)` **inclusive** (a wt-4 fill of `w` paints
  `w + 1` columns; the stock header bar is `w = 799` for 800 px). See
  `recipe_ops.AddLowVoltsControl._box_record`'s docstring for the mode-1 reading.
* **explicit in colour** -- every colour field the record's type decodes is written, never left
  mode 0 (inherit the previous record's slot: the Low Volts leak, GUICODE Finding 29) and never
  mode 7 (a variable-table colour cell that may be uninitialised, Finding 67). Colours are
  `(r, g, b)` tuples written mode 3, the inline u24 the decoder reads B, G, R
  (`FUN_00252140`), so the decoded int is `0xRRGGBB`. The ON-state pair (fillA2/fillB2, selected
  by `render+0x704F`, Finding 28) is written equal to the OFF pair, so a record looks the same
  whatever that flag holds.
* **explicit in font and alignment**: the R38 font selector is persistent across records, so a
  text primitive always writes a real slot; and every record's header align is non-zero, because
  align 0 does not write the persistent `state+9` and so inherits the previous record's, which
  shifts a path-1 box (Finding 24; `ALIGN_NO_SHIFT`). Path-1 primitives write 1 (no shift); a
  readout writes its own 1..3. Labels are single-line.

**Text backgrounds.** For a text record (wt-11 label, wt-8 readout) fillA/fillA2 are the INK and
fillB/fillB2 the BACKGROUND (`render.py`'s label ink resolution; the ALTERNATOR `CLEAR EEPROM`
evidence in Finding 38). Block-B mode 5 is not an inherit: it sets the transparency bit
(`orr r3,#2` at `0x253532` for fillB, `orr #0x8000` at `0x25362A` for fillB2), and mode 3 clears
it (`bic #2` at `0x253514`, `bic #0x8000` at `0x25360E`). So `label(bg=None)` writes the
background pair mode 5 -- the stock label pattern (285[1]) and an explicit state, not a leak --
and `label(bg=rgb)` an opaque mode-3 box. A readout's `bg` is REQUIRED: its donor (793[5])
paints an opaque background (fillB mode 7 idx 8), which is what erases the previous digits when
the live value redraws.

**Donors.** Each primitive starts from a stock record whose bytes are pinned here and verified
against the image before use; a drifted donor raises `PageDrawError("donor ... drifted")`.

| primitive | donor | changed |
|---|---|---|
| `fill` (wt-4) | object 32 record 2, the header bar | geom0/1 inserted; fillA mode 7 -> 3; fillA2 0 -> 3 |
| `rbox` (wt-0x20) | object 368 record 1, the Status box | aux byte (out-regs 1) dropped; b1 0x55 -> 0x50; fillB 7 -> 3; A2/B2 0 -> 3 |
| `capsule` (wt-0x21) | object 285 record 0 via `AddLowVoltsControl._box_record` | fillA 7 -> 3; fillA2 0 -> 3 |
| `hline` (wt-2) | object 367 record 6 | fillA/fillA2 0 -> 3 (3 bytes each inserted) |
| `label`/`glyph` (wt-11) | object 285 record 1 via `AddLowVoltsControl._label_record` | fillA 2 -> 3; A2 0 -> 3; B/B2 5 or 3 |
| `readout` (wt-9 + wt-8) | object 793 records 4/5 via `readout.author_readout_object` | font, align; A/A2 0 -> 3; B 7 -> 3; B2 0 -> 3 |
| `touch` (wt-12) | object 186 record 1, an absolute touch region | geom; R50 touch id; align 0 -> 1 |

A mode change that alters a field's inline width (mode 7 u16 -> mode 3 u24; mode 0 none -> u24)
is done by `_set_colour`: it computes the field's cursor position the way `FUN_00252CB0` reads it
(header, R9-R10 clip, R11-R12 mode bytes, then the colour fields in order), splices the inline
value, rewrites the mode bits, and re-decodes, requiring every OTHER decoded field to be
unchanged -- the same decode-and-check `aescompact._red_glyph_record` uses for its mode-3 ink.
"""
from __future__ import annotations

import struct

from . import cornermark, guidata, metrics, readout as _readout, records


class PageDrawError(ValueError):
    """A donor drifted, a request is out of range, or an authored record failed its self-check."""


# ---- pinned donors (identical in stock 13.6 and v33) ------------------------------------------

FILL_DONOR = (32, 2)
FILL_STOCK = bytes.fromhex("04500c070000000004001f032400")
RBOX_DONOR = (368, 1)
RBOX_STOCK = bytes.fromhex("60550f3b000010009e9e9e030044f6ff0500fa00aa000a00")
CAPSULE_DONOR = (285, 0)
CAPSULE_STOCK = bytes.fromhex("21550c07000010002d00690032000500")
HLINE_DONOR = (367, 6)
HLINE_STOCK = bytes.fromhex("02500f000000000058029a0050005000")
LABEL_DONOR = (285, 1)
LABEL_STOCK = bytes.fromhex("0b550c2a0000000069003200010eff020214"
                            "00094c6f770a566f6c747300")
DIRECTIVE_DONOR = (_readout.DONOR_OBJECT, _readout.DONOR_READOUT_REC - 1)
DIRECTIVE_STOCK = bytes.fromhex("0950000000000000" "00180300")
READOUT_DONOR = (_readout.DONOR_OBJECT, _readout.DONOR_READOUT_REC)
READOUT_STOCK = bytes.fromhex("085503f8200008000800320000006900" "46000f1601")
# An absolute stock touch region (x 609, y 280, 75x52, id 0x20), byte-for-byte the shape
# `optionrow._author_touch` authors: header 0c 50 0f 00, four geom u16s, the R50 touch id.
TOUCH_DONOR = (186, 1)
TOUCH_STOCK = bytes.fromhex("0c500f0000000000610218014b0034002000")

# Header (byte, shift) of each colour field's 3-bit mode (records.decode_record).
_MODE_BITS = {"fillA": (3, 0), "fillB": (3, 3), "fillA2": (5, 1), "fillB2": (5, 4)}
_INLINE_W = {3: 3, 6: 1, 7: 2}          # bytes a mode carries inline; every other mode: none
# The six geometry slots' presence bits (records._PEN_BITS), (header byte, bit).
_GEOM_BITS = ((2, 0x01), (2, 0x02), (2, 0x04), (2, 0x08), (6, 0x10), (6, 0x20))

# 285[1]'s own R39 byte and R49 line-height pair, kept for every authored label.
_LABEL_R39 = 0xFF
_LABEL_LINE_HEIGHT = 0x14
_LABEL_ADDS = 0x00


def _donor(data: bytes, where: tuple[int, int], pinned: bytes) -> bytes:
    obj, j = where
    try:
        gs, _ = guidata.guidata_extent(data)
        r = records.object_records(data, gs + guidata.object_tables(data)[1][obj])[j]
        got = bytes(data[r.offset:r.offset + r.size])
    except (IndexError, ValueError, KeyError) as e:
        raise PageDrawError(f"donor {obj}[{j}] drifted: it no longer decodes ({e})") from e
    if got != pinned:
        raise PageDrawError(f"donor {obj}[{j}] drifted: {got.hex()} != pinned {pinned.hex()}")
    return got


def _rgb(rgb) -> tuple[int, int, int]:
    t = tuple(rgb)
    if len(t) != 3 or not all(isinstance(c, int) and 0 <= c <= 0xFF for c in t):
        raise PageDrawError(f"colour must be (r, g, b) of 0..255, got {rgb!r}")
    return t


def _u16(name: str, v: int) -> int:
    if not isinstance(v, int) or not 0 <= v <= 0xFFFF:
        raise PageDrawError(f"{name} must be 0..0xFFFF, got {v!r}")
    return v


def _decode(frag: bytes) -> records.WRecord:
    r = records.decode_record(frag, 0)
    if r.size != len(frag):
        raise PageDrawError(f"record does not decode standalone: {r.size} != {len(frag)} "
                            f"({frag.hex()})")
    return r


def _fields(r: records.WRecord, skip_colour: str | None = None, skip_geom: int | None = None):
    """Every decoded field of `r` except the colour role / geom slot being edited."""
    return (r.wtype, r.label, r.font, r.base_modes, r.in_regs, r.out_regs, r.value_slot,
            r.count, r.corner_mask, r.h_align, r.v_align, r.line_height,
            r.line_height_adds_font, r.fmt, r.child_index,
            [g for i, g in enumerate(r.geom) if i != skip_geom],
            [c for c in r.colors if c[0] != skip_colour])


def _colour_block_start(frag: bytes) -> int:
    """Cursor where `FUN_00252CB0` reads the first colour field: after the 8-byte header, the
    R9-R10 clip block (header[7] & 0x20: a mask byte + one u16 per set low bit) and the R11-R12
    colour-mode bytes (header[7] & 0x40)."""
    pos = 8
    if frag[7] & 0x20:
        pos += 1 + 2 * bin(frag[8] & 0x0F).count("1")
    if frag[7] & 0x40:
        pos += 2
    return pos


def _set_mode(frag: bytes, role: str, mode: int, inline: bytes = b"") -> bytes:
    """Rewrite colour field `role` to `mode` carrying `inline`, resizing the record as needed."""
    if len(inline) != _INLINE_W.get(mode, 0):
        raise PageDrawError(f"mode {mode} carries {_INLINE_W.get(mode, 0)} inline bytes")
    rec = _decode(frag)
    pos = _colour_block_start(frag)
    for i, (r, m, _v) in enumerate(rec.colors):
        if r == role:
            old_mode = m
            if rec.color_off[i] is not None and rec.color_off[i] != pos:
                raise PageDrawError(f"{role} cursor {pos} disagrees with decoder "
                                    f"{rec.color_off[i]}")
            break
        pos += _INLINE_W.get(m, 0)
    else:
        raise PageDrawError(f"wtype {rec.wtype} has no {role} colour field ({rec.colors})")
    out = bytearray(frag[:pos] + inline + frag[pos + _INLINE_W.get(old_mode, 0):])
    hb, sh = _MODE_BITS[role]
    out[hb] = (out[hb] & ~(7 << sh) & 0xFF) | (mode << sh)
    out = bytes(out)
    new = _decode(out)
    got = [(c, o) for c, o in zip(new.colors, new.color_off) if c[0] == role]
    want_v = (inline[0] | inline[1] << 8 | inline[2] << 16) if mode == 3 else \
        (inline[0] if mode == 6 else (inline[0] | inline[1] << 8) if mode == 7 else None)
    if (len(got) != 1 or got[0][0] != (role, mode, want_v)
            or (inline and got[0][1] != pos)
            or _fields(new, skip_colour=role) != _fields(rec, skip_colour=role)
            or len(out) != len(frag) - _INLINE_W.get(old_mode, 0) + len(inline)):
        raise PageDrawError(f"colour rewrite of {role} failed its self-decode: {out.hex()}")
    return out


def _set_colour(frag: bytes, role: str, rgb) -> bytes:
    """Write `rgb` into colour field `role` as mode 3 (inline, file order B, G, R)."""
    r, g, b = _rgb(rgb)
    return _set_mode(frag, role, 3, bytes([b, g, r]))


def _set_geom(frag: bytes, i: int, value: int) -> bytes:
    """Write geometry slot `i`, inserting it (and setting its presence bit) when absent. An
    absent slot is inserted at the offset of the next present slot, which is where the R23-R28
    loop would read it."""
    _u16(f"geom[{i}]", value)
    rec = _decode(frag)
    out = bytearray(frag)
    if rec.geom[i] is not None:
        off = records.geom_offset(frag, rec, i)
        struct.pack_into("<H", out, off, value)
    else:
        nxt = [rec.geom_off[j] for j in range(i + 1, 6) if rec.geom_off[j] is not None]
        if not nxt:
            raise PageDrawError(f"cannot insert geom[{i}]: no later slot to anchor it")
        at = nxt[0]
        out[at:at] = struct.pack("<H", value)
        hb, bit = _GEOM_BITS[i]
        out[hb] |= bit
    out = bytes(out)
    new = _decode(out)
    if new.geom[i] != value or _fields(new, skip_geom=i) != _fields(rec, skip_geom=i):
        raise PageDrawError(f"geom[{i}] write failed its self-decode: {out.hex()}")
    return out


def _set_font(frag: bytes, font: int) -> bytes:
    _check_font(font)
    rec = _decode(frag)
    off = records.font_offset(frag, rec)
    if off is None:
        raise PageDrawError(f"wtype {rec.wtype} carries no font selector")
    out = bytearray(frag)
    out[off] = font
    out = bytes(out)
    new = _decode(out)
    if (new.font != font or new.header != rec.header or new.size != rec.size
            or _fields(new)[:2] + _fields(new)[3:] != _fields(rec)[:2] + _fields(rec)[3:]):
        raise PageDrawError(f"font write failed its self-decode: {out.hex()}")
    return out


# Header align (b3 >> 6) every path-1 primitive writes. `FUN_00252CB0` stores `state+9` only
# for a NON-zero align (`lsrs r3,#6` @0x2531FC, `beq` @0x2531FE past `strb r3,[r2,#9]`
# @0x253200), so align 0 INHERITS the previous record's persistent align. The layout engine
# shifts a path-1 box (wtypes 2, 4, 11, 0x20, 0x21; `layout._align_path1`) by that state when
# it is 2 or 3 (0x258088-0x258090); 1 is an explicit "no shift". GUICODE Finding 24.
ALIGN_NO_SHIFT = 1


def _set_align(frag: bytes, align: int) -> bytes:
    if align not in (1, 2, 3):
        raise PageDrawError(f"header align must be 1..3 (0 inherits state+9), got {align}")
    rec = _decode(frag)
    out = bytearray(frag)
    out[3] = (out[3] & 0x3F) | (align << 6)
    out = bytes(out)
    new = _decode(out)
    if new.align != align or _fields(new) != _fields(rec) or new.size != rec.size:
        raise PageDrawError(f"align write failed its self-decode: {out.hex()}")
    return out


def _drop_aux18(frag: bytes) -> bytes:
    """Remove the R18 aux byte (header[0] & 0x40): the per-coord register indices. The Status
    box's `0x44` stores its x0/y0 into register 1 for siblings to lay out against; an absolute
    record must not clobber a register the rest of the page may read."""
    if not frag[0] & 0x40:
        return frag
    rec = _decode(frag)
    pos = _colour_block_start(frag) + sum(_INLINE_W.get(m, 0) for _r, m, _v in rec.colors)
    if (rec.wtype in (10, 18)) and frag[6] & 2:        # R17, types 10/18 only
        pos += 3
    out = bytearray(frag[:pos] + frag[pos + 1:])
    out[0] &= ~0x40 & 0xFF
    out = bytes(out)
    new = _decode(out)
    if (new.in_regs[:2] != [0, 0] or new.out_regs[:2] != [0, 0] or new.geom != rec.geom
            or new.colors != rec.colors):
        raise PageDrawError(f"dropping the aux byte failed its self-decode: {out.hex()}")
    return out


def _absolute(frag: bytes) -> bytes:
    out = bytearray(frag)
    out[1] = 0x50
    return bytes(out)


def _finish(frag: bytes, wtype: int, geom: list, ink_roles: dict,
            align: int = ALIGN_NO_SHIFT) -> bytes:
    """Final self-check shared by every primitive: type, standalone size, absolute placement,
    an explicit (non-zero) header align, no register traffic, the requested geometry, and every
    colour field explicit with the value it was given (`ink_roles`: role -> (mode, value))."""
    r = _decode(frag)
    colours = {role: (m, v) for role, m, v in r.colors}
    if not (r.wtype == wtype and r.header[1] == 0x50 and r.base_modes[:4] == [0, 0, 1, 1]
            and r.align == align
            and r.in_regs == [0] * 6 and r.out_regs == [0] * 6
            and r.geom[:len(geom)] == geom and colours == ink_roles):
        raise PageDrawError(f"authored wtype-{wtype} record failed its self-decode: "
                            f"{frag.hex()} (colours {r.colors}, geom {r.geom})")
    return frag


def _rgb_int(rgb) -> int:
    r, g, b = _rgb(rgb)
    return (r << 16) | (g << 8) | b


def _paint(frag: bytes, pairs: dict) -> bytes:
    """Write each `role: rgb-or-None` (None = mode 5, explicit transparent)."""
    for role, rgb in pairs.items():
        frag = _set_colour(frag, role, rgb) if rgb is not None else _set_mode(frag, role, 5)
    return frag


def _expect(pairs: dict) -> dict:
    return {role: ((3, _rgb_int(rgb)) if rgb is not None else (5, None))
            for role, rgb in pairs.items()}


# ---- primitives ------------------------------------------------------------------------------

def fill(data: bytes, x: int, y: int, w: int, h: int, rgb) -> bytes:
    """A wt-4 filled rectangle `(x, y)-(x+w, y+h)` inclusive, in `rgb`."""
    frag = _absolute(_donor(data, FILL_DONOR, FILL_STOCK))
    for i, v in enumerate((x, y, w, h)):
        frag = _set_geom(frag, i, v)
    pairs = {"fillA": rgb, "fillA2": rgb}
    return _finish(_set_align(_paint(frag, pairs), ALIGN_NO_SHIFT), 4, [x, y, w, h],
                   _expect(pairs))


def rbox(data: bytes, x: int, y: int, w: int, h: int, radius: int, border_rgb, fill_rgb) -> bytes:
    """A wt-0x20 rounded box: `border_rgb` outline (fillA) around a `fill_rgb` interior (fillB),
    corner radius `radius` (geom[4], `WRecord.corner_radius`)."""
    frag = _absolute(_drop_aux18(_donor(data, RBOX_DONOR, RBOX_STOCK)))
    for i, v in enumerate((x, y, w, h, radius)):
        frag = _set_geom(frag, i, v)
    pairs = {"fillA": border_rgb, "fillA2": border_rgb, "fillB": fill_rgb, "fillB2": fill_rgb}
    return _finish(_set_align(_paint(frag, pairs), ALIGN_NO_SHIFT), 0x20, [x, y, w, h, radius],
                   _expect(pairs))


def capsule(data: bytes, x: int, y: int, w: int, h: int, radius: int, rgb) -> bytes:
    """A wt-0x21 solid rounded rectangle in `rgb` (the 0x21 case draws entirely in fillA)."""
    _donor(data, CAPSULE_DONOR, CAPSULE_STOCK)
    stock = records.decode_record(CAPSULE_STOCK, 0)
    from .recipe_ops import AddLowVoltsControl       # lazy: recipe_ops will import the page op
    frag = AddLowVoltsControl._box_record(
        _u16("x", x), _u16("y", y), _u16("w", w), _u16("h", h),
        stock.colors[0][2], _u16("radius", radius))
    pairs = {"fillA": rgb, "fillA2": rgb}
    return _finish(_set_align(_paint(frag, pairs), ALIGN_NO_SHIFT), 0x21, [x, y, w, h, radius],
                   _expect(pairs))


def hline(data: bytes, x: int, y: int, w: int, rgb) -> bytes:
    """A wt-2 horizontal 1 px line from `(x, y)` to `(x+w, y)` inclusive (`FUN_00254064`'s
    `y0 == y1` span; Finding 72)."""
    frag = _absolute(_donor(data, HLINE_DONOR, HLINE_STOCK))
    for i, v in enumerate((x, y, w, 0)):
        frag = _set_geom(frag, i, v)
    pairs = {"fillA": rgb, "fillA2": rgb}
    return _finish(_set_align(_paint(frag, pairs), ALIGN_NO_SHIFT), 2, [x, y, w, 0],
                   _expect(pairs))


def label(data: bytes, text: str, x: int, y: int, w: int, h: int, *, font: int, rgb,
          h_align: int = 2, v_align: int = 2, bg=None) -> bytes:
    """A wt-11 label in box `(x, y)-(x+w, y+h)`, ink `rgb`, font `font`, R49 `h_align`/`v_align`
    (2 centre, 3 right/bottom, else left/top). `bg=None` keeps the background transparent
    (block-B mode 5); `bg=rgb` paints the box opaque.

    One line only: a `'\n'` is refused, because the R49 line pitch is fixed (285[1]'s 20 px, no
    font-height add) and would overlap any font whose cell is taller. Refuses a box the text
    would not fit: the panel clips wtype-11 text to the box inclusively (Finding 69/70) and wraps
    a line wider than `w + 1` (Finding 39; `metrics.wrap_lines`), so the text must measure
    `<= w + 1` and the font's cell height `<= h + 1`. The header align is 1 (`ALIGN_NO_SHIFT`):
    the text's own placement is the R49 `h_align`/`v_align`."""
    _donor(data, LABEL_DONOR, LABEL_STOCK)
    if w <= 0 or h <= 0:
        raise PageDrawError(f"a visible wtype-11 label needs a box, got {w}x{h}")
    for name, v in (("h_align", h_align), ("v_align", v_align)):
        if v not in (0, 1, 2, 3):
            raise PageDrawError(f"{name} must be 0..3, got {v}")
    try:
        tb = text.encode("latin-1")
    except UnicodeEncodeError as e:
        raise PageDrawError(f"label text must be latin-1: {text!r}") from e
    if not 0 < len(tb) < 0xFF:
        raise PageDrawError(f"label length must be 1..254, got {len(tb)}")
    if "\n" in text:
        raise PageDrawError(f"multi-line labels are not supported (fixed 20 px pitch): {text!r}")
    fm = metrics.font_metrics(data, _check_font(font))
    tw = metrics.text_width(data, font, text)
    if tw is None:
        raise PageDrawError(f"font {font} cannot draw {text!r} (missing glyph)")
    if tw > w + 1:
        raise PageDrawError(f"{text!r} is {tw} px in font {font}; box {w} would wrap it")
    if fm.height > h + 1:
        raise PageDrawError(f"font {font} is {fm.height} px tall; box {h} would clip it")
    from .recipe_ops import AddLowVoltsControl       # lazy: see capsule
    frag = AddLowVoltsControl._label_record(
        "?" * len(tb), _u16("x", x), _u16("y", y), _u16("w", w), _u16("h", h), font,
        _LABEL_R39, h_align, v_align, _LABEL_LINE_HEIGHT, _LABEL_ADDS)
    lo = _decode(frag).label_offset
    frag = frag[:lo] + tb + frag[lo + len(tb):]
    pairs = {"fillA": rgb, "fillA2": rgb, "fillB": bg, "fillB2": bg}
    out = _finish(_set_align(_paint(frag, pairs), ALIGN_NO_SHIFT), 11, [x, y, w, h],
                   _expect(pairs))
    r = _decode(out)
    if not (r.label == text and r.font == font and r.h_align == h_align
            and r.v_align == v_align and r.align == ALIGN_NO_SHIFT):
        raise PageDrawError(f"authored label {text!r} failed its self-decode: {out.hex()}")
    return out


def _check_font(font: int) -> int:
    if not isinstance(font, int) or not 0 <= font <= 0xFE:
        raise PageDrawError(f"font must be a real slot 0..0xFE (0xFF inherits), got {font!r}")
    return font


def glyph(data: bytes, char: str, x: int, y: int, w: int, h: int, *, font: int, rgb,
          bg=None) -> bytes:
    """A wt-11 icon: one character of font `font`, centred in `(x, y)-(x+w, y+h)`."""
    if len(char) != 1:
        raise PageDrawError(f"glyph takes one character, got {char!r}")
    return label(data, char, x, y, w, h, font=font, rgb=rgb, h_align=2, v_align=2, bg=bg)


def readout(data: bytes, x: int, y: int, w: int, h: int, *, value_slot: int, width: int,
            places: int, font: int, rgb, bg, align: int = 3,
            justify: str = "right") -> tuple[bytes, bytes]:
    """`(directive_record, wt8_record)`: a live number from display cell `value_slot`, formatted
    `width`/`places` (`readout.directive_bytes`, GUICODE Finding 65), drawn in `font` with ink
    `rgb` on an opaque `bg` inside the box `(x, y)-(x+w, y+h)`.

    `justify` places the digits within the `width` field (`readout.JUSTIFY`): the panel pads a
    short value with spaces (Finding 65, `0x002529DC`), so a right-justified field drawn with a
    centred pen shifts the digits right by the padding. A centred readout wants "centre".

    A wt-8 carries a PEN, not a box (the donor has geom0/1 only): `FUN_002557C0` anchors the text
    by the header align (`x0 = pen` / `pen - width/2` / `pen - (width-1)`) and draws its cell
    top at `pen_y - font[0x0b]` (`metrics.py`). The pen is derived from the box so the cell sits
    vertically centred and the text left / centred / right-aligned in it. The wt-8 keeps the
    donor's `header[4] & 0x20` (live-redraw enrollment) and names `value_slot`."""
    if align not in (1, 2, 3):
        raise PageDrawError(f"readout align must be 1..3 (0 inherits state+9), got {align}")
    if w <= 0 or h <= 0:
        raise PageDrawError(f"readout needs a box, got {w}x{h}")
    _u16("value_slot", value_slot)
    _donor(data, DIRECTIVE_DONOR, DIRECTIVE_STOCK)
    _donor(data, READOUT_DONOR, READOUT_STOCK)
    fm = metrics.font_metrics(data, _check_font(font))
    if fm.height > h + 1:
        raise PageDrawError(f"font {font} is {fm.height} px tall; box {h} would not hold it")
    pen_x = {1: x, 2: x + w // 2, 3: x + w}[align]
    pen_y = y + fm.top + (h + 1 - fm.height) // 2
    try:
        directive = _readout.directive_bytes(width, places, justify=justify)
        obj = _readout.author_readout_object(data, x=_u16("pen_x", pen_x),
                                             y=_u16("pen_y", pen_y), display_slot=value_slot,
                                             directive=directive, donor=READOUT_DONOR)
    except _readout.ReadoutError as e:
        raise PageDrawError(str(e)) from e
    d_rec, r_rec = records.object_records(obj, 0)
    dfrag = obj[d_rec.offset:d_rec.end]
    rfrag = obj[r_rec.offset:r_rec.end]
    rfrag = _set_align(_set_font(_absolute(rfrag), font), align)
    pairs = {"fillA": rgb, "fillA2": rgb, "fillB": _rgb(bg), "fillB2": bg}
    rfrag = _finish(_paint(rfrag, pairs), 8, [pen_x, pen_y], _expect(pairs), align=align)
    d, r = _decode(dfrag), _decode(rfrag)
    if not (d.wtype == 9 and d.fmt == tuple(directive) and d.header[1] == 0x50
            and r.value_slot == value_slot and r.font == font and r.align == align
            and r.header[4] & 0x20):
        raise PageDrawError(f"authored readout failed its self-decode: {dfrag.hex()} "
                            f"{rfrag.hex()}")
    return dfrag, rfrag


def touch(data: bytes, x: int, y: int, w: int, h: int, touch_id: int) -> bytes:
    """A wt-12 touch region `(x, y)-(x+w, y+h)` carrying `touch_id`, the trailing R50 u16
    (`aed & 0x1000`; `records.decode_record` consumes it without surfacing it). The touch id ->
    group map is global, so the id fires its group whatever object hosts the record.

    The donor's header align is 0, but wt-12 is a path-1 wtype (`layout._align_path1`, mask
    `0x600C7C9C`), so align 0 would inherit `state+9` and a preceding align-2/3 record (a
    centred or right-aligned readout) would shift the hit box; the primitive writes 1."""
    frag = _donor(data, TOUCH_DONOR, TOUCH_STOCK)
    if w <= 0 or h <= 0:
        raise PageDrawError(f"a touch region needs a box, got {w}x{h}")
    for i, v in enumerate((x, y, w, h)):
        frag = _set_geom(frag, i, v)
    frag = frag[:-2] + struct.pack("<H", _u16("touch_id", touch_id))
    frag = _finish(_set_align(frag, ALIGN_NO_SHIFT), 12, [x, y, w, h], {})
    if len(frag) != len(TOUCH_STOCK) or frag[-2:] != struct.pack("<H", touch_id):
        raise PageDrawError(f"authored touch failed its self-decode: {frag.hex()}")
    return frag


def stream(recs: list[bytes]) -> bytes:
    """`<count>` + records, padded to the 4-byte object alignment (`cornermark._object`)."""
    if not 0 < len(recs) <= 0xFF:
        raise PageDrawError(f"an object holds 1..255 records, got {len(recs)}")
    try:
        return cornermark._object(list(recs))
    except cornermark.CornerMarkError as e:
        raise PageDrawError(str(e)) from e
