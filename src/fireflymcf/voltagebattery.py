"""The 48V battery per-tick routine: read a bus voltage slot, drive a cloned battery's three
isolated status cells (value number, colour word, gauge fill level), tail-chain on the hook.

Spec: its design note (not published).

`AddVoltageBattery` (recipe_ops.py) clones donor 443 into a fresh object beside the chassis
battery and repoints its colour / gauge-level / value cells onto three fresh SRAM words nothing
stock names. That leaves those cells dead -- the stock per-tick block `FUN_00247BF8` writes only
the two ORIGINAL batteries' cells. This module authors the routine that fills the clone's three
cells every tick, appended to GuiCode and chained on the per-tick hook exactly the way
`readout.build` chains its copy routine.

The frame -- prologue, literal-pool layout, `assert_disassembles_to` gate, tail-branch to the
previous chain head -- MIRRORS `readout.build` (src/fireflymcf/readout.py); only the
computation body differs. The body mirrors the native battery block `FUN_00247BF8`
(@ 0x24a326-0x24a5f8) with 48V constants:

  raw = u16 at slot_addr (instance-1 48V pack voltage, x0.05 V; e.g. 1080 == 54.0 V).
  VALUE : OPTIONAL (tenths = raw >> 1 -> strh to value_cell). Omitted by add_voltage_battery,
          whose clone reads the live receive-fed cell 209 directly so the number repaints live;
          a fresh value cell is off the panel's repaint watch-list and would show a stale number.
  COLOR : raw >= thresh (1008, == 50.4 V) -> green (0x2BB673) else red (0xCC2127), a full
          32-bit word (str) to color_cell -- a mode-7 fill is read as a raw word, no variable-
          type dispatch (GUICODE Finding 18).
  LEVEL : clamp raw to [lo, hi] = [960, 1096]; empty_offset = ((hi - raw_clamped) * 77) >> 8,
          a byte in 0..40 (0 = full at 54.8 V, 40 = empty at 48.0 V), strb to level_cell -- the
          value-bound partial fill reads this as the empty-from-top offset (native strb at
          0x24a5e4). This cloned gauge reuses the STOCK battery-gauge scale, whose empty-offset
          spans a full 40 units (native `FUN_00247BF8` stores `(125-tenths)*4`, max 40, and
          `render._battery_level_fraction` normalises by 40). The offset must therefore reach 40
          at empty, not 34 -- an earlier `(hi - raw_clamped) >> 2` maxed at 34, leaving the pack
          ~15% full on hardware at 48.0 V. `* 77 >> 8` is the clean Thumb rescale of the 136-unit
          voltage window [960,1096] onto 0..40: delta 0 -> 0, delta 68 (51.4 V) -> 20, delta 136
          -> 40 (10472 >> 8), monotonic, no overflow (max product 136*77 = 10472). This matches
          `render.coach_battery_fraction` (0.0 at 48.0 V, 1.0 at 54.8 V) and the design window.

The routine feeds only FRESH cells, so -- unlike a routine feeding a stock cell -- there is no
config-byte store to silence: the chain runs before `FUN_00247BF8`, which never touches these
addresses (CLAUDE.md's hook caveat applies only when feeding a stock cell).
"""
from __future__ import annotations

import struct

from . import thumb


class VoltageBatteryError(Exception):
    """Refuse rather than guess: a mis-aligned load address, or an out-of-range constant."""


ENTRY_OFF = 0        # no frames precede the code: the entry is the blob's first byte

# 48V defaults (api-notes.md's 48V-source section):
GREEN = 0x2BB673
RED = 0xCC2127
THRESH = 1008        # raw x0.05 V == 50.4 V; below this the battery reads red
LO = 960             # 48.0 V, gauge empty
HI = 1096            # 54.8 V, gauge full
# Empty-offset rescale: map the (hi-lo)=136-unit window onto the stock gauge's 0..40 empty-offset
# span. `((hi - raw) * 77) >> 8` is the clean Thumb form -- 136*77=10472, 10472>>8 = 40.
LEVEL_MUL = 77
LEVEL_SHIFT = 8


