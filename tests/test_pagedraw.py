"""pagedraw: absolute, explicit-colour record primitives for authored pages (LEVEL page, Task 6).

Every primitive returns ONE record's bytes that decode standalone at offset 0 (so a caller can
concatenate them into any object with `stream`), are placed absolutely (header byte 1 = 0x50,
base modes [0,0,1,1]) and carry an explicit colour in every colour field they have -- never a
mode-0 inherit and never a mode-7 cell (GUICODE Findings 28/29/38).
"""
import pytest

from fireflymcf import guidata, metrics, pagedraw, records

V33 = "builds/mcf-13.6-BattHeatGlyph-v33.bin"
STOCK = "firefly-13.6/mcf.bin"
D = open(V33, "rb").read()
AMBER, GREY = (0xE6, 0xA0, 0x28), (0x80, 0x82, 0x85)
NAVY = (0x15, 0x18, 0x3A)

INK_ROLES = ("fillA", "fillA2")          # a label's / readout's text ink (Finding 28, render.py)
BG_ROLES = ("fillB", "fillB2")           # a label's / readout's background (mode 5 = transparent)


def _rgb_int(rgb):
    return (rgb[0] << 16) | (rgb[1] << 8) | rgb[2]


@pytest.mark.parametrize("make,wt", [
    (lambda: pagedraw.fill(D, 10, 20, 30, 40, GREY), 4),
    (lambda: pagedraw.rbox(D, 10, 20, 30, 40, 8, GREY, (0x26, 0x2C, 0x60)), 0x20),
    (lambda: pagedraw.capsule(D, 10, 20, 30, 40, 6, AMBER), 0x21),
    (lambda: pagedraw.hline(D, 10, 20, 30, GREY), 2),
    (lambda: pagedraw.label(D, "FRONT", 10, 20, 80, 28, font=15, rgb=GREY), 11),
    (lambda: pagedraw.label(D, "FRONT", 10, 20, 80, 28, font=15, rgb=GREY, bg=NAVY), 11),
])
def test_primitive_is_absolute_explicit_and_self_decodes(make, wt):
    raw = make()
    r = records.decode_record(raw, 0)
    assert r.wtype == wt and r.size == len(raw)
    assert r.header[1] == 0x50 and r.base_modes[:4] == [0, 0, 1, 1]
    assert r.geom[:2] == [10, 20]
    assert r.in_regs == [0] * 6 and r.out_regs == [0] * 6
    assert r.colors
    for role, mode, _v in r.colors:
        # Every field explicit. The only non-mode-3 allowed is mode 5 on a TEXT record's
        # background pair: the stock label pattern, an explicit "transparent" (it sets the
        # transparency bit, 0x253532 / 0x25362A), not an inherit.
        if wt == 11 and role in BG_ROLES:
            assert mode in (3, 5), r.colors
        else:
            assert mode == 3, r.colors
    if wt == 11:
        assert r.font == 15 and r.h_align == 2 and r.v_align == 2


def test_colour_is_written_bgr():
    r = records.decode_record(pagedraw.capsule(D, 0, 0, 5, 5, 2, AMBER), 0)
    assert r.colors[0][2] == 0xE6A028          # decoded as 0xRRGGBB
    raw = pagedraw.capsule(D, 0, 0, 5, 5, 2, AMBER)
    off = r.color_off[0]
    assert raw[off:off + 3] == bytes([0x28, 0xA0, 0xE6])      # file order B, G, R


