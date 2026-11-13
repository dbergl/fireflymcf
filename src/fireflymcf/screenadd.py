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

from . import guidata, insert, layout, memo, navmap, options, patch, records, relocate, theme
from . import levelpage as lp
from . import levelsetup as ls
from . import pagedraw as pd

class ScreenAddError(ValueError):
    """A screen could not be added (no free id, an unexpected band or root, a gate refused)."""


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


# The blank root is built from screen 12's root (object 15, `levelsetup.POPUP_ROOT`): its
# full-screen background, header bar and title (records 0-2), and its wt-5 references to the
# header widgets and the nav rail (records 3-4), then the LEVEL page's page-area fill. The title
# record is retitled in place, so the new title has the old one's length (17 characters).
TITLE_INDENT = "   "


def blank_root(data: bytes, title: str) -> bytes:
    """An empty screen's root object (`<count><records>`, padded to 4): screen 12's background,
    header bar and `title`, the header widgets and nav rail, and the navy page area."""
    gs, _ = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    recs = records.object_records(data, gs + table[ls.POPUP_ROOT])
    refs = [recs[i] for i in ls.KEPT_ROOT_RECORDS]
    if [r.child_index for r in refs] != list(ls.ROOT_REFS) or any(r.wtype != 5 for r in refs):
        raise ScreenAddError(f"object {ls.POPUP_ROOT} records {ls.KEPT_ROOT_RECORDS} are not the "
                             f"wt-5 references to {ls.ROOT_REFS}")
    tr = recs[ls.TITLE_RECORD]
    width = len(tr.label or "")
    text = TITLE_INDENT + title
    if width != len(ls.TITLE_STOCK) or len(text) > width:
        raise ScreenAddError(f"title {title!r} does not fit the {width}-character header field")
    parts = []
    for i in ls.BACKGROUND_RECORDS:
        r = recs[i]
        raw = bytearray(data[r.offset:r.end])
        if i == ls.TITLE_RECORD:
            at = r.label_offset - r.offset
            raw[at:at + width] = text.ljust(width).encode("ascii")
        parts.append(bytes(raw))
    parts += [bytes(data[r.offset:r.end]) for r in refs]
    parts.append(pd.fill(data, *lp.LAYOUT["page"], lp.NAVY))
    body = bytes([len(parts)]) + b"".join(parts)
    return body + b"\0" * (-len(body) % 4)


def original_root(data: bytes, obj: int) -> int:
    """The root object a band entry draws: a trampoline's target, or the entry itself."""
    return layout.trampoline_target(data, obj)


def _trampoline(template: bytes, root: int) -> bytes:
    raw = bytearray(template)
    struct.pack_into("<H", raw, records.child_index_offset(template, records.decode_record(template, 0)),
                     root)
    body = bytes([1]) + bytes(raw)
    return body + b"\0" * (-len(body) % 4)


def _switch(data: bytes):
    gs, _ = guidata.guidata_extent(data)
    return layout._screen_switch_record(data, guidata.find_match_table(data), gs)


def add_screen(data: bytes, root_bytes: bytes, screen_id: int | None = None
               ) -> tuple[bytes, int, int]:
    """Add a screen whose root is `root_bytes`. Returns `(image, screen_id, root_object)`.

    The screen switch's band `[child_index, child_index+count)` is rebuilt at the top of object
    table 1: one trampoline per entry (a lone wt-5 reference to the entry's ORIGINAL root, so a
    second call never chains trampolines), in the same order with the same match values (the
    `0xFFFF` default first), then the new root with `screen_id` (default `free_screen_id`). The
    switch's `child_index` and `count` are then repointed. No existing object index changes.
    """
    sw = _switch(data)
    band = guidata.switch_candidates(data, sw)
    if not band or band[0][0] != layout.MATCH_DEFAULT:
        raise ScreenAddError("the screen switch's band does not start with its 0xFFFF default")
    sid = free_screen_id(data) if screen_id is None else screen_id
    if not 0 <= sid <= 0xFF or sid in stored_screen_ids(data):
        raise ScreenAddError(f"screen id {sid} is in use or not a u8")
    if not root_bytes or len(root_bytes) % 4:
        raise ScreenAddError("root object bytes must be non-empty and 4-aligned")

    gs, _ = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    template, trec = insert._container_template(
        data, records.object_records(data, gs + table[ls.POPUP_ROOT]), positioned=False)
    if trec.geom[0] is not None or trec.geom[1] is not None:
        raise ScreenAddError("the wt-5 template carries geometry; a trampoline must not offset")

    values = [v for v, _ in band] + [sid]
    padded = values + ([0xFFFF] if len(values) % 2 else [])
    try:
        # Two entries, then their two objects: `find_match_table` accepts a table at most two
        # entries wider than the object count, so the whole band cannot be grown first.
        objs = [_trampoline(template, original_root(data, o)) for _v, o in band]
        objs.append(bytes(root_bytes))
        out, idxs = data, []
        for k in range(0, len(padded), 2):
            out, _e = relocate.grow_match_table(out, padded[k:k + 2])
            for obj in objs[k:k + 2]:
                out, _e, i = insert._append_object(out, obj)
                idxs.append(i)
    except relocate.RelocationError as e:
        raise ScreenAddError(f"growing the object table failed: {e}") from e
    if idxs != list(range(idxs[0], idxs[0] + len(idxs))):
        raise ScreenAddError(f"the new band did not land contiguously ({idxs})")

    sw2 = _switch(out)
    ci = records.child_index_offset(out, sw2)
    buf = bytearray(out)
    struct.pack_into("<H", buf, ci, idxs[0])
    struct.pack_into("<H", buf, ci - 2, len(values))
    out, _labels = patch.reseal(bytes(buf))
    chk = _switch(out)
    if (chk.child_index, chk.count) != (idxs[0], len(values)):
        raise ScreenAddError("the screen switch did not take the new band")
    return out, sid, idxs[-1]
