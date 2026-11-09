"""Readouts the panel silently REFUSES to update -- the range fences in the per-tick decoder.

`FUN_00247BF8` decodes received values into display cells once per tick. Some of those decodes
sit behind a **magnitude test on the value itself**, and **nothing on screen says the value was
rejected**. That is a silent failure mode, and it cost a live coach session before this module
existed (`BUS-BASELINE.md` **PC.55**): `Absorb`/`Float` were injected at 28.8 V / 27.2 V, the
panel ignored them, and the obvious readings -- wrong DGN, wrong byte, wrong filter -- were all
wrong. The DGN, byte and filter were right; the *value* was out of range.

**What a reported guard does (GUICODE Finding 91, 2026-11-08).** The scanner reports every
`cmp #imm>16` + conditional branch between a block's slot loads and its store; what the branch
does differs, and only one of 13.6's seven guards truly refuses a value:

- **Absorb/Float (vars 207, 206; 13.6 `0x24A81A cmp #239; ble`, `0x24A826 cmp #362; bge`
  after `0x24A824 adds r3, #1`) skips its store** (to `0x24A86E`, past the Absorb store
  `0x24A844`). The fence is **two-sided and inherited**: the test is on `Float` and it gates
  `Absorb` as well, so a good absorb value is discarded because a different field is out of
  range. It admits Float **raw 239-360** (12.0-18.0 V displayed); on a 48 V coach it can never
  pass. A rejected decode does **not** keep the old value: the cell is rewritten every tick from
  a config byte (13.6 `0x247C34`, 14.7 `0x247CE8`), so the readout reverts to that (the
  "25.5 V" of the recipe note). Emulated on stock 14.7 2026-11-08 (239 and 360 stored, 238, 361
  and 1080 reverted); 13.6 has the same instructions.
- **Vars 51, 52, 47, 48 and 211 substitute a fixed value**: out of range, the block stores 100
  (51, 52: 13.6 `0x2499FA movs r3, #0x64`), 1000 (47, 48: `0x24A3B0`/`0x24A3B6
  mov.w r3, #0x3E8`) or 60 (211: `0x24A3C0 movs r3, #0x3C`) instead of skipping. 14.7's var-52
  bound admits 0..99 where 13.6's admits 1..99 (14.7 `0x249E06 cmp #99`).
- **Var 271 is a scanner artefact**: the fence (13.6 `0x247CB8 cmp #99; bls`) tests a config
  byte (`0x247CB2 ldrb r2, [r3, #0x1e]`) and skips only `movs r2, #0x50`; the store of GuiCodeState
  byte `+0x21F` that follows is unconditional (inference from the listing, not emulated).

The scanner is left as it is so its output does not change; read its guards with this list.

**The scanning trap, stated because getting it wrong hides the very case that motivated this.** A
fence belongs to the **block**, not to the last field loaded. The first cut of this scanner reset
its guard list on every `ldrsh.w [r4,#imm]`, and since the `Absorb` load sits *between* the fence
and the store, that reset dropped the fence and reported the site as unguarded. Guards are
therefore cleared only at the store.

`r4` is the receive-slot array base (`FUN_00247BF8`'s parameter), so `[r4,#imm]` is **variable
`imm/2`** -- the same numbering `guidata.variable_table` uses.

**The range (2026-11-08).** The scan covers the per-tick decoder's whole extent, located from its
own code (`decoder_range`, with `decoder_range_proof` citing the addresses):

1. the start is the per-tick callee `hooklocate` locates (13.6 `0x00247BF8 push {r4-r7, lr}`);
2. exactly one `tanknames.decoder_ranges` region starts there, and its end (the first r4 write
   after `mov r4, r0`) must be a `pop {.., r4, .., pc}` -- the epilogue (13.6 `0x0024EDB0`);
3. no other instruction in between writes pc except direct branches and calls; a `tbb`/`tbh` is
   admitted only as a bounded switch (`cmp rX, #N; bhi; tbb [pc, rX]`) whose every arm lands
   inside, and its table is stepped over;
4. every branch that leaves the extent lands in an out-of-line tail block after the epilogue
   that reaches an unconditional `b` back inside within `TAIL_LIMIT` bytes and loads no
   `[r4,#imm]` (13.6 has five, `0x24EDB2`-`0x24EDDE`), so no decode lives outside the range.
   The tail rule sees only direct loads through r4, not loads through a copy of it -- the same
   limit the guard scanner has; every branch target inside the extent must be an instruction
   boundary.

Anything else refuses `DecoderRangeUnknown`. The located ranges are 13.6 `(0x247BF8, 0x24EDB0)`,
14.7 `(0x247CAC, 0x24F1FC)` and 11.23 `(0x247590, 0x24CDFA)`. Until 2026-11-08 the end was a 13.6
pin, `0x24AB00`, a word inside the literal pool `0x24AAE4`-`0x24AB2F` with no recorded rationale;
the located range finds the same seven guards on 13.6, and on 14.7 also the Absorb/Float fence
(`0x0024ABE0 cmp #239`), which lies past that old end. An explicit `start`/`end` is the caller's
own claim and is scanned as given.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import hooklocate, memo, slotsource

MIN_RANGE_IMMEDIATE = 16          # below this a `cmp` is an enum/state test, not a range fence
_SKIP_BRANCHES = ("ble", "bge", "blt", "bgt", "bls", "bhi")


@dataclass
class RangeGuard:
    """One decode site whose store is fenced by a magnitude test."""

    cell: int
    variables: list
    fences: list                  # (instruction address, immediate, branch mnemonic)

    @property
    def two_sided(self) -> bool:
        return len(self.fences) >= 2


class DecoderRangeUnknown(ValueError):
    """The per-tick decoder's extent does not prove out on this image."""
    code = "DECODER_RANGE_UNKNOWN"


