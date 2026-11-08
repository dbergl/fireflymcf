"""`donors`: frozen 13.6 record templates behind the grammar proof replace the index reads of
donor records (2026-11-08). The 14.7 numbers below are expectations, measured 2026-11-07/08; the
module locates nothing by them."""
from __future__ import annotations

import pytest

from fireflymcf import (cardreadouts, donors, grammar, guidata, metrics, pagedraw, readout, recipe,
                        records, rvcbutton, statelights, switchbuild)

NEEDS_RESEARCH_DATA = "reads the three stock images"

# Each template's old pin on 13.6: (object, record). The test, not the module, reads them.
OLD_PINS_136 = {
    "rvc.box": (829, 7), "rvc.capsule": (285, 0), "rvc.caption": (285, 1),
    "rvc.touch": (315, 9), "switch": (9, 9), "readout.directive": (793, 4),
    "readout.wt8": (793, 5), "pd.fill": (32, 2), "pd.rbox": (368, 1), "pd.hline": (367, 6),
    "pd.frame": (947, 3), "pd.touch": (186, 1),
}
OLD_GROUP_PINS_136 = {"group.tap": 103, "group.hold": 17}

# Witnesses on 14.7 (2026-11-07 research, `donor_locator.out`; readout.wt8 with its fillB value
# masked, the colour the theme locator supplies).
WITNESSES_147 = {
    "rvc.box": 0, "rvc.capsule": 35, "rvc.caption": 4, "rvc.touch": 43, "switch": 36,
    "readout.directive": 297, "pd.fill": 170, "pd.rbox": 1, "pd.hline": 0, "pd.frame": 9,
    "pd.touch": 8, "readout.wt8": 9,
}
PANEL_INDEX = {"13.6": 8, "14.7": 1}       # theme.locate_theme_init's panel role


@pytest.fixture
def stocks(mcf_136, mcf_147, mcf_1123):
    return {"13.6": mcf_136, "14.7": mcf_147, "11.23": mcf_1123}


def _record(data: bytes, obj: int, j: int) -> bytes:
    gs, _ = guidata.guidata_extent(data)
    r = records.object_records(data, gs + guidata.object_tables(data)[1][obj])[j]
    return bytes(data[r.offset:r.end])


def test_twelve_templates():
    assert set(donors.TEMPLATES) == set(OLD_PINS_136)


@pytest.mark.parametrize("name", sorted(OLD_PINS_136))
def test_template_equals_136_old_pin(stocks, name):
    pin = _record(stocks["13.6"], *OLD_PINS_136[name])
    assert donors.TEMPLATES[name].raw == pin
    raw, proof = donors.template(stocks["13.6"], name)
    assert raw == pin
    assert OLD_PINS_136[name] in proof.witnesses
    assert proof.decoder == 0x252CB0


@pytest.mark.parametrize("name", sorted(OLD_GROUP_PINS_136))
def test_group_prefix_equals_136_old_pin(stocks, name):
    raw = guidata.group_table(stocks["13.6"])[OLD_GROUP_PINS_136[name]].raw
    prefix, _proof = donors.group_prefix(stocks["13.6"], name)
    assert prefix == donors.GROUP_PREFIXES[name].prefix == raw[:4]
    assert len(prefix) == 4
    assert raw[guidata.GROUP_ENTRY_SIZE - 1] == 0      # the pad byte the clone writes


@pytest.mark.parametrize("name", sorted(OLD_PINS_136))
def test_template_on_147(stocks, name):
    raw, proof = donors.template(stocks["14.7"], name)
    assert proof.decoder == 0x255578
    assert len(proof.witnesses) == WITNESSES_147[name]
    if donors.TEMPLATES[name].text:
        assert proof.witnesses
    if name != "readout.wt8":
        assert raw == donors.TEMPLATES[name].raw


@pytest.mark.parametrize("version", ["13.6", "14.7"])
def test_readout_background_is_the_panel_cell(stocks, version):
    raw, proof = donors.template(stocks[version], "readout.wt8")
    r = records.decode_record(raw, 0)
    assert dict((role, (m, v)) for role, m, v in r.colors)["fillB"] == (7, PANEL_INDEX[version])
    assert proof.colour == {"fillB": PANEL_INDEX[version]}
    # everything else is the frozen record
    assert donors.canonical(raw, "readout.wt8") == donors.canonical(
        donors.TEMPLATES["readout.wt8"].raw, "readout.wt8")