def test_every_colour_field_carries_the_requested_value():
    fill = records.decode_record(pagedraw.fill(D, 1, 2, 3, 4, GREY), 0)
    assert [(ro, v) for ro, _m, v in fill.colors] == [("fillA", _rgb_int(GREY)),
                                                       ("fillA2", _rgb_int(GREY))]
    body = (0x26, 0x2C, 0x60)
    box = records.decode_record(pagedraw.rbox(D, 1, 2, 30, 40, 8, GREY, body), 0)
    assert dict((ro, v) for ro, _m, v in box.colors) == {
        "fillA": _rgb_int(GREY), "fillA2": _rgb_int(GREY),
        "fillB": _rgb_int(body), "fillB2": _rgb_int(body)}
    lab = records.decode_record(pagedraw.label(D, "X", 1, 2, 30, 28, font=15, rgb=AMBER), 0)
    assert [(ro, m, v) for ro, m, v in lab.colors] == [
        ("fillA", 3, _rgb_int(AMBER)), ("fillA2", 3, _rgb_int(AMBER)),
        ("fillB", 5, None), ("fillB2", 5, None)]


def test_geometry_is_the_requested_box():
    assert records.decode_record(pagedraw.fill(D, 5, 6, 7, 8, GREY), 0).geom[:4] == [5, 6, 7, 8]
    box = records.decode_record(pagedraw.rbox(D, 5, 6, 70, 80, 9, GREY, NAVY), 0)
    assert box.geom[:5] == [5, 6, 70, 80, 9] and box.corner_radius == 9
    cap = records.decode_record(pagedraw.capsule(D, 5, 6, 70, 80, 4, GREY), 0)
    assert cap.geom[:5] == [5, 6, 70, 80, 4]
    line = records.decode_record(pagedraw.hline(D, 5, 6, 70, GREY), 0)
    assert line.geom[:4] == [5, 6, 70, 0]            # y1 = y0 + 0: horizontal
    lab = records.decode_record(pagedraw.label(D, "PITCH", 5, 6, 80, 28, font=15, rgb=GREY), 0)
    assert lab.geom[:4] == [5, 6, 80, 28] and lab.label == "PITCH"


def test_glyph_is_one_centred_character():
    raw = pagedraw.glyph(D, "\x7f", 100, 120, 32, 32, font=23, rgb=AMBER)
    r = records.decode_record(raw, 0)
    assert r.wtype == 11 and r.label == "\x7f" and r.font == 23
    assert r.h_align == 2 and r.v_align == 2 and r.geom[:4] == [100, 120, 32, 32]
    assert r.size == len(raw) and r.header[1] == 0x50


def test_readout_is_directive_plus_live_wt8():
    d, w = pagedraw.readout(D, 600, 100, 80, 30, value_slot=700, width=5, places=1, font=17,
                            rgb=(0xFF, 0xFF, 0xFF), bg=NAVY)
    dr, wr = records.decode_record(d, 0), records.decode_record(w, 0)
    assert dr.wtype == 9 and dr.size == len(d) and dr.fmt == (0, 0x18, 5, 1)
    assert dr.header[1] == 0x50 and dr.colors == []
    assert wr.wtype == 8 and wr.size == len(w)
    assert wr.header[1] == 0x50 and wr.base_modes[:4] == [0, 0, 1, 1]
    assert wr.header[4] & 0x20                       # live-redraw enrollment kept
    assert wr.value_slot == 700 and wr.font == 17 and wr.align == 3
    assert [(ro, m, v) for ro, m, v in wr.colors] == [
        ("fillA", 3, 0xFFFFFF), ("fillA2", 3, 0xFFFFFF),
        ("fillB", 3, _rgb_int(NAVY)), ("fillB2", 3, _rgb_int(NAVY))]
    # the pen is derived from the box: right-aligned -> pen x is the box's right edge, and the
    # cell (pen_y - top .. + height) sits centred in it.
    fm = metrics.font_metrics(D, 17)
    assert wr.geom[0] == 600 + 80
    cell_top = wr.geom[1] - fm.top
    assert 100 <= cell_top and cell_top + fm.height - 1 <= 100 + 30


