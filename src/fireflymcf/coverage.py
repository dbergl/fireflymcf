"""Byte-coverage accounting for an MCF.

Every byte must be claimed by exactly one parser. This is the strongest correctness
check available: a wrong record size leaves a trail of unclaimed bytes, and two parsers
disagreeing about a region shows up as an overlap. Both are caught here rather than
silently producing a plausible-looking but wrong decode.

Two depths are available.

`analyse(data)` accounts for the container: the fixed header, the descriptor pointer
array, the header checksum, each section descriptor and its checksum, and each section
payload and its checksum. Every checksum claimed here is *verified*, not assumed --
see `crc16_aug_ccitt`.

`analyse(data, deep=True)` additionally replaces the single `section:...GuiData` span
with GuiData's internal structure: the table descriptors and the tables they declare,
the zero fill up to `0x10000`, the image sub-container header, each object-pointer
table and each object those tables delimit. This is the falsifying version -- the
sub-claims have to tile the section exactly, so a wrong table size or a mis-read
pointer leaves a gap or raises an overlap.

Claims are structural, never catch-alls. The one label that absorbs bytes without
naming a structure is `padding`, and it is deliberately narrow: a run is padding only
if every byte in it is `0x00` or `0xFF`. That rule exists so it cannot be widened to
paper over a parsing hole.

What the deep pass does NOT claim to have decoded:

  * an object's interior. An `object:...` span is exactly what the object-pointer
    table asserts -- "an object begins here, and the next one begins there". The
    display-list records inside it are decoded by `guidata.parse_screen`, which can
    only walk ~1.8-2.6% of the GuiData section -- 1.8% in 11.23, 2.4% in 13.6 and 2.6%
    in 14.7, as the union of the spans `guidata.iter_screens` walks over the section
    length (`guidata`'s opcode table is known to be incomplete). Record-level coverage
    is therefore far below span-level coverage.
  * where an `object-trailing:...` span ends. One object per table -- the last -- has
    no next pointer to bound it and runs to wherever the next structure starts. The
    guard on those spans is a size bound, not a decode, and `report.trailing_headroom`
    measures exactly how much slack it leaves: 58,092 bytes in 11.23 (6.42% of the
    file), 59,144 in 13.6 (5.54%) and 60,148 in 14.7 (7.51%). See `_claim_guidata`.
  * the interior of the image sub-container header at section offset `0x10000`. Its
    magic is verified and its extent is pinned between that magic and the first
    object-pointer table, but the palette-like quads and u16s inside it are not
    decoded.
  * the interiors of the LCD Firmware, GuiCode and G12 sections.
"""
from __future__ import annotations

import binascii
import bisect
import struct
from dataclasses import dataclass, field

from . import container, wordruns

# The MCF's checksum, recovered here and verified against every checksum field in the
# corpus: CRC-16/AUG-CCITT -- polynomial 0x1021, initial value 0x1D0F, input and output
# unreflected, no final xor. It matches the stored u16 in all 3 header checksums, all
# 12 descriptor checksums and all 12 section-payload checksums -- 27 of 27 -- while 14
# other standard CRC-16 variants (CCITT-FALSE, XMODEM, KERMIT, ARC, MODBUS, USB, MAXIM,
# DDS-110, GENIBUS, MCRF4XX, RIELLO, T10-DIF, DECT-R, EN-13757) match 0 of 27.
# `docs/PARSER-LOCATION.md` proves *where* each checksum is read and over what extent;
# this constant is what those bytes are checked against, and
# `tests/test_coverage.py::test_crc_matches_every_stored_checksum` re-runs the 27.
CRC_POLY = 0x1021
CRC_INIT = 0x1D0F

# Section-payload checksum position, from cli.bin's flash-readback verify at
# 0x200151E4-0x20015202: `length - 4` bytes are summed and compared against the u16
# stored at `(length - 4) & ~1`.
SECTION_CRC_BACKOFF = 4

# Descriptor checksum position, from the extra-word countdown at 0x200164BE: the u16
# lands at `descriptor + size - 4`.
DESC_CRC_BACKOFF = 4

