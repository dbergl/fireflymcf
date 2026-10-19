"""The mode-7 chrome palette — the colours the firmware theme writes into SRAM at boot.

`docs/GUICODE.md` Finding 18 and its 2026-08-15 colour-dig correction. A chrome fill
(header, nav rail, tab, button, body) is a **mode-7 colour-table reference**: the widget
record carries a `u16` index, and the mode-7 table (`DAT_002535B4`, GuiData-relative
`0x44BC8` in 13.6 — the very same array as the value-slot variable data-pointer table,
`records.WRecord.value_slot`) resolves that index to a **32-bit SRAM cell address**, not a
colour. The colour lives at that cell, written once at boot by the firmware theme init. So a
mode-7 fill is a runtime variable — which is why it never turned up as a static value in
GuiData, and why the render kept it a heuristic (open thread 1).

The colour-dig found the writer: a straight-line block inside **`FUN_00247bf8`** (GuiCode,
13.6 `~0x00247F44`–`0x0024ADxx`) stores **hardcoded RGB888 constants** (`0x00RRGGBB`) into
those SRAM cells. The store shapes it uses, all recovered here:

    direct      ldr r3,[=cell] ; ldr r2,[=const]        ; str r2,[r3]   (idx 8  = 0x00272862)
    add-chain   ldr r3,[=cell] ; add.w r2,r2,#i ; addw r2,r2,#j ; str    (idx 4  = 0x00BE1E2D)
    immediate   ldr r2,[=cell] ; mov.w/movw/mvn r1,#imm ; str r1,[r2]    (0x00FFFFFF via mvn)

The add-chain builds on the register value the *previous* store used, so recovery needs the
running register state across the block, not a per-store peephole — the block is emulated
linearly. Recognizable constants in the block (`0x00FFFFFF` = `mvn r1,#0xff000000`,
`0x00141414`, `0x00AA00AA` = `mov.w r1,#0xaa00aa`, `0x0000AEEF` = `movw r1,#0xaeef`) pin the
format as **RGB888**.

STATUS: the *mechanism* (mode-7 table → SRAM cell → theme-init constant) is `proven-from-code`
(Finding 18). This module's decoder is a faithful replay of that block's register arithmetic,
validated on 13.6 against the Ghidra-traced constants (`extractions/color_*.txt`): idx 3 =
`0x0015183A`, idx 4 = `0x00BE1E2D` (the chrome red, the two-step add-chain), idx 8 =
`0x00272862`, plus the direct/immediate/mvn/movw forms — every one exact.

How the theme-init is located, build-generally (no hardcoded 13.6 addresses):

  * The target set is every mode-7 cell — a type-10 entry of the variable table
    (`guidata.find_variable_tables`; type 10 is a colour cell per `FUN_00251E38` / the
    dig). Their values are 32-bit SRAM addresses.
  * The GuiCode section is sliced from the container by name; its mapped base is read from the
    section descriptor's tag-`0x40` flash-destination entry (`0x00240000` on all three builds).
  * The whole section is disassembled (capstone, Thumb) and emulated linearly, tracking only
    the constant-forming opcodes (PC-relative `ldr`, `mov`/`movw`/`movt`/`mvn` immediate,
    `add`/`addw`/`sub`/`subw` immediate) and `str`. A value is recorded **only** when a store's
    base register holds an *exact* target cell address (obtainable solely from a correct
    PC-relative literal load of that address) and the stored value is a valid RGB888
    (`<= 0x00FFFFFF`). Register state is cleared at every control-flow instruction and at every
    data island (where the linear decode desyncs and is resynced on the next 2-byte boundary),
    so the add-chain carry only ever spans the contiguous, branch-free theme-init block — which
    carries no embedded data, so it decodes identically to a clean from-boundary disassembly.

A cell whose store cannot be recovered (only ever written a *runtime* value — e.g. copied from
another cell, or `str`ing a register this decoder does not track) is a **named residual**: it is
omitted from the result, never guessed, so the caller keeps its heuristic (the cardinal rule).
"""
from __future__ import annotations

import functools
import re
import struct
from typing import Dict, Tuple

from . import container, guidata, memo


def _content_cached(fn):
    """`memo.content_cached` for a `f(data) -> dict`: memoised on the CONTENT of `data` (never
    its id -- an id-keyed cache went stale after an in-place bytearray edit), bounded, and
    copied out so a caller mutating the result cannot corrupt the cache. The disassembly these
    functions do (0.7-2 s) dwarfs the key cost, and the GuiCode section they read is never
    touched by the same-size/relocating GuiData writers, so hits are the norm across a
    multi-page render or a test run."""
    return memo.content_cached(fn, copy=dict)


