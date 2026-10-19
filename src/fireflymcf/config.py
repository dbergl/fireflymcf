"""The option config-buffer schema — `cfg offset -> SRAM register` (`docs/GUICODE.md`
Finding 20) — and the `cfg offset -> 0x15FCE selector` join on top of it
(`docs/GUICODE.md` Finding 23).

Two halves, both located structurally. **`config_offsets` is `proven-from-code` per pair; the
selector join's *mechanism* is `proven-from-code` but its individual Rule-B attributions are
`inferred-from-code`** — they rest on code adjacency plus a value correspondence, which the
finding grades A2/A1/B+ rather than proving (`docs/GUICODE.md` Finding 23, "The two rules, and
their honest weight").
The 14.7 rows are `inferred-from-code` and unverified; see `option_selector`.

* `config_offsets` — the config-unpack (below).
* `selector_sites` / `selector_for_offset` / `option_selector` — the join between a config
  offset and the `0x1FED9` selector byte the panel puts on the wire when that option
  changes. See the second module section, "The selector join", for the mechanism.

`GuiCode_entry1` calls a config-unpack function (13.6 `FUN_00247bf8` @`0x00247BF8`; 14.7
`FUN_00247cac` @`0x00247CAC`) that dereferences a config-buffer pointer at
`GuiCodeState+0x214` (`ldr rY,[rX,#0x214]`) and then, for a fixed set of byte offsets, copies
straight into an SRAM option register:

    ldrb r1,[r3,#0x1b]     ; config byte 0x1B
    ldr  r2,=0x200396FE    ; the option's SRAM register
    strb r1,[r2,#0x0]

This module finds that three-instruction shape *structurally* — by locating the function that
dereferences `+0x214` and reading its instruction stream — and returns every `(offset,
register)` pair it can prove. The seven pairs in Finding 20's table are a **verification
oracle only**; they were not searched for.

**Located, not searched for.** `+0x214` is the config-buffer pointer's own offset from
`GuiCodeState`, cited in Finding 20 from the disassembly, not fitted to any known
`(offset, register)` pair — it is how the unpack code recognizably announces itself, the same
way `theme.py` locates the theme-init by the mode-7 colour cells it writes, not by grepping for
a colour.

**Tracer discipline — a linear register-file walk, in the same style as
`theme._trace_constant_stores`, with one deliberate difference.** Three kinds of register state
are tracked: a concrete 32-bit value (from a PC-relative literal load, or a `mov`/`movw`/
`movt`/`mvn` immediate — the address-forming instructions), the config-buffer identity (set only
by `ldr rt,[rn,#0x214]`), and an as-yet-unread config byte (set only by `ldrb rd,[rb,#imm]` where
`rb` is a *proven* config-buffer register — a pair whose base is not proven is dropped, not
guessed). A `strb rt,[rn]` records `{imm: address}` only when `rt` still holds an unread config
byte and `rn` holds a concrete, in-range SRAM address.

Register state is invalidated on every write this tracer cannot follow, and cleared wholesale on
calls (`bl`/`blx`, caller-saved registers are gone), returns, and data-island resync — the same
discipline `theme.py` uses. **The deviation**: an ordinary *conditional* branch (`bcc`, `cbz`,
`cbnz` — capstone condition code != `AL`, or the `cbz`/`cbnz` mnemonics whose condition capstone
does not expose via `cc`) does **not** clear state. The unpack's own instruction stream
interleaves short local clamp branches between required offsets — e.g. 13.6's `bls` guarding the
byte-0x0b entry sits between the byte-0x0a (Heat Pump) and byte-0x0c/0x0d (Bath Fan) stores
(`0x00247C4C`) — and both arms of such a branch share the identical preceding state (neither arm
touches the config-buffer register), so clearing there would drop every offset that follows the
first clamp in the function, including several of Finding 20's cited pairs. Only a genuine
unconditional escape (`b`/`b.w` with `cc == AL`) is treated as leaving the flow. `it`/`ite` are
left alone for the same reason: they write no register themselves, and the predicated
instruction they guard still only invalidates whatever *it* writes — so no wrong value can ever
be reported, only some are conservatively dropped, which is the cardinal rule this project holds
to.

An offset written to more than one distinct address (or read from a register that ends up
ambiguous) is **omitted, never guessed** — matching `theme.py`'s "a cell written more than one
distinct constant has no single answer" rule.

**One fix worth recording here rather than only in git history.** The `strb` handler originally
recorded `(offset, address)` without clearing `rt`'s config-byte tag, so a later unrelated store
through that same register — before any clearing event — could attribute a *second* address to
the same offset. Downstream the ambiguity rule above then dropped the offset entirely, so the
symptom was a silent loss, not a wrong answer. `invalidate(rt)` now runs immediately after a
successful record. Effect, measured across the corpus: 13.6 and 14.7 are byte-identical to
before (18 pairs each, same registers); **11.23 gained one pair that had been falsely ambiguous
— cfg `0x00` -> `0x20039A8E` (floorplan) — taking it from 12 to 13 pairs.** That before/after is
**not covered by a pinned regression test**: `tests/test_config_offsets.py`'s 11.23 case
(`test_config_offsets_1123_does_not_crash`) deliberately asserts only non-emptiness and SRAM
range, because the build may legitimately resolve a different set. Anyone who reverts or
reworks the `strb` path should re-measure 11.23's count by hand.

**The clamp/boolean-squeeze idiom.** Not every option is a raw copy: 13.6 itself validates a
few fields in place (e.g. `0x00247C48`: `ldrb r2,[r3,#0xb] ; cmp r2,#5 ; bls #.. ; movs r2,#1 ;
ldr r1,=reg ; strb r2,[r1]` clamps byte 0xb above 5 down to 1 before storing), and 11.23's build
does this for nearly every field — a boolean squeeze (`cmp rd,#1 ; ite ne ; movne rd,#0 ; moveq
rd,#1`), a zero-normalize (`adds rd,#0 ; it ne ; movne rd,#1`), or a sentinel clamp (`cmp rd,#N ;
bcc .. ; mov rd,#v`). In every case the value ultimately stored is a pure function of the *same*
register the `ldrb` populated — the offset identity survives even though the concrete value
becomes unrecoverable (which was already true; this tracer only ever followed offsets, never
byte values). A tight, bounded **clamp guard** recognizes this: a `cmp rd,#imm` or a value-
preserving `adds/subs rd,#0` on a register `rd` that currently holds an unread config byte arms
the guard for `rd`; a subsequent immediate `mov`/predicated `mov<cc>` into that *same* `rd`
(reached, if at all, only through the conditional/branch instructions this tracer already leaves
alone) is then a squeeze, not a fresh assignment, and the offset tag survives. Anything else that
writes `rd` — a real memory load, an unrelated instruction, a new `ldrb` — invalidates normally,
so the guard cannot outlive the tight compare-then-conditionally-overwrite window it was armed
in, and it is wiped wholesale at every call/return/unconditional-jump/data-island clear along with
the rest of the register file.

STATUS: `proven-from-code` for every pair this returns (each is a cited `ldrb`/literal-load/
`strb` triple, possibly with a clamp squeeze in between, in the traced function). 14.7 resolves
at least the Starlink pair Finding 20 cites (`0x1B -> 0x200396FE`) by the same mechanism; 11.23
resolves a different, smaller set (mostly through the clamp-guard path, since almost every field
there is validated rather than copied) — this module does not special-case any build, and an
unresolvable offset is dropped, not hardcoded.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, FrozenSet, Set, Tuple

from . import memo, theme
from .theme import CapstoneUnavailable

# GuiCodeState+0x214: the config-buffer pointer. Cited in Finding 20's disassembly; this is the
# structural anchor used to *locate* the unpack, not a value fitted to a known pair.
CONFIG_BUFFER_OFFSET = 0x214

# The option registers are SRAM cells, same range `theme.py` uses for the colour cells.
SRAM_LOW = theme.SRAM_LOW
SRAM_HIGH = theme.SRAM_HIGH


def config_offsets(data: bytes) -> Dict[int, int]:
    """`{cfg offset: SRAM register}` for every pair the config-unpack's instruction stream
    proves, across the whole GuiCode section (build-general — no hardcoded function address).

    `data` is the full MCF container. Raises `CapstoneUnavailable` if capstone cannot be
    imported.
    """
    section = theme.guicode_section(data)
    base = theme.guicode_base(section)
    code = data[section.offset:section.offset + section.length]
    return _trace_config_offsets(code, base)


CFG_FLOORPLAN = 0x00   # the one cfg offset stable across all three builds (Finding 20/23)


def _slot_of_register(data: bytes, register: int) -> int | None:
    """The variable slot whose pointer-table entry is `register`, or None.

    Walks Finding 17's GuiData variable pointer table (located structurally by
    `guidata.find_variable_tables`) with the same run rule that table is defined by --
    entries are 0 or SRAM addresses, the first word that is neither ends the table.
    A register that appears zero times or more than once yields None, never a guess
    (the module's standing ambiguity rule)."""
    from . import guidata

    dptr_off, _ = guidata.find_variable_tables(data)
    gs, _ge = guidata.guidata_extent(data)
    hits = []
    slot = 0
    while True:
        at = gs + dptr_off + slot * 4
        if at + 4 > len(data):
            break
        word = struct.unpack_from("<I", data, at)[0]
        if word != 0 and not guidata.SRAM_LOW <= word < guidata.SRAM_HIGH:
            break
        if word == register:
            hits.append(slot)
        slot += 1
    return hits[0] if len(hits) == 1 else None


@lru_cache(maxsize=4)
def floorplan_slot(data: bytes) -> int | None:
    """This build's floorplan variable slot, derived from the receive mechanism -- or
    None when the derivation cannot complete (no cfg-`0x00` pair proven, or the register
    is absent/ambiguous in the variable table).

    Two proven joints, no fitting: the config-unpack stores cfg byte `0x00` (the
    floorplan -- the offset all three builds share, Finding 20/23) into the floorplan
    register (`config_offsets`, cited-instruction pairs only), and that register's
    unique index in Finding 17's variable pointer table is the slot the type-6
    page-gating switches read. Validated 2026-08-30 by reproducing both independently
    known answers -- 13.6 slot 5 and 14.7 slot 10, the build where the rejected
    read-histogram heuristic picked 114 -- before reading 11.23's answer (slot 7,
    register `0x20039A8E`) off the same procedure. Confirmed twice over since: on the
    coach (BUS-BASELINE PC.40), and by 11.23's floorplan-name switch (objects 852/853,
    also slot 7) once the corrected value map unmasked it -- so this is the independent
    second mechanism behind the switch path, and the fallback for a build with no
    matchable switch. Raises `CapstoneUnavailable` like `config_offsets`; any other
    failure to resolve is a None, never a guess."""
    register = config_offsets(data).get(CFG_FLOORPLAN)
    if register is None:
        return None
    return _slot_of_register(data, register)


@memo.content_cached(copy=dict)
def _trace_config_offsets(code: bytes, base: int) -> Dict[int, int]:
    try:
        import capstone
        from capstone.arm import ARM_CC_AL, ARM_OP_IMM, ARM_OP_MEM, ARM_OP_REG, ARM_REG_PC
    except ImportError as exc:  # pragma: no cover - capstone is installed in this repo
        raise CapstoneUnavailable(
            "capstone is required to disassemble the GuiCode config-unpack") from exc

    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    md.detail = True

    def literal(addr: int) -> int | None:
        off = addr - base
        if 0 <= off <= len(code) - 4:
            return struct.unpack_from("<I", code, off)[0]
        return None

    regs: Dict[int, int] = {}       # register -> concrete 32-bit value
    cfgbuf: Set[int] = set()        # registers currently holding the config-buffer pointer
    cfgbyte: Dict[int, int] = {}    # register -> cfg offset, value not yet stored anywhere
    clamp_guard: Set[int] = set()   # registers mid-squeeze: see the module docstring
    seen: Dict[int, Set[int]] = {}  # cfg offset -> {candidate SRAM addresses}

    def invalidate(reg: int) -> None:
        regs.pop(reg, None)
        cfgbuf.discard(reg)
        cfgbyte.pop(reg, None)
        clamp_guard.discard(reg)

    def clear_all() -> None:
        regs.clear()
        cfgbuf.clear()
        cfgbyte.clear()
        clamp_guard.clear()

    pos = 0
    while pos < len(code):
        decoded_end = pos
        for ins in md.disasm(code[pos:], base + pos):
            decoded_end = ins.address - base + ins.size
            mnem = ins.mnemonic
            ops = ins.operands

            # PC-relative literal load: a concrete 32-bit value (an address, in the shapes we
            # care about).
            if (mnem in ("ldr", "ldr.w") and len(ops) == 2
                    and ops[1].type == ARM_OP_MEM and ops[1].mem.base == ARM_REG_PC):
                rt = ops[0].reg
                val = literal((ins.address & ~3) + 4 + ops[1].mem.disp)
                invalidate(rt)
                if val is not None:
                    regs[rt] = val
                continue

            # The config-buffer pointer itself: `ldr rt,[rn,#0x214]`. Structural, not fitted.
            if (mnem in ("ldr", "ldr.w") and len(ops) == 2
                    and ops[1].type == ARM_OP_MEM and ops[1].mem.base != ARM_REG_PC
                    and ops[1].mem.disp == CONFIG_BUFFER_OFFSET):
                rt = ops[0].reg
                invalidate(rt)
                cfgbuf.add(rt)
                continue

            # A raw config byte: `ldrb rd,[rb,#imm]`, only recognized when `rb` is a *proven*
            # config-buffer register — otherwise this is some unrelated byte load and rd is
            # simply invalidated (dropped, not guessed).
            if (mnem in ("ldrb", "ldrb.w") and len(ops) == 2
                    and ops[1].type == ARM_OP_MEM and ops[1].mem.index == 0):
                mem = ops[1].mem
                rt = ops[0].reg
                invalidate(rt)
                if mem.base in cfgbuf:
                    cfgbyte[rt] = mem.disp
                continue

            # Arms the clamp guard: `cmp rd,#imm` reads a config byte without writing anything,
            # so it is not otherwise handled below — but if `rd` still holds an unread offset,
            # a squeeze on that exact register is likely about to follow.
            if (mnem in ("cmp", "cmp.w") and len(ops) == 2
                    and ops[0].type == ARM_OP_REG and ops[1].type == ARM_OP_IMM):
                if ops[0].reg in cfgbyte:
                    clamp_guard.add(ops[0].reg)
                continue

            # Also arms the clamp guard: `adds/subs rd,#0` is a value-preserving no-op (used
            # purely to set flags for the `it ne` that follows), so a register it targets keeps
            # its cfg-byte tag rather than being invalidated as an ordinary write.
            if (mnem in ("adds", "subs", "add", "sub", "add.w", "sub.w") and len(ops) == 2
                    and ops[1].type == ARM_OP_IMM and ops[1].imm == 0):
                rt = ops[0].reg
                if rt in cfgbyte:
                    clamp_guard.add(rt)
                else:
                    invalidate(rt)
                continue

            # Address-forming immediates: mov/movw (low 16 or full 32), movt (high 16), mvn. A
            # predicated form (movne/moveq/...) is included here too, but *only* takes the
            # concrete-value path when it is not completing a clamp guard below.
            if ((mnem in ("mov", "movs", "mov.w", "movw")
                 or (mnem.startswith("mov") and mnem not in ("movt",)))
                    and len(ops) == 2 and ops[1].type == ARM_OP_IMM):
                rt = ops[0].reg
                if rt in clamp_guard:
                    # A boolean/clamp squeeze completing over the SAME register a `cmp`/
                    # `adds rd,#0` just guarded (see module docstring): the value about to be
                    # stored is still a pure function of that one config byte, so the offset
                    # identity survives even though the concrete value no longer matches it.
                    continue
                invalidate(rt)
                if mnem in ("mov", "movs", "mov.w", "movw"):
                    regs[rt] = ops[1].imm & 0xFFFFFFFF
                continue
            if mnem == "movt" and len(ops) == 2 and ops[1].type == ARM_OP_IMM:
                rt = ops[0].reg
                if rt in regs:
                    regs[rt] = (regs[rt] & 0xFFFF) | ((ops[1].imm & 0xFFFF) << 16)
                else:
                    invalidate(rt)
                continue
            if mnem in ("mvn", "mvns", "mvn.w") and len(ops) == 2 and ops[1].type == ARM_OP_IMM:
                rt = ops[0].reg
                invalidate(rt)
                regs[rt] = (~ops[1].imm) & 0xFFFFFFFF
                continue

            # Plain register-to-register mov: propagate whichever kind of state the source
            # register carries (concrete value, config-buffer identity, or an unread cfg byte),
            # so a copy into another register before the store is not lost.
            if (mnem in ("mov", "movs", "mov.w") and len(ops) == 2
                    and ops[1].type == ARM_OP_REG):
                rt, rs = ops[0].reg, ops[1].reg
                invalidate(rt)
                if rs in cfgbuf:
                    cfgbuf.add(rt)
                elif rs in cfgbyte:
                    cfgbyte[rt] = cfgbyte[rs]
                elif rs in regs:
                    regs[rt] = regs[rs]
                continue

            # `strb rt,[rn]` — the zero-displacement byte store Finding 20 shows. Recorded only
            # when rt still holds an unread cfg byte and rn holds a concrete, in-range SRAM
            # address. Once recorded, rt's tag is spent: invalidate it immediately so a later,
            # unrelated store through the same register (before any clearing event) can never
            # be mistaken for a second write of this offset.
            if mnem in ("strb", "strb.w") and len(ops) == 2 and ops[1].type == ARM_OP_MEM:
                mem = ops[1].mem
                rt = ops[0].reg
                if (mem.base != ARM_REG_PC and mem.index == 0 and mem.disp == 0
                        and rt in cfgbyte and mem.base in regs):
                    addr = regs[mem.base]
                    if SRAM_LOW <= addr < SRAM_HIGH:
                        seen.setdefault(cfgbyte[rt], set()).add(addr)
                    invalidate(rt)
                continue

            # Calls clobber caller-saved registers; returns end the function; an unconditional
            # branch (cc == AL, and not cbz/cbnz — capstone reports cc == AL for those too, even
            # though they have a real fallthrough) genuinely leaves this linear flow. An ordinary
            # conditional branch does not: see the module docstring for why (Finding 20's own
            # local clamp branches sit between required offsets).
            is_escape = (
                ins.group(capstone.CS_GRP_CALL) or ins.group(capstone.CS_GRP_RET)
                or (ins.group(capstone.CS_GRP_JUMP) and ins.cc == ARM_CC_AL
                    and mnem not in ("cbz", "cbnz"))
            )
            if is_escape:
                clear_all()
                continue

            # Anything else (including conditional branches, cbz/cbnz, it/ite, and ordinary
            # instructions this tracer does not specifically model): invalidate whatever
            # registers it actually writes, so a value we cannot follow never masquerades as a
            # known one. `it`/`ite` write nothing themselves, so this is a no-op for them.
            _, written = ins.regs_access()
            for reg in written:
                invalidate(reg)

        # capstone stopped: end of section, or an undecodable byte (a data island). Resync two
        # bytes on (Thumb is 2-byte aligned) with all state cleared, exactly as `theme.py` does.
        if decoded_end <= pos:
            decoded_end = pos + 2
        if decoded_end < len(code):
            clear_all()
        pos = decoded_end

    return {offset: next(iter(addrs)) for offset, addrs in seen.items() if len(addrs) == 1}


# ===========================================================================================
# The selector join: `cfg offset -> 0x15FCE selector`
# ===========================================================================================
#
# Mechanism (`docs/GUICODE.md` Finding 23, independently re-verified byte-exact by two
# reviewers). The selector is never computed.
# GuiCode's `.rodata` holds an array of pre-built 12-byte CAN records
# (13.6 ~`0x00259430..0x00259ACC`), each `<u32 0x19FED900><8 payload bytes>`, and a row's tap
# handler emits one by copying it wholesale:
#
#     0x0024CA90  add    r3,sp,#4
#     0x0024CA92  ldr    r2,[pc,#0x140]     ; = 0x002596A0, a 0x1FED9 template
#     0x0024CA94  ldm    r2,{r0,r1,r2}      ; 12 bytes of frame
#     0x0024CA96  stm.w  r3,{r0,r1,r2}
#     0x0024CA9A  ldr    r2,[pc,#0x130]     ; = 0x2003986C, the host sender slot
#     0x0024CAA0  blx    r2
#
#     template 0x002596A0 : 00 d9 fe 19 | ff 96 e9 0f 01 00 d1 ea
#                                              ^ selector 0xE9   ^ value 1
#
# so the selector is a literal `.rodata` byte: no offset-indexed table exists, which is why
# `GUICODE.md` Finding 20's search for one was correctly negative. The offset and the
# selector are joined **only by code adjacency** inside one handler's straight-line run. Two
# rules recover that adjacency, and each carries a value cross-check, because adjacency alone
# has known false positives (below).
#
# **Rule A — the config-set call.** At every `bl <config_set>` (the helper that compares
# `config_buffer[offset]` and notifies the host; located structurally as the only site
# materialising the pseudo-PGN `0x1EE00` beside a `[rX,#0x214]` config-buffer load), `r1` is
# the cfg offset and `r2` the value being written. The row's own frame is the first write
# template in the run **whose payload value equals `r2`**.
#
# **Rule B — the gate register.** Rows saved in the bulk-save block have no adjacent
# `bl config_set`; their handler instead gates on the option's own SRAM register, which
# `config_offsets` maps back to a cfg offset. The row's own frame is the first write template
# strictly *after* the gate load, and the emitted value must be a provable *change* given the
# arm the run is on (`cbnz rd,else` → this arm has `rd == 0`, so a value != 0 is a change;
# `cmp rd,#K ; beq else` → this arm has `rd != K`, so the value K is a change). That is the
# trace's "value correspondence", and it is what separates a row's own write from a
# neighbouring frame.
#
# **The four guards, all of which caught a real false positive during the trace:**
#
#   (i)   *Backward bleed.* Both rules are **forward-only**. The trace's first cut also
#         scanned backwards from the gate load and walked into the previous handler's emit
#         sequence, reporting cfg `0x11` -> `0xEA` where the oracle says `0xE5`.
#   (ii)  *Gate-of-another-row.* An option register is also read as a plain guard by other
#         rows: 13.6's floorplan register `0x2003A77A` is read two instructions into the
#         *Cargo Light* handler (`0x0024CE00`) and again in the *Bath Light* one
#         (`0x0024CF1E`), and a naive reading hands cfg `0x00` the selectors `{0xE3, 0xE4}`.
#         `_gate_sites` rejects both, by two requirements that were measured together (relax
#         both and cfg `0x00` -> `{E3,E4}` comes straight back): the loaded byte must be
#         tested **raw** — the floorplan guard is `ldrb r3,[r3] ; subs r3,#4 ; uxtb r3,r3 ;
#         cmp r3,#1`, and the `subs` writes `r3` — and the test must be an equality
#         (`cbz`/`cbnz`, or `cmp` + `beq`/`bne`), because only an equality says what the
#         register *is* on this arm. The floorplan's `cmp r3,#1 ; bls` is a range test and
#         says only that it is one of several values, which cannot support a value check.
#   (iii) *Unrelated later read.* 14.7's cfg `0x14` register is read again at `0x0024E6D4`,
#         outside its own handler, in a run that emits `sel 0x0C value 0x3C`. **The value
#         check is what rejects it**, and it is the only thing that does: that arm implies
#         `r3 != 0` (`cmp r3,#0 ; beq`), and `0x3C` is not a provable change under it.
#         Measured, not assumed — disabling the value check alone brings the site straight
#         back; disabling the containment filter alone leaves it rejected. The **containment
#         filter is inert on this corpus**: switched off, it admits no false positive in any
#         build, only additional *same-selector* sites for offsets already recovered
#         (13.6 cfg `0x13` gains two, `0x19`/`0x1A` one each; 14.7 cfg `0x13` gains one). It
#         is kept as a build-generality guard, not because it earns its keep here — and it
#         costs two cited true sites (13.6's `0x0024BF76` `cmp #3` -> `EF:=3` and
#         `0x0024D106` `cmp #2` -> `EF:=2`, the three-gate value-correspondence argument that
#         earned cfg `0x13` its B+ grade; those two citations are recorded permanently in
#         `docs/GUICODE.md` Finding 23, "cfg `0x13` -> `0xEF`, by three-gate value
#         correspondence").
#   (iv)  *Rule A over-match.* A handler can emit a burst of frames before the next `bl`
#         (11.23 cfg `0x0B` -> `{A0, ED, F5}` in the trace's spike). Requiring the *first*
#         write template *and* the value correspondence reduces that to `0xED` here; where
#         two sites still disagree (11.23 cfg `0x0F` -> `{F0, F5}`) the disagreement is
#         reported, not resolved — `option_selector` returns None.
#
# `selector_for_offset` returns a **frozenset**: `cfg offset -> selector` is a relation, not a
# function. One offset can be written from several sites (both arms of a toggle, one handler
# per floorplan), and those sites need not agree. Note that a handler's run frequently emits
# *more* frames than the row's own — the cfg-`0x17` (Progressive Inverter) run emits `0xE6`
# and then the dependency `0xEF` (AGS), exactly as `BUS-BASELINE.md` PC.3 Result 2 captured,
# and the Bath/Cargo pair writes `E4:=1` then `E3:=0`. Those are `SelectorSite.frames`; only
# `SelectorSite.selector`, the value-checked first frame, is *attributed* to the offset.
#
# STATUS: the mechanism is `proven-from-code`. The 13.6 pairs this returns reproduce six of
# Finding 20's seven cited pairs (the seventh, cfg `0x00` -> `0xF5`, is a Rule A pair in
# 11.23; 13.6 routes the floorplan through the bulk-save block and this module returns an
# empty set for it, which is the honest answer). 14.7's `0x1B` -> `0xE4` and `0x14` -> `0xE7`
# are `inferred-from-code` and **unverified** — see `option_selector`'s docstring for the
# retraction of Finding 20's `0x1B` -> `0xE7`.

# The RV-C 0x1FED9 frame template's first word: `<u32 CAN id>` with the source byte still 0
# (the host fills in the panel's own address 0x9F in transit; every payload byte, selector
# included, is unchanged on the wire — `BUS-BASELINE.md:1015`).
FRAME_TAG_1FED9 = 0x19FED900

# The same first word for a DC_DIMMER_COMMAND_2 template (`00 db fe 19 <inst> ff <lvl> <cmd>
# ...`): 13.6's Bath/Cargo handlers copy one of these beside each `0x1FED9` write --
# `0x00259820` = instance 0x1A command 3, `0x00259838` = instance 0x19 command 3.
FRAME_TAG_1FEDB = 0x19FEDB00
DIMMER_COMMAND_OFF = 3

# Payload bytes 5..7 of the two template kinds: `ff 96 <sel> 0f <val> 00 d1 ea` is a write and
# `ff 96 <sel> 0f ff ff d3 ff` the read-back that follows it (PC.2 Result 3). Only writes
# carry a value, so only writes are attributed.
WRITE_FRAME_TAIL = b"\x00\xd1\xea"

# The config-set helper materialises this pseudo-PGN (`mov.w rN,#0x1ee00`) next to its
# config-buffer load; that pair is how the helper is located, in any build.
CONFIG_PSEUDO_PGN = 0x1EE00

# How far a straight-line run is followed past its join point, in instructions. The run also
# ends at the next `bl` or at a return.
#
# **This is the one constant whose value changes what is recovered, so it gets the full
# accounting.** The attributed selectors are flat across 12..400 (measured: 12, 13, 16, 20,
# 40, 100, 400 are byte-identical in all three builds). Below 12 rows are *lost*, never
# changed — no window setting anywhere reports a different selector for an offset, only fewer
# offsets. The boundary is 11.23's cfg `0x00` -> `0xF5`, the seventh oracle pair: its emit is
# the **12th** instruction after the `bl` @`0x0024B000`, because the handler stores a derived
# value into SRAM before sending —
#
#     0  bl 0x256e20        4  add.w r3,r3,#0x190     8  str  r3,[r2]
#     1  ldr.w r3,[r4,#0x204] 5  b     #0x24b016      9  ldr  r6,[pc,#0x148]
#     2  cmn.w r3,#0x190    6  movs  r3,#1           10  add  r5,sp,#4
#     3  beq   #0x24b014    7  ldr   r2,[pc,#0x154]  11  add.w r3,r6,#0x1ec
#                                                    12  ldm.w r3,{r0,r1,r2}
#
# — three instructions of preamble, then an if/else diamond (`0x24B00C..0x24B016`) that
# rejoins *before* the emit, then the store. So the spike's window of 10 missed it and 20
# does not. That is the reason the constant was raised, and it is a property of that
# handler's shape, not a threshold tuned until an expected byte appeared: 12 through 400 all
# give the same answer, and the pair it gains was already Finding 20's, recovered here by
# Rule A's own value correspondence (`v=0`, template `0x002571A4` carrying value 0).
#
# `SelectorSite.frames` — the run's *other* frames, which are context and not attributed —
# does keep growing with the window.
RUN_WINDOW = 20

# A Rule B gate must sit in its handler's prologue, within this many bytes of the entry (the
# real ones are 0..14 bytes in). Results are identical for 0x10, 0x20 and 0x40.
GATE_PROLOGUE = 0x20

# ...and the raw test of the loaded byte must follow within this many instructions.
GATE_TEST_WINDOW = 5

# A dispatch tree: a run of compares of ONE register against at least this many distinct
# constants, no two consecutive compares further apart than the gap. 13.6's tap dispatcher is
# a binary search over 16-bit action ids (`cmp r2,#0x850 ; beq ; bls ; movw r3,#0x856 ;
# cmp r2,r3 ; beq ; bhi`), so both branch targets *and* fallthroughs are handler entries.
DISPATCH_TREE_GAP = 0x200
DISPATCH_TREE_MIN_CONSTANTS = 8


@dataclass(frozen=True)
class SelectorFrame:
    """One 12-byte `0x1FED9` template copied by an `ldm rX,{r0,r1,r2}`."""

    emit: int        # address of the `ldm`
    template: int    # mapped address of the template in GuiCode's .rodata
    selector: int    # payload byte 2
    value: int       # payload byte 4
    write: bool      # False for the `ff ff d3 ff` read-back variant


@dataclass(frozen=True)
class DimmerFrame:
    """One 12-byte `0x1FEDB` (DC_DIMMER_COMMAND_2) template copied by the same `ldm` idiom
    in a tap handler's run -- the companion OFF a rebinding option sends to the channel it
    abandons (GUICODE Finding 23's final section; on the wire in `BUS-BASELINE.md` PC.33)."""

    emit: int        # address of the `ldm`
    template: int    # mapped address of the template
    instance: int    # payload byte 0 -- the G12 output instance (== channel, PC.8/PC.9)
    command: int     # payload byte 3 (3 = OFF)


@dataclass(frozen=True)
class SelectorSite:
    """One recovered join: a cfg offset, and the frames the run beside it emits."""

    offset: int
    rule: str                          # "A" (bl config_set) or "B" (gate register)
    join: int                          # the `bl` (A) or the gate `ldrb` (B)
    frames: tuple[SelectorFrame, ...]  # every write frame in the run, in order
    handler: int | None = None         # dispatcher handler entry, rule B only
    register: int | None = None        # the gate's SRAM register, rule B only
    predicate: tuple[str, int] | None = None   # ("eq"|"ne", K): what this arm implies of it
    dimmer_frames: tuple[DimmerFrame, ...] = ()   # every 0x1FEDB template in the same run

    @property
    def selector(self) -> int:
        """The selector attributed to the offset: the first write frame in the run."""
        return self.frames[0].selector

    @property
    def value(self) -> int:
        """The value that frame writes."""
        return self.frames[0].value


def selector_sites(data: bytes) -> Dict[int, Tuple[SelectorSite, ...]]:
    """`{cfg offset: (SelectorSite, ...)}` — every join the two rules prove, with the
    instruction addresses that prove it.

    `data` is the full MCF container. Raises `CapstoneUnavailable` if capstone is missing.
    """
    return dict(_selector_sites(bytes(data)))


def selectors_by_offset(data: bytes) -> Dict[int, FrozenSet[int]]:
    """`{cfg offset: frozenset of attributed selectors}` for every offset with a site."""
    return {offset: frozenset(site.selector for site in sites)
            for offset, sites in selector_sites(data).items()}


def selector_for_offset(data: bytes, offset: int) -> FrozenSet[int]:
    """The `0x15FCE` selectors this config-buffer offset writes — empty when nothing is
    recovered.

    A **set**, because the join is a relation: an offset is written from as many sites as the
    UI has ways to change it, and nothing guarantees they agree (11.23's cfg `0x0F` yields
    `{0xF0, 0xF5}` from three sites). A singleton is the common case and the only one
    `option_selector` will act on.
    """
    return selectors_by_offset(data).get(offset, frozenset())


def option_selector(data: bytes, offset: int) -> int | None:
    """The one selector this offset writes, or None when the sites disagree or find nothing.

    **Finding 20's `0x1B` -> `0xE7` is retracted.** That inference came from eliminating over
    14.7's variable-table option run `{E7,E4,E5,E3,EB,EC}`, on the unstated premise that
    `0xE4` there was Bath Light's kept-but-hidden declaration. The code contradicts it: 14.7's
    dispatcher id `0x855` handler gates on `0x200396FE` (Starlink Power, cfg `0x1B`, attested
    from GuiData by `options.option_bindings` and from GuiCode by `config_offsets`) and emits
    `0x0025C0D8 = FF 96 E4 0F 01 00 D1 EA`, so this function returns **`0xE4`** for `0x1B` —
    and `0xE7` for cfg `0x14` (Lithionics Batteries), the row Finding 20's reading had to
    leave with no selector at all.

    Both 14.7 rows are `inferred-from-code` and **not verified**: no oracle entry and no
    14.7 capture exists. Finding 20's standing advice holds — toggle Starlink Power on a 14.7
    panel and diff the `0x1FED9` write before treating either as fact.
    """
    found = selector_for_offset(data, offset)
    return next(iter(found)) if len(found) == 1 else None


def off_channels_by_selector(data: bytes) -> Dict[int, int]:
    """`{selector: G12 instance}` -- the GuiCode half of the `channel` join.

    GUICODE Finding 23's rule, wire-confirmed in PC.33: a tap handler that rebinds a control
    sends a `0x1FEDB` command-3 OFF to the channel it abandons, **beside the `0x1FED9` write
    that sets that channel's selector to 0**. So in any run carrying exactly one OFF and
    exactly one `:= 0` write, the write's selector and the OFF's instance are a pair. 13.6's
    two select arms give both pairs: `0x0024CE0C` (Cargo, `E3 := 1`, `E4 := 0`, OFF `0x1A`)
    -> `E4` = 26, and `0x0024CF2A` (Bath, `E4 := 1`, `E3 := 0`, OFF `0x19`) -> `E3` = 25.

    Only the fall-through arm of a Rule-B gate is swept (`_gate_sites` follows the gate, not
    its `bne` target), so the single-selector deselect arms Finding 23 also cites
    (`0x0024CE7C`, `0x0024CF9A`) are not sites here; the two-write arms above are what the
    sweep reaches and they suffice. A run with two OFFs or two `:= 0` writes is ambiguous
    and contributes nothing; a selector paired with two different instances is dropped.
    Rows whose handlers send no companion (Bunk Accent, Black Tank, Heat Pump -- PC.34) are
    simply absent. `optiongates.gated_channel` derives the same number from GuiData with no
    disassembler; `tests/test_option_gates.py` holds the two routes to agreement.
    """
    pairs: Dict[int, Set[int]] = {}
    for sites in selector_sites(data).values():
        for site in sites:
            zeros = {f.selector for f in site.frames if f.value == 0}
            offs = {f.instance for f in site.dimmer_frames if f.command == DIMMER_COMMAND_OFF}
            if len(zeros) == 1 and len(offs) == 1:
                pairs.setdefault(next(iter(zeros)), set()).add(next(iter(offs)))
    return {sel: next(iter(chs)) for sel, chs in pairs.items() if len(chs) == 1}


def option_off_channel(data: bytes, selector: int) -> int | None:
    """The instance the panel turns OFF when `selector` is written 0, or None
    (`off_channels_by_selector`)."""
    return off_channels_by_selector(data).get(selector)


@memo.content_cached(maxsize=4, copy=None)
def _stream(data: bytes) -> Tuple[bytes, int, list]:
    """`(code, base, _sweep(code, base))` for an MCF, swept once."""
    section = theme.guicode_section(data)
    base = theme.guicode_base(section)
    code = data[section.offset:section.offset + section.length]
    return code, base, _sweep(code, base)


def config_set_helper(data: bytes) -> int | None:
    """The config-set helper's entry address (`_find_config_set`): 13.6 `0x00259100`, 14.7
    `0x0025B9B0`, 11.23 `0x00256E20`; None when the build has no such site."""
    return _find_config_set(_stream(bytes(data))[2])


@lru_cache(maxsize=4)
def _selector_sites(data: bytes) -> Dict[int, Tuple[SelectorSite, ...]]:
    code, base, stream = _stream(data)
    sites: Dict[int, list] = {}
    helper = _find_config_set(stream)
    if helper is not None:
        for site in _rule_a(stream, code, base, helper):
            sites.setdefault(site.offset, []).append(site)
    registers = {register: offset
                 for offset, register in _trace_config_offsets(code, base).items()}
    if registers:
        for site in _rule_b(stream, code, base, registers, _dispatch_handlers(stream)):
            sites.setdefault(site.offset, []).append(site)
    return {offset: tuple(found) for offset, found in sorted(sites.items())}


def _sweep(code: bytes, base: int) -> list:
    """`[(insn, registers_before_it)]` for a linear Thumb sweep of the whole section, with a
    register file of *concrete constants only*.

    Deliberately not `_trace_config_offsets`'s tracer: that one tracks three kinds of tagged
    state (config-buffer identity, unread config byte, clamp guard) and is tuned to survive
    the unpack's local clamp branches. This one only needs "which register holds which
    address/immediate right here", which is what resolves a literal-pool load into a template
    address and `r1`/`r2` into an offset and a value. A register whose *write* this sweep
    cannot follow is dropped (`regs_access`), never guessed.

    **Two ways this model is looser than the sibling tracer's, disclosed** (docstring
    correction, review fix wave 2 — an earlier version of this line claimed "so a wrong value
    can never be reported", which is not true of *this* sweep):

      1. **Calls do not clear the register file.** `bl`/`blx` are not special-cased here, so
         they fall to the `regs_access` default — and capstone reports only `lr` (and `pc`)
         as written by them. Caller-saved `r0`-`r3` therefore *survive* a call in this model,
         whereas AAPCS says they are dead. `_trace_config_offsets` does `clear_all()` on
         every `bl`/`blx`/return/unconditional jump and says so in the module docstring; this
         sweep does not, and the asymmetry between the two tracers is deliberate but was
         previously undocumented.
      2. **No clearing at branch joins.** The sweep is linear: a basic block reached by a
         branch from elsewhere inherits whatever register file the *fallthrough* predecessor
         left behind, which need not hold on the branched-in path.

    Neither has produced a wrong result on this corpus, and two things downstream stand
    between them and one: `_run_frames` breaks at the next `bl`, so a scan stays inside one
    handler's straight-line tail rather than walking into the next row's emits; and every
    attribution is gated by the value check — the emitted payload value must equal the value
    being written. But "cannot report a wrong value" is a property of `_trace_config_offsets`,
    not of this function; what this one guarantees is only that it never *invents* a register
    value.
    """
    try:
        import capstone
        from capstone.arm import ARM_OP_IMM, ARM_OP_MEM, ARM_OP_REG, ARM_REG_PC
    except ImportError as exc:  # pragma: no cover - capstone is installed in this repo
        raise CapstoneUnavailable(
            "capstone is required to disassemble the GuiCode selector join") from exc

    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    md.detail = True

    out: list = []
    regs: Dict[int, int] = {}
    pos = 0
    while pos < len(code) - 1:
        try:
            ins = next(md.disasm(code[pos:pos + 4], base + pos, count=1))
        except StopIteration:
            # a data island: resync two bytes on with the register file cleared
            pos += 2
            regs = {}
            continue
        mnem = ins.mnemonic
        ops = ins.operands
        before = dict(regs)
        handled = False
        if (mnem in ("ldr", "ldr.w") and len(ops) == 2
                and ops[1].type == ARM_OP_MEM and ops[1].mem.base == ARM_REG_PC):
            lit = ((ins.address + 4) & ~3) + ops[1].mem.disp - base
            if 0 <= lit <= len(code) - 4:
                regs[ops[0].reg] = struct.unpack_from("<I", code, lit)[0]
                handled = True
        elif (mnem in ("mov", "movs", "mov.w", "movw") and len(ops) == 2
                and ops[1].type == ARM_OP_IMM):
            regs[ops[0].reg] = ops[1].imm & 0xFFFFFFFF
            handled = True
        elif (mnem == "movt" and len(ops) == 2 and ops[1].type == ARM_OP_IMM
                and ops[0].reg in regs):
            regs[ops[0].reg] = (regs[ops[0].reg] & 0xFFFF) | ((ops[1].imm & 0xFFFF) << 16)
            handled = True
        elif (mnem in ("add", "add.w", "adds", "addw") and len(ops) == 3
                and ops[1].type == ARM_OP_REG and ops[2].type == ARM_OP_IMM
                and ops[1].reg in regs):
            regs[ops[0].reg] = (regs[ops[1].reg] + ops[2].imm) & 0xFFFFFFFF
            handled = True
        elif (mnem in ("mov", "mov.w") and len(ops) == 2 and ops[1].type == ARM_OP_REG
                and ops[1].reg in regs):
            regs[ops[0].reg] = regs[ops[1].reg]
            handled = True
        if not handled:
            for reg in ins.regs_access()[1]:
                regs.pop(reg, None)
        out.append((ins, before))
        pos += ins.size
    return out


def _frame_at(code: bytes, base: int, addr: int) -> Tuple[int, int, bool] | None:
    """`(selector, value, is_write)` if `addr` holds a 12-byte 0x1FED9 template."""
    lo = addr - base
    if not (0 <= lo <= len(code) - 12):
        return None
    if struct.unpack_from("<I", code, lo)[0] != FRAME_TAG_1FED9:
        return None
    return code[lo + 6], code[lo + 8], code[lo + 9:lo + 12] == WRITE_FRAME_TAIL


def _template_copy(ins, regs: Dict[int, int], code: bytes, base: int) -> SelectorFrame | None:
    """The emit idiom: `ldm <concrete address>,{r0,r1,r2}` where that address holds a
    template. The 12-byte `ldm`/`stm` pair *is* the frame build (Task 2 Step 3)."""
    from capstone.arm import ARM_OP_REG

    if ins.mnemonic not in ("ldm", "ldm.w", "ldmia", "ldmia.w"):
        return None
    if not ins.operands or ins.operands[0].type != ARM_OP_REG:
        return None
    addr = regs.get(ins.operands[0].reg)
    if addr is None:
        return None
    frame = _frame_at(code, base, addr)
    if frame is None:
        return None
    return SelectorFrame(emit=ins.address, template=addr, selector=frame[0], value=frame[1],
                         write=frame[2])


def _run_frames(stream: list, index: int, code: bytes, base: int) -> Tuple[SelectorFrame, ...]:
    """Every write frame emitted in the straight-line run *after* `index`, in order.

    **Forward-only** — false-positive class (i). The run ends at the next `bl` (the next row's
    config-set call, or any other call out), at a return, or after `RUN_WINDOW` instructions.
    A `blx rX` does *not* end it: that is the frame send itself, through the host callback
    slot, and a handler sends several in a row.
    """
    import capstone

    out = []
    for j in range(index + 1, min(index + 1 + RUN_WINDOW, len(stream))):
        ins, regs = stream[j]
        if ins.mnemonic == "bl" or ins.group(capstone.CS_GRP_RET):
            break
        frame = _template_copy(ins, regs, code, base)
        if frame is not None and frame.write:
            out.append(frame)
    return tuple(out)


def _dimmer_at(code: bytes, base: int, addr: int) -> Tuple[int, int] | None:
    """`(instance, command)` if `addr` holds a 12-byte 0x1FEDB template."""
    lo = addr - base
    if not (0 <= lo <= len(code) - 12):
        return None
    if struct.unpack_from("<I", code, lo)[0] != FRAME_TAG_1FEDB:
        return None
    return code[lo + 4], code[lo + 7]


def _run_dimmers(stream: list, index: int, code: bytes, base: int) -> Tuple[DimmerFrame, ...]:
    """Every `0x1FEDB` template the straight-line run after `index` copies -- the same
    window, the same `ldm` idiom and the same stop rule as `_run_frames`, only the tag
    differs. Empty for a row whose handler sends no companion (Bunk Accent, Black Tank --
    PC.34), which is a fact about the row, not a miss."""
    import capstone
    from capstone.arm import ARM_OP_REG

    out = []
    for j in range(index + 1, min(index + 1 + RUN_WINDOW, len(stream))):
        ins, regs = stream[j]
        if ins.mnemonic == "bl" or ins.group(capstone.CS_GRP_RET):
            break
        if ins.mnemonic not in ("ldm", "ldm.w", "ldmia", "ldmia.w"):
            continue
        if not ins.operands or ins.operands[0].type != ARM_OP_REG:
            continue
        addr = regs.get(ins.operands[0].reg)
        if addr is None:
            continue
        found = _dimmer_at(code, base, addr)
        if found is not None:
            out.append(DimmerFrame(emit=ins.address, template=addr,
                                   instance=found[0], command=found[1]))
    return tuple(out)


def _find_config_set(stream: list) -> int | None:
    """The config-set helper's entry address, located structurally: the site that
    materialises the pseudo-PGN `0x1EE00` within 0x40 bytes of a `[rX,#0x214]` config-buffer
    load (13.6 `0x00259100`, 14.7 `0x0025B9B0` = Finding 20's `FUN_0025b9b0`, 11.23
    `0x00256E20`). The entry itself is then the most-called `bl` target landing just before
    that load — the helper's `cmp r1,#0xf9` bound check.
    """
    from capstone.arm import ARM_OP_IMM, ARM_OP_MEM

    pgn: list = []
    cfgbuf: list = []
    calls: Dict[int, int] = {}
    for ins, _regs in stream:
        ops = ins.operands
        if (ins.mnemonic in ("mov", "mov.w", "movw") and len(ops) == 2
                and ops[1].type == ARM_OP_IMM and (ops[1].imm & 0xFFFFFFFF) == CONFIG_PSEUDO_PGN):
            pgn.append(ins.address)
        elif (ins.mnemonic in ("ldr", "ldr.w") and len(ops) == 2
                and ops[1].type == ARM_OP_MEM and ops[1].mem.disp == CONFIG_BUFFER_OFFSET):
            cfgbuf.append(ins.address)
        elif ins.mnemonic == "bl" and ops and ops[0].type == ARM_OP_IMM:
            target = ops[0].imm & ~1
            calls[target] = calls.get(target, 0) + 1
    bodies = [c for c in cfgbuf if any(0 < p - c < 0x40 for p in pgn)]
    if not bodies:
        return None
    anchor = bodies[0]
    near = {target: n for target, n in calls.items() if anchor - 0x20 <= target <= anchor}
    if not near:
        return None
    return max(near, key=lambda target: (near[target], -target))


def _rule_a(stream: list, code: bytes, base: int, helper: int):
    """Rule A — adjacency to `bl <config_set>`, with the value cross-check.

    `r1` is the cfg offset and `r2` the value the call writes; the row's own frame is the
    first write template in the run *carrying that same value*. Requiring the value match is
    what keeps a burst of neighbouring frames (guard (iv)) from being attributed: 13.6's cfg
    `0x10` run also emits `sel 0xEA value 2` beside its own `sel 0xE8 value 0`.
    """
    from capstone.arm import ARM_OP_IMM, ARM_REG_R1, ARM_REG_R2

    for i, (ins, regs) in enumerate(stream):
        if ins.mnemonic != "bl" or not ins.operands or ins.operands[0].type != ARM_OP_IMM:
            continue
        if (ins.operands[0].imm & ~1) != helper:
            continue
        offset, value = regs.get(ARM_REG_R1), regs.get(ARM_REG_R2)
        if offset is None or value is None:
            continue
        frames = _run_frames(stream, i, code, base)
        if frames and frames[0].value == value:
            yield SelectorSite(offset=offset, rule="A", join=ins.address, frames=frames,
                               dimmer_frames=_run_dimmers(stream, i, code, base))


def _rule_b(stream: list, code: bytes, base: int, registers: Dict[int, int],
            handlers: Dict[int, FrozenSet[int]]):
    """Rule B — the gate register, with all three guards (see the section header).

    Three requirements, and the section header records which of them is actually load-bearing
    on this corpus. A gate must be a *raw* equality test of the loaded byte (guard (ii) — the
    floorplan guard modifies the byte before comparing it, and compares a range); the first
    frame's value must be a provable change on this arm (guard (iii) — **this is the one that
    rejects the class-(iii) false positive**); and the load must sit in a dispatcher handler's
    prologue, which on this corpus rejects nothing and is kept for build-generality.
    """
    entries = sorted(handlers)
    for index, register, predicate in _gate_sites(stream, registers):
        gate = stream[index][0]
        handler = _prologue_of(entries, gate.address)
        if handler is None:
            continue
        frames = _run_frames(stream, index, code, base)
        if not frames:
            continue
        kind, constant = predicate
        changed = frames[0].value != constant if kind == "eq" else frames[0].value == constant
        if not changed:
            continue
        yield SelectorSite(offset=registers[register], rule="B", join=gate.address,
                           frames=frames, handler=handler, register=register,
                           predicate=predicate,
                           dimmer_frames=_run_dimmers(stream, index, code, base))


def _prologue_of(entries: list, address: int) -> int | None:
    """The dispatcher handler whose prologue contains `address`, if any."""
    import bisect

    pos = bisect.bisect_right(entries, address) - 1
    if pos < 0:
        return None
    entry = entries[pos]
    return entry if address - entry <= GATE_PROLOGUE else None


def _gate_sites(stream: list, registers: Dict[int, int]):
    """`(index, register, predicate)` for every `ldrb rd,[rn]` of a known option register
    whose loaded byte is then tested **raw**.

    `predicate` is what the *fallthrough* arm — the run this scan follows — implies about the
    register: `("eq", K)` for "it equals K", `("ne", K)` for "it does not". `cbnz rd,else`
    gives `("eq", 0)`; `cmp rd,#K ; beq else` gives `("ne", K)`.

    Guard (ii) is these two requirements together — "raw" (any instruction writing `rd`
    between the load and the test ends the search) and "an equality" (a range test says only
    that the register is one of several values, which no value check can use). 13.6's
    floorplan guard `ldrb r3,[r3] ; subs r3,#4 ; uxtb r3,r3 ; cmp r3,#1 ; bls` fails both, so
    cfg `0x00` is never handed the Cargo/Bath selectors. Relaxing both together puts
    `{0xE3, 0xE4}` back on cfg `0x00`, which is how the pair was measured.
    """
    from capstone.arm import ARM_CC_AL, ARM_OP_IMM, ARM_OP_MEM

    import capstone

    for i, (ins, regs) in enumerate(stream):
        if ins.mnemonic not in ("ldrb", "ldrb.w"):
            continue
        ops = ins.operands
        if len(ops) != 2 or ops[1].type != ARM_OP_MEM or ops[1].mem.index != 0:
            continue
        held = regs.get(ops[1].mem.base)
        if held is None:
            continue
        register = held + ops[1].mem.disp
        if register not in registers:
            continue
        rd = ops[0].reg
        predicate = None
        for j in range(i + 1, min(i + 1 + GATE_TEST_WINDOW, len(stream))):
            test = stream[j][0]
            if test.mnemonic in ("cbz", "cbnz") and test.operands \
                    and test.operands[0].reg == rd:
                predicate = ("eq", 0) if test.mnemonic == "cbnz" else ("ne", 0)
                break
            if test.mnemonic in ("cmp", "cmp.w") and len(test.operands) == 2 \
                    and test.operands[0].reg == rd and test.operands[1].type == ARM_OP_IMM:
                constant = test.operands[1].imm
                if j + 1 < len(stream):
                    branch = stream[j + 1][0]
                    if branch.group(capstone.CS_GRP_JUMP) and branch.cc != ARM_CC_AL:
                        if branch.mnemonic.startswith("beq"):
                            predicate = ("ne", constant)
                        elif branch.mnemonic.startswith("bne"):
                            predicate = ("eq", constant)
                break
            if rd in test.regs_access()[1]:
                break
        if predicate is not None:
            yield i, register, predicate


def _dispatch_handlers(stream: list) -> Dict[int, FrozenSet[int]]:
    """`{handler entry: the ids that reach it}` for every *dispatch tree* in the section.

    A dispatch tree is a run of compares of one register against at least
    `DISPATCH_TREE_MIN_CONSTANTS` distinct constants with no gap wider than
    `DISPATCH_TREE_GAP` — the shape of 13.6's tap dispatcher, a binary search over 16-bit
    action ids. Both the branch target and the fallthrough are handler entries, because the
    search takes both: `cmp r2,r3 ; beq.w <0x856's handler> ; bhi.w <the greater half>` is
    how the Bath Light row (whose gate this rule needs) is reached at all.

    This is not identification of *which* id a handler serves — it does not need to be. It is
    a containment test: a register read that is not in any handler prologue is not a row's
    gate.

    **Inert on this corpus, and kept anyway.** Switching the containment off changes no
    selector in any build — it only admits additional *same-selector* sites (13.6 cfg `0x13`
    +2, `0x19`/`0x1A` +1 each; 14.7 cfg `0x13` +1), and it does *not* admit the class-(iii)
    false positive, whose gate at 14.7 `0x0024E6D4` this filter accepts anyway (its handler
    entry `0x0024E6D2` is two bytes away). The value check is what rejects that one. So this
    filter currently rejects nothing real; it is retained because a build outside this corpus
    may read an option register from a run that has no dispatcher above it at all, and
    because loosening it can only ever admit a gate the value check must still pass. It is
    not free: it costs two cited true sites on 13.6 (`0x0024BF76` `cmp #3` -> `EF:=3`,
    `0x0024D106` `cmp #2` -> `EF:=2`).
    """
    from capstone.arm import ARM_CC_AL, ARM_OP_IMM, ARM_OP_REG

    import capstone

    compares: Dict[int, list] = {}
    for i, (ins, regs) in enumerate(stream):
        if ins.mnemonic not in ("cmp", "cmp.w"):
            continue
        ops = ins.operands
        if len(ops) != 2 or ops[0].type != ARM_OP_REG:
            continue
        if ops[1].type == ARM_OP_IMM:
            constant = ops[1].imm
        elif ops[1].type == ARM_OP_REG:
            constant = regs.get(ops[1].reg)
        else:
            continue
        if constant is None:
            continue
        # a cmp may be read by a RUN of conditional branches sharing its flags
        for j in range(i + 1, min(i + 4, len(stream))):
            branch = stream[j][0]
            if not branch.group(capstone.CS_GRP_JUMP) or branch.cc == ARM_CC_AL:
                break
            if not branch.operands or branch.operands[0].type != ARM_OP_IMM:
                break
            compares.setdefault(ops[0].reg, []).append(
                (ins.address, constant, branch.operands[0].imm & ~1, branch.address + branch.size))

    entries: Dict[int, set] = {}
    for sites in compares.values():
        sites.sort(key=lambda site: (site[0], site[1]))
        run: list = []
        for site in sites + [None]:
            if run and (site is None or site[0] - run[-1][0] > DISPATCH_TREE_GAP):
                if len({constant for _a, constant, _t, _f in run}) >= DISPATCH_TREE_MIN_CONSTANTS:
                    for _addr, constant, target, fallthrough in run:
                        entries.setdefault(target, set()).add(constant)
                        entries.setdefault(fallthrough, set()).add(constant)
                run = []
            if site is not None:
                run.append(site)
    return {entry: frozenset(ids) for entry, ids in entries.items()}
