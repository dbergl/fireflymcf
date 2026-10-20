"""Byte-exact widget-record decoder — a Python replay of GuiCode's `FUN_00252CB0`.

`proven-from-code` (GUICODE.md Findings 10-12) and validated end-to-end: an object in the
`+0x46148` table is `<count><records...><pad to 4-byte boundary>`, so a correct decoder
walks exactly `count` records and lands 0-3 bytes short of the next object. This decoder
does that for **every object in all three corpus builds** — 1421/1421 (13.6), 1076/1076
(14.7), 1200/1200 (11.23), zero derailments (`tests/test_records.py`). That whole-corpus
landing is the oracle; nothing here is fitted to a screen.

Unlike `guidata.py`'s heuristic record walk (whose `TAILS` sizes were chosen to land on
boundaries for the *decodable* screens only), this follows the actual instruction-level read
order, so it yields each record's real fields: widget type, the six layout coordinates, the
container child index, and the label string.

The read order was traced from the decompilation; the reader primitives are
`FUN_0025211C` (u8) and `FUN_00252128` (u16 LE), `FUN_00252140` (u24 LE = inline BGR colour).
Type-membership gates use the masks resolved from the GuiCode blob (e.g. colour blocks
`0x2003A97F` / `0x600BA969`, matching Finding 11).
"""
from __future__ import annotations

from dataclasses import dataclass, field


def _tset(t: int, lo: int, hi: int) -> bool:
    """Widget type `t` is in the compile-time type set (`lo` for types 0-31, `hi` for 32-63)."""
    return bool(lo & (1 << t)) if t < 32 else bool(hi & (1 << (t - 32)))


# Coordinate slots seeded by the six geometry u16s (FUN_00252CB0 -> pen accumulators
# +0x6bec..+0x6bf6, consumed by the layout engine FUN_00257BFC). Gated by header byte2
# bits 1/2/4/8 and byte6 bits 0x10/0x20.
_PEN_BITS = ((2, 0x01), (2, 0x02), (2, 0x04), (2, 0x08), (6, 0x10), (6, 0x20))


# The render-dispatch type -> a short name, read off the 24-case switch in GuiCode's
# FUN_00256C54 (`docs/GUICODE.md`, "Finding 10 completed"). `render+0x8a = firstByte & 0x3f` is
# the selector, so `wtype` indexes this directly. Values not listed fall to the switch default
# (nothing drawn). `inferred-from-code` for the names, `proven-from-code` for the dispatch.
WTYPES = {
    0x00: "icon",              # single icon / char
    0x01: "bar",               # filled bar/shape
    0x02: "line",              # a 1 px LINE, not a rectangle: `FUN_00254064` branches on
                               # `x0 == x1` to a vertical span and `y0 == y1` to a horizontal one,
                               # both inclusive, and on neither to a run-length slope walk. The
                               # "two fill styles" are two DRAWERS -- flag bit 2 picks the styled
                               # `FUN_00254350`, unexercised in this corpus (GUICODE Finding 72)
    0x03: "button",            # rounded-rect button body
    0x04: "fill",              # a plain FILLED RECTANGLE, inclusive on both axes -- case 4 has no
                               # per-record gate, only the global draw-enable at state+0x704C that
                               # every case tests. The gated one is 0x13 (GUICODE Finding 71)
    0x05: "container",         # container (recurse)
    0x06: "switch",            # container that value-matches the register table
    0x07: "clip",              # clip / scroll region
    0x08: "text",              # single-line text / numeric value
    0x09: "spacer",            # no draw case -- but it is the NUMBER-FORMAT DIRECTIVE: its 4
                               # trailing bytes set the persistent format state the wtype-8
                               # readouts that follow it draw with (GUICODE Finding 65; `fmt`)
    0x0A: "image",             # image-button
    0x0B: "text_multiline",    # multi-line text label
    0x0C: "touch",             # touch-region registration (hit-box + action id)
    0x0D: "text_dynamic",      # dynamic value-driven multi-line text
    0x0E: "flow",              # flow/clip anchor (advances pen, no draw)
    0x10: "dot",               # filled dot / rounded indicator
    0x11: "dot",
    0x12: "image_touch",       # image-button + touch registration (alias of 0x0A)
    0x13: "gated_fill",        # a fill GATED on the transparent-interior bit: case 0x13 fills only
                               # `if ((flags & 3) >> 1) == 0`, i.e. when `hollow` is clear. All 27
                               # in the corpus are hollow, so none of them ever draws -- which is
                               # why the render omitting them is correct, not a gap
    0x20: "gauge_well",        # tank / bar gauge well (compound)
    0x21: "capsule",           # rounded-capsule fill (button body with round ends)
    0x22: "dot",
    0x23: "dot",
    0x2E: "switch",            # alias of 0x06
    0x2F: "special",           # specialised, undigested
}


