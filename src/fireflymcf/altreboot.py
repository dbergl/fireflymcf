"""A "REBOOT APS-500" button on the ALTERNATOR SYSTEM page (screen 21, object 24): a 3-second
hold transmits the RV-C `GENERAL_RESET` (DGN `0x17F00 | dest`) reboot command to the Renogy
APS-500 alternator charger at source address `0x80`; a tap sends nothing.

The button is a clone of the page's own CLEAR EEPROM button (object 24 records 19-21: a filled
wtype-33 box, its wtype-11 caption, a wtype-12 touch), placed 13 px to its left -- the gap the
nav tabs use -- relabelled, and its touch stamped with a freshly-minted command group. The bottom
bar is object 24's own record stream, so the button draws on this page only.

**The frame.** `GENERAL_RESET` byte 0 is four 2-bit fields: reboot (bits 0-1), clear faults
(2-3), reset to defaults (4-5), reset statistics (6-7); `01` requests the action
(`rvc2mqtt/rvc-spec.yml` `17F`). The DGN is PDU1 (PF `0x7F` < `0xF0`), so its low byte is the
destination address. The owner's `rvc2mqtt` sends this DGN to the APS-500 as
`17F80#05 00 FF FF FF FF FF FF` (reboot + clear faults, `entity/aps500.py` `reset_aps`); this
button sends the reboot bit alone, `01`, with the same byte 1 and padding. The panel stamps
priority 6 and SA `0x9F` (`newbutton.binding_wire_frame`), so the frame on the wire is
`197F809F#0100FFFFFFFFFFFF`.

The APS-500's other reboot path, `$RBT:` on TERMINAL (`0x17E80`), is not usable from a binding:
it is 7 bytes sent unpadded (`format_terminal_message` notes 0xFF padding misbehaves), and a
binding always transmits 8.

**The gesture.** The group entry is Batt Heat's hold donor (group 17, `10 00 0c 00`, flashed
with `add_aes_batt_heat`) with two changes, each observed on hardware in the hold-to-toggle
trial (`CONTROL-BEHAVIOR.md` §8, 2026-08-19): byte 0's high nibble 1 -> 3 (hold threshold in
seconds, `0x21BD80`), and byte 2's held-phase repeat index (bits 2-3) -> 0. The one binding
carries phase mask `0x04` (held) without its repeat bit, so it fires once when the threshold
passes, and nothing on a tap or a release.
"""
from __future__ import annotations

import struct

from . import guidata, layout, newbutton, records

PAGE_OBJECT = 24                              # screen 21, ALTERNATOR SYSTEM, on 13.6
PAGE_ROOT = 24                                # its --root index
PAGE_TITLE = "   ALTERNATOR SYSTEM"           # record 2's label, asserted before any edit

# CLEAR EEPROM's three records in object 24 on stock 13.6 (box, caption, touch), byte-verbatim.
DONOR_RECORDS = (19, 20, 21)
DONOR_BYTES = (
    bytes.fromhex("61500d070000100028001130026e0028000500"),
    bytes.fromhex("0bf5040200000000ffff010eff020211000c434c45415220454550524f4d00"),
    bytes.fromhex("0cf50e0000000000fbffffff0500e307"),
)

# Box x is relative to the bar's inner origin (CLEAR EEPROM: 560 -> drawn at 630). 437 draws the
# button at 507-617, 13 px left of CLEAR EEPROM (630), the gap between the nav tabs.
BUTTON_X = 437
BUTTON_DRAWN = (507, 435, 617, 475)           # the placed box, asserted by verify
BUTTON_TEXT = "REBOOT APS-500"                # wraps at the space, as "CLEAR EEPROM" does

APS500_ADDRESS = 0x80
GENERAL_RESET_DGN = 0x17F00 | APS500_ADDRESS
REBOOT_WIRE = bytes.fromhex("0100ffffffffffff")
REBOOT_CLEAR_FAULTS_WIRE = bytes.fromhex("0500ffffffffffff")   # rvc2mqtt's reset_aps frame
HELD_MASK = 0x04                              # gesture phase: held, past the threshold
HOLD_SECONDS = 3
HOLD_DONOR_GROUP = 17                         # Batt Heat's hold donor, 10 00 0c 00 on 13.6


