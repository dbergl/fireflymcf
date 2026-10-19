"""Approach A: absolute screen coordinates computed from GuiData, no calibration.

These pin the top-level Climate layout the walker reproduces from the bytes — the values that
match the panel photo. They are the regression guard for the layout engine replay
(`FUN_00257BFC` + `FUN_00258330`). Deep-nested vertical origin and type-6 value-match lists
are not yet exact (see the module docstring / the render); those elements are not asserted
here, so this test states only what is proven.
"""
from __future__ import annotations

import dataclasses

import pytest

from fireflymcf import guidata, layout, metrics, records

BUILDS = {
    "11.23": "firefly-11.23/mcf.bin",
    "13.6": "firefly-13.6/mcf.bin",
    "14.7": "firefly-14.7 (Terrain)/mcf.bin",
}


def _load(path):
    with open(path, "rb") as handle:
        return handle.read()


# Independently transcribed from the `FUN_00257BFC` decompile (not imported from
# `layout._GENERIC_FRAME_TYPES`), so a mutation to the production set is actually caught —
# see `test_generic_frame_types_constant_matches_the_switch`.
_EXPECTED_GENERIC_TYPES = frozenset({
    2, 3, 4, 7, 0xa, 0xb, 0xc, 0xd, 0xe, 0xf, 0x12, 0x13, 0x1d, 0x1e,
    0x20, 0x21, 0x24, 0x26, 0x27, 0x28, 0x29, 0x2a, 0x2b, 0x2c, 0x2d,
})

# Also independently transcribed: every type the switch has no case for at all (containers
# 5/6/0x2e, the spacer 9, and the types simply absent from the switch body).
_EXPECTED_UNCHANGED_TYPES = ({5, 6, 9, 0x2e} | set(range(0x14, 0x1d)) | {0x1f, 0x25}
                              | set(range(0x30, 0x40)))


def _rec(wtype, **kw):
    """A minimal synthetic WRecord for exercising `_update_frame`'s per-type classification
    in isolation, without a full object walk. Only the fields each case actually reads
    matter (wtype always; label/align/font only for the content-extent case)."""
    kw.setdefault("label", None)
    kw.setdefault("align", 0)
    return records.WRecord(offset=0, size=0, wtype=wtype, header=b"\x00" * 8,
                           geom=[None] * 6, **kw)


@pytest.fixture(scope="module")
def climate_placed():
    with open("firefly-13.6/mcf.bin", "rb") as handle:
        data = handle.read()
    scr = layout.Screen(data)
    return scr.walk(layout.climate_root(data))


def _find(placed, label):
    return [p for p in placed if p.label and p.label.strip() == label]


def test_header_bar_spans_the_screen_top(climate_placed) -> None:
    hdr = _find(climate_placed, "CLIMATE CONTROL")
    assert hdr, "header title not placed"
    p = hdr[0]
    assert p.y0 == 0 and p.x1 - p.x0 >= 780     # full-width strip pinned to the top


def test_nav_rail_is_a_left_column(climate_placed) -> None:
    # the tall type-4 box down the left edge
    rails = [p for p in climate_placed
             if p.wtype == 4 and p.x0 == 0 and p.x1 <= 64 and (p.y1 - p.y0) > 400]
    assert rails, "left nav rail not placed"


def test_climate_tab_decodes_to_the_calibrated_position(climate_placed) -> None:
    """The tab hand-calibrated to (224,70) decodes to (211,76) — approach A validated."""
    tab = _find(climate_placed, "CLIMATE")
    assert tab, "CLIMATE tab not placed"
    p = tab[0]
    assert (p.x0, p.y0) == (211, 76)


def test_off_button_right_justifies_above_ret() -> None:
    """The OFF button (box 0x9534b + its label 0x9535f) right-justifies so it sits above RET,
    via the persistent align byte (state+9, like state.font): the align-3 box shifts left by its
    own geometry width, and the align-0 OFF LABEL inherits that persistent align 3 and shifts
    with it -- both land at x0=327/x1=397 (RET is at 333, the 6px row offset). ON (align 0, no
    inherited 2/3), EXT and RET are unchanged. Non-vacuous: without the align block the OFF box is
    at the raw pen 397; without persistent-align inheritance the OFF label separates to 397."""
    data = _load(BUILDS["13.6"])
    placed = layout.Screen(data, match_table_off=layout.MATCH_TABLE_136).walk(
        4, slot_values={5: 8, 112: 1})
    box = {"ON": 0x95312, "OFF": 0x9534b, "EXT": 0x97865, "RET": 0x96c69,
           "OFF_lbl": 0x9535f}
    pos = {name: next(p for p in placed if p.record_offset == off) for name, off in box.items()}
    assert (pos["OFF"].x0, pos["OFF"].x1) == (327, 397)          # right-justified box
    assert (pos["OFF_lbl"].x0, pos["OFF_lbl"].x1) == (327, 397)  # label inherits, stays with box
    assert pos["OFF"].x0 < pos["OFF_lbl"].x1 and pos["OFF_lbl"].x0 >= pos["OFF"].x0  # together
    assert (pos["ON"].x0, pos["ON"].x1) == (96, 166)            # align 0 -> unchanged
    assert (pos["EXT"].x0, pos["EXT"].x1) == (102, 172)
    assert (pos["RET"].x0, pos["RET"].x1) == (333, 403)         # OFF (327) ~ above RET (333)


