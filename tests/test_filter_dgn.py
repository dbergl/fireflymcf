"""`statusbind.retarget_filter` -- pointing a receive-filter entry at a different DGN.

The edit this covers is the cheapest way to make the panel subscribe to something new: the boot
loop (`FUN_0021BE50` @`0x0021C03E`) calls `FUN_0021A618` once per variable-table entry, so
rewriting an entry's DGN bytes is the whole change -- registration happens on the next boot.

Two firmware limits give these tests their teeth, and both fail SILENTLY on hardware:
  * extraction modes 0 and 3 are skipped by the `tbb` @`0x0021C99A`, so an entry carrying one
    matches its DGN and writes nothing;
  * `FUN_0021A618` refuses past 83 distinct DGNs (`cmp r2,#0x53` @`0x0021A62A`) and returns 0,
    which no caller checks.
"""
import struct

import pytest

from fireflymcf import coverage, guidata, patch, relocate, statusbind

STOCK = "firefly-13.6/mcf.bin"
PROBE_VAR = 32           # bridged AND the G12 OUTPUTS row-15 painter; pin 15 is released
PROBE_DGN = 0x1FEA0      # GPS_DATE_TIME_STATUS, absent from this corpus and from the coach's bus


@pytest.fixture(scope="module")
def stock():
    with open(STOCK, "rb") as fh:
        return fh.read()


def _entry(data, var):
    return guidata.variable_table(data)[var]


def _decode(raw):
    return {
        "dgn": struct.unpack_from("<I", raw, 0)[0] & statusbind.DGN_MASK,
        "mode": raw[2] >> 4,
        "refresh": (raw[2] >> 2) & 3,
        "payload_byte": raw[3] & 7,
        "instance": raw[4],
    }


def test_registered_dgns_matches_an_independent_count(stock):
    """66 distinct DGNs on 13.6 -- computed here without reusing the helper's own expression."""
    by_hand = set()
    gs, _ = guidata.guidata_extent(stock)
    for v in guidata.variable_table(stock):
        b = stock[gs + v.offset:gs + v.offset + 4]
        by_hand.add((b[0] | b[1] << 8 | b[2] << 16 | b[3] << 24) & 0x3FFFF)
    assert statusbind.registered_dgns(stock) == by_hand
    assert len(by_hand) == 66
    assert len(by_hand) < statusbind.REGISTRY_CAP


def test_cap_is_the_disassembled_constant():
    """0x53 is `cmp r2,#0x53` at 0x0021A62A, not a chosen headroom figure."""
    assert statusbind.REGISTRY_CAP == 0x53


def test_retarget_is_same_size_and_round_trips(stock):
    out, edits = statusbind.retarget_filter(stock, PROBE_VAR, PROBE_DGN,
                                            mode=1, payload_byte=3)
    assert len(out) == len(stock)
    got = _decode(_entry(out, PROBE_VAR).raw)
    assert got["dgn"] == PROBE_DGN
    assert got["mode"] == 1
    assert got["payload_byte"] == 3
    assert got["instance"] == statusbind.FILTER_DISABLED
    # refresh class is inherited, not invented
    assert got["refresh"] == _decode(_entry(stock, PROBE_VAR).raw)["refresh"]
    assert [e.structure for e in edits] == ["filter-dgn", "checksum"]


def test_only_the_entry_and_the_guidata_checksum_move(stock):
    out, _ = statusbind.retarget_filter(stock, PROBE_VAR, PROBE_DGN, mode=1, payload_byte=3)
    gs, _ = guidata.guidata_extent(stock)
    entry = _entry(stock, PROBE_VAR)
    lo, hi = gs + entry.offset, gs + entry.offset + 8
    csums = {p for f in patch.checksum_fields(out) for p in (f.position, f.position + 1)}
    stray = [i for i in range(len(stock))
             if stock[i] != out[i] and not (lo <= i < hi) and i not in csums]
    assert stray == [], f"unexpected bytes changed at {stray[:8]}"


