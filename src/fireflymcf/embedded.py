"""Recover load addresses for the ARM images bundled inside an MCF, and (the part
that actually works on this corpus) characterise each section's container format.

`identify`/`classify`/`DEFAULT_BASES` are the load-address recovery path: the only
structural handle for an unheadered image is the Cortex-M vector table at the start
of its payload, accepted only if its handlers are Thumb-tagged and land inside the
image at the candidate base. A wrong base is worse than no answer, because it
silently misdirects a disassembly, so `identify` stays exactly this strict rather
than being loosened to force a match.

On this corpus it identifies exactly one section: `LCD Firmware`, at base
0x00210000. It finds nothing for GuiCode/GuiData/G12, which are not Cortex-M images
at all.

`characterise` is the Task 4 deliverable: a lightweight fingerprint of each section
from its first few words, sufficient to tell a Cortex-M image apart from the
GuiCode/GuiData/G12 container formats. Locating the real parser for those other
formats is left to a later task; the MCF container itself is now solved, in cli.bin
region 1 — see docs/PARSER-LOCATION.md.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from . import armimg, container

# --- characterise() constants -------------------------------------------------
#
# Observed, byte-identical across all three corpus files (11.23 / 13.6 / 14.7):
#
#   LCD Firmware   word[0] == 0x20050000              (answer_key.CLI_INITIAL_SP,
#                                                       exactly -- cli.bin's own SRAM
#                                                       stack pointer)
#   GuiCode        word[0] == 0x1A2B3C4D               (a literal magic, not a
#                                                       plausible stack pointer)
#   GuiData / G12  word[0] & 0xFFFF == 0x2066           (a shared container/magic
#                                                       family; the high 16 bits
#                                                       differ by section: 0x0314 for
#                                                       GuiData, 0x0716 for G12, both
#                                                       constant across versions)
#
# See the Task 4 report for the zlib/gzip/lzma/bz2 negative results that rule out
# treating GuiCode/GuiData/G12 as standard-compressed data.
#
# CORRECTION (Task 6): `LCD Firmware` IS a directly loadable, uncompressed Cortex-M
# image, based at 0x00210000. Task 4 concluded the opposite -- that its reset vector
# (0x002104D0) implied a >2 MB image and so the stored 62 KB section had to be
# compressed. That reasoning was an artifact of only ever trying base 0: at base 0
# the vector table needs an image_len above 0x400000 to contain its own handlers,
# whereas at base 0x00210000 the reset vector and every handler land inside the
# 62 KB payload and armimg.looks_like_vector_table() returns True in all three files.
# The base is proven independently: Task 6 recovered 0x00210000 from the MCF section
# descriptor's tag-0x40 entry, and cli.bin loads the literal 0x00210400 (at
# 0x20014D88) to read the version magic 0x44D29AD6 that sits at payload offset
# 0x400. See docs/PARSER-LOCATION.md.
LCD_INITIAL_SP = 0x20050000
GUI_CODE_MAGIC = 0x1A2B3C4D
GUI_CONTAINER_LOW_MAGIC = 0x2066

DEFAULT_BASES: tuple[int, ...] = (
    0x20000000,   # SRAM, matches cli.bin's own load address
    0x08000000,   # common STM32 internal flash
    0x00000000,   # aliased flash
    0xC0400000,   # the unexplained cli.bin +0x24 word
    # Proven flash destinations, read from the MCF section descriptors' tag-0x40
    # entries by the parser in cli.bin region 1 (docs/PARSER-LOCATION.md).
    0x00210000,   # LCD Firmware's destination
    0x00240000,   # GuiCode's destination
)


@dataclass(frozen=True)
class EmbeddedImage:
    section_name: str
    load_addr: int
    entry: int
    initial_sp: int
    payload_offset: int


def classify(data: bytes) -> list[str]:
    """Names of every named, non-empty section considered by identify()."""
    return [s.name for s in container.parse_container(data).sections
            if s.name and s.length]


def identify(data: bytes,
             candidate_bases: tuple[int, ...] = DEFAULT_BASES) -> list[EmbeddedImage]:
    out: list[EmbeddedImage] = []
    for s in container.parse_container(data).sections:
        if not s.name or not s.length:
            continue
        payload = data[s.offset:s.offset + s.length]
        for base in candidate_bases:
            if armimg.looks_like_vector_table(payload, base, len(payload)):
                vt = armimg.read_vector_table(payload, base)
                out.append(EmbeddedImage(
                    section_name=s.name,
                    load_addr=base,
                    entry=vt.reset,
                    initial_sp=vt.initial_sp,
                    payload_offset=s.offset,
                ))
                break
    return out


@dataclass(frozen=True)
class SectionSignature:
    section_name: str
    length: int
    first_words: tuple[int, ...]   # first 4 LE u32 of the payload (fewer if short)
    kind: str                      # "vector-table-like" | "gui-code" | "gui-container" | "unknown"
    build_tag: int | None          # per-file build tag where the format carries one, else None


def _first_words(payload: bytes, count: int = 4) -> tuple[int, ...]:
    n = min(count, len(payload) // 4)
    if n == 0:
        return ()
    return struct.unpack_from(f"<{n}I", payload, 0)


def _kind_of(words: tuple[int, ...]) -> str:
    if not words:
        return "unknown"
    if words[0] == LCD_INITIAL_SP:
        return "vector-table-like"
    if words[0] == GUI_CODE_MAGIC:
        return "gui-code"
    if (words[0] & 0xFFFF) == GUI_CONTAINER_LOW_MAGIC:
        return "gui-container"
    return "unknown"


def _build_tag(words: tuple[int, ...], kind: str) -> int | None:
    # The tag sits at a different word index depending on the container format:
    # "gui-code" carries it in word[2], "gui-container" in word[1]. Both positions
    # were found by comparing the same section across all three corpus files and
    # noting which word varies exactly the same way (low 16 bits) in every one of
    # them -- see the Task 4 report.
    if kind == "gui-code":
        idx = 2
    elif kind == "gui-container":
        idx = 1
    else:
        return None
    if len(words) <= idx:
        return None
    return words[idx] & 0xFFFF


def characterise(data: bytes) -> list[SectionSignature]:
    """Fingerprint every named, non-empty section of an MCF from its first words.

    Deliberately does not claim a load address -- that is `identify`'s job, and it
    now resolves `LCD Firmware` to 0x00210000. GuiCode/GuiData/G12 remain
    unresolved: they are not Cortex-M images, so no base address applies to them.
    A section that matches none of the known signatures is reported as
    `kind="unknown"`, never silently dropped.
    """
    out: list[SectionSignature] = []
    for s in container.parse_container(data).sections:
        if not s.name or not s.length:
            continue
        payload = data[s.offset:s.offset + s.length]
        words = _first_words(payload)
        kind = _kind_of(words)
        out.append(SectionSignature(
            section_name=s.name,
            length=s.length,
            first_words=words,
            kind=kind,
            build_tag=_build_tag(words, kind),
        ))
    return out
