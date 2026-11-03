"""The G12's thermostat: THERMOSTAT_COMMAND_1 in, THERMOSTAT_STATUS_1 out, A/C outputs.

The temperature sensor is wired to the G12 (owner), so the G12 is the coach's
thermostat: it broadcasts the ambient (1FF9C, table 0x61 #8 from cell 0x6B) and
1FFE2, and answers 1FEF9 by switching its A/C outputs.

Only the fan arm is in the MCF: the blob reads cell 0x68 (status bytes 1 | 2 << 8:
operating mode, fan mode, fan speed) and with fan mode 1 runs fan low (rules #37
on, #34 off) at speed 100 or fan high (#38, #35) at 200.  The rest is the G12
application, modelled on the coach captures (pc8, pc34, pc37, pc38, pc63):

* ``1FEF9`` instance 0 updates the state; per field FF (nibble F / 3) keeps it.
  Byte 1 = mode (bits 0-3) | fan mode (4-5) | schedule (6-7, not reported).
  Fan speed 0 or 100 reads back 100, 200 reads back 200 (pc34, pc8).  A setpoint
  of FFFA / FFF9 steps it 1 degF up / down; the steps 2501 -> 2513 -> 2501 ->
  24F0 (pc8) fit ``ceil(((F - 32) / 1.8 + 273.25) * 32)`` -- three points.
  ``1FFE2`` answers at once and every 5 s, ``[0, mode | fan << 4, speed, heat,
  cool, 00]``; a changed cool setpoint is copied to heat at the next periodic one.
* Fan mode back to auto with the compressor off turns the fan off: 0B (#34)
  after speed 200, 0C (#35) after 100 -- the blob does nothing there.
* Fan mode 1 or a new speed switches the fan at once, before the status: the
  old fan off (as above), then the new one's pair -- 0B FF, 0C 200 (#36, #37)
  for low, 0B 200, 0C FF (#38, #39) for high.  The blob's own off rule (#34 /
  #35, 0F) follows on its next pass, ~10 ms later (pc8 1108.43, 1117.82; pc37).
* Rules of kind 0x24 / 0x28 go out as pairs (``G12.PAIRED``).
* Cool (mode 1) or auto (3) with the ambient (cell 0x6B) above the cool setpoint
  starts the compressor 3.0 s after the command: 0B off, 0C on, 2B and 09 on
  (rules #36, #37, #32, #33), then 14 ms later 0B on, 0C off (#38, #39) -- the
  same on every capture whatever the fan speed.  2B/09 ON is re-sent every 5 s
  from the start.  Turning it off never stopped it early: every run lasted
  exactly 120 s, ending on a 5 s re-send with 0B/0C off 0F (#34, #35) and 2B/09
  OFF three times 250 ms apart (#30, #31).  The ambient condition is inferred:
  heat mode at 35 degC against a 22 degC setpoint drove nothing (pc34), and every
  cooling run had the ambient above the setpoint.
* Heating: the heat pump is the OPTIONS row Heat Pump (cell 0xEB, which also
  reveals the Heat Pump and Auto modes); the owner's coach has none, but with
  the option turned on (coach tests 2026-10-22, run 3) heat (mode 2) or auto,
  with the ambient below the heat setpoint, runs exactly like cooling on 0A
  alone (#41 ON, re-sent every 5 s; #40 OFF three times 250 ms apart) -- the
  compressor (2B/09) stays off -- with the same fan sequence and the same
  120 s run.  The last 5 s re-send before the stop is a race: pc34, pc38 and
  run 3 send it, pc8 does not.
* After a run the A/C rests 120 s: a start comes 3.0 s after the command or
  120 s after the previous run's end, whichever is later (run 3: Auto at
  17:40:15.47, start 17:41:19.04, 120.00 s after the 17:39:19.04 stop).
* At power-up, 26 ms after the first sweep frame, everything goes off: #34,
  #35, then #30, #31 and #40 three times 250 ms apart (pc63).
"""
import math

STATUS, COMMAND = 0x1FFE2, 0x1FEF9
FLAGS_CELL, AMBIENT_CELL, HEAT_PUMP_CELL = 0x68, 0x6B, 0xEB
OFF, COOL, HEAT, AUTO = 0, 1, 2, 3
START_DELAY, RESEND, MIN_RUN, FAN_SETTLE, REST = 3.0, 5.0, 120.0, 0.014, 120.0
STEP_UP, STEP_DOWN = 0xFFFA, 0xFFF9


def to_f(raw):
    return round(((raw / 32) - 273.25) * 1.8 + 32)


def from_f(f):
    return math.ceil(((f - 32) / 1.8 + 273.25) * 32)


