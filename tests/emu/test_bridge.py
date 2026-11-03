"""The two-way SSH link's transmit gate (no SSH: the remote end is faked)."""
import io

from fireflymcf.emu.machine import Machine
from fireflymcf.emu.periph.bxcan import Frame


class FakeRemote:
    def __init__(self):
        self.stdin = io.StringIO()


def bus(mode, allow_config=False, foreign=0):
    b = Machine().canbus
    b.remote, b.tx_mode, b.allow_config, b.tx_log = FakeRemote(), mode, allow_config, None
    b.tx_counts, b.foreign_panel = {"sent": 0, "held": 0}, foreign
    return b


FRAMES = [Frame(0x18EEFF9F, bytes.fromhex("0000a024a0650080"), True),   # address claim
          Frame(0x19FEDB9F, bytes.fromhex("16ffc805ff00ffff"), True),   # a light toggle
          Frame(0x19FED99F, bytes.fromhex("ff969b0f0000d1ff"), True),   # G12 write 9B := 0
          Frame(0x19EE009F, bytes.fromhex("0d0000ffffffffff"), True)]   # option 0D := 0


def sent(b):
    for f in FRAMES:
        b._remote_tx(f)
    return [line.split("#")[0] for line in b.remote.stdin.getvalue().split()]


def test_dry_run_sends_nothing():
    b = bus("dry")
    assert sent(b) == [] and b.tx_counts == {"sent": 0, "held": 4}


def test_live_holds_settings_writes_unless_allowed():
    assert sent(bus("live")) == ["18EEFF9F", "19FEDB9F"]
    assert sent(bus("live", allow_config=True)) == ["18EEFF9F", "19FEDB9F", "19FED99F", "19EE009F"]


def test_live_stops_when_a_real_panel_is_on_the_bus():
    assert sent(bus("live", foreign=1)) == []
