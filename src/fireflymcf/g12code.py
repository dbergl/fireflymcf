"""Recursive-descent disassembler for the G12 code blob (section table `0x67`).

A linear sweep reaches ~1.4 KB of the 6.8 KB blob and then dies on the first literal pool,
which it cannot tell from code. Recursive descent fixes that: follow control flow from known
entry points, stop at returns, and mark pc-relative literal targets as data so the sweep never
tries to decode them.

Why this and not Ghidra: the blob is a **table inside a container section**, not an image with a
load address, and it is per-build generated code (`docs/G12-CODE.md`) -- so there is nothing to
import and re-analyse per build. A 200-line walker that runs from the corpus is the cheaper tool
and keeps every claim reproducible from `pytest`.

Architecture, established in `docs/G12-CODE.md` and re-asserted here: the blob is **Thumb-1**
-- no Thumb-2 encoding anywhere. That is about the encoding only: the core itself is an
STM32L4 / Cortex-M4 per the device's own `McuDevID` (`BUS-BASELINE.md` PC.19), so this line's
old "(Cortex-M0 class)" gloss was an inference the hardware has since contradicted.

`capstone` is run in THUMB mode, which decodes both widths; `analyse` reports any 32-bit instruction it meets so a
build that breaks that assumption is visible rather than silently mis-decoded.

Addresses are **blob-relative** throughout. Nothing here needs an absolute base: every branch is pc-relative and every literal is
pc-relative, so the whole graph is self-contained.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import container, g12_config

TABLE_TAG = 0x67

# Instruction classes we must recognise to follow flow. Thumb-1 encodings.
_UNCOND_B = (0xE000, 0xE800)      # b     #imm11   (0xE000..0xE7FF)
_COND_B = (0xD000, 0xDF00)        # b<cc> #imm8    (0xD000..0xDEFF; 0xDF = svc)
_LDR_LIT = (0x4800, 0x5000)       # ldr rX,[pc,#imm8]

# The lr-relative jump-table thunk. The blob's per-instance tank bucketer dispatches on a 2-bit
# state byte through this thunk, whose target the recursive walk cannot follow: it ends in `bx lr`
# (a return, to the walker) after adding a table entry to lr. The table is the run of displacement
# entries immediately after the `bl` that calls the thunk, indexed by the switch value; each arm is
# at `(addr after the bl) + entry*2`. See `docs/G12-CODE.md`, "The per-instance tank bucketer".
#
# It is present on all three builds in two spellings, distinguished by the load width: 13.6 uses a
# u16 table (`ldrh`, index pre-doubled), 11.23/14.7 a u8 table (`ldrb`). `_find_jump_thunk` matches
# both structurally rather than by exact bytes.
#
#   13.6:       push {r0,r1}; mov r1,lr; lsrs r1,#1; lsls r0,#1; lsls r1,#1; ldrh r1,[r1,r0];
#               lsls r1,#1; add lr,r1; pop {r0,r1}; bx lr
#   11.23/14.7: push {r1};    mov r1,lr; lsrs r1,#1;             lsls r1,#1; ldrb r1,[r1,r0];
#               lsls r1,#1; add lr,r1; pop {r1};    bx lr
_ADD_LR = (0xFF87, 0x4486)        # (hw & mask) == value  ->  add lr, rM
_BX_LR = 0x4770
_PUSH = (0xB400, 0xB500)          # push {..}  (the jump thunk saves scratch, not lr)
_LDRH_REG = (0x5A00, 0x5C00)      # ldrh rD,[rB,rM]   -> u16 table
_LDRB_REG = (0x5C00, 0x5E00)      # ldrb rD,[rB,rM]   -> u8 table
_CMP_IMM8 = (0x2800, 0x3000)      # cmp rX,#imm8   (0x28xx)


def blob(data: bytes) -> bytes:
    """The raw table-0x67 bytes of `data`."""
    section = next(s for s in container.parse_container(data).sections
                   if s.name.startswith("G12"))
    table = next(t for t in g12_config.parse_g12(data).tables if t.tag == TABLE_TAG)
    return data[section.offset + table.offset:
                section.offset + table.offset + table.size]


@dataclass
class Analysis:
    """What a walk found. All addresses are blob-relative."""
    size: int = 0
    code: set = field(default_factory=set)        # bytes decoded as instructions
    literals: set = field(default_factory=set)    # bytes claimed by a pc-relative literal
    starts: set = field(default_factory=set)      # instruction start addresses
    functions: set = field(default_factory=set)   # bl targets + seed entry points
    returns: set = field(default_factory=set)     # addresses of return instructions
    wide: set = field(default_factory=set)        # any 32-bit instruction met (expected: none)
    calls: dict = field(default_factory=dict)     # caller -> set of callees

    @property
    def covered(self) -> int:
        return len(self.code | self.literals)

    @property
    def coverage(self) -> float:
        return self.covered / self.size if self.size else 0.0

    def gaps(self, min_len: int = 8) -> list:
        """Unclaimed runs of at least `min_len` bytes: `(start, length)`."""
        claimed = self.code | self.literals
        out, run_start = [], None
        for i in range(self.size + 1):
            if i < self.size and i not in claimed:
                if run_start is None:
                    run_start = i
            elif run_start is not None:
                if i - run_start >= min_len:
                    out.append((run_start, i - run_start))
                run_start = None
        return out


def entry_points(body: bytes) -> list:
    """Seed addresses for the walk.

    `+0x0000` is an unconditional branch (`0xE19B` on every corpus build) -- its target is the
    program entry. `+0x000C` is code too: it is where the first defaults routine begins, right
    after the two header words. Both are taken as seeds because nothing in the blob *calls*
    `+0x000C`; the header branch jumps over it.
    """
    import struct

    seeds = [0x0C]
    if len(body) >= 2:
        hw = int.from_bytes(body[0:2], "little")
        if _UNCOND_B[0] <= hw < _UNCOND_B[1]:
            imm = hw & 0x7FF
            if imm & 0x400:
                imm -= 0x800
            seeds.append(4 + imm * 2)
    # Header word +0x08 is a THUMB FUNCTION POINTER: an odd value whose cleared low bit lands
    # inside the blob. On 11.23 and 13.6 it points at the exact first byte a two-seed walk
    # cannot reach (0x6FC / 0x918); on 14.7 it points into that same unreached region. So it is
    # a third entry point, not data.
    if len(body) >= 12:
        ptr = struct.unpack_from("<I", body, 8)[0]
        if ptr & 1 and 0 < (ptr & ~1) < len(body):
            seeds.append(ptr & ~1)
    return sorted({s for s in seeds if 0 <= s < len(body)})


def _switch_count(body: bytes, out: "Analysis", call_addr: int, default: int = 4) -> int:
    """How many arms the jump table at `call_addr` has.

    The dispatch guards the `bl` with `cmp rX,#N` / `bls`, so the table holds `N+1` entries.
    Read that immediate from the nearest preceding `cmp rX,#imm8` on a decoded instruction
    boundary; fall back to `default` if none is in range.
    """
    starts = sorted(a for a in out.starts if a < call_addr)
    for addr in reversed(starts[-8:]):
        hw = int.from_bytes(body[addr:addr + 2], "little")
        if _CMP_IMM8[0] <= hw < _CMP_IMM8[1]:
            return (hw & 0xFF) + 1
    return default


def _find_jump_thunk(body: bytes):
    """`(entry, entry_width)` of the lr-relative jump thunk, or `None`.

    Structural, so it survives the register/width differences between builds: an `add lr, rM`
    with `bx lr` within three halfwords, a `push` five-to-eight halfwords back (the entry), and a
    register-offset load between them whose width (`ldrh` -> 2, `ldrb` -> 1) is the table's entry
    size.
    """
    for off in range(0, len(body) - 2, 2):
        hw = int.from_bytes(body[off:off + 2], "little")
        if (hw & _ADD_LR[0]) != _ADD_LR[1]:
            continue
        near = [int.from_bytes(body[off + 2 * k:off + 2 * k + 2], "little") for k in range(1, 4)]
        if _BX_LR not in near:
            continue
        width = entry = None
        for k in range(1, 9):
            h = int.from_bytes(body[off - 2 * k:off - 2 * k + 2], "little")
            if _LDRH_REG[0] <= h < _LDRH_REG[1]:
                width = 2
            elif _LDRB_REG[0] <= h < _LDRB_REG[1]:
                width = 1
            if _PUSH[0] <= h < _PUSH[1]:
                entry = off - 2 * k
        if width and entry is not None:
            return entry, width
    return None


def jump_table_arms(body: bytes, out: "Analysis") -> set:
    """Targets of every lr-relative jump table dispatched through the thunk.

    Static given a completed walk: the thunk's call sites are in `out.calls`, and each table is
    the `entry_width`-wide displacement run right after its `bl`. Empty when the thunk is absent
    or uncalled.
    """
    found = _find_jump_thunk(body)
    if found is None:
        return set()
    entry_addr, width = found
    arms = set()
    for call_addr, callees in out.calls.items():
        if entry_addr not in callees:
            continue
        table = call_addr + 4                     # the `bl` is 32-bit; the table follows it
        for i in range(_switch_count(body, out, call_addr)):
            entry = int.from_bytes(body[table + i * width:table + i * width + width], "little")
            target = table + entry * 2
            if 0 <= target < len(body):
                arms.add(target)
    return arms


def analyse(data: bytes, seeds=None, follow_jump_tables: bool = True) -> Analysis:
    """Walk the blob from `seeds` (default `entry_points`) following control flow.

    With `follow_jump_tables` (the default), after the flow walk settles it also follows the
    lr-relative jump table the walker cannot otherwise reach (`jump_table_arms`) and re-walks
    from its arms, to a fixpoint. Pass `follow_jump_tables=False` for the pure flow-only walk.
    """
    import capstone

    body = blob(data)
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    md.detail = True

    out = Analysis(size=len(body))
    seeds = list(seeds) if seeds is not None else entry_points(body)
    out.functions.update(seeds)
    work = list(seeds)
    seen = set()

    while True:
        while work:
            addr = work.pop()
            while 0 <= addr < len(body) - 1:
                if addr in seen:
                    break
                seen.add(addr)
                got = list(md.disasm(body[addr:addr + 4], addr, count=1))
                if not got:
                    break                                  # undecodable: stop this path
                ins = got[0]
                out.starts.add(addr)
                for b in range(ins.address, ins.address + ins.size):
                    out.code.add(b)
                if ins.size != 2:
                    out.wide.add(addr)

                hw = int.from_bytes(body[addr:addr + 2], "little")
                mnem = ins.mnemonic

                # a pc-relative literal claims 4 bytes of data
                if _LDR_LIT[0] <= hw < _LDR_LIT[1]:
                    target = ((addr + 4) & ~3) + (hw & 0xFF) * 4
                    for b in range(target, min(target + 4, len(body))):
                        out.literals.add(b)

                # returns end the path
                if mnem in ("bx",) or (mnem == "pop" and "pc" in ins.op_str):
                    out.returns.add(addr)
                    break

                # calls: record the callee and continue after the call.
                #
                # NB `bl` is doing double duty here. On 13.6 all but one of the 101 `bl`s are
                # Thumb-1 *long branches* (a far `goto`), and their fall-through -- often a literal
                # pool -- is not reached, so decoding past them makes phantom code (the "functions"
                # column reads 90 against 3 real functions). The obvious fix, keying on a
                # `push {..,lr}` prologue, is UNSOUND across builds: 14.7 calls leaf functions that
                # return via `bx lr` without pushing `lr`, and stopping after those `bl`s loses a
                # quarter of its code. Telling a long branch from a leaf call needs interprocedural
                # return analysis, which is not worth it for a cosmetic count -- so this keeps
                # continuing, and `functions` stays an over-count (documented in `docs/G12-CODE.md`).
                if mnem in ("bl", "blx"):
                    for op in ins.operands:
                        if op.type == capstone.arm.ARM_OP_IMM and 0 <= op.imm < len(body):
                            out.functions.add(op.imm)
                            out.calls.setdefault(addr, set()).add(op.imm)
                            if op.imm not in seen:
                                work.append(op.imm)
                    addr += ins.size
                    continue

                # branches
                if mnem == "b" or mnem.startswith("b") and mnem not in ("bic", "bl", "blx", "bx"):
                    tgt = None
                    for op in ins.operands:
                        if op.type == capstone.arm.ARM_OP_IMM:
                            tgt = op.imm
                    if tgt is not None and 0 <= tgt < len(body) and tgt not in seen:
                        work.append(tgt)
                    if _UNCOND_B[0] <= hw < _UNCOND_B[1]:
                        break                              # unconditional: path ends here
                    addr += ins.size
                    continue

                addr += ins.size

        # The flow walk has settled. Follow any lr-relative jump table it could not, then
        # re-drain from the arms. Repeats to a fixpoint (an arm may hold another dispatch).
        if not follow_jump_tables:
            break
        new = [a for a in jump_table_arms(body, out) if a not in seen]
        if not new:
            break
        work.extend(new)                          # arms are jump targets, not functions

    return out


# --- the frame table -------------------------------------------------------
#
# The blob's data tail is a run of fixed 12-byte RV-C transmit records, and it is the ONLY
# pc-relative data the code addresses: every `add rX, pc` site in the blob resolves into it
# (`docs/G12-CODE.md`). A record is
#
#     <u8 flags><u16 DGN-low LE><u8 CAN-id high byte><8-byte payload>
#
# with the payload in **wire order**. That last point matters: the panel's binding payloads and
# the `0x62` rule templates are stored `[7,6,5,4,11,10,9,8]` (`GUICODE.md` Finding 52), and
# carrying that byte order across to this table produces frames that match nothing.

FRAME_RECORD_SIZE = 12
_ADD_RX_PC = (0x4478, 0x4480)     # add rX, pc   (rX = r0..r7)
_ADDS_IMM8 = 0x3000               # adds rX, #imm8


@dataclass(frozen=True)
class G12Frame:
    """One transmit record from the blob's frame table."""
    offset: int                   # blob-relative
    index: int                    # record number within the table
    dgn: int                      # 18-bit RV-C DGN
    id_high: int                  # the CAN id's top byte, stored literally
    payload: bytes                # 8 bytes, wire order

    def can_id(self, source: int = 0x9C) -> int:
        """The CAN id this record transmits with, for `source` (the G12 is 0x9C)."""
        return (self.id_high << 24) | ((self.dgn & 0xFFFF) << 8) | source

    def frame(self, source: int = 0x9C) -> str:
        """`candump -L` style `<id>#<payload>`, directly greppable against a capture."""
        return f"{self.can_id(source):08X}#{self.payload.hex().upper()}"


