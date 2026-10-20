"""Talk to a G12 over RV-C -- read-only.

This module can **enumerate** a device: ask what upload targets it carries and what versions
they are, and nothing else. It cannot write. The transfer half of the protocol
(`MCF-FORMAT.md` §4.4, `BUS-BASELINE.md` PC.63) is deliberately not implemented here; see
its design note (not published) for what has to be
answered on hardware before it is.

**There is no read-back anywhere in this protocol**, so there is no way to fetch a device's
current config before changing it. Enumeration tells you *which* config the device holds --
`ConfigType`, `ConfigID`, `ConfigRev`, `LdAddr` -- never its bytes. The MCF you are running is
the only backup that exists.

The transport is a `Protocol`, not a base class, so the client is exercised against recorded
frames and no test opens a socket. `SocketCanBus` uses stdlib `AF_CAN`/`CAN_RAW`: the bus host
has no `python-can` and PEP 668 blocks installing one, and this needs neither.
"""
from __future__ import annotations

import socket
import struct
import time
from dataclasses import dataclass, field
from typing import Protocol

from . import upload
from .upload import Frame

#: `struct can_frame`: id, dlc, 3 pad bytes, 8 data bytes.
_FRAME = struct.Struct("<IB3x8s")

#: Set on the id of every 29-bit frame; RV-C uses no other kind.
_EFF_FLAG = 0x80000000
_EFF_MASK = 0x1FFFFFFF


class CanBus(Protocol):
    """What the client needs of a bus. Implemented for real by `SocketCanBus`."""

    def send(self, can_id: int, data: bytes) -> None: ...

    def recv(self, timeout: float):
        """One frame, or None if `timeout` elapsed first."""


class SocketCanBus:
    """A raw socketcan interface. The only thing here that touches the kernel."""

    def __init__(self, interface: str = "can1"):
        self.interface = interface
        self._sock = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
        self._sock.bind((interface,))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self) -> None:
        self._sock.close()

    def send(self, can_id: int, data: bytes) -> None:
        payload = bytes(data).ljust(8, b"\xff")[:8]
        self._sock.send(_FRAME.pack(can_id | _EFF_FLAG, len(payload), payload))

    def recv(self, timeout: float):
        if timeout <= 0:
            return None
        self._sock.settimeout(timeout)
        try:
            raw = self._sock.recv(_FRAME.size)
        except (TimeoutError, socket.timeout):
            return None
        can_id, dlc, data = _FRAME.unpack(raw)
        return Frame(time.time(), can_id & _EFF_MASK, data[:dlc])


@dataclass
class Device:
    """One upload target of one device: what a `0FF01` announced, plus its `0FF02` parameters."""

    source: int
    """The device's RV-C source address (`0x9C` for the G12 on this coach)."""

    status: upload.UploadStatus
    parameters: dict = field(default_factory=dict)
    """ParamID -> `UploadParameter`."""

    @property
    def key(self):
        return (self.status.device_instance, self.status.upload_type)

    @property
    def complete(self) -> bool:
        """Did as many parameters arrive as the status frame declared?"""
        return len(self.parameters) == self.status.parameter_count

    def value(self, name: str):
        """The value of a named parameter, or None if this target did not report it."""
        for parameter in self.parameters.values():
            if parameter.name == name:
                return parameter.value
        return None


@dataclass
class Enumeration:
    """What one listen heard."""

    devices: list = field(default_factory=list)
    identities: dict = field(default_factory=dict)
    """source address -> `upload.ConfigIdentity`, from each node's `1FED8` broadcast.

    Unprompted and independent of the upload targets: one frame carrying the manufacturer code,
    `FuncBank`, `FuncInst`, `ConfigRev`, `ConfigID` and `ConfigType`. The loader caches exactly
    this at slot 0 of its own node table and reads the manufacturer code back out of it to build
    a config upload's opening frame.
    """

    heard: set = field(default_factory=set)
    """Every source address seen. Lets a caller check its own address is free before reusing it."""

    transmitted: bool = False
    """Did this run put a frame on the bus? False for a purely passive listen."""

    requested_target: int | None = None
    """The address our request was DIRECTED at, if any.

    Parameters are only ever expected from that one device: PC.63 shows a broadcast request
    drawing `0FF01` alone, and a device we did not address has no reason to answer at all. A
    caller reporting "incomplete" must consult this, or it will accuse every device on the bus of
    a short dump whenever any request is sent (PC.67).
    """

    def was_asked(self, device) -> bool:
        """Were parameters actually solicited from this device?"""
        return self.transmitted and self.requested_target == device.source

    def keys(self):
        return [d.key for d in self.devices]

    def __getitem__(self, key):
        for device in self.devices:
            if device.key == key:
                return device
        raise KeyError(key)


def enumerate_devices(
    bus: CanBus,
    listen: float = 20.0,
    send_request: bool = False,
    target: int | None = None,
    source: int = upload.PROBE_SA,
    now=time.monotonic,
) -> Enumeration:
    """Listen for `listen` seconds and report every upload target heard.

    With `send_request` false this **transmits nothing** and rides on somebody else's request:
    the panel polls `1FED8` every 14.0 s and the G12 answers within ~6 ms, so a passive listen
    names every target, its firmware version and how many parameters it would report. **That
    depends on the panel being present and polling** -- on a bus without one, a passive listen
    hears nothing, and the silence would mean no poller rather than no device. Setting
    `send_request` sends one 8-byte REQUEST of our own, directed at `target` if given, which is
    the form PC.63 and PC.65 show drawing the full parameter dump.
    """
    found = Enumeration(
        transmitted=send_request,
        requested_target=target if send_request else None,
    )
    deadline = now() + listen

    if send_request:
        bus.send(*upload.request_frame(target=target, source=source))

    while True:
        remaining = deadline - now()
        if remaining <= 0:
            break
        frame = bus.recv(timeout=remaining)
        # A real socket returns None when its timeout elapses. That is a quiet bus, not the end
        # of the listen -- keep going until the deadline.
        if frame is None:
            continue
        found.heard.add(frame.source)

        identity = upload.parse_identity(frame)
        if identity is not None:
            found.identities[frame.source] = identity
            continue

        status = upload.parse_status(frame)
        if status is not None:
            key = (status.device_instance, status.upload_type)
            if key not in found.keys():
                found.devices.append(Device(source=frame.source, status=status))
            continue

        parameter = upload.parse_parameter(frame)
        if parameter is not None:
            key = (parameter.device_instance, parameter.upload_type)
            try:
                found[key].parameters[parameter.param_id] = parameter
            except KeyError:
                # A parameter for a target whose status frame we have not heard yet. Dropping it
                # is right: without the declared count there is nothing to check it against, and
                # the status frame repeats every ~14 s.
                pass

    return found
