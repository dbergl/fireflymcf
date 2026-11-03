"""Replay a recorded coach bus (candump -l format) onto the emulated CAN bus.

Lines look like ``(1787540931.145574) can1 19FFB79C#000003FFFFFFFFFF``.  Frames
are played on simulated time with their original spacing.  Frames sent by the
recorded panel itself (source 0x9F by default) are skipped: the emulator is
the panel, and replaying its address claim would make the firmware reset on an
address conflict.
"""
import logging

from . import rvc
from .canbus import Peer
from .periph.bxcan import Frame

log = logging.getLogger("replay")


def parse_line(line):
    """One ``candump -L`` line -> (timestamp, Frame), or None."""
    line = line.strip()
    if not line.startswith("("):
        return None
    try:
        ts, _iface, payload = line.split()
        ident, _, data = payload.partition("#")
        t = float(ts.strip("()"))
        if data.startswith("R"):
            return None  # remote frames: not used on RV-C
        return t, Frame(int(ident, 16), bytes.fromhex(data), extended=len(ident) > 3)
    except ValueError:
        return None


def parse_candump(path):
    """-> list of (seconds_from_start, Frame)."""
    out = []
    t0 = None
    for line in open(path, encoding="utf-8"):
        parsed = parse_line(line)
        if parsed is None:
            continue
        t, frame = parsed
        t0 = t if t0 is None else t0
        out.append((t - t0, frame))
    return out


class CandumpReplay(Peer):
    def __init__(self, path, skip_sources=(0x9F,), loop=False):
        self.frames = [(t, f) for t, f in parse_candump(path)
                       if not (f.extended and rvc.split_id(f.can_id)[2] in skip_sources)]
        self.loop = loop
        self.path = path
        self.duration = self.frames[-1][0] if self.frames else 0
        log.info("replay %s: %d frames over %.1f s%s", path, len(self.frames), self.duration,
                 " (looping)" if loop else "")

    def attach(self, bus):
        super().attach(bus)
        self.start = bus.m.cycles
        self.pos = 0
        self.passes = 0

    def pump(self, now):
        """Deliver every frame now due; return cycles until the next one (or None)."""
        hz = self.bus.m.cpu_hz
        while self.frames:
            if self.pos >= len(self.frames):
                if not self.loop:
                    return None
                self.pos = 0
                self.passes += 1
                self.start += int((self.duration + 0.1) * hz)
            t, frame = self.frames[self.pos]
            due = self.start + int(t * hz)
            if due > now:
                return due - now
            self.bus.deliver(frame, self)
            self.pos += 1
        return None
