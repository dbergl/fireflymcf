"""The probe CLI. Nothing here opens a bus.

The two things worth holding the CLI to are that it declines before touching the interface when
the request makes no sense, and that its report names the fields a reader came for.
"""

from pathlib import Path

import pytest

from fireflymcf import g12flash
from fireflymcf import g12net, upload

ROOT = Path(__file__).resolve().parents[1]
CAPTURE = ROOT / "captures" / "pc63-g12-network-upload-2026-09-08.log"


class FakeBus:
    def __init__(self, frames, step=0.001):
        self.frames, self.step, self.sent, self.clock = list(frames), step, [], 0.0

    def now(self):
        return self.clock

    def send(self, can_id, data):
        self.sent.append((can_id, data))

    def recv(self, timeout):
        self.clock += self.step
        return self.frames.pop(0) if self.frames else None


@pytest.fixture(scope="module")
def found():
    frames = upload.parse_frames(CAPTURE.read_text())
    bus = FakeBus(frames)
    return g12net.enumerate_devices(bus, listen=60.0, now=bus.now)


def test_as_loader_without_request_is_refused_before_the_interface_is_touched(capsys):
    """The refusal must land before `SocketCanBus`, or it needs a bus to say a wrong thing."""
    assert g12flash.main(["probe", "--as-loader", "--interface", "definitely-not-a-can-iface"]) == 2
    assert "--as-loader" in capsys.readouterr().err


def test_an_interface_that_does_not_exist_is_refused_not_crashed(capsys):
    assert g12flash.main(["probe", "--interface", "definitely-not-a-can-iface"]) == 2
    assert "cannot open" in capsys.readouterr().err


def test_the_report_names_every_target_and_says_which_is_the_config(found):
    text = g12flash._describe(found)
    assert text.count("UploadType") == 4
    assert "config (an MCF's G12 section)" in text
    assert "application" in text


def test_the_report_carries_the_identifiers_a_reader_came_for(found):
    text = g12flash._describe(found)
    for expected in ("ConfigType", "0x1234", "ConfigID", "ConfigRev", "LdAddr", "0x8038000"):
        assert expected in text, expected


def test_a_passive_run_does_not_call_every_target_incomplete(found):
    """The first hardware run cried wolf on all four targets, and this is why.

    `0FF02` answers a request and nothing else, so a listen that transmitted nothing can never
    have parameters. Calling that "INCOMPLETE" fires on every passive run and trains the reader
    to ignore the one case where it means something.
    """
    assert found.transmitted is False
    text = g12flash._describe(found)
    assert "INCOMPLETE" not in text
    assert "not requested" in text


def test_an_incomplete_target_is_flagged_once_that_DEVICE_has_been_asked(found):
    """A short parameter dump must be visible in the output, not just in the object."""
    found.transmitted = True
    found.requested_target = 0x9C
    config = found[(0, 2)]
    dropped = config.parameters.pop(next(iter(config.parameters)))
    try:
        text = g12flash._describe(found)
        assert "INCOMPLETE: declared 7, heard 6" in text
    finally:
        found.transmitted = False
        found.requested_target = None
        config.parameters[dropped.param_id] = dropped


def test_a_device_we_did_not_ADDRESS_is_not_called_incomplete(found):
    """PC.67: the request went to 0x9F, and every 0x9C target was reported INCOMPLETE.

    Those frames were the G12 answering the panel's own poll. A device we never addressed has no
    reason to have sent parameters, so saying it came up short is an alarm about nothing.
    """
    found.transmitted = True
    found.requested_target = 0x9F           # the panel; every device here is 0x9C
    try:
        text = g12flash._describe(found)
        assert "INCOMPLETE" not in text
        assert text.count("not requested") == 4
    finally:
        found.transmitted = False
        found.requested_target = None


def test_the_report_shows_the_config_identity_the_device_broadcasts(found):
    """`1FED8` names which config the device holds, without transmitting anything."""
    text = g12flash._describe(found)
    assert "config identity" in text
    assert "manufacturer 293 (Firefly)" in text
    assert "ConfigType 0x1234" in text