def frame_table_offset(body: bytes) -> int:
    """Where the frame table starts, derived from the code that reads it.

    Taken as the lowest `add rX, pc` target rather than a constant, so a build whose tail sits
    elsewhere is followed rather than mis-read.
    """
    targets = {t for _, t in add_pc_sites(body)}
    if not targets:
        raise ValueError("no `add rX, pc` site: cannot locate the frame table")
    return min(targets)


def add_pc_sites(body: bytes) -> list:
    """Every `ldr rX,[pc,#N]` + `add rX, pc` pair, as `(site_offset, target)`.

    The blob is position-independent: the pool word is a displacement from the `add`, not an
    address, which is why scanning for absolute pointers or `ADR` finds nothing.
    """
    out = []
    for off in range(0, len(body) - 1, 2):
        hw = int.from_bytes(body[off:off + 2], "little")
        if not (_ADD_RX_PC[0] <= hw < _ADD_RX_PC[1]):
            continue
        rd = hw & 7
        for back in range(2, 26, 2):
            p = off - back
            if p < 0:
                break
            prev = int.from_bytes(body[p:p + 2], "little")
            if (prev & 0xF800) == 0x4800 and ((prev >> 8) & 7) == rd:
                lit = ((p + 4) & ~3) + (prev & 0xFF) * 4
                if lit + 4 <= len(body):
                    disp = int.from_bytes(body[lit:lit + 4], "little")
                    out.append((off, (disp + off + 4) & 0xFFFFFFFF))
                break
    return out


