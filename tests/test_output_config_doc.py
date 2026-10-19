"""Tests for the generated doc `fireflymcf.output_config_doc`.

Two things are asserted:

1. `render(data)` is deterministic -- calling it twice on the same bytes produces byte-
   identical markdown (no set/dict-ordering leaks, no wall-clock content).
2. `build_rows(data)` reproduces a handful of specific G12-hardware + panel-side facts,
   joining `g12_config.output_config` (G12 hardware, table 0x82) with the panel-side
   GuiData facts (`guidata.dimmable_channels`, `output_config_doc.master_groups`):
     * output 44 (Water Pump) = high-current 12A, panel latch, not a member of either
       master group;
     * output 22 (Kitchen OHC) = dimmable (panel) and a member of the Interior/Light
       Master group;
     * output 21 (Awning LT) = dimmable (panel) and a member of the Exterior Master
       group (note: the file's Exterior Master group ALSO includes channel 35 -- see
       `output_config_doc._master_note` -- while the canonical pin-sheet exterior set is
       {21, 23}; this file honestly reports the union, not the narrowed pin-sheet set);
     * outputs 27-30 = high-current 6A, panel latch, not a member of either master group
       (free/spare high-current outputs).
"""
from __future__ import annotations

import pathlib

import pytest

from fireflymcf import output_config_doc as docgen

ROOT = pathlib.Path(__file__).resolve().parents[1]
MCF_13_6 = ROOT / "firefly-13.6" / "mcf.bin"


def _data():
    return MCF_13_6.read_bytes()


# --- determinism -------------------------------------------------------------------------
def test_render_is_deterministic():
    data = _data()
    assert docgen.render(data) == docgen.render(data)


def test_generated_doc_matches_committed_file():
    """`docs/G12-OUTPUT-CONFIG.md` is generated output, not hand-authored -- regenerating it
    from the committed 13.6 corpus must leave it byte-identical (this is the `git diff`
    empty check from the commit's own verification, re-asserted so drift is caught in CI)."""
    committed = (ROOT / "docs" / "G12-OUTPUT-CONFIG.md").read_text()
    assert docgen.render(_data()) == committed


# --- row-level facts, byte-derived --------------------------------------------------------
def test_output_44_high_current_12a_latch_no_master():
    rows = {r.instance: r for r in docgen.build_rows(_data())}
    r = rows[44]
    assert r.output_type == "high-current"
    assert r.rating == "12 A"
    assert r.dim_latch == "latch"
    assert r.interior_master is False
    assert r.exterior_master is False


def test_output_22_dimmable_interior_master():
    rows = {r.instance: r for r in docgen.build_rows(_data())}
    r = rows[22]
    assert r.dim_latch == "dimmer"
    assert r.interior_master is True
    assert r.exterior_master is False


def test_output_21_dimmable_exterior_master():
    rows = {r.instance: r for r in docgen.build_rows(_data())}
    r = rows[21]
    assert r.dim_latch == "dimmer"
    assert r.exterior_master is True
    assert r.interior_master is False


@pytest.mark.parametrize("n", [27, 28, 29, 30])
def test_outputs_27_30_free_high_current_6a_latch_no_master(n):
    rows = {r.instance: r for r in docgen.build_rows(_data())}
    r = rows[n]
    assert r.output_type == "high-current"
    assert r.rating == "6 A"
    assert r.dim_latch == "latch"
    assert r.interior_master is False
    assert r.exterior_master is False


def test_master_groups_structural():
    """Cross-check note, not a fitted value: the file's own group union for Exterior
    Master includes channel 35 in addition to the canonical pin-sheet {21, 23} -- 35 is
    shared with the Interior/Light Master group. Reported honestly (see `_master_note`),
    not narrowed to match `tests/answer_key.py::EXTERIOR_MASTER`."""
    groups = docgen.master_groups(_data())
    assert groups["Light Master"] == [22, 24, 25, 26, 32, 34, 35]
    assert groups["Exterior Master"] == [21, 23, 35]


