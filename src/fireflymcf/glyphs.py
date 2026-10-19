"""Fonts and glyph bitmaps inside GuiData.

`docs/GUICODE.md` Finding 3 established the *lookup*: a 20-byte font descriptor
`<u8 first><u8 last><u16 base><u16 fallback>`, and `GLYPH_TABLE[base + c - first]` for a
character in range, the fallback index otherwise. That was read out of `FUN_00251AA0`'s
decompilation, so it is `proven-from-code`.

This module adds what that finding did not cover: **the bitmap encoding**. It is now
`proven-from-code` -- a faithful transcription of the glyph blitter **`FUN_002510C0`**
(`docs/GUICODE.md` Finding 22, `extractions/blitter.txt`), the function the text drawers
`FUN_002557C0`/`FUN_002559A0` call once per character. An earlier flat-grid reading of this
module carried three `unknown`s (a 17..20 "start" heuristic, `header[13]` not matching the row
count, and a class of thin/tall glyphs decoding to a fraction of their height); **all three
were one bug** -- the bitmap is compressed and the flat reader did not implement the
compression. Transcribing the blitter dissolves them.

    header[0xa]  u8   x offset
    header[0xb]  u8   width in pixels
    header[0xc]  u8   y offset
    header[0xd]  u8   height in rows          <- the real row count; see the RLE below
    +0xe         ceil(height/8) bytes  per-row RLE bitstream (see below)
    +0xe+rle     pixel rows                    <- one STORED row per run

**The per-row vertical-repeat RLE.** The blitter walks the `height` output rows top to bottom,
reading a 1-bit-per-row bitstream at `+0xe`, **LSB-first, starting at bit 1** of byte `+0xe`
(`uVar15 = glyph[0xe] >> 1` -- bit 0 is skipped; equivalently, output row `r` reads stream bit
`r + 1`, refilling from `+0xf`, `+0x10`... as `uVar13 & 7` wraps). It counts a run of consecutive
**set** bits -- terminated by a **clear** bit *or* by reaching the last row -- to get a run
length `n`, reads **one** stored pixel row, and emits that same row for `n` consecutive output
rows (the DMA vertical repeat `puVar8[0x11] = n | 0x10000`), then advances. Sum of run lengths ==
`height`, so `decode` always yields exactly `header[0xd]` rows. A vertically-uniform stroke (an
`I` stem, an `H`/`T` bar) stores a single row and repeats it -- which is exactly why those glyphs
were tiny on disk and decoded short under the old flat reader. When every RLE bit is clear each
run is length 1 (one stored row per output row), which is the flat case the icons happen to hit --
so `header[0xd]` there equals the stored-row count and the decode is byte-identical to the old
reader (13.6's fan keeps ink 9623). Where a glyph *does* compress (the slot-24 filled ellipse
`'>'` stores 37 rows that expand to its declared 48), the RLE decode is the correct one and the
old flat reader was truncating.

**`start`.** With the format known, the pixel data begins at a fixed `0xe + ceil(height/8)` from
the glyph pointer -- no heuristic. That closed form reproduces every value the old border-ink
heuristic had to search for (the fan's 19 = `0xe + ceil(38/8)`, the ellipse's 20 =
`0xe + ceil(48/8)`, the slot-23 chevron's 17), which is why they varied 17..20: it was
`ceil(height/8)` all along. `Glyph.start` records it; a caller may still pass `start=` to read
the pixel rows from a different offset (used only to demonstrate that the offset matters).

**bpp.** 4bpp (two nibbles/byte, low nibble = left pixel, `ceil(width/2)` bytes/row) when the
font is anti-aliased, else 1bpp (`ceil(width/8)` bytes/row, `(byte >> (7-k)) & 1`, stored as full
0xF coverage). The blitter selects on `*(font + 8) == 4` (`OFF_BPP`); `font_bpp` reads that byte.
In this corpus that partitions the slots cleanly: **4bpp** = slots 10-20 and 23-28 (the value/
text fonts 11/13/15/17 and all the icon fonts), **1bpp** = slots 0-9, 21, 22. NB: `docs/GUICODE.md`
Finding 22's prose lists slot 9 among the 4bpp fonts; the byte says otherwise and the bytes win --
slot 9 (`font+8 == 1`) decodes cleanly only at 1bpp (at 4bpp it over-reads its record 2x). bpp is
*not* inferred from byte-accounting (fonts 11/13 carry trailing pad bytes, which would mislead);
it is read from the descriptor byte the blitter itself tests.

Table locations are *found*, not hardcoded: the three corpus builds put them at different
GuiData offsets, so `find_glyph_table` scans for the pointer run instead.
"""
from __future__ import annotations