def build_routine(*, slot_addr: int, color_cell: int, level_cell: int,
                  entry_addr: int, tail_target: int, value_cell: int | None = None,
                  green: int = GREEN, red: int = RED,
                  thresh: int = THRESH, lo: int = LO, hi: int = HI) -> bytes:
    """The per-tick routine + a word-aligned literal pool, as bytes to append at `entry_addr`.

    Structurally identical to `readout.build`: a stream of `(size, fn)` emitters and `lbl`
    markers, resolved in two passes (labels/pool addresses first, then encode), verified
    instruction-for-instruction by `thumb.assert_disassembles_to`, then the pool appended
    word-aligned. `tail_target` is the previous per-tick chain head (`followchannel.hook_state`);
    the routine ends `b.w tail_target`, so the chain becomes hook -> this -> previous.
    """
    if entry_addr % 4:
        raise VoltageBatteryError(f"entry_addr {entry_addr:#x} is not 4-aligned")
    for name, v in (("thresh", thresh), ("lo", lo), ("hi", hi)):
        if not 0 <= v <= 0xFFFF:
            raise VoltageBatteryError(f"{name} must fit a movw imm16 (0..0xFFFF), got {v:#x}")
    if lo > hi:
        raise VoltageBatteryError(f"lo {lo} must be <= hi {hi}")
    checks = [("green", green), ("red", red), ("slot_addr", slot_addr),
              ("color_cell", color_cell), ("level_cell", level_cell)]
    if value_cell is not None:
        checks.append(("value_cell", value_cell))
    for name, v in checks:
        if not 0 <= v <= 0xFFFFFFFF:
            raise VoltageBatteryError(f"{name} must be a 32-bit word, got {v:#x}")

    # `value_cell` is optional: when the clone's value widget keeps its donor value_slot (a live,
    # receive-filter-fed decoded voltage cell -- e.g. the coach-48V cell 209 the ELECTRICAL page
    # reads, which the panel repaints live), the routine drives only colour + level and does not
    # write the value at all. A fresh value cell is not on the panel's live-repaint watch-list, so
    # it would show a stale number.
    pool_order = ["slot", "green", "red", "color_cell", "level_cell"]
    pool_val = {"slot": slot_addr, "green": green, "red": red, "color_cell": color_cell,
                "level_cell": level_cell}
    if value_cell is not None:
        pool_order.append("value_cell")
        pool_val["value_cell"] = value_cell
    stream: list = []

    def emit(size, fn):
        stream.append(("ins", size, fn))

    def label(name):
        stream.append(("lbl", name))

    def ldrlit_text(rt, here, target):
        off = target - ((here + 4) & ~3)
        return f"ldr r{rt}, [pc, #{off:#x}]" if off else f"ldr r{rt}, [pc]"

    # r1 = raw voltage, preserved across the whole body (value display needs the unclamped raw);
    # r2/r3 are scratch. r0 is the store base address, reloaded per store.
    emit(2, lambda h, s: (thumb.push_reglist([0, 1, 2, 3, 14]), "push {r0, r1, r2, r3, lr}"))
    emit(2, lambda h, s: (thumb.ldr_literal(0, h, s("slot")), ldrlit_text(0, h, s("slot"))))
    emit(2, lambda h, s: (thumb.ldrh_imm(1, 0, 0), "ldrh r1, [r0]"))

    # --- COLOR: raw >= thresh -> green else red, stored as a full 32-bit word ---
    emit(4, lambda h, s: (thumb.movw(2, thresh), f"movw r2, #{thresh:#x}"))
    emit(2, lambda h, s: (thumb.cmp_reg(1, 2), "cmp r1, r2"))
    emit(2, lambda h, s: (thumb.bcc_n(h, s("green_arm"), "cs"), f"bhs #{s('green_arm'):#x}"))
    emit(2, lambda h, s: (thumb.ldr_literal(3, h, s("red")), ldrlit_text(3, h, s("red"))))
    emit(2, lambda h, s: (thumb.b_n(h, s("color_store")), f"b #{s('color_store'):#x}"))
    label("green_arm")
    emit(2, lambda h, s: (thumb.ldr_literal(3, h, s("green")), ldrlit_text(3, h, s("green"))))
    label("color_store")
    emit(2, lambda h, s: (thumb.ldr_literal(0, h, s("color_cell")),
                          ldrlit_text(0, h, s("color_cell"))))
    emit(2, lambda h, s: (thumb.str_imm(3, 0, 0), "str r3, [r0]"))

    # --- LEVEL: clamp raw into [lo, hi] (r2), empty_offset = ((hi - r2) * 77) >> 8, byte store.
    # The *77>>8 rescale (vs a bare >>2) lifts the empty-offset onto the stock gauge's full 0..40
    # span, so the clone bottoms out at 48.0 V instead of ~15% fill (see the module docstring). ---
    emit(2, lambda h, s: (thumb.mov_reg(2, 1), "mov r2, r1"))
    emit(4, lambda h, s: (thumb.movw(3, lo), f"movw r3, #{lo:#x}"))
    emit(2, lambda h, s: (thumb.cmp_reg(2, 3), "cmp r2, r3"))
    emit(2, lambda h, s: (thumb.bcc_n(h, s("ge_lo"), "cs"), f"bhs #{s('ge_lo'):#x}"))
    emit(2, lambda h, s: (thumb.mov_reg(2, 3), "mov r2, r3"))          # r2 = lo
    label("ge_lo")
    emit(4, lambda h, s: (thumb.movw(3, hi), f"movw r3, #{hi:#x}"))
    emit(2, lambda h, s: (thumb.cmp_reg(2, 3), "cmp r2, r3"))
    emit(2, lambda h, s: (thumb.bcc_n(h, s("le_hi"), "ls"), f"bls #{s('le_hi'):#x}"))
    emit(2, lambda h, s: (thumb.mov_reg(2, 3), "mov r2, r3"))          # r2 = hi
    label("le_hi")
    # r3 still holds hi here (last write was movw r3,#hi), but reload it explicitly so the
    # arithmetic does not depend on the clamp path not having been re-ordered.
    emit(4, lambda h, s: (thumb.movw(3, hi), f"movw r3, #{hi:#x}"))
    emit(2, lambda h, s: (thumb.subs_reg(2, 3, 2), "subs r2, r3, r2"))  # r2 = hi - raw_clamped
    # rescale delta (0..136) onto the stock 0..40 empty-offset span: r2 = (r2 * 77) >> 8.
    # r3 is free here (held hi, already consumed by the subs). muls r2,r3 -> r2 = r3 * r2.
    emit(2, lambda h, s: (thumb.movs_imm(3, LEVEL_MUL), f"movs r3, #{LEVEL_MUL:#x}"))
    emit(2, lambda h, s: (thumb.muls(2, 3), "muls r2, r3, r2"))
    emit(2, lambda h, s: (thumb.lsrs_imm(2, 2, LEVEL_SHIFT), f"lsrs r2, r2, #{LEVEL_SHIFT}"))
    emit(2, lambda h, s: (thumb.ldr_literal(0, h, s("level_cell")),
                          ldrlit_text(0, h, s("level_cell"))))
    emit(4, lambda h, s: (thumb.strb_imm12(2, 0, 0), "strb.w r2, [r0]"))

    # --- VALUE (optional): tenths = raw >> 1, halfword store (widget's one-decimal format).
    # Omitted when the clone's value widget reads a live receive-fed cell directly (see above). ---
    if value_cell is not None:
        emit(2, lambda h, s: (thumb.lsrs_imm(1, 1, 1), "lsrs r1, r1, #1"))
        emit(2, lambda h, s: (thumb.ldr_literal(0, h, s("value_cell")),
                              ldrlit_text(0, h, s("value_cell"))))
        emit(2, lambda h, s: (thumb.strh_imm(1, 0, 0), "strh r1, [r0]"))

    # --- epilogue: restore scratch + lr, tail-branch to the previous chain head ---
    emit(4, lambda h, s: (thumb.pop_w_reglist([0, 1, 2, 3, 14]), "pop.w {r0, r1, r2, r3, lr}"))
    emit(4, lambda h, s: (thumb.b_w(h, tail_target), f"b.w #{tail_target:#x}"))

    # Pass 1: label offsets and total code length.
    labels: dict = {}
    code_len = 0
    for item in stream:
        if item[0] == "lbl":
            labels[item[1]] = code_len
        else:
            code_len += item[1]
    entry = entry_addr + ENTRY_OFF
    pool_off = (ENTRY_OFF + code_len + 3) & ~3
    pool_abs = {n: entry_addr + pool_off + i * 4 for i, n in enumerate(pool_order)}

    def sym(name):
        return entry + labels[name] if name in labels else pool_abs[name]

    # Pass 2: encode, collecting the claimed disassembly for the gate.
    code = bytearray()
    expected: list = []
    for item in stream:
        if item[0] == "lbl":
            continue
        _k, size, fn = item
        raw, text = fn(entry + len(code), sym)
        if len(raw) != size:
            raise VoltageBatteryError(f"size mismatch at {entry + len(code):#x}: {text!r}")
        code += raw
        expected.append(text)
    thumb.assert_disassembles_to(bytes(code), entry, expected)

    blob = bytearray(code)
    while len(blob) < pool_off:
        blob += b"\x00"
    for n in pool_order:
        blob += struct.pack("<I", pool_val[n])
    assert len(blob) % 4 == 0
    return bytes(blob)