def frame_table(data: bytes) -> list:
    """Decode the blob's frame table into `G12Frame` records."""
    body = blob(data)
    start = frame_table_offset(body)
    frames, index = [], 0
    off = start
    while off + FRAME_RECORD_SIZE <= len(body):
        rec = body[off:off + FRAME_RECORD_SIZE]
        dgn = 0x10000 | (rec[2] << 8) | rec[1]
        # The table ends where the records stop looking like RV-C frames; on 13.6 that is the
        # `G8 Test` string. Guard on the id-high byte, which is `0x19` for every real record.
        if rec[3] != 0x19:
            break
        frames.append(G12Frame(offset=off, index=index, dgn=dgn,
                               id_high=rec[3], payload=rec[4:12]))
        off += FRAME_RECORD_SIZE
        index += 1
    return frames


# --- the config-defaults routine -------------------------------------------
#
# `docs/G12-CODE.md`: a u16 config field reading 0xFFFF is "unset", and the blob writes a
# hardcoded default into it. The idiom is
#
#     ldrh rV,[rB,#off]    ; rB points at the config struct
#     cmp  rV,rS           ; rS holds the 0xFFFF sentinel
#     bne  keep
#     <build a constant into rW>
#     strh rW,[rB,#off]
#   keep:
#
# with three ways of building the constant (`movs`, `movs`+`lsls`, a literal-pool `ldr`) and
# three ways of forming the address (immediate offset, `adds rD,rB,#n` then a second immediate,
# `movs rD,rB` then `adds rD,#n`). Earlier ad-hoc extractions matched fixed instruction
# sequences and so recovered only part of the set; this one simulates the registers instead,
# which is what makes the address arithmetic fall out rather than needing a case per spelling.

