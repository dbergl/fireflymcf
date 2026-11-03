"""The panel's FAULTS list (SETTINGS > Network Diagnostics).

13.6 has three entries (object 525): G12 offline (variable 235), the Front
thermistor (236) and the wireless wall switch's battery (237)."""
import os

import pytest

from fireflymcf.emu.machine import Machine
from fireflymcf.emu.peers import demo_network

# The research corpus, linked into the repo root by scripts/link-research.sh; without it
# tests/conftest.py does not collect this module.
CORPUS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
MCF = os.path.join(CORPUS, "firefly-13.6", "mcf.bin")
FAULTS = (slice(70, 420), slice(315, 795))     # the list box, empty when there are none


SWITCH_DEADLINE = 0x200398B4   # 13.6: boot + 600 000 ms, then variable 237 is evaluated


def faults_shown(ambient=None, remote_mv=2800, skip_switch_wait=False):
    m = Machine()
    m.load_mcf(MCF)
    m.boot()
    m.rfremote.battery_mv = remote_mv
    demo_network(m.canbus, m.guidata, ambient=ambient)
    hz = m.cpu_hz
    m.run(3 * hz)
    for x, y in ((400, 425), (30, 405), (337, 92)):     # I ACCEPT, SETTINGS, Network Diagnostics
        m.touch.touch([(x, y)])
        m.run(hz // 6)
        m.touch.touch([])
        m.run(int(1.5 * hz))
    m.run(2 * hz)
    if skip_switch_wait:
        m.run(5 * hz)                                   # the LCD's first report at ~10.7 s
        now_ms = m.cycles // (hz // 1000)
        m.uc.mem_write(SWITCH_DEADLINE, (now_ms + 100).to_bytes(4, "little"))
        m.run(3 * hz)
    return (m.periph["LTDC"].render()[FAULTS].astype(int).sum(2) > 600).any()


@pytest.mark.parametrize("ambient, fault", [(21.0, False), (-50.0, False), (81.0, False),
                                            (-60.0, True), (85.0, True)])
def test_front_thermistor_fault_is_the_ambient_out_of_range(ambient, fault):
    # Data point 9 (1FF9C instance 0) at or below 0x1BC0 (222 K) or at or above
    # 0x2C60 (355 K), or stale for 30 s, lists "The Front thermistor has an
    # invalid reading." (bisected in the emulator).
    assert faults_shown(ambient) == fault


@pytest.mark.parametrize("remote_mv, fault", [(2800, False), (2000, False), (None, True), (3600, True)])
def test_wall_switch_fault_is_its_battery_report_missing_or_out_of_range(remote_mv, fault):
    # The LCD reports the remote's battery on 1FFFD 0x81 and loops it into data point
    # 16; from 10 minutes after boot class 0 -- stale, 0 or >= 3.6 V -- lists "<switch>
    # has a dead battery or is disconnected."  2.0 V is "low" (class 2), not listed.
    assert faults_shown(remote_mv=remote_mv, skip_switch_wait=True) == fault
