"""Prove, from the LCD firmware's own code, what GuiCode's group-variant setter is (2026-11-08).

GuiCode reaches the LCD only through the host-API table the LCD builds on its stack and hands to
`GuiCode_entry0` (`hooklocate.host_api_cells` reads entry0's copies of it to SRAM). Word `+0x04`
is copied to the cell `newbutton.guicode_selected_groups` scans for. This module proves what that
word is, so the scan's answer means "the groups GuiCode drives by number" on any firmware rather
than by 13.6's numbers.

**The table builder** (13.6, 11.23 and 14.7 LCD alike, proven-from-code): the LCD loads Thumb code
addresses from its pool and stores them to `[sp,#T+4k]`, then calls entry0 with the table:
`ldr r3,=0x21B679 ; str r3,[sp,#0x28]` @`0x21C18E`/`0x21C196` (table at `sp+0x24`, so `+0x04`),
then `ldr r3,[r5,#0xc] ; add r0,sp,#0x24 ; blx r3` @`0x21C1AE`-`0x21C1B2` (`r5` the GuiCode
header, `+0x0C` entry0). `host_writer` finds the unique such call site.

**The writer** `+0x04` = `FUN_0021B678` on all three (`0x21B678`-`0x21B688`):

    ldr r3,=0x20006E5C ; ldrb r3,[r3,#0xf] ; add r0,r3 ; ldr r3,=0x20006E6C
    add.w r0,r3,r0,lsl #4 ; strb r1,[r0,#0xa] ; strb r1,[r0,#0xb] ; bx lr

It writes both variant counters of runtime group record `b3 + r0` (16 bytes each) with no byte-1
gate and no variant bound. `0x20006E5C` is the group-table loader's state block (`FUN_0021B420`,
LCD-FIRMWARE Finding 14) and `+0x0F` is where it keeps the GuiData tag-0x10 directory `b3`; the
records follow the block at `+0x10`. So when `b3` is 0 (every corpus image), GuiCode's `r0` is the
group-table index. `FUN_0021B5E8`, the setter gated on byte 1 `0x80` and the variant nibble, is
called only from the LCD's own `FUN_0021AB88` (`0x21B002`), never through the host table.

Every failure is a `HostWriterUnknown`; nothing here guesses. An image whose container has no LCD
Firmware section (a GuiCode-only container) refuses: the proof is of the LCD the image carries.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass

from . import guidata, memo

# How far before the call site the builder's code-address stores may sit (13.6: `0x21C18E`).
BUILDER_WINDOW = 48
# GuiData's table directory: a count byte at +0x07, 8-byte descriptors from +0x08, tag in byte 0
# (LCD-FIRMWARE Findings 12, 14).
_DIR_COUNT = 0x07
_DIR_START = 0x08
_DIR_STRIDE = 8
GROUP_TAG = 0x10

_LIT = re.compile(r"(r\d+), \[pc, #(0x[0-9a-f]+|\d+)\]")
_SP_STORE = re.compile(r"(r\d+), \[sp, #(0x[0-9a-f]+|\d+)\]")

# The writer, instruction for instruction: `(mnemonic, operand pattern)`. `t` is the scratch
# register (r3 in every image); r0 (group) and r1 (variant) are the AAPCS arguments.
_SHAPE = (
    ("ldr", r"(?P<t>r\d+), \[pc, #(?P<s>0x[0-9a-f]+|\d+)\]"),
    ("ldrb", r"(?P=t), \[(?P=t), #0xf\]"),
    ("add", r"r0, (?P=t)"),
    ("ldr", r"(?P=t), \[pc, #(?P<r>0x[0-9a-f]+|\d+)\]"),
    ("add.w", r"r0, (?P=t), r0, lsl #4"),
    ("strb", r"r1, \[r0, #0xa\]"),
    ("strb", r"r1, \[r0, #0xb\]"),
    ("bx", r"lr"),
)


class HostWriterUnknown(ValueError):
    """VARIANT_SETTER_UNKNOWN: the LCD's host-API `+0x04` did not prove to be the ungated
    group-variant writer (no LCD section, no unique table builder, a writer of another shape,
    or a non-zero GuiData tag-0x10 `b3`). `newbutton.guicode_selected_groups` refuses on it."""
    code = "VARIANT_SETTER_UNKNOWN"


@dataclass(frozen=True)
class HostWriter:
    builder: int        # the `ldr rA,[rB,#0xc]` of the call that hands entry0 the table
    target: int         # host API +0x04, Thumb bit clear (13.6 0x21B678)
    state: int          # the group loader's state block (13.6 0x20006E5C)
    records: int        # the per-group runtime records, 16 bytes each (state + 0x10)
    b3: int             # GuiData tag-0x10 directory b3: GuiCode's index offset (0)
    proof: tuple        # `(address, "mnemonic operands")` of the call site and the writer


def _cs():
    try:
        from capstone import CS_ARCH_ARM, CS_MODE_THUMB, Cs
    except ImportError as exc:  # pragma: no cover - environment guard
        from .theme import CapstoneUnavailable
        raise CapstoneUnavailable("capstone is required to read the LCD firmware") from exc
    return Cs(CS_ARCH_ARM, CS_MODE_THUMB)


def _literal(body: bytes, base: int, at: int, disp: str):
    la = ((at + 4) & ~3) + int(disp, 0) - base
    if 0 <= la <= len(body) - 4:
        return struct.unpack_from("<I", body, la)[0]
    return None


def _builders(body: bytes, base: int) -> list:
    """Every `ldr rA,[rB,#0xc] ; add r0,sp,#T ; blx rA` (at any halfword) whose preceding
    `BUILDER_WINDOW` bytes store literal LCD code addresses to `[sp,#T+k]`: `[(at, {k: addr})]`."""
    md = _cs()
    code = range(base, base + len(body))
    out = []
    for off in range(BUILDER_WINDOW, len(body) - 6, 2):
        if body[off + 5] != 0x47:                     # cheap pre-filter: `blx rA` is 0x47xx
            continue
        ins = list(md.disasm(body[off:off + 6], base + off, 3))
        if len(ins) < 3:
            continue
        (m0, o0), (m1, o1), (m2, o2) = [(i.mnemonic, i.op_str) for i in ins]
        a = re.fullmatch(r"(r\d+), \[r\d+, #0xc\]", o0)
        t = re.fullmatch(r"r0, sp, #(0x[0-9a-f]+|\d+)", o1)
        if not (m0 == "ldr" and a and m1 == "add" and t and m2 == "blx" and o2 == a.group(1)):
            continue
        table = int(t.group(1), 0)
        regs, slots = {}, {}
        for i in md.disasm(body[off - BUILDER_WINDOW:off], base + off - BUILDER_WINDOW):
            lit = _LIT.fullmatch(i.op_str) if i.mnemonic == "ldr" else None
            if lit:
                regs[lit.group(1)] = _literal(body, base, i.address, lit.group(2))
                continue
            st = _SP_STORE.fullmatch(i.op_str) if i.mnemonic == "str" else None
            if st and regs.get(st.group(1)) is not None:
                v = regs[st.group(1)]
                if v & 1 and (v & ~1) in code:
                    slots[int(st.group(2), 0) - table] = v
        if slots:
            out.append((base + off, slots))
    return out


def _b3(data: bytes) -> int:
    gs, _ = guidata.guidata_extent(data)
    tags = [data[gs + _DIR_START + i * _DIR_STRIDE:gs + _DIR_START + (i + 1) * _DIR_STRIDE]
            for i in range(data[gs + _DIR_COUNT])]
    groups = [e for e in tags if e[0] == GROUP_TAG]
    if len(groups) != 1:
        raise HostWriterUnknown(f"GuiData's directory has {len(groups)} tag-0x10 descriptors")
    return groups[0][3]


@memo.content_cached(copy=None)
def _host_writer(data: bytes, lcd_base: int) -> HostWriter:
    from . import raminit
    try:
        body = raminit._lcd_body(data)
    except ValueError as e:
        raise HostWriterUnknown(f"this container carries no LCD Firmware section ({e}), so "
                                f"the variant writer GuiCode calls cannot be proven") from e
    found = _builders(body, lcd_base)
    if len(found) != 1:
        raise HostWriterUnknown(f"{len(found)} host-API table builders in the LCD firmware "
                                f"(at {[hex(a) for a, _ in found]}); expected one")
    builder, slots = found[0]
    if 4 not in slots:
        raise HostWriterUnknown(f"the host-API table built at {builder:#x} has no +0x04 store")
    target = slots[4] & ~1
    md = _cs()
    off = target - lcd_base
    ins = list(md.disasm(body[off:off + 4 * len(_SHAPE)], target, len(_SHAPE)))
    text = " ; ".join(f"{i.mnemonic} {i.op_str}".strip() for i in ins)
    pattern = " ; ".join(f"{m} {o}".strip() for m, o in _SHAPE)
    m = re.fullmatch(pattern, text)
    if len(ins) != len(_SHAPE) or not m:
        raise HostWriterUnknown(f"host API +0x04 = {target:#x} is not the ungated variant "
                                f"writer's shape: {text}")
    state = _literal(body, lcd_base, ins[0].address, m.group("s"))
    recs = _literal(body, lcd_base, ins[3].address, m.group("r"))
    if state is None or recs is None or recs != state + 0x10:
        raise HostWriterUnknown(f"the writer at {target:#x} loads state {state} and records "
                                f"{recs}, not a state block followed by its records")
    try:
        b3 = _b3(data)
    except ValueError as e:
        if isinstance(e, HostWriterUnknown):
            raise
        raise HostWriterUnknown(f"GuiData's table directory does not read ({e})") from e
    if b3 != 0:
        raise HostWriterUnknown(f"GuiData's tag-0x10 b3 is {b3}: GuiCode's index is offset "
                                f"from the group number, which no image has shown")
    proof = tuple((i.address, f"{i.mnemonic} {i.op_str}".strip())
                  for i in md.disasm(body[builder - lcd_base:builder - lcd_base + 6], builder, 3))
    proof += tuple((i.address, f"{i.mnemonic} {i.op_str}".strip()) for i in ins)
    return HostWriter(builder, target, state, recs, b3, proof)


def host_writer(data: bytes) -> HostWriter:
    """The LCD's host-API `+0x04`, proven to be the ungated group-variant writer (module
    docstring). Refuses `HostWriterUnknown`."""
    from .raminit import LCD_BASE
    return _host_writer(data, LCD_BASE)
