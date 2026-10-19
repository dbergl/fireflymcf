"""The LCD firmware's startup bounds, and what they say about the unreferenced slots."""
from pathlib import Path

import pytest

from fireflymcf import raminit, slotsource

ROOT = Path(__file__).resolve().parents[1]
BUILDS = {
    "13.6": ROOT / "firefly-13.6" / "mcf.bin",
    "11.23": ROOT / "firefly-11.23" / "mcf.bin",
    "14.7": ROOT / "firefly-14.7 (Terrain)" / "mcf.bin",
}


@pytest.fixture(scope="module")
def images():
    return {k: v.read_bytes() for k, v in BUILDS.items()}


def test_the_startup_bounds_are_derived_from_the_reset_handler(images):
    r = raminit.ram_init(images["13.6"])
    assert r.reset_handler == 0x002104D0
    assert r.stack_top == 0x20050000
    assert (r.data_start, r.data_end) == (0x20000000, 0x2000021C)
    assert (r.bss_start, r.bss_end) == (0x20000228, 0x2000AD98)
    assert r.data_source == 0x0021F39C


def test_every_build_carries_the_same_startup(images):
    bounds = {k: raminit.ram_init(v) for k, v in images.items()}
    assert len({(b.data_start, b.data_end, b.bss_start, b.bss_end) for b in bounds.values()}) == 1


def test_the_unmanaged_tail_is_276_kb(images):
    lo, hi = raminit.ram_init(images["13.6"]).unmanaged
    assert (lo, hi) == (0x2000AD98, 0x20050000)
    assert (hi - lo) // 1024 == 276


def test_the_absorb_float_gate_is_not_runtime_initialised(images):
    r = raminit.ram_init(images["13.6"])
    assert r.is_initialised(0x20039999) is False
    # the receive status-slot area IS inside .bss, which is why receive-fed values are
    # deterministic at boot while slot 23 is not
    assert r.is_initialised(0x20007FC8) is True
    assert r.is_initialised(0x20008000) is True


def test_the_bounds_discriminate_and_are_not_vacuous(images):
    """A predicate that answered True (or False) for everything would pass the tests above."""
    r = raminit.ram_init(images["13.6"])
    assert r.is_initialised(0x20000100) is True      # .data
    assert r.is_initialised(0x20005000) is True      # .bss
    assert r.is_initialised(0x2000021E) is False     # the alignment gap between them
    assert r.is_initialised(0x2000AD98) is False     # _ebss is exclusive
    assert r.is_initialised(0x2003A38C) is False     # GuiCode RAM


def test_slot_23_is_never_established(images):
    never = {s.slot for s in slotsource.never_established(images["13.6"])}
    assert 23 in never
    assert len(never) == 210
    # and it is a strict subset test, not "everything": written slots stay out of it
    written = {s.slot for s in slotsource.slot_sources(images["13.6"]) if s.kind == "written"}
    assert written and not (written & never)


def test_a_build_with_an_unrecognised_startup_is_refused(images):
    """The deriver must refuse rather than report bounds it has not identified."""
    broken = bytearray(images["13.6"])
    from fireflymcf import container

    lcd = next(s for s in container.parse_container(bytes(broken)).sections
               if "LCD Firmware" in (s.name or ""))
    # point the reset vector outside the section
    broken[lcd.offset + 4:lcd.offset + 8] = (0x00990001).to_bytes(4, "little")
    with pytest.raises(ValueError):
        raminit.ram_init(bytes(broken))
