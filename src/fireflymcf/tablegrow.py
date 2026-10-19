"""Discovery half of a generic relocate-and-grow primitive for a bound-checked,
base-pointer GuiData table (like the display-cell table at GuiData `+0x44BC8`).

A table of this shape has two kinds of patch surface in GuiCode, and this module finds
both, read-only:

- **base references** -- every place GuiCode loads the table's absolute base address
  (`0x9004xxxx`), so a relocation can repoint each one to the table's new home. On this
  firmware every load seen so far is a PC-relative literal (`ldr rX, [pc, #imm]`), but
  Thumb-2 can also materialise a 32-bit constant with a `movw`/`movt` pair straight into a
  register with no literal pool at all, so that form is scanned for too even though 13.6
  uses none.
- **bound sites** -- every place GuiCode range-checks a table index against the table's
  row count (`cmp.w rX, #bound`) before indexing into *some* table. A same-register
  `cmp.w`/indexed-load pair that is not actually gating our table would be a false
  positive; filtering to sites where an indexed load follows (directly, or one branch hop
  away -- the common "clamp out-of-range to 0, fall into the shared reader" idiom) keeps
  the surface to real index-bound checks. On 13.6, `bound=0x298` has no such false
  positives to filter: every `cmp.w rX, #0x298` in GuiCode gates an indexed read.

Task 3 (not this one) uses these two lists to relocate the table and rewrite every base
reference and bound immediate; **nothing here modifies an image**.
"""
from __future__ import annotations

import re
import struct
from collections import deque
from dataclasses import dataclass

from . import guidata, memo, patch, relocate, theme, xref

_GUIDATA_MAP_BASE = 0x90000000


class TableGrowError(Exception):
    """A patch-surface discovery or (in Task 3) rewrite request cannot be satisfied."""


@dataclass
class IndexedTable:
    """A bound-checked, base-pointer GuiData table: a flash-absolute `base_addr` and the
    per-row `width` in bytes GuiCode indexes it with (e.g. `ldr.w rX, [rBase, rIdx, lsl #2]`
    for `width == 4`)."""

    base_addr: int
    width: int


# An indexed memory operand: two registers in the brackets (`[rBase, rIdx]`, optionally
# with a shift), as opposed to an immediate/PC-relative displacement.
_INDEXED_MEM_RE = re.compile(r"\[\s*r\d+\s*,\s*r\d+")

# A capstone branch/compare-and-branch operand's absolute target, e.g. "#0x25318e".
_BRANCH_TARGET_RE = re.compile(r"#(0x[0-9a-fA-F]+)\b")

# A cmp.w immediate operand, e.g. "r0, #0x298".
_CMP_IMM_RE = re.compile(r"#(0x[0-9a-fA-F]+|\d+)\s*$")

_BRANCH_MNEMONICS_NO_FALLTHROUGH = ("b", "b.w")


def _guicode_slice(data: bytes) -> tuple[bytes, int]:
    """`(code, base)` for the GuiCode section: the raw bytes and their runtime base."""
    section = theme.guicode_section(data)
    base = theme.guicode_base(section)
    code = data[section.offset:section.offset + section.length]
    return code, base


def _capstone_thumb():
    try:
        import capstone
    except ImportError as exc:  # pragma: no cover - capstone is installed in this repo
        raise theme.CapstoneUnavailable(
            "capstone is required to scan GuiCode for table patch surfaces") from exc
    return capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)


def enc_movw(imm: int, rd: int) -> bytes:
    """Thumb-2 `movw rd, #imm` encoding, as the two little-endian halfwords in flash order."""
    i = (imm >> 11) & 1
    imm4 = (imm >> 12) & 0xF
    imm3 = (imm >> 8) & 0x7
    imm8 = imm & 0xFF
    return struct.pack("<HH", 0xF240 | (i << 10) | imm4, (imm3 << 12) | (rd << 8) | imm8)


