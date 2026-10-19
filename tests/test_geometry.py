"""Moving a widget's draw elements.

`set_position` rests on a hardware fact (`docs/MCF-FORMAT.md` §6): the two u16 immediately
before a text/icon record's `01 <font> ff` selector are its absolute `(x, y)`. That was
proven on the coach by probe 1 -- writing the Climate Starlink caption's x (file
`0x097233`, `80 -> 400`) moved the word ~320 px right. These tests anchor on that exact
offset and value, so a decode drift fails against ground truth, not against a fixture.
"""
from __future__ import annotations

import struct

import pytest

from fireflymcf import guidata, patch


BUILDS = {
    "11.23": "firefly-11.23/mcf.bin",
    "13.6": "firefly-13.6/mcf.bin",
    "14.7": "firefly-14.7 (Terrain)/mcf.bin",
}

# Probe-1 ground truth, in stock 13.6 (the rename did not move the geometry): the Climate
# Bath Fan caption's length byte, and the coordinate the probe moved.
CLIMATE_CAPTION_LEN = 0x097240
CLIMATE_CAPTION_X_OFFSET = 0x097233
CLIMATE_CAPTION_XY = (80, 75)


def load(build: str) -> bytes:
    with open(BUILDS[build], "rb") as handle:
        return handle.read()


@pytest.fixture(scope="module")
def mcf_136() -> bytes:
    return load("13.6")


def test_finder_matches_probe1_ground_truth(mcf_136: bytes) -> None:
    """The one offset a hardware probe pinned: the finder must return exactly it."""
    element = patch.find_draw_element(mcf_136, CLIMATE_CAPTION_LEN)
    assert element.x_offset == CLIMATE_CAPTION_X_OFFSET
    assert (element.x, element.y) == CLIMATE_CAPTION_XY
    assert element.font_offset == CLIMATE_CAPTION_X_OFFSET + 4


def test_set_position_reproduces_the_probe(mcf_136: bytes) -> None:
    """Moving the caption to x=400 must touch exactly the two probe bytes."""
    element = patch.find_draw_element(mcf_136, CLIMATE_CAPTION_LEN)
    p = patch.McfPatcher(mcf_136)
    p.set_position(element, x=400)
    edits = p.edits()
    assert len(edits) == 1
    assert edits[0].file_offset == CLIMATE_CAPTION_X_OFFSET
    assert edits[0].old == struct.pack("<H", 80)
    assert edits[0].new == struct.pack("<H", 400)


def test_move_passes_the_ladder_and_is_confined(mcf_136: bytes) -> None:
    element = patch.find_draw_element(mcf_136, CLIMATE_CAPTION_LEN)
    p = patch.McfPatcher(mcf_136)
    p.set_position(element, x=400, y=200)
    out, changed = p.build()
    patch.verify(mcf_136, out, p.edits(), changed)
    assert changed == ["section-checksum:Lyra 13v6 GuiData"]
    reread = patch.find_draw_element(out, CLIMATE_CAPTION_LEN)
    assert (reread.x, reread.y) == (400, 200)


def test_move_is_invertible(mcf_136: bytes) -> None:
    element = patch.find_draw_element(mcf_136, CLIMATE_CAPTION_LEN)
    p = patch.McfPatcher(mcf_136)
    p.set_position(element, x=400, y=200)
    out, _ = p.build()

    back = patch.McfPatcher(out)
    back.set_position(patch.find_draw_element(out, CLIMATE_CAPTION_LEN),
                      x=CLIMATE_CAPTION_XY[0], y=CLIMATE_CAPTION_XY[1])
    restored, _ = back.build()
    assert restored == mcf_136


def test_only_the_named_axis_moves(mcf_136: bytes) -> None:
    element = patch.find_draw_element(mcf_136, CLIMATE_CAPTION_LEN)
    p = patch.McfPatcher(mcf_136)
    p.set_position(element, y=200)      # x omitted
    edits = p.edits()
    assert len(edits) == 1
    assert edits[0].file_offset == element.y_offset


