"""Make an object repaint, in place, when a display cell changes.

The panel repaints a drawn control without a page draw only through its **live-redraw list**:
the enrolled records of the page now showing, each re-checked every GUI tick. This module puts a
display cell of the caller's choosing on that list for one object, so that a tick routine's store
to the cell is what repaints the object: the next tick after the cell's u32 changes, the object
is drawn again, whole, from its records. Nothing else changes: no slot, no cell another widget
reads, no real value.

**The mechanism** (*proven-from-code*, 13.6 GuiCode; every address below is an instruction):

1. **When it runs.** `GuiCode_entry1` (`0x24EE28`) calls the per-tick hook at `0x24EE3C`
   (`bl 0x247BF8`, the chain every user tick routine is linked into) and then, in the same call,
   either composes a new page (`0x24EE8C bl 0x258650`) or runs the live redraw
   (`0x24EEB8 bl 0x2588A8`). A routine's stores are complete before the check reads them: one
   function, one task, no window between them.
2. **What it checks.** `FUN_002588A8` walks the enrolled widgets (`0x2588BE bl 0x256264`, the
   first; `0x258978 bl 0x258550`, the next) and asks `FUN_002568A0` of each (`0x25891E`); on a
   non-zero answer it restores the widget's saved render state (`0x258928 bl 0x256720`;
   `0x258934 bl 0x2591F2`, 0x98 bytes) and draws it again (`0x2588E6 bl 0x256278`,
   `0x258902 bl 0x256C54` with `r0 = 2`).
3. **What changes count.** `FUN_002568A0` calls the change-detect read `FUN_00256868` four
   times (`0x2568F2`, `0x256910`, `0x256920`, `0x25693A`) on the four display-cell rows the
   widget saved at `+0x138/+0x13A/+0x13C/+0x13E`, against cached words at
   `+0x128/+0x12C/+0x130/+0x134`, and ORs the answers into its result (`orrs r4, r0`). It does
   so for both widget kinds: the value widgets (`ldrb.w r3, [r3, #0x154]`; `cmp r3, #1`), whose
   value-slot compare (`0x256994 bl 0x2591D4`) sets the starting `r4`, branch back to `0x2568D2`
   for the same four reads. `FUN_00256868` (`0x256868`) returns 0 for row `0xFFFF`
   (`cmp r1, r3`; `beq`) or a row past the table's bound (`cmp.w r1, #0x298`; `bhi`, a site
   the cell-table grow rebumps), loads the row's SRAM pointer (`ldr.w r3, [r3, r1, lsl #2]`),
   reads the u32 there (`ldr r3, [r3]`), and on a difference stores it to the cache
   (`str r3, [r0]`) and returns 1.
4. **Which rows a widget watches: its own record's mode-7 colour rows.** The record decoder
   `FUN_00252CB0` resets each colour field's row to `0xFFFF` before decoding it (fillA:
   `0x25333E movw r2, #0xffff`; `0x253342 strh.w r2, [r3, #0x74]`) and writes the row only in
   the mode-7 case (fillA: `0x2533B4 bl 0x252128` reads the u16, `0x2533D8 strh.w r0,
   [r3, #0x74]`); the other three fields keep theirs at `+0x76`, `+0x78`, `+0x7A`
   (`0x253402 strh.w r2, [r3, #0x78]` is the next field's reset). The renderer `FUN_00256C54`
   enrols a record whose state flags carry `0x20` (`0x256C98 tst.w r3, #0x20`; `bne 0x256D4E`),
   set by the decoder from header byte 4 bit `0x20`, through `0x256D5C bl 0x256450`, which
   copies 0x98 bytes of render state into the widget (`0x25652A movs r2, #0x98`;
   `0x256530 bl 0x2591F2`): render-state `+0x74..+0x7A` land at widget `+0x138..+0x13E`, and
   the colour words `+0x64..+0x70` (the cache's first values) at `+0x128..+0x134`.

So a record that is enrolled and carries a colour field in mode 7 on row N is repainted
whenever the u32 at row N's SRAM changes. An object is drawn by the **wt-5 containers** whose
`child_index` names it; an enrolled container is a widget whose repaint draws its whole child
(the tank gauges' level changes repaint this way: every stock container of a gauge carries the
enrol bit and watches the level cell through its `value_slot`). `repaint_on_cell` gives each
enrolled container of the object a mode-7 colour field on the caller's row. The cell's u32 then
also sits in that colour field of the render state, from the container's decode until the
child's first record sets the field: so the call requires the child's first record to set the
field itself (mode 1, 2, 3, 6 or 7), and the container's field to be mode 0 (it set nothing)
and its coordinate out-registers to be zero (the decoder copies each colour word and row into a
register slot under 2-bit fields at shifts 2 and 6, `0x252EE2 ands r2, r4, #0xc` ..
`0x252F68`; that these are the out-register bytes is *inference* from the layout `records`
decodes, so the check refuses any out-register). Under those three conditions no drawn
pixel can read the cell's word through the container: the only reader the edit adds is the
change-detect.

**Composing it with a colour cell.** `colourcell.own_colour_cells(..., repaint=True)` (or this
module's `repaint_on_cell(data, object, cell.cell_index)` after `own_colour_cell`) watches the
colour cell itself: the routine's store of a new colour is the store that repaints, so the
documented shape (store the normal colour, then the alarm colour over it when the condition
holds) repaints exactly in the tick where the colour the routine leaves in the cell differs from
the last tick's. The two stores of one tick are not seen apart (point 1). `own_repaint_trigger`
allocates a private cell of its own for a routine that repaints on some other decision: any
change of the word it stores repaints, once.

What it does not do: it repaints only where the object is drawn by an enrolled container
(`NO_LIVE_CONTAINER` otherwise); a page that draws the object through a container without the
enrol bit still shows the new colour at its next draw. The repaint draws the whole object, and
the object's own records erase what they cover, as a level change does.

Refusals (`RepaintError.code`): BAD_OBJECT, BAD_CELL, NO_LIVE_CONTAINER, UNSUPPORTED_CONTAINER
(no colour field the conditions above allow, or out-registers set), CHILD_INHERITS (the
object's first record does not set any field a container could watch), BAD_EDIT,
NO_DISPLAY_CELL (the trigger's grow), GUIDATA_TOO_LARGE.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from . import celltables, guidata, memo, pagedraw, records, relocate

ENROLL_BIT = 0x20                 # header byte 4: the decoder sets render-state flag 0x20
CONTAINER_WTYPE = 5
# The fields a container can watch, in preference order. All four are read in mode 7 by the
# decoder (rows at render-state +0x74/+0x76/+0x78/+0x7A, all four checked by FUN_002568A0).
ROLES = ("fillA", "fillB", "fillA2", "fillB2")
_EXPLICIT = (1, 2, 3, 6, 7)       # modes that set the field (0/4 inherit; 5 is fillB's
                                  # transparency in block B and an inherit in block A)
TRIGGER_TYPE = 10                 # a 4-byte word in `sramreach._TYPE_WIDTH`, as colourcell's


class RepaintError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Watcher:
    """One enrolled container now watching the cell."""
    parent: int                     # the object holding the container record
    record: int                     # the container's index within the parent
    role: str                       # the colour field it watches the cell through


@dataclass(frozen=True)
class Repaint:
    """What `repaint_on_cell` bound: the object repaints when the u32 at `addr` changes."""
    object_index: int
    cell_index: int                 # the display-cell row watched
    addr: int                       # its SRAM word
    watchers: tuple                 # Watcher, one per enrolled container of the object
    skipped: tuple                  # (parent, record) containers of the object without the
                                    # enrol bit: those pages show a change at their next draw


def _table(data: bytes):
    return guidata.object_tables(data)[relocate.OBJECT_TABLE_INDEX]


def _recs(data: bytes, oi: int):
    gs, _ = guidata.guidata_extent(data)
    return records.object_records(data, gs + _table(data)[oi])


def _check_object(data: bytes, object_index) -> None:
    n = len(_table(data))
    if not isinstance(object_index, int) or isinstance(object_index, bool) \
            or not 0 <= object_index < n:
        raise RepaintError("BAD_OBJECT", f"object index {object_index!r} is outside the object "
                           f"table (0..{n - 1})")


def _cell_addr(data: bytes, cell_index) -> int:
    rows = celltables.row_count(data)
    if not isinstance(cell_index, int) or isinstance(cell_index, bool) \
            or not 0 <= cell_index < rows:
        raise RepaintError("BAD_CELL", f"cell row {cell_index!r} is outside the display-cell "
                           f"table (0..{rows - 1})")
    gs, _ = guidata.guidata_extent(data)
    addr = struct.unpack_from("<I", data, gs + celltables.cell_table_off(data) + 4 * cell_index)[0]
    if addr == 0:
        raise RepaintError("BAD_CELL", f"cell row {cell_index} has no SRAM address (null "
                           "pointer): the change-detect read never fires on it")
    return addr


@memo.content_cached
def _container_index(data: bytes) -> dict:
    """{child object: ((parent, record index, enrolled), ...)} over every wt-5 container."""
    out: dict = {}
    for oi in range(len(_table(data))):
        try:
            recs = _recs(data, oi)
        except Exception:  # noqa: BLE001 - an entry that does not decode draws nothing here
            continue
        for k, r in enumerate(recs):
            if r.wtype == CONTAINER_WTYPE and r.child_index is not None:
                out.setdefault(r.child_index, []).append((oi, k, bool(r.header[4] & ENROLL_BIT)))
    return {c: tuple(v) for c, v in out.items()}


def containers(data: bytes, object_index: int) -> list[tuple[int, int, bool]]:
    """Every wt-5 container that draws `object_index`: (parent object, record index, enrolled)."""
    return list(_container_index(data).get(object_index, ()))


def _child_roles(data: bytes, object_index: int) -> list[str]:
    """The roles the object's first record sets itself, in `ROLES` order."""
    recs = _recs(data, object_index)
    if not recs:
        return []
    modes = {ro: m for ro, m, _v in recs[0].colors}
    return [ro for ro in ROLES if modes.get(ro) in _EXPLICIT]


