"""GuiData (QSPI image) structures used by the LCD firmware.

Section directory: byte 7 = section count, 8-byte entries from offset 8;
entry[0] is the section tag, u16 at entry+4 its offset.  Section 'A' is the
data-point table that FUN_0021c760 (ev_8a5f3378) uses to turn received RV-C
frames into 16-bit values in the GUI state block (0x20007fc8 + 2*index).
"""
import struct
import sys
from dataclasses import dataclass

EXTRACT = {1: "u8", 2: "u16", 4: "bits0-1", 5: "bits2-3", 6: "bits4-5", 7: "bits6-7"}
TIMEOUT_MS = {0: 2000, 1: 10000, 2: 30000}


@dataclass
class DataPoint:
    index: int
    pgn: int
    kind: str          # how the value is extracted
    byte: int          # data byte holding the value
    timeout_ms: int    # value goes stale ("---") after this long without a frame
    match_byte: int    # data byte compared against match_value (usually the instance)
    match_value: int   # 0xFF = match anything
    match_mask: int    # bit mask applied to the comparison

    def matches(self, data):
        if self.match_value == 0xFF:
            return True
        return not (self.match_mask & (self.match_value ^ data[self.match_byte]))

    def extract(self, data):
        b = data[self.byte]
        if self.kind == "u16":
            return b | data[self.byte + 1] << 8
        if self.kind.startswith("bits"):
            lo = int(self.kind[4])
            return (b >> lo) & 3
        return b


def sections(img):
    out = {}
    for i in range(img[7]):
        e = img[8 + 8 * i: 16 + 8 * i]
        out[chr(e[0]) if 32 <= e[0] < 127 else e[0]] = e
    return out


def data_points(img):
    e = sections(img)["A"]
    count = e[3] + (e[1] & 3) * 0x100
    base = struct.unpack_from("<H", e, 4)[0]
    pts = []
    for i in range(count):
        raw = img[base + 8 * i: base + 8 * i + 8]
        (w,) = struct.unpack_from("<I", raw)
        sel = raw[5] >> 4
        mask = 0
        for bit, pair in ((8, 0xC0), (4, 0x30), (2, 0x0C), (1, 0x03)):
            if sel & bit:
                mask |= pair
        if sel == 0:
            mask = 0xFF
        pts.append(DataPoint(i, w & 0x3FFFF, EXTRACT.get(raw[2] >> 4, f"?{raw[2] >> 4}"),
                             raw[3] & 7, TIMEOUT_MS.get((raw[2] & 0xF) >> 2, 0),
                             raw[5] & 0xF, raw[4], mask))
    return pts


if __name__ == "__main__":
    img = open(sys.argv[1], "rb").read()
    for p in data_points(img):
        match = "any" if p.match_value == 0xFF else f"d[{p.match_byte}]&{p.match_mask:02x}=={p.match_value:02x}"
        print(f"{p.index:3} PGN {p.pgn:05X} {p.kind:8} d[{p.byte}]  {match:16} stale {p.timeout_ms} ms")
