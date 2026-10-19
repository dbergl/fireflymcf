"""Which INDIRECT store forms could write a working-area cell without ever naming it.

Why this exists (2026-09-04, `GUICODE.md` Finding 67). `slotsource` classifies a widget slot by
the instructions that name its address directly (pool literal, `movw`/`movs` immediate, or a
traced base register), and `sramreach` adds base+offset and register-indexed tables. Both leave
the same gap, and it is the gap the standing "argument from absence" rule warns about: a store
through a POINTER -- one loaded from a struct field, parked on the stack, received as a function
parameter, or produced by a table lookup -- reaches an address that appears nowhere in the code.
That is exactly how a designed writer for slots 174/175 (the `SLIDES/AWNINGS` in-progress
toggle) would have hidden from every earlier scan, so before the "no writer" negative could be
stated those forms had to be enumerated, not argued away.

This module enumerates them, per image section, so the negative is reproducible and so a future
build that adds one is flagged by the pinned tests rather than by folklore:

``deref_stores``
    `ldr rP,[<anything but pc>]` (any base register including `sp`, and `ldrd`) followed by a
    store through `rP` before `rP` is redefined. Keyed by the load's source operand, which is what
    identifies the struct field or local the pointer came from.
``param_store_functions``
    Functions (every `bl` target) that store through a pointer received in `r0`-`r3`, or through
    a callee-saved copy of one, anywhere in the function body. The parameter-base form is the
    one `sramreach` states it cannot see.
``table_fed_calls``
    Calls whose argument was loaded by a word-indexed table read (`[rB, rI, lsl #2]` -- the
    GuiData pointer-table shape) or from the render state's data pointer at `+0x24`. These are
    the only ways a cell's address is materialised without a literal, so a callee that stores
    through such an argument would be a generic "set slot" path.
``base_calls``
    Calls with `r0` a literal in a given address window, with `r1`/`r2` immediates when they
    are constants -- the `memset`/`memcpy`/`strcpy` shape whose RANGE can cover a cell its
    base does not name.
``stm_stores``
    Block stores, which `sramreach` does not parse (no `[` in the operand), with the base
    resolved to a literal where one was traced.
``heap``
    The LCD firmware's `_sbrk`: the break cell, the initial break (which is `raminit`'s
    `bss_end`) and every `malloc` call site with its constant size, so the heap's ceiling can be
    bounded. A heap block is a writer of whatever it overlaps and needs no literal either.

None of these is a fitted answer: each is a syntactic pattern over the full-coverage
disassembly (`slotsource._disassemble`, with restarts past pool islands), and the 13.6 results
are pinned exactly in `tests/test_indirectwrites.py`. What the pattern scans cannot do is
CLASSIFY a hit -- that is done by reading each one, and the reading is recorded in Finding 67.
"""
from __future__ import annotations

import re
import struct
from collections import OrderedDict
from dataclasses import dataclass, field

from . import container, memo, raminit, slotsource, theme

GUICODE = "GuiCode"               # section-name markers: matched by substring, so every build resolves
LCD = "LCD Firmware"
STATE_DATA_PTR_OFF = 0x24          # render state +0x24 = DAT_00253D04[slot] (GUICODE.md Finding 17)

_ST = re.compile(r"^r\d+, \[(r\d+|sp)(?:, #-?\w+|, r\d+(?:, lsl #\d)?)?\]!?$")
_LD_ANY = re.compile(r"^r(\d+), \[(.*)\]!?$")
_LDRD = re.compile(r"^r(\d+), r(\d+), \[(.*)\]!?$")
_IDXW = re.compile(r"^r\d+, \[r\d+, r\d+, lsl #2\]$")
_STATE24 = re.compile(r"^r\d+, \[r\d+, #0x24\]$")
_PCLIT = re.compile(r"^r(\d+), \[pc, #(0x[0-9a-f]+|\d+)\]$")
_IMM = re.compile(r"^r(\d+), #(0x[0-9a-f]+|\d+)$")
_MOVRR = re.compile(r"^r(\d+), r(\d+)$")
_RETURNS = ("bx", "pop", "pop.w")
_BRANCHES = ("b", "b.w")
_NOT_DEFS = ("str", "cmp", "tst", "teq", "cmn", "push", "cbz", "cbnz", "stm")


@dataclass(frozen=True)
class Site:
    """One store through a pointer: where the pointer was loaded and where it was stored through."""
    load_at: int
    load: str
    store_at: int
    store: str


