"""STM32F7-based Spyder Controls 7in Lite LCD board (CPU at 160 MHz)."""
import ctypes
import logging
import struct

from unicorn import (
    UC_ARCH_ARM, UC_HOOK_MEM_FETCH_UNMAPPED, UC_HOOK_MEM_READ_UNMAPPED,
    UC_HOOK_MEM_WRITE_UNMAPPED, UC_MODE_MCLASS, UC_MODE_THUMB, UC_PROT_ALL, Uc, UcError,
)
from capstone import CS_ARCH_ARM, CS_MODE_MCLASS, CS_MODE_THUMB, Cs
from unicorn.arm_const import (
    UC_ARM_REG_BASEPRI, UC_ARM_REG_FAULTMASK, UC_ARM_REG_MSP, UC_ARM_REG_PC, UC_ARM_REG_PRIMASK,
    UC_ARM_REG_R0, UC_ARM_REG_SP, UC_ARM_REG_XPSR, UC_CPU_ARM_CORTEX_M7,
)

from . import fastreg, mcf, native
from .canbus import CanBus
from .cortexm import DWT, PENDSV, SCS, Core, CoreFault
from .periph import stm32f7
from .periph.base import Bus

log = logging.getLogger("machine")

KB, MB = 1024, 1024 * 1024

FLASH = 0x08000000
FLASH_ITCM = 0x00200000
FLASH_SIZE = 1 * MB
SYSMEM = 0x1FF00000
SRAM = 0x20000000
SRAM_SIZE = 320 * KB
BKPSRAM = 0x40024000
QSPI_MEM = 0x90000000
SDRAM1 = 0xC0000000
SDRAM2 = 0xD0000000

EARLY_STOP_COST = 50
UNMASK_REG = {"primask": UC_ARM_REG_PRIMASK, "basepri": UC_ARM_REG_BASEPRI,
              "faultmask": UC_ARM_REG_FAULTMASK}
INTR_SWI = 2
INTR_EXCEPTION_EXIT = 8

# Peripheral pages backed by plain RAM: no behaviour worth modelling, and the
# firmware hits them in hot loops (the watchdog is kicked inside busy-waits).
RAM_BACKED = [
    (0x40003000, 0x400),  # IWDG
    (BKPSRAM, 0x1000),    # Backup SRAM
]


# Peripherals mapped with their own write callback (``Peripheral.fast_write``): the
# GUI's anti-aliasing programs DMA2D for one pixel at a time, ~14 register writes
# each, ~330,000 writes for one page change.
FAST_MMIO = {"DMA2D": (0x4002B000, 0xC00)}


def _mmio_windows(start, end):
    """Split [start, end) around RAM_BACKED and FAST_MMIO pages."""
    out, cur = [], start
    for base, size in sorted(RAM_BACKED + list(FAST_MMIO.values())):
        if start <= base < end:
            if base > cur:
                out.append((cur, base - cur))
            cur = base + size
    if cur < end:
        out.append((cur, end - cur))
    return out


MMIO = _mmio_windows(0x40000000, 0x40080000) + [
    (0x50000000, 0x00070000),
    (0xA0000000, 0x00002000),
    (0xE0000000, 0x00100000),
]


