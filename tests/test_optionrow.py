"""Tests for `optionrow.build_config_toggle` -- cloning group 129's config-toggle
group+bindings for an arbitrary CAN selector, growing the binding table by four entries.
"""
import os
import struct

import pytest

from fireflymcf import container, coverage, guidata, insert, patch, records, relocate
from fireflymcf import render, statusbind
from fireflymcf import layout, optionrow

# The stock Heat Pump OPTIONS row on object 46: switch=rec6 (wtype-6, child 394, value_slot
# 107, count 3), label=rec7 (wtype-11 '   Heat Pump'), touch=rec8 (wtype-12). The switch's
# three candidate arms are objects 394/395/396 (match values 0/1/2), so value 0 draws the
# empty box (394) and value 1 the checked glyph (395) -- see the OPEN INVESTIGATION test.
OPTIONS_OBJECT = 46
DONOR_SWITCH_REC = 6
DONOR_SWITCH_CHILD = 394

# Group 129's four bindings on 13.6 (the 0x2F "Low Volts" config toggle), byte-for-byte.
DONOR_129 = [
    b"\xd9\xfe\x01\x0a\x0f\x2f\x96\xff\xea\xd1\x00\x00",
    b"\xd9\xfe\x11\x0a\x0f\x2f\x96\xff\xea\xd1\x00\x01",
    b"\xd9\xfe\x01\x0a\x0f\x2f\x96\xff\xff\xd3\xff\xff",
    b"\xd9\xfe\x11\x0a\x0f\x2f\x96\xff\xff\xd3\xff\xff",
]


def test_config_toggle_clones_129_with_selector_retargeted(mcf_136):
    tog = optionrow.build_config_toggle(mcf_136, selector=0xEE)
    # four bindings, byte-identical to group 129's except selector byte [5] == 0xEE
    for got, want in zip(tog.bindings, DONOR_129):
        assert got == want[:5] + b"\xee" + want[6:]
    assert len(tog.bindings) == 4
    assert tog.touch_id == tog.new_group_index


def test_grown_image_passes_every_relocator_gate(mcf_136):
    """The grown image the module produces must pass all of relocate's self-check gates,
    and `patch.reseal` must predict exactly the checksum set the module already resealed."""
    tog = optionrow.build_config_toggle(mcf_136, selector=0xEE)
    grown = tog.grown

    # image size is unchanged (growth is absorbed from the padding arena, not appended)
    assert len(grown) == len(mcf_136)

    # every relocate gate the shared machinery exposes
    relocate._verified_pointer_tables(grown)
    ok, reason = relocate._deep_coverage_ok(grown)
    assert ok, reason
    ok, reason = relocate._boundary_oracle(grown)
    assert ok, reason
    # nothing at or after the pinned image container moved, so object-pointer tables and
    # GuiCode pointers must be byte-identical (verified against at=IMG, k=0).
    ok, reason = relocate._verify_table_shifts(
        mcf_136, grown, coverage.IMAGE_CONTAINER_OFFSET, 0)
    assert ok, reason
    ok, reason = relocate._verify_guicode_pointers(
        mcf_136, grown, coverage.IMAGE_CONTAINER_OFFSET, 0)
    assert ok, reason

    # reseal predicts exactly the one checksum a same-size GuiData rewrite changes.
    _, changed = patch.reseal(grown)
    assert changed == [], f"module handed back an un-resealed image: {changed}"
    # ...and starting from the module's PRE-reseal edits, reseal changes exactly GuiData's.
    # (proven indirectly: a second reseal is now a no-op, above.)


def test_growth_appends_four_bindings_and_repoints_group_164(mcf_136):
    tog = optionrow.build_config_toggle(mcf_136, selector=0xEE)
    grown = tog.grown

    before_bt = guidata.binding_table(mcf_136)
    after_bt = guidata.binding_table(grown)
    assert len(after_bt) == len(before_bt) + 4
    # the four appended entries sit at indices 746..749 and carry the retargeted selector
    for i, want in zip(range(len(before_bt), len(before_bt) + 4), DONOR_129):
        assert after_bt[i].raw == want[:5] + b"\xee" + want[6:]

    # group 164 (an unused slot) now points at the four new bindings, group-129 shape
    g = guidata.group_table(grown)[164]
    assert g.start == len(before_bt)
    assert g.count == 4
    assert g.raw == bytes([0x11, 0x80, 0x0c, 0x80]) + struct.pack("<H", len(before_bt)) \
        + bytes([4, 0])
    assert tog.new_group_index == 164

    # the variable table content is preserved byte-for-byte (only shifted in place)
    assert [v.raw for v in guidata.variable_table(grown)] == \
        [v.raw for v in guidata.variable_table(mcf_136)]

    # the image sub-container is still pinned at its fixed offset
    img = coverage.IMAGE_CONTAINER_OFFSET
    assert struct.unpack_from("<I", grown, gs_of(mcf_136) + img)[0] == \
        coverage.IMAGE_CONTAINER_MAGIC


