"""Where a received value goes: from a receive-filter slot read to every store and call it decides.

The receive filter (`MCF-FORMAT.md` §5.4a, LCD `FUN_0021C760`) writes entry `var`'s value to
`register.status_slot_addr(var)`. GuiCode reaches the slot array only through the base the LCD
hands `GuiCode_entry1` in `r0` each paint tick (`GUICODE.md` Finding 33). `state_reach` follows
that base from `GuiCode_entry1` through every register, stack spill and call it is passed on in,
and records every load made through it; a load at `slot_disp(var)` is a read of `var` (the
offset applies the index split: var > 255 sits `0x38` further on, so 13.6's DM_RV var 277 is
`+0x262`, Finding 77). 13.6's per-tick decoder `FUN_00247BF8` keeps the base in `r4`, so most
reads are `ldrsh.w rD,[r4,#2*var]`. An appended routine reads a slot by its absolute address
instead (`ldr rX,=slot` then `ldrsh rD,[rX]`); both are traced here.

Because `state_reach` has followed the base everywhere it goes, the absence of a load is a
guaranteed negative (`unread_guarantee`) unless the base escapes the analysis, which it does not
on any corpus build.

## What the trace follows

`tanknames._trace_from` follows **data** flow only: a register is tainted when an instruction
computes it from a tainted one. That is enough for a tank level (`(100*level+res/2)/res` is
arithmetic on the loaded value), and not enough for most blocks, which *decide* on the value:

```
248d96  ldrsh.w r2,[r4,#0xa0]     ; var 80 (DC_DIMMER_STATUS_3, instance 0x26)
248d9a  cmp     r2,#1
248d9c  beq.w   0x2493a8          ; on: an out-of-line arm loads the ON colour master
248da0  ldr     r1,[pc,#0x280]    ; off: the OFF colour master
248da2  ldr     r0,[r1]
248da4  ldr     r1,[pc,#0x288]    ; both arms meet here
248da6  str     r0,[r1]           ; -> the indicator's colour cell
```

The colour stored at `0x248da6` is not computed from `r2`; which colour is stored is. So this
tracer also follows **control** flow: a compare on a tainted register taints the flags; a
conditional branch on tainted flags (or a `cbz`/`cbnz` on a tainted register) explores both
successors, each along its fall-through path, to the first address both reach (the join). Every
register either arm writes before the join is tainted at the join (its value depends on which arm
ran), and every store either arm makes is a sink with `flow="control"`. A predicated instruction
under tainted flags is treated the same way. When no join is found within `ARM_LIMIT`
instructions, both arms' sinks are kept and the trace ends there.

## Sinks

| kind | meaning |
|---|---|
| `cell` | a store to an absolute SRAM address (a pool literal, possibly `+imm`) |
| `state` | a store to `GuiCodeState + off` (through `r4` or a copy of it) |
| `config` | a store into the config buffer (`[r4,#0x214]`, `config.CONFIG_BUFFER_OFFSET`) at `off` |
| `call` | a `bl`/`blx` reached with a tainted argument register, or inside a decided arm |
| `store?` | a store whose address the tracer could not resolve |

Every sink is a cited instruction address; the sinks are `proven-from-code` as far as "this store
executes with, or because of, the received value". The tracer follows only the paths described
above, so it can miss a sink (a store on an untainted branch's taken side, or past `TRACE_LIMIT`);
it never invents one. The positive control is GuiCode's own bridge dump
(`data/slot_bridge_136.txt`, extracted by Ghidra independently): `tests/test_busmap.py` checks
every clean colour/bool pair it lists is found here.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from . import config, memo, register, tanknames

SLOT_BASE = register.STATUS_SLOT_BASE
TRACE_LIMIT = 400          # instructions one trace may execute, over all its arms
MAX_DEPTH = 6              # nested decided branches explored
ARM_RUN = 160              # instructions a decided arm may run before it is a runaway
ARM_LIMIT = 64             # instructions explored down each arm of a decided branch
MEM_LOOKAHEAD = 24         # instructions a trace keeps going with only a stored cell live, for a
                           # block that re-reads what it just stored (13.6 var 42 @0x24A198)
SRAM_LOW, SRAM_HIGH = 0x20000000, 0x20080000

_FLAG_SETTERS = {"cmp", "cmn", "tst", "teq", "cmp.w", "cmn.w", "tst.w", "teq.w"}


def slot_disp(var: int) -> int:
    """`var`'s slot offset from the slot-array base GuiCode is handed (the index split)."""
    return register.status_slot_addr(var) - SLOT_BASE


