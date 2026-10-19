# tests/test_emu.py
"""tests/emu.py runs authored routines on Unicorn. Its known answer is bitcombine, whose fold is
coach-verified on v32 (the Batt Heat status); the guards make stray memory access and runaway
loops fail rather than pass silently."""
import pytest

from tests import emu
from fireflymcf import bitcombine, readout, thumb

BASE = 0x259F60                       # 4-aligned, GuiCode range
S0, S1, CELL = 0x20008254, 0x20008256, 0x2003A000
TAIL = BASE + 0x400


def _plan():
    cell = readout.DisplayCell(700, CELL, 2, 3, True)
    return bitcombine.CombinePlan(
        sources=(bitcombine.BitSource(S0, 1, 1), bitcombine.BitSource(S1, 1, 2)),
        cell=cell, hook_at=0xEE3C, hook_old=b"\0" * 4, tail_target=TAIL)


def _u16(mem, a):
    return mem[a] | mem[a + 1] << 8


@pytest.mark.parametrize("h,c,want", [(1, 0, 1), (0, 1, 2), (1, 1, 3), (0xFFFF, 1, 2), (0, 0, 0), (3, 2, 1)])
def test_bitcombine_fold_matches_coach_behaviour(h, c, want):
    code, _ = bitcombine.build(_plan(), BASE)
    mem = {S0: h & 0xFF, S0 + 1: h >> 8, S1: c & 0xFF, S1 + 1: c >> 8, CELL: 0x5A, CELL + 1: 0xA5}
    out = emu.run(code, BASE, mem, stop_at=TAIL)
    assert _u16(out, CELL) == want


def test_unprovided_address_faults():
    code, _ = bitcombine.build(_plan(), BASE)
    with pytest.raises(AssertionError, match="unmapped"):
        emu.run(code, BASE, {S0: 1, S0 + 1: 0}, stop_at=TAIL)      # S1 and CELL not provided


def test_same_page_neighbour_faults():
    """S0 and S1 share the 4 KB page 0x20008000, so mapping S0's page also maps S1's bytes at
    the Unicorn page-table level. The guarantee this harness gives is byte-level, not
    page-level: reading S1's low byte when only its high byte was provided must still fault,
    naming the exact missing byte (0x20008256), not silently return 0 (the PC.44 class of bug:
    an adjacent, wrong slot reads as a plausible value instead of faulting)."""
    code, _ = bitcombine.build(_plan(), BASE)
    mem = {S0: 1, S0 + 1: 0, S1 + 1: 0, CELL: 0x5A, CELL + 1: 0xA5}      # S1 low byte omitted
    with pytest.raises(AssertionError, match="unmapped") as exc:
        emu.run(code, BASE, mem, stop_at=TAIL)
    assert "20008256" in str(exc.value)


def test_same_page_neighbour_partial_faults():
    """The other half of the same-page guarantee: S1's low byte IS provided but its high byte
    is not, so the 2-byte read at S1 must fault on the missing high byte specifically
    (0x20008257), not on S1's base address."""
    code, _ = bitcombine.build(_plan(), BASE)
    mem = {S0: 1, S0 + 1: 0, S1: 1, CELL: 0x5A, CELL + 1: 0xA5}          # S1 high byte omitted
    with pytest.raises(AssertionError, match="unmapped") as exc:
        emu.run(code, BASE, mem, stop_at=TAIL)
    assert "20008257" in str(exc.value)


def test_runaway_loop_is_caught():
    loop = thumb.b_n(BASE, BASE)                                    # b . (branch to self)
    with pytest.raises(AssertionError, match="stop_at"):
        emu.run(loop + b"\0\0", BASE, {}, stop_at=TAIL, max_insns=50)
