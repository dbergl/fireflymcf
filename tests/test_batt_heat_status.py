"""The `add_aes_batt_heat_status` recipe op: makes the "Batt Heat" button reflect two live BMS
states carried by status DGN 0x18FF7070 -- the battery heater (byte 2 bit 0 -> the body reads
button blue off / cyan on) and the battery's internal heater CONTACTOR (byte 1 bit 2 -> a red
wavy-lines glyph, top-right).

The op APPENDS two variable-table entries to receive 0xFF70, folds both bits into ONE display cell
with a `bitcombine` routine, and drives ONE four-arm wtype-6 switch from that cell. The arms are
whole buttons -- capsule, caption and (when the contactor is closed) the glyph -- which is what
makes the repaint correct; see `test_every_arm_redraws_the_whole_button`. This exercises the op
composed on the compact frame (swap_aes_electrical + add_aes_batt_heat + add_aes_batt_heat_status).
"""
import json
import struct

from fireflymcf import aescompact, guidata, recipe, records, statusbind

STOCK = "firefly-13.6/mcf.bin"

RECIPE = json.dumps({
    "schema": 1,
    "base_sha256": "d66b41ffafaae4f5ec47cf90714f14bb1954fc3a5ae38defa98a94cd9643d382",
    "build_name": "batt-heat-status-test",
    "ops": [
        {"id": "aesswap", "type": "swap_aes_electrical", "params": {}, "enabled": True},
        {"id": "battheat", "type": "add_aes_batt_heat", "params": {}, "enabled": True},
        {"id": "status", "type": "add_aes_batt_heat_status", "params": {}, "enabled": True},
    ],
})


def _build():
    stock = open(STOCK, "rb").read()
    res = recipe.compile_project(recipe.Project.from_json(RECIPE), stock)
    assert res.ok, res.errors
    return stock, res


def _status_vars(data):
    """The two variable-table entries this op APPENDED to receive 0xFF70, keyed by role.

    Handles are not exposed on CompileResult, so the vars are derived from the image: both carry
    DGN 0xFF70 and differ by extraction mode (4 = heater byte 2, 5 = contactor byte 1)."""
    hits = [v for v in guidata.variable_table(data)
            if v.dgn_low == (aescompact.BATT_HEAT_STATUS_DGN & 0xFFFF)]
    by_mode = {v.raw[2] >> 4: v for v in hits}
    return hits, by_mode


def _frame_records(data):
    gs, tab = guidata.guidata_extent(data)[0], guidata.object_tables(data)[1]
    return records.object_records(data, gs + tab[aescompact.ELECTRICAL_AES_BODY])


def _state_cell(data):
    """`(cell index, cell address)` of the combined-state cell the op's fold routine writes.

    `bitcombine.build`'s literal pool is `[slot0, slot1, cell]`, adjacent and in that order, so
    the second word after the HEATER var's receive slot IS the cell address -- which pairs the
    routine to its vars unambiguously rather than trusting a lone address match."""
    from fireflymcf import register, status_map, theme
    _hits, by_mode = _status_vars(data)
    heat = by_mode[aescompact.BATT_HEAT_STATUS_HEATER_MODE].index
    con = by_mode[aescompact.BATT_HEAT_STATUS_CONTACTOR_MODE].index
    gc = theme.guicode_section(data)
    code = data[gc.offset:gc.offset + gc.length]
    at = code.find(struct.pack("<I", register.status_slot_addr(heat)))
    assert at != -1, "no routine carries the heater var's receive slot"
    assert struct.unpack_from("<I", code, at + 4)[0] == register.status_slot_addr(con), \
        "the heater slot literal is not followed by the contactor slot; the fold reads one var"
    cell_addr = struct.unpack_from("<I", code, at + 8)[0]
    cells = status_map.cell_table(data)
    assert cell_addr in cells, "routine writes %#x, which no cell-table index names" % cell_addr
    return cells.index(cell_addr), cell_addr


def _state_switch(data):
    slot, _addr = _state_cell(data)
    return [r for r in _frame_records(data) if r.wtype == 6 and r.value_slot == slot]