@dataclass(frozen=True)
class Sink:
    at: int                  # the store / call instruction
    kind: str                # "cell" | "state" | "config" | "call" | "store?"
    target: int | None       # SRAM address, state/config offset, call target (None: indirect)
    flow: str                # "data": the stored value is computed from the slot
                             # "control": the store runs (or its value is chosen) because of it
    width: int = 0           # bytes stored (0 for a call)
    via: int | None = None   # an indirect call's function-pointer cell (`ldr rX,[cell]; blx rX`)
    args: tuple = ()         # a call's ((reg, kind, value), ...) for r0-r3: kind "abs" (a known
                             # constant), "state" (GuiCodeState), "config", "value" (computed
                             # from the slot), "choice" (chosen because of it)


@dataclass(frozen=True)
class SlotRead:
    at: int                  # the load
    how: str                 # "base" (through the slot-array base, `state_reach`) |
                             # "literal" (the slot's absolute address from a pool word)
    sinks: tuple             # Sink, ...


# ------------------------------------------------------------------------------ the machine

class _St:
    __slots__ = ("taint", "data", "flags", "conc", "mem")

    def __init__(self, taint=None, flags=False, conc=None, mem=None, data=None):
        self.taint = set(taint or ())     # registers carrying the value (or a choice made on it)
        self.data = set(data or ())       # the subset computed from it by data flow alone
        self.flags = flags                # the flags were set from such a register
        self.conc = dict(conc or {})      # register -> ("abs"|"state"|"config"|"mem", value)
        self.mem = set(mem or ())         # absolute addresses this trace has stored the value to

    def copy(self) -> "_St":
        return _St(self.taint, self.flags, self.conc, self.mem, self.data)


def _mem_op(i):
    import capstone.arm as A
    for op in i.operands:
        if op.type == A.ARM_OP_MEM:
            return op
    return None


def _width(m: str) -> int:
    base = m.split(".")[0]
    if base.endswith(("b", "sb")):
        return 1
    if base.endswith(("h", "sh")):
        return 2
    if base.endswith("d"):
        return 8
    return 4


def _is_store(m: str) -> bool:
    return m.startswith("str")


def _is_load(m: str) -> bool:
    return m.startswith("ldr") and not m.startswith("ldrex")


_CONDS = ("eq", "ne", "hs", "lo", "cs", "cc", "mi", "pl", "vs", "vc", "hi", "ls", "ge", "lt",
          "gt", "le")
_COND_BRANCHES = {"b" + c for c in _CONDS}


def _cond_branch(i) -> bool:
    """A conditional branch: `b<cc>` (narrow or `.w`), `cbz` or `cbnz`."""
    m = i.mnemonic
    return m in ("cbz", "cbnz") or m.split(".")[0] in _COND_BRANCHES


def _predicated(i) -> bool:
    import capstone.arm as A
    return i.cc not in (0, A.ARM_CC_AL) and not _cond_branch(i)


def _sets_flags(i) -> bool:
    m = i.mnemonic
    if m in _FLAG_SETTERS:
        return True
    try:
        return bool(i.update_flags)
    except Exception:            # pragma: no cover
        return False