def gs_of(data):
    return guidata.guidata_extent(data)[0]


def test_refuses_non_byte_selector(mcf_136):
    for bad in (-1, 256, 0x1234, "ee"):
        with pytest.raises(optionrow.OptionRowError):
            optionrow.build_config_toggle(mcf_136, selector=bad)


def test_refuses_selector_that_already_has_a_group(mcf_136):
    # 0x2F is the donor group 129's own selector -- already in use.
    with pytest.raises(optionrow.OptionRowError):
        optionrow.build_config_toggle(mcf_136, selector=0x2F)


# ======================================================================================
# find_config_toggle -- fix round 1 (Task 9, finding C1): the predicate build_config_toggle
# uses privately to refuse a second toggle on the same selector, factored public so a later
# caller can ADOPT an existing group instead of refusing.
# ======================================================================================

def test_find_config_toggle_returns_none_when_no_group_matches(mcf_136):
    assert optionrow.find_config_toggle(mcf_136, 0xEE) is None


def test_find_config_toggle_finds_the_stock_low_volts_donor_by_its_own_selector(mcf_136):
    # Group 129 IS a config-toggle group (all four bindings 0xFED9/0x2F) -- find_config_toggle
    # must locate it by the same predicate build_config_toggle's donor check uses.
    assert optionrow.find_config_toggle(mcf_136, 0x2F) == 129


def test_find_config_toggle_finds_a_freshly_built_toggle(mcf_136):
    tog = optionrow.build_config_toggle(mcf_136, selector=0xEE)
    assert optionrow.find_config_toggle(tog.grown, 0xEE) == tog.new_group_index


def test_find_config_toggle_is_what_build_config_toggle_now_refuses_on(mcf_136):
    """build_config_toggle's own "already has a config-toggle group" refusal and
    find_config_toggle's non-None return must agree -- one predicate, both call sites."""
    tog = optionrow.build_config_toggle(mcf_136, selector=0xEE)
    assert optionrow.find_config_toggle(tog.grown, 0xEE) is not None
    with pytest.raises(optionrow.OptionRowError, match="already has a config-toggle group"):
        optionrow.build_config_toggle(tog.grown, selector=0xEE)


def test_find_config_toggle_refuses_when_ambiguous(mcf_136):
    """Two groups both fully config-toggle-shaped on the same selector: find_config_toggle
    must refuse rather than guess which one a caller should adopt. Synthesised by retargeting
    a second stock config-toggle-shaped group (164, itself an unrelated 0xFEDB pair on this
    build) onto 0x2F -- group 129's own selector -- so both qualify."""
    gs = guidata.guidata_extent(mcf_136)[0]
    bt = guidata.binding_table(mcf_136)
    g164 = guidata.group_table(mcf_136)[164]
    out = bytearray(mcf_136)
    for j in range(g164.start, g164.start + g164.count):
        struct.pack_into("<H", out, gs + bt[j].offset, optionrow.CONFIG_TOGGLE_DGN_LOW)
        out[gs + bt[j].offset + 5] = 0x2F
    with pytest.raises(optionrow.OptionRowError, match="ambiguous"):
        optionrow.find_config_toggle(bytes(out), 0x2F)


def test_refuses_when_donor_group_is_not_the_0x2f_toggle(mcf_136):
    """The donor is pinned by identity, not just shape: if group 129 does not carry the 0x2F
    'Low Volts' selector on this build, the module refuses rather than clone a wrong donor.
    Synthesised by flipping group 129's selector byte (byte[5]) away from 0x2F."""
    gs = guidata.guidata_extent(mcf_136)[0]
    bt = guidata.binding_table(mcf_136)
    g = guidata.group_table(mcf_136)[129]
    out = bytearray(mcf_136)
    for j in range(g.start, g.start + g.count):
        out[gs + bt[j].offset + 5] = 0x40      # a selector that is NOT the 0x2F toggle
    with pytest.raises(optionrow.OptionRowError):
        optionrow.build_config_toggle(bytes(out), selector=0xEE)


