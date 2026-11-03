"""STM32 bxCAN controller (RM0385 s.40).  Frames leave through ``sink`` and
arrive through ``receive()``, which applies the acceptance filters: the
FIFO and filter match index (FMI) matter, as the firmware dispatches on FMI."""
import logging
from collections import deque
from dataclasses import dataclass

from .base import Peripheral

log = logging.getLogger("can")

MCR, MSR, TSR, RF0R, RF1R, IER, ESR, BTR = 0x00, 0x04, 0x08, 0x0C, 0x10, 0x14, 0x18, 0x1C
TX_BASE, RX_BASE = 0x180, 0x1B0
FMR, FM1R, FS1R, FFA1R, FA1R, FBANK = 0x200, 0x204, 0x20C, 0x214, 0x21C, 0x240
NUM_BANKS = 28


def rir_of(frame):
    """Frame identifier in RIR/filter register layout."""
    if frame.extended:
        return frame.can_id << 3 | 4 | (2 if frame.rtr else 0)
    return frame.can_id << 21 | (2 if frame.rtr else 0)


def rir16_of(frame):
    """16-bit filter layout: STID[10:0] RTR IDE EXID[17:15]."""
    if frame.extended:
        stid, exid_hi = frame.can_id >> 18, (frame.can_id >> 15) & 7
        return stid << 5 | (16 if frame.rtr else 0) | 8 | exid_hi
    return frame.can_id << 5 | (16 if frame.rtr else 0)


@dataclass
class Frame:
    can_id: int
    data: bytes
    extended: bool = False
    rtr: bool = False
    fmi: int = 0  # set by the receiving controller's acceptance filters

    def __str__(self):
        ident = f"{self.can_id:08X}" if self.extended else f"{self.can_id:03X}"
        return f"{ident} [{len(self.data)}] {self.data.hex(' ')}" + (" RTR" if self.rtr else "")