def test_mode_grid_labels_are_present_and_stepped(climate_placed) -> None:
    """Cool/High/Low/Auto place in a grid with a consistent vertical step (shared flow)."""
    ys = {}
    for name in ("High", "Low", "Auto"):
        hits = _find(climate_placed, name)
        assert hits, f"{name} not placed"
        ys[name] = hits[0].y0
    step1 = ys["Low"] - ys["High"]
    step2 = ys["Auto"] - ys["Low"]
    assert step1 == step2 and step1 > 0        # evenly stacked, top to bottom


def test_mode_row_sits_below_the_setpoint(climate_placed) -> None:
    """The vertical-origin fix (GUICODE Finding 14): the setpoint value widget gives its font
    cell real height, and the type-9 spacer no longer resets the flow — so the mode row lands
    *below* the setpoint instead of riding up over the CLIMATE tab.

    Before the fix Cool decoded to y=76 (overlapping the tab at y=76). It must now be well
    below both the tab and the setpoint value widgets.
    """
    tab = _find(climate_placed, "CLIMATE")[0]
    cool = _find(climate_placed, "Cool")[0]
    # the type-0/8 setpoint value widgets in the body container
    setpoint_ys = [p.y0 for p in climate_placed if p.wtype in (0, 8) and 0 < p.y0 < 300]
    assert setpoint_ys, "no setpoint value widget placed on-screen"
    assert cool.y0 > tab.y0 + 100          # the mode row is far below the tab, not on it
    assert cool.y0 > max(setpoint_ys)      # and below the setpoint number it labels


def test_option_conditional_widget_is_a_value_match_switch_candidate() -> None:
    """GUICODE Finding 16: the Bath Fan/Starlink button (obj 247) is the value-1 candidate of
    the obj-881 switch (child_base 246). It is absent by default (value 0 -> the empty base) and
    appears, top-right, when the switch is driven to value 1."""
    with open("firefly-13.6/mcf.bin", "rb") as handle:
        data = handle.read()

    def bath_fan(placed):
        return [p for p in placed if p.obj_index == 247 and p.label and p.label.strip() == "Bath Fan"]

    default = layout.Screen(data, match_table_off=layout.MATCH_TABLE_136).walk(7)
    assert not bath_fan(default), "obj 247 should not render at the default (value 0)"

    on = layout.Screen(data, match_table_off=layout.MATCH_TABLE_136).walk(7, switch_values={246: 1})
    hits = bath_fan(on)
    assert hits, "obj 247 (Bath Fan button) should render when the switch is value 1"
    p = hits[0]
    assert p.x0 > 500 and p.y0 < 160        # top-right, where the photo shows the Starlink button


def test_option_checkbox_and_widget_share_the_register() -> None:
    """GUICODE Finding 17 (thread 4, display side closed): the OPTIONS 'Bath Fan' checkbox and
    the Climate-page conditional button both resolve to the *same* variable slot 109 → the same
    SRAM register. Enabling the option flips one register and both follow."""
    from fireflymcf import records
    with open("firefly-13.6/mcf.bin", "rb") as handle:
        data = handle.read()
    scr = layout.Screen(data)
    gs = scr.gs

    def slots(root):
        seen, stack, found = set(), [root], set()
        while stack:
            i = stack.pop()
            if i in seen or not (0 <= i < len(scr.table)):
                continue
            seen.add(i)
            off = scr.table[i]
            if not (0 < off < (scr.ge - gs)):
                continue
            for r in records.object_records(data, gs + off):
                if r.wtype == 6 and r.value_slot is not None:
                    found.add(r.value_slot)
                if r.wtype in (5, 6, 0x2e) and r.child_index is not None and r.child_index < len(scr.table):
                    stack.append(r.child_index)
        return found

    # the Bath Fan button switch (Climate) and the Bath Fan checkbox (OPTIONS) both read slot 109
    assert 109 in slots(7)             # Climate page conditional button
    assert 109 in slots(46)            # OPTIONS page checkbox — same register
    addr, vtype = layout.variable_register(data, 109)
    assert addr == 0x20039B49 and vtype == 0     # a bool in SRAM: the option register


