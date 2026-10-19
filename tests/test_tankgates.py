"""The tank-heater name gates (GUICODE Finding 35), emulated from the real instructions.

The emulator exists because reading the gate logic by eye is not trustworthy -- three successive
hand-written models of this block produced confident, plausible, WRONG whitelists (an `ite`
predication bug, a dropped conditional-branch form, and IT-block state lost by disassembling one
instruction at a time, which made both arms of `ite ne / movne / moveq` execute).

What makes the current one trustworthy is that it reproduces two INDEPENDENT oracles on the stock
image, which is what these tests pin:
  * the coach: floorplan WD names rows 05 and 08 only (owner, 2026-08-18);
  * Finding 35's simulation: the best any floorplan achieves is 3/4, and none reaches 4/4.
"""
from __future__ import annotations

import pytest

from fireflymcf import options, tankgates

BUILD = "firefly-13.6/mcf.bin"


def _data() -> bytes:
    with open(BUILD, "rb") as fh:
        return fh.read()


def test_stock_reproduces_the_coach_wd_observation() -> None:
    """WD (floorplan 8) names Tank Heat Fresh (row 05) and Tank Heat Grey 2 (row 08), and leaves
    Grey (06) and Black (07) blank -- exactly what the owner sees. This is the hardware oracle;
    a model that disagrees with it is wrong however plausible its reading of the code."""
    assert tankgates.gates(_data(), 8) == {"05 Fresh", "08 Grey 2"}


def test_stock_reproduces_finding_35s_negative() -> None:
    """The defect itself: no floorplan populates all four tank-heater rows, and the best any
    reaches is 3/4 (WT). Non-vacuous -- an emulator that over-fires (the `ite` bug) reported 4/4
    for WD, and one that under-fires reported 1/4 everywhere."""
    data = _data()
    per = {code: tankgates.gates(data, value)
           for value, code in options.floorplan_options(data).items()}
    assert max(len(v) for v in per.values()) == 3, {k: sorted(v) for k, v in per.items()}
    assert not [k for k, v in per.items() if len(v) == 4]
    assert len(per["WT"]) == 3


def test_black_row_is_gated_on_the_black_tank_option() -> None:
    """Row 07 needs the Black Tank option as well as a whitelisted floorplan -- the
    `[0x2003a4b4] == 1` test at 0x249528. WT is whitelisted for Black, so toggling the option is
    what moves it."""
    data = _data()
    assert "07 Black" in tankgates.gates(data, 9, black_tank=True)
    assert "07 Black" not in tankgates.gates(data, 9, black_tank=False)