def test_edits_are_same_size_and_reproduce_grown(mcf_136):
    """Because the growth is absorbed from padding, every edit is a same-size overwrite;
    applying them to stock reproduces the grown image exactly."""
    tog = optionrow.build_config_toggle(mcf_136, selector=0xEE)
    out = bytearray(mcf_136)
    for e in tog.edits:
        assert len(e.old) == len(e.new), (e.structure, len(e.old), len(e.new))
        assert bytes(out[e.file_offset:e.file_offset + len(e.old)]) == e.old, e.structure
        out[e.file_offset:e.file_offset + len(e.new)] = e.new
    assert bytes(out) == tog.grown


# --------------------------------------------------------------------------------------
# append_row -- clone a stock OPTIONS checkbox row (switch + label + touch) onto a page
# --------------------------------------------------------------------------------------
def _appended_records(data, before_len):
    """Records of the freshly-appended top-index object. The append never shifts an existing
    index, so the new object is always the last table entry."""
    gs = guidata.guidata_extent(data)[0]
    tab = guidata.object_tables(data)[1]
    assert len(tab) == before_len + 1
    return records.object_records(data, gs + tab[-1]), len(tab) - 1


def test_append_row_clones_the_options_triple(mcf_136):
    before = len(guidata.object_tables(mcf_136)[1])
    display_slot = statusbind.spare_cell_indices(mcf_136)[0]

    out, new_idx, edits = optionrow.append_row(
        mcf_136, parent_obj=OPTIONS_OBJECT, donor_switch_obj=OPTIONS_OBJECT,
        donor_switch_rec=DONOR_SWITCH_REC, label="Pump Link", touch_id=164,
        display_slot=display_slot, x=100, y=300)

    recs, top = _appended_records(out, before)
    assert new_idx == top

    # the wtype-6 switch: value_slot repointed to display_slot, child 394 (+ count) kept
    sw = next(r for r in recs if r.wtype == 6)
    assert sw.value_slot == display_slot
    assert sw.child_index == DONOR_SWITCH_CHILD
    assert set(sw.in_regs) == {0}          # absolute-anchored (no pen register add)
    assert sw.align == 1                   # leading left-align reset

    # the wtype-11 label rewritten to "Pump Link"
    lbl = next(r for r in recs if r.wtype == 11)
    assert records.label_text(lbl.label).strip() == "Pump Link"

    # the wtype-12 touch region with its id set to touch_id (id is the trailing u16)
    tch = next(r for r in recs if r.wtype == 12)
    assert (out[tch.end - 2] | (out[tch.end - 1] << 8)) == 164

    # the object table grew by exactly one entry (append_object, not a shift)
    assert len(guidata.object_tables(out)[1]) == before + 1


def test_append_row_does_not_shift_any_existing_child_index(mcf_136):
    """A spot-check unrelated object's records are byte-identical pre/post: insert_object
    appends at the TOP index, so no existing child_index moves."""
    display_slot = statusbind.spare_cell_indices(mcf_136)[0]
    gs0 = guidata.guidata_extent(mcf_136)[0]
    tab0 = guidata.object_tables(mcf_136)[1]

    out, _idx, _edits = optionrow.append_row(
        mcf_136, parent_obj=OPTIONS_OBJECT, donor_switch_obj=OPTIONS_OBJECT,
        donor_switch_rec=DONOR_SWITCH_REC, label="Pump Link", touch_id=164,
        display_slot=display_slot, x=100, y=300)
    gs1 = guidata.guidata_extent(out)[0]
    tab1 = guidata.object_tables(out)[1]

    for spot in (315, 285, 100):
        a = records.object_records(mcf_136, gs0 + tab0[spot])
        b = records.object_records(out, gs1 + tab1[spot])
        assert mcf_136[a[0].offset:a[-1].end] == out[b[0].offset:b[-1].end]
        assert [r.child_index for r in a] == [r.child_index for r in b]


# ========================================================================================
# B2.3 -- render + gate acceptance: build_config_toggle (B2.1) and append_row (B2.2)
# compose onto stock into ONE valid image, at the real (x, y) placement.
# ========================================================================================
#
# Placement survey (rendered, not assumed): stock object 46's six checkbox rows anchor
# their switch at x=95 (left column: Heat Pump, Bunk Accent, Bath Fan, Cargo/Bath Light,
# Bath Light) or x=335 (right column: Black Tank), on a steady 56px vertical grid --
# y = 88, 144, 200, 256, 312 in the left column (see the touch-box survey in
# `test_pump_link_row_placement_clears_every_existing_options_touch_region` below, which
# reads these from a stock render rather than hard-coding them). The bottom nav row
# (Floorplans / Main Options / Electrical Options / Go Power! Settings) starts at y=420.
# Continuing the same 56px grid one row below "Bath Light" lands at y=368, whose touch box
# [88,343)-[318,393) clears both the last stock row (ends at y=337) and the nav row (starts
# at y=420) with room to spare.
PUMP_LINK_X, PUMP_LINK_Y = 95, 368