def test_registry_gains_exactly_one_dgn(stock):
    before = statusbind.registered_dgns(stock)
    out, _ = statusbind.retarget_filter(stock, PROBE_VAR, PROBE_DGN, mode=1, payload_byte=3)
    after = statusbind.registered_dgns(out)
    assert after - before == {PROBE_DGN}
    # 1FEDA survives: 41 other entries still watch it, so its loss would mean the wrong entry
    # was edited -- the kind of mistake that is invisible in a byte diff.
    assert guidata.DGN_DC_DIMMER_STATUS_3 in {d & 0xFFFF for d in after}


def test_output_passes_the_structural_gates(stock):
    out, _ = statusbind.retarget_filter(stock, PROBE_VAR, PROBE_DGN, mode=1, payload_byte=3)
    ok, why = relocate._boundary_oracle(out)
    assert ok, why
    assert coverage.analyse(out, deep=True).unclaimed == []
    resealed, changed = patch.reseal(out)
    assert resealed == out and changed == []


@pytest.mark.parametrize("mode", [0, 3, 8, 15])
def test_refuses_modes_the_dispatch_skips(stock, mode):
    """Modes 0/3/8-15 branch to the loop increment: the entry would match and write nothing."""
    with pytest.raises(statusbind.StatusBindError, match="dispatch|mode"):
        statusbind.retarget_filter(stock, PROBE_VAR, PROBE_DGN, mode=mode, payload_byte=3)


def test_refuses_mode_2_running_off_the_payload(stock):
    """Mode 2 reads payload[n] AND payload[n+1]; n=7 would read a ninth byte."""
    statusbind.retarget_filter(stock, PROBE_VAR, PROBE_DGN, mode=2, payload_byte=6)  # fine
    with pytest.raises(statusbind.StatusBindError, match="off the end"):
        statusbind.retarget_filter(stock, PROBE_VAR, PROBE_DGN, mode=2, payload_byte=7)


def test_refuses_an_instance_without_its_compare_byte(stock):
    with pytest.raises(statusbind.StatusBindError, match="compare_byte"):
        statusbind.retarget_filter(stock, PROBE_VAR, PROBE_DGN, mode=1, payload_byte=3,
                                   instance=0x81)
    out, _ = statusbind.retarget_filter(stock, PROBE_VAR, PROBE_DGN, mode=1, payload_byte=3,
                                        instance=0x81, compare_byte=0x00)
    assert _entry(out, PROBE_VAR).raw[4] == 0x81


@pytest.mark.parametrize("dgn", [-1, 0x40000])
def test_refuses_a_non_18_bit_dgn(stock, dgn):
    with pytest.raises(statusbind.StatusBindError, match="18-bit"):
        statusbind.retarget_filter(stock, PROBE_VAR, dgn, mode=1, payload_byte=3)


def test_refuses_exceeding_the_silent_registry_cap(stock):
    """Fill the table with distinct DGNs until the next one would pass 83, then check it refuses.

    Built by repeated same-size retargets rather than by hand-writing bytes, so the guard is
    exercised through the same path a caller would use.
    """
    data = stock
    used = statusbind.registered_dgns(data)
    fresh = (d for d in range(0x30000, 0x3FFFF) if d not in used)
    var = 0
    vt_len = len(guidata.variable_table(data))
    while len(statusbind.registered_dgns(data)) < statusbind.REGISTRY_CAP:
        assert var < vt_len, "ran out of entries before reaching the cap"
        data, _ = statusbind.retarget_filter(data, var, next(fresh), mode=1, payload_byte=0)
        var += 1
    assert len(statusbind.registered_dgns(data)) == statusbind.REGISTRY_CAP
    with pytest.raises(statusbind.StatusBindError, match="cap"):
        statusbind.retarget_filter(data, var, next(fresh), mode=1, payload_byte=0)


# ----------------------------------------------------------------------------------------
# narrowing an open filter: `statusbind.pin_filter_instance`
#
# The three DGNs below are the ones the coach's alternator DC charger (SA 0x80) broadcasts at
# charger instance 0x31 -- 869 `1FFC7`, 8,688 `1FEA3` and 869 `1FF99` frames across ten capture
# sessions, every one of them from that one device. 13.6 subscribes to all three with the `0xFF`
# sentinel, so a second charger would drive the same widgets.
# ----------------------------------------------------------------------------------------