_SENTINEL = 0xFFFF


@dataclass(frozen=True)
class ConfigDefault:
    """One `(config-struct offset, default)` pair the blob writes into an unset field."""
    offset: int          # byte offset into the G12 config struct
    value: int           # the u16 actually stored
    site: int            # blob offset of the `strh`
    source: str          # how the constant was built: movs | movs+lsls | literal
    literal: int = None  # for `source == "literal"`, the full pool word


def _pc_literal(addr: int, imm: int) -> int:
    """Address of a Thumb `ldr rX,[pc,#imm]` operand: Align(PC, 4) + imm, PC = addr + 4."""
    return ((addr + 4) & ~3) + imm


def config_defaults(data: bytes, analysis: "Analysis" = None) -> list:
    """Every `(offset, default)` pair the config-defaults routine writes.

    Instruction boundaries come from the recursive-descent walk rather than a linear sweep, so
    literal pools are never decoded as code. A literal-sourced default whose pool address the
    walk did not claim as data is **dropped**, not reported: that mismatch is exactly the
    symptom of a bad pc-relative calculation, and reporting a plausible-looking number from a
    wrong address is how this table acquired its implausible entries before.
    """
    import capstone

    body = blob(data)
    analysis = analysis or analyse(data)
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    md.detail = True

    out, regs = [], {}
    pending = None          # (register, field offset) awaiting its sentinel compare
    armed = None            # field offset whose guarded store we are now expecting

    def imm_of(reg):
        v = regs.get(reg)
        return v[1] if v and v[0] == "imm" else None

    def base_of(reg):
        v = regs.get(reg)
        return v[1] if v and v[0] == "base" else None

    for addr in sorted(analysis.starts):
        block = body[addr:addr + 4]
        ins = next(iter(md.disasm(block, addr)), None)
        if ins is None:
            continue
        m, ops = ins.mnemonic, ins.op_str.split(", ")
        dst = ops[0] if ops else None

        if m == "ldrh" and len(ops) >= 2 and ops[1].startswith("["):
            tgt = _mem_target(ops[1:], base_of, imm_of)
            pending = (dst, tgt) if tgt is not None else None
            armed = None
            continue

        if m == "cmp" and pending and dst == pending[0] and imm_of(ops[1]) == _SENTINEL:
            armed = pending[1]
            continue

        if m == "strh" and armed is not None and len(ops) >= 2:
            tgt = _mem_target(ops[1:], base_of, imm_of)
            value = imm_of(dst)
            if tgt is not None and tgt == armed and value is not None:
                kind, lit = regs[dst][2], regs[dst][3]
                out.append(ConfigDefault(offset=armed, value=value & 0xFFFF,
                                         site=addr, source=kind, literal=lit))
            armed = pending = None
            continue

        # --- register model ------------------------------------------------
        if m == "ldr" and len(ops) >= 2 and ops[1].startswith("[pc"):
            text = ", ".join(ops[1:])
            imm = int(text.strip("[]").split("#")[-1], 0) if "#" in text else 0
            at = _pc_literal(addr, imm)
            if at in analysis.literals and at + 4 <= len(body):
                word = int.from_bytes(body[at:at + 4], "little")
                regs[dst] = ("imm", word & 0xFFFF, "literal", word)
            else:
                regs[dst] = None            # a bad pc calculation: refuse the value
        elif m == "ldr" and len(ops) == 2 and ops[1] in ("[r0]", "[r4]"):
            regs[dst] = ("base", 0, "", None)
        elif m == "movs" and len(ops) == 2 and ops[1].startswith("#"):
            regs[dst] = ("imm", int(ops[1][1:], 0), "movs", None)
        elif m == "movs" and len(ops) == 2:
            regs[dst] = regs.get(ops[1])
        elif m == "adds" and len(ops) == 3 and ops[2].startswith("#"):
            regs[dst] = _offset(regs.get(ops[1]), int(ops[2][1:], 0))
        elif m == "adds" and len(ops) == 2 and ops[1].startswith("#"):
            regs[dst] = _offset(regs.get(dst), int(ops[1][1:], 0))
        elif m == "lsls" and len(ops) == 3 and ops[2].startswith("#"):
            src = regs.get(ops[1])
            regs[dst] = (("imm", (src[1] << int(ops[2][1:], 0)) & 0xFFFFFFFF,
                          "movs+lsls", None) if src and src[0] == "imm" else None)
        elif m.startswith("b") or m in ("cmp", "push", "pop", "nop", "strh", "ldrh"):
            pass                             # no register written
        elif dst and dst.startswith("r"):
            regs[dst] = None                 # unmodelled write: forget it
    return out