def test_ags_generator_section_is_option_gated() -> None:
    """The HOME generator/AGS section (Start/Stop/GEN, bottom-right) needs two registers: the
    battery/generator quadrant variant (slot 6 -> gen-capable value) and the AGS option (slot 37
    -> SRAM 0x2003A38C). Neither alone shows it; together they reveal Start/Stop/GEN. This is the
    HOME-INVERTER-AGS coach state (Progressive Inverter auto-selects AGS -- a write-side dep)."""
    with open("firefly-13.6/mcf.bin", "rb") as handle:
        data = handle.read()

    def labels(sv):
        placed = layout.Screen(data, match_table_off=layout.MATCH_TABLE_136).walk(4, slot_values=sv)
        return {p.label.strip() for p in placed if p.label and p.label.strip()}

    assert layout.variable_register(data, 37) == (0x2003A38C, 1)
    base = {5: 8, 112: 1}
    assert not ({"Start", "Stop", "GEN"} & labels({**base, 6: 2}))          # quadrant alone: no gen
    assert not ({"Start", "Stop", "GEN"} & labels({**base, 37: 1}))         # AGS alone: not reached
    assert {"Start", "Stop", "GEN"} <= labels({**base, 6: 2, 37: 1})        # both: generator shows


def test_black_tank_option_adds_the_fourth_gauge() -> None:
    """The BLACK tank is an option on top of the floorplan (slot 112 -> SRAM 0x2003A4B4, bool),
    not part of the floorplan itself. With floorplan WD alone HOME shows FRESH/GREY/GREY2; adding
    slot 112 = 1 brings in BLACK and its gauge — the option->widget mechanism (Finding 16/17)."""
    with open("firefly-13.6/mcf.bin", "rb") as handle:
        data = handle.read()

    def tanks(sv):
        placed = layout.Screen(data, match_table_off=layout.MATCH_TABLE_136).walk(4, slot_values=sv)
        return {p.label.strip() for p in placed if p.label and p.label.strip()
                in ("FRESH", "GREY", "GREY2", "BLACK")}

    assert layout.variable_register(data, 112) == (0x2003A4B4, 0)
    assert tanks({5: 8}) == {"FRESH", "GREY", "GREY2"}          # floorplan WD: three tanks
    assert tanks({5: 8, 112: 1}) == {"FRESH", "GREY", "GREY2", "BLACK"}   # + black-tank option


def test_floorplan_variable_drives_whole_pages() -> None:
    """The floorplan is variable slot 5 -> SRAM 0x2003A77A (u8); 'WD' is value 8. Driving it
    (slot_values={5: 8}) turns the LIGHTS page from 'NO FLOORPLAN SELECTED' into the configured
    Interior/Exterior light grid — the switch mechanism gating an entire page (Finding 16/17)."""
    with open("firefly-13.6/mcf.bin", "rb") as handle:
        data = handle.read()
    scr = layout.Screen(data, match_table_off=layout.MATCH_TABLE_136)
    assert layout.variable_register(data, 5) == (0x2003A77A, 1)   # floorplan register
    assert scr._match_value(326) == 8                              # obj 326 == 'WD'

    def labels(placed):
        return {p.label.strip() for p in placed if p.label and p.label.strip()}

    default = labels(layout.Screen(data, match_table_off=layout.MATCH_TABLE_136).walk(6))
    wd = labels(layout.Screen(data, match_table_off=layout.MATCH_TABLE_136)
                .walk(6, slot_values={5: 8}))
    assert "NO FLOORPLAN SELECTED" in default and "Main\nCeiling" not in default
    assert "Main\nCeiling" in wd and "Step\nLight" in wd          # the configured light grid


def test_match_value_of_the_option_group() -> None:
    """The parallel match table gives the option-button group its selector values: 246->0
    (empty), 247->1 (Bath Fan), 248->2 — the byte evidence behind Finding 16."""
    with open("firefly-13.6/mcf.bin", "rb") as handle:
        data = handle.read()
    scr = layout.Screen(data, match_table_off=layout.MATCH_TABLE_136)
    assert (scr._match_value(246), scr._match_value(247), scr._match_value(248)) == (0, 1, 2)
    count = 3                                      # the switch record's own +0x94 count (bytes)
    assert scr._switch_child(246, 1, count) == 247   # value 1 selects the Bath Fan button
    assert scr._switch_child(246, 0, count) == 246   # value 0 selects the empty base
    assert scr._switch_child(246, 9, count) is None  # no candidate matches -> render nothing


