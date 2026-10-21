"""Append NEW receive-filter entries to the variable table, instead of retargeting existing ones.

Until now the tooling only re-pointed an existing 8-byte entry (`statusbind.retarget_filter`),
because append-new was "hardware-unverified": the boot loop's count, the receive filter and the
slot arrays all have to agree. This module grows the table, and each piece below was checked
against the 13.6 bytes on 2026-09-21 (`proven-from-code`):

* **The count.** The boot loader `FUN_0021BE50` walks the GuiData header directory and, for the
  tag-0x41 descriptor (GuiData+0x20, `41 01 00 2a 70 2a 50 09`), reads the entry count as
  `((desc[1] & 3) << 8) | desc[3]` (0x0021BF8A-0x0021BF9E: `ldrb [sl,#9]`, `and #0x300`,
  `ldrb [sl,#0xb]`) -- 298 -- and the table offset from the u16 at desc[4]. The u16 at desc[6]
  (0x950 = 298*8) is the size the tooling (`guidata._table_bounds`) reads; both are kept in step.
* **The GuiCode cross-check.** Right after (0x0021BF66-0x0021BFEA), the u16 at GuiCode header
  +0x20 must EQUAL that count and be <= 0x3E8 (1000), else the GUI does not start. Stock holds
  298. So the header is bumped with the table. (A mismatch fails to boot the screen -- recoverable
  by reflashing, not a brick.)
* **The slots and timers.** `FUN_0021C760` writes the slot at `state+0x10C+2*idx` (idx <= 0xFF)
  or `state+0x144+2*idx` (idx > 0xFF), and the per-var deadline at `state+0x914+4*idx`
  (0x0021C9B0-0x0021C9DA), state = 0x20007EBC. The idx arithmetic is u16 throughout, and no LCD
  Firmware or GuiCode instruction addresses the new entries' slot/timer as a fixed field (checked
  for idx 298: `0x20008254`, timer `state+0xDBC`; the only `#0x398` displacements are `[sp,...]`).
* **The placement.** The table ends where the 12-byte tag-0x42 table begins, and after that is a
  ~52 KB zero padding arena up to the image sub-container pinned at GuiData+0x10000. The growth
  uses the SAME in-place window rewrite as `optionrow._grow_bindings` -- which the coach already
  runs (v27's binding growth shifted this very table 0x2A70 -> 0x2B00): insert the new entries at
  the table end, shift the in-window tables down, trim the same number of padding bytes, fix the
  descriptors. Nothing at or after 0x10000 moves.
* **The registry.** `FUN_0021A618` silently drops DGNs past 83 distinct (`statusbind.REGISTRY_CAP`);
  an entry on an already-registered DGN costs nothing, a new DGN costs one.
* **The instance gate (entry `+4`/`+5`).** `FUN_0021C760` (0x0021C93A-0x0021C98E) skips the compare
  entirely when `+4` is `0xFF` (`statusbind.FILTER_DISABLED`; every instance matches). A real value
  at `+4` activates a masked compare of payload byte `+5`: its low nibble picks the payload index
  (`and r0,r0,#0xf`; an 8-byte payload caps it at 7) and its high nibble expands to a 2-bit-pair
  field mask (nibble 0 => the whole byte). `NewFilter.__post_init__` refuses an `instance` with no
  `compare_byte` (the pair is what the firmware tests together) and a `compare_byte` whose low
  nibble reads past the payload -- the same gate `statusbind.retarget_filter`/`pin_filter_instance`
  apply when repointing an existing entry.

What this does NOT make true: a widget naming the new var index as its `value_slot` does NOT read
the var -- `value_slot` indexes the display-cell table. Show a new var through a routine that
copies its slot into a cell (`add_register_readout`, which takes this module's handle as a ref).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from . import container, coverage, guidata, optionrow, patch, register, relocate, statusbind
from .relocate import Edit

# The GuiCode header u16 FUN_0021BE50 requires to equal the variable-table count.
GUICODE_VAR_COUNT_FIELD = 0x20
GUICODE_VAR_COUNT_MAX = 0x3E8                     # `cmp.w r6,#0x3e8; bhi fail` @0x0021BFE6

# The count's high bits live in the descriptor's tag word (desc[1] bits 0-1), which the tooling
# also checks as the tag (`guidata.DESCRIPTOR_TAGS` = 0x0141). Keep the high bits fixed rather
# than widen that check: 256..511 entries all read as 0x0141.
_COUNT_HIGH_FIXED = 1
_MAX_COUNT_THIS_TAG = 0x1FF


class AppendFilterError(relocate.RelocationError):
    """The append cannot be made safely on this image; nothing is produced."""


@dataclass(frozen=True)
class NewFilter:
    """One entry to append. Encoded exactly as `statusbind.retarget_filter` encodes a retarget."""
    dgn: int                     # 18-bit DGN
    mode: int                    # statusbind.LIVE_MODES
    payload_byte: int
    refresh: int = 3             # staleness class 0-3 = 2 s/10 s/30 s/never; 3 is the DC_DIMMER vars' class
    instance: int = statusbind.FILTER_DISABLED
    compare_byte: int = statusbind.FILTER_DISABLED

    def __post_init__(self):
        if self.instance != 0xFF and self.compare_byte == 0xFF:
            raise AppendFilterError(
                "instance %#x needs a compare_byte (+5): high nibble = 2-bit field mask select "
                "(0 = whole byte), low nibble = payload index" % self.instance)
        if self.compare_byte != 0xFF and (self.compare_byte & 0x0F) > 7:
            raise AppendFilterError(
                "compare_byte %#x indexes payload byte %d; FUN_0021C760 reads past the 8-byte "
                "payload above 7" % (self.compare_byte, self.compare_byte & 0x0F))

    def raw(self) -> bytes:
        if not 0 <= self.dgn <= statusbind.DGN_MASK:
            raise AppendFilterError(f"DGN {self.dgn:#x} is not an 18-bit value")
        if self.mode not in statusbind.LIVE_MODES:
            raise AppendFilterError(
                f"extraction mode {self.mode} is not one FUN_0021C760 dispatches "
                f"({sorted(statusbind.LIVE_MODES)})")
        if not 0 <= self.payload_byte <= (6 if self.mode == 2 else 7):
            raise AppendFilterError(f"payload byte {self.payload_byte} does not fit mode {self.mode}")
        if not 0 <= self.refresh <= 3:
            raise AppendFilterError(f"refresh class {self.refresh} outside 0..3")
        return bytes([
            self.dgn & 0xFF, (self.dgn >> 8) & 0xFF,
            ((self.dgn >> 16) & 0x03) | (self.refresh << 2) | (self.mode << 4),
            self.payload_byte, self.instance, self.compare_byte, 0, 0])


def _guicode_section(data: bytes):
    return next(s for s in container.parse_container(data).sections if "GuiCode" in s.name)


def append_filters(data: bytes, filters: list[NewFilter]) -> tuple[bytes, list[Edit], list[int]]:
    """Append `filters` to the variable table. Returns `(image, edits, new var indices)`.

    Refuses (AppendFilterError) unless: the GuiCode count mirrors the table; the new count stays
    within the tag's high bits and the firmware's 1000; the registry stays under its cap; the
    padding arena can absorb the growth; no declared table straddles the window; and nothing
    points into the window. The finished image must pass every relocate gate.
    """
    if not filters:
        raise AppendFilterError("nothing to append")
    payload = b"".join(f.raw() for f in filters)
    delta = len(payload)
    gs, ge = guidata.guidata_extent(data)
    vt = guidata.variable_table(data)
    old_count, new_count = len(vt), len(vt) + len(filters)

    desc = gs + guidata.VARIABLE_TABLE_DESCRIPTOR
    tag, _, voff, vsize = struct.unpack_from("<HHHH", data, desc)
    decoded = ((data[desc + 1] & 3) << 8) | data[desc + 3]
    if decoded != old_count or vsize != old_count * guidata.VARIABLE_ENTRY_SIZE:
        raise AppendFilterError(
            f"variable descriptor count {decoded} / size {vsize:#x} disagree with the "
            f"{old_count}-entry table")
    if new_count > _MAX_COUNT_THIS_TAG or (new_count >> 8) != _COUNT_HIGH_FIXED:
        raise AppendFilterError(
            f"{new_count} entries changes the count's high bits (desc[1]); this module keeps "
            f"them at {_COUNT_HIGH_FIXED} (256..511 entries)")
    if new_count > GUICODE_VAR_COUNT_MAX:
        raise AppendFilterError(f"{new_count} entries exceeds the firmware's {GUICODE_VAR_COUNT_MAX}")

    gc = _guicode_section(data)
    gc_count_pos = gc.offset + GUICODE_VAR_COUNT_FIELD
    gc_count = struct.unpack_from("<H", data, gc_count_pos)[0]
    if gc_count != old_count:
        raise AppendFilterError(
            f"GuiCode header +{GUICODE_VAR_COUNT_FIELD:#x} holds {gc_count}, not the table's "
            f"{old_count}; the boot cross-check shape is not the one this module was proven on")

    before = statusbind.registered_dgns(data)
    after = before | {f.dgn & statusbind.DGN_MASK for f in filters}
    if len(after) > statusbind.REGISTRY_CAP:
        raise AppendFilterError(
            f"{len(after)} distinct DGNs would be registered from the table, over the cap of "
            f"{statusbind.REGISTRY_CAP}; FUN_0021A618 drops the excess silently")

    # --- padding-arena preconditions (optionrow._grow_bindings, for the variable table) ---
    at = voff + vsize
    img = coverage.IMAGE_CONTAINER_OFFSET
    if not at + delta < img or img > ge - gs:
        raise AppendFilterError(f"no room between GuiData+{at:#x} and the image container {img:#x}")
    if set(data[gs + img - delta:gs + img]) != {0}:
        raise AppendFilterError("the bytes before the image container are not zero padding")
    first_tag, _, first_off, _ = struct.unpack_from("<HHHH", data, gs + coverage.GUIDATA_HEAD_SIZE)
    desc_end = gs + first_off
    p = gs + coverage.GUIDATA_HEAD_SIZE
    shift_desc: list[tuple[int, int]] = []
    while p < desc_end:
        t, _, off, size = struct.unpack_from("<HHHH", data, p)
        end = off + size
        if p == desc:
            pass
        elif size and off < at < end:
            raise AppendFilterError(f"declared table {t:#06x} [{off:#x},{end:#x}) straddles {at:#x}")
        elif at <= off < img:
            shift_desc.append((p + 4, off))
        if size and end > img - delta and off < img:
            raise AppendFilterError(f"declared table {t:#06x} reaches into the trim region")
        p += coverage.GUIDATA_DESCRIPTOR_SIZE
    stranded = optionrow._window_references(data, gs, at, img)
    if stranded:
        raise AppendFilterError("pointers target the rewritten window:\n  " + "\n  ".join(stranded[:8]))

    # --- the same-size window rewrite ----------------------------------------------------
    out = bytearray(data)
    edits: list[Edit] = []
    win_lo, win_hi = gs + at, gs + img
    old_window = bytes(out[win_lo:win_hi])
    new_window = payload + old_window[:img - at - delta]
    out[win_lo:win_hi] = new_window
    edits.append(Edit(win_lo, old_window[:delta], payload, "variable-append",
                      f"{len(filters)} receive filter(s) appended at indices "
                      f"{old_count}..{new_count - 1}"))
    edits.append(Edit(win_lo + delta, old_window[delta:], new_window[delta:], "window-shift",
                      f"aux tables +{delta}, padding -{delta} (image container pinned at {img:#x})"))

    # descriptor: size, and the count (desc[3] low byte; desc[1] high bits unchanged)
    struct.pack_into("<H", out, desc + 6, vsize + delta)
    edits.append(Edit(desc + 6, struct.pack("<H", vsize), struct.pack("<H", vsize + delta),
                      "variable-table-size", f"{vsize:#x} -> {vsize + delta:#x}"))
    out[desc + 3] = new_count & 0xFF
    edits.append(Edit(desc + 3, bytes([old_count & 0xFF]), bytes([new_count & 0xFF]),
                      "variable-table-count", f"count {old_count} -> {new_count}"))
    for off_pos, old_off in shift_desc:
        struct.pack_into("<H", out, off_pos, old_off + delta)
        edits.append(Edit(off_pos, struct.pack("<H", old_off), struct.pack("<H", old_off + delta),
                          "table-offset", f"declared table offset {old_off:#x} -> {old_off + delta:#x}"))
    # GuiCode header count, the boot cross-check
    struct.pack_into("<H", out, gc_count_pos, new_count)
    edits.append(Edit(gc_count_pos, struct.pack("<H", old_count), struct.pack("<H", new_count),
                      "guicode-var-count", f"GuiCode header +0x20 {old_count} -> {new_count}"))

    resealed, _ = patch.reseal(bytes(out))

    # --- self-check: every relocate gate, and the table reads back as intended -------------
    relocate._verified_pointer_tables(resealed)
    for name, (ok, reason) in (
            ("deep coverage", relocate._deep_coverage_ok(resealed)),
            ("pointer tables", relocate._verify_table_shifts(data, resealed, img, 0)),
            ("boundary oracle", relocate._boundary_oracle(resealed)),
            ("GuiCode pointers", relocate._verify_guicode_pointers(data, resealed, img, 0)),
            ("LCD pointers", relocate._verify_no_stranded_lcd_pointers(resealed, at))):
        if not ok:
            raise AppendFilterError(f"appended image fails {name}: {reason}")
    if patch.reseal(resealed)[1]:
        raise AppendFilterError("appended image is not fully resealed")
    vt2 = guidata.variable_table(resealed)
    if [v.raw for v in vt2[:old_count]] != [v.raw for v in vt] or \
            b"".join(v.raw for v in vt2[old_count:]) != payload:
        raise AppendFilterError("the grown table does not read back as the old table + the new entries")

    edits.sort(key=lambda e: e.file_offset)
    return resealed, edits, list(range(old_count, new_count))


def handle_for(data: bytes, var_index: int) -> dict:
    """A `resolve_register`-shaped handle for an appended var, so `add_register_readout` /
    `follow_register` can take it by `ref`."""
    v = guidata.variable_table(data)[var_index]
    return {"var_index": v.index, "slot_addr": register.status_slot_addr(v.index),
            "dgn_low": v.dgn_low, "value": v.value, "extract_mode": v.raw[2] >> 4,
            "source_field": v.raw[3] & 7}