# Header checksum position, from 0x200161C6: `hdr[0x04] - 4`. A separate fact from the
# two above that happens to use the same 4, so it gets its own name.
HEADER_CRC_BACKOFF = 4

# GuiData internals. The section begins with an 8-byte head followed by a run of
# 8-byte table descriptors `<u16 tag><u16 ?><u16 offset><u16 size>`; the descriptor run
# ends where the first table it declares begins.
GUIDATA_HEAD_SIZE = 8
GUIDATA_DESCRIPTOR_SIZE = 8

# Magic of the image sub-container that follows the zero fill. Verified at section
# offset 0x10000 in all three corpus files.
IMAGE_CONTAINER_MAGIC = 0x8F7E6D5C
IMAGE_CONTAINER_OFFSET = 0x10000
# The sub-container header is claimed as "from its magic to its pointer table", which
# without a bound would be an unlimited catch-all: miss the pointer table and the claim
# silently swallows everything up to the next one. It measures 0x80 bytes in 11.23 and
# 13.6 and 0x8C in 14.7, so anything approaching this bound means the table was missed.
# Removing this check lets `MIN_POINTER_TABLE = 64` absorb 221 KB and still report 100%.
IMAGE_CONTAINER_HEADER_MAX = 0x200

# An object-pointer table is a maximal run of u32s of the form 0x900XXXXX whose low
# bits land inside the section -- GuiData is mapped whole at 0x90000000, so such a word
# is a section offset. See `guidata.object_tables`, which uses the same rule with a
# higher minimum length.
POINTER_BASE = 0x90000000
POINTER_MASK = 0xFFF00000
# Four is enough because every run found is checked against a property no coincidence
# survives: its lowest pointer must equal the run's own end offset (see
# `_pointer_tables`). Measured on the corpus: all 12 runs of >= 4 words have that
# property (8/9, 29/30, 1077-1422 and 2687-3295 words per file), and of the 616 runs of
# 1..3 words across the three files, zero do.
MIN_POINTER_TABLE = 4

PADDING_BYTES = frozenset({0x00, 0xFF})


def is_padding(chunk: bytes) -> bool:
    """True if every byte is 0x00 or 0xFF.

    Deliberately narrow. This is the only claim in the module that names no structure,
    so it is the only one that could be stretched to hide a parsing hole; keeping it to
    two byte values means a gap full of real data stays unclaimed and fails the build.
    """
    return set(chunk) <= PADDING_BYTES


class CoverageError(ValueError):
    """A structure this module claims to understand does not hold in this file."""


def crc16_aug_ccitt(data: bytes) -> int:
    """CRC-16/AUG-CCITT of `data` (poly `CRC_POLY`, init `CRC_INIT`, unreflected, no xor-out).

    `binascii.crc_hqx` is the C implementation of exactly this CRC family (poly 0x1021,
    unreflected, no final xor) with a caller-supplied initial value, so seeding it with
    `CRC_INIT` gives the AUG-CCITT variant. It replaced the bit-serial pure-Python loop that
    `_crc16_aug_ccitt_reference` keeps: that loop cost ~0.9 s per MB and was **44% of a full
    `builds/from_stock.py` run** (315 calls, 28 s of 65 s -- every reseal recomputes nine
    checksums, three of them over whole sections). `tests/test_coverage.py` pins the two
    implementations equal on random inputs, and the 27-of-27 corpus check still runs.
    """
    return binascii.crc_hqx(data, CRC_INIT)


def _crc16_aug_ccitt_reference(data: bytes) -> int:
    """The bit-serial definition of the CRC. The oracle `crc16_aug_ccitt` is tested against."""
    crc = CRC_INIT
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ CRC_POLY) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