def enc_movt(imm: int, rd: int) -> bytes:
    """Thumb-2 `movt rd, #imm` encoding (same layout as `enc_movw`, `0xF2C0` base)."""
    i = (imm >> 11) & 1
    imm4 = (imm >> 12) & 0xF
    imm3 = (imm >> 8) & 0x7
    imm8 = imm & 0xFF
    return struct.pack("<HH", 0xF2C0 | (i << 10) | imm4, (imm3 << 12) | (rd << 8) | imm8)


def _movw_movt_refs(code: bytes, base: int, value: int) -> list[int]:
    """Runtime addresses of `movw rd, #lo` instructions immediately (within a few
    instructions) followed by a same-`rd` `movt rd, #hi` materialising the 32-bit `value`.

    Kept general per the brief even though 13.6 has none: a `movw`/`movt` pair loads a
    32-bit constant into a register with no literal pool at all, so `find_base_refs` would
    otherwise miss a base reference built that way.
    """
    lo = value & 0xFFFF
    hi = (value >> 16) & 0xFFFF
    out: list[int] = []
    for rd in range(13):
        movw = enc_movw(lo, rd)
        movt = enc_movt(hi, rd)
        start = 0
        while True:
            idx = code.find(movw, start)
            if idx == -1:
                break
            start = idx + 1
            # movt must land within a short span after movw -- a few instructions, not a
            # coincidental byte match somewhere unrelated later in the section.
            if movt in code[idx:idx + 32]:
                out.append(base + idx)
    return out


def find_base_refs(data: bytes, base_addr: int) -> list[int]:
    """Sorted GuiCode runtime addresses of every load of `base_addr`: literal-pool words
    (found the same way `xref.find_u32` finds any other constant) and `movw`/`movt` pairs.

    Raises `theme.CapstoneUnavailable` if capstone cannot be imported (needed only for the
    `movw`/`movt` half; the literal-pool scan alone needs no disassembly).
    """
    code, base = _guicode_slice(data)
    hits = {base + off for off in xref.find_u32(code, base_addr)}
    hits.update(_movw_movt_refs(code, base, base_addr))
    return sorted(hits)


def _indexed_read_follows(md, code: bytes, base: int, start_off: int,
                           max_visits: int = 24, max_depth: int = 2) -> bool:
    """Whether an indexed load (`ldr`/`ldr.w`/`ldrb`-family, `[rBase, rIdx]`) is reachable
    from `start_off` within a small instruction budget, following at most `max_depth`
    branches (the "clamp out-of-range, fall into the shared indexed reader" idiom jumps
    away from the bound check entirely, so a purely linear scan misses it)."""
    frontier = deque([(start_off, 0)])
    seen: set[int] = set()
    visits = 0
    while frontier and visits < max_visits:
        off, depth = frontier.popleft()
        if off in seen or off < 0 or off + 4 > len(code):
            continue
        seen.add(off)
        insns = list(md.disasm(code[off:off + 4], base + off))
        if not insns:
            continue
        insn = insns[0]
        visits += 1
        if insn.mnemonic.startswith("ldr") and _INDEXED_MEM_RE.search(insn.op_str):
            return True
        is_branch = insn.mnemonic.startswith("b") or insn.mnemonic in ("cbz", "cbnz")
        if is_branch:
            match = _BRANCH_TARGET_RE.search(insn.op_str)
            if match and depth < max_depth:
                frontier.append((int(match.group(1), 16) - base, depth + 1))
            if insn.mnemonic in _BRANCH_MNEMONICS_NO_FALLTHROUGH:
                continue  # unconditional: no fallthrough path to add
        frontier.append((off + insn.size, depth))
    return False


@memo.content_cached(copy=tuple)
def _cmp_w_sites(code: bytes, base: int) -> tuple:
    """`(offset, size, op_str)` of every `cmp.w` decoded at a 2-byte boundary of `code`.

    Both bound searches below start from this scan, which is the expensive part (a capstone
    decode at every halfword of GuiCode). It depends only on the section's bytes, so it is
    memoised on them: a compile asks it again after every GuiData edit, which never changes
    GuiCode."""
    md = _capstone_thumb()
    out = []
    for off in range(0, len(code) - 4, 2):
        insns = list(md.disasm(code[off:off + 4], base + off))
        if insns and insns[0].mnemonic == "cmp.w":
            out.append((off, insns[0].size, insns[0].op_str))
    return tuple(out)


