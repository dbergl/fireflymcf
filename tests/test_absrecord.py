"""`absrecord`: re-encoding a GuiData record at an absolute position.

Measured on the Low Volts hold image (`lv_hold`): the compact AES card 1427 (roots 5 and 39),
the AES settings body 1425 (root 31) and the Low Volts face child 285 (under 1427 rec[8]). Each
re-encoded record is pasted as the only record of a scratch object attached under 1425 by
`insert.insert_object`, and proven by the real walk of root 31, not by decode alone.
"""
import dataclasses

import pytest

from fireflymcf import absrecord, guidata, insert, pagewalk, records
from fireflymcf.recipe_ops import AddLowVoltsControl

AT = (300, 200)
SCRATCH_PARENT, SCRATCH_ROOT = 1425, 31

# (root, obj, index): one real record per supported wtype, plus the title (in_regs x from
# register 3) and the align-3 inner box.
CASES = {
    0: (31, 1425, 12),     # 'V' unit, R38 = 0xff (inherits font 14)
    4: (5, 1427, 3),       # fill
    5: (5, 1427, 8),       # attach of child 285, value_slot 45 (R42)
    6: (5, 1427, 6),       # switch on slot 11 (R41)
    8: (31, 1425, 26),     # readout on slot 17 (R41)
    11: (5, 285, 1),       # 'Low\nVolts'
    12: (5, 1427, 9),      # Low Volts touch region
    13: (5, 1427, 5),      # dynamic text on slot 129 (R41)
    32: (31, 1425, 8),     # inner box, persistent align 3
    33: (5, 1427, 1),      # Low Volts box, out_regs x -> 3
}
# wtypes the decoder gives an R38 font field (FUN_00252CB0 R38 gate `0x2000a961 & aed`)
HAS_FONT = {0, 5, 6, 8, 11, 13}
# wtypes that can carry a value_slot: R41 (`aed & 0x2140`) or R42 (`aed & 0x70c3f`, `aee & 0xf`)
HAS_SLOT = {0, 4, 5, 6, 8, 11, 13, 32, 33}
TEST_SLOT = 45


def _rec(data, obj, index):
    gs, _ = guidata.guidata_extent(data)
    return records.object_records(data, gs + guidata.object_tables(data)[1][obj])[index]


def _placed(data, root, obj, index):
    hits = [w for w in pagewalk.walk_records(data, root) if w.obj == obj and w.index == index]
    assert hits, (root, obj, index)
    return hits[0]


def _paste(data, *recs: bytes):
    """Attach a scratch object holding `recs` under 1425 and return (image, its index)."""
    payload = bytes([len(recs)]) + b"".join(recs)
    payload += b"\x00" * (-len(payload) % 4)
    out, _ = insert.insert_object(data, payload, SCRATCH_PARENT)
    return out, len(guidata.object_tables(out)[1]) - 1


def _walked(data, obj):
    return [w for w in pagewalk.walk_records(data, SCRATCH_ROOT) if w.obj == obj]


@pytest.mark.parametrize("wtype", sorted(CASES))
def test_reencoded_record_walks_at_the_absolute_position(lv_hold, wtype):
    root, obj, index = CASES[wtype]
    rec = _rec(lv_hold, obj, index)
    assert rec.wtype == wtype
    placed = _placed(lv_hold, root, obj, index)
    slot = TEST_SLOT if wtype in HAS_SLOT else None
    out = absrecord.absolute(lv_hold, rec, placed, *AT, explicit_font=wtype in HAS_FONT,
                             reset_align=True, value_slot=slot)
    dec = records.decode_record(out, 0)
    assert dec.size == len(out)
    assert dec.wtype == wtype
    assert dec.base_modes[:4] == [0, 0, 1, 1]
    assert dec.in_regs == [0] * 6 and dec.out_regs == [0] * 6
    assert dec.geom[4:] == rec.geom[4:]
    if wtype in (5, 6):
        assert dec.child_index == rec.child_index

    image, new = _paste(lv_hold, out)
    got = _walked(image, new)
    assert got and got[0].index == 0
    w, h = placed.box[2] - placed.box[0], placed.box[3] - placed.box[1]
    x, y = AT
    if wtype == 32:
        # placed.align 3 written back: the align block right-justifies a path-1 box on its
        # pen, so the declared x is the right edge (layout._placed_label_box)
        assert placed.align == 3
        assert got[0].box == (x - w, y, x, y + h)
    else:
        assert got[0].box == (x, y, x + w, y + h)
    assert got[0].align == placed.align
    if wtype in HAS_FONT:
        assert got[0].font == placed.font
    assert got[0].value_slot == slot
    assert got[0].in_regs == (0,) * 6


def test_clears_a_register_read_and_write(lv_hold):
    title = _rec(lv_hold, 1427, 7)            # 'x += reg 3'
    box = _rec(lv_hold, 1427, 1)              # 'reg 3 := x'
    assert title.in_regs[0] == 3 and box.out_regs[0] == 3
    for index, rec in ((7, title), (1, box)):
        out = absrecord.absolute(lv_hold, rec, _placed(lv_hold, 5, 1427, index), *AT, explicit_font=False, reset_align=False, value_slot=None)
        dec = records.decode_record(out, 0)
        assert dec.in_regs == [0] * 6 and dec.out_regs == [0] * 6
        assert dec.geom[:2] == list(AT)


