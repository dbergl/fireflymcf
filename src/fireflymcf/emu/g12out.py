"""The G12 as the coach's load driver: outputs 1-44 under DC_DIMMER_COMMAND_2.

Nothing here is in the MCF: it is modelled on the coach captures (pc8/pc15/pc16/
pc27/pc28; docs/BUS-BASELINE.md PC.15/PC.16 and the "Two cadences" note).  What
the G12 sends, as SA 0x9C:

* ``0E8FF`` ACK ``00 FF FF FF FF DB FE 01`` for every 1FEDB command, its own
  included (pc8: 97 + 68 commands, 164 ACKs; pc27: 24 + 3, 27);
* ``1FEDA`` DC_DIMMER_STATUS_3 ``[inst, FF, level, FC|lock, duration, last cmd,
  04 on / 00 off | 40 undercurrent, FF relay / 00 PWM output]`` at once after a
  command, every 10 levels during a ramp, and every 2 s while the output is on
  or locked (0.5 s after the G12's 1 s tick; duration 02 answering a ramp
  command, 01 on ramp steps and on periodic statuses while ramping).  On some outputs (the coach's loads with undercurrent detection)
  bit 0x40 latches at a check on the G12's half-second grid (its 1 s tick and
  the status tick 0.5 s later) once the output has been on for 0.23 s; statuses
  answering a command report the bit but do not check.  Fitted to all 111
  statuses after a switch-on in the captures, which a plain time-since-on fits
  with no threshold (0.225 s clear, 0.254 s latched at the same phase; a
  switch-off at 0.378 s clear, no check in between).  Not every case fits:
  output 38 latched at 0.226 s in pc14 but was clear at 0.251 s in pc15, same
  command and tick phase, so something off the bus (a sampling clock of its
  own?) also decides.  On the half-bridge outputs 9-20 a command's own status
  never shows the bit, but the latch stays (output 9's ON re-sent every 5 s:
  04, and 44 in the periodic status 3 ms later, pc38 874.346 / 874.349);
  outputs 21-44
  still report it in their switch-off status and then clear it, the
  half-bridge outputs 9-20 only while on;
* ``16F84`` (to SA 0x84) ``[FF, inst, FF, FF, current, 7D, 05 on, 00]`` every
  second while on.  Byte 4 is the **load current** (unit unknown), not the
  level: at full brightness output 24 reports 0A, 43 02, 32 0F and 26/38 00, the
  awning motors 37-79 while running, the tank heater pad (relay 5) 2A-2C.  It
  follows the *actual* output: switching on from off fades in over ~0.6 s
  (soft start) while 1FEDA already shows the target; a level change while on is
  applied at once (live capture 2026-10-21: 0x0F per 0.13 s or faster).  The half-bridge outputs
  9-20 have no current sense and send ``FF FF`` in bytes 4-5.  A zero frame goes
  out when an output switches on, and when it is switched off by toggle (5) or
  memory-off (6) -- not by OFF (3), and not for relays (live 2026-10-21, tank
  heater), nor when a timed command runs out;
* ``16C84`` (to SA 0x84) ``[FF, inst, u16 count, u16 minutes, 00, 00]`` at
  switch-on and every 60 s while on; the minutes count up (an on-time counter;
  the first field is read as a switch-on count -- inferred).

Behaviour (continued): outputs 1-8 are relays (on/off; byte 7 = FF on the wire; command 1
switches one on whatever the level, pc8 ``05 FF 00 01``), 9-44 dim (0.5 % steps).  Command 21 ramps while re-sent (window 2 s) in steps of 1 % (2 levels),
waiting ``step_dwell(level)`` = 78.64 - 0.375 * level ms at each level before
the next step (3.6 ms at full, 77 ms at the bottom), fitted to every 10-level
crossing of the six holds on record to 1.1 ms, so a ramp takes 3.69 s from 200
to 10 and the 1FEDA level is always even.  The same dwell up and down explains
why each 10 levels take ~3.7 ms longer going up (the dwells at L..L+8 instead
of L+2..L+10).  Re-sent commands do not restart the dwell.
**Every hold starts downward**, turns back up at the bottom (level 4; the turn
costs one more dwell) and stays at full while held there -- all six holds on
record: live 2026-10-21 (output 21 from the app, 21 re-sent every 0.5 s) from
200 to 10 at +3.694 s, 4 by +4.02 s, 6 again at +4.14 s; the next hold, from
6, dipped to 4 first (10 only at +0.383 s), climbed to 200 at +4.147 s and
stayed there 0.74 s until released; pc15's second hold, from 14, also went
down first ("10, pause, 10 again" is the dip below 10 and back, statuses
going out only at multiples of 10).  Model and real step times agree to 0.4 ms.
PC.15's "direction alternates between holds" is
what that looks like from the panel.  Command 4 stops it.  Levels above 200 mean "remembered level": FB restores the state
stored by memory-off (6), FA/C8 toggles use the last on level.  33 locks an
output (byte 3 FD) and it then ignores everything but 34.  Commands with a
duration (0-3) switch off exactly that many seconds later; byte 4 reports
floor(D - elapsed), elapsed in the G12's 10 ms ticks (pc34 0F 5 ms after the
command, pc37 already 0E 2 ms after -- a tick fell in between; 0.81 s later 0E
... 00, off at 15.0 s; pc8's output 3, 2 s held).  OFF (3) with a duration is a delayed off:
the output stays on until then (the A/C fans' run-on, 0B/0C OFF 0F: pc8, pc34,
pc37, pc38), and the expiry status goes out even if the output was already off
(pc8 1132.829).  A plain OFF, SET or toggle cancels it.  16F84 keeps reporting
while a timed command runs.
"""

