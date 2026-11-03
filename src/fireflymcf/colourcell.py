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
   in the image reaches (`sramreach.free_runs`), typed 10, as stock's 152 colour rows are; the
   type makes `sramreach` count all 4 bytes as taken, so later allocations avoid them.
3. **Check** the result: the object decodes with the field on the new row and every other byte
   of every record unchanged, and the row holds the reserved address and type 10.

**What the cell holds at power-on: garbage.** The C runtime initialises SRAM only up to
`0x2000AD98` (`raminit.py`); the cell is far above it, and nothing in stock writes it. A tick
routine must store a colour into it on every tick, in every branch (the alarm colour, or
`ColourCell.before_word` / the word at `before_cell_addr` to restore). That is enough for the
control never to draw garbage, by this argument (*inference* from *proven-from-code* facts, not
checked on the coach): the tick chain runs inside `GuiCode_entry1`, before `FUN_00247BF8`, and
`FUN_00247BF8`'s theme-init is the only writer of the stock chrome colour cells, which live in the
same uninitialised SRAM (Finding 18). A page drawn before the first tick would draw garbage chrome;
the panel's pages draw their theme, so no page is drawn before the chain's first run, and the
routine's store precedes the control's first draw. In the emulator the outline shows the
routine's colour in the first screenshot of HOME (`tests/test_colourcell.py`, *emulator*).

**When the new colour shows.** A write to the cell repaints nothing by itself: the panel repaints
a control when a cell it watches changes or its page is drawn, and no container watches a colour
row. Drive the colour from the same input that repaints the control (a tank gauge repaints when its
level cell changes), and read that input from its receive slot, which the routine sees before
`FUN_00247BF8` copies it on, so the colour is written no later than the tick that repaints
(*inference*; *emulator*: the fresh gauge's outline turns red and back with its level).

Refusals (`ColourCellError.code`): BAD_OBJECT, NO_COLOUR_FIELD, AMBIGUOUS_RECORD,
UNSUPPORTED_FIELD, UNSUPPORTED_MODE, STATEFUL_RECORD, INHERITED_COLOUR, FIRMWARE_MISMATCH,
NO_DISPLAY_CELL, BAD_EDIT.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

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
COLOUR_CELL_TYPE = 10           # stock's colour rows are type 10 (152 of 665 on 13.6)
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
    this check does not walk, so it refuses too."""
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
    return None


def own_colour_cell(data: bytes, object_index: int, field: str, *, record: int | None = None,
                    expect: bytes | None = None) -> tuple[bytes, ColourCell]:
    """Give `field` ("border" or "fill") of one record of object `object_index` a private mode-7
    colour cell; return the new image and a `ColourCell`. See the module docstring."""
    if field not in FIELDS:
        raise ColourCellError("UNSUPPORTED_FIELD", f"field {field!r} is not one of {FIELDS}")
    data = bytes(data)
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

    # 1. the field, on the row the grow will add (`allocate_fresh_cells` appends at row_count)
    try:
        _cb, _tb, rows = readout.resolve_cell_tables(data)
    except readout.ReadoutError as e:
        raise ColourCellError("NO_DISPLAY_CELL", str(e)) from e
    try:
        new_frag = pagedraw._set_mode(frag, role, 7, struct.pack("<H", rows))
    except pagedraw.PageDrawError as e:
        raise ColourCellError("BAD_EDIT", str(e)) from e
    first = recs[0].offset
    if any(r.offset != (recs[k - 1].end if k else first) for k, r in enumerate(recs)) \
            or data[first - 1] != len(recs):
        raise ColourCellError("BAD_EDIT", f"object {object_index}'s records are not one "
                              "contiguous stream after its count byte")
    body = (bytes([len(recs)]) + data[first:rec.offset] + new_frag
            + data[rec.end:recs[-1].end])
    try:
        out, _ = relocate.replace_object_records(data, object_index, body)
    except relocate.RelocationError as e:
        raise ColourCellError("BAD_EDIT", str(e)) from e

    # 2. the row
    try:
        out, cells, _ = readout.allocate_fresh_cells(out, [(WIDTH, COLOUR_CELL_TYPE)])
    except readout.ReadoutError as e:
        raise ColourCellError("NO_DISPLAY_CELL", str(e)) from e
    (cell,) = cells
    if cell.slot != rows:
        raise ColourCellError("BAD_EDIT", f"the grown row is {cell.slot}, not the {rows} the "
                              "field was written for")

    # 3. check
    after = _object_records(out, object_index)
    ok = len(after) == len(recs) and all(
        bytes(out[a.offset:a.end]) == (new_frag if k == i else bytes(data[b.offset:b.end]))
        for k, (a, b) in enumerate(zip(after, recs)))
    if not ok or (role, 7, rows) not in after[i].colors:
        raise ColourCellError("BAD_EDIT", f"object {object_index} did not decode back with "
                              f"{role} on row {rows} and its other bytes unchanged")
    gs, _ = guidata.guidata_extent(out)
    got = struct.unpack_from("<I", out, gs + celltables.cell_table_off(out) + 4 * rows)[0]
    typ = out[gs + celltables.type_table_off(out) + rows]
    if (got, typ) != (cell.addr, COLOUR_CELL_TYPE) or cell.addr % 4:
        raise ColourCellError("BAD_EDIT", f"row {rows} holds {got:#x} type {typ}, not "
                              f"{cell.addr:#x} type {COLOUR_CELL_TYPE}")
    rgb = None if before_word is None else ((before_word >> 16) & 0xFF,
                                            (before_word >> 8) & 0xFF, before_word & 0xFF)
    return out, ColourCell(rows, cell.addr, WIDTH, ENCODING, rgb, before_word, mode,
                           before_cell, object_index, i, role)