TAIL_LIMIT = 0x40           # an out-of-line tail block reaches its branch back within this


def decoder_range(data: bytes) -> tuple[int, int]:
    """(start, end) of the per-tick decoder: the located callee to its one epilogue (exclusive).
    Refuses (`DecoderRangeUnknown`) whenever the extent does not prove out
    (`decoder_range_proof` says how it did)."""
    start, end, _proof = _locate(data)
    return start, end


def decoder_range_proof(data: bytes) -> str:
    """Why `decoder_range(data)` is the decoder's whole extent, citing addresses."""
    return _locate(data)[2]


def _locate(data: bytes) -> tuple[int, int, str]:
    # Not memoised up to the region lookup: its inputs (`hooklocate`, `tanknames.decoder_ranges`)
    # are, and a cache here would serve a stale range past a stubbed input. The walk over the
    # region is, keyed on the bytes, the region and the tail limit (`_walk`).
    from . import tanknames

    try:
        start = hooklocate.locate_hook(data).stock_callee
    except hooklocate.HookNotFound as e:
        raise DecoderRangeUnknown(f"the per-tick hook did not prove out: {e}") from e
    try:
        regions = [r for r in tanknames.decoder_ranges(data) if r[0] == start]
    except tanknames.TankNameError as e:
        raise DecoderRangeUnknown(f"no r4=GuiCodeState region on this image: {e}") from e
    if len(regions) != 1:
        raise DecoderRangeUnknown(
            f"{len(regions)} r4=GuiCodeState regions start at the callee {start:#x} "
            f"(tanknames.decoder_ranges); exactly one is its extent")
    lo, hi = regions[0]
    return _walk(data, lo, hi, TAIL_LIMIT)


@memo.content_cached(copy=None)
def _walk(data: bytes, lo: int, hi: int, tail_limit: int) -> tuple[int, int, str]:
    """Prove `[lo, hi)` is the whole decoder (module docstring, rules 2-4) and say how."""
    import capstone.arm as A
    from . import tanknames

    code, base, ins, at = tanknames._guicode(data)
    if hi not in at:
        raise DecoderRangeUnknown(f"the region end {hi:#x} is not an instruction boundary")
    ep = ins[at[hi]]
    if not _is_pop_r4_pc(ep):
        raise DecoderRangeUnknown(
            f"{hi:#x} `{ep.mnemonic} {ep.op_str}` is not the callee's epilogue "
            f"(`pop {{.., r4, .., pc}}`)")
    tails: dict = {}
    switches = []
    k, k_end = at[lo], at[hi]
    while k < k_end:
        i = ins[k]
        k += 1
        if i.id in (A.ARM_INS_TBB, A.ARM_INS_TBH):
            table_end = _switch(ins, k - 1, code, base, lo, hi, at, A)
            switches.append(i.address)
            while k < k_end and ins[k].address < table_end:    # table bytes, not code
                k += 1
            continue
        if _leaves_unless_branch_or_call(i, A):
            raise DecoderRangeUnknown(
                f"another return inside the callee [{lo:#x}, {hi:#x}): {i.address:#x} "
                f"`{i.mnemonic} {i.op_str}`")
        if i.id not in (A.ARM_INS_B, A.ARM_INS_CBZ, A.ARM_INS_CBNZ):
            continue
        t = _branch_target(i, A)
        if t is None:
            raise DecoderRangeUnknown(f"{i.address:#x} `{i.mnemonic} {i.op_str}` has no target")
        if lo <= t <= hi:
            if t not in at:
                raise DecoderRangeUnknown(
                    f"{i.address:#x} `{i.mnemonic} {i.op_str}` lands at {t:#x}, inside the "
                    f"callee but not on an instruction boundary")
            continue
        if t < hi or t not in at:
            raise DecoderRangeUnknown(
                f"{i.address:#x} `{i.mnemonic} {i.op_str}` leaves the callee to {t:#x}, "
                f"not to a tail block after its epilogue")
        if t not in tails:
            tails[t] = _tail_back(ins, at, t, lo, hi, tail_limit, A, tanknames)
    proof = (f"{lo:#x} `push`..`mov r4, r0` (hooklocate's per-tick callee, the one "
             f"tanknames.decoder_ranges region starting there) to {hi:#x} "
             f"`{ep.mnemonic} {ep.op_str}`, its only return; {len(tails)} out-of-line tail "
             f"blocks ({', '.join(f'{t:#x}..{b:#x}' for t, b in sorted(tails.items()))}) "
             f"rejoin it and load no receive slot"
             + (f"; {len(switches)} bounded switch(es) ({', '.join(f'{a:#x}' for a in switches)}) "
                f"stay inside it" if switches else ""))
    return lo, hi, proof


