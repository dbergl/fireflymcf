"""The incremental GuiCode decodes against the loops they replaced, on synthetic Thumb code.

`theme.LinearSweep` re-decodes only around changed bytes and replays a previous decode
everywhere else; `tablegrow._cmp_w_sites` re-decodes only the offsets a change can reach;
`tanknames._disassemble` answers its pool-aware calls from the shared decode. Each must give
exactly what the original full loop gives, for any edit: these tests run the originals (copied
below as they were) against them over seeded random code dense in IT blocks, PC-relative loads
and 32-bit encodings, through chains of edits that also grow and shrink the code.
"""
import random
import re

import pytest

capstone = pytest.importorskip("capstone")

from fireflymcf import tablegrow, tanknames, theme  # noqa: E402

BASE = 0x240000
_PCREL_LDR = re.compile(r"\[pc, #(-?)0x([0-9a-f]+)\]$")


def _orig_lits(code, base):
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    out, pos, limit = [], 0, len(code) - 4
    while pos < len(code):
        decoded_end = pos
        for addr, size, mnemonic, op_str in md.disasm_lite(code[pos:], base + pos):
            decoded_end = addr - base + size
            if mnemonic in ("ldr", "ldr.w"):
                m = _PCREL_LDR.search(op_str)
                if m:
                    disp = int(m.group(2), 16) * (-1 if m.group(1) else 1)
                    lit_off = (addr & ~3) + 4 + disp - base
                    if 0 <= lit_off <= limit:
                        out.append(lit_off)
        if decoded_end <= pos:
            decoded_end = pos + 2
        pos = decoded_end
    return out


def _orig_runs(code, base):
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    md.detail = True
    out, pos = [], 0
    while pos < len(code):
        decoded_end, cur = pos, []
        for ins in md.disasm(code[pos:], base + pos):
            decoded_end = ins.address - base + ins.size
            cur.append((ins.address, ins.size, ins.mnemonic, ins.op_str))
        if decoded_end <= pos:
            decoded_end = pos + 2
        out.append((pos, decoded_end, cur))
        pos = decoded_end
    return out


def _orig_cmp_w(code, base):
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    out = []
    for off in range(0, len(code) - 4, 2):
        insns = list(md.disasm(code[off:off + 4], base + off))
        if insns and insns[0].mnemonic == "cmp.w":
            out.append((off, insns[0].size, insns[0].op_str))
    return tuple(out)


def _orig_pool_sweep(code, base):
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    md.detail = True

    def sweep(pools):
        out, off = [], 0
        while off < len(code) - 1:
            if base + off in pools:
                off += 2
                continue
            moved = False
            for ins in md.disasm(code[off:], base + off):
                if ins.address in pools or (ins.size == 4 and ins.address + 2 in pools):
                    break
                out.append(ins)
                moved = True
                off = ins.address - base + ins.size
            if not moved:
                off += 2
        return out

    pools: set = set()
    for _ in range(4):
        out = sweep(pools)
        grown = pools | tanknames._pool_words(code, base, out)
        if grown == pools:
            break
        pools = grown
    return [(i.address, i.size, i.mnemonic, i.op_str) for i in out]


def _halfword(rng):
    r = rng.random()
    if r < 0.08:                                       # it / itt / ite ... with a condition
        return 0xBF00 | rng.randrange(0x10) << 4 | rng.choice([8, 4, 0xC, 2, 6, 0xA, 0xE, 1])
    if r < 0.18:                                       # ldr rX, [pc, #imm8]
        return 0x4800 | rng.randrange(0x800)
    if r < 0.40:                                       # first half of a 32-bit encoding
        return rng.choice([0xE800, 0xF000, 0xF800]) | rng.randrange(0x800)
    if r < 0.45:                                       # cmp.w rN, #imm
        return 0xF1B0 | rng.randrange(0x10)
    return rng.randrange(0x10000)


def _code(rng, n_halfwords):
    return b"".join(_halfword(rng).to_bytes(2, "little") for _ in range(n_halfwords))


def _edit(rng, code):
    c = bytearray(code)
    r = rng.random()
    if r < 0.15:                                       # an appended routine
        c += _code(rng, rng.choice([1, 2, 20, 200]))
    elif r < 0.25 and len(c) > 400:                    # a shorter section
        del c[len(c) - 2 * rng.choice([1, 2, 50]):]
    for _ in range(rng.choice([1, 2, 5, 17, 40])):
        at = rng.randrange(0, len(c) - 4)
        if rng.random() < 0.5:                         # a halfword the generator could make
            c[at & ~1:(at & ~1) + 2] = _halfword(rng).to_bytes(2, "little")
        else:
            for k in range(rng.choice([1, 2, 4])):
                c[at + k] = rng.randrange(256)
    return bytes(c)


@pytest.fixture(autouse=True)
def _fresh_recent():
    for recent in theme._RECENT_SWEEPS.values():
        recent.clear()
    tablegrow._RECENT_CMP_W.clear()
    yield


def _norm(runs):
    return [(a, b, [(i.address, i.size, i.mnemonic, i.op_str) for i in run])
            for a, b, run in runs]


@pytest.mark.parametrize("seed", range(6))
def test_sweeps_match_the_original_loops_through_a_chain_of_edits(seed):
    rng = random.Random(seed)
    code = _code(rng, 6000)
    for step in range(25):
        assert _norm(theme.detail_runs(code, BASE)) == _orig_runs(code, BASE), step
        assert theme._pcrel_literal_offsets.__wrapped__(code, BASE) == _orig_lits(code, BASE), step
        code = _edit(rng, code)


def test_an_update_really_reuses_the_previous_decode():
    rng = random.Random(42)
    code = _code(rng, 6000)
    first = theme.LinearSweep.of(code, BASE, detail=True)
    edited = bytearray(code)
    edited[6000] ^= 0xFF
    second = theme.LinearSweep.of(bytes(edited), BASE, detail=True)
    old = {id(i) for i in first.items if i is not None}
    shared = sum(1 for i in second.items if i is not None and id(i) in old)
    assert shared > 0.5 * len(first.items)          # 88% on this seed


@pytest.mark.parametrize("seed", range(4))
def test_cmp_w_scan_matches_the_original_through_a_chain_of_edits(seed):
    rng = random.Random(100 + seed)
    code = _code(rng, 6000)
    for step in range(25):
        assert tablegrow._cmp_w_sites.__wrapped__(code, BASE) == _orig_cmp_w(code, BASE), step
        code = _edit(rng, code)


@pytest.mark.slow
@pytest.mark.parametrize("seed", range(4))
def test_pool_aware_decode_matches_the_original_through_a_chain_of_edits(seed):
    rng = random.Random(200 + seed)
    code = _code(rng, 6000)
    for step in range(10):
        got = [(i.address, i.size, i.mnemonic, i.op_str)
               for i in tanknames._disassemble(code, BASE)]
        assert got == _orig_pool_sweep(code, BASE), step
        code = _edit(rng, code)
