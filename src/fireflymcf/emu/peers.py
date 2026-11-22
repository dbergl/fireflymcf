"""Simulated RV-C devices for the emulated network."""
import logging
import time

from . import rvc
from .canbus import Peer

log = logging.getLogger("peers")


class Clock(Peer):
    """Broadcasts DATE_TIME_STATUS from the host clock once a second."""

    sa = 0x80

    def attach(self, bus):
        super().attach(bus)
        self.every(1.0, lambda: (rvc.DATE_TIME_STATUS, rvc.date_time_status(time.localtime())))


class DcSources(Peer):
    """DC_SOURCE_STATUS_1 for each {instance: volts}."""

    sa = 0x81

    def __init__(self, volts):
        self.volts = dict(volts)

    def attach(self, bus):
        super().attach(bus)
        for i, (inst, v) in enumerate(self.volts.items()):
            self.every(1.0, lambda inst=inst: (rvc.DC_SOURCE_STATUS_1,
                                               rvc.dc_source_status_1(inst, self.volts[inst])),
                       phase=0.05 * i)


class Thermostats(Peer):
    """THERMOSTAT_AMBIENT_STATUS for each {instance: deg C}."""

    sa = 0x82

    def __init__(self, temps):
        self.temps = dict(temps)

    def attach(self, bus):
        super().attach(bus)
        for i, inst in enumerate(self.temps):
            self.every(2.0, lambda inst=inst: (rvc.THERMOSTAT_AMBIENT_STATUS,
                                               rvc.thermostat_ambient_status(inst, self.temps[inst])),
                       phase=0.03 * i)


class Periodic(Peer):
    """Arbitrary periodic frames: [(seconds, dgn, data_bytes), ...]."""

    def __init__(self, frames, sa=0x83):
        self.frames = list(frames)
        self.sa = sa

    def attach(self, bus):
        super().attach(bus)
        for i, (period, dgn, data) in enumerate(self.frames):
            self.every(period, lambda dgn=dgn, data=data: (dgn, rvc.pad(data)), phase=0.02 * i)


class FireflyBatteries(Peer):
    """House/Chassis voltages on DGN 1AAFD (instance 1 = House, 2 = Chassis, 0.05 V),
    the DGN the HOME page's battery widgets read (data points 4/5)."""

    sa = 0x84
    DGN = 0x1AAFD

    def __init__(self, house=13.1, chassis=12.6):
        self.volts = {1: house, 2: chassis}

    def attach(self, bus):
        super().attach(bus)
        for inst in self.volts:
            self.every(1.0, lambda inst=inst: (self.DGN, [inst, 0xFF, *rvc.volts(self.volts[inst])
                                                          .to_bytes(2, "little")]),
                       phase=0.04 * inst)


