"""The v48 four-card ELECTRICAL page: object 829 (the compact AES frame) re-laid out as HOME's
2x2 card grid -- Batteries, Inverter, Auto Engine Start, Water Pump.

Pinned to the v47 shape: the frame's and the private AES cluster's wtype sequences, and every
edited geometry slot's prior value, are asserted before anything is written. Shared objects
(443, 440, 1409, 1421, 285, 393, 249, 251, 154, 526) are never edited; they move only through the
829 and cluster records that place them.

How a coordinate composes (`layout.Screen.walk`, FUN_00257BFC): `geom + reg[in_reg] + base`,
with separate x and y register files. 829[0] (the battery strip) writes regx/regy[1], which
829[3] ('Chassis' y) and 829[5] (the cluster container) read -- so the strip is kept as an
invisible, card-coloured anchor rather than deleted. The values below were solved against the
walk on the v47 bytes.

Draw order is stream order: the three new cards and then the four titles are PREPENDED (the
Water Pump card is the pump section's own box, recoloured in place). Every v47 record after them
sets the font, align and colour it draws with, so the shared controls draw exactly as in v47 and
the frame ends in v47's persistent state (tests/test_card_layout.py pins both).
"""
from __future__ import annotations

import struct

from . import aescompact, guidata, layout, records, relocate, switchbuild

CARD_FILL_INDEX = 8          # HOME's card colour: mode-7 index 8 = (39, 40, 98)
CARD_RADIUS = 5              # HOME's card radius (905[0] geom[4])
CARDS = (("Batteries", (70, 48, 356, 170)),
         ("Inverter", (436, 48, 354, 170)),
         ("Auto Engine Start", (70, 228, 356, 242)),
         ("Water Pump", (436, 228, 354, 242)))
TITLE_TEXTS = tuple(t for t, _ in CARDS)
TITLE_DX, TITLE_DY, TITLE_W, TITLE_H = 16, 2, 300, 28
TITLE_FONT = 14              # the 'House' / 'Water Pump' caption font
WHITE_BGR = (0xFF, 0xFF, 0xFF)

V47_FRAME_WTYPES = [33, 11, 5, 11, 5, 5, 6, 32, 32, 11, 11, 6, 12, 11, 5, 11, 12, 12, 9, 8, 0,
                    32, 11, 6, 12]
V47_CLUSTER_WTYPES = [5, 33, 12, 4, 11, 13, 6, 11, 5, 12, 33, 11, 12, 11]

# {record: {geom slot: (v47 value, v48 value)}}, signed as `layout._s16` reads them.
FRAME_EDITS = {
    0: {0: (200, 138), 1: (60, 80), 2: (460, 0), 3: (80, 0)},  # strip -> anchor: reg[1] = 138/80
    1: {0: (15, -60), 2: (90, 60)},                            # 'House' 78..138, y 108
    3: {0: (70, 21)},                                          # 'Chassis' 238..308
    5: {1: (94, 186)},                                         # cluster origin (138, 80+186)
    6: {0: (439, 452), 1: (154, 82)},                          # inverter pill; x 682..778 kept for v49 readouts
    8: {0: (2, 320), 1: (292, 252), 2: (228, 0), 3: (169, 0), 4: (5, 0)},
                                     # pump inset -> a 0x0, radius-0 anchor at x 760, clear of the
                                     # 'Water Pump' title: v50 drew its radius-5 blob over the e/r
    9: {0: (6, -257)},               # heading back to the content column at x 503
}                                                              # after the restore record's +20
CLUSTER_EDITS = {
    7: {1: (58, 31)},                                          # row anchor ('Triggers:' -> '')
    8: {0: (59, 0)},                                           # Low Volts at 138,400
    10: {0: (-59, 110), 1: (104, 0), 2: (220, 110)},           # AES gear capsule 248..358
    11: {0: (60, 8)},                                          # '  AES' at 256
    12: {0: (-60, -8), 2: (220, 110)},                         # gear touch 248..358
    13: {0: (110, 58)},                                        # gear icon at 306
}
FRAME_PUMP_BOX, FRAME_PUMP_INSET, FRAME_PUMP_HEADING, FRAME_HEATER_BOX = 7, 8, 9, 21
# The heater must already be the full-width Batteries-card bar (add_aes_batt_heat x/y/w/h); at the
# stock 122x173 at 672,290 it would sit on the Water Pump card.
HEATER_BOX = (84, 156, 328, 54)
# BMS command buttons (add_aes_batt_heat instances) after the v47 frame: each is the group
# (backing box, flow anchor, state switch, touch). One button (v48) is the full-width heater; two
# (v49) are the heater and Ignore Alarms side by side.
BUTTON_GROUP = [32, 11, 6, 12]
BUTTON_BOXES = {1: [HEATER_BOX],
                2: [(84, 156, 163, 54), (249, 156, 163, 54)]}
