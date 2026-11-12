"""A live RV-C temperature readout that follows the panel's Temp Units setting.

The panel's own temperature readouts (the CLIMATE ambient and setpoint, object 872) are built
from three pieces, all `proven-from-code` on 13.6:

* **The units flag** is display cell 9 (13.6 `0x2003A2A3`, GUICODE Finding 27): 0 Celsius,
  1 Fahrenheit. GuiCode's ambient conversion reads it at `0x24A9DE` (`ldrb r5,[r3]`, the literal
  at `0x24AAFC`) and `cbz r5` takes the Celsius arm.
* **The conversion** (`0x24A9E4`-`0x24AA8A`) stores a signed halfword (`sxth`, `strh`): tenths
  of a degree C on the Celsius arm (`(raw - 8736) * 10 >> 5`), whole degrees F on the other
  (`(raw - 8736) * 9 / 160 + 32`, by the `0x66666667` multiply). The cell is typed 4, which the
  value formatter `FUN_00251E38` reads with `ldrsh` (its jump-table case 4, `0x251EAA`), so a
  negative temperature prints with its sign.
* **The format** is chosen by a stock type-6 switch on cell 9 whose two arms are number-format
  directives (872[6] -> objects 93/94): match 0 is `00 18 05 01` (width 5, one place: `-12.5`),
  match 1 is `00 18 03 00` (width 3, whole degrees: `41`).

This module authors the same three for any received RV-C temperature (`1/32 K` steps,
`0xFB00`-`0xFFFF` not a reading): a per-tick routine that converts the register into a fresh
type-4 cell by the units flag, and an object that copies the stock directive switch, draws the
cell with a wtype-8 readout, and follows it with a second switch on cell 9 whose arms are the
unit labels `°C` / `°F`. A missing or stale reading stores `INVALID`, which is wider than either
directive's field, so the panel draws its own placeholder: dashes (GUICODE Finding 65,
`0x002528A4`).

The rounding is the nearest degree (F) or tenth (C), where the stock ambient truncates; for a
battery a half-step is not worth matching truncation for.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from . import cardlayout, followchannel, guidata, layout, records, status_map, switchbuild, thumb
from .readout import DisplayCell

UNITS_CELL = 9            # the Temp Units flag's display cell (GUICODE Finding 27)
CELSIUS, FAHRENHEIT = 0, 1
VALUE_TYPE = 4            # s16: FUN_00251E38 case 4 is `ldrsh` (0x251EAA)
RVC_MAX = 0xFAFF          # the largest RV-C 16-bit data value; 0xFB00-0xFFFF are not readings
INVALID = 30000           # wider than both stock fields ("3000.0" > 5, "30000" > 3): dashes
KELVIN0 = 8736            # 273 degC in 1/32 steps: RV-C's temperature zero
UNIT_TEXT = {CELSIUS: "°C", FAHRENHEIT: "°F"}
ENTRY_OFF = 0


class TempReadoutError(ValueError):
    """Refuse rather than guess."""


@dataclass(frozen=True)
class UnitsSwitch:
    record: bytes          # the stock directive switch's bytes, copied verbatim
    arms: dict             # {match value: (width, places)}
    flag_addr: int         # the units flag's SRAM address (display cell 9)


def units_switch(data: bytes) -> UnitsSwitch:
    """The stock type-6 switch on cell 9 whose two arms are a Celsius (one place) and a
    Fahrenheit (no places) number-format directive, and the flag's address. Refuses unless the
    image carries exactly that shape: one form, wherever it is used."""
    gs, _ = guidata.guidata_extent(data)
    tab = guidata.object_tables(data)[1]
    scr = layout.Screen(data)
    found: dict = {}
    for i in range(len(tab)):
        try:
            recs = records.object_records(data, gs + tab[i])
        except (ValueError, IndexError):
            continue
        for r in recs:
            if r.wtype != 6 or r.value_slot != UNITS_CELL or r.count != 2:
                continue
            arms = {}
            for k in range(2):
                obj = r.child_index + k
                mv = scr._match_value(obj)
                try:
                    arm = records.object_records(data, gs + tab[obj])
                except (ValueError, IndexError):
                    arm = []
                if len(arm) != 1 or arm[0].wtype != 9 or arm[0].fmt is None or arm[0].fmt[0] != 0:
                    break
                arms[mv] = (arm[0].fmt[2], arm[0].fmt[3])
            if set(arms) == {CELSIUS, FAHRENHEIT} and arms[CELSIUS][1] == 1 \
                    and arms[FAHRENHEIT][1] == 0:
                found.setdefault(bytes(data[r.offset:r.end]), arms)
    if len(found) != 1:
        raise TempReadoutError(
            "no single stock Celsius/Fahrenheit directive switch on cell %d (%d forms)"
            % (UNITS_CELL, len(found)))
    raw, arms = next(iter(found.items()))
    cells = status_map.cell_table(data)
    return UnitsSwitch(raw, arms, cells[UNITS_CELL])


@dataclass(frozen=True)
class TempPlan:
    slot_addr: int
    flag_addr: int
    cell: DisplayCell
    hook_at: int
    hook_old: bytes
    tail_target: int


def resolve(data: bytes, *, slot_addr: int, cell: DisplayCell,
            stock: bytes | None = None) -> TempPlan:
    if cell.width != 2:
        raise TempReadoutError("a temperature cell is a halfword, got width %d" % cell.width)
    sw = units_switch(data)
    hook_at, hook_old, tail = followchannel.hook_site(data, stock=stock)
    return TempPlan(slot_addr, sw.flag_addr, cell, hook_at, hook_old, tail)


def celsius_tenths(raw: int) -> int:
    """The routine's Celsius arm in Python: tenths of a degree, rounded half up."""
    return ((raw * 10 + 16) >> 5) - 2730


