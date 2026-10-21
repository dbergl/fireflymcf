"""A minimal Thumb-2 encoder — only the instructions this project authors.

**Why this exists and why it is deliberately tiny.** Every GuiCode edit before 2026-08-23 was a
same-size swap of an *existing* instruction: a constant here, a register there, or a NOP. Those
never needed an assembler, because the encoding was already in the image and only an immediate
field moved. Authoring a dispatch stub into dead space does need one — it has branches whose
targets are computed — so this module encodes exactly the handful of forms that stub uses and
nothing else. An encoder that covers more than the caller needs is a liability: every unused
form is untested surface.

**The discipline that makes hand-encoding safe here.** Nothing produced by this module is
trusted on inspection. `assert_disassembles_to` re-reads each emitted instruction with capstone
and compares the mnemonic and operands against what the caller *said* it was building; the
callers use it on every instruction. And each encoder is checked against a real instruction
already in 13.6 (`tests/test_thumb.py`), so a wrong encoding fails against Firefly's own bytes
rather than against my model of the ISA.

Branch offsets are relative to `pc + 4` (the Thumb rule that the PC reads two halfwords ahead),
which is the single easiest thing to get wrong and the reason every branch here takes its own
address explicitly rather than inferring one.
"""
from __future__ import annotations

import struct

try:
    import capstone
except ImportError:                                     # pragma: no cover
    capstone = None

from .theme import CapstoneUnavailable

# Condition codes, ARM encoding.
COND = {"eq": 0x0, "ne": 0x1, "cs": 0x2, "cc": 0x3, "mi": 0x4, "pl": 0x5, "vs": 0x6,
        "vc": 0x7, "hi": 0x8, "ls": 0x9, "ge": 0xA, "lt": 0xB, "gt": 0xC, "le": 0xD}

NOP2 = bytes.fromhex("00bf")
NOP4 = bytes.fromhex("aff30080")


def _rel(at: int, target: int) -> int:
    """Branch displacement from the instruction at `at` to `target` (PC reads at `at + 4`)."""
    if (at & 1) or (target & 1):
        raise ValueError(f"unaligned branch {at:#x} -> {target:#x}")
    return target - (at + 4)


def b_w(at: int, target: int) -> bytes:
    """`b.w target` — T4 unconditional branch, 4 bytes, +-16 MB."""
    off = _rel(at, target)
    if not -(1 << 24) <= off < (1 << 24) or off & 1:
        raise ValueError(f"b.w out of range: {off}")
    off >>= 1
    s = (off >> 23) & 1
    i1 = (off >> 22) & 1
    i2 = (off >> 21) & 1
    imm10 = (off >> 11) & 0x3FF
    imm11 = off & 0x7FF
    j1 = (~i1 ^ s) & 1                       # J1 = NOT(I1 EOR S)
    j2 = (~i2 ^ s) & 1
    hw1 = 0xF000 | (s << 10) | imm10
    hw2 = 0x9000 | (j1 << 13) | (j2 << 11) | imm11
    return struct.pack("<HH", hw1, hw2)


def b_n(at: int, target: int) -> bytes:
    """`b target` — T2 unconditional branch, 2 bytes, +-2 KB."""
    off = _rel(at, target)
    if not -2048 <= off < 2048 or off & 1:
        raise ValueError(f"b (narrow) out of range: {off}")
    return struct.pack("<H", 0xE000 | ((off >> 1) & 0x7FF))


def bcc_n(at: int, target: int, cond: str) -> bytes:
    """`b<cond> target` — T1 conditional branch, 2 bytes, +-256 B."""
    if cond not in COND:
        raise ValueError(f"unknown condition {cond!r}")
    off = _rel(at, target)
    if not -256 <= off < 256 or off & 1:
        raise ValueError(f"b{cond} out of range: {off}")
    return struct.pack("<H", 0xD000 | (COND[cond] << 8) | ((off >> 1) & 0xFF))