# --- the 2026-08-28 enriched findings, validated against the bytes -------------------------
# The generator embeds these as prose (it always has, in this style); these tests anchor that
# prose to the actual table-0x82 bytes, so a build where the finding no longer holds turns the
# generated doc from "documented fact" into a caught test failure rather than a quiet lie.

def test_region_a_word2_is_the_ocp_time_enum():
    """Region A (relays 1-8) is `<u16 mA><u16 0x00fa><u16 ocp_enum><u16 key>`. The 2026-08-28
    decode: word2 is the OCP-time enum, independent of current -- relays 3 and 4 carry 0xa
    (1.0 s) while every other relay carries 0x8 (.26 s), at three different current ratings."""
    from fireflymcf import g12_config
    import struct
    body, base = g12_config._config_table(_data())
    ocp, mA, w1 = {}, {}, set()
    for i in range(8):
        m, a, o, _k = struct.unpack_from("<HHHH", body, base + 0x2c + i * 8)
        ocp[i + 1] = o
        mA[i + 1] = m
        w1.add(a)
    assert w1 == {0x00fa}                                   # word1 is the constant 0x00fa
    assert {n for n, o in ocp.items() if o == 0xa} == {3, 4}   # 1.0 s exactly on relays 3, 4
    assert {o for o in ocp.values()} == {0x8, 0xa}            # only the two enums appear
    assert mA == {1: 15000, 2: 15000, 3: 20000, 4: 20000,    # matches the config PDF
                  5: 10000, 6: 10000, 7: 10000, 8: 10000}


def test_region_d_code_is_redundant_with_region_c_type_lo():
    """The 2026-08-28 identification: region D's per-output code is not an independent field --
    it is fully determined by region C's `type_lo`. Regions C and D are parallel arrays in the
    same PHYSICAL order (region C also carries an index byte that permutes physical position to
    output number; region D is stored positionally), so the redundancy is a physical-order fact:
    reading the two arrays entry-by-entry, `type_lo` fixes the Dcode every time."""
    from fireflymcf import g12_config
    import struct
    body, base = g12_config._config_table(_data())
    mapping = {}
    for i in range(24):
        type_lo = struct.unpack_from("<H", body, base + 0xcc + i * 8)[0]
        code = struct.unpack_from("<H", body, base + 0x18c + i * 4 + 2)[0]
        assert mapping.setdefault(type_lo, code) == code, "type_lo does not determine Dcode"
    assert mapping == {0xa38a: 1221, 0x3916: 3498, 0x722b: 3498}

    # output-number view (region C mapped through its index byte): the two shared type_lo values
    # partition 21-43 into the period-4 AABB interleave the doc lists, with 44 alone as 0x722b.
    hc = g12_config.highcurrent_config(_data())
    outs = lambda tl: sorted(o for o, r in hc.items() if r["type_lo"] == tl)
    assert outs(0xa38a) == [21, 22, 25, 26, 29, 30, 33, 34, 37, 38, 41, 42]
    assert outs(0x3916) == [23, 24, 27, 28, 31, 32, 35, 36, 39, 40, 43]
    assert outs(0x722b) == [44]

    # ...and NOT the 6 A/12 A current capability: the PDF's 12 A-capable positions are neither
    # type_lo group, so the A/B attribute is something else (checked 2026-08-28, doc's claim).
    twelve_a = {22, 23, 24, 27, 30, 32, 33, 35, 39, 41, 42, 44}
    assert twelve_a != set(outs(0xa38a)) and twelve_a != set(outs(0x3916))

    # Because regions C and D are parallel arrays, `highcurrent_config` must map region D through
    # region C's index byte (not positionally), so the redundancy holds BY OUTPUT NUMBER too --
    # every output's type_lo fixes its region_d_code. (A positional mapping mis-assigns the code
    # wherever the index byte is not identity: e.g. output 22 is type_lo 0xa38a -> code 1221, not
    # the 3498 a positional read would give.)
    code_of_typelo = {}
    for out, r in hc.items():
        assert code_of_typelo.setdefault(r["type_lo"], r["code"]) == r["code"]
    assert code_of_typelo == {0xa38a: 1221, 0x3916: 3498, 0x722b: 3498}
    assert g12_config.output_config(_data())[22].region_d_code == 1221
