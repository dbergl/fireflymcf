"""CAN bus around the emulated bxCAN: simulated RV-C peers and optional python-can.

Frames the firmware transmits go to every peer and to the external bus;
frames from peers or the external bus are delivered to the bxCAN RX FIFO.
Peers run on simulated time (machine cycles) so idle fast-forward respects
them; external frames arrive on a reader thread and are drained between
emulation slices.
"""
import logging
import queue
import shlex
import threading
import time
from collections import deque

from . import rvc
from .periph.bxcan import Frame

log = logging.getLogger("canbus")

# Runs on the remote host (python3 -c, standard library only): a raw SocketCAN socket,
# received frames out as ``candump -L`` lines, lines ``ID#DATA`` in as transmissions.
# With "rx" as its second argument it never reads stdin and never sends.  A raw
# socket does not receive its own frames, so nothing loops back.
BRIDGE = r'''
import socket, struct, sys, threading, time
ch, mode = sys.argv[1], sys.argv[2]
s = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
s.bind((ch,))
F = "=IB3x8s"
def rx():
    while True:
        cid, dlc, data = struct.unpack(F, s.recv(16))
        ext = cid & socket.CAN_EFF_FLAG
        cid &= socket.CAN_EFF_MASK if ext else socket.CAN_SFF_MASK
        ident = ("%08X" if ext else "%03X") % cid
        sys.stdout.write("(%.6f) %s %s#%s\n" % (time.time(), ch, ident, data[:dlc].hex().upper()))
        sys.stdout.flush()
if mode == "rx":
    rx()
threading.Thread(target=rx, daemon=True).start()
for line in sys.stdin:
    ident, _, hexdata = line.strip().partition("#")
    if not ident:
        continue
    cid = int(ident, 16) | (socket.CAN_EFF_FLAG if len(ident) > 3 else 0)
    d = bytes.fromhex(hexdata)
    s.send(struct.pack(F, cid, len(d), d.ljust(8, b"\0")))
'''

class Peer:
    """Base class: override on_frame() and/or schedule periodic frames with every()."""

    sa = 0x80

    def attach(self, bus):
        self.bus = bus
        self.periodic = []  # [next_due_cycle, period_cycles, fn() -> (dgn, data) or None]

    def every(self, seconds, fn, phase=0.0):
        hz = self.bus.m.cpu_hz
        self.periodic.append([self.bus.m.cycles + int(phase * hz), int(seconds * hz), fn])
        self.bus.m.reschedule()

    def send(self, dgn, data, prio=6):
        self.bus.deliver(Frame(rvc.can_id(dgn, self.sa, prio), bytes(data), extended=True), self)

    def on_frame(self, frame):
        pass