# --------------------------------------------------------------------------------------
# Task 1 — per-record-type frame audit (`_update_frame` against FUN_00257BFC's switch).
# Each test below asserts a *computed-from-bytes* invariant (the frame formula the switch's
# decompile actually performs for that case), never a hardcoded/photo-fitted pixel value.
# --------------------------------------------------------------------------------------

def test_frame_point_and_degenerate_and_centred_cases() -> None:
    """Types 1 (point), 0x2f (degenerate rect) and the centred pairs 0x10/0x22, 0x11/0x23,
    transcribed straight from `FUN_00257BFC`'s switch (see `Screen._update_frame`'s
    docstring for the cited formula each branch below matches)."""
    data = _load(BUILDS["13.6"])
    scr = layout.Screen(data)
    c = [10, 20, 33, 44, 5, 6]        # x0,y0,x1,y1,x2,y2 -- arbitrary, only arithmetic matters
    x0, y0, x1, y1, x2, y2 = c

    state = layout._State()
    scr._update_frame(state, _rec(1), c)
    assert (state.ref[0x3a], state.ref[0x3c], state.ref[0x3e], state.ref[0x40]) == \
           (x0, y0, x0 + 1, y0)

    state = layout._State()
    scr._update_frame(state, _rec(0x2f), c)
    assert (state.ref[0x3a], state.ref[0x3c], state.ref[0x3e], state.ref[0x40]) == \
           (x0, y0, x0, y0)

    for t in (0x10, 0x22):
        state = layout._State()
        scr._update_frame(state, _rec(t), c)
        assert (state.ref[0x3a], state.ref[0x3c], state.ref[0x3e], state.ref[0x40]) == \
               (x0, y0 - x2, x2 * 2 + x0 + 1, y0 + x2)

    for t in (0x11, 0x23):
        state = layout._State()
        scr._update_frame(state, _rec(t), c)
        assert (state.ref[0x3a], state.ref[0x3c], state.ref[0x3e], state.ref[0x40]) == \
               (x0, y0 - y2, x2 * 2 + x0 + 1, y0 + y2)


def test_generic_frame_types_constant_matches_the_switch() -> None:
    """Pins `layout._GENERIC_FRAME_TYPES` against an independently hand-transcribed copy of
    the same case list, so a mutation to the production constant (e.g. dropping a type) is
    caught here even though the other tests below key off the production constant too."""
    assert layout._GENERIC_FRAME_TYPES == _EXPECTED_GENERIC_TYPES


def test_frame_generic_types_use_the_declared_box() -> None:
    """The switch's plain-box cases (2,3,4,7,0xa..0xf,0x12,0x13,0x1d,0x1e,0x20,0x21,0x24,
    0x26-0x2d) copy the declared geometry (x0,y0,x1+1,y1) verbatim -- no measurement, no
    alignment. Non-vacuous: stubbing a type out of `_GENERIC_FRAME_TYPES` makes this fail
    for that type (the frame would fall to 'unchanged' instead)."""
    data = _load(BUILDS["13.6"])
    scr = layout.Screen(data)
    c = [10, 20, 33, 44, 5, 6]
    x0, y0, x1, y1, _, _ = c
    assert _EXPECTED_GENERIC_TYPES        # the set isn't empty
    for t in sorted(_EXPECTED_GENERIC_TYPES):
        state = layout._State()
        scr._update_frame(state, _rec(t), c)
        assert (state.ref[0x3a], state.ref[0x3c], state.ref[0x3e], state.ref[0x40]) == \
               (x0, y0, x1 + 1, y1), f"type {t:#x}"


def test_frame_unchanged_types_leave_the_previous_frame_untouched() -> None:
    """Containers (5,6,0x2e), the type-9 spacer, and the types genuinely absent from
    `FUN_00257BFC`'s switch (0x14-0x1c, 0x1f, 0x25, 0x30-0x3f) have no case at all -- the
    frame carries over from whatever the previous sibling set, not a generic box. This is
    the bounded-gap branch for the types the corpus never emits (see the docstring)."""
    data = _load(BUILDS["13.6"])
    scr = layout.Screen(data)
    c = [10, 20, 33, 44, 5, 6]
    for t in sorted(_EXPECTED_UNCHANGED_TYPES):
        state = layout._State()
        sentinel = (state.ref[0x3a], state.ref[0x3c], state.ref[0x3e], state.ref[0x40])
        scr._update_frame(state, _rec(t), c)
        assert (state.ref[0x3a], state.ref[0x3c], state.ref[0x3e], state.ref[0x40]) == \
               sentinel, f"type {t:#x} should leave the frame unchanged"
        # and it really is untouched, not coincidentally equal to the sentinel zeros:
        state.ref[0x3a] = 999
        before = dict(state.ref)
        scr._update_frame(state, _rec(t), c)
        assert state.ref == before, f"type {t:#x} mutated a non-zero frame"