def fahrenheit(raw: int) -> int:
    """The routine's Fahrenheit arm in Python: whole degrees, rounded half up.
    raw*9/160 - 459.4 = (raw*9 + 176)/160 - 460, the numerator kept positive for `udiv`."""
    return (raw * 9 + 176) // 160 - 460


def build(plan: TempPlan, code_load_addr: int) -> tuple[bytes, list]:
    """The per-tick conversion, chained on the hook:

        raw = slot; if raw > 0xFAFF: v = INVALID
        elif flag == 0: v = ((raw * 10 + 16) >> 5) - 2730        # tenths of a degree C
        else:           v = (raw * 9 + 176) / 160 - 460           # whole degrees F
        cell = v (strh: the low 16 bits, read back signed)

    Verified instruction for instruction by `thumb.assert_disassembles_to`."""
    if code_load_addr % 4:
        raise TempReadoutError(f"code_load_addr {code_load_addr:#x} is not 4-aligned")
    pool_order = ["slot", "flag", "cell"]
    pool_val = {"slot": plan.slot_addr, "flag": plan.flag_addr, "cell": plan.cell.addr}
    stream: list = []

    def emit(size, fn):
        stream.append(("ins", size, fn))

    def label(name):
        stream.append(("lbl", name))

    def ldrlit(rt, name):
        def fn(h, s):
            off = s(name) - ((h + 4) & ~3)
            return (thumb.ldr_literal(rt, h, s(name)),
                    f"ldr r{rt}, [pc, #{off:#x}]" if off else f"ldr r{rt}, [pc]")
        emit(2, fn)

    def movw(rd, v):
        emit(4, lambda h, s: (thumb.movw(rd, v), f"movw r{rd}, {thumb.imm_text(v)}"))

    def br(cond, name):
        if cond is None:
            emit(2, lambda h, s: (thumb.b_n(h, s(name)), f"b #{s(name):#x}"))
        else:
            emit(2, lambda h, s: (thumb.bcc_n(h, s(name), cond),
                                  f"b{thumb.COND_TEXT[cond]} #{s(name):#x}"))

    emit(2, lambda h, s: (thumb.push_reglist([0, 1, 2, 3, 14]), "push {r0, r1, r2, r3, lr}"))
    ldrlit(0, "slot")
    emit(2, lambda h, s: (thumb.ldrh_imm(1, 0, 0), "ldrh r1, [r0]"))
    movw(2, RVC_MAX)
    emit(2, lambda h, s: (thumb.cmp_reg(1, 2), "cmp r1, r2"))
    br("hi", "invalid")
    ldrlit(0, "flag")
    emit(2, lambda h, s: (thumb.ldrb_imm(0, 0, 0), "ldrb r0, [r0]"))
    emit(2, lambda h, s: (thumb.cmp_imm8(0, 0), "cmp r0, #0"))
    br("ne", "fahrenheit")
    # Celsius: ((raw * 10 + 16) >> 5) - 2730
    emit(2, lambda h, s: (thumb.movs_imm(2, 10), "movs r2, #0xa"))
    emit(2, lambda h, s: (thumb.muls(1, 2), "muls r1, r2, r1"))
    emit(2, lambda h, s: (thumb.adds_imm8(1, 16), "adds r1, #0x10"))
    emit(2, lambda h, s: (thumb.lsrs_imm(1, 1, 5), "lsrs r1, r1, #5"))
    movw(2, 2730)
    emit(2, lambda h, s: (thumb.subs_reg(1, 1, 2), "subs r1, r1, r2"))
    br(None, "store")
    label("fahrenheit")
    # Fahrenheit: (raw * 9 + 176) / 160 - 460
    emit(2, lambda h, s: (thumb.movs_imm(2, 9), "movs r2, #9"))
    emit(2, lambda h, s: (thumb.muls(1, 2), "muls r1, r2, r1"))
    emit(2, lambda h, s: (thumb.adds_imm8(1, 176), "adds r1, #0xb0"))
    emit(2, lambda h, s: (thumb.movs_imm(2, 160), "movs r2, #0xa0"))
    emit(4, lambda h, s: (thumb.udiv(1, 1, 2), "udiv r1, r1, r2"))
    movw(2, 460)
    emit(2, lambda h, s: (thumb.subs_reg(1, 1, 2), "subs r1, r1, r2"))
    br(None, "store")
    label("invalid")
    movw(1, INVALID)
    label("store")
    ldrlit(0, "cell")
    emit(2, lambda h, s: (thumb.strh_imm(1, 0, 0), "strh r1, [r0]"))
    emit(4, lambda h, s: (thumb.pop_w_reglist([0, 1, 2, 3, 14]), "pop.w {r0, r1, r2, r3, lr}"))
    emit(4, lambda h, s: (thumb.b_w(h, plan.tail_target), f"b.w #{plan.tail_target:#x}"))

    labels: dict = {}
    code_len = 0
    for item in stream:
        if item[0] == "lbl":
            labels[item[1]] = code_len
        else:
            code_len += item[1]
    entry = code_load_addr + ENTRY_OFF
    pool_off = (ENTRY_OFF + code_len + 3) & ~3
    pool_abs = {n: code_load_addr + pool_off + i * 4 for i, n in enumerate(pool_order)}

    def sym(name):
        return entry + labels[name] if name in labels else pool_abs[name]

    code = bytearray()
    expected: list = []
    for item in stream:
        if item[0] == "lbl":
            continue
        _k, size, fn = item
        raw, text = fn(entry + len(code), sym)
        if len(raw) != size:
            raise TempReadoutError(f"size mismatch at {entry + len(code):#x}: {text!r}")
        code += raw
        expected.append(text)
    thumb.assert_disassembles_to(bytes(code), entry, expected)
    blob = bytearray(code)
    while len(blob) < pool_off:
        blob += b"\x00"
    for n in pool_order:
        blob += struct.pack("<I", pool_val[n])
    return bytes(blob), thumb.disassemble(bytes(code), entry)


