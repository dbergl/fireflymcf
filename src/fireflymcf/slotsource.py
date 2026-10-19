"""How is a widget value slot DRIVEN? -- the classifier, 2026-09-03.

A widget's `value_slot` is resolved through the pointer table at GuiData `+0x44BC8`
(`MCF-FORMAT.md` §5.4b, `layout.variable_register`) to an SRAM address, and the layout engine
reads that address. **What writes it is a separate question, and for a third of the slots this
project cannot answer it.** This module makes that answerable per slot instead of leaving it as
folklore, and makes the unexplained set countable so it can be attacked as a class.

The classes, and what each is evidence of:

``config``
    The address is one of `config.config_offsets`' registers -- written by the config-payload
    unpack (`GUICODE.md` Findings 19/20). `proven-from-code`.
``written``
    A GuiCode instruction stores to the address. The address must appear as a pool literal and at
    least one `str*` must target it. `proven-from-code`.
``read-only``
    The address appears as a GuiCode pool literal but only `ldr*` reach it: code observes the slot
    and never sets it, so its writer is elsewhere.
``unreferenced``
    **No instruction reaches the address at all.** On 13.6 this is **210** of the 664 slots whose
    pointer lands in GuiCode RAM, and it is the open class. (A bare literal-presence count gives
    209: one slot's address appears as a pool word but no access to it resolves. This module
    counts *resolved accesses*, which is the stricter and more useful measure.) It is NOT a type class: the
    unreferenced set spans type bytes 0, 1, 3, 4, 6, 9 and 10 in roughly the proportions the
    referenced set does.

**Why "unreferenced" is a real finding and not a gap in this scan.** The scan resolves
pc-relative literals, `movw`/`movs` immediates and all three addressing forms, and it is validated
two ways: known-written registers come back `written` (the option registers carry 4-7 references
each), and Ghidra's own reference database agrees on the negative for the case that motivated this
module (`ghidra/find_addr_xrefs.py`: 0 xrefs for `0x20039999`, 3 for its neighbour `0x20039997`,
6 for `0x20039CF8`).

**What an `unreferenced` slot is NOT.** It is not receive-fed. The receive path writes the separate
status-slot area (`0x20007FC8 + idx*2`, the index-split rule), and a received value only reaches a
widget when one of the 46 hard-coded GuiCode bridge blocks copies it to a cell -- a copy that needs
the destination as a literal, which by definition an unreferenced slot does not have. Tested on the
coach and refuted there too (`BUS-BASELINE.md` PC.54 / `GUICODE.md` Finding 63): injecting the DGN
named by the same-numbered receive-filter entry changed nothing. The equal indices are a
coincidence of two index spaces.

The worked case is the `INVERTER / CHARGER SETTINGS` page's `Absorb`/`Float` rows, gated by a
type-6 switch on slot **23**, which this module classifies `unreferenced`.

**`unreferenced` means unwritten, not merely unnamed (2026-09-04).** This module sees only stores
that name their address; a store through a pointer parked in a struct, on the stack, in a
parameter or produced by a table lookup would be invisible here. `indirectwrites` enumerates every
one of those forms in both images and `GUICODE.md` Finding 67 reads each hit: all resolve to fixed
targets, none index-selected, so nothing writes this class by a route this scan cannot see either.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import config, guidata, layout, memo, raminit, theme

SRAM_LOW = 0x20030000
SRAM_HIGH = 0x20040000

_LIT = re.compile(r"^(\w+), \[pc, #(0x[0-9a-f]+|\d+)\]$")
_IMM = re.compile(r"^(\w+), #(0x[0-9a-f]+|\d+)$")
_M_IMM = re.compile(r"^(\w+), \[(\w+), #(-?0x[0-9a-f]+|-?\d+)\]$")
_M_REG = re.compile(r"^(\w+), \[(\w+), (\w+)(?:, lsl #(\d+))?\]$")
_M_BARE = re.compile(r"^(\w+), \[(\w+)\]$")
_MEM_OPS = {
    "strb", "ldrb", "strh", "ldrh", "str", "ldr",
    "strb.w", "ldrb.w", "strh.w", "ldrh.w", "str.w", "ldr.w",
}


@dataclass
class SlotSource:
    """One widget slot and how it is driven."""

    slot: int
    address: int
    vtype: int
    kind: str
    reads: list = field(default_factory=list)
    writes: list = field(default_factory=list)

    @property
    def explained(self) -> bool:
        return self.kind in ("config", "written")


def _disassemble(body: bytes, base: int):
    """Every instruction, restarting past bytes capstone cannot decode.

    Without the restart a single pool island truncates the pass -- one early attempt at this
    stopped after 1,521 of 40,759 instructions and produced a confident, empty answer.
    """
    try:
        from capstone import CS_ARCH_ARM, CS_MODE_THUMB, Cs
    except ImportError as exc:  # pragma: no cover - environment guard
        raise theme.CapstoneUnavailable("capstone is required to classify slot sources") from exc
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    out, off = [], 0
    while off < len(body) - 1:
        moved = False
        for insn in md.disasm(body[off:], base + off):
            out.append(insn)
            moved = True
            off = insn.address - base + insn.size
        if not moved:
            off += 2
    return out


@memo.content_cached(copy=None)
def _address_refs(body: bytes, base: int) -> tuple:
    """`({addr: [read sites]}, {addr: [write sites]})` for SRAM addresses the code reaches."""
    reads: dict = {}
    writes: dict = {}
    regs: dict = {}
    for insn in _disassemble(body, base):
        mnemonic, ops = insn.mnemonic, insn.op_str
        m = _LIT.match(ops)
        if mnemonic.startswith("ldr") and m:
            at = ((insn.address + 4) & ~3) + int(m.group(2), 0) - base
            if 0 <= at < len(body) - 3:
                regs[m.group(1)] = int.from_bytes(body[at:at + 4], "little")
            continue
        m = _IMM.match(ops)
        if mnemonic in ("movw", "movs", "mov", "mov.w") and m:
            regs[m.group(1)] = int(m.group(2), 0)
            continue
        if mnemonic in _MEM_OPS:
            addr = rt = None
            m = _M_IMM.match(ops)
            if m:
                rt = m.group(1)
                if m.group(2) in regs:
                    addr = regs[m.group(2)] + int(m.group(3), 0)
            else:
                m = _M_REG.match(ops)
                if m:
                    rt = m.group(1)
                    if m.group(2) in regs and m.group(3) in regs:
                        addr = regs[m.group(2)] + (regs[m.group(3)] << int(m.group(4) or 0))
                else:
                    m = _M_BARE.match(ops)
                    if m:
                        rt = m.group(1)
                        if m.group(2) in regs:
                            addr = regs[m.group(2)]
            if addr is not None and SRAM_LOW <= addr < SRAM_HIGH:
                (reads if mnemonic.startswith("ldr") else writes).setdefault(addr, []).append(
                    insn.address)
            if mnemonic.startswith("ldr") and rt:
                regs.pop(rt, None)
            continue
        if ops:
            dst = ops.split(",")[0].strip()
            if dst in regs:
                regs.pop(dst, None)
    return reads, writes


def _guicode_body(data: bytes) -> tuple:
    from . import container

    for section in container.parse_container(data).sections:
        if "GuiCode" in (section.name or ""):
            return data[section.offset:section.offset + section.length], 0x240000
    raise ValueError("no GuiCode section")


@memo.content_cached()
def slot_sources(data: bytes) -> list:
    """Classify every widget slot whose pointer lands in GuiCode's SRAM working area."""
    body, base = _guicode_body(data)
    reads, writes = _address_refs(body, base)
    cfg = set(config.config_offsets(data).values())
    dptr, type_off = guidata.find_variable_tables(data)
    gs, _ = guidata.guidata_extent(data)
    count = (guidata.object_tables(data)[1] and len(guidata.object_tables(data)[1])) or 0
    out = []
    for slot in range(count):
        addr, vtype = layout.variable_register(data, slot, dptr, type_off)
        if not (SRAM_LOW <= addr < SRAM_HIGH):
            continue
        r, w = reads.get(addr, []), writes.get(addr, [])
        if addr in cfg:
            kind = "config"
        elif w:
            kind = "written"
        elif r:
            kind = "read-only"
        else:
            kind = "unreferenced"
        out.append(SlotSource(slot, addr, vtype, kind, r, w))
    return out


def slot_source(data: bytes, slot: int):
    """The classification for one slot, or None if it does not point into GuiCode RAM."""
    for entry in slot_sources(data):
        if entry.slot == slot:
            return entry
    return None


def unexplained(data: bytes) -> list:
    """The open class: slots no GuiCode instruction reaches at all."""
    return [s for s in slot_sources(data) if s.kind == "unreferenced"]


def never_established(data: bytes) -> list:
    """Slots that no instruction writes AND the C runtime does not initialise.

    This is the stronger statement `unexplained` cannot make on its own. `unexplained` says only
    that this scan found no writer; adding `raminit` says the startup does not establish the cell
    either, so on a cold boot it holds the SRAM's own power-up bias and on a warm reset it holds
    whatever the previous run left. See `raminit` for the derivation and the stack/heap caveats.

    On all three corpus builds every GuiCode-RAM slot is outside the initialised regions, so this
    returns the whole `unreferenced` set -- but it is computed, not assumed, and would shrink on a
    build that linked those cells into `.bss`.
    """
    init = raminit.ram_init(data)
    return [s for s in unexplained(data) if not init.is_initialised(s.address)]
