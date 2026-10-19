"""What GuiCode writes into a set of string cells, and under which inputs -- by running it.

A dynamic G12 OUTPUTS row (`MCF-FORMAT.md` §6.1) is a `'NN - '` prefix label plus a wtype-13
record whose `value_slot` is a type-9 string cell. GuiCode fills those cells in one straight-line
block of the per-tick function (13.6 `FUN_00247BF8`, `0x2494B2`..`0x249794`), reading the floorplan
register and the option registers and copying a name out of its string table with
`ldm rS!,{...}` / `str` runs -- or storing a single `0` byte, which leaves the row blank
(`GUICODE.md` Finding 35 transcribed four of those gates by hand). This module does not transcribe
any gate. It executes the block's own instructions under every combination of the inputs the block
reads, and reports what each cell holds afterwards.

## The procedure (nothing per build)

1. **The block.** The cells' GuiCode write sites come from `slotsource` (every `str*` whose address
   resolves to the cell). The block runs from the nearest branch target at or before the lowest
   site from which the code executes without reading a register or flag it did not set itself,
   to the first unconditional branch after the highest site (which closes that site's store
   run); a run ends when control leaves that range. So nothing outside the block feeds it except
   memory.
2. **The interpreter.** A small concrete Thumb-2 interpreter over capstone's own decode (so IT
   blocks, flag-setting and the predicate come from capstone, not from a mnemonic table). It
   supports exactly the instruction forms such a block uses -- literal loads, byte/half/word loads
   and stores, `ldm`, moves, add/sub/orr/and, `uxtb`, compares, `cbz`/`cbnz`, conditional
   branches -- and raises `CellEmulationError` on anything else, on a call, on a read of
   memory outside GuiCode and SRAM, or on a read of a register the block never set. A refusal
   is the answer when the shape differs; nothing is guessed.
3. **The inputs** are discovered, not listed: every SRAM byte the block reads before writing it.
   Each is enumerated over the values it can hold (`input_values`, supplied by the caller: the
   floorplan values for the floorplan register, the OPTIONS values for an option register) until
   the set of inputs stops growing.
4. **Coverage gate.** Every write site of every cell must have executed in at least one run.
   A site no combination reaches means the block was cut short or an input was missed, so the
   function raises rather than report a partial table.

`proven-from-code` for what it reports: each name is the bytes the cited instructions store
under the stated inputs. Emulation is trusted because it reproduces two independent oracles on
13.6 (`tests/test_outputs.py`): the coach's WD screen (rows 05 and 08 named, 06 and 07 blank,
`BUS-BASELINE.md` PC.18b) and `tankgates.gates`, the hand-checked emulator of the four tank gates,
on every floorplan.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass

from . import memo, theme

SRAM_LOW, SRAM_HIGH = theme.SRAM_LOW, theme.SRAM_HIGH
STEP_LIMIT = 4000          # one pass of a fill block is a few hundred instructions
MAX_RUNS = 200_000         # input combinations; a block over this many is refused
LEADER_LOOKBACK = 0x200    # how far before the first write site a block start is sought
NAME_MAX = 48              # a row name is short; reading stops at NUL or here


class CellEmulationError(Exception):
    """The block does not have the shape this interpreter runs; nothing is reported."""


# ---------------------------------------------------------------------------- the interpreter

class _Undefined(Exception):
    """A register or the flags were read before the block set them."""


def _add_flags(a: int, b: int, carry: int = 0) -> tuple:
    r = (a + b + carry) & 0xFFFFFFFF
    c = (a + b + carry) > 0xFFFFFFFF
    v = ((a ^ r) & (b ^ r) & 0x80000000) != 0
    return r, [bool(r & 0x80000000), r == 0, c, v]


def _sub_flags(a: int, b: int) -> tuple:
    return _add_flags(a, (~b) & 0xFFFFFFFF, 1)


_CONDS = {"eq", "ne", "hs", "lo", "cs", "cc", "mi", "pl", "vs", "vc", "hi", "ls", "ge", "lt",
          "gt", "le"}
_BRANCHES = {"b"} | {"b" + c for c in _CONDS}
_MEM = {"ldr": 4, "ldrb": 1, "ldrh": 2, "ldrsb": 1, "ldrsh": 2, "str": 4, "strb": 1, "strh": 2}
_ALU = {"add": "add", "adds": "add", "addw": "add", "sub": "sub", "subs": "sub",
        "subw": "sub", "orr": "orr", "orrs": "orr", "and": "and", "ands": "and", "eor": "eor",
        "eors": "eor", "bic": "bic", "bics": "bic", "lsl": "lsl", "lsls": "lsl", "lsr": "lsr",
        "lsrs": "lsr"}


@dataclass
class Run:
    """One execution of the block."""
    written: dict          # SRAM byte -> value, everything the block stored
    writer: dict           # SRAM byte -> address of the instruction that last stored it
    inputs_read: set       # SRAM bytes read before the block wrote them
    executed: set          # instruction addresses executed


@dataclass(frozen=True)
class _Ins:
    """One instruction, flattened out of capstone once so a run touches no capstone object."""
    address: int
    size: int
    core: str           # mnemonic without width suffix or IT predicate: mov, orr, cmp, b, ...
    cc: int             # capstone condition code (0 / ARM_CC_AL = always)
    setf: bool          # updates the flags
    writeback: bool
    ops: tuple          # ('imm', v) | ('reg', r, shift_type, shift_value)
                        # | ('mem', base, index, disp, shift_type, shift_value) | ('pc', literal)
    text: str


def _flatten(ins) -> _Ins:
    import capstone.arm as A
    core = ins.mnemonic.split(".")[0]
    if ins.cc not in (0, A.ARM_CC_AL) and core[-2:] in _CONDS and core not in _BRANCHES:
        core = core[:-2]                      # an IT-predicated form: movne, orreq, cmpne
    ops = []
    for op in ins.operands:
        if op.type == A.ARM_OP_IMM:
            ops.append(("imm", op.imm & 0xFFFFFFFF))
        elif op.type == A.ARM_OP_REG:
            ops.append(("reg", op.reg, op.shift.type, op.shift.value))
        elif op.type == A.ARM_OP_MEM:
            if op.mem.base == A.ARM_REG_PC:
                ops.append(("pc", ((ins.address + 4) & ~3) + op.mem.disp))
            else:
                ops.append(("mem", op.mem.base, op.mem.index, op.mem.disp,
                            op.shift.type, op.shift.value))
        else:
            ops.append(("other", op.type))
    return _Ins(ins.address, ins.size, core, ins.cc, bool(ins.update_flags),
                bool(ins.writeback), tuple(ops), "%s %s" % (ins.mnemonic, ins.op_str))


class _Machine:
    def __init__(self, code: bytes, base: int, ins_at: dict):
        import capstone.arm as A
        self.code, self.base, self.ins_at = code, base, ins_at
        self.lsl, self.lsr = A.ARM_SFT_LSL, A.ARM_SFT_LSR
        self.always = (0, A.ARM_CC_AL)
        self.conds = {A.ARM_CC_EQ: lambda n, z, c, v: z, A.ARM_CC_NE: lambda n, z, c, v: not z,
                      A.ARM_CC_HS: lambda n, z, c, v: c, A.ARM_CC_LO: lambda n, z, c, v: not c,
                      A.ARM_CC_MI: lambda n, z, c, v: n, A.ARM_CC_PL: lambda n, z, c, v: not n,
                      A.ARM_CC_VS: lambda n, z, c, v: v, A.ARM_CC_VC: lambda n, z, c, v: not v,
                      A.ARM_CC_HI: lambda n, z, c, v: c and not z,
                      A.ARM_CC_LS: lambda n, z, c, v: (not c) or z,
                      A.ARM_CC_GE: lambda n, z, c, v: n == v,
                      A.ARM_CC_LT: lambda n, z, c, v: n != v,
                      A.ARM_CC_GT: lambda n, z, c, v: (not z) and n == v,
                      A.ARM_CC_LE: lambda n, z, c, v: z or n != v}

    def run(self, start: int, stop_above: int, inputs: dict) -> Run:
        regs: dict = {}
        flags: list = []                      # [n, z, c, v] once set
        written: dict = {}
        writer: dict = {}
        read_inputs: set = set()
        executed: set = set()
        code, base, end = self.code, self.base, self.base + len(self.code)
        lsl, lsr = self.lsl, self.lsr

        def reg(r):
            try:
                return regs[r]
            except KeyError:
                raise _Undefined("register %d" % r) from None

        def load(addr: int, size: int) -> int:
            out = 0
            for k in range(size):
                a = (addr + k) & 0xFFFFFFFF
                if base <= a < end:
                    b = code[a - base]
                elif SRAM_LOW <= a < SRAM_HIGH:
                    b = written.get(a)
                    if b is None:
                        read_inputs.add(a)
                        b = inputs.get(a, 0) & 0xFF
                else:
                    raise CellEmulationError("read of %#x, outside GuiCode and SRAM" % a)
                out |= b << (8 * k)
            return out

        def store(addr: int, size: int, value: int) -> None:
            for k in range(size):
                a = (addr + k) & 0xFFFFFFFF
                if not SRAM_LOW <= a < SRAM_HIGH:
                    raise CellEmulationError("store to %#x, outside SRAM" % a)
                written[a] = (value >> (8 * k)) & 0xFF
                writer[a] = pc

        def value(op, at):
            if op[0] == "imm":
                return op[1]
            if op[0] == "reg":
                v = reg(op[1])
                if op[2] == lsl:
                    v = (v << op[3]) & 0xFFFFFFFF
                elif op[2] == lsr:
                    v >>= op[3]
                elif op[2]:
                    raise CellEmulationError("shift type %d at %#x" % (op[2], at))
                return v
            raise CellEmulationError("operand %r at %#x" % (op, at))

        def address(op, at):
            if op[0] == "pc":
                return op[1]
            if op[0] != "mem":
                raise CellEmulationError("operand %r at %#x" % (op, at))
            a = reg(op[1]) + op[3]
            if op[2]:
                idx = reg(op[2])
                if op[4] == lsl:
                    idx <<= op[5]
                elif op[4]:
                    raise CellEmulationError("index shift %d at %#x" % (op[4], at))
                a += idx
            return a & 0xFFFFFFFF

        def nz(r):
            c, v = (flags[2], flags[3]) if flags else (False, False)
            return [bool(r & 0x80000000), r == 0, c, v]

        pc = start
        for _ in range(STEP_LIMIT):
            if not start <= pc <= stop_above:
                return Run(written, writer, read_inputs, executed)
            ins = self.ins_at.get(pc)
            if ins is None:
                raise CellEmulationError("no decoded instruction at %#x" % pc)
            core, ops, nxt = ins.core, ins.ops, pc + ins.size
            if core.startswith("it"):
                pc = nxt
                continue
            executed.add(pc)
            if ins.cc not in self.always:
                if not flags:
                    raise _Undefined("flags")
                if not self.conds[ins.cc](*flags):
                    pc = nxt
                    continue
            if core in ("bl", "blx"):
                raise CellEmulationError("call at %#x inside the block" % pc)
            if core == "bx" or (core == "pop" and "pc" in ins.text):
                return Run(written, writer, read_inputs, executed)
            if core in _BRANCHES:
                pc = ops[0][1]
                continue
            if core in ("cbz", "cbnz"):
                pc = ops[1][1] if (reg(ops[0][1]) == 0) == (core == "cbz") else nxt
                continue
            if core == "nop":
                pc = nxt
                continue
            if core in _MEM:
                if ins.writeback:
                    raise CellEmulationError("writeback %s at %#x" % (ins.text, pc))
                size = _MEM[core]
                addr = address(ops[1], pc)
                if core.startswith("ldr"):
                    v = load(addr, size)
                    if core == "ldrsb" and v & 0x80:
                        v |= 0xFFFFFF00
                    elif core == "ldrsh" and v & 0x8000:
                        v |= 0xFFFF0000
                    regs[ops[0][1]] = v
                else:
                    store(addr, size, reg(ops[0][1]))
                pc = nxt
                continue
            if core in ("ldm", "ldmia", "stm", "stmia"):
                addr = reg(ops[0][1])
                for k, op in enumerate(ops[1:]):
                    if core.startswith("ldm"):
                        regs[op[1]] = load(addr + 4 * k, 4)
                    else:
                        store(addr + 4 * k, 4, reg(op[1]))
                if ins.writeback:
                    regs[ops[0][1]] = (addr + 4 * (len(ops) - 1)) & 0xFFFFFFFF
                pc = nxt
                continue
            if core in ("mov", "movs", "movw"):
                v = value(ops[1], pc)
                regs[ops[0][1]] = v
                if ins.setf:
                    flags = nz(v)
                pc = nxt
                continue
            if core == "movt":
                regs[ops[0][1]] = (reg(ops[0][1]) & 0xFFFF) | ((ops[1][1] & 0xFFFF) << 16)
                pc = nxt
                continue
            if core in ("uxtb", "uxth", "sxtb", "sxth"):
                v = reg(ops[1][1])
                v = v & 0xFF if core.endswith("b") else v & 0xFFFF
                if core == "sxtb" and v & 0x80:
                    v |= 0xFFFFFF00
                elif core == "sxth" and v & 0x8000:
                    v |= 0xFFFF0000
                regs[ops[0][1]] = v
                pc = nxt
                continue
            if core in ("cmp", "cmn", "tst"):
                a, b = reg(ops[0][1]), value(ops[1], pc)
                if core == "cmp":
                    flags = _sub_flags(a, b)[1]
                elif core == "cmn":
                    flags = _add_flags(a, b)[1]
                else:
                    flags = nz(a & b)
                pc = nxt
                continue
            if core in _ALU:
                if len(ops) == 3:
                    a, b = value(ops[1], pc), value(ops[2], pc)
                else:
                    a, b = reg(ops[0][1]), value(ops[1], pc)
                op = _ALU[core]
                if op == "add":
                    r, fl = _add_flags(a, b)
                elif op == "sub":
                    r, fl = _sub_flags(a, b)
                else:
                    r = {"orr": a | b, "and": a & b, "eor": a ^ b, "bic": a & ~b,
                         "lsl": a << b, "lsr": a >> b}[op] & 0xFFFFFFFF
                    fl = nz(r)
                regs[ops[0][1]] = r
                if ins.setf:
                    flags = fl
                pc = nxt
                continue
            raise CellEmulationError("unsupported instruction %s at %#x" % (ins.text, pc))
        raise CellEmulationError("block from %#x did not leave within %d steps"
                                 % (start, STEP_LIMIT))



# ------------------------------------------------------------------------------- the block

@dataclass(frozen=True)
class CellTable:
    """The block, its inputs, and every cell's string under every input combination."""
    start: int                  # first instruction executed
    end: int                    # the branch closing the last write site's store run
    sites: tuple                # every write site of every cell, all of which executed
    inputs: tuple               # SRAM bytes the block reads, sorted
    values: tuple               # per input, the values tried (same order)
    outcomes: tuple             # ((input values...), ((cell, text or None, site), ...)), ...

    def table(self) -> dict:
        """`{cell: {input values tuple: text or None}}` (None: the run left the cell unwritten)."""
        out: dict = {}
        for key, cells in self.outcomes:
            for cell, text, _site in cells:
                out.setdefault(cell, {})[key] = text
        return out

    def sites_for(self, cell: int, text: str) -> tuple:
        """The store instructions that wrote `text`'s first byte into `cell`, over every run."""
        return tuple(sorted({site for _key, cells in self.outcomes
                             for c, t, site in cells if c == cell and t == text
                             and site is not None}))