def find_bound_sites(data: bytes, bound: int) -> list[int]:
    """Sorted GuiCode runtime addresses of every `cmp.w rX, #bound` that gates an indexed
    table read -- an indexed load (`ldr(.w)`/`ldrb` with a `[rBase, rIdx]` operand) reachable
    within a handful of instructions, chasing at most one branch hop.

    Every instruction boundary is disassembled independently (2-byte steps), not a linear
    decode, because a linear pass desyncs across GuiCode's interleaved literal pools.

    Raises `theme.CapstoneUnavailable` if capstone cannot be imported.
    """
    code, base = _guicode_slice(data)
    md = _capstone_thumb()

    cmp_sites: list[tuple[int, int]] = []  # (offset, instruction size)
    for off, size, op_str in _cmp_w_sites(code, base):
        match = _CMP_IMM_RE.search(op_str)
        if not match:
            continue
        if int(match.group(1), 0) != bound:
            continue
        cmp_sites.append((off, size))

    out = {
        base + off
        for off, size in cmp_sites
        if _indexed_read_follows(md, code, base, off + size)
    }
    return sorted(out)


# `cmp.w rIdx, #imm` split into (index register, immediate).
_CMP_REG_IMM_RE = re.compile(r"^r(\d+),\s*#(0x[0-9a-fA-F]+|\d+)\s*$")
# `ldr rDst, [pc, #k]` -- a literal-pool load (rDst, displacement).
_PCREL_LDR_RE = re.compile(r"^r(\d+),\s*\[pc,\s*#(0x[0-9a-fA-F]+|\d+)\]\s*$")
# `ldr[.w] rDst, [rBase, rIdx ...]` -- capture (base register, index register).
_INDEXED_LDR_RE = re.compile(r"^r\d+,\s*\[r(\d+),\s*r(\d+)")


def _pcrel_ldr(insn, base: int) -> "tuple[int, int] | None":
    """If `insn` is `ldr rDst, [pc, #k]`, return `(rDst, literal_word_runtime_addr)`, else
    `None`. The literal address is `Align(PC, 4) + k`, `PC == insn.address + 4` in Thumb."""
    m = _PCREL_LDR_RE.match(insn.op_str)
    if not m:
        return None
    return int(m.group(1)), ((insn.address + 4) & ~3) + int(m.group(2), 0)


def _reaches_indexed_read_off_base(md, code: bytes, base: int, start_off: int, idx_reg: int,
                                   pool_addrs: "set[int]", max_visits: int = 28,
                                   max_depth: int = 2) -> bool:
    """Like `_indexed_read_follows`, but requires the indexed load to (a) use `idx_reg` as its
    index and (b) read through a base register loaded from one of `pool_addrs` (the literal
    words holding the table base). Tracks which registers currently hold the table base along
    each path, so a `cmp.w` is credited to a table only when its gated read is that table's."""
    frontier = deque([(start_off, 0, frozenset())])
    seen: set = set()
    visits = 0
    while frontier and visits < max_visits:
        off, depth, base_regs = frontier.popleft()
        if (off, base_regs) in seen or off < 0 or off + 4 > len(code):
            continue
        seen.add((off, base_regs))
        insns = list(md.disasm(code[off:off + 4], base + off))
        if not insns:
            continue
        insn = insns[0]
        visits += 1

        regs = set(base_regs)
        lit = _pcrel_ldr(insn, base)
        if lit is not None:                       # a literal load (re)defines its dest register
            dst, target = lit
            regs.discard(dst)
            if target in pool_addrs:
                regs.add(dst)

        idx_match = _INDEXED_LDR_RE.match(insn.op_str)
        if insn.mnemonic.startswith("ldr") and idx_match:
            b, ix = int(idx_match.group(1)), int(idx_match.group(2))
            if ix == idx_reg and b in regs:
                return True

        frozen = frozenset(regs)
        is_branch = insn.mnemonic.startswith("b") or insn.mnemonic in ("cbz", "cbnz")
        if is_branch:
            tgt = _BRANCH_TARGET_RE.search(insn.op_str)
            if tgt and depth < max_depth:
                frontier.append((int(tgt.group(1), 16) - base, depth + 1, frozen))
            if insn.mnemonic in _BRANCH_MNEMONICS_NO_FALLTHROUGH:
                continue
        frontier.append((off + insn.size, depth, frozen))
    return False


