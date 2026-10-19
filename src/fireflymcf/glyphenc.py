"""Glyph ENCODER and EXPORTER -- the inverse of glyphs.decode_glyph.

Record layout (from decode_glyph / FUN_002510C0):
    +0x00..0x04  left ink profile   (used by the measurer FUN_002518F8 for kerning)
    +0x05..0x09  right ink profile
    +0x0a x   +0x0b width   +0x0c y   +0x0d height     (all u8)
    +0x0e        RLE bitstream, ceil(height/8) bytes -- and its bit 0 is NOT RLE (below)
    then         one pixel row per RUN, ceil(width/2) bytes (4bpp) or ceil(width/8) (1bpp)

**The RLE is written the way the corpus writes it.** `decode_glyph` reads stream bit `r+1` per
output row and ends a run on a CLEAR bit, so an all-zero bitstream is a legal *identity*
encoding -- one stored row per output row. This module used to emit that, and it is the one
thing in the corpus that does: a greedy-maximal run encoding reproduces the shipped bitstream on
2,875 of the 2,884 glyphs in the three builds, the other nine differing only in bit 0. Identity
was the unusual encoding, not compression.

**Byte +0x0e bit 0 belongs to the MEASURER, not the RLE.** The blitter skips it (output row `r`
reads bit `r+1`), and `metrics._GHdr` reads the same byte as the flag that decides which bearing
`text_width` uses at a string's endpoints. Dropping it is therefore invisible to a pixel
round-trip and still changes measured text width, so `encode_glyph` carries it explicitly.

**The ink profiles cannot be derived from the bitmap.** `_profiles` below is an approximation --
it reproduces 6 of 1115 shipped profiles on 13.6 -- and the real rule is not recoverable from
these files: no partition of a glyph's rows into bands explains its profile (a DP over every
contiguous partition admits only 102 of 2884), no fixed row window fits a whole font cohort, and
630 profile values fall outside the glyph's own ink bounds entirely. The values are computed by
Firefly's font tool from the source outline, which no image here contains. So `encode_glyph`
takes `profiles` and writes them verbatim when a caller has real ones -- which `export_glyph`
always does -- and falls back to the approximation only when authoring a glyph that has none.
See `docs/GUICODE.md` Finding 46.
"""
from . import glyphs


def _profiles(rows, width, height):
    """The 5-band left/right ink profiles, matching the shape observed in real records
    (a full-bleed glyph reads left=0.., right=width-2..)."""
    left, right = [], []
    for b in range(5):
        lo, hi = (b * height) // 5, ((b + 1) * height) // 5
        band = [r for r in rows[lo:hi] if any(r)]
        if not band:
            left.append(0); right.append(max(0, width - 2)); continue
        l = min(next(i for i, p in enumerate(r) if p) for r in band)
        rgt = max(width - 1 - next(i for i, p in enumerate(reversed(r)) if p) for r in band)
        left.append(min(255, l)); right.append(min(255, max(0, rgt - 1)))
    return left, right


def _runs(rows):
    """Greedy-maximal vertical runs: the stored rows, and the bitstream that repeats them.

    Matches `decode_glyph` exactly -- output row `r` reads stream bit `r+1`, a SET bit continues
    the current run and a CLEAR bit ends it -- so a run of `n` identical rows sets the `n-1` bits
    after the run's first, then leaves one clear. Bit 0 is never written here; it is the
    measurer's flag and `encode_glyph` owns it.
    """
    height = len(rows)
    stored, lengths = [], []
    r = 0
    while r < height:
        n = 1
        while r + n < height and rows[r + n] == rows[r]:
            n += 1
        stored.append(rows[r])
        lengths.append(n)
        r += n
    bits = bytearray((height + 7) >> 3)
    q = 1
    for n in lengths:
        for _ in range(n - 1):
            bits[q >> 3] |= 1 << (q & 7)
            q += 1
        q += 1                                          # the clear bit that ends the run
    return stored, bits


def encode_glyph(rows, bpp: int, x: int = 0, y: int = 0, profiles=None, flag: int = 0) -> bytes:
    """Encode `rows` (a sequence of per-pixel intensity tuples) into a glyph record.

    `profiles` is the 10 ink-profile bytes (5 left, then 5 right) when the caller has the real
    ones -- from `export_glyph` -- and None to fall back to `_profiles`'s approximation. `flag`
    is byte +0x0e bit 0, the measurer's flag. See the module docstring for why both are
    parameters rather than derived.
    """
    height = len(rows)
    width = len(rows[0]) if height else 0
    if not (1 <= width <= 255 and 1 <= height <= 255):
        raise ValueError("glyph must be 1..255 in each axis (both are u8 fields), got %dx%d"
                         % (width, height))
    if any(len(r) != width for r in rows):
        raise ValueError("ragged rows")
    rows = [tuple(r) for r in rows]
    if profiles is None:
        left, right = _profiles(rows, width, height)
    else:
        profiles = list(profiles)
        if len(profiles) != 10 or any(not (0 <= v <= 255) for v in profiles):
            raise ValueError("profiles must be 10 bytes (5 left, 5 right), got %r" % (profiles,))
        left, right = profiles[:5], profiles[5:]
    stored, bits = _runs(rows)
    bits[0] |= flag & 1
    out = bytearray(left + right)
    out += bytes((x & 0xFF, width, y & 0xFF, height))
    out += bits
    for row in stored:
        if bpp == 4:
            packed = bytearray((width + 1) // 2)
            for i, p in enumerate(row):
                v = max(0, min(15, int(p)))
                if i % 2 == 0:
                    packed[i // 2] |= v
                else:
                    packed[i // 2] |= v << 4
        else:
            packed = bytearray((width + 7) // 8)
            for i, p in enumerate(row):
                if p:
                    packed[i // 8] |= 1 << (7 - (i % 8))   # MSB-first, per _read_pixel_row
        out += packed
    return bytes(out)


def export_glyph(data: bytes, slot: int, char: str, table=None) -> dict:
    """One shipped glyph as plain JSON-able data -- exactly `install_glyph`'s parameters.

    Returns `slot`, `char`, `x`, `y`, `rows` (one hex digit of 4bpp coverage per pixel, one
    string per row), `profiles` (the 10 kerning bytes, verbatim) and `flag` (byte +0x0e bit 0).
    Feeding all of it back to `encode_glyph` reproduces the record byte for byte -- which is what
    lets a project file carry a glyph without a rasteriser, and without inventing the kerning
    data this module cannot derive (see the module docstring).

    The rows are the DECODED ones, so an exported glyph is its pixels, not its encoding: a
    record stored under the vertical-repeat RLE comes back as `height` rows and is re-compressed
    on the way in.
    """
    if len(char) != 1:
        raise ValueError("char must be exactly one character, got %r" % (char,))
    font = glyphs.font_for_slot(data, slot, table=table)
    bpp = glyphs.font_bpp(data, font)
    index = font.glyph_index(ord(char))
    offset, _extent = glyphs.glyph_extent(data, index, table=table)
    g = glyphs.decode_glyph(data, index, table=table, bpp=bpp)
    return {
        "slot": slot,
        "char": char,
        "x": g.x,
        "y": g.y,
        "rows": ["".join("%x" % v for v in row) for row in g.rows],
        "profiles": list(data[offset:offset + 10]),
        "flag": data[offset + glyphs.OFF_RLE] & 1,
    }