def section_body(data: bytes, name: str) -> tuple[bytes, int]:
    """`(bytes, load address)` of the section whose name contains `name` (`GUICODE` or `LCD`)."""
    for section in container.parse_container(data).sections:
        if name in (section.name or ""):
            body = bytes(data[section.offset:section.offset + section.length])
            base = raminit.LCD_BASE if name == LCD else theme.guicode_base(section)
            return body, base
    raise ValueError(f"no section named like {name!r}")


@memo.content_cached(copy=None)
def _disasm(body: bytes, base: int) -> tuple:
    return tuple(slotsource._disassemble(body, base))


def _dst(insn):
    m = re.match(r"^(r\d+|sp|sb|sl|fp|ip|lr)", insn.op_str)
    return m.group(1) if m else None


def _defines(insn, reg: str) -> bool:
    """Does this instruction (re)define `reg`? Stores/compares/pushes read their first operand."""
    if insn.mnemonic.startswith(_NOT_DEFS):
        return False
    if _dst(insn) == reg:
        return True
    if insn.mnemonic in ("pop", "pop.w") and reg in insn.op_str:
        return True
    return False


def _lit_word(body: bytes, base: int, insn):
    m = _PCLIT.match(insn.op_str)
    if not (insn.mnemonic == "ldr" and m):
        return None
    at = ((insn.address + 4) & ~3) + int(m.group(2), 0) - base
    if 0 <= at <= len(body) - 4:
        return struct.unpack_from("<I", body, at)[0]
    return None


# ---------------------------------------------------------------------------------------------
@memo.content_cached(copy=None)
def _deref_stores(body: bytes, base: int, window: int) -> tuple:
    ins = _disasm(body, base)
    out = []
    for i, insn in enumerate(ins):
        srcs = []
        if insn.mnemonic in ("ldr", "ldr.w") and "pc" not in insn.op_str:
            m = _LD_ANY.match(insn.op_str)
            if m:
                srcs = [(f"r{m.group(1)}", m.group(2))]
        elif insn.mnemonic == "ldrd":
            m = _LDRD.match(insn.op_str)
            if m:
                srcs = [(f"r{m.group(1)}", m.group(3)), (f"r{m.group(2)}", m.group(3))]
        for rp, src in srcs:
            for j in range(i + 1, min(i + window, len(ins))):
                n = ins[j]
                if n.mnemonic.startswith("str") and not n.mnemonic.startswith("strd"):
                    mm = _ST.match(n.op_str)
                    if mm and mm.group(1) == rp:
                        out.append((f"[{src}]", Site(insn.address, f"{insn.mnemonic} {insn.op_str}",
                                                     n.address, f"{n.mnemonic} {n.op_str}")))
                        break
                if _defines(n, rp):
                    break
                if n.mnemonic in ("bl", "blx") and rp in ("r0", "r1", "r2", "r3"):
                    break
                if n.mnemonic in _RETURNS + _BRANCHES:
                    break
    return tuple(out)


def deref_stores(data: bytes, section: str = GUICODE, window: int = 20) -> "OrderedDict[str, list[Site]]":
    """Stores through a pointer loaded from memory, grouped by the load's source operand."""
    body, base = section_body(data, section)
    grouped: OrderedDict = OrderedDict()
    for src, site in _deref_stores(body, base, window):
        grouped.setdefault(src, []).append(site)
    return grouped


# ---------------------------------------------------------------------------------------------
def _function_extent(addrs: list, ins_at: dict, entry: int) -> int:
    """Last instruction address before the next `push` (the project's function delimiter)."""
    k = addrs.index(entry)
    last = entry
    for j in range(k + 1, len(addrs)):
        if ins_at[addrs[j]].mnemonic.startswith("push"):
            break
        last = addrs[j]
    return last


@memo.content_cached(copy=None)
def _param_store_functions(body: bytes, base: int) -> tuple:
    ins = _disasm(body, base)
    ins_at = {i.address: i for i in ins}
    addrs = sorted(ins_at)
    entries = sorted({int(i.op_str[1:], 16) for i in ins if i.mnemonic == "bl"} & set(ins_at))
    out = []
    for entry in entries:
        end = _function_extent(addrs, ins_at, entry)
        live = {"r0", "r1", "r2", "r3"}
        sites = []
        for a in addrs[addrs.index(entry):addrs.index(end) + 1]:
            n = ins_at[a]
            if n.mnemonic.startswith("str") and not n.mnemonic.startswith("strd"):
                mm = _ST.match(n.op_str)
                if mm and mm.group(1) in live:
                    sites.append(Site(entry, "param", n.address, f"{n.mnemonic} {n.op_str}"))
                continue
            m = _MOVRR.match(n.op_str)
            if n.mnemonic == "mov" and m and f"r{m.group(2)}" in live:
                live.add(f"r{m.group(1)}")
                continue
            d = _dst(n)
            if d in live and _defines(n, d):
                live.discard(d)
            if n.mnemonic in ("bl", "blx"):
                live -= {"r0", "r1", "r2", "r3"}
            if not live:
                break
        if sites:
            out.append((entry, tuple(sites)))
    return tuple(out)


