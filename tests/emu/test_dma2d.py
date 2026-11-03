"""DMA2D: the scalar small-transfer path must match the numpy path bit for bit."""
import itertools
import random

import pytest

from fireflymcf.emu.machine import Machine
from fireflymcf.emu.periph import pixfmt

FG, BG, OUT = 0xC0000000, 0xC0010000, 0xC0020000
DIRECT = [pixfmt.ARGB8888, pixfmt.RGB888, pixfmt.RGB565, pixfmt.ARGB1555, pixfmt.ARGB4444]


def transfer(m, small, mode, fg_fmt, bg_fmt, out_fmt, am, w=3, h=2, offset=2):
    d = m.periph["DMA2D"]
    d.SMALL = 64 if small else 0
    m.uc.mem_write(OUT, b"\0" * 256)
    d.set(d.FGMAR, FG)
    d.set(d.BGMAR, BG)
    d.set(d.OMAR, OUT)
    d.set(d.FGOR, offset)
    d.set(d.BGOR, offset)
    d.set(d.OOR, offset)
    d.set(d.FGPFCCR, fg_fmt | am << 16 | 0x9C << 24)
    d.set(d.BGPFCCR, bg_fmt | 0xFF << 24)
    d.set(d.FGCOLR, 0x123456)
    d.set(d.OPFCCR, out_fmt)
    d.set(d.NLR, w << 16 | h)
    d.write_reg(d.CR, mode << 16 | 1, 0)
    return bytes(m.uc.mem_read(OUT, 256))


@pytest.mark.parametrize("mode", [0, 1, 2])
def test_small_path_matches_numpy(mode):
    m = Machine()
    rng = random.Random(mode)
    m.uc.mem_write(FG, bytes(rng.randrange(256) for _ in range(256)))
    m.uc.mem_write(BG, bytes(rng.randrange(256) for _ in range(256)))
    for fg_fmt, bg_fmt, out_fmt, am in itertools.product(DIRECT + [pixfmt.A8], DIRECT, DIRECT,
                                                         (0, 1, 2)):
        args = (mode, fg_fmt, bg_fmt, out_fmt, am)
        assert transfer(m, True, *args) == transfer(m, False, *args), args


def test_one_pixel_a8_blend_matches_numpy():
    # The GUI's anti-aliasing: one A8 pixel over RGB888 into RGB888, ~16,000 per page
    # change, takes DMA2D._blend1.  Compare with the numpy path for every alpha mode,
    # foreground alpha and background alpha mode.
    m = Machine()
    d = m.periph["DMA2D"]
    rng = random.Random(7)
    for am, bg_am in itertools.product((0, 1, 2), (0, 1, 2)):
        for _ in range(40):
            m.uc.mem_write(FG, bytes([rng.randrange(256)]))
            m.uc.mem_write(BG, bytes(rng.randrange(256) for _ in range(3)))
            got = []
            for fast in (True, False):
                d._blend1 = (lambda: False) if not fast else type(d)._blend1.__get__(d)
                d.SMALL = 0
                m.uc.mem_write(OUT, b"\0" * 4)
                for off, v in ((d.FGMAR, FG), (d.BGMAR, BG), (d.OMAR, OUT), (d.FGOR, 0),
                               (d.BGOR, 0), (d.OOR, 0), (d.FGCOLR, rng.randrange(1 << 24) if fast else d.get(d.FGCOLR)),
                               (d.FGPFCCR, pixfmt.A8 | am << 16 | 0x9C << 24),
                               (d.BGPFCCR, pixfmt.RGB888 | bg_am << 16 | 0x71 << 24),
                               (d.OPFCCR, pixfmt.RGB888), (d.NLR, 1 << 16 | 1)):
                    d.set(off, v)
                d.write_reg(d.CR, 2 << 16 | 1, 0)
                got.append(bytes(m.uc.mem_read(OUT, 4)))
            assert got[0] == got[1], (am, bg_am)


@pytest.mark.parametrize("w,h,offset", [(1, 1, 0), (5, 3, 2), (40, 4, 0), (120, 9, 7)])
def test_register_fill_writes_ocolr(w, h, offset):
    m = Machine()
    d = m.periph["DMA2D"]
    m.uc.mem_write(OUT, b"\xAA" * 4096)
    for off, v in ((d.OMAR, OUT), (d.OOR, offset), (d.OCOLR, 0x00123456),
                   (d.OPFCCR, pixfmt.RGB888), (d.NLR, w << 16 | h)):
        d.set(off, v)
    d.write_reg(d.CR, 3 << 16 | 1, 0)
    got = bytes(m.uc.mem_read(OUT, 4096))
    stride = (w + offset) * 3
    for y in range(h):
        assert got[y * stride:y * stride + w * 3] == bytes.fromhex("563412") * w
        assert got[y * stride + w * 3:(y + 1) * stride] == b"\xAA" * (offset * 3)