def wtype_name(wtype: int) -> str:
    """Short name for a widget-dispatch type (`WTYPES`), or 'unknown_<hex>' for a default case."""
    return WTYPES.get(wtype, f"unknown_{wtype:#04x}")


@dataclass
class WRecord:
    """One decoded widget record. Offsets are file offsets."""
    offset: int
    size: int
    wtype: int                       # header byte0 & 0x3f — the render dispatch type
    header: bytes                    # the 8 header bytes
    geom: list                       # six layout coords [x0,y0,x1,y1,x2,y2]; None = absent
    geom_off: list = field(default_factory=lambda: [None] * 6)  # file offset of each present geom
                                     # u16 (None where absent). Captured mid-decode (the decoder's
                                     # cursor at R23-R28), so it is correct regardless of which
                                     # presence-masked fields precede the geometry loop; exposed
                                     # with a round-trip guard by `geom_offset(data, rec, i)`.
    child_index: int | None = None   # container child (type 5/6/0x2e) -> +0x46148 index
    child_index_off: int | None = None  # file offset of the R48 child_index u16 (None if absent).
                                     # Captured mid-decode (the decoder's cursor at R48), so it is
                                     # correct regardless of which presence-masked fields precede it;
                                     # `child_index_offset(data, rec)` exposes it with a round-trip
                                     # guard. Lets a caller repoint/null a container's child (repoint
                                     # to a clone, or write 0xFFFF = the firmware's no-child sentinel).
    label: str | None = None         # length-prefixed label string, if any
    label_offset: int | None = None
    font: int | None = None          # R38 font selector -> glyphs.py slot; None = inherit (0xff)
    font_off: int | None = None      # file offset of the R38 selector byte, when the field is
                                     # PRESENT -- including when it holds 0xff and `font` is
                                     # therefore None. That is the case worth having: an inherit
                                     # slot can be given a real font by a one-byte same-size edit.
                                     # `font_offset(data, rec)` exposes it with a round-trip guard.
    base_modes: list = field(default_factory=list)   # per-coord base-reference mode (0..3)
    in_regs: list = field(default_factory=list)       # per-coord register to add from (0=none)
    out_regs: list = field(default_factory=list)      # per-coord register to store to (0=none)
    align: int = 0                   # text alignment (state+9 = header[3]>>6): 0 left,2 centre,3 right
    value_slot: int | None = None    # R40/41/42 index into the variable table (switch/value source)
    count: int | None = None         # R45 u16 -> record +0x94: a type-6/0x2e switch's candidate
                                     # count (the firmware scans [child_index, child_index+count));
    count_off: int | None = None     # file offset of the R45 count u16 (None if absent), captured
                                     # mid-decode like child_index_off so `switchpin` can edit it
                                     # None for non-switch records that do not carry the field
    corner_mask: int | None = None   # R43/R44 byte -> state+0x8b: the 8-bit octant mask the shape
                                     # renderer FUN_00256C54 passes to the circle primitive
                                     # FUN_00254F26 for the dot/circle widget (type 0x10); 0xff is a
                                     # full circle, a bit subset is an arc. None when absent.
    h_align: int | None = None       # R49 byte -> state+0x94: multi-line text per-line h-align
    v_align: int | None = None       # R49 byte -> state+0x95: multi-line text block v-align in the
                                     # box (2 centre, 3 bottom, else top; FUN_002559A0 @0x255C44)
    line_height: int | None = None   # R49 byte -> state+0x96: inter-line advance for multi-line text
    line_height_adds_font: int | None = None   # R49 byte -> state+0x97: when non-zero the drawer
                                     # adds the FONT CELL HEIGHT to line_height once, before the
                                     # first advance (FUN_002559A0 0x255a0c-0x255a26), so the
                                     # effective advance is line_height + font[0x07]. See
                                     # `effective_line_height`.
    fmt: tuple | None = None         # R47 (wtype 9 only): (mode, flags, width, places) -> render
                                     # state +0x8F/+0x8E&flags/+0x8C/+0x8D. PERSISTENT along the
                                     # screen walk, reset only at the screen root (FUN_00258650
                                     # 0x258724-0x258734), so a wtype-8 readout draws with the last
                                     # directive walked before it. GUICODE Finding 65.
    fmt_off: int | None = None       # file offset of those 4 bytes
    value_slot_off: int | None = None  # file offset of the value_slot u16 (last-write-wins R40/41/
                                     # 42), or None. `value_slot_offset(data, rec)` exposes it with a
                                     # round-trip guard, to repoint a widget's status/value variable.
    colors: list = field(default_factory=list)   # (role, mode, value) colour fields, read order
    color_off: list = field(default_factory=list)  # file offset of each colour field's INLINE value,
                                     # parallel to `colors` (None where the mode carries no inline
                                     # value -- 0/4 inherit). Captured mid-decode like geom_off, so it
                                     # is correct regardless of preceding presence-masked fields;
                                     # `color_offset(data, rec, role)` exposes it with a round-trip
                                     # guard. Lets a caller repoint a mode-7 fill index (recolour).
    # Cursor positions the decoder passes whether or not the field there is present, so a
    # caller can ADD a field (set its gate bit, insert its bytes here) without replaying the
    # decoder (`absrecord`). File offsets, like the others.
    aux_off: int | None = None       # cursor at R18: the R18-R20 register-index bytes start here
    geom_base_off: int | None = None  # cursor at R23: the geometry loop starts here
    r42_off: int | None = None       # cursor at the R42 gate: R42's u16 is (or would be) here

    @property
    def end(self) -> int:
        return self.offset + self.size

    @property
    def corner_radius(self) -> int | None:
        """The corner/circle radius, or `None` if this record carries no fifth geometry slot.

        The six geometry u16s seed state `+0x2a..+0x34`; the fifth (`geom[4]` -> state `+0x32`)
        is the **corner radius** the shape renderer `FUN_00256C54` reads for the rounded
        widgets — the rounded rectangle (0x20), the capsule (0x21), the button body (0x03), and
        the circle/dot (0x10). It is not a coordinate; `geom[0..3]` are the box, `geom[4]` the
        radius. `GUICODE.md` records the primitive (`FUN_00254F26`) it drives."""
        return self.geom[4] if len(self.geom) > 4 else None

    @property
    def is_dynamic_text(self) -> bool:
        """wtype 13 — the dynamic-string widget. Its on-screen text is the runtime value
        of `value_slot`'s variable-table entry (`layout.variable_register`), NOT an inline
        label. Corpus-invariant: every wtype-13 record in all three builds carries a
        `value_slot` and no inline label (48/75/35 in 11.23/13.6/14.7). This is the widget
        the AES/AGS run-time-label selection targets — the ELECTRICAL caption
        (slot 269 -> SRAM 0x20039DEC) and the AES/AGS mode indicator (slot 129 ->
        0x20039B9C), both type-9 string variables that GuiCode's `FUN_00247BF8` copies
        from the received CAN config (GUICODE Finding 31). The slot's variable *type*
        (9 = string, 1 = bool-selected string) is resolved from the GuiData variable table,
        which this byte-only decoder does not read; use `layout.variable_register`."""
        return self.wtype == 13


