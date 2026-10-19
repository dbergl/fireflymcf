"""Binding-frame constructor / predictor -- the battery-heater button's Phase-1 gate,
codified.

`newbutton.binding_raw` composes the 12 stored bytes of a binding that transmits an
arbitrary 18-bit DGN with an arbitrary 8-byte wire payload on chosen gesture phases;
`newbutton.binding_wire_frame` reconstructs the exact 29-bit CAN id + 8 data bytes the
firmware puts on the wire from those stored bytes, the way `FUN_0021B8B4`/`FUN_0021825C`
assemble them (priority hard-wired to 6, 18-bit DGN bit-field-inserted at id bit 8 so a
PDU1 DGN's low byte lands in the destination/PS position, source address stamped 0x9F, the
8 wire bytes un-reversed from the two stored little-endian u32 words).

The ground-truth oracle is a real DC_DIMMER binding whose stored bytes reproduce a frame
captured on the coach; the target assertions are the battery-heater enable/off frames.
"""
from fireflymcf import newbutton


# --- The hardware oracle: stored bytes -> wire frame, byte for byte. ---
# Stock 13.6 DC_DIMMER_COMMAND_2 binding at mcf.bin offset 0x29BC8, observed on the coach
# (captures/candump-2026-08-12_121941.log) as 19FEDB9F#16FFFB00FF00FFFF.
_ORACLE_RAW = bytes.fromhex("dbfe0102 00fbff16 ffff00ff".replace(" ", ""))
_ORACLE_ID = 0x19FEDB9F
_ORACLE_DATA = bytes.fromhex("16fffb00ff00ffff")

# --- The battery-heater target (proprietary-A, DP=0, destination 0x70). ---
DGN_BATTERY_HEATER = 0x0EF70          # 18-bit; low word 0xEF70, high bits 00 -> DP=0
ENABLE_WIRE = bytes.fromhex("aa55000000008ca0")   # 0x8CA0 = 36000 x 100ms = 60 min
OFF_WIRE = bytes.fromhex("aa5500000000 0000".replace(" ", ""))
MASK_HELD = 0x04                      # press-and-hold -> enable
MASK_TAP = 0x02                       # short-tap release -> off
# The panel stamps its own SA; the BMS is destination-filtered, so this is accepted.
ENABLE_ID = 0x18EF709F
OFF_ID = 0x18EF709F


def test_predictor_reproduces_the_hardware_oracle():
    """The reassembly must reproduce a frame actually seen on the wire -- not fitted."""
    can_id, data = newbutton.binding_wire_frame(_ORACLE_RAW)
    assert can_id == _ORACLE_ID
    assert data == _ORACLE_DATA


def test_enable_binding_emits_the_exact_enable_frame():
    raw = newbutton.binding_raw(DGN_BATTERY_HEATER, ENABLE_WIRE, gesture_mask=MASK_HELD)
    can_id, data = newbutton.binding_wire_frame(raw)
    assert can_id == ENABLE_ID
    assert data == ENABLE_WIRE
    assert raw[3] == MASK_HELD                    # fires only while held
    assert raw[2] & 0x04 == 0                     # repeat flag clear -> fires once


def test_off_binding_emits_the_exact_off_frame():
    raw = newbutton.binding_raw(DGN_BATTERY_HEATER, OFF_WIRE, gesture_mask=MASK_TAP)
    can_id, data = newbutton.binding_wire_frame(raw)
    assert can_id == OFF_ID
    assert data == OFF_WIRE
    assert raw[3] == MASK_TAP                      # fires only on a short-tap release


def test_binding_payload_is_the_inverse_of_binding_raw():
    for wire, mask in ((ENABLE_WIRE, MASK_HELD), (OFF_WIRE, MASK_TAP)):
        raw = newbutton.binding_raw(DGN_BATTERY_HEATER, wire, gesture_mask=mask)
        assert newbutton.binding_payload(raw) == wire
        # DGN round-trips out of the stored bytes
        dgn = raw[0] | (raw[1] << 8) | ((raw[2] & 0x03) << 16)
        assert dgn == DGN_BATTERY_HEATER


def test_stored_bytes_match_the_documented_reversal():
    """Guard the exact stored form so a future reversal change is caught here, not on the bus."""
    raw = newbutton.binding_raw(DGN_BATTERY_HEATER, ENABLE_WIRE, gesture_mask=MASK_HELD)
    #        DGN   b2  msk  <---- 8 data bytes, 3,2,1,0,7,6,5,4 ---->
    assert raw == bytes.fromhex("70ef 00 04 0000 55aa a08c 0000".replace(" ", ""))


def test_priority_is_always_six_and_sa_is_the_panel():
    """The two firmware-forced fields, made explicit."""
    raw = newbutton.binding_raw(DGN_BATTERY_HEATER, ENABLE_WIRE, gesture_mask=MASK_HELD)
    can_id, _ = newbutton.binding_wire_frame(raw)
    assert (can_id >> 26) & 0x7 == 6              # priority
    assert can_id & 0xFF == 0x9F                  # source address
    assert (can_id >> 8) & 0xFF == 0x70           # PDU1 destination lands in PS
    assert (can_id >> 16) & 0xFF == 0xEF          # PF


def test_binding_raw_rejects_bad_inputs():
    import pytest
    with pytest.raises(ValueError):
        newbutton.binding_raw(0x40000, ENABLE_WIRE, gesture_mask=MASK_HELD)   # >18 bits
    with pytest.raises(ValueError):
        newbutton.binding_raw(DGN_BATTERY_HEATER, b"\x00" * 7, gesture_mask=MASK_HELD)