class AltRebootError(ValueError):
    """The build does not carry the ALTERNATOR SYSTEM page this op was verified against."""


def _page_records(data: bytes) -> list[records.WRecord]:
    gs, _ = guidata.guidata_extent(data)
    return records.object_records(data, gs + guidata.object_tables(data)[1][PAGE_OBJECT])


def check_page(data: bytes) -> list[records.WRecord]:
    """Object 24's records, after asserting it is 13.6's ALTERNATOR SYSTEM page with the CLEAR
    EEPROM donor records byte-for-byte as verified."""
    recs = _page_records(data)
    if len(recs) < 3 or recs[2].label != PAGE_TITLE:
        raise AltRebootError(f"object {PAGE_OBJECT} is not the ALTERNATOR SYSTEM page on this build")
    for j, want in zip(DONOR_RECORDS, DONOR_BYTES):
        if j >= len(recs) or bytes(data[recs[j].offset:recs[j].end]) != want:
            raise AltRebootError(
                f"object {PAGE_OBJECT} record {j} is not the CLEAR EEPROM record this op clones")
    return recs


def group_entry(donor_raw: bytes, start: int) -> bytes:
    """The new group's 8 bytes: the hold donor with a 3-second threshold, no held-phase repeat,
    and `start`/count 1 spliced in."""
    if donor_raw[0] >> 4 != 1 or donor_raw[1] != 0 or donor_raw[3] != 0:
        raise AltRebootError(f"hold donor group {HOLD_DONOR_GROUP} is not 1x 00 xx 00: "
                             f"{donor_raw.hex()}")
    raw = bytearray(donor_raw)
    raw[0] = (HOLD_SECONDS << 4) | (raw[0] & 0x0F)
    raw[2] &= ~0x0C & 0xFF
    struct.pack_into("<H", raw, guidata.GROUP_START_FIELD, start)
    raw[guidata.GROUP_COUNT_FIELD] = 1
    return bytes(raw)


def binding(clear_faults: bool = False) -> bytes:
    return newbutton.binding_raw(GENERAL_RESET_DGN,
                                 REBOOT_CLEAR_FAULTS_WIRE if clear_faults else REBOOT_WIRE,
                                 gesture_mask=HELD_MASK)


def button_records(touch_id: int) -> list[bytes]:
    """The three cloned records: box moved to `BUTTON_X`, caption relabelled, touch stamped."""
    box = bytearray(DONOR_BYTES[0])
    rec = records.decode_record(bytes(box), 0)
    struct.pack_into("<H", box, records.geom_offset(bytes(box), rec, 0), BUTTON_X)

    cap_src = DONOR_BYTES[1]
    cap_rec = records.decode_record(cap_src, 0)
    head = cap_src[:cap_rec.label_offset - 1]
    cap = head + bytes([len(BUTTON_TEXT)]) + BUTTON_TEXT.encode("ascii") + b"\x00"
    dec = records.decode_record(cap, 0)
    if not (dec.wtype == 11 and dec.label == BUTTON_TEXT and dec.size == len(cap)):
        raise AltRebootError("relabelled caption failed its self-decode")

    touch = bytearray(DONOR_BYTES[2])
    struct.pack_into("<H", touch, len(touch) - 2, touch_id)
    return [bytes(box), cap, bytes(touch)]


def placed_button(data: bytes, touch_id: int) -> list:
    """The walk's placements for the button's box, caption and touch on the page, in order.

    The touch is found by its id; the box and caption are the records right before it."""
    placed = [p for p in layout.Screen(data).walk(PAGE_ROOT) if p.obj_index == PAGE_OBJECT]
    for i, p in enumerate(placed):
        if p.wtype != 12:
            continue
        end = records.decode_record(data, p.record_offset).end
        if data[end - 2] | (data[end - 1] << 8) == touch_id:
            return placed[i - 2:i + 1]
    return []
