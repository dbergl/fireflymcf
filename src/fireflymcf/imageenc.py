"""Encoder for the GuiData artwork images -- the inverse of `images.decode_image`.

Produces the per-row RLE the panel blitter (`FUN_002513a4`) reads: for each row, a `u16` byte
length then runs of `<(literal<<7)|count>` + RGB888 (fill = one RGB repeated `count` times,
literal = `count` RGB pixels). A row identical to the one above is emitted as a **zero-length
row** (the vertical-repeat the decoder honours). Lets an owner replace a shipped logo/QR with
their own artwork; pair with a same-size patch or the relocator to write it back.

Validated by re-encoding every shipped image and decoding the result to the same pixels
(`tests/test_imageenc.py`); on this corpus it also reproduces the original bytes exactly.
"""
from __future__ import annotations

import struct

from . import guidata, images, patch

_MAX_RUN = 0x7F                 # count is 7 bits; runs longer than 127 split


def encode_row(row: bytes, width: int) -> bytes:
    """Encode one row of `width` RGB888 pixels (len == width*3) to its run stream (no length
    prefix). Fill runs for >=2 identical pixels, literal runs for stretches of distinct pixels --
    the choice the shipped images use (a lone pixel is the same size either way and joins the
    surrounding literal)."""
    if len(row) != width * 3:
        raise ValueError(f"row is {len(row)} bytes, expected {width * 3}")
    px = [row[i * 3:i * 3 + 3] for i in range(width)]
    out = bytearray()
    i = 0
    while i < width:
        j = i
        while j < width and px[j] == px[i]:
            j += 1
        run = j - i
        if run >= 2:                                   # fill run(s)
            while run > 0:
                n = min(run, _MAX_RUN)
                out += bytes([n]) + px[i]
                run -= n
            i = j
        else:                                          # literal run: to the next >=2 repeat
            start = i
            i += 1
            while i < width and not (i + 1 < width and px[i] == px[i + 1]):
                i += 1
            k = start
            while k < i:
                n = min(i - k, _MAX_RUN)
                out += bytes([0x80 | n]) + b"".join(px[k:k + n])
                k += n
    return bytes(out)


def encode_image(width: int, height: int, pixels: bytes) -> bytes:
    """Encode a `width`x`height` opaque RGB888 image (len(pixels) == width*height*3) to the RLE
    byte stream the image table stores. A row equal to the previous becomes a zero-length row."""
    if len(pixels) != width * height * 3:
        raise ValueError(f"pixels is {len(pixels)} bytes, expected {width * height * 3}")
    stride = width * 3
    out = bytearray()
    prev = None
    for r in range(height):
        row = pixels[r * stride:(r + 1) * stride]
        if row == prev:
            out += b"\x00\x00"                          # repeat previous row
            continue
        body = encode_row(row, width)
        out += struct.pack("<H", len(body) + 2) + body
        prev = row
    return bytes(out)


def _encoded_length(data: bytes, index: int) -> int:
    """Byte length image `index` occupies in the file -- what its rows consume, so a replacement
    that is no longer is guaranteed to fit in place (images sit at their own table pointer and are
    read for `height` rows, so trailing bytes are never touched)."""
    gs = guidata.guidata_extent(data)[0]
    p = gs + guidata.object_tables(data)[images.TABLE_INDEX][index]
    start = p
    width, height = images.dimensions(data, index)
    for _ in range(height):
        row_len = struct.unpack_from("<H", data, p)[0]
        p += row_len if row_len else 2
    return p - start


def replace_image(data: bytes, index: int, width: int, height: int, pixels: bytes) -> bytes:
    """Return a new MCF with image `index` replaced by `width`x`height` opaque RGB888 `pixels`,
    resealed. The new image is written **in place** (no relocation), so it must encode to no more
    bytes than the original occupies -- true for a same-or-simpler image; a larger one raises and
    would need the relocator. Its dimension-array entries are updated (a same-size u16 edit), so the
    new image may be a different size than the one it replaces as long as it still fits the bytes."""
    enc = encode_image(width, height, pixels)
    avail = _encoded_length(data, index)
    if len(enc) > avail:
        raise ValueError(
            f"encoded image is {len(enc)} bytes but only {avail} are available in place for "
            f"image {index}; a larger image needs the relocator (not yet supported here)")
    gs = guidata.guidata_extent(data)[0]
    t0 = guidata.object_tables(data)[images.TABLE_INDEX]
    start = gs + t0[index]
    buf = bytearray(data)
    buf[start:start + len(enc)] = enc
    for i in range(start + len(enc), start + avail):      # clear the unused tail, deterministically
        buf[i] = 0
    # update this image's width/height (the two u16 arrays below the pointer table; see images.py)
    n = len(t0)
    tbl = gs + guidata.object_table_offset(data, images.TABLE_INDEX)
    padded = struct.unpack_from("<H", data, tbl - 2)[0] == 0
    L = n + 1 if padded else n
    struct.pack_into("<H", buf, tbl - L * 2 + index * 2, width)
    struct.pack_into("<H", buf, tbl - L * 4 + index * 2, height)
    out, _ = patch.reseal(bytes(buf))
    return out


# --- P6 PPM interchange -------------------------------------------------------------------
# PPM (binary "P6") is the lowest-common-denominator RGB format: every image tool reads and
# writes it, and it needs no library here -- a header of `P6 <w> <h> 255` then raw RGB888. Owners
# export a shipped logo to PPM, edit it, and swap it back.

def write_ppm(width: int, height: int, pixels: bytes) -> bytes:
    """Serialise opaque RGB888 `pixels` as a binary P6 PPM."""
    if len(pixels) != width * height * 3:
        raise ValueError(f"pixels is {len(pixels)} bytes, expected {width * height * 3}")
    return b"P6\n%d %d\n255\n" % (width, height) + pixels


def read_ppm(blob: bytes) -> tuple[int, int, bytes]:
    """Parse a binary P6 PPM to (width, height, RGB888 pixels). Tolerates comments and any
    whitespace in the header, as the format allows; rejects anything but 8-bit P6."""
    if blob[:2] != b"P6":
        raise ValueError("not a binary P6 PPM (magic is not 'P6')")
    fields, i, n = [], 2, len(blob)
    while len(fields) < 3:
        while i < n and blob[i:i + 1].isspace():
            i += 1
        if i < n and blob[i:i + 1] == b"#":                 # comment to end of line
            while i < n and blob[i:i + 1] != b"\n":
                i += 1
            continue
        start = i
        while i < n and not blob[i:i + 1].isspace():
            i += 1
        fields.append(int(blob[start:i]))
    width, height, maxval = fields
    if maxval != 255:
        raise ValueError(f"only 8-bit PPM (maxval 255) is supported, got {maxval}")
    pixels = blob[i + 1:]                                    # exactly one whitespace byte, then data
    want = width * height * 3
    if len(pixels) < want:
        raise ValueError(f"PPM body is {len(pixels)} bytes, expected {want}")
    return width, height, pixels[:want]