# Each button's switch takes -20,-20 to undo the +20,+20 of the restore record before it.
SWITCH_EDIT = {0: (0, -20), 1: (0, -20)}


def _button_bases(frame) -> list[int]:
    """Start index of every button group after the v47 frame's own records (21..)."""
    n = (len(frame) - FRAME_HEATER_BOX) // len(BUTTON_GROUP)
    return [FRAME_HEATER_BOX + k * len(BUTTON_GROUP) for k in range(n)]


def _frame_edits(frame) -> dict:
    edits = dict(FRAME_EDITS)
    for b in _button_bases(frame):
        edits[b + 2] = SWITCH_EDIT
    return edits
CLUSTER_TRIGGERS = 7
CLUSTER_PILL_SWITCH = 6
# The pump section's box (829[7]) stays as a 0x0 card-coloured anchor INSIDE its card: the card
# itself is prepended with the other three so every title is drawn after every card (v48 on the
# coach lost 'Water Pump' under the section box, which was drawn after the titles).
PUMP_ANCHOR = (440, 232)
# The restore record sits after the heater's flow anchor (829[22]), offset under the capsule the
# switch draws next, so its 0x0 white background pixel is covered. Before the anchor it would make
# the anchor itself inherit white and paint the button's corner pixel -- v48 on the coach showed
# exactly that dot; after it, the anchor inherits the card fill and its pixel is invisible.
RESTORE_DXY = (20, 20)
# The AES enable pill's state band (stock 154-161, shared with 16, 315, 795 and 1425): its two
# DISABLED arms fill index 8, the card colour, so the half vanished into the card. The cluster
# gets a private copy with those arms in the engine-status navy (1426[0]'s index 3).
PILL_BAND, PILL_BAND_COUNT = 154, 8
PILL_DISABLED_TEXT, PILL_DISABLED_FILL, PILL_STOCK_FILL = "DISABLED", 3, CARD_FILL_INDEX


class CardLayoutError(ValueError):
    pass


def _s16(v):
    return v - 0x10000 if v is not None and v >= 0x8000 else v


def _body(parts: list[bytes]) -> bytes:
    """`<count>` + records, unpadded: what `relocate.replace_object_records` takes."""
    return bytes([len(parts)]) + b"".join(parts)


def _edit_geom(data: bytes, rec, edits: dict) -> bytes:
    frag = bytearray(data[rec.offset:rec.end])
    for slot, (old, new) in edits.items():
        if _s16(rec.geom[slot]) != old:
            raise CardLayoutError("record at %#x geom[%d] is %s, expected %d"
                                  % (rec.offset, slot, _s16(rec.geom[slot]), old))
        struct.pack_into("<h", frag, records.geom_offset(data, rec, slot) - rec.offset, new)
    return bytes(frag)


def card_record(x: int, y: int, w: int, h: int, wtype: int = 33,
                radius: int = CARD_RADIUS) -> bytes:
    """An absolute rounded card in HOME's colour and radius: x,y base mode 0, w,h mode 1.

    wtype 33 (HOME's card, 905[0] made absolute) carries one mode-7 fill; wtype 32 (the
    pump/heater box shape, 829[7]) carries fillA and fillB, both mode 7 here -- header byte 3
    `0x7F` is align 1 with both modes 7, as the v47 inset outline's `0x3F` is align 0."""
    if wtype == 33:
        rec = (bytes([0x21, 0x50, 0x0F, 0x47, 0, 0, 0x10, 0])
               + struct.pack("<H", CARD_FILL_INDEX)
               + struct.pack("<hhhhh", x, y, w, h, radius))
    elif wtype == 32:
        rec = (bytes([0x20, 0x50, 0x0F, 0x7F, 0, 0, 0x10, 0])
               + struct.pack("<HH", CARD_FILL_INDEX, CARD_FILL_INDEX)
               + struct.pack("<hhhhh", x, y, w, h, radius))
    else:
        raise CardLayoutError("a card is wtype 32 or 33, not %d" % wtype)
    d = records.decode_record(rec, 0)
    fills = [(m, v) for role, m, v in d.colors if role in ("fillA", "fillB") and m]
    want = [(7, CARD_FILL_INDEX)] * (2 if wtype == 32 else 1)
    if not (d.wtype == wtype and d.size == len(rec) and d.base_modes[:2] == [0, 0]
            and d.align == 1 and [_s16(g) for g in d.geom[:5]] == [x, y, w, h, radius]
            and fills == want):
        raise CardLayoutError("authored card record failed its self-decode: %s" % rec.hex())
    return rec