def _content_cached2(fn):
    """`_content_cached` for a `f(data, key) -> dict-of-dicts`: memoised on (content, key) and
    deep-copied out, so a caller mutating the result cannot corrupt the cache."""
    return memo.content_cached(fn, copy=lambda d: {k: dict(v) for k, v in d.items()})


# `ldr rX, [pc, #±imm]` as capstone's `disasm_lite` prints it. The pointer scans below need
# only the literal's displacement, so they use the lite decoder (no `CsInsn`/detail objects,
# ~7x faster per instruction) and read it from `op_str`; the operand-object form this
# replaced was compared literal-for-literal against this one on all three builds.
_PCREL_LDR = re.compile(r"\[pc, #(-?)0x([0-9a-f]+)\]$")


@memo.content_cached(copy=list)
def _pcrel_literal_offsets(code: bytes, base: int) -> list[int]:
    """Section-relative offset of every literal word a PC-relative `ldr` in `code` loads,
    in instruction order (duplicates kept). A resyncing linear decode: capstone stops at a data
    island, and we resume on the next 2-byte boundary, like every other scan in this module.

    Memoised on the SECTION's bytes, not the file's: a compile calls this on every intermediate
    image, and a GuiData edit changes the file but not GuiCode, so only an op that writes
    GuiCode pays for a new disassembly (about 60 ms each, 286 times in one v50 compile)."""
    try:
        import capstone
    except ImportError as exc:  # pragma: no cover - capstone is installed in this repo
        raise CapstoneUnavailable("capstone is required to scan PC-relative loads") from exc
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    out: list[int] = []
    pos = 0
    limit = len(code) - 4
    while pos < len(code):
        decoded_end = pos
        for addr, size, mnemonic, op_str in md.disasm_lite(code[pos:], base + pos):
            decoded_end = addr - base + size
            if mnemonic in ("ldr", "ldr.w"):
                m = _PCREL_LDR.search(op_str)
                if m:
                    disp = int(m.group(2), 16)
                    if m.group(1):
                        disp = -disp
                    lit_off = (addr & ~3) + 4 + disp - base
                    if 0 <= lit_off <= limit:
                        out.append(lit_off)
        if decoded_end <= pos:
            decoded_end = pos + 2
        pos = decoded_end
    return out


# The mode-7 colour table is the value-slot variable data-pointer table; a colour cell is a
# type-10 entry of the parallel type array (`FUN_00251E38`; GUICODE Finding 18 colour-dig).
COLOUR_CELL_TYPE = 10

# Fallback mapped base of the GuiCode section. Read per-build from the section descriptor's
# tag-0x40 flash-destination entry; this constant is only the default when that read fails.
GUICODE_BASE_DEFAULT = 0x00240000
GUICODE_SECTION_MARKER = "GuiCode"

# The theme-init cells are SRAM addresses; the variable table's data pointers are each either
# 0 (an unused slot) or an address in this range.
SRAM_LOW = 0x20000000
SRAM_HIGH = 0x20080000

# The theme constants are RGB888, stored as 0x00RRGGBB: the top byte is always zero. Used both
# to decode the colour and, as the module docstring notes, to reject a decode that landed on
# non-colour data.
RGB888_MAX = 0x00FFFFFF

# Descriptor fields that carry the section's flash destination (see container.py's docstring):
# the u16 tagged-entry count at +0x46, the 8-byte tagged entries from +0x58, tag 0x40's u32.
_DESC_ENTRY_COUNT = 0x46
_DESC_ENTRIES = 0x58
_DESC_TAG_FLASH = 0x40


class CapstoneUnavailable(RuntimeError):
    """capstone is not importable, so the GuiCode cannot be disassembled."""


class PaletteError(ValueError):
    """A requested fill colour/index is not one the firmware palette can name."""


def guicode_section(data: bytes) -> container.Section:
    for section in container.parse_container(data).sections:
        if GUICODE_SECTION_MARKER in section.name:
            return section
    raise ValueError("no GuiCode section in this MCF")


# GuiData is mapped at this QSPI base; a GuiCode word in [BASE, BASE+guidata_len) is a pointer
# into GuiData (see guidata.OBJECT_POINTER_BASE).
_GUIDATA_MAP_BASE = 0x90000000


def _section_base(section: container.Section) -> int:
    """Mapped flash base of any section, from its descriptor's tag-0x40 entry (like
    `guicode_base`, but for an arbitrary section)."""
    entry = section.raw_entry
    if len(entry) >= _DESC_ENTRY_COUNT + 2:
        count = struct.unpack_from("<H", entry, _DESC_ENTRY_COUNT)[0]
        for k in range(count):
            at = _DESC_ENTRIES + k * 8
            if at + 8 > len(entry):
                break
            if struct.unpack_from("<H", entry, at)[0] == _DESC_TAG_FLASH:
                return struct.unpack_from("<I", entry, at + 4)[0]
    return GUICODE_BASE_DEFAULT