def decode_record(data: bytes, off: int, loader_r35: bool = False) -> WRecord:
    """Decode the record at file offset `off`. Raises IndexError past the buffer.

    `loader_r35` selects the loader's variant of the R35 field gate. cli.bin's record decoder
    (`FUN_20009cd8`) reads the R35 cursor byte whenever header byte-5 bit `0x80` is set, while
    the panel's (`FUN_00252CB0`, the default here) additionally gates it on
    `(uV16 & 0x2901) or (aee & 0x40)` — the sole decoder difference between the two, found by
    disassembling both (`MCF-FORMAT.md` §4.2). It matters only for the loader's own GUI blob
    (`subcontainer.loader_gui_screens`); leave it False for GuiData.
    """
    p = [off]

    def u8() -> int:
        v = data[p[0]]; p[0] += 1; return v

    def u16() -> int:
        v = data[p[0]] | (data[p[0] + 1] << 8); p[0] += 2; return v

    def u24() -> None:
        p[0] += 3

    hdr = bytes(data[off:off + 8]); p[0] = off + 8            # R1-R8 header
    b0, b1, b2, b3, b4, b5, b6, b7 = hdr
    t = b0 & 0x3f
    aed = (1 << t) if t < 32 else 0
    aee = (1 << (t - 32)) if t >= 32 else 0

    if b7 & 0x20:                                             # R9-R10 clip geometry (+0x50..)
        m = u8()
        for bit in (1, 2, 4, 8):
            if m & bit:
                u16()

    uV18 = uV16 = 0
    if b7 & 0x40:                                             # R11-R12 colour-mode bytes
        uV18 = u8(); uV16 = u8()

    colors: list = []           # (role, mode, value): the widget's colour fields, in read order
    color_off: list = []        # file offset of each colour field's inline value (parallel to colors)

    def colour(role: str, mode: int) -> None:
        # mode 3 = inline BGR u24; 6 = palette index (u8); 7 = colour-table index (u16);
        # others (0/4 = inherit, etc.) carry no inline value here.
        if mode == 3:
            color_off.append(p[0])
            b0_, b1_, b2_ = data[p[0]], data[p[0] + 1], data[p[0] + 2]; p[0] += 3
            colors.append((role, 3, b0_ | (b1_ << 8) | (b2_ << 16)))
        elif mode == 6:
            color_off.append(p[0])
            colors.append((role, 6, u8()))
        elif mode == 7:
            color_off.append(p[0])
            colors.append((role, 7, u16()))
        else:
            color_off.append(None)
            colors.append((role, mode, None))

    if _tset(t, 0x2003a97f, 0x7fff):                          # colour block A (fill)
        if (uV18 & 3) == 0:    colour("fillA", b3 & 7)
        if (uV18 & 0x30) == 0: colour("fillA2", (b5 >> 1) & 7)
    if _tset(t, 0x600ba969, 0x7ffd):                          # colour block B (text / border)
        if (uV16 & 3) == 0:    colour("fillB", (b3 >> 3) & 7)
        if (uV16 & 0x30) == 0: colour("fillB2", (b5 >> 4) & 7)
    if _tset(t, 0x00040400, 0) and (b6 & 2):                  # R17: the +0x94 count field
        # 3 bytes, written for types 10/18 only (mask 0x00040400): the count a type-6 switch
        # scans and a type-10/18 image widget passes to FUN_00255F80. Consumed here (byte-exact,
        # so the oracle is satisfied); the value is not surfaced because in this corpus every
        # switch resolves to a single child (GUICODE Finding 15).
        u24()

    # R18-R20 aux bytes pack the per-coord register indices (2 bits each: in, out, in, out).
    aux_off = p[0]
    a18 = u8() if b0 & 0x40 else 0
    a19 = u8() if b0 & 0x80 else 0
    a20 = u8() if b7 & 1 else 0
    if b6 & 1: u8()                                           # R21
    in_regs = [a18 & 3, (a18 >> 4) & 3, a19 & 3, (a19 >> 4) & 3, a20 & 3, (a20 >> 4) & 3]
    out_regs = [(a18 >> 2) & 3, a18 >> 6, (a19 >> 2) & 3, a19 >> 6, (a20 >> 2) & 3, a20 >> 6]
    # base-reference modes: header byte1 -> coords 0-3, byte7 -> coords 4-5.
    base_modes = [b1 & 3, (b1 >> 2) & 3, (b1 >> 4) & 3, (b1 >> 6) & 3,
                  (b7 >> 1) & 3, (b7 >> 3) & 3]
    if b6 & 8:                                                # R22
        u16(); u8(); u8()

    geom: list = [None] * 6                                   # R23-R28 the six layout coords
    geom_off: list = [None] * 6
    geom_base_off = p[0]
    for i, (byte, bit) in enumerate(_PEN_BITS):
        if (b2 if byte == 2 else b6) & bit:
            geom_off[i] = p[0]
            geom[i] = u16()
    for bit in (0x10, 0x20, 0x40, 0x80):                      # R29-R32 value/index slots
        if b2 & bit:
            u16()
    if b6 & 0x40: u16()                                       # R33-R34
    if b6 & 0x80: u16()

    # --- tail (all type-gated; line refs in scratchpad parse.txt) ---
    if loader_r35:                                            # R35, loader FUN_20009cd8 variant
        if b5 & 0x80: u8()                                    # read regardless of the outer gate
    elif (uV16 & 0x2901) or (aee & 0x40):                     # R35, panel FUN_00252CB0
        if b5 & 0x80: u8()
    if (aed & 4) and (b4 & 0x10):                             # R36
        u8()
    cnt = u8() if ((aed & 0x801) or (aee & 0x40)) else 1      # R37 label/list count
    font_sel = None
    font_off = None
    if (0x2000a961 & aed) or (aee & 0x6cc0):                  # R38 font selector(s)
        # FUN_00252CB0 lines 659-664: reads 1 byte (3 for type 0x26); a byte != 0xff sets the
        # current font (`state[2] = byte + 1` as a pointer-table index), 0xff leaves it — the
        # font is persistent across records. The raw byte is the glyphs.py slot (render.py's
        # `01 <slot> ff` is this byte; hardware-verified on the Bath Fan icon 0x18/caption 0x0f).
        font_off = p[0]
        sels = [u8() for _ in range(3 if t == 0x26 else 1)]
        if sels[0] != 0xff:
            font_sel = sels[0]
    if aed & 0x2800: u8()                                     # R39
    # R40/R41/R42: the value-slot index (last write wins), indexing the variable table
    # DAT_00253D04/D08 to set state+0x24 (data ptr) and state+0x91 (type) — the variable a
    # type-6 switch / value widget reads via FUN_00251E38 (FUN_00252CB0 lines 671/683/694).
    value_slot = None
    value_slot_off = None
    if aee & 0x2cf0: value_slot_off = p[0]; value_slot = u16()  # R40
    if (aed & 0x2140) or (aee & 0x4000): value_slot_off = p[0]; value_slot = u16()  # R41
    r42_off = p[0]
    if (b4 & 0x20) and ((aed & 0x00070c3f) or (aee & 0xf)):   # R42
        value_slot_off = p[0]; value_slot = u16()
    corner_mask = None
    if aed & 0x00030008:                                      # R43 -> state+0x8b (types 3/0x10/0x11)
        # FUN_00252CB0 line 700 (`+0x8b = FUN_0025211c(cursor)`): a byte the shape renderer
        # FUN_00256C54 reads as an 8-bit octant mask for the circle/dot widget (type 0x10) --
        # each bit enables one octant of the circle primitive FUN_00254F26 (0xff = full circle,
        # a subset = an arc/partial round). `GUICODE.md` Finding on the shape primitives.
        corner_mask = u8()
    if aee & 0xc:                                             # R44 -> state+0x8b (types 0x22/0x23)
        corner_mask = u8() & 3
    count = None
    count_off = None
    if (aed & 0x40) or (aee & 0x4000):                        # R45 -> +0x94 switch candidate count
        # Written in FUN_00252CB0 at 0x00253B2A (strh …,[r4,#0x94]) under exactly this mask
        # (wtype 6 or 0x2e), and read by the switch scan at 0x00257B3A (ldrsh r3,[r6,#0x94]). The
        # decoder already consumed this u16 to stay byte-aligned; it is now surfaced as the real
        # candidate count so `layout._switch_child` can bound its scan (which was an over-scanning
        # repeat-value heuristic — GUICODE Finding 16 note; see `WRecord.count`).
        count_off = p[0]
        count = u16()
    if aed & 0x4080: u8()                                     # R46
    fmt = None
    fmt_off = None
    if aed & 0x200:                                           # R47 -> the format directive (wtype 9)
        # FUN_00252CB0 0x00253C62-0x00253CD2: byte0 -> state+0x8F (display mode), byte1 -> flag
        # bits (bits 2-3 -> +0x8E justification; bits 0/1/4/5 -> state-word bits 0x8/0x10/
        # 0x1000/0x2000), byte2 -> +0x8C (field width), byte3 -> +0x8D (decimal places). All four
        # are always consumed: the bit-5 branch at 0x00253D18 rejoins at 0x00253CBE.
        fmt_off = p[0]
        fmt = (u8(), u8(), u8(), u8())
    child_index = None
    child_index_off = None
    if (aed & 0x00040460) or (aee & 0x4000):                  # R48 container child index -> +0x28
        child_index_off = p[0]                                # cursor at R48: the child_index u16
        child_index = u16()
    h_align = v_align = line_height = line_height_adds_font = None
    if aed & 0x2800:                                          # R49 -> state+0x94/0x95/0x96/0x97
        # FUN_00252CB0 stores these 4 bytes at 0x00253D2E/D38/D42/D4C as strb …,[r4,#0x94..0x97]:
        # (h-align, v-align, line-height, flag). The multi-line text drawer FUN_002559A0 reads
        # +0x95 to place the text block vertically inside the record's box (2 centre, 3 bottom,
        # else top; 0x00255C44) and +0x94 per line horizontally. Consumed here before (byte-
        # aligned) but discarded on the assumption every label is single-line; now surfaced.
        h_align = u8(); v_align = u8(); line_height = u8(); line_height_adds_font = u8()
    if aed & 0x1000: u16()                                    # R50
    if aee & 0x8000: u16()                                    # R51

    label = None; label_off = None
    if (aed & 0x801) or (aee & 0x40):                         # final loop: length-prefixed strings
        parts = []
        for _ in range(cnt):
            n = u8()
            if n == 0xff:
                n = u16()
            if label_off is None:
                label_off = p[0]
            parts.append(bytes(data[p[0]:p[0] + n]))
            p[0] += n
        p[0] += 1                                            # trailing NUL
        raw = b"".join(parts)
        # latin-1, not ascii. A label is a byte string the text drawers index the font with
        # (`FUN_002557C0` -> `glyphs.Font.glyph_index(byte)`), so a byte above 0x7f is an
        # ordinary character of whatever font the R38 selector names -- an icon, a degree sign,
        # or in an icon font the icon itself. Decoding as ascii-or-nothing dropped 47 of 13.6's
        # 2,844 labelled records (39 on 11.23, 29 on 14.7) to `None`, which read as "this record
        # has no label" when it means "this label is not ascii". latin-1 is exact and total for
        # a byte string, so every label now round-trips: `chr(b)` for each byte, and
        # `label.encode('latin-1')` gives the bytes back.
        label = raw.decode("latin-1")

    # alignment: state+9 = header[3]>>6 (FUN_00252CB0 line 589), 0 left / 2 centre / 3 right.
    return WRecord(offset=off, size=p[0] - off, wtype=t, header=hdr, geom=geom,
                   geom_off=geom_off,
                   child_index=child_index, child_index_off=child_index_off,
                   label=label, label_offset=label_off,
                   base_modes=base_modes, in_regs=in_regs, out_regs=out_regs, font=font_sel,
                   font_off=font_off,
                   align=b3 >> 6, value_slot=value_slot, value_slot_off=value_slot_off,
                   count=count, count_off=count_off, corner_mask=corner_mask,
                   h_align=h_align, v_align=v_align, line_height=line_height,
                   line_height_adds_font=line_height_adds_font, colors=colors,
                   fmt=fmt, fmt_off=fmt_off,
                   color_off=color_off, aux_off=aux_off, geom_base_off=geom_base_off,
                   r42_off=r42_off)


