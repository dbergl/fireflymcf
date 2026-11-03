"""Chrom-ART / LTDC pixel formats <-> RGBA8888 numpy arrays (shape (..., 4), uint8)."""
import numpy as np

ARGB8888, RGB888, RGB565, ARGB1555, ARGB4444, L8, AL44, AL88, L4, A8, A4 = range(11)
BPP = {ARGB8888: 32, RGB888: 24, RGB565: 16, ARGB1555: 16, ARGB4444: 16,
       L8: 8, AL44: 8, AL88: 16, L4: 4, A8: 8, A4: 4}


def _expand(v, bits):
    v = v.astype(np.uint16)
    return ((v << (8 - bits)) | (v >> max(0, 2 * bits - 8))).astype(np.uint8)


def decode(raw, fmt, count, clut=None, color=0):
    """Decode ``count`` pixels from bytes ``raw``.  ``color`` supplies RGB for A8/A4."""
    out = np.empty((count, 4), np.uint8)
    b = np.frombuffer(raw, np.uint8)
    if fmt == ARGB8888:
        p = b[: count * 4].reshape(-1, 4)
        out[:, 0], out[:, 1], out[:, 2], out[:, 3] = p[:, 2], p[:, 1], p[:, 0], p[:, 3]
    elif fmt == RGB888:
        p = b[: count * 3].reshape(-1, 3)
        out[:, 0], out[:, 1], out[:, 2], out[:, 3] = p[:, 2], p[:, 1], p[:, 0], 255
    elif fmt in (RGB565, ARGB1555, ARGB4444, AL88):
        w = b[: count * 2].view("<u2")
        if fmt == RGB565:
            out[:, 0], out[:, 1], out[:, 2] = _expand(w >> 11, 5), _expand((w >> 5) & 63, 6), _expand(w & 31, 5)
            out[:, 3] = 255
        elif fmt == ARGB1555:
            out[:, 0], out[:, 1], out[:, 2] = _expand((w >> 10) & 31, 5), _expand((w >> 5) & 31, 5), _expand(w & 31, 5)
            out[:, 3] = np.where(w & 0x8000, 255, 0)
        elif fmt == ARGB4444:
            out[:, 0], out[:, 1], out[:, 2] = _expand((w >> 8) & 15, 4), _expand((w >> 4) & 15, 4), _expand(w & 15, 4)
            out[:, 3] = _expand(w >> 12, 4)
        else:  # AL88
            out[:] = _lut(clut, (w & 0xFF).astype(np.uint8))
            out[:, 3] = (w >> 8).astype(np.uint8)
    elif fmt in (L8, AL44, A8):
        p = b[:count]
        if fmt == L8:
            out[:] = _lut(clut, p)
        elif fmt == AL44:
            out[:] = _lut(clut, p & 15)
            out[:, 3] = _expand(p >> 4, 4)
        else:
            out[:, :3] = _rgb(color)
            out[:, 3] = p
    elif fmt in (L4, A4):
        p = b[: (count + 1) // 2]
        nib = np.empty(len(p) * 2, np.uint8)
        nib[0::2], nib[1::2] = p & 15, p >> 4
        nib = nib[:count]
        if fmt == L4:
            out[:] = _lut(clut, nib)
        else:
            out[:, :3] = _rgb(color)
            out[:, 3] = _expand(nib, 4)
    else:
        raise ValueError(f"pixel format {fmt}")
    return out


def _rgb(color):
    return np.array([(color >> 16) & 255, (color >> 8) & 255, color & 255], np.uint8)


def _lut(clut, idx):
    if clut is None:
        clut = np.stack([np.arange(256, dtype=np.uint8)] * 3 + [np.full(256, 255, np.uint8)], 1)
    return clut[idx]


def encode(rgba, fmt):
    """Encode an (N, 4) RGBA array into bytes of output format ``fmt`` (0-4)."""
    r, g, b, a = (rgba[:, i].astype(np.uint32) for i in range(4))
    if fmt == ARGB8888:
        return (a << 24 | r << 16 | g << 8 | b).astype("<u4").tobytes()
    if fmt == RGB888:
        return np.stack([b, g, r], 1).astype(np.uint8).tobytes()
    if fmt == RGB565:
        return ((r >> 3) << 11 | (g >> 2) << 5 | (b >> 3)).astype("<u2").tobytes()
    if fmt == ARGB1555:
        return ((a >> 7) << 15 | (r >> 3) << 10 | (g >> 3) << 5 | (b >> 3)).astype("<u2").tobytes()
    if fmt == ARGB4444:
        return ((a >> 4) << 12 | (r >> 4) << 8 | (g >> 4) << 4 | (b >> 4)).astype("<u2").tobytes()
    raise ValueError(f"output pixel format {fmt}")


def clut_from(raw, rgb888, size):
    """Decode a CLUT in memory (ARGB8888 or RGB888 entries) into a (256, 4) array."""
    clut = np.zeros((256, 4), np.uint8)
    clut[:, 3] = 255
    clut[:size] = decode(raw, RGB888 if rgb888 else ARGB8888, size)
    return clut


def blend(fg, bg):
    """Porter-Duff 'over' as done by DMA2D/LTDC (8-bit fixed point)."""
    fa = fg[:, 3:4].astype(np.uint32)
    ba = bg[:, 3:4].astype(np.uint32)
    mult = ba * (255 - fa) // 255
    oa = fa + mult
    safe = np.maximum(oa, 1)
    rgb = (fg[:, :3] * fa + bg[:, :3] * mult) // safe
    return np.concatenate([rgb, oa], 1).astype(np.uint8)


# -- scalar codecs for tiny transfers (the firmware blends single pixels) -------
# Bit-exact with decode/encode/blend above; formats outside these tables take
# the numpy path.

def _x5(v):
    return (v << 3) | (v >> 2)


def _x6(v):
    return (v << 2) | (v >> 4)


def _x4(v):
    return (v << 4) | v


def _d565(m, i):
    w = m[i] | m[i + 1] << 8
    return _x5(w >> 11), _x6((w >> 5) & 63), _x5(w & 31), 255


def _d1555(m, i):
    w = m[i] | m[i + 1] << 8
    return _x5((w >> 10) & 31), _x5((w >> 5) & 31), _x5(w & 31), 255 if w & 0x8000 else 0


def _d4444(m, i):
    w = m[i] | m[i + 1] << 8
    return _x4((w >> 8) & 15), _x4((w >> 4) & 15), _x4(w & 15), _x4(w >> 12)


DECODE1 = {  # fmt -> f(buffer, byte offset) -> (r, g, b, a); A8 handled by the caller
    ARGB8888: lambda m, i: (m[i + 2], m[i + 1], m[i], m[i + 3]),
    RGB888: lambda m, i: (m[i + 2], m[i + 1], m[i], 255),
    RGB565: _d565, ARGB1555: _d1555, ARGB4444: _d4444,
}

ENCODE1 = {  # fmt -> f(r, g, b, a) -> bytes
    ARGB8888: lambda r, g, b, a: bytes((b, g, r, a)),
    RGB888: lambda r, g, b, a: bytes((b, g, r)),
    RGB565: lambda r, g, b, a: ((r >> 3) << 11 | (g >> 2) << 5 | (b >> 3)).to_bytes(2, "little"),
    ARGB1555: lambda r, g, b, a: ((a >> 7) << 15 | (r >> 3) << 10 | (g >> 3) << 5
                                  | (b >> 3)).to_bytes(2, "little"),
    ARGB4444: lambda r, g, b, a: ((a >> 4) << 12 | (r >> 4) << 8 | (g >> 4) << 4
                                  | (b >> 4)).to_bytes(2, "little"),
}


def blend1(fr, fg, fb, fa, br, bg, bb, ba):
    mult = ba * (255 - fa) // 255
    oa = fa + mult
    safe = oa or 1
    return ((fr * fa + br * mult) // safe, (fg * fa + bg * mult) // safe,
            (fb * fa + bb * mult) // safe, oa)
