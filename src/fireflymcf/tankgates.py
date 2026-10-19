"""Emulate GuiCode's tank-heater name gates (Finding 35) for a floorplan value.

Runs the real instructions at 0x2494b2..0x249570, so the same code simulates a stock and a
patched image -- nothing about the whitelist is hardcoded here.
"""
import capstone
from capstone.arm import (ARM_OP_IMM, ARM_OP_REG, ARM_OP_MEM, ARM_CC_AL, ARM_CC_EQ,
                          ARM_CC_NE, ARM_CC_HS, ARM_CC_LO, ARM_CC_HI, ARM_CC_LS)
from fireflymcf import theme

START, END = 0x2494B2, 0x249572
# the four name-writing blocks (each ldm/str-copies a name from the GuiCode string table)
WRITERS = {0x2494FA: "05 Fresh", 0x24950A: "06 Grey", 0x24954E: "07 Black", 0x24955E: "08 Grey 2"}
# where each writer rejoins the chain, so emulation continues past it
REJOIN = {0x2494FA: 0x2494C4, 0x24950A: 0x24951E, 0x24954E: 0x249536, 0x24955E: 0x249570}
FLOORPLAN_REG, BLACKTANK_REG = 0x2003A77A, 0x2003A4B4


def gates(data: bytes, floorplan: int, black_tank: bool = True) -> set:
    sec = theme.guicode_section(data); base = theme.guicode_base(sec)
    code = data[sec.offset:sec.offset + sec.length]
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB); md.detail = True

    # Pre-decode the whole block in ONE linear pass. Disassembling instruction-by-instruction
    # loses IT-block state, so `ite ne / movne / moveq` comes back as two unconditional `movs`
    # and both arms execute -- which reported Grey and Black as named on every floorplan.
    insns = {}
    for ins in md.disasm(code[START - base:END - base], START):
        insns[ins.address] = ins

    regs, mem = {}, {FLOORPLAN_REG: floorplan, BLACKTANK_REG: 1 if black_tank else 0}
    z = c = None
    itcond = None            # pending IT condition for the next instruction
    hit, pc, steps = set(), START, 0

    def val(o):
        return o.imm if o.type == ARM_OP_IMM else regs.get(o.reg)

    while pc < END and steps < 4000:
        steps += 1
        if pc in WRITERS:
            hit.add(WRITERS[pc]); pc = REJOIN[pc]; continue
        ins = insns.get(pc)
        if ins is None:
            break
        m, ops, nxt = ins.mnemonic, ins.operands, pc + ins.size
        # Predication comes from capstone's own condition code, which is set both for a
        # suffixed mnemonic (movne) and for an instruction inside an IT block. Reading only a
        # pending "it" condition and applying it to the NEXT instruction is wrong for ite/itt --
        # it left the else-arm unpredicated, which made `ite ne / movne r7,#0 / moveq r7,#1`
        # always end with r7 = 1 and reported Grey/Black as named on every floorplan.
        cc = ins.cc
        if m == "it" or m.startswith(("it", "ite", "itt")):
            pc = nxt; continue
        if cc not in (0, ARM_CC_AL):
            ok = {ARM_CC_EQ: z, ARM_CC_NE: (z is not None and not z),
                  ARM_CC_HS: c, ARM_CC_LO: (c is not None and not c),
                  ARM_CC_HI: (c and z is not None and not z),
                  ARM_CC_LS: ((c is not None and not c) or z)}.get(cc)
            if ok is None:
                raise NotImplementedError("condition %s at 0x%06x" % (cc, pc))
            if not ok:
                pc = nxt; continue
        if m in ("ldr", "ldr.w") and ops[1].type == ARM_OP_MEM:
            if ops[1].mem.base == capstone.arm.ARM_REG_PC:
                off = (pc & ~3) + 4 + ops[1].mem.disp - base
                regs[ops[0].reg] = int.from_bytes(code[off:off + 4], "little")
            else:
                regs[ops[0].reg] = mem.get(regs.get(ops[1].mem.base), 0)
        elif m in ("ldrb", "ldrb.w"):
            regs[ops[0].reg] = mem.get(regs.get(ops[1].mem.base), 0) & 0xFF
        elif m in ("subs", "sub", "sub.w", "subs.w"):
            a = val(ops[1]) if len(ops) == 3 else regs.get(ops[0].reg)
            b = val(ops[-1])
            regs[ops[0].reg] = (a - b) & 0xFFFFFFFF
        elif m in ("uxtb", "uxtb.w"):
            regs[ops[0].reg] = regs.get(ops[1].reg, 0) & 0xFF
        elif m in ("cmp", "cmp.w", "cmpne", "cmpeq"):
            a, b = regs.get(ops[0].reg, 0), val(ops[1])
            z, c = (a == b), (a >= b)
        elif m in ("mov", "movs", "mov.w", "movne", "moveq"):
            regs[ops[0].reg] = val(ops[1])
        elif m in ("orr", "orr.w", "orreq", "orrne"):
            regs[ops[0].reg] = (val(ops[1]) or 0) | (val(ops[2]) or 0)
        elif m == "cbz":
            if not regs.get(ops[0].reg, 0):
                pc = ops[1].imm; continue
        elif m == "cbnz":
            if regs.get(ops[0].reg, 0):
                pc = ops[1].imm; continue
        elif m.startswith("b") and len(ops) == 1 and ops[0].type == ARM_OP_IMM:
            # any branch -- b/b.w and every conditional form (beq/bls/bhi/...). The condition was
            # already applied above from ins.cc, so reaching here means it is TAKEN.
            pc = ops[0].imm; continue
        # strb / ldm / str / nop and anything else: no effect on the gate outcome
        pc = nxt
    return hit


