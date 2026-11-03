"""Emulator test for user:tick-counter: the cell rises while the panel runs, and the panel
still boots to HOME and navigates.

`fireflymcf verify BUILD STOCK --emulate --project P --allow-user-ops` calls `scenario(s)` with a
`fireflymcf.emu.scenario.Scenario` on the built image, not yet booted. Anything that raises is
the failure, with its line. Write this file before op.py, and watch it fail on stock first.
"""
import struct

from fireflymcf import container

TAG = b"uCNT"          # the op's prefix tag (op.py): the cell's address follows it


def _cell(image: bytes) -> int:
    gc = next(s for s in container.parse_container(image).sections if "GuiCode" in s.name)
    body = image[gc.offset:gc.offset + gc.length]
    at = body.find(TAG)
    assert at >= 0, "no counter in this image (did the op run?)"
    assert body.find(TAG, at + 1) < 0, "two counter tags in GuiCode"
    return struct.unpack_from("<I", body, at + 4)[0]


def _u32(s, addr: int) -> int:
    return int.from_bytes(s.read_mem(addr, 4), "little")


def scenario(s):
    cell = _cell(s.image)
    assert 0x20000000 <= cell < 0x20080000, f"{cell:#x} is not in SRAM"
    s.boot()                                  # past the terms page, onto HOME
    assert s.page == "HOME", s.page
    a = _u32(s, cell)
    s.run(5.0)
    b = _u32(s, cell)
    s.run(5.0)
    c = _u32(s, cell)
    # The cell is garbage at power-on (the C runtime does not initialise it), so assert that it
    # rises, not what it starts at. u32 wrap within 10 s would take ~4e8 ticks: not a concern.
    assert a < b < c, (a, b, c)
    # The routine must not disturb the panel: it still answers a tap. (Do not assert "the panel
    # sent nothing": stock 13.6 itself sends a D1 config write on selector 0x9B at boot in the
    # emulator. Run a test on stock first and assert only what differs.)
    s.goto("SETTINGS")
    assert s.screen == 4, s.screen