def title_record(x: int, y: int, text: str, *, font: int = TITLE_FONT, w: int = TITLE_W,
                 h: int = TITLE_H) -> bytes:
    """An absolute white wtype-11 title with a declared box (GUICODE Finding 69), font
    TITLE_FONT, left-aligned, centred vertically in TITLE_H. The colour is the mode-3 inline BGR
    form of the coach-verified white contactor glyph (`aescompact._red_glyph_record`)."""
    # header byte 3: align 1 | fillB mode 5 (transparent, as stock captions) | fillA mode 3 --
    # an inherited, opaque fillB let each glyph cell paint over its neighbour (v50: the 'm' of
    # 'Amps' lost its right edge on the coach)
    rec = (bytes([0x0B, 0x50, 0x0F, 0x6B, 0, 0, 0, 0]) + bytes(WHITE_BGR)
           + struct.pack("<hhhh", x, y, w, h)
           + bytes([0x01, font, 0xFF, 0x01, 0x02, 0x0A, 0x00])
           + bytes([len(text)]) + text.encode("ascii") + b"\x00")
    d = records.decode_record(rec, 0)
    ink = [(m, v) for role, m, v in d.colors if role == "fillA"]
    if not (d.wtype == 11 and d.label == text and d.font == font and d.size == len(rec)
            and d.base_modes[:2] == [0, 0] and d.align == 1 and d.v_align == 2
            and [_s16(g) for g in d.geom[:4]] == [x, y, w, h]
            and ink == [(3, 0xFFFFFF)] and ("fillB", 5, None) in d.colors):
        raise CardLayoutError("authored title %r failed its self-decode: %s" % (text, rec.hex()))
    return rec


def restore_record(dx: int = 0, dy: int = 0) -> bytes:
    """An empty, zero-offset wtype-11 label whose only effect is to set fillA and fillB back to
    mode 2 -- what v47's white heater backing box (829[21]) left them at, before that box was
    recoloured to the card. Draws nothing (no text), moves no flow (base mode 1, offset 0),
    inherits the font (0xFF). It DOES paint its 0x0 background as one white pixel, so the caller
    offsets it by `(dx, dy)` under a capsule drawn after it, and compensates the next record."""
    rec = (bytes([0x0B, 0x55, 0x03, 0x12, 0, 0, 0, 0]) + struct.pack("<hh", dx, dy)
           + bytes([0x01, 0xFF, 0xFF, 0x02, 0x02, 0x0A, 0x00]) + bytes([0x00, 0x00]))
    d = records.decode_record(rec, 0)
    fills = [(role, m, v) for role, m, v in d.colors if role in ("fillA", "fillB")]
    if not (d.wtype == 11 and (d.label or "") == "" and d.size == len(rec) and d.font is None
            and d.base_modes[:2] == [1, 1] and [_s16(g) for g in d.geom[:2]] == [dx, dy]
            and fills == [("fillA", 2, None), ("fillB", 2, None)]):
        raise CardLayoutError("authored restore record failed its self-decode: %s" % rec.hex())
    return rec


def _records(data: bytes, obj: int):
    gs, tab = guidata.guidata_extent(data)[0], guidata.object_tables(data)[1]
    return records.object_records(data, gs + tab[obj])


def _object_payload(data: bytes, obj: int) -> bytes:
    """Object `obj`'s payload verbatim: `<count>` + records, padded to 4 (an arm for append_arms)."""
    gs, tab = guidata.guidata_extent(data)[0], guidata.object_tables(data)[1]
    recs = records.object_records(data, gs + tab[obj])
    raw = bytes(data[gs + tab[obj]:recs[-1].end])
    return raw + b"\x00" * (-len(raw) % 4)


