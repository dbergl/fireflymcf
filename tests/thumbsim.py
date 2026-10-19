"""A minimal Thumb-2 interpreter for the instruction subset this project's authored routines use.

Moved from test_pumpauto.py so every authored routine can be executed in tests, not only disassembled.
"""

import re


class _Unsupported(Exception):
    pass


class Sim:
    STATE_BASE = 0x20050000        # r4 on entry: the GuiCode state pointer
    STACK_TOP = 0x2007F000

    def __init__(self, blob, code_load, disasm):
        self.mem = bytearray(0x100000)          # 0x20000000-0x200FFFFF, byte addressable
        self.base = 0x20000000
        self.rom = {}
        for i, b in enumerate(blob):
            self.rom[code_load + i] = b
        self.code = {a: t for a, t in disasm}
        self.entry = disasm[0][0]
        self.sent = []
        self.clock = 0
        self.regs = [0] * 16
        self.steps = 0

    # --- memory ---------------------------------------------------------------
    def rd8(self, a):
        if a in self.rom:
            return self.rom[a]
        return self.mem[a - self.base]

    def wr8(self, a, v):
        if a in self.rom:
            raise _Unsupported(f"write into the appended blob at {a:#x}")
        self.mem[a - self.base] = v & 0xFF

    def rd(self, a, n):
        return int.from_bytes(bytes(self.rd8(a + i) for i in range(n)), "little")

    def wr(self, a, v, n):
        for i in range(n):
            self.wr8(a + i, (v >> (8 * i)) & 0xFF)

    # --- run ------------------------------------------------------------------
    def run(self, limit=20000):
        r = self.regs
        r[4] = self.STATE_BASE
        sp = self.STACK_TOP
        pc = self.entry
        z = c = False
        while True:
            self.steps += 1
            if self.steps > limit:
                raise _Unsupported("step limit -- the routine did not terminate")
            t = self.code.get(pc)
            if t is None:
                raise _Unsupported(f"no instruction at {pc:#x}")
            size = 4 if (t.startswith(("ldr.w", "strb.w", "pop.w", "b.w", "movw"))) else 2
            nxt = pc + size

            m = re.fullmatch(r"push \{(.*)\}", t)
            if m:
                regs = [14 if x == "lr" else int(x[1:]) for x in m.group(1).split(", ")]
                for i in sorted(regs, reverse=True):          # lowest register, lowest address
                    sp -= 4
                    self.wr(sp, r[i], 4)
                pc = nxt
                continue
            m = re.fullmatch(r"pop\.w \{(.*)\}", t)
            if m:
                regs = [14 if x == "lr" else int(x[1:]) for x in m.group(1).split(", ")]
                for i in sorted(regs):
                    r[i] = self.rd(sp, 4)
                    sp += 4
                pc = nxt
                continue
            m = re.fullmatch(r"b\.w #(0x[0-9a-f]+)", t)
            if m:
                self.tail = int(m.group(1), 16)
                self.sp_at_exit = sp
                return
            m = re.fullmatch(r"b #(0x[0-9a-f]+)", t)
            if m:
                pc = int(m.group(1), 16)
                continue
            m = re.fullmatch(r"b(eq|ne|lo|hs|ls) #(0x[0-9a-f]+)", t)
            if m:
                # ls == "unsigned lower or same" == C clear OR Z set
                cond = {"eq": z, "ne": not z, "lo": not c, "hs": c,
                        "ls": (not c) or z}[m.group(1)]
                pc = int(m.group(2), 16) if cond else nxt
                continue
            m = re.fullmatch(r"ldr r(\d), \[pc(?:, #(0x[0-9a-f]+))?\]", t)
            if m:
                off = int(m.group(2), 16) if m.group(2) else 0
                r[int(m.group(1))] = self.rd(((pc + 4) & ~3) + off, 4)
                pc = nxt
                continue
            m = re.fullmatch(r"(ldr|ldrh|ldrb|ldr\.w) r(\d), \[r(\d)(?:, #(0x[0-9a-f]+|\d+))?\]", t)
            if m:
                width = {"ldr": 4, "ldr.w": 4, "ldrh": 2, "ldrb": 1}[m.group(1)]
                off = int(m.group(4), 0) if m.group(4) else 0
                base = r[int(m.group(3))]
                if base == self.STATE_BASE and off == 0x204:
                    r[int(m.group(2))] = self.clock
                else:
                    r[int(m.group(2))] = self.rd(base + off, width)
                pc = nxt
                continue
            m = re.fullmatch(r"(str|strh|strb\.w) r(\d), \[r(\d)(?:, #(0x[0-9a-f]+|\d+))?\]", t)
            if m:
                width = {"str": 4, "strh": 2, "strb.w": 1}[m.group(1)]
                off = int(m.group(4), 0) if m.group(4) else 0
                self.wr(r[int(m.group(3))] + off, r[int(m.group(2))], width)
                pc = nxt
                continue
            m = re.fullmatch(r"str r(\d), \[sp(?:, #(0x[0-9a-f]+|\d+))?\]", t)
            if m:
                off = int(m.group(2), 0) if m.group(2) else 0
                self.wr(sp + off, r[int(m.group(1))], 4)
                pc = nxt
                continue
            m = re.fullmatch(r"(movs|movw) r(\d), #(0x[0-9a-f]+|\d+)", t)
            if m:
                r[int(m.group(2))] = int(m.group(3), 0)
                pc = nxt
                continue
            m = re.fullmatch(r"mov r(\d), sp", t)
            if m:
                r[int(m.group(1))] = sp
                pc = nxt
                continue
            m = re.fullmatch(r"mov r(\d), r(\d)", t)
            if m:
                r[int(m.group(1))] = r[int(m.group(2))]
                pc = nxt
                continue
            m = re.fullmatch(r"ands r(\d), r(\d)", t)
            if m:
                r[int(m.group(1))] &= r[int(m.group(2))]
                z = r[int(m.group(1))] == 0                 # ANDS (T1) sets N and Z
                pc = nxt
                continue
            m = re.fullmatch(r"(adds|subs) r(\d), #(\d+|0x[0-9a-f]+)", t)
            if m:
                d = int(m.group(2)); v = int(m.group(3), 0)
                r[d] = (r[d] + v if m.group(1) == "adds" else r[d] - v) & 0xFFFFFFFF
                pc = nxt
                continue
            m = re.fullmatch(r"subs r(\d), r(\d), r(\d)", t)
            if m:
                a, b = r[int(m.group(2))], r[int(m.group(3))]
                r[int(m.group(1))] = (a - b) & 0xFFFFFFFF
                z, c = a == b, a >= b                   # C = no borrow (unsigned a >= b)
                pc = nxt
                continue
            m = re.fullmatch(r"adds r(\d), r(\d), r(\d)", t)
            if m:
                r[int(m.group(1))] = (r[int(m.group(2))] + r[int(m.group(3))]) & 0xFFFFFFFF
                pc = nxt
                continue
            m = re.fullmatch(r"lsrs r(\d), r(\d), #(0x[0-9a-f]+|\d+)", t)
            if m:
                r[int(m.group(1))] = r[int(m.group(2))] >> int(m.group(3), 0)
                pc = nxt
                continue
            m = re.fullmatch(r"muls r(\d), r(\d), r(\d)", t)
            if m:
                assert m.group(1) == m.group(3), t          # MULS <Rdm>,<Rn>,<Rdm>
                r[int(m.group(1))] = (r[int(m.group(2))] * r[int(m.group(3))]) & 0xFFFFFFFF
                pc = nxt
                continue
            m = re.fullmatch(r"cmp r(\d), #(0x[0-9a-f]+|\d+)", t)
            if m:
                a, b = r[int(m.group(1))], int(m.group(2), 0)
                z, c = a == b, a >= b
                pc = nxt
                continue
            m = re.fullmatch(r"cmp r(\d), r(\d)", t)
            if m:
                a, b = r[int(m.group(1))], r[int(m.group(2))]
                z, c = a == b, a >= b
                pc = nxt
                continue
            m = re.fullmatch(r"(sub|add) sp, #(0x[0-9a-f]+|\d+)", t)
            if m:
                v = int(m.group(2), 0)
                sp = sp - v if m.group(1) == "sub" else sp + v
                pc = nxt
                continue
            if t == "blx r2":
                if sp % 8:
                    raise _Unsupported(f"sp is not 8-byte aligned at the call: {sp:#x}")
                self.sent.append(bytes(self.rd8(sp + i) for i in range(12)))
                for i in (0, 1, 2, 3):
                    r[i] = 0xDEADBEEF                 # AAPCS: the callee may clobber r0-r3
                r[14] = 0xDEADBEEF
                pc = nxt
                continue
            raise _Unsupported(f"unsupported instruction {t!r} at {pc:#x}")