@memo.content_cached
def section_guidata_pointers(data: bytes, marker: str) -> list:
    """`[(literal_file_offset, target_rel), ...]` for every distinct literal word a PC-relative
    `ldr` in the section named `marker` loads whose value is a pointer into GuiData
    (`0x90000000 + rel`, `rel` inside GuiData). Distinct by literal position (two `ldr`s sharing
    a pool word yield one entry). `literal_file_offset` is the whole-MCF file offset of the word.

    Memoised on the file's content: the relocator's gates call this on every intermediate
    image, and a GuiData-only edit leaves the answer unchanged.

    Raises `CapstoneUnavailable` if capstone cannot disassemble the section.
    """
    section = next((s for s in container.parse_container(data).sections
                    if marker in s.name), None)
    if section is None:
        return []
    base = _section_base(section)
    code = data[section.offset:section.offset + section.length]
    gs, ge = guidata.guidata_extent(data)
    span = ge - gs

    found: Dict[int, int] = {}          # literal_file_offset -> target_rel
    for lit_off in _pcrel_literal_offsets(code, base):
        word = struct.unpack_from("<I", code, lit_off)[0]
        rel = word - _GUIDATA_MAP_BASE
        if 0 <= rel < span:
            found[section.offset + lit_off] = rel
    return sorted(found.items())


def guicode_guidata_pointers(data: bytes) -> list:
    """`[(literal_file_offset, target_rel), ...]` for GuiCode's absolute pointers into GuiData --
    `section_guidata_pointers(data, "GuiCode")`.

    These are the cross-section references the relocator must fix: GuiCode addresses several
    GuiData structures (the four pointer tables, plus a few object-data addresses) by absolute
    value, so any insertion that shifts those targets leaves the GuiCode literal stale unless it
    is relocated too. GuiCode sits before GuiData and never moves, so a caller patches each word
    in place. Raises `CapstoneUnavailable` (an insertion cannot be made runtime-safe without it).
    """
    return section_guidata_pointers(data, GUICODE_SECTION_MARKER)


# Back-compat alias: this was private until `config.py` became a second caller (2026-08-15).
_guicode_section = guicode_section


def guicode_base(section: container.Section) -> int:
    """Mapped base of the GuiCode section, from its descriptor's tag-0x40 flash destination.

    Falls back to `GUICODE_BASE_DEFAULT` if the descriptor does not carry a tag-0x40 entry.
    """
    entry = section.raw_entry
    if len(entry) < _DESC_ENTRY_COUNT + 2:
        return GUICODE_BASE_DEFAULT
    count = struct.unpack_from("<H", entry, _DESC_ENTRY_COUNT)[0]
    for k in range(count):
        at = _DESC_ENTRIES + k * 8
        if at + 8 > len(entry):
            break
        tag = struct.unpack_from("<H", entry, at)[0]
        if tag == _DESC_TAG_FLASH:
            return struct.unpack_from("<I", entry, at + 4)[0]
    return GUICODE_BASE_DEFAULT


# Back-compat alias: this was private until `config.py` became a second caller (2026-08-15).
_guicode_base = guicode_base


def _colour_cells(data: bytes) -> Dict[int, int]:
    """`{SRAM cell address: mode-7 index}` for every type-10 (colour) entry of the variable
    table. Located structurally via `guidata.find_variable_tables`, not by a hardcoded offset.
    """
    gs, ge = guidata.guidata_extent(data)
    dptr_off, type_off = guidata.find_variable_tables(data)
    cells: Dict[int, int] = {}
    index = 0
    while True:
        at = gs + dptr_off + index * 4
        if at + 4 > ge or gs + type_off + index >= ge:
            break
        addr = struct.unpack_from("<I", data, at)[0]
        # The data-pointer array runs until the first word that is neither zero nor an SRAM
        # pointer; that is where the table ends (see find_variable_tables' run detection).
        if not (addr == 0 or SRAM_LOW <= addr < SRAM_HIGH):
            break
        if addr and data[gs + type_off + index] == COLOUR_CELL_TYPE:
            cells.setdefault(addr, index)
        index += 1
    return cells


def _trace_constant_stores(code: bytes, base: int, cells: Dict[int, int]) -> Dict[int, int]:
    """Memoised front for `_trace_constant_stores_impl` (whose docstring is the contract): the
    tracer is a ~1 s full-section decode, and `statusbind` re-runs it per recolour."""
    return _trace_constant_stores_cached(code, base, frozenset(cells.items()))