def _is_pop_r4_pc(i) -> bool:
    import capstone.arm as A
    if i.mnemonic not in ("pop", "pop.w") or i.cc not in (0, A.ARM_CC_AL):
        return False
    regs = {i.reg_name(op.reg) for op in i.operands}
    return {"r4", "pc"} <= regs


def _leaves_unless_branch_or_call(i, A) -> bool:
    """A return or a computed jump: anything that writes pc other than a direct branch or call."""
    if i.id in (A.ARM_INS_B, A.ARM_INS_CBZ, A.ARM_INS_CBNZ, A.ARM_INS_BL, A.ARM_INS_BLX):
        return False
    if i.id in (A.ARM_INS_TBB, A.ARM_INS_TBH, A.ARM_INS_BX, A.ARM_INS_BXJ):
        return True
    try:
        _r, w = i.regs_access()
    except Exception:                                   # pragma: no cover - no detail
        return True
    return "pc" in {i.reg_name(x) for x in w}


def _switch(ins, k, code, base, lo, hi, at, A) -> int:
    """The end of the table of the `tbb`/`tbh` at `ins[k]`, once it is proven a bounded switch
    whose every arm lands inside the callee: `cmp rX, #N; bhi default; tb[bh] [pc, rX]`, with
    N+1 entries, the table ending on an instruction boundary and no arm landing in it. Anything
    else is a computed jump this locator cannot follow."""
    i = ins[k]
    cmp, guard = (ins[k - 2], ins[k - 1]) if k >= 2 else (None, None)
    mem = [op for op in i.operands if op.type == A.ARM_OP_MEM]
    index = i.reg_name(mem[0].mem.index) if mem and mem[0].mem.index else None
    imms = [op.imm for op in cmp.operands if op.type == A.ARM_OP_IMM] if cmp else []
    if not (cmp is not None and cmp.mnemonic in ("cmp", "cmp.w") and len(imms) == 1
            and cmp.reg_name(cmp.operands[0].reg) == index and index != "pc"
            and guard.id == A.ARM_INS_B and guard.cc == A.ARM_CC_HI
            and mem[0].mem.base and i.reg_name(mem[0].mem.base) == "pc"):
        raise DecoderRangeUnknown(
            f"a computed jump inside the callee: {i.address:#x} `{i.mnemonic} {i.op_str}` is not "
            f"a bounded switch (`cmp rX, #N; bhi; tbb/tbh [pc, rX]`)")
    count = imms[0] + 1
    width = 1 if i.id == A.ARM_INS_TBB else 2
    table = i.address + 4
    off = table - base
    raw = bytes(code[off:off + count * width])
    if len(raw) != count * width:
        raise DecoderRangeUnknown(f"the switch table at {table:#x} runs off GuiCode")
    table_end = table + ((count * width + 1) & ~1)
    if table_end not in at:
        raise DecoderRangeUnknown(
            f"the switch table after {i.address:#x} does not end on an instruction "
            f"({table_end:#x})")
    for n in range(count):
        entry = int.from_bytes(raw[n * width:(n + 1) * width], "little")
        arm = table + 2 * entry
        if not (lo < arm < hi and arm in at) or table <= arm < table_end:
            raise DecoderRangeUnknown(
                f"the switch at {i.address:#x} sends case {n} to {arm:#x}, not to an "
                f"instruction inside the callee")
    return table_end