def _pick_role(rec, allowed: list[str], where: str) -> str:
    if any(rec.out_regs):
        raise RepaintError("UNSUPPORTED_CONTAINER", f"{where} stores coordinates to registers "
                           f"(out_regs {rec.out_regs}); the decoder would copy the watched "
                           "colour word into a register slot too (0x252EE0)")
    modes = {ro: m for ro, m, _v in rec.colors}
    for ro in allowed:
        if modes.get(ro) == 0:
            return ro
    raise RepaintError("UNSUPPORTED_CONTAINER", f"{where} has no mode-0 colour field among "
                       f"{allowed} (its fields: {rec.colors})")


def plan(data: bytes, object_index: int) -> list[Watcher]:
    """The watchers `repaint_on_cell` would bind, checked; refuses as it would."""
    _check_object(data, object_index)
    found = containers(data, object_index)
    live = [(p, k) for p, k, e in found if e]
    if not live:
        raise RepaintError(
            "NO_LIVE_CONTAINER", f"object {object_index} is drawn by no enrolled wt-5 container "
            f"(containers: {[(p, k) for p, k, _e in found] or 'none'}): nothing on the "
            "live-redraw list draws it, so no cell change can repaint it in place")
    allowed = _child_roles(data, object_index)
    if not allowed:
        recs = _recs(data, object_index)
        raise RepaintError(
            "CHILD_INHERITS", f"object {object_index}'s first record sets none of {ROLES} itself "
            f"({recs[0].colors if recs else 'no records'}), so a watched field on its container "
            "would reach what it draws")
    return [Watcher(p, k, _pick_role(_recs(data, p)[k], allowed,
                                     f"object {p} record {k} (container of {object_index})"))
            for p, k in live]


