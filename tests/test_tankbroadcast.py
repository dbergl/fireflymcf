"""`tankbroadcast` — the G12's 1FFB7 TANK_STATUS rules and the switch that turns one off.

Everything here is derived from the corpus. The point of pinning the derivation per build is
that a rebuilt or foreign image which does not match the shape the module reads fails a test
instead of being silently mis-patched.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from fireflymcf import g12_config, patch, tankbroadcast, thumb

REPO = Path(__file__).resolve().parents[1]
CORPUS = ("firefly-11.23/mcf.bin", "firefly-13.6/mcf.bin", "firefly-14.7 (Terrain)/mcf.bin")


def image(name):
    return (REPO / name).read_bytes()


@pytest.fixture(scope="module")
def stock():
    return image("firefly-13.6/mcf.bin")


# --- the derivation --------------------------------------------------------------------
def test_every_build_broadcasts_the_same_four_water_and_waste_tanks():
    """The 12-byte records are byte-identical across the corpus; only their index shifts.

    That is what licenses one module for all three: same DGN, same kind 0x81 (1 Hz periodic),
    same substitution mask, same resolution 3, same level cells B5/B6/B7/B8, same instances.
    """
    seen = {}
    for name in CORPUS:
        data = image(name)
        rows = {b.instance: b for b in tankbroadcast.tank_broadcasts(data)}
        assert {0, 1, 2, 18} <= set(rows), (name, sorted(rows))
        for instance in (0, 1, 2, 18):
            raw = data[rows[instance].rule_file_offset:][:g12_config.RULE_RECORD_SIZE]
            seen.setdefault(instance, raw)
            assert raw == seen[instance], (name, instance, raw.hex(), seen[instance].hex())
    assert seen[0].hex() == "b7ff8102ff03b500ffffffff"
    assert seen[1].hex() == "b7ff8102ff03b701ffffffff"
    assert seen[2].hex() == "b7ff8102ff03b602ffffffff"
    assert seen[18].hex() == "b7ff8102ff03b812ffffffff"


def test_the_level_cell_matches_the_documented_selector_block():
    """`G12-CODE.md`: cells B5-B8 hold the bucketed levels for instances 0/2/1/0x12."""
    rows = {b.instance: b.selector for b in tankbroadcast.tank_broadcasts(image("firefly-13.6/mcf.bin"))}
    assert (rows[0], rows[2], rows[1], rows[18]) == (0xB5, 0xB6, 0xB7, 0xB8)
    assert rows[3] == 0xC2                       # LPG, the one with a real sender


def test_13_6_activation_sites_are_where_the_disassembly_puts_them():
    rows = {b.instance: b for b in tankbroadcast.tank_broadcasts(image("firefly-13.6/mcf.bin"))}
    assert {i: rows[i].activate_at for i in (0, 1, 2, 18)} == \
        {0: 0x08DA, 1: 0x111E, 2: 0x08E0, 18: 0x1144}
    assert {i: rows[i].rule_index for i in (0, 1, 2, 18)} == {0: 45, 1: 47, 2: 46, 18: 48}


def test_every_activation_site_really_is_a_blx_r3():
    """The guard `suppress` writes against, checked independently of `suppress`."""
    for name in CORPUS:
        data = image(name)
        for b in tankbroadcast.tank_broadcasts(data):
            assert data[b.activate_file:b.activate_file + 2] == thumb.blx_reg(3), (name, b)


def test_only_the_per_tick_gated_rules_carry_an_is_active_query():
    """13.6's two gated tanks — black (`cfg[0xEC]`) and grey-2 (the floorplan) — are queried
    before activation; the two switched on once at config-apply are not. The query exists to
    keep a per-tick activation idempotent, so this split is the gate's signature."""
    rows = {b.instance: b for b in tankbroadcast.tank_broadcasts(image("firefly-13.6/mcf.bin"))}
    assert [rows[i].guarded for i in (0, 2)] == [False, False]
    assert [rows[i].guarded for i in (1, 18)] == [True, True]


def test_the_lpg_rule_is_the_oracle_for_the_enable_disable_pairing():
    """Rule #93 is activated at one site and DEACTIVATED at another — and it is the broadcast
    the coach is observed starting and stopping with the `0xEF` mode write. Nothing else in the
    corpus pins the meaning of the two context-struct slots this well."""
    rows = {b.instance: b for b in tankbroadcast.tank_broadcasts(image("firefly-13.6/mcf.bin"))}
    lpg = rows[3]
    assert lpg.rule_index == 93 and lpg.activate_at == 0x18F6
    assert lpg.deactivated_at == (0x19AC,)
    # No water/waste rule has one, which is exactly why those need a patch.
    assert all(rows[i].deactivated_at == () for i in (0, 1, 2, 18))


