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

import collections
import functools
import re
import struct
from dataclasses import dataclass
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
    GuiCode pays for a new disassembly. Most of those writes are the relocator moving a few
    GuiData-pointer literals, so a miss re-decodes only around the changed bytes of the last
    decode of the same length (`LinearSweep.update`), not the whole section."""
    sweep = LinearSweep.of(code, base, detail=False)
    limit = len(code) - 4
    return [lit for lit in sweep.items if lit is not None and 0 <= lit <= limit]


def detail_runs(code: bytes, base: int) -> tuple:
    """The resyncing linear decode every detail pass over GuiCode runs, as
    `((start, end, (CsInsn, ...)), ...)`: one entry per `md.disasm(code[start:], base + start)`
    call of the loop

        pos = 0
        while pos < len(code):
            decoded_end = pos
            for ins in md.disasm(code[pos:], base + pos):     # md.detail = True
                decoded_end = ins.address - base + ins.size
            if decoded_end <= pos:
                decoded_end = pos + 2
            pos = decoded_end

    with `end` that call's `decoded_end`. The instruction objects are shared between callers
    and between intermediate images (an unchanged instruction keeps its object), so their
    lazily decoded operands are paid for once; treat them as read-only. Updated from the last
    decode of the same length like `_pcrel_literal_offsets`.

    Raises `CapstoneUnavailable` without capstone."""
    return LinearSweep.of(code, base, detail=True).runs()


# Capstone's only decode state that crosses instructions is an open IT block: `it*` opens one,
# and an instruction inside it decodes differently (`ldr` prints as `ldreq`, `adds` as `add`).
# Every `disasm` call starts with none. So where the state is empty, the decode depends only on
# the bytes from there on: such an entry ("clean") is a point a re-decode can start from, or
# rejoin a previous decode at. How many instructions a block spans is capstone's business (an
# instruction from some decoder tables takes no slot), so emptiness is not counted: it is asked.
# A decode from the last clean entry, with `_PROBE` placed at the candidate, prints `movs` there
# only if no block is open (`InITBlock` drops the `s`, whatever the condition, `al` included).
# Only an entry after an `it*` needs asking, and only within `_PROBE_REACH` bytes of the last
# clean entry; past that it is left unclean, which is always safe.
_PROBE = b"\x00\x20"                    # movs r0, #0
_PROBE_REACH = 64
_BATCH = 64


class _CleanTracker:
    """Clean flags for the entries of one capstone call, in decode order."""

    __slots__ = ("code", "base", "lite", "clean_off", "it_open")

    def __init__(self, code: bytes, base: int, lite, at: int):
        self.code, self.base, self.lite = code, base, lite
        self.clean_off, self.it_open = at, False     # `at` starts a call, or is clean

    def check(self, off: int, mnemonic: str) -> bool:
        """Whether the state before the entry at `off` is empty; then note the entry."""
        if not self.it_open:
            clean = True
        elif off - self.clean_off > _PROBE_REACH:
            clean = False
        else:
            buf = self.code[self.clean_off:off] + _PROBE
            last = None
            for addr, _size, mn, _ops in self.lite.disasm_lite(buf, self.base + self.clean_off):
                last = (addr, mn)
            clean = last == (self.base + off, "movs")
        if clean:
            self.clean_off, self.it_open = off, False
        if mnemonic.startswith("it"):
            self.it_open = True
        return clean


class LinearSweep:
    """The resyncing linear decode of one `code`, entry by entry, so a later `code` can be
    decoded by re-running it only where bytes changed.

    Entry `k` is the decode attempt at `offs[k]`: an instruction of `sizes[k]` bytes, or
    `sizes[k] == 0` for a fresh `disasm` call that decoded nothing (the scan then steps 2
    bytes). `starts[k]` says a `disasm` call of the original loop begins at it. `items[k]` is,
    lite, the section offset a PC-relative `ldr` loads, unchecked against the section's extent
    (else None); detail, the `CsInsn` (None for a failed attempt). `clean[k]` says the decode
    state before it was empty (see `_PROBE`). Capstone decides an entry from at most the 4 bytes
    at its offset, and ends a call at an entry's offset only from those same bytes, so an entry
    whose `[off, off + 4)` holds no changed byte, reached in the same state, decodes the same.
    """

    __slots__ = ("code", "base", "detail", "offs", "sizes", "items", "clean", "starts",
                 "_runs", "_index")

    def __init__(self, code: bytes, base: int, detail: bool):
        self.code, self.base, self.detail = code, base, detail
        self.offs: list = []
        self.sizes: list = []
        self.items: list = []
        self.clean: list = []
        self.starts: list = []
        self._runs = None
        self._index = None

    @classmethod
    def of(cls, code: bytes, base: int, detail: bool) -> "LinearSweep":
        """The sweep of `code`: an update of a recent one of the same base and mode (of the
        same length if there is one), else a full decode."""
        try:
            import capstone  # noqa: F401
        except ImportError as exc:  # pragma: no cover - capstone is installed in this repo
            raise CapstoneUnavailable("capstone is required to decode GuiCode") from exc
        code = bytes(code)
        recent = _RECENT_SWEEPS[detail]
        same = [p for p in recent if p.base == base]
        prev = next((p for p in reversed(same) if len(p.code) == len(code)),
                    same[-1] if same else None)
        sweep = prev.update(code) if prev is not None else None
        if sweep is None:
            sweep = cls(code, base, detail)
            sweep._run(0, None, 0)
        if not recent or recent[-1] is not sweep:
            recent.append(sweep)
        return sweep

    def runs(self) -> tuple:
        if self._runs is None:
            out, cur, start, end = [], [], None, 0
            for off, size, item, first in zip(self.offs, self.sizes, self.items, self.starts):
                if first:
                    if start is not None:
                        out.append((start, end, tuple(cur)))
                    start, cur, end = off, [], off + 2
                if size:
                    cur.append(item)
                    end = off + size
            if start is not None:
                out.append((start, end, tuple(cur)))
            self._runs = tuple(out)
        return self._runs

    @staticmethod
    def _lite():
        import capstone
        return capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)

    def fresh_call(self, at: int):
        """The instructions a fresh detail `md.disasm(code[at:], base + at)` call yields, in
        order, for a scan that starts calls where this sweep's loop does not (a pool-aware
        sweep resumes past each literal word). Where this sweep has a clean entry the call
        decodes as this sweep did from there to the end of that entry's call (same bytes, same
        empty state), so those objects are replayed; capstone runs only from a position with no
        clean entry until the call reaches one."""
        if not self.detail:
            raise ValueError("fresh_call needs a detail sweep")
        if self._index is None:
            self._index = {o: k for k, o in enumerate(self.offs)}
        index, n = self._index, len(self.offs)
        sizes, items, clean, starts = self.sizes, self.items, self.clean, self.starts

        def replay(k):
            if sizes[k] == 0:                    # a fresh call here decodes nothing
                return
            while True:
                yield items[k]
                k += 1
                if k >= n or starts[k]:          # this sweep's call ended here, so does ours
                    return

        k = index.get(at)
        if k is not None and clean[k]:
            yield from replay(k)
            return
        import capstone
        md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
        md.detail = True
        lite = self._lite()
        code, base = self.code, self.base
        pos, bounded = at, True
        while True:
            track = _CleanTracker(code, base, lite, pos)
            batch, last_clean = [], 0
            window = code[pos:pos + 4 * _BATCH] if bounded else code[pos:]
            for ins in md.disasm(window, base + pos, _BATCH if bounded else 0):
                off = ins.address - base
                is_clean = track.check(off, ins.mnemonic)
                if is_clean and off != at:
                    k = index.get(off)
                    if k is not None and clean[k]:
                        yield from batch
                        yield from replay(k)
                        return
                if is_clean:
                    last_clean = len(batch)
                batch.append(ins)
            if not bounded or len(batch) < _BATCH:   # capstone really stopped: the call ends
                yield from batch
                return
            if last_clean == 0:                  # no clean entry to go on from: the rest whole
                bounded = False
                continue
            yield from batch[:last_clean]        # go on from the last clean one, fresh
            pos = batch[last_clean].address - base

    def _decoder(self):
        import capstone
        md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
        if self.detail:
            md.detail = True

            def decode(at, bounded):
                window = self.code[at:at + 4 * _BATCH] if bounded else self.code[at:]
                for ins in md.disasm(window, self.base + at, _BATCH if bounded else 0):
                    yield ins.address, ins.size, ins.mnemonic, ins
        else:
            base = self.base

            def decode(at, bounded):
                # the literal's offset, unchecked: whether it lies inside the section depends
                # on the section's length, which an update can change under a copied entry
                window = self.code[at:at + 4 * _BATCH] if bounded else self.code[at:]
                for addr, size, mnemonic, op_str in md.disasm_lite(
                        window, base + at, _BATCH if bounded else 0):
                    lit = None
                    if mnemonic in ("ldr", "ldr.w"):
                        m = _PCREL_LDR.search(op_str)
                        if m:
                            disp = int(m.group(2), 16)
                            if m.group(1):
                                disp = -disp
                            lit = (addr & ~3) + 4 + disp - base
                    yield addr, size, mnemonic, lit
        return decode

    def _run(self, pos: int, old: "LinearSweep | None", after: int, mid: bool = False,
             old_dirty=None):
        """Decode from `pos` toward the end, appending entries. `pos` is a call start, or with
        `mid` a clean entry inside a call (the call goes on from it as a fresh one would). With
        `old`, stop at the first clean point at or past `after` where `old` has a clean entry
        none of whose 4 bytes changed (`old_dirty[j]` false), append that entry with this
        decode's call-start flag, and return the index after it in `old`; otherwise decode to
        the end and return None."""
        import bisect
        decode = self._decoder()
        lite = self._lite()
        code, base = self.code, self.base
        offs, sizes, items, clean, starts = (self.offs, self.sizes, self.items, self.clean,
                                             self.starts)

        def rejoin(at: int, first: bool):
            if old is None or at < after:
                return None
            j = bisect.bisect_left(old.offs, at)
            if (j < len(old.offs) and old.offs[j] == at and old.clean[j]
                    and not old_dirty[j]):
                offs.append(at)
                sizes.append(old.sizes[j])
                items.append(old.items[j])
                clean.append(True)
                starts.append(first)
                return j + 1
            return None

        while pos < len(code):
            j = rejoin(pos, not mid)
            if j is not None:
                return j
            call_start = None if mid else pos
            got_any = False
            decoded_end = pos
            # One call of the loop, decoded in batches: capstone decodes a call's whole input
            # before yielding, so a re-decode near the start of a long call would pay for all
            # of it. A batch is at most `_BATCH` instructions from a window they cannot
            # outrun, so fewer than `_BATCH` means capstone really stopped there; a full batch
            # resumes from its last clean entry, where a fresh call decodes as this one would,
            # or, with none past its first, decodes the rest of the call whole.
            at, bounded = pos, True
            while True:
                track = _CleanTracker(code, base, lite, at)
                first = len(offs)
                got = 0
                for addr, size, mnemonic, item in decode(at, bounded):
                    got += 1
                    off = addr - base
                    is_clean = track.check(off, mnemonic)
                    if is_clean and off != pos:
                        j = rejoin(off, False)
                        if j is not None:
                            return j
                    offs.append(off)
                    sizes.append(size)
                    items.append(item)
                    clean.append(is_clean)
                    starts.append(off == call_start)
                    got_any = True
                    decoded_end = off + size
                if not bounded or got < _BATCH:
                    break
                last = len(offs) - 1        # the last clean entry past this batch's first
                while last > first and not clean[last]:
                    last -= 1
                if last == first:
                    bounded = False
                else:
                    at = offs[last]
                del offs[last:], sizes[last:], items[last:], clean[last:], starts[last:]
            if mid and not got_any:         # the call ended here; the next one starts here
                mid = False
                continue
            if decoded_end <= pos:
                offs.append(pos)
                sizes.append(0)
                items.append(None)
                clean.append(True)
                starts.append(True)
                decoded_end = pos + 2
            pos = decoded_end
            mid = False
        return None

    def update(self, code: bytes) -> "LinearSweep":
        """The sweep of `code` (same base as this one's). Entries are copied from this sweep up
        to the first whose 4 bytes changed; the decode re-runs from the last clean entry before
        it until it reaches a clean entry of this sweep past the change, and copying resumes
        there. When the lengths differ (an op appended a routine), the bytes from 4 before the
        shorter end on count as changed: the last call ended at the old end of the buffer, and
        every entry whose 4 bytes reach that far is decoded again, to the new end."""
        import numpy as np
        code = bytes(code)
        if code == self.code:
            return self
        common = min(len(code), len(self.code))
        changed = np.flatnonzero(np.frombuffer(code, np.uint8, common)
                                 != np.frombuffer(self.code, np.uint8, common))
        if len(code) != len(self.code):
            changed = np.union1d(changed, np.arange(max(0, common - 4),
                                                    max(len(code), len(self.code))))
        offs_arr = np.asarray(self.offs, dtype=np.int64)
        n = len(self.offs)
        # entry k is dirty when a changed byte c has offs[k] in (c - 4, c]
        mark = np.zeros(n + 1, dtype=np.int64)
        np.add.at(mark, np.searchsorted(offs_arr, changed - 3, side="left"), 1)
        np.add.at(mark, np.searchsorted(offs_arr, changed, side="right"), -1)
        is_dirty = np.cumsum(mark)[:n] > 0
        dirty = np.flatnonzero(is_dirty)

        new = LinearSweep(code, self.base, self.detail)
        k = 0                                   # next entry of `self` to copy
        while True:
            at = int(np.searchsorted(dirty, k))
            d = int(dirty[at]) if at < len(dirty) else n
            new.offs.extend(self.offs[k:d])
            new.sizes.extend(self.sizes[k:d])
            new.items.extend(self.items[k:d])
            new.clean.extend(self.clean[k:d])
            new.starts.extend(self.starts[k:d])
            if d == n:
                return new
            # restart at the last clean entry strictly before entry d, inside its call or as
            # the call start it is
            r = len(new.offs) - 1
            while r > 0 and not new.clean[r]:
                r -= 1
            pos, mid = (new.offs[r], not new.starts[r]) if r >= 0 else (0, False)
            r = max(r, 0)
            del new.offs[r:], new.sizes[r:], new.items[r:], new.clean[r:], new.starts[r:]
            back = new._run(pos, self, self.offs[d] + 4, mid, is_dirty)
            if back is None:
                return new
            k = back


_RECENT_SWEEPS = {False: collections.deque(maxlen=4), True: collections.deque(maxlen=4)}


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


def flash_destination(section: container.Section) -> int | None:
    """Any section's mapped base: its descriptor's tag-0x40 flash destination (GuiCode
    `0x00240000`, GuiData `0x90000000` on 11.23, 13.6 and 14.7), or None if it carries none."""
    entry = section.raw_entry
    if len(entry) < _DESC_ENTRY_COUNT + 2:
        return None
    count = struct.unpack_from("<H", entry, _DESC_ENTRY_COUNT)[0]
    for k in range(count):
        at = _DESC_ENTRIES + k * 8
        if at + 8 > len(entry):
            break
        tag = struct.unpack_from("<H", entry, at)[0]
        if tag == _DESC_TAG_FLASH:
            return struct.unpack_from("<I", entry, at + 4)[0]
    return None


def guicode_base(section: container.Section) -> int:
    """Mapped base of the GuiCode section, from its descriptor's tag-0x40 flash destination.

    Falls back to `GUICODE_BASE_DEFAULT` if the descriptor does not carry a tag-0x40 entry. A
    GuiCode default: for another section use `flash_destination`, which has no fallback.
    """
    base = flash_destination(section)
    return GUICODE_BASE_DEFAULT if base is None else base


# Back-compat alias: this was private until `config.py` became a second caller (2026-08-15).
_guicode_base = guicode_base


# ----------------------------------------------------------------------- the colour decode

class ColourDecodeError(ValueError):
    """The record decoder's colour-mode switch was not found in the shape `colour_decode`
    proves, or was found more than once."""


@dataclass(frozen=True)
class ColourDecode:
    """The record decoder's colour-mode switch, as `colour_decode` reads it from the code.

    Addresses are runtime addresses: `mode3_reader` in GuiCode, `palette_base` and
    `mode7_table` in mapped GuiData (`0x90000000 + offset`). `proof` is `((label, value), ...)`:
    the instruction and literal addresses each value was read from, and the literal that bounds
    the palette (`"next structure"`, a mapped GuiData address)."""
    mode3_reader: int
    palette_base: int
    palette_len: int
    mode7_table: int
    mode7_bound: int
    proof: tuple

    @property
    def palette_offset(self) -> int:
        """The mode-6 palette's GuiData offset."""
        return self.palette_base - _GUIDATA_MAP_BASE

    @property
    def mode7_table_offset(self) -> int:
        """The mode-7 table's GuiData offset (the variable data-pointer table)."""
        return self.mode7_table - _GUIDATA_MAP_BASE


# The sub-container the mode-6 palette is the payload of (MCF-FORMAT §5.5): magic at `+0x00`,
# palette at `+0x10`.
_SUBCONTAINER_MAGIC = 0x8F7E6D5C
_SUBCONTAINER_HEADER = 0x10
_READER_MAX = 16          # instructions a 3-byte reader may take before its `bx lr`


def colour_decode(data: bytes) -> ColourDecode:
    """The colour-mode switch of the record decoder (13.6 `FUN_00252CB0`, 14.7 `0x255578`),
    located from the code: where mode 3's inline colour is read, where the mode-6 palette is
    and how long it is, and where the mode-7 table is and its index bound.

    The method (`GUICODE.md` Finding 18; measured 2026-11-07):

      1. The anchor is the unique `mvn r2,#0xff000000; str r2,[r3,#0x68]` in the resyncing
         linear decode of GuiCode: mode 2 stores white into render-state `+0x68` (13.6
         `0x2534F8`, 14.7 `0x255DC0`). None, or more than one, refuses.
      2. The switch that holds it is the nearest `tbb [pc, r3]` before it, entered by
         `ubfx r3,r3,#3,#3; subs r3,#1; cmp r3,#6; bhi`: the mode is three bits and the table
         has seven byte entries, arm `m` at `tbb + 4 + 2 * table[m - 1]` (13.6 `0x2534DA`, 14.7
         `0x255DA2`). The anchor must lie in arm 2.
      3. Arm 3 is `ldr r0,=..; bl X; ldr r3,=..; str r0,[r3,#0x68]`, and X is three `ldrb`
         merged little-endian (`add.w ..,lsl #8`, `add.w r0,..,lsl #16`, `bx lr`): an inline
         24-bit colour, read from the record (13.6 X = `0x252140`, 14.7 X = `0x254A08`, the
         same 32 bytes). `proven-from-code`.
      4. Arm 6 is `ldr r0,=..; bl ..; ldr r3,=PAL; ldr.w r2,[r3,r0,lsl #2]`: the palette is
         indexed with no bound check.
      5. Arm 7 is `ldr r0,=..; bl ..; uxth r0,r0; cmp.w r0,#BOUND; bls; movs r0,#0;
         ldr r3,=TAB; ldr.w r3,[r3,r0,lsl #2]`: an index above BOUND reads entry 0.

    `palette_len` is *inference*: the decoder does not bound the mode-6 index, so the length is
    the distance from PAL to the lowest other GuiData literal GuiCode loads above it, in words
    (13.6 `0x90010060`, 20 entries; 14.7 `0x90010064`, 21). That literal is taken to mark the
    next structure in the same sub-container, so the words past it are not palette entries.
    PAL must be the payload of a `0x8F7E6D5C` sub-container (`PAL - 0x10` holds the magic).

    11.23 refuses: its arm 3 builds its argument with `movw r0,#0x67e4; add r0,r6` and stores
    through r6, a shape this locator does not prove. Raises `ColourDecodeError` with the
    instruction addresses on any mismatch. Needs capstone (`CapstoneUnavailable`).
    """
    try:
        import capstone  # noqa: F401 -- before the cache, so a bare install fails the same way
    except ImportError as e:                                    # pragma: no cover
        raise CapstoneUnavailable(str(e)) from e
    return _colour_decode(data, _GUIDATA_MAP_BASE)


def _arm_insns(md, code: bytes, base: int, addr: int, n: int) -> list:
    off = addr - base
    if not 0 <= off < len(code):
        raise ColourDecodeError("switch arm %#x lies outside GuiCode" % addr)
    return list(md.disasm(code[off:off + 4 * n], addr, n))


def _shape(ins, mnemonic: str, op_str: str | None = None, prefix: str | None = None) -> bool:
    if ins.mnemonic != mnemonic:
        return False
    if op_str is not None and ins.op_str != op_str:
        return False
    return prefix is None or ins.op_str.startswith(prefix)


@memo.content_cached(copy=None)
def _colour_decode(data: bytes, gd_base: int) -> ColourDecode:
    import capstone
    from capstone.arm import ARM_OP_IMM, ARM_OP_MEM, ARM_REG_PC
    flow = (capstone.CS_GRP_JUMP, capstone.CS_GRP_CALL, capstone.CS_GRP_RET,
                    capstone.CS_GRP_BRANCH_RELATIVE)

    section = guicode_section(data)
    base = guicode_base(section)
    code = data[section.offset:section.offset + section.length]

    def word(addr):
        off = addr - base
        if not 0 <= off <= len(code) - 4:
            raise ColourDecodeError("literal %#x lies outside GuiCode" % addr)
        return struct.unpack_from("<I", code, off)[0]

    def literal(ins):
        """`(literal address, word)` of a PC-relative `ldr`, else None."""
        if ins.mnemonic not in ("ldr", "ldr.w") or len(ins.operands) != 2:
            return None
        mem = ins.operands[1]
        if mem.type != ARM_OP_MEM or mem.mem.base != ARM_REG_PC:
            return None
        la = (ins.address & ~3) + 4 + mem.mem.disp
        return la, word(la)

    # 1. the mode-2 anchor
    flat = [i for _s, _e, run in detail_runs(code, base) for i in run]
    hits = [k for k in range(len(flat) - 1)
            if _shape(flat[k], "mvn", "r2, #0xff000000")
            and _shape(flat[k + 1], "str", "r2, [r3, #0x68]")]
    if len(hits) != 1:
        raise ColourDecodeError(
            "%d mode-2 stores (`mvn r2,#0xff000000; str r2,[r3,#0x68]`) in GuiCode%s; the colour "
            "decode is located by exactly one" % (
                len(hits), (" at " + ", ".join("%#x" % flat[k].address for k in hits))
                if hits else ""))
    k = hits[0]
    mode2 = flat[k].address

    # 2. the switch that holds it
    t = next((j for j in range(k - 1, max(k - 24, 0) - 1, -1)
              if _shape(flat[j], "tbb", "[pc, r3]")), None)
    if t is None or t < 4 or not (
            _shape(flat[t - 4], "ubfx", "r3, r3, #3, #3") and _shape(flat[t - 3], "subs", "r3, #1")
            and _shape(flat[t - 2], "cmp", "r3, #6") and flat[t - 1].mnemonic.startswith("bhi")):
        raise ColourDecodeError(
            "the mode-2 store at %#x is not in a `ubfx r3,r3,#3,#3; subs r3,#1; cmp r3,#6; bhi; "
            "tbb [pc,r3]` switch" % mode2)
    tbb = flat[t].address
    table = code[tbb + 4 - base:tbb + 4 - base + 7]
    arms = {m: tbb + 4 + 2 * table[m - 1] for m in range(1, 8)}
    if not arms[2] <= mode2 < min(a for a in arms.values() if a > arms[2]):
        raise ColourDecodeError("the mode-2 store at %#x is not in arm 2 (%#x) of the switch at "
                                "%#x" % (mode2, arms[2], tbb))

    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    md.detail = True

    # 3. mode 3: an inline 24-bit colour
    a3 = _arm_insns(md, code, base, arms[3], 4)
    if not (len(a3) == 4 and literal(a3[0]) and a3[0].op_str.startswith("r0,")
            and a3[1].mnemonic == "bl" and literal(a3[2]) and a3[2].op_str.startswith("r3,")
            and _shape(a3[3], "str", "r0, [r3, #0x68]")):
        raise ColourDecodeError(
            "the mode-3 arm at %#x is not `ldr r0,=..; bl X; ldr r3,=..; str r0,[r3,#0x68]`: %s"
            % (arms[3], "; ".join("%s %s" % (i.mnemonic, i.op_str) for i in a3)))
    reader = a3[1].operands[0].imm
    body = _arm_insns(md, code, base, reader, _READER_MAX)
    end = next((j for j, i in enumerate(body) if _shape(i, "bx", "lr")), None)
    body = body[:end] if end is not None else None
    if not (body and sum(i.mnemonic == "ldrb" for i in body) == 3
            and any(_shape(i, "add.w") and i.op_str.endswith("lsl #8") for i in body)
            and _shape(body[-1], "add.w", prefix="r0, ") and body[-1].op_str.endswith("lsl #16")
            and not any(i.group(g) for i in body for g in flow)
            and not any(i.mnemonic in ("pop", "push") for i in body)):
        raise ColourDecodeError("the mode-3 reader %#x (called at %#x) is not three `ldrb` merged "
                                "little-endian ending in `bx lr`" % (reader, a3[1].address))
    reader_bx = reader + sum(i.size for i in body)

    # 4. mode 6: the palette
    a6 = _arm_insns(md, code, base, arms[6], 4)
    pal = literal(a6[2]) if len(a6) == 4 else None
    if not (pal and literal(a6[0]) and a6[1].mnemonic == "bl" and a6[2].op_str.startswith("r3,")
            and _shape(a6[3], "ldr.w", "r2, [r3, r0, lsl #2]")):
        raise ColourDecodeError(
            "the mode-6 arm at %#x is not `ldr r0,=..; bl ..; ldr r3,=PAL; "
            "ldr.w r2,[r3,r0,lsl #2]`" % arms[6])

    # 5. mode 7: the variable table and its bound
    a7 = _arm_insns(md, code, base, arms[7], 8)
    tab = literal(a7[6]) if len(a7) == 8 else None
    if not (tab and literal(a7[0]) and a7[1].mnemonic == "bl" and _shape(a7[2], "uxth", "r0, r0")
            and _shape(a7[3], "cmp.w", prefix="r0, #") and a7[3].operands[1].type == ARM_OP_IMM
            and a7[4].mnemonic == "bls" and _shape(a7[5], "movs", "r0, #0")
            and a7[6].op_str.startswith("r3,")
            and _shape(a7[7], "ldr.w", "r3, [r3, r0, lsl #2]")):
        raise ColourDecodeError(
            "the mode-7 arm at %#x is not `ldr r0,=..; bl ..; uxth r0,r0; cmp.w r0,#BOUND; bls; "
            "movs r0,#0; ldr r3,=TAB; ldr.w r3,[r3,r0,lsl #2]`" % arms[7])
    bound = a7[3].operands[1].imm

    # the palette's length (inference: the next structure's literal bounds it)
    gs, ge = guidata.guidata_extent(data)
    pal_off = pal[1] - gd_base
    if not (_SUBCONTAINER_HEADER <= pal_off < ge - gs
            and struct.unpack_from("<I", data, gs + pal_off - _SUBCONTAINER_HEADER)[0]
            == _SUBCONTAINER_MAGIC):
        raise ColourDecodeError("the mode-6 palette %#x (literal %#x) is not the payload of a "
                                "%#x sub-container" % (pal[1], pal[0], _SUBCONTAINER_MAGIC))
    above = sorted(rel for _o, rel in guicode_guidata_pointers(data) if rel > pal_off)
    if not above or (above[0] - pal_off) % 4:
        raise ColourDecodeError("no GuiCode literal bounds the mode-6 palette %#x in whole words"
                                % pal[1])
    nxt = above[0]

    proof = (("switch tbb", tbb), ("mode 2 mvn", mode2),
             ("mode 3 arm", arms[3]), ("mode 3 bl", a3[1].address),
             ("mode 3 store", a3[3].address), ("mode 3 reader bx", reader_bx),
             ("mode 6 arm", arms[6]), ("mode 6 palette ldr", a6[2].address),
             ("mode 6 palette literal", pal[0]), ("mode 6 indexed load", a6[3].address),
             ("mode 7 arm", arms[7]), ("mode 7 cmp.w", a7[3].address),
             ("mode 7 table ldr", a7[6].address), ("mode 7 table literal", tab[0]),
             ("next structure", nxt + gd_base))
    return ColourDecode(mode3_reader=reader, palette_base=pal[1],
                        palette_len=(nxt - pal_off) // 4, mode7_table=tab[1],
                        mode7_bound=bound, proof=proof)


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

    def literal(addr: int) -> int | None:
        off = addr - base
        if 0 <= off <= len(code) - 4:
            return struct.unpack_from("<I", code, off)[0]
        return None

    seen: Dict[int, set] = {}
    regs: Dict[int, int] = {}
    for _start, decoded_end, run in detail_runs(code, base):   # one entry per capstone call
        for ins in run:
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
        if decoded_end < len(code):
            regs.clear()
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