class Tracer:
    """Runs traces over one GuiCode image (`tanknames._guicode`'s pool-aware disassembly)."""

    def __init__(self, data: bytes):
        self.code, self.base, self.ins, self.at = tanknames._guicode(data)

    # -- one instruction ----------------------------------------------------------------
    def _literal(self, i) -> int | None:
        return tanknames._literal(self.code, self.base, i)

    def _addr(self, st: _St, op) -> tuple | None:
        """`(kind, value)` for a `[rn,#disp]` address, from what is known of `rn`."""
        if op.mem.index != 0:
            return None
        rn = self._name(op.mem.base)
        c = st.conc.get(rn)
        if c is None:
            return None
        return (c[0], c[1] + op.mem.disp)

    def _name(self, reg) -> str:
        return self.ins[0].reg_name(reg)

    def step(self, i, st: _St, implicit: bool, sinks: list, want: tuple) -> None:
        """Apply `i` to `st`. `implicit`: inside a decided arm (every write depends on the slot).
        `want`: the address `(kind, value)` whose load *is* the slot read."""
        import capstone.arm as A
        m = i.mnemonic
        under = implicit or (_predicated(i) and st.flags)

        def taint(reg: str, by_data: bool) -> None:
            st.taint.add(reg)
            if by_data:
                st.data.add(reg)
            else:
                st.data.discard(reg)

        def clean(reg: str) -> None:
            st.taint.discard(reg)
            st.data.discard(reg)

        if tanknames._is_call(i):
            args = {"r0", "r1", "r2", "r3"}
            if under or (args & st.taint):
                tgt = tanknames._branch_target(i)
                via = None
                if tgt is None and i.operands and i.operands[0].type == A.ARM_OP_REG:
                    c = st.conc.get(i.reg_name(i.operands[0].reg))
                    via = c[1] if c is not None and c[0] == "mem" else None
                flow = "data" if args & st.data else "control"
                known = []
                for r in ("r0", "r1", "r2", "r3"):
                    if r in st.data:
                        known.append((r, "value", None))
                    elif r in st.conc and st.conc[r][0] != "mem":
                        known.append((r,) + tuple(st.conc[r]))
                    elif r in st.taint:
                        known.append((r, "choice", None))
                sinks.append(Sink(i.address, "call", tgt, flow, 0, via, tuple(known)))
            for r in ("r0", "r1", "r2", "r3", "r12", "lr"):
                clean(r)
                st.conc.pop(r, None)
            st.flags = False
            return
        op = _mem_op(i)
        if _is_store(m) and op is not None and len(i.operands) >= 2:
            if m.startswith(("strd", "stm")):
                vals = {i.reg_name(o.reg) for o in i.operands if o.type == A.ARM_OP_REG}
            else:
                vals = {i.reg_name(i.operands[0].reg)}
            if (vals & st.taint) or under:
                addr = self._addr(st, op)
                flow = "data" if vals & st.data else "control"
                w = _width(m)
                if addr is None or addr[0] == "mem":
                    sinks.append(Sink(i.address, "store?", None, flow, w))
                elif addr[0] == "abs":
                    kind = "cell" if SRAM_LOW <= addr[1] < SRAM_HIGH else "store?"
                    sinks.append(Sink(i.address, kind, addr[1], flow, w))
                    st.mem.update(range(addr[1], addr[1] + w))
                else:
                    sinks.append(Sink(i.address, addr[0], addr[1], flow, w))
            if i.writeback:                      # a write-back store moves its base
                st.conc.pop(i.reg_name(op.mem.base), None)
            return
        if _is_load(m) and op is not None:
            dests = [i.reg_name(o.reg) for o in i.operands if o.type == A.ARM_OP_REG]
            if op.mem.base == A.ARM_REG_PC:
                val = self._literal(i)
                for d in dests:
                    st.conc.pop(d, None)
                    if under:
                        taint(d, False)
                    else:
                        clean(d)
                if val is not None and len(dests) == 1:
                    st.conc[dests[0]] = ("abs", val)
                return
            addr = self._addr(st, op)
            regs = {i.reg_name(op.mem.base)} | ({i.reg_name(op.mem.index)} if op.mem.index else set())
            hit = addr is not None and (addr == want or (addr[0] == "abs" and addr[1] in st.mem))
            for d in dests:
                st.conc.pop(d, None)
                if hit or (regs & st.data):
                    taint(d, True)
                elif under or (regs & st.taint):
                    taint(d, False)
                else:
                    clean(d)
            if addr is not None and len(dests) == 1 and m in ("ldr", "ldr.w"):
                if addr == ("state", config.CONFIG_BUFFER_OFFSET):
                    st.conc[dests[0]] = ("config", 0)
                elif addr[0] == "abs":
                    st.conc[dests[0]] = ("mem", addr[1])
            if i.writeback:
                st.conc.pop(i.reg_name(op.mem.base), None)
            return
        # register arithmetic
        reads = tanknames._reads(i)
        writes = tanknames._writes(i) - {"pc", "sp", "itstate", "cpsr", "apsr", "fpscr"}
        src_t = bool(reads & st.taint)
        src_d = bool(reads & st.data)
        if _sets_flags(i):
            st.flags = src_t or under or (_predicated(i) and st.flags)
        if not writes:
            return
        newconc = None                      # address-forming moves keep a known value
        ops = i.operands
        if m in ("mov", "movs", "mov.w", "movw") and len(ops) == 2:
            if ops[1].type == A.ARM_OP_IMM:
                newconc = ("abs", ops[1].imm & 0xFFFFFFFF)
            elif ops[1].type == A.ARM_OP_REG:
                newconc = st.conc.get(i.reg_name(ops[1].reg))
        elif m.split(".")[0] in ("add", "adds", "addw", "sub", "subs", "subw") and len(ops) == 3 \
                and ops[1].type == A.ARM_OP_REG and ops[2].type == A.ARM_OP_IMM:
            c = st.conc.get(i.reg_name(ops[1].reg))
            if c is not None:
                k = ops[2].imm if m.startswith("add") else -ops[2].imm
                newconc = (c[0], c[1] + k)
        elif m.split(".")[0] in ("add", "adds") and len(ops) == 2 and ops[1].type == A.ARM_OP_IMM:
            c = st.conc.get(i.reg_name(ops[0].reg))
            if c is not None:
                newconc = (c[0], c[1] + ops[1].imm)
        for w in writes:
            st.conc.pop(w, None)
            if newconc is not None and len(writes) == 1 and not _predicated(i):
                st.conc[w] = newconc
            if src_t or under:
                taint(w, src_d)
            elif not _predicated(i):
                clean(w)

    # -- paths --------------------------------------------------------------------------
    def _path(self, j: int, limit: int) -> list:
        """Instruction indices from `j` along fall-throughs, following unconditional `b`s."""
        out, seen = [], set()
        while j is not None and j < len(self.ins) and len(out) < limit:
            if j in seen:
                break
            seen.add(j)
            out.append(j)
            i = self.ins[j]
            if tanknames._is_unconditional_exit(i):
                t = tanknames._branch_target(i)
                if i.mnemonic in ("b", "b.w") and t is not None and t in self.at:
                    j = self.at[t]
                    continue
                break
            j += 1
        return out

    def run(self, k: int, st: _St, want: tuple, sinks: list) -> None:
        """Trace from instruction index `k` (inclusive)."""
        self._exec(k, st, want, sinks, False, None, [TRACE_LIMIT], 0)

    @staticmethod
    def _merge(outs: list) -> _St:
        m = _St()
        m.taint = set().union(*(o.taint for o in outs))
        m.data = set().union(*(o.data for o in outs))
        m.flags = any(o.flags for o in outs)
        m.conc = {r: c for r, c in outs[0].conc.items() if all(o.conc.get(r) == c for o in outs)}
        m.mem = set().union(*(o.mem for o in outs))
        return m

    def _join(self, starts: tuple) -> int | None:
        """Where the two successors of a decided branch meet: the first instruction on the
        fall-through path that the taken path also reaches, moved forward while a branch inside
        either arm skips over it to a later point of the fall-through path (13.6 var 207's
        `ble 0x24A86E` at `0x24A81C` leaves the arm of the `beq` before it for the end of the
        whole block). A branch to a point off the fall-through path is an out-of-line arm and
        does not move the join."""
        pa = self._path(starts[0], ARM_LIMIT)
        pb = self._path(starts[1], ARM_LIMIT)
        pos_a = {x: n for n, x in enumerate(pa)}
        set_b = set(pb)
        meet = next((x for x in pa if x in set_b), None)
        while meet is not None:
            region = pa[:pos_a[meet]] + pb[:pb.index(meet)] if meet in pb else pa[:pos_a[meet]]
            later = []
            for x in region:
                i = self.ins[x]
                if _cond_branch(i):
                    t = tanknames._branch_target(i)
                    k = self.at.get(t) if t is not None else None
                    if k is not None and k in pos_a and pos_a[k] > pos_a[meet] and k in set_b:
                        later.append(k)
            if not later:
                break
            meet = max(later, key=lambda k: pos_a[k])
        return meet

    def _exec(self, j: int, st: _St, want: tuple, sinks: list, implicit: bool, stop,
              budget: list, depth: int):
        """Run from index `j` to index `stop`, returning `(state there or None, how)`: `how` is
        "stop", "end" (a return, an unresolvable branch, the budget, or -- outside a decided arm
        -- nothing carries the value any more), or "runaway" (a decided arm that ran `ARM_RUN`
        instructions without reaching its join).

        Inside a decided arm (`implicit`) every conditional branch is explored both ways, since
        whichever way it goes the code runs only because of the slot's value. An arm's sinks are
        kept only if it reaches its join or ends; a runaway arm's are dropped, since past the
        point it should have rejoined, the code no longer depends on the decision."""
        idle = 0
        seen: dict = {}
        steps = 0
        while j is not None and j < len(self.ins):
            if j == stop:
                return st, "stop"
            if budget[0] <= 0:
                return None, "end"
            budget[0] -= 1
            steps += 1
            if implicit and steps > ARM_RUN:
                return None, "runaway"
            seen[j] = seen.get(j, 0) + 1
            if seen[j] > 2:
                return None, "end"
            i = self.ins[j]
            m = i.mnemonic
            if _cond_branch(i):
                if m in ("cbz", "cbnz"):
                    decided = i.reg_name(i.operands[0].reg) in st.taint
                else:
                    decided = st.flags
                if not (decided or implicit) or depth >= MAX_DEPTH:
                    j += 1                 # an undecided branch: follow the fall-through
                    continue
                t = tanknames._branch_target(i)
                if t is None or t not in self.at:
                    return None, "end"
                starts = (j + 1, self.at[t])
                meet = self._join(starts)
                if stop is not None and meet is not None:
                    pa = self._path(starts[0], ARM_LIMIT)
                    if stop in pa and (meet not in pa or pa.index(stop) < pa.index(meet)):
                        meet = stop
                outs = []
                for s0 in starts:
                    arm_sinks: list = []
                    o, how = self._exec(s0, st.copy(), want, arm_sinks, True,
                                        meet if meet is not None else stop, budget, depth + 1)
                    if how != "runaway":
                        sinks.extend(arm_sinks)
                    if o is not None:
                        outs.append(o)
                if meet is None or not outs:
                    return None, "end"
                if meet == stop:
                    return self._merge(outs), "stop"
                st = self._merge(outs)
                j = meet
                idle = 0
                continue
            if tanknames._is_unconditional_exit(i):
                t = tanknames._branch_target(i)
                if m in ("b", "b.w") and t is not None and t in self.at:
                    j = self.at[t]
                    continue
                return None, "end"
            self.step(i, st, implicit, sinks, want)
            if not implicit:
                if st.taint or st.flags:
                    idle = 0
                else:
                    idle += 1
                    if stop is None and (not st.mem or idle > MEM_LOOKAHEAD):
                        return None, "end"
            j += 1
        return None, "end"