def geom_offset(data: bytes, rec: WRecord, i: int) -> int | None:
    """File offset of `rec`'s geometry slot `i` (0-5) u16, or `None` if that slot is absent.

    Recovered by **replaying the decoder's cursor** the same way `child_index_offset` does:
    `decode_record` records where it reads each present geometry u16 (`WRecord.geom_off`), so
    the offset is correct no matter which presence-masked fields precede the R23-R28 loop.

    Round-trip guarded: re-reads the u16 at the offset and raises `ValueError` unless it equals
    `rec.geom[i]`, so a replay bug can never return a wrong offset silently. Use it to move a
    record (rewrite its x/y) without hardcoding a fixed field position.
    """
    if not 0 <= i < 6:
        raise ValueError(f"geometry slot index {i} must be 0..5")
    if rec.geom[i] is None:
        return None
    off = decode_record(data, rec.offset).geom_off[i]
    if off is None:
        raise ValueError(
            f"record @ {rec.offset:#x} has geom[{i}]={rec.geom[i]} but no decoded offset")
    got = data[off] | (data[off + 1] << 8)
    if got != rec.geom[i]:
        raise ValueError(
            f"geom_offset {off:#x} reads {got}, expected {rec.geom[i]} (record @ {rec.offset:#x})")
    return off