def clone_pill_band(data: bytes) -> tuple[bytes, int]:
    """Append a private copy of the AES pill's state band with its DISABLED arms in the engine
    navy; returns `(image, band_base)`. Match values are copied from the stock band, the 0xFFFF
    default arm first (switchbuild.append_arms' rule)."""
    screen = layout.Screen(data)
    values = [screen._match_value(PILL_BAND + k) for k in range(PILL_BAND_COUNT)]
    if values[0] != 0xFFFF or None in values:
        raise CardLayoutError("pill band %d match values %s: not the stock band"
                              % (PILL_BAND, values))
    arms = []
    for k in range(PILL_BAND_COUNT):
        obj = PILL_BAND + k
        recs = _records(data, obj)
        if [r.wtype for r in recs] != [4, 33, 11]:
            raise CardLayoutError("pill band arm %d is not (4, 33, 11): %s"
                                  % (obj, [r.wtype for r in recs]))
        arm = bytearray(_object_payload(data, obj))
        if recs[2].label == PILL_DISABLED_TEXT:
            if recs[0].colors[0] != ("fillA", 7, PILL_STOCK_FILL):
                raise CardLayoutError("DISABLED arm %d fills %s, not index %d"
                                      % (obj, recs[0].colors[0], PILL_STOCK_FILL))
            off = recs[0].color_off[0] - (_gs(data) + guidata.object_tables(data)[1][obj])
            struct.pack_into("<H", arm, off, PILL_DISABLED_FILL)
        arms.append(bytes(arm))
    if sum(records.object_records(a, 0)[0].colors[0] == ("fillA", 7, PILL_DISABLED_FILL)
           for a in arms) != 2:
        raise CardLayoutError("expected exactly two DISABLED arms in band %d" % PILL_BAND)
    # The panel picks an arm by scanning the band for the value (GUICODE Finding 16), so only the
    # 0xFFFF default's place is fixed. The stock order 0,1,2,... is the very run
    # `guidata.find_match_table` scores on, and appending it makes a shifted window win
    # (grow_match_table refuses: "the match table moved"); the non-default arms are therefore
    # ordered so no 0,1,2 run is appended.
    order = [0] + sorted(range(1, PILL_BAND_COUNT), key=lambda k: (values[k] != 0, values[k]))
    for i in range(1, len(order) - 2):
        if [values[order[i]], values[order[i + 1]], values[order[i + 2]]] == [0, 1, 2]:
            order[i + 1], order[i + 2] = order[i + 2], order[i + 1]
    return switchbuild.append_arms(data, [arms[k] for k in order], [values[k] for k in order])


def _gs(data: bytes) -> int:
    return guidata.guidata_extent(data)[0]


def _check_shape(data: bytes, cluster_index: int) -> None:
    body = aescompact.ELECTRICAL_AES_BODY
    frame = _records(data, body)
    shapes = [V47_FRAME_WTYPES + BUTTON_GROUP * (n - 1) for n in sorted(BUTTON_BOXES)]
    if [r.wtype for r in frame] not in shapes:
        raise CardLayoutError("object %d is not the v47 compact frame (wtypes %s)"
                              % (body, [r.wtype for r in frame]))
    if frame[5].child_index != cluster_index:
        raise CardLayoutError("frame[5] points at %s, not the cluster %d"
                              % (frame[5].child_index, cluster_index))
    cluster = _records(data, cluster_index)
    if [r.wtype for r in cluster] != V47_CLUSTER_WTYPES:
        raise CardLayoutError("object %d is not the v47 AES cluster (wtypes %s)"
                              % (cluster_index, [r.wtype for r in cluster]))
    if cluster[CLUSTER_TRIGGERS].label != "Triggers:":
        raise CardLayoutError("cluster record %d is %r, not 'Triggers:'"
                              % (CLUSTER_TRIGGERS, cluster[CLUSTER_TRIGGERS].label))
    sw = cluster[CLUSTER_PILL_SWITCH]
    if (sw.child_index, sw.count) != (PILL_BAND, PILL_BAND_COUNT):
        raise CardLayoutError("cluster pill switch selects %s x %s, not the stock band %d x %d"
                              % (sw.child_index, sw.count, PILL_BAND, PILL_BAND_COUNT))
    inset_fills = [(role, m, v) for role, m, v in frame[FRAME_PUMP_INSET].colors
                   if role in ("fillA", "fillB")]
    if inset_fills != [("fillA", 7, 3), ("fillB", 7, 3)]:
        raise CardLayoutError("pump inset outline is not the v47 navy (fills %s)" % inset_fills)
    bases = _button_bases(frame)
    boxes = [tuple(_s16(g) for g in frame[b].geom[:4]) for b in bases]
    if boxes != BUTTON_BOXES[len(bases)]:
        raise CardLayoutError("the BMS button boxes are %s, not %s: build add_aes_batt_heat "
                              "with those x/y/w/h first" % (boxes, BUTTON_BOXES[len(bases)]))
    # every geometry edit's prior value, so a mismatch writes nothing
    for j, r in enumerate(cluster):
        _edit_geom(data, r, CLUSTER_EDITS.get(j, {}))
    for j, r in enumerate(frame):
        _edit_geom(data, r, _frame_edits(frame).get(j, {}))


