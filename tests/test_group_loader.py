"""What the LCD firmware's group-table loader asks of a file.

`FUN_0021B420` (`docs/LCD-FIRMWARE.md` Finding 14) builds one runtime record per group at boot.
It drops the whole table -- every group, so every control -- if any group has both top bits of
byte 1 set, or if the tag-0x10 directory entry's extra count (`b3`) exceeds 10. Neither is a
structural gate, so a build that broke one would still pass the ladder. Checked on every stock
build and on the running coach's recipe.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from fireflymcf import guidata, recipe

BUILDS = ("firefly-11.23/mcf.bin", "firefly-13.6/mcf.bin", "firefly-14.7 (Terrain)/mcf.bin")
EXPERIMENT = Path("builds/golden-v22-anchor.ffproj.json")


def _descriptor(data: bytes, tag: int) -> bytes:
    gs, _ = guidata.guidata_extent(data)
    for i in range(data[gs + 7]):
        entry = data[gs + 8 + i * 8:gs + 16 + i * 8]
        if entry[0] == tag:
            return entry
    raise AssertionError(f"no tag-{tag:#04x} directory entry")


@pytest.fixture(scope="module", params=BUILDS + ("experiment recipe",))
def image(request) -> bytes:
    if request.param == "experiment recipe":
        stock = Path("firefly-13.6/mcf.bin").read_bytes()
        result = recipe.compile_project(recipe.Project.from_json(EXPERIMENT.read_text()), stock)
        assert result.ok, result.errors
        return result.data
    return Path(request.param).read_bytes()


def test_the_group_count_is_the_directory_b2_plus_b3(image: bytes) -> None:
    entry = _descriptor(image, 0x10)
    assert entry[2] + entry[3] == len(guidata.group_table(image))


def test_the_extra_count_is_within_the_loaders_bound(image: bytes) -> None:
    assert _descriptor(image, 0x10)[3] <= 10


def test_no_group_sets_both_top_bits_of_byte_1(image: bytes) -> None:
    bad = [i for i, g in enumerate(guidata.group_table(image)) if g.raw[1] & 0xC0 == 0xC0]
    assert not bad, f"groups {bad} would make the loader drop every control"


def test_every_slice_lies_inside_the_binding_table(image: bytes) -> None:
    bindings = len(guidata.binding_table(image))
    for i, g in enumerate(guidata.group_table(image)):
        start = g.raw[4] | g.raw[5] << 8
        span = g.raw[6] + (0 if g.raw[7] == 0xFF else g.raw[7])
        assert start + span <= bindings, f"group {i} runs past the binding table"


def test_no_group_is_armed_at_boot(image: bytes) -> None:
    """A group with byte 2 bits 4-5 set, whose slice holds a binding flagged to repeat in the
    short-tap-release phase, is loaded already in that phase: the panel would re-send the frame
    periodically from power-on with no press. No build sets those bits, so the path has never
    run; this keeps it that way until someone means it."""
    armed = [i for i, g in enumerate(guidata.group_table(image)) if g.raw[2] & 0x30]
    assert not armed
