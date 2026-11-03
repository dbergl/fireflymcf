"""STM32F7 I2C (v2 IP, RM0385 s.30) master mode with pluggable target devices."""
import logging

from .base import Peripheral

log = logging.getLogger("i2c")

CR1, CR2, ISR, ICR, RXDR, TXDR = 0x00, 0x04, 0x18, 0x1C, 0x24, 0x28
TXE, TXIS, RXNE, NACKF, STOPF, TC, TCR, BUSY = 1, 2, 4, 16, 32, 64, 128, 1 << 15
START, STOP, RD_WRN, RELOAD, AUTOEND = 1 << 13, 1 << 14, 1 << 10, 1 << 24, 1 << 25
# CR1 interrupt enable -> ISR flags it covers
IRQ_SOURCES = ((1 << 1, TXIS), (1 << 2, RXNE), (1 << 4, NACKF), (1 << 5, STOPF), (1 << 6, TC | TCR))


class I2CDevice:
    """Override write()/read() to model a target.  Transactions are whole messages."""

    def write(self, data):
        pass

    def read(self, n):
        return bytes(n)


class Memory(I2CDevice):
    """EEPROM-style device: first ``addr_bytes`` of a write set the pointer.

    With ``attach_file`` the contents are loaded from and written through to a
    file, so settings survive emulator restarts.
    """

    def __init__(self, size, addr_bytes=1, fill=0xFF):
        self.mem = bytearray([fill]) * size
        self.addr_bytes = addr_bytes
        self.ptr = 0
        self.path = None

    def attach_file(self, path):
        import os

        self.path = path
        if os.path.exists(path):
            data = open(path, "rb").read()[: len(self.mem)]
            self.mem[: len(data)] = data
            log.info("EEPROM loaded from %s", path)

    def write(self, data):
        if len(data) >= self.addr_bytes:
            self.ptr = int.from_bytes(data[: self.addr_bytes], "big") % len(self.mem)
            for b in data[self.addr_bytes:]:
                self.mem[self.ptr] = b
                self.ptr = (self.ptr + 1) % len(self.mem)
            if self.path and len(data) > self.addr_bytes:
                with open(self.path, "wb") as f:
                    f.write(self.mem)

    def read(self, n):
        out = bytearray()
        for _ in range(n):
            out.append(self.mem[self.ptr])
            self.ptr = (self.ptr + 1) % len(self.mem)
        return bytes(out)


class I2C(Peripheral):
    POLL_SAFE = frozenset({ISR})

    def __init__(self, m, base, name, ev_irq):
        self.devices = {}  # 7-bit address -> I2CDevice
        self.ev_irq = ev_irq
        super().__init__(m, base, name)

    def reset(self):
        self.isr = TXE
        self.dev = None
        self.addr = None
        self.reading = False
        self.remaining = 0
        self.txbuf = bytearray()
        self.rxbuf = bytearray()
        self.logged_absent = set()

    def _flush(self):
        if self.dev is not None and self.txbuf:
            log.debug("%s 0x%02x <- %s", self.name, self.addr, self.txbuf.hex())
            self.dev.write(bytes(self.txbuf))
        self.txbuf.clear()

    def _end_of_chunk(self):
        cr2 = self.get(CR2)
        if cr2 & RELOAD:
            self.isr |= TCR
        elif cr2 & AUTOEND:
            self._stop()
        else:
            self.isr |= TC

    def _stop(self):
        self._flush()
        self.isr = (self.isr | STOPF | TXE) & ~(BUSY | TXIS | TC | TCR)
        self.dev = None

    def _start(self, cr2):
        self._flush()  # repeated start ends the previous write phase
        self.addr = (cr2 >> 1) & 0x7F
        self.reading = bool(cr2 & RD_WRN)
        self.remaining = (cr2 >> 16) & 0xFF
        self.dev = self.devices.get(self.addr)
        self.isr = (self.isr | BUSY) & ~(TC | TCR | STOPF | NACKF)
        if self.dev is None:
            if self.addr not in self.logged_absent:
                self.logged_absent.add(self.addr)
                log.info("%s 0x%02x: no device (NACK) pc=0x%08x", self.name, self.addr, self.m.pc())
            self.isr |= NACKF
            self._stop()
            return
        if self.remaining == 0:  # address-only probe (HAL_I2C_IsDeviceReady)
            self._end_of_chunk()
        elif self.reading:
            self.rxbuf = bytearray(self.dev.read(self.remaining))
            log.debug("%s 0x%02x -> %s", self.name, self.addr, self.rxbuf.hex())
            self.isr |= RXNE
        else:
            self.isr = (self.isr | TXIS) & ~TXE

    def _continue(self, cr2):
        """NBYTES reloaded after TCR."""
        self.remaining = (cr2 >> 16) & 0xFF
        self.isr &= ~TCR
        if self.reading:
            self.rxbuf += self.dev.read(self.remaining)
            self.isr |= RXNE
        else:
            self.isr |= TXIS

    def _update_irq(self):
        cr1 = self.get(CR1)
        if cr1 & 1 and any(cr1 & en and self.isr & flags for en, flags in IRQ_SOURCES):
            self.m.core.irq(self.ev_irq)

    def read_reg(self, off):
        if off == ISR:
            return self.isr
        if off == RXDR:
            v = self._read_rxdr()
            self._update_irq()
            return v
        return self.get(off)

    def _read_rxdr(self):
        if not self.rxbuf:
            return 0
        b = self.rxbuf.pop(0)
        self.remaining -= 1
        if not self.rxbuf:
            self.isr &= ~RXNE
        if self.remaining <= 0:
            self._end_of_chunk()
        return b

    def write_reg(self, off, value, old):
        self._write_reg(off, value, old)
        self._update_irq()

    def _write_reg(self, off, value, old):
        if off == CR1:
            self.set(off, value)
            if not value & 1:  # PE=0 resets the state machine
                self.reset()
            return
        if off == CR2:
            self.set(off, value & ~(START | STOP))
            if value & START:
                self._start(value)
            elif self.isr & TCR:
                self._continue(value)
            if value & STOP:
                self._stop()
            return
        if off == ICR:
            self.isr &= ~(value & 0x3F38)
            return
        if off == TXDR:
            if self.dev is not None and self.remaining > 0:
                self.txbuf.append(value & 0xFF)
                self.remaining -= 1
                if self.remaining == 0:
                    self.isr &= ~TXIS
                    self._end_of_chunk()
            return
        self.set(off, value)