def color_offset(data: bytes, rec: WRecord, role: str) -> int | None:
    """File offset of `rec`'s colour field `role` (`'fillA'`/`'fillA2'`/`'fillB'`/`'fillB2'`)
    inline value, or `None` if that role is absent or carries no inline value (mode 0/4 inherit).

    Recovered by **replaying the decoder's cursor** the same way `geom_offset` does
    (`WRecord.color_off`), so it is correct no matter which presence-masked fields precede the
    colour blocks. Round-trip guarded: re-reads the value at the offset (u16 for mode 7, u8 for
    mode 6, u24 for mode 3) and raises `ValueError` unless it equals the decoded value, so a
    replay bug can never return a wrong offset silently. Use it to repoint a mode-7 fill index
    (recolour a button) without hardcoding a field position.
    """
    fresh = decode_record(data, rec.offset)
    for (r, mode, value), off in zip(fresh.colors, fresh.color_off):
        if r != role:
            continue
        if off is None:
            return None
        if mode == 7:
            got = data[off] | (data[off + 1] << 8)
        elif mode == 6:
            got = data[off]
        elif mode == 3:
            got = data[off] | (data[off + 1] << 8) | (data[off + 2] << 16)
        else:
            return None
        if got != value:
            raise ValueError(
                f"color_offset {off:#x} reads {got}, expected {value} for {role} "
                f"(record @ {rec.offset:#x})")
        return off
    return None