def movw(rd: int, imm16: int) -> bytes:
    """`movw rD,#imm16` — T3, 4 bytes."""
    if not 0 <= imm16 <= 0xFFFF:
        raise ValueError(f"imm16 out of range: {imm16:#x}")
    if not 0 <= rd <= 15:
        raise ValueError(f"bad register r{rd}")
    hw1 = 0xF240 | (((imm16 >> 11) & 1) << 10) | ((imm16 >> 12) & 0xF)
    hw2 = (((imm16 >> 8) & 7) << 12) | (rd << 8) | (imm16 & 0xFF)
    return struct.pack("<HH", hw1, hw2)


def movs_imm(rd: int, imm8: int) -> bytes:
    """`movs rD,#imm8` — T1, 2 bytes, low registers only."""
    if not 0 <= imm8 <= 0xFF:
        raise ValueError(f"imm8 out of range: {imm8:#x}")
    if not 0 <= rd <= 7:
        raise ValueError(f"movs T1 needs a low register, got r{rd}")
    return struct.pack("<H", 0x2000 | (rd << 8) | imm8)


def cmp_reg(rn: int, rm: int) -> bytes:
    """`cmp rN,rM` — T1, 2 bytes, low registers only."""
    for r in (rn, rm):
        if not 0 <= r <= 7:
            raise ValueError(f"cmp T1 needs low registers, got r{r}")
    return struct.pack("<H", 0x4280 | (rm << 3) | rn)


def strb_imm12(rt: int, rn: int, imm12: int) -> bytes:
    """`strb.w rT,[rN,#imm12]` — T3, 4 bytes."""
    if not 0 <= imm12 <= 0xFFF:
        raise ValueError(f"imm12 out of range: {imm12:#x}")
    return struct.pack("<HH", 0xF880 | rn, (rt << 12) | imm12)


def bl(at: int, target: int) -> bytes:
    """`bl target` — T1 (32-bit) branch-with-link, 4 bytes, +-16 MB.

    Identical to `b_w` except the second halfword's fixed field is `0xD000` (link) rather than
    `0x9000`. Used only to retarget the detoured caller's own `bl`.
    """
    off = _rel(at, target)
    if not -(1 << 24) <= off < (1 << 24) or off & 1:
        raise ValueError(f"bl out of range: {off}")
    off >>= 1
    s = (off >> 23) & 1
    i1 = (off >> 22) & 1
    i2 = (off >> 21) & 1
    imm10 = (off >> 11) & 0x3FF
    imm11 = off & 0x7FF
    j1 = (~i1 ^ s) & 1
    j2 = (~i2 ^ s) & 1
    hw1 = 0xF000 | (s << 10) | imm10
    hw2 = 0xD000 | (j1 << 13) | (j2 << 11) | imm11
    return struct.pack("<HH", hw1, hw2)


def ldr_literal(rt: int, at: int, target: int) -> bytes:
    """`ldr rT,[pc,#imm]` — T1 literal (pool) load, 2 bytes, low registers only.

    The firmware's own idiom for materialising a 32-bit constant (there is no `movt` anywhere in
    13.6): the word lives in a literal pool and `pc` reads from `(at + 4) & ~3`. `target` is the
    absolute address of the pool word; the displacement must be a non-negative multiple of 4 in
    `0..1020`.
    """
    if not 0 <= rt <= 7:
        raise ValueError(f"ldr (literal) T1 needs a low register, got r{rt}")
    pc = (at + 4) & ~3
    off = target - pc
    if off < 0 or off > 1020 or off & 3:
        raise ValueError(f"ldr (literal) displacement out of range: {off}")
    return struct.pack("<H", 0x4800 | (rt << 8) | (off >> 2))


def ldr_literal_w(rt: int, at: int, target: int) -> bytes:
    """`ldr.w rT,[pc,#+-imm12]` — T2 literal (pool) load, 4 bytes, +-4095 from `(at + 4) & ~3`.

    The wide sibling of `ldr_literal`, for a routine long enough that its pool sits past T1's
    1020-byte reach. `rT` of 13/15 writes sp/pc and is refused."""
    if not 0 <= rt <= 12 and rt != 14:
        raise ValueError(f"ldr.w (literal) needs r0-r12 or lr, got r{rt}")
    off = target - ((at + 4) & ~3)
    if not -0xFFF <= off <= 0xFFF:
        raise ValueError(f"ldr.w (literal) displacement out of range: {off}")
    u = 1 if off >= 0 else 0
    return struct.pack("<HH", 0xF85F | (u << 7), (rt << 12) | abs(off))