@pytest.mark.parametrize("align,pen_x", [(1, 600), (2, 640), (3, 680)])
def test_readout_pen_follows_alignment(align, pen_x):
    _d, w = pagedraw.readout(D, 600, 100, 80, 30, value_slot=700, width=3, places=0, font=17,
                             rgb=GREY, bg=NAVY, align=align)
    r = records.decode_record(w, 0)
    assert r.align == align and r.geom[0] == pen_x


def test_readout_refuses_inheriting_alignment():
    # align 0 does not write the persistent state+9 (GUICODE Finding 24): it would inherit.
    with pytest.raises(pagedraw.PageDrawError):
        pagedraw.readout(D, 0, 0, 80, 30, value_slot=700, width=3, places=0, font=17,
                         rgb=GREY, bg=NAVY, align=0)


def test_label_refuses_a_box_it_would_wrap_or_clip_in():
    # 'FRONT' in font 15 measures 75 px and the cell is 26 rows (metrics.font_metrics): the
    # wrap budget is w + 1 and the wtype-11 clip is the inclusive box (GUICODE Finding 69).
    with pytest.raises(pagedraw.PageDrawError, match="wrap"):
        pagedraw.label(D, "FRONT", 0, 0, 60, 28, font=15, rgb=GREY)
    with pytest.raises(pagedraw.PageDrawError, match="clip"):
        pagedraw.label(D, "FRONT", 0, 0, 80, 20, font=15, rgb=GREY)
    assert pagedraw.label(D, "FRONT", 0, 0, 74, 25, font=15, rgb=GREY)   # exactly fits
    with pytest.raises(pagedraw.PageDrawError):
        pagedraw.label(D, "X", 0, 0, 0, 20, font=15, rgb=GREY)      # zero-width box


def test_stream_is_a_padded_object():
    recs = [pagedraw.fill(D, 0, 0, 5, 5, GREY), pagedraw.hline(D, 0, 9, 5, GREY),
            pagedraw.label(D, "LEVEL", 0, 20, 80, 28, font=15, rgb=GREY)]
    s = pagedraw.stream(recs)
    assert s[0] == 3 and len(s) % 4 == 0 and s[1:1 + sum(map(len, recs))] == b"".join(recs)
    dec = records.object_records(s, 0)
    assert [r.wtype for r in dec] == [4, 2, 11]


def test_works_on_stock_too():
    s = open(STOCK, "rb").read()
    assert pagedraw.capsule(s, 1, 2, 3, 4, 2, AMBER) == pagedraw.capsule(D, 1, 2, 3, 4, 2, AMBER)


@pytest.mark.parametrize("obj,rec,make", [
    (32, 2, lambda d: pagedraw.fill(d, 0, 0, 5, 5, GREY)),
    (368, 1, lambda d: pagedraw.rbox(d, 0, 0, 5, 5, 2, GREY, NAVY)),
    (285, 0, lambda d: pagedraw.capsule(d, 0, 0, 5, 5, 2, GREY)),
    (367, 6, lambda d: pagedraw.hline(d, 0, 0, 5, GREY)),
    (285, 1, lambda d: pagedraw.label(d, "A", 0, 0, 30, 28, font=15, rgb=GREY)),
    (793, 5, lambda d: pagedraw.readout(d, 0, 0, 80, 30, value_slot=1, width=3, places=0,
                                        font=17, rgb=GREY, bg=NAVY)),
    (793, 4, lambda d: pagedraw.readout(d, 0, 0, 80, 30, value_slot=1, width=3, places=0,
                                        font=17, rgb=GREY, bg=NAVY)),
])
def test_refuses_a_drifted_donor(obj, rec, make):
    gs = guidata.guidata_extent(D)[0]
    r = records.object_records(D, gs + guidata.object_tables(D)[1][obj])[rec]
    bad = bytearray(D)
    bad[r.end - 1] ^= 0x01                       # one byte of the donor's tail
    with pytest.raises(pagedraw.PageDrawError, match="drifted"):
        make(bytes(bad))


# ---- header align: never 0 (inherit), never a leak from a previous readout -------------------