class CanBus:
    def __init__(self, machine, can):
        self.m = machine
        self.can = can
        self.peers = []
        self.external = None
        self.inbox = queue.Queue()
        self.log_rx = False
        self.pending = deque()   # frames waiting for the (serial) bus
        self.bus_free_at = 0     # cycle when the frame on the wire finishes
        can.sink = self._from_firmware

    # -- wiring ------------------------------------------------------------
    def add_peer(self, peer):
        peer.attach(self)
        self.peers.append(peer)
        self.m.reschedule()
        return peer

    def connect(self, spec, tx=None, allow_config=False, tx_log=None):
        """Bridge to a python-can bus, e.g. 'socketcan:vcan0' or 'virtual:rvc', or
        listen to a remote SocketCAN bus over SSH: 'ssh:USER@HOST:CHANNEL'.  With
        ``tx`` ("dry" or "live") the SSH link is two-way (``bridge_ssh``)."""
        if spec.startswith("ssh:"):
            _, host, channel = spec.split(":", 2)
            if tx:
                self.bridge_ssh(host, channel, tx, allow_config, tx_log)
            else:
                self.listen_ssh(host, channel)
            return
        import can

        interface, _, channel = spec.partition(":")
        self.external = can.Bus(interface=interface, channel=channel or None)
        threading.Thread(target=self._reader, daemon=True, name="can-reader").start()
        log.info("bridged to python-can %s", spec)

    def listen_ssh(self, host, channel, skip_sources=(0x9F,)):
        """Feed a remote bus (``ssh host candump -L channel``) to the emulated panel.

        Listen-only by design: nothing the emulator sends reaches the remote bus.
        On a coach the emulated panel would claim the real panel's address (0x9F)
        and the two would fight over it, and the real panel's own frames are
        dropped here for the same reason (the firmware resets on an address
        conflict).
        """
        import subprocess

        from .replay import parse_line

        proc = subprocess.Popen(["ssh", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=10",
                                 host, "candump", "-L", channel],
                                stdout=subprocess.PIPE, text=True, bufsize=1)
        self.remote = proc
        self.remote_dropped = 0

        def reader():
            for line in proc.stdout:
                parsed = parse_line(line)
                if parsed is None:
                    continue
                frame = parsed[1]
                if frame.extended and rvc.split_id(frame.can_id)[2] in skip_sources:
                    self.remote_dropped += 1
                    continue
                self.inbox.put(frame)
            log.warning("remote bus %s:%s closed", host, channel)

        threading.Thread(target=reader, daemon=True, name="ssh-candump").start()
        self.polling = True
        log.info("listening to %s on %s (listen-only; SA 9F dropped)", channel, host)

    def bridge_ssh(self, host, channel, tx, allow_config=False, tx_log=None):
        """Join a remote bus over SSH as the panel, for a coach whose real panel is
        disconnected (two nodes at 0x9F reset each other, see NOTES).

        ``tx="dry"``: the remote end is started receive-only and every frame the
        emulated panel would send is written to ``tx_log`` instead.  ``tx="live"``:
        frames go onto the bus, except settings writes unless ``allow_config``: G12
        config writes (1FED9 group 0x96, D1/D2) and the panel's option broadcasts
        (1EE00, e.g. ``0A 00 01`` turned the Heat Pump option and the G12's cell 0xEB
        on, 2026-10-22).  At boot the emulated panel writes 0x9B (the AES-enabled
        mirror) and options 0x0D and 0x18 from its own EEPROM, which need not match
        the coach.  Held frames are logged.  Any frame from 0x9F on
        the remote bus means a real panel is there: it is reported and, live,
        transmission stops.  Simulated time is paced to the wall clock.
        """
        import subprocess

        from .replay import parse_line

        if tx not in ("dry", "live"):
            raise ValueError("tx must be 'dry' or 'live'")
        cmd = f"python3 -u -c {shlex.quote(BRIDGE)} {shlex.quote(channel)} " + \
            ("rx" if tx == "dry" else "tx")
        proc = subprocess.Popen(["ssh", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=10",
                                 host, cmd], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                text=True, bufsize=1)
        self.remote, self.tx_mode, self.allow_config = proc, tx, allow_config
        self.tx_log = open(tx_log, "a", encoding="utf-8") if tx_log else None
        self.tx_counts = {"sent": 0, "held": 0}
        self.foreign_panel = 0

        def reader():
            for line in proc.stdout:
                parsed = parse_line(line)
                if parsed is None:
                    continue
                frame = parsed[1]
                if frame.extended and rvc.split_id(frame.can_id)[2] == 0x9F:
                    if not self.foreign_panel:
                        log.error("a real panel (SA 9F) is on %s: %s -- %s", channel, frame,
                                  "transmission stopped" if tx == "live" else "dry run only")
                    self.foreign_panel += 1
                    continue
                self.inbox.put(frame)
            log.warning("remote bus %s:%s closed", host, channel)

        threading.Thread(target=reader, daemon=True, name="ssh-bridge").start()
        self.polling = True
        self.pace = (time.monotonic(), self.m.cycles)
        log.info("joined %s on %s as the panel: %s", channel, host,
                 "DRY RUN, nothing is transmitted" if tx == "dry" else
                 "LIVE" + ("" if allow_config else ", G12 config writes held"))

    def _remote_tx(self, frame):
        d = bytes(frame.data)
        ident = ("%08X" if frame.extended else "%03X") % frame.can_id
        _, dgn, _ = rvc.split_id(frame.can_id) if frame.extended else (0, 0, 0)
        config_write = ((dgn == 0x1FED9 and len(d) >= 7 and d[1] == 0x96
                         and d[6] in (0xD1, 0xD2)) or dgn == 0x1EE00)
        hold = (self.tx_mode == "dry" or self.foreign_panel
                or (config_write and not self.allow_config))
        if self.tx_log:
            self.tx_log.write(f"({time.time():.6f}) {'held' if hold else 'sent'} "
                              f"{ident}#{d.hex().upper()}\n")
            self.tx_log.flush()
        if hold:
            self.tx_counts["held"] += 1
            if config_write and self.tx_mode == "live":
                log.warning("held a settings write: %s#%s", ident, d.hex().upper())
            return
        self.remote.stdin.write(f"{ident}#{d.hex().upper()}\n")
        self.remote.stdin.flush()
        self.tx_counts["sent"] += 1

    def _reader(self):
        while True:
            msg = self.external.recv()
            if msg is not None and not msg.is_error_frame:
                self.inbox.put(Frame(msg.arbitration_id, bytes(msg.data), msg.is_extended_id,
                                     msg.is_remote_frame))

    # -- traffic -----------------------------------------------------------
    def _from_firmware(self, frame):
        for p in self.peers:
            p.on_frame(frame)
        if getattr(self, "tx_mode", None):
            self._remote_tx(frame)
        if self.external is not None:
            import can

            self.external.send(can.Message(arbitration_id=frame.can_id, data=frame.data,
                                           is_extended_id=frame.extended,
                                           is_remote_frame=frame.rtr))

    BITS_PER_FRAME = 130  # 29-bit ID, 8 data bytes, stuffing: ~130 bit times

    def deliver(self, frame, origin=None):
        """Queue a frame for the bus; frames go out one at a time at bus speed, so
        bursts don't overflow the controller's 3-deep receive FIFOs."""
        self.pending.append((frame, origin))
        self.m.reschedule()

    def _transmit(self, frame, origin):
        if self.log_rx:
            log.info("rx %s  %s", frame, rvc.dgn_name(rvc.split_id(frame.can_id)[1]))
        self.can.receive(frame)
        for p in self.peers:
            if p is not origin:
                p.on_frame(frame)
        if self.external is not None and origin is not None:
            import can

            self.external.send(can.Message(arbitration_id=frame.can_id, data=frame.data,
                                           is_extended_id=frame.extended))

    # -- machine integration (timed peripheral protocol) -------------------
    def next_event(self):
        while not self.inbox.empty():
            self.pending.append((self.inbox.get_nowait(), None))
        now = self.m.cycles
        nxt = None
        if self.pending and now >= self.bus_free_at:
            self._transmit(*self.pending.popleft())
            self.bus_free_at = now + self.can.bit_cycles() * self.BITS_PER_FRAME

        for p in self.peers:
            if hasattr(p, "pump"):
                d = p.pump(now)
                if d is not None:
                    nxt = d if nxt is None else min(nxt, d)
            for job in p.periodic:
                if now >= job[0]:
                    job[0] += job[1] * max(1, (now - job[0]) // job[1] + 1)
                    out = job[2]()
                    if out is not None:  # None: nothing to send this time
                        p.send(*out)
                nxt = job[0] - now if nxt is None else min(nxt, job[0] - now)
        if self.pending:
            wait = max(1, self.bus_free_at - now)
            nxt = wait if nxt is None else min(nxt, wait)
        if self.external is not None or getattr(self, "polling", False):
            nxt = min(nxt or 10**9, self.m.cpu_hz // 1000)  # keep polling the inbox
        pace = getattr(self, "pace", None)
        if pace is not None:               # a live bus: never run ahead of the wall clock
            ahead = (now - pace[1]) / self.m.cpu_hz - (time.monotonic() - pace[0])
            if ahead > 0.002:
                time.sleep(ahead)
        return nxt
