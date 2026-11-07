"""Test helpers shared by the firmware-identity tests."""
from __future__ import annotations

from fireflymcf import container


def _rename_guicode(image: bytes, name: bytes) -> bytes:
    """Overwrite the GuiCode section's name field with `name` (zero padded); does not reseal."""
    c = container.parse_container(image)
    section = next(s for s in c.sections if s.name.endswith("GuiCode"))
    desc_off = int.from_bytes(image[c.header_word_20 + 4 * section.index:
                                    c.header_word_20 + 4 * section.index + 4], "little")
    at = desc_off + container.DESC_NAME_OFFSET
    out = bytearray(image)
    out[at:at + container.DESC_NAME_LEN] = name.ljust(container.DESC_NAME_LEN, b"\x00")
    return bytes(out)
