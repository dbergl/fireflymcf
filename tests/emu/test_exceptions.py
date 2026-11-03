"""Exception entry/return round trips on the bare core (no firmware)."""
import struct

import pytest
from unicorn.arm_const import (
    UC_ARM_REG_CONTROL, UC_ARM_REG_IPSR, UC_ARM_REG_MSP, UC_ARM_REG_PC, UC_ARM_REG_PSP,
    UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R5, UC_ARM_REG_R6,
    UC_ARM_REG_S0, UC_ARM_REG_SP,
)

from fireflymcf.emu.cortexm import PENDSV, SVCALL, SYSTICK
from fireflymcf.emu.machine import SRAM, Machine

VT = SRAM            # vector table
MAIN = SRAM + 0x400
HANDLER = SRAM + 0x800
MSP_TOP = SRAM + 0x8000
PSP_TOP = SRAM + 0x6000


def machine(main, handler, vectors=(SYSTICK, PENDSV, SVCALL)):
    m = Machine()
    vt = [MSP_TOP, MAIN | 1] + [0] * 14
    for v in vectors:
        vt[v] = HANDLER | 1
    m.uc.mem_write(VT, struct.pack("<16I", *vt))
    m.uc.mem_write(MAIN, bytes.fromhex(main))
    m.uc.mem_write(HANDLER, bytes.fromhex(handler))
    m.boot(VT)
    return m


LOOP = "fee7"  # b .


@pytest.mark.parametrize("use_psp", [False, True])
def test_interrupt_round_trip(use_psp):
    # main: movs r0,#1 ; movs r1,#2 ; b .     handler: movs r0,#7 ; adds r5,#1 ; bx lr
    m = machine("0120 0221" + LOOP, "0720 0135 7047")
    uc = m.uc
    if use_psp:
        uc.reg_write(UC_ARM_REG_PSP, PSP_TOP)
        uc.reg_write(UC_ARM_REG_CONTROL, 2)
    m.run(10)
    assert uc.reg_read(UC_ARM_REG_PC) == MAIN + 4
    m.core.set_pending(PENDSV)
    m.run(10)
    assert uc.reg_read(UC_ARM_REG_R5) == 1           # handler ran once
    assert uc.reg_read(UC_ARM_REG_R0) == 1           # r0 restored from frame
    assert uc.reg_read(UC_ARM_REG_R1) == 2
    assert uc.reg_read(UC_ARM_REG_IPSR) == 0
    assert m.core.active == []
    assert uc.reg_read(UC_ARM_REG_PC) == MAIN + 4
    assert uc.reg_read(UC_ARM_REG_CONTROL) & 2 == (2 if use_psp else 0)
    assert uc.reg_read(UC_ARM_REG_MSP) == MSP_TOP
    if use_psp:
        assert uc.reg_read(UC_ARM_REG_PSP) == PSP_TOP
        assert uc.reg_read(UC_ARM_REG_SP) == PSP_TOP


def test_svc_returns_after_instruction():
    # main: svc 0 ; movs r1,#9 ; b .     handler: adds r5,#1 ; bx lr
    m = machine("00df 0921" + LOOP, "0135 7047")
    m.run(200)
    assert m.uc.reg_read(UC_ARM_REG_R5) == 1
    assert m.uc.reg_read(UC_ARM_REG_R1) == 9
    assert m.core.active == []


def test_pop_pc_exception_return():
    # handler: push {r4,lr} ; adds r5,#1 ; pop {r4,pc}
    m = machine(LOOP, "10b5 0135 10bd")
    m.run(5)
    m.core.set_pending(PENDSV)
    m.run(20)
    assert m.uc.reg_read(UC_ARM_REG_R5) == 1
    assert m.core.active == []
    assert m.uc.reg_read(UC_ARM_REG_PC) == MAIN


def test_fp_context_frame():
    # main: vmov s0, r1 ; b .   (sets CONTROL.FPCA)   handler: vmov s0, r5 ; bx lr
    m = machine("00ee101a" + LOOP, "00ee105a 7047")
    uc = m.uc
    uc.reg_write(UC_ARM_REG_R1, 0x3F800000)
    m.run(5)
    m.core.set_pending(PENDSV)
    m.run(20)
    assert m.core.active == []
    assert uc.reg_read(UC_ARM_REG_MSP) == MSP_TOP
    assert uc.reg_read(UC_ARM_REG_S0) == 0x3F800000  # restored from extended frame


def test_mmio_stop_does_not_replay_store():
    # main: str r2,[r3] (ICSR PENDSVSET) twice from the same PC via a loop.
    #   loop: str r2,[r3] ; adds r5,#1 ; cmp r5,#3 ; bne loop ; b .
    m = machine("1a60 0135 032d fbd1" + LOOP, "0136 7047", vectors=(PENDSV,))
    uc = m.uc
    uc.reg_write(UC_ARM_REG_R3, 0xE000ED04)
    uc.reg_write(UC_ARM_REG_R2, 1 << 28)
    m.run(2000)
    assert uc.reg_read(UC_ARM_REG_R5) == 3            # each store executed exactly once
    assert uc.reg_read(UC_ARM_REG_R6) == 3            # and PendSV ran once per yield


def test_pending_taken_when_basepri_lowered():
    # movs r0,#0x50 ; msr basepri,r0 ; str r2,[r3] (pend PendSV, masked) ; movs r0,#0 ;
    # msr basepri,r0 ; movs r1,r5 (must see PendSV already run) ; b .
    m = machine("5020 80f31188 1a60 0020 80f31188 2946" + LOOP, "0135 7047", vectors=(PENDSV,))
    m.core.prio[PENDSV] = 0xF0
    uc = m.uc
    uc.reg_write(UC_ARM_REG_R3, 0xE000ED04)
    uc.reg_write(UC_ARM_REG_R2, 1 << 28)
    m.install_unmask_patches([(MAIN, 0x20)])
    m.run(2000)
    assert uc.reg_read(UC_ARM_REG_R5) == 1
    assert uc.reg_read(UC_ARM_REG_R1) == 1            # handler ran before the next instruction


def test_pending_taken_after_cpsie():
    # cpsid i ; str r2,[r3] ; cpsie i ; movs r1,r5 ; b .
    m = machine("72b6 1a60 62b6 2946" + LOOP, "0135 7047", vectors=(PENDSV,))
    uc = m.uc
    uc.reg_write(UC_ARM_REG_R3, 0xE000ED04)
    uc.reg_write(UC_ARM_REG_R2, 1 << 28)
    m.install_unmask_patches([(MAIN, 0x20)])
    m.run(2000)
    assert uc.reg_read(UC_ARM_REG_R1) == 1