CHARGER_PINS = [(115, 0x1FFC7), (260, 0x1FEA3), (261, 0x1FF99)]
ALTERNATOR_INSTANCE = 0x31


def test_the_three_charger_entries_ship_open(stock):
    """The premise, asserted rather than assumed: stock accepts these DGNs from any device."""
    for var, dgn in CHARGER_PINS:
        raw = _entry(stock, var).raw
        assert _decode(raw)["dgn"] == dgn, (var, raw.hex(" "))
        assert raw[statusbind.FILTER_INSTANCE_FIELD] == statusbind.FILTER_DISABLED
        # +5 == 0 is what makes this a ONE-byte edit: payload byte 0, mask 0xff already.
        assert raw[statusbind.FILTER_COMPARE_FIELD] == 0


def test_pin_is_one_byte_per_entry(stock):
    out = stock
    for var, dgn in CHARGER_PINS:
        out, edits = statusbind.pin_filter_instance(out, var, ALTERNATOR_INSTANCE, expect_dgn=dgn)
        assert [e.structure for e in edits] == ["filter-instance", "checksum"]
    assert len(out) == len(stock)
    gs, _ = guidata.guidata_extent(stock)
    allowed = {gs + _entry(stock, v).offset + statusbind.FILTER_INSTANCE_FIELD
               for v, _d in CHARGER_PINS}
    csums = {p for f in patch.checksum_fields(out) for p in (f.position, f.position + 1)}
    stray = [i for i in range(len(stock))
             if stock[i] != out[i] and i not in allowed and i not in csums]
    assert stray == [], f"unexpected bytes changed at {stray[:8]}"
    # three entry bytes + one 2-byte checksum
    assert sum(1 for i in range(len(stock)) if stock[i] != out[i]) == 5


def test_pin_leaves_what_the_entry_decodes_untouched(stock):
    """The DGN, extraction mode and payload byte must not move: this narrows WHO is matched,
    not WHAT is read. `1FFC7`'s var 115 reads payload[6], the charge-state enum."""
    for var, dgn in CHARGER_PINS:
        before = _entry(stock, var).raw
        out, _ = statusbind.pin_filter_instance(stock, var, ALTERNATOR_INSTANCE, expect_dgn=dgn)
        after = _entry(out, var).raw
        assert after[:4] == before[:4]
        assert after[statusbind.FILTER_COMPARE_FIELD:] == before[statusbind.FILTER_COMPARE_FIELD:]
        assert _decode(after) | {"instance": statusbind.FILTER_DISABLED} == _decode(before)


def test_pin_output_passes_the_structural_gates(stock):
    out = stock
    for var, dgn in CHARGER_PINS:
        out, _ = statusbind.pin_filter_instance(out, var, ALTERNATOR_INSTANCE, expect_dgn=dgn)
    ok, why = relocate._boundary_oracle(out)
    assert ok, why
    assert coverage.analyse(out, deep=True).unclaimed == []
    resealed, changed = patch.reseal(out)
    assert resealed == out and changed == []
    # The registry is untouched -- narrowing a filter must not change what the panel subscribes to.
    assert statusbind.registered_dgns(out) == statusbind.registered_dgns(stock)


def test_pin_refuses_repointing_a_live_filter(stock):
    """Var 16 already compares against the keypad's instance 0x81; re-pointing it is a different
    decision from enabling a disabled one, so it must not be reachable through this door."""
    assert _entry(stock, 16).raw[statusbind.FILTER_INSTANCE_FIELD] == 0x81
    with pytest.raises(statusbind.StatusBindError, match="already compares"):
        statusbind.pin_filter_instance(stock, 16, ALTERNATOR_INSTANCE)


def test_pin_refuses_a_dgn_that_does_not_match(stock):
    with pytest.raises(statusbind.StatusBindError, match="not the expected"):
        statusbind.pin_filter_instance(stock, 115, ALTERNATOR_INSTANCE, expect_dgn=0x1FEA3)