def test_templates_refuse_on_1123(stocks):
    data = stocks["11.23"]
    for name in donors.TEMPLATES:
        with pytest.raises(donors.DonorError) as e:
            donors.template(data, name)
        assert e.value.code == "GRAMMAR"
    for name in donors.GROUP_PREFIXES:
        with pytest.raises(donors.DonorError) as e:
            donors.group_prefix(data, name)
        assert e.value.code == "GRAMMAR"
    with pytest.raises(donors.DonorError) as e:
        donors.mode7_index_limit(data)
    assert e.value.code == "GRAMMAR"


def test_builders_refuse_on_1123_with_a_code(stocks):
    """Every rewired builder refuses 11.23 with GRAMMAR, never a raw IndexError/ValueError."""
    data = stocks["11.23"]
    calls = [
        lambda: rvcbutton.button_records(data, touch_id=5, w=100, h=50, text="Hi", glyph=None,
                                         font=None, fill_index=10),
        lambda: rvcbutton.gesture_group(data, form="tap", hold_seconds=None,
                                        binding_start=0, count=1),
        lambda: rvcbutton.gesture_group(data, form="hold", hold_seconds=2,
                                        binding_start=0, count=1),
        lambda: statelights.capsule(data, fill_index=10, w=100, h=50),
        lambda: switchbuild.author_switch(data, value_slot=1, arms_base=2, count=2),
        lambda: readout.author_readout_object(data, x=1, y=1, display_slot=3,
                                              directive=readout.directive_bytes(3, 0)),
        lambda: pagedraw.fill(data, 0, 0, 10, 10, (1, 2, 3)),
        lambda: pagedraw.rbox(data, 0, 0, 10, 10, 2, (1, 2, 3), (4, 5, 6)),
        lambda: pagedraw.capsule(data, 0, 0, 10, 10, 2, (1, 2, 3)),
        lambda: pagedraw.hline(data, 0, 0, 10, (1, 2, 3)),
        lambda: pagedraw.frame(data, 0, 0, 10, 10, (1, 2, 3)),
        lambda: pagedraw.label(data, "Hi", 0, 0, 60, 30, font=15, rgb=(1, 2, 3)),
        lambda: pagedraw.readout(data, 0, 0, 60, 30, value_slot=3, width=3, places=0, font=15,
                                 rgb=(1, 2, 3), bg=(4, 5, 6)),
        lambda: pagedraw.touch(data, 0, 0, 10, 10, 5),
    ]
    for call in calls:
        with pytest.raises(donors.DonorError) as e:
            call()
        assert e.value.code == "GRAMMAR"


def test_text_template_without_witness_refuses(stocks, monkeypatch):
    """Where the text drawers are not twins (14.7), a text record no stock record shares the form
    of refuses NO_TEMPLATE (the gate can fail: the caption's R49 line height changed to one no
    record carries). On 13.6 the drawers' twin proof satisfies the gate."""
    t = donors.TEMPLATES["rvc.caption"]
    raw = bytearray(t.raw)
    r = records.decode_record(t.raw, 0)
    lh = r.label_offset - 1 - 2           # R49: h, v, line height, adds; then the label length
    assert raw[lh] == 0x14
    raw[lh] = 0x77
    fake = donors.Template(**{**t.__dict__, "raw": bytes(raw)})
    monkeypatch.setitem(donors.TEMPLATES, "rvc.caption", fake)
    _raw, proof = donors.template(stocks["13.6"], "rvc.caption")
    assert proof.witnesses == () and "text drawers are twins" in proof.lines[-1]
    with pytest.raises(donors.DonorError) as e:
        donors.template(stocks["14.7"], "rvc.caption")
    assert e.value.code == "NO_TEMPLATE"


# The pagedraw text forms the reference recipes emit on 13.6 (2026-11-08): (font, h_align) labels
# with header align 1, v_align 2 and pitch (20, 0); readouts in fonts 15 and 17, align 3.
REFERENCE_LABELS = [(12, 1), (12, 2), (13, 1), (13, 2), (13, 3), (15, 1), (15, 2), (16, 2),
                    (17, 2), (17, 3), (24, 2)]


@pytest.mark.parametrize("version", ["13.6", "14.7"])
def test_pagedraw_reference_text_forms_accepted(stocks, version):
    data = stocks[version]
    for font, h in REFERENCE_LABELS:
        pagedraw.label(data, "A", 0, 0, 60, 60, font=font, rgb=(1, 2, 3), h_align=h)
    pagedraw.label(data, "A\nB", 0, 0, 60, 120, font=15, rgb=(1, 2, 3), multiline=True)
    for font in (15, 17):
        pagedraw.readout(data, 0, 0, 80, 60, value_slot=3, width=3, places=0, font=font,
                         rgb=(1, 2, 3), bg=(4, 5, 6))