@dataclass
class CoverageReport:
    total: int
    spans: list[tuple[int, int, str]] = field(default_factory=list)
    # Bytes that the trailing-object bound would still have accepted (see
    # `_claim_guidata`): the amount by which the deep pass's unpinned spans could grow
    # before a check fires. Zero for the shallow pass. This is a statement about how
    # much slack the guards leave, not about bytes known to be wrong -- but it is the
    # honest upper bound on what a missed structure could hide behind an `object:`
    # claim, so it is reported rather than left implicit.
    trailing_headroom: int = 0

    @property
    def claimed(self) -> int:
        return sum(length for _, length, _ in self.spans)

    @property
    def unclaimed(self) -> list[tuple[int, int]]:
        gaps: list[tuple[int, int]] = []
        cursor = 0
        for start, length, _ in sorted(self.spans):
            if start > cursor:
                gaps.append((cursor, start - cursor))
            cursor = max(cursor, start + length)
        if cursor < self.total:
            gaps.append((cursor, self.total - cursor))
        return gaps

    def bytes_by_label(self) -> dict[str, int]:
        """Claimed bytes per label prefix (the part before the first ':')."""
        out: dict[str, int] = {}
        for _, length, label in self.spans:
            key = label.split(":", 1)[0]
            out[key] = out.get(key, 0) + length
        return out


def claim(report: CoverageReport, start: int, length: int, label: str) -> None:
    """Record [start, start+length) as understood. Raises on overlap or out-of-range.

    `report.spans` is kept sorted by start so that the overlap check only has to look
    at the two neighbouring spans; the deep pass makes over 12,000 claims per file and
    a linear scan per claim would be quadratic.
    """
    if start < 0 or length < 0 or start + length > report.total:
        raise ValueError(
            f"claim {label} [{start:#x}+{length:#x}] out of range for "
            f"{report.total:#x} bytes")
    if length == 0:
        # A zero-length claim accounts for nothing while looking like a claim. Nothing
        # in this module needs one -- zero-size table descriptors and zero-length
        # sections are skipped at the call site -- so it is an error, not a no-op.
        raise ValueError(f"claim {label} at {start:#x} is empty")
    index = bisect.bisect_left(report.spans, (start,))
    if index:
        prev_start, prev_len, prev_label = report.spans[index - 1]
        if prev_start + prev_len > start:
            raise ValueError(
                f"claim {label} [{start:#x}+{length:#x}] overlaps "
                f"{prev_label} [{prev_start:#x}+{prev_len:#x}]")
    if index < len(report.spans):
        next_start, next_len, next_label = report.spans[index]
        if start + length > next_start:
            raise ValueError(
                f"claim {label} [{start:#x}+{length:#x}] overlaps "
                f"{next_label} [{next_start:#x}+{next_len:#x}]")
    report.spans.insert(index, (start, length, label))


def _claim_checksum(report: CoverageReport, data: bytes, covered_start: int,
                    at: int, label: str) -> None:
    """Claim the u16 at `at` as a checksum, having verified it covers [covered_start, at).

    Verification is the point: an unverified "checksum" claim would be a two-byte
    catch-all. If the stored word does not match, this raises rather than claiming.
    """
    stored = struct.unpack_from("<H", data, at)[0]
    computed = crc16_aug_ccitt(data[covered_start:at])
    if stored != computed:
        raise CoverageError(
            f"{label}: stored checksum {stored:#06x} at {at:#x} does not match "
            f"CRC-16/AUG-CCITT {computed:#06x} over [{covered_start:#x}, {at:#x})")
    claim(report, at, 2, label)


def _pointer_tables(data: bytes, start: int, end: int) -> list[tuple[int, int, list[int]]]:
    """Every object-pointer table in the absolute range [start, end).

    Returns (table_start, table_end, pointers) with pointers as absolute file offsets.

    A run qualifies only if it is at least MIN_POINTER_TABLE words long *and* its
    lowest pointer equals the run's own end -- i.e. the table is immediately followed
    by the objects it addresses. That second condition is what makes a short minimum
    safe: it holds for all 12 runs in the corpus and cannot plausibly hold by accident,
    since it ties the run's contents to the run's own position.
    """
    span = end - start
    tables: list[tuple[int, int, list[int]]] = []
    # Every whole aligned word in [start, end), scanned for runs by `wordruns.pointer_runs`
    # (this runs on every reseal and every relocation gate).
    n = (end - start) // 4
    for first, rels in wordruns.pointer_runs(data, start, n, POINTER_BASE, POINTER_MASK, span):
        if len(rels) < MIN_POINTER_TABLE:
            continue
        offset = start + 4 * first                  # file offset of the run's first word
        run_end = offset + 4 * len(rels)
        pointers = [r + start for r in rels]
        if min(pointers) != run_end:
            raise CoverageError(
                f"pointer table at {offset - start:#x} has lowest pointer "
                f"{min(pointers) - start:#x}, expected its own end "
                f"{run_end - start:#x}")
        tables.append((offset, run_end, pointers))
    return tables