def test_attach_matches_the_coach_proven_template(lv_hold):
    """1427 rec[8] re-encoded is `AddLowVoltsControl._attach_record` byte for byte."""
    rec = _rec(lv_hold, 1427, 8)
    out = absrecord.absolute(lv_hold, rec, _placed(lv_hold, 5, 1427, 8), 615, 62,
                             explicit_font=False, reset_align=False, value_slot=45)
    assert out == AddLowVoltsControl._attach_record(615, 62, 285, value_slot=45)


def test_box_and_label_match_the_coach_proven_templates(lv_hold):
    """285's box and caption re-encoded are `_box_record`/`_label_record` byte for byte."""
    box, lbl = _rec(lv_hold, 285, 0), _rec(lv_hold, 285, 1)
    out = absrecord.absolute(lv_hold, box, _placed(lv_hold, 5, 285, 0), 615, 62,
                             explicit_font=False, reset_align=False, value_slot=45)
    assert out == AddLowVoltsControl._box_record(615, 62, 105, 50, 45, 5, value_slot=45)
    out = absrecord.absolute(lv_hold, lbl, _placed(lv_hold, 5, 285, 1), 615, 62,
                             explicit_font=False, reset_align=False, value_slot=45)
    r39 = lv_hold[lbl.font_off + 1]
    assert out == AddLowVoltsControl._label_record(
        lbl.label, 615, 62, 105, 50, lbl.font, r39, lbl.h_align, lbl.v_align,
        lbl.line_height, lbl.line_height_adds_font, value_slot=45)


def test_unsupported_wtype_is_refused(lv_hold):
    rec = _rec(lv_hold, 1425, 22)
    assert rec.wtype == 9
    with pytest.raises(absrecord.Unsupported):
        absrecord.absolute(lv_hold, rec, _placed(lv_hold, 31, 1425, 22), *AT,
                           explicit_font=False, reset_align=False, value_slot=None)


@pytest.mark.parametrize("wtype", sorted(set(CASES) - HAS_FONT))
def test_font_on_a_type_without_r38_is_refused(lv_hold, wtype):
    root, obj, index = CASES[wtype]
    with pytest.raises(absrecord.Unsupported, match="font"):
        absrecord.absolute(lv_hold, _rec(lv_hold, obj, index),
                           _placed(lv_hold, root, obj, index), *AT,
                           explicit_font=True, reset_align=False, value_slot=None)


def test_value_slot_on_a_touch_region_is_refused(lv_hold):
    root, obj, index = CASES[12]
    with pytest.raises(absrecord.Unsupported, match="value_slot"):
        absrecord.absolute(lv_hold, _rec(lv_hold, obj, index),
                           _placed(lv_hold, root, obj, index), *AT,
                           explicit_font=False, reset_align=False, value_slot=TEST_SLOT)


def test_label_without_explicit_font_inherits_the_previous_one(lv_hold):
    """The 'V' unit (R38 0xff) re-encoded with explicit_font=False draws in whatever font the
    record before it set -- here 20 -- not its source's 14. This is why `move_control` makes
    the font explicit on the first text record."""
    root, obj, index = CASES[0]
    v = _rec(lv_hold, obj, index)
    placed = _placed(lv_hold, root, obj, index)
    assert v.font is None and placed.font == 14
    setter = absrecord.absolute(lv_hold, _rec(lv_hold, 285, 1),
                                dataclasses.replace(_placed(lv_hold, 5, 285, 1), font=20),
                                400, 100, explicit_font=True, reset_align=False,
                                value_slot=None)
    label = absrecord.absolute(lv_hold, v, placed, *AT, explicit_font=False,
                               reset_align=False, value_slot=None)
    image, new = _paste(lv_hold, setter, label)
    got = _walked(image, new)
    assert [g.index for g in got] == [0, 1]
    assert got[0].font == 20 and got[1].font == 20
    explicit = absrecord.absolute(lv_hold, v, placed, *AT, explicit_font=True,
                                  reset_align=False, value_slot=None)
    image, new = _paste(lv_hold, setter, explicit)
    assert _walked(image, new)[1].font == 14


def test_decoder_cursor_positions_agree_with_the_present_fields(lv_hold):
    """`aux_off`/`geom_base_off`/`r42_off` (the cursors `absolute` splices at) coincide with
    the fields when they are present."""
    attach = _rec(lv_hold, 1427, 8)            # R42 present, geom 0/1 present, no aux
    assert attach.r42_off == attach.value_slot_off
    assert attach.geom_base_off == attach.geom_off[0] == attach.aux_off
    title = _rec(lv_hold, 1427, 7)             # R18 present (b0 0x40), geom from slot 1
    assert title.geom_base_off == title.aux_off + 1 == title.geom_off[1]