from . import rvc

STATUS, COMMAND, LEVEL16, COUNTER = 0x1FEDA, 0x1FEDB, 0x16F84, 0x16C84
ACK = bytes.fromhex("00ffffffffdbfe01")  # ACK of 1FEDB
OUTPUTS = range(1, 45)
RELAYS = range(1, 9)
HALF_BRIDGE = range(9, 21)      # 16F84 bytes 4-5 = FF FF (no current sense)
# 16F84 byte 4 at full output on the reference coach (pc8/pc15/pc16/pc27/pc28).
# Tank heaters 5-8 (live 2026-10-21, all on): 5 = 2C, 6 and 7 = 0 (heaters not
# working, owner), 8 = 41.
COACH_LOADS = {3: 0x3F, 4: 0x6D, 5: 0x2C, 8: 0x41, 21: 0x07, 22: 0x0B, 23: 0x16,
               24: 0x0A, 25: 0x06, 32: 0x0F, 34: 0x0E, 35: 0x0A, 43: 0x02}
# Outputs whose 1FEDA latches the undercurrent bit (all coach captures + live 2026-10-21).
COACH_UNDERCURRENT = {9, 17, 18, 21, 24, 25, 26, 35, 38, 43, 44}
UNDERCURRENT = 0x40
UC_DELAY = 0.23         # s on at a half-second check before the bit latches (0.222-0.236)
SET, ON_DURATION, ON_DELAY, OFF, STOP, TOGGLE, MEMORY_OFF = 0, 1, 2, 3, 4, 5, 6
RAMP_UP, RAMP_DOWN, RAMP_UP_DOWN, LOCK, UNLOCK = 19, 20, 21, 33, 34
FULL, FLOOR = 200, 4      # FLOOR: the bottom of a ramp (live 2026-10-21)
RAMP_WINDOW = 2.0
# 16F84 current while ramping lags the level by ~0.27 s (live 2026-10-21 ramp:
# every moving sample allows exactly 0.27; pc15 0.22-0.34, so +-1 count of noise)
SENSE_LAG = 0.27
SOFT_START = (0.12, 0.65)  # s after switch-on: actual output ramps 0 -> target (pc15, live)