@memo.content_cached(copy=dict)
def _trace_constant_stores_cached(code: bytes, base: int, cell_items: frozenset) -> Dict[int, int]:
    return _trace_constant_stores_impl(code, base, dict(cell_items))


def _trace_constant_stores_impl(code: bytes, base: int, cells: Dict[int, int]) -> Dict[int, int]:
    """Emulate the GuiCode's constant-forming instructions and return `{index: rgb888}` for
    every colour cell the theme-init writes a *single, unambiguous* constant into.

    A linear register tracer over the whole section: capstone gives correct instruction
    boundaries inside real code; where it desyncs on a data island the generator stops, and we
    resync on the next 2-byte boundary with the register file cleared (so no stale value crosses
    a data island). Register state is likewise cleared at every branch/call/return. The result
    is that the running state a store reads back only ever reflects the contiguous, branch-free
    theme-init block — exactly the region whose add-chains must be carried.

    Every distinct constant seen written to a cell is collected; a cell written **more than one
    distinct** constant is a runtime-selected colour (a value-match switch picks the candidate —
    e.g. 11.23 writes cell `0x200395c8` from a `cmp`/`bhi` chain of three different colours), so
    it has no single answer and is **omitted**, exactly as an unwritten cell is. Reporting one
    of several would be a guess (the cardinal rule).
    """
    try:
        import capstone
        from capstone.arm import (
            ARM_OP_IMM, ARM_OP_MEM, ARM_OP_REG, ARM_REG_PC,
        )
    except ImportError as exc:  # pragma: no cover - capstone is installed in this repo
        raise CapstoneUnavailable(
            "capstone is required to disassemble the GuiCode theme-init") from exc

    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    md.detail = True

    def literal(addr: int) -> int | None:
        off = addr - base
        if 0 <= off <= len(code) - 4:
            return struct.unpack_from("<I", code, off)[0]
        return None

    seen: Dict[int, set] = {}
    regs: Dict[int, int] = {}
    pos = 0
    while pos < len(code):
        decoded_end = pos
        for ins in md.disasm(code[pos:], base + pos):
            decoded_end = ins.address - base + ins.size
            mnem = ins.mnemonic
            ops = ins.operands
            _, written = ins.regs_access()

            # PC-relative literal load: rt <- *(literal pool). Any other ldr reads runtime
            # memory, so it falls through to the invalidate path and clears rt.
            if (mnem in ("ldr", "ldr.w") and len(ops) == 2
                    and ops[1].type == ARM_OP_MEM and ops[1].mem.base == ARM_REG_PC):
                val = literal((ins.address & ~3) + 4 + ops[1].mem.disp)
                rt = ops[0].reg
                if val is None:
                    regs.pop(rt, None)
                else:
                    regs[rt] = val
                continue

            if (mnem in ("mov", "movs", "mov.w", "movw")
                    and len(ops) == 2 and ops[1].type == ARM_OP_IMM):
                regs[ops[0].reg] = ops[1].imm & 0xFFFFFFFF
                continue

            if mnem == "movt" and len(ops) == 2 and ops[1].type == ARM_OP_IMM:
                rt = ops[0].reg
                if rt in regs:
                    regs[rt] = (regs[rt] & 0xFFFF) | ((ops[1].imm & 0xFFFF) << 16)
                else:
                    regs.pop(rt, None)
                continue

            if (mnem in ("mvn", "mvns", "mvn.w")
                    and len(ops) == 2 and ops[1].type == ARM_OP_IMM):
                regs[ops[0].reg] = (~ops[1].imm) & 0xFFFFFFFF
                continue

            # add/sub with an immediate — the add-chain. Two-operand (`add rd,#i`) and
            # three-operand (`add.w rd,rn,#i`) forms both occur.
            if (mnem in ("add", "addw", "add.w", "sub", "subw", "sub.w")
                    and ops and ops[-1].type == ARM_OP_IMM):
                dst = ops[0].reg
                src = (ops[1].reg if len(ops) == 3 and ops[1].type == ARM_OP_REG else dst)
                if src in regs:
                    imm = ops[-1].imm & 0xFFFFFFFF
                    if mnem.startswith("sub"):
                        regs[dst] = (regs[src] - imm) & 0xFFFFFFFF
                    else:
                        regs[dst] = (regs[src] + imm) & 0xFFFFFFFF
                else:
                    regs.pop(dst, None)
                continue

            # str rt,[rn] — the theme-init writes each cell with a zero-displacement store.
            if (mnem in ("str", "str.w") and len(ops) == 2 and ops[1].type == ARM_OP_MEM):
                mem = ops[1].mem
                rt = ops[0].reg
                if (mem.base != ARM_REG_PC and mem.index == 0 and mem.disp == 0
                        and mem.base in regs and rt in regs
                        and regs[mem.base] in cells):
                    value = regs[rt]
                    if value <= RGB888_MAX:
                        seen.setdefault(cells[regs[mem.base]], set()).add(value)
                continue

            # Every real branch/call/return (b, b<cond>, bl, blx, bx, cbz, cbnz, ...) is
            # already tagged by capstone's own groups -- verified empirically, so this does
            # not also need a `mnem.startswith("b")` fallback. That fallback was a bug: it
            # also matched `bic`/`bfi`/`bfc` (data-processing ops whose mnemonic happens to
            # start with "b", not control flow), which would have spuriously cleared live
            # registers on those instructions. `it` (if-then) is not a branch and carries no
            # group, but is still cleared here deliberately: it begins a block of
            # conditionally-executed instructions this tracer does not model, so treating the
            # register state as unknown from that point is the safe choice.
            if (ins.group(capstone.CS_GRP_JUMP) or ins.group(capstone.CS_GRP_CALL)
                    or ins.group(capstone.CS_GRP_RET) or mnem == "it"):
                regs.clear()
                continue

            # Any other instruction: invalidate whatever registers it writes, so a value this
            # tracer cannot follow never masquerades as a known constant.
            for reg in written:
                regs.pop(reg, None)

        # capstone stopped: either at end of section, or on a byte it could not decode. In the
        # latter case resync two bytes on (Thumb is 2-byte aligned) with state cleared.
        if decoded_end <= pos:
            decoded_end = pos + 2
        if decoded_end < len(code):
            regs.clear()
        pos = decoded_end
    return {index: next(iter(values)) for index, values in seen.items() if len(values) == 1}


