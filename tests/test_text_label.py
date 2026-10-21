"""`add_text_label`: static text on any page.

Corpus-reading: compiles against stock 13.6 and places "FAN SPEED" on the CLIMATE CONTROL root
(object 7) at (86, 206). The label is one absolute boxed wtype-11 (after an optional fill) in a
new object attached under `parent`, ended by the restore record that hands back the font and
align the walk carried in (`enumlabel.restore_record`).
"""
import json
import struct
from pathlib import Path

import pytest

from fireflymcf import enumlabel, guidata, metrics, pagewalk, recipe, recipe_ops, records, theme

REPO = Path(__file__).resolve().parents[1]
STOCK = REPO / "firefly-13.6/mcf.bin"
SHA = "d66b41ffafaae4f5ec47cf90714f14bb1954fc3a5ae38defa98a94cd9643d382"
CLIMATE_ROOT = 7
SLOT_PARENT = 899                # its attaches carry value_slot 0: the label would inherit it
LEAK_PARENT = 924                # root 18: without the restore record the font leaks
X, Y = 86, 206


def _op(**over):
    params = {"parent": CLIMATE_ROOT, "x": X, "y": Y, "w": 120, "h": 24, "font": 14,
              "text": "FAN SPEED"}
    params.update(over)
    return {"id": "fan", "type": "add_text_label", "enabled": True, "params": params}


def _stock():
    try:
        return STOCK.read_bytes()
    except FileNotFoundError:
        pytest.skip("needs the stock 13.6 image")


def _compile(ops):
    p = {"schema": 1, "base_sha256": SHA, "build_name": "text-label-test", "ops": ops}
    return recipe.compile_project(recipe.Project.from_json(json.dumps(p)), _stock())


def _gs_tab(data):
    return guidata.guidata_extent(data)[0], guidata.object_tables(data)[1]


def _label_object(data):
    gs, tab = _gs_tab(data)
    obj = len(tab) - 1
    return obj, records.object_records(data, gs + tab[obj])


def _errors(op):
    res = _compile([op])
    assert not res.ok
    return res.errors[0]["code"], res.errors[0]["message"]


@pytest.fixture(scope="module")
def built14():
    res = _compile([_op()])
    assert res.ok, res.errors
    return res.data


@pytest.fixture(scope="module")
def built16():
    res = _compile([_op(font=16, w=130, h=31)])
    assert res.ok, res.errors
    return res.data


# CLIMATE's walk reaches the attach point in font 14 / align 1: font 14 needs no restore record,
# font 16 needs one writing font 14 back (align 1 is what the label leaves too).
@pytest.mark.parametrize("font,w,h,fixture,restore", [(14, 120, 24, "built14", None),
                                                      (16, 130, 31, "built16", 14)])
def test_fan_speed_on_climate(request, font, w, h, fixture, restore):
    data = request.getfixturevalue(fixture)
    gs, tab = _gs_tab(data)
    obj, recs = _label_object(data)
    parent = records.object_records(data, gs + tab[CLIMATE_ROOT])
    assert parent[-1].wtype == 5 and parent[-1].child_index == obj, "not attached to CLIMATE"
    lab = recs[0]
    assert lab.wtype == 11 and lab.label == "FAN SPEED" and lab.font == font
    assert lab.base_modes[:4] == [0, 0, 1, 1] and lab.geom[:4] == [X, Y, w, h]
    assert lab.h_align == enumlabel.ALIGNS["left"] and lab.value_slot is None
    white = theme.resolve_fill_index(data, color=(255, 255, 255))
    assert ("fillA", 7, white) in lab.colors
    if restore is None:
        assert len(recs) == 1, "a restore record where none is needed"
    else:
        assert len(recs) == 2 and recs[1].wtype == 11 and not recs[1].label, "no restore record"
        assert (recs[1].font, recs[1].align) == (restore, 0)
    walk = pagewalk.walk_records(data, CLIMATE_ROOT)
    drawn = [r for r in walk if r.obj == obj and r.label]
    assert [(r.label, r.box) for r in drawn] == [("FAN SPEED", (X, Y, X + w, Y + h))]
    assert enumlabel.foreign_deltas(_stock(), data, CLIMATE_ROOT, obj) == []