import bisect
import struct
from dataclasses import dataclass

from . import container, memo

# A glyph table entry is an absolute pointer into the QSPI window GuiData is mapped at.
QSPI_BASE = 0x90000000
QSPI_LIMIT = 0x90100000

# Font descriptors are 20 bytes and sit immediately below the glyph table, ascending
# addresses running *backwards* through the slot numbering (slot 0 is the last descriptor,
# the one adjacent to the table). `docs/GUICODE.md` Finding 3.
FONT_DESCRIPTOR_SIZE = 20

# Glyph-record header byte offsets that are understood (`FUN_002510C0` reads +0xa..+0xd, then
# the RLE bitstream at +0xe). The pixel data begins a fixed `0xe + ceil(height/8)` in; there is
# no per-glyph start heuristic any more (see the module docstring).
OFF_X, OFF_WIDTH, OFF_Y, OFF_HEIGHT = 0x0A, 0x0B, 0x0C, 0x0D
OFF_RLE = 0x0E

# Font-descriptor byte the blitter tests for anti-aliasing: `*(font + 8) == 4` selects the 4bpp
# (two-nibbles-per-byte) pixel path, anything else the 1bpp path. `proven-from-code`
# (FUN_002510C0). `font_bpp` reads it.
OFF_BPP = 0x08


class GlyphError(ValueError):
    """A font or glyph could not be decoded from this image."""


def _guidata(data: bytes) -> tuple[int, int]:
    for section in container.parse_container(data).sections:
        if "GuiData" in section.name:
            return section.offset, section.length
    raise GlyphError("no GuiData section in this image")


def find_glyph_table(data: bytes, min_run: int = 200) -> tuple[int, int]:
    """Locate the glyph pointer table: `(file offset, entry count)`.

    The table is the longest run of consecutive words that are all in-range QSPI pointers.
    Scanning beats a hardcoded offset because the three builds disagree -- 13.6 puts it at
    GuiData `+0x7503C`, 14.7 at `+0x623E4`.

    Memoised on the CONTENT of `data` (`memo.content_cached`), never on `id(data)`: an earlier
    id-keyed cache survived GC id-reuse but NOT in-place mutation of the *same* object --
    `data[i] = ...` on a bytearray leaves `id(data)` unchanged, so it kept returning the stale
    table. A content key cannot go stale: a mutated buffer is a different key. Before this
    cache, a test decoding 968 glyphs one-off rescanned GuiData 1,958 times (52 s).
    `resolve_glyph_table` remains the explicit, threaded-through form for a render pass.
    """
    return _find_glyph_table(data, min_run)