class DimmerModule(Peer):
    """DC dimmer channels answering DC_DIMMER_COMMAND_2 with DC_DIMMER_STATUS_3.

    Status byte 6 bits 2-3 are the load status the GUI reads (01 = on).
    Levels are RV-C 0-200 (0.5 %).
    """

    sa = 0x44
    # DC_DIMMER_COMMAND_2 byte 3 (rvc-spec.yml)
    SET, ON_DURATION, ON_DELAY, OFF, STOP, TOGGLE, MEMORY_OFF = 0, 1, 2, 3, 4, 5, 6
    RAMP_BRIGHTNESS, RAMP_TOGGLE, RAMP_UP, RAMP_DOWN, RAMP_UP_DOWN = 17, 18, 19, 20, 21
    RESUME = 0xFB  # desired level the panel uses for "on at last level"
    # Light Master OFF sends MEMORY_OFF (off, remembering the level that a later
    # RESUME / Light Master ON restores).

    def __init__(self, instances):
        self.level = {i: 0 for i in instances}
        self.last = {i: 200 for i in instances}

    def attach(self, bus):
        super().attach(bus)
        self.every(5.0, self._status_all)
        self._next = 0

    def _status(self, inst):
        lvl = self.level[inst]
        return rvc.pad([inst, 0xFF, lvl, 0x00, 0xFF, 0xFF, 0x04 if lvl else 0x00])

    def _status_all(self):
        # one channel per call keeps the bus spread out; cycles through all of them
        insts = sorted(self.level)
        inst = insts[self._next % len(insts)]
        self._next += 1
        return rvc.DC_DIMMER_STATUS_3, self._status(inst)

    def on_frame(self, frame):
        _, dgn, _ = rvc.split_id(frame.can_id)
        if dgn != rvc.DC_DIMMER_COMMAND_2 or len(frame.data) < 4:
            return
        inst, desired, cmd = frame.data[0], frame.data[2], frame.data[3]
        if inst not in self.level:
            return
        on = self.last[inst] if desired > 200 else desired
        if cmd in (self.SET, self.ON_DURATION, self.ON_DELAY, self.RAMP_BRIGHTNESS):
            self.level[inst] = on
        elif cmd in (self.OFF, self.MEMORY_OFF, self.RAMP_DOWN):
            self.level[inst] = 0
        elif cmd in (self.TOGGLE, self.RAMP_TOGGLE, self.RAMP_UP_DOWN):
            self.level[inst] = 0 if self.level[inst] else on
        elif cmd == self.RAMP_UP:
            self.level[inst] = 200
        else:
            log.info("dimmer %02X: command %d not modelled (ignored)", inst, cmd)
            return
        if self.level[inst]:
            self.last[inst] = self.level[inst]
        log.info("dimmer %02X -> %d%%", inst, self.level[inst] // 2)
        self.send(rvc.DC_DIMMER_STATUS_3, self._status(inst))


class ProtocolG12(Peer):
    """Fallback G12 model (no G12 logic available): configuration selectors over RV-C.

    Protocol per rvc2mqtt's rvc-spec.yml (15FCE, 1FED9, 1FACF):
    * The panel writes/reads selectors with 1FED9 group 0x96:
      ``FF 96 <sel> 0F <u16 LE> D1 EA`` = SET, ``FF 96 <sel> 0F FF FF D3 FF`` = QUERY.
      The panel's own +/- steppers send ``D2`` with a signed delta (byte 7 FF), then
      a zero-delta ``D2 .. EA``, then a QUERY; its absolute writes (floorplan
      transaction) use D1.  So D2 is modelled as delta.  The spec also reports
      selectors that act as deltas under D1 on the real G12 (DELTA below).
    * The G12 (source 0x9C) answers and periodically sweeps every selector on
      15FCE as ``<sel> 00 <u16 LE> 00 00 00 00``.
    * 1FACF carries F1 / F5 (floorplan) / F2 at payload bytes 2 / 5 / 7; the panel
      learns the controller's address from it. F1.F2 is shown on SETTINGS as
      "Logic Controller Version" (coach: 0x0D.0x06 = 13.6, the MCF version).
    Selectors persist to ``path`` (JSON) when given, as the real G12 stores them.
    """

    sa = 0x9C
    CONFIG_DGN, WRITE_DGN, HEARTBEAT_DGN = 0x15FCE, 0x1FED9, 0x1FACF
    DELTA = {0xCC, 0xCD, 0xCE, 0x82, 0x86, 0x88, 0x92, 0x99}
    FLOORPLANS = {1: "RT", 2: "RA", 3: "RD", 4: "SY", 5: "TB", 6: "CB", 7: "WA", 8: "WD",
                  9: "WT", 10: "RY"}  # Jayco B-Van V13 (MCF 13.6) only

    TANK_DGN = 0x0BFC1  # G12_TANK_LEVEL_SENSOR: instance, raw level u16 (1 fresh,
    #                     2 gray, 3 black, 4 gray 2); % via thresholds CC/CD/CE
    # The panel's tank gauges read standard TANK_STATUS (1FFB7: 0 fresh, 1 black,
    # 2 gray, 18 gray 2), so the G12 is modelled as relaying its raw sensors
    # through the thresholds, bucketed 0/33/67/100 % of resolution 100 (the real G12, `emu.g12`,
    # sends the bucket 0-3 of resolution 3 instead). Raw ADC counts, HIGH = EMPTY
    # (BUS-BASELINE PC.10: 0% <- CC 54000 -> 33% <- CD 40000 -> 67% <- CE 25000 -> 100%).
    # The boundary rule (a reading AT a threshold is the emptier bucket) is the one the
    # G12's own bucketer gives when the MCF's G12 blob runs (`emu.g12.G12`, arms
    # +0x1858.. `cmp r0,r7`; tests/emu/test_g12.py::test_bucketer_boundaries) from a cold
    # start; the blob's hysteresis between buckets is not modelled here.
    # The raw values show on HOME > hold a tank gauge ~5.5 s (PROBE TANK DIAGNOSTICS).
    TANK_STATUS_INSTANCE = {1: 0, 3: 1, 2: 2, 4: 18}
    # The coach's thresholds (BUS-BASELINE PC.10; G12-CODE "IDENTIFIED -- they are the TANK
    # LEVEL THRESHOLDS"), the defaults until a write or a stored selector says otherwise.
    TANK_THRESHOLDS = {0xCC: 54000, 0xCD: 40000, 0xCE: 25000}
    # One 0BFC1 and one 1FFB7 per tank per second (BUS-BASELINE PC.62: "four instances x 1 Hz").
    TANK_PERIOD_S = 1.0

    def __init__(self, selectors=None, path=None, tanks=None):
        self.sel = {0xF1: 0x0D, 0xF2: 0x06, 0xF5: 0}
        self.tanks = dict(tanks or {})
        self.path = path
        if path:
            import json
            import os

            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    self.sel.update({int(k, 16): v for k, v in json.load(f).items()})
        if selectors:
            for k, v in selectors.items():
                self.sel.setdefault(k, v)
        for k, v in self.TANK_THRESHOLDS.items():    # stored, then given, then the coach's
            self.sel.setdefault(k, v)
        self._sweep = 0

    def attach(self, bus):
        super().attach(bus)
        self.every(1.0, self._heartbeat)
        self.every(0.1, self._next_sweep)
        for i, inst in enumerate(self.tanks):
            self.every(self.TANK_PERIOD_S, lambda inst=inst: (self.TANK_DGN,
                                               [inst, *int(self.tanks[inst]).to_bytes(2, "little")]),
                       phase=0.05 * i)
            if inst in self.TANK_STATUS_INSTANCE:
                self.every(self.TANK_PERIOD_S, lambda inst=inst: (rvc.TANK_STATUS, rvc.tank_status(
                    self.TANK_STATUS_INSTANCE[inst], self.tank_percent(inst), 100)),
                           phase=0.05 * i + 0.02)

    def tank_percent(self, inst):
        raw = self.tanks[inst]
        steps = [self.sel.get(k) for k in (0xCC, 0xCD, 0xCE)]
        if None in steps:
            return 0xFF
        return (0, 33, 67, 100)[sum(raw < t for t in steps)]    # high raw = empty

    def _heartbeat(self):
        g = self.sel.get
        return self.HEARTBEAT_DGN, [0xFF, 0xFF, g(0xF1, 0xFF), 0xFF, 0xFF, g(0xF5, 0xFF),
                                    0xFF, g(0xF2, 0xFF)]

    def _report(self, sel):
        v = self.sel.get(sel, 0xFFFF) & 0xFFFF
        return [sel, 0x00, v & 0xFF, v >> 8, 0, 0, 0, 0]

    def _next_sweep(self):
        keys = sorted(self.sel)
        sel = keys[self._sweep % len(keys)]
        self._sweep += 1
        return self.CONFIG_DGN, self._report(sel)

    def _save(self):
        if self.path:
            import json

            json.dump({f"{k:02X}": v for k, v in sorted(self.sel.items())},
                      open(self.path, "w", encoding="utf-8"),
                      indent=1)

    def on_frame(self, frame):
        _, dgn, _ = rvc.split_id(frame.can_id)
        d = frame.data
        if dgn != self.WRITE_DGN or len(d) < 7 or d[1] != 0x96:
            return
        sel, func = d[2], d[6]
        if func in (0xD1, 0xD2):
            value = d[4] | d[5] << 8
            if func == 0xD2 or sel in self.DELTA:
                delta = value - 0x10000 if value & 0x8000 else value
                value = (self.sel.get(sel, 0) + delta) & 0xFFFF
            self.sel[sel] = value
            name = f" ({self.FLOORPLANS.get(value, '?')})" if sel == 0xF5 else ""
            log.info("G12 set %02X = %d%s", sel, value, name)
            self._save()
        elif func != 0xD3:
            return
        self.send(self.CONFIG_DGN, self._report(sel))


class Inverter(Peer):
    """RV-C inverter obeying INVERTER_COMMAND (1FFD3), reporting INVERTER_STATUS
    (1FFD4) plus AC (1FFD7) and DC (1FEBD) status.

    GuiCode binds up to two inverters by the source address of the first
    1FFD4 for instance 1/2 (FUN_0024f1fc) and toggles them from status byte 1
    (FUN_0024f6d8: status 0 -> send 0x11 enable, else 0x10).
    """

    sa = 0x42
    STATUS, COMMAND, AC_STATUS, DC_STATUS = 0x1FFD4, 0x1FFD3, 0x1FFD7, 0x1FEBD

    def __init__(self, instance=1, enabled=False, ac_volts=120.0, dc_volts=13.2):
        self.instance = instance
        self.enabled = enabled
        self.passthru = True
        self.ac_volts, self.dc_volts = ac_volts, dc_volts

    def attach(self, bus):
        super().attach(bus)
        self.every(1.0, self._status)
        self.every(1.0, self._ac, phase=0.3)
        self.every(1.0, self._dc, phase=0.6)

    def _status(self):
        flags = (0x10 if self.enabled else 0) | (0x40 if self.passthru else 0)
        return self.STATUS, [self.instance, 1 if self.enabled else 0, flags, 0xFC]

    def _ac(self):
        volts = rvc.volts(self.ac_volts if self.enabled else 0)
        return self.AC_STATUS, [self.instance, *volts.to_bytes(2, "little"),
                                *(32000).to_bytes(2, "little"), *(60 * 128).to_bytes(2, "little")]

    def _dc(self):
        return self.DC_STATUS, [self.instance, *rvc.volts(self.dc_volts).to_bytes(2, "little"),
                                *(32000).to_bytes(2, "little")]

    def on_frame(self, frame):
        _, dgn, _ = rvc.split_id(frame.can_id)
        if dgn != self.COMMAND or not frame.data or frame.data[0] != self.instance:
            return
        cmd = frame.data[1]
        self.enabled = cmd & 3 == 1
        self.passthru = (cmd >> 4) & 3 == 1
        log.info("inverter %d %s", self.instance, "enabled" if self.enabled else "disabled")
        self.send(*self._status())


class SolarController(Peer):
    """Go Power!-style RV-C solar charge controller (DGN layouts per rvc-spec.yml).

    The ELECTRICAL page's SOLAR box only updates once it has seen 1FDFF (array
    status) together with one of 1FE82 / 1FE83 / 1FE85.
    """

    sa = 0x52
    ARRAY, BATTERY, STATUS_6, STATUS_5, STATUS_4, STATUS_2 = (
        0x1FDFF, 0x1FE80, 0x1FE81, 0x1FE82, 0x1FE83, 0x1FE85)

    def __init__(self, instance=1, array_volts=18.5, array_amps=6.5, battery_volts=13.4):
        self.instance = instance
        self.array_volts, self.array_amps, self.battery_volts = array_volts, array_amps, battery_volts

    def attach(self, bus):
        super().attach(bus)
        u16 = lambda v: list(int(v).to_bytes(2, "little"))  # noqa: E731
        i = self.instance
        self.every(1.0, lambda: (self.ARRAY, [i, *u16(rvc.volts(self.array_volts)),
                                              *u16(rvc.amps16(self.array_amps))]))
        self.every(1.0, lambda: (self.BATTERY, [i, 0xFF, 0xFF, *u16(rvc.volts(self.battery_volts)),
                                                *u16(rvc.amps16(self.array_amps * 1.3)), 25 + 40]),
                   phase=0.2)
        self.every(5.0, lambda: (self.STATUS_4, [i, *u16(42), *u16(55), *u16(38)]), phase=0.4)
        self.every(5.0, lambda: (self.STATUS_5, [i, *u16(310), *(1234).to_bytes(4, "little")]),
                   phase=0.6)
        self.every(5.0, lambda: (self.STATUS_6, [i, *u16(400), *u16(273 + 30)]), phase=0.8)
        self.every(10.0, lambda: (self.STATUS_2, [i, *u16(rvc.volts(12)), *u16(rvc.amps16(30)),
                                                  0xFF, 0xFF, 0xFF]), phase=0.9)


class APS500(Peer):
    """Alternator regulator acting as the coach's virtual BMS (source 0x80).

    Per the owner: the AES page's "Battery Voltage" comes from this device. The
    panel reads DC_SOURCE_STATUS_1 (1FFFD) instance 1 for it (any source address;
    instance 0x81 does not work). Also broadcasts DC_SOURCE_STATUS_2 (SOC) and
    J1939 Alternator Information 1 (0FED5), layouts per rvc-spec.yml, and the
    coach's DM_RV every 5 s. 14.7 only counts a SOC from a source whose DM_RV
    operating status is non-zero; without it the panel relays 0xDA = FFFF.
    """

    sa = 0x80
    ALT_INFO = 0x0FED5
    DC_SOURCE_STATUS_2 = 0x1FFFC

    def __init__(self, volts=52.8, amps=12.5, soc=85.0, temp_c=24.0, alt_rpm=0):
        self.volts, self.amps, self.soc, self.temp_c, self.alt_rpm = volts, amps, soc, temp_c, alt_rpm

    def attach(self, bus):
        super().attach(bus)
        self.every(0.5, lambda: (rvc.DC_SOURCE_STATUS_1,
                                 rvc.dc_source_status_1(1, self.volts, self.amps, priority=120)))
        self.every(1.0, lambda: (self.DC_SOURCE_STATUS_2,
                                 [1, 120, *rvc.kelvin_u16(self.temp_c).to_bytes(2, "little"),
                                  min(200, round(self.soc * 2)), 0xFF, 0xFF, 0xFF]), phase=0.25)
        self.every(5.0, lambda: (rvc.DM_RV, [0x05, 0x4C, 0xFF, 0xFF, 0xFF, 0xFF, 0x00, 0xFF]),
                   phase=1.0)
        self.every(1.0, lambda: (self.ALT_INFO,
                                 [*round(self.alt_rpm / 0.125).to_bytes(2, "little"), 0x00]),
                   phase=0.5)


class Timberline(Peer):
    """Elwell Timberline 1.5 hydronic heater controller: water heater, thermostat, circulation
    pump and Elwell's proprietary 1EF65 messages, all instance 1, from source 0x65.

    **What it sends** is a coach's own unit, from a 90 s receive-only capture of source 0x65
    (2026-11-18, heater idle) and rvc2mqtt's `entity/timberline.py` for the byte meanings:

    - one 4.992 s cycle of 12 slots 416 ms apart: 1FFF7 WATERHEATER_STATUS, 1FE99
      WATERHEATER_STATUS_2, 1FFE2 THERMOSTAT_STATUS_1, 1FEFA THERMOSTAT_STATUS_2, two empty
      slots, 1FE97 CIRCULATION_PUMP_STATUS, 1FF9C THERMOSTAT_AMBIENT_STATUS, 1EF65 0x84, 1EF65
      0x8A, 1FECA DM_RV, 1FFE4 FURNACE_STATUS (the capture lacks DM_RV in 3 of 18 cycles and
      1FFE4 in 1; why is not known, and the model always sends them);
    - 1EF65 0x85 (timers) every 1.000 s, on its own clock;
    - 1EF65 0x87, 0x88, 0x86 (panel, HCU and heater info) in turn every 20.0 s, on a third
      clock, so each once a minute.

    Idle, every frame is the captured one byte for byte.

    **What it obeys** (rvc2mqtt's command builders): 1FFF6 WATERHEATER_COMMAND byte 1 = 0 off,
    1 combustion, 2 electric, 3 both; 1FEF9 THERMOSTAT_COMMAND_1 byte 1 = schedule << 6 |
    fan << 4 | mode (all-ones fields mean no change), bytes 3-4 the heat setpoint (clamped
    10..32 C as rvc2mqtt clamps it); 1EF65 0x83 byte 1 bits 0-1 hot water priority (00 heating,
    01 domestic water: the inverse of 1FE99's encoding) and bits 2-3 the temperature sensor;
    1FE96 CIRCULATION_PUMP_COMMAND byte 1 = output status.

    **On change** (two captures of can1, receive only, 2026-11-18, the element switched each way):
    a 1FFF6 command from another node (`19FFF682#0102FF..`, `#0100FF..`) was answered by ONE
    1FFF7 carrying the new mode 1 ms and 10 ms later, with no ACK; a change on the unit's own
    panel (no RV-C command) by TWO, 85-90 ms apart. Either way the frames sit outside the cycle,
    whose phase does not move, and byte 6's burner/element bits in them are the last slot's; the
    new bits follow at the next periodic slot. Nothing else left its slot. The model does both:
    a 1FFF6 command reports once `apply_s` (default 5 ms) after it, `panel_set_mode` twice.
    `report_on_change` False reports a change only at the next slot.

    **Not measured, so modelled:** whether thermostat, hot water or pump commands report on change
    (here they do not); combustion and "both" (only the element was switched on the coach).
    - `ignition_s` / `element_s`: from a mode that includes combustion / electric to 1FFF7
      byte 6's burner (bits 2-3) / AC element (bits 4-5) reading 1. The model always has demand,
      so a selected source runs; the real unit cycles it on its own thermostat.

    `log` lists every command applied, `(seconds, name, payload hex)`.
    """

    sa = 0x65
    WH_STATUS, WH_STATUS_2, WH_COMMAND = 0x1FFF7, 0x1FE99, 0x1FFF6
    TH_STATUS_1, TH_STATUS_2, TH_COMMAND = 0x1FFE2, 0x1FEFA, 0x1FEF9
    PUMP_STATUS, PUMP_COMMAND = 0x1FE97, 0x1FE96
    AMBIENT, FURNACE_STATUS, PROPRIETARY = 0x1FF9C, 0x1FFE4, 0x1EF65
    CYCLE_S, SLOTS, TIMER_S, INFO_S = 4.992, 12, 1.000, 20.0
    REPEAT_S = 0.086                 # a panel change's second 1FFF7 copy (85-90 ms captured)
    INSTANCE = 1
    SETPOINT_MIN_C, SETPOINT_MAX_C = 10.0, 32.0

    def __init__(self, *, mode=0, report_on_change=True, apply_s=0.005, ignition_s=15.0,
                 element_s=2.0):
        self.mode = mode                 # 1FFF7 byte 1
        self.th_mode, self.th_schedule = 2, 0        # 1FFE2 byte 1: heat, schedule disabled
        self.setpoint = 0x24A0           # 20.0 C
        self.ambient = 0x22C6
        self.wh_setpoint, self.water = 0x2D60, 0x2353
        self.tank, self.heater = 0x2416, 0x2320
        self.priority = 1                # 1FE99 encoding: 01 heating priority (the capture's)
        self.sensor = 0                  # 0x84 bits 2-3: external
        self.circ_pump = 0     # not `pump`: CanBus calls a peer's `pump(now)`
        self.minutes = 0x001616          # 0x87 bytes 1-3
        self.report_on_change = report_on_change
        self.apply_s, self.ignition_s, self.element_s = apply_s, ignition_s, element_s
        self._since = {1: None, 2: None}  # when combustion / electric was last selected
        self._info_k = 0
        self._byte6 = 0xC3               # 1FFF7 byte 6 as the last periodic slot sent it
        self.log = []

    # -- time -----------------------------------------------------------------------------
    def _now(self):
        return self.bus.m.cycles / self.bus.m.cpu_hz

    def _later(self, seconds, fn):
        """Run `fn()` once, `seconds` from now (a one-shot `every`); it may return a frame."""
        done = []

        def once():
            if done:
                return None
            done.append(1)
            return fn()
        self.every(1e6, once, phase=seconds)

    def attach(self, bus):
        super().attach(bus)
        if self.mode:
            for bit in (1, 2):
                if self.mode & bit:
                    self._since[bit] = -1e9   # running since before boot
        slot = self.CYCLE_S / self.SLOTS
        sched = [self._wh_status, self._wh_status_2, self._th_status_1, self._th_status_2,
                 None, None, self._pump_status, self._ambient,
                 self._ext_status, self._timer_setup, self._dm_rv, self._furnace]
        for k, fn in enumerate(sched):
            if fn is not None:
                self.every(self.CYCLE_S, fn, phase=k * slot)
        self.every(self.TIMER_S, lambda: (self.PROPRIETARY, [0x85] + [0] * 7), phase=0.66)
        self.every(self.INFO_S, self._info, phase=11.56)    # the capture's offset from 1FFF7

    # -- status ---------------------------------------------------------------------------
    def _running(self, bit, delay):
        t = self._since[bit]
        return self.mode & bit and t is not None and self._now() - t >= delay

    def _wh_status(self):
        burner = 1 if self._running(1, self.ignition_s) else 0
        element = 1 if self._running(2, self.element_s) else 0
        self._byte6 = 0xC3 | burner << 2 | element << 4
        return self._wh_frame()

    def _wh_frame(self):
        """1FFF7 now, with byte 6 as the last periodic slot left it (the on-change copies)."""
        return self.WH_STATUS, [self.INSTANCE, self.mode, *self._u16(self.wh_setpoint),
                                *self._u16(self.water), self._byte6, 0x00]

    def _wh_status_2(self):
        return self.WH_STATUS_2, [self.INSTANCE, 0xFF, 0x3F | self.priority << 6] + [0xFF] * 5

    def _th_status_1(self):
        return self.TH_STATUS_1, [self.INSTANCE, self.th_schedule << 6 | 0x30 | self.th_mode,
                                  0xFF, *self._u16(self.setpoint), 0xFF, 0xFF, 0xFF]

    def _th_status_2(self):
        return self.TH_STATUS_2, [self.INSTANCE, 0x00, 0x02] + [0xFF] * 5

    def _info(self):
        k, self._info_k = self._info_k, (self._info_k + 1) % 3
        if k == 0:
            self.minutes += 1
            return self.PROPRIETARY, [0x87, *self.minutes.to_bytes(3, "little"),
                                      0x09, 0x00, 0x64, 0x22]
        if k == 1:
            return self.PROPRIETARY, [0x88, 0xFF, 0xFF, 0xFF, 0x1A, 0x00, 0x01, 0x16]
        return self.PROPRIETARY, [0x86, 0x91, 0x0D, 0x00, 0x1B, 0x01, 0x02, 0x09]

    def _pump_status(self):
        return self.PUMP_STATUS, [self.INSTANCE, 0xF0 | self.circ_pump] + [0xFF] * 6

    def _ambient(self):
        return self.AMBIENT, [self.INSTANCE, *self._u16(self.ambient)] + [0xFF] * 5

    def _ext_status(self):
        solenoid = 1 if self._running(1, 0.0) else 0
        return self.PROPRIETARY, [0x84, 0xF0 | self.sensor << 2 | solenoid, *self._u16(self.tank),
                                  *self._u16(self.heater), 0xBA, 0xFF]

    def _timer_setup(self):
        return self.PROPRIETARY, [0x8A, 0x20, 0x1C, 0x1E, 0xFF, 0xFF, 0xFF, 0xFF]

    def _dm_rv(self):
        return rvc.DM_RV, [0x01, 0x65] + [0xFF] * 6

    def _furnace(self):
        return self.FURNACE_STATUS, [self.INSTANCE, 0xFC, 0x00] + [0xFF] * 5

    @staticmethod
    def _u16(v):
        return [v & 0xFF, v >> 8 & 0xFF]

    # -- commands -------------------------------------------------------------------------
    def on_frame(self, frame):
        _, dgn, _ = rvc.split_id(frame.can_id)
        d = bytes(frame.data)
        if len(d) < 2:
            return
        if dgn == self.WH_COMMAND and d[0] == self.INSTANCE and d[1] <= 3:
            self._command("1FFF6", d, lambda: self._set_mode(d[1], copies=1))
        elif dgn == self.TH_COMMAND and d[0] == self.INSTANCE and len(d) >= 5:
            self._command("1FEF9", d, lambda: self._thermostat(d))
        elif dgn == self.PROPRIETARY and d[0] == 0x83:
            self._command("1EF65", d, lambda: self._extension(d[1]))
        elif dgn == self.PUMP_COMMAND and d[0] == self.INSTANCE and d[1] in (0, 1, 5):
            self._command("1FE96", d, lambda: setattr(self, "circ_pump", d[1]))

    def _command(self, name, d, apply):
        self.log.append((round(self._now(), 3), name, d.hex()))
        log.info("timberline %s %s", name, d.hex())
        if self.apply_s <= 0:
            apply()
        else:
            self._later(self.apply_s, lambda: apply() and None)

    def panel_set_mode(self, mode):
        """The mode changed on the unit's own panel: no RV-C command, two on-change reports."""
        self._set_mode(mode, copies=2)

    def _set_mode(self, mode, copies):
        if mode != self.mode and self.report_on_change:
            for k in range(copies):
                self._later(k * self.REPEAT_S, self._wh_frame)
        for bit in (1, 2):
            if mode & bit and not self.mode & bit:
                self._since[bit] = self._now()
            elif not mode & bit:
                self._since[bit] = None
        self.mode = mode

    def _thermostat(self, d):
        if d[1] & 0x0F != 0x0F:
            self.th_mode = d[1] & 0x0F
        if d[1] >> 6 != 3:
            self.th_schedule = d[1] >> 6
        sp = d[3] | d[4] << 8
        if sp != 0xFFFF:
            lo, hi = rvc.kelvin_u16(self.SETPOINT_MIN_C), rvc.kelvin_u16(self.SETPOINT_MAX_C)
            self.setpoint = max(lo, min(hi, sp))

    def _extension(self, b):
        if b & 3 != 3:
            self.priority = 0 if b & 3 == 1 else 1   # 0x83's 01 (domestic) is 1FE99's 00
        if b >> 2 & 3 != 3:
            self.sensor = b >> 2 & 3


# Illustrative AES/AGS parameters for the demo G12 (0.05 V units for volts). Only
# 0D = 54.40 V is a value quoted from the coach (rvc-spec.yml); the rest are
# placeholders inside the ranges rvc2mqtt treats as sane.
DEMO_G12_SELECTORS = {
    0x31: rvc.volts(50.0),   # start at voltage
    0x0C: 60,                # time at start volts (s)
    0x0D: rvc.volts(54.4),   # stop at voltage
    0x0E: 900,               # time at stop volts (s)
    0x16: 90,                # max engine run time (min)
    0x2B: 0 | 22 << 8,       # quiet time start 22:00 (byte2 = min, byte3 = hr)
    0x2C: 0 | 7 << 8,        # quiet time stop 07:00
    0x0A: rvc.volts(52.8),   # measured battery voltage
    0x05: 0,                 # AES state: disabled
    # tank thresholds, raw ADC counts, high = empty: 0 % / 33 % / 67 % / 100 % boundaries
    **ProtocolG12.TANK_THRESHOLDS,       # the coach's 54000 / 40000 / 25000 (PC.10)
}
# Raw sensor values (high = empty; buckets at CC/CD/CE = 54000/40000/25000), for both G12
# models: fresh full, grey 2/3, black 1/3, grey 2 empty.
DEMO_TANKS = {1: 20000, 2: 30000, 3: 45000, 4: 60000}
DEMO_G12_INPUTS = {"tanks": dict(DEMO_TANKS),
                   "house": 13.1, "chassis": 12.6, "ambient": 21.0, "lpg": 80}
# The reference coach: floorplan WD, AES mode (EF = 3).  The G12 logic's own
# mode-change handler then applies the AES profile on its first tick.
DEMO_G12_CELLS = {0xF5: 8, 0xEF: 3}


def demo_network(bus, guidata, g12_version=None, g12_path=None, ambient=None):
    """A plausible coach: G12 logic controller (running the MCF's G12 logic when
    possible; it is also the clock, battery-voltage and ambient-temperature
    source), every dimmer channel the GUI knows, inverter, solar, APS-500."""
    from .g12 import from_mcf
    from .guidata import data_points

    dimmers = sorted({p.match_value for p in data_points(guidata)
                      if p.pgn == rvc.DC_DIMMER_STATUS_3 and p.match_value != 0xFF})
    mcf_data = getattr(bus.m, "mcf_data", None)
    # The tanks dict is copied too: a caller that moves a sensor (Scenario.set_tank_raw) must
    # not move it for every later network built in the same process.
    inputs = dict(DEMO_G12_INPUTS, tanks=dict(DEMO_G12_INPUTS["tanks"]),
                  **({} if ambient is None else {"ambient": ambient}))
    g12 = from_mcf(mcf_data, path=g12_path, inputs=inputs,
                   cells=DEMO_G12_CELLS) if mcf_data else None
    if g12 is None:
        bus.add_peer(Clock())
        bus.add_peer(Thermostats({0: 21.0 if ambient is None else ambient}))
        bus.add_peer(FireflyBatteries())
        g12 = ProtocolG12(DEMO_G12_SELECTORS, path=g12_path, tanks=DEMO_TANKS)
    else:  # the G12 drives outputs 1-44 itself
        dimmers = [i for i in dimmers if not g12.outputs.owns(i)]
    bus.add_peer(DimmerModule(dimmers))
    bus.add_peer(g12)
    bus.add_peer(Inverter())
    bus.add_peer(SolarController())
    bus.add_peer(APS500())
    log.info("demo network: %s, inverter, solar, APS-500 (0x80), %d dimmer channels",
             type(g12).__name__, len(dimmers))