@pytest.mark.parametrize("fixture", ["built14", "built16"])
def test_restore_hands_back_the_entering_state(request, fixture):
    built = request.getfixturevalue(fixture)
    stock = _stock()
    walk = pagewalk.walk_records(stock, CLIMATE_ROOT)
    last = max(i for i, r in enumerate(walk) if r.obj == CLIMATE_ROOT or
               any(o == CLIMATE_ROOT for o, _i in r.key[:-1]))
    obj, _recs = _label_object(built)
    after = [r for r in pagewalk.walk_records(built, CLIMATE_ROOT) if r.obj == obj]
    assert (after[-1].font, after[-1].align) == (walk[last].font, walk[last].align)


def test_two_lines():
    """A two-line label carries stock's multi-line pitch (line_height 0, adds-font 1: the lines
    advance by the font's own cell, the 'Go Power!\\nSettings' records')."""
    res = _compile([_op(text="FAN\nSPEED", h=48)])
    assert res.ok, res.errors
    _obj, recs = _label_object(res.data)
    lab = recs[0]
    assert lab.label == "FAN\nSPEED"
    assert (lab.line_height, lab.line_height_adds_font) == (0, 1)


def test_two_lines_refused_when_they_do_not_fit_h():
    code, msg = _errors(_op(text="FAN\nSPEED", h=40))
    assert code == "BAD_PARAMS" and "2 lines" in msg, msg


def test_one_line_keeps_pagedraws_pitch(built14):
    _obj, recs = _label_object(built14)
    assert (recs[0].line_height, recs[0].line_height_adds_font) == (0x14, 0)


def test_background_and_colour_and_align():
    res = _compile([_op(background=[65, 81, 163], colour=[254, 197, 45], align="right")])
    assert res.ok, res.errors
    _obj, recs = _label_object(res.data)
    bg = theme.resolve_fill_index(res.data, color=(65, 81, 163))
    ink = theme.resolve_fill_index(res.data, color=(254, 197, 45))
    assert [r.wtype for r in recs][:2] == [33, 11]
    assert recs[0].geom[:4] == [X, Y, 120, 24] and recs[0].colors[0] == ("fillA", 7, bg)
    assert ("fillA", 7, ink) in recs[1].colors and recs[1].h_align == 3


@pytest.mark.parametrize("over,why", [
    ({"text": ""}, "non-empty"),
    ({"text": 5}, "text"),
    ({"text": "FAN €"}, "latin-1"),
    ({"align": "middle"}, "align"),
    ({"w": 0}, "positive"),
    ({"h": -1}, "positive"),
    ({"font": 0xFF}, "font"),
    ({"colour": [1, 2]}, "colour"),
    ({"parent": "seven"}, "target"),
    ({"w": 40}, "would wrap it"),
    ({"h": 10}, "would clip it"),
])
def test_bad_params(over, why):
    code, msg = _errors(_op(**over))
    assert code == "BAD_PARAMS" and why in msg, msg


def test_bad_ref():
    code, _msg = _errors(_op(parent={"ref": "nope"}))
    assert code == "BAD_REF"


# font 21 covers only '0'-'5' (no space: a space draws the font's fallback); font 25 covers the
# space with its solid 72x72 "unused" block, shared by characters 5-47
ICON_DIGITS = {"font": 21, "w": 200, "h": 70, "y": 150}


@pytest.mark.parametrize("over,char", [
    ({"text": "1A2", **ICON_DIGITS}, "'A'"),
    ({"text": "1 2", **ICON_DIGITS}, "' '"),
    ({"text": " ", "font": 25, "w": 100, "h": 80, "y": 150}, "' '"),
])
def test_no_glyph(over, char):
    """A character the font does not draw as a glyph or as a blank: outside its range, or a
    shared solid block. A space is checked like any other character."""
    code, msg = _errors(_op(**over))
    assert code == "NO_GLYPH" and char in msg, msg