@_content_cached
def mode7_palette(data: bytes) -> Dict[int, Tuple[int, int, int]]:
    """`{mode-7 index: (R, G, B)}` for every chrome colour recoverable from this MCF.

    Each entry is byte-derived: the index resolves through the variable table to an SRAM cell,
    and the value is the RGB888 constant the GuiCode theme-init (`FUN_00247bf8`) stores into that
    cell, recovered by emulating the block. Indices whose cell is only ever given a runtime value
    are omitted (a named residual — the caller keeps its heuristic), never guessed.

    `data` is the full MCF container. Raises `CapstoneUnavailable` if capstone cannot be
    imported.
    """
    section = guicode_section(data)
    base = guicode_base(section)
    code = data[section.offset:section.offset + section.length]
    cells = _colour_cells(data)
    stores = _trace_constant_stores(code, base, cells)
    return {
        index: ((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF)
        for index, value in stores.items()
    }


@memo.content_cached(copy=set)
def _pcrel_sram_addrs(code: bytes, base: int) -> set:
    """Every SRAM address the section loads via a PC-relative `ldr` — the candidate cells a
    `str` could target. Used to seed the single-constant search with the master cells that a
    colour cell is copied *from*, which are not themselves mode-7 colour cells. Memoised on
    the section's content."""
    out: set = set()
    for off in _pcrel_literal_offsets(code, base):
        val = struct.unpack_from("<I", code, off)[0]
        if SRAM_LOW <= val < SRAM_HIGH:
            out.add(val)
    return out


def _copy_store_masters(code: bytes, base: int, cells: set) -> Dict[int, set]:
    """One resyncing linear pass over the section; return `{cell: {default-master or None}}` for
    every colour cell in `cells` that is the destination of a zero-displacement `str`.

    A store `str rV,[rD]` where `rD` was just loaded with a cell address (`ldr rD,[=cell]`) is a
    write to that cell. Its DEFAULT source is the two instructions before the cell-load:
    `ldr rM,[=master]; ldr rV,[rM]` — the straight-line copy the theme-init leaves in place before
    any on/off override branch (which `b`s into the cell-load, so it never displaces these two).
    A store whose preceding idiom is not that clean single-master copy contributes `None` (an
    unknown source), so a cell with any uncharacterised or differing write is later dropped.

    The pass resyncs on data islands exactly like `_trace_constant_stores`, so it reaches the
    copy block even though a linear decode from `base` desyncs upstream of it.
    """
    try:
        import capstone
        from capstone.arm import ARM_OP_MEM, ARM_REG_PC
    except ImportError as exc:  # pragma: no cover
        raise CapstoneUnavailable("capstone is required") from exc
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    md.detail = True

    def literal(addr: int):
        off = addr - base
        return struct.unpack_from("<I", code, off)[0] if 0 <= off <= len(code) - 4 else None

    def pcrel_ldr(ins):
        if ins.mnemonic in ("ldr", "ldr.w") and len(ins.operands) == 2:
            o = ins.operands[1]
            if o.type == ARM_OP_MEM and o.mem.base == ARM_REG_PC:
                return literal((ins.address & ~3) + 4 + o.mem.disp)
        return None

    out: Dict[int, set] = {}
    hist: list = []          # last few decoded instructions, cleared at branches/islands
    pos = 0
    while pos < len(code):
        decoded_end = pos
        for ins in md.disasm(code[pos:], base + pos):
            decoded_end = ins.address - base + ins.size
            if (ins.mnemonic in ("str", "str.w") and len(ins.operands) == 2
                    and ins.operands[1].type == ARM_OP_MEM
                    and ins.operands[1].mem.base != ARM_REG_PC
                    and ins.operands[1].mem.index == 0
                    and ins.operands[1].mem.disp == 0
                    and hist):
                rD = ins.operands[1].mem.base
                load = hist[-1]
                cell = pcrel_ldr(load)
                if cell in cells and len(load.operands) and load.operands[0].reg == rD:
                    master = None
                    rV = ins.operands[0].reg
                    if len(hist) >= 3:
                        read, mload = hist[-2], hist[-3]
                        if (read.mnemonic in ("ldr", "ldr.w") and len(read.operands) == 2
                                and read.operands[0].reg == rV
                                and read.operands[1].type == ARM_OP_MEM
                                and read.operands[1].mem.base != ARM_REG_PC
                                and read.operands[1].mem.index == 0
                                and read.operands[1].mem.disp == 0):
                            rM = read.operands[1].mem.base
                            m = pcrel_ldr(mload)
                            if m is not None and len(mload.operands) and mload.operands[0].reg == rM:
                                master = m
                    out.setdefault(cell, set()).add(master)
                hist = []
                continue
            if (ins.group(capstone.CS_GRP_JUMP) or ins.group(capstone.CS_GRP_CALL)
                    or ins.group(capstone.CS_GRP_RET) or ins.mnemonic == "it"):
                hist = []
                continue
            hist.append(ins)
            if len(hist) > 3:
                hist.pop(0)
        if decoded_end <= pos:
            decoded_end = pos + 2
        if decoded_end < len(code):
            hist = []
        pos = decoded_end
    return out


@_content_cached
def resting_defaults(data: bytes) -> Dict[int, Tuple[int, int, int]]:
    """`{mode-7 index: (R, G, B)}` for runtime-state colour cells whose RESTING/default value is
    a copy of a single-constant master cell.

    These are the state-selected indicator cells `mode7_palette` deliberately omits (their live
    value is chosen at runtime by an on/off/unavailable branch). Their *default* — the value the
    straight-line theme-init leaves in the cell before any state override — is well-defined and
    byte-derived here: the cell is copied from a master, and that master holds exactly one RGB888
    constant. The result is the resting default only, never the live state; a caller must label
    it as such. A cell whose default master is not a single constant, or whose store idiom is not
    a clean copy, is omitted (never guessed). Indices already resolved by `mode7_palette` (direct
    constant stores) are excluded — this function only covers the copy-fed state cells.
    """
    section = guicode_section(data)
    base = guicode_base(section)
    code = data[section.offset:section.offset + section.length]
    cells = _colour_cells(data)
    already = set(_trace_constant_stores(code, base, cells))
    master_consts = _trace_constant_stores(
        code, base, {a: a for a in _pcrel_sram_addrs(code, base)})
    copy_masters = _copy_store_masters(code, base, set(cells))
    out: Dict[int, Tuple[int, int, int]] = {}
    for cell_addr, index in cells.items():
        if index in already:
            continue
        masters = copy_masters.get(cell_addr)
        # Require exactly one default master, and that master a single RGB888 constant. Any
        # None (uncharacterised store) or a second distinct master drops the cell.
        if not masters or len(masters) != 1:
            continue
        master = next(iter(masters))
        if master is None or master not in master_consts:
            continue
        v = master_consts[master]
        out[index] = ((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)
    return out


@_content_cached
def fill_palette(data: bytes) -> Dict[Tuple[int, int, int], Dict[str, list]]:
    """`{(R, G, B): {'static': [idx...], 'resting': [idx...]}}` — every colour a mode-7 fill index
    can name in this MCF, and which indices carry it.

    `'static'` indices come from `mode7_palette` (an invariant chrome cell: the index always shows
    that colour). `'resting'` indices come from `resting_defaults` (a runtime-state cell whose
    *resting/default* is that colour, but whose live value the firmware may change). These are the
    firmware-legal choices for recolouring a button: you cannot invent a colour (the values are
    baked into GuiCode's theme-init), only point a fill at one the firmware already defines. Prefer
    a static index when a colour offers one — it has no coupling to another widget's state.
    """
    static = mode7_palette(data)
    resting = resting_defaults(data)
    out: Dict[Tuple[int, int, int], Dict[str, list]] = {}
    for index, rgb in static.items():
        out.setdefault(rgb, {"static": [], "resting": []})["static"].append(index)
    for index, rgb in resting.items():
        out.setdefault(rgb, {"static": [], "resting": []})["resting"].append(index)
    for rgb in out:
        out[rgb]["static"].sort()
        out[rgb]["resting"].sort()
    return out


def resolve_fill_index(data: bytes, *, color: Tuple[int, int, int] | None = None,
                       index: int | None = None, prefer_static: bool = True) -> int:
    """The firmware-legal mode-7 fill index for a recolour request. Exactly one of `color` /
    `index` must be given.

    - `index=N`: verify N resolves to a real palette colour (static or resting); return N, else
      raise `PaletteError`. This lets a caller name an index directly (e.g. share a known cell).
    - `color=(r,g,b)`: return an index that resolves to that colour, preferring a **static**
      (invariant) index over a **resting** (state-selected) one when `prefer_static` is set, so the
      recoloured button does not couple to another widget's on/off state. Raise `PaletteError` if
      the colour is not in the palette.

    Raising for an unknown colour/index is the firmware rule: a fill index that resolves to nothing
    would draw an undefined colour, so it is refused rather than written.
    """
    if (color is None) == (index is None):
        raise PaletteError("pass exactly one of color= or index=")
    palette = fill_palette(data)
    if index is not None:
        known = {i for kinds in palette.values() for i in kinds["static"] + kinds["resting"]}
        if index not in known:
            raise PaletteError(
                f"mode-7 index {index} resolves to no palette colour; it cannot be used as a fill")
        return index
    if color not in palette:
        raise PaletteError(
            f"colour {color} is not in the firmware palette; choose one of "
            f"{sorted(palette)} (colours are baked into GuiCode, not editable)")
    kinds = palette[color]
    order = (kinds["static"] + kinds["resting"]) if prefer_static else (
        kinds["resting"] + kinds["static"])
    return order[0]


# The floorplan-selection highlight (GUICODE Finding 36). `selection_fills` emulates a
# "value-selected colour cascade": a straight-line run of per-button colour writes, entered with
# the selector value in a register, where each button's `cmp <value>; b<cond> <stub>` diverts to a
# two-instruction stub that reloads the OTHER colour master and branches back to that button's own
# store. Exactly one button therefore ends up with the highlight colour. Emulating it is more
# honest than pattern-matching the idiom: the result is what the code actually leaves in each cell.
_SEL_MAX_STEPS = 4000
_SEL_VALUES = 16          # floorplan values 0..14 are declared; 16 covers the byte's used range


def _literal_load_sites(code: bytes, base: int, want: int) -> list:
    """Addresses of every `ldr rX,[pc,#imm]` in the section whose literal equals `want`.

    Resyncs on data islands exactly like the other passes here (a linear decode from `base`
    desyncs upstream of most real code)."""
    try:
        import capstone
        from capstone.arm import ARM_OP_MEM, ARM_REG_PC
    except ImportError as exc:  # pragma: no cover
        raise CapstoneUnavailable("capstone is required") from exc
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    md.detail = True
    out, pos = [], 0
    while pos < len(code):
        end = pos
        for ins in md.disasm(code[pos:], base + pos):
            end = ins.address - base + ins.size
            if ins.mnemonic in ("ldr", "ldr.w") and len(ins.operands) == 2:
                o = ins.operands[1]
                if o.type == ARM_OP_MEM and o.mem.base == ARM_REG_PC:
                    at = (ins.address & ~3) + 4 + o.mem.disp
                    off = at - base
                    if 0 <= off <= len(code) - 4 and \
                            struct.unpack_from("<I", code, off)[0] == want:
                        out.append(ins.address)
        if end <= pos:
            end = pos + 2
        pos = end
    return out


def _selection_cascade(code: bytes, base: int, entry: int, selector: int,
                       cells: Dict[int, int]) -> Dict[int, int]:
    """Run the cascade at `entry` with the selector register holding `selector`; return
    `{mode-7 index: rgb888}` for every colour cell it writes.

    A tiny interpreter over the only forms the cascade uses -- `ldr rX,[pc,#imm]` (literal),
    `ldr rX,[rY]`, `str rX,[rY]`, `cmp rX,#imm`, `b`/`beq`/`bne`. It stops at the first
    instruction outside that set, which is the cascade's natural end (the block is followed by
    unrelated arithmetic). Registers are tracked as concrete words; a read of an untracked
    register just yields `None` and that store is skipped rather than guessed.
    """
    try:
        import capstone
        from capstone.arm import ARM_OP_MEM, ARM_OP_IMM, ARM_OP_REG, ARM_REG_PC
    except ImportError as exc:  # pragma: no cover
        raise CapstoneUnavailable("capstone is required") from exc
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    md.detail = True

    def literal(addr: int):
        off = addr - base
        return struct.unpack_from("<I", code, off)[0] if 0 <= off <= len(code) - 4 else None

    regs: Dict[int, int] = {}
    # The selector register is whatever the entry's `ldrb` leaves; seed it after that load.
    sel_reg = None
    zflag = None
    out: Dict[int, int] = {}
    pc = entry
    steps = 0
    while steps < _SEL_MAX_STEPS:
        steps += 1
        off = pc - base
        if not (0 <= off < len(code)):
            break
        ins = next(md.disasm(code[off:off + 4], pc, 1), None)
        if ins is None:
            break
        m, ops = ins.mnemonic, ins.operands
        nxt = pc + ins.size
        if m in ("ldr", "ldr.w") and len(ops) == 2 and ops[1].type == ARM_OP_MEM:
            dst = ops[0].reg
            if ops[1].mem.base == ARM_REG_PC:
                regs[dst] = literal((pc & ~3) + 4 + ops[1].mem.disp)
            elif ops[1].mem.index == 0 and ops[1].mem.disp == 0:
                src = regs.get(ops[1].mem.base)
                # a load through a master pointer: the VALUE is that master's constant, which the
                # caller resolves; here we carry the master address itself and resolve on store.
                regs[dst] = ("deref", src) if src is not None else None
            else:
                break
        elif m in ("ldrb", "ldrb.w") and len(ops) == 2 and ops[1].type == ARM_OP_MEM:
            regs[ops[0].reg] = selector
            sel_reg = ops[0].reg
        elif m in ("str", "str.w") and len(ops) == 2 and ops[1].type == ARM_OP_MEM \
                and ops[1].mem.index == 0 and ops[1].mem.disp == 0:
            cell = regs.get(ops[1].mem.base)
            val = regs.get(ops[0].reg)
            if isinstance(cell, int) and cell in cells and isinstance(val, tuple):
                out[cells[cell]] = val[1]          # master address; resolved by the caller
        elif m == "cmp" and len(ops) == 2 and ops[1].type == ARM_OP_IMM:
            cur = regs.get(ops[0].reg)
            zflag = None if not isinstance(cur, int) else (cur == ops[1].imm)
        elif m in ("b", "b.w") and len(ops) == 1 and ops[0].type == ARM_OP_IMM:
            pc = ops[0].imm
            continue
        elif m in ("beq", "beq.w", "bne", "bne.w") and len(ops) == 1 and ops[0].type == ARM_OP_IMM:
            if zflag is None:
                break
            take = zflag if m.startswith("beq") else not zflag
            pc = ops[0].imm if take else nxt
            continue
        else:
            break                                   # end of the cascade
        pc = nxt
    if sel_reg is None:
        return {}
    return out


@_content_cached2
def selection_fills(data: bytes, register: int) -> Dict[int, Dict[int, Tuple[int, int, int]]]:
    """`{selector value: {mode-7 index: (R,G,B)}}` for a value-selected colour cascade keyed on
    the SRAM byte at `register` -- e.g. the FLOORPLAN SELECTION highlight, whose register is the
    floorplan byte (`layout.variable_register(data, 5)`).

    Locates the cascade structurally: the entry is the `ldr rX,[=register]` whose next
    instruction is a `ldrb` of it, and which is followed by a `cmp`/branch chain that writes
    colour cells. Each selector value 0..`_SEL_VALUES` is emulated separately and the resulting
    per-cell master resolved to its constant, so the result says what the firmware actually
    leaves in each cell for that value. Returns `{}` if no such cascade exists (the honest answer
    for a build without the screen -- 11.23).
    """
    section = guicode_section(data)
    base = guicode_base(section)
    code = data[section.offset:section.offset + section.length]
    cells = _colour_cells(data)
    masters = _trace_constant_stores(code, base, {a: a for a in _pcrel_sram_addrs(code, base)})

    entries = _literal_load_sites(code, base, register)
    out: Dict[int, Dict[int, Tuple[int, int, int]]] = {}
    for entry in entries:
        best: Dict[int, Dict[int, Tuple[int, int, int]]] = {}
        for value in range(_SEL_VALUES):
            got = _selection_cascade(code, base, entry, value, cells)
            resolved = {i: masters[m] for i, m in got.items() if m in masters}
            if resolved:
                best[value] = {i: ((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)
                               for i, v in resolved.items()}
        # keep the richest cascade found at any load site of this register
        if len(best) > len(out) or (len(best) == len(out)
                                    and sum(map(len, best.values())) > sum(map(len, out.values()))):
            out = best
    return out

