"""The USB port's inbound reboot command, pinned to the corpus LCD image.

`docs/LCD-FIRMWARE.md` Finding 14: the panel's micro-USB port parses host commands, and command
`0x84`/`0x0101` calls `FUN_0021E78C`, which reboots the panel with a Cortex-M `SYSRESETREQ`. This
was read from code and has never been exercised, so the guard is a transcription check on the two
literals the routine loads: the LCD section in `firefly-13.6/mcf.bin` is byte-identical to the
`extractions/13.6` image the disassembly was read from, so the constants can be pinned here.
"""
from __future__ import annotations

import struct

import pytest

from fireflymcf import container

MCF = "firefly-13.6/mcf.bin"
LCD_BASE = 0x210000                 # the raw loader base the disassembly uses

SCB_AIRCR = 0xE000ED0C              # AIRCR = SCB + 0x0C; the routine loads SCB (0xE000ED00) + 0xC
SYSRESETREQ = 0x05FA0004           # VECTKEY 0x05FA in the top half, SYSRESETREQ (bit 2) below
RESET_SCB_LITERAL = 0x0021E7A8     # FUN_0021E78C's first pool word
RESET_VAL_LITERAL = 0x0021E7AC     # its second


@pytest.fixture(scope="module")
def lcd() -> bytes:
    with open(MCF, "rb") as handle:
        data = handle.read()
    section = next(s for s in container.parse_container(data).sections if "LCD" in s.name)
    return data[section.offset:section.offset + section.length]


def _word(body: bytes, addr: int) -> int:
    return struct.unpack_from("<I", body, addr - LCD_BASE)[0]


def test_the_reset_routine_targets_the_cortex_m_reset_register(lcd: bytes) -> None:
    assert _word(lcd, RESET_SCB_LITERAL) + 0xC == SCB_AIRCR
    assert _word(lcd, RESET_VAL_LITERAL) == SYSRESETREQ


def test_the_value_carries_the_vectkey_and_only_the_reset_request(lcd: bytes) -> None:
    value = _word(lcd, RESET_VAL_LITERAL)
    assert value >> 16 == 0x05FA                 # VECTKEY, or the write is ignored
    assert value & 0x7 == 0x4                     # SYSRESETREQ, not VECTRESET/VECTCLRACTIVE
