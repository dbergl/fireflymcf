"""LTDC (LCD-TFT controller) and DMA2D (Chrom-ART) for STM32F7."""
import logging
import struct

import numpy as np

from . import pixfmt
from .base import Peripheral

log = logging.getLogger("display")

FALLBACK_FRAME_HZ = 60  # until the pixel clock and timings are programmed


class LTDC(Peripheral):
    name = "LTDC"
    SSCR, BPCR, AWCR, TWCR, GCR, SRCR, BCCR = 0x08, 0x0C, 0x10, 0x14, 0x18, 0x24, 0x2C
    IER, ISR, ICR, LIPCR, CPSR, CDSR = 0x34, 0x38, 0x3C, 0x40, 0x44, 0x48
    LAYERS = (0x84, 0x104)

    def __init__(self, m, base, irq, cpu_hz):
        self.irq = irq
        self.cpu_hz = cpu_hz
        super().__init__(m, base)
        self.origin = None      # machine cycle of a frame start (line 0)
        self.next_lif = None    # next time the scan reaches the LIPCR line
        self.next_frame = None  # next frame boundary
        self.frames = 0
        self.line_events = 0
        self.on_frame = None  # callable(ltdc) once per frame while enabled

    def reset(self):
        self.isr = 0
        self.clut = [np.zeros((256, 4), np.uint8) for _ in self.LAYERS]

    # -- geometry ----------------------------------------------------------
    def active_area(self):
        bpcr, awcr = self.get(self.BPCR), self.get(self.AWCR)
        ahbp, avbp = bpcr >> 16 & 0xFFF, bpcr & 0x7FF
        return ahbp, avbp, (awcr >> 16 & 0xFFF) - ahbp, (awcr & 0x7FF) - avbp

    def enabled(self):
        return bool(self.get(self.GCR) & 1)

    # -- timing ------------------------------------------------------------
    def timing(self):
        """(CPU cycles per line, lines per frame).

        Pixel clock = PLL input (derived from the CPU clock and PLLCFGR N/P) x
        PLLSAI N / R / DIVR; line = TOTALW+1 pixels, frame = TOTALH+1 lines.
        On this panel: 32 MHz, 908 x 992 -> 35.5 Hz, and the firmware programs
        two line events per frame (lines 29 and 512), so the GUI runs at ~71 Hz.
        """
        twcr = self.get(self.TWCR)
        totw, toth = (twcr >> 16 & 0xFFF) + 1, (twcr & 0x7FF) + 1
        rcc = self.m.periph.get("RCC") if hasattr(self.m, "periph") else None
        if rcc is not None and totw > 1 and toth > 1:
            pll, sai, dck = rcc.get(0x04), rcc.get(0x88), rcc.get(0x8C)
            n, p = pll >> 6 & 0x1FF, (pll >> 16 & 3) * 2 + 2
            sai_n, sai_r = sai >> 6 & 0x1FF, sai >> 28 & 7
            divr = (2, 4, 8, 16)[dck >> 16 & 3]
            if n and sai_n and sai_r:
                pixel_hz = self.cpu_hz * p / n * sai_n / sai_r / divr
                return self.cpu_hz * totw / pixel_hz, toth
        toth = max(toth, 1)
        return self.cpu_hz / FALLBACK_FRAME_HZ / toth, toth

    def _next_at_line(self, line, after):
        """First cycle >= after at which the scan starts ``line``."""
        line_c, total = self.timing()
        frame_c = line_c * total
        offset = line * line_c
        k = -(-(after - self.origin - offset) // frame_c)  # ceil
        return int(self.origin + offset + max(0, k) * frame_c)

    def _retime(self):
        now = self.m.cycles
        if self.origin is None:
            self.origin = now
        lipcr = self.get(self.LIPCR) & 0x7FF
        _, total = self.timing()
        self.next_lif = self._next_at_line(lipcr, now + 1) if lipcr < total else None
        self.next_frame = self._next_at_line(0, now + 1)

    def next_event(self):
        now = self.m.cycles
        if not self.enabled():
            self.origin = self.next_lif = self.next_frame = None
            return None
        if self.next_frame is None:
            self._retime()
        if self.next_lif is not None and now >= self.next_lif:
            self.line_events += 1
            self.isr |= 1  # LIF
            if self.get(self.IER) & 1:
                self.m.core.irq(self.irq)
            lipcr = self.get(self.LIPCR) & 0x7FF
            self.next_lif = self._next_at_line(lipcr, now + 1)
        if now >= self.next_frame:
            self._vblank()
            self.next_frame = self._next_at_line(0, now + 1)
        due = [t for t in (self.next_lif, self.next_frame) if t is not None]
        return max(1, min(due) - now)

    def _vblank(self):
        self.frames += 1
        srcr = self.get(self.SRCR)
        if srcr & 2:  # VBR: shadow reload at vertical blanking
            self.set(self.SRCR, 0)
            self.isr |= 8  # RRIF
            if self.get(self.IER) & 8:
                self.m.core.irq(self.irq)
        if self.on_frame:
            self.on_frame(self)

    # -- registers ---------------------------------------------------------
    def read_reg(self, off):
        if off == self.ISR:
            return self.isr
        if off == self.CDSR:  # VDES/HDES/VSYNCS/HSYNCS: report "in active display"
            return 0x3
        if off == self.CPSR:
            return 0
        return self.get(off)

    def write_reg(self, off, value, old):
        if off in (self.GCR, self.TWCR, self.LIPCR):
            self.set(off, value)
            if self.enabled():
                self._retime()
            self.m.reschedule()
            return
        if off == self.ICR:
            self.isr &= ~value
            return
        if off == self.SRCR:
            if value & 1:  # IMR: immediate reload
                self.isr |= 8
                if self.get(self.IER) & 8:
                    self.m.core.irq(self.irq)
                value &= ~1
        for i, lb in enumerate(self.LAYERS):
            if off == lb + 0x44:  # CLUTWR
                idx = value >> 24
                self.clut[i][idx] = ((value >> 16) & 255, (value >> 8) & 255, value & 255, 255)
                return
        self.set(off, value)

    # -- rendering ---------------------------------------------------------
    def render(self):
        """Composite the enabled layers into an (H, W, 3) uint8 RGB image, or None."""
        ahbp, avbp, width, height = self.active_area()
        if width <= 0 or height <= 0 or width > 2048 or height > 2048:
            return None
        bccr = self.get(self.BCCR)
        img = np.empty((height, width, 4), np.uint8)
        img[..., 0], img[..., 1], img[..., 2], img[..., 3] = bccr >> 16 & 255, bccr >> 8 & 255, bccr & 255, 255
        for i, lb in enumerate(self.LAYERS):
            self._layer(img, i, lb, ahbp, avbp, width, height)
        return img[..., :3]

    def _layer(self, img, i, lb, ahbp, avbp, width, height):
        g = lambda o: self.get(lb + o)  # noqa: E731
        if not g(0x00) & 1:
            return
        whpcr, wvpcr = g(0x04), g(0x08)
        x0 = (whpcr & 0xFFF) - ahbp - 1
        x1 = (whpcr >> 16 & 0xFFF) - ahbp + 1 - 1
        y0 = (wvpcr & 0x7FF) - avbp - 1
        y1 = (wvpcr >> 16 & 0x7FF) - avbp + 1 - 1
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(width, x1), min(height, y1)
        fmt = g(0x10) & 7
        addr = g(0x28)
        pitch = g(0x2C) >> 16 & 0x1FFF
        lines = min(g(0x30) & 0x7FF, y1 - y0)
        w = x1 - x0
        if w <= 0 or lines <= 0 or pitch == 0:
            return
        bpp = pixfmt.BPP[fmt] // 8
        n = pitch * (lines - 1) + w * bpp
        raw = self.m.mem(addr, n)
        if raw is None:
            try:
                raw = bytes(self.m.uc.mem_read(addr, n))
            except Exception:
                return
        rows = np.lib.stride_tricks.as_strided(np.frombuffer(raw, np.uint8), (lines, w * bpp), (pitch, 1))
        ca = g(0x14) & 255
        dst = img[y0:y0 + lines, x0:x1]
        if ca == 255 and fmt == pixfmt.RGB888:  # opaque: blending reduces to a copy
            dst[..., 2::-1] = rows.reshape(lines, w, 3)
            return
        clut = self.clut[i] if g(0x00) & 0x10 else None
        px = pixfmt.decode(np.ascontiguousarray(rows), fmt, w * lines, clut).reshape(lines, w, 4)
        if ca == 255 and fmt == pixfmt.RGB565:
            dst[..., :3] = px[..., :3]
            return
        px[..., 3] = (px[..., 3].astype(np.uint16) * ca // 255).astype(np.uint8)
        out = pixfmt.blend(px.reshape(-1, 4), dst.reshape(-1, 4)).reshape(dst.shape)
        out[..., 3] = 255
        img[y0:y0 + lines, x0:x1] = out


class DMA2D(Peripheral):
    name = "DMA2D"
    size = 0xC00
    CR, ISR, IFCR, FGMAR, FGOR, BGMAR, BGOR = 0x00, 0x04, 0x08, 0x0C, 0x10, 0x14, 0x18
    FGPFCCR, FGCOLR, BGPFCCR, BGCOLR, FGCMAR, BGCMAR = 0x1C, 0x20, 0x24, 0x28, 0x2C, 0x30
    OPFCCR, OCOLR, OMAR, OOR, NLR = 0x34, 0x38, 0x3C, 0x40, 0x44

    def __init__(self, m, base, irq):
        self.irq = irq
        super().__init__(m, base)

    def reset(self):
        self.isr = 0
        self.transfers = 0

    _PACK = struct.Struct("<I").pack_into

    def fast_write(self, off, size, value):
        """Machine's direct write path (``FAST_MMIO``) for whole-word writes: stored
        without the read-modify-write dispatch unless they start something (CR START,
        a PFCCR's CLUT START); IFCR clears flags.  Sub-word writes take the full path.
        Called straight from libunicorn, so it reports its own exceptions."""
        try:
            if size != 4:
                self.bus_write(off, size, value)
            elif off == 0x08:                                   # IFCR
                self.isr &= ~value
            elif (off == 0x00 and value & 1) or ((off == 0x1C or off == 0x24) and value & 0x20):
                self.write_reg(off, value, self.get(off))
            else:
                self._PACK(self.regs, off, value)
        except BaseException as e:
            self.m._hook_failed(e)

    def read_reg(self, off):
        if off == self.ISR:
            return self.isr
        return self.get(off)

    def write_reg(self, off, value, old):
        if off == self.IFCR:
            self.isr &= ~value
            return
        if off in (self.FGPFCCR, self.BGPFCCR) and value & 0x20:  # START: load CLUT
            self.set(off, value & ~0x20)
            self.isr |= 0x10  # CTCIF
            return
        if off == self.CR and value & 1:
            self.set(off, value & ~1)
            try:
                self._run(value)
            except Exception as e:  # keep the guest running; flag a transfer error
                log.warning("DMA2D transfer failed: %s", e)
                self.isr |= 1
            self.isr |= 2  # TCIF
            if value & (1 << 9):
                self.m.core.irq(self.irq)
            return
        self.set(off, value)

    SMALL = 64  # transfers up to this many pixels use the scalar path

    def _guest(self, addr, n):
        """Guest memory as a zero-copy view where possible (bytes otherwise)."""
        view = self.m.mem(addr, n)
        return view if view is not None else bytes(self.m.uc.mem_read(addr, n))

    def _clut(self, pfccr, cmar_off, clut_off):
        size = (pfccr >> 8 & 0xFF) + 1
        rgb888 = bool(pfccr & 0x10)
        raw = bytes(self.regs[clut_off:clut_off + 1024])
        cmar = self.get(cmar_off)
        if cmar:
            raw = self._guest(cmar, size * (3 if rgb888 else 4))
        return pixfmt.clut_from(raw, rgb888, size)

    def _read_plane(self, mar, offr, pfccr, colr, cmar_off, clut_off, w, h):
        fmt = pfccr & 0xF
        bits = pixfmt.BPP[fmt]
        clut = self._clut(pfccr, cmar_off, clut_off) if fmt in (5, 6, 7, 8) else None
        line_offset = self.get(offr) & 0x3FFF
        stride_px = w + line_offset
        if bits >= 8:
            bpp = bits // 8
            raw = self._guest(self.get(mar), (stride_px * (h - 1) + w) * bpp)
            rows = np.lib.stride_tricks.as_strided(np.frombuffer(raw, np.uint8),
                                                   (h, w * bpp), (stride_px * bpp, 1))
            px = pixfmt.decode(np.ascontiguousarray(rows), fmt, w * h, clut, self.get(colr))
        else:  # 4bpp: decode line by line (rows may start mid-byte)
            raw = bytes(self._guest(self.get(mar), (stride_px * (h - 1) + w) * bits // 8 + 1))
            out = []
            for y in range(h):
                start = y * stride_px
                chunk = raw[start // 2: (start + w + 1) // 2 + 1]
                line = pixfmt.decode(chunk, fmt, w + (start & 1), clut, self.get(colr))
                out.append(line[start & 1: (start & 1) + w])
            px = np.concatenate(out)
        am, alpha = pfccr >> 16 & 3, pfccr >> 24
        if am == 1:
            px[:, 3] = alpha
        elif am == 2:
            px[:, 3] = (px[:, 3].astype(np.uint16) * alpha // 255).astype(np.uint8)
        return px

    def _run(self, cr):
        mode = cr >> 16 & 3
        nlr = self.get(self.NLR)
        w, h = nlr >> 16 & 0x3FFF, nlr & 0xFFFF
        if not w or not h:
            return
        self.transfers += 1
        if w == 1 and h == 1 and mode == 2 and self._blend1():
            return
        ofmt = self.get(self.OPFCCR) & 7
        fg_fmt = self.get(self.FGPFCCR) & 0xF
        if mode == 0 and fg_fmt <= 4:  # memory to memory, no conversion
            ofmt = fg_fmt
        bpp = pixfmt.BPP[ofmt] // 8
        stride = (w + (self.get(self.OOR) & 0x3FFF)) * bpp
        out = self.m.mem(self.get(self.OMAR), stride * (h - 1) + w * bpp)
        if mode != 3 and w * h <= self.SMALL and out is not None and self._run_small(mode, w, h, ofmt, out, stride):
            return
        if mode == 3 and out is not None:   # register to memory, straight into guest RAM
            row = self.get(self.OCOLR).to_bytes(4, "little")[:bpp] * w
            if stride == w * bpp:
                out[:] = row * h
            else:
                for y in range(h):
                    out[y * stride:y * stride + w * bpp] = row
            return
        if mode == 3:  # register to memory: OCOLR is already in output format
            data = np.tile(np.frombuffer(self.get(self.OCOLR).to_bytes(4, "little")[:bpp], np.uint8),
                           w * h)
        else:
            fg = self._read_plane(self.FGMAR, self.FGOR, self.get(self.FGPFCCR), self.FGCOLR,
                                  self.FGCMAR, 0x400, w, h)
            if mode == 2:
                bg = self._read_plane(self.BGMAR, self.BGOR, self.get(self.BGPFCCR), self.BGCOLR,
                                      self.BGCMAR, 0x800, w, h)
                fg = pixfmt.blend(fg, bg)
            data = np.frombuffer(pixfmt.encode(fg, ofmt), np.uint8)
        data = data.reshape(h, w * bpp)
        if out is not None:
            np.lib.stride_tricks.as_strided(np.frombuffer(out, np.uint8), (h, w * bpp),
                                            (stride, 1), writeable=True)[:] = data
        else:
            for y in range(h):
                self.m.uc.mem_write(self.get(self.OMAR) + y * stride, data[y].tobytes())

    def _blend1(self):
        """One A8 pixel over one RGB888 pixel into RGB888 -- the GUI's anti-aliasing,
        ~16,000 per page change.  Bit-exact with ``pixfmt.blend``; False for any
        other formats."""
        get = self.get
        fgp, bgp = get(0x1C), get(0x24)
        if fgp & 0xF != 9 or bgp & 0xF != 1 or get(0x34) & 7 != 1:
            return False
        mem = self.m.mem
        fm, bm, om = mem(get(0x0C), 1), mem(get(0x14), 3), mem(get(0x3C), 3)
        if fm is None or bm is None or om is None:
            return False
        fa = fm[0]
        am = fgp >> 16 & 3
        if am == 1:
            fa = fgp >> 24
        elif am == 2:
            fa = fa * (fgp >> 24) // 255
        am = bgp >> 16 & 3
        ba = 255 if am == 0 else bgp >> 24            # RGB888 alpha 255; *a//255 = a
        mult = ba * (255 - fa) // 255
        oa = max(fa + mult, 1)
        c = get(0x20)                                 # FGCOLR
        om[0] = ((c & 255) * fa + bm[0] * mult) // oa
        om[1] = ((c >> 8 & 255) * fa + bm[1] * mult) // oa
        om[2] = ((c >> 16 & 255) * fa + bm[2] * mult) // oa
        return True

    def _run_small(self, mode, w, h, ofmt, out, stride):
        """Scalar transfer for a few pixels; False if a format needs the numpy path."""
        enc = pixfmt.ENCODE1.get(ofmt)
        fg = self._plane1(self.FGMAR, self.FGOR, self.FGPFCCR, self.FGCOLR, w, h)
        bg = (self._plane1(self.BGMAR, self.BGOR, self.BGPFCCR, self.BGCOLR, w, h)
              if mode == 2 else None)
        if enc is None or fg is None or (mode == 2 and bg is None):
            return False
        bpp = pixfmt.BPP[ofmt] // 8
        blend = pixfmt.blend1
        k = 0
        for y in range(h):
            o = y * stride
            for x in range(w):
                px = fg[k] if bg is None else blend(*fg[k], *bg[k])
                out[o:o + bpp] = enc(*px)
                o += bpp
                k += 1
        return True

    def _plane1(self, mar, offr, pfccr_off, colr, w, h):
        """Decode a plane to a list of (r, g, b, a) tuples, or None if unsupported."""
        pfccr = self.get(pfccr_off)
        fmt = pfccr & 0xF
        if fmt == pixfmt.A8:
            bpp, color = 1, self.get(colr)
            rgb = (color >> 16 & 255, color >> 8 & 255, color & 255)
            dec = lambda m, i: (*rgb, m[i])  # noqa: E731
        else:
            dec = pixfmt.DECODE1.get(fmt)
            if dec is None:
                return None
            bpp = pixfmt.BPP[fmt] // 8
        stride = (w + (self.get(offr) & 0x3FFF)) * bpp
        mem = self.m.mem(self.get(mar), stride * (h - 1) + w * bpp)
        if mem is None:
            return None
        am, alpha = pfccr >> 16 & 3, pfccr >> 24
        px = []
        for y in range(h):
            for x in range(w):
                r, g, b, a = dec(mem, y * stride + x * bpp)
                if am == 1:
                    a = alpha
                elif am == 2:
                    a = a * alpha // 255
                px.append((r, g, b, a))
        return px
