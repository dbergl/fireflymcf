"""Which SRAM does the panel's C runtime actually INITIALISE? -- 2026-09-03.

The LCD firmware is an ordinary CMSIS/newlib image: its reset handler copies one ``.data``
region from flash and zeroes one ``.bss`` region, then calls ``SystemInit`` and ``main``. Both
regions are bounded by literals in the pool immediately after the loops, so the bounds are
**derivable from the bytes** rather than assumed -- which matters, because the answer is that
the startup covers far less RAM than the panel uses.

On 13.6 the two loops cover ``0x20000000-0x2000021C`` (``.data``) and ``0x20000228-0x2000AD98``
(``.bss``), against an initial stack pointer of ``0x20050000``. So **276 KB of SRAM above
``_ebss`` is never initialised by the C runtime**, and that tail is where GuiCode's working area
lives: on all three corpus builds *every* widget slot whose pointer lands in GuiCode RAM is
outside both initialised regions (13.6 664/664, 11.23 561/561, 14.7 504/504). All three builds
carry the same startup and the same bounds.

Two things do live in that tail and are not a contradiction: the **stack**, which occupies its
top and is reached through ``sp`` rather than any literal (GuiCode's highest referenced address
is ``0x2003B12A``, leaving ~84 KB of headroom below ``0x20050000``); and possibly the **heap**,
since newlib grows it upward from ``_ebss``. Whether GuiCode's cells are statically linked there
or handed out by the allocator is not settled here, and it does not need to be: ``malloc`` does
not zero its blocks -- only ``calloc`` does -- so on either layout a cell no instruction writes
has no value established for it.

**Why this matters, and what it explains.** A cell in the unmanaged tail holds a defined value
only if some routine explicitly writes it. GuiCode does exactly that for the cells it owns --
654 individually-addressed literals, including a straight-line builder at ``0x24F900`` that sets
88 of them -- but for a cell that no instruction reaches, nothing establishes a value at all. The
LCD firmware does not fill the gap: its **only** fixed-address reference into the whole 276 KB
tail is the ``_ebss`` bound in this very pool, i.e. the marker recording where it stopped.

On an STM32F7 such a cell keeps its contents across a **warm** reset -- a software reset or NRST,
which is how a firmware update ends -- and takes the die's own power-up bias pattern on a **cold**
start. That is the mechanism behind the `Absorb`/`Float` gate on slot 23 (``GUICODE.md``
Finding 63): its register ``0x20039999`` is in the unmanaged tail, is written by no instruction in
either image, and sits in a one-byte hole between two neighbours that GuiCode *does* set. It also
gives a concrete reason for the vendor's standing instruction to power-cycle after a firmware
update, rather than relying on the reset the update itself performs.

This module answers `is_initialised(addr)` from the image, so `slotsource` can separate the
slots it merely cannot find a writer for from the ones the runtime provably never establishes.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from . import memo

LCD_BASE = 0x00210000
_WINDOW = 0x80          # bytes of reset handler to decode; the loops fit in ~0x30
_MAX_INIT_GAP = 0x40    # tolerated alignment gap between _edata and _sbss


@dataclass(frozen=True)
class RamInit:
    """The regions the LCD firmware's startup establishes, derived from its reset handler."""

    reset_handler: int
    stack_top: int
    data_start: int
    data_end: int
    data_source: int
    bss_start: int
    bss_end: int

    @property
    def managed_end(self) -> int:
        """First address above everything the C runtime initialises."""
        return self.bss_end

    @property
    def unmanaged(self) -> tuple:
        """The SRAM tail the startup never writes: (start, end)."""
        return (self.bss_end, self.stack_top)

    def is_initialised(self, addr: int) -> bool:
        """True if the C runtime establishes this address at every reset."""
        return (self.data_start <= addr < self.data_end) or (self.bss_start <= addr < self.bss_end)


def _lcd_body(data: bytes) -> bytes:
    from . import container

    for section in container.parse_container(data).sections:
        if "LCD Firmware" in (section.name or ""):
            return data[section.offset:section.offset + section.length]
    raise ValueError("no LCD Firmware section")


def _pool_literals(body: bytes, start: int) -> list:
    """Every pc-relative literal VALUE loaded in the window starting at `start` (an address)."""
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB

    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    off = start - LCD_BASE
    out = []
    for insn in md.disasm(body[off:off + _WINDOW], start):
        if not insn.mnemonic.startswith("ldr") or "[pc" not in insn.op_str:
            continue
        disp = insn.op_str.split("#")[-1].rstrip("]")
        pool = ((insn.address + 4) & ~3) + int(disp, 0)
        fo = pool - LCD_BASE
        if 0 <= fo <= len(body) - 4:
            out.append(struct.unpack_from("<I", body, fo)[0])
    return out


@memo.content_cached()
def ram_init(data: bytes) -> RamInit:
    """Derive the startup's `.data`/`.bss` bounds from the LCD firmware's reset handler.

    Raises `ValueError` on a build whose startup does not match the single-region CMSIS shape,
    rather than reporting bounds it has not actually identified.
    """
    body = _lcd_body(data)
    stack_top, reset = struct.unpack_from("<II", body, 0)
    reset &= ~1
    if not (LCD_BASE <= reset < LCD_BASE + len(body)):
        raise ValueError(f"reset handler {reset:#010x} outside the LCD Firmware section")

    lits = _pool_literals(body, reset)
    # `stack_top` itself is loaded here too (the handler sets sp from the same pool); it is a
    # bound of the RAM, not of an initialised region, so it is excluded.
    ram = sorted({v for v in lits if 0x20000000 <= v < stack_top})
    flash = sorted({v for v in lits if LCD_BASE <= v < LCD_BASE + len(body)})
    if len(ram) != 4 or len(flash) != 1:
        raise ValueError(
            f"unrecognised startup shape: {len(ram)} RAM bounds, {len(flash)} flash sources"
        )

    data_start, data_end, bss_start, bss_end = ram
    if not 0 <= bss_start - data_end <= _MAX_INIT_GAP:
        raise ValueError(
            f"`.data`/`.bss` are not adjacent ({data_end:#010x} -> {bss_start:#010x}); "
            "the four RAM literals may not be the bounds this code assumes"
        )
    return RamInit(
        reset_handler=reset,
        stack_top=stack_top,
        data_start=data_start,
        data_end=data_end,
        data_source=flash[0],
        bss_start=bss_start,
        bss_end=bss_end,
    )


def is_initialised(data: bytes, addr: int) -> bool:
    """True if the LCD firmware's startup establishes `addr` at every reset."""
    return ram_init(data).is_initialised(addr)