def test_two_variables_now_receive_ff70():
    _stock, res = _build()
    hits, by_mode = _status_vars(res.data)
    assert len(hits) == 2, [hex(v.dgn_low) for v in hits]
    modes = sorted((v.raw[2] >> 4, v.raw[3] & 7) for v in hits)
    assert modes == sorted([
        (aescompact.BATT_HEAT_STATUS_HEATER_MODE, aescompact.BATT_HEAT_STATUS_HEATER_BYTE),
        (aescompact.BATT_HEAT_STATUS_CONTACTOR_MODE, aescompact.BATT_HEAT_STATUS_CONTACTOR_BYTE),
    ]), modes
    assert aescompact.BATT_HEAT_STATUS_HEATER_MODE in by_mode
    assert aescompact.BATT_HEAT_STATUS_CONTACTOR_MODE in by_mode


def test_appends_rather_than_taking_over_a_subscription():
    """The two 0xFF70 entries are APPENDED, and every stock entry is left exactly as it was --
    the check the DC_DIMMER (G12 OUTPUTS rows) and solar donor revisions would have failed."""
    stock, res = _build()
    before = guidata.variable_table(stock)
    after = guidata.variable_table(res.data)
    assert len(after) == len(before) + 2
    assert [v.raw for v in after[:len(before)]] == [v.raw for v in before], \
        "an existing subscription was modified"
    hits, _ = _status_vars(res.data)
    assert sorted(v.index for v in hits) == [len(before), len(before) + 1]


def test_switch_watches_a_cell_fed_from_both_variables():
    """THE GUARD for the v28/v29 defect: a switch's value_slot indexes the CELL table, so it must
    resolve to an address a GuiCode routine writes from the variables' receive slots. Pointing a
    switch straight at a variable index renders identically and never moves on the coach."""
    _stock, res = _build()
    _hits, by_mode = _status_vars(res.data)
    assert len(_state_switch(res.data)) == 1, "no single switch watches the folded state cell"
    frame = _frame_records(res.data)
    for v in by_mode.values():
        assert all(r.value_slot != v.index for r in frame if r.wtype == 6), \
            "a switch names var %d as its value_slot; that indexes the cell table" % v.index


def test_one_switch_drives_the_button_and_it_is_enrolled():
    from fireflymcf import recipe_ops
    _stock, res = _build()
    sw = _state_switch(res.data)
    assert len(sw) == 1, sw
    assert sw[0].count == len(aescompact.BATT_HEAT_STATE_ARMS), sw[0].count
    assert sw[0].header[4] & recipe_ops._ENROLL_BIT, "state switch not enrolled for live redraw"
    frame = _frame_records(res.data)
    # The static capsule is gone (replaced by the switch) and no second switch shares the band.
    assert not any(r.wtype == 33 and r.colors
                   and r.colors[0] == ("fillA", 7, aescompact.BATT_HEAT_FILL_INDEX)
                   for r in frame), "static capsule still present"
    band = range(sw[0].child_index, sw[0].child_index + sw[0].count)
    others = [r for r in frame
              if r.wtype == 6 and r.offset != sw[0].offset and r.child_index in band]
    assert not others, "a second switch selects the Batt Heat arm band: %s" % (
        [r.value_slot for r in others],)


