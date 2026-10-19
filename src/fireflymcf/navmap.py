"""How do you REACH a screen? -- the navigation map, 2026-09-03.

The panel's current screen is the byte at `state+0x20A`. Every navigation in the firmware is a
store to it, and the ACTION dispatcher decides which store runs by comparing the touched region's
id (`r2`) against a binary-search tree of constants. This module reads that tree, so
"which page can the user actually get to, and from where" is answerable from the bytes instead of
by reading disassembly by hand each time.

It exists because this project keeps needing it. Screen 14 (`INVERTER / CHARGER SETTINGS`) was
orphaned in stock and got its first route from the hold-region split; screen 28 got one by
repurposing the leaf `0x7DE`; `retarget_inverter_gear` moved another from screen 39 to 14 and left
39 with no route at all. Each of those was worked out one at a time.

**The two leaf shapes, both handled.** A comparison either branches TO the store or branches PAST
it:

    movw r3,#0x7dc / cmp r2,r3 / beq.w 0x24c702      -> screen is at the TARGET
    movw r3,#0x7d1 / cmp r2,r3 / bne.w <skip>        -> screen is in the FALL-THROUGH
    movs r3,#1 / strb.w r3,[r4,#0x20a]

and the operand order is not fixed (`movw r2,#0x838 / cmp r3,r2` also occurs), so both are matched.

**What it does not do.** It reads direct `movs r3,#imm` stores only. A store whose value came from
a register computed elsewhere is not resolved and is reported as an unresolved site rather than
guessed at, so `routes` is a lower bound on reachability and `orphaned` is a claim that needs the
unresolved count read alongside it. It also says nothing about whether a route is *displayed*: a
touch id only reaches the user if some drawn wt-12 region carries it, which is a GuiData question
(`bootlottery._reachable_screens`, `guidata`), and `reachable_routes` joins the two.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import guidata, memo, options

SCREEN_FIELD = 0x20A
_GUICODE_BASE = 0x240000


@dataclass(frozen=True)
class Route:
    """One `touch id -> screen` navigation leaf in the ACTION dispatcher."""

    touch_id: int
    screen: int
    compare_at: int
    store_at: int


def _guicode(data: bytes) -> tuple:
    from . import container

    for section in container.parse_container(data).sections:
        if "GuiCode" in (section.name or ""):
            return data[section.offset:section.offset + section.length], _GUICODE_BASE
    raise ValueError("no GuiCode section")


def _disasm(body: bytes, base: int) -> list:
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB

    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    out, off = [], 0
    while off < len(body) - 2:
        got = False
        for insn in md.disasm(body[off:], base + off):
            got = True
            out.append(insn)
            off = insn.address - base + insn.size
        if not got:
            off += 2
    return out


def _screen_at(ins: list, idx: dict, addr: int) -> "int | None":
    """The screen constant stored by the `movs rN,#imm; strb.w rN,[r4,#0x20a]` pair at `addr`."""
    n = idx.get(addr)
    if n is None or n + 1 >= len(ins):
        return None
    a, b = ins[n], ins[n + 1]
    if not (a.mnemonic in ("movs", "mov", "mov.w") and "#" in a.op_str):
        return None
    if not (b.mnemonic.startswith("strb") and f"#{SCREEN_FIELD:#x}" in b.op_str):
        return None
    if a.op_str.split(",")[0].strip() != b.op_str.split(",")[0].strip():
        return None
    try:
        return int(a.op_str.split("#")[-1], 0)
    except ValueError:
        return None


@memo.content_cached()
def routes(data: bytes) -> tuple:
    """Every `touch id -> screen` leaf the ACTION dispatcher implements."""
    body, base = _guicode(data)
    ins = _disasm(body, base)
    idx = {i.address: n for n, i in enumerate(ins)}
    out = []
    for n, insn in enumerate(ins):
        if insn.mnemonic not in ("movw", "mov.w", "movs", "mov") or "#" not in insn.op_str:
            continue
        reg = insn.op_str.split(",")[0].strip()
        try:
            value = int(insn.op_str.split("#")[-1], 0)
        except ValueError:
            continue
        if not (0x700 <= value <= 0xFFF):          # the ACTION id space
            continue
        if n + 2 >= len(ins):
            continue
        cmp_i, br = ins[n + 1], ins[n + 2]
        if cmp_i.mnemonic not in ("cmp", "cmp.w"):
            continue
        ops = [o.strip() for o in cmp_i.op_str.split(",")]
        if reg not in ops:
            continue
        if not br.mnemonic.startswith("b") or "#" not in br.op_str:
            continue
        target = int(br.op_str.split("#")[-1], 0)
        if br.mnemonic.startswith("beq"):
            screen, store = _screen_at(ins, idx, target), target
        elif br.mnemonic.startswith("bne"):
            fall = ins[n + 3].address if n + 3 < len(ins) else None
            screen, store = (_screen_at(ins, idx, fall), fall) if fall else (None, None)
        else:
            continue
        if screen is not None:
            out.append(Route(value, screen, insn.address, store))
    return tuple(sorted(set(out), key=lambda r: (r.screen, r.touch_id)))


def routes_to(data: bytes, screen: int) -> tuple:
    return tuple(r for r in routes(data) if r.screen == screen)


@memo.content_cached()
def displayed_touch_ids(data: bytes) -> frozenset:
    """Every touch id carried by a wt-12 region in an object some screen root can reach."""
    from . import bootlottery
    import struct

    gs, ge = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    out = set()
    for obj in bootlottery._reachable_screens(data):
        for rec in options._own_records(data, gs, ge, table, obj):
            if rec.wtype == 12:
                out.add(struct.unpack_from("<H", data, rec.end - guidata.GROUP_REFERENCE_TAIL)[0])
    return frozenset(out)


def reachable_routes(data: bytes) -> tuple:
    """Routes whose touch id is actually carried by a region on a reachable screen."""
    shown = displayed_touch_ids(data)
    return tuple(r for r in routes(data) if r.touch_id in shown)


def orphaned(data: bytes) -> tuple:
    """Screens the object table defines that no *displayed* route reaches.

    Read alongside `routes`: a screen may also be entered by a store this module does not resolve
    (see the module docstring), so this is a candidate list, not a proof of unreachability.
    """
    from . import layout

    have = {r.screen for r in reachable_routes(data)}
    return tuple(sorted(s for s in layout.screen_object_map(data) if s not in have))