def test_pagedraw_text_field_without_occurrence_refuses_on_147(stocks):
    """14.7's wt-8 records use fonts 7, 9, 11-15 and 17 only, and no wt-11 has h_align 0: a
    readout in font 24 or a label with h_align 0 refuses, naming the field. 13.6 accepts both
    (the drawers are twins)."""
    def ro(d):
        return pagedraw.readout(d, 0, 0, 80, 60, value_slot=3, width=3, places=0, font=24,
                                rgb=(1, 2, 3), bg=(4, 5, 6))

    def lab(d):
        return pagedraw.label(d, "A", 0, 0, 60, 60, font=15, rgb=(1, 2, 3), h_align=0)
    for make, field in ((ro, "font 24"), (lab, "h_align 0")):
        make(stocks["13.6"])
        with pytest.raises(donors.DonorError) as e:
            make(stocks["14.7"])
        assert e.value.code == "NO_TEMPLATE" and field in str(e.value)


def test_wtype_whose_arm_differs_refuses_on_147(stocks, monkeypatch):
    """A template of wt 10/14/18 (renderer arms not twins on 14.7) is accepted on 13.6 and
    refused GRAMMAR on 14.7. 13.6 carries wt 10 records (8) and no wt 14/18."""
    data = stocks["13.6"]
    gs, _ = guidata.guidata_extent(data)
    for obj, off in enumerate(guidata.object_tables(data)[1]):
        try:
            recs = records.object_records(data, gs + off)
        except (IndexError, ValueError):
            continue
        hit = next((r for r in recs if r.wtype == 10), None)
        if hit:
            break
    raw = bytes(data[hit.offset:hit.end])
    fake = donors.Template(source=(obj, recs.index(hit)), raw=raw, rewrites=(), text=False,
                           draws=True)
    monkeypatch.setitem(donors.TEMPLATES, "probe.wt10", fake)
    donors.template(stocks["13.6"], "probe.wt10")
    with pytest.raises(donors.DonorError) as e:
        donors.template(stocks["14.7"], "probe.wt10")
    assert e.value.code == "GRAMMAR" and "wt 10" in str(e.value)


def test_font_slot_checked(stocks):
    with pytest.raises(donors.DonorError) as e:
        donors.template(stocks["13.6"], "rvc.caption", font=200)
    assert e.value.code == "NO_TEMPLATE" and "200" in str(e.value)
    donors.template(stocks["13.6"], "rvc.caption", font=15)
    with pytest.raises(donors.DonorError) as e:
        rvcbutton.button_records(stocks["13.6"], touch_id=5, w=100, h=50, text="Hi", glyph=None,
                                 font=200, fill_index=10)
    assert e.value.code == "NO_TEMPLATE"


def _without_glyph(monkeypatch, char: str):
    """Every stock font covers every byte (2026-11-08), so a missing glyph is simulated: the
    measurer reports `char` missing, as `metrics.text_width` does for an absent glyph."""
    real = metrics.text_width

    def fake(data, slot, text, table=None):
        return None if char in text else real(data, slot, text, table)
    monkeypatch.setattr(metrics, "text_width", fake)


def test_caption_font_must_cover_the_text(stocks, monkeypatch):
    data = stocks["13.6"]
    rvcbutton.button_records(data, touch_id=5, w=100, h=50, text="Hi\nyo", glyph=None,
                             font=None, fill_index=10)
    _without_glyph(monkeypatch, "y")
    with pytest.raises(donors.DonorError) as e:
        rvcbutton.button_records(data, touch_id=5, w=100, h=50, text="Hi\nyo", glyph=None,
                                 font=None, fill_index=10)
    assert e.value.code == "NO_TEMPLATE" and "'yo'" in str(e.value)


@pytest.mark.parametrize("char", list(donors.READOUT_CHARS))
def test_readout_font_must_cover_digits_point_and_minus(stocks, monkeypatch, char):
    data = stocks["13.6"]
    calls = [
        lambda: readout.author_readout_object(data, x=1, y=1, display_slot=3,
                                              directive=readout.directive_bytes(3, 0)),
        lambda: readout.author_readout_object(data, x=1, y=1, display_slot=3,
                                              directive=readout.directive_bytes(3, 0),
                                              font=cardreadouts.VALUE_FONT),
        lambda: pagedraw.readout(data, 0, 0, 60, 30, value_slot=3, width=3, places=0, font=17,
                                 rgb=(1, 2, 3), bg=(4, 5, 6)),
    ]
    for call in calls:
        call()
    _without_glyph(monkeypatch, char)
    for call in calls:
        with pytest.raises(donors.DonorError) as e:
            call()
        assert e.value.code == "NO_TEMPLATE"


