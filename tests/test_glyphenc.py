"""Round-trip tests for the glyph encoder.

The encoder's only correctness claim is that `glyphs.decode_glyph` reads back exactly what
`glyphenc.encode_glyph` wrote. That is checkable against the corpus without any oracle: take a
real glyph, re-encode its decoded rows, and decode the result. Anything the encoder gets wrong
about the record layout (field order, the u8 dims, the RLE bitstream length, 4bpp nibble order,
1bpp bit order) shows up as a pixel or dimension mismatch.

1bpp is not a formality here: the first version of the encoder packed 1bpp LSB-first and passed
every 4bpp font, failing only on slot 21.
"""
import pytest

from fireflymcf import glyphenc, glyphs

def _decode_record(rec: bytes, bpp: int):
    """Read a standalone glyph record back, mirroring `glyphs.decode_glyph`.

    This MUST replay the vertical-repeat RLE, not just walk `height` pixel rows. An earlier
    version of this helper read the rows directly and so proved only the pixel packing -- it
    would have passed even if the encoder's identity-RLE claim (an all-zero bitstream means one
    stored row per output row) were wrong.
    """
    width, height = rec[glyphs.OFF_WIDTH], rec[glyphs.OFF_HEIGHT]
    row_bytes = (width + 1) // 2 if bpp == 4 else (width + 7) // 8
    pixel = glyphs.OFF_RLE + ((height + 7) >> 3)

    def rle_bit(q: int) -> int:
        return (rec[glyphs.OFF_RLE + (q >> 3)] >> (q & 7)) & 1

    rows: list[tuple[int, ...]] = []
    r = 0
    while r < height:
        run = 0
        while True:
            run += 1
            cur = rle_bit(r + 1)
            r += 1
            if cur == 0 or r >= height:
                break
        rows.extend([glyphs._read_pixel_row(rec, pixel, row_bytes, width, bpp)] * run)
        pixel += row_bytes
    return width, height, tuple(rows), pixel


def _fonts_with_glyphs(data):
    """Every decodable (slot, bpp, glyph) in a build, capped so the test stays quick."""
    for slot in (21, 23, 24, 25, 27):
        try:
            font = glyphs.font_for_slot(data, slot)
            bpp = glyphs.font_bpp(data, font)
        except Exception:
            continue
        for code in range(font.first, min(font.last, font.first + 8) + 1):
            try:
                yield slot, bpp, glyphs.decode_glyph(data, font.glyph_index(code))
            except Exception:
                continue


def test_round_trip_reproduces_corpus_glyphs(mcf_136):
    """Re-encoding a decoded glyph yields the same dimensions and the same pixels."""
    checked = 0
    for slot, bpp, g in _fonts_with_glyphs(mcf_136):
        rec = glyphenc.encode_glyph(list(g.rows), bpp, x=g.x, y=g.y)
        width, height, rows, consumed = _decode_record(rec, bpp)
        assert (width, height) == (g.width, g.height), f"slot {slot}: dims differ"
        assert rows == g.rows, f"slot {slot}: pixels differ"
        assert consumed == len(rec), f"slot {slot}: decoder consumed {consumed} of {len(rec)}"
        checked += 1
    assert checked >= 16, f"only {checked} glyphs exercised -- the corpus walk broke"


def test_round_trip_covers_both_depths(mcf_136):
    """The walk must actually reach a 1bpp font and a 4bpp font, or the test proves half of it."""
    depths = {bpp for _, bpp, _ in _fonts_with_glyphs(mcf_136)}
    assert depths == {1, 4}, f"expected both pixel depths, got {depths}"