@pytest.mark.parametrize("build", sorted(BUILDS))
def test_every_observed_wtype_is_frame_classified(build) -> None:
    """Audit (Task 1's acceptance criterion): every widget type any of the three builds'
    decoders actually emit must land in one of `_update_frame`'s cited cases -- point,
    degenerate, centred, content-extent, generic, or genuinely-unchanged -- never an
    uncited default. `records.object_records` is the untouched decoder boundary oracle
    (`test_records.py`); this walks every object with it and classifies every type seen.
    Non-vacuous: narrowing `_GENERIC_FRAME_TYPES` to exclude an observed type (e.g. 4, the
    nav-rail box) fails immediately, since it would no longer classify as anything."""
    data = _load(BUILDS[build])
    scr = layout.Screen(data)
    gs, ge = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    seen: set[int] = set()
    for off in table:
        if not (0 < off < (ge - gs)):
            continue
        try:
            recs = records.object_records(data, gs + off)
        except (IndexError, ValueError):
            continue
        for r in recs:
            seen.add(r.wtype)
    assert seen, f"{build}: decoder produced no records at all"

    c = [11, 22, 33, 44, 5, 6]
    x0, y0, x1, y1, x2, y2 = c
    for wtype in sorted(seen):
        state = layout._State()             # state.font is None: types 0/8 fall to
        sentinel = (0, 0, 0, 0)              # 'unchanged' here too (no font ever selected,
        scr._update_frame(state, _rec(wtype), c)   # see the docstring's bounded gap)
        got = (state.ref[0x3a], state.ref[0x3c], state.ref[0x3e], state.ref[0x40])
        if wtype == 1:
            assert got == (x0, y0, x0 + 1, y0), f"type {wtype:#x}"
        elif wtype == 0x2f:
            assert got == (x0, y0, x0, y0), f"type {wtype:#x}"
        elif wtype in (0x10, 0x22):
            assert got == (x0, y0 - x2, x2 * 2 + x0 + 1, y0 + x2), f"type {wtype:#x}"
        elif wtype in (0x11, 0x23):
            assert got == (x0, y0 - y2, x2 * 2 + x0 + 1, y0 + y2), f"type {wtype:#x}"
        elif wtype in _EXPECTED_GENERIC_TYPES:
            assert got == (x0, y0, x1 + 1, y1), f"type {wtype:#x}"
        elif wtype in (0, 8) or wtype in _EXPECTED_UNCHANGED_TYPES:
            # 0/8 with no font selected, containers (5,6,0x2e), the spacer (9), or a type
            # absent from the switch entirely: all leave the frame unchanged.
            assert got == sentinel, f"type {wtype:#x} unexpectedly changed the frame"
        else:
            pytest.fail(f"type {wtype:#x} is not classified by any cited case")


def test_frame_content_extent_uses_exact_width_and_alignment() -> None:
    """Type 0/8's frame x-extent replays `FUN_002557C0`'s own formula exactly: the pen is
    alignment-adjusted *before* the measured width is added (GUICODE Finding 14), using the
    real font descriptor + glyph metrics for a labelled type-0 record pulled straight out
    of the corpus -- not a geometry-box guess. `metrics.text_width`/`aligned_x0` are already
    independently tested (`test_metrics.py`); this pins that `_update_frame` actually calls
    them for this case rather than falling back to the approximate box."""
    data = _load(BUILDS["13.6"])
    scr = layout.Screen(data)
    gs, ge = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    rec = None
    for off in table:
        if not (0 < off < (ge - gs)):
            continue
        try:
            recs = records.object_records(data, gs + off)
        except (IndexError, ValueError):
            continue
        for r in recs:
            if (r.wtype == 0 and r.label and r.font is not None
                    and metrics.text_width(data, r.font, r.label) is not None):
                rec = r
                break
        if rec:
            break
    assert rec is not None, "no labelled, glyph-resolvable type-0 record found in the corpus"

    width = metrics.text_width(data, rec.font, rec.label)
    fm = metrics.font_metrics(data, rec.font)
    pen_x, pen_y = 100, 50

    # the record's own (usually left, align=0) case:
    state = layout._State(font=rec.font)
    scr._update_frame(state, rec, [pen_x, pen_y, 0, 0, 0, 0])
    expected_x0 = metrics.aligned_x0(pen_x, width, rec.align)
    assert (state.ref[0x3a], state.ref[0x3c], state.ref[0x3e], state.ref[0x40]) == (
        expected_x0, pen_y - fm.top, expected_x0 + width, pen_y - fm.top + fm.height - 1)

    # force centre and right alignment (dataclasses.replace on the same real record/label/
    # font) so a mutation that drops the alignment adjustment (e.g. using the raw pen instead
    # of `aligned_x0`) is actually caught -- most corpus records are left-aligned (align=0),
    # where aligned_x0 is a no-op, so the case above alone would not catch that mutation.
    for align in (2, 3):
        rec_aligned = dataclasses.replace(rec, align=align)
        state = layout._State(font=rec.font)
        scr._update_frame(state, rec_aligned, [pen_x, pen_y, 0, 0, 0, 0])
        expected_x0 = metrics.aligned_x0(pen_x, width, align)
        assert expected_x0 != pen_x, "test precondition: alignment must actually shift x0"
        assert (state.ref[0x3a], state.ref[0x3e]) == (expected_x0, expected_x0 + width), \
            f"align={align}"


