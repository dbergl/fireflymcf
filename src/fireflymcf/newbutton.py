"""Build driver for cloning an on-screen button into a NEW control that commands its
own RV-C instance.

A byte-cloned object (Water Pump, obj 295) inherits the donor's command-group id (44),
so the clone and Water Pump would share the same binding entries (174/175): a
`set_channel` on either moves both, and `set_channel`'s invariant I6 refuses to touch a
group two controls share. The clone therefore needs its **own** command group.

There are **no spare binding-table entries** on any corpus build -- all 746 are inside
some group's `[start, start+count)` window -- so "repurpose a spare binding" does not
exist. What *does* exist is a supply of command **groups** that no located control
commands and no object references: `unused_command_groups`. `allocate_command_group`
hands the build one of those (route "repurpose", no edits -- Task 3 retargets its
bindings with `set_channel`), and only if that supply were empty would it fall back to
growing the tables (route "grow").

Mechanism note (`guidata.find_control_groups`): an object references a command group
through the trailing `GROUP_REFERENCE_TAIL` u16 of an `IMAGE_OPCODE` (0x0C) record. That
is the *only* object->group reference; a naive "any wtype-12 record" scan mis-attributes
(it wrongly flags the live group 80), so "referenced" here is built exactly the way
`find_control_groups` reads the join.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass

from . import (donors, firmware, grammar, guidata, insert, layout, memo, pagewalk, patch,
               records, relocate)
from .cli import _resolve_page_root


@memo.content_cached(copy=set)
def _object_referenced_groups(data: bytes) -> set[int]:
    """Every group id an object references, via the `IMAGE_OPCODE` record's trailing
    `GROUP_REFERENCE_TAIL` u16 -- the only object->group reference there is. Out-of-range
    tails are ignored (they are image-table indices, not group indices).

    **Corrected 2026-08-19.** This used to walk `guidata.iter_screens`, the older
    boundary-fitted positional screen scan that `records.py` replaced -- so it saw only the
    objects that walk reaches and reported ~21 of the ~230 groups. Objects 108 and 186 both
    name group 65 in stock 13.6, and it missed both, which is how `unused_command_groups`
    came to offer live groups for repurposing. It now walks the object-pointer table and
    decodes each object with `records.object_records`, the decoder validated by the boundary
    oracle on every object in all three builds, and considers EVERY `IMAGE_OPCODE` record in
    an object rather than stopping at the first (object 186 carries three, naming 65/32/31).
    """
    gt = guidata.group_table(data)
    gs, _ = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    referenced: set[int] = set()
    for idx in range(len(table)):
        try:
            recs = records.object_records(data, gs + table[idx])
        except Exception:
            continue                      # an object the decoder refuses cannot be a reference
        for rec in recs:
            if rec.wtype != guidata.IMAGE_OPCODE:
                continue
            gid = struct.unpack_from("<H", data, rec.end - guidata.GROUP_REFERENCE_TAIL)[0]
            if gid < len(gt):
                referenced.add(gid)
    return referenced


def group_command_pairs(data: bytes, gid: int) -> set:
    """The `(channel, wire_command)` pairs group `gid` can transmit.

    The wire command is binding byte[4] (byte[3] is metadata -- CAN capture, 2026-08-17).
    """
    gt = guidata.group_table(data)
    bt = guidata.binding_table(data)
    g = gt[gid]
    return {(bt[j].channel, bt[j].raw[4])
            for j in range(g.start, g.start + g.count) if 0 <= j < len(bt)}


@memo.content_cached(copy=None)
def _all_group_command_pairs(data: bytes) -> tuple:
    """`group_command_pairs` for every group, in one pass over the two tables (memoised on
    content -- `sole_produced_pairs` needs every OTHER group's pairs, and used to rebuild all
    233 sets per call, 2,563 table walks per build)."""
    gt = guidata.group_table(data)
    bt = guidata.binding_table(data)
    return tuple(frozenset((bt[j].channel, bt[j].raw[4])
                           for j in range(g.start, g.start + g.count) if 0 <= j < len(bt))
                 for g in gt)


def sole_produced_pairs(data: bytes, gid: int) -> set:
    """`(channel, command)` pairs that ONLY group `gid` produces.

    Repurposing such a group deletes a capability from the file: nothing else can send that
    command to that channel any more. This is the check that matters, and it needs no
    reachability analysis at all -- just the group and binding tables.
    """
    pairs = _all_group_command_pairs(data)
    mine = {p for p in pairs[gid] if p[0] is not None}
    if not mine:
        return set()
    others: set = set()
    for other, ps in enumerate(pairs):
        if other != gid:
            others |= ps
    return mine - others


@memo.content_cached
def unused_command_groups(data: bytes) -> list[int]:
    """Group-table indices that are safe to repurpose, in ascending id order.

    Three independent exclusions, of which the third is the one that actually holds:

    1. referenced by no object record tail (`_object_referenced_groups`);
    2. named by no located control (`guidata.find_control_groups`);
    3. **the sole producer of no `(channel, command)` pair** (`sole_produced_pairs`).

    **Why (3) was added, 2026-08-19.** (1) and (2) are reachability scans and both are badly
    incomplete on this file -- they locate roughly 21 and 17 of the ~230 groups -- so a group
    carrying perfectly live bindings sails through both. On stock 13.6 that made **23 of 33**
    candidates unsafe, including group 3, the sole producer of `(3, 1)` = AWNING EXT, and
    group 4, the sole producer of `(4, 1)` = AWNING RET. `build_home_button` took group 3, so
    every Pin-15 build (and their descendants -- all flashed) silently lost the
    awning-extend command. Criterion (3) is not a reachability guess: it is a direct statement
    about what the file can still transmit, computed from the two tables alone, and it demotes
    exactly those groups.

    Restricted, as before, to Water-Pump-shaped groups: every binding a
    `DGN_DC_DIMMER_COMMAND_2`, count 2-3. Never a group GuiCode drives by number
    (`guicode_selected_groups`; the finish gate refuses reusing one), so the allocator is not
    the last to notice: none is a candidate on 11.23, 13.6 or 14.7 (2026-11-08), and the
    subtraction keeps that true on any image. Refuses `VariantSetterUnknown` where the driven
    groups are not known.
    """
    gt = guidata.group_table(data)
    bt = guidata.binding_table(data)
    referenced = _object_referenced_groups(data)
    control = {c.group for c in guidata.find_control_groups(data)}
    selected = guicode_selected_groups(data)
    out: list[int] = []
    for gid, g in enumerate(gt):
        if gid in referenced or gid in control or gid in selected:
            continue
        if not (2 <= g.count <= 3):
            continue
        ents = [bt[j] for j in range(g.start, g.start + g.count) if 0 <= j < len(bt)]
        if len(ents) != g.count or not all(
                e.dgn_low == guidata.DGN_DC_DIMMER_COMMAND_2 for e in ents):
            continue
        if sole_produced_pairs(data, gid):
            continue
        out.append(gid)
    return out


# 13.6's setter cell (`hooklocate.host_api_cells` +0x04 on 13.6). A test expectation and a docs
# anchor only: the scan reads the located cell on every image.
SET_VARIANT_PTR = 0x20039908

# The scan's bounds, passed to the cached scan as arguments (`memo`'s rule). How many
# instructions the paths from one load of the cell may visit while it is live (the corpus's
# most is 1586, 11.23; exhausting it refuses); how many instructions a deref may take to reach
# its `blx`; how many unconditional `b`s that path may follow (14.7 `0x2490D0` reaches its
# `blx` through `b 0x249164`).
SETTER_LOAD_WINDOW = 4096
SETTER_CALL_WINDOW = 16
SETTER_BRANCH_HOPS = 1


class VariantSetterUnknown(ValueError):
    """VARIANT_SETTER_UNKNOWN: which groups GuiCode drives by number is not known on this
    image. The variant setter's cell (`hooklocate.host_api_cells` +0x04) did not locate, the
    LCD did not prove that cell's function the ungated variant writer
    (`lcdgroups.host_writer`), or a use of the cell did not resolve: a deref that reaches no
    `blx` of its register, a call whose `r0` is not a constant, another store to the cell, a
    use other than a deref, or a `ldr.w`/`movw`+`movt` form of it the scan does not follow."""
    code = "VARIANT_SETTER_UNKNOWN"


@dataclass(frozen=True)
class SetterCall:
    load: int           # the `ldr rN,[pc,#]` of the cell
    deref: int          # the `ldr rX,[rN]`
    blx: int            # the `blx rX` it reaches
    group: int          # the constant in r0 there


@dataclass(frozen=True)
class SetterScan:
    cell: int
    n_literal_loads: int  # `ldr rN,[pc,#]` of the cell in the decode the walk runs on
    n_raw_loads: int    # the same by a raw halfword scan (0x48xx and ldr.w): must agree
    n_derefs: int
    n_calls: int        # == n_derefs by construction (an unresolved deref refuses)
    calls: tuple        # `SetterCall`, in address order
    writer: object      # `lcdgroups.HostWriter`: the LCD proof of what the cell holds

    @property
    def groups(self) -> frozenset:
        return frozenset(c.group for c in self.calls)


def guicode_selected_groups(data: bytes) -> frozenset:
    """Group indices GuiCode passes to `setVariant(group, v)`: every group GuiCode drives by
    number, from outside the screen (`setter_calls`).

    Why an allocator avoids them (coach, 2026-09-30): 13.6 sets the variant of groups 130-139
    (`0x82`-`0x8B`) as a block (`0x24AC66`-`0x24ACB0`, `0x24AF1A`-`0x24AF64`); no stock touch
    region uses 136-139, and `free_group_slots` listed them. A Track + on group 136 sent a tap
    but never repeated on a hold, and the APS-500 reboot (137, hold-only) sent nothing, while
    the identically built 96/126/127 worked. The setter GuiCode calls (`lcdgroups.host_writer`,
    13.6 `FUN_0021B678`) rewrites both variant counters of the group's record with no gate, and
    the after-hold path reads one of them; that GuiCode drives these groups is established.

    **Driven is sufficient to exclude, not proof that a hold is dead.** Group 129 (Low Volts) is
    driven on 13.6 and 14.7, and a hold on it works on the coach (2026-10-19 and every build
    since); holds died on 13.6's 136/137 (coach) and on 14.7's 17 and 136 (emulated,
    2026-11-08). What separates the two is not established (inference: a per-tick unconditional
    write clobbers a hold's state, an event-driven write does not). The allocators exclude every
    driven group as a conservative superset; an op that adds a hold to an existing control
    (`set_gesture`) is not refused on a driven group.

    Refuses `VariantSetterUnknown` rather than answer with fewer groups than GuiCode drives.
    """
    return setter_calls(data).groups


def setter_calls(data: bytes) -> SetterScan:
    """Every call GuiCode makes through the variant setter's cell, with its proof.

    1. The cell is `hooklocate.host_api_cells(data)[0x04]`, which refuses a second entry0 store.
    2. `lcdgroups.host_writer` proves the word in it is the LCD's ungated variant writer and
       that GuiCode's `r0` is the group-table index.
    3. A linear sweep of GuiCode visits every `ldr rN,[pc,#]` whose pool word is the cell, and
       every `ldr rX,[rN]` on every path while `rN` still holds it: the fall-through, each
       conditional branch's target and each unconditional `b` followed, up to a write of `rN`,
       a return, or a call when `rN` is caller-saved. A walk that exhausts
       `SETTER_LOAD_WINDOW` instructions, or branches to an undecoded address, refuses.
    4. Each deref must reach `blx rX` within `SETTER_CALL_WINDOW` instructions, following at
       most `SETTER_BRANCH_HOPS` unconditional `b`, with `r0` a constant by propagation from the
       deref (`movs r0,#k`, or `mov r0,rY` after `movs rY,#k`). The path is linear: a
       conditional branch (`b<cc>`, `cbz`/`cbnz`), or a conditional call or write of `r0`, the
       call register or `pc`, between the deref and its `blx` refuses (none on the corpus,
       2026-11-08).

    **Completeness.** `n_derefs == n_calls` holds by construction: a deref that does not
    resolve refuses. The completeness check is the load census: a raw halfword scan for
    `ldr rN,[pc,#]` (`0x48xx`) whose pool word is the cell must count exactly the loads the
    resyncing decode saw (`n_raw_loads == n_literal_loads`), so no load hides inside an
    instruction the decode misaligned on; the census also counts 32-bit `ldr.w Rt,[pc,#imm]`
    (`0xF8DF`/`0xF85F`), which the decode refuses wherever it sees one. **Assumption:** the
    cell is reached only by a pc-relative literal load of its own pool word (or `movw`/`movt`,
    refused). A base-plus-offset load through a neighbouring pool word (a pool word holding
    `cell - 8`, then `ldr r1,[r0,#8]`) is invisible to the census and the walk; none is known.

    While the cell is live, a conditional write of its register does not end the walk (the
    other arm still holds it), a return (`bx lr`, `pop {..., pc}`) ends it, and any other
    indirect transfer (`tbb`/`tbh`, `bx rX`, a write to `pc`) refuses: the paths past it are not
    followed.

    The cell's only other use allowed is the one store (entry0's copy). A `ldr.w` or
    `movw`/`movt` of the cell is counted and refused, since the sweep does not follow those.
    On 13.6: 76 loads, 105 derefs, 105 calls, 49 groups; 14.7 86/113/113, 47 groups; 11.23
    90 calls, 39 groups (2026-11-08)."""
    from . import followchannel, hooklocate, lcdgroups
    try:
        cell = hooklocate.host_api_cells(data)[0x04]
    except (followchannel.FollowChannelError, KeyError) as e:
        raise VariantSetterUnknown(
            f"the variant setter's cell does not locate on this image ({e}), so which groups "
            f"GuiCode drives by number is not known") from e
    try:
        writer = lcdgroups.host_writer(data)
    except lcdgroups.HostWriterUnknown as e:
        raise VariantSetterUnknown(
            f"the LCD does not prove the setter cell {cell:#x} holds the variant writer ({e}), "
            f"so which groups GuiCode drives by number is not known") from e
    return _setter_calls(data, cell, writer, SETTER_LOAD_WINDOW, SETTER_CALL_WINDOW,
                         SETTER_BRANCH_HOPS)


_LIT_RE = re.compile(r"(r\d+), \[pc, #(-?0x[0-9a-f]+|-?\d+)\]")
_DEREF_RE = re.compile(r"(r\d+), \[(r\d+)\]")
_IMM_RE = re.compile(r"(\w+), #(0x[0-9a-f]+|\d+)")
_CALLER_SAVED = frozenset({"r0", "r1", "r2", "r3", "r12", "ip"})
_COND_BRANCH = re.compile(r"b(eq|ne|cs|hs|cc|lo|mi|pl|vs|vc|hi|ls|ge|lt|gt|le)(\.w)?|cbn?z")


def _conditional(ins) -> bool:
    """Executed under a condition (an IT block, or a conditional branch)."""
    from capstone.arm import ARM_CC_AL, ARM_CC_INVALID
    return ins.cc not in (ARM_CC_AL, ARM_CC_INVALID)


def _transfers(ins) -> bool:
    """A jump or a call (capstone's groups), whatever its condition."""
    from capstone import CS_GRP_CALL, CS_GRP_JUMP
    return ins.group(CS_GRP_JUMP) or ins.group(CS_GRP_CALL)


def _both_arms(conds: set) -> bool:
    """`conds` holds a condition and its inverse (capstone pairs them as odd, odd + 1:
    EQ/NE, HS/LO, MI/PL, VS/VC, HI/LS, GE/LT, GT/LE)."""
    return any(c % 2 == 1 and c + 1 in conds for c in conds)


def _is_return(ins) -> bool:
    m, o = ins.mnemonic, ins.op_str
    return (m == "bx" and o == "lr") or (m in ("pop", "pop.w") and "pc" in o)


def _ends_flow(ins) -> bool:
    """An unconditional transfer: the walk cannot continue past it linearly."""
    m, o = ins.mnemonic, ins.op_str
    return (m in ("b", "b.w", "bx", "tbb", "tbh")
            or (m.startswith("pop") and "pc" in o)
            or (m.startswith(("ldr", "mov")) and o.startswith("pc,")))


def _reads_writes(ins) -> tuple:
    read, written = ins.regs_access()
    return ({ins.reg_name(r) for r in read}, {ins.reg_name(r) for r in written})


@memo.content_cached(copy=None)
def _setter_calls(data: bytes, cell: int, writer, load_window: int, call_window: int,
                  branch_hops: int) -> SetterScan:
    from . import theme
    gc = theme.guicode_section(data)
    img = data[gc.offset:gc.offset + gc.length]
    base = theme.GUICODE_BASE_DEFAULT
    ins = [i for _s, _e, run in theme.detail_runs(img, base) for i in run]
    index = {i.address: k for k, i in enumerate(ins)}

    def pool_at(addr):
        la = addr - base
        return struct.unpack_from("<I", img, la)[0] if 0 <= la <= len(img) - 4 else None

    def pool(i, disp):
        return pool_at(((i.address + 4) & ~3) + int(disp, 0))

    def resolve(q, fn):
        """From the deref at `ins[q]` loading `fn`: `(blx address, r0)` or a refusal."""
        consts: dict = {}
        j, steps, hops = q + 1, 0, 0
        while j < len(ins) and steps < call_window:
            i = ins[j]
            # The walk is linear: a conditional branch would leave r0 two values (`movs r0,#5;
            # cbz r1,L; movs r0,#6; L: blx r3`), and a conditional call or write of r0 or `fn`
            # (`it eq; blxeq r3`) is not the unconditional call it resolves. Refuse both.
            _r, w = _reads_writes(i)
            if _COND_BRANCH.fullmatch(i.mnemonic) or (_conditional(i) and (
                    _transfers(i) or w & {"r0", fn, "pc"})):
                raise VariantSetterUnknown(
                    f"{i.address:#x} is conditional ({i.mnemonic} {i.op_str}) between the "
                    f"setter deref at {ins[q].address:#x} and its call; the walk is linear")
            if i.mnemonic == "blx" and i.op_str == fn:
                if consts.get("r0") is None:
                    raise VariantSetterUnknown(
                        f"the setter call at {i.address:#x} (deref {ins[q].address:#x}): r0 is "
                        f"not a constant")
                return i.address, consts["r0"]
            if i.mnemonic in ("b", "b.w") and hops < branch_hops:
                j = index.get(int(i.op_str.lstrip("#"), 0), -1)
                hops += 1
                if j < 0:
                    break
                continue
            if _ends_flow(i) or i.mnemonic in ("bl", "blx"):
                break
            imm = _IMM_RE.fullmatch(i.op_str)
            src = i.op_str.split(", ")[1] if i.op_str.count(", ") == 1 else None
            for reg in w:
                consts[reg] = None
            if i.mnemonic in ("movs", "mov", "mov.w", "movw") and imm:
                consts[imm.group(1)] = int(imm.group(2), 0)
            elif i.mnemonic in ("movs", "mov") and src in consts and len(w) == 1:
                consts[next(iter(w))] = consts[src]
            if fn in w:
                break
            steps += 1
            j += 1
        raise VariantSetterUnknown(
            f"the setter deref at {ins[q].address:#x} reaches no blx {fn} within "
            f"{call_window} instructions")

    n_loads, derefs, stored, calls, movw = 0, set(), set(), [], {}
    for k, i in enumerate(ins):
        if i.mnemonic == "movw" and (imm := _IMM_RE.fullmatch(i.op_str)):
            movw[imm.group(1)] = int(imm.group(2), 0)
        elif i.mnemonic == "movt" and (imm := _IMM_RE.fullmatch(i.op_str)):
            lo = movw.get(imm.group(1))
            if lo is not None and (int(imm.group(2), 0) << 16 | lo) == cell:
                raise VariantSetterUnknown(
                    f"{i.address:#x} builds the setter cell {cell:#x} with movw/movt, a form the "
                    f"scan does not follow")
        lit = _LIT_RE.fullmatch(i.op_str) if i.mnemonic.startswith("ldr") else None
        if not lit or pool(i, lit.group(2)) != cell:
            continue
        if i.mnemonic != "ldr" or i.size != 2:
            raise VariantSetterUnknown(
                f"{i.address:#x} loads the setter cell {cell:#x} with {i.mnemonic}, a form the "
                f"scan does not follow")
        n_loads += 1
        r = lit.group(1)
        # Every path from the load while `r` holds the cell: the fall-through, every conditional
        # branch's target, and an unconditional `b` followed, each instruction visited once.
        work, seen = [k + 1], set()
        while work:
            q = work.pop()
            arms: set = set()                 # conditions r was written under, this IT block
            while q < len(ins) and q not in seen:
                if len(seen) >= load_window:
                    raise VariantSetterUnknown(
                        f"the setter cell loaded at {i.address:#x} is still live after "
                        f"{load_window} instructions; its uses are not all visited")
                seen.add(q)
                j = ins[q]
                reads, writes = _reads_writes(j)
                dm = _DEREF_RE.fullmatch(j.op_str)
                if r in reads:
                    if j.mnemonic in ("ldr", "ldr.w") and dm and dm.group(2) == r:
                        if j.address not in derefs:
                            derefs.add(j.address)
                            blx, group = resolve(q, dm.group(1))
                            calls.append(SetterCall(i.address, j.address, blx, group))
                    elif j.mnemonic in ("str", "str.w") and dm and dm.group(2) == r:
                        stored.add(j.address)
                    else:
                        raise VariantSetterUnknown(
                            f"{j.address:#x} uses the setter cell {cell:#x} other than by a "
                            f"deref: {j.mnemonic} {j.op_str}")
                # A conditional write leaves the cell live on the other arm: keep walking (it
                # checks more derefs, never fewer). An unconditional one ends this path, and so
                # do writes under both a condition and its inverse in one IT block (`ite ne;
                # movne rN; moveq rN`, 11.23 0x249CF0): every arm overwrote it.
                if j.mnemonic.startswith("it"):
                    arms = set()
                if r in writes and _conditional(j):
                    arms.add(j.cc)
                if (r in writes and not _conditional(j)) or _both_arms(arms) or \
                        (j.mnemonic in ("bl", "blx") and r in _CALLER_SAVED):
                    break
                cond = _COND_BRANCH.fullmatch(j.mnemonic)
                if cond or j.mnemonic in ("b", "b.w"):
                    tgt = index.get(int(j.op_str.split("#")[-1], 0))
                    if tgt is None:
                        raise VariantSetterUnknown(
                            f"{j.address:#x} branches while the setter cell is live to "
                            f"{j.op_str}, which the sweep did not decode")
                    if cond:
                        work.append(tgt)
                        q += 1
                    else:
                        q = tgt
                    continue
                if _is_return(j) and not _conditional(j):
                    break
                if _ends_flow(j) or (j.mnemonic not in ("bl", "blx") and "pc" in writes):
                    if not _is_return(j):
                        raise VariantSetterUnknown(
                            f"{j.address:#x} transfers control ({j.mnemonic} {j.op_str}) while "
                            f"the setter cell is live; the paths past it are not followed")
                q += 1
    if len(stored) != 1:
        raise VariantSetterUnknown(
            f"{len(stored)} stores to the setter cell {cell:#x} "
            f"({[hex(a) for a in sorted(stored)]}); entry0's copy should be the only one")
    # The census counts both literal-load encodings at every halfword: `ldr rN,[pc,#imm8*4]`
    # (`0x48xx`) and `ldr.w Rt,[pc,#±imm12]` (`0xF8DF`/`0xF85F`). The decode refuses any `ldr.w`
    # it sees, so a 32-bit one found here was hidden from it and fails the comparison.
    raw = 0
    for pc in range(0, len(img) - 1, 2):
        h = img[pc] | (img[pc + 1] << 8)
        lit = ((base + pc + 4) & ~3)
        if h >> 11 == 0b01001 and pool_at(lit + (h & 0xFF) * 4) == cell:
            raw += 1
        elif h in (0xF8DF, 0xF85F) and pc + 3 < len(img):
            imm = (img[pc + 2] | (img[pc + 3] << 8)) & 0xFFF
            if pool_at(lit + imm if h == 0xF8DF else lit - imm) == cell:
                raw += 1
    if raw != n_loads:
        raise VariantSetterUnknown(
            f"a raw halfword scan finds {raw} pc-relative loads of the setter cell {cell:#x} "
            f"and the decode {n_loads} `ldr rN,[pc,#]`; a load the decode does not see is not "
            f"walked")
    calls.sort(key=lambda c: c.deref)
    return SetterScan(cell, n_loads, raw, len(derefs), len(calls), tuple(calls), writer)


# The DGNs of the stock inverter/charger enable/disable templates (`1FFD3h`, `1FFC5h`): two-variant
# toggles (variant 1 enable, variant 0 disable, tap-release) that `docs/HARDWARE-VERIFIED.md`'s
# PD-inverter bridge brief names as the route that bridge will drive (controller ruling,
# 2026-09-26). Protocol constants, which is what the reservation means; the GROUPS that hold them
# are located (`reserved_groups`).
RESERVED_DGNS = (0x1FFD3, 0x1FFC5)


class ReservedGroupUnknown(ValueError):
    """RESERVED_GROUP_UNKNOWN: which group holds a stock inverter/charger template
    (`RESERVED_DGNS`) is not known on this image: no group's whole binding slice carries that
    DGN, or several do. An allocator refuses rather than hand out a template, or a group on a
    guess that another is the template."""
    code = "RESERVED_GROUP_UNKNOWN"


def binding_dgn(b: guidata.Binding) -> int:
    """A binding's 18-bit DGN: the u16 at +0 and byte 2 bits 0-1 (`binding_raw`)."""
    return b.dgn_low | ((b.raw[2] & 0x03) << 16)


@memo.content_cached(copy=None)
def _template_groups(data: bytes, dgns: tuple) -> tuple:
    """`((dgn, ((group, start, count), ...)), ...)`: for each DGN, every group whose binding
    slice is non-empty, inside the table, and all of that DGN."""
    gt = guidata.group_table(data)
    bt = guidata.binding_table(data)
    out = {d: [] for d in dgns}
    for g in gt:
        if not g.count or g.start + g.count > len(bt):
            continue
        found = {binding_dgn(b) for b in bt[g.start:g.start + g.count]}
        if len(found) == 1 and (d := next(iter(found))) in out:
            out[d].append((g.index, g.start, g.count))
    return tuple((d, tuple(out[d])) for d in dgns)


def template_groups(data: bytes) -> dict[int, tuple]:
    """`{dgn: (group, ...)}`: every group whose whole slice is that reserved DGN, zero, one or
    several per DGN. The finish gate (`recipe._assert_reserved_groups_intact`) protects all of
    them; an allocator needs exactly one each (`reserved_groups`)."""
    return {d: tuple(g for g, _s, _c in hits) for d, hits in _template_groups(data,
                                                                               RESERVED_DGNS)}


def reserved_groups(data: bytes) -> dict[int, int]:
    """`{group: dgn}`: the group holding each stock inverter/charger template (`RESERVED_DGNS`),
    located as the unique group whose binding slice is non-empty and wholly that DGN. Read from
    GuiData alone, so a built image used as stock locates its own (the templates stay put: the
    finish gate refuses a build that changes one).

    11.23, 13.6 and 14.7: `{68: 0x1FFD3, 69: 0x1FFC5}`, and those four bindings (two per
    group) are the only ones carrying either DGN (2026-11-08). Refuses `ReservedGroupUnknown`
    when a DGN has no such group or several. So a build that adds its own group whose slice is
    wholly `1FFD3` or `1FFC5` makes every later allocating op refuse RESERVED_GROUP_UNKNOWN on
    it, by design: which group is the stock template is then not known."""
    out = {}
    for dgn, hits in _template_groups(data, RESERVED_DGNS):
        if len(hits) != 1:
            raise ReservedGroupUnknown(
                f"{len(hits)} groups have a binding slice wholly DGN {dgn:#x} "
                f"({', '.join(f'group {g} [{s}, {s + c})' for g, s, c in hits) or 'none'}); "
                f"the stock template's group is not known")
        out[hits[0][0]] = dgn
    return out


def hold_safe_groups(data: bytes) -> list[int]:
    """Free group slots fit for a control whose HOLD must work: not a reserved template
    (`reserved_groups`), not driven by GuiCode (`guicode_selected_groups`), and with byte 1 bits
    6-7 clear.

    The GuiCode-driven exclusion is proven (the setter scan). The byte-1 rule is
    **inferred-from-data**, a conservative filter: every hold proven on a coach sits on a slot
    with those bits clear (Batt Heat 46; the wheelbase and track - on 96/126/127, 13.6), and the
    2026-09-30 failures (136/137, 13.6) carried `11 80` and were also GuiCode-driven, so the
    observations do not separate the two causes. 61/77-82 were `0x80` slots whose TAPS work;
    no hold on one has been seen. All of this is 13.6: no hold on 14.7 has been observed, so on
    14.7 the rule is carried over, not checked. 13.6: 26 slots; 14.7: 44 (2026-11-08). Refuses
    `ReservedGroupUnknown` and `VariantSetterUnknown` rather than offer a group either might
    be."""
    reserved = reserved_groups(data)
    selected = guicode_selected_groups(data)
    gt = guidata.group_table(data)
    return [g for g in free_group_slots(data)
            if g not in reserved and g not in selected and not gt[g].raw[1] & 0xC0]


@memo.content_cached
def assignable_groups(data: bytes) -> list[int]:
    """`free_group_slots` minus the groups GuiCode drives by number
    (`guicode_selected_groups`): the slots an allocator may hand out at all. The finish gate
    (`recipe._assert_no_guicode_selected_group_reused`) refuses any build that reuses a
    GuiCode-driven group, so a pool that still offers 13.6's 136-139 turns "the pool ran out"
    into an assertion at finish instead of a NO_FREE_GROUP from the op that ran out."""
    selected = guicode_selected_groups(data)
    return [g for g in free_group_slots(data) if g not in selected]


def free_group_slots(data: bytes) -> list[int]:
    """Group-table indices safe to repurpose for a **config-shaped** group, ascending.

    The same three exclusions as `unused_command_groups` — referenced by no object
    record tail, named by no located control, and the sole producer of no
    `(channel, command)` pair — **without** its fourth restriction to Water-Pump-shaped
    (`DGN_DC_DIMMER_COMMAND_2`, count 2-3) groups.

    Why a separate function rather than a flag: `unused_command_groups` allocates slots
    for *output command* groups, where the shape restriction is a genuine safety property
    (a group that transmits a channel command is one whose loss is a lost capability).
    A `0x1FED9` config group commands no channel, so the shape restriction only hides
    capacity — but relaxing it inside the existing function would silently widen the gate
    for every existing caller, including `build_home_button`. Callers of this function opt
    in explicitly.

    **Keep all three exclusions.** Criterion 3 is not a reachability guess: `build_home_button`
    once took group 3, the sole producer of `(3, 1)` = AWNING EXT, and every flashed Pin-15
    build silently lost awning-extend.

    **This allocator carries strictly less support than `unused_command_groups`**, and no
    evidence label of its own: the shape restriction it drops is the one that made the narrower
    function's output self-limiting, and nothing here has been flashed. Do not read the shared
    three exclusions as evidentiary parity with the sibling.
    """
    gt = guidata.group_table(data)
    referenced = _object_referenced_groups(data)
    control = {c.group for c in guidata.find_control_groups(data)}
    return [g for g in range(len(gt))
            if g not in referenced
            and g not in control
            and not sole_produced_pairs(data, g)]


def allocate_command_group(data: bytes):
    """Allocate a single-use command group for a cloned button.

    Returns ``(group_id, edits, route)`` where ``route in {"repurpose", "grow"}``:

    * **repurpose** (route taken on every corpus build): the lowest
      `unused_command_groups` id. It is referenced by no object and commanded by no
      located control, and its two/three bindings are Water-Pump-shaped
      (`DGN_DC_DIMMER_COMMAND_2`) -- ready for `set_channel` (Task 3) to point at the new
      instance. No edits are needed here; the clone simply carries this id in its
      `GROUP_REFERENCE_TAIL`. ``edits`` is ``[]``.

    * **grow** (only if no unused group exists -- never on this corpus): append two
      Water-Pump-shaped binding entries cloned from 174/175 and a group-table entry
      pointing at them. See the caveat below.
    """
    unused = unused_command_groups(data)
    if unused:
        return unused[0], [], "repurpose"
    return _grow_command_group(data)


def _grow_command_group(data: bytes):
    """Fallback allocation when no unused group exists.

    **Unreachable on the corpus** (all three builds expose dozens of unused
    Water-Pump-shaped groups; see `unused_command_groups`), and therefore delivered as a
    guarded, documented negative rather than as unverified byte-surgery.

    Growing a group means appending entries to the binding table *and* the group table.
    Both are index-addressed with no self-pointers, so their *entries* relocate cleanly --
    but each is a header-declared table, and `relocate.relocate` cannot grow one: its
    coverage gate (`relocate._deep_coverage_ok`) requires every GuiData byte to be claimed,
    and a declared table is claimed only up to its descriptor `size`. Inserting entries
    without also enlarging that `size` (which `relocate` deliberately does not do -- it
    preserves every table's entry count) leaves the new bytes unclaimed and the gate fails.
    A correct grow needs a descriptor-size-aware relocation on the pattern of
    `insert._append_object` (reuse relocate's `_apply_downstream_shift` helper, edit the two
    table descriptors' `size` fields, reseal, then run the four gates), not a bare
    `relocate.relocate` call. That path is deferred until a build without an unused group
    actually appears.
    """
    from .relocate import RelocationError
    raise RelocationError(
        "no unused command group to repurpose; the grow route (append binding + group "
        "entries) requires a descriptor-size-aware relocation -- relocate.relocate alone "
        "cannot enlarge a declared table (its coverage gate rejects the un-declared "
        "bytes). Unreachable on the current corpus; implement when a build without an "
        "unused group appears.")


# --------------------------------------------------------------------------------------
# clone a boxed button and attach it to a page root
# --------------------------------------------------------------------------------------

# The object is the proven template `donors.button_object`: three records, shape `[33, 11, 12]`
# -- a visible 84x50 box, a caption, and a touch region (MCF-FORMAT.md Sec.6.0a) -- whose
# documented source is 13.6 object 279 ("Fresh Tank Heater"), one of four byte-identical
# unattached tank-heater objects; on 13.6 the assembled bytes equal object 279's span, so a 13.6
# build is unchanged by construction (research notes 2026-11-09).
#
# The form is chosen for a structural reason: it carries a `value_slot` field on BOTH its box and
# its caption. That field's PRESENCE is gated by the record header, so a same-size edit cannot
# add one -- and without it the clone can never register for in-place redraw. The obvious donor,
# the HOME Water Pump (13.6 obj 295), has no value_slot on any of its records: it inherits one
# from its PARENT wtype-5 container record (value_slot 160 == its fillA index 160), and a clone
# attached by our own generated container does not inherit that. Cloning it therefore gives a
# button whose colour is right but which only repaints on re-entering the page (a probe on the
# coach, 2026-08-17).
#
# The template's own value_slots (13.6: 53 box, 59 caption) are type-1 CONFIG GATES ("is this
# tank heater fitted?"), not status cells, so `retarget_indicator` rewrites both to the pin's
# cell.


def clone_onto_home(data: bytes, position: tuple[int, int] | None = None, *,
                    page: str = "home") -> tuple[bytes, int, list["insert.Edit"]]:
    """Attach add_button's object (`donors.button_object`, the proven template) as a new child
    of the `page` root (default HOME), optionally at absolute `position=(x, y)`.

    Two calls into `insert.py`: `donors.button_object` gives the object's bytes (refusing
    `DonorError` GRAMMAR or NO_TEMPLATE where the image does not prove the template), and
    `insert_object` appends it as a new object at the top object-table index (so no existing
    `child_index` shifts) and wires it in as a child of the page root
    (`cli._resolve_page_root(data, page)`). When `position` is given, `insert_object` uses a
    geometry-bearing wtype-5 attach template and rewrites its x/y so the framed clone lands at
    that origin (the clone's own geometry sets the box *size*, not its position).

    Returns `(new_data, new_obj_index, edits)`.
    """
    clone, _proof = donors.button_object(data)
    root = _resolve_page_root(data, page)
    new_data, edits = insert.insert_object(data, clone, root, position=position)
    new_idx = len(guidata.object_tables(new_data)[1]) - 1
    return new_data, new_idx, edits


class PlacementOverlap(ValueError):
    """PLACEMENT_OVERLAP: the new button's attach origin is not a u16, its footprint leaves the
    page's canvas (the page root's background), or, in the base arm of every switch, it covers a
    placed record other than a root background fill around it and the one wt-33 card that
    encloses it."""
    code = "PLACEMENT_OVERLAP"


@dataclass(frozen=True)
class Placement:
    box: tuple          # (x0, y0, x1, y1): the new object's placed records' bounding box
    canvas: tuple       # the page root's background (its first placed wt-4 record)
    card: int | None    # the object of the one wt-33 card that encloses `box`, if any
    # Report only: the switch-arm paths (other than the base) under which the footprint covers
    # a record the base arm does not admit (`check_placement`). Each entry is
    # `{"arm": {"<switch child_base>": value, ...}, "objects": [object, ...],
    #   "path": [{"child_base": int, "slots": [value_slot, ...], "value": int}, ...],
    #   "covers": [{"object": int, "caption": str | None, "touch": bool}, ...]}`,
    # `arm` and `path` outermost switch first. A configuration sets the `slots`, not the
    # child_base. Which arm a coach draws is its configuration, which a compile does not see, so
    # these do not refuse.
    arms: tuple = ()


def _bbox(quads) -> tuple:
    return (min(min(q.x0, q.x1) for q in quads), min(min(q.y0, q.y1) for q in quads),
            max(max(q.x0, q.x1) for q in quads), max(max(q.y0, q.y1) for q in quads))


def _encloses(outer: tuple, inner: tuple) -> bool:
    return (outer[0] <= inner[0] and outer[1] <= inner[1]
            and inner[2] <= outer[2] and inner[3] <= outer[3])


def _touches(a: tuple, b: tuple) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def check_origin(x: int, y: int) -> None:
    """PlacementOverlap unless `(x, y)` fits the attach record's u16 geometry fields."""
    for name, v in (("x", x), ("y", y)):
        if not 0 <= v <= 0xFFFF:
            raise PlacementOverlap(f"attach origin {name}={v} is outside 0..0xFFFF")


def _caption(recs) -> str | None:
    """An object's caption for a report: its first wt-11/13 label of more than one character,
    lines joined by a space. A one-character label is taken for an icon (an icon is a
    character), which is inference: a one-letter caption is not named."""
    for r in recs:
        if r.wtype in (11, 13) and r.label:
            text = " ".join(records.label_text(r.label).split())
            if len(text) > 1:
                return text
    return None


def _covered(data: bytes, obj: int) -> dict:
    """`{"object", "caption", "touch"}` for a covered object, from its OWN records: `touch` is
    whether it carries a wt-12 touch region."""
    gs, ge = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    try:
        recs = records.object_records(data, gs + table[obj])
    except (IndexError, ValueError):
        recs = []
    return {"object": obj, "caption": _caption(recs),
            "touch": any(r.wtype == 12 for r in recs)}


def check_placement(data: bytes, root: int, obj: int) -> Placement:
    """The footprint gate over every switch-arm path of the page (`pagewalk.switch_arm_paths`:
    the base, each switch's arms one at a time, and recursively the arms of every switch nested
    inside a non-base arm, combined with the arm path that reaches it), each walked by
    `layout.Screen.walk`, the layout engine's replay.

    In the base arm (`{}`) it refuses `PlacementOverlap` unless object `obj`'s placed records lie
    inside the page root's background (its first placed wt-4 record: the canvas) and touch no
    placed record but root wt-4 fills that enclose them (the background, a panel) and one wt-33
    record of another object that encloses them (the card the button sits on). The root's other
    records (its header, its own touch regions and rows) are not admitted. Boxes are compared
    inclusively, so an edge-to-edge neighbour refuses too.

    Under each other arm path, records the base rule would not admit are reported in
    `Placement.arms`, not refused: which arm a coach draws depends on its configuration, which
    the build does not see. Each entry names the value slots the path sets, and each covered
    object's caption and whether it carries a touch region. A nested path that covers exactly
    the objects its parent path covers is not repeated. Only the all-base walk refuses."""
    paths = pagewalk.switch_arm_paths(data, [root])
    screen = layout.Screen(data)

    def walk(sv):
        return list(screen.walk(root, switch_values=sv))

    def foreign(placed, box):
        """`(records box touches that are not admitted, the admitted card's object or None)`:
        root wt-4 fills around `box` are admitted, and so is one wt-33 record of another object
        around it (two such cards are both foreign)."""
        hits = []
        for q in placed:
            b = _bbox([q])
            if q.obj_index == obj or not _touches(b, box):
                continue
            if not (q.obj_index == root and q.wtype == 4 and _encloses(b, box)):
                hits.append((q, b))
        cards = [h for h in hits if h[0].obj_index != root and h[0].wtype == 33
                 and _encloses(h[1], box)]
        if len(cards) == 1:
            hits.remove(cards[0])
            return hits, cards[0][0].obj_index
        return hits, None

    base = walk({})
    mine = [q for q in base if q.obj_index == obj]
    if not mine:
        raise PlacementOverlap(f"object {obj} places nothing under page root {root}")
    bg = next((q for q in base if q.obj_index == root and q.wtype == 4), None)
    if bg is None:
        raise PlacementOverlap(f"page root {root} places no wt-4 background, so its canvas is "
                               f"not known")
    box, canvas = _bbox(mine), _bbox([bg])
    if not _encloses(canvas, box):
        raise PlacementOverlap(f"object {obj}'s footprint {box} leaves the page's canvas "
                               f"{canvas}")
    hits, card = foreign(base, box)
    if hits:
        raise PlacementOverlap(
            f"object {obj}'s footprint {box} covers "
            + ", ".join(f"object {q.obj_index} wt {q.wtype} {b}" for q, b in hits))

    arms = []
    covered: dict = {frozenset(): []}
    for sv in paths.sets:
        if not sv:
            continue
        objs = sorted({q.obj_index for q, _b in foreign(walk(sv), box)[0]})
        covered[frozenset(sv.items())] = objs
        parent = frozenset(list(sv.items())[:-1])
        if not objs or (len(sv) > 1 and objs == covered[parent]):
            continue
        arms.append({"arm": {str(k): v for k, v in sv.items()}, "objects": objs,
                     "path": [{"child_base": k, "slots": list(paths.slots.get(k, ())),
                               "value": v} for k, v in sv.items()],
                     "covers": [_covered(data, o) for o in objs]})
    return Placement(box, canvas, card, tuple(arms))


def placement_text(entry: dict) -> str:
    """One `Placement.arms` entry as the manifest prints it, e.g.
    `slot 102 = 1: object 240 "Star Link" (touch)`."""
    def step(s):
        slots = s["slots"]
        if not slots:
            return "switch %d = %d" % (s["child_base"], s["value"])
        return "%s %s = %d" % ("slot" if len(slots) == 1 else "slots",
                               " and ".join(map(str, slots)), s["value"])

    def obj(c):
        return "object %d%s%s" % (c["object"],
                                  ' "%s"' % c["caption"] if c["caption"] else "",
                                  " (touch)" if c["touch"] else "")
    return "%s: %s" % (" and ".join(step(s) for s in entry["path"]),
                       ", ".join(obj(c) for c in entry["covers"]))


# --------------------------------------------------------------------------------------
# retarget the clone's command group to its own RV-C instance
# --------------------------------------------------------------------------------------

# A 13.6 test default for direct callers (the add_button op always passes its channel): RV-C
# instance == G12 pin 15, unbound on 13.6 (no existing binding commands it, per
# BUS-BASELINE/answer-key), latch, and its indicator colour cell is indexed -- chosen 2026-08-16
# after a dig found the earlier candidate (27) is a motor channel with a command conflict.
TARGET_CHANNEL = 15

# The single-press toggle: ONE DC_DIMMER_COMMAND_2 binding carrying command 5 (toggle). The
# TRANSMITTED command is binding byte [4] (byte [3]=0x0a is metadata, not the wire command --
# established by CAN capture 2026-08-17). Every tap sends command 5 and the G12 alternates the
# output's state -- the proven light-button / Bath-Fan-remap pattern (CLAUDE.md remap section:
# "the panel only ever sends command 5; the G12 holds and alternates state").
#
# Why not the Water Pump's own TWO-binding group [toggle(5), off(3)]: that group is a *smart
# toggle* -- the panel picks off-vs-toggle from the commanded instance's tracked on/off state.
# On the coach the clone commanded instance 15, which nothing drives, so the panel had no status
# for it, defaulted to "believed on", and sent OFF (command 3) on every tap (CAN capture,
# 2026-08-17: only ever `0FFF0003FF00FFFF`). A SINGLE-binding group removes the choice: there is
# no off binding to select, so command 5 fires unconditionally -- exactly how the 15 single-
# command-5 light groups on this build toggle their (latching) outputs on/off from the panel.
#
# The bytes are light group 78's binding verbatim (`dbfe010a05c8ff0a...`, channel 10) with the
# instance byte (BINDING_CHANNEL_FIELD = 7) zeroed as a placeholder for TARGET_CHANNEL -- a
# proven-working single-command-5 entry, retargeted, the same discipline as the remap.
_TOGGLE_ONLY = bytes.fromhex("dbfe010a05c8ff00ffff00ff")      # byte[4]=05 command 5 (toggle), level 0xC8


# A binding stores its 8-byte wire payload as TWO LITTLE-ENDIAN u32 WORDS, so the payload the
# panel puts on the wire is `raw[7], raw[6], raw[5], raw[4], raw[11], raw[10], raw[9], raw[8]`.
# `proven-from-bus` 2026-08-22, and PREDICTIVE 2026-08-23: three stock bindings reproduce
# captured `19FEDB9F#` frames byte-for-byte under this rule --
#     binding  52  db fe 01 0a 03 00 ff 08 ff ff 00 ff  ->  08FF0003FF00FFFF
#     binding 119  db fe 01 0a 03 00 ff 1a ff ff 00 ff  ->  1AFF0003FF00FFFF
#     binding 167  db fe 01 0a 03 00 ff 28 ff ff 00 ff  ->  28FF0003FF00FFFF
# -- which is why `BINDING_CHANNEL_FIELD` is byte 7 (payload[0], the RV-C instance) and the
# "wire command" is byte 4 (payload[3]). The order is plain byte order, NOT a per-DGN layout,
# so a binding can carry a DGN the firmware has no special knowledge of.
#
# Confirmed on hardware 2026-08-23 in the stronger direction: the transmit probe used this rule
# to PREDICT a frame that had never been sent, and the coach put exactly it on the wire --
# `19FEA09F#1DFFC805FF00FFFF`, CAN id included. So the runtime also supplies priority 6 for a
# DGN it has no knowledge of, which the binding itself does not encode.
_PAYLOAD_ORDER = (7, 6, 5, 4, 11, 10, 9, 8)


def binding_payload(raw: bytes) -> bytes:
    """The 8 wire payload bytes a 12-byte binding entry transmits."""
    return bytes(raw[i] for i in _PAYLOAD_ORDER)


def binding_raw(dgn: int, wire_payload: bytes, *, gesture_mask: int,
                variant: int = 0, repeat: bool = False) -> bytes:
    """Compose the 12 stored bytes of a binding that transmits `dgn` (an 18-bit RV-C DGN)
    with the eight given WIRE data bytes on the gesture phase(s) named by `gesture_mask`.

    This is the inverse of `binding_payload`: the eight wire bytes are stored as two
    little-endian u32 words (the `_PAYLOAD_ORDER` reversal), so `binding_payload(binding_raw
    (d, p, ...)) == p` and the entry decodes back to `d`. Priority (always 6) and the source
    address (0x9F) are supplied by the firmware at transmit and are NOT part of the entry --
    see `binding_wire_frame` and the `_PAYLOAD_ORDER` comment above.

    `gesture_mask` selects the phase(s) the frame fires on: `1` press, `2` short-tap release,
    `4` while held, `8` release after a hold (`CONTROL-BEHAVIOR.md` §3). `repeat` sets byte 2
    bit 2 so a held frame re-sends on the repeat ticks; left clear a `4` frame fires once when
    the hold threshold is crossed. `variant` (0-15) is matched against the runtime variant
    counter; single-variant groups use 0.
    """
    if not 0 <= dgn <= 0x3FFFF:
        raise ValueError(f"DGN {dgn:#x} is not an 18-bit value")
    if len(wire_payload) != 8:
        raise ValueError(f"wire_payload must be 8 bytes, got {len(wire_payload)}")
    if not 0 <= gesture_mask <= 0xFF:
        raise ValueError(f"gesture_mask {gesture_mask:#x} is not a byte")
    if not 0 <= variant <= 0xF:
        raise ValueError(f"variant {variant} is not a 4-bit value")
    raw = bytearray(12)
    raw[0] = dgn & 0xFF
    raw[1] = (dgn >> 8) & 0xFF
    # byte 2: bits 0-1 = DGN bits 16-17, bit 2 = re-send-on-repeat, bits 4-7 = variant.
    raw[2] = ((dgn >> 16) & 0x03) | (0x04 if repeat else 0x00) | ((variant & 0xF) << 4)
    raw[3] = gesture_mask & 0xFF
    for i, pos in enumerate(_PAYLOAD_ORDER):
        raw[pos] = wire_payload[i]
    return bytes(raw)


def binding_wire_frame(raw: bytes, *, source_address: int = 0x9F,
                       priority: int = 6) -> tuple[int, bytes]:
    """The exact 29-bit CAN id and 8 data bytes a 12-byte binding entry puts on the wire,
    reconstructed the way the firmware assembles them (`FUN_0021B8B4` / `FUN_0021825C`,
    decompiled 2026-09-21): the 18-bit DGN is bit-field-inserted at id bit 8 (so a PDU1 DGN's
    low byte lands in the PS / destination-address position), the priority is bit-field-
    inserted at bit 26 -- hard-wired to 6 in the firmware, not carried by the entry -- and the
    source-address byte is stamped by the firmware (0x9F for this panel).

    `proven-from-code` and corroborated by a hardware oracle: the stock binding
    `db fe 01 02 00 fb ff 16 ff ff 00 ff` reconstructs to `0x19FEDB9F` / `16 ff fb 00 ff 00 ff
    ff`, the exact frame in `captures/candump-2026-08-12_121941.log`. The `source_address` and
    `priority` arguments exist only to make those firmware-forced values explicit and testable;
    the real panel always transmits priority 6 from SA 0x9F.
    """
    if len(raw) != 12:
        raise ValueError(f"a binding entry is 12 bytes, got {len(raw)}")
    dgn = raw[0] | (raw[1] << 8) | ((raw[2] & 0x03) << 16)
    can_id = ((priority & 0x7) << 26) | (dgn << 8) | (source_address & 0xFF)
    return can_id, binding_payload(raw)


def set_binding_dgn(data: bytes, binding_index: int, dgn: int, *,
                    allow_capability_change: bool = False) -> tuple[bytes, list]:
    """Point one binding entry at a different DGN -- a 2-byte same-size edit of `+0..+2`.

    The payload bytes are left exactly as they are: what they *mean* is the new DGN's business,
    and for a DGN nothing on the bus consumes there is nothing to mean. Callers wanting a
    specific payload should write it separately.

    Moving a binding off `DC_DIMMER_COMMAND_2` **removes a channel capability** -- the panel can
    no longer command that pin through this entry -- so it needs `allow_capability_change`, the
    same gate `patch.set_channel` puts on the equivalent move. That is not ceremony: two reference
    builds shipped having silently lost a real control this way.
    """
    if not 0 <= dgn <= 0x3FFFF:
        raise ValueError(f"DGN {dgn:#x} is not an 18-bit value")
    entries = guidata.binding_table(data)
    if not 0 <= binding_index < len(entries):
        raise ValueError(f"binding {binding_index} out of range [0, {len(entries)})")
    entry = entries[binding_index]
    old_dgn = struct.unpack_from("<I", entry.raw, 0)[0] & 0x3FFFF
    if old_dgn == dgn:
        raise ValueError(f"binding {binding_index} already carries DGN {dgn:#07x}")
    if entry.channel is not None and not allow_capability_change:
        raise ValueError(
            f"binding {binding_index} commands channel {entry.channel} on "
            f"DC_DIMMER_COMMAND_2; moving it to {dgn:#07x} removes that capability. "
            f"Pass allow_capability_change=True if that is the intent")

    gs, _ = guidata.guidata_extent(data)
    off = gs + entry.offset
    out = bytearray(data)
    out[off] = dgn & 0xFF
    out[off + 1] = (dgn >> 8) & 0xFF
    # byte 2's bit 0 is DGN bit 16; the rest of the byte is the variant selector, kept as-is.
    out[off + 2] = (entry.raw[2] & 0xFE) | ((dgn >> 16) & 1)

    edits = [relocate.Edit(off, entry.raw[:3], bytes(out[off:off + 3]), "binding-dgn",
                           f"binding {binding_index}: DGN {old_dgn:#07x} -> {dgn:#07x} "
                           f"(payload unchanged, wire {binding_payload(entry.raw).hex()})")]
    checksum_positions = {f.label: f.position for f in patch.checksum_fields(bytes(out))}
    resealed, changed_labels = patch.reseal(bytes(out))
    expected = [firmware.guidata_checksum_label(data)]
    if changed_labels != expected:
        raise ValueError(f"reseal changed {changed_labels}, expected exactly {expected}")
    for label in changed_labels:
        pos = checksum_positions[label]
        edits.append(relocate.Edit(pos, bytes(out)[pos:pos + 2], resealed[pos:pos + 2],
                                   "checksum", f"resealed {label}"))
    return resealed, edits


class NewButtonError(ValueError):
    """A refused binding edit. `code` is the recipe error code the op reports."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def set_binding_bytes(data: bytes, group: int, *, dgn: int, bindings: "str | list[int]",
                      changes: dict[int, tuple[int, int]]) -> tuple[bytes, list]:
    """Rewrite chosen WIRE payload bytes of existing bindings in `group` -- a same-size edit.

    `changes` maps wire byte position (0-7, the order `binding_wire_frame` and candump show)
    to `(from, to)`. The position is translated through `_PAYLOAD_ORDER`, the same word order
    `binding_payload` / `binding_raw` use, never a raw offset. `bindings` is `"all"` or the
    positions within the group's run of bindings. Every edited binding must carry `dgn` and
    every `from` byte must match its current value, so a project written against one build
    fails loudly on another. Bytes 0-3 of the entry (DGN, variant, gesture mask) are untouched.

    Returns `(sealed, edits)`. Raises `NewButtonError` with code `NO_SUCH_GROUP`, `BAD_PARAMS`
    or `EXPECT_MISMATCH`.
    """
    groups = guidata.group_table(data)
    table = guidata.binding_table(data)
    if not 0 <= group < len(groups):
        raise NewButtonError("NO_SUCH_GROUP", f"group {group} out of range [0, {len(groups)})")
    if not changes:
        raise NewButtonError("BAD_PARAMS", "no payload bytes to change")
    for pos, (frm, to) in changes.items():
        if not 0 <= pos <= 7:
            raise NewButtonError("BAD_PARAMS", f"payload byte {pos} outside 0..7")
        if not (0 <= frm <= 0xFF and 0 <= to <= 0xFF):
            raise NewButtonError("BAD_PARAMS", f"byte {pos}: from/to must be 0..255")
    members = guidata.group_bindings(groups, table, group)
    if bindings == "all":
        chosen = list(range(len(members)))
    elif isinstance(bindings, list) and all(isinstance(i, int) for i in bindings):
        bad = [i for i in bindings if not 0 <= i < len(members)]
        if bad or not bindings:
            raise NewButtonError(
                "BAD_PARAMS", f"binding position(s) {bad or bindings} outside group {group}'s "
                f"{len(members)} binding(s)")
        chosen = sorted(set(bindings))
    else:
        raise NewButtonError("BAD_PARAMS", 'bindings must be "all" or a list of positions')
    if not chosen:
        raise NewButtonError("BAD_PARAMS", f"group {group} has no bindings")

    gs, _ = guidata.guidata_extent(data)
    out = bytearray(data)
    edits = []
    for i in chosen:
        entry = members[i]
        raw = entry.raw
        have = raw[0] | (raw[1] << 8) | ((raw[2] & 0x03) << 16)
        if have != dgn:
            raise NewButtonError(
                "EXPECT_MISMATCH", f"binding {entry.index} (group {group} position {i}) carries "
                f"DGN {have:#07x}, not {dgn:#07x}; payload {binding_payload(raw).hex()}")
        new = bytearray(raw)
        for pos, (frm, to) in changes.items():
            cur = raw[_PAYLOAD_ORDER[pos]]
            if cur != frm:
                raise NewButtonError(
                    "EXPECT_MISMATCH", f"binding {entry.index} payload byte {pos} is "
                    f"{cur:#04x}, not {frm:#04x}; payload {binding_payload(raw).hex()}")
            new[_PAYLOAD_ORDER[pos]] = to
        off = gs + entry.offset
        out[off:off + 12] = new
        edits.append(relocate.Edit(
            off, bytes(raw), bytes(new), "binding-bytes",
            f"binding {entry.index} (group {group}): wire {binding_payload(raw).hex()} -> "
            f"{binding_payload(bytes(new)).hex()}"))
    if len(out) != len(data):
        raise NewButtonError("BAD_PARAMS", "set_binding_bytes must be same-size")

    checksum_positions = {f.label: f.position for f in patch.checksum_fields(bytes(out))}
    resealed, changed_labels = patch.reseal(bytes(out))
    expected = [firmware.guidata_checksum_label(data)]
    if changed_labels != expected:
        raise ValueError(f"reseal changed {changed_labels}, expected exactly {expected}")
    for label in changed_labels:
        pos = checksum_positions[label]
        edits.append(relocate.Edit(pos, bytes(out)[pos:pos + 2], resealed[pos:pos + 2],
                                   "checksum", f"resealed {label}"))
    after = guidata.binding_table(resealed)
    for i in chosen:
        wire = binding_payload(after[members[i].index].raw)
        for pos, (_frm, to) in changes.items():
            if wire[pos] != to:
                raise NewButtonError("BAD_PARAMS", f"binding {members[i].index} byte {pos} "
                                     f"reads {wire[pos]:#04x} after the edit, not {to:#04x}")
    return resealed, edits


def object_group(data: bytes, obj_index: int) -> int:
    """The command-group id object `obj_index` references, read the same way
    `_object_referenced_groups`/`guidata.find_control_groups` do: the trailing
    `GROUP_REFERENCE_TAIL` u16 of its `IMAGE_OPCODE` (wtype 12, opcode 0x0C) record.

    Unlike `find_control_groups`, this does not go through `guidata.iter_screens`'s
    positional scan -- it decodes the object directly from the object-pointer table
    (`records.object_records`), so it sees a standalone object attached only by
    child-pointer (a relocator-inserted clone), which `iter_screens` does not walk.
    Raises `ValueError` if the object carries no `IMAGE_OPCODE` record.
    """
    gs, _ = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[insert.OBJECT_TABLE_INDEX]
    if not 0 <= obj_index < len(table):
        raise ValueError(f"object index {obj_index} out of range [0, {len(table)})")
    for rec in records.object_records(data, gs + table[obj_index]):
        if rec.wtype == guidata.IMAGE_OPCODE:
            return struct.unpack_from(
                "<H", data, rec.end - guidata.GROUP_REFERENCE_TAIL)[0]
    raise ValueError(f"object {obj_index} has no IMAGE_OPCODE (wtype 12) record")


def retarget_command(data: bytes, obj_index: int, *, channel: int | None = None,
                     allow_commanded_channel: bool = False) -> tuple[bytes, list]:
    """Give the cloned button (`obj_index`, from `clone_onto_home`) its own command
    group, commanding an RV-C instance (`channel`, default `TARGET_CHANNEL`) with a clean
    toggle.

    **Why not `patch.set_channel`:** that primitive's I6 check requires the group's one
    sharer to be locatable via `guidata.find_control_groups`, which walks screens
    positionally (`guidata.iter_screens`) -- a standalone object attached only by
    child-pointer (the clone) is invisible to it, so I6 would always fail. This function
    performs the same *kind* of edit (repoint a group's bindings' channel byte) but reads
    the clone's group through `object_group` above instead, and re-derives the safety
    checks `set_channel` would have made inline:

    1. `allocate_command_group` hands back the lowest `unused_command_groups` id (route
       "repurpose", no edits of its own) -- referenced by no object and commanded by no
       located control, so retargeting it disturbs nothing already on screen.
    2. The allocated group must have room for the single toggle binding (`count >= 1`) --
       always true for the Water-Pump-shaped (count 2-3) candidates `unused_command_groups`
       returns.
    3. `TARGET_CHANNEL` must be commanded by no existing binding (the I7 check
       `set_channel` would have made) -- this is a deliberate move onto a pin the file has
       never commanded, same as `set_channel(..., allow_capability_change=True)`.
    4. Every binding in the allocated group must be `DGN_DC_DIMMER_COMMAND_2` (the I5
       check) -- `unused_command_groups` already restricts its candidates to exactly this
       shape, so this re-asserts rather than newly discovers it.

    Three same-size edits follow: the clone's `GROUP_REFERENCE_TAIL` is pointed at the
    allocated group, the group's FIRST binding entry is overwritten with the single
    command-5 toggle template (`_TOGGLE_ONLY`) on `TARGET_CHANNEL`, and the group's binding
    count is set to 1 so the panel sees only that one binding (and always sends command 5;
    see `_TOGGLE_ONLY` for why single-binding, not the Water Pump's two-binding smart
    toggle). `patch.reseal` then fixes the one checksum a same-size payload edit always
    invalidates.

    `channel` selects the RV-C instance to command, defaulting to `TARGET_CHANNEL` (15).

    `allow_commanded_channel` opts out of check 3 above. Use it only when the bindings that
    already name the channel belong to controls that cannot be reached on the target coach --
    e.g. pin 29 on 13.6 is named by the slide-out groups 29/30/95 (paired with 30/83/84,
    act-while-held commands 0x21/0x22), which a B-van floorplan never shows. Note that the
    object scan behind "reachable" (`_object_referenced_groups`) locates only ~21 of the ~230
    groups, so its silence is weak evidence: passing this flag is a judgement about the coach,
    not a proof, and it must be recorded by the caller.

    Returns `(new_data, edits)`.
    """
    channel = TARGET_CHANNEL if channel is None else channel
    if not 0 <= channel <= 0xFF:
        raise ValueError(f"channel {channel} does not fit the binding's u8 instance field")
    gid, _alloc_edits, _route = allocate_command_group(data)

    gt = guidata.group_table(data)
    bt = guidata.binding_table(data)
    if not 0 <= gid < len(gt):
        raise ValueError(f"allocated group {gid} is outside the {len(gt)}-entry group table")
    group = gt[gid]
    entries = [bt[j] for j in range(group.start, group.start + group.count)]
    if not entries:
        raise ValueError(f"allocated group {gid} selects no binding entries")

    # A single-press G12-output toggle is a ONE-binding group carrying command 5: we overwrite the
    # group's first binding with `_TOGGLE_ONLY` (command 5) and shorten the group to 1. Every tap
    # then sends command 5 and the G12 alternates the output -- the proven light-button pattern,
    # with no state-dependent off-vs-toggle selection (see `_TOGGLE_ONLY`). Needs a group with at
    # least one binding.
    if group.count < 1:
        raise ValueError(
            f"allocated group {gid} has {group.count} binding(s); the toggle template needs 1")

    # I7-equivalent: the channel must not already be commanded.
    commanding = [b.index for b in bt if b.channel == channel]
    if commanding and not allow_commanded_channel:
        raise ValueError(
            f"channel {channel} is already commanded by binding(s) {commanding}; "
            f"retargeting onto it would drive one pin from two controls. Pass "
            f"allow_commanded_channel=True if those bindings belong to controls this coach "
            f"cannot reach, and say so in the build script")

    # I5-equivalent: every entry in the allocated group is the decoded DGN.
    foreign = {e.dgn_low for e in entries if e.dgn_low != guidata.DGN_DC_DIMMER_COMMAND_2}
    if foreign:
        listed = ", ".join(hex(dgn) for dgn in sorted(foreign))
        raise ValueError(
            f"allocated group {gid} contains non-DC_DIMMER_COMMAND_2 entries ({listed})")

    gs, _ = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[insert.OBJECT_TABLE_INDEX]
    if not 0 <= obj_index < len(table):
        raise ValueError(f"object index {obj_index} out of range [0, {len(table)})")
    group_rec = None
    for rec in records.object_records(data, gs + table[obj_index]):
        if rec.wtype == guidata.IMAGE_OPCODE:
            group_rec = rec
            break
    if group_rec is None:
        raise ValueError(f"object {obj_index} has no IMAGE_OPCODE (wtype 12) record")

    out = bytearray(data)
    edits: list[relocate.Edit] = []

    # Point the clone's group reference at the allocated group.
    tail_off = group_rec.end - guidata.GROUP_REFERENCE_TAIL
    old_tail = data[tail_off:tail_off + 2]
    struct.pack_into("<H", out, tail_off, gid)
    edits.append(relocate.Edit(
        tail_off, old_tail, out[tail_off:tail_off + 2], "group-reference-tail",
        f"object {obj_index} IMAGE_OPCODE group tail {struct.unpack('<H', old_tail)[0]} "
        f"-> {gid}"))

    # Overwrite the group's first binding with the single command-5 toggle template on channel =
    # TARGET_CHANNEL, and shorten the group to that one binding. The G12 alternates the output on
    # each command 5 -- a state-independent toggle (see `_TOGGLE_ONLY`). The earlier two-binding
    # smart toggle sent only OFF on the coach because instance 15 has no tracked status; dropping
    # to a single command-5 binding removes that dependence.
    entry = entries[0]
    payload = _TOGGLE_ONLY[:guidata.BINDING_CHANNEL_FIELD] + bytes([channel]) \
        + _TOGGLE_ONLY[guidata.BINDING_CHANNEL_FIELD + 1:]
    rec_off = gs + entry.offset
    old = data[rec_off:rec_off + guidata.BINDING_ENTRY_SIZE]
    out[rec_off:rec_off + guidata.BINDING_ENTRY_SIZE] = payload
    edits.append(relocate.Edit(
        rec_off, old, payload, "binding-toggle-template",
        f"binding {entry.index} -> toggle (command 5) on channel {channel} (group {gid})"))
    count_off = gs + group.offset + guidata.GROUP_COUNT_FIELD
    if data[count_off] != 1:
        old = data[count_off:count_off + 1]
        out[count_off] = 1
        edits.append(relocate.Edit(
            count_off, old, bytes([1]), "group-count",
            f"group {gid} binding count {old[0]} -> 1 (single command-5 toggle)"))

    checksum_positions = {f.label: f.position for f in patch.checksum_fields(bytes(out))}
    resealed, changed_labels = patch.reseal(bytes(out))
    expected = [firmware.guidata_checksum_label(data)]
    if changed_labels != expected:
        raise ValueError(
            f"reseal changed {changed_labels}, expected exactly {expected}")
    for label in changed_labels:
        pos = checksum_positions[label]
        before = bytes(out)[pos:pos + 2]
        after = resealed[pos:pos + 2]
        edits.append(relocate.Edit(pos, before, after, "checksum", f"resealed {label}"))

    edits.sort(key=lambda e: e.file_offset)
    return resealed, edits


# --------------------------------------------------------------------------------------
# retarget the clone's on/off indicator to instance 15's colour cell (Task 4)
# --------------------------------------------------------------------------------------

# A 13.6 test default for direct callers (the add_button op always passes the cell
# `statusbind.bind_pin` returns): instance 15's indexed colour cell on 13.6. The clone inherits
# the template's own indicator cell -- 145 is the cell already indexed for instance 15, chosen
# 2026-08-16 alongside TARGET_CHANNEL (see that constant's note).
INDICATOR_COLOUR_INDEX = 145

# The value_slot written to BOTH the box (wtype 33) and the caption (wtype 11).
#
# It is deliberately `INDICATOR_COLOUR_INDEX`, not an independent number. The mode-7 colour
# table and the value-slot variable table are the SAME table (GUICODE.md Finding 18), so a
# "colour index N" and a "value_slot N" name one cell -- and every live button in 13.6 has its
# box fillA index, its box value_slot and its caption value_slot all equal to that one cell:
# objs 194(114), 222(136), 255(146), 277(154), 291(159), 814(122/146/136), 871(156),
# 883(146/136/154), 989(455); all type 10. See MCF-FORMAT.md Sec.6.0a.
#
# The six records that break the rule are unattached TEMPLATE objects (247, 278-281, 1038) whose
# value_slot is a type-1/0 config gate ("is this tank heater fitted?"), not a status cell. The
# donor is one of those, so both of its value_slot fields must be retargeted here.
#
# Hardware history (all on the coach, three probes): the first left the box with no value_slot at
# all -> colour was
# right but only repainted on re-entering the page. The second set the value_slots to 32 while fillA
# stayed 145 -- two DIFFERENT live cells -- so the box repainted in place but the caption
# vanished until a full page render. The third, with all three equal, redraws box AND caption in
# place
# (verified 2026-08-17).
# Kept as a name for the default: the status slot and the colour index are one cell
# (Finding 18 -- the colour table IS the value-slot table), which is the invariant itself.
INDICATOR_STATUS_SLOT = INDICATOR_COLOUR_INDEX


def _fillA_mode7_offset(data: bytes, rec: "records.WRecord") -> int:
    """Byte offset (GuiData/file coordinates) of `rec`'s `fillA` colour field's mode-7
    `u16`, found by replaying `records.decode_record`'s exact read order up to that field
    -- header (R1-R8), optional clip geometry (R9-R10, gated on header byte7 bit 0x20),
    optional colour-mode bytes (R11-R12, gated on byte7 bit 0x40) -- `fillA` is the very
    next field read (`records.decode_record`'s first `colour(...)` call).

    Self-verifying: the value at the computed offset must equal what `rec.colors` already
    decoded, so a future change to the header-gate order would be caught here rather than
    silently rewriting the wrong bytes. Raises `ValueError` if `rec` carries no mode-7
    `fillA` field (the only shape this targets -- the wtype-33 indicator record has it).
    """
    fillA = next((c for c in rec.colors if c[0] == "fillA"), None)
    if fillA is None or fillA[1] != 7:
        raise ValueError(
            f"record at {rec.offset} (wtype {rec.wtype}) has no mode-7 fillA colour "
            f"field: {rec.colors}")

    b7 = rec.header[7]
    p = rec.offset + 8                             # R1-R8 header
    if b7 & 0x20:                                  # R9-R10 optional clip geometry
        m = data[p]; p += 1
        for bit in (1, 2, 4, 8):
            if m & bit:
                p += 2
    if b7 & 0x40:                                   # R11-R12 colour-mode bytes
        p += 2

    value = struct.unpack_from("<H", data, p)[0]
    if value != fillA[2]:
        raise ValueError(
            f"computed fillA offset {p} holds {value}, expected {fillA[2]} from the "
            f"already-decoded colors -- header-gate replay is out of sync with "
            f"records.decode_record")
    return p


def retarget_indicator(data: bytes, obj_index: int, *,
                       colour_index: int | None = None) -> tuple[bytes, list]:
    """Rewrite the cloned button's on/off indicator to a pin's colour cell.

    `colour_index` is that cell's `+0x44BC8` table index -- `statusbind.bind_pin` returns the
    right one for a given pin. It defaults to `INDICATOR_COLOUR_INDEX` (145, instance 15).

    The clone's wtype-33 record (the visible box / indicator, byte-cloned from the donor)
    carries `fillA` as a mode-7 colour-table index -- inherited verbatim as 155, the
    donor's own indicator cell. This rewrites that one `u16` to `INDICATOR_COLOUR_INDEX`
    (145), the cell already indexed for instance 15, leaving the donor object's own
    indicator and the clone's other two records (draw, touch) alone. A same-size edit;
    `patch.reseal` fixes the one checksum it always invalidates.

    Independent of `retarget_command` -- it touches a different record (wtype 33, not the
    wtype-12 `IMAGE_OPCODE`/binding table) -- so it can run directly on a
    `clone_onto_home` result, before or after `retarget_command`.

    Returns `(new_data, edits)`.
    """
    gs, _ = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[insert.OBJECT_TABLE_INDEX]
    if not 0 <= obj_index < len(table):
        raise ValueError(f"object index {obj_index} out of range [0, {len(table)})")

    colour_index = INDICATOR_COLOUR_INDEX if colour_index is None else colour_index

    recs = records.object_records(data, gs + table[obj_index])
    w33 = next((r for r in recs if r.wtype == 33), None)
    if w33 is None:
        raise ValueError(f"object {obj_index} has no wtype-33 (indicator) record")
    # EVERY wtype-11, not just the first: a button with both an icon record and a caption record
    # has two, and a record left on the donor's config gate does not repaint in place (a probe,
    # 2026-08-17).
    w11s = [r for r in recs if r.wtype == 11]

    off = _fillA_mode7_offset(data, w33)
    old = data[off:off + 2]

    out = bytearray(data)
    struct.pack_into("<H", out, off, colour_index)
    edits: list[relocate.Edit] = [relocate.Edit(
        off, old, out[off:off + 2], "fillA-colour-index",
        f"object {obj_index} wtype-33 fillA colour index "
        f"{struct.unpack('<H', old)[0]} -> {colour_index}")]

    # Point the box AND the caption at the same cell the box paints from. Both inherited the
    # donor's type-1 config gates (53 box, 59 caption); rewriting them all to `colour_index`
    # (== INDICATOR_COLOUR_INDEX) satisfies the live-redraw invariant every working button on
    # this build obeys -- fillA index == box value_slot == caption value_slot (MCF-FORMAT.md
    # Sec.6.0a). Retargeting only the box is what a probe did (2026-08-17), and on the coach the box
    # repainted in
    # place while the caption vanished until a full page render. Same-size u16 edits; a record
    # carrying no value_slot is skipped (the caller then keeps colour-cell-only behaviour).
    targets = [(w33, "wtype-33 box")] + [(r, f"wtype-11 text #{i}") for i, r in enumerate(w11s)]
    for rec, what in targets:
        if rec is None:
            continue
        vso = records.value_slot_offset(data, rec)
        if vso is None:
            continue
        old_vs = data[vso:vso + 2]
        struct.pack_into("<H", out, vso, colour_index)
        edits.append(relocate.Edit(
            vso, old_vs, out[vso:vso + 2], "indicator-status-slot",
            f"object {obj_index} {what} value_slot {struct.unpack('<H', old_vs)[0]} -> "
            f"{colour_index} (the pin's colour/status cell)"))

    checksum_positions = {f.label: f.position for f in patch.checksum_fields(bytes(out))}
    resealed, changed_labels = patch.reseal(bytes(out))
    expected = [firmware.guidata_checksum_label(data)]
    if changed_labels != expected:
        raise ValueError(
            f"reseal changed {changed_labels}, expected exactly {expected}")
    for label in changed_labels:
        pos = checksum_positions[label]
        before = bytes(out)[pos:pos + 2]
        after = resealed[pos:pos + 2]
        edits.append(relocate.Edit(pos, before, after, "checksum", f"resealed {label}"))

    edits.sort(key=lambda e: e.file_offset)
    return resealed, edits


# --------------------------------------------------------------------------------------
# Task 5: label, place, and compose the end-to-end build
# --------------------------------------------------------------------------------------

# The caption written when a direct caller gives no `label`: right-padded to the template
# caption's field (17 bytes on 13.6 and 14.7), the bytes the coach button carried 2026-08-17.
DEFAULT_LABEL = "Pin 15"

# The 13.6 default placement, `(page, x, y)`: the (x, y) origin the positioned attach record
# places the framed button at (its wtype-5 geom[0]/geom[1], base-mode 0). The clone is a real
# box, so its OWN geometry sets the box size and the template's own geom[0]=135 x-offset rides
# on top: the drawn box left edge lands at x + 135, the box is 84 wide x 50 tall.
# Centred in the 13.6 HOME bottom-right quadrant (panel x[435,790], y[263,468]; centre x=612),
# below the Chassis battery indicator (bottom y~391) with padding from the quadrant edges:
# centring an 84-wide box at x=612 wants a left edge of 570, hence x = 435. y=404 sits ~13px
# under the battery and ~14px above the quadrant's bottom edge. Tuned 2026-08-17 by rendering
# HOME against a photo of the panel; verified on the coach 2026-08-17. On another image the spot
# is admitted only by `check_placement`, never assumed free.
DEFAULT_AT_136 = ("home", 435, 404)
NEW_X, NEW_Y = DEFAULT_AT_136[1:]       # the 13.6 default x, y, kept by name for direct callers


def last_object_index(data: bytes) -> int:
    """The current top object-table index -- what `clone_onto_home` appends at."""
    return len(guidata.object_tables(data)[1]) - 1


def build_home_button(data: bytes, *, channel: int | None = None,
                      allow_commanded_channel: bool = False,
                      colour_index: int | None = None,
                      label: str | None = None,
                      at: tuple[str, int, int] | None = None) -> tuple[bytes, list]:
    """The full byte-verified build: attach the proven button template
    (`donors.button_object`) to a page as its own boxed button placed at `at` (default
    `DEFAULT_AT_136`, HOME (435, 404)), command an RV-C instance with a clean toggle, indicate it
    via a colour cell, relabel it, and reseal.

    `channel`, `allow_commanded_channel`, `colour_index` and `label` are forwarded to the
    composed call that already understood each (`retarget_command`, `retarget_indicator`, and
    the label rewrite). Left `None`, `channel` and `colour_index` fall back to `TARGET_CHANNEL`
    (15) and `INDICATOR_COLOUR_INDEX` (145), 13.6 test defaults (the add_button op always passes
    both), and `label` to `DEFAULT_LABEL`. They exist because building on pin 15 and *then*
    retargeting leaves a real defect: every `retarget_command` call allocates a FRESH group from
    `allocate_command_group`, so the first group -- still carrying a command-5 binding on the
    abandoned channel -- stays in the table as an orphan. It is unreachable, but it is a live
    capability the image can still express, and the capability gate (`recipe.capabilities`)
    correctly refuses it. Pass the destination channel here and only one group is ever
    allocated.

    `label` is padded with trailing spaces to the template caption's field width and must not
    exceed it; a longer caption is a relocating edit, not this one.

    `at=(page, x, y)` names the page (`cli._resolve_page_root`) and the attach record's origin;
    the box's left edge lands at x plus the template box's own x offset (135).

    Gates, in order: the template (`donors.button_object`: DonorError GRAMMAR, NO_TEMPLATE); the
    colour cell (`donors.check_mode7_index`: MODE7_INDEX); the footprint
    (`check_placement`: PlacementOverlap); the LCD group handling that reads the group and
    binding the build writes (`grammar.prove_lcd`, the loader `FUN_0021B420` and the sender
    `FUN_0021B8B4`: DonorError LCD_GRAMMAR), before `retarget_command`.

    Composes `clone_onto_home` with a positioned attach -> `retarget_command` ->
    `retarget_indicator`, then one more same-size edit pass of its own -- the label rewrite --
    followed by one final `patch.reseal`. Positioning is done by the attach record (not by
    moving the clone's own geometry): a framed clone's geometry is its box *size*, so the box is
    placed by `insert_object`'s `position=` argument. Each composed call already reseals
    internally (one checksum each, every intermediate stage is a valid re-decodable image); the
    label edit is resealed once more at the end.

    Returns `(new_data, edits)`, edits accumulated across every stage in final-image
    coordinates (structural insert + every same-size field edit + every reseal), sorted by
    offset.
    """
    edits: list = []
    page, x, y = DEFAULT_AT_136 if at is None else at
    check_origin(x, y)

    _obj, proof = donors.button_object(data)
    field = proof.caption_width
    if label is not None:
        raw = label.encode("ascii")
        if len(raw) > field:
            raise ValueError(
                f"label {label!r} is {len(raw)} bytes and the template's caption field is "
                f"{field}; growing it needs a relocating edit")
    # ...and the PADDING is decided further down, once the caption record has been decoded:
    # where the slack goes depends on that record's own h-align byte, and getting it wrong is
    # visible on the panel (see the padding block below).
    donors.check_mode7_index(
        data, INDICATOR_COLOUR_INDEX if colour_index is None else colour_index,
        "add_button's indicator cell")

    out, idx, e = clone_onto_home(data, position=(x, y), page=page)
    edits += e
    check_placement(out, _resolve_page_root(out, page), idx)
    try:
        grammar.prove_lcd(data)
    except grammar.LcdGrammarError as err:
        raise donors.DonorError(
            "LCD_GRAMMAR", f"add_button: the LCD firmware's group handling, which reads the "
                           f"group and binding this build writes, is not proven 13.6's: {err}"
        ) from err
    out, e = retarget_command(out, idx, channel=channel,
                              allow_commanded_channel=allow_commanded_channel)
    edits += e
    out, e = retarget_indicator(out, idx, colour_index=colour_index)
    edits += e

    gs, _ = guidata.guidata_extent(out)
    table = guidata.object_tables(out)[insert.OBJECT_TABLE_INDEX]
    if not 0 <= idx < len(table):
        raise ValueError(f"object index {idx} out of range [0, {len(table)})")
    recs = records.object_records(out, gs + table[idx])

    w11 = next((r for r in recs if r.wtype == 11), None)
    if w11 is None:
        raise ValueError(f"clone {idx} has no wtype-11 (caption) record")
    if w11.label_offset is None:
        raise ValueError(f"clone {idx} wtype-11 record carries no label")
    label_off = w11.label_offset
    # An invariant, not a gate: the clone and `proof.caption_label` both come from the
    # template (`donors.button_object`), so they agree by construction. The gate on the caption
    # is the template proof's NO_TEMPLATE refusal in `donors`.
    current = out[label_off:label_off + field]
    assert current == proof.caption_label

    # Pad the caption to the donor field, the way THIS record is drawn.
    #
    # A left-aligned label draws from its box's left edge, so trailing spaces are invisible and
    # right-padding preserves the indent (`patch.repad`'s hardware-settled default). A CENTRED
    # one is different: the panel centres each line by the width of the string it was given, pad
    # included, so slack parked at the end of the last line drags the visible text left by half
    # of it. That is what shipped in the first reference builds -- 'Overflow\nValve   ' drew its
    # second line 9 px
    # left of centre, next to a 'Water\nPump' that needed no padding and looked right (owner,
    # 2026-08-21). `align="center"` splits the slack around that line instead.
    if label is None:
        new_label_raw = DEFAULT_LABEL.encode("ascii").ljust(field)
    else:
        donor_field = proof.caption_label.decode("latin-1")
        new_label_raw = patch.repad(
            donor_field, donor_field, label, field,
            "center" if w11.h_align == 2 else "right").encode("ascii")

    working = bytearray(out)
    working[label_off:label_off + len(new_label_raw)] = new_label_raw
    edits.append(relocate.Edit(
        label_off, bytes(current), bytes(new_label_raw), "label",
        f"object {idx} wtype-11 label {current!r} -> {new_label_raw!r}"))

    checksum_positions = {f.label: f.position for f in patch.checksum_fields(bytes(working))}
    resealed, changed_labels = patch.reseal(bytes(working))
    expected = [firmware.guidata_checksum_label(data)]
    if changed_labels != expected:
        raise ValueError(
            f"reseal changed {changed_labels}, expected exactly {expected}")
    for label in changed_labels:
        pos = checksum_positions[label]
        before = bytes(working)[pos:pos + 2]
        after = resealed[pos:pos + 2]
        edits.append(relocate.Edit(pos, before, after, "checksum", f"resealed {label}"))

    edits.sort(key=lambda e: e.file_offset)
    return resealed, edits