def _combined_image(mcf_136):
    """Compose B2.1 (the 0xEE config-toggle group, reusing slot 164) with B2.2 (the cloned
    checkbox row) onto stock `mcf_136`, returning `(tog, out, new_idx, display_slot)`.
    `out` is the single, doubly-resealed image both later tasks build on."""
    tog = optionrow.build_config_toggle(mcf_136, selector=0xEE)
    display_slot = statusbind.spare_cell_indices(tog.grown)[0]
    out, new_idx, _edits = optionrow.append_row(
        tog.grown, parent_obj=OPTIONS_OBJECT, donor_switch_obj=OPTIONS_OBJECT,
        donor_switch_rec=DONOR_SWITCH_REC, label="Pump Link", touch_id=tog.touch_id,
        display_slot=display_slot, x=PUMP_LINK_X, y=PUMP_LINK_Y)
    return tog, out, new_idx, display_slot


def test_pump_link_row_placement_clears_every_existing_options_touch_region(mcf_136):
    """Survey object 46's STOCK touch regions first (rendered, not assumed), then prove the
    chosen (x, y) puts the new row's touch box outside every one of them -- unlike B2.2's
    own test fixture (100, 300), which lands ON the stock 'Bunk Accent' row's touch box."""
    stock = layout.Screen(mcf_136, match_table_off=layout.MATCH_TABLE_136).walk(OPTIONS_OBJECT)
    stock_touches = [(p.x0, p.y0, p.x1, p.y1) for p in stock if p.wtype == 12]
    # the six stock checkbox rows + the four bottom-nav buttons
    assert len(stock_touches) >= 10, f"expected >=10 stock touch boxes, found {stock_touches}"

    row_x0 = PUMP_LINK_X + optionrow._ROW_TOUCH_DX
    row_y0 = PUMP_LINK_Y + optionrow._ROW_TOUCH_DY
    new_box = (row_x0, row_y0, row_x0 + optionrow._ROW_TOUCH_W, row_y0 + optionrow._ROW_TOUCH_H)

    def overlaps(a, b):
        return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]

    offenders = [box for box in stock_touches if overlaps(new_box, box)]
    assert not offenders, f"chosen row {new_box} overlaps existing touch box(es) {offenders}"

    # B2.2's own fixture (100, 300) is the counter-example this placement must NOT repeat:
    # prove it really does collide with a stock row, so the assertion above is non-vacuous.
    old_x0, old_y0 = 100 + optionrow._ROW_TOUCH_DX, 300 + optionrow._ROW_TOUCH_DY
    old_box = (old_x0, old_y0, old_x0 + optionrow._ROW_TOUCH_W, old_y0 + optionrow._ROW_TOUCH_H)
    assert any(overlaps(old_box, box) for box in stock_touches), \
        "expected B2.2's (100, 300) fixture to collide with a stock row"


