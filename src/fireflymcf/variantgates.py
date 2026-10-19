"""Controls that GuiCode ENABLES or DISABLES every tick from a receive slot, by setting their
variant -- and the one-byte edit that ungates one. `GUICODE.md` Finding 68 (2026-09-05).

The mechanism, every joint cited. A group whose header byte 1 is `0x80` is *externally
selected* (`CONTROL-BEHAVIOR.md` §5a): the press never cycles its variant counter, only the
LCD's setter `FUN_0021B5E8` can, and the sender `FUN_0021B8B4` (`0x21B944`) fires only bindings
whose byte-2 high nibble equals that counter. GuiCode reaches the setter through the function
pointer at `0x20039908` (`setVariant(group, variant)`), and `FUN_00247BF8` -- the per-tick
routine handed the receive-slot array in `r0` -- calls it in the shape

    ldrsh.w r3,[r4,#2*var] ; cmp r3,#0 ; bne <other arm>
    movs r1,#V ; movs r0,#GROUP ; blx [0x20039908]

So the control's live variant is a function of one receive slot. Where the group carries NO
binding for variant `V`, that arm is a DISABLE: the press finds nothing to send and the
control is silent. That is the defect `CLAUDE.md` 0b describes -- `Shore Brkr Size`'s `-`
(group 199, header `11 80`) is disabled whenever var 213 reads 0, and var 213 is `DGN_CONFIG`
selector `0x95`, the G12's "all AES parameters at their seed values" flag (`G12-CODE.md`),
which is 0 on every tuned coach and 0 in all 3,329 sweep frames captured here.

`gated_variants` enumerates every such site on a build (13.6 and 11.23 have exactly this one;
14.7 keeps the `11 80` group but drops the call, so its `-` is never disabled). `ungate` rewrites the disabling arm's `movs r1,#V` to `movs r1,#0`, one byte,
guarded on the instruction and on the group really lacking variant-`V` bindings, so the control
follows its variant-0 bindings on every slot value. The gate itself (the compare and the other
arm) is left in place.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import guidata, indirectwrites, theme, thumb



@dataclass(frozen=True)
class GatedVariant:
    call_at: int          # the `blx` runtime address
    variant_at: int       # the `movs r1,#V` runtime address
    group: int
    variant: int
    var_index: int        # the receive slot read
    dgn_low: int
    selector: int         # the var-table entry's compare byte (selector for DGN_CONFIG)
    group_header: bytes
    disabling: bool       # the group carries no binding for this variant

    @property
    def is_config(self) -> bool:
        return self.dgn_low == (guidata.DGN_CONFIG & 0xFFFF)


def _bindings_variants(data: bytes, group: int) -> set:
    gt = guidata.group_table(data)
    bt = guidata.binding_table(data)
    g = gt[group]
    return {bt[k].raw[2] >> 4 for k in range(g.start, g.start + g.count)}


def gated_variants(data: bytes) -> list:
    """Every `setVariant(group, V)` in GuiCode whose arm is selected by a receive-slot read."""
    body, base = indirectwrites.section_body(data, indirectwrites.GUICODE)
    ins = indirectwrites._disasm(body, base)
    at = {i.address: i for i in ins}
    addrs = sorted(at)
    vt = guidata.variable_table(data)
    gt = guidata.group_table(data)
    out = []
    for k, a in enumerate(addrs):
        if at[a].mnemonic != "blx":
            continue
        r0 = r1 = var = None
        r1_at = None
        ptr = False
        for j in range(k - 1, max(k - 9, -1), -1):
            p = at[addrs[j]]
            # the setter is reached as `ldr r3,[pc]=<cell>; ldr r3,[r3]; ... blx r3`: a
            # function-pointer CELL in GuiCode RAM (0x20039908 on 13.6, elsewhere on 11.23)
            w = indirectwrites._lit_word(body, base, p)
            if w is not None and 0x20030000 <= w < 0x20040000 and p.op_str.startswith("r3,") \
                    and j + 1 < len(addrs) and at[addrs[j + 1]].op_str == "r3, [r3]":
                ptr = True
            m = re.match(r"^r0, #(\w+)$", p.op_str)
            if m and p.mnemonic.startswith("mov") and r0 is None:
                r0 = int(m.group(1), 0)
            m = re.match(r"^r1, #(\w+)$", p.op_str)
            if m and p.mnemonic.startswith("mov") and r1 is None:
                r1, r1_at = int(m.group(1), 0), p.address
            m = re.match(r"^r\d, \[r4, #(\w+)\]$", p.op_str)
            if m and p.mnemonic.startswith(("ldrsh", "ldrh")) and var is None:
                var = int(m.group(1), 0) // 2        # 13.6 reads it signed, 11.23 unsigned
        if not (ptr and r0 is not None and r1 is not None and var is not None):
            continue
        if not (0 <= r0 < len(gt) and 0 <= var < len(vt)):
            continue
        v = vt[var]
        out.append(GatedVariant(a, r1_at, r0, r1, var, v.dgn_low, v.value, bytes(gt[r0].raw),
                                r1 not in _bindings_variants(data, r0)))
    return out


class VariantGateError(Exception):
    pass


def ungate(data: bytes, group: int) -> tuple[bytes, list]:
    """Make `group`'s disabling arm select variant 0 instead: ONE byte of GuiCode, guarded.
    Returns `(image, edits)` with `(file_off, old, new, note)`; not resealed."""
    gates = [g for g in gated_variants(data) if g.group == group]
    if not gates:
        raise VariantGateError(f"group {group} has no receive-slot-gated variant on this build")
    disabling = [g for g in gates if g.disabling]
    if len(disabling) != 1:
        raise VariantGateError(
            f"group {group} has {len(disabling)} disabling arms, expected exactly one: {gates}")
    g = disabling[0]
    if g.variant == 0:
        raise VariantGateError(f"group {group}'s disabling arm already passes variant 0")
    sec = theme.guicode_section(data)
    base = theme.guicode_base(sec)
    off = sec.offset + (g.variant_at - base)
    old = bytes(data[off:off + 2])
    thumb.assert_disassembles_to(old, g.variant_at, [f"movs r1, #{g.variant:#x}" if g.variant >= 10
                                                     else f"movs r1, #{g.variant}"])
    new = thumb.movs_imm(1, 0)
    thumb.assert_disassembles_to(new, g.variant_at, ["movs r1, #0"])
    out = bytearray(data)
    out[off:off + 2] = new
    edits = [(off, old, new,
              f"ungate group {group}: setVariant({group}, {g.variant}) -> setVariant({group}, 0) at "
              f"{g.variant_at:#x}; the arm taken when var {g.var_index} (DGN {g.dgn_low:#06x} "
              f"selector {g.selector:#04x}) reads 0 no longer disables the control")]
    return bytes(out), edits


def restore_gate_selector(data: bytes, group: int, selector: int) -> tuple[bytes, list]:
    """Point the gate's receive var at `selector` instead: ONE byte of GuiData (the var-table
    entry's compare byte, `statusbind.FILTER_INSTANCE_FIELD`), guarded on the var really being
    the one the gate reads and on `DGN_CONFIG`.

    This is the fix that follows the firmware's own design: 11.23 gates group 199 on var 213 =
    selector `0x99` (the breaker size, so `-` is disabled at 0 A); 13.6 carries the identical
    code and group and only this byte differs (`0x95`). Returns `(image, edits)`; not resealed."""
    from . import statusbind
    gates = [g for g in gated_variants(data) if g.group == group]
    if len(gates) != 1:
        raise VariantGateError(f"group {group} has {len(gates)} receive-slot gates, expected one")
    g = gates[0]
    if not g.is_config:
        raise VariantGateError(f"group {group}'s gate var {g.var_index} is on DGN {g.dgn_low:#06x}, "
                               "not DGN_CONFIG; a selector retarget does not apply")
    if not 0 <= selector < statusbind.FILTER_DISABLED:
        raise VariantGateError(f"selector {selector:#04x} is not a config selector")
    if g.selector == selector:
        raise VariantGateError(f"var {g.var_index} already reads selector {selector:#04x}")
    vt = guidata.variable_table(data)
    entry = vt[g.var_index]
    gs, _ = guidata.guidata_extent(data)
    off = gs + entry.offset + statusbind.FILTER_INSTANCE_FIELD     # `offset` is GuiData-relative
    old = bytes(data[off:off + 1])
    assert old[0] == g.selector, (old, g)
    out = bytearray(data)
    out[off] = selector
    edits = [(off, old, bytes([selector]),
              f"var {g.var_index} (the gate of group {group}) selector {g.selector:#04x} -> "
              f"{selector:#04x}: the control is now enabled whenever that selector reads non-zero")]
    return bytes(out), edits