def param_store_functions(data: bytes, section: str = GUICODE) -> dict:
    """{function entry: [Site]} for functions storing through a pointer parameter (or a copy)."""
    body, base = section_body(data, section)
    return {entry: list(sites) for entry, sites in _param_store_functions(body, base)}


# ---------------------------------------------------------------------------------------------
@memo.content_cached(copy=None)
def _table_fed_calls(body: bytes, base: int, lookback: int) -> tuple:
    ins = _disasm(body, base)
    out = []
    for k, insn in enumerate(ins):
        if insn.mnemonic not in ("bl", "blx"):
            continue
        live = {"r0", "r1", "r2", "r3"}
        for j in range(k - 1, max(k - lookback, -1), -1):
            p = ins[j]
            m = re.match(r"^(r\d+), (.*)$", p.op_str)
            if not m:
                if p.mnemonic in ("bl", "blx") + _RETURNS + _BRANCHES:
                    break
                continue
            d = m.group(1)
            mm = _MOVRR.match(p.op_str)
            if p.mnemonic == "mov" and mm and d in live:
                live.discard(d)
                live.add(f"r{mm.group(2)}")
                continue
            if d in live and p.mnemonic in ("ldr", "ldr.w") and (
                    _IDXW.match(p.op_str) or _STATE24.match(p.op_str)):
                out.append(Site(p.address, f"{p.mnemonic} {p.op_str}",
                                insn.address, f"{insn.mnemonic} {insn.op_str}"))
            if d in live and _defines(p, d):
                live.discard(d)
            if p.mnemonic in ("bl", "blx") + _RETURNS + _BRANCHES:
                break
    return tuple(out)


def table_fed_calls(data: bytes, section: str = GUICODE, lookback: int = 14) -> list:
    """Calls whose argument came from a word-indexed table load or the state data pointer."""
    body, base = section_body(data, section)
    return list(_table_fed_calls(body, base, lookback))


# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class BaseCall:
    at: int
    callee: str
    r0: int
    r1: int | None
    r2: int | None

    def covers(self, addr: int, width: int = 1, max_len: int = 0x10000) -> bool:
        """True if [r0, r0 + max(r1, r2)) overlaps [addr, addr+width), taking every constant
        argument below `max_len` as a candidate length. A superset on purpose: a `movw` offset
        in r2 counts too, so a hit is a lead to read, never a verdict."""
        lens = [n for n in (self.r1, self.r2) if isinstance(n, int) and n < max_len]
        hi = self.r0 + (max(lens) if lens else 0)
        return self.r0 < addr + width and hi > addr


@memo.content_cached(copy=None)
def _base_calls_and_stm(body: bytes, base: int, lo: int, hi: int) -> tuple:
    ins = _disasm(body, base)
    regs: dict = {}
    calls, stms = [], []
    for i in ins:
        w = _lit_word(body, base, i)
        m = _PCLIT.match(i.op_str)
        if i.mnemonic == "ldr" and m:
            if w is None:
                regs.pop(f"r{m.group(1)}", None)
            else:
                regs[f"r{m.group(1)}"] = w
            continue
        m = _IMM.match(i.op_str)
        if i.mnemonic in ("movs", "mov", "mov.w", "movw") and m:
            regs[f"r{m.group(1)}"] = int(m.group(2), 0)
            continue
        m = _MOVRR.match(i.op_str)
        if i.mnemonic == "mov" and m:
            if f"r{m.group(2)}" in regs:
                regs[f"r{m.group(1)}"] = regs[f"r{m.group(2)}"]
            else:
                regs.pop(f"r{m.group(1)}", None)
            continue
        if i.mnemonic in ("bl", "blx"):
            r0 = regs.get("r0")
            if r0 is not None and lo <= r0 < hi:
                calls.append(BaseCall(i.address, f"{i.mnemonic} {i.op_str}", r0,
                                      regs.get("r1"), regs.get("r2")))
            for k in ("r0", "r1", "r2", "r3"):
                regs.pop(k, None)
            continue
        if i.mnemonic.startswith("stm"):
            b = i.op_str.split(",")[0].rstrip("!")
            stms.append((i.address, f"{i.mnemonic} {i.op_str}", regs.get(b)))
        d = _dst(i)
        if d in regs and _defines(i, d):
            regs.pop(d, None)
    return tuple(calls), tuple(stms)


