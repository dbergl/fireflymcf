"""Readouts the panel silently refuses to update.

The case these pin is `Absorb`/`Float`, where a two-sided fence on one field gates a second field
too, and a live injection at a plausible voltage was discarded with no indication on screen
(`BUS-BASELINE.md` PC.55).
"""
from __future__ import annotations

import pytest

from fireflymcf import decodeguards

pytest.importorskip("capstone")

ABSORB_CELL = 0x20039F30


def test_the_absorb_float_fence_is_found_with_both_sides(mcf_136):
    guard = decodeguards.guard_for_cell(mcf_136, ABSORB_CELL)
    assert guard is not None, "the fence that cost a coach session is not being found"
    assert guard.two_sided
    assert {f[1] for f in guard.fences} == {239, 362}
    assert set(guard.variables) == {206, 207}


def test_the_fence_is_inherited_by_a_second_field(mcf_136):
    """`Float` (var 207) carries the test; `Absorb` (var 206) is discarded with it.

    This is what makes the failure confusing on hardware: a good value for one field is dropped
    because a different field is out of range.
    """
    guard = decodeguards.guard_for_cell(mcf_136, ABSORB_CELL)
    assert guard.variables.index(207) < guard.variables.index(206)


def test_the_scan_finds_the_other_fenced_readouts_too(mcf_136):
    guards = decodeguards.range_guards(mcf_136)
    cells = {g.cell for g in guards}
    assert ABSORB_CELL in cells
    assert len(guards) >= 5, "the scan collapsed -- it should find several fenced readouts"
    assert all(g.fences for g in guards)


def test_enum_tests_are_not_reported_as_ranges(mcf_136):
    """A `cmp #1` state test is not a range fence; only magnitudes count."""
    for guard in decodeguards.range_guards(mcf_136):
        assert all(f[1] > decodeguards.MIN_RANGE_IMMEDIATE for f in guard.fences)


def test_the_sibling_build_scans_without_error(mcf_1123):
    guards = decodeguards.range_guards(mcf_1123)
    assert isinstance(guards, list)