# ------------------------------------------------------------------------------ the reads

@memo.content_cached(copy=None)
def literal_loads(data: bytes) -> dict:
    """`{word: (instruction index, ...)}` for every PC-relative `ldr` in GuiCode, by the word it
    loads -- built once."""
    import capstone.arm as A
    tr = _tracer(data)
    out: dict = {}
    for k, i in enumerate(tr.ins):
        if i.mnemonic in ("ldr", "ldr.w") and len(i.operands) == 2:
            op = i.operands[1]
            if op.type == A.ARM_OP_MEM and op.mem.base == A.ARM_REG_PC:
                v = tr._literal(i)
                if v is not None:
                    out.setdefault(v, []).append(k)
    return {w: tuple(v) for w, v in out.items()}


@memo.content_cached(copy=None)
def _tracer(data: bytes) -> Tracer:
    return Tracer(data)


@memo.content_cached(maxsize=4096, copy=None)
def slot_reads(data: bytes, var: int) -> tuple:
    """`(SlotRead, ...)` for every GuiCode read of `var`'s receive slot this module can prove.

    Two forms: a load at `slot_disp(var)` through a register `state_reach` proves holds the
    slot-array base (13.6's per-tick decoder keeps it in `r4`), and a load through a register
    holding the slot's absolute address from a pool literal (an appended routine's form). A load
    that covers the slot from another offset (a word load at `disp-2`) is not traced here;
    `unread_guarantee` reports it instead of claiming a negative.
    """
    tr = _tracer(data)
    disp = slot_disp(var)
    out = []
    for addr, reg, off in state_reach(data).loads.get(disp, ()):
        k = tr.at[addr]
        if tr.ins[k].mnemonic.startswith("ldrd"):
            continue
        sinks: list = []
        tr.run(k, _St(conc={reg: ("state", off)}), ("state", disp), sinks)
        out.append(SlotRead(addr, "base", tuple(dict.fromkeys(sinks))))
    out += cell_reads(data, register.status_slot_addr(var))
    return tuple(out)


