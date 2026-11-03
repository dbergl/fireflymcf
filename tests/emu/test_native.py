"""Native block-hook slicer: exact cost accounting and budget stops."""
import pytest
from unicorn import UC_ARCH_ARM, UC_MODE_MCLASS, UC_MODE_THUMB, Uc
from unicorn.arm_const import UC_ARM_REG_R1, UC_CPU_ARM_CORTEX_M7

from fireflymcf.emu import native


def engine():
    uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    uc.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M7)
    uc.mem_map(0, 0x1000)
    s = native.slicer(uc)
    if s is None:
        pytest.skip("no C compiler for the native slicer")
    return uc, s


def test_charges_halfwords_of_executed_blocks():
    uc, s = engine()
    # mov.w r0, #100 (32-bit) ; loop: adds r1, #1 ; subs r0, #1 ; bne loop ; b .
    uc.mem_write(0, bytes.fromhex("4ff06400" "0131" "0138" "fcd1" "fee7"))
    s.arm(10**9)
    uc.emu_start(1, 10)
    assert uc.reg_read(UC_ARM_REG_R1) == 100
    assert s.used == 2 + 100 * 3


def test_budget_stops_emulation():
    uc, s = engine()
    uc.mem_write(0, bytes.fromhex("0131" "fde7"))  # loop: adds r1, #1 ; b loop
    s.arm(1000)
    uc.emu_start(1, 0xFFFFFFFF)
    assert 1000 <= s.used < 1004  # the block crossing the budget runs to its end
    assert uc.reg_read(UC_ARM_REG_R1) == s.used // 2
