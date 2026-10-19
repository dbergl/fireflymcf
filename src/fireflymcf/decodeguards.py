"""Readouts the panel silently REFUSES to update — the range fences in the per-tick decoder.

`FUN_00247BF8` decodes received values into display cells once per tick. Several of those
decodes sit behind a **magnitude test on the value itself**, and when the test fails the block is
skipped: the cell keeps whatever it had and **nothing on screen says the value was rejected**.
That is a silent failure mode, and it cost a live coach session before this module existed
(`BUS-BASELINE.md` **PC.55**): `Absorb`/`Float` were injected at 28.8 V / 27.2 V, the panel ignored
them, and the obvious readings — wrong DGN, wrong byte, wrong filter — were all wrong. The DGN,
byte and filter were right; the *value* was out of range.

The `Absorb`/`Float` fence is the worst case because it is **two-sided and inherited**: the test is
on `Float` and it gates `Absorb` as well, so a perfectly good absorb value is discarded because a
different field is out of range. On a 48 V coach it can never pass, since it admits only
raw 239-361 (12.0-18.0 V displayed).

**The scanning trap, stated because getting it wrong hides the very case that motivated this.** A
fence belongs to the **block**, not to the last field loaded. The first cut of this scanner reset
its guard list on every `ldrsh.w [r4,#imm]`, and since the `Absorb` load sits *between* the fence
and the store, that reset dropped the fence and reported the site as unguarded. Guards are
therefore cleared only at the store.

`r4` is the receive-slot array base (`FUN_00247BF8`'s parameter), so `[r4,#imm]` is **variable
`imm/2`** — the same numbering `guidata.variable_table` uses.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import memo, slotsource

DECODER_START = 0x247BF8
DECODER_END = 0x24AB00
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


@memo.content_cached()
def range_guards(data: bytes, start: int = DECODER_START, end: int = DECODER_END) -> list:
    """Every per-tick decode whose store is skipped when the value fails a magnitude test."""
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