# --- the edit --------------------------------------------------------------------------
def test_suppress_changes_eight_bytes_and_one_checksum(stock):
    out, edits = tankbroadcast.suppress(stock)
    assert len(edits) == 4
    assert all(new == thumb.NOP2 and old == thumb.blx_reg(3) for _o, old, new, _w in edits)
    sealed, changed = patch.reseal(out)
    assert changed == ["section-checksum:G12"]
    assert sum(1 for a, b in zip(stock, sealed) if a != b) == 10   # 8 instruction + 2 checksum
    assert len(sealed) == len(stock)


def test_suppress_leaves_the_lpg_tank_alone(stock):
    sealed, _ = patch.reseal(tankbroadcast.suppress(stock)[0])
    assert sorted(tankbroadcast.suppressed_instances(sealed)) == [0, 1, 2, 18]
    lpg = [b for b in tankbroadcast.tank_broadcasts(sealed) if b.instance == 3][0]
    assert lpg.activate_at == 0x18F6


def test_suppress_leaves_the_rule_records_intact(stock):
    """The image still DESCRIBES the broadcast, so the revert is one byte-pair per instance and
    `g12-rules` keeps reporting what the firmware would send if it were switched back on."""
    sealed, _ = patch.reseal(tankbroadcast.suppress(stock)[0])
    for b in tankbroadcast.tank_broadcasts(stock):
        at = b.rule_file_offset
        assert sealed[at:at + g12_config.RULE_RECORD_SIZE] == \
            stock[at:at + g12_config.RULE_RECORD_SIZE]


def test_suppress_is_selective(stock):
    sealed, _ = patch.reseal(tankbroadcast.suppress(stock, [1])[0])
    assert tankbroadcast.suppressed_instances(sealed) == [1]


def test_suppress_refuses_a_second_pass(stock):
    """Idempotence is NOT silent here: a site that is already a NOP is a shape this module
    cannot prove it wrote, so it refuses rather than assume."""
    sealed, _ = patch.reseal(tankbroadcast.suppress(stock)[0])
    with pytest.raises(tankbroadcast.TankBroadcastError, match="already suppressed"):
        tankbroadcast.suppress(sealed)


def test_suppress_refuses_an_instance_this_build_does_not_broadcast(stock):
    with pytest.raises(tankbroadcast.TankBroadcastError, match="broadcasts no 1FFB7"):
        tankbroadcast.suppress(stock, [17])


def test_a_damaged_activation_site_is_refused_as_missing(stock):
    """Corrupt the instruction and the SCAN stops seeing it, so the refusal comes from "nothing
    activates this rule" — not from the byte guard. Pinned with the message it actually raises,
    because a test that passes for the wrong reason vouches for the wrong guard."""
    rows = {b.instance: b for b in tankbroadcast.tank_broadcasts(stock)}
    tampered = bytearray(stock)
    tampered[rows[0].activate_file] ^= 0xFF
    with pytest.raises(tankbroadcast.TankBroadcastError, match="nothing in this blob switches"):
        tankbroadcast.suppress(bytes(tampered), [0])


def test_the_instruction_guard_catches_a_wrong_blob_base(monkeypatch, stock):
    """What the `old != blx r3` guard is actually for.

    A corrupt instruction is caught upstream by the scan (above), so the guard's real job is the
    one failure the scan cannot see: blob-relative addresses resolved against the wrong file
    base. Mutating the guard away leaves every other test in this file green — checked — so it
    needs its own, and this is the shape that fires it."""
    real = tankbroadcast.blob_file_offset(stock)
    monkeypatch.setattr(tankbroadcast, "blob_file_offset", lambda data: real + 2)
    with pytest.raises(tankbroadcast.TankBroadcastError, match="expected `blx r3`"):
        tankbroadcast.suppress(stock, [0])


def test_suppress_works_on_every_build_in_the_corpus():
    """11.23 activates all four unconditionally and has no LPG rule; 14.7 gates all five. The
    edit is derived per build, so it applies to each without a version check."""
    for name in CORPUS:
        data = image(name)
        sealed, changed = patch.reseal(tankbroadcast.suppress(data)[0])
        # The section's NAME carries the build ("G12", "G12 11v23"), so match the prefix -- but
        # still insist it is the only checksum that moved.
        assert len(changed) == 1 and changed[0].startswith("section-checksum:G12"), (name, changed)
        assert sorted(tankbroadcast.suppressed_instances(sealed)) == [0, 1, 2, 18], name