def apply_card_layout(data: bytes, cluster_index: int) -> tuple[bytes, dict]:
    """Re-lay out object 829 and the private AES cluster `cluster_index` as four cards.
    Returns `(image, {"object": 829, "cluster": cluster_index, "pill_band": base})`; raises
    CardLayoutError on any shape mismatch (nothing is written) and relocate.RelocationError if a
    splice is refused."""
    body = aescompact.ELECTRICAL_AES_BODY
    _check_shape(data, cluster_index)

    # 0. the private pill band (appended at the end of the object table; nothing renumbers).
    out, band = clone_pill_band(data)

    # 1. the cluster, in place: geometry, the pill switch onto the private band, and
    #    'Triggers:' emptied (it stays as the row anchor).
    cluster = _records(out, cluster_index)
    cstream = [_edit_geom(out, r, CLUSTER_EDITS.get(j, {})) for j, r in enumerate(cluster)]
    sw = cluster[CLUSTER_PILL_SWITCH]
    buf = bytearray(cstream[CLUSTER_PILL_SWITCH])
    struct.pack_into("<H", buf, records.child_index_offset(out, sw) - sw.offset, band)
    cstream[CLUSTER_PILL_SWITCH] = bytes(buf)
    cstream[CLUSTER_TRIGGERS] = aescompact._relabel(
        out, cluster_index, CLUSTER_TRIGGERS, "", bytearray(cstream[CLUSTER_TRIGGERS]))
    out, _ = relocate.replace_object_records(out, cluster_index, _body(cstream))

    # 2. the frame, re-read after that relocation (the fragments are offset-free).
    frame = _records(out, body)
    edits = _frame_edits(frame)
    fstream = [_edit_geom(out, r, edits.get(j, {})) for j, r in enumerate(frame)]
    fstream[FRAME_PUMP_BOX] = card_record(*PUMP_ANCHOR, 0, 0, wtype=32, radius=0)
    inset = frame[FRAME_PUMP_INSET]
    buf = bytearray(fstream[FRAME_PUMP_INSET])
    for (role, _m, _v), off in zip(inset.colors, inset.color_off):
        if role in ("fillA", "fillB") and off is not None:
            struct.pack_into("<H", buf, off - inset.offset, CARD_FILL_INDEX)
    fstream[FRAME_PUMP_INSET] = bytes(buf)
    fstream[FRAME_PUMP_HEADING] = aescompact._relabel(
        out, body, FRAME_PUMP_HEADING, "", bytearray(fstream[FRAME_PUMP_HEADING]))
    bases = _button_bases(frame)
    for b, box in zip(bases, BUTTON_BOXES[len(bases)]):
        fstream[b] = card_record(*box, wtype=32)

    # Order: all four cards, all four titles, then v47's records with the restore record right
    # after the heater's flow anchor. Every title follows every card; every v47 record after them sets the
    # state it draws with exactly as in v47, the heater arms are entered with v47's mode-2 fills,
    # and the frame ends in v47's persistent state -- what a live repaint might inherit (GUICODE
    # Finding 78 has not located that path).
    cards = [card_record(*box) for _t, box in CARDS]
    titles = [title_record(x + TITLE_DX, y + TITLE_DY, t) for t, (x, y, _w, _h) in CARDS]
    stream = cards + titles
    anchors = {b + 1 for b in bases}
    for k, frag in enumerate(fstream):
        stream.append(frag)
        if k in anchors:
            stream.append(restore_record(*RESTORE_DXY))
    out, _ = relocate.replace_object_records(out, body, _body(stream))
    return out, {"object": body, "cluster": cluster_index, "pill_band": band}