def find_index_bound(data: bytes, base_addr: int) -> int:
    """Row count (`old_len`) of the table indexed off `base_addr`, discovered purely from
    GuiCode with NO pinned bound-site address.

    Finds each `cmp.w rIdx, #imm` whose `rIdx` gates an indexed read (`ldr[.w] rDst,
    [rBase, rIdx, lsl #2]`) whose `rBase` was loaded from a literal word holding `base_addr`,
    and returns `imm + 1`. The gating immediate is the largest valid index, so the row count
    is one more. Tables indexed in lockstep off different bases (the parallel cell and type
    tables) share one bound, so both resolve to the same value.

    Raises `TableGrowError` if no `cmp.w` gates a read of this base (e.g. `base_addr` is not a
    table base on this build) or if the correlated sites disagree on the immediate.
    """
    code, base = _guicode_slice(data)
    md = _capstone_thumb()
    pool_addrs = {base + off for off in xref.find_u32(code, base_addr)}
    if not pool_addrs:
        raise TableGrowError(f"no literal word holds table base {base_addr:#x}")

    bounds: set[int] = set()
    for off, size, op_str in _cmp_w_sites(code, base):
        m = _CMP_REG_IMM_RE.match(op_str)
        if not m:
            continue
        idx_reg, imm = int(m.group(1)), int(m.group(2), 0)
        if _reaches_indexed_read_off_base(md, code, base, off + size, idx_reg, pool_addrs):
            bounds.add(imm)

    if not bounds:
        raise TableGrowError(
            f"no cmp.w gates an indexed read of table base {base_addr:#x}")
    if len(bounds) > 1:
        raise TableGrowError(
            f"ambiguous index bound for {base_addr:#x}: {sorted(hex(b) for b in bounds)}")
    return next(iter(bounds)) + 1


# --------------------------------------------------------------------------------------
# Task 3: relocate, grow, repoint, rebump, reseal.
# --------------------------------------------------------------------------------------

def thumb_expand_imm_encode(value: int) -> int | None:
    """Invert Thumb-2 `ThumbExpandImm` -- given a 32-bit `value`, the 12-bit `imm12`
    field a `cmp.w`/`mov.w`/... modified-immediate operand would carry to expand to it, or
    `None` if `value` is not representable.

    Mirrors the ARM pseudocode exactly: `imm12[11:10] == 00` selects one of four byte-
    replication forms; otherwise the low 7 bits with an implicit leading 1 (`0x80 | ...`)
    are rotated right by `imm12[11:7]` (a rotation `>= 8`, which is what forces
    `imm12[11:10] != 00`). We try the special forms first, then every rotation `8..31`.
    Verified: `0x298 -> 0xF26`, `0x300 -> 0xF40`, `0x123 -> None`.
    """
    value &= 0xFFFFFFFF
    b0 = value & 0xFF
    b1 = (value >> 8) & 0xFF
    b2 = (value >> 16) & 0xFF
    b3 = (value >> 24) & 0xFF

    # Special forms (imm12[11:10] == 00): the byte-replication cases.
    if b1 == 0 and b2 == 0 and b3 == 0:            # 0x000000XY
        return b0
    if b0 != 0 and b1 == 0 and b2 == b0 and b3 == 0:   # 0x00XY00XY
        return (0b01 << 8) | b0
    if b0 == 0 and b1 != 0 and b2 == 0 and b3 == b1:   # 0xXY00XY00
        return (0b10 << 8) | b1
    if b0 != 0 and b0 == b1 == b2 == b3:               # 0xXYXYXYXY
        return (0b11 << 8) | b0

    # Rotation form: value == ROR(unrotated_byte, rotation), unrotated_byte = 0x80 | low7.
    # rotation lives in imm12[11:7] and must be >= 8 (else imm12[11:10] == 00 -> special).
    for rotation in range(8, 32):
        unrotated = ((value << rotation) | (value >> (32 - rotation))) & 0xFFFFFFFF
        if unrotated <= 0xFF and (unrotated & 0x80):
            return (rotation << 7) | (unrotated & 0x7F)
    return None