@memo.content_cached(copy=None)
def _window(data: bytes, lo: int, hi: int) -> tuple:
    """`(code, base, {address: _Ins})` for GuiCode's pool-aware decode between `lo` and `hi`."""
    from . import tanknames
    code, base, ins, _at = tanknames._guicode(data)
    return code, base, {i.address: _flatten(i) for i in ins if lo <= i.address <= hi}


def _text_at(written: dict, cell: int) -> str | None:
    if cell not in written:
        return None
    out = bytearray()
    for k in range(NAME_MAX):
        b = written.get(cell + k)
        if b is None or b == 0:
            break
        out.append(b)
    return out.decode("latin-1")


def _block_start(machine: _Machine, first: int, last: int) -> int:
    """The nearest branch target at or before `first` from which the block runs on its own."""
    targets = set()
    for ins in machine.ins_at.values():
        if ins.core in _BRANCHES:
            targets.add(ins.ops[0][1])
        elif ins.core in ("cbz", "cbnz"):
            targets.add(ins.ops[1][1])
    for t in sorted((t for t in targets if t <= first), reverse=True):
        try:
            machine.run(t, last, {})
        except (_Undefined, CellEmulationError):
            continue
        return t
    raise CellEmulationError("no branch target before %#x starts a self-contained block" % first)