def step_dwell(level):
    """Seconds a ramp stays at ``level`` before the next 2-level step (pc15 and
    live 2026-10-21 holds, least squares over all 10-level crossings)."""
    return 0.07864 - 0.00037512 * level


def load_current(full, level):
    """16F84 byte 4 for a load drawing ``full`` at level 200: proportional.  Output 32
    (full 0F) matches the 2026-08-30 brightness sweep at 24 of 25 levels."""
    return max(0, full * int(level) // FULL)


class Output:
    def __init__(self, inst, load=0, undercurrent=False):
        self.inst = inst
        self.relay = inst in RELAYS
        self.load = load        # 16F84 byte 4 at full output
        self.undercurrent = undercurrent  # this load trips undercurrent detection
        self.uc_latched = False
        self.uc_since = None    # cycle the undercurrent timing (re)started
        self.on_since = None    # cycle of the last switch-on (soft start)
        self.level = 0          # target (what 1FEDA reports)
        self.trail = [(0, 0)]   # recent (cycle, level) changes, for the sensing lag
        self.last_on = FULL     # used by "remembered level" toggles
        self.memory = None      # stored by memory-off, restored by FB
        self.last_cmd = 0xFF
        self.locked = False
        self.dur_len = 0xFF     # running timed command's seconds, FF = none
        self.dur_start = 0      # G12 tick it started
        self.dur_gen = 0        # invalidates a superseded expiry
        self.ramp_until = None  # cycle when the ramp window closes
        self.ramp_dir = 1       # +1 up, -1 down; flipped per new hold
        self.ramp_gen = 0       # bumped per hold; stale step timers check it
        self.count = 0          # 16C84 first field (switch-ons, inferred)
        self.minutes = 0

    @property
    def on(self):
        return self.level > 0


class G12Outputs:
    """Owned by a ``g12.G12``; uses its scheduler (``_at``) and transmitter (``_emit``)."""

    def __init__(self, g12, loads=None, undercurrent=None):
        self.g = g12
        loads = COACH_LOADS if loads is None else loads
        uc = COACH_UNDERCURRENT if undercurrent is None else undercurrent
        self.out = {i: Output(i, loads.get(i, 0), i in uc) for i in OUTPUTS}
        self._quiet_uc = False

    def start(self):
        self._statuses = True
        self.g._at(0.5, self._half_tick)
        self.g._at(60.0, self._minute_tick)

    def owns(self, inst):
        return inst in self.out

    def seed(self, inst, level, locked=False, last_cmd=0xFF, count=None, minutes=None,
             load=None, uc_latched=False):
        """Start an output in a given state (e.g. as a capture shows it), silently."""
        o = self.out[inst]
        o.level, o.locked, o.last_cmd = level, locked, last_cmd
        o.trail = [(0, level)]
        o.uc_latched = uc_latched
        if level:
            o.last_on = level
        if count is not None:
            o.count, o.minutes = count, minutes
        if load is not None:
            o.load = load

    # -- frames ------------------------------------------------------------------
    def _check_uc(self):
        m = self.g.bus.m
        for o in self.out.values():
            if o.undercurrent and o.on and o.uc_since is not None:
                if m.cycles - o.uc_since >= UC_DELAY * m.cpu_hz:
                    o.uc_latched = True

    def _duration(self, o):
        """Byte 4: floor(D - elapsed) while a timed command runs, else FF."""
        if o.dur_len == 0xFF:
            return 0xFF
        return max(0, (o.dur_len * 100 - (self.g.ticks - o.dur_start)) // 100)

    def _timed(self, o, seconds):
        o.dur_len, o.dur_start = seconds, self.g.ticks
        o.dur_gen += 1
        self.g._at(seconds, lambda gen=o.dur_gen: self._expire(o, gen))

    def _cancel(self, o):
        o.dur_len = 0xFF
        o.dur_gen += 1

    def _expire(self, o, gen):
        if gen != o.dur_gen:
            return
        o.dur_len = 0xFF
        if o.on:
            self._set(o, 0, edge=False)
        self._status(o)
        if not o.on:
            o.uc_latched = False

    def _status(self, o, duration=None):
        dur = self._duration(o) if duration is None else duration
        uc = o.uc_latched and not self._quiet_uc
        flags = (0x04 if o.on else 0x00) | (UNDERCURRENT if uc else 0)
        self.g._emit(STATUS, [o.inst, 0xFF, o.level, 0xFC | o.locked, dur, o.last_cmd,
                              flags, 0xFF if o.relay else 0x00])

    def _level16(self, o, edge=False):
        if o.inst in HALF_BRIDGE:
            current, b5 = 0xFF, 0xFF
        else:
            current = 0 if edge else min(0xFD, load_current(o.load, self._actual(o)))
            b5 = 0x7D
        self.g._emit(LEVEL16, [0xFF, o.inst, 0xFF, 0xFF, current, b5, 0x00 if edge else 0x05, 0x00])

    def _counter(self, o):
        self.g._emit(COUNTER, [0xFF, o.inst, o.count & 0xFF, o.count >> 8,
                               o.minutes & 0xFF, o.minutes >> 8 & 0xFF, 0, 0])

    # -- periodic -----------------------------------------------------------------
    def second(self):
        """Called by the G12 every second: the undercurrent check and 16F84."""
        self._check_uc()
        for o in self.out.values():
            if o.on:
                self._level16(o)

    def _half_tick(self):
        """0.5 s after each 1 s tick: the undercurrent check, and every other time
        the periodic statuses."""
        self.g._at(1.0, self._half_tick)
        self._check_uc()
        self._statuses = not self._statuses
        if not self._statuses:
            self._status_tick()

    def _status_tick(self):
        now = self.g.bus.m.cycles
        for o in self.out.values():
            if o.on or o.locked:    # duration 01 while a hold is ramping (live 2026-10-21)
                ramping = o.ramp_until is not None and now < o.ramp_until
                self._status(o, duration=1 if ramping else None)

    def _minute_tick(self):
        self.g._at(60.0, self._minute_tick)
        for o in self.out.values():
            if o.on:
                o.minutes += 1
                self._counter(o)

    def _track(self, o):
        now = self.g.bus.m.cycles
        o.trail.append((now, o.level))
        keep = now - int(2 * SENSE_LAG * self.g.bus.m.cpu_hz)
        while len(o.trail) > 1 and o.trail[1][0] <= keep:
            del o.trail[0]

    def _actual(self, o):
        """What the current sense sees: soft start after a switch-on, otherwise the
        level SENSE_LAG ago (relays switch fully at once)."""
        if not o.on or o.relay:
            return float(o.level)
        m = self.g.bus.m
        if o.on_since is not None:
            dt = (m.cycles - o.on_since) / m.cpu_hz
            t0, t1 = SOFT_START
            if dt < t1:
                return o.level * max(0.0, (dt - t0) / (t1 - t0))
        then = m.cycles - int(SENSE_LAG * m.cpu_hz)
        return float(next((lv for c, lv in reversed(o.trail) if c <= then), o.trail[0][1]))

    # -- commands ----------------------------------------------------------------
    def command(self, d, external=True):
        """DC_DIMMER_COMMAND_2 ``d`` (from the bus, or the G12's own rules)."""
        inst = d[0]
        o = self.out.get(inst)
        if o is None or len(d) < 4:
            return
        self.g._emit(rvc.ACKNOWLEDGEMENT | 0xFF, ACK)
        self._quiet_uc = o.inst in HALF_BRIDGE   # its command statuses hide the bit (pc38)
        try:
            self._command(o, d)
        finally:
            self._quiet_uc = False

    def _command(self, o, d):
        desired, cmd = d[2], d[3]
        duration = d[4] if len(d) > 4 else 0xFF
        o.last_cmd = cmd
        if o.locked and cmd != UNLOCK:
            self._status(o)
            return
        if cmd in (SET, ON_DURATION, ON_DELAY):
            if desired == 0xFB:
                target = o.memory if o.memory is not None else o.last_on
            elif desired > FULL:
                target = o.last_on
            else:
                target = desired
            if o.relay and cmd in (ON_DURATION, ON_DELAY):
                target = FULL
            self._cancel(o)
            self._set(o, target)
            if target and duration not in (0, 0xFF):
                self._timed(o, duration)
        elif cmd == OFF and duration not in (0, 0xFF):
            self._timed(o, duration)            # delayed off
        elif cmd in (OFF, MEMORY_OFF):
            if cmd == MEMORY_OFF:
                o.memory = o.level
            self._cancel(o)
            self._set(o, 0, edge=cmd == MEMORY_OFF)
        elif cmd == TOGGLE:
            self._cancel(o)
            self._set(o, 0 if o.on else (o.last_on if desired > FULL else desired), edge=True)
        elif cmd == STOP:
            o.ramp_until = None
        elif cmd in (RAMP_UP, RAMP_DOWN, RAMP_UP_DOWN):
            self._ramp(o, cmd)
            return
        elif cmd == LOCK:
            o.locked = True
        elif cmd == UNLOCK:
            o.locked = False
        self._status(o)
        if not o.on:
            o.uc_latched = False  # reported once in the switch-off status

    def _set(self, o, level, edge=True):
        if o.relay and level:
            level = FULL
        was_on = o.on
        o.level = min(level, FULL)
        o.ramp_until = None
        if o.on:
            o.last_on = o.level
        self._track(o)
        if o.on != was_on:
            if o.on:
                o.on_since = o.uc_since = self.g.bus.m.cycles
                o.uc_latched = False
            if o.on or (edge and o.dur_len == 0xFF and not o.relay):
                self._level16(o, edge=True)
            if o.on:
                o.count += 1
                self._counter(o)
            else:
                if o.inst in HALF_BRIDGE:
                    o.uc_latched = False

    # -- ramping -------------------------------------------------------------------
    def _ramp(self, o, cmd):
        if o.relay:
            self._status(o)
            return
        m = self.g.bus.m
        new_hold = o.ramp_until is None or m.cycles >= o.ramp_until
        if cmd == RAMP_UP:
            o.ramp_dir = 1
        elif new_hold or cmd == RAMP_DOWN:     # a hold always starts downward
            o.ramp_dir = -1
        if new_hold and not o.on:
            o.level = FLOOR
            self._track(o)
        o.ramp_until = m.cycles + int(RAMP_WINDOW * m.cpu_hz)
        self._status(o, duration=2)
        if new_hold:
            o.ramp_gen += 1
            gen = o.ramp_gen
            self.g._at(step_dwell(o.level), lambda: self._ramp_step(o, gen))

    def _ramp_step(self, o, gen):
        m = self.g.bus.m
        if gen != o.ramp_gen:
            return
        if o.ramp_until is None or m.cycles >= o.ramp_until:
            o.ramp_until = None
            return
        if o.ramp_dir < 0 and o.level <= FLOOR:     # bottom: the turn takes one dwell
            o.ramp_dir = 1
        elif o.ramp_dir > 0 and o.level >= FULL:    # full: stay there while held
            pass
        else:
            o.level = min(max(o.level + 2 * o.ramp_dir, FLOOR), FULL)
            o.last_on = o.level
            self._track(o)
            if o.level % 10 == 0:
                self._status(o, duration=1)
        self.g._at(step_dwell(o.level), lambda: self._ramp_step(o, gen))