class G12Thermostat:
    def __init__(self, g12, status=bytes.fromhex("000000e024e02400")):
        self.g = g12
        self.mode, self.fan = status[1] & 0x0F, status[1] >> 4 & 3
        self.speed = status[2]
        self.heat, self.cool = status[3] | status[4] << 8, status[5] | status[6] << 8
        self.follow = False        # copy cool -> heat at the next periodic status
        self.running = None        # start time of a cooling / heating run, or None
        self.kind = None           # COOL or HEAT while running
        self.stopped = None        # when the last run ended (the 120 s rest)
        self._gen = 0              # invalidates a pending start or re-send chain

    def start(self, cold=True):
        self._flags()
        if cold:
            self._all_off()        # pc63: 26 ms after the first sweep frame; here at once
        self.g._at(3.013, self._periodic)

    def _all_off(self):
        """At power-up (pc63, after the network upload): fans off (0F), then 2B, 09
        and 0A OFF three times 250 ms apart."""
        for n in (34, 35):
            self.g._transmit(n)
        for n in (30, 31, 40):
            self.g._activate(n)

    def _now(self):
        return self.g.bus.m.cycles / self.g.bus.m.cpu_hz

    def _flags(self):
        self.g.set_cell(FLAGS_CELL, (self.mode | self.fan << 4) | self.speed << 8)

    def status(self):
        return [0, self.mode | self.fan << 4, self.speed, self.heat & 0xFF, self.heat >> 8,
                self.cool & 0xFF, self.cool >> 8, 0]

    def _periodic(self):
        self.g._at(RESEND, self._periodic)
        if self.follow:
            self.heat, self.follow = self.cool, False
        self.g._emit(STATUS, self.status())

    @staticmethod
    def _setpoint(old, new):
        if new == 0xFFFF:
            return old
        if new in (STEP_UP, STEP_DOWN):
            return from_f(to_f(old) + (1 if new == STEP_UP else -1))
        return new

    def command(self, d):
        if len(d) < 7 or d[0] != 0:
            return
        fan_on, speed = self.fan == 1, self.speed
        if d[1] & 0x0F != 0x0F:
            self.mode = d[1] & 0x0F
        if d[1] >> 4 & 3 != 3:
            self.fan = d[1] >> 4 & 3
        if d[2] != 0xFF:
            self.speed = 100 if d[2] <= 100 else 200
        self.heat = self._setpoint(self.heat, d[3] | d[4] << 8)
        cool = self._setpoint(self.cool, d[5] | d[6] << 8)
        self.follow |= cool != self.cool
        self.cool = cool
        self._flags()
        if self.running is None:
            if fan_on and (self.fan != 1 or self.speed != speed):
                self._fan_off(speed)
            if self.fan == 1 and (not fan_on or self.speed != speed):
                self.g._activate(37 if self.speed == 100 else 38)
        self.g._emit(STATUS, self.status())
        if self.demand() and self.running is None:
            self._gen += 1
            delay = START_DELAY
            if self.stopped is not None:
                delay = max(delay, self.stopped + REST - self._now())
            self.g._at(delay, lambda gen=self._gen: self._start(gen))

    def _fan_off(self, speed):
        """Fan mode back to auto, or a new speed: the fan that ran goes off (0F), as the
        blob never does -- #34 (0B, high) after speed 200 (pc8), #35 (0C, low) after
        100 (pc37, pc8 low -> high).
        Its rules are cleared so the blob fires them again next time."""
        self.g._transmit(34 if speed == 200 else 35)
        for n in range(34, 40):
            self.g._deactivate(n)

    def demand(self):
        """COOL, HEAT (inferred) or None."""
        ambient = self.g.cell(AMBIENT_CELL)
        if self.mode in (COOL, AUTO) and ambient > self.cool:
            return COOL
        if self.mode in (HEAT, AUTO) and self.g.cell(HEAT_PUMP_CELL) == 1 and ambient < self.heat:
            return HEAT
        return None

    def _on_rules(self):
        return (32, 33) if self.kind == COOL else (41,)

    def _off_rules(self):
        return (30, 31) if self.kind == COOL else (40,)

    def _start(self, gen):
        if gen != self._gen or self.running is not None or not self.demand():
            return
        self.running, self.kind = self._now(), self.demand()
        self.g._activate(36)       # kind 0x24: with #37
        for n in self._on_rules():
            self.g._transmit(n)
        self.g._at(FAN_SETTLE, lambda: self.g._activate(38))   # kind 0x28: with #39
        self.g._at(RESEND, lambda gen=gen: self._cycle(gen))

    def _cycle(self, gen):
        if gen != self._gen or self.running is None:
            return
        for n in self._on_rules():
            self.g._transmit(n)
        if self.demand() != self.kind and self._now() - self.running >= MIN_RUN - 1e-3:
            self.running = None
            self.stopped = self._now()
            self._gen += 1
            for n in (34, 35):
                self.g._transmit(n)
            for n in self._off_rules():
                self.g._activate(n)    # kind 0x40: three times, 250 ms apart
            return
        self.g._at(RESEND, lambda: self._cycle(gen))
