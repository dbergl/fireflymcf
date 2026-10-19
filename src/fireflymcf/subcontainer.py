"""The `0x8F7E6D5C` GUI-resource sub-container.

Two images in the corpus carry a sub-container tagged with this magic, and until now they
were documented apart (`MCF-FORMAT.md` §2 and §5.5) without noting the shared wrapper:

  * **`cli.bin` region 2**, loaded at `0xC0400000` (`armimg` header `+0x24`, §4.2) -- the
    loader's own GUI resource blob. Its payload is two pointer tables: a **131-entry
    ParamID -> name table** at `+0x10` (the names the loader prints in its diagnostic log,
    indexed by descriptor tag; `[0xC0400010 + tag*4]` read at region-1 `0x2001485A`, §2), and
    a **138-entry table** at `+0x10c8` of the loader's own screen/widget records -- a GuiData
    dialect carrying the boot-menu strings ("Press START AUTO LOAD to perform any required
    ...", "ON", "OFF"), read by the loader's renderer at the `0xC04010C8` literals
    `0x2000e71c` / `0x2000e9c0`.
  * **`GuiData` at section offset `0x10000`** (§5.5) -- the same magic, but the payload after
    the header is the panel's mode-6 colour palette, not a pointer table.

So the sub-container is a generic resource wrapper: a fixed **16-byte header**, then a payload
whose shape is decided by who reads it. The header:

    +0x00  u32  magic 0x8F7E6D5C
    +0x04  u32  build word -- u16 +0x04 is the version stamp, byte +0x07 the build id
    +0x08  u32  reserved (0 across the corpus)
    +0x0C  u32  reserved (0 across the corpus)
    +0x10       payload

The build word is not free: the LCD Firmware cross-checks the sub-container's u16 `+0x04` and
byte `+0x07` against the parent section head (`GuiData +0x00`/`+0x04`, `GuiCode +0x0A`/`+0x08`)
and rejects the blobs on a mismatch (`LCD-FIRMWARE.md` Finding 8, `MCF-FORMAT.md` §5.1). On
13.6 every instance -- in `mcf.bin` *and* in the separately-shipped `cli.bin` -- carries the
same `0x66_011234` (build id `0x66`, version `0x1234`), a firmware-family stamp for the release.

The loader's GUI **record grammar** is the GuiData widget-record format itself, not a dialect:
`records.object_records` decodes the loader screens straight, and the labels that come out are
exactly the loader's on-screen text -- "START AUTO LOAD", "CANCEL", "AUTO LOAD CONFIGURATION",
"Error accessing Master Config File", "EXIT", "ON", "OFF" (`loader_gui_screens` /
`loader_gui_text`). cli.bin reuses the panel's GUI toolkit: `FUN_2000E028` walks an object's
records exactly like `object_records`, calling the per-record decoder `FUN_20009cd8` -- which is
`FUN_00252CB0` field-for-field -- and unioning child boxes the way GuiData's `FUN_00257BFC` does.
Disassembling `FUN_20009cd8` turned up the one difference from the panel decoder: it reads the
R35 field's cursor byte whenever header byte-5 bit `0x80` is set, where `FUN_00252CB0` also gates
that on `(uV16 & 0x2901) or (aee & 0x40)`. `records.decode_record(..., loader_r35=True)` applies
the loader's gate, and with it **every 13.6/14.7 loader screen decodes**. Only the older 11.23
toolkit still has two screens (the CONFIGURATION pages) using a further path this decoder does
not model -- the last open point, `MCF-FORMAT.md` §10 item 12.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from . import armimg, records

MAGIC = 0x8F7E6D5C
HEADER_LEN = 0x10
#: The loader's "no name for this tag" sentinel string, stored once and pointed at by every
#: out-of-range slot of the ParamID table.
UNKNOWN_NAME = "Unknown"


@dataclass(frozen=True)
class SubContainerHeader:
    magic: int
    version: int      # u16 at +0x04 -- the version stamp cross-checked against the parent
    build_id: int     # byte at +0x07 -- the build id cross-checked against the parent


def parse_header(blob: bytes, off: int = 0) -> SubContainerHeader:
    """Read the 16-byte sub-container header at `off` in `blob`.

    Raises `ValueError` if the magic is wrong, so a caller can use it to test "is there a
    sub-container here" by catching it. `blob` is the containing image; `off` its start.
    """
    if len(blob) - off < HEADER_LEN:
        raise ValueError(f"need {HEADER_LEN} bytes for a sub-container header at {off:#x}")
    magic, build_word = struct.unpack_from("<II", blob, off)
    if magic != MAGIC:
        raise ValueError(f"bad sub-container magic {magic:#010x}, expected {MAGIC:#010x}")
    return SubContainerHeader(
        magic=magic,
        version=build_word & 0xFFFF,
        build_id=(build_word >> 24) & 0xFF,
    )


def _cstr(blob: bytes, off: int) -> str:
    end = blob.find(b"\x00", off)
    if end < 0:
        end = len(blob)
    return blob[off:end].decode("latin1")


def _pointer_table(blob: bytes, base: int, start: int):
    """Yield `(index, target_offset)` for the run of `[base, base+len)` pointers from `start`.

    Stops at the first word that is not a pointer into the blob -- the tables in this
    sub-container are laid out as a contiguous pointer array followed by the pool they point
    into, so the array ends exactly where the pool begins.
    """
    idx = 0
    off = start
    n = len(blob)
    while off + 4 <= n:
        word = struct.unpack_from("<I", blob, off)[0]
        if not base <= word < base + n:
            break
        yield idx, word - base
        idx += 1
        off += 4


def loader_region2(cli: bytes) -> tuple[bytes, int]:
    """`(blob, load_base)` for `cli.bin`'s region-2 sub-container.

    `load_base` is the region's real load address -- the `armimg` header's `+0x24` word
    (`0xC0400000`), NOT its declared `+0x18` (which overlays region 1; §4.2). Validates the
    magic so a non-loader image fails here rather than returning noise.
    """
    hdr = armimg.parse_header(cli)
    r2 = hdr.regions[1]
    blob = cli[r2.file_offset:r2.file_offset + r2.length]
    parse_header(blob)                       # raises on a wrong magic
    return blob, hdr.word_24


def loader_param_names(cli: bytes) -> dict[int, str]:
    """`{ParamID: name}` from `cli.bin` region 2's sub-container name table.

    This is the firmware's own table -- the loader indexes `[0xC0400010 + tag*4]` at
    `0x2001485A` to format `"  Param %s = %X, target = %X"` in its diagnostic log (§2), so
    these are the authoritative names for the descriptor tags (§2) and the RV-C `0FF02`
    parameters (`upload.py`). Out-of-range slots point at the shared `"Unknown"` string and
    are omitted, so the result holds only the named parameters.
    """
    blob, base = loader_region2(cli)
    out: dict[int, str] = {}
    for idx, target in _pointer_table(blob, base, HEADER_LEN):
        name = _cstr(blob, target)
        if name != UNKNOWN_NAME:
            out[idx] = name
    return out


def loader_param_name_count(cli: bytes) -> int:
    """How many slots the ParamID table has (named or `Unknown`) -- 0x83 on the corpus."""
    blob, base = loader_region2(cli)
    return sum(1 for _ in _pointer_table(blob, base, HEADER_LEN))


def _pointer_runs(blob: bytes, base: int):
    """`[(start, count)]` for every maximal run of >= 2 word-aligned `[base, base+len)`
    pointers in `blob`. The sub-container's two tables (names at `+0x10`, GUI records after)
    show up as the two long runs; short coincidences are filtered by the >= 2 floor and, at the
    call sites, by which run starts at the header."""
    runs = []
    off = 0
    n = len(blob)
    while off + 4 <= n:
        word = struct.unpack_from("<I", blob, off)[0]
        if base <= word < base + n:
            j = off
            while j + 4 <= n and base <= struct.unpack_from("<I", blob, j)[0] < base + n:
                j += 4
            if (j - off) // 4 >= 2:
                runs.append((off, (j - off) // 4))
            off = j
        else:
            off += 4
    return runs


def loader_gui_record_table(cli: bytes) -> list[int]:
    """Blob-relative offsets of the loader's GUI screen records (`cli.bin` region 2).

    The second pointer table in the sub-container (after the ParamID name table at `+0x10`):
    137-138 pointers, one per screen object, at `+0xec4` (11.23) / `+0x10c8` (13.6, 14.7). Each
    target is a GuiData object -- `<count><records>` -- so it is located by structure (the long
    run that is not the name table), not a per-build constant.
    """
    blob, base = loader_region2(cli)
    runs = [(o, c) for o, c in _pointer_runs(blob, base) if o != HEADER_LEN]
    if not runs:
        raise ValueError("no GUI-record pointer table found in the loader sub-container")
    start, count = max(runs, key=lambda rc: rc[1])
    return [struct.unpack_from("<I", blob, start + k * 4)[0] - base for k in range(count)]


def loader_gui_screens(cli: bytes) -> list[list | None]:
    """The loader's GUI, one entry per record-table slot, decoded with the GuiData decoder.

    **The loader's GUI record grammar is the GuiData widget-record format** (`records.py`, the
    replay of `FUN_00252CB0`): an object is `<count><records>`, each record an 8-byte header
    plus fields chosen by widget type and the header's presence bits, and text records carry
    the label inline. cli.bin reuses the panel's GUI toolkit and its own render/layout engine
    (`FUN_2000E028` decodes a record; the caller unions child boxes exactly as GuiData's
    `FUN_00257BFC` does), so no separate grammar exists to transcribe -- this returns
    `records.WRecord` lists, the same type the panel path produces.

    Records are decoded with `records.object_records(..., loader_r35=True)`, the loader's
    variant of the R35 field gate: `FUN_20009cd8` reads the R35 cursor byte whenever header
    byte-5 bit `0x80` is set, where the panel's `FUN_00252CB0` also requires
    `(uV16 & 0x2901) or (aee & 0x40)` (§4.2, found by disassembling both). With that, **every
    screen on 13.6 and 14.7 decodes** -- the reference-build loader GUI is fully accounted for.

    Each entry is the decoded record list, or `None` for a screen that still derails. On
    13.6/14.7 there are none. On the **older 11.23 toolkit** two screens (the AUTO LOAD /
    MANUAL LOAD CONFIGURATION pages, table slots 125 and 130) overrun for a *different*,
    un-classified reason -- another field-presence path that toolkit revision uses and this
    decoder does not model; that is the remaining open point (`MCF-FORMAT.md` §10 item 12).
    Decoding is bounded by each object's extent (the next-higher table target), so a derail is
    reported as `None` rather than raising or bleeding into the next object.
    """
    blob, _base = loader_region2(cli)
    targets = loader_gui_record_table(cli)
    extent_end = sorted(set(targets))
    import bisect

    out: list[list | None] = []
    for start in targets:
        i = bisect.bisect_right(extent_end, start)
        end = extent_end[i] if i < len(extent_end) else len(blob)
        try:
            recs = records.object_records(blob, start, loader_r35=True)
        except Exception:
            out.append(None)
            continue
        # A clean decode lands within the object's extent (0-3 bytes of padding before the next).
        out.append(recs if recs and recs[-1].end <= end else None)
    return out


def loader_gui_text(cli: bytes) -> list[str]:
    """Every label string in the loader's GUI, in table then record order.

    The loader's on-screen text -- `"Press START AUTO LOAD to perform any required updates"`,
    `"START AUTO LOAD"`, `"CANCEL"`, `"AUTO LOAD Successful\\nRemove USB Flash Drive to resume
    normal operation"`, `"ON"`, `"OFF"` -- read from the decodable screens (`loader_gui_screens`).
    """
    out: list[str] = []
    for recs in loader_gui_screens(cli):
        if recs:
            out += [r.label for r in recs if r.label]
    return out