def base_calls(data: bytes, section: str = GUICODE, lo: int = 0x20030000, hi: int = 0x20040000) -> list:
    """Calls with r0 a literal in [lo, hi): the memset/memcpy/strcpy shape."""
    body, base = section_body(data, section)
    return list(_base_calls_and_stm(body, base, lo, hi)[0])


def stm_stores(data: bytes, section: str = GUICODE) -> list:
    """`(site, text, base literal or None)` for every block store."""
    body, base = section_body(data, section)
    return list(_base_calls_and_stm(body, base, 0x20030000, 0x20040000)[1])


# ---------------------------------------------------------------------------------------------
@dataclass
class Heap:
    sbrk: int                 # the `_sbrk` routine: `brk = *cell ?: start; *cell += n`
    brk_cell: int             # the RAM word holding the current break
    start: int                # the initial break (== raminit bss_end on 13.6)
    malloc: int               # the `malloc` entry (tail-branches into `_malloc_r`)
    calls: list = field(default_factory=list)   # (site, size or None) for every malloc call

    @property
    def bounded_top(self) -> int | None:
        """Upper bound on the break if every malloc call runs once with its constant size,
        each block carrying newlib-nano's 8-byte header and 4-byte alignment. None if any
        call's size is not a constant."""
        if any(n is None for _, n in self.calls):
            return None
        return self.start + sum(((n + 3) & ~3) + 8 for _, n in self.calls)


def heap(data: bytes) -> Heap | None:
    """Locate the LCD firmware's heap by the `_sbrk` shape, or None if there is none."""
    body, base = section_body(data, LCD)
    ins = _disasm(body, base)
    ins_at = {i.address: i for i in ins}
    addrs = sorted(ins_at)
    bss_end = raminit.ram_init(data).bss_end
    sbrk = None
    for k, i in enumerate(ins):
        if _lit_word(body, base, i) != bss_end:
            continue
        # the shape: ldr rC,[pc]=cell ; ldr rS,[pc]=start ; ldr r3,[rC] ; cmp r3,#0 ; it eq ; moveq r3,rS
        win = ins[max(0, k - 2):k + 8]
        texts = [f"{w.mnemonic} {w.op_str}" for w in win]
        if any(t.startswith("moveq") for t in texts) and any(t.startswith("add r0") for t in texts):
            cell = next((_lit_word(body, base, w) for w in win
                         if _lit_word(body, base, w) not in (None, bss_end)), None)
            sbrk = (win[0].address, cell)
            break
    if sbrk is None:
        return None
    sbrk_at, cell = sbrk
    # _malloc_r is the function calling _sbrk's wrapper; `malloc` is the stub that tail-branches
    # into it with r0 = reent. Find the stub by its `b.w` into a function that (transitively)
    # calls sbrk.
    def callers(fn):
        return [i.address for i in ins if i.mnemonic == "bl" and i.op_str == f"#{fn:#x}"]
    def entry_of(a):
        return [p for p in addrs if p <= a and ins_at[p].mnemonic.startswith("push")][-1]
    frontier = [entry_of(c) for c in callers(sbrk_at)]
    malloc_r = None
    for _ in range(3):
        nxt = []
        for e in frontier:
            cs = callers(e)
            if not cs:
                malloc_r = e
                break
            nxt += [entry_of(c) for c in cs]
        if malloc_r is not None:
            break
        frontier = nxt
    if malloc_r is None:
        return Heap(sbrk_at, cell, bss_end, 0)
    stubs = [i.address for i in ins if i.mnemonic in _BRANCHES and i.op_str == f"#{malloc_r:#x}"]
    if not stubs:
        return Heap(sbrk_at, cell, bss_end, malloc_r)
    # the stub begins at the `ldr r3,[pc]` two instructions above the branch
    stub_end = stubs[0]
    k = addrs.index(stub_end)
    malloc = addrs[k - 3]
    calls = []
    for c in callers(malloc):
        kk = addrs.index(c)
        size = None
        for j in range(kk - 1, max(kk - 6, -1), -1):
            p = ins_at[addrs[j]]
            m = _IMM.match(p.op_str)
            if m and m.group(1) == "0" and p.mnemonic in ("movs", "mov", "mov.w", "movw"):
                size = int(m.group(2), 0)
                break
            if _dst(p) == "r0":
                break
        calls.append((c, size))
    return Heap(sbrk_at, cell, bss_end, malloc, calls)
