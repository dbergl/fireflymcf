"""`battdur`'s pure rules: the step code each byte of cell `0x72` holds, its clamp and default, and
the start frame a hold sends."""
import pytest

from fireflymcf.battdur import (CODE_BASE, DEFAULTS, canonical_word, clamp_code, decode_byte,
                                decode_word, encode, pack, start_wire)

HEATER = bytes.fromhex("aa5500000000")
ALARMS = bytes.fromhex("4d5300000000")


def test_start_wire_matches_todays_bindings():
    assert start_wire(HEATER, 60) == bytes.fromhex("aa55000000008ca0")
    assert start_wire(ALARMS, 10) == bytes.fromhex("4d53000000001770")


def test_start_wire_every_grid_value():
    for m in range(5, 61, 5):
        assert start_wire(b"\0" * 6, m)[6:] == (m * 600).to_bytes(2, "big")


def test_start_wire_refuses_off_grid():
    for bad in (0, 3, 65, 7):
        with pytest.raises(ValueError):
            start_wire(HEATER, bad)


def test_encode_grid():
    assert CODE_BASE == 0x10
    assert [encode(m) for m in range(5, 61, 5)] == list(range(0x11, 0x1D))


def test_decode_byte_table():
    cases = {0x10: 5, 0x1D: 60, 0x00: 60, 0xFF: 60, 0x0F: 60, 0x1E: 60, 0x11: 5, 0x1C: 60,
             0x17: 35}
    for code, want in cases.items():
        assert decode_byte(code, 60) == want, hex(code)
    assert decode_byte(0x00, 10) == 10 and decode_byte(0x10, 10) == 5


def test_clamp_code_is_encode_of_decode():
    for code in range(256):
        assert clamp_code(code, 10) == encode(decode_byte(code, 10))


def test_defaults_and_packing():
    assert DEFAULTS == {"heater": 60, "alarms": 10}
    assert pack(60, 10) == 0x121C
    assert decode_word(0x121C) == (60, 10)
    assert decode_word(0x1711) == (5, 35)


@pytest.mark.parametrize("word", [0xFFFF, 0x0000, 0xFEFF, 0x00FF, 0xFFFE])
def test_wraps_from_unset_give_defaults(word):
    assert decode_word(word) == (60, 10)


def test_canonical_word():
    assert canonical_word(0x121C) is None                 # already canonical
    assert canonical_word(0xFFFF) is None                 # unset or not yet heard: never overwrite
    assert canonical_word(0x121D) == 0x121C               # heater + past 60
    assert canonical_word(0x1011) == 0x1111               # alarms - past 5
    assert canonical_word(0x0000) == 0x121C               # a wrap from unset: both defaults
    assert canonical_word(0x1210) == 0x1211               # heater - past 5


def test_steps_never_borrow_across_bytes():
    """Every legal byte is >= 0x10, so a -1 on the low byte or -0x100 on the high byte of a
    canonical word never reaches the other byte."""
    for h in range(5, 61, 5):
        for a in range(5, 61, 5):
            w = pack(h, a)
            assert (w - 1) >> 8 == w >> 8
            assert (w + 1) >> 8 == w >> 8
            assert (w - 0x100) & 0xFF == w & 0xFF
            assert (w + 0x100) & 0xFF == w & 0xFF