def test_combined_image_renders_the_row_and_passes_every_gate(mcf_136):
    """The B2.3 acceptance test: B2.1 + B2.2 compose into ONE image that (a) renders the
    checkbox + 'Pump Link' label at the chosen (x, y) in BOTH display states, (b) passes
    every relocator self-check gate, and (c) is exactly, and idempotently, resealed."""
    tog, out, new_idx, display_slot = _combined_image(mcf_136)

    # image grew (append_row is a real insertion, unlike B2.1's same-size window rewrite)
    assert len(out) > len(tog.grown)

    # --- B2.1's toggle-group growth survives the append_row composition, unchanged -------
    # `build_config_toggle`'s edit is confined below GuiData+0x10000 (the padding-arena
    # window; module docstring), while `append_row`'s insert operates entirely at/above it
    # (the object region) -- so nothing here should touch the binding table or group 164.
    # Read both back from the FINAL composed `out` and compare against `tog.grown` (B2.1's
    # own output) to prove that survives the composition, rather than assume it: a future
    # regression that corrupted the toggle group while appending the row would otherwise
    # pass every other assertion in this test silently.
    assert guidata.binding_table(out) == guidata.binding_table(tog.grown)
    assert guidata.group_table(out)[164] == guidata.group_table(tog.grown)[164]

    # --- (a) render.screen_placements shows the checkbox and the label at (x, y) ---------
    # The checkbox is a wt-0x10 circle pair spliced in from whichever match-arm child object
    # the switch selects (394 empty / 395 checked); object 394 is a SHARED match-arm child
    # every stock row also splices in (at ITS OWN row's position) when its own switch reads
    # cell 0, so disambiguating by `screen_placements`' Prim tag alone is not anchor-specific.
    # Use the same stricter idiom `test_append_row_switch_renders_empty_vs_checked` (B2.2)
    # already established: `layout.Screen(...).walk()` -- the exact walk `screen_placements`
    # is built on -- filtered to `obj_index in (394, 395, 396)` AND an exact `(x0, y0)` match
    # on the row anchor.
    def walked(cell):
        return layout.Screen(out, match_table_off=layout.MATCH_TABLE_136).walk(
            OPTIONS_OBJECT, slot_values={display_slot: cell})

    def drew(placed):
        return {p.obj_index for p in placed if p.obj_index in (394, 395, 396)
                and p.x0 == PUMP_LINK_X and p.y0 == PUMP_LINK_Y}

    placed_off, placed_on = walked(0), walked(1)
    assert drew(placed_off) == {394}, f"cell=0 should draw the empty box; drew {drew(placed_off)}"
    assert drew(placed_on) == {395}, f"cell=1 should draw the checked glyph; drew {drew(placed_on)}"

    # the label + touch sit at the row origin (byte-derived box, not just "present")
    row = {p.wtype: (p.x0, p.y0, p.x1, p.y1) for p in placed_off if p.obj_index == new_idx}
    assert row[11] == (PUMP_LINK_X + optionrow._ROW_LABEL_DX, PUMP_LINK_Y + optionrow._ROW_LABEL_DY,
                       PUMP_LINK_X + optionrow._ROW_LABEL_DX + optionrow._ROW_LABEL_W,
                       PUMP_LINK_Y + optionrow._ROW_LABEL_DY + optionrow._ROW_LABEL_H)
    assert row[12] == (PUMP_LINK_X + optionrow._ROW_TOUCH_DX, PUMP_LINK_Y + optionrow._ROW_TOUCH_DY,
                       PUMP_LINK_X + optionrow._ROW_TOUCH_DX + optionrow._ROW_TOUCH_W,
                       PUMP_LINK_Y + optionrow._ROW_TOUCH_DY + optionrow._ROW_TOUCH_H)

    # and `render.screen_placements` itself (not just the walk it is built on) really does
    # include the "Pump Link" label text, in BOTH display states.
    def has_label(cell):
        prims = render.screen_placements(
            out, OPTIONS_OBJECT, match_table_off=layout.MATCH_TABLE_136,
            slot_values={display_slot: cell})[0].prims
        return any(p.kind == "text" and "Pump Link" in p.text for p in prims)

    assert has_label(0) and has_label(1)

    # --- (b) every relocator self-check gate on the COMBINED image -----------------------
    # `_verify_table_shifts`/`_verify_guicode_pointers` model a single flat shift point,
    # which does not apply here: append_row's insert grows the image via TWO nested
    # insertions (the object-table append, then the attach record into object 46's own
    # stream), so pointers split into more than two shift bands (proven empirically while
    # building this test). The gates below are the ones `tests/test_insert.py` uses for
    # exactly this shape of change (an insertion, not an in-place rewrite): self-consistent
    # pointer tables, full zero-unclaimed deep coverage, the decoder boundary oracle, exact
    # object-graph equivalence (the growth-aware analogue of a table-shift check), and a
    # container re-parse (the CRCs verify).
    relocate._verified_pointer_tables(out)
    report = coverage.analyse(out, deep=True)
    assert report.unclaimed == [], report.unclaimed
    assert report.claimed == report.total
    ok, reason = relocate._boundary_oracle(out)
    assert ok, reason

    before_graph = insert.object_child_graph(tog.grown)
    after_graph = insert.object_child_graph(out)
    assert after_graph != before_graph, "graph did not change; the check below would be vacuous"
    expected_graph = dict(before_graph)
    expected_graph[OPTIONS_OBJECT] = sorted(before_graph[OPTIONS_OBJECT] + [new_idx])
    expected_graph[new_idx] = [DONOR_SWITCH_CHILD]     # the switch's own match-arm child_index
    assert after_graph == expected_graph

    c = container.parse_container(out)
    assert any(guidata.GUIDATA_SECTION_MARKER in s.name for s in c.sections)

    # --- (c) reseal predicts exactly the changed checksum set (none -- already resealed) --
    _, changed = patch.reseal(out)
    assert changed == [], f"combined image handed back un-resealed: {changed}"
    _, changed_again = patch.reseal(out)
    assert changed_again == [], "a second reseal is not a no-op"


