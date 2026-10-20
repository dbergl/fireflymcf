"""The LEVEL page's per-tick routine: copies the fourteen 0x0FFB0 receive slots into display cells
and derives the wheel/banner/short/word state cells.

One routine, one hook link. Every slot read treats 0xFFFF as n/a (FUN_0021C374 writes it on
refresh-class expiry, and it is the boot value).

Per tick, with `S(x)` slot `x`'s u16 and `NA` = 0xFFFF:

- pitch, roll: `S` unchanged (the formatter dashes the sensor's 0xFFFE fault angle itself);
- total, carried, lay_*, lift_*: `NA` if `S >= 0xFE` (which includes `NA`), else `S`;
- wheel_k: `NA` if lay_k's cell is `NA`, 0 if it is 0, else 1;
- banner: `NA` if `S(flags)` is `NA`; 2 if `(flags>>4)&3 == 1`; 1 if `flags&3 == 1`; else 0;
- short: 1 if `S(flags)` is live and `(flags>>2)&3 == 1`, else 0;
- pitch_word: 0 if `S(dirs)` is `NA`; 1 if `dirs&3 == 1`; 2 if `(dirs>>2)&3 == 1`; else 0;
- roll_word: the same on `dirs` bits 4-5 and 6-7.

Same shape as `bitcombine.build`: a stream of instructions and labels, two-pass label resolution,
a literal pool after the code, and `thumb.assert_disassembles_to` over the whole blob. Registers:
r0 address, r1 value, r2 scratch, r3 holds `NA` for the whole routine (never used as scratch).
Every conditional branch is local to its block, so each stays inside `b<cond>.n`'s +-256 B.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from . import bitcombine, followchannel, readout, theme, thumb

NA = followchannel.STALE_SENTINEL        # 0xFFFF

VARS = (  # (name, record, payload byte, extraction mode)
    ("pitch", 1, 1, 2), ("roll", 1, 3, 2), ("flags", 1, 5, 1), ("dirs", 1, 6, 1), ("total", 1, 7, 1),
    ("lay_fl", 2, 1, 1), ("lay_fr", 2, 2, 1), ("lay_rl", 2, 3, 1), ("lay_rr", 2, 4, 1),
    ("lift_fl", 3, 1, 1), ("lift_fr", 3, 2, 1), ("lift_rl", 3, 3, 1), ("lift_rr", 3, 4, 1),
    ("carried", 4, 6, 1),
)
CELLS = (  # (name, width); all u16 so 0xFFFF can mean n/a everywhere
    ("pitch", 2), ("roll", 2), ("total", 2), ("carried", 2),
    ("lay_fl", 2), ("lay_fr", 2), ("lay_rl", 2), ("lay_rr", 2),
    ("lift_fl", 2), ("lift_fr", 2), ("lift_rl", 2), ("lift_rr", 2),
    ("wheel_fl", 2), ("wheel_fr", 2), ("wheel_rl", 2), ("wheel_rr", 2),
    ("banner", 2), ("short", 2), ("pitch_word", 2), ("roll_word", 2),
)

# The LEVEL SETUP popup's three values (sensor request 2026-09-29): block height (record 04h byte
# 5, 0.05 in per bit), tolerance in force (04h byte 7, 0.01 in) and the auto-tolerance flag
# (02h byte 5 bits 0-1: 01b auto, 00b fixed, 11b n/a). Present only when `add_level_page` has
# `setup`, so every earlier build keeps its exact routine. Height is scaled x5 into 0.01 in so
# the panel's decimal formatter can show it as inches with two places.
SETUP_VARS = (("height", 4, 5, 1), ("tol", 4, 7, 1), ("auto", 2, 5, 4))
SETUP_CELLS = (("height", 2), ("tol", 2), ("auto", 2))
HEIGHT_SCALE = 5

# The popup's wheelbase and track (v45): record 04h bytes 1-2 and 3-4, u16 in 0.1 in, copied
# straight (the sensor keeps them in 80-300 / 40-110 in; the boot/expiry 0xFFFF stays n/a and the
# formatter dashes it). Present only with `geometry`, so v42-v44 keep their exact routine.
GEOMETRY_VARS = (("wb", 4, 1, 2), ("track", 4, 3, 2))
GEOMETRY_CELLS = (("wb", 2), ("track", 2))
AUTO_NA = 3                                           # the 2-bit field's n/a, 11b

# Repaint-on-change (v43): a readout repaints only its new text's extent, so a narrower value
# leaves the edge of the wider old one (the stray dash before pitch; '2.75' after '12.50' on the
# popup, owner photo 2026-09-29). For each of these values the routine keeps the last value
# (`<name>_p`) and flips a toggle (`<name>_t`, always 0 or 1) when it changes; a two-arm switch on
# the toggle repaints the value's whole box, background first, as a wheel tile's arm does.
REFRESH_LEVEL = ("pitch", "roll", "total", "carried")
REFRESH_SETUP = ("height", "tol")
REFRESH_GEOMETRY = ("wb", "track")


def refresh_names(setup: bool, refresh: bool, geometry: bool = False) -> tuple:
    if not refresh:
        return ()
    return REFRESH_LEVEL + (REFRESH_SETUP if setup else ()) \
        + (REFRESH_GEOMETRY if geometry else ())


def refresh_cells(setup: bool, refresh: bool, geometry: bool = False) -> tuple:
    return tuple((f"{n}_{k}", 2) for n in refresh_names(setup, refresh, geometry) for k in "pt")


WHEELS = ("fl", "fr", "rl", "rr")
# A u8 field at or above this is the sensor's n/a / error range; the cell reads NA instead.
U8_LIMIT = 0xFE


class LevelFeedError(Exception):
    """The LEVEL routine could not be resolved or built (bad slots, cells or load address)."""


@dataclass(frozen=True)
class LevelFeedPlan:
    slots: dict
    cells: dict
    hook_at: int
    hook_old: bytes
    tail_target: int


def _has_setup(slots: dict) -> bool:
    return "height" in slots


def _has_refresh(cells: dict) -> bool:
    return "pitch_t" in cells


def _has_geometry(slots: dict) -> bool:
    return "wb" in slots


def vars_for(setup: bool, geometry: bool = False) -> tuple:
    return VARS + (SETUP_VARS if setup else ()) + (GEOMETRY_VARS if geometry else ())


def cells_for(setup: bool, refresh: bool = False, geometry: bool = False) -> tuple:
    return CELLS + (SETUP_CELLS if setup else ()) + (GEOMETRY_CELLS if geometry else ()) \
        + refresh_cells(setup, refresh, geometry)


def _check_names(slots: dict, cells: dict) -> None:
    setup, geometry = _has_setup(slots), _has_geometry(slots)
    if geometry and not setup:
        raise LevelFeedError("geometry needs the setup values")
    want_s = [n for n, *_r in vars_for(setup, geometry)]
    want_c = [n for n, _w in cells_for(setup, _has_refresh(cells), geometry)]
    if sorted(slots) != sorted(want_s):
        raise LevelFeedError(f"slots must name exactly {want_s}, got {sorted(slots)}")
    if sorted(cells) != sorted(want_c):
        raise LevelFeedError(f"cells must name exactly {want_c}, got {sorted(cells)}")
    for name, width in cells_for(setup, _has_refresh(cells), geometry):
        if cells[name].width != width:
            raise LevelFeedError(f"cell {name!r} must be width {width}, got {cells[name].width}")
    for name, a in list(slots.items()) + [(n, c.addr) for n, c in cells.items()]:
        if a % 2:
            raise LevelFeedError(f"{name!r} address {a:#x} is not halfword-aligned")


def resolve(data: bytes, *, slots: dict, cells: dict) -> LevelFeedPlan:
    """Validate `slots`/`cells` and read the current detour state off `data`.

    The hook is resolved by `bitcombine.resolve` itself (with a dummy source), so both routines
    stay on one code path and a routine resolved on an image already carrying one chains ahead.
    """
    _check_names(slots, cells)
    probe = bitcombine.resolve(
        data, sources=(bitcombine.BitSource(slots[VARS[0][0]], 1, 1),),
        cell=cells[CELLS[0][0]])
    return LevelFeedPlan(dict(slots), dict(cells), probe.hook_at, probe.hook_old,
                         probe.tail_target)


def build(plan: LevelFeedPlan, code_load_addr: int) -> tuple[bytes, list]:
    """The per-tick routine as `(blob, listing)`; verified by capstone before it is returned."""
    if code_load_addr % 4:
        raise LevelFeedError(f"code_load_addr {code_load_addr:#x} is not 4-aligned")
    _check_names(plan.slots, plan.cells)

    def imm(v: int) -> str:
        return f"{v:#x}" if v >= 10 else str(v)

    setup = _has_setup(plan.slots)
    geometry = _has_geometry(plan.slots)
    refresh = _has_refresh(plan.cells)
    # Pool: every slot, then every cell, in VARS/CELLS order, one word per distinct address.
    pool_order: list = []
    for a in [plan.slots[n] for n, *_r in vars_for(setup, geometry)] \
            + [plan.cells[n].addr for n, _w in cells_for(setup, refresh, geometry)]:
        if a not in pool_order:
            pool_order.append(a)

    def S(name):
        return ("pool", plan.slots[name])

    def C(name):
        return ("pool", plan.cells[name].addr)

    stream: list = []
    uniq = iter(range(1 << 30))

    def emit(size, fn):
        stream.append(("ins", size, fn))

    def label(name):
        stream.append(("lbl", name))

    def fresh(stem):
        return f"{stem}{next(uniq)}"

    def ldr_pool(rt, key):
        def fn(h, s):
            target = s(key)
            off = target - ((h + 4) & ~3)
            text = f"ldr r{rt}, [pc, #{off:#x}]" if off else f"ldr r{rt}, [pc]"
            return thumb.ldr_literal(rt, h, target), text
        emit(2, fn)

    def ldrh():
        emit(2, lambda h, s: (thumb.ldrh_imm(1, 0, 0), "ldrh r1, [r0]"))

    def strh():
        emit(2, lambda h, s: (thumb.strh_imm(1, 0, 0), "strh r1, [r0]"))

    def bcond(cond, lbl, mnem):
        emit(2, lambda h, s: (thumb.bcc_n(h, s(lbl), cond), f"{mnem} #{s(lbl):#x}"))

    def b(lbl):
        emit(2, lambda h, s: (thumb.b_n(h, s(lbl)), f"b #{s(lbl):#x}"))

    def movs(rd, v):
        emit(2, lambda h, s: (thumb.movs_imm(rd, v), f"movs r{rd}, #{imm(v)}"))

    def cmp_imm(rn, v):
        emit(2, lambda h, s: (thumb.cmp_imm8(rn, v), f"cmp r{rn}, #{imm(v)}"))

    def cmp_na():
        emit(2, lambda h, s: (thumb.cmp_reg(1, 3), "cmp r1, r3"))

    def field_is_one(shift, hit):
        """r2 = (r1 >> shift) & 3; branch to `hit` when it is 01b (clobbers r0, r2)."""
        if shift:
            emit(2, lambda h, s: (thumb.lsrs_imm(2, 1, shift), f"lsrs r2, r1, #{shift}"))
            movs(0, 3)
            emit(2, lambda h, s: (thumb.ands_reg(2, 0), "ands r2, r0"))
        else:
            movs(2, 3)
            emit(2, lambda h, s: (thumb.ands_reg(2, 1), "ands r2, r1"))
        cmp_imm(2, 1)
        bcond("eq", hit, "beq")

    emit(2, lambda h, s: (thumb.push_reglist([0, 1, 2, 3, 14]), "push {r0, r1, r2, r3, lr}"))
    emit(4, lambda h, s: (thumb.movw(3, NA), f"movw r3, #{NA:#x}"))

    # 1. pitch, roll: straight copies.
    for name in ("pitch", "roll"):
        ldr_pool(0, S(name)); ldrh(); ldr_pool(0, C(name)); strh()

    # 2. u8 fields: NA (or the sensor's FEh/FFh) -> NA, else the value.
    for name in ("total", "carried") + tuple(f"lay_{k}" for k in WHEELS) \
            + tuple(f"lift_{k}" for k in WHEELS):
        keep = fresh("keep")
        ldr_pool(0, S(name)); ldrh()
        cmp_imm(1, U8_LIMIT)
        bcond("cc", keep, "blo")
        emit(2, lambda h, s: (thumb.mov_reg(1, 3), "mov r1, r3"))
        label(keep)
        ldr_pool(0, C(name)); strh()

    # 3. wheels, from the lay cells just written: NA -> NA, 0 -> 0, else 1.
    for k in WHEELS:
        store = fresh("wstore")
        ldr_pool(0, C(f"lay_{k}")); ldrh()
        cmp_na(); bcond("eq", store, "beq")
        cmp_imm(1, 0); bcond("eq", store, "beq")
        movs(1, 1)
        label(store)
        ldr_pool(0, C(f"wheel_{k}")); strh()

    # 4. banner: NA stays NA; fault field (bits 4-5) == 1 -> 2; bits 0-1 == 1 -> 1; else 0.
    two, one, store = fresh("btwo"), fresh("bone"), fresh("bstore")
    ldr_pool(0, S("flags")); ldrh()
    cmp_na(); bcond("eq", store, "beq")
    field_is_one(4, two)
    field_is_one(0, one)
    movs(1, 0); b(store)
    label(one); movs(1, 1); b(store)
    label(two); movs(1, 2)
    label(store)
    ldr_pool(0, C("banner")); strh()

    # 5. short: NA -> 0; bits 2-3 == 1 -> 1; else 0.
    one, zero, store = fresh("sone"), fresh("szero"), fresh("sstore")
    ldr_pool(0, S("flags")); ldrh()
    cmp_na(); bcond("eq", zero, "beq")
    field_is_one(2, one)
    label(zero); movs(1, 0); b(store)
    label(one); movs(1, 1)
    label(store)
    ldr_pool(0, C("short")); strh()

    # 6. direction words from `dirs`: NA -> 0; first field == 1 -> 1; second == 1 -> 2; else 0.
    for cell, (first, second) in (("pitch_word", (0, 2)), ("roll_word", (4, 6))):
        one, two, zero, store = fresh("wone"), fresh("wtwo"), fresh("wzero"), fresh("wdstore")
        ldr_pool(0, S("dirs")); ldrh()
        cmp_na(); bcond("eq", zero, "beq")
        field_is_one(first, one)
        field_is_one(second, two)
        label(zero); movs(1, 0); b(store)
        label(one); movs(1, 1); b(store)
        label(two); movs(1, 2)
        label(store)
        ldr_pool(0, C(cell)); strh()

    # 7. the setup popup's values: height NA-mapped then x5 (0.01 in), tolerance NA-mapped, and
    #    the auto flag with its 11b n/a mapped to NA.
    if setup:
        for name in ("height", "tol"):
            keep, store = fresh("skeep"), fresh("sstore")
            ldr_pool(0, S(name)); ldrh()
            cmp_imm(1, U8_LIMIT)
            bcond("cc", keep, "blo")
            emit(2, lambda h, s: (thumb.mov_reg(1, 3), "mov r1, r3"))
            b(store)
            label(keep)
            if name == "height":
                movs(2, HEIGHT_SCALE)
                emit(2, lambda h, s: (thumb.muls(1, 2), "muls r1, r2, r1"))
            label(store)
            ldr_pool(0, C(name)); strh()
        keep = fresh("akeep")
        ldr_pool(0, S("auto")); ldrh()
        cmp_imm(1, AUTO_NA)
        bcond("cc", keep, "blo")
        emit(2, lambda h, s: (thumb.mov_reg(1, 3), "mov r1, r3"))
        label(keep)
        ldr_pool(0, C("auto")); strh()

    # 7b. wheelbase and track: straight copies.
    if geometry:
        for name in ("wb", "track"):
            ldr_pool(0, S(name)); ldrh(); ldr_pool(0, C(name)); strh()

    # 8. repaint-on-change: when a value cell differs from its last value, remember it and flip
    #    the toggle (normalised to 0/1: the SRAM starts as junk, `raminit.py`).
    for name in refresh_names(setup, refresh, geometry):
        same, one, tstore = fresh("rsame"), fresh("rone"), fresh("rtstore")
        ldr_pool(0, C(name)); ldrh()
        ldr_pool(0, C(f"{name}_p"))
        emit(2, lambda h, s: (thumb.ldrh_imm(2, 0, 0), "ldrh r2, [r0]"))
        emit(2, lambda h, s: (thumb.cmp_reg(1, 2), "cmp r1, r2"))
        bcond("eq", same, "beq")
        strh()
        ldr_pool(0, C(f"{name}_t")); ldrh()
        cmp_imm(1, 0); bcond("eq", one, "beq")
        movs(1, 0); b(tstore)
        label(one); movs(1, 1)
        label(tstore); strh()
        label(same)

    # 9. back into the hook chain.
    emit(4, lambda h, s: (thumb.pop_w_reglist([0, 1, 2, 3, 14]), "pop.w {r0, r1, r2, r3, lr}"))
    emit(4, lambda h, s: (thumb.b_w(h, plan.tail_target), f"b.w #{plan.tail_target:#x}"))

    labels: dict = {}
    code_len = 0
    for item in stream:
        if item[0] == "lbl":
            labels[item[1]] = code_len
        else:
            code_len += item[1]
    entry = code_load_addr + readout.ENTRY_OFF
    pool_off = (readout.ENTRY_OFF + code_len + 3) & ~3
    pool_abs = {a: code_load_addr + pool_off + i * 4 for i, a in enumerate(pool_order)}

    def sym(key):
        if isinstance(key, tuple):
            return pool_abs[key[1]]
        return entry + labels[key]

    code = bytearray()
    expected: list = []
    for item in stream:
        if item[0] == "lbl":
            continue
        _k, size, fn = item
        try:
            raw, text = fn(entry + len(code), sym)
        except ValueError as e:
            raise LevelFeedError(f"cannot encode at {entry + len(code):#x}: {e}") from e
        if len(raw) != size:
            raise LevelFeedError(f"size mismatch at {entry + len(code):#x}: {text!r}")
        code += raw
        expected.append(text)
    thumb.assert_disassembles_to(bytes(code), entry, expected)

    blob = bytearray(code)
    while len(blob) < pool_off:
        blob += b"\x00"
    for a in pool_order:
        blob += struct.pack("<I", a)
    assert len(blob) % 4 == 0
    return bytes(blob), thumb.disassemble(bytes(code), entry)


def detour_edit(plan: LevelFeedPlan, code_load_addr: int) -> tuple[int, bytes, bytes]:
    """`(hook_offset, stock_bytes, new_bl)` -- identical contract to `bitcombine.detour_edit`."""
    followchannel._check_plan_hook(plan)
    hook_runtime = theme.GUICODE_BASE_DEFAULT + plan.hook_at
    return plan.hook_at, plan.hook_old, thumb.bl(hook_runtime, code_load_addr + readout.ENTRY_OFF)