class BxCAN(Peripheral):
    POLL_SAFE = frozenset({MSR, TSR, RF0R, RF1R, ESR})

    def __init__(self, m, base, name, irq_tx, irq_rx0, irq_rx1, irq_sce):
        self.irq_tx, self.irq_rx0 = irq_tx, irq_rx0
        self.fifo = [deque(), deque()]
        self.sink = None  # callable(Frame)
        self.log_frames = True
        super().__init__(m, base, name)

    def reset(self):
        self.overruns = 0
        self.left_init = None  # machine cycle when INRQ was cleared
        self.set(MCR, 0x00010002)
        self.set(MSR, 0x00000C02)
        self.set(TSR, 0x1C000000)  # all three TX mailboxes empty

    # -- acceptance filters -------------------------------------------------
    def match(self, frame):
        """-> (fifo, fmi) of the winning filter, or None if the frame is rejected.

        FMIs are numbered per FIFO in bank order (32-bit mask = 1 filter, 32-bit
        list / 16-bit mask = 2, 16-bit list = 4), active or not.  Priority:
        32-bit over 16-bit, list over mask, then lower filter (bank) number.
        """
        mode, scale = self.get(FM1R), self.get(FS1R)
        assign, active = self.get(FFA1R), self.get(FA1R)
        r32, r16 = rir_of(frame) & ~1, rir16_of(frame)
        next_fmi = [0, 0]
        best = None
        for bank in range(NUM_BANKS):
            fifo = assign >> bank & 1
            is_list, is_32 = mode >> bank & 1, scale >> bank & 1
            r1, r2 = self.get(FBANK + 8 * bank), self.get(FBANK + 8 * bank + 4)
            base = next_fmi[fifo]
            next_fmi[fifo] += (1 if not is_list else 2) if is_32 else (2 if not is_list else 4)
            if not active >> bank & 1:
                continue
            hits = []
            if is_32:
                if is_list:
                    hits = [i for i, v in enumerate((r1, r2)) if (v & ~1) == r32]
                elif not (r32 ^ r1) & r2 & ~1:
                    hits = [0]
            else:
                halves = (r1 & 0xFFFF, r1 >> 16, r2 & 0xFFFF, r2 >> 16)
                if is_list:
                    hits = [i for i, v in enumerate(halves) if v == r16]
                else:
                    hits = [i for i, (v, m) in enumerate(((halves[0], halves[1]), (halves[2], halves[3])))
                            if not (r16 ^ v) & m]
            for h in hits:
                key = (not is_32, not is_list, bank, h)
                if best is None or key < best[0]:
                    best = (key, fifo, base + h)
        return None if best is None else (best[1], best[2])

    # -- receive path ------------------------------------------------------
    def receive(self, frame):
        if self.get(MCR) & 1:  # in init mode: not on the bus
            return
        hit = self.match(frame)
        if hit is None:
            return
        fifo, frame.fmi = hit
        q = self.fifo[fifo]
        if len(q) >= 3:
            self.overruns += 1  # dropped (FOVR flag not modelled)
            return
        q.append(frame)
        if self.get(IER) & (1 << (1 + 3 * fifo)):  # FMPIE0 / FMPIE1
            self.m.core.irq(self.irq_rx0 + fifo)

    def _rx_reg(self, off):
        n = (off - RX_BASE) // 0x10
        q = self.fifo[n]
        if not q:
            return 0
        f = q[0]
        reg = off & 0xF
        if reg == 0x0:  # RIR
            if f.extended:
                return f.can_id << 3 | 4 | (2 if f.rtr else 0)
            return f.can_id << 21 | (2 if f.rtr else 0)
        if reg == 0x4:  # RDTR
            return getattr(f, "fmi", 0) << 8 | len(f.data)
        d = f.data.ljust(8, b"\0")
        return int.from_bytes(d[0:4] if reg == 0x8 else d[4:8], "little")

    # -- transmit path -----------------------------------------------------
    def _transmit(self, n):
        base = TX_BASE + 0x10 * n
        tir, tdtr = self.get(base), self.get(base + 4)
        data = (self.get(base + 8).to_bytes(4, "little") + self.get(base + 12).to_bytes(4, "little"))
        ext = bool(tir & 4)
        frame = Frame(tir >> 3 if ext else tir >> 21, data[: min(tdtr & 0xF, 8)], ext, bool(tir & 2))
        self.set(base, tir & ~1)
        if self.log_frames:
            log.info("%s tx %s", self.name, frame)
        if self.sink:
            self.sink(frame)
        # RQCPx | TXOKx, mailbox empty again
        self.set(TSR, self.get(TSR) | (3 << (8 * n)) | (1 << (26 + n)))
        if self.get(IER) & 1:  # TMEIE
            self.m.core.irq(self.irq_tx)

    def bit_cycles(self):
        """CPU cycles per CAN bit from BTR (CAN is clocked from APB1 = CPU / 4)."""
        btr = self.get(BTR)
        tq = (btr & 0x3FF) + 1
        bits = 1 + ((btr >> 16) & 0xF) + 1 + ((btr >> 20) & 7) + 1
        return tq * bits * 4

    def _syncing(self):
        """INAK stays set until 11 recessive bits have been seen after leaving init."""
        if self.left_init is None:
            return 0
        return 1 if self.m.cycles - self.left_init < 11 * self.bit_cycles() else 0

    # -- registers ---------------------------------------------------------
    def read_reg(self, off):
        if off == MSR:
            mcr = self.get(MCR)
            inak = mcr & 1 or self._syncing()
            slak = 1 if (mcr & 2 and not inak) else 0
            return (self.get(MSR) & ~3) | inak | slak << 1
        if off in (RF0R, RF1R):
            n = (off - RF0R) // 4
            return min(len(self.fifo[n]), 3)
        if RX_BASE <= off < RX_BASE + 0x20:
            return self._rx_reg(off)
        return self.get(off)

    def write_reg(self, off, value, old):
        if off == MCR:
            if old & 1 and not value & 1:
                self.left_init = self.m.cycles
            self.set(off, value & ~(1 << 15))  # RESET self-clears
            return
        if off == TSR:  # rc_w1 RQCP/TXOK/ALST/TERR bits; ABRQ ignored
            self.set(off, old & ~(value & 0x000F0F0F))
            return
        if off in (RF0R, RF1R):
            n = (off - RF0R) // 4
            if value & (1 << 5) and self.fifo[n]:  # RFOM: release output mailbox
                self.fifo[n].popleft()
            return
        if TX_BASE <= off < TX_BASE + 0x30:
            self.set(off, value)
            if off & 0xF == 0 and value & 1:  # TXRQ
                self._transmit((off - TX_BASE) // 0x10)
            return
        self.set(off, value)