def test_card_value_font_is_checked(stocks, monkeypatch):
    seen = []
    real = donors.template

    def spy(data, name, *, font=None):
        seen.append((name, font))
        return real(data, name, font=font)
    monkeypatch.setattr(donors, "template", spy)
    cardreadouts._value_records(stocks["13.6"], 10, 10, 3, 3, 0)
    assert ("readout.wt8", cardreadouts.VALUE_FONT) in seen


def test_mode7_index_bound(stocks):
    assert donors.mode7_index_limit(stocks["13.6"]) == 0x298
    assert donors.mode7_index_limit(stocks["14.7"]) == 0x1F8
    for version, limit in (("13.6", 0x298), ("14.7", 0x1F8)):
        data = stocks[version]
        rvcbutton.button_records(data, touch_id=5, w=100, h=50, text="Hi", glyph=None,
                                 font=None, fill_index=limit - 1)
        statelights.capsule(data, fill_index=limit - 1, w=100, h=50)
        for call in (
                lambda: rvcbutton.button_records(data, touch_id=5, w=100, h=50, text="Hi",
                                                 glyph=None, font=None, fill_index=limit),
                lambda: statelights.capsule(data, fill_index=limit, w=100, h=50)):
            with pytest.raises(donors.DonorError) as e:
                call()
            assert e.value.code == "MODE7_INDEX"


def test_canonical_masks_rewritten_fields():
    t = donors.TEMPLATES["rvc.caption"]
    a = bytearray(t.raw)
    r = records.decode_record(t.raw, 0)
    a[r.geom_off[2]] ^= 0x11
    relabelled = bytes(a[:r.label_offset - 1]) + bytes([3]) + b"abc\x00"
    assert donors.canonical(relabelled, "rvc.caption") == donors.canonical(t.raw, "rvc.caption")
    b = bytearray(t.raw)
    b[r.font_off] = 0x0F      # the font is kept, not rewritten by the template
    assert donors.canonical(bytes(b), "rvc.caption") != donors.canonical(t.raw, "rvc.caption")
    with pytest.raises(donors.DonorError):
        donors.canonical(t.raw + b"\x00", "rvc.caption")


def test_recipe_surfaces_coded_refusals():
    class Spec:
        id = "probe"

    class Op:
        spec = Spec()

        def __init__(self, exc):
            self.exc = exc

        def apply(self, data, ctx):
            raise self.exc

        def verify(self, data, ctx):
            raise self.exc

    for exc, code in ((donors.DonorError("NO_TEMPLATE", "x"), "NO_TEMPLATE"),
                      (donors.DonorError("NO_DONOR", "x"), "NO_DONOR"),
                      (grammar.GrammarError("x"), "GRAMMAR")):
        with pytest.raises(recipe.RecipeError) as e:
            recipe._apply(Op(exc), b"", None, 8)
        assert e.value.code == code and e.value.op_id == "probe"
        with pytest.raises(recipe.RecipeError) as e:
            recipe._verify(Op(exc), b"", None)
        assert e.value.code == code and e.value.op_id == "probe"


def test_custom_readout_donor_refuses_with_a_code(stocks):
    with pytest.raises(donors.DonorError) as e:
        readout.author_readout_object(stocks["13.6"], x=1, y=1, display_slot=3,
                                      directive=readout.directive_bytes(3, 0),
                                      donor=(10 ** 6, 5))
    assert e.value.code == "NO_DONOR"
    for bad in ((-1, 5), (793, 0), (793, -2)):     # a negative index would wrap silently
        with pytest.raises(donors.DonorError) as e:
            readout.author_readout_object(stocks["13.6"], x=1, y=1, display_slot=3,
                                          directive=readout.directive_bytes(3, 0), donor=bad)
        assert e.value.code == "NO_DONOR"
    with pytest.raises(donors.DonorError) as e:      # the grammar gate comes first
        readout.author_readout_object(stocks["11.23"], x=1, y=1, display_slot=3,
                                      directive=readout.directive_bytes(3, 0), donor=(782, 5))
    assert e.value.code == "GRAMMAR"
    with pytest.raises(donors.DonorError) as e:
        readout.author_readout_object(stocks["11.23"], x=1, y=1, display_slot=3,
                                      directive=readout.directive_bytes(3, 0), donor=(10, 2))
    assert e.value.code == "GRAMMAR"