def test_frame_content_extent_is_a_bounded_gap_without_a_label() -> None:
    """Type 8 never carries a label (`records.py`'s aed mask structurally excludes its label
    loop -- it draws a runtime-computed number, not a stored string), so its frame x-extent
    cannot be measured from the bytes. This is the documented negative: `_update_frame` falls
    back to the declared geometry box for exactly this case, rather than inventing a width."""
    data = _load(BUILDS["13.6"])
    scr = layout.Screen(data)
    fm = metrics.font_metrics(data, 9)          # the Climate setpoint's big digit font
    pen_x, pen_y, decl_x1 = 100, 50, 130
    state = layout._State(font=9)
    rec = _rec(8, label=None)                    # type 8: structurally never has a label
    scr._update_frame(state, rec, [pen_x, pen_y, decl_x1, 0, 0, 0])
    assert (state.ref[0x3a], state.ref[0x3c], state.ref[0x3e], state.ref[0x40]) == (
        pen_x, pen_y - fm.top, decl_x1 + 1, pen_y - fm.top + fm.height - 1)


# --------------------------------------------------------------------------------------
# render-fidelity Task 2: a widget's own placement (Placed.x0/x1), not just its frame,
# reflects the alignment byte.
# --------------------------------------------------------------------------------------

def _first_labelled_record(data: bytes):
    """The first labelled, glyph-resolvable record in the object table -- real bytes, not a
    synthetic fixture, so the width/pen arithmetic below is pinned to an actual corpus font
    and string (mirrors `test_frame_content_extent_uses_exact_width_and_alignment`)."""
    gs, ge = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    for off in table:
        if not (0 < off < (ge - gs)):
            continue
        try:
            recs = records.object_records(data, gs + off)
        except (IndexError, ValueError):
            continue
        for r in recs:
            if (r.label and r.font is not None
                    and metrics.text_width(data, r.font, r.label) is not None):
                return r
    return None


def test_placed_label_box_uses_exact_width_and_alignment() -> None:
    """`Screen._placed_label_box` -- the x-extent a record's *own* label is actually drawn at
    (render-fidelity Task 2), as opposed to the frame `_update_frame` leaves behind for later
    siblings -- applies the same align-adjusted-pen formula as the type-0/8 frame case
    (GUICODE Finding 14: `x0 = pen; align==2: x0 -= width/2; align==3: x0 -= width-1`), but for
    *any* labelled record: the align byte (`records.py`: `state+9 = header[3]>>6`) is read
    unconditionally for every record, not gated by widget type.

    Alignment is driven by the PERSISTENT align byte `state.align` (state+9), not the record's
    own `rec.align`: `FUN_002557C0` reads state+9 (0x002557FE), the persistent value walk maintains
    (GUICODE Finding 24). So an align-0/1 record inherits whatever align is in effect and, when
    that is left (0/1), the declared box `(c[0], c[2])` is returned verbatim -- ~92% of the corpus,
    unchanged. A persistent centre/right (2/3) must shift by the *measured* text width
    (`metrics.text_width`), computed here from the bytes -- not a hardcoded pixel count -- so a
    mutation that drops the adjustment (e.g. always returning the declared box) is caught by the
    `!=` precondition below. This inheritance is what right-justifies a value widget's trailing
    unit (the tank '%').
    """
    data = _load(BUILDS["13.6"])
    scr = layout.Screen(data)
    rec = _first_labelled_record(data)
    assert rec is not None, "no labelled, glyph-resolvable record found in the corpus"

    width = metrics.text_width(data, rec.font, rec.label)
    pen_x, pen_y = 100, 50
    c = [pen_x, pen_y, pen_x + 37, 0, 0, 0]     # x1 (c[2]) arbitrary and != pen_x + width,
                                                 # so a left/right mix-up would be caught too

    # Exercise the non-path-1 text-width branch (a value/unit type, wtype 0): a path-1 type
    # (wtype 11 '   HOME') would instead take the box-geometry align branch (Finding 24). rec's
    # own align is set to 0 to prove the box aligns off the PERSISTENT state.align, not rec.align.
    assert not layout._align_path1(0)
    rec0 = dataclasses.replace(rec, wtype=0, align=0)
    for align in (0, 1):
        state = layout._State(font=rec.font)
        state.align = align                     # persistent align in effect (inherited)
        assert scr._placed_label_box(state, rec0, c) == (pen_x, c[2]), f"state.align={align}"

    for align in (2, 3):
        state = layout._State(font=rec.font)
        state.align = align                     # an align-0 record inherits this centre/right
        expected_x0 = metrics.aligned_x0(pen_x, width, align)
        assert expected_x0 != pen_x, "test precondition: alignment must actually shift x0"
        assert scr._placed_label_box(state, rec0, c) == (expected_x0, expected_x0 + width), \
            f"state.align={align}"