def _rewrite(data: bytes, w: Watcher, cell_index: int) -> bytes:
    recs = _recs(data, w.parent)
    rec = recs[w.record]
    frag = bytes(data[rec.offset:rec.end])
    try:
        new = pagedraw._set_mode(frag, w.role, 7, struct.pack("<H", cell_index))
    except pagedraw.PageDrawError as e:
        raise RepaintError("BAD_EDIT", f"object {w.parent} record {w.record}: {e}") from e
    first = recs[0].offset
    if any(r.offset != (recs[k - 1].end if k else first) for k, r in enumerate(recs)) \
            or data[first - 1] != len(recs):
        raise RepaintError("BAD_EDIT", f"object {w.parent}'s records are not one contiguous "
                           "stream after its count byte")
    body = bytes([len(recs)]) + data[first:rec.offset] + new + data[rec.end:recs[-1].end]
    try:
        out, _ = relocate.replace_object_records(data, w.parent, body)
    except relocate.RelocationError as e:
        raise RepaintError("BAD_EDIT", f"putting object {w.parent} back failed: {e}") from e
    after = _recs(out, w.parent)
    ok = len(after) == len(recs) and all(
        bytes(out[a.offset:a.end]) == (new if k == w.record else bytes(data[b.offset:b.end]))
        for k, (a, b) in enumerate(zip(after, recs)))
    if not ok or (w.role, 7, cell_index) not in after[w.record].colors \
            or after[w.record].child_index != rec.child_index \
            or after[w.record].value_slot != rec.value_slot:
        raise RepaintError("BAD_EDIT", f"object {w.parent} did not decode back with record "
                           f"{w.record}'s {w.role} on row {cell_index} and every other byte "
                           "unchanged")
    return out


