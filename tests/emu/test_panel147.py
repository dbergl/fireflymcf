"""The 14.7 panel's state-of-charge relay to the G12 (cell 0xDA)."""
import os

import pytest

from fireflymcf.emu import rvc
from fireflymcf.emu.canbus import Peer
from fireflymcf.emu.machine import Machine

# The research corpus, linked into the repo root by scripts/link-research.sh; without it
# tests/conftest.py does not collect this module.
CORPUS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
MCF147 = os.path.join(CORPUS, "firefly-14.7 (Terrain)", "mcf.bin")


class Battery(Peer):
    """The coach's WS500 (0x80) as a SOC source: 1FFFC every 0.5 s, DM_RV every 5 s."""

    def __init__(self, soc_x2, status=0x05, instance=1, soc_until=None):
        self.soc_x2, self.status, self.instance, self.soc_until = soc_x2, status, instance, soc_until

    def attach(self, bus):
        super().attach(bus)
        self.every(0.5, self._soc)
        self.every(5.0, lambda: (rvc.DM_RV, [self.status, 0x4C, 0xFF, 0xFF, 0xFF, 0xFF, 0x00, 0xFF]),
                   phase=1.0)

    def _soc(self):
        if self.soc_until is not None and self.bus.m.cycles > self.soc_until * self.bus.m.cpu_hz:
            return None
        return 0x1FFFC, [self.instance, 120, 0xA0, 0x24, self.soc_x2, 0xFF, 0xFF, 0xFF]


def da_writes(battery, seconds):
    m = Machine()
    m.load_mcf(MCF147)
    m.boot()
    m.canbus.add_peer(battery)
    seen = []

    class Spy(Peer):
        def on_frame(self, f):
            _, dgn, sa = rvc.split_id(f.can_id)[:3]
            if sa == 0x9F and dgn == 0x1FED9 and f.data[2] == 0xDA:
                seen.append((round(m.cycles / m.cpu_hz, 1), int.from_bytes(f.data[4:6], "little")))

    m.canbus.add_peer(Spy())
    m.run(int(seconds * m.cpu_hz))
    return seen


def test_soc_is_relayed_once_its_source_sends_dm_rv():
    seen = da_writes(Battery(0xAE), 8)
    assert [v for _, v in seen] == [0xAE, 0xAE, 0xAE]   # every 3 s from 1.8 s
    assert [v for _, v in da_writes(Battery(0xAE, soc_until=0), 5)] == [0xFFFF, 0xFFFF]


@pytest.mark.parametrize("status,instance", [(0x00, 1), (0x05, 5)])
def test_no_soc_without_a_working_status_or_outside_instances_1_to_4(status, instance):
    assert {v for _, v in da_writes(Battery(0xAE, status, instance), 8)} == {0xFFFF}


def test_soc_source_going_quiet_reads_as_zero_percent():
    # The slot is freed ~30 s after the last 1FFFC but the DM_RV state lingers,
    # so the average divides by zero records: 0, not "unknown".
    seen = da_writes(Battery(0xAE, soc_until=8), 46)
    assert seen[-1][1] == 0 and 0xAE in [v for _, v in seen]
