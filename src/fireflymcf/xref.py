"""Constant scanning, to find the code that parses the MCF.

ARM Thumb-2 cannot encode an arbitrary 32-bit immediate in one instruction, so a
constant like the MCF magic is nearly always stored in a PC-relative literal pool and
loaded with LDR. Searching for the raw little-endian bytes therefore finds the literal
even without disassembling, which is enough to point Ghidra at the right function.
"""
from __future__ import annotations

import struct

# Values worth locating, and why each one matters. These are data-side observations,
# not constants the firmware is known to use -- see the note on 0x84 below.
INTERESTING_STRIDES: tuple[int, ...] = (
    # 0x84 is the size of an MCF section descriptor, and so the apparent spacing
    # between them. It is NOT a stride the firmware uses: it locates each descriptor
    # through a u32 pointer array whose file offset comes from the u16 at MCF+0x22,
    # and the descriptors merely happen to be contiguous. Kept because the size is
    # still a real data-side signal (it is the u16 at descriptor +0x02), but finding
    # it in an image proves nothing about that image. Proven in Task 6 --
    # see docs/PARSER-LOCATION.md.
    0x84,
    # The MCF header size, and the file offset of that pointer array (u16 at +0x22).
    0xA4,
    0x2C,   # cli.bin header length / region-1 file offset
)


def find_u32(data: bytes, value: int) -> list[int]:
    """Byte offsets where `value` appears as a little-endian u32."""
    needle = struct.pack("<I", value & 0xFFFFFFFF)
    out: list[int] = []
    start = 0
    while True:
        idx = data.find(needle, start)
        if idx == -1:
            return out
        out.append(idx)
        start = idx + 1


def find_u16(data: bytes, value: int) -> list[int]:
    needle = struct.pack("<H", value & 0xFFFF)
    out: list[int] = []
    start = 0
    while True:
        idx = data.find(needle, start)
        if idx == -1:
            return out
        out.append(idx)
        start = idx + 1


def find_stride_constants(data: bytes) -> dict[int, list[int]]:
    """Locate the structural constants. Returns {value: [offsets]} for those present."""
    found: dict[int, list[int]] = {}
    for value in INTERESTING_STRIDES:
        hits = find_u16(data, value)
        if hits:
            found[value] = hits
    return found


def scan_images(images: dict[str, bytes]) -> dict[str, dict]:
    """Scan several images for the MCF magic and the structural constants.

    **The magic hit is the only load-bearing result.** Task 6 located the parser this
    way and it is in the one image that carries `container.MAGIC`: cli.bin region 1.

    The `stride_hits` are reported for context only and must not be treated as
    evidence. They are 16-bit byte searches, so they fire dozens of times per image by
    chance (0x84 alone hits 36 times in 13.6's GuiData, which parses nothing), and the
    firmware never uses 0x84 as a stride in any case -- see INTERESTING_STRIDES.
    """
    from . import container

    report: dict[str, dict] = {}
    for name, data in images.items():
        report[name] = {
            "magic_hits": find_u32(data, container.MAGIC),
            "stride_hits": find_stride_constants(data),
            "size": len(data),
        }
    return report