def _claim_guidata(report: CoverageReport, data: bytes, start: int, end: int) -> None:
    """Claim GuiData's internals over the absolute range [start, end).

    `end` is the section's checksum position, not the section end: the checksum and any
    bytes after it are claimed by the caller.
    """
    # --- the table descriptors and the tables they declare -------------------
    first_tag, _, first_offset, _ = struct.unpack_from(
        "<HHHH", data, start + GUIDATA_HEAD_SIZE)
    descriptor_end = start + first_offset
    # Strictly greater: the first table must leave room for at least the one descriptor
    # that declares it. Equality would claim an empty descriptor run and walk no
    # descriptors at all, which used to reach `tags[0]` on an empty list.
    if (first_offset <= GUIDATA_HEAD_SIZE
            or (first_offset - GUIDATA_HEAD_SIZE) % GUIDATA_DESCRIPTOR_SIZE):
        raise CoverageError(
            f"GuiData's first table starts at {first_offset:#x}, which is not a whole "
            f"number of {GUIDATA_DESCRIPTOR_SIZE}-byte descriptors past "
            f"{GUIDATA_HEAD_SIZE:#x}")
    claim(report, start, GUIDATA_HEAD_SIZE, "guidata-head")
    claim(report, start + GUIDATA_HEAD_SIZE,
          first_offset - GUIDATA_HEAD_SIZE, "guidata-table-descriptors")

    at = start + GUIDATA_HEAD_SIZE
    tags = []
    while at < descriptor_end:
        tag, _, offset, size = struct.unpack_from("<HHHH", data, at)
        tags.append(tag)
        # A zero-size descriptor declares no bytes; claiming a zero-length span at the
        # same offset as the next table would read as an overlap, so skip it.
        if size:
            if start + offset + size > end:
                raise CoverageError(
                    f"GuiData table {tag:#06x} at {offset:#x}+{size:#x} runs past the "
                    f"section")
            claim(report, start + offset, size, f"guidata-table:{tag:#06x}")
        at += GUIDATA_DESCRIPTOR_SIZE
    if tags[0] != first_tag:
        raise CoverageError("GuiData descriptor walk disagrees with its first entry")

    # --- the image sub-container --------------------------------------------
    image_at = start + IMAGE_CONTAINER_OFFSET
    magic = struct.unpack_from("<I", data, image_at)[0]
    if magic != IMAGE_CONTAINER_MAGIC:
        raise CoverageError(
            f"expected the image sub-container magic {IMAGE_CONTAINER_MAGIC:#010x} at "
            f"section offset {IMAGE_CONTAINER_OFFSET:#x}, found {magic:#010x}")

    tables = _pointer_tables(data, start, end)
    if not tables:
        raise CoverageError("GuiData has no object-pointer table")
    header_size = tables[0][0] - image_at
    if header_size <= 0 or header_size > IMAGE_CONTAINER_HEADER_MAX:
        raise CoverageError(
            f"the image sub-container header would be {header_size:#x} bytes, outside "
            f"(0, {IMAGE_CONTAINER_HEADER_MAX:#x}] -- its pointer table was not found "
            f"where one is expected, so this claim would be absorbing unknown bytes")
    # Header of the image sub-container: pinned between its verified magic and the
    # first object-pointer table, and bounded above so it cannot absorb a missed table.
    # Its interior is not decoded.
    claim(report, image_at, header_size, "image-container-header")

    # --- the object-pointer tables and the objects they delimit --------------
    # Each table is followed by the objects it addresses, which run up to the next
    # table (tables nest: an object may itself be a sub-container with its own table)
    # or, for the last table, up to the section checksum.
    boundaries = [table_start for table_start, _, _ in tables[1:]] + [end]
    for (table_start, table_end, pointers), region_end in zip(tables, boundaries):
        claim(report, table_start, table_end - table_start,
              f"object-table:{table_start - start:#x}[{len(pointers)}]")
        distinct = sorted(set(pointers))
        if distinct[0] != table_end:
            raise CoverageError(
                f"object table at {table_start - start:#x} does not begin its objects "
                f"at its own end")
        if distinct[-1] >= region_end:
            raise CoverageError(
                f"object table at {table_start - start:#x} points at "
                f"{distinct[-1] - start:#x}, at or past the next structure at "
                f"{region_end - start:#x}")
        if len(distinct) < 2:
            raise CoverageError(
                f"object table at {table_start - start:#x} has one distinct pointer; "
                f"its trailing object cannot be bounded")
        # Every object but the last is bounded by the next pointer. The last one runs to
        # the next structure, and its END IS ASSERTED BY NOTHING BUT WHERE THAT NEXT
        # STRUCTURE STARTS. Without a bound it is a pure catch-all -- with the bound it
        # is a partial one, and the size of what it could still hide is measured below
        # and reported as `trailing_headroom`. This is the weakest claim in the module.
        #
        # The bound depends on the table's shape. Measured trailing / largest /
        # headroom per table:
        #
        #   11.23   25208/64488/39280   32/2616/2584   20/20/0   16/16244/16228
        #   13.6    26164/64488/38324   32/2728/2696   20/20/0   16/18140/18124
        #   14.7     9056/64488/55432   36/2708/2672   20/20/0   16/2060/2044
        #
        # There are two kinds of table here, and only one takes the max-sibling bound:
        #
        #  * The 29/30-entry table is UNIFORM STRIDE: every object it delimits is
        #    exactly 20 bytes, in all three builds (28 interior records in 14.7, 29 in
        #    11.23/13.6, no exceptions -- verified in `test_the_uniform_stride_table_...`).
        #    Its trailing object is one more record of the same table, so its end is not
        #    a heuristic at all: it must be exactly the stride. That is why its headroom
        #    is zero -- correct by construction, not the knife edge it once read as
        #    (§10 item 13). A trailing span that is not exactly one stride is either an
        #    unpointed extra record or an absorbed structure; either way the gate fires.
        #  * The other three tables have objects of many sizes, so the only available
        #    bound on the last one is that it be no larger than a sibling this same
        #    table already delimits. The largest single unpinned span is the image
        #    table's trailing object (`object:0x3fb14`, 26,164 bytes in 13.6, 25,208 in
        #    11.23). What supports it being one object rather than an object plus
        #    something unrecognised is its head: `22 00 03 ...`, matching the
        #    `<u8> 00 <u8>` shape of all seven siblings in that table (`0a 00 7f`), and
        #    in 14.7 a sibling shares its leading `06` exactly. That is evidence about
        #    where the span BEGINS. It says nothing about where it ends, which is why
        #    the headroom is reported -- this is the weakest claim in the module.
        #
        # `guidata.parse_screen` cannot decode any of the four trailing objects, so
        # there is no record walk available to pin an end on the non-uniform ones. If
        # one is found, use it here.
        sizes = [b - a for a, b in zip(distinct, distinct[1:])]
        trailing = region_end - distinct[-1]
        if len(set(sizes)) == 1:
            # Uniform-stride table: the trailing record must be exactly one stride.
            stride = sizes[0]
            if trailing != stride:
                raise CoverageError(
                    f"the trailing object of the uniform-stride table at "
                    f"{table_start - start:#x} would be {trailing:#x} bytes, not the "
                    f"table's {stride:#x}-byte stride; it is absorbing something else")
            # Headroom is genuinely zero on a uniform-stride table -- add nothing.
        else:
            largest = max(sizes)
            if trailing > largest:
                raise CoverageError(
                    f"the trailing object of the table at {table_start - start:#x} would "
                    f"be {trailing:#x} bytes, larger than the biggest object this table "
                    f"delimits ({largest:#x}); it is absorbing something else")
            report.trailing_headroom += largest - trailing
        for object_start, next_start in zip(distinct, distinct[1:] + [region_end]):
            label = "object-trailing" if next_start == region_end else "object"
            claim(report, object_start, next_start - object_start,
                  f"{label}:{object_start - start:#x}")