def thumb_expand_imm_decode(imm12: int) -> int:
    """Expand a 12-bit `imm12` modified-immediate field to its 32-bit value (ARM
    `ThumbExpandImm`), the inverse of `thumb_expand_imm_encode`."""
    imm12 &= 0xFFF
    if (imm12 >> 10) == 0:                       # special byte-replication forms
        byte = imm12 & 0xFF
        form = (imm12 >> 8) & 0b11
        if form == 0b00:
            return byte
        if form == 0b01:
            return (byte << 16) | byte
        if form == 0b10:
            return (byte << 24) | (byte << 8)
        return (byte << 24) | (byte << 16) | (byte << 8) | byte
    unrotated = 0x80 | (imm12 & 0x7F)            # rotation form
    rotation = imm12 >> 7
    return ((unrotated >> rotation) | (unrotated << (32 - rotation))) & 0xFFFFFFFF


def read_guicode_word(data: bytes, runtime: int) -> int:
    """The u32 at GuiCode runtime address `runtime` (e.g. a table-base literal pool word).
    Used to read a table's CURRENT base after a prior grow relocated it."""
    section = theme.guicode_section(data)
    base = theme.guicode_base(section)
    return struct.unpack_from("<I", data, _guicode_off(section, base, runtime))[0]


def read_cmpw_bound(data: bytes, site_runtime: int) -> int:
    """The immediate value a `cmp.w rN, #imm` at GuiCode `site_runtime` compares against --
    i.e. a bound-checked table's CURRENT `row_count - 1`. Raises `TableGrowError` if the
    bytes there are not a `cmp.w rN, #imm`."""
    section = theme.guicode_section(data)
    base = theme.guicode_base(section)
    off = _guicode_off(section, base, site_runtime)
    hw1, hw2 = struct.unpack_from("<HH", data, off)
    if (hw1 & 0xFBF0) != 0xF1B0 or (hw2 & 0x8F00) != 0x0F00:
        raise TableGrowError(
            f"{site_runtime:#x} is not a cmp.w rN,#imm (hw1={hw1:#06x} hw2={hw2:#06x})")
    imm12 = (((hw1 >> 10) & 1) << 11) | (((hw2 >> 12) & 0x7) << 8) | (hw2 & 0xFF)
    return thumb_expand_imm_decode(imm12)


def _runtime_to_guidata_off(gs: int, runtime: int) -> int:
    """File offset of a GuiData runtime address `0x900xxxxx`."""
    return gs + (runtime - _GUIDATA_MAP_BASE)


def _guicode_off(section, base: int, runtime: int) -> int:
    """File offset in `section` of a GuiCode runtime address."""
    return section.offset + (runtime - base)


def _reencode_cmpw(orig4: bytes, new_bound: int) -> bytes:
    """Re-encode a `cmp.w rN, #imm` (4 Thumb-2 bytes) to `cmp.w rN, #new_bound`, keeping
    the register. Raises `TableGrowError` if `orig4` is not a `cmp.w #imm` or the new bound
    is not an encodable modified immediate."""
    hw1, hw2 = struct.unpack("<HH", orig4)
    # T1 cmp (immediate): hw1 = 11110 i 0 1101 1 Rn ; hw2 = 0 imm3 1111 imm8.
    if (hw1 & 0xFBF0) != 0xF1B0 or (hw2 & 0x8F00) != 0x0F00:
        raise TableGrowError(
            f"bound site does not carry a cmp.w rN,#imm (hw1={hw1:#06x} hw2={hw2:#06x})")
    rn = hw1 & 0xF
    imm12 = thumb_expand_imm_encode(new_bound)
    if imm12 is None:
        raise TableGrowError(f"new bound {new_bound:#x} is not an encodable modified immediate")
    new_hw1 = 0xF1B0 | (((imm12 >> 11) & 1) << 10) | rn
    new_hw2 = (((imm12 >> 8) & 0x7) << 12) | (0xF << 8) | (imm12 & 0xFF)
    return struct.pack("<HH", new_hw1, new_hw2)


