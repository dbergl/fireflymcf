"""Parser for the 0x2C-byte image header Firefly puts on ARM Cortex-M firmware,
plus Cortex-M vector-table validation.

The header is a fixed two-region descriptor:

    +0x00  u32  magic 0xC11B102E
    +0x04  u32  version (0x0201 across the whole corpus)
    +0x08  u32  zero
    +0x0C  u32  region 1 load address
    +0x10  u32  region 1 file offset
    +0x14  u32  region 1 length
    +0x18  u32  region 2 load address
    +0x1C  u32  region 2 file offset
    +0x20  u32  region 2 length
    +0x24  u32  region 2's real load address, 0xC0400000 across the corpus (MCF-FORMAT §4.2)
    +0x28  u32  per-build scalar, still UNRESOLVED (0x9E3 on 13.6/14.7, 0x1E6A on 11.23)

Both region-2 load addresses read 0x20000000, the same as region 1, which cannot be
literally true since the regions do not overlap in the file. Treat region 2's load
address as UNRESOLVED rather than trusting it -- +0x24 carries the real one.

+0x28 does not yield to code-first analysis: the magic 0xC11B102E appears in NO corpus
image, so cli.bin's own header is read only by the panel's primary bootloader (in the
MCU's boot flash, outside the MCF), which we do not have. Empirically ruled out (2026-09-10,
MCF-FORMAT §4.2): it is not a checksum of region 1, region 2, the header, or the whole file
under sum8/16/32, xor16, Fletcher-16, CRC16 (init 0/0xFFFF) or CRC32; not a clean offset into
region 2 (0x9E3 lands mid-string in the tag-name pool, 0x1E6A lands in a binary table -- two
different structures); not region 1's entry/reset vector; and not referenced by any region-1
literal. Recorded as a defensible negative, not fitted.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

MAGIC = 0xC11B102E
HEADER_LEN = 0x2C


@dataclass(frozen=True)
class Region:
    load_addr: int
    file_offset: int
    length: int


@dataclass(frozen=True)
class ArmImageHeader:
    magic: int
    version: int
    regions: list[Region]
    word_24: int
    word_28: int


def parse_header(data: bytes) -> ArmImageHeader:
    if len(data) < HEADER_LEN:
        raise ValueError(
            f"need at least {HEADER_LEN} bytes for a header, got {len(data)}")
    fields = struct.unpack_from("<11I", data, 0)
    magic, version, _zero = fields[0], fields[1], fields[2]
    if magic != MAGIC:
        raise ValueError(f"bad magic {magic:#010x}, expected {MAGIC:#010x}")
    regions = [
        Region(load_addr=fields[3], file_offset=fields[4], length=fields[5]),
        Region(load_addr=fields[6], file_offset=fields[7], length=fields[8]),
    ]
    return ArmImageHeader(
        magic=magic, version=version, regions=regions,
        word_24=fields[9], word_28=fields[10],
    )


@dataclass(frozen=True)
class VectorTable:
    initial_sp: int
    reset: int
    handlers: list[int]


def read_vector_table(payload: bytes, load_addr: int, count: int = 16) -> VectorTable:
    """Read a Cortex-M vector table from the start of `payload`.

    Word 0 is the initial stack pointer, word 1 the reset vector, and the rest are
    exception handlers. Thumb addresses carry bit 0 set; it is cleared here so callers
    get the real instruction address, matching what a disassembler expects.
    """
    if len(payload) < count * 4:
        raise ValueError(f"payload too short for {count} vectors")
    words = struct.unpack_from(f"<{count}I", payload, 0)
    return VectorTable(
        initial_sp=words[0],
        reset=words[1] & ~1,
        handlers=list(words[2:]),
    )


def looks_like_vector_table(payload: bytes, load_addr: int, image_len: int) -> bool:
    """Heuristic: does `payload` start with a plausible Cortex-M vector table for an
    image loaded at `load_addr`?

    Used to recover load addresses for the images embedded in an MCF, which carry no
    header of their own. Deliberately strict — a false positive sends the disassembly
    to the wrong base address, which is far more costly than a missed detection.
    """
    if len(payload) < 64:
        return False
    try:
        vt = read_vector_table(payload, load_addr)
    except ValueError:
        return False

    # The initial SP must be word-aligned and non-trivial.
    if vt.initial_sp == 0 or vt.initial_sp & 3:
        return False

    lo, hi = load_addr, load_addr + image_len

    # The reset vector must be Thumb-tagged and inside the image.
    raw_reset = struct.unpack_from("<I", payload, 4)[0]
    if not raw_reset & 1 or not (lo <= vt.reset < hi):
        return False

    # Several handlers must also be Thumb-tagged and in range. Null entries are normal
    # for unused exceptions and are skipped rather than counted against the image.
    good = 0
    for h in vt.handlers:
        if h == 0:
            continue
        if h & 1 and lo <= (h & ~1) < hi:
            good += 1
        else:
            return False
    return good >= 4
