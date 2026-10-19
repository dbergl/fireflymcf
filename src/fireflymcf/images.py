"""Decoder for the 8 GuiData artwork images (object table 0, `MCF-FORMAT.md` §5.5).

The format is `proven-from-code`: the blitter is `FUN_002513a4` in GuiCode (called by the
8-image accessor `FUN_00255F80`). It reads, per output row:

    <u16 row_byte_length>            # length INCLUDING this 2-byte prefix; 0 => REPEAT PREV ROW
    then runs until the row is consumed:
      <ctrl>                         # ctrl = (literal << 7) | count   (count = ctrl & 0x7f)
      ctrl top bit 0 -> FILL:    one RGB888 pixel, repeated `count` times
      ctrl top bit 1 -> LITERAL: `count` RGB888 pixels follow verbatim

There is no per-image header; the byte stream is the rows back to back, and every non-empty row's
runs fill the full width. **A row length of 0 repeats the previous row** (a vertical run): the
blitter leaves its row-data pointer unchanged for a zero-length row, so the same run stream draws
again. QR / data-matrix artwork is full of these (square modules span identical rows); smooth
logos have almost none -- so mis-reading it as a blank row corrupted *only* the QR images. The
blitter writes RGB with no alpha, so images are **fully opaque RGB888**.

Width and height come from the two u16 dimension arrays below the pointer table (`dimensions`).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from . import guidata

TABLE_INDEX = 0                 # object-pointer table 0 = the image table
# Two u16[N] dimension arrays sit immediately below the N-entry image pointer table, in order
# height, width, table. So width array base = table - N*2, height array base = table - N*4.
# Anchoring to the table and N (not a fixed 0x10070/0x10060) keeps this correct across builds --
# 14.7 has 9 images and relocates the block, so the fixed 13.6 offsets do not carry.


@dataclass
class Image:
    index: int
    width: int
    height: int
    pixels: bytes               # width*height*3 RGB888, row-major top-to-bottom (fully opaque)


def _gs(data: bytes) -> int:
    return guidata.guidata_extent(data)[0]


def image_count(data: bytes) -> int:
    return len(guidata.object_tables(data)[TABLE_INDEX])


def dimensions(data: bytes, index: int) -> tuple[int, int]:
    """(width, height) for image `index`, from the two u16 dimension arrays below the table.

    Layout: `[height: L u16][width: L u16][pointer table]`, width ending exactly at the table.
    `L == image count` on 11.23 / 13.6, but **14.7 (SW20) reserves a trailing zero slot** so
    `L == count + 1`. Detect it from the u16 immediately before the table (0 => padded), which
    keeps this byte-exact on all three builds."""
    gs = _gs(data)
    n = image_count(data)
    tbl = gs + guidata.object_table_offset(data, TABLE_INDEX)
    padded = struct.unpack_from("<H", data, tbl - 2)[0] == 0
    length = n + 1 if padded else n
    w = struct.unpack_from("<H", data, tbl - length * 2 + index * 2)[0]
    h = struct.unpack_from("<H", data, tbl - length * 4 + index * 2)[0]
    return w, h


def decode_image(data: bytes, index: int) -> Image:
    """Decode image `index` to opaque RGB888.

    Each row carries a `u16` byte length; **length 0 repeats the previous row** (a vertical run,
    per the blitter `FUN_002513a4`). A non-zero row's runs must fill exactly `width` pixels. Raises
    ValueError on any drift -- a non-repeat row that does not fill the width, or a leading repeat
    with no previous row."""
    gs = _gs(data)
    table = guidata.object_tables(data)[TABLE_INDEX]
    if not (0 <= index < len(table)):
        raise ValueError(f"image index {index} out of range 0..{len(table) - 1}")
    width, height = dimensions(data, index)
    p = gs + table[index]
    out = bytearray()
    prev = None                                  # the previous decoded row (width*3 bytes)
    for row in range(height):
        row_len = struct.unpack_from("<H", data, p)[0]
        if row_len == 0:                         # repeat the previous row
            if prev is None:
                raise ValueError(f"image {index} row {row}: repeat with no previous row")
            out += prev
            p += 2
            continue
        q = p + 2
        row_end = p + row_len
        buf = bytearray()
        painted = 0
        while q < row_end:
            ctrl = data[q]; q += 1
            count = ctrl & 0x7F
            if ctrl & 0x80:                      # literal: `count` distinct RGB pixels
                buf += data[q:q + count * 3]; q += count * 3
            else:                                # fill: one RGB repeated `count` times
                buf += data[q:q + 3] * count; q += 3
            painted += count
        if painted != width:
            raise ValueError(f"image {index} row {row}: filled {painted} px, expected {width}")
        out += buf
        prev = buf
        p = row_end
    if len(out) != width * height * 3:
        raise ValueError(f"image {index}: decoded {len(out)} bytes, expected {width*height*3}")
    return Image(index=index, width=width, height=height, pixels=bytes(out))