@pytest.mark.parametrize("text", ["FAN SPEED", "FAN\xa0SPEED", "FAN\x8dSPEED"])
def test_blank_glyphs_are_allowed(text):
    """In font 14 the space, NBSP and the C1 characters 0x8d etc. are blank glyphs (they
    declare height 0, so the blitter draws nothing): they are spaces, not fallbacks."""
    from fireflymcf import rvcbutton
    assert all(rvcbutton.glyph_blank(_stock(), 14, c) for c in " \xa0\x8d")
    res = _compile([_op(text=text)])
    assert res.ok, res.errors
    _obj, recs = _label_object(res.data)
    assert recs[0].label == text


def test_glyph_blank_is_per_font():
    from fireflymcf import rvcbutton
    stock = _stock()
    assert rvcbutton.glyph_blank(stock, 14, " ")
    assert not rvcbutton.glyph_blank(stock, 14, "A")       # inked
    assert not rvcbutton.glyph_blank(stock, 21, " ")       # not covered
    assert not rvcbutton.glyph_blank(stock, 25, " ")       # a solid block
    assert not rvcbutton.glyph_blank(stock, 0xFE, " ")     # no font


@pytest.mark.parametrize("over,code,why", [
    ({"text": " "}, "BAD_PARAMS", "no visible character"),
    ({"text": "\xa0 \x8d"}, "BAD_PARAMS", "no visible character"),
    ({"text": " ", "font": 0xFE}, "NO_GLYPH", "no font"),
    ({"text": "FAN", "font": 0x30}, "NO_GLYPH", "no font"),
])
def test_invisible_text_or_missing_font(over, code, why):
    got, msg = _errors(_op(**over))
    assert got == code and why in msg, msg


@pytest.mark.parametrize("text", ["\n", "FAN\n", "\nFAN", "FAN\n\nSPEED"])
def test_empty_line_refused(text):
    code, msg = _errors(_op(text=text, h=80))
    assert code == "BAD_PARAMS" and "empty line" in msg, msg


def test_text_over_254_bytes_refused():
    code, msg = _errors(_op(text="F" * 255))
    assert code == "BAD_PARAMS" and "at most 254" in msg, msg


def test_off_canvas():
    assert _errors(_op(x=700))[0] == "OFF_CANVAS"
    assert _errors(_op(y=470))[0] == "OFF_CANVAS"
    assert _errors(_op(x=-1))[0] == "OFF_CANVAS"


def test_no_colour():
    assert _errors(_op(colour=[1, 2, 3]))[0] == "NO_COLOUR"
    assert _errors(_op(background=[1, 2, 3]))[0] == "NO_COLOUR"


def test_inherited_slot_refused():
    code, msg = _errors(_op(parent=SLOT_PARENT, x=88, y=372))
    assert code == "LAYOUT_REFUSED" and "inherits value_slot 0" in msg, msg


def test_leak_refused_without_the_restore(monkeypatch):
    assert _compile([_op(parent=LEAK_PARENT)]).ok
    monkeypatch.setattr(enumlabel, "restore_state", lambda *_a: None)
    code, msg = _errors(_op(parent=LEAK_PARENT))
    assert code == "LAYOUT_REFUSED" and "besides its own" in msg, msg


@pytest.mark.parametrize("states,why", [
    (set(), "no page root reaches"),
    ({(None, 1)}, "cannot be written back"),
    ({(23, 1), (14, 1)}, "different font/align"),
])
def test_refused_when_the_entering_state_is_unknown(monkeypatch, states, why):
    monkeypatch.setattr(enumlabel, "entering_state", lambda *_a: set(states))
    code, msg = _errors(_op())
    assert code == "LAYOUT_REFUSED" and why in msg, msg


def test_unverified():
    res = _compile([_op()])
    assert res.unverified == ["fan"]


# --- verify reads back what apply placed ------------------------------------------------------