def ldr_imm(rt: int, rn: int, imm: int) -> bytes:
    """`ldr rT,[rN,#imm]` — T1, 2 bytes, low registers; `imm` is a byte offset (0..124, /4)."""
    for r in (rt, rn):
        if not 0 <= r <= 7:
            raise ValueError(f"ldr T1 needs low registers, got r{r}")
    if imm & 3 or not 0 <= imm <= 124:
        raise ValueError(f"ldr T1 imm out of range: {imm}")
    return struct.pack("<H", 0x6800 | ((imm >> 2) << 6) | (rn << 3) | rt)


def str_imm(rt: int, rn: int, imm: int) -> bytes:
    """`str rT,[rN,#imm]` — T1, 2 bytes, low registers; `imm` is a byte offset (0..124, /4).

    The mirror of `ldr_imm` (base 0x6000 vs 0x6800); `str r2,[r3]` is `0x601a`, byte-identical
    to the stock cell-45 recolor store at 0x0024A1A6 in 13.6.
    """
    for r in (rt, rn):
        if not 0 <= r <= 7:
            raise ValueError(f"str T1 needs low registers, got r{r}")
    if imm & 3 or not 0 <= imm <= 124:
        raise ValueError(f"str T1 imm out of range: {imm}")
    return struct.pack("<H", 0x6000 | ((imm >> 2) << 6) | (rn << 3) | rt)


def ldrb_imm(rt: int, rn: int, imm5: int) -> bytes:
    """`ldrb rT,[rN,#imm5]` — T1, 2 bytes, low registers; `imm5` is a byte offset (0..31)."""
    for r in (rt, rn):
        if not 0 <= r <= 7:
            raise ValueError(f"ldrb T1 needs low registers, got r{r}")
    if not 0 <= imm5 <= 31:
        raise ValueError(f"ldrb T1 imm5 out of range: {imm5}")
    return struct.pack("<H", 0x7800 | (imm5 << 6) | (rn << 3) | rt)


def str_sp_imm(rt: int, imm: int) -> bytes:
    """`str rT,[sp,#imm]` — T2, 2 bytes, low register; `imm` is a byte offset (0..1020, /4)."""
    if not 0 <= rt <= 7:
        raise ValueError(f"str T2 needs a low register, got r{rt}")
    if imm & 3 or not 0 <= imm <= 1020:
        raise ValueError(f"str T2 sp imm out of range: {imm}")
    return struct.pack("<H", 0x9000 | (rt << 8) | (imm >> 2))


def cmp_imm8(rn: int, imm8: int) -> bytes:
    """`cmp rN,#imm8` — T1, 2 bytes, low registers only."""
    if not 0 <= rn <= 7:
        raise ValueError(f"cmp T1 needs a low register, got r{rn}")
    if not 0 <= imm8 <= 0xFF:
        raise ValueError(f"imm8 out of range: {imm8:#x}")
    return struct.pack("<H", 0x2800 | (rn << 8) | imm8)


def sub_sp_imm(imm: int) -> bytes:
    """`sub sp,#imm` — T1, 2 bytes; `imm` is a byte amount (0..508, /4)."""
    if imm & 3 or not 0 <= imm <= 508:
        raise ValueError(f"sub sp imm out of range: {imm}")
    return struct.pack("<H", 0xB080 | (imm >> 2))


def add_sp_imm(imm: int) -> bytes:
    """`add sp,#imm` — T2, 2 bytes; `imm` is a byte amount (0..508, /4)."""
    if imm & 3 or not 0 <= imm <= 508:
        raise ValueError(f"add sp imm out of range: {imm}")
    return struct.pack("<H", 0xB000 | (imm >> 2))