def _offset(state, delta):
    if not state:
        return None
    kind = state[0]
    return (kind, state[1] + delta, state[2], state[3])


def _mem_target(parts, base_of, imm_of):
    """Effective config-struct offset addressed by `[rB]`, `[rB,#imm]` or `[rB,rM]`.

    `rB` must hold a struct base and, in the register-offset form, `rM` a known immediate -- which
    is how the register-indexed defaults spell their address (`movs #sel; lsls #1; ldrh [rB,rM]`),
    the form the earlier `[rB,#imm]`-only reader could not see (it undercounted 99 vs 128 on 13.6).
    Returns `None` when the base or index is not resolvable.
    """
    text = ", ".join(parts).strip("[]")
    bits = [b.strip() for b in text.split(",")]
    if not bits or not bits[0].startswith("r"):
        return None
    base = base_of(bits[0])
    if base is None:
        return None
    if len(bits) == 1:
        return base
    disp = bits[1]
    if disp.startswith("#"):
        return base + int(disp[1:], 0)
    if disp.startswith("r"):
        idx = imm_of(disp)
        return base + idx if idx is not None else None
    return None


# The G12 addresses a config value by **2 x its `0x15FCE` selector**, the same convention the
# panel uses for value slots (`GUICODE.md`: "`var` is one number throughout -- receive-filter
# entry index == slot index == GuiCode's `2*var` immediate"). Established by scoring the
# defaults against the G12's own `0x15FCE` sweep; see `docs/G12-CODE.md`.
CONFIG_CELL_STRIDE = 2