def test_pin_refuses_an_entry_the_dispatch_skips(stock):
    """Var 275 is a mode-0 `1FFD4` entry: it matches and writes nothing, so narrowing what it
    matches changes nothing observable. 13.6 ships 14 such entries."""
    assert _entry(stock, 275).raw[2] >> 4 == 0
    with pytest.raises(statusbind.StatusBindError, match="tbb skips"):
        statusbind.pin_filter_instance(stock, 275, ALTERNATOR_INSTANCE)


def test_pin_refuses_the_disable_sentinel(stock):
    """0xFF would re-open the filter; this primitive only ever narrows."""
    with pytest.raises(statusbind.StatusBindError, match="disable sentinel"):
        statusbind.pin_filter_instance(stock, 115, statusbind.FILTER_DISABLED)


def test_pin_refuses_a_compare_field_it_did_not_expect(stock):
    """+5 selects the payload byte and the mask. Pinning without agreeing on it would test the
    wrong byte -- silently, since the frame would simply stop matching."""
    with pytest.raises(statusbind.StatusBindError, match=r"compare field \+5"):
        statusbind.pin_filter_instance(stock, 115, ALTERNATOR_INSTANCE, expect_compare=0x02)


# ----------------------------------------------------------------------------------------
# transmit side: `newbutton.set_binding_dgn`
# ----------------------------------------------------------------------------------------

def test_binding_payload_reproduces_captured_frames(stock):
    """`proven-from-bus`: three stock bindings equal frames the panel actually put on the wire.

    This is what establishes that a binding's payload is two little-endian u32 words, and
    therefore that the byte order is DGN-independent -- the property that makes a novel DGN
    transmittable at all.
    """
    from fireflymcf import newbutton
    want = {
        52: "08FF0003FF00FFFF",
        119: "1AFF0003FF00FFFF",
        167: "28FF0003FF00FFFF",
    }
    bt = guidata.binding_table(stock)
    for idx, frame in want.items():
        assert newbutton.binding_payload(bt[idx].raw).hex().upper() == frame


def test_set_binding_dgn_is_two_bytes_and_keeps_the_payload(stock):
    from fireflymcf import newbutton
    bt = guidata.binding_table(stock)
    idx = next(b.index for b in bt if b.channel == 22)
    before = bt[idx].raw
    out, edits = newbutton.set_binding_dgn(stock, idx, 0x1FEA0, allow_capability_change=True)
    after = guidata.binding_table(out)[idx].raw
    assert len(out) == len(stock)
    assert struct.unpack_from("<I", after, 0)[0] & 0x3FFFF == 0x1FEA0
    assert newbutton.binding_payload(after) == newbutton.binding_payload(before)
    assert after[3:] == before[3:], "only bytes 0..2 may move"
    assert [e.structure for e in edits] == ["binding-dgn", "checksum"]


def test_set_binding_dgn_guards_the_lost_capability(stock):
    """Moving off DC_DIMMER_COMMAND_2 drops a channel capability -- v11 and v18 both shipped
    having lost a real control exactly this way, so it must be asked for."""
    from fireflymcf import newbutton
    idx = next(b.index for b in guidata.binding_table(stock) if b.channel == 22)
    with pytest.raises(ValueError, match="capability"):
        newbutton.set_binding_dgn(stock, idx, 0x1FEA0)


def test_set_binding_dgn_refuses_a_no_op_and_a_bad_dgn(stock):
    from fireflymcf import newbutton
    idx = next(b.index for b in guidata.binding_table(stock) if b.channel == 22)
    with pytest.raises(ValueError, match="already carries"):
        newbutton.set_binding_dgn(stock, idx, 0x1FEDB, allow_capability_change=True)
    with pytest.raises(ValueError, match="18-bit"):
        newbutton.set_binding_dgn(stock, idx, 0x40000, allow_capability_change=True)


def test_set_binding_dgn_output_passes_the_gates(stock):
    from fireflymcf import newbutton
    idx = next(b.index for b in guidata.binding_table(stock) if b.channel == 22)
    out, _ = newbutton.set_binding_dgn(stock, idx, 0x1FEA0, allow_capability_change=True)
    ok, why = relocate._boundary_oracle(out)
    assert ok, why
    assert coverage.analyse(out, deep=True).unclaimed == []
    resealed, changed = patch.reseal(out)
    assert resealed == out and changed == []
