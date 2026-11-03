"""Goodix GT911 capacitive touch controller (I2C, 16-bit big-endian register addresses)."""
import logging
import struct

from .i2c import I2CDevice

log = logging.getLogger("touch")

REG_COMMAND = 0x8040
REG_CONFIG = 0x8047
REG_PRODUCT_ID = 0x8140
REG_STATUS = 0x814E
REG_POINTS = 0x814F
SCAN_HZ = 100  # the controller rescans and reports every ~10 ms while touched


class GT911(I2CDevice):
    def __init__(self, width=800, height=480):
        self.regs = bytearray(0x10000)
        self.ptr = 0
        self.regs[REG_PRODUCT_ID:REG_PRODUCT_ID + 4] = b"911\0"
        struct.pack_into("<H", self.regs, 0x8144, 0x1060)        # firmware version
        struct.pack_into("<HH", self.regs, 0x8146, width, height)  # x/y resolution
        self.regs[0x814A] = 0x00                                  # vendor id
        self.regs[REG_CONFIG] = 0x41                              # config version
        struct.pack_into("<HH", self.regs, 0x8048, width, height)
        self.regs[0x804C] = 5                                     # max touch points
        self.points = []
        self.on_irq = None    # callable() pulsing the INT line
        self.m = None         # machine, for scan timing (bind())
        self.next_scan = None
        self.release_pending = False

    def bind(self, machine):
        self.m = machine

    def touch(self, points):
        """Set the current touch points: list of (x, y). Empty list = released.

        Like the real controller, a report is posted now and then again every
        scan while a finger stays down (holds), plus one empty report on release.
        """
        was_down = bool(self.points)
        self.points = list(points)[:5]
        self.release_pending = was_down and not self.points
        self._report()
        if self.m is not None:
            self.next_scan = self.m.cycles + self.m.cpu_hz // SCAN_HZ
            self.m.reschedule()

    def _report(self):
        self.regs[REG_STATUS] = 0x80 | len(self.points)
        for i, (x, y) in enumerate(self.points):
            struct.pack_into("<BHHHB", self.regs, REG_POINTS + 8 * i, i, int(x), int(y), 30, 0)
        if self.on_irq:
            self.on_irq()

    def next_event(self):
        """Timed-peripheral hook: rescan while touched."""
        if self.next_scan is None:
            return None
        now = self.m.cycles
        if now >= self.next_scan:
            if self.points:
                # Post a fresh report once the host has consumed the previous one.
                if not self.regs[REG_STATUS] & 0x80:
                    self._report()
            elif self.release_pending and not self.regs[REG_STATUS] & 0x80:
                self.release_pending = False
            if not self.points and not self.release_pending:
                self.next_scan = None
                return None
            self.next_scan = now + self.m.cpu_hz // SCAN_HZ
        return self.next_scan - now

    def write(self, data):
        if len(data) < 2:
            return
        self.ptr = (data[0] << 8) | data[1]
        payload = data[2:]
        if payload:
            if self.ptr == REG_STATUS and payload[0] == 0:
                self.regs[REG_STATUS] = 0  # host acknowledges the report
            elif self.ptr == REG_COMMAND:
                log.debug("GT911 command 0x%02x", payload[0])
            else:
                self.regs[self.ptr:self.ptr + len(payload)] = payload
            self.ptr += len(payload)

    def read(self, n):
        out = bytes(self.regs[self.ptr:self.ptr + n])
        self.ptr += n
        return out