# The config array is `+0x000 … +0x1FE` -- one u16 per one-byte selector. Everything the init
# routine sentinel-fills at or above `+0x200` is runtime state (the per-instance status cells
# `+0x204`..`+0x218`, the timer bank), not a config selector, so `config_defaults_by_selector`
# stops here. `docs/G12-CODE.md`: "Config below +0x200, runtime above."
CONFIG_ARRAY_BYTES = 0x200


def config_cell_offset(selector: int) -> int:
    """Byte offset of `selector`'s cell in the G12 config struct."""
    return CONFIG_CELL_STRIDE * selector


def config_defaults_by_selector(data: bytes, analysis: "Analysis" = None) -> dict:
    """`{selector: default}` for every selector the defaults routine seeds.

    On 13.6 this is 128 selectors: the routine seeds them in two spellings -- an immediate offset
    for the low cells and a register index (`movs #sel; lsls #1; strh [rB,rM]`) for the rest, both
    modelled here. Runtime cells the routine also sentinel-fills (`+0x200` and up) are excluded;
    see `config_defaults` for the raw field list including those.
    """
    by_offset = {d.offset: d.value for d in config_defaults(data, analysis)}
    return {off // CONFIG_CELL_STRIDE: val
            for off, val in by_offset.items()
            if off % CONFIG_CELL_STRIDE == 0 and off < CONFIG_ARRAY_BYTES}


# --- the AGS/AES parameter limits ------------------------------------------
#
# `RVC-GAPS.md` §3 recorded these as "not in the firmware, as far as this work can tell", from a
# search for a min/max *structure*. There isn't one: the limits exist only as compare-and-store
# pairs in this blob, at `+0x0A14`..`+0x0AB8`. Each reads the cell, clamps high, clamps low, and
# stores. The relational rule below is not expressible as a table at all.

