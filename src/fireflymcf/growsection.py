"""Append bytes to the end of a non-GuiData section's content and reseal.

Growing GuiCode is structurally simpler than a GuiData insertion: GuiCode's load
address is fixed and its existing content does not move, so there are NO object
tables, NO GuiData directory offsets, and NO GuiCode->GuiData literal pointers to
repair (relocate._apply_downstream_shift's job). The only downstream references are
container descriptor fields: the grown section's own length, and every later
section's file offset. A section's last 4 bytes are its stored checksum, so the
payload is inserted BEFORE the checksum word (at content_end - 4 within the section);
reseal then recomputes the checksum over the grown content and writes it at the new
tail. Confidence: container geometry proven-from-code (container.py); this primitive
is the same append-then-reseal shape as the GuiData relocator, minus the GuiData
repairs.
"""
from __future__ import annotations

import struct

from fireflymcf import container, patch


class GrowSectionError(ValueError):
    pass


def grow_section(data: bytes, section_marker: str, payload: bytes):
    if len(payload) % 4 != 0:
        raise GrowSectionError(f"payload must be 4-aligned, got {len(payload)} bytes")

    c = container.parse_container(data)
    try:
        target = next(s for s in c.sections if section_marker in s.name)
    except StopIteration:
        raise GrowSectionError(f"no section matching {section_marker!r}")
    if "GuiData" in target.name:
        raise GrowSectionError("use relocate.relocate to grow GuiData, not grow_section")

    delta = len(payload)
    # content = section minus its trailing 4-byte checksum; insert at content end.
    ins = target.offset + target.length - 4
    load_off = target.length - 4
    out = bytearray(data[:ins] + payload + data[ins:])

    desc_offsets = container._descriptor_offsets(data, c.section_count, c.header_word_20)
    for section, desc_at in zip(c.sections, desc_offsets):
        if section.index == target.index:
            pos = desc_at + container.DESC_EXTENT_OFFSET + 4          # +0x4C length
            old = section.length
            new = old + delta
        elif section.offset > target.offset:
            pos = desc_at + container.DESC_EXTENT_OFFSET               # +0x48 offset
            old = section.offset
            new = old + delta
        else:
            continue
        # positions in the descriptor array are BEFORE the insertion point, so unshifted.
        cur = struct.unpack_from("<I", out, pos)[0]
        if cur != old:
            raise GrowSectionError(
                f"descriptor at {pos:#x}: expected {old:#x}, found {cur:#x}")
        struct.pack_into("<I", out, pos, new)

    sealed, changed = patch.reseal(bytes(out))
    return sealed, load_off, changed