def analyse(data: bytes, deep: bool = False) -> CoverageReport:
    """Claim every byte of an MCF using the parsers built in earlier tasks.

    With `deep=True`, the GuiData section is claimed structure by structure instead of
    as one span; the sub-claims must tile the section exactly.
    """
    report = CoverageReport(total=len(data))
    c = container.parse_container(data)

    # --- header, descriptor pointer array, header checksum -------------------
    # The firmware reads a fixed 0xA4-byte header (0x2001614A) and then seeks to
    # hdr[0x22] to read count u32 descriptor pointers (0x20016196-0x200161B0).
    header_size = c.header_word_20
    claim(report, 0, header_size, "header")
    claim(report, header_size, c.section_count * 4, "descriptor-pointers")

    # The header checksum sits at hdr[0x04] - 4 (0x200161C6) and covers everything
    # before it -- the header and the pointer array.
    checksum_at = (c.header_word_04 & 0xFFFF) - HEADER_CRC_BACKOFF
    if checksum_at != header_size + c.section_count * 4:
        raise CoverageError(
            f"the header checksum at {checksum_at:#x} does not sit immediately after "
            f"the pointer array ending at {header_size + c.section_count * 4:#x}")
    _claim_checksum(report, data, 0, checksum_at, "header-checksum")

    # --- section descriptors -------------------------------------------------
    for i, section in enumerate(c.sections):
        descriptor_at = struct.unpack_from("<I", data, header_size + 4 * i)[0]
        size = struct.unpack_from("<H", data, descriptor_at + 2)[0]
        body = size - DESC_CRC_BACKOFF
        claim(report, descriptor_at, body, f"descriptor:{i}")
        _claim_checksum(report, data, descriptor_at, descriptor_at + body,
                        f"descriptor-checksum:{i}")

    # --- section payloads ----------------------------------------------------
    for section in c.sections:
        if not section.length:
            continue
        checksum_at = (section.length - SECTION_CRC_BACKOFF) & ~1
        name = section.name or str(section.index)
        if deep and _is_guidata(name):
            _claim_guidata(report, data, section.offset, section.offset + checksum_at)
        else:
            claim(report, section.offset, checksum_at, f"section:{name}")
        _claim_checksum(report, data, section.offset, section.offset + checksum_at,
                        f"section-checksum:{name}")

    # Anything left is padding. Claim it explicitly rather than tolerating gaps, so a
    # genuine parsing hole cannot hide among the padding -- and only if every byte is
    # 0x00 or 0xFF, so the rule cannot be stretched to cover one.
    for start, length in list(report.unclaimed):
        if is_padding(data[start:start + length]):
            claim(report, start, length, "padding")

    return report