#: `{selector: (minimum, maximum)}` -- raw config units (voltages are 0.05 V/unit).
AGS_LIMITS = {
    0x31: (1000, 1080),      # start at voltage   50.0 .. 54.0 V
    0x0D: (1040, 1096),      # stop at voltage    52.0 .. 54.8 V
    0x0C: (10, 300),         # time at start volts, seconds
    0x0E: (600, 3600),       # time at stop volts, seconds
    0x16: (60, 115),         # max engine run time, unit not established
}

#: The G12 also enforces a separation between the two setpoints (`+0x0A76`): it requires
#: `stop_at >= start_at + AGS_SETPOINT_GAP`, and forces `stop_at = start_at + 10` when that fails.
AGS_SETPOINT_GAP = 8


def clamp_ags_parameter(selector: int, value: int) -> int:
    """What the G12 will store for `value` on `selector`, per its own clamps."""
    if selector not in AGS_LIMITS:
        return value
    low, high = AGS_LIMITS[selector]
    return high if value > high else low if value < low else value


# --- what arms the automatic engine start ----------------------------------
#
# The tail of the routine at entry `+0x0918` writes two cells that nothing in this blob ever
# reads, so their consumer is the G12 application at `0x08006000`: the *measured* voltage the
# auto-start watchdog compares, and the *threshold* it compares against. Both are ordinary
# config cells, so the panel can see them in a `0x15FCE` sweep -- which is how the first was
# identified (it tracks the live house-battery voltage) and how the second was shown to be
# absent from the sweep entirely.
#
# In pseudocode, and this is the whole block:
#
#     config[0x0A] = config[0xEF] in {2, 3} ? config[0x55] : config[0x56]
#     config[0x0B] = config[0x2F] ? config[0x31] : 0
#
# The second line is the interesting one. `0x31` is the AES "Start At" voltage; `0x2F` is a
# separate flag, and when it is zero the threshold is written as **0** -- a value the measured
# voltage can never fall below. See `docs/G12-CODE.md`.


@dataclass(frozen=True)
class StartArming:
    """The auto-start threshold block, decoded from the blob rather than assumed."""
    offset: int              # blob offset of the first instruction
    mode_selector: int       # the selector holding the AGS/AES mode
    mode_low: int            # the mode range taken by the first arm ...
    mode_span: int           # ... as `mode_low <= mode <= mode_low + mode_span`
    aes_source: int          # selector copied to `measured` inside that range
    measured: int            # selector receiving the measured voltage
    arming: int              # selector gating the threshold copy
    threshold_source: int    # selector copied to `threshold` when `arming` is non-zero
    threshold: int           # selector receiving the threshold (0 when not armed)


def start_arming(data: bytes, analysis: "Analysis" = None) -> "StartArming":
    """Locate and decode the auto-start arming block, or return `None`.

    Matched by instruction *shape*, not by a byte signature or a fixed offset: the selectors are
    read out of the operands, so a build that moves the block or changes a selector is decoded
    rather than silently mismatched. Only 13.6 has the mode-gated form; 11.23 and 14.7 reach the
    same two stores by other routes, and this returns `None` for them rather than guessing.
    """
    import capstone

    body = blob(data)
    analysis = analysis or analyse(data)
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    md.detail = True

    # `movs rM,#sel ; ldr rB,[r4] ; lsls rM,rM,#1 ; ldrh rM,[rB,rM] ; subs rM,#lo ; cmp rM,#span`
    for start in sorted(analysis.starts):
        ins = list(md.disasm(body[start:start + 44], start))
        if len(ins) < 20:
            continue
        text = [(i.mnemonic, i.op_str) for i in ins]
        if not (text[0][0] == "movs" and text[0][1].endswith("#0xef")
                and text[1] == ("ldr", "r3, [r4]")
                and text[2][0] == "lsls" and text[3][0] == "ldrh"
                and text[4][0] == "subs" and text[5][0] == "cmp"):
            continue
        mode_sel = int(text[0][1].split("#")[1], 0)
        mode_low = int(text[4][1].split("#")[1], 0)
        mode_span = int(text[5][1].split("#")[1], 0)

        # the three `movs rX,r3 ; adds rX,#off ; ldrh rX,[rX]` reads, in order, and the two
        # `strh rX,[r3,#off]` stores they feed
        adds = [int(op.split("#")[1], 0) for mn, op in text if mn == "adds" and ", #" in op]
        strh = [int(op.split("#")[1].rstrip("]"), 0)
            for mn, op in text if mn == "strh" and "[r3, #" in op]
        if len(adds) < 3 or len(strh) < 2:
            continue
        return StartArming(
            offset=start,
            mode_selector=mode_sel,
            mode_low=mode_low,
            mode_span=mode_span,
            aes_source=adds[0] // CONFIG_CELL_STRIDE,
            measured=strh[0] // CONFIG_CELL_STRIDE,
            arming=adds[1] // CONFIG_CELL_STRIDE,
            threshold_source=adds[2] // CONFIG_CELL_STRIDE,
            threshold=strh[1] // CONFIG_CELL_STRIDE,
        )
    return None