def test_every_arm_redraws_the_whole_button():
    """THE GUARD for the two repaint defects the owner reported on v31 (2026-09-22).

    The panel repaints only the arm a switch selects, so an arm that omits an element leaves the
    outgoing arm's pixels on the glass. v31 shipped two switches: enabling the heater painted the
    body arm over the sibling 'Batt Heat' caption with nothing to redraw it, and disabling the
    contactor selected an arm that drew nothing, leaving the red glyph lit. Every arm here must
    carry the full-size opaque capsule in its own state's colour AND the caption; only the
    contactor-closed arms carry the glyph. The caption must NOT also remain in the record stream,
    where it would draw a second time at the pen the arm's caption advanced to.
    """
    _stock, res = _build()
    data = res.data
    gs, tab = guidata.guidata_extent(data)[0], guidata.object_tables(data)[1]
    sw = _state_switch(data)[0]
    want_w = aescompact.BATT_HEAT_W - 2 * aescompact.PUMP_OUTLINE_INSET
    want_h = aescompact.BATT_HEAT_H - 2 * aescompact.PUMP_OUTLINE_INSET
    for i, (match, heater_on, contactor_on) in enumerate(aescompact.BATT_HEAT_STATE_ARMS):
        arm = records.object_records(data, gs + tab[sw.child_index + i])
        caps = [r for r in arm if r.wtype == 33 and r.colors and r.colors[0][:2] == ("fillA", 7)
                and r.geom[2:4] == [want_w, want_h]]
        assert len(caps) == 1, "arm for match %#x has %d full-size capsules" % (match, len(caps))
        assert any(r.wtype == 11 and r.label == aescompact.BATT_HEAT_TEXT for r in arm), \
            "arm for match %#x omits the caption, so selecting it would wipe it" % match
        glyphs = [r for r in arm if r.wtype == 11
                  and r.label == aescompact.CONTACTOR_GLYPH_CHAR
                  and r.font == aescompact.CONTACTOR_GLYPH_FONT_SLOT]
        assert len(glyphs) == (1 if contactor_on else 0), \
            "arm for match %#x has %d contactor glyphs, expected %d" % (
                match, len(glyphs), 1 if contactor_on else 0)
    assert not [r for r in _frame_records(data)
                if r.wtype == 11 and r.label == aescompact.BATT_HEAT_TEXT], \
        "the caption also survives in the record stream; it would draw twice"


def test_registry_stays_under_cap():
    _stock, res = _build()
    assert len(statusbind.registered_dgns(res.data)) <= statusbind.REGISTRY_CAP


def test_op_is_hardware_verified():
    """Coach-verified 2026-09-22: receive half on v31, drawing half on v32 -- injected 18FF70C0
    frames transitioned through every edge and the caption and glyph repainted correctly."""
    _stock, res = _build()
    assert "status" not in res.unverified


def test_capability_gate_sees_no_delta():
    stock, res = _build()
    assert recipe.capabilities(res.data) == recipe.capabilities(stock)


def test_body_renders_blue_off_cyan_on():
    """The body reads the standard button blue for heater-off states {0, 2} and cyan for the
    heater-on states {1, 3} -- the pair Water Pump / Starlink / Overflow / Tank Heater use."""
    from fireflymcf import options, render, cli
    _stock, res = _build()
    data = res.data
    slot, _addr = _state_cell(data)
    config = options.CoachConfig.from_json("build/coach-wd.json")
    base = dict(options.slot_values(data, config))
    for s, v in options.derived_slots(data, base).items():
        base.setdefault(s, v)
    root = cli._resolve_page_root(data, "electrical")
    cx = aescompact.BATT_HEAT_X + aescompact.BATT_HEAT_W // 2
    cy = aescompact.BATT_HEAT_Y + aescompact.BATT_HEAT_H // 2

    def body_rgb(v):
        sv = dict(base)
        sv[slot] = v
        img = render.render_screen_image(data, root, slot_values=sv).convert("RGB")
        return img.getpixel((cx, cy))

    def near(a, b, tol=40):
        return all(abs(x - y) <= tol for x, y in zip(a, b))

    for v in (0, 2):
        assert near(body_rgb(v), aescompact.BATT_HEAT_OFF_RGB), \
            "heater off state %d not button blue: %s" % (v, body_rgb(v))
    for v in (1, 3):
        assert near(body_rgb(v), aescompact.BATT_HEAT_ON_RGB), \
            "heater on state %d not cyan: %s" % (v, body_rgb(v))


def test_contactor_glyph_is_red_slot23_char2():
    """The contactor glyph draws font slot 23 char '2' tinted red (fillA mode-3 inline BGR)."""
    from fireflymcf import render
    _stock, res = _build()
    data = res.data
    gs, tab = guidata.guidata_extent(data)[0], guidata.object_tables(data)[1]
    found = None
    for oi in range(len(tab)):
        for r in records.object_records(data, gs + tab[oi]):
            if r.wtype == 11 and r.label == aescompact.CONTACTOR_GLYPH_CHAR \
                    and r.font == aescompact.CONTACTOR_GLYPH_FONT_SLOT:
                color, _src = render._glyph_color(r, {})
                if color == (190, 30, 45):
                    found = (oi, color)
    assert found is not None, "no red slot-23 '2' contactor glyph found"