def test_walk_shifts_aligned_labels_off_their_declared_pen(climate_placed) -> None:
    """End-to-end: walking a real screen (Climate) actually wires `_placed_label_box` into
    `Placed.x0`/`x1`, not just leaving it reachable-but-unused. Compares the real walk against
    the same walk with alignment disabled (patched to return the declared box verbatim, i.e.
    the pre-Task-2 behaviour) so the expected shift is derived from the bytes via
    `metrics.aligned_x0`/`text_width`, never hardcoded -- and a regression to the old
    always-left-aligned placement collapses `shifted` to empty, failing the first assertion.
    """
    data = _load(BUILDS["13.6"])
    root = layout.climate_root(data)

    baseline_scr = layout.Screen(data)
    baseline_scr._placed_label_box = lambda state, rec, c: (c[0], c[2])
    baseline = {p.record_offset: (p.x0, p.x1) for p in baseline_scr.walk(root)}

    aligned = {p.record_offset: p for p in climate_placed}

    shifted = [off for off in aligned if aligned[off].x0 != baseline[off][0]
               or aligned[off].x1 != baseline[off][1]]
    assert shifted, "no record's own placement changed -- alignment is not wired into Placed"

    # each shift is one of: (a) box-geometry align (path-1 types shifted by their own box width,
    # persistent align 2/3 -- FUN_00257BFC align block), (b) text-width align (type-0/8 value/text
    # placed by glyph width, own align 2/3), or (c) a small frame cascade -- an upstream aligned
    # box's aligned frame feeds `_update_frame`, nudging a downstream record's own pen a pixel or
    # two. At least one must be a DIRECT alignment match (proves the wiring), never hardcoded.
    direct = 0
    for off in shifted:
        p = aligned[off]
        bx0, bx1 = baseline[off]
        w = bx1 - bx0
        box3 = (bx0 - w, bx0)                              # right-justify by geometry width
        box2 = (bx0 - w // 2, bx1 - w // 2)               # centre by geometry width
        if (p.x0, p.x1) in (box3, box2):
            direct += 1
            continue
        if p.label is not None and p.font is not None and p.align in (2, 3):
            tw = metrics.text_width(data, p.font, p.label)
            if tw is not None:
                tx0 = metrics.aligned_x0(bx0, tw, p.align)
                if (p.x0, p.x1) == (tx0, tx0 + tw):
                    direct += 1
                    continue
        assert abs(p.x0 - bx0) <= 2 and abs(p.x1 - bx1) <= 2, (
            f"offset {off:#x} shifted {baseline[off]} -> ({p.x0},{p.x1}) by no known mechanism")
    assert direct, "no record matched a direct alignment formula -- alignment not wired"


def test_inverter_block_is_count_bounded_and_empty_when_off() -> None:
    """The HOME inverter block (obj 899's type-6 switch, value_slot 113 -> Progressive Inverter
    register 0x2003A62B) is gated by the ordinary value-match switch, bounded by its own decoded
    count (=2, candidates obj 1409 mv=1 / 1410 mv=2). Progressive Inverter OFF drives slot 113=0,
    which matches neither candidate; the base's match value is not 0xFFFF, so the firmware renders
    the 0xFFFF no-op -> the block is EMPTY (matching the owner's coach HOME.jpg). Driving 113=1
    shows the Inverter widget. Non-vacuous against the OLD unbounded heuristic, which over-scanned
    past the count into the inner mode switch and matched obj 1412 (mv=0) -> a spurious 'Disabled'."""
    data = _load(BUILDS["13.6"])

    def labels(sv):
        placed = layout.Screen(data, match_table_off=layout.MATCH_TABLE_136).walk(4, slot_values=sv)
        return {p.label.strip() for p in placed if p.label and p.label.strip()}

    assert layout.variable_register(data, 113) == (0x2003A62B, 0)
    base = {5: 8, 112: 1}
    off = labels({**base, 113: 0})
    assert "Inverter" not in off and "Disabled" not in off and "N/A" not in off   # empty
    on = labels({**base, 113: 1})
    assert "Inverter" in on                                                        # widget shows


def test_value_unit_inherits_persistent_align_for_placement() -> None:
    """A value widget's trailing unit is placed by the PERSISTENT align (FUN_002557C0 reads
    state+9), the value-widget analog of Finding 24. The value (wt-8) is align 3 and sets the
    persistent align; the tank '%' (wt-0, rec.align 0) INHERITS it and right-justifies -- its box
    becomes exactly the glyph width with its right edge at the pen, tucking it under the tank
    rather than drawing left-aligned off to the right. The battery 'V' (wt-0, rec.align 1)
    OVERRIDES the persistent align back to left and stays a left-anchored point in its box.
    Non-vacuous: the old code used rec.align, so the align-0 '%' never shifted (a zero-width point);
    this asserts it now carries the text width and moved left."""
    from fireflymcf import metrics
    data = _load(BUILDS["13.6"])
    cfg = {5: 8, 37: 1, 108: 1, 110: 1, 112: 1, 6: 1, 113: 0}
    placed = layout.Screen(data, match_table_off=layout.MATCH_TABLE_136).walk(4, slot_values=cfg)

    pct = [p for p in placed if (p.label or "").strip() == "%"]
    assert len(pct) == 4, "four tank % units on HOME"
    for p in pct:
        w = metrics.text_width(data, p.font, "%")
        assert w and p.x1 - p.x0 == w        # right-justified: box == glyph width, not a point
        # its right edge is at the value pen; before the fix it drew left-aligned from there
        assert p.x0 < p.x1

    v = [p for p in placed if (p.label or "").strip() == "V"]
    assert v, "battery V present"
    for p in v:
        assert p.x0 == p.x1                  # align 1 overrides to left: declared point, no shift


def test_apply_colors_inherits_mode0_and_scopes_mode5() -> None:
    """GUICODE Finding 29: a mode-0 colour field inherits the persistent slot's last explicit
    value; an explicit mode writes the slot; a mode-5 field is transparency (not a colour to
    inherit) so it stays unresolved. `_apply_colors` returns the effective fields with mode-0
    substituted. Non-vacuous: a stub that ignored the slot state fails the inherit assert."""
    state = layout._State()
    # an explicit fillA writes the slot
    e1 = layout._apply_colors(state, [("fillA", 7, 8), ("fillB", 3, 0x00A79D)])
    assert e1 == [("fillA", 7, 8), ("fillB", 3, 0x00A79D)]
    assert state.fill_slots["fillA"] == (7, 8)
    # a later mode-0 fillA inherits (7,8); a mode-5 fillB stays (5, None) (transparent, not filled)
    e2 = layout._apply_colors(state, [("fillA", 0, None), ("fillB", 5, None)])
    assert e2 == [("fillA", 7, 8), ("fillB", 5, None)]
    # mode-0 with nothing yet in the slot is left as-is (not invented)
    assert layout._apply_colors(layout._State(), [("fillA", 0, None)]) == [("fillA", 0, None)]


def test_colour_inheritance_is_parent_scoped_no_cross_sibling_leak() -> None:
    """The persistent colour slots are parent-scoped: a child inherits its parent container's
    colour, but a child's own colour writes are restored after the recursion so they do NOT leak
    into the parent's later siblings (GUICODE Finding 29). Real anchor: ELECTRICAL 'Start'/'Stop'
    (obj 249/251) have a mode-0 fillB; without save/restore they inherited the header's red
    (idx4) from far back and rendered red -- the coach shows them not-red. With scoping their
    fillB stays unresolved (mode 0, nothing in scope). Non-vacuous: dropping the save/restore
    re-leaks idx4 and this asserts fillB is NOT the red index 4."""
    from fireflymcf import cli
    data = _load(BUILDS["13.6"])
    root = cli._resolve_page_root(data, "electrical")
    placed = layout.Screen(data, match_table_off=layout.MATCH_TABLE_136).walk(
        root, slot_values={5: 8, 37: 1})
    starts = [p for p in placed if (p.label or "").strip() in ("Start", "Stop") and p.x0 < 320]
    assert starts, "Start/Stop labels not placed"
    for p in starts:
        fillb = next((v for r, m, v in p.colors if r == "fillB"), "none")
        fillb_mode = next((m for r, m, v in p.colors if r == "fillB"), None)
        # must NOT have leaked the header red (mode-7 idx 4); stays mode-0 unresolved
        assert not (fillb_mode == 7 and fillb == 4), f"{p.label!r} leaked the header red idx4"