def detour_edit(plan: TempPlan, code_load_addr: int) -> tuple[int, bytes, bytes]:
    from . import theme
    followchannel._check_plan_hook(plan)
    hook_runtime = theme.GUICODE_BASE_DEFAULT + plan.hook_at
    return plan.hook_at, plan.hook_old, thumb.bl(hook_runtime, code_load_addr + ENTRY_OFF)


def unit_arm(x: int, y: int, w: int, h: int, font: int, unit: int) -> bytes:
    """One unit arm object: a single absolute white `°C`/`°F` label."""
    payload = bytes([1]) + cardlayout.title_record(x, y, UNIT_TEXT[unit], font=font, w=w, h=h)
    return payload + b"\x00" * (-len(payload) % 4)


def append_unit_arms(data: bytes, box: tuple, font: int) -> tuple[bytes, int]:
    """Grow the match table and append the two unit arms (match 0 `°C`, match 1 `°F`), as the
    stock directive arms are matched. Returns `(image, arms_base)`."""
    x, y, w, h = box
    return switchbuild.append_arms(
        data, [unit_arm(x, y, w, h, font, CELSIUS), unit_arm(x, y, w, h, font, FAHRENHEIT)],
        [CELSIUS, FAHRENHEIT])


def build_object(data: bytes, *, x: int, y: int, display_slot: int, font: int,
                 unit_arms_base: int | None) -> bytes:
    """`<count>` + the stock directive switch (copied) + a wtype-8 readout absolute at the pen
    (x, y) in `font` reading `display_slot` + (when `unit_arms_base`) a switch on cell 9 over the
    unit arms. The readout right-justifies in the directive's field, so its right edge is x."""
    from . import readout
    sw = units_switch(data)
    # the readout record: readout.author_readout_object's second record, its font set
    payload = readout.author_readout_object(data, x=x, y=y, display_slot=display_slot,
                                            directive=readout.directive_bytes(3, 0), font=font)
    _d, r = records.object_records(payload, 0)
    rfrag = bytearray(payload[r.offset:r.end])
    off = records.font_offset(payload, r)
    if off is None:
        raise TempReadoutError("the readout template carries no font selector to set")
    rfrag[off - r.offset] = font
    parts = [sw.record, bytes(rfrag)]
    if unit_arms_base is not None:
        parts.append(switchbuild.author_switch(data, value_slot=UNITS_CELL,
                                               arms_base=unit_arms_base, count=2))
    obj = bytes([len(parts)]) + b"".join(parts)
    obj += b"\x00" * (-len(obj) % 4)
    chk = records.object_records(obj, 0)
    if not (len(chk) == len(parts) and chk[0].wtype == 6 and chk[0].value_slot == UNITS_CELL
            and chk[1].wtype == 8 and chk[1].value_slot == display_slot and chk[1].font == font
            and chk[1].geom[0] == x and chk[1].geom[1] == y
            and (unit_arms_base is None or (chk[2].wtype == 6 and chk[2].value_slot == UNITS_CELL
                                            and chk[2].child_index == unit_arms_base))):
        raise TempReadoutError("authored temperature readout failed its self-decode")
    return obj
