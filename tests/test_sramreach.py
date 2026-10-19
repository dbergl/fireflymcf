"""`sramreach`: base+offset reachability of the GuiCode working area (2026-09-04)."""
from __future__ import annotations

import re

import pytest

from fireflymcf import followchannel, sramreach, statusbind, status_map, theme

STOCK = "firefly-13.6/mcf.bin"


@pytest.fixture(scope="module")
def data() -> bytes:
    with open(STOCK, "rb") as handle:
        return handle.read()


def test_the_scanner_premise_is_false_movw_exists_and_offsets_are_large(data: bytes) -> None:
    """`_guicode_references` says 13.6 has no movw. It has hundreds, and the render state is
    addressed with offsets up to ~0x96b4 off 0x200301B0 — a form a pool scan cannot see."""
    r = sramreach.reach(data)
    assert r.state_span[0] == sramreach.RENDER_STATE_BASE
    assert r.state_span[1] - r.state_span[0] >= 0x96B4
    assert len(r.base_offset) > 1000


def test_base_offset_reaches_addresses_no_pool_word_names(data: bytes) -> None:
    """The base+offset leg contributes: addresses reached only that way exist, and the public
    predicate reports them. This is what a raw-word scan silently misses."""
    r = sramreach.reach(data)
    only_via_offset = [a for a in r.base_offset
                       if not any(f != "base_offset" for f, _ in r.forms(a, 1))]
    assert len(only_via_offset) > 50
    for a in only_via_offset[:20]:
        assert sramreach.is_referenced(data, a)
        assert not followchannel._guicode_references(
            data[theme.guicode_section(data).offset:
                 theme.guicode_section(data).offset + theme.guicode_section(data).length],
            theme.guicode_base(theme.guicode_section(data)), a)


def test_every_scratch_cell_the_shipping_builds_use_survives_the_base_offset_trace(data: bytes) -> None:
    """The four halfword-safe spare cells on stock — 211/213/215/217, which the follower, the Low
    Volts bridge and the pump routine allocate on v10-v15 — are reached by NO base+offset form,
    so the unflashed v15's scratch allocation stands under a stronger scan than it was made with."""
    r = sramreach.reach(data)
    cells = status_map.cell_table(data)
    for idx in (211, 213, 215, 217):
        addr = cells[idx]
        assert idx in statusbind.spare_cell_indices(data)
        forms = sramreach.reaching_forms(data, addr, 2)
        assert forms["base_offset"] == [], f"cell {idx} reached by base+offset: {forms}"
        assert not forms["state_struct"]
        assert forms["raw_word"] == []               # spare: no pool word names it on stock
        # only cell-table entries inside the halfword, and every one of them spare
        assert hex(addr) in forms["cell_table"]
        spare = statusbind.spare_cell_indices(data)
        for c in forms["cell_table"]:
            assert int(c, 16) in (addr, addr + 1)
            assert all(i in spare for i, a in enumerate(cells) if a == int(c, 16))


def test_the_register_indexed_bases_are_the_four_bounded_ones(data: bytes) -> None:
    """The residual this trace cannot bound by itself. Each is bounded by hand in GUICODE.md;
    a new one appearing means the argument must be redone, not extended."""
    r = sramreach.reach(data)
    assert set(r.indexed_bases) == {0x200301B0, 0x200371D1, 0x2003A8B8, 0x2003A9A8}
    # the two unbounded ones poison everything above them; nothing below 0x2003A8B8 is offered
    assert (0x2003A8B8, sramreach.SRAM_HIGH) in r.poisoned
    assert all(a + 4 <= 0x2003A8B8 for a in sramreach.free_words(data))


def test_free_words_exist_and_are_unreferenced_by_every_form(data: bytes) -> None:
    free = sramreach.free_words(data)
    assert len(free) >= 30
    # These two are the assertion: `free_words` filters on "4-aligned, not `referenced`", so a
    # per-element loop re-checking that predicate over `free` cannot fail -- it would pass even
    # if `free_words` returned every address in range. `0x2003A000` (a real hole) and
    # `0x20039D52` (a known live cell, AES Start At / slot 272) are external anchors instead.
    assert 0x2003A000 in free
    assert 0x20039D52 not in free


def test_free_runs_include_the_hole_above_cell_167(data: bytes) -> None:
    runs = sramreach.free_runs(data, 16)
    assert runs and runs[0][1] >= 30
    # External anchor, not a re-check of `free_runs`'s own "not referenced" filter (see the
    # comment in the sibling test above) -- a run genuinely covering the known hole is the thing
    # that can fail.
    assert any(s <= 0x2003A000 < s + n for s, n in runs)


def test_reaching_forms_reports_a_poisoned_address_without_raising(data: bytes) -> None:
    """`Reach.forms` can yield an `indexed_table` detail as a (lo, hi) tuple; `reaching_forms`
    used to seed `out` with only the other four keys and format every detail with `hex()`, so a
    poisoned address raised `KeyError`/`TypeError` instead of reporting the reach. 0x2003A8C0 is
    above the unbounded 0x2003A8B8 indexed base (module docstring), so it is poisoned."""
    forms = sramreach.reaching_forms(data, 0x2003A8C0, 4)
    assert forms["indexed_table"], forms
    for lo_hi in forms["indexed_table"]:
        assert re.fullmatch(r"0x[0-9A-F]+-0x[0-9A-F]+", lo_hi), lo_hi
    # a spare cell below the poisoned range reports no indexed_table reach at all
    spare = sramreach.reaching_forms(data, 0x20039F68, 4)
    assert spare["indexed_table"] == []