def test_combined_image_renders_to_png_for_eyeballing(mcf_136):
    """Not a correctness gate -- a rendered PNG of object 46 in both display states, so the
    placement can be eyeballed. Written to `build/experiment-images/` (gitignored)."""
    _tog, out, _new_idx, display_slot = _combined_image(mcf_136)
    out_dir = os.path.join("build", "experiment-images")
    os.makedirs(out_dir, exist_ok=True)
    for cell, suffix in ((0, "off"), (1, "on")):
        img = render.render_screen_image(
            out, OPTIONS_OBJECT, match_table_off=layout.MATCH_TABLE_136,
            slot_values={display_slot: cell})
        img.save(os.path.join(out_dir, f"options-pumplink-{suffix}.png"))
    assert os.path.exists(os.path.join(out_dir, "options-pumplink-off.png"))
    assert os.path.exists(os.path.join(out_dir, "options-pumplink-on.png"))


def test_append_row_switch_renders_empty_vs_checked(mcf_136):
    """OPEN INVESTIGATION: a wtype-6 value-match switch inside a freshly-APPENDED child object
    must resolve its value_slot -> cell and swap child 394 (empty) <-> 395 (checked). Render
    object 46 with the appended row in BOTH display states and confirm the swap, that the
    'Pump Link' label sits on the row, and that nothing else on the page moved."""
    before = len(guidata.object_tables(mcf_136)[1])
    display_slot = statusbind.spare_cell_indices(mcf_136)[0]
    x, y = 100, 300

    out, new_idx, _edits = optionrow.append_row(
        mcf_136, parent_obj=OPTIONS_OBJECT, donor_switch_obj=OPTIONS_OBJECT,
        donor_switch_rec=DONOR_SWITCH_REC, label="Pump Link", touch_id=164,
        display_slot=display_slot, x=x, y=y)

    def render(cell):
        placed = layout.Screen(out).walk(OPTIONS_OBJECT, slot_values={display_slot: cell})
        # which switch candidate object drew at the row anchor (x, y)
        drew = {p.obj_index for p in placed
                if p.obj_index in (394, 395, 396) and p.x0 == x and p.y0 == y}
        # the appended row's own label + touch boxes
        row = {p.wtype: (p.x0, p.y0, p.x1, p.y1) for p in placed if p.obj_index == new_idx}
        return drew, row

    empty, row0 = render(0)
    checked, row1 = render(1)

    assert empty == {394}, f"display_slot=0 should draw the empty box (394); drew {empty}"
    assert checked == {395}, f"display_slot=1 should draw the checked glyph (395); drew {checked}"

    # the label + touch are present and identical between states (only the switch face swapped)
    assert 11 in row0 and 12 in row0
    assert row0 == row1

    # nothing else on the page moved: every stock placement (bar the appended row's own
    # new object, and the switch face that legitimately swaps at the row anchor) is preserved.
    def other_boxes(placed):
        return {(p.obj_index, p.wtype, p.x0, p.y0, p.x1, p.y1) for p in placed
                if p.obj_index != new_idx and not (p.obj_index in (394, 395) and p.y0 == y)}

    stock = other_boxes(layout.Screen(mcf_136).walk(OPTIONS_OBJECT))
    after = other_boxes(layout.Screen(out).walk(OPTIONS_OBJECT, slot_values={display_slot: 0}))
    assert stock <= after, "an existing widget moved when the row was appended"


# ========================================================================================
# build_config_stepper -- clone the stock 0x0E +/-300 arrow groups (103/104) onto any
# selector with a +/-step delta. Task 4 of the pump-auto-off plan.
# ========================================================================================
def test_build_config_stepper_emits_the_exact_delta_wire_bytes():
    """The mistake PC.47 exists to prevent: the minute deltas must be +/-5 in the LOW
    byte (0x0005 / 0xFFFB), never +/-0x0500."""
    from fireflymcf import optionrow
    data = open("firefly-13.6/mcf.bin", "rb").read()
    st = optionrow.build_config_stepper(data, 0xEE, 5)

    plus_press, plus_release, plus_read, minus_press, minus_release, minus_read = st.bindings
    assert plus_press.hex() == "d9fe05060fee96ffffd20005"
    assert minus_press.hex() == "d9fe05060fee96ffffd2fffb"
    # release and read-back are the donors' own bytes with only the selector changed
    assert plus_release.hex() == "d9fe010a0fee96ffead20000"
    assert plus_read.hex() == "d9fe050e0fee96ffffd3ffff"
    assert minus_release == plus_release
    assert minus_read == plus_read


