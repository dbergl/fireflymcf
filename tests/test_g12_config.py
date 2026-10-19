"""Tests for the G12-section decoder (`fireflymcf.g12_config`).

Two things are asserted:

1. The container structure (head, self-terminating directory, tables tiling the body)
   reproduces across all three builds.
2. The per-output hardware config in **table 0x82** reproduces the owner's oracles WITHOUT
   fitting -- the fields are read from bytes and the oracle only validates them:
     * outputs 1-8 current rating = 15/15/20/20/10/10/10/10 A (raw milliamps in region A);
     * output 44 is the unique 12 A high-current output (region C minority class byte), in
       every build; 21-43 are 6 A;
     * region A ratings are byte-identical across builds (base config, not coach automation).

   The honest negatives are also locked in: `output_config` never fabricates the two fields
   that genuinely live in GuiData, not the G12 section (latch/dimmer and master).
"""
from __future__ import annotations

import pathlib

import pytest

from fireflymcf import g12_config

ROOT = pathlib.Path(__file__).resolve().parents[1]
BUILDS = {
    "11.23": ROOT / "firefly-11.23" / "mcf.bin",
    "13.6": ROOT / "firefly-13.6" / "mcf.bin",
    "14.7": ROOT / "firefly-14.7 (Terrain)" / "mcf.bin",
}
COMMON_TAGS = {0x0010, 0x0020, 0x0030, 0x0061, 0x0062, 0x0065, 0x1766, 0x0068, 0x0082, 0x0067}


def _data(name):
    return BUILDS[name].read_bytes()


# --- structure --------------------------------------------------------------------------
@pytest.mark.parametrize("name", list(BUILDS))
def test_parse_structure_all_builds(name):
    g = g12_config.parse_g12(_data(name))
    assert g.name.startswith("G12")
    assert len(g.tables) == 11
    assert COMMON_TAGS <= {t.tag for t in g.tables}
    code = g.table(g12_config.CODE_TABLE_TAG)
    assert code is not None and code.size > 0
    assert g.body[code.offset:code.offset + 2] == g12_config.CODE_HEADER_SIG


def test_minor_version_and_build_tag_13_6():
    g = g12_config.parse_g12(_data("13.6"))
    assert g.build_tag == 0x1234
    assert g.minor_version == 0x06


# --- per-output config: the oracles that reproduce --------------------------------------
def test_outputs_1_8_current_ratings_match_pdf():
    """Region A. Outputs 1-8 relay ratings, raw milliamps in the bytes. PDF: 1,2=15A;
    3,4=20A; 5-8=10A."""
    cfg = g12_config.output_config(_data("13.6"))
    amps = [cfg[n].current_amps for n in range(1, 9)]
    assert amps == [15.0, 15.0, 20.0, 20.0, 10.0, 10.0, 10.0, 10.0]
    assert [cfg[n].output_type for n in range(1, 9)] == ["relay"] * 8


@pytest.mark.parametrize("name", list(BUILDS))
def test_relay_ratings_identical_across_builds(name):
    """Region A is base hardware config: byte-identical in every build."""
    assert g12_config.relay_ratings(_data(name)) == {
        1: 15000, 2: 15000, 3: 20000, 4: 20000, 5: 10000, 6: 10000, 7: 10000, 8: 10000}


@pytest.mark.parametrize("name", list(BUILDS))
def test_output_44_is_the_lone_12A_high_current(name):
    """Region C. In every build exactly one output (44) carries the minority class byte =
    12 A; 21-43 are 6 A. Derived from the bytes; the oracle only labels/validates."""
    cfg = g12_config.output_config(_data(name))
    highs = [n for n in range(21, 45) if cfg[n].current_class == "high"]
    assert highs == [44]
    assert cfg[44].current_amps == 12.0
    assert all(cfg[n].current_amps == 6.0 for n in range(21, 44))


def test_config_table_partitions_all_44_outputs():
    """8 relay + 12 half-bridge + 24 high-current = 44."""
    cfg = g12_config.output_config(_data("13.6"))
    types = {}
    for n in range(1, 45):
        types.setdefault(cfg[n].output_type, []).append(n)
    assert types["relay"] == list(range(1, 9))
    assert types["half-bridge"] == list(range(9, 21))
    assert types["high-current"] == list(range(21, 45))


def test_halfbridge_polarity_partial():
    """Region B. Outputs 9-18 carry the positive-polarity config word (0x10); 19-20 carry
    0x00 (reported as None, an honest partial rather than a forced 'all positive')."""
    cfg = g12_config.output_config(_data("13.6"))
    assert all(cfg[n].polarity == "positive" for n in range(9, 19))
    assert cfg[19].polarity is None and cfg[20].polarity is None


# --- honest negatives -------------------------------------------------------------------
def test_output_config_does_not_fabricate_guidata_fields():
    """latch/dimmer and master are panel-side (GuiData) facts, never read from table 0x82."""
    cfg = g12_config.output_config(_data("13.6"))
    for info in cfg.values():
        assert info.latch_or_dimmer is None
        assert info.master is None


def test_automation_rules_are_coach_specific_not_base_config():
    """The 12-byte rule tables ARE coach automation (empty 0x20 on 14.7), distinct from the
    base-config table 0x82 which is populated in every build."""
    g14 = g12_config.parse_g12(_data("14.7"))
    assert g14.table(0x0020).size == 0
    assert g14.table(0x0082).size > 0


def test_free_outputs_27_30_are_decoded_but_unreferenced():
    """The owner's question. 27-30 are decoded as high-current 6 A standard outputs, and are
    untouched by the coach automation program (free/spare)."""
    cfg = g12_config.output_config(_data("13.6"))
    for n in (27, 28, 29, 30):
        assert cfg[n].output_type == "high-current"
        assert cfg[n].current_amps == 6.0
        assert cfg[n].current_class == "standard"
        assert cfg[n].referenced is False
        assert cfg[n].in_registry is False


def test_awning_ext_interlock_rule_present():
    """Rule-format oracle: a non-button trigger DGN acting on the AWNING-EXT output (3). The
    proprietary DGN 0xFFB7 -> output 3 rule (the ignition/park interlock candidate) is present
    on 13.6."""
    hits = [r for r in g12_config.rule_records(_data("13.6"))
            if r.dgn_lo == 0xFFB7 and not r.is_registry and r.target_instance == 3]
    assert len(hits) == 1
    assert hits[0].raw.hex() == "b7ff8102ff64c203ffffffff"


def test_registry_membership_matches_scan():
    reg = g12_config.signal_registry(_data("13.6"))
    real = sorted(i for i in reg if i < 0x45)
    assert real == [0, 1, 3, 4, 5, 6, 10, 12, 13, 14, 15, 17, 21, 22, 38, 43, 44, 46, 47, 49, 54]