@memo.content_cached(copy=None)
def _find_glyph_table(data: bytes, min_run: int) -> tuple[int, int]:
    start, length = _guidata(data)
    best: tuple[int, int] | None = None
    run_start = 0
    run = 0
    # Bulk-unpack the words the per-word loop used to read one at a time: the positions
    # `start, start+4, ..., < start + length - 4`, exactly as before.
    n = max(0, (length - 4 + 3) // 4)
    for i, word in enumerate(struct.unpack_from(f"<{n}I", data, start)):
        if QSPI_BASE <= word < QSPI_LIMIT:
            if run == 0:
                run_start = start + 4 * i
            run += 1
            continue
        if run >= min_run and (best is None or run > best[1]):
            best = (run_start, run)
        run = 0
    if run >= min_run and (best is None or run > best[1]):
        best = (run_start, run)
    if best is None:
        raise GlyphError(f"no run of >= {min_run} QSPI pointers found in GuiData")
    return best


@dataclass(frozen=True)
class GlyphTable:
    """A resolved glyph pointer table: the scan result of `find_glyph_table` plus the sorted
    address list `glyph_extent` needs to bound each glyph's byte extent.

    Built **once per render pass** by `resolve_glyph_table` and threaded explicitly through
    `fonts`/`font_for_slot`/`glyph_extent`/`decode_glyph`/`decode_font` (all accept an optional
    `table=`) -- this is what replaces the old id(data)-keyed cache. It is explicitly scoped:
    a caller holding a `GlyphTable` is asserting "I'm not going to mutate `data` while I still
    have this," which is a much smaller, visible promise than a hidden cross-call cache making
    the same assumption silently. Passing a `GlyphTable` resolved against one buffer to calls
    against a *different* buffer is a caller bug, same as passing any other mismatched pair of
    arguments -- there is no id-based detection here, on purpose.
    """
    offset: int
    entries: int
    addrs: tuple[int, ...]


def resolve_glyph_table(data: bytes, min_run: int = 200) -> GlyphTable:
    """Resolve the glyph table + its sorted address list once, for a caller to reuse across
    many `glyph_extent`/`decode_glyph`/`font_for_slot` calls within a single render pass.

    Doing this once instead of per character is the actual fix for the redundant-rescan cost
    (`render.panel_text_images` decodes every character of every label): a full GuiData scan
    plus an O(n log n) address sort, done once instead of once per glyph.
    """
    return _resolve_glyph_table(data, min_run)


@memo.content_cached(copy=None)
def _resolve_glyph_table(data: bytes, min_run: int) -> GlyphTable:
    """`resolve_glyph_table`, memoised on content (`GlyphTable` is frozen, so it is shared)."""
    offset, entries = find_glyph_table(data, min_run)
    addrs = tuple(sorted({struct.unpack_from("<I", data, offset + i * 4)[0]
                          for i in range(entries)}))
    return GlyphTable(offset, entries, addrs)


@dataclass(frozen=True)
class Font:
    """One 20-byte font descriptor."""
    slot: int
    offset: int
    first: int
    last: int
    base: int
    fallback: int

    @property
    def count(self) -> int:
        return self.last - self.first + 1

    def covers(self, char: int) -> bool:
        return self.first <= char <= self.last

    def glyph_index(self, char: int) -> int:
        """The glyph table index this font draws `char` with -- fallback if out of range.

        This mirrors `FUN_00251AA0` exactly (`docs/GUICODE.md` Finding 3): a character
        outside `[first, last]` does not fail, it renders the fallback glyph. That is why
        putting `'['` on a button whose font stops at `0x47` drew a filled block rather
        than nothing.
        """
        if not self.covers(char):
            return self.fallback
        return self.base + char - self.first


def fonts(data: bytes, limit: int = 34, table: GlyphTable | None = None) -> list[Font]:
    """Font descriptors, slot 0 first (the descriptor adjacent to the glyph table).

    A descriptor is accepted only if it is self-consistent -- `first < last` and both
    indices inside the table. The scan walks down until that fails, so the returned count
    is whatever the image actually carries rather than an assumed bound.

    `table`, if given (from `resolve_glyph_table`), skips the table scan -- pass one when
    calling this many times in one render pass over the same, unmutated `data`.
    """
    if table is None:
        table = resolve_glyph_table(data)      # memoised on content: no rescan per call
    return _fonts(data, limit, table)


@memo.content_cached
def _fonts(data: bytes, limit: int, table: GlyphTable) -> list[Font]:
    table_off, entries = table.offset, table.entries
    out: list[Font] = []
    for slot in range(limit):
        off = table_off - (slot + 1) * FONT_DESCRIPTOR_SIZE
        if off < 0:
            break
        first, last = data[off], data[off + 1]
        base, fallback = struct.unpack_from("<HH", data, off + 2)
        if not (first < last and base < entries and fallback < entries):
            break
        out.append(Font(slot, off, first, last, base, fallback))
    return out


def font_for_slot(data: bytes, slot: int, table: GlyphTable | None = None) -> Font:
    """The font a widget's selector byte names.

    A text or icon record is preceded by `01 <slot> ff` (`docs/MCF-FORMAT.md` §6.2). The
    arithmetic is confirmed on hardware twice: 13.6's Climate button carries `0x18` on its
    icon and `0x0f` on its caption, and changing each produced exactly the predicted glyph
    and text size on the panel.

    `table`, if given, is passed through to `fonts` to skip the table scan.
    """
    for font in fonts(data, table=table):
        if font.slot == slot:
            return font
    raise GlyphError(f"no font descriptor for slot {slot}")


def glyph_extent(data: bytes, index: int, table: GlyphTable | None = None) -> tuple[int, int]:
    """`(file offset, byte extent)` of one glyph's record.

    The extent is bounded by the next glyph in *address* order, not index order -- glyphs
    are not stored in index sequence, and several indices share one pointer (the
    placeholder block, which is how an out-of-range character renders).

    `table`, if given (from `resolve_glyph_table`), supplies the table location and the
    sorted address list directly -- skipping both the table scan and the O(n log n) address
    sort, which otherwise get redone on every call (every character of every label). Pass one
    when decoding many glyphs from the same, unmutated `data` in one render pass; leave it
    `None` for a one-off lookup (this rebuilds it fresh, same cost/behaviour as before this
    parameter existed -- there is no cross-call caching here, see `find_glyph_table`).
    """
    gd_start, _ = _guidata(data)
    if table is None:
        table = resolve_glyph_table(data)      # memoised on content: no rescan/re-sort per call
    table_off, entries, addrs = table.offset, table.entries, table.addrs
    if not 0 <= index < entries:
        raise GlyphError(f"glyph {index} outside the {entries}-entry table")
    word = struct.unpack_from("<I", data, table_off + index * 4)[0]
    offset = word - QSPI_BASE + gd_start
    position = bisect.bisect_left(addrs, word)
    if position + 1 < len(addrs):
        extent = addrs[position + 1] - word
    else:
        extent = 2048
    return offset, extent


@dataclass(frozen=True)
class Glyph:
    """A decoded glyph bitmap: `rows[y][x]` holds a 0..15 coverage value."""
    index: int
    offset: int
    x: int
    y: int
    width: int
    start: int
    rows: tuple[tuple[int, ...], ...]

    @property
    def height(self) -> int:
        return len(self.rows)

    def ink(self) -> int:
        return sum(sum(row) for row in self.rows)

    def asymmetry(self) -> float:
        """Mean left/right mismatch. Near zero for a mirror-symmetric glyph decoded right.

        This is the measurement that settled the format: it is only meaningful when the
        row is exactly `width` wide, because rendering the padding half-byte as a real
        column moves the mirror axis half a pixel and swamps the signal.
        """
        total = count = 0
        for row in self.rows:
            for x in range(self.width // 2):
                total += abs(row[x] - row[self.width - 1 - x])
                count += 1
        return total / count if count else 0.0

    def to_ascii(self, shades: str = " .:-=+*#%@") -> str:
        scale = len(shades) - 1
        return "\n".join(
            "".join(shades[value * scale // 15] for value in row) for row in self.rows)


def font_bpp(data: bytes, font: Font) -> int:
    """4 or 1, the bits-per-pixel of `font`'s glyph bitmaps.

    `FUN_002510C0` selects the 4bpp (two-nibbles-per-byte, anti-aliased) pixel path on
    `*(font + 8) == 4` and the 1bpp path otherwise -- so this is `proven-from-code`, read from
    the one descriptor byte the blitter itself tests (`OFF_BPP`). See the module docstring for
    which slots land where and why byte-accounting is *not* used to decide.
    """
    return 4 if data[font.offset + OFF_BPP] == 4 else 1


def _font_for_index(data: bytes, index: int, table: GlyphTable | None = None) -> Font | None:
    """The font whose range owns glyph `index` (its `[base, fallback]`, inclusive).

    The descriptors partition the glyph table (tested), so at most one font owns any index.
    Used by `decode_glyph` to find the bpp of a glyph resolved only by index.
    """
    for font in fonts(data, table=table):
        if font.base <= index <= font.fallback:
            return font
    return None


def _read_pixel_row(data: bytes, base: int, row_bytes: int, width: int,
                    bpp: int) -> tuple[int, ...]:
    """One stored pixel row, `width` values on the 0..15 coverage scale.

    4bpp: two nibbles per byte, low nibble = left pixel. 1bpp: MSB-first, a set bit is stored
    as full 0xF coverage so it sits on the same scale as the 4bpp path (`ink`/`asymmetry`/
    `to_ascii` all read 0..15). Mirrors the two pixel loops of `FUN_002510C0`.
    """
    row = []
    for k in range(width):
        if bpp == 4:
            byte = data[base + (k >> 1)]
            row.append(byte & 0x0F if (k & 1) == 0 else byte >> 4)
        else:
            byte = data[base + (k >> 3)]
            row.append(0x0F if (byte >> (7 - (k & 7))) & 1 else 0)
    return tuple(row)


def decode_glyph(data: bytes, index: int, start: int | None = None,
                 table: GlyphTable | None = None, bpp: int | None = None) -> Glyph:
    """Decode one glyph, a faithful transcription of the blitter `FUN_002510C0`.

    The record is the header (`+0xa` x, `+0xb` width, `+0xc` y, `+0xd` height), then a per-row
    RLE bitstream of `ceil(height/8)` bytes at `+0xe`, then the pixel rows. Output rows are
    produced by the vertical-repeat RLE the module docstring describes: exactly `header[0xd]`
    rows, each `width` pixels wide. `bpp` (4 or 1) defaults to the owning font's descriptor byte
    (`font_bpp`); pass it to skip the font lookup, or to force a decode. `start` (the pixel-data
    offset, default `0xe + ceil(height/8)`) is overridable but no longer a heuristic; the value
    used is recorded in `Glyph.start`. `table`, if given, is threaded through to skip re-scans.
    """
    offset, extent = glyph_extent(data, index, table=table)
    width = data[offset + OFF_WIDTH]
    height = data[offset + OFF_HEIGHT]
    if width < 1:
        raise GlyphError(f"glyph {index} declares width {width}")
    if height < 1:
        raise GlyphError(f"glyph {index} declares height {height}")

    if bpp is None:
        owner = _font_for_index(data, index, table=table)
        # No font found owning this index: not observed in this corpus (the descriptors
        # partition the table, see `_font_for_index`), but a caller can still reach this via
        # an arbitrary index, so default to 4bpp rather than raise -- 4bpp is the more common
        # case in this corpus (value/text/icon fonts) and the safer guess of the two.
        bpp = font_bpp(data, owner) if owner is not None else 4

    rle_bytes = (height + 7) >> 3            # ceil(height/8); FUN_002510C0 forces >= 1
    if rle_bytes == 0:
        rle_bytes = 1   # unreachable: height < 1 already raised above, so height+7>>3 >= 1
    chosen_start = start if start is not None else OFF_RLE + rle_bytes
    row_bytes = (width + 1) >> 1 if bpp == 4 else (width + 7) >> 3

    def rle_bit(q: int) -> int:
        # Output row r reads stream bit q = r + 1 (bit 0 of byte +0xe is skipped): the blitter's
        # `uVar15 = glyph[0xe] >> 1` initial state plus its `uVar13 & 7 == 7` byte refill.
        return (data[offset + OFF_RLE + (q >> 3)] >> (q & 7)) & 1

    rows: list[tuple[int, ...]] = []
    pixel = offset + chosen_start
    r = 0
    while r < height:
        run = 0
        while True:                          # count a run of set bits (clear bit or last row ends it)
            run += 1
            cur = rle_bit(r + 1)
            r += 1
            if cur == 0 or r >= height:
                break
        prow = _read_pixel_row(data, pixel, row_bytes, width, bpp)
        pixel += row_bytes
        rows.extend([prow] * run)            # the DMA `n | 0x10000` vertical repeat

    return Glyph(index=index, offset=offset, x=data[offset + OFF_X],
                 y=data[offset + OFF_Y], width=width, start=chosen_start, rows=tuple(rows))


def decode_font(data: bytes, slot: int) -> dict[str, Glyph]:
    """Every character a font draws, keyed by the character that selects it.

    Characters whose glyph is the shared placeholder are omitted: they all resolve to one
    pointer with a few bytes behind it, and drawing them yields the filled block the panel
    shows for an out-of-range character. Resolves the glyph table once (`resolve_glyph_table`)
    and reuses it for the whole font, rather than rescanning per character.
    """
    table = resolve_glyph_table(data)
    font = font_for_slot(data, slot, table=table)
    bpp = font_bpp(data, font)
    out: dict[str, Glyph] = {}
    for char in range(font.first, font.last + 1):
        index = font.glyph_index(char)
        _, extent = glyph_extent(data, index, table=table)
        if extent < 64:                       # the placeholder: no bitmap to speak of
            continue
        try:
            out[chr(char)] = decode_glyph(data, index, table=table, bpp=bpp)
        except GlyphError:
            continue
    return out