def value_slot_offset(data: bytes, rec: WRecord) -> int | None:
    """File offset of `rec`'s value_slot u16 (records R40/41/42), or `None` if it carries none.
    Replays the decoder cursor (`WRecord.value_slot_off`), round-trip guarded: re-reads the u16
    and raises `ValueError` unless it equals `rec.value_slot`. Use it to repoint a widget's
    status/value variable (e.g. a button's on/off indicator slot) without a hardcoded offset."""
    if rec.value_slot is None:
        return None
    off = decode_record(data, rec.offset).value_slot_off
    if off is None:
        raise ValueError(
            f"record @ {rec.offset:#x} has value_slot={rec.value_slot} but no decoded offset")
    got = data[off] | (data[off + 1] << 8)
    if got != rec.value_slot:
        raise ValueError(
            f"value_slot_offset {off:#x} reads {got}, expected {rec.value_slot} "
            f"(record @ {rec.offset:#x})")
    return off


def font_offset(data: bytes, rec: WRecord) -> int | None:
    """File offset of `rec`'s R38 font-selector byte, or `None` if the record carries no such
    field. Replays the decoder cursor (`WRecord.font_off`), round-trip guarded.

    Unlike the other offset helpers this returns an offset even when the decoded value is
    "absent": the selector is a single byte where `0xff` means *inherit the persistent font*, so
    `rec.font is None` still has a byte behind it. Writing a real slot number there is the
    documented one-byte font change -- and note the selector is **persistent**, so it also
    changes what every later record in the walk inherits until something sets it again.
    """
    off = decode_record(data, rec.offset).font_off
    if off is None:
        return None
    got = data[off]
    want = 0xFF if rec.font is None else rec.font
    if got != want:
        raise ValueError(
            f"font_offset {off:#x} reads {got:#04x}, expected {want:#04x} "
            f"(record @ {rec.offset:#x})")
    return off