def test_build_config_stepper_keeps_every_donor_field_but_selector_and_value():
    from fireflymcf import guidata, optionrow
    data = open("firefly-13.6/mcf.bin", "rb").read()
    bt = guidata.binding_table(data)
    donor_plus = bt[388].raw
    st = optionrow.build_config_stepper(data, 0xEE, 5)
    built = st.bindings[0]
    for i in range(12):
        if i in (optionrow.SELECTOR_FIELD, 10, 11):
            continue
        assert built[i] == donor_plus[i], f"byte {i} drifted from the donor"


def test_build_config_stepper_registers_both_groups_at_free_slots():
    from fireflymcf import guidata, newbutton, optionrow
    data = open("firefly-13.6/mcf.bin", "rb").read()
    free_before = set(newbutton.free_group_slots(data))
    st = optionrow.build_config_stepper(data, 0xEE, 5)

    assert st.plus_group in free_before
    assert st.minus_group in free_before
    assert st.plus_group != st.minus_group
    assert st.plus_touch_id == st.plus_group
    assert st.minus_touch_id == st.minus_group

    gt = guidata.group_table(st.grown)
    bt = guidata.binding_table(st.grown)
    for gid, expect in ((st.plus_group, "d9fe05060fee96ffffd20005"),
                        (st.minus_group, "d9fe05060fee96ffffd2fffb")):
        g = gt[gid]
        assert g.count == 3
        assert bt[g.start].raw.hex() == expect


def test_build_config_stepper_grows_the_binding_table_by_six():
    from fireflymcf import guidata, optionrow
    data = open("firefly-13.6/mcf.bin", "rb").read()
    before = len(guidata.binding_table(data))
    st = optionrow.build_config_stepper(data, 0xEE, 5)
    assert len(guidata.binding_table(st.grown)) == before + 6
    assert len(st.grown) == len(data)


def test_build_config_stepper_refuses_a_step_that_crosses_a_byte():
    """A step big enough to carry out of the minute byte would corrupt the flag -- the
    whole point of the layout. 5 is fine; 256 is not."""
    import pytest
    from fireflymcf import optionrow
    data = open("firefly-13.6/mcf.bin", "rb").read()
    for bad in (0, 256, 300, -5):
        with pytest.raises(optionrow.OptionRowError):
            optionrow.build_config_stepper(data, 0xEE, bad)


def test_build_config_stepper_refuses_a_non_stock_donor():
    """If this build's group 104 is not the 0x0E +300 arrow, refuse rather than clone
    the wrong donor -- the 13.6-pinned-constants rule."""
    import pytest
    from fireflymcf import guidata, optionrow
    data = bytearray(open("firefly-13.6/mcf.bin", "rb").read())
    gs, _ = guidata.guidata_extent(bytes(data))
    bt = guidata.binding_table(bytes(data))
    # Binding.offset is GUIDATA-RELATIVE (binding 388 -> 0x19A8); it must be rebased
    # onto the section start to address the file. Verified in the pre-flight scan.
    data[gs + bt[388].offset + optionrow.SELECTOR_FIELD] = 0x99   # not 0x0E any more
    with pytest.raises(optionrow.OptionRowError):
        optionrow.build_config_stepper(bytes(data), 0xEE, 5)


# ======================================================================================
# toggle_to_flag_delta -- convert group 164's two D1 absolute writes to D2 deltas of
# -0x0100/+0x0100, so the toggle moves only the flag byte and leaves the packed
# minute count in the low byte untouched.
# ======================================================================================

def test_toggle_to_flag_delta_rewrites_both_writes_only():
    from fireflymcf import guidata, optionrow
    data = open("firefly-13.6/mcf.bin", "rb").read()
    toggle = optionrow.build_config_toggle(data, 0xEE)
    out, edits = optionrow.toggle_to_flag_delta(
        toggle.grown, toggle.new_group_index, 0xEE)

    gt = guidata.group_table(out)
    bt = guidata.binding_table(out)
    g = gt[toggle.new_group_index]
    raws = [bt[g.start + i].raw for i in range(g.count)]

    writes = [r for r in raws if r[optionrow.FUNCTION_FIELD] == optionrow.DELTA_FUNCTION]
    reads = [r for r in raws if r[optionrow.FUNCTION_FIELD] == 0xD3]
    assert len(writes) == 2 and len(reads) == 2

    deltas = sorted(struct.unpack_from(">h", r, optionrow.VALUE_FIELD)[0] for r in writes)
    assert deltas == [-256, 256], "the flag must move by 0x0100, not by 1"


