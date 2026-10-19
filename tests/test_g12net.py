"""The enumeration client, driven by a fake bus replaying PC.63.

No test here opens a socket. `CanBus` is a Protocol precisely so the client can be exercised
against recorded frames, and `SocketCanBus` -- the only part that touches the kernel -- is
covered by construction alone.
"""

from pathlib import Path

import pytest

from fireflymcf import g12net, upload

ROOT = Path(__file__).resolve().parents[1]
CAPTURE = ROOT / "captures" / "pc63-g12-network-upload-2026-09-08.log"

#: The four upload targets the G12 advertises, as `(DeviceInst, UploadType)`.
TARGETS = [(0, 1), (0, 2), (0, 0xFF), (1, 1)]


class FakeBus:
    """Replays frames and advances an injected clock, so no test ever sleeps."""

    def __init__(self, frames, step=0.001):
        self.frames = list(frames)
        self.step = step
        self.sent = []
        self.clock = 0.0

    def now(self):
        return self.clock

    def send(self, can_id, data):
        self.sent.append((can_id, data))

    def recv(self, timeout):
        self.clock += self.step
        return self.frames.pop(0) if self.frames else None


@pytest.fixture(scope="module")
def frames():
    return upload.parse_frames(CAPTURE.read_text())


def enumerate_over(frames, **kw):
    bus = FakeBus(frames)
    return bus, g12net.enumerate_devices(bus, listen=60.0, now=bus.now, **kw)


def test_a_passive_listen_transmits_nothing_and_still_finds_every_target(frames):
    """Rung 1 of the probe ladder. This is the whole safety argument for it."""
    bus, found = enumerate_over(frames)
    assert bus.sent == []
    assert found.transmitted is False
    assert sorted(found.keys(), key=TARGETS.index) == TARGETS


def test_a_directed_request_sends_exactly_one_frame(frames):
    bus, _ = enumerate_over(frames, send_request=True, target=0x9C)
    assert bus.sent == [upload.request_frame(target=0x9C, source=upload.PROBE_SA)]


def test_the_request_goes_out_from_the_probe_address_not_the_loaders(frames):
    bus, _ = enumerate_over(frames, send_request=True, target=0x9C)
    (can_id, _), = bus.sent
    assert can_id & 0xFF == upload.PROBE_SA == 0xC0
    assert can_id & 0xFF != upload.LOADER_SA


def test_a_broadcast_request_is_sent_when_no_target_is_named(frames):
    bus, _ = enumerate_over(frames, send_request=True)
    assert bus.sent == [upload.request_frame(source=upload.PROBE_SA)]


def test_every_target_reports_the_parameters_it_declared(frames):
    _, found = enumerate_over(frames)
    for key in TARGETS:
        device = found[key]
        assert len(device.parameters) == device.status.parameter_count
        assert device.complete
    assert sum(d.status.parameter_count for d in found.devices) == 29


def test_the_config_target_names_the_g12_section_this_project_patches(frames):
    """UploadType 2 is the config region an MCF's `G12` section is written to."""
    _, found = enumerate_over(frames)
    config = found[(0, 2)]
    assert config.value("LdAddr") == 0x08038000
    assert config.value("ConfigType") == 0x1234
    assert config.value("ConfigRev") == 6
    assert config.source == 0x9C


def test_the_application_target_is_a_different_region_we_never_write(frames):
    _, found = enumerate_over(frames)
    assert found[(0, 1)].value("LdAddr") == 0x08006000


def test_a_target_that_sent_fewer_parameters_than_it_declared_is_reported_incomplete(frames):
    """Honest reporting: a short dump is said to be short, never silently accepted."""
    short = [
        f
        for f in frames
        if not (
            upload.dgn(f.can_id) == upload.PARAMETER_DGN
            and f.data[:2] == bytes([0, 2])
            and f.data[2] == 0x40
        )
    ]
    _, found = enumerate_over(short)
    config = found[(0, 2)]
    assert config.status.parameter_count == 7
    assert len(config.parameters) == 6
    assert not config.complete
    assert config.value("LdAddr") is None


def test_the_source_addresses_heard_are_reported_so_a_probe_can_check_its_own_is_free(frames):
    _, found = enumerate_over(frames)
    assert 0x9C in found.heard and 0xF9 in found.heard
    assert upload.PROBE_SA not in found.heard


def test_listening_stops_at_the_deadline_rather_than_at_the_first_quiet_moment():
    """A real socket returns None on timeout; that must not end the listen early."""
    late = upload.Frame(0.0, 0x18FF019C, bytes.fromhex("00011f010300640a"))
    bus = FakeBus([None, None, late], step=0.001)
    bus.frames = [None, None, late]
    found = g12net.enumerate_devices(bus, listen=1.0, now=bus.now)
    assert sorted(found.keys()) == [(0, 1)]


def test_a_zero_length_listen_returns_nothing_and_touches_nothing(frames):
    bus = FakeBus(frames)
    found = g12net.enumerate_devices(bus, listen=0.0, now=bus.now)
    assert found.devices == [] and bus.sent == []