def label_text(label: str) -> str:
    """A label's content with each LINE's own padding removed.

    A same-size rename pads the text to the stored field's width, and where that pad goes
    depends on how the record draws: a left-aligned label takes it all at the end (invisible),
    while a centred one takes it on both sides of its last line, because the panel centres each
    line by its padded width (`patch.repad` align="center"; owner-observed 2026-08-21). So a
    whole-string `.strip()` no longer recovers the text that was asked for -- `'Overflow\n  Valve '`
    strips to `'Overflow\n  Valve'`, which matches nothing.

    Use this for "is this the caption I set?" comparisons. It is not a decode step: nothing reads
    it back into the file, and the stored bytes keep their padding.
    """
    return "\n".join(line.strip() for line in label.split("\n"))


def object_records(data: bytes, obj_offset: int, loader_r35: bool = False) -> list[WRecord]:
    """Decode all `count` records of the object at `obj_offset` (`<count><records>`).

    `loader_r35` is passed to `decode_record`; see there. It is only for the loader's own GUI
    records, and defaults off so GuiData decoding is unchanged.
    """
    count = data[obj_offset]
    out: list[WRecord] = []
    pos = obj_offset + 1
    for _ in range(count):
        rec = decode_record(data, pos, loader_r35=loader_r35)
        out.append(rec)
        pos = rec.end
    return out