def grow_indexed_tables(data, tables: "list[IndexedTable]", old_len: int, extra: int, *,
                        expect_base_refs: "dict[int, int]", expect_bound_sites: int,
                        new_entry_fill: "dict[int, bytes] | None" = None
                        ) -> "tuple[bytes, list[int], list[relocate.Edit]]":
    """Relocate the given parallel `tables` to the end of GuiData, grow each from `old_len`
    to a common `new_len`, repoint every GuiCode base literal to its table's new home, bump
    every GuiCode index bound from `old_len-1` to `new_len-1`, and reseal.

    `tables` share one row-count bound (they are indexed in lockstep), so all of them grow
    to the same `new_len` and every one of the `expect_bound_sites` bound checks is rewritten
    once. `new_len` is the smallest value `>= old_len + extra` whose bound `new_len-1` is an
    encodable Thumb-2 modified immediate (`TableGrowError` if none within a small window).

    Returns `(image, new_indices, edits)` where `new_indices == range(old_len,
    old_len+extra)` and `edits` is the full audit trail (the `relocate` insertion/shift
    edits plus one `Edit` per repointed literal and rebumped bound). Refuses (raises
    `TableGrowError`) if a discovered patch-surface count does not match `expect_base_refs` /
    `expect_bound_sites`.
    """
    data = bytes(data)
    new_entry_fill = new_entry_fill or {}
    gs, ge = guidata.guidata_extent(data)
    span = ge - gs
    at = span - 4  # append before GuiData's 4-byte checksum (facts: at = span - 4)

    # --- 1. discover + assert patch surfaces (on the ORIGINAL image; runtime addrs stable)
    base_refs: dict[int, list[int]] = {}
    for table in tables:
        refs = find_base_refs(data, table.base_addr)
        want = expect_base_refs.get(table.base_addr)
        if want is None or len(refs) != want:
            raise TableGrowError(
                f"table {table.base_addr:#x}: found {len(refs)} base refs, "
                f"expected {want}")
        base_refs[table.base_addr] = refs

    old_bound = old_len - 1
    bound_sites = find_bound_sites(data, old_bound)
    if len(bound_sites) != expect_bound_sites:
        raise TableGrowError(
            f"found {len(bound_sites)} bound sites for #{old_bound:#x}, "
            f"expected {expect_bound_sites}")

    # --- choose new_len: smallest >= need with an encodable bound (search a small window)
    need = old_len + extra
    new_len = None
    for candidate in range(need, need + 4096):
        if thumb_expand_imm_encode(candidate - 1) is not None:
            new_len = candidate
            break
    if new_len is None:
        raise TableGrowError(
            f"no encodable table bound at or just above {need - 1:#x}")
    new_bound = new_len - 1

    # --- 2. build the combined grown payload and the new base of each table
    payload = bytearray()
    new_bases: dict[int, int] = {}
    if at % 4:
        raise TableGrowError(f"append point {at:#x} is not 4-aligned")
    for table in tables:
        w = table.width
        # Align each table's START to its element width, so a width-4 table is indexed with an
        # aligned `ldr.w [rBase, rIdx, lsl #2]` on the memory-mapped GuiData region. Padding only
        # the whole payload (below) is NOT enough: a width-1 table of odd length before a width-4
        # table would push the width-4 table's base off a 4-byte edge (a real STM32F7 fault / the
        # format's every-table-4-byte-edge invariant). `at` is 4-aligned, so aligning `len(payload)`
        # to `w` makes the base `w`-aligned.
        while len(payload) % w:
            payload.append(0)
        base = _GUIDATA_MAP_BASE + at + len(payload)
        assert base % w == 0, "table %#x base %#x not %d-aligned" % (table.base_addr, base, w)
        new_bases[table.base_addr] = base
        old_off = _runtime_to_guidata_off(gs, table.base_addr)
        payload += data[old_off:old_off + old_len * w]  # copy stock entries verbatim
        # `new_entry_fill[base]` gives the initial bytes for the `extra` REQUESTED new entries
        # (extra*width bytes, distinct per entry -- e.g. a different fresh SRAM pointer each);
        # any further entries up to `new_len` exist only to reach an encodable bound and are
        # always zero. Default (missing) = all-zero.
        fill = new_entry_fill.get(table.base_addr, b"\x00" * (extra * w))
        if len(fill) != extra * w:
            raise TableGrowError(
                f"new_entry_fill for {table.base_addr:#x} is {len(fill)} bytes, "
                f"expected extra*width = {extra * w}")
        payload += fill
        payload += b"\x00" * ((new_len - old_len - extra) * w)
    while len(payload) % 4:  # keep the section 4-aligned
        payload.append(0)

    grown, edits = relocate.relocate(data, at, bytes(payload))
    edits = list(edits)

    # --- 3. repoint every base literal, rebump every bound site (in the grown image)
    section = theme.guicode_section(grown)
    gc_base = theme.guicode_base(section)
    out = bytearray(grown)

    for table in tables:
        new_base = new_bases[table.base_addr]
        want_old = struct.pack("<I", table.base_addr & 0xFFFFFFFF)
        new_word = struct.pack("<I", new_base & 0xFFFFFFFF)
        for runtime in base_refs[table.base_addr]:
            off = _guicode_off(section, gc_base, runtime)
            cur = bytes(out[off:off + 4])
            if cur != want_old:
                raise TableGrowError(
                    f"base ref {runtime:#x} holds {cur.hex()}, expected "
                    f"{want_old.hex()} (not a literal load of {table.base_addr:#x}?)")
            out[off:off + 4] = new_word
            edits.append(relocate.Edit(
                file_offset=off, old=cur, new=new_word,
                structure="guicode:table-base-literal",
                note=f"repoint {table.base_addr:#x} -> {new_base:#x}"))

    new_cmp_by_off: dict[int, bytes] = {}
    for runtime in bound_sites:
        off = _guicode_off(section, gc_base, runtime)
        cur = bytes(out[off:off + 4])
        new_cmp = _reencode_cmpw(cur, new_bound)
        out[off:off + 4] = new_cmp
        new_cmp_by_off[off] = new_cmp
        edits.append(relocate.Edit(
            file_offset=off, old=cur, new=new_cmp,
            structure="guicode:index-bound",
            note=f"cmp.w bound {old_bound:#x} -> {new_bound:#x}"))

    # --- 4. reseal (GuiCode CRC changed by the in-place same-size edits above)
    final, _ = patch.reseal(bytes(out))

    # --- verify: bases, bounds, preserved entries, and deep coverage on the finished image
    fsection = theme.guicode_section(final)
    fbase = theme.guicode_base(fsection)
    for table in tables:
        new_base = new_bases[table.base_addr]
        new_word = struct.pack("<I", new_base & 0xFFFFFFFF)
        for runtime in base_refs[table.base_addr]:
            off = _guicode_off(fsection, fbase, runtime)
            if bytes(final[off:off + 4]) != new_word:
                raise TableGrowError(f"base ref {runtime:#x} not repointed in final image")
    for off, new_cmp in new_cmp_by_off.items():
        if bytes(final[off:off + 4]) != new_cmp:
            raise TableGrowError("a bound site did not survive reseal")

    fgs, _fge = guidata.guidata_extent(final)
    for table in tables:
        new_base = new_bases[table.base_addr]
        new_off = _runtime_to_guidata_off(fgs, new_base)
        old_off = _runtime_to_guidata_off(gs, table.base_addr)
        w = table.width
        if bytes(final[new_off:new_off + old_len * w]) != data[old_off:old_off + old_len * w]:
            raise TableGrowError(f"table {table.base_addr:#x} entries not copied faithfully")

    # relocate already gated deep coverage pre-edit; re-check after the GuiCode rewrites.
    from . import coverage
    coverage.analyse(final, deep=True)

    return final, list(range(old_len, old_len + extra)), edits