PATH1 = [
    ("fill", 4, lambda: pagedraw.fill(D, 10, 20, 30, 40, GREY)),
    ("rbox", 0x20, lambda: pagedraw.rbox(D, 10, 20, 30, 40, 8, GREY, NAVY)),
    ("capsule", 0x21, lambda: pagedraw.capsule(D, 10, 20, 30, 40, 6, AMBER)),
    ("hline", 2, lambda: pagedraw.hline(D, 10, 20, 30, GREY)),
    ("label", 11, lambda: pagedraw.label(D, "FRONT", 10, 20, 80, 28, font=15, rgb=GREY)),
    ("glyph", 11, lambda: pagedraw.glyph(D, "\x7f", 10, 20, 32, 32, font=23, rgb=AMBER)),
]


@pytest.mark.parametrize("name,wt,make", PATH1)
def test_every_primitive_writes_an_explicit_align(name, wt, make):
    # FUN_00252CB0 stores state+9 only when header align != 0 (beq @0x2531FE past the strb
    # @0x253200), and the layout engine shifts a path-1 box by state+9 == 2/3 (0x25808A-0x258090).
    r = records.decode_record(make(), 0)
    assert r.wtype == wt and r.align in (1, 2, 3)
    assert r.align == pagedraw.ALIGN_NO_SHIFT


def test_readout_align_is_explicit():
    for align in (1, 2, 3):
        _d, w = pagedraw.readout(D, 0, 0, 80, 30, value_slot=1, width=3, places=0, font=17,
                                 rgb=GREY, bg=NAVY, align=align)
        assert records.decode_record(w, 0).align == align


def test_primitives_after_a_readout_keep_their_own_align():
    d, w = pagedraw.readout(D, 600, 100, 80, 30, value_slot=700, width=3, places=0, font=17,
                            rgb=GREY, bg=NAVY, align=3)
    recs = [d, w] + [make() for _n, _wt, make in PATH1]
    dec = records.object_records(pagedraw.stream(recs), 0)
    assert dec[1].wtype == 8 and dec[1].align == 3
    # each decodes its OWN align: a 0 here would inherit the readout's 3 and right-shift the box
    assert [(r.wtype, r.align) for r in dec[2:]] == [(wt, 1) for _n, wt, _m in PATH1]


def test_label_and_glyph_refuse_multiline_text():
    with pytest.raises(pagedraw.PageDrawError, match="multi-line"):
        pagedraw.label(D, "A\nB", 0, 0, 80, 60, font=15, rgb=GREY)
    with pytest.raises(pagedraw.PageDrawError):
        pagedraw.glyph(D, "\n", 0, 0, 32, 32, font=23, rgb=GREY)


def test_touch_is_absolute_sized_and_carries_its_id():
    raw = pagedraw.touch(D, 552, 368, 46, 46, 61)
    r = records.decode_record(raw, 0)
    assert r.wtype == 12 and r.size == len(raw)
    assert r.header[1] == 0x50 and r.base_modes[:4] == [0, 0, 1, 1]
    assert r.geom[:4] == [552, 368, 46, 46]
    # records.decode_record consumes a wt-12's R50 touch id without surfacing it: the raw tail.
    assert raw[-2:] == (61).to_bytes(2, "little")
    # wt-12 is a path-1 wtype (layout._align_path1): an align-0 header would inherit state+9
    # and a preceding right-aligned readout would shift the hit box.
    assert r.align == pagedraw.ALIGN_NO_SHIFT
    assert r.in_regs == [0] * 6 and r.out_regs == [0] * 6


def test_touch_refuses_a_bad_id_or_box():
    with pytest.raises(pagedraw.PageDrawError):
        pagedraw.touch(D, 10, 20, 30, 40, 0x10000)
    with pytest.raises(pagedraw.PageDrawError):
        pagedraw.touch(D, 10, 20, 0, 40, 5)
