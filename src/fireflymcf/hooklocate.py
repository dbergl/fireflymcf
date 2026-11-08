"""Locate the per-tick hook, its chain, the stock callee, the append point and the host-API cells
from the bytes of any GuiCode, with no per-firmware constant (2026-11-07).

**The mechanism (proven-from-code on 13.6, 14.7 and 11.23).** The LCD firmware's paint tick
calls the GuiCode header vector `+0x10`, `GuiCode_entry1`, with the slot-array base in `r0`
(13.6 `0x0021CBFC ldr r3,[r3,#0x10]; 0x0021CC00 blx r3`; `slottrace.guicode_entry1`). entry1
opens `push {..., lr}`, runs a display-enable early-out, keeps `r0` (`mov r4, r0`), and its
**first `bl`** is the hook: 13.6 `0x0024EE3C bl #0x247BF8`, 14.7 `0x0024F288 bl #0x247CAC`,
11.23 `0x0024CE24 bl #0x247590`, each with `r0` still entry1's argument.

The callee F is the per-tick builder: it opens `push {..., lr}`, copies `r0`, and loads the
config buffer through it (`ldr.w rY, [r0-copy, #0x214]`, `config.CONFIG_BUFFER_OFFSET`; 13.6
`0x00247C18`, 14.7 `0x00247CCC`, 11.23 `0x00247598`). Its only `bl` caller in the whole section is
the hook (a scan of every `bl` encoding). An op that grows GuiCode retargets the hook at an
appended routine that opens `push {r0, r1, r2, r3, lr}` and closes
`pop.w {r0, r1, r2, r3, lr}; b.w next` (`routinekit.PROLOGUE_TEXT`/`EPILOGUE_TEXT`); the walk
follows those links to the first target that is not one, and proves it is F.

**The stock append point** is `base + len(stock GuiCode) - 4`, the checksum word an op inserts
before. It is not a code fact: nothing in the section references its own end and the GuiCode
header carries no length, so on a built image it cannot be recovered from the image alone (a
defensible negative). `stock_append_point` therefore takes the stock image and refuses one
whose hook already reaches a link.

**The host-API cells.** `GuiCode_entry0` (header `+0x0C`) keeps `r0` (the LCD's host API
table) and copies each entry to an SRAM cell: `ldr r2,[r4,#k]; ldr r3,=CELL; str r2,[r3]`.
`+0x04` is the group-variant setter (13.6 `0x20039908`), `+0x08` the CAN-frame sender
(13.6 `0x2003986C`). That 14.7's and 11.23's `+0x04`/`+0x08` mean the same functions is
inferred from position (the same entry0 shape), not read from their LCD firmware.

Every refusal is a `HookNotFound`; nothing here guesses.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass

from . import config, memo, theme
from .followchannel import FollowChannelError

ENTRY0_VECTOR = 0x0C
ENTRY1_VECTOR = 0x10
# How far into entry1 the hook may sit (13.6 and 14.7 at +0x14, 11.23 at +0x14).
ENTRY1_WINDOW = 0x40
# How far into F its config-buffer load may sit (13.6 +0x20, after the tick counter).
CALLEE_WINDOW = 0x100
# How far into entry0 its host-API copies run (13.6: five runs, `0x247A90`-`0x247AB6`).
ENTRY0_WINDOW = 0x50
LINK_PROLOGUE = "push {r0, r1, r2, r3, lr}"
LINK_EPILOGUE = "pop.w {r0, r1, r2, r3, lr}"

_PUSH_LR = re.compile(r"push(\.w)? \{.*lr\}")
_MOV_FROM_R0 = re.compile(r"mov (r\d+), r0")
_BL = re.compile(r"bl #(0x[0-9a-f]+)")
_BRANCH = re.compile(r"(b|b\.w|bx|blx|tbb|tbh) ")


class HookNotFound(FollowChannelError):
    """The per-tick hook, its chain, F or the host-API cells did not prove out."""


@dataclass(frozen=True)
class Hook:
    entry1: int                 # GuiCode_entry1, runtime address (Thumb bit clear)
    hook_addr: int              # runtime address of entry1's first `bl`
    hook_off: int               # its GuiCode-section offset
    hook_bytes: bytes           # its 4 bytes
    links: tuple[int, ...]      # appended routine entries, call order
    stock_callee: int           # F, the per-tick builder the chain ends in
    proof: tuple[str, ...]


def _require_capstone() -> None:
    try:
        import capstone  # noqa: F401 -- before the cache, so a bare install fails the same way
    except ImportError as e:                                    # pragma: no cover
        raise theme.CapstoneUnavailable(str(e)) from e


def _section(data: bytes, default_base: int) -> tuple[bytes, int]:
    """The GuiCode section and its runtime base (`theme.guicode_base`, with its fallback
    passed in so a cached caller never reads a module constant)."""
    try:
        sec = theme.guicode_section(data)
    except ValueError as e:
        raise HookNotFound(str(e)) from e
    base = theme.flash_destination(sec)
    return (bytes(data[sec.offset:sec.offset + sec.length]),
            default_base if base is None else base)


def _dis(g: bytes, base: int, addr: int, n: int) -> list:
    from . import thumb
    lo = addr - base
    return thumb.disassemble(g[lo:lo + n], addr)


def _vector(g: bytes, base: int, off: int, name: str) -> int:
    if len(g) < off + 4:
        raise HookNotFound(f"GuiCode is too short for its header +{off:#x}")
    w = struct.unpack_from("<I", g, off)[0]
    if not w & 1 or not base <= (w & ~1) < base + len(g) - 4:
        raise HookNotFound(f"GuiCode header +{off:#x} = {w:#x} ({name}) is not a Thumb address "
                           "inside the section")
    return w & ~1


def _writes_r0(text: str) -> bool:
    mnem, _, ops = text.partition(" ")
    if mnem.startswith(("pop", "ldm")):
        return re.search(r"\br0\b", ops.split("}")[0]) is not None
    if (mnem.startswith(("str", "cmp", "cmn", "tst", "teq", "push", "stm", "it"))
            or mnem in ("cbz", "cbnz") or re.fullmatch(r"b(l|lx|x)?(\w\w)?(\.[nw])?", mnem)):
        return False
    return re.match(r"r0\b", ops) is not None


def _bl_callers(g: bytes, base: int, target: int) -> list[int]:
    """Every halfword-aligned `bl` encoding (T1: `11110 S imm10`, `11 J1 1 J2 imm11`) in the
    section, its checksum word excluded, whose target is `target`."""
    hits = []
    end = len(g) - 4
    for i in range(0, end - 3, 2):
        hw1, hw2 = struct.unpack_from("<HH", g, i)
        if (hw1 & 0xF800) != 0xF000 or (hw2 & 0xD000) != 0xD000:
            continue
        s = (hw1 >> 10) & 1
        i1 = 1 ^ ((hw2 >> 13) & 1) ^ s
        i2 = 1 ^ ((hw2 >> 11) & 1) ^ s
        off = (s << 24) | (i1 << 23) | (i2 << 22) | ((hw1 & 0x3FF) << 12) | ((hw2 & 0x7FF) << 1)
        if s:
            off -= 1 << 25
        if base + i + 4 + off == target:
            hits.append(base + i)
    return hits


@memo.content_cached(copy=None)
def _locate(data: bytes, limit: int, lower: int | None, config_off: int, default_base: int,
            entry1_vector: int, entry1_window: int, callee_window: int, link_prologue: str,
            link_epilogue: str) -> Hook:
    # Every module constant comes in as an argument: the cache is keyed on them (`memo`).
    g, base = _section(data, default_base)
    end = base + len(g) - 4                    # the current append point (the checksum word)
    proof = []

    # 1. entry1 and its first bl.
    e1 = _vector(g, base, entry1_vector, "GuiCode_entry1")
    proof.append(f"GuiCode header +{entry1_vector:#x} = {e1 | 1:#x}: GuiCode_entry1, the LCD's "
                 "per-tick call")
    ins = _dis(g, base, e1, entry1_window)
    if not ins or not _PUSH_LR.fullmatch(ins[0][1]):
        raise HookNotFound(f"entry1 {e1:#x} does not open with push {{..., lr}}")
    kept, hook = None, None
    for a, t in ins[1:]:
        m = _BL.fullmatch(t)
        if m:
            hook = (a, int(m.group(1), 16))
            break
        if _BRANCH.match(t):
            raise HookNotFound(f"entry1 leaves at {a:#x} ({t}) before its first bl")
        m = _MOV_FROM_R0.fullmatch(t)
        if m:
            kept = m.group(1)
        elif _writes_r0(t):
            raise HookNotFound(f"entry1 overwrites r0 at {a:#x} ({t}) before its first bl")
    if hook is None:
        raise HookNotFound(f"no bl within {entry1_window:#x} bytes of entry1 {e1:#x}")
    hook_addr, target = hook
    proof.append(f"{hook_addr:#x} bl #{target:#x}: entry1's first call, r0 = entry1's r0"
                 + (f" (also kept in {kept})" if kept else ""))

    # 2. The appended links.
    links: list[int] = []
    while True:
        if not base <= target < end or target & 1:
            raise HookNotFound(f"chain target {target:#x} is outside the section "
                               f"[{base:#x},{end:#x})")
        first = _dis(g, base, target, 2)
        if not first or first[0][1] != link_prologue:
            break
        if target in links:
            raise HookNotFound(f"per-tick chain loops back to {target:#x}: "
                               f"{[hex(x) for x in links]}")
        if len(links) >= limit:
            raise HookNotFound(f"per-tick chain exceeds {limit} links: {[hex(x) for x in links]}")
        if lower is not None and target < lower:
            raise HookNotFound(f"link {target:#x} is below the stock append point {lower:#x}")
        body = _dis(g, base, target, end - target)
        idx = next((i for i, (_, t) in enumerate(body) if t.startswith("b.w #")), None)
        if idx is None or idx == 0 or body[idx - 1][1] != link_epilogue:
            raise HookNotFound(f"link {target:#x}: no `{link_epilogue}; b.w` tail")
        links.append(target)
        proof.append(f"{target:#x} {link_prologue} ... {body[idx - 1][0]:#x} {link_epilogue}; "
                     f"{body[idx][0]:#x} {body[idx][1]}: an appended link")
        target = int(body[idx][1][5:], 16)
    callee = target

    # 3. Prove F: prologue, r0 copy, the config-buffer load, and its bl-callers.
    fins = _dis(g, base, callee, callee_window)
    if not fins or not _PUSH_LR.fullmatch(fins[0][1]):
        raise HookNotFound(f"callee {callee:#x} does not open with push {{..., lr}}: "
                           f"{fins[0][1] if fins else 'undecodable'}")
    base_reg, deref = None, None
    load = re.compile(r"ldr(\.w)? (r\d+), \[(r\d+), #" + hex(config_off) + r"\]")
    for a, t in fins[1:]:
        m = _MOV_FROM_R0.fullmatch(t)
        if m and base_reg is None:
            base_reg = m.group(1)
            continue
        m = load.fullmatch(t)
        if m and m.group(3) in ("r0", base_reg):
            deref = (a, t)
            break
    if deref is None:
        raise HookNotFound(f"callee {callee:#x}: no `ldr rY, [r0 or its copy, #{config_off:#x}]` "
                           f"config-buffer load within {callee_window:#x} bytes")
    proof.append(f"{callee:#x} {fins[0][1]}; {base_reg or 'r0'} = r0; {deref[0]:#x} {deref[1]}: "
                 "the config-buffer load")
    callers = _bl_callers(g, base, callee)
    expect = [] if links else [hook_addr]      # a chained hook targets the first link, not F
    if callers != expect:
        raise HookNotFound(f"callee {callee:#x} bl-callers {[hex(c) for c in callers]}, "
                           f"expected {[hex(c) for c in expect]}")
    proof.append(f"{callee:#x} has bl-callers {[hex(c) for c in callers]} "
                 "(scan of every bl encoding in the section)")
    off = hook_addr - base
    return Hook(e1, hook_addr, off, g[off:off + 4], tuple(links), callee, tuple(proof))


def _locate_now(data: bytes, limit: int, lower: int | None) -> Hook:
    return _locate(data, limit, lower, config.CONFIG_BUFFER_OFFSET, theme.GUICODE_BASE_DEFAULT,
                   ENTRY1_VECTOR, ENTRY1_WINDOW, CALLEE_WINDOW, LINK_PROLOGUE, LINK_EPILOGUE)


def locate_hook(data: bytes, *, stock: bytes | None = None, limit: int = 64) -> Hook:
    """The per-tick hook of `data`. With `stock` (the image `data` was grown from), every link
    must also lie at or above `stock_append_point(stock)`, and the chain must end in stock's
    own callee."""
    _require_capstone()
    if stock is None:
        return _locate_now(data, limit, None)
    h = _locate_now(data, limit, stock_append_point(stock))
    want = _locate_now(stock, 64, None).stock_callee
    if h.stock_callee != want:
        raise HookNotFound(f"the chain ends in {h.stock_callee:#x}, not the stock callee "
                           f"{want:#x}")
    return h


def stock_append_point(stock: bytes) -> int:
    """`base + len(GuiCode) - 4` of a stock image: where the first appended routine goes.
    Refuses an image whose hook already reaches a link: on a built image the stock end is not
    recoverable from the image alone."""
    _require_capstone()
    h = _locate_now(stock, 64, None)
    if h.links:
        raise HookNotFound(f"not a stock image: the hook already reaches appended routines "
                           f"{[hex(x) for x in h.links]}")
    g, base = _section(stock, theme.GUICODE_BASE_DEFAULT)
    return base + len(g) - 4


@memo.content_cached
def _host_api_cells(data: bytes, vector: int, window: int,
                    default_base: int) -> dict[int, int]:
    g, base = _section(data, default_base)
    e0 = _vector(g, base, vector, "GuiCode_entry0")
    base_reg, cells, pend, regs = None, {}, {}, {}
    for a, t in _dis(g, base, e0, window):
        m = _MOV_FROM_R0.fullmatch(t)
        if m:
            base_reg = m.group(1)
            continue
        m = re.fullmatch(r"ldr (r\d+), \[(r\d+), #(0x[0-9a-f]+|\d+)\]", t)
        if m and base_reg is not None and m.group(2) == base_reg:
            pend[m.group(1)] = int(m.group(3), 0)
            continue
        m = re.fullmatch(r"ldr (r\d+), \[pc, #(0x[0-9a-f]+|\d+)\]", t)
        if m:
            la = ((a + 4) & ~3) + int(m.group(2), 0)
            if base <= la < base + len(g) - 4:
                regs[m.group(1)] = struct.unpack_from("<I", g, la - base)[0]
            continue
        m = re.fullmatch(r"str (r\d+), \[(r\d+)\]", t)
        if m and m.group(1) in pend and m.group(2) in regs:
            k = pend.pop(m.group(1))
            if k in cells:
                raise HookNotFound(f"entry0 {e0:#x}: host API +{k:#x} stored twice "
                                   f"(again at {a:#x})")
            cells[k] = regs[m.group(2)]
    if not cells:
        raise HookNotFound(f"entry0 {e0:#x} copies no host-API entry to SRAM")
    return cells


def host_api_cells(data: bytes) -> dict[int, int]:
    """`{host API offset: SRAM cell}` from GuiCode_entry0's copies. `+0x04` is the
    group-variant setter's cell, `+0x08` the CAN-frame sender's."""
    _require_capstone()
    return _host_api_cells(data, ENTRY0_VECTOR, ENTRY0_WINDOW, theme.GUICODE_BASE_DEFAULT)