def _block_end(machine: _Machine, last: int) -> int:
    """The first unconditional branch at or after the last write site: the store run that site
    opens (a name is copied by several `str`s at increasing offsets) finishes before it."""
    pc = last
    while pc in machine.ins_at:
        ins = machine.ins_at[pc]
        if ins.core == "b" and ins.cc in machine.always:
            return pc
        pc += ins.size
    raise CellEmulationError("no unconditional branch closes the block after %#x" % last)


@memo.content_cached(copy=None)
def _cell_table(data: bytes, cells: tuple, value_items: tuple) -> CellTable:
    from . import slotsource
    writes = {}
    for entry in slotsource.slot_sources(data):
        if entry.address in cells and entry.writes:
            writes[entry.address] = tuple(entry.writes)
    missing = [hex(c) for c in cells if c not in writes]
    if missing:
        raise CellEmulationError("no GuiCode store reaches cell(s) %s" % ", ".join(missing))
    sites = tuple(sorted(s for ss in writes.values() for s in ss))
    code, base, ins_at = _window(data, sites[0] - LEADER_LOOKBACK, sites[-1] + LEADER_LOOKBACK)
    machine = _Machine(code, base, ins_at)
    stop = _block_end(machine, sites[-1])
    start = _block_start(machine, sites[0], stop)
    value_map = dict(value_items)

    # Seed with the caller's named inputs whose address the block loads as a literal, so the
    # usual case needs one pass; the loop below still finds every input actually read.
    literals = set()
    for ins in ins_at.values():
        if start <= ins.address <= stop and ins.core == "ldr" and ins.ops[-1][0] == "pc":
            at = ins.ops[-1][1] - base
            if 0 <= at <= len(code) - 4:
                literals.add(int.from_bytes(code[at:at + 4], "little"))
    inputs: list = sorted(a for a in value_map if a in literals)
    while True:
        values = [tuple(value_map.get(a, (0, 1))) for a in inputs]
        combos = 1
        for v in values:
            combos *= len(v)
        if combos > MAX_RUNS:
            raise CellEmulationError("%d input combinations exceed %d" % (combos, MAX_RUNS))
        outcomes = []
        seen_inputs: set = set()
        executed: set = set()
        for key in itertools.product(*values) if inputs else [()]:
            try:
                run = machine.run(start, stop, dict(zip(inputs, key)))
            except _Undefined as e:
                raise CellEmulationError("block from %#x reads an unset %s" % (start, e)) from None
            seen_inputs |= run.inputs_read
            executed |= run.executed
            outcomes.append((key, tuple((c, _text_at(run.written, c), run.writer.get(c))
                                        for c in cells)))
        new = sorted(seen_inputs - set(inputs))
        if not new:
            break
        inputs = sorted(set(inputs) | set(new))
    unreached = [hex(s) for s in sites if s not in executed]
    if unreached:
        raise CellEmulationError("write site(s) %s never executed from %#x"
                                 % (", ".join(unreached), start))
    return CellTable(start=start, end=stop, sites=sites, inputs=tuple(inputs),
                     values=tuple(values), outcomes=tuple(outcomes))


def cell_table(data: bytes, cells, input_values: dict) -> CellTable:
    """Run the block that writes `cells` (SRAM addresses of string cells) over every input.

    `input_values` maps an SRAM byte to the values to try for it; an input it does not name is
    tried at 0 and 1. Raises `CellEmulationError` whenever the procedure in the module docstring
    cannot complete, and `theme.CapstoneUnavailable` without capstone.
    """
    items = tuple(sorted((int(a), tuple(sorted(set(int(x) for x in v))))
                         for a, v in input_values.items()))
    return _cell_table(data, tuple(sorted(set(int(c) for c in cells))), items)


def relevant_inputs(table: dict, inputs: tuple, values: tuple) -> tuple:
    """Indices of the inputs whose value changes `table`'s text for some combination.

    `table` is one cell's `{input values tuple: text}`. An input that never changes the text with
    every other input held fixed is irrelevant to that cell and drops out of its conditions.
    """
    keep = []
    for i in range(len(inputs)):
        for key, text in table.items():
            if any(table.get(key[:i] + (v,) + key[i + 1:]) != text for v in values[i]):
                keep.append(i)
                break
    return tuple(keep)