def test_offscreen_coordinate_is_refused(mcf_136: bytes) -> None:
    element = patch.find_draw_element(mcf_136, CLIMATE_CAPTION_LEN)
    with pytest.raises(patch.PatchError, match="screen range"):
        patch.McfPatcher(mcf_136).set_position(element, x=5000)


@pytest.mark.parametrize("build", sorted(BUILDS))
@pytest.mark.parametrize("text", ["Water\nPump", "Cool"])
def test_finder_generalises_across_builds(build: str, text: str) -> None:
    """The `<x><y> 01 <font> ff` layout holds for other labels and other builds.

    Not every build has every label; a build lacking one simply yields no sites, and the
    test asserts only on the sites that exist -- that each resolves to a plausible on-screen
    coordinate, which is the property `set_position` relies on.
    """
    data = load(build)
    try:
        sites = patch.find_label_sites(data, text)
    except patch.PatchError:
        pytest.skip(f"{text!r} is ambiguous in {build}")
    if not sites:
        pytest.skip(f"{text!r} absent in {build}")
    # The same text can appear both as a positioned draw element and in non-draw contexts
    # (a diagnostics-list row, a menu string). The finder resolves the former and refuses
    # the latter; assert that at least one resolves, and every resolved one is on-screen.
    resolved = 0
    for site in sites:
        try:
            element = patch.find_draw_element(data, site.length_offset)
        except patch.PatchError:
            continue
        resolved += 1
        assert 0 <= element.x <= patch.SCREEN_MAX
        assert 0 <= element.y <= patch.SCREEN_MAX
    assert resolved, f"no occurrence of {text!r} resolved to a draw element in {build}"


def test_finder_refuses_two_selectors() -> None:
    """Two `01 <font> ff` in the window is ambiguous -- the finder must not pick one.

    Built as a synthetic buffer because the corpus has no natural double-selector, but the
    refusal is exactly the guard that stops a wrong coordinate being edited.
    """
    # <x><y> 01 f0 ff  <x><y> 01 f1 ff  <len>  -- two selectors before the length byte.
    buf = bytearray(b"\x00" * 4)
    buf += struct.pack("<H", 50) + struct.pack("<H", 60) + b"\x01\xf0\xff"
    buf += struct.pack("<H", 70) + struct.pack("<H", 80) + b"\x01\xf1\xff"
    length_offset = len(buf)
    buf += b"\x04TEST\x00"
    with pytest.raises(patch.PatchError, match="ambiguous"):
        patch.find_draw_element(bytes(buf), length_offset)


def test_finder_refuses_when_no_selector(mcf_136: bytes) -> None:
    """A label with no `01 <font> ff` before it has no locatable position -- must raise."""
    # A diagnostics-list row is prefix-only text with no draw selector nearby.
    sites = patch.find_label_sites(mcf_136, "Tank Heat Fresh")
    if not sites:
        pytest.skip("fixture label absent")
    raised = False
    for site in sites:
        try:
            patch.find_draw_element(mcf_136, site.length_offset)
        except patch.PatchError:
            raised = True
    assert raised, "expected at least one label with no draw selector to raise"


@pytest.mark.parametrize("build", sorted(BUILDS))
def test_screen_object_map_is_build_general(build):
    """The screen dispatch is located structurally (match table + the switch object), not at the
    13.6 offset/index -- so it must resolve on every build, not just 13.6. Regression for the
    2026-08-29 cross-coach fix: it previously read the 13.6 match table and object index, which was
    silently wrong on 11.23 and raised on 14.7."""
    from fireflymcf import layout
    data = load(build)
    m = layout.screen_object_map(data)
    assert len(m) > 20, f"{build}: only {len(m)} screens mapped"
    # nav-rail screens 0-4 are the five top pages; they map to five consecutive objects on every
    # build (the base differs: 4 on 13.6, 6 on 11.23, 9 on 14.7).
    objs = [m.get(s) for s in range(5)]
    assert all(o is not None for o in objs), f"{build}: nav-rail screens 0-4 not all mapped: {objs}"
    assert objs == list(range(objs[0], objs[0] + 5)), f"{build}: nav rail not consecutive: {objs}"
    assert len(set(m.values())) == len(m), f"{build}: two screen ids share an object"