def mov_reg(rd: int, rm: int) -> bytes:
    """`mov rD,rM` — T1 (any register, including sp/lr), 2 bytes."""
    for r in (rd, rm):
        if not 0 <= r <= 15:
            raise ValueError(f"bad register r{r}")
    return struct.pack("<H", 0x4600 | (((rd >> 3) & 1) << 7) | ((rm & 0xF) << 3) | (rd & 7))


def blx_reg(rm: int) -> bytes:
    """`blx rM` — T1, 2 bytes; call through a register (any register)."""
    if not 0 <= rm <= 15:
        raise ValueError(f"bad register r{rm}")
    return struct.pack("<H", 0x4780 | (rm << 3))


def ands_reg(rd: int, rm: int) -> bytes:
    """`ands rD,rM` — T1 bitwise-AND, 2 bytes, low registers only (rD &= rM)."""
    for r in (rd, rm):
        if not 0 <= r <= 7:
            raise ValueError(f"ands T1 needs low registers, got r{r}")
    return struct.pack("<H", 0x4000 | (rm << 3) | rd)


def ldrh_imm(rt: int, rn: int, imm5: int) -> bytes:
    """`ldrh rT,[rN,#imm5]` — T1, 2 bytes, low registers; imm5 is a byte offset, must be even."""
    for r in (rt, rn):
        if not 0 <= r <= 7:
            raise ValueError(f"ldrh T1 needs low registers, got r{r}")
    if not (0 <= imm5 <= 62 and imm5 % 2 == 0):
        raise ValueError(f"ldrh T1 imm must be even in 0..62, got {imm5}")
    return struct.pack("<H", 0x8800 | ((imm5 >> 1) << 6) | (rn << 3) | rt)


def strh_imm(rt: int, rn: int, imm5: int) -> bytes:
    """`strh rT,[rN,#imm5]` — T1, 2 bytes, low registers; imm5 is a byte offset, must be even."""
    for r in (rt, rn):
        if not 0 <= r <= 7:
            raise ValueError(f"strh T1 needs low registers, got r{r}")
    if not (0 <= imm5 <= 62 and imm5 % 2 == 0):
        raise ValueError(f"strh T1 imm must be even in 0..62, got {imm5}")
    return struct.pack("<H", 0x8000 | ((imm5 >> 1) << 6) | (rn << 3) | rt)


def lsrs_imm(rd: int, rm: int, imm5: int) -> bytes:
    """`lsrs rD,rM,#imm5` — T1 logical shift right, 2 bytes, low registers; `imm5` is 1..31.

    `imm5 == 0` encodes a shift of **32** in this encoding, not a shift of 0, so it is refused
    rather than silently assembled into something the caller did not ask for.
    """
    for r in (rd, rm):
        if not 0 <= r <= 7:
            raise ValueError(f"lsrs T1 needs low registers, got r{r}")
    if not 1 <= imm5 <= 31:
        raise ValueError(f"lsrs T1 imm5 out of range (1..31, 0 means 32): {imm5}")
    return struct.pack("<H", 0x0800 | (imm5 << 6) | (rm << 3) | rd)


def adds_imm8(rdn: int, imm8: int) -> bytes:
    """`adds rDN,#imm8` — T2, 2 bytes, low registers only (rDN += imm8)."""
    if not 0 <= rdn <= 7:
        raise ValueError(f"adds T2 needs a low register, got r{rdn}")
    if not 0 <= imm8 <= 0xFF:
        raise ValueError(f"imm8 out of range: {imm8:#x}")
    return struct.pack("<H", 0x3000 | (rdn << 8) | imm8)


def subs_imm8(rdn: int, imm8: int) -> bytes:
    """`subs rDN,#imm8` — T2, 2 bytes, low registers only (rDN -= imm8)."""
    if not 0 <= rdn <= 7:
        raise ValueError(f"subs T2 needs a low register, got r{rdn}")
    if not 0 <= imm8 <= 0xFF:
        raise ValueError(f"imm8 out of range: {imm8:#x}")
    return struct.pack("<H", 0x3800 | (rdn << 8) | imm8)


