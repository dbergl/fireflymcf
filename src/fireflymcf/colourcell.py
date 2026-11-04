"""Give one record's colour field a private run-time colour cell.

A colour field in mode 7 names a row of the display-cell table (`DAT_002535B4`, stock GuiData
`+0x44BC8`); the row holds an SRAM address, and the renderer paints the u32 it finds there as
RGB888 `0x00RRGGBB` (`GUICODE.md` Finding 18, *proven-from-code*: the theme-init stores such
words into these cells). A field in mode 2 (white), 3 (inline) or 6 (palette) is a constant. So
to drive one control's colour at run time, the field must be mode 7 on a row nothing else names.

`own_colour_cell` does that in three steps, each the library's own:

1. **Rewrite the field** to mode 7 on the next row the table will get (`pagedraw._set_mode`, the
   self-decoding field rewrite the authored pages use), and put the object's new record stream
   back at its own index (`relocate.replace_object_records`, the in-place object swap
   `add_batt_heat_battery_glyph` uses). A mode-2 field grows the record by 2 bytes, a mode-3
   field shrinks it by 1; every downstream reference is repaired and the relocator's gates run.
2. **Grow the cell table by one row** (`readout.allocate_fresh_cells`, the grow
   `add_voltage_battery` uses for its colour word, *hardware*: flashed and verified 2026-09-12).
   It relocates the cell table and its type table to the end of GuiData, repoints every GuiCode
   literal that loads their bases and rebumps every index-bound `cmp.w`, so the call changes
   **GuiCode and GuiData**. The new row points at 4 fresh, 4-aligned bytes of SRAM that nothing
   in the image reaches (`sramreach.free_runs`). Its type byte is 10. The panel never reads it
   on the colour path (below); it matters only to this library's SRAM reach, where type 10 is
   4 bytes wide (`sramreach._TYPE_WIDTH`), so later allocations avoid all 4 bytes.
3. **Check** the result: the object decodes with the field on the new row and every other byte
   of every record unchanged, and the row holds the reserved address and type 10.

**How the panel reads a mode-7 colour** (*proven-from-code*, 13.6 GuiCode `0x2533B2`-`0x2533D6`,
inside the record decoder `FUN_00252CB0`, the fillA case): it reads the field's u16 row
(`bl 0x252128`; `uxth r0,r0`), clamps it (`cmp.w r0,#0x298`; `bls`, else `movs r0,#0`: row 0),
loads the row's pointer from the table (`ldr r3,[pc]` = `DAT_002535B4`, `0x90044BC8` on stock;
`ldr.w r3,[r3,r0,lsl #2]`), takes the default colour literal at `0x2535AC` if the pointer is
null (`cbnz r3`), and otherwise reads the u32 at it (`ldr r2,[r3]`) into the render state's
fillA slot (`str r2,[r3,#0x64]`). The type table is never read on this path. The `cmp.w` is one
of the bound sites the grow rebumps, and the literal is one of the base literals it repoints.

**What the cell holds at power-on: garbage.** The C runtime initialises SRAM only up to
`0x2000AD98` (`raminit.py`); the cell is far above it, and nothing in stock writes it. A tick
routine must write it on every tick, in this shape: at entry, unconditionally store
`ColourCell.before_word` (or the word at `before_cell_addr`); then, when its condition holds,
store the new colour over it. That is enough for the
control never to draw garbage, by this argument (*inference* from *proven-from-code* facts, not
checked on the coach): the tick chain runs inside `GuiCode_entry1`, before `FUN_00247BF8`, and
`FUN_00247BF8`'s theme-init is the only writer of the stock chrome colour cells, which live in the
same uninitialised SRAM (Finding 18). A page drawn before the first tick would draw garbage chrome;
the panel's pages draw their theme, so no page is drawn before the chain's first run, and the
routine's store precedes the control's first draw. In the emulator a cell seeded with magenta
before the first tick never draws magenta, and the outline shows the routine's colour in the
first screenshot of HOME (`tests/test_colourcell.py`, *emulator*).

**When the new colour shows.** A write to the cell repaints nothing by itself: the live redraw
(`FUN_002588A8`, `GUICODE.md` Finding 87) repaints an enrolled widget when one of ITS OWN record's
mode-7 colour rows changes, or its value bytes, and the field rewritten here belongs to a record
inside the object, not to the enrolled container that draws it. So without more the new colour
shows at the control's next repaint: a change of the value its container watches (a tank gauge's
level) or a page draw (*emulator*). With `repaint=True` the object's enrolled containers watch the
cell too (`repaint.repaint_on_cell`), and the store of a new colour repaints it in place in the
same tick's live redraw (*proven-from-code*; *emulator*: `tests/test_repaint.py`).

Refusals (`ColourCellError.code`): BAD_OBJECT, NO_COLOUR_FIELD, AMBIGUOUS_RECORD,
UNSUPPORTED_FIELD, UNSUPPORTED_MODE, STATEFUL_RECORD, INHERITED_COLOUR, FIRMWARE_MISMATCH,
NO_DISPLAY_CELL, BAD_EDIT, GUIDATA_TOO_LARGE (GuiData would end past the flash ceiling,
`limits.GUIDATA_END_MAX`), and with `repaint` `repaint.RepaintError`'s codes.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, replace

from . import celltables, guidata, pagedraw, readout, records, relocate, render, theme

# Which colour field a widget type draws as what (`GUICODE.md` Finding 28, `FUN_00256C54`'s box
# dispatch, *proven-from-code*): fillA is the border and fillB the interior of the rounded box
# (wt 3) and the tank well (wt 32); the wt-33 panel/button is one solid rect in fillA.
FIELD_ROLES = {
    3: {"border": "fillA", "fill": "fillB"},
    32: {"border": "fillA", "fill": "fillB"},
    33: {"fill": "fillA"},
}
FIELDS = ("border", "fill")
# The row's type byte. The panel's mode-7 colour read never looks at it (GuiCode
# 0x2533B2-0x2533D6, see the module docstring); 10 is 4 bytes wide in `sramreach._TYPE_WIDTH`,
# so the library's SRAM reach counts the whole colour word as taken.
COLOUR_CELL_TYPE = 10
WIDTH = 4                       # a u32 word, `str`
ENCODING = "rgb888"             # 0x00RRGGBB
WHITE = 0xFFFFFF                # mode 2: the decoder's hardcoded white (Finding 28)
# Modes a field's colour can be read from (and so restored to): 2 white, 3 inline, 6 palette,
# 7 a row. 0/4/5 inherit (Finding 29), 1 is the theme default, which nothing decodes.
_CONSTANT_MODES = (2, 3, 6, 7)


class ColourCellError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ColourCell:
    """What a tick routine needs to drive the cell, and what it changed."""
    cell_index: int                 # the new cell-table row the field names (mode 7)
    addr: int                       # the SRAM word a routine writes
    width: int                      # 4: store with `str`
    encoding: str                   # "rgb888": the word is 0x00RRGGBB
    before: tuple | None            # (r, g, b) the field drew before, or None (a run-time row)
    before_word: int | None         # the same as one 0x00RRGGBB word
    before_mode: int                # the field's mode before (2, 3, 6 or 7)
    before_cell_addr: int | None    # mode 7 only: the SRAM word of the row it named before
    object_index: int
    record: int                     # the record's index within the object
    role: str                       # the record's colour field ("fillA", "fillB")
    initialised_at_boot: bool = False   # never: the routine must write it every tick
    repaint: object = None          # `repaint.Repaint` when the request asked for it: the
                                    # object's enrolled containers watch this cell, so a store
                                    # of a different colour repaints it in place


def _object_records(data: bytes, object_index: int):
    table = guidata.object_tables(data)[relocate.OBJECT_TABLE_INDEX]
    if not isinstance(object_index, int) or isinstance(object_index, bool) \
            or not 0 <= object_index < len(table):
        raise ColourCellError("BAD_OBJECT", f"object index {object_index!r} is outside the "
                              f"object table (0..{len(table) - 1})")
    gs, _ = guidata.guidata_extent(data)
    return records.object_records(data, gs + table[object_index])


def _pick_record(recs, object_index: int, field: str, record: int | None) -> int:
    if record is not None:
        if not isinstance(record, int) or isinstance(record, bool) \
                or not 0 <= record < len(recs):
            raise ColourCellError("NO_COLOUR_FIELD", f"object {object_index} has records "
                                  f"0..{len(recs) - 1}, not {record!r}")
        roles = FIELD_ROLES.get(recs[record].wtype)
        if roles is None or field not in roles:
            raise ColourCellError(
                "UNSUPPORTED_FIELD", f"object {object_index} record {record} is wtype "
                f"{recs[record].wtype}, which has no {field!r} field this primitive drives "
                f"(supported: {_supported()})")
        return record
    hits = [i for i, r in enumerate(recs) if field in FIELD_ROLES.get(r.wtype, {})]
    if not hits:
        raise ColourCellError(
            "NO_COLOUR_FIELD", f"object {object_index} has no record with a {field!r} field "
            f"(record types {[r.wtype for r in recs]}; supported: {_supported()})")
    if len(hits) > 1:
        raise ColourCellError(
            "AMBIGUOUS_RECORD", f"object {object_index} has a {field!r} field on records "
            f"{hits} (types {[recs[i].wtype for i in hits]}); pass record=")
    return hits[0]


def _supported() -> str:
    return "; ".join(f"wtype {t}: {', '.join(sorted(f))}" for t, f in sorted(FIELD_ROLES.items()))


def _before(data: bytes, mode: int, value) -> tuple[int | None, int | None]:
    """(word, old row's SRAM address) for a field in `mode` carrying `value`."""
    if mode == 2:
        return WHITE, None
    if mode == 3:
        return value, None
    if mode == 6:
        r, g, b = render._guidata_palette(data)[value]
        return (r << 16) | (g << 8) | b, None
    gs, _ = guidata.guidata_extent(data)
    if not 0 <= value < celltables.row_count(data):
        raise ColourCellError("UNSUPPORTED_MODE", f"the field names row {value}, outside the "
                              f"cell table ({celltables.row_count(data)} rows)")
    addr = struct.unpack_from("<I", data, gs + celltables.cell_table_off(data) + 4 * value)[0]
    rgb = theme.mode7_palette(data).get(value)
    word = None if rgb is None else (rgb[0] << 16) | (rgb[1] << 8) | rgb[2]
    return word, addr


def _inheritor(recs, start: int, role: str) -> str | None:
    """Why a later record of the object would take this field's colour, or None. The colour slots
    persist across records (Finding 29): a later field in mode 0, 4 (or 5 in block A) keeps the
    slot's value, so changing this field would change it too. The scan stops at the first later
    record that sets `role` explicitly; a container or switch before that draws a child object
    this check does not walk, so it refuses too. So does reaching the end of the object: the
    state is one for the whole page walk (Finding 29, corrected 2026-11-02), and what the page
    draws after this object depends on its parent, which this check does not prove."""
    inherit = {0, 4} | ({5} if role in ("fillA", "fillA2") else set())
    for j in range(start + 1, len(recs)):
        r = recs[j]
        if r.child_index is not None:
            return (f"record {j} (wtype {r.wtype}) draws child object {r.child_index} before "
                    f"any record sets {role} again")
        mode = next((m for ro, m, _v in r.colors if ro == role), None)
        if mode is None:
            continue
        if mode in inherit:
            return f"record {j} (wtype {r.wtype}) inherits {role} (mode {mode})"
        return None
    return (f"the object ends before any later record sets {role} again, so whatever the page "
            "draws next could inherit it (one colour state for the whole walk, Finding 29)")


def _headroom(data: bytes) -> str:
    """GuiData's tail headroom, for a refusal: how many bytes an append can still add."""
    from . import coverage
    try:
        room = coverage.analyse(data, deep=True).tail_headroom
    except Exception as e:  # noqa: BLE001 - only a message
        return f"GuiData's tail headroom could not be measured ({e})"
    return (f"GuiData's tail headroom is {room} bytes (the coverage gate's bound on what an "
            f"append may add after the last object)")


def _relocation_failed(code: str, data: bytes, what: str, e: Exception) -> ColourCellError:
    return ColourCellError(code, f"{what} failed: {e}; {_headroom(data)}")


@dataclass
class _Plan:
    object_index: int
    record: int
    role: str
    mode: int
    before_word: int | None
    before_cell: int | None
    row: int
    new_frag: bytes


def _plan(data: bytes, object_index, field, record, expect, row: int) -> _Plan:
    """Check one request against `data` and build its record's rewrite onto `row`."""
    if field not in FIELDS:
        raise ColourCellError("UNSUPPORTED_FIELD", f"field {field!r} is not one of {FIELDS}")
    recs = _object_records(data, object_index)
    i = _pick_record(recs, object_index, field, record)
    rec = recs[i]
    role = FIELD_ROLES[rec.wtype][field]
    frag = bytes(data[rec.offset:rec.offset + rec.size])
    if expect is not None and bytes(expect) != frag:
        raise ColourCellError(
            "FIRMWARE_MISMATCH", f"this op does not match your firmware: object {object_index} "
            f"record {i} holds {frag.hex()}, the op expects {bytes(expect).hex()}")
    colour = next(((m, v) for ro, m, v in rec.colors if ro == role), None)
    if colour is None:
        raise ColourCellError(
            "UNSUPPORTED_FIELD", f"object {object_index} record {i} (wtype {rec.wtype}) carries "
            f"no {role} field (its colour-mode bytes gate it off: {rec.colors})")
    mode, value = colour
    if mode not in _CONSTANT_MODES:
        raise ColourCellError(
            "UNSUPPORTED_MODE", f"object {object_index} record {i} {role} is mode {mode} "
            "(0/4/5 inherit a sibling's colour, 1 is the theme default); only modes 2, 3, 6 "
            "and 7 have a colour to restore")
    if rec.value_slot is not None:
        raise ColourCellError(
            "STATEFUL_RECORD", f"object {object_index} record {i} watches value slot "
            f"{rec.value_slot}: its on/off state selects the fillA2/fillB2 pair (Finding 28), "
            f"so its {role} is not the colour it always draws")
    why = _inheritor(recs, i, role)
    if why is not None:
        raise ColourCellError(
            "INHERITED_COLOUR", f"object {object_index} record {i} {role}: {why}, so the new "
            "cell would recolour it too")
    before_word, before_cell = _before(data, mode, value)
    try:
        new_frag = pagedraw._set_mode(frag, role, 7, struct.pack("<H", row))
    except pagedraw.PageDrawError as e:
        raise ColourCellError("BAD_EDIT", str(e)) from e
    return _Plan(object_index, i, role, mode, before_word, before_cell, row, new_frag)


def _swap_record(data: bytes, plan: _Plan) -> bytes:
    """`data` with `plan`'s record rewritten, the object put back at its own index."""
    recs = _object_records(data, plan.object_index)
    rec = recs[plan.record]
    first = recs[0].offset
    if any(r.offset != (recs[k - 1].end if k else first) for k, r in enumerate(recs)) \
            or data[first - 1] != len(recs):
        raise ColourCellError("BAD_EDIT", f"object {plan.object_index}'s records are not one "
                              "contiguous stream after its count byte")
    body = (bytes([len(recs)]) + data[first:rec.offset] + plan.new_frag
            + data[rec.end:recs[-1].end])
    try:
        out, _ = relocate.replace_object_records(data, plan.object_index, body)
    except relocate.RelocationError as e:
        raise _relocation_failed("BAD_EDIT", data, f"putting object {plan.object_index} back",
                                 e) from e
    after = _object_records(out, plan.object_index)
    ok = len(after) == len(recs) and all(
        bytes(out[a.offset:a.end]) == (plan.new_frag if k == plan.record
                                       else bytes(data[b.offset:b.end]))
        for k, (a, b) in enumerate(zip(after, recs)))
    if not ok or (plan.role, 7, plan.row) not in after[plan.record].colors:
        raise ColourCellError("BAD_EDIT", f"object {plan.object_index} did not decode back "
                              f"with {plan.role} on row {plan.row} and its other bytes "
                              "unchanged")
    return out


def own_colour_cells(data: bytes, requests, *, repaint: bool = False
                     ) -> tuple[bytes, list[ColourCell]]:
    """See `_own_colour_cells`; the flash ceiling's refusal is GUIDATA_TOO_LARGE."""
    from . import limits
    from . import repaint as _repaint
    try:
        return _own_colour_cells(data, requests, repaint)
    except limits.GuiDataTooLarge as e:
        raise ColourCellError(limits.CODE, str(e)) from e
    except _repaint.RepaintError as e:
        raise ColourCellError(e.code, str(e)) from e


def _own_colour_cells(data: bytes, requests, repaint_all: bool = False
                      ) -> tuple[bytes, list[ColourCell]]:
    """Give each request's field a private mode-7 colour cell, with ONE grow of the cell table
    for all of them; return the new image and a `ColourCell` per request, in order.

    A request is `[object_index, field]`, `[object_index, field, record]`, or a dict with keys
    `object`, `field` and optionally `record`, `expect` and `repaint` (`own_colour_cell`'s
    arguments; `repaint` defaults to the call's). A request with `repaint` also has the object's
    enrolled containers watch its cell (`repaint.repaint_on_cell`), so a routine's store of a new
    colour repaints the object in place; without it the image is byte-identical to before the
    option existed. Every request is checked before anything changes; the same field twice is
    refused. One
    grow costs GuiData about 5 bytes per stock row plus 5 per new row, once, where one
    `own_colour_cell` call per field pays the whole table each time."""
    data = bytes(data)
    if not isinstance(repaint_all, bool):
        raise ColourCellError("BAD_EDIT", f"repaint {repaint_all!r} is not a bool")
    reqs, wants = [], []
    for q in list(requests):
        if isinstance(q, dict):
            unknown = set(q) - {"object", "field", "record", "expect", "repaint"}
            if unknown or "object" not in q or "field" not in q:
                raise ColourCellError("BAD_EDIT", f"request {q!r}: want object, field and "
                                      "optionally record, expect and repaint")
            rp = q.get("repaint", repaint_all)
            if not isinstance(rp, bool):
                raise ColourCellError("BAD_EDIT", f"request {q!r}: repaint is not a bool")
            reqs.append((q["object"], q["field"], q.get("record"), q.get("expect")))
            wants.append(rp)
        elif isinstance(q, (list, tuple)) and len(q) in (2, 3):
            reqs.append((q[0], q[1], q[2] if len(q) == 3 else None, None))
            wants.append(repaint_all)
        else:
            raise ColourCellError("BAD_EDIT", f"request {q!r} is not [object, field(, record)] "
                                  "or a dict")
    if not reqs:
        raise ColourCellError("BAD_EDIT", "no requests")
    try:
        _cb, _tb, rows = readout.resolve_cell_tables(data)
    except readout.ReadoutError as e:
        raise ColourCellError("NO_DISPLAY_CELL", str(e)) from e

    # 1. check every request on the input, then rewrite each field onto the row the grow will
    #    add for it (`allocate_fresh_cells` appends rows at row_count, in spec order)
    plans = [_plan(data, oi, f, r, x, rows + k) for k, (oi, f, r, x) in enumerate(reqs)]
    from . import repaint as _repaint
    for p, rp in zip(plans, wants):
        if rp:      # refuse before anything changes (a field this call rewrites is mode 2, 3,
            _repaint.plan(data, p.object_index)     # 6 or 7 before and after: set either way)
    seen = set()
    for p in plans:
        key = (p.object_index, p.record, p.role)
        if key in seen:
            raise ColourCellError("BAD_EDIT", f"object {p.object_index} record {p.record} "
                                  f"{p.role} is requested twice")
        seen.add(key)
    # re-planned on the image as it stands, so two fields of one record compose
    # (the colour each field had is the input's, kept from the first pass)
    out = data
    for k, (oi, f, r, _x) in enumerate(reqs):
        again = _plan(out, oi, f, r, None, rows + k)
        plans[k].new_frag = again.new_frag
        out = _swap_record(out, plans[k])

    # 2. the rows, in one grow
    try:
        out, cells, _ = readout.allocate_fresh_cells(out, [(WIDTH, COLOUR_CELL_TYPE)] * len(plans))
    except readout.ReadoutError as e:
        raise _relocation_failed("NO_DISPLAY_CELL", out, "growing the cell table", e) from e
    except relocate.RelocationError as e:
        raise _relocation_failed("NO_DISPLAY_CELL", out, "growing the cell table", e) from e
    if [c.slot for c in cells] != [p.row for p in plans]:
        raise ColourCellError("BAD_EDIT", f"the grown rows are {[c.slot for c in cells]}, not "
                              f"the {[p.row for p in plans]} the fields were written for")

    # 3. check the rows
    gs, _ = guidata.guidata_extent(out)
    result = []
    for p, cell in zip(plans, cells):
        got = struct.unpack_from("<I", out, gs + celltables.cell_table_off(out) + 4 * p.row)[0]
        typ = out[gs + celltables.type_table_off(out) + p.row]
        if (got, typ) != (cell.addr, COLOUR_CELL_TYPE) or cell.addr % 4:
            raise ColourCellError("BAD_EDIT", f"row {p.row} holds {got:#x} type {typ}, not "
                                  f"{cell.addr:#x} type {COLOUR_CELL_TYPE}")
        w = p.before_word
        rgb = None if w is None else ((w >> 16) & 0xFF, (w >> 8) & 0xFF, w & 0xFF)
        result.append(ColourCell(p.row, cell.addr, WIDTH, ENCODING, rgb, w, p.mode,
                                 p.before_cell, p.object_index, p.record, p.role))
    addrs = [a for c in result for a in range(c.addr, c.addr + WIDTH)]
    if len(set(addrs)) != len(addrs):
        raise ColourCellError("BAD_EDIT", "two cells share SRAM")

    # 4. the repaint watches, on the grown image (the rows exist now)
    for k, rp in enumerate(wants):
        if rp:
            out, bound = _repaint._repaint_on_cell(out, result[k].object_index,
                                                   result[k].cell_index)
            if bound.addr != result[k].addr:
                raise ColourCellError("BAD_EDIT", f"row {bound.cell_index} watched at "
                                      f"{bound.addr:#x}, not the cell {result[k].addr:#x}")
            result[k] = replace(result[k], repaint=bound)
    return out, result


def own_colour_cell(data: bytes, object_index: int, field: str, *, record: int | None = None,
                    expect: bytes | None = None, repaint: bool = False
                    ) -> tuple[bytes, ColourCell]:
    """Give `field` ("border" or "fill") of one record of object `object_index` a private mode-7
    colour cell; return the new image and a `ColourCell`. See the module docstring."""
    out, (cell,) = own_colour_cells(data, [{"object": object_index, "field": field,
                                            "record": record, "expect": expect,
                                            "repaint": repaint}])
    return out, cell