def test_toggle_to_flag_delta_returns_a_uniform_list_of_edits():
    """The returned edits must all be `relocate.Edit` instances, not a mix of Edits and
    the plain checksum-label strings `patch.reseal` returns -- Task 9 collects this list
    into the recipe context and touches `.file_offset` on every entry."""
    from fireflymcf import optionrow, relocate
    data = open("firefly-13.6/mcf.bin", "rb").read()
    toggle = optionrow.build_config_toggle(data, 0xEE)
    _, edits = optionrow.toggle_to_flag_delta(toggle.grown, toggle.new_group_index, 0xEE)
    assert edits, "expected at least one edit"
    for e in edits:
        assert isinstance(e, relocate.Edit), f"{e!r} is not a relocate.Edit"
        assert isinstance(e.file_offset, int)


def test_toggle_to_flag_delta_leaves_the_read_backs_byte_identical():
    from fireflymcf import guidata, optionrow
    data = open("firefly-13.6/mcf.bin", "rb").read()
    toggle = optionrow.build_config_toggle(data, 0xEE)
    before = guidata.binding_table(toggle.grown)
    g = guidata.group_table(toggle.grown)[toggle.new_group_index]
    read_before = [before[g.start + i].raw for i in range(g.count)
                   if before[g.start + i].raw[optionrow.FUNCTION_FIELD] == 0xD3]

    out, _ = optionrow.toggle_to_flag_delta(toggle.grown, toggle.new_group_index, 0xEE)
    after = guidata.binding_table(out)
    read_after = [after[g.start + i].raw for i in range(g.count)
                  if after[g.start + i].raw[optionrow.FUNCTION_FIELD] == 0xD3]
    assert read_before == read_after


def test_toggle_to_flag_delta_is_same_size_and_minimal():
    from fireflymcf import guidata, optionrow
    data = open("firefly-13.6/mcf.bin", "rb").read()
    toggle = optionrow.build_config_toggle(data, 0xEE)
    out, _ = optionrow.toggle_to_flag_delta(toggle.grown, toggle.new_group_index, 0xEE)
    assert len(out) == len(toggle.grown)
    differing = [i for i in range(len(out)) if out[i] != toggle.grown[i]]
    # Not the arithmetically-obvious 8 (3 bytes/write binding + 2-byte checksum): the
    # absolute-0 write's low value byte is 0x00 (0x0000) and stays 0x00 under its delta
    # encoding too (-256 == 0xff00), so that write only changes 2 bytes (function +
    # high byte), not 3. 2 + 3 (the other write, all 3 bytes change) + 2 (checksum) = 7.
    assert len(differing) == 7, differing

    # The byte count is a proxy for "only the two writes changed" -- pin that directly,
    # since it's what would actually break if someone widened the edit.
    before = guidata.binding_table(toggle.grown)
    after = guidata.binding_table(out)
    g = guidata.group_table(toggle.grown)[toggle.new_group_index]
    read_before = [before[g.start + i].raw for i in range(g.count)
                   if before[g.start + i].raw[optionrow.FUNCTION_FIELD] == 0xD3]
    read_after = [after[g.start + i].raw for i in range(g.count)
                  if after[g.start + i].raw[optionrow.FUNCTION_FIELD] == 0xD3]
    assert read_before == read_after


def test_toggle_to_flag_delta_refuses_a_group_that_is_not_an_absolute_toggle():
    """Idempotence guard: running it twice would double the deltas."""
    import pytest
    from fireflymcf import optionrow
    data = open("firefly-13.6/mcf.bin", "rb").read()
    toggle = optionrow.build_config_toggle(data, 0xEE)
    once, _ = optionrow.toggle_to_flag_delta(toggle.grown, toggle.new_group_index, 0xEE)
    with pytest.raises(optionrow.OptionRowError):
        optionrow.toggle_to_flag_delta(once, toggle.new_group_index, 0xEE)


def test_toggle_to_flag_delta_refuses_a_wrong_selector():
    import pytest
    from fireflymcf import optionrow
    data = open("firefly-13.6/mcf.bin", "rb").read()
    toggle = optionrow.build_config_toggle(data, 0xEE)
    with pytest.raises(optionrow.OptionRowError):
        optionrow.toggle_to_flag_delta(toggle.grown, toggle.new_group_index, 0x2F)