def adds_reg(rd: int, rn: int, rm: int) -> bytes:
    """`adds rD,rN,rM` — T1, 2 bytes, low registers only."""
    for r in (rd, rn, rm):
        if not 0 <= r <= 7:
            raise ValueError(f"adds T1 needs low registers, got r{r}")
    return struct.pack("<H", 0x1800 | (rm << 6) | (rn << 3) | rd)


def subs_reg(rd: int, rn: int, rm: int) -> bytes:
    """`subs rD,rN,rM` — T1 register subtract, 2 bytes, low registers only (rD = rN - rM).

    The register-register mirror of `adds_reg` (base 0x1A00 vs 0x1800). Authored for the 48V
    battery routine's `hi - raw` clamp arithmetic, where the minuend is a register (the `hi`
    literal materialised by `movw`), so `subs_imm8` (which subtracts an immediate) does not fit.
    """
    for r in (rd, rn, rm):
        if not 0 <= r <= 7:
            raise ValueError(f"subs T1 needs low registers, got r{r}")
    return struct.pack("<H", 0x1A00 | (rm << 6) | (rn << 3) | rd)


def muls(rdm: int, rn: int) -> bytes:
    """`muls rDM,rN,rDM` — T1 multiply, 2 bytes, low registers only (rDM = rN * rDM).

    The destination is also the second source: that asymmetry is the encoding's, not a
    convenience, and it is why the operand order in the disassembly reads `rDM, rN, rDM`.
    Stock 13.6 contains no `muls` at all (both sections, both alignments), so unlike every
    other encoder here this one is checked only against capstone — stated in its test.
    """
    for r in (rdm, rn):
        if not 0 <= r <= 7:
            raise ValueError(f"muls T1 needs low registers, got r{r}")
    return struct.pack("<H", 0x4340 | (rn << 3) | rdm)


def ldr_imm12(rt: int, rn: int, imm12: int) -> bytes:
    """`ldr.w rT,[rN,#imm12]` — T3, 4 bytes. The wide sibling of `ldr_imm`, needed for the
    GuiCodeState millisecond clock at `[r4,#0x204]` (T1's imm5*4 tops out at 124).

    `rN` of 15 is a different instruction entirely (a PC-relative literal load) and `rT` of
    13/15 writes sp/pc, so all three are refused rather than silently encoded."""
    if not 0 <= rt <= 12 or not 0 <= rn <= 14:
        raise ValueError(f"bad register r{rt}/r{rn} (rT must be r0-r12, rN r0-r14; "
                         "rN=pc is ldr_literal, not this form)")
    if not 0 <= imm12 <= 0xFFF:
        raise ValueError(f"imm12 out of range: {imm12:#x}")
    return struct.pack("<HH", 0xF8D0 | rn, (rt << 12) | imm12)


def _reglist_mask(regs) -> int:
    mask = 0
    for r in regs:
        if not 0 <= r <= 15 or r == 13:
            raise ValueError(f"bad register in list: r{r}")
        if mask & (1 << r):
            raise ValueError(f"duplicate register r{r} in list")
        mask |= 1 << r
    return mask


def push_reglist(regs) -> bytes:
    """`push {…}` — T1, 2 bytes. `regs` are r0-r7 and/or lr (14); pc is not allowed."""
    mask = _reglist_mask(regs)
    if mask & 0x8000:
        raise ValueError("push T1 cannot push pc")
    if mask & ~0x40FF:
        raise ValueError("push T1 register list must be within {r0-r7, lr}")
    m = 1 if (mask & (1 << 14)) else 0
    return struct.pack("<H", 0xB400 | (m << 8) | (mask & 0xFF))


def pop_w_reglist(regs) -> bytes:
    """`pop.w {…}` — T2 (`ldmia.w sp!`), 4 bytes. Needed because the T1 `pop` can restore pc but
    **not** lr, and the tail-call contract requires restoring lr without branching.
    """
    mask = _reglist_mask(regs)
    if (mask & (1 << 14)) and (mask & (1 << 15)):
        raise ValueError("pop.w cannot restore both lr and pc")
    return struct.pack("<HH", 0xE8BD, mask)