def _spy_apply(monkeypatch):
    seen = {}
    real = recipe_ops.AddTextLabel.apply

    def spy(self, data, ctx):
        out = real(self, data, ctx)
        seen.update(op=self, ctx=ctx)
        return out
    monkeypatch.setattr(recipe_ops.AddTextLabel, "apply", spy)
    return seen


FONT16 = {"font": 16, "w": 130, "h": 31}


def test_verify_catches_tampering(monkeypatch):
    seen = _spy_apply(monkeypatch)
    res = _compile([_op(**FONT16)])
    assert res.ok, res.errors
    data, op, ctx = res.data, seen["op"], seen["ctx"]
    assert "UNVERIFIED" in op.verify(data, ctx)
    obj, recs = _label_object(data)
    lab = recs[0]
    tampers = {
        "box": lambda b: struct.pack_into("<h", b, lab.geom_off[2], 50),
        "x": lambda b: struct.pack_into("<h", b, lab.geom_off[0], X + 1),
        "text": lambda b: b.__setitem__(lab.label_offset, ord("X")),
        "font": lambda b: b.__setitem__(records.font_offset(data, lab), 13),
        "restore": lambda b: b.__setitem__(records.font_offset(data, recs[1]), 13),
    }
    for name, tamper in tampers.items():
        bad = bytearray(data)
        tamper(bad)
        with pytest.raises(AssertionError):
            op.verify(bytes(bad), ctx)


def test_verify_catches_a_leaked_state(monkeypatch):
    """verify walks the page again: an object that no longer hands back the entering state (its
    restore record rewritten to the label's own font, so the record check alone would pass
    with a matching handle) is caught by the walk."""
    seen = _spy_apply(monkeypatch)
    res = _compile([_op(**FONT16)])
    assert res.ok, res.errors
    data, op, ctx = res.data, seen["op"], seen["ctx"]
    _obj, recs = _label_object(data)
    bad = bytearray(data)
    bad[records.font_offset(data, recs[1])] = 16
    h = ctx.handles["fan"]
    restore = h["restore"]
    h["restore"] = (16, restore[1])
    try:
        with pytest.raises(AssertionError, match="enters with font/align"):
            op.verify(bytes(bad), ctx)
    finally:
        h["restore"] = restore


def test_verify_reads_every_field_apply_placed(monkeypatch):
    """v-align, the transparent background, the fill's radius and second ink, and the attach's
    own geometry: each corrupted after apply is caught."""
    seen = _spy_apply(monkeypatch)
    res = _compile([_op(background=[65, 81, 163])])
    assert res.ok, res.errors
    data, op, ctx = res.data, seen["op"], seen["ctx"]
    op.verify(data, ctx)
    gs, tab = _gs_tab(data)
    obj, recs = _label_object(data)
    fill, lab = recs[0], recs[1]
    attach = records.object_records(data, gs + tab[CLIMATE_ROOT])[-1]
    assert attach.child_index == obj
    r49 = data.index(bytes([1, 14, 0xFF, 1, 2, 0x14, 0]), lab.offset) + 4     # v_align
    other = theme.resolve_fill_index(data, color=(254, 197, 45))

    def vbyte(b):
        b[r49] = 1

    def opaque(b):                       # fillB mode 5 (transparent) -> 0 (no inline either)
        b[lab.offset + 3] &= ~(7 << 3)

    def radius(b):
        struct.pack_into("<h", b, fill.geom_off[4], 3)

    def ink2(b):
        frag = recipe_ops.pd._set_mode(bytes(data[fill.offset:fill.end]), "fillA2", 7,
                                       struct.pack("<H", other))
        assert len(frag) == fill.size
        b[fill.offset:fill.end] = frag

    def attach_geom(b):                  # base modes [0,0,1,1] -> [1,1,1,1]
        b[attach.offset + 1] = 0x55
    for name, tamper in (("v_align", vbyte), ("fillB", opaque), ("radius", radius),
                         ("fillA2", ink2), ("attach", attach_geom)):
        bad = bytearray(data)
        tamper(bad)
        with pytest.raises(AssertionError):
            op.verify(bytes(bad), ctx)