@dataclass
class LowVoltsGate:
    """The AGS low-volts arming of the start threshold, decoded from the blob.

    `threshold = arming ? threshold_source : 0`, i.e. the start threshold cell is set from the
    Start-At selector only while the Low-Volts arming selector is non-zero, else zeroed. This is
    the mechanism behind CLAUDE.md defect 0c (arming = 0 -> threshold 0 -> auto-start never fires;
    `BUS-BASELINE.md` PC.39). Unlike `start_arming` (the AES mode-gated re-point, 13.6-only), this
    gate is the **AGS** watchdog and is present, byte-shape-identical, in ALL three builds -- AGS
    predates AES, so `0x2F` ("AGS low volts trigger") and `0x31` ("Start At") are not AES-new."""
    offset: int              # blob offset of the strh that writes the threshold
    arming: int              # selector gating the copy (0x2F)
    threshold_source: int    # selector copied when armed (0x31)
    threshold: int           # selector receiving it (0x0B)


def _reachable_insns(data: bytes, analysis: "Analysis" = None) -> dict:
    """`{blob_offset: capstone.CsInsn}` over the recursive-descent reachable code (never a linear
    sweep, which derails on the first literal pool)."""
    import capstone

    body = blob(data)
    analysis = analysis or analyse(data)
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    md.detail = True
    out = {}
    run_start = prev = None
    for off in sorted(analysis.code):
        if run_start is None:
            run_start = prev = off
        elif off == prev + 1:
            prev = off
        else:
            for ins in md.disasm(body[run_start:prev + 1], run_start):
                out[ins.address] = ins
            run_start = prev = off
    if run_start is not None:
        for ins in md.disasm(body[run_start:prev + 1], run_start):
            out[ins.address] = ins
    return out


def low_volts_gate(data: bytes, analysis: "Analysis" = None) -> "LowVoltsGate":
    """Locate and decode the AGS low-volts start-threshold gate, or return `None`.

    Matched by instruction *shape* over the reachable code, and the selectors are read from the
    operands, so a build that moves the block or renumbers a selector is decoded, not mismatched.
    The shape (identical in 11.23 / 13.6 / 14.7):

        adds rX,#2*arming ; ldrh rX,[rX] ; cmp rX,#0 ; beq <the strh> ;
        adds rY,#2*threshold_source ; ldrh rY,[rY] ; strh rZ,[base,#2*threshold]

    The `beq` skips straight to the store (leaving whatever `rZ` held -- 0 -- as the threshold),
    which is the defect-0c path.
    """
    import re
    strh_cell = re.compile(r"^r\d+, \[r\d+, #(0x[0-9a-f]+)\]$")
    ins = _reachable_insns(data, analysis)
    addrs = sorted(ins)
    for k, addr in enumerate(addrs):
        i = ins[addr]
        if i.mnemonic != "strh":
            continue
        m = strh_cell.match(i.op_str)
        if not m:
            continue
        threshold_off = int(m.group(1), 0)
        win = [ins[a] for a in addrs[max(0, k - 8):k]]
        texts = [(w.mnemonic, w.op_str) for w in win]
        adds = [int(o.split("#")[1], 0) for mn, o in texts if mn == "adds" and ", #" in o]
        has_gate = (any(mn == "cmp" and o.endswith("#0") for mn, o in texts)
                    and any(mn == "beq" for mn, o in texts)
                    and CONFIG_CELL_STRIDE * 0x2F in adds     # the arming selector's cell offset
                    and CONFIG_CELL_STRIDE * 0x31 in adds)    # the Start-At source's cell offset
        if has_gate:
            return LowVoltsGate(
                offset=addr,
                arming=0x2F,
                threshold_source=0x31,
                threshold=threshold_off // CONFIG_CELL_STRIDE,
            )
    return None