class Routine:
    """An authored routine as a stream of sized instructions, labels and a literal pool.

    `emit(size, fn)` appends one instruction: `fn(here, sym)` returns `(bytes, text)`, where
    `sym(name)` is a label's or pool word's absolute address. `assemble` fixes every label in a
    size pass, places the pool 4-aligned after the code, encodes, and verifies the code with
    `assert_disassembles_to` before returning `(blob, disassembly)`. `pool` maps each pool word's
    name to its value, in pool order.
    """

    def __init__(self, pool: dict, *, wide_literals: bool = False, error=ValueError):
        self.pool = dict(pool)
        self.wide_literals = wide_literals
        self.error = error
        self.stream: list = []

    def emit(self, size: int, fn) -> None:
        self.stream.append(("ins", size, fn))

    def label(self, name: str) -> None:
        self.stream.append(("lbl", name))

    def ldr_lit(self, rt: int, name: str) -> None:
        """`ldr rT, =pool[name]`: T1 (2 bytes), or T2 `ldr.w` (4) when `wide_literals`."""
        if self.wide_literals:
            def fn(h, s):
                off = s(name) - ((h + 4) & ~3)
                text = f"ldr.w r{rt}, [pc, #{off:#x}]" if off >= 0 else \
                    f"ldr.w r{rt}, [pc, #-{-off:#x}]"
                return ldr_literal_w(rt, h, s(name)), text
            self.emit(4, fn)
        else:
            def fn(h, s):
                off = s(name) - ((h + 4) & ~3)
                text = f"ldr r{rt}, [pc, #{off:#x}]" if off else f"ldr r{rt}, [pc]"
                return ldr_literal(rt, h, s(name)), text
            self.emit(2, fn)

    def assemble(self, code_load_addr: int, entry_off: int = 0) -> tuple[bytes, list]:
        labels: dict = {}
        code_len = 0
        for item in self.stream:
            if item[0] == "lbl":
                labels[item[1]] = code_len
            else:
                code_len += item[1]
        entry = code_load_addr + entry_off
        pool_off = (entry_off + code_len + 3) & ~3
        pool_abs = {n: code_load_addr + pool_off + i * 4 for i, n in enumerate(self.pool)}

        def sym(name):
            return entry + labels[name] if name in labels else pool_abs[name]

        code = bytearray()
        expected: list = []
        for item in self.stream:
            if item[0] == "lbl":
                continue
            _k, size, fn = item
            raw, text = fn(entry + len(code), sym)
            if len(raw) != size:
                raise self.error(f"size mismatch at {entry + len(code):#x}: {text!r}")
            code += raw
            expected.append(text)
        assert_disassembles_to(bytes(code), entry, expected)

        blob = bytearray(entry_off) + code
        while len(blob) < pool_off:
            blob += b"\x00"
        for n in self.pool:
            blob += struct.pack("<I", self.pool[n])
        assert len(blob) % 4 == 0
        return bytes(blob), disassemble(bytes(code), entry)


def disassemble(code: bytes, at: int) -> list:
    """`[(address, "mnemonic operands"), ...]` for `code` placed at `at`."""
    if capstone is None:
        raise CapstoneUnavailable("capstone is required to verify authored Thumb-2")
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    return [(i.address, f"{i.mnemonic} {i.op_str}".strip()) for i in md.disasm(code, at)]


def assert_disassembles_to(code: bytes, at: int, expected: list) -> None:
    """Re-read authored bytes and require them to say what the author claimed.

    This is the whole safety argument for emitting hand-built machine code: the encoder's
    output is checked against an independent decoder, instruction for instruction, before it
    is written into an image someone will flash.
    """
    got = [text for _addr, text in disassemble(code, at)]
    want = [e.strip() for e in expected]
    if got != want:
        raise ValueError(
            "authored code does not disassemble as intended:\n"
            + "\n".join(f"  {i:2d}  got {g!r:40s} want {w!r}"
                        for i, (g, w) in enumerate(
                            __import__("itertools").zip_longest(got, want, fillvalue="<none>"))))