@memo.content_cached(maxsize=4096, copy=None)
def cell_reads(data: bytes, addr: int) -> tuple:
    """`(SlotRead, ...)` for every GuiCode load of the absolute SRAM address `addr` (a pool literal
    loaded into a register, then a load through it), traced the same way: what GuiCode does with
    the value it finds there. Only reads whose trace reaches a sink are returned."""
    tr = _tracer(data)
    out = []
    for k in literal_loads(data).get(addr, ()):
        sinks: list = []
        tr.run(k, _St(), ("abs", addr), sinks)
        if sinks:
            out.append(SlotRead(tr.ins[k].address, "literal", tuple(dict.fromkeys(sinks))))
    return tuple(out)


@memo.content_cached(copy=None)
def literal_words(data: bytes) -> frozenset:
    """Every aligned u32 in GuiCode (pool literals among them)."""
    from . import theme
    sec = theme.guicode_section(data)
    body = bytes(data[sec.offset:sec.offset + sec.length])
    return frozenset(w for (w,) in struct.iter_unpack("<I", body[:len(body) // 4 * 4]))


def unread_guarantee(data: bytes, var: int) -> tuple:
    """`(guaranteed, evidence)` that nothing in GuiCode reads `var`'s slot.

    Guaranteed when `state_reach` followed the base everywhere it goes (no escapes) and found no
    load through it covering either byte of the slot, and no pool word holds the slot's absolute
    address. The base reaches GuiCode only as `GuiCode_entry1`'s `r0` (`GUICODE.md` Finding 33;
    the LCD writes the slot array and never reads it, `MCF-FORMAT.md` §5.4a), so a read would
    have to be one of those. This generalises Finding 77's byte scan for wide loads from any base:
    that scan cannot tell the slot array from another struct with a field at the same offset,
    and cannot see a narrow load at all.
    """
    disp = slot_disp(var)
    addr = register.status_slot_addr(var)
    sr = state_reach(data)
    tr = _tracer(data)
    covering = []
    for off, sites in sr.loads.items():
        for a, _reg, _o in sites:
            w = _width(tr.ins[tr.at[a]].mnemonic)
            if off < disp + 2 and disp < off + w:
                covering.append(a)
    lit = sorted({addr, addr + 1} & literal_words(data))
    if sr.escapes:
        return False, ("the slot-array base escapes at %s; no negative holds" %
                       ", ".join("%#x (%s)" % e for e in sr.escapes))
    if covering:
        return False, ("loads through the base cover offset %#x at %s"
                       % (disp, ", ".join("%#x" % a for a in sorted(covering))))
    if lit:
        return False, "a pool word holds the slot address %#x" % addr
    return True, ("the base reaches %d functions (from GuiCode_entry1 %#x) and no load through it "
                  "covers offset %#x; no pool word holds %#x"
                  % (len(sr.functions), guicode_entry1(data) or 0, disp, addr))


# ------------------------------------------------------------------------- where the base goes

@dataclass(frozen=True)
class StateReach:
    """Every place the slot-array base can be, and every load through it."""
    functions: tuple          # ((entry, end, {reg: offset} at entry), ...) analysed
    loads: dict               # {offset from the base: ((load address, base reg, its offset), ...)}
    escapes: tuple            # ((address, why), ...): uses this analysis cannot follow


ENTRY1_VECTOR = 0x10      # GuiCode header vector word the LCD calls each paint tick (Finding 33)


def guicode_entry1(data: bytes) -> int | None:
    """`GuiCode_entry1`: the header-vector word at GuiCode `+0x10`, the function the LCD calls
    with the slot-array base in `r0` (13.6 `0x0021CBFC ldr r3,[r3,#0x10]` / `blx r3`)."""
    from . import theme
    sec = theme.guicode_section(data)
    w = struct.unpack_from("<I", data, sec.offset + ENTRY1_VECTOR)[0]
    return (w & ~1) if w & 1 else None


def _function_end(tr: Tracer, k: int) -> int:
    """Index one past function `k`'s body: the next `push {..., lr}` prologue."""
    for j in range(k + 1, len(tr.ins)):
        if tanknames._is_push_lr(tr.ins[j]):
            return j
    return len(tr.ins)


@memo.content_cached(copy=None)
def state_reach(data: bytes) -> StateReach:
    """Follow the slot-array base from where it enters GuiCode to every load made through it.

    It enters as `r0` of the per-tick decoders (`tanknames.decoder_ranges`; their caller
    `GuiCode_entry1` receives it from the LCD, `GUICODE.md` Finding 33). Each function is swept
    linearly with the set of registers holding `base + offset`: a `mov`/`add #imm` copies it, any
    other write drops it, a call drops `r0-r3`, and a `bl F` with the base in `r0-r3` queues `F`
    with it there. A load through such a register at `#imm` is a read of `base + offset + imm`.

    Anything else done with a base register -- stored to memory, passed to an indirect call, used
    in arithmetic this cannot follow -- is an **escape**: the base could then be anywhere, and no
    negative built on this analysis holds. Linear sweeping ignores branch joins; that can only
    keep a register marked longer than it holds the base, which adds loads, never hides one
    reached on the fall-through order. The residual is a register set on a branched-to block that
    is reached from a predecessor laid out after it.
    """
    import capstone.arm as A
    tr = _tracer(data)
    at = tr.at
    work = []
    e1 = guicode_entry1(data)
    if e1 is not None and e1 in at:
        work.append((at[e1], (("r0", 0),)))
    for lo, _hi in tanknames.decoder_ranges(data):
        work.append((at[lo], (("r0", 0),)))
    done: dict = {}
    loads: dict = {}
    escapes: list = []
    funcs = []
    while work:
        k, entry_state = work.pop()
        key = (k, entry_state)
        if key in done:
            continue
        done[key] = True
        end = _function_end(tr, k)
        held = dict(entry_state)
        spill: dict = {}          # stack slot (relative to the entry sp) -> base offset
        sp = 0
        for j in range(k, end):
            i = tr.ins[j]
            m = i.mnemonic
            ops = i.operands
            if m.split(".")[0] in ("sub", "add") and len(ops) >= 2 and ops[0].type == A.ARM_OP_REG \
                    and i.reg_name(ops[0].reg) == "sp" and ops[-1].type == A.ARM_OP_IMM:
                sp += -ops[-1].imm if m.startswith("sub") else ops[-1].imm
                continue
            if m.startswith(("push", "pop")) or (m.startswith(("stmdb",)) and "sp!" in i.op_str):
                n = sum(1 for o in ops if o.type == A.ARM_OP_REG)
                sp += -4 * n if not m.startswith("pop") else 4 * n
            reads = tanknames._reads(i)
            writes = tanknames._writes(i) - {"pc", "sp", "itstate", "cpsr", "apsr", "fpscr"}
            used = reads & set(held)
            if tanknames._is_call(i):
                tgt = tanknames._branch_target(i)
                args = tuple(sorted((r, held[r]) for r in ("r0", "r1", "r2", "r3") if r in held))
                if args:
                    if m == "bl" and tgt is not None and tgt in at:
                        work.append((at[tgt], args))
                    else:
                        escapes.append((i.address, "base passed to an indirect call"))
                for r in ("r0", "r1", "r2", "r3", "r12", "lr"):
                    held.pop(r, None)
                continue
            op = _mem_op(i)
            if op is not None and (_is_load(m) or _is_store(m)) and not m.startswith(("ldm", "stm",
                                                                                       "push", "pop")):
                bn = i.reg_name(op.mem.base)
                vals = {i.reg_name(o.reg) for o in i.operands if o.type == A.ARM_OP_REG}
                if bn == "sp" and op.mem.index == 0 and not i.writeback:
                    slot = sp + op.mem.disp
                    if _is_store(m):
                        v = i.reg_name(i.operands[0].reg)
                        if v in held and _width(m) == 4:
                            spill[slot] = held[v]
                        else:
                            spill.pop(slot, None)
                        continue
                    for w in writes:
                        held.pop(w, None)
                    if slot in spill and len(writes) == 1 and _width(m) == 4:
                        held[next(iter(writes))] = spill[slot]
                    continue
                if _is_store(m) and (vals & set(held)):
                    escapes.append((i.address, "base stored to memory"))
                if bn in held and op.mem.index == 0:
                    if _is_load(m):
                        loads.setdefault(held[bn] + op.mem.disp, []).append(
                            (i.address, bn, held[bn]))
                elif op.mem.index and i.reg_name(op.mem.index) in held:
                    escapes.append((i.address, "base used as an index register"))
                if bn in held and op.mem.index:
                    escapes.append((i.address, "register-indexed access through the base"))
                for w in writes:
                    if not (i.writeback and w == bn):
                        held.pop(w, None)
                if i.writeback and bn in held:
                    escapes.append((i.address, "write-back through the base"))
                    held.pop(bn, None)
                continue
            if m.startswith(("ldm", "stm", "push", "pop")):
                if used and not m.startswith("push"):
                    escapes.append((i.address, "block transfer through the base"))
                if m.startswith("push") and used:
                    pass            # a callee-saved copy; pop restores it to the same register
                for w in writes:
                    held.pop(w, None)
                continue
            ops = i.operands
            new = None
            if used:
                if m in ("mov", "movs", "mov.w") and len(ops) == 2 and ops[1].type == A.ARM_OP_REG:
                    new = held[i.reg_name(ops[1].reg)]
                elif m.split(".")[0] in ("add", "adds", "addw") and len(ops) == 3 and \
                        ops[1].type == A.ARM_OP_REG and ops[2].type == A.ARM_OP_IMM and \
                        i.reg_name(ops[1].reg) in held:
                    new = held[i.reg_name(ops[1].reg)] + ops[2].imm
                elif m in _FLAG_SETTERS:
                    new = None      # a compare of the pointer writes nothing
                else:
                    escapes.append((i.address, "base in arithmetic (%s %s)" % (m, i.op_str)))
            for w in writes:
                held.pop(w, None)
                if new is not None and len(writes) == 1:
                    held[w] = new
        funcs.append((tr.ins[k].address, tr.ins[end - 1].address if end > k else tr.ins[k].address,
                      entry_state))
    return StateReach(tuple(sorted(set(funcs))),
                      {d: tuple(sorted(set(v))) for d, v in loads.items()},
                      tuple(sorted(set(escapes))))