def test_describe_names_every_broadcast(stock):
    text = tankbroadcast.describe(stock)
    for instance in (0, 1, 2, 3, 18):
        assert f"instance {instance} " in text
    assert "UNCONDITIONAL" in text and "gated" in text


# --- the audit: is this module's shape really THIS image's shape? -----------------------
#
# `tank_broadcasts` reads the rule table, which is robust anywhere; the activation site is a
# structural scan validated on exactly three images. `audit` is what stands between that scan
# and a build nobody here has seen, so these tests pin what it accepts and what it refuses.


def test_every_corpus_build_passes_the_audit():
    for name in CORPUS:
        report = tankbroadcast.audit(image(name))
        assert report.ok, (name, [s for s in report.signals if not s.ok])


def test_the_audit_derives_the_activate_slot_rather_than_asserting_it():
    """Both derivations must land on 8 independently: the tank family, and the whole blob.

    The constant is what `suppress` patches through, so the audit's job is to show the
    constant is right for THIS image rather than to be believed on the corpus's behalf.
    """
    for name in CORPUS:
        report = tankbroadcast.audit(image(name))
        assert report.family_slot == tankbroadcast.ACTIVATE_SLOT, name
        assert report.modal_slot == tankbroadcast.ACTIVATE_SLOT, name


def test_the_audit_reports_walker_coverage_above_the_corpus_floor():
    for name in CORPUS:
        report = tankbroadcast.audit(image(name))
        assert report.coverage >= tankbroadcast.MIN_BLOB_COVERAGE, (name, report.coverage)
        assert report.coverage <= 1.0


def test_a_foreign_rule_record_fails_the_shape_signal(stock):
    """Flip a byte of the fresh-water record's fixed prefix; the audit must notice."""
    rows = {b.instance: b for b in tankbroadcast.tank_broadcasts(stock)}
    off = rows[0].rule_file_offset
    broken = bytearray(stock)
    broken[off + 3] = 0x06                      # substitution mask 0x02 -> something else
    report = tankbroadcast.audit(bytes(broken))
    # The DGN is unchanged, so the record is still found -- and now fails on its shape.
    assert not report.ok
    assert not report.signal("rule-shape").ok


def test_a_record_that_differs_from_the_corpus_fails_only_the_softer_signal(stock):
    """A changed level cell is legal RV-C but is not what the three known builds ship."""
    rows = {b.instance: b for b in tankbroadcast.tank_broadcasts(stock)}
    broken = bytearray(stock)
    broken[rows[0].rule_file_offset + 6] = 0xB9      # level cell B5 -> B9
    report = tankbroadcast.audit(bytes(broken))
    assert report.signal("rule-shape").ok            # prefix and tail are untouched
    assert not report.signal("known-records").ok


def test_an_already_suppressed_image_audits_clean_and_says_so(stock):
    """The four NOPs remove every slot-8 tank call, so the family derivation cannot run.

    That is not a damaged build and must not read as one: v22 is on the coach.
    """
    patched, _ = tankbroadcast.suppress(stock)
    report = tankbroadcast.audit(patched)
    assert report.ok, [s for s in report.signals if not s.ok]
    assert report.modal_slot == tankbroadcast.ACTIVATE_SLOT
    assert "already suppressed" in report.signal("activation-sites").detail
    # The LPG rule is deliberately left alone, so one live rule still carries the evidence.
    assert report.family_slot == tankbroadcast.ACTIVATE_SLOT

    everything, _ = tankbroadcast.suppress(patched, [3])
    blank = tankbroadcast.audit(everything)
    assert blank.ok, [s for s in blank.signals if not s.ok]
    assert blank.family_slot is None
    assert "already suppressed" in blank.signal("activate-slot").detail


def test_the_audit_refuses_an_image_with_no_tank_rules():
    with pytest.raises(tankbroadcast.TankBroadcastError):
        tankbroadcast.audit(image("firefly-13.6/cli.bin"))


def test_the_audit_text_names_every_signal_and_its_verdict(stock):
    text = tankbroadcast.audit(stock).report()
    for signal in ("rule-shape", "known-records", "activation-sites",
                   "activate-slot", "slot-plurality", "blob-coverage"):
        assert signal in text
    assert "PASS" in text


def test_the_coverage_floor_is_wired_to_the_verdict_not_just_printed(monkeypatch, stock):
    monkeypatch.setattr(tankbroadcast, "MIN_BLOB_COVERAGE", 0.99)
    report = tankbroadcast.audit(stock)
    assert not report.ok
    assert not report.signal("blob-coverage").ok