# ---------------------------------------------------------------------------------------------
# The whitelist-widening patch (GUICODE Finding 35's TODO). Same-size GuiCode edits that give the
# six Ethos/Swift floorplans -- RT(1) RA(2) RD(3) WA(7) WD(8) WT(9) -- all four tank-heater name
# rows. `{1,2,3,7,8,9}` is the union of two contiguous runs, so no single `uxtb(r5-K) <= N`
# expresses it; Fresh/Black/Grey2 use `uxtb(r5-1) <= 8` = 1..9 (the target plus SY/TB/CB, which
# the owner accepted losing), while Grey has exactly six equality slots and lands on the target
# precisely. Confirmed on the coach 2026-08-18 (BUS-BASELINE PC.18b).
WIDEN_EDITS = [
    (0x2494B6, "eb1f", "6b1e", "Fresh: subs r3,r5,#7 -> #1"),
    (0x2494BA, "022b", "082b", "Fresh: cmp r3,#2 -> #8"),
    (0x2494CC, "062d", "022d", "Grey: cmp r5,#6 -> #2"),
    (0x2494D8, "042d", "032d", "Grey: cmp r5,#4 -> #3"),
    (0x2494DC, "052d", "072d", "Grey: cmp r5,#5 -> #7"),
    (0x2494E4, "0a2d", "082d", "Grey: cmp r5,#0xa -> #8"),
    (0x24951E, "092d08bf47f001071fb1", "6b1edbb2082b00bf03d8",
     "Black: cmp/it/orreq/cbz -> subs r3,r5,#1; uxtb; cmp #8; nop; bhi"),
    (0x249536, "ab1e", "6b1e", "Grey2: subs r3,r5,#2 -> #1"),
    (0x24953E, "012b", "082b", "Grey2: cmpne r3,#1 -> #8"),
]


def widen(data: bytes) -> tuple:
    """Apply `WIDEN_EDITS` to `data`; return `(patched, [(file_offset, old, new, why)])`.

    Every edit asserts its expected old bytes first, so this refuses on a build whose GuiCode
    already differs at these sites rather than corrupting it. The addresses are GuiCode virtual
    addresses and GuiCode is never shifted by a GuiData insertion, so the same patch applies to a
    stock image and to a relocated one (e.g. the Pin-15 build) alike -- which is how v11 = v9 +
    this patch is produced. The caller must still `patch.reseal`.
    """
    section = theme.guicode_section(data)
    base = theme.guicode_base(section)
    out = bytearray(data)
    applied = []
    for vaddr, old_hex, new_hex, why in WIDEN_EDITS:
        off = section.offset + (vaddr - base)
        old, new = bytes.fromhex(old_hex), bytes.fromhex(new_hex)
        if len(old) != len(new):
            raise ValueError("not same-size: " + why)
        got = bytes(out[off:off + len(old)])
        if got != old:
            raise ValueError(f"{why}: expected {old.hex()} at {vaddr:#x}, found {got.hex()}")
        out[off:off + len(new)] = new
        applied.append((off, old, new, why))
    return bytes(out), applied
