"""Approach A compositor: compute absolute screen coordinates from GuiData.

Replays the layout engine `FUN_00257BFC` (per-record pen arithmetic) and `FUN_00258330`
(container recursion), on top of the byte-exact record decoder (`records.py`). Turns an
object-table index into placed widgets, no calibration.

**On "screen root".** The panel does not composite one object per screen. `GuiCode_entry1`
calls the screen-setup `FUN_00258650(index)` on a *small* set of top-level roots — index 0
(the home/background container) plus popup overlays (0x2f2 pairing, 0x1ac/0x1ad, resolved
from the same `+0x46148` table, `DAT_0025887c = 0x90046148`). A page like Climate is *not*
one of those roots: it is a child page selected inside root 0's tree by a runtime switch
(a type-6 value-match list; `guidata.switch_candidates` now expands its candidate children,
and `guidata.reachable_objects` follows them, which is what reaches a page body — obj 7,
CLIMATE, is candidate value 3 of the top screen switch, `MCF-FORMAT.md` §10 item 5). So
`climate_root()` returns the page-content object (obj 7 in 13.6) found by graph reachability
— walking it reproduces the page's *relative* layout (the CLIMATE tab lands at (211,76),
matching the photo), which is what this module is validated on.

**Vertical origin — resolved (GUICODE Finding 14).** The Climate setpoint is a type-0/8 value
widget; `FUN_00257BFC`'s per-type frame switch sets the frame box `+0x3a..+0x40` from the
*rendered content extents* `+0x6c12..+0x6c18` for types 0/8 (`+0x3e = +0x6c16 + 1`), which are
the font cell — so `_update_frame` computes them from the current font's `height`/`top`
(`metrics.font_metrics`) rather than the near-zero geometry box. The current font is
`state.font`, updated from each record's R38 selector (`records.WRecord.font`) and persistent
across records, so a value widget inherits it. And the flow origin `+0x36/+0x38` advances only
for record *types* in the flow-gate mask (`_FLOW_GATE_MASK`, `FUN_00257BFC` line 33) — the
type-9 spacer is excluded, so it no longer resets the setpoint's origin to zero. With both, the
mode grid (Cool/High/Low/Auto, y via base-mode 2) sits below the setpoint (y = 252/300/348/396),
identically across all three builds. The multi-root hypothesis was checked first and ruled out
(obj 7 already carries the whole page).

**The frame switch is now exhaustive (GUICODE Finding 21, render-fidelity Task 1).** Every
`wtype` 0-0x3f is classified against a complete transcription of `FUN_00257BFC`'s switch, not
a "generic box for anything unrecognised" default: point (1), degenerate rect (0x2f), centred
x/y (0x10/0x22, 0x11/0x23), content-extent (0/8), the declared-box generic set
(`_GENERIC_FRAME_TYPES`), and a cited "no case — frame unchanged" set (containers 5/6/0x2e, the
spacer 9, and types the switch's C source has no arm for at all: 0x14-0x1c, 0x1f, 0x25,
0x30-0x3f — none of the last group is ever emitted by any of the three builds, so that branch
is asserted from the disassembly, not exercised by data — a bounded gap, not a fitted default).
The type-0/8 case's x-extent now also replays the *alignment*-adjusted pen (`FUN_002557C0`'s
own formula: align the pen, then add the measured width) via `metrics.text_width`/
`aligned_x0`, whenever the record carries a resolvable label (structurally only type 0 ever
does — type 8 draws a runtime number, never a stored string); when it doesn't, the old
geometry-box approximation remains, a bounded gap, not an invented width.

**Horizontal alignment of a widget's own placement (render-fidelity Task 2).** The above is
the *frame* (what a later sibling references via base-mode 2/3) — a separate question from
where a record's own label actually lands, which is what `render.py` draws. `Screen.
_placed_label_box` answers that for `Placed.x0`/`x1`, generally (not just types 0/8): the align
byte (`records.WRecord.align`, read unconditionally for every record) shifts the anchor pen by
the measured width via the same `metrics.aligned_x0`/`text_width` — left (0/1) is a no-op, so
the declared box comes through unchanged for the ~92% of records that are left-aligned; centre
(2) and right (3) recompute `x0`/`x1` tight around the rendered text. No pixel oracle exists
for width (see `metrics.py`); it is validated by strictly-monotone glyph positions + magnitude,
never tuned to a photo.

Per coordinate (six per record, three x/y pairs):

    coord = static_geometry              # records.WRecord.geom[i]
          + register_file[in_reg]        # a saved position, if the record names one
          + base_reference(mode)         # container / sibling origin (GUICODE Finding 12)

then `register_file[out_reg] = coord` if named, and `+0x36/+0x38` (the flow reference) become
the just-placed x/y so the next record can lay out relative to it. Containers recurse into
`object_table[child_index]`; `FUN_00258330` runs with its save-state flag clear, so children
**share** the parent's flow and register state (they do not get a fresh frame).

`proven-from-code`; validated by the top-level Climate layout matching the panel photo (the
CLIMATE tab decodes to (211,76)).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from . import glyphs, guidata, metrics, records

OBJECT_TABLE = 0x46148          # the 1422-ish entry screen/object table (GUICODE Finding 12)

# FUN_00257BFC line 33: a record participates in the coordinate/flow computation only when its
# type is in this mask (DAT_002582bc, for types 0-31) or is a high type (>= 32). Type 9 (the
# spacer, bit 9 clear) is excluded, so it must not advance the flow origin. `proven-from-code`.
_FLOW_GATE_MASK = 0x600FFDFF

# FUN_00257BFC's per-type frame switch (`state+0x8a`, run after FUN_00256C54 draws the
# record): the exact set of types whose case computes the frame as the plain declared box
# `(x0, y0, x1+1, y1)`, transcribed case-by-case from the decompile. `proven-from-code`
# (`_update_frame`'s docstring lists every case in the switch). Every other type either has
# its own explicit case (0/8, 1, 0x10/0x22, 0x11/0x23, 0x2f) or is absent from the switch
# entirely, in which case the frame is left unchanged.
_GENERIC_FRAME_TYPES = frozenset({
    2, 3, 4, 7, 0xa, 0xb, 0xc, 0xd, 0xe, 0xf, 0x12, 0x13, 0x1d, 0x1e,
    0x20, 0x21, 0x24, 0x26, 0x27, 0x28, 0x29, 0x2a, 0x2b, 0x2c, 0x2d,
})


def _in_flow_gate(wtype: int) -> bool:
    return bool((_FLOW_GATE_MASK >> wtype) & 1) if wtype < 32 else True


# FUN_00257BFC's align block (0x258060-0x258074) selects "path 1" -- the branch that shifts a
# record's own box coords by the align byte -- by two type-bit masks: `(0x600c7c9c & (1<<t))` for
# t<32, or `((1<<(t-32)) & 0x3ff3)` for t>=32 (the same `1<<t` / `1<<(t-32)` type words records.py
# models). Only path-1 types get the box right-justify/centre; others keep their pen box.
_ALIGN_PATH1_LO = 0x600C7C9C
_ALIGN_PATH1_HI = 0x3FF3


def _align_path1(wtype: int) -> bool:
    if wtype < 32:
        return bool((_ALIGN_PATH1_LO >> wtype) & 1)
    return bool((_ALIGN_PATH1_HI >> (wtype - 32)) & 1)


# The decoder (FUN_00252CB0) only writes the persistent align byte (state+9) for record types in
# its write-gate: `(0x600ffdfd & (1<<t))` for t<32, or `((1<<(t-32)) & 0x7fff)` for t>=32
# (0x2531e2 / 0x2531ee). A gated record with a non-zero align sets the persistent value; others
# leave it untouched.
_ALIGN_WRITE_LO = 0x600FFDFD
_ALIGN_WRITE_HI = 0x7FFF


def _writes_align(wtype: int) -> bool:
    if wtype < 32:
        return bool((_ALIGN_WRITE_LO >> wtype) & 1)
    return bool((_ALIGN_WRITE_HI >> (wtype - 32)) & 1)


def _s16(v) -> int:
    if v is None:
        return 0
    return v - 0x10000 if v > 0x7fff else v


@dataclass
class Placed:
    """A leaf widget with absolute screen coordinates."""
    wtype: int
    label: str | None
    x0: int
    y0: int
    x1: int
    y1: int
    depth: int
    obj_index: int
    record_offset: int
    font: int | None = None     # the current font slot (persistent state.font) when placed
    align: int = 0              # text alignment (0 left, 2 centre, 3 right)
    v_align: int | None = None  # multi-line text block v-align in the box (records.py R49 +0x95:
                                # 2 centre, 3 bottom, else top; FUN_002559A0). None if not carried.
    line_height: int | None = None   # multi-line inter-line advance (R49 +0x96)
    line_height_adds_font: int | None = None   # R49 +0x97: add the font cell height to it once
    label_offset: int | None = None  # file offset of the label bytes (WRecord.label_offset) -- lets
                                # a unit widget be identified by its raw char (e.g. the degree 0xb0
                                # temperature unit, which is non-ASCII so `label` is None)
    value_slot: int | None = None    # the variable slot a value/switch widget reads (WRecord.
                                # value_slot) -- e.g. the clock value widget is slot 226 (the
                                # minutes-since-midnight time register), a byte-derived clock id
    coord4: int | None = None   # geometry coord index 4 (WRecord.geom[4]) -- for a tank well
                                # (wt 0x20) this is the border/edge WIDTH the box primitive
                                # strokes in fillA (GUICODE Finding 28); a radius for other types
    hollow: bool = False        # header byte0 bit1: the transparent-interior flag -- the box
                                # primitive skips the interior fill (outline only), e.g. the
                                # CLIMATE box (byte0 0x43). GUICODE Finding 28.
    h_align: int | None = None  # the LABEL's per-line horizontal align (R49 +0x94; 2 centre,
                                # 3 right, else left) -- the real text alignment for a wt-11/13
                                # label, distinct from the header align byte (which positions the
                                # box). GUICODE Finding 26/30. None if the record carries no R49.
    colors: list = field(default_factory=list)   # the record's (role, mode, value) colour fields


@dataclass
class _State:
    ref: dict = field(default_factory=lambda: {0x36: 0, 0x38: 0, 0x3a: 0, 0x3c: 0,
                                               0x3e: 0, 0x40: 0})
    regx: dict = field(default_factory=lambda: {1: 0, 2: 0, 3: 0})
    regy: dict = field(default_factory=lambda: {1: 0, 2: 0, 3: 0})
    font: int | None = None      # the persistent current font (state[2]); R38 selector updates it
    align: int = 0               # the persistent align byte (state+9); a record's align byte only
                                 # UPDATES it when non-zero (decoder FUN_00252CB0 0x2531fe: skip the
                                 # store when align==0), so it persists like `font` until the next
                                 # non-zero align -- which is how the align-0 'OFF' label inherits
                                 # its align-3 box's right-justify. Never reset to 0 by a record.
    fill_slots: dict = field(default_factory=dict)   # persistent colour slots {role: (mode, value)}.
    fill_transparent: dict = field(default_factory=dict)   # persistent transparency flags {role: bool}
                                                    # -- set by mode 5, cleared by an explicit mode,
                                                    # untouched by mode 0 (GUICODE Finding 38).
                                 # The 4 colour fields land in one global render-state buffer never
                                 # reset per record (GUICODE Finding 29): an explicit mode (1/2/3/4/
                                 # 6/7) writes its slot; a mode-0/5 field keeps the previous value.
                                 # So a mode-0 box INHERITS the last colour written to that slot in
                                 # depth-first decode order (containers included), like font/align.


def _apply_colors(state: "_State", rec_colors: list) -> list:
    """Update the persistent colour slots from `rec_colors` and return the record's EFFECTIVE
    colours (mode-0/5 fields substituted by what they inherit). GUICODE Finding 29.

    The decoder resolves each colour field by a `switch(mode)` with no `case 0` (and no `case 5`
    on the block-A fillA/fillA2 fields), so a mode-0/5 field skips its slot store and the slot
    keeps its previous value -- the last colour an earlier record (in depth-first decode order,
    containers included) wrote to that slot with an explicit mode. So: an explicit mode
    (1/2/3/4/6/7) writes `state.fill_slots[role]`; a mode-0/5 field reads it back. Returns the
    fields with mode-0/5 replaced by the inherited `(role, mode, value)` so `render._decoded_fill`
    resolves the inherited colour unchanged; a field with nothing yet to inherit is left as-is."""
    eff = []
    for role, mode, value in rec_colors:
        if mode == 0:
            # inherit BOTH the slot colour and the transparency flag: mode 0 has no case in the
            # decode switch, so it writes neither (GUICODE Finding 38). A mode-0 field that follows
            # a mode-5 one is therefore still transparent -- this is what keeps the ALTERNATOR
            # 'CLEAR EEPROM' caption from painting the quadrant navy over its olive button.
            if state.fill_transparent.get(role):
                eff.append((role, 5, None))
                continue
            inh = state.fill_slots.get(role)
            eff.append((role, inh[0], inh[1]) if inh is not None else (role, mode, value))
        elif mode == 5:
            # transparency flag, NOT a colour (`orr r3,#2` @0x253532): the slot keeps its previous
            # value (so later records still inherit it) but THIS record -- and any mode-0 field
            # after it -- paints nothing here. Left as (role,5,None), which _decoded_fill does not
            # resolve. (Treating mode 5 as inherit-and-fill wrongly painted transparent value-box
            # interiors, e.g. ELECTRICAL 0x8f994.)
            state.fill_transparent[role] = True
            eff.append((role, mode, value))
        else:
            # every explicit colour mode clears the flag (`bic r2,#2` at 0x2534ee/0x253500/
            # 0x253514/0x253526/0x25354c, one per case) as well as writing the slot.
            state.fill_slots[role] = (mode, value)
            state.fill_transparent[role] = False
            eff.append((role, mode, value))
    return eff


def _base_ref(i: int, mode: int, ref: dict, c: list) -> int:
    """The base reference added to coordinate `i` for base-mode `mode` (FUN_00257BFC)."""
    if mode == 0:
        return 0
    table = {
        0: {1: ref[0x36], 2: ref[0x3a], 3: ref[0x3e]},
        1: {1: ref[0x38], 2: ref[0x3c], 3: ref[0x40]},
        2: {1: c[0],       2: ref[0x3a], 3: ref[0x3e]},
        3: {1: c[1],       2: ref[0x3c], 3: ref[0x40]},
        4: {1: c[0],       2: ref[0x3a], 3: ref[0x3e]},
        5: {1: c[1],       2: ref[0x3c], 3: ref[0x40]},
    }
    return table[i][mode]


# The parallel match-value table (`DAT_00257BF8`, `0x9004562C` in 13.6): one `u16` per object,
# same indexing as the object table. A type-6 switch renders the candidate in
# `[child_base, child_base+count)` whose match value equals its runtime value (GUICODE Finding
# 16). The base of a group has match value 0, so the default (render the base) shows value 0.
#
# Test anchor / explicit-override only — `Screen` now locates this structurally by default via
# `guidata.find_match_table` (Task 1), which returns this exact offset on 13.6.
MATCH_TABLE_136 = 0x4562C

# The variable table a switch/value widget reads through its value-slot (`records.WRecord.
# value_slot`, R40/41/42): `DAT_00253D04` = u32 data pointers, `DAT_00253D08` = u8 types, both
# living in GuiData (13.6 offsets below). `variable_register` maps a value-slot to the register
# it reads — the SRAM address an OPTIONS checkbox toggles (GUICODE Finding 17).
#
# Test anchors / explicit-override only — `variable_register` now locates these structurally by
# default via `guidata.find_variable_tables` (Task 1), which returns this exact pair on 13.6.
VAR_DPTR_TABLE_136 = 0x44BC8
VAR_TYPE_TABLE_136 = 0x4492C


# The screen switch: object 0 -> object 1 is a single type-6 value-match switch whose
# candidates are every screen object, and whose match values are the ids written to
# `GuiCodeState+0x20A`. `GuiCode_entry1` always calls the screen setup as
# `FUN_00258650(0, ...)`, so a screen is NOT a root -- it is a candidate of this switch, and
# the screen id -> object mapping is therefore DATA, read from the match table.
SCREEN_SWITCH_OBJECT = 1        # 13.6's index; superseded by structural discovery (see below)
_SCREEN_SWITCH_SCAN = 32        # the dispatch is always among the first objects
# The `0xFFFF` no-match default; its candidate is the fallback screen, not a reachable id.
MATCH_DEFAULT = 0xFFFF


def screen_object_map(data: bytes, match_table_off: int | None = None) -> dict:
    """`{screen id: object index}` for `state+0x20A`, read from the screen switch's match table.

    **Do not compute this as `object - 4`.** That rule was inferred from the nav rail, where it
    happens to hold (screens 0-4 -> objects 4-8), and it is wrong from screen 6 onward: the ids
    are not contiguous (6, 35-38 and others are absent), so the offset drifts. It put SERVICE
    MODE at screen 16 when the panel puts it at 17 -- caught on the coach 2026-08-23, after the
    wrong mapping had been used to aim a build at a page. Read the table; it is cheap and exact.

    The `0xFFFF` default candidate is excluded: it is the no-match fallback, not a screen id
    anything writes.

    The match table is located structurally per `data` (`guidata.find_match_table`) when
    `match_table_off` is None -- **not** assumed to be the 13.6 offset, so this is correct on
    11.23 / 14.7 too (their tables sit at different offsets). Pass `MATCH_TABLE_136` (or any offset)
    to pin one explicitly. *(Until 2026-08-29 this defaulted to the 13.6 constant, which silently
    read the wrong table on other builds -- the same class of build-pinning the render/Screen path
    had already fixed.)*
    """
    off = guidata.find_match_table(data) if match_table_off is None else match_table_off
    gs, _ge = guidata.guidata_extent(data)
    sw = _screen_switch_record(data, off, gs)
    # The switch's candidate list is `guidata.switch_candidates`; a screen map is just that
    # list minus the 0xFFFF no-match default (which is the fallback screen, not an id).
    return {value: obj for value, obj in guidata.switch_candidates(data, sw, off)
            if value != MATCH_DEFAULT}


def _screen_switch_record(data: bytes, match_off: int, gs: int):
    """The top-level screen-dispatch record (`state+0x20A`), located structurally.

    It is the wtype-6 switch whose child match-values cover the nav-rail screens {0,1,2,3,4} and
    span the MOST screens -- sub-switches (floorplan, page variants) cover only subsets. Found this
    way rather than at a fixed object index because object numbering is not stable across builds
    (13.6 puts it at object 1, 14.7 at 6, 11.23 at 1). Only the first `_SCREEN_SWITCH_SCAN` objects
    are scanned; the dispatch is always near the top."""
    tab = guidata.object_tables(data)[1]
    best = None
    for obj in range(min(len(tab), _SCREEN_SWITCH_SCAN)):
        try:
            recs = records.object_records(data, gs + tab[obj])
        except Exception:
            continue
        for r in recs:
            if r.wtype != 6 or r.child_index is None or not r.count:
                continue
            mvs = {v for v, _obj in guidata.switch_candidates(data, r, match_off)}
            if {0, 1, 2, 3, 4} <= mvs and (best is None or r.count > best.count):
                best = r
    if best is None:
        raise ValueError("no top-level screen switch found (a wtype-6 covering screens 0-4)")
    return best


def screen_for_object(data: bytes, obj_index: int,
                      match_table_off: int | None = None) -> int:
    """The `state+0x20A` id that selects `obj_index`, or raise.

    Prefer this over a literal screen number anywhere a build aims at a page: the object index
    is stable and checkable (you can render it), the screen id is a table lookup that no amount
    of arithmetic will give you.
    """
    for screen, obj in screen_object_map(data, match_table_off).items():
        if obj == obj_index:
            return screen
    raise ValueError(f"object {obj_index} is not a candidate of the screen switch")


def variable_register(data: bytes, slot: int, dptr_table_off: int | None = None,
                      type_table_off: int | None = None) -> tuple[int, int]:
    """`(data_address, type)` of variable `slot` — the register a switch/value widget reads.

    `data_address` is where the value lives (an SRAM address like `0x20039B49` for a runtime/
    config bool; type 0 = bool, per `FUN_00251E38`). Table offsets are GuiData-relative and
    per-build (`DAT_00253D04/D08`); left `None` (the default), both are found structurally per
    `data` via `guidata.find_variable_tables` rather than assumed to be the 13.6 offsets — pass
    either explicitly (e.g. `VAR_DPTR_TABLE_136`/`VAR_TYPE_TABLE_136`) to pin a specific location
    instead. `proven-from-code` (GUICODE Finding 17) for the mechanism; the locator itself is
    `inferred-from-data` (see its docstring).
    """
    if dptr_table_off is None or type_table_off is None:
        found_dptr, found_type = guidata.find_variable_tables(data)
        if dptr_table_off is None:
            dptr_table_off = found_dptr
        if type_table_off is None:
            type_table_off = found_type
    gs, _ = guidata.guidata_extent(data)
    addr = struct.unpack_from("<I", data, gs + dptr_table_off + slot * 4)[0]
    vtype = data[gs + type_table_off + slot]
    return addr, vtype


class Screen:
    """Walk a screen root and collect placed widgets.

    `match_table_off` (GuiData-relative) turns on match-table-aware type-6 switch selection: a
    `switch_values` dict passed to `walk` maps a switch's `child_base` to the value to render,
    so an option-conditional widget (e.g. the Bath Fan/Starlink button, obj 247, the value-1
    candidate of the obj-881 switch) can be shown. Left `None` (the default), the table is
    located structurally per `data` via `guidata.find_match_table` (Task 1) rather than assumed
    to be the 13.6 offset — pass `MATCH_TABLE_136` (or any other offset) explicitly to pin a
    specific location instead. Either way, a type-6 switch still renders its base (value 0)
    exactly as before whenever `walk` is called without `switch_values`/`slot_values` — the
    table location only matters once a caller asks to drive a switch — so the default path and
    cross-build tests that never pass either are unchanged. If the structural locate fails (no
    corpus file is known to fail it; see `guidata.find_match_table`), switch selection is
    disabled exactly as the old hardcoded-`None` default was, rather than raising out of the
    constructor.
    """

    def __init__(self, data: bytes, match_table_off: int | None = None):
        self.data = data
        self.gs, self.ge = guidata.guidata_extent(data)
        # The screen/object table sits at a different GuiData offset per build (+0x46148 in
        # 13.6). It is the second object table (index 1); find it rather than hardcode.
        self.table = guidata.object_tables(data)[1]
        if match_table_off is None:
            try:
                match_table_off = guidata.find_match_table(data)
            except guidata.MatchTableNotFound:
                match_table_off = None
        self.match_table_off = match_table_off
        # Resolve the glyph pointer table ONCE for this Screen and thread it through every
        # metrics call in `walk` (text_width/font_metrics), rather than letting each call
        # rescan GuiData for it -- a `walk` measures dozens of labels, and the scan is the
        # dominant cost of a page layout (glyphs.find_glyph_table is O(GuiData)). This is the
        # explicit per-pass `GlyphTable` the glyphs module documents as the alternative to a
        # hidden module cache: the Screen holds `data` for its lifetime and does not mutate it,
        # so the "bytes did not change" promise is scoped to this object. None if the build has
        # no resolvable glyph table (metrics then fall back to a per-call scan, as before).
        try:
            self._glyph_table: glyphs.GlyphTable | None = glyphs.resolve_glyph_table(data)
        except glyphs.GlyphError:
            self._glyph_table = None
        self.placed: list[Placed] = []

    @property
    def glyph_table(self) -> "glyphs.GlyphTable | None":
        """The per-pass resolved glyph table, for callers measuring text about THIS
        screen's `data` (e.g. the studio server's hit-box widening) -- so one Screen
        means one glyph-table scan across the walk and everything derived from it."""
        return self._glyph_table

    def _obj_offset(self, index: int) -> int:
        return self.table[index] if 0 <= index < len(self.table) else 0

    def _match_value(self, index: int) -> int | None:
        """The match value of object `index` (`None` if no match table is configured)."""
        if self.match_table_off is None or not (0 <= index < len(self.table)):
            return None
        off = self.gs + self.match_table_off + index * 2
        return struct.unpack_from("<H", self.data, off)[0]

    def _switch_child(self, base: int, value: int, count: "int | None") -> "int | None":
        """Pick the candidate of the switch based at `base` for `value`; `None` = render nothing.

        Faithful replay of `FUN_00256C54`'s scan (`0x00257B3E–0x00257B6E`): scan EXACTLY the
        `count` entries `[base, base+count)` for the first whose match value equals `value`.
        `count` is the switch record's own `+0x94` field (`records.WRecord.count`, the u16 read at
        `0x00257B3A ldrsh r3,[r6,#0x94]`); there is no 0xffff-skipping (every entry decrements the
        count) and no repeated-value heuristic. On no match, the firmware renders the base only if
        `match[base] == 0xffff` (an explicit default candidate), otherwise it calls
        `FUN_00258330(0xffff)` which returns immediately (`0x00258336`) — i.e. renders NOTHING,
        returned here as `None`. `proven-from-code` (GUICODE Finding 16 + the count field).

        The old code scanned up to 64 entries with a "repeated match value => new group" boundary
        (labelled heuristic) and fell back to the base on no match. That over-scanned past the
        real group — e.g. the HOME inverter switch (count 2) ran on into the inner mode switch and
        matched obj 1412 (mv 0) => a spurious 'Disabled' where the hardware shows an empty block.
        A switch record missing its count (unexpected) falls back to rendering the base.
        """
        if count is None or self.match_table_off is None:
            return base
        for k in range(count):
            if self._match_value(base + k) == value:
                return base + k
        return base if self._match_value(base) == 0xFFFF else None

    def _update_frame(self, state: "_State", rec: "records.WRecord", c: list) -> None:
        """Set +0x3a/+0x3c/+0x3e/+0x40 from the just-placed record — a complete, byte-exact
        transcription of `FUN_00257BFC`'s per-type switch on `state+0x8a` (the type byte),
        traced end to end from the decompile (all cases below; every branch cited, none
        inferred from a photo). The six cases, over the full 6-bit type range 0-0x3f:

            case 0/8:            content-render extent (GUICODE Finding 14) -- see below
            case 1:               (x0, y0, x0+1, y0)                     -- a point
            case 0x10/0x22:       (x0, y0-c4, 2*c4+x0+1, y0+c4)           -- centred on x (c4=+0x32)
            case 0x11/0x23:       (x0, y0-c5, 2*c4+x0+1, y0+c5)           -- centred on y (c5=+0x34)
            case 0x2f:            (x0, y0, x0, y0)                       -- degenerate rect
            case {2,3,4,7,0xa,0xb,0xc,0xd,0xe,0xf,0x12,0x13,0x1d,0x1e,
                  0x20,0x21,0x24,0x26,0x27,0x28,0x29,0x2a,0x2b,0x2c,0x2d}:
                                   (x0, y0, x1+1, y1)                     -- the declared box
            everything else (5, 6, 9, 0x14-0x1c, 0x1f, 0x25, 0x2e, 0x30-0x3f):
                                   no case in the switch -> frame carries over unchanged, so a
                                   later sibling references the *previous* drawn frame. This is
                                   not a fallback guess: the switch genuinely has no arm for
                                   these types (containers 5/6/0x2e recurse instead of drawing;
                                   9 is the spacer; the rest are simply absent from the switch).
                                   Bounded gap, not fitted: of these, only 5/6/9 are observed in
                                   the corpus (`tests/test_layout.py::
                                   test_every_observed_wtype_is_frame_classified`); 0x14-0x1c,
                                   0x1f, 0x25, 0x30-0x3f are never emitted by any of the three
                                   builds' decoders, so the "unchanged" branch for them is
                                   asserted from the disassembly, not exercised by data.

        Types 0/8 (value/text) are the one case with a *computed* box: `FUN_00256C54` calls
        `FUN_002557C0`, which measures the record's label in the current font (`state.font`,
        the persistent R38 selector) and writes the rendered box to +0x6c12..+0x6c18 --
        *after* applying the alignment byte to the pen x (Finding 14's own formula: `x0 =
        pen_x; if align==2: x0 -= width/2; elif align==3: x0 -= width-1`), so the frame's x0
        here is the aligned pen, not the raw geometry. We replay that with
        `metrics.text_width`/`metrics.aligned_x0` whenever the record carries a label whose
        every glyph resolves (`records.py`: every type-0 record structurally may carry a
        label; type-8 never does -- its aed mask excludes the label loop, since it draws a
        runtime-computed number with no string in the file). When the label is absent (always
        true for type 8) or a glyph doesn't resolve, there is no way to know the rendered
        width from the bytes -- a bounded gap, not fitted: we keep the old geometry-box
        approximation for exactly that instance, never invent a width.
        """
        wtype = rec.wtype
        x0, y0, x1, y1, x2, y2 = c
        if wtype == 1:
            fr = (x0, y0, x0 + 1, y0)
        elif wtype == 0x2f:
            fr = (x0, y0, x0, y0)
        elif wtype in (0x10, 0x22):                      # centred: +0x32 (=c[4]) is half-width
            fr = (x0, y0 - x2, x2 * 2 + x0 + 1, y0 + x2)
        elif wtype in (0x11, 0x23):                      # centred vertically: +0x34 (=c[5])
            fr = (x0, y0 - y2, x2 * 2 + x0 + 1, y0 + y2)
        elif wtype in (0, 8):                             # value/text: content-render box
            if state.font is None:
                return                                    # no font ever selected: cannot draw
            try:
                fm = metrics.font_metrics(self.data, state.font, table=self._glyph_table)
            except Exception:
                return
            top = y0 - fm.top
            width = (metrics.text_width(self.data, state.font, rec.label,
                                        table=self._glyph_table)
                     if rec.label is not None else None)
            if width is not None:                          # exact: FUN_002557C0's own formula
                ax0 = metrics.aligned_x0(x0, width, rec.align)
                fr = (ax0, top, ax0 + width, top + fm.height - 1)
            else:                                          # bounded gap (see docstring)
                fr = (x0, top, (x1 if x1 else x0) + 1, top + fm.height - 1)
        elif wtype in _GENERIC_FRAME_TYPES:
            fr = (x0, y0, x1 + 1, y1)
        else:                                             # 5,6,9,0x14-0x1c,0x1f,0x25,0x2e,0x30-0x3f
            return                                         # switch has no case: frame unchanged
        state.ref[0x3a], state.ref[0x3c], state.ref[0x3e], state.ref[0x40] = fr

    def _placed_label_box(self, state: "_State", rec: "records.WRecord", c: list) -> tuple[int, int]:
        """The x-extent a `Placed` entry actually uses: `c[0]`/`c[2]` shifted by the record's
        own alignment byte (+9), same formula as `_update_frame`'s type-0/8 case (`FUN_00256C54`
        -> `FUN_002557C0`, GUICODE Finding 14: `x0 = pen_x; align==2: x0 -= width/2; align==3:
        x0 -= width-1`) -- generalised here to *any* labelled record, because the align byte is
        a per-record header field (`records.py`: `state+9 = header[3]>>6`), read unconditionally
        for every record, not gated by widget type. `_update_frame`'s per-type switch decides
        what a record's *frame* becomes for later siblings' base-mode 2/3 references (the
        declared box, for most types); this is a separate question -- where *this* record's own
        label actually lands on screen, which is what `render.py` draws. The two coincide for
        type 0/8 (there the frame IS the drawn box) and can differ for every other labelled type
        (e.g. wtype 0xb static text, whose frame stays the declared box while its drawn text is
        still alignment-adjusted).

        Left alignment (align 0/1) is a no-op in `metrics.aligned_x0` (returns the pen
        unchanged), so this returns the declared box `(c[0], c[2])` verbatim whenever align is
        not 2/3 -- deliberately: it keeps the (vastly more common, ~92% of records) left-aligned
        case byte-for-byte identical to before this method existed. It also falls back to the
        declared box when there is no label, no font, or a glyph doesn't resolve -- the same
        bounded gap `_update_frame` already documents, never an invented width.
        """
        # Box-geometry alignment. FUN_00257BFC's align block (0x258058-0x258180), run after the
        # flow-origin commit and before the draw dispatch, shifts a "path-1" record's own box
        # coords (+0x2a/+0x2e) by the PERSISTENT align byte (`state.align`, above) and the box's
        # own geometry width -- align==3 moves the box left by its full width (declared x0 becomes
        # the right edge), align==2 by half. This right-justifies the OFF button box (wtype-0x21)
        # AND its trailing 'OFF' label (wtype-0xb, align 0, inheriting the box's persistent align 3)
        # to x0=327/x1=397, seated above RET, with render.py centring "OFF" in that box.
        # `proven-from-code`. It takes precedence over the text-width branch below for path-1 types.
        if state.align in (2, 3) and _align_path1(rec.wtype):
            w = c[2] - c[0]
            if state.align == 3:
                return c[0] - w, c[0]
            return c[0] - w // 2, c[2] - w // 2          # align == 2: centre on the pen
        # Non-path-1 labelled records (type 0/8 value/text, `FUN_002557C0`, GUICODE Finding 14):
        # the glyph is placed by the text width, not the box geometry, using the PERSISTENT align
        # `state.align` (not `rec.align`) -- `FUN_002557C0` reads state+9 (0x002557FE), which is the
        # persistent align byte (Finding 24), so an align-0 record inherits the align in effect. This
        # is what right-justifies a value widget's trailing unit: the tank '%' (rec.align 0) inherits
        # the value's align 3 -> its right edge sits at the pen, tucked under the tank; the battery
        # 'V' (rec.align 1) overrides back to left and stays in its box. `proven-from-code`.
        if rec.label is not None and state.font is not None and state.align in (2, 3):
            width = metrics.text_width(self.data, state.font, rec.label,
                                       table=self._glyph_table)
            if width is not None:
                x0 = metrics.aligned_x0(c[0], width, state.align)
                return x0, x0 + width
        return c[0], c[2]

    def walk(self, obj_index: int, state: _State | None = None, depth: int = 0,
             switch_values: dict | None = None, slot_values: dict | None = None,
             trace: list | None = None, path: tuple = ()) -> list[Placed]:
        """Replay `FUN_00257BFC` over `obj_index` and its children; returns `self.placed`.

        `trace`, when given, receives one entry per record walked, containers (wtype 5/6/0x2e)
        included and listed before their children: `(key, obj_index, record_index, rec, box,
        font, align, colors)`. `key` is `path + ((obj_index, record_index),)`, the chain from
        the walk's root to the record, with `record_index` the record's position in
        `records.object_records`; `box` is `(x0, y0, x1, y1)` after the align block (as
        `Placed` carries it); `font`/`align` are the persistent `state.font`/`state.align` after
        the record; `colors` its effective colours (`_apply_colors`). The entry is appended
        after the box and state are computed and before any recursion; it reads the walk and
        never changes it (`pagewalk` builds on it).
        """
        if state is None:
            state = _State()
            self.placed = []
        if depth > 20:
            return self.placed
        off = self._obj_offset(obj_index)
        if not (0 < off < (self.ge - self.gs)):
            return self.placed
        try:
            recs = records.object_records(self.data, self.gs + off)
        except (IndexError, ValueError):
            return self.placed

        for rec_index, rec in enumerate(recs):
            c = [0] * 6
            for i in range(6):
                pen = _s16(rec.geom[i])
                reg = state.regx if i % 2 == 0 else state.regy
                if rec.in_regs[i]:
                    pen += reg.get(rec.in_regs[i], 0)
                pen += _base_ref(i, rec.base_modes[i], state.ref, c)
                if rec.out_regs[i]:
                    reg[rec.out_regs[i]] = pen
                c[i] = pen
            # the R38 font selector updates the persistent current font (state[2]); a record
            # without one keeps the previous, which is how value widgets inherit a font.
            if rec.font is not None:
                state.font = rec.font
            # the align byte (state+9) is persistent the same way: the decoder writes it only for
            # gated types with a NON-ZERO align, so it carries across records (and containers -- the
            # recursion's state snapshot flag is clear) until the next non-zero align. This is how
            # the align-0 'OFF' label inherits its align-3 box's right-justify.
            if _writes_align(rec.wtype) and rec.align != 0:
                state.align = rec.align
            # persistent colour slots (GUICODE Finding 29): apply this record's colour fields in
            # decode order -- an explicit mode writes its slot, a mode-0/5 field inherits the slot's
            # previous value. Done for EVERY record (containers included, so children inherit their
            # parent's fill) before the recurse/leaf branch below. `eff_colors` is the record's
            # own fields with mode-0/5 substituted by what they inherit, so render.py resolves the
            # inherited colour with its existing _decoded_fill.
            eff_colors = _apply_colors(state, rec.colors)
            # flow reference (FUN_00257BFC): the whole coordinate commit — +0x2a/+0x2c and the
            # flow origin +0x36/+0x38 (lines 99-100) — runs only when the record's *type* is in
            # the flow-gate mask (line 33: `(DAT_002582bc & aed) || aee`, DAT_002582bc =
            # 0x600FFDFF). Types outside it (notably the type-9 spacer, bit 9 clear) leave the
            # flow untouched. Updating it unconditionally let an obj93 spacer at (0,0) reset the
            # origin and drop the setpoint ~190px off-screen; the type gate is what fixes it.
            # The flow uses the PRE-align pen c[0] (the box align below is applied after -- using
            # the aligned x here regresses the Climate tab 211->183).
            if _in_flow_gate(rec.wtype):
                state.ref[0x36], state.ref[0x38] = c[0], c[1]

            # the record's own box after the align byte (FUN_00257BFC align block, applied after
            # the flow commit): align 3 right-justifies its box, align 2 centres. This shifts the
            # record's own drawn box + its frame for later mode-2/3 references, not the flow.
            ax0, ax1 = self._placed_label_box(state, rec, c)
            c[0], c[2] = ax0, ax1
            key = path + ((obj_index, rec_index),)
            if trace is not None:
                trace.append((key, obj_index, rec_index, rec, (c[0], c[1], c[2], c[3]),
                              state.font, state.align, tuple(eff_colors)))

            if (rec.wtype in (5, 6, 0x2e)
                    and rec.child_index is not None and rec.child_index < len(self.table)):
                # only the container types recurse (FUN_00256C54 dispatches 5/6/0x2e to
                # FUN_00258330 with the save-flag clear, so children share this state). The
                # same `+0x28` field on a type-10/18 record is an *image index* (0..7 into the
                # 8-image table via FUN_00255F80), NOT an object index — so those must not
                # recurse. `proven-from-code` (GUICODE Finding 15).
                child = rec.child_index
                if rec.wtype == 6 and self.match_table_off is not None:
                    # option/state-conditional switch (Finding 16/17): choose the candidate for
                    # the driven value. `slot_values` keys on the variable the switch reads
                    # (`value_slot` -> a config register, e.g. {5: 8} = floorplan WD), the
                    # principled form; `switch_values` keys on the switch's own child_base.
                    val = None
                    if (slot_values is not None and rec.value_slot is not None
                            and rec.value_slot in slot_values):
                        val = slot_values[rec.value_slot]
                    elif switch_values is not None and child in switch_values:
                        val = switch_values[child]
                    if val is not None:
                        child = self._switch_child(child, val, rec.count)
                if child is not None:                # None = the switch's 0xFFFF no-op (nothing)
                    # colour slots are parent-scoped: a child inherits the parent's colour (set by
                    # this container record just above), but the child's own colour writes must NOT
                    # leak to this container's LATER siblings. Save/restore around the recursion --
                    # without it the header's red (idx4) leaked across the whole tree into unrelated
                    # boxes (e.g. ELECTRICAL Start/Stop rendered red instead of their own colour).
                    saved_fill = dict(state.fill_slots)
                    saved_transp = dict(state.fill_transparent)
                    self.walk(child, state, depth + 1, switch_values, slot_values,
                              trace, key)
                    state.fill_slots = saved_fill
                    state.fill_transparent = saved_transp
            else:
                self.placed.append(Placed(          # c[0]/c[2] already aligned above
                    wtype=rec.wtype, label=rec.label,
                    x0=c[0], y0=c[1], x1=c[2], y1=c[3],
                    depth=depth, obj_index=obj_index, record_offset=rec.offset,
                    font=state.font, align=rec.align, v_align=rec.v_align,
                    line_height=rec.line_height,
                    line_height_adds_font=rec.line_height_adds_font,
                    label_offset=rec.label_offset,
                    value_slot=rec.value_slot, coord4=rec.geom[4],
                    hollow=bool((rec.header[0] >> 1) & 1), h_align=rec.h_align,
                    colors=eff_colors))

            # base frame for the next record's mode-2/3 references (FUN_00257BFC's per-type
            # switch, run after the record renders). +0x3a/+0x3c/+0x3e/+0x40. Uses the aligned
            # box (align happens before this per-type frame switch in the engine).
            self._update_frame(state, rec, c)
        return self.placed


def climate_root(data: bytes) -> int:
    """The 13.6 Climate page-content object (obj 7 — header + nav + body + mode grid).

    Not a `FUN_00258650` root (see module docstring): the engine reaches this via root 0's
    page switch. Found by graph reachability; walking it reproduces the page's relative
    layout. Per-build the index differs (11.23 -> 9, 14.7 -> 12)."""
    return 7