def child_index_offset(data: bytes, rec: WRecord) -> int | None:
    """File offset of `rec`'s container child-index u16 (records R48), or `None` if the record
    carries none. Recovered by **replaying the decoder's cursor** — `decode_record` walks every
    preceding presence-masked field and records where R48 is read (`WRecord.child_index_off`), so
    the offset is correct no matter which optional fields precede it; it is not the fixed
    `size - 2` (which only happens to be right because R49-R51 and the label loop are all absent
    for the container types 5/6/0x2e).

    Use it to repoint a container at a different child, or to **null** a child reference by
    writing `0xFFFF` there — the firmware's own no-child sentinel: a switch/container recursion
    on index `0xFFFF` calls `FUN_00258330(0xFFFF)`, which returns immediately (`0x00258336`) and
    draws nothing. `proven-from-code` (records R48; GUICODE Findings 10-12/16).

    Round-trip guarded: re-reads the u16 at the offset and confirms it equals `rec.child_index`,
    raising `ValueError` on any mismatch (a wrong offset can never be returned silently).
    """
    if rec.child_index is None:
        return None
    off = decode_record(data, rec.offset).child_index_off
    if off is None:                                           # decode disagrees the record has one
        raise ValueError(
            f"record @ {rec.offset:#x} has child_index {rec.child_index} but no decoded R48 offset")
    got = data[off] | (data[off + 1] << 8)
    if got != rec.child_index:
        raise ValueError(
            f"child_index_offset {off:#x} reads {got:#x}, expected {rec.child_index:#x} "
            f"(record @ {rec.offset:#x})")
    return off