def _branch_target(i, A):
    for op in i.operands:
        if op.type == A.ARM_OP_IMM:
            return op.imm
    return None


def _tail_back(ins, at, t, lo, hi, tail_limit, A, tanknames) -> int:
    """The address of the unconditional `b` back into (lo, hi] that ends the tail block at `t`.
    The block may make calls; it may not load `[r4,#imm]` or `ldm r4` (a decode outside the
    scanned range), branch anywhere else, or return. Only direct loads through r4 are seen, not
    loads through a copy of r4 -- the same limit the guard scanner has."""
    k = at[t]
    while k < len(ins):
        j = ins[k]
        if j.address - t > tail_limit:
            break
        if (j.mnemonic.startswith("ldr") and tanknames._r4_disp(j) is not None) or (
                j.mnemonic.startswith("ldm") and j.reg_name(j.operands[0].reg) == "r4"):
            raise DecoderRangeUnknown(
                f"the tail block at {t:#x} loads a receive slot: {j.address:#x} "
                f"`{j.mnemonic} {j.op_str}`")
        if j.id in (A.ARM_INS_B, A.ARM_INS_CBZ, A.ARM_INS_CBNZ):
            back = _branch_target(j, A)
            if (j.id == A.ARM_INS_B and j.cc in (0, A.ARM_CC_AL)
                    and back is not None and lo < back <= hi and back in at):
                return j.address
            raise DecoderRangeUnknown(
                f"the tail block at {t:#x} does not rejoin the callee: {j.address:#x} "
                f"`{j.mnemonic} {j.op_str}`")
        if _leaves_unless_branch_or_call(j, A):
            raise DecoderRangeUnknown(
                f"the tail block at {t:#x} does not rejoin the callee: it leaves at "
                f"{j.address:#x} `{j.mnemonic} {j.op_str}`")
        k += 1
    raise DecoderRangeUnknown(
        f"the tail block at {t:#x} does not rejoin the callee within {tail_limit:#x} bytes")


def range_guards(data: bytes, start: int | None = None, end: int | None = None) -> list:
    """Every per-tick decode whose store is skipped when the value fails a magnitude test.
    The default range is `decoder_range(data)`."""
    if start is None or end is None:
        located = decoder_range(data)
        start = located[0] if start is None else start
        end = located[1] if end is None else end
    return _scan(data, start, end)


@memo.content_cached()
def _scan(data: bytes, start: int, end: int) -> list:
    body, base = slotsource._guicode_body(data)
    md = slotsource._disassemble(body, base)
    ins = [i for i in md if start <= i.address < end]
    lit = re.compile(r"^(\w+), \[pc, #(0x[0-9a-f]+|\d+)\]$")
    slot = re.compile(r"^(\w+), \[r4, #(0x[0-9a-f]+)\]$")
    bare = re.compile(r"^(\w+), \[(\w+)\]$")
    loads = ("ldrsh.w", "ldrh.w", "ldrh", "ldrsh", "ldrb.w", "ldrb")

    regs: dict = {}
    variables: list = []
    fences: list = []
    out: list = []
    for k, insn in enumerate(ins):
        m = lit.match(insn.op_str)
        if insn.mnemonic.startswith("ldr") and m:
            at = ((insn.address + 4) & ~3) + int(m.group(2), 0) - base
            if 0 <= at < len(body) - 3:
                regs[m.group(1)] = int.from_bytes(body[at:at + 4], "little")
            continue
        m = slot.match(insn.op_str)
        if insn.mnemonic in loads and m:
            var = int(m.group(2), 0) // 2
            if var not in variables:
                variables.append(var)
            continue                                  # fences persist across loads
        if insn.mnemonic in ("cmp", "cmp.w") and "#" in insn.op_str:
            try:
                value = int(insn.op_str.split("#")[1], 0)
            except ValueError:
                continue
            nxt = ins[k + 1] if k + 1 < len(ins) else None
            if nxt and nxt.mnemonic.split(".")[0] in _SKIP_BRANCHES:
                fences.append((insn.address, value, nxt.mnemonic.split(".")[0]))
            continue
        m = bare.match(insn.op_str)
        if insn.mnemonic.startswith("str") and m and m.group(2) in regs:
            ranged = [f for f in fences if f[1] > MIN_RANGE_IMMEDIATE]
            if variables and ranged:
                out.append(RangeGuard(regs[m.group(2)], list(variables), ranged))
            variables, fences = [], []                 # reset only at the store
    return out


def guard_for_cell(data: bytes, cell: int):
    """The range fence on one display cell, or None."""
    for guard in range_guards(data):
        if guard.cell == cell:
            return guard
    return None
