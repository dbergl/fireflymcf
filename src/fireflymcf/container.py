"""Parser for the MCF container: a bundle carrying LCD firmware, the Lyra GuiCode and
GuiData, and optionally a G12 firmware image.

Layout, now recovered from the code that parses it -- cli.bin region 1, function
FUN_20015dd8. Every offset below is proven from an instruction; see
docs/PARSER-LOCATION.md for the address of each proof.

    +0x00  u32  magic 0xFD53B912
    +0x04  u16  a file offset, used only to locate the header checksum at
                hdr[0x04] - 4; equals 0xB8 across the corpus, which is also where
                the first descriptor sits, but the firmware never reads it as such
    +0x20  u16  section descriptor count (rejected above 0x100)
    +0x22  u16  header size in bytes, and the file offset of the pointer array
                below (0xA4 across the corpus)
    +0x24  str  NUL-terminated config name
    hdr[0x22]  u32[count]  file offsets of the section descriptors
    hdr[0x04] - 4  u16  checksum over the header and the pointer array

Each section descriptor:

    +0x00  u16  magic 0xDECD
    +0x02  u16  descriptor size (0x84, or 0x74 for the G12 descriptor)
    +0x04  str  section name, a 0x40-byte NUL-padded field
    +0x44  u8   descriptor type (0 for LCD/GuiCode/GuiData, 1 for G12)
    +0x46  u16  count of the 8-byte tagged entries that start at +0x58
    +0x48  u32  payload file offset
    +0x4C  u32  payload length in bytes
    +0x58  ...  8-byte tagged entries; the one tagged 0x40 carries the flash
                destination address as a u32 at entry+4
    +size-4  u16  descriptor checksum

The descriptors are located through the pointer array, not by a fixed stride: on this
corpus that array reads [0xB8, 0x13C, 0x1C0, 0x244], which is why they appear to be
0x84 apart. SECTION_TABLE_OFFSET and SECTION_ENTRY_STRIDE below record that
observation but the firmware never uses either value.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path

MAGIC = 0xFD53B912
CONFIG_NAME_OFFSET = 0x24
COUNT_OFFSET = 0x20
HEADER_SIZE_OFFSET = 0x22   # u16; also the file offset of the descriptor pointer array

# Observed spacing of the descriptors on this corpus. Kept for the minimum-length
# guard and for documentation; the firmware derives both from the pointer array.
SECTION_TABLE_OFFSET = 0xBC
SECTION_ENTRY_STRIDE = 0x84

# Proven descriptor field positions, all descriptor-relative.
DESCRIPTOR_MAGIC = 0xDECD
DESC_NAME_OFFSET = 0x04
DESC_NAME_LEN = 0x40
DESC_EXTENT_OFFSET = 0x48


@dataclass(frozen=True)
class Section:
    index: int
    name: str
    offset: int
    length: int
    raw_entry: bytes


@dataclass(frozen=True)
class McfContainer:
    config_name: str
    section_count: int
    sections: list[Section]
    header_word_04: int
    # u16 at +0x22: the header size, and the file offset of the descriptor pointer
    # array. Named for its offset for backwards compatibility with earlier tasks.
    header_word_20: int


def _cstr(data: bytes, offset: int, limit: int) -> str:
    end = data.find(b"\x00", offset, offset + limit)
    if end == -1:
        end = offset + limit
    return data[offset:end].decode("ascii", errors="replace")


def _candidate_positions(entry: bytes, file_len: int) -> set[int]:
    """Entry-relative byte positions holding a u32 pair that plausibly is (offset,
    length): offset lands inside the file, length is non-zero, and offset+length
    does not run past EOF. Several positions pass this weak test in every entry (a
    load address repeated across entries, small unrelated counters, ...), so this is
    only the raw candidate set -- see _find_extent_position for how one is chosen.
    """
    positions = set()
    for pos in range(0, len(entry) - 8 + 1, 4):
        off, length = struct.unpack_from("<II", entry, pos)
        if 0 < off < file_len and 0 < length <= file_len - off:
            positions.add(pos)
    return positions


def _extents_tile_the_file(entries: list[bytes], pos: int, file_len: int) -> bool:
    """True if reading (offset, length) at entry-relative byte `pos` in every entry
    produces spans that chain end-to-end (sorted by offset, each span's end equals
    the next span's start) with the last one ending exactly at `file_len`.

    This is the actual structural guarantee, not just "looked like an offset once":
    a position that only coincidentally resembles a valid pair in each entry
    independently (e.g. a repeated load address, or a small unrelated counter) will
    not also tile the whole file this precisely.
    """
    spans = []
    for entry in entries:
        off, length = struct.unpack_from("<II", entry, pos)
        if not (0 < off < file_len and 0 < length <= file_len - off):
            return False
        spans.append((off, off + length))
    spans.sort()
    for (_, prev_end), (next_start, _) in zip(spans, spans[1:]):
        if prev_end != next_start:
            return False
    return spans[-1][1] == file_len


def _find_extent_position(entries: list[bytes], file_len: int) -> int:
    """Pick the entry-relative byte position that holds the (offset, length) pair.

    Two checks, both required:
      1. The position must be a plausible candidate in *every* entry of the table
         (see _candidate_positions) -- not just the first one scanned.
      2. The resulting (offset, length) pairs must actually tile the file: sorted
         by offset, each section's end must equal the next section's start, and
         the last section must end exactly at EOF (see _extents_tile_the_file).

    Check 1 alone is not enough: in this corpus, entry offset 0x64 also survives it
    (for the last entry in every file it happens to read as offset 0xC, length
    1-3 -- values that land inside the header, not a section). Check 2 is what
    rules that out and leaves only entry offset 0x44, independently, in all three
    corpus files.

    If zero positions pass both checks, or more than one does, this raises rather
    than guessing: a plausible-but-wrong extent silently corrupts every downstream
    result, while an exception is immediately visible.

    This is no longer how parse_container reads the extents -- DESC_EXTENT_OFFSET is
    proven from the firmware. The scan is retained as an independent cross-check that
    parse_container runs on every file, so that a file whose real layout disagrees
    with the proven positions raises instead of being silently mis-parsed.
    """
    common: set[int] | None = None
    for entry in entries:
        positions = _candidate_positions(entry, file_len)
        common = positions if common is None else common & positions
    common = common or set()

    verified = sorted(pos for pos in common if _extents_tile_the_file(entries, pos, file_len))
    if len(verified) != 1:
        raise ValueError(
            "ambiguous or inconsistent section extent position: "
            f"candidates common to every entry = {sorted(common)}, "
            f"verified against chain+EOF consistency = {verified}")
    return verified[0]


def _descriptor_offsets(data: bytes, count: int, array_offset: int) -> list[int]:
    """File offsets of the section descriptors, read from the u32 array at
    `array_offset` -- which the firmware takes from the u16 at +0x22, not from a
    constant (0x20016196: `ldrh r1,[r4,#0x90a]` feeding the seek at 0x2001619c,
    followed by a read of count*4 bytes at 0x200161b0).

    Each u32 there is then used directly as a seek target, one per section
    (0x20016398: `ldr r1,[r10, r5, lsl #2]` feeding the seek at 0x2001639c).
    """
    end = array_offset + count * 4
    if end > len(data):
        raise ValueError(
            f"section count {count} needs a pointer array ending at {end:#x}, "
            f"past EOF ({len(data):#x})")
    return list(struct.unpack_from(f"<{count}I", data, array_offset))


def parse_container(data: bytes) -> McfContainer:
    if len(data) < SECTION_TABLE_OFFSET + SECTION_ENTRY_STRIDE:
        raise ValueError(f"file too short to be an MCF: {len(data)} bytes")
    magic = struct.unpack_from("<I", data, 0)[0]
    if magic != MAGIC:
        raise ValueError(f"bad magic {magic:#010x}, expected {MAGIC:#010x}")

    word_04 = struct.unpack_from("<I", data, 4)[0]
    count = struct.unpack_from("<H", data, COUNT_OFFSET)[0]
    header_size = struct.unpack_from("<H", data, HEADER_SIZE_OFFSET)[0]
    config_name = _cstr(data, CONFIG_NAME_OFFSET, 0x60)

    entries: list[bytes] = []
    for desc_off in _descriptor_offsets(data, count, header_size):
        if desc_off + 4 > len(data):
            raise ValueError(f"descriptor pointer {desc_off:#x} lies past EOF")
        desc_magic, desc_size = struct.unpack_from("<HH", data, desc_off)
        if desc_magic != DESCRIPTOR_MAGIC:
            raise ValueError(
                f"descriptor at {desc_off:#x} has magic {desc_magic:#06x}, "
                f"expected {DESCRIPTOR_MAGIC:#06x}")
        entry = data[desc_off:desc_off + desc_size]
        if len(entry) < DESC_EXTENT_OFFSET + 8:
            raise ValueError(
                f"descriptor at {desc_off:#x} is {len(entry):#x} bytes, too short to "
                f"hold the extent at {DESC_EXTENT_OFFSET:#x}")
        entries.append(entry)

    # Proven positions are what we read. The structural scan is then re-run as a
    # cross-check: if some other file's real layout ever disagrees, raise loudly.
    scanned = _find_extent_position(entries, len(data))
    if scanned != DESC_EXTENT_OFFSET:
        raise ValueError(
            f"structural scan puts the (offset, length) pair at descriptor offset "
            f"{scanned:#x}, but the firmware reads it at {DESC_EXTENT_OFFSET:#x}")

    sections: list[Section] = []
    for i, entry in enumerate(entries):
        name = _cstr(entry, DESC_NAME_OFFSET, DESC_NAME_LEN)
        offset, length = struct.unpack_from("<II", entry, DESC_EXTENT_OFFSET)
        sections.append(Section(
            index=i, name=name, offset=offset, length=length, raw_entry=entry))

    return McfContainer(
        config_name=config_name,
        section_count=count,
        sections=sections,
        header_word_04=word_04,
        header_word_20=header_size,
    )


def _safe_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


def extract_sections(data: bytes, dest: Path) -> list[Path]:
    """Write each named, non-empty section to `dest`. Returns the paths written."""
    dest.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for s in parse_container(data).sections:
        if not s.name or not s.length:
            continue
        path = dest / f"{s.index:02d}-{_safe_filename(s.name)}.bin"
        path.write_bytes(data[s.offset:s.offset + s.length])
        written.append(path)
    return written
