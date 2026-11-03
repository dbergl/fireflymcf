"""Cortex-M7 system layer for Unicorn.

Unicorn executes M-profile Thumb code but does not take exceptions: an SVC is
only reported through UC_HOOK_INTR (2) and a branch to an EXC_RETURN value is
reported as intr 8 with PC = EXC_RETURN & ~1.  This module performs exception
entry/return (basic and FP-extended frames), and models the NVIC, SCB and
SysTick registers of the System Control Space.
"""
import logging
import struct

from unicorn.arm_const import (
    UC_ARM_REG_BASEPRI, UC_ARM_REG_CONTROL, UC_ARM_REG_FAULTMASK,
    UC_ARM_REG_FPSCR, UC_ARM_REG_IPSR, UC_ARM_REG_LR, UC_ARM_REG_MSP,
    UC_ARM_REG_PC, UC_ARM_REG_PRIMASK, UC_ARM_REG_PSP, UC_ARM_REG_R0,
    UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R12,
    UC_ARM_REG_S0, UC_ARM_REG_XPSR,
)

from .fastreg import FastRegs
from .periph.base import Peripheral

log = logging.getLogger("core")

NMI, HARDFAULT, MEMMANAGE, BUSFAULT, USAGEFAULT = 2, 3, 4, 5, 6
SVCALL, DEBUGMON, PENDSV, SYSTICK = 11, 12, 14, 15
NUM_IRQ = 112  # STM32F7 has 98-110 external interrupts depending on part
FIXED_PRIO = {1: -3, NMI: -2, HARDFAULT: -1}

CONTROL_SPSEL = 2
CONTROL_FPCA = 4
STACKED_REGS = (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
                UC_ARM_REG_R12, UC_ARM_REG_LR)


class CoreFault(Exception):
    pass