def repaint_on_cell(data: bytes, object_index: int, cell_index: int) -> tuple[bytes, Repaint]:
    """See `_repaint_on_cell`; the flash ceiling's refusal is GUIDATA_TOO_LARGE."""
    from . import limits
    try:
        return _repaint_on_cell(data, object_index, cell_index)
    except limits.GuiDataTooLarge as e:
        raise RepaintError(limits.CODE, str(e)) from e


def _repaint_on_cell(data: bytes, object_index: int, cell_index: int) -> tuple[bytes, Repaint]:
    """Make every enrolled wt-5 container that draws `object_index` watch display-cell row
    `cell_index` (a mode-7 colour field on the container, module docstring): a change of the u32
    at the row's SRAM word then repaints the object in place, in the next live redraw.

    Each parent's container record grows by 2 bytes and the parent is swapped back in at its
    own index (`relocate.replace_object_records`), which shifts every later GuiData reference,
    GuiCode's literals into GuiData among them: it changes GuiData and GuiCode."""
    data = bytes(data)
    addr = _cell_addr(data, cell_index)
    watchers = plan(data, object_index)
    out = data
    for w in watchers:
        out = _rewrite(out, w, cell_index)
    skipped = tuple((p, k) for p, k, e in containers(out, object_index) if not e)
    return out, Repaint(object_index, cell_index, addr, tuple(watchers), skipped)


@dataclass(frozen=True)
class RepaintTrigger:
    """A private cell whose change repaints an object: store any word at `addr` that differs
    from the last tick's, and the object repaints once."""
    addr: int
    width: int                      # 4: store with `str`
    cell_index: int
    repaint: Repaint
    initialised_at_boot: bool = False   # garbage at power-on; a change from it repaints once


def own_repaint_trigger(data: bytes, object_index: int) -> tuple[bytes, RepaintTrigger]:
    """A new display-cell row on 4 fresh bytes of SRAM (`readout.allocate_fresh_cells`, as
    `colourcell` grows its rows), watched for `object_index` by `repaint_on_cell`. Changes GuiCode
    and GuiData (the grow repoints GuiCode literals)."""
    from . import limits, readout
    data = bytes(data)
    try:
        plan(data, object_index)                # refuse before anything grows
        try:
            out, (cell,), _ = readout.allocate_fresh_cells(data, [(4, TRIGGER_TYPE)])
        except (readout.ReadoutError, relocate.RelocationError) as e:
            raise RepaintError("NO_DISPLAY_CELL", f"growing the cell table failed: {e}") from e
        out, rp = _repaint_on_cell(out, object_index, cell.slot)
    except limits.GuiDataTooLarge as e:
        raise RepaintError(limits.CODE, str(e)) from e
    if rp.addr != cell.addr or cell.addr % 4:
        raise RepaintError("BAD_EDIT", f"row {cell.slot} holds {rp.addr:#x}, not the reserved "
                           f"{cell.addr:#x}")
    return out, RepaintTrigger(cell.addr, 4, cell.slot, rp)
