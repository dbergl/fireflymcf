"""`tanknames` -- each 1FFB7 TANK_STATUS instance's name, read from the panel's own gauge captions.

The derivation is pinned per corpus build, and two tests prove it does not lean on the RV-C
standard's table or on any per-instance constant: the standard's table is emptied, and an
instance is renumbered in the receive filter, and the answer follows the bytes.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from fireflymcf import layout, tankbroadcast, tanknames, theme

REPO = Path(__file__).resolve().parents[1]

# instance -> caption, per build. Every caption agrees with RV-C's table on these three builds;
# 11.23's floorplan cluster 872 alone calls instance 2 "GREY1" (it also draws GREY2).
EXPECTED = {
    "firefly-13.6/mcf.bin": {0: "FRESH", 1: "BLACK", 2: "GREY", 3: "LP", 18: "GREY2"},
    "firefly-11.23/mcf.bin": {0: "FRESH", 1: "BLACK", 2: "GREY / GREY1", 3: "LP", 18: "GREY2"},
    "firefly-14.7 (Terrain)/mcf.bin": {0: "FRESH", 1: "BLACK", 2: "GREY", 3: "LP", 18: "GREY2"},
}


def image(name):
    return (REPO / name).read_bytes()


def _clear():
    for f in (tanknames.tank_gauges, tanknames.level_stores, tanknames._captioned_containers,
              tanknames.decoder_ranges, tanknames._guicode):
        f.cache_clear()


@pytest.fixture
def clean_caches():
    _clear()
    yield
    _clear()


def check_names(data, expected):
    got = {i: n for i, (n, _src) in tanknames.tank_names(data).items()}
    assert got == expected


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_names_per_build(name):
    check_names(image(name), EXPECTED[name])


def test_136_chain_is_the_cited_one():
    """13.6: var 0 is read at 0x24838A, its percent stored to 0x200399E0 (cell 255) and its
    73-px empty offset to 0x2003A6D5 (cell 254); the gauge container after 'FRESH' names 255
    in every HOME floorplan cluster (911-921) and both PROBE TANK DIAGNOSTICS rows."""
    data = image("firefly-13.6/mcf.bin")
    g = {x.instance: x for x in tanknames.tank_gauges(data)}
    assert g[0].var == 0 and g[0].load_at == (0x24838A, 0x2483B2)
    assert g[0].stores == ((0x2483B0, 0x200399E0), (0x2483EC, 0x2003A6D5))
    assert g[0].cell_indices == (254, 255)
    assert {o for o, _r, _t in g[0].captions} == set(range(911, 922)) | {1018, 1019}
    assert g[18].var == 138 and g[18].load_at == (0x2484D2,)
    assert g[18].stores[0] == (0x2484FA, 0x2003A13B)
    assert tanknames.decoder_ranges(data)[0] == (0x247BF8, 0x24EDB0)


def test_caption_sits_on_its_gauge_well():
    """The caption inference, checked against the layout engine's own placement: on 13.6 WD
    with the Black Tank option on, each caption box ends exactly where its well begins and
    shares its centre line. This is what 'the label before the container is its caption' means."""
    data = image("firefly-13.6/mcf.bin")
    placed = layout.Screen(data).walk(4, slot_values={5: 8, 112: 1})
    wells = {p.obj_index: p for p in placed if p.wtype == 32}
    gauge_child = {"FRESH": 721, "GREY": 723, "GREY2": 725, "BLACK": 719}
    labels = {p.label.strip(): p for p in placed
              if p.wtype == 11 and p.label and p.label.strip() in gauge_child}
    assert set(labels) == set(gauge_child)
    for text, child in gauge_child.items():
        cap, well = labels[text], wells[child]
        assert cap.y1 == well.y0, text
        assert abs((cap.x0 + cap.x1) - (well.x0 + well.x1)) <= 1, text


def test_does_not_read_the_rvc_table(clean_caches, monkeypatch):
    monkeypatch.setattr(tankbroadcast, "INSTANCE_NAMES", {})
    check_names(image("firefly-13.6/mcf.bin"), EXPECTED["firefly-13.6/mcf.bin"])


def test_a_renumbered_instance_follows_the_filter(clean_caches):
    """Point var 0's filter at instance 7: the name moves with it. Nothing is keyed on 0."""
    from fireflymcf import guidata
    data = bytearray(image("firefly-13.6/mcf.bin"))
    gs, _ge = guidata.guidata_extent(data)
    entry = gs + guidata.variable_table(bytes(data))[0].offset
    assert data[entry:entry + 5] == bytes.fromhex("b7ff290100")
    data[entry + 4] = 7
    got = {i: n for i, (n, _s) in tanknames.tank_names(bytes(data)).items()}
    assert got == {7: "FRESH", 1: "BLACK", 2: "GREY", 3: "LP", 18: "GREY2"}


def test_a_decoder_of_another_shape_names_nothing(clean_caches):
    """Break the decoder's `mov r4, r0` (0x247BFC): r4 is no longer provably the slot array, so
    no level is traced and every name is None -- never a borrowed RV-C name."""
    data = bytearray(image("firefly-13.6/mcf.bin"))
    sec = theme.guicode_section(bytes(data))
    off = sec.offset + (0x247BFC - theme.guicode_base(sec))
    assert data[off:off + 2] == bytes.fromhex("0446")          # mov r4, r0
    data[off:off + 2] = bytes.fromhex("0546")                  # mov r5, r0
    names = tanknames.tank_names(bytes(data))
    assert set(names) == {0, 1, 2, 3, 18}
    assert all(n is None for n, _s in names.values())


def test_the_pin_can_fail(clean_caches, monkeypatch):
    # clean_caches first: its teardown runs AFTER monkeypatch restores the real functions, so
    # no stubbed result is left memoised for a later test in this worker.
    """Stub the GuiCode trace to find nothing: the per-build check must then fail."""
    monkeypatch.setattr(tanknames, "level_stores", lambda data, var: ((), ()))
    with pytest.raises(AssertionError):
        check_names(image("firefly-13.6/mcf.bin"), EXPECTED["firefly-13.6/mcf.bin"])
    monkeypatch.setattr(tanknames, "_captioned_containers", lambda data: ())
    with pytest.raises(AssertionError):
        check_names(image("firefly-13.6/mcf.bin"), EXPECTED["firefly-13.6/mcf.bin"])
