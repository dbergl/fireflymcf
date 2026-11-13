"""screenadd -- add a NEW screen: a screen id and a root object the stock image does not have.

The facts this rests on (GUICODE.md Finding 93, `proven-from-code` on 13.6):

* The top-level screen dispatch is one wtype-6 switch (`layout._screen_switch_record`) reading
  display cell 2 (`0x2003A1FC`), which GuiCode copies from `state+0x20A` on a screen change
  (`0x24EE56`-`0x24EE76`). Its candidates are the fixed band `[child_index, child_index+count)`
  of object table 1 (13.6: objects 3..49, count 47, object 3 the `0xFFFF` default). No other
  GuiData record reads cell 2.
* GuiCode loads `state+0x20A` at nine sites (`screen_id_readers`); each compares it with a
  constant (`0x60`, `0x64`, `0x11`), saves it to a cell, or compares it with the last-drawn id.
  None indexes a table by it. A screen id GuiCode never writes is inert to GuiCode.
* A screen is drawn from object 0 (`0x24EE8A movs r0,#0 / bl FUN_00258650`), through the switch;
  the other entries draw fixed objects 428, 429, 754. So the band can move.
* Objects can only be appended at the top index (`insert._append_object`), and object 50 follows
  the band, so the band cannot grow in place: `add_screen` rebuilds it at the top.
"""
from __future__ import annotations

import struct

from . import guidata, layout, memo, navmap, options, theme

SCREEN_OFFSET = 0x20A               # state+0x20A: the screen id (u8)
_LOADS = (0xF89, 0xF99)             # ldrb.w / ldrsb.w Rt,[Rn,#imm12] first halfword >> 4
_STRB_W = 0xF88


def _guicode(data: bytes) -> tuple[bytes, int]:
    sec = theme.guicode_section(data)
    return bytes(data[sec.offset:sec.offset + sec.length]), theme.guicode_base(sec)


def _imm12_sites(code: bytes, base: int, ops: tuple, imm: int) -> list[tuple[int, int]]:
    out = []
    for o in range(0, len(code) - 3, 2):
        h1, h2 = struct.unpack_from("<HH", code, o)
        if h1 >> 4 in ops and h2 & 0xFFF == imm:
            out.append((base + o, h2 >> 12))
    return out


def _mov_imm_before(code: bytes, base: int, o: int, rt: int) -> int | None:
    """The immediate of the last `movs`/`mov`/`mov.w`/`movw rt,#imm` that decodes cleanly up to
    `o` from up to 16 bytes back; None when the stored register comes from memory."""
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB

    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    want = "r%d, #" % rt
    for back in range(2, 18, 2):
        val = None
        for ins in md.disasm(code[o - back:o], base + o - back):
            if ins.mnemonic in ("movs", "mov", "mov.w", "movw") and ins.op_str.startswith(want):
                val = int(ins.op_str.split("#")[1], 0)
        if val is not None:
            return val
    return None


@memo.content_cached()
def screen_id_readers(data: bytes) -> tuple[int, ...]:
    """Every GuiCode address that loads the screen id (`ldrb.w`/`ldrsb.w Rt,[Rn,#0x20A]`)."""
    code, base = _guicode(data)
    return tuple(a for a, _rt in _imm12_sites(code, base, _LOADS, SCREEN_OFFSET))


@memo.content_cached()
def stored_screen_ids(data: bytes) -> frozenset:
    """Every screen id GuiCode writes to `state+0x20A` as a constant (a `movs`/`mov` of the stored
    register within the 8 halfwords before the `strb.w`), every navigation route's screen, and
    every id the screen switch already matches."""
    code, base = _guicode(data)
    ids = set()
    for addr, rt in _imm12_sites(code, base, (_STRB_W,), SCREEN_OFFSET):
        o = addr - base
        v = _mov_imm_before(code, base, o, rt)
        if v is not None:
            ids.add(v)
    ids |= {r.screen for r in navmap.routes(data)}
    ids |= set(layout.screen_object_map(data))
    return frozenset(ids)


def free_screen_id(data: bytes) -> int:
    """The lowest screen id (0..255) nothing writes, routes to or matches."""
    used = stored_screen_ids(data)
    return next(i for i in range(256) if i not in used)


@memo.content_cached()
def free_action_leaves(data: bytes) -> tuple[int, ...]:
    """ACTION ids whose dispatcher leaf stores a screen id, that have exactly one leaf, whose
    store no other id shares, and that no wt-12 region in ANY object carries -- leaves a new
    control can take and `navicon.retarget_action_leaf` can repoint."""
    gs, ge = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    carried = set()
    for obj in range(len(table)):
        try:
            recs = options._own_records(data, gs, ge, table, obj)
        except Exception:                         # an object that does not decode carries none
            continue
        for rec in recs:
            if rec.wtype == 12:
                carried.add(struct.unpack_from("<H", data, rec.end - guidata.GROUP_REFERENCE_TAIL)[0])
    routes = navmap.routes(data)
    by_id: dict[int, list] = {}
    for r in routes:
        by_id.setdefault(r.touch_id, []).append(r)
    stores: dict[int, set] = {}
    for r in routes:
        stores.setdefault(r.store_at, set()).add(r.touch_id)
    return tuple(sorted(t for t, hs in by_id.items()
                        if len(hs) == 1 and t not in carried and stores[hs[0].store_at] == {t}))
