"""Memory-mapped peripheral plumbing.

Every peripheral is a register file backed by a bytearray.  Sub-word accesses
are merged into the containing 32-bit word, so subclasses only ever see
aligned 32-bit reads/writes through ``read_reg``/``write_reg``.
"""
import logging
import struct

log = logging.getLogger("periph")

POLL_LIMIT = 16  # identical status reads (no MMIO writes between) treated as a busy-wait


class Peripheral:
    name = "periph"
    size = 0x400
    POLL_SAFE = frozenset()  # register offsets whose reads have no side effects

    def __init__(self, machine, base, name=None, size=None):
        self.m = machine
        self.base = base
        if name:
            self.name = name
        if size:
            self.size = size
        self.regs = bytearray(self.size)
        self.reset()

    def reset(self):
        pass

    # -- storage helpers -------------------------------------------------
    def get(self, off):
        return struct.unpack_from("<I", self.regs, off)[0]

    def set(self, off, value):
        struct.pack_into("<I", self.regs, off, value & 0xFFFFFFFF)

    # -- override points -------------------------------------------------
    def read_reg(self, off):
        """Return the value of the aligned 32-bit register at ``off``."""
        return self.get(off)

    def write_reg(self, off, value, old):
        """Handle a write of the full new word ``value`` (``old`` = previous)."""
        self.set(off, value)

    # -- bus entry points ------------------------------------------------
    def bus_read(self, off, size):
        word = self.read_reg(off & ~3) & 0xFFFFFFFF
        shift = (off & 3) * 8
        return (word >> shift) & ((1 << (size * 8)) - 1)

    def bus_write(self, off, size, value):
        aligned = off & ~3
        old = self.get(aligned)
        if size == 4:
            new = value & 0xFFFFFFFF
        else:
            shift = (off & 3) * 8
            mask = ((1 << (size * 8)) - 1) << shift
            new = (old & ~mask) | ((value << shift) & mask)
        self.write_reg(aligned, new, old)


class Generic(Peripheral):
    """Plain read/write storage; logs the first accesses to each register."""

    def __init__(self, machine, base, name=None, size=None):
        super().__init__(machine, base, name, size)
        self.seen = set()

    def _note(self, kind, off, value):
        key = (kind, off)
        if key not in self.seen:
            self.seen.add(key)
            log.info("%-10s %s +0x%03x = 0x%08x  pc=0x%08x (unmodelled)",
                     self.name, kind, off, value, self.m.pc())

    def read_reg(self, off):
        v = self.get(off)
        self._note("R", off, v)
        return v

    def write_reg(self, off, value, old):
        self._note("W", off, value)
        self.set(off, value)


class Bus:
    """Routes MMIO accesses to peripherals at 1 KiB granularity."""

    PAGE = 10

    def __init__(self, machine, names):
        self.m = machine
        self.pages = {}
        self.names = names  # base -> name, for auto-created generics
        self.stats = None   # Counter of (name, offset, R/W, pc) when profiling
        self.polls = {}     # busy-wait detection, see _poll

    def attach(self, periph):
        for a in range(periph.base, periph.base + periph.size, 1 << self.PAGE):
            self.pages[a >> self.PAGE] = periph
        return periph

    def lookup(self, addr):
        p = self.pages.get(addr >> self.PAGE)
        if p is None:
            base = addr & ~((1 << self.PAGE) - 1)
            p = self.attach(Generic(self.m, base, self.names.get(base, f"@{base:08x}")))
        return p

    def read(self, addr, size):
        p = self.pages.get(addr >> self.PAGE) or self.lookup(addr)
        off = addr - p.base
        if self.stats is not None:
            self.stats[(p.name, off, "R", self.m.pc())] += 1
        if size == 4 and not off & 3:
            value = p.read_reg(off) & 0xFFFFFFFF
        else:
            value = p.bus_read(off, size)
        if off & ~3 in p.POLL_SAFE:
            self._poll(addr, value)
        return value

    def _poll(self, addr, value):
        """Spot a busy-wait on a status register (same PC, same value, no writes)."""
        key = (self.m.pc(), addr, value)
        n = self.polls.get(key, 0) + 1
        self.polls[key] = n
        if n >= POLL_LIMIT:
            self.polls.clear()
            self.m.poll_wait()

    def write(self, addr, size, value):
        if self.polls:
            self.polls.clear()
        p = self.pages.get(addr >> self.PAGE) or self.lookup(addr)
        if self.stats is not None:
            self.stats[(p.name, addr - p.base, "W", self.m.pc())] += 1
        p.bus_write(addr - p.base, size, value)