def _is_guidata(name: str) -> bool:
    from . import guidata
    return guidata.GUIDATA_SECTION_MARKER in name


def format_report(report: CoverageReport) -> str:
    lines = [
        f"total   {report.total:>10} bytes",
        f"claimed {report.claimed:>10} bytes "
        f"({100.0 * report.claimed / report.total:.4f}%)",
        "NOTE: claimed means accounted for by a structure, NOT decoded. An object: span",
        "      is the extent an object table asserts; its records are not walked. Only",
        "      ~1.8-2.6% of the GuiData section decodes at record level, by build:",
        "      11.23 1.8%, 13.6 2.4%, 14.7 2.6%.",
    ]
    if report.trailing_headroom:
        lines.append(
            f"      Trailing-object spans end where the next structure starts; the "
            f"bound on them\n      would still accept {report.trailing_headroom} more "
            f"bytes ({100.0 * report.trailing_headroom / report.total:.2f}% of the "
            f"file). See coverage._claim_guidata.")
    for label, count in sorted(report.bytes_by_label().items(),
                               key=lambda kv: -kv[1]):
        lines.append(f"  {label:<28} {count:>10}")
    if report.unclaimed:
        lines.append(f"unclaimed spans: {len(report.unclaimed)}")
        for start, length in report.unclaimed[:20]:
            lines.append(f"  {start:#010x} {length:>10}")
    else:
        lines.append("unclaimed spans: none")
    return "\n".join(lines)