def test_encoder_is_sensitive_to_pixels(mcf_136):
    """A one-pixel change must survive the round trip -- otherwise the test above is vacuous."""
    font = glyphs.font_for_slot(mcf_136, 24)
    g = glyphs.decode_glyph(mcf_136, font.glyph_index(font.first))
    rows = [list(r) for r in g.rows]
    rows[g.height // 2][g.width // 2] ^= 0xF
    rec = glyphenc.encode_glyph(rows, 4, x=g.x, y=g.y)
    _, _, back, _ = _decode_record(rec, 4)
    assert back != g.rows
    assert back == tuple(tuple(r) for r in rows)


@pytest.mark.parametrize("width,height", [(0, 8), (8, 0), (256, 8), (8, 256)])
def test_rejects_out_of_range_dimensions(width, height):
    """Width and height are u8 fields; the encoder refuses rather than silently truncating."""
    rows = [tuple([0] * width) for _ in range(height)]
    with pytest.raises(ValueError):
        glyphenc.encode_glyph(rows, 4)


def test_rejects_ragged_rows():
    with pytest.raises(ValueError):
        glyphenc.encode_glyph([(0, 0, 0), (0, 0)], 4)


# ---- compression, the flag bit, and export ----------------------------------------------------
#
# The encoder used to emit the RLE's IDENTITY encoding -- an all-zero bitstream, one stored row
# per output row. That is legal (the blitter reads it back correctly) but it is not what any
# shipped glyph does: a greedy-maximal run encoding reproduces the shipped bitstream on 2,875 of
# the corpus's 2,884 glyphs, and the nine that differ do so only in bit 0, which is not part of
# the RLE at all (see below). So compression is not a new invention here -- identity was the
# unusual encoding, and this suite pins the shipped one.


def _stored_rows(rec: bytes) -> int:
    """How many pixel rows the record actually carries (the point of compressing)."""
    height = rec[glyphs.OFF_HEIGHT]
    width = rec[glyphs.OFF_WIDTH]
    row_bytes = (width + 1) // 2
    return (len(rec) - glyphs.OFF_RLE - ((height + 7) >> 3)) // row_bytes


def test_repeated_rows_are_stored_once(mcf_136):
    """A vertically uniform glyph stores one row per RUN, not one per output row."""
    rows = [tuple([0xF] * 8)] * 6 + [tuple([0] * 8)] * 6
    rec = glyphenc.encode_glyph(rows, 4)
    assert _stored_rows(rec) == 2, "expected 2 stored rows for 2 runs"
    _, _, back, consumed = _decode_record(rec, 4)
    assert back == tuple(rows), "compressed record does not decode back to the same pixels"
    assert consumed == len(rec)


def test_compression_reproduces_the_shipped_bitstream(mcf_136, mcf_1123, mcf_147):
    """The encoder's RLE must be the one the corpus ships, byte for byte.

    Bit 0 of byte +0x0e is excluded: `decode_glyph` skips it (output row r reads bit r+1) and
    `metrics._GHdr` reads the same byte as the measurer's flag, so it is not RLE payload. It is
    checked separately by the flag test below.
    """
    checked = mismatch = 0
    for data in (mcf_136, mcf_1123, mcf_147):
        table = glyphs.resolve_glyph_table(data)
        for slot in (9, 13, 17, 23, 24, 27):
            try:
                font = glyphs.font_for_slot(data, slot, table=table)
                bpp = glyphs.font_bpp(data, font)
            except Exception:
                continue
            for code in range(font.first, font.last + 1):
                index = font.glyph_index(code)
                try:
                    off, extent = glyphs.glyph_extent(data, index, table=table)
                    if extent < 64:
                        continue
                    g = glyphs.decode_glyph(data, index, table=table, bpp=bpp)
                except Exception:
                    continue
                rec = glyphenc.encode_glyph(list(g.rows), bpp, x=g.x, y=g.y)
                n = (g.height + 7) >> 3
                ours = bytearray(rec[glyphs.OFF_RLE:glyphs.OFF_RLE + n])
                theirs = bytearray(data[off + glyphs.OFF_RLE:off + glyphs.OFF_RLE + n])
                ours[0] &= 0xFE
                theirs[0] &= 0xFE
                checked += 1
                mismatch += ours != theirs
    assert checked >= 200, f"only {checked} glyphs exercised"
    assert mismatch == 0, f"{mismatch} of {checked} glyphs re-encode to a different bitstream"


def test_flag_bit_is_carried_and_is_not_rle(mcf_136):
    """Byte +0x0e bit 0 is the MEASURER's flag (`metrics._GHdr.flag`), not a spare bit.

    The blitter skips it, so dropping it is invisible in a pixel round-trip -- and it changes
    what `metrics.text_width` computes for a string's endpoints. The encoder must therefore
    carry it, and must not let it disturb the run encoding.
    """
    rows = [tuple([0xF] * 8)] * 4 + [tuple([0] * 8)] * 4
    plain = glyphenc.encode_glyph(rows, 4)
    flagged = glyphenc.encode_glyph(rows, 4, flag=1)
    assert plain[glyphs.OFF_RLE] & 1 == 0
    assert flagged[glyphs.OFF_RLE] & 1 == 1
    assert flagged[glyphs.OFF_RLE] & 0xFE == plain[glyphs.OFF_RLE] & 0xFE, "flag disturbed the RLE"
    for rec in (plain, flagged):
        assert _decode_record(rec, 4)[2] == tuple(rows), "the flag changed the pixels"


def test_explicit_profiles_are_written_verbatim():
    """`_profiles` is an approximation (it reproduces 6 of 1115 shipped profiles), so an
    exported glyph carries its real ones and the encoder must not recompute them."""
    rows = [tuple([0xF] * 8)] * 4
    want = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    rec = glyphenc.encode_glyph(rows, 4, profiles=want)
    assert list(rec[:10]) == want
    with pytest.raises(ValueError):
        glyphenc.encode_glyph(rows, 4, profiles=[1, 2, 3])


def test_export_then_encode_reproduces_the_record_byte_for_byte(mcf_136, mcf_1123, mcf_147):
    """THE round-trip oracle: any shipped glyph exported to JSON-able data and re-encoded is
    the same bytes. This is what makes a project file able to carry a glyph without a
    rasteriser, and without silently altering the kerning data it does not know how to derive.
    """
    checked = 0
    for data in (mcf_136, mcf_1123, mcf_147):
        table = glyphs.resolve_glyph_table(data)
        for slot in (9, 13, 17, 21, 23, 24, 27):
            try:
                font = glyphs.font_for_slot(data, slot, table=table)
                bpp = glyphs.font_bpp(data, font)
            except Exception:
                continue
            for code in range(font.first, font.last + 1):
                index = font.glyph_index(code)
                try:
                    off, extent = glyphs.glyph_extent(data, index, table=table)
                    if extent < 64:
                        continue
                except Exception:
                    continue
                try:
                    spec = glyphenc.export_glyph(data, slot, chr(code), table=table)
                except Exception:
                    continue
                rec = glyphenc.encode_glyph(
                    [tuple(int(c, 16) for c in row) for row in spec["rows"]], bpp,
                    x=spec["x"], y=spec["y"], profiles=spec["profiles"], flag=spec["flag"])
                assert rec == data[off:off + len(rec)], (
                    f"slot {slot} {chr(code)!r}: re-encoded record differs from the shipped one")
                checked += 1
    assert checked >= 200, f"only {checked} glyphs exercised -- the corpus walk broke"


def test_export_is_json_serialisable(mcf_136):
    """The whole point is that a project file can carry it, so it must survive JSON."""
    import json

    spec = glyphenc.export_glyph(mcf_136, 27, "A")
    assert json.loads(json.dumps(spec)) == spec
    assert all(isinstance(r, str) for r in spec["rows"])
    assert len({len(r) for r in spec["rows"]}) == 1


def test_guidata_section_ends_with_its_checksum(mcf_136, mcf_1123, mcf_147):
    """The GuiData section's last four bytes are its stored checksum, not payload.

    This is the premise behind where an appended glyph record may go. `guidata_extent` returns
    the whole section, checksum included, so inserting at `extent_end` leaves the record's tail
    under the checksum word and `reseal` writes through it -- silently corrupting the record's
    final bytes. `build-satellite-glyph.py` does exactly that: its 700-byte record takes the
    checksum at +696, clobbering the end of the satellite's bottom pixel row. None of the five
    structural gates notices, because they check structure and not glyph pixels.
    """
    from fireflymcf import guidata, patch

    for name, data in (("13.6", mcf_136), ("11.23", mcf_1123), ("14.7", mcf_147)):
        _, end = guidata.guidata_extent(data)
        field = [f for f in patch.checksum_fields(data) if f.label.endswith("GuiData")]
        assert len(field) == 1, f"{name}: expected exactly one GuiData section checksum"
        assert field[0].position == end - 4, (
            f"{name}: GuiData checksum at {field[0].position:#x}, "
            f"section ends {end:#x} -- the append offset rule assumes end-4")
