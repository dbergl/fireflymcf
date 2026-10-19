"""How a widget slot is driven -- and the size of the class nothing explains.

These tests pin `slotsource`'s classification to the file. The important one is the *negative*:
210 of 13.6's 664 GuiCode-RAM slots are reached by no instruction at all, which is what makes the
`Absorb`/`Float` gate (slot 23) unexplained rather than merely undocumented. Two anti-vacuity
guards stand beside it, because a classifier that silently stopped scanning would report every
slot unreferenced and look like a discovery.
"""
from __future__ import annotations

import collections

import pytest

from fireflymcf import config, slotsource

pytest.importorskip("capstone")


def test_every_guicode_ram_slot_is_classified(mcf_136):
    entries = slotsource.slot_sources(mcf_136)
    assert len(entries) == 664
    assert {e.kind for e in entries} <= {"config", "written", "read-only", "unreferenced"}


def test_the_classification_counts_are_what_the_bytes_say(mcf_136):
    counts = collections.Counter(e.kind for e in slotsource.slot_sources(mcf_136))
    assert counts["config"] == 15
    assert counts["written"] == 439
    assert counts["unreferenced"] == 210


def test_the_scan_is_not_vacuous(mcf_136):
    """If the disassembly pass broke, everything would come back unreferenced."""
    entries = slotsource.slot_sources(mcf_136)
    explained = [e for e in entries if e.explained]
    assert len(explained) > 400, "the scan resolved almost nothing -- it is broken"
    assert any(e.writes for e in entries), "no write site resolved anywhere"


def test_known_config_registers_classify_as_config(mcf_136):
    """A control chosen from another module's answer, not from this one's output."""
    registers = set(config.config_offsets(mcf_136).values())
    assert registers, "config_offsets returned nothing -- the control is vacuous"
    for entry in slotsource.slot_sources(mcf_136):
        if entry.address in registers:
            assert entry.kind == "config", f"slot {entry.slot} at {entry.address:#x}"


def test_slot_23_the_absorb_float_gate_is_unexplained(mcf_136):
    """`GUICODE.md` Finding 63: the gate is proven, its writer is not.

    Ghidra agrees on this exact address (0 xrefs, against 3 and 6 for two controls), and the
    coach refuted the receive-fed reading, so this is a real negative and not a scan gap.
    """
    entry = slotsource.slot_source(mcf_136, 23)
    assert entry is not None
    assert entry.address == 0x20039999
    assert entry.vtype == 0
    assert entry.kind == "unreferenced"
    assert entry.reads == [] and entry.writes == []


def test_the_unexplained_class_is_not_a_type_class(mcf_136):
    """It spans the same type bytes the explained set does, so it is not one kind of widget."""
    entries = slotsource.slot_sources(mcf_136)
    unexplained_types = {e.vtype for e in entries if e.kind == "unreferenced"}
    explained_types = {e.vtype for e in entries if e.explained}
    assert len(unexplained_types) >= 5
    assert len(unexplained_types & explained_types) >= 5


def test_the_sibling_build_carries_the_same_open_class(mcf_1123):
    """11.23's own inverter page gates on slot 32, and that slot is unexplained there too."""
    entry = slotsource.slot_source(mcf_1123, 32)
    assert entry is not None and entry.kind == "unreferenced"
    counts = collections.Counter(e.kind for e in slotsource.slot_sources(mcf_1123))
    assert counts["unreferenced"] > 50
    assert counts["written"] > 200