class Machine:
    def __init__(self, slice_cycles=20000):
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        self.uc.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M7)
        self.cycles = 0
        self.idle_cycles = 0
        self._timed = None
        self.components = []
        self.guidata = b""
        self.mcf_data = None
        self._resched = True
        self._deadline = None
        self._skip_store = None
        self._hook_error = None
        self._unmask_sites = {}
        self.in_emu = False
        self._store_cache = {}
        self._cs = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)
        self._cs.detail = True
        self.slice = slice_cycles
        self.stop_reason = None
        self.reset_reason = None
        self._buffers = []
        self._regions = []  # (start, end, memoryview) for every RAM mapping incl. aliases
        self._last_region = (0, 0, None)
        self._map_memory()
        self.core = Core(self)
        self.bus = Bus(self, stm32f7.PERIPHERAL_NAMES)
        self.bus.attach(SCS(self, 0xE000E000))
        self.bus.attach(DWT(self, 0xE0001000))
        self.cpu_hz = stm32f7.CPU_HZ
        self.periph = stm32f7.attach_all(self)
        self._map_fast_mmio()
        self.canbus = CanBus(self, self.periph["CAN1"])
        fastreg.hook_intr(self.uc, self._on_intr)
        self.slicer = native.slicer(self.uc)  # None: fall back to Unicorn's count limit
        self.uc.hook_add(UC_HOOK_MEM_READ_UNMAPPED | UC_HOOK_MEM_WRITE_UNMAPPED
                         | UC_HOOK_MEM_FETCH_UNMAPPED, self._on_unmapped)

    # -- memory ----------------------------------------------------------
    def _ram(self, addr, size, *aliases):
        buf = (ctypes.c_ubyte * size)()
        self._buffers.append(buf)
        view = memoryview(buf).cast("B")
        for a in (addr,) + aliases:
            self.uc.mem_map_ptr(a, size, UC_PROT_ALL, ctypes.addressof(buf))
            self._regions.append((a, a + size, view))
        return buf

    def mem(self, addr, size):
        """Writable zero-copy memoryview of guest RAM, or None if not plain RAM."""
        start, end, view = self._last_region
        if start <= addr and addr + size <= end:
            return view[addr - start:addr - start + size]
        for region in self._regions:
            start, end, view = region
            if start <= addr and addr + size <= end:
                self._last_region = region
                return view[addr - start:addr - start + size]
        return None

    def _map_memory(self):
        self._ram(0x00000000, 16 * KB)                  # ITCM RAM
        self.flash = self._ram(FLASH, FLASH_SIZE, FLASH_ITCM)
        self._ram(SYSMEM, 64 * KB, 0x00100000)          # system memory / OTP / IDs
        self._ram(SRAM, SRAM_SIZE)                      # DTCM + SRAM1 + SRAM2
        for base, size in RAM_BACKED:
            self._ram(base, size)
        self.qspi = self._ram(QSPI_MEM, 16 * MB)
        self._ram(SDRAM1, 16 * MB)
        self._ram(SDRAM2, 16 * MB)
        # Device identity: flash size (KiB) and a fixed 96-bit unique ID.
        self.uc.mem_write(0x1FF0F442, struct.pack("<H", FLASH_SIZE // KB))
        self.uc.mem_write(0x1FF0F420, bytes.fromhex("464952454600000031333336"))
        for base, size in MMIO:
            fastreg.mmio_map(self.uc, base, size, self._mmio_read, self._mmio_write)
        for base, size in FAST_MMIO.values():   # until the peripherals exist
            fastreg.mmio_map(self.uc, base, size, self._mmio_read, self._mmio_write)

    def _map_fast_mmio(self):
        """Remap FAST_MMIO with the peripheral's ``fast_write`` as the write callback
        (no bus dispatch: such writes are not counted by ``bus.stats``)."""
        for name, (base, size) in FAST_MMIO.items():
            self.uc.mem_unmap(base, size)
            fastreg.mmio_map_offsets(self.uc, base, size, self._mmio_read,
                                     self.periph[name].fast_write)

    def _mmio_read(self, addr, size):
        try:
            return self.bus.read(addr, size)
        except BaseException as e:
            self._hook_failed(e)
            return 0

    def _mmio_write(self, addr, size, value):
        try:
            self.bus.write(addr, size, value)
        except BaseException as e:
            self._hook_failed(e)

    def _hook_failed(self, exc):
        """An exception escaped a hook: stop and re-raise it from run()."""
        if self._hook_error is None:
            self._hook_error = exc
        self._early = True
        self.uc.emu_stop()

    # -- firmware --------------------------------------------------------
    def load_mcf(self, path):
        title, comps = mcf.load(path)
        self.components = comps
        with open(path, "rb") as f:
            self.mcf_data = f.read()  # whole package, for the G12 model
        log.info("loading %s (%s)", path, title)
        entry = None
        code_regions = []
        for c in comps:
            addr = c.load_addr
            if FLASH_ITCM <= addr < FLASH_ITCM + FLASH_SIZE:
                addr = addr - FLASH_ITCM + FLASH
            try:
                self.uc.mem_write(addr, c.data)
            except UcError:
                log.info("  %-20s -> skipped (0x%08x is not LCD memory)", c.name, c.load_addr)
                continue
            log.info("  %-20s -> 0x%08x (%d bytes)", c.name, addr, len(c.data))
            if addr == QSPI_MEM:
                self.guidata = c.data
            if c.is_code or c.gui_exports:
                code_regions.append((c.load_addr, len(c.data)))  # execution address
            if c.is_code and entry is None:
                entry = c.load_addr
        if entry is None:
            raise ValueError("no code component found")
        self.install_unmask_patches(code_regions)
        self.reset_vector_base = entry
        return entry

    def install_unmask_patches(self, regions):
        """Patch every interrupt-unmasking instruction into an SVC trap.

        FreeRTOS pends PendSV inside critical sections and relies on it being
        taken the moment interrupts are unmasked.  Code hooks on those sites
        would make Unicorn run its hook dispatcher on every instruction (4x
        slower), so instead each `cpsie i/f` becomes `svc #0xFF` and each
        `msr primask/basepri/basepri_max/faultmask, rN` becomes `svc #0xFF; nop`.
        The interrupt hook applies the original register write and delivers any
        exception it unmasked.  Patches are in emulated memory only.
        """
        sysm = {0x10: "primask", 0x11: "basepri", 0x12: "basepri_max", 0x13: "faultmask"}
        for base, size in regions:
            code = bytes(self.uc.mem_read(base, size))
            for off in range(0, len(code) - 3, 2):
                hw0, hw1 = struct.unpack_from("<HH", code, off)
                site = base + off
                if hw0 in (0xB662, 0xB661):
                    action, length = ("cpsie", "primask" if hw0 & 2 else "faultmask"), 2
                elif (hw0 & 0xFFF0 == 0xF380 and hw0 & 0xF <= 12
                      and hw1 in (0x8810, 0x8811, 0x8812, 0x8813)):
                    if self._after_cpsid(code, off):
                        continue  # PRIMASK is set: this write cannot unmask anything
                    action, length = ("msr", sysm[hw1 & 0xFF], hw0 & 0xF), 4
                else:
                    continue
                ins = next(self._cs.disasm(code[off:off + length], site, 1), None)
                if ins is None or ins.mnemonic not in ("cpsie", "msr"):
                    continue  # not an instruction boundary we can trust
                self.uc.mem_write(site, b"\xff\xdf" + (b"\x00\xbf" if length == 4 else b""))
                self._unmask_sites[site] = action
        log.info("unmask patches: %d sites", len(self._unmask_sites))

    @staticmethod
    def _after_cpsid(code, off):
        """True if the instruction before ``off`` is `cpsid i`.

        FreeRTOS's CM7 port raises BASEPRI as `cpsid i; msr basepri, rN; dsb;
        isb; cpsie i`; only the trailing cpsie needs the trap.  The halfword
        before must not be the second half of a 32-bit instruction.
        """
        if off < 4:
            return False
        prev, before = struct.unpack_from("<HH", code, off - 4)[::-1]
        return prev == 0xB672 and before >> 11 not in (0b11101, 0b11110, 0b11111)

    def _unmask_trap(self, site):
        """Perform the patched instruction at ``site``; True if it was one."""
        action = self._unmask_sites.get(site)
        if action is None:
            return False
        regs = self.core.regs
        if action[0] == "cpsie":
            regs.write(UNMASK_REG[action[1]], 0)
        else:
            _, name, rn = action
            value = regs.read(UC_ARM_REG_R0 + rn)  # r0-r12 have consecutive Unicorn ids
            if name == "basepri_max":
                cur = regs.read(UC_ARM_REG_BASEPRI) & 0xFF
                if not (value & 0xFF and (cur == 0 or (value & 0xFF) < cur)):
                    return True
                name = "basepri"
            regs.write(UNMASK_REG[name], value & (0xFF if name == "basepri" else 1))
        self.core.take_pending()
        return True

    def component(self, name):
        return next((c for c in self.components if c.name == name), None)

    def boot(self, vector_base=None):
        vb = self.reset_vector_base if vector_base is None else vector_base
        sp, pc = struct.unpack("<II", self.uc.mem_read(vb, 8))
        self.core.vtor = vb
        self.uc.reg_write(UC_ARM_REG_MSP, sp)
        self.uc.reg_write(UC_ARM_REG_SP, sp)
        self.uc.reg_write(UC_ARM_REG_PC, pc | 1)
        log.info("boot: vtor=0x%08x sp=0x%08x pc=0x%08x", vb, sp, pc)

    # -- hooks -----------------------------------------------------------
    def pc(self):
        return self.core.read_pc()

    def _on_intr(self, intno):
        # SVC and exception return are handled inside the hook: Unicorn resumes
        # from whatever PC/registers the hook leaves, so no slice stop is needed.
        uc = self.uc
        try:
            if intno == INTR_SWI:
                pc = self.core.read_pc()
                if self._unmask_trap(pc - 2):
                    return
                self.core.svc_pc = pc
                self.core.take_pending()
                return
            if intno == INTR_EXCEPTION_EXIT:
                self.core.leave(self.core.read_pc() | 1)
                self.core.take_pending()  # tail-chain
                return
            self.stop_reason = f"cpu exception intno={intno} at pc=0x{self.pc():08x}"
        except CoreFault as e:
            self.stop_reason = str(e)
        except BaseException as e:
            self._hook_failed(e)
            return
        self._early = True
        uc.emu_stop()

    def _on_unmapped(self, uc, access, addr, size, value, user):
        kind = {19: "read", 20: "write", 21: "fetch"}.get(access, str(access))
        self.stop_reason = f"unmapped {kind} 0x{addr:08x} (size {size}) at pc=0x{self.pc():08x}"
        return False

    # -- scheduling ------------------------------------------------------
    def reschedule(self):
        """A timer was reprogrammed; end the current slice early."""
        self._resched = True

    def stop_slice(self):
        """End the current slice from inside an MMIO *write* callback.

        Unicorn leaves PC on the store when stopped from its MMIO callback and
        replays it on resume, so the stop is only taken for a plain store (no
        writeback, outside an IT block) which the run loop then steps over.
        Returns False if the stop could not be taken safely.
        """
        pc = self.pc()
        size = self._plain_store_size(pc)
        if not size or self.uc.reg_read(UC_ARM_REG_XPSR) & 0x0600FC00:
            return False
        self._skip_store = (pc, size)
        self._early = True
        self.uc.emu_stop()
        return True

    def _plain_store_size(self, pc):
        size = self._store_cache.get(pc)
        if size is None:
            size = 0
            for ins in self._cs.disasm(bytes(self.uc.mem_read(pc, 4)), pc, 1):
                if ins.mnemonic in ("str", "strh", "strb", "str.w", "strh.w", "strb.w") \
                        and not ins.writeback:
                    size = ins.size
            self._store_cache[pc] = size
        return size

    def poll_wait(self):
        """Firmware is spinning on a status register: like idle, skip to the next event.

        Stopping from an MMIO read replays the load on resume, which is harmless
        for the side-effect-free registers that can trigger this.
        """
        self.core.idle = True
        self._early = True
        self.uc.emu_stop()

    def request_reset(self, why):
        self.reset_reason = why
        self._early = True
        self.uc.emu_stop()

    def next_event(self):
        """Cycles until the next timer event (None if none).

        Peripherals are only polled when the cached deadline has passed or one of
        them called reschedule(); polling also fires any events that are due.
        """
        now = self.cycles
        if self._resched or (self._deadline is not None and now >= self._deadline):
            self._resched = False
            deltas = [d for d in (p.next_event() for p in self.timed) if d is not None]
            self._deadline = now + min(deltas) if deltas else None
        return None if self._deadline is None else max(1, self._deadline - now)

    @property
    def timed(self):
        if self._timed is None:
            self._timed = [self.bus.pages[0xE000E000 >> Bus.PAGE], self.canbus] + [
                p for p in self.periph.values() if hasattr(p, "next_event")]
        return self._timed

    def _service(self):
        try:
            self.core.service()
        except CoreFault as e:
            self.stop_reason = str(e)

    def run(self, max_cycles=None):
        uc, slicer = self.uc, self.slicer
        limit = None if max_cycles is None else self.cycles + max_cycles
        self._service()
        while self.stop_reason is None and self.reset_reason is None:
            if limit is not None and self.cycles >= limit:
                break
            n = self.slice
            ev = self.next_event()
            if ev is not None:
                n = max(1, min(n, ev))
            if limit is not None:
                n = max(1, min(n, limit - self.cycles))
            self._early = False
            pc = self.core.read_pc()
            self.in_emu = True
            try:
                if slicer is not None:
                    slicer.arm(n)
                    uc.emu_start(pc | 1, 0xFFFFFFFF)
                else:
                    uc.emu_start(pc | 1, 0xFFFFFFFF, count=n)
            except UcError as e:
                if self.stop_reason is None:
                    self.stop_reason = f"{e} at pc=0x{self.pc():08x}"
            finally:
                self.in_emu = False
            if self._hook_error is not None:
                err, self._hook_error = self._hook_error, None
                raise err
            if self._skip_store is not None:
                spc, ssize = self._skip_store
                self._skip_store = None
                if (uc.reg_read(UC_ARM_REG_PC) & ~1) == spc:
                    uc.reg_write(UC_ARM_REG_PC, (spc + ssize) | 1)
            if slicer is not None:
                self.cycles += slicer.used
            else:
                # Unicorn does not report how far an early-stopped slice got; charge
                # a nominal cost so frequent SVC/exception exits don't eat the budget.
                self.cycles += min(n, EARLY_STOP_COST) if self._early else n
            # The RTOS idle task spins yielding via PENDSVSET.  Once it is detected and
            # nothing but PendSV is pending, skip to the next timer event as WFI would.
            if self.core.idle and self.core.deliverable() <= {PENDSV}:
                ev = self.next_event()
                if ev is not None:
                    skip = ev if limit is None else min(ev, limit - self.cycles)
                    self.cycles += max(0, skip)
                    self.idle_cycles += max(0, skip)
            self.core.idle = False
            self.next_event()  # fire anything now due
            self._service()
        return self.stop_reason or self.reset_reason