class Core:
    def __init__(self, machine):
        self.m = machine
        self.uc = machine.uc
        self.vtor = 0
        self.prigroup = 0
        self.pending = set()      # exception numbers
        self.enabled = set()      # exception numbers of enabled external IRQs
        self.prio = {}            # exception number -> 8-bit priority
        self.active = []          # stack of active exception numbers
        self.exc_exit = None      # EXC_RETURN latched by the intr hook
        self.svc_pc = None        # return address of a latched SVC
        self.sleeping = False
        self.idle = False         # idle task detected spinning (see SCS ICSR write)
        self.last_yield_pc = None
        self.other_exceptions = 0
        self.regs = regs = FastRegs(self.uc)
        self.read_pc = lambda: regs.read(UC_ARM_REG_PC)
        self._masks, _ = regs.batch([UC_ARM_REG_BASEPRI, UC_ARM_REG_PRIMASK, UC_ARM_REG_FAULTMASK])
        self._entry_state, _ = regs.batch([UC_ARM_REG_CONTROL, UC_ARM_REG_IPSR, UC_ARM_REG_MSP,
                                           UC_ARM_REG_PSP, UC_ARM_REG_XPSR, *STACKED_REGS])
        # Write order matters: SPSEL only changes in thread mode, so CONTROL goes
        # before IPSR on entry and after it on return.
        _, self._entry_set = regs.batch([UC_ARM_REG_CONTROL, UC_ARM_REG_IPSR, UC_ARM_REG_MSP,
                                         UC_ARM_REG_PSP, UC_ARM_REG_LR, UC_ARM_REG_PC])
        self._sp_state, _ = regs.batch([UC_ARM_REG_MSP, UC_ARM_REG_PSP, UC_ARM_REG_CONTROL])
        _, self._return_set = regs.batch([*STACKED_REGS, UC_ARM_REG_IPSR, UC_ARM_REG_CONTROL,
                                          UC_ARM_REG_MSP, UC_ARM_REG_PSP, UC_ARM_REG_XPSR,
                                          UC_ARM_REG_PC])
        self._fp_get, self._fp_set = regs.batch([UC_ARM_REG_S0 + i for i in range(16)]
                                                + [UC_ARM_REG_FPSCR])

    # -- priorities ----------------------------------------------------------
    def group_prio(self, exc):
        if exc in FIXED_PRIO:
            return FIXED_PRIO[exc]
        p = self.prio.get(exc, 0)
        return p & (0xFF << (self.prigroup + 1)) & 0xFF

    def exec_prio(self):
        prio = 256
        for exc in self.active:
            prio = min(prio, self.group_prio(exc))
        basepri, primask, faultmask = self._masks()
        basepri &= 0xFF
        if basepri:
            prio = min(prio, basepri & (0xFF << (self.prigroup + 1)) & 0xFF)
        if primask & 1:
            prio = min(prio, 0)
        if faultmask & 1:
            prio = min(prio, -1)
        return prio

    def set_pending(self, exc):
        self.pending.add(exc)
        # Inside an MMIO callback: take a now-preempting exception promptly.
        if self.m.in_emu and self.next_exception() is not None:
            self.m.stop_slice()

    def irq(self, n):
        """Pend external interrupt ``n`` (IRQn, not exception number)."""
        self.set_pending(16 + n)

    def deliverable(self):
        """Pending exceptions that are enabled (ignores priority masking)."""
        return {e for e in self.pending if e < 16 or e in self.enabled}

    def next_exception(self):
        if not self.pending:
            return None
        cands = self.deliverable()
        if not cands:
            return None
        best = min(cands, key=lambda e: (self.group_prio(e), self.prio.get(e, 0), e))
        if self.group_prio(best) < self.exec_prio():
            return best
        return None

    # -- entry/return --------------------------------------------------------
    def take_pending(self):
        """Called between emulation slices: perform any due exception entry."""
        if self.svc_pc is not None:
            pc, self.svc_pc = self.svc_pc, None
            self.enter(SVCALL, pc)
            return True
        exc = self.next_exception()
        if exc is None:
            return False
        self.pending.discard(exc)
        self.sleeping = False
        self.enter(exc, self.read_pc())
        return True

    def enter(self, exc, return_pc):
        uc = self.uc
        control, ipsr, msp, psp, xpsr, *stacked = self._entry_state()
        thread = (ipsr & 0x1FF) == 0
        use_psp = thread and (control & CONTROL_SPSEL)
        fpca = bool(control & CONTROL_FPCA)
        sp = psp if use_psp else msp

        frame = 0x68 if fpca else 0x20
        new_sp = sp - frame
        if new_sp & 4:
            new_sp &= ~7
            xpsr |= 1 << 9
        else:
            xpsr &= ~(1 << 9)
        data = struct.pack("<8I", *stacked, return_pc & ~1, xpsr | (1 << 24))
        if fpca:
            data += struct.pack("<18I", *self._fp_get(), 0)
        view = self.m.mem(new_sp, len(data))
        if view is not None:
            view[:] = data
        else:
            uc.mem_write(new_sp, data)

        exc_return = 0xFFFFFFE1 | (0 if fpca else 0x10)
        if thread:
            exc_return |= 0x8 | (0x4 if use_psp else 0)
        if use_psp:
            psp = new_sp
        else:
            msp = new_sp
        vec = self.m.mem(self.vtor + exc * 4, 4) or uc.mem_read(self.vtor + exc * 4, 4)
        vector = struct.unpack("<I", vec)[0]
        if vector == 0:
            raise CoreFault(f"exception {exc} has a null vector")
        self._entry_set([control & ~(CONTROL_SPSEL | CONTROL_FPCA), exc, msp, psp,
                         exc_return, vector | 1])
        self.active.append(exc)
        if exc != PENDSV:
            self.other_exceptions += 1

    def leave(self, exc_return):
        uc = self.uc
        if not self.active:
            raise CoreFault(f"EXC_RETURN 0x{exc_return:08x} with no active exception")
        self.active.pop()
        to_thread = bool(exc_return & 0x8)
        use_psp = bool(exc_return & 0x4)
        fpca = not (exc_return & 0x10)
        msp, psp, control = self._sp_state()
        sp = psp if use_psp else msp
        frame = 0x68 if fpca else 0x20
        raw = self.m.mem(sp, frame)
        if raw is None:
            raw = bytes(uc.mem_read(sp, frame))
        words = struct.unpack_from("<8I", raw)
        if fpca:
            self._fp_set(struct.unpack_from("<17I", raw, 0x20))
        xpsr = words[7]
        sp += frame + (4 if xpsr & (1 << 9) else 0)
        if use_psp:
            psp = sp
        else:
            msp = sp
        ipsr = 0 if to_thread else (self.active[-1] if self.active else 0)
        control &= ~(CONTROL_SPSEL | CONTROL_FPCA)
        if to_thread and use_psp:
            control |= CONTROL_SPSEL
        if fpca:
            control |= CONTROL_FPCA
        # Restore flags/IT/GE state but keep the IPSR we just set.
        self._return_set([*words[:6], ipsr, control, msp, psp,
                          (xpsr & 0xFFFFFE00 & ~(1 << 9)) | ipsr, words[6] | 1])

    def service(self):
        """Process events latched during the last slice. Returns True if PC changed."""
        changed = False
        if self.exc_exit is not None:
            er, self.exc_exit = self.exc_exit, None
            self.leave(er)
            changed = True
        return self.take_pending() or changed


class SCS(Peripheral):
    """System Control Space 0xE000E000: SysTick, NVIC, SCB, FPU and cache ops."""

    name = "SCS"
    size = 0x1000

    def reset(self):
        self.core = self.m.core
        self.st_ctrl = 0
        self.st_load = 0
        self.st_val = 0
        self.st_last = 0  # machine cycle count when st_val was last synced
        self.set(0xD00, 0x411FC271)  # CPUID: Cortex-M7 r1p1
        self.set(0xD80, 0xF01FE019)  # CCSIDR: 16 KiB, 4-way, 32-byte lines
        self.set(0xD88, 0x00F00000)  # CPACR: FPU enabled (Unicorn always executes VFP)

    # -- SysTick ---------------------------------------------------------
    def _st_sync(self):
        """Bring the SysTick counter up to the current machine cycle count.

        The counter counts VAL..0; reaching 0 sets COUNTFLAG (and pends the
        exception if TICKINT); the next clock reloads LOAD.  A cleared VAL is
        held as LOAD+1 so the first reload does not fire.
        """
        now = self.m.cycles
        elapsed = now - self.st_last
        self.st_last = now
        if not self.st_ctrl & 1 or elapsed <= 0:
            return
        if elapsed < self.st_val:
            self.st_val -= elapsed
            return
        elapsed -= self.st_val
        self.st_ctrl |= 1 << 16
        if self.st_ctrl & 2:
            self.core.set_pending(SYSTICK)
        period = self.st_load + 1
        k = elapsed % period if self.st_load else 0
        self.st_val = self.st_load - (k - 1) if k else (period if self.st_load else 0)

    def next_event(self):
        """Cycles until the next SysTick interrupt, or None."""
        if self.st_ctrl & 3 == 3 and self.st_load:
            self._st_sync()
            return max(1, self.st_val)
        return None

    # -- registers -------------------------------------------------------
    def read_reg(self, off):
        core = self.core
        if off == 0x010:
            self._st_sync()
            v = self.st_ctrl
            self.st_ctrl &= ~(1 << 16)
            return v
        if off == 0x014:
            return self.st_load
        if off == 0x018:
            self._st_sync()
            return min(self.st_val, self.st_load) if self.st_val != self.st_load + 1 else 0
        if off == 0x01C:
            return 0x40000000 | 19999  # CALIB: no ref clock, 1ms @ 20 MHz (HCLK/8)
        if 0x100 <= off < 0x180 or 0x180 <= off < 0x200:
            base = 16 + ((off & 0x7F) * 8)
            return sum(1 << i for i in range(32) if base + i in core.enabled)
        if 0x200 <= off < 0x300:
            base = 16 + ((off & 0x7F) * 8)
            return sum(1 << i for i in range(32) if base + i in core.pending)
        if 0x300 <= off < 0x380:
            base = 16 + ((off & 0x7F) * 8)
            return sum(1 << i for i in range(32) if base + i in core.active)
        if 0x400 <= off < 0x4F0:
            exc = 16 + off - 0x400
            return sum(core.prio.get(exc + i, 0) << (8 * i) for i in range(4))
        if off == 0xD04:  # ICSR
            v = core.active[-1] if core.active else 0
            pend = [e for e in core.pending if e < 16 or e in core.enabled]
            if pend:
                v |= min(pend) << 12 | 1 << 22
            if PENDSV in core.pending:
                v |= 1 << 28
            if SYSTICK in core.pending:
                v |= 1 << 26
            if len(core.active) <= 1:
                v |= 1 << 11  # RETTOBASE
            return v
        if off == 0xD08:
            return core.vtor
        if off == 0xD0C:
            return 0xFA050000 | core.prigroup << 8
        if 0xD18 <= off < 0xD24:
            exc = 4 + off - 0xD18
            return sum(core.prio.get(exc + i, 0) << (8 * i) for i in range(4))
        return self.get(off)

    def write_reg(self, off, value, old):
        core = self.core
        if off == 0x010:
            self._st_sync()
            if value & 1 and not self.st_ctrl & 1:
                self.st_last = self.m.cycles
                if not self.st_val:
                    self.st_val = self.st_load + 1
            self.st_ctrl = (self.st_ctrl & (1 << 16)) | (value & 7)
            self.m.reschedule()
            return
        if off == 0x014:
            self.st_load = value & 0xFFFFFF
            self.m.reschedule()
            return
        if off == 0x018:
            self._st_sync()
            self.st_val = self.st_load + 1
            self.st_ctrl &= ~(1 << 16)
            self.m.reschedule()
            return
        if 0x100 <= off < 0x200 or 0x200 <= off < 0x300:
            base = 16 + ((off & 0x7F) * 8)
            for i in range(32):
                if value >> i & 1:
                    e = base + i
                    {2: core.enabled.add, 3: core.enabled.discard,
                     4: core.set_pending, 5: core.pending.discard}[off >> 7](e)
            return
        if 0x400 <= off < 0x4F0:
            exc = 16 + off - 0x400
            for i in range(4):
                core.prio[exc + i] = (value >> (8 * i)) & 0xF0
            return
        if off == 0xF00:  # STIR
            core.irq(value & 0x1FF)
            return
        if off == 0xD04:
            if value & (1 << 28):
                core.set_pending(PENDSV)
                # Two yields from the same thread-mode PC with no other exception in
                # between: the scheduler went straight back to the idle task.
                pc = self.m.pc()
                if not core.active and pc == core.last_yield_pc and not core.other_exceptions:
                    core.idle = True
                    self.m.stop_slice()  # best effort; otherwise noticed at slice end
                core.last_yield_pc = pc
                core.other_exceptions = 0
            if value & (1 << 27):
                core.pending.discard(PENDSV)
            if value & (1 << 26):
                core.set_pending(SYSTICK)
            if value & (1 << 25):
                core.pending.discard(SYSTICK)
            if value & (1 << 31):
                core.set_pending(NMI)
            return
        if off == 0xD08:
            core.vtor = value & 0xFFFFFF80
            return
        if off == 0xD0C:
            if value >> 16 == 0x05FA:
                core.prigroup = (value >> 8) & 7
                if value & 4:
                    self.m.request_reset("AIRCR.SYSRESETREQ")
            return
        if 0xD18 <= off < 0xD24:
            exc = 4 + off - 0xD18
            for i in range(4):
                core.prio[exc + i] = (value >> (8 * i)) & 0xF0
            return
        # Cache maintenance (0xF50-0xF78), CPACR, FPCCR, SCR, CCR etc.: just store.
        self.set(off, value)


class DWT(Peripheral):
    """Data Watchpoint and Trace: only CYCCNT is meaningful."""

    name = "DWT"
    size = 0x1000

    def read_reg(self, off):
        if off == 0x004:
            return self.m.cycles & 0xFFFFFFFF
        return self.get(off)
