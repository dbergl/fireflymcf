"""Firefly G12 logic controller driven by the logic shipped in the MCF's G12 section.

The G12 section (container part 3) carries the coach-specific program the G12
runs (decoded by fireflymcf: docs/G12-CODE.md and ``fireflymcf.g12rules``):

* table 0x67, a position-independent Thumb-1 blob with two entry points: init
  (+0x0C: defaults for unset config cells, boot broadcasts) and the tick
  (header word +0x08, every 10 ms: AES/AGS timers and clamps, mode switch, interlocks,
  quiet hours, the tank bucketer, rule gating).  It is called with r0 = a
  context struct (ABI 13; 17 in G12Logic.LAYOUTS) ``{u32 cells; u16 count; u8 ?; u8 extra_ticks;
  fn activate(n); fn deactivate(n); fn is_active(n); fn log(str);
  fn send(record)}`` -- the last sends a 12-byte frame record built from the
  blob's own frame table (awning interlock, channel 18 OFF);
* table 0x62, 12-byte RV-C transmit templates that the blob activates by
  index; masked payload bytes name config cells whose value is substituted;
* table 0x61, the G12 application's 1 Hz cell broadcasts and the frames it
  copies into cells.

``G12Logic`` executes the blob in its own Unicorn instance.  ``G12`` is the
bus node (SA 0x9C) around it: it supplies what the G12 application does and
the MCF does not describe -- the 1FED9 config protocol, NVM, the rule
scheduler, sensors, the clock -- modelled on the coach captures
(docs/BUS-BASELINE.md).  Rule repeat behaviour per (table, kind)
follows ``fireflymcf.g12rules.KIND_OBSERVED``.
"""
import ctypes
import hashlib
import heapq
import json
import logging
import os
import struct
import time

from unicorn import UC_ARCH_ARM, UC_HOOK_INTR, UC_MODE_MCLASS, UC_MODE_THUMB, UC_PROT_ALL, Uc
from unicorn.arm_const import (
    UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_SP, UC_CPU_ARM_CORTEX_M4,
)

from .. import g12code, g12rules
from . import rvc
from .canbus import Peer
from .g12out import G12Outputs
from .g12thermo import COMMAND as THERMOSTAT_COMMAND, G12Thermostat

log = logging.getLogger("g12")


class G12Error(RuntimeError):
    pass


class G12Logic:
    """The G12 section's code blob on an emulated Cortex-M4 (the G12 is an STM32L43x)."""

    BASE = 0x08038AE4  # config LdAddr 0x08038000 + the blob's table offset (inferred; PIC anyway)
    CTX, STUBS, RET, CELLS, STACK = 0x20000000, 0x20000100, 0x20000300, 0x20001000, 0x2000F000
    COUNT = 0x200      # cells: config 0x00-0xFF, runtime above, timer bank at the top
    INIT = 0x0C
    MAX_INSNS = 2_000_000

    # Header word +0x04 versions the app<->blob context layout (docs/G12-CODE.md):
    # the callback slots per version, each called with r0 = argument.
    # Both read byte +0x07 (missed ticks, 0 here) in the tick.  13 (11.23, 13.6):
    # log(str) is 13.6's one "G8 Test" call in init.  17 (14.7) drops it and adds
    # button(n) -> u16, the state of push-button input n (0 idle, 1 pressed,
    # 2 tapped, 3 held, 4 released after a hold), which the blob turns into
    # 1FEDB toggle/ramp/stop and a 1FFB8 status, replacing 13.6's table-0x20 input
    # rules; and notify(value), called with three just-defaulted cells (0x0E,
    # 0x0F, 0x16), result unused.
    LAYOUTS = {
        13: {0x08: "activate", 0x0C: "deactivate", 0x10: "is_active", 0x14: "log",
             0x18: "send"},
        17: {0x08: "activate", 0x0C: "deactivate", 0x10: "is_active", 0x18: "send",
             0x24: "button", 0x40: "notify"},
    }
    SLOTS = range(0x08, 0x48, 4)   # every word after the header is a callable stub

    def __init__(self, blob, callbacks):
        """``callbacks``: name -> function(arg); missing names are no-ops returning 0.
        A slot outside the build's layout raises if the blob ever calls it."""
        self.abi = struct.unpack_from("<I", blob, 4)[0]
        layout = self.LAYOUTS.get(self.abi)
        if layout is None:
            raise G12Error(f"G12 code context ABI {self.abi} is not supported "
                           f"(only {sorted(self.LAYOUTS)})")
        self.layout = layout
        self.callbacks = {name: callbacks.get(name) or (lambda arg: 0)
                          for name in layout.values()}
        self.tick_entry = struct.unpack_from("<I", blob, 8)[0] & ~1
        uc = self.uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        uc.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M4)
        page = self.BASE & ~0xFFF
        uc.mem_map(page, (self.BASE - page + len(blob) + 0xFFF) & ~0xFFF)
        uc.mem_write(self.BASE, blob)
        self._ram = (ctypes.c_ubyte * 0x10000)()
        uc.mem_map_ptr(0x20000000, 0x10000, UC_PROT_ALL, ctypes.addressof(self._ram))
        off = self.CELLS - 0x20000000
        self.cells = memoryview(self._ram).cast("B")[off:off + 2 * self.COUNT].cast("H")
        uc.mem_write(self.CTX, struct.pack("<IHBB", self.CELLS, self.COUNT, 0, 0))
        for slot in self.SLOTS:                # slot stub = `svc #slot; bx lr`
            stub = self.STUBS + slot
            uc.mem_write(stub, struct.pack("<HH", 0xDF00 | slot, 0x4770))
            uc.mem_write(self.CTX + slot, struct.pack("<I", stub | 1))
        uc.mem_write(self.RET, struct.pack("<H", 0xE7FE))
        for i in range(self.COUNT):
            self.cells[i] = 0xFFFF             # fresh NVM: every cell unset
        uc.hook_add(UC_HOOK_INTR, self._svc)
        self._error = None

    def _svc(self, uc, intno, user):
        try:
            if intno != 2:
                raise G12Error(f"G12 code raised exception {intno} at "
                               f"0x{uc.reg_read(UC_ARM_REG_PC):08x}")
            slot = struct.unpack("<H", uc.mem_read(uc.reg_read(UC_ARM_REG_PC) - 2, 2))[0] & 0xFF
            name = self.layout.get(slot)
            if name is None:
                raise G12Error(f"G12 code (ABI {self.abi}) called unmapped context slot "
                               f"+0x{slot:02x}")
            arg = uc.reg_read(UC_ARM_REG_R0)
            if name == "log":
                arg = bytes(uc.mem_read(arg, 64)).split(b"\0")[0].decode("latin-1")
            elif name == "send":
                arg = bytes(uc.mem_read(arg, 12))
            ret = self.callbacks[name](arg)
            if name == "is_active":
                ret = int(bool(ret))
            elif name != "button":
                ret = 0
            uc.reg_write(UC_ARM_REG_R0, ret & 0xFFFFFFFF)
        except Exception as e:
            self._error = e
            uc.emu_stop()

    def _call(self, entry):
        uc = self.uc
        uc.reg_write(UC_ARM_REG_SP, self.STACK)
        uc.reg_write(UC_ARM_REG_R0, self.CTX)
        uc.reg_write(UC_ARM_REG_LR, self.RET | 1)
        uc.emu_start((self.BASE + entry) | 1, self.RET, count=self.MAX_INSNS)
        if self._error is not None:
            err, self._error = self._error, None
            raise err
        if uc.reg_read(UC_ARM_REG_PC) != self.RET:
            raise G12Error(f"G12 code did not return (pc=0x{uc.reg_read(UC_ARM_REG_PC):08x})")

    def init(self):
        self._call(self.INIT)

    def tick(self):
        self._call(self.tick_entry)


# How the G12 sends a table-0x62 record once the blob activates it, per kind
# (fireflymcf g12rules.KIND_OBSERVED, from the coach captures).
PERIODIC_1S = {0x80}           # e.g. TANK_STATUS, re-sent every second while active
PERIODIC_5S = {0xC0, 0xCC}     # 15FCE sweep + 1FACF; A/C program ON frames
TRIPLE = {0x40}                # sent 3x at 250 ms
PAIRED = {0x24, 0x28}          # the A/C fan pairs: activating one sends both (pc8, pc37)

# Table-0x61 broadcasts: the bytes around the cell value, as on the coach
# (pc23-baseline).  Byte 0 is the record's instance unless its range is FF.
STATUS_TEMPLATE = {
    0x1AAFD: bytes.fromhex("01ff000000000000"),   # DC voltage, u16 0.05 V at byte 2
    0x0BFC1: bytes.fromhex("0000000000000000"),   # raw tank sensor, u16 at byte 1
    0x1FF9C: bytes.fromhex("0000000000000000"),   # THERMOSTAT_AMBIENT_STATUS
    0x1AADC: bytes.fromhex("0000000000ffffff"),   # generator status
    0x1AAB7: bytes.fromhex("030064ffffffffff"),   # Firefly LPG level mirror
}
# Read by kind as a broadcast, but the coach's G12 never sends it, and its AES
# measured voltage (cell 0x0A, fed from 0x55) equals the APS-500's 1FFFD
# instance 1 exactly (pc23: 1077 = 53.85 V): it is a receive into the cell.
STATUS_RECEIVE = {0x1FFFD}

# G12 application traffic the MCF does not describe, as captured from the coach
# (pc23-baseline): (period s, phase s, DGN, payload, priority).
APP_PERIODIC = [
    (5.0, 2.008, rvc.DM_RV, "05841f16ffffffff", 6),
    (5.0, 2.026, 0x17EFE, "303a6e755278614d", 7),
]
# Answers to the panel's broadcast requests (every 14 s on the coach).
APP_RESPONSES = {
    0x1FED8: [(0x1FED8, "25b11f3006163412"), (0x0FF01, "00011f010300640a"),
              (0x0FF01, "00021f01ffffff07"), (0x0FF01, "00ff1f010203ff06"),
              (0x0FF01, "01011f0103006206")],
}
PRODUCT_ID = b"Spyder Controls*G12 Panel 2v2 3v0d*0000000*22*"   # 0FEEB, sent by BAM


class G12(Peer):
    """The G12 node: runs the MCF's G12 logic and speaks RV-C as SA 0x9C.

    ``inputs`` are the simulated sensors: ``tanks`` {sensor 1-4: raw ADC value,
    high = empty; 1 fresh, 2 grey, 3 black, 4 grey 2}, ``house``/``chassis``
    volts, ``ambient`` deg C, ``lpg`` level %, ``pins`` {input pin: active}
    (pin 10 = ignition sense).  ``cells`` seeds config cells not yet stored.

    Every frame the G12 sends also goes through its own receive path, as on the
    coach: its 1FED9 rules set its own cells and its ignition broadcast (1FBDA
    instance FB) feeds cell 0x28 four milliseconds before the awning interlock
    reacts (pc28).
    """

    sa = 0x9C
    CONFIG_DGN, WRITE_DGN = 0x15FCE, 0x1FED9
    # Selectors that take a signed delta even under D1 (rvc-spec.yml; PC.11/PC.23).
    DELTA = {0xCC, 0xCD, 0xCE, 0x82, 0x86, 0x88, 0x92, 0x99}
    NVM_CELLS = range(0x100)
    TANK_CELL, HOUSE_CELL, AMBIENT_CELL, CLOCK_CELL, LPG_CELL = 0xB0, 0x56, 0x6B, 0x2A, 0xC2
    # The application calls the blob's tick every 10 ms: init arms cell[count-1]
    # = 100 and the tick counts it down, advancing the 16-timer bank once per
    # wrap (13.6 +0x0968, 14.7 +0x1FA2); 14.7 re-sends a held button's ramp when
    # its 300-tick counter is a multiple of 100.  It is why the coach relays a
    # config write within 2-10 ms (PC.23), locks the awning 4 ms after ignition
    # off (pc28) and clears BF 714 / 343 ms after setting it (pc24 / pc39).
    TICK = 0.01
    # Input pins: ON frames repeat every 1.000 s from the ON edge and taps up to
    # 872 ms never repeat (PC.24), so "held" is taken to begin at 1 s.
    HOLD = 1.0
    PRESS, TAP_RELEASE, HELD, HOLD_RELEASE = 1, 2, 4, 8
    # ABI 17 (14.7): the blob polls each pin as button(pin - 1) instead of the
    # application firing table-0x20 rules; the same phases as its states.  Release
    # states last two ticks, enough for the blob to see the change.
    BUTTON_STATE = {PRESS: 1, TAP_RELEASE: 2, HELD: 3, HOLD_RELEASE: 4}

    def __init__(self, mcf_data, path=None, inputs=None, cells=None, clock=time.localtime,
                 cold_start=True, output_phase=0.0):
        self.rules = {r.index: r for r in g12rules.all_rules(mcf_data, tags=(0x62,))}
        self.status = [r for r in g12rules.status_rules(mcf_data)]
        blob = g12code.blob(mcf_data)
        self.section_id = hashlib.sha256(blob).hexdigest()[:16]
        self.buttons = {}
        self.logic = G12Logic(blob, {
            "activate": self._activate, "deactivate": self._deactivate,
            "is_active": self._is_active, "send": self._send_record,
            "log": lambda s: log.info("G12 code log: %s", s), "button": self._button})
        self.pin_rules = {e.pin: g12rules.input_group_rules(mcf_data, e.pin)
                          for e in g12rules.input_dispatch(mcf_data) if e.rule_count}
        self._pins = {}            # pin -> [press token, held]
        self._token = 0
        self.inputs = {"tanks": {}, "house": None, "chassis": None, "ambient": None, "lpg": None,
                       "pins": {}}
        self.inputs.update(inputs or {})
        self.clock = clock
        self.cold_start = cold_start   # False: joining a running coach (capture replays)
        # The output driver's 1 s tick (16F84, statuses 0.5 s later) runs on its own
        # phase: 2026-10-22 run 2 has it 0.46 s after the sweep and the blob's 1 s timer
        # bank (BF 00, the 60 s channel-0x73 OFF), which share theirs.
        self.output_phase = output_phase
        self.path = path
        self.active = {}           # rule index -> activation order, for periodic kinds
        self.on = set()            # every rule activated and not deactivated (is_active)
        self._order = 0
        self._timers = []          # heap of (due cycle, seq, fn)
        self._seq = 0
        self.ticks = 0             # 10 ms ticks since attach (timed commands count these)
        self._below = None         # when the auto-start source fell below its threshold
        self._above = None         # when it rose above stop volts during a run
        self._aes = None           # application AES state behind cells 03/05 (_aes_state)
        self.outputs = G12Outputs(self)    # outputs 1-44 (g12out.py)
        self.thermostat = G12Thermostat(self, *([self.inputs["thermostat"]]
                                                if self.inputs.get("thermostat") else []))
        self._load(cells or {})

    # -- persistence (the G12 keeps its config in NVM) ---------------------
    def _load(self, seed):
        stored = {}
        if self.path and os.path.exists(self.path):
            doc = json.load(open(self.path, encoding="utf-8"))
            if "cells" not in doc:     # the protocol-only model's flat {sel: value}
                stored = {int(k, 16): v for k, v in doc.items()
                          if int(k, 16) not in (0xCC, 0xCD, 0xCE, 0x0A)}
                log.info("G12: migrated %d selectors from %s", len(stored), self.path)
            elif doc.get("section") == self.section_id:
                stored = {int(k, 16): v for k, v in doc["cells"].items()}
            else:
                log.warning("G12: %s was saved for another G12 section; starting fresh",
                            self.path)
        for sel, value in {**seed, **stored}.items():
            self.logic.cells[sel] = value & 0xFFFF
        self._saved = self._nvm()

    def _nvm(self):
        return {i: self.logic.cells[i] for i in self.NVM_CELLS if self.logic.cells[i] != 0xFFFF}

    def _save(self):
        nvm = self._nvm()
        if nvm == self._saved:
            return
        self._saved = nvm
        if self.path:
            json.dump({"section": self.section_id,
                       "cells": {f"{k:02X}": v for k, v in sorted(nvm.items())}},
                      open(self.path, "w", encoding="utf-8"), indent=1)

    # -- cells ---------------------------------------------------------------
    def cell(self, sel):
        return self.logic.cells[sel]

    def set_cell(self, sel, value):
        self.logic.cells[sel] = value & 0xFFFF

    # -- scheduling ------------------------------------------------------------
    def attach(self, bus):
        super().attach(bus)
        self._run(self.logic.init)
        self._at(self.TICK, self._tick)
        self._at(1.0, self._second)
        self._at(5.0, self._sweep)
        for period, phase, dgn, data, prio in APP_PERIODIC:
            self._every(period, phase, dgn, bytes.fromhex(data), prio)
        self._at(self.output_phase, self._start_outputs)
        self.thermostat.start(self.cold_start)
        for pin, active in self.inputs["pins"].items():
            self.set_input(pin, active)

    def _at(self, seconds, fn):
        due = self.bus.m.cycles + int(seconds * self.bus.m.cpu_hz)
        self._seq += 1
        heapq.heappush(self._timers, (due, self._seq, fn))
        self.bus.m.reschedule()

    def _every(self, period, phase, dgn, data, prio):
        def fire():
            self._emit(dgn, data, prio)
            self._at(period, fire)

        self._at(phase, fire)

    def pump(self, now):
        while self._timers and self._timers[0][0] <= now:
            _, _, fn = heapq.heappop(self._timers)
            fn()
        return self._timers[0][0] - now if self._timers else None

    def _run(self, entry):
        entry()
        self._save()

    def _tick(self):
        self._at(self.TICK, self._tick)
        self.ticks += 1
        self._run(self.logic.tick)
        self._aes_state()
        self._watchdog()

    def _second(self):
        self._at(1.0, self._second)
        self._feed_inputs()
        for n in sorted(self.active, key=self.active.get):
            if self.rules[n].kind in PERIODIC_1S:
                self._transmit(n)
        self._broadcast_status()

    def _start_outputs(self):
        self.outputs.start()
        self._at(1.0, self._output_second)

    def _output_second(self):
        self._at(1.0, self._output_second)
        self.outputs.second()

    def _sweep(self):
        self._at(5.0, self._sweep)
        for n in sorted(self.active, key=self.active.get):
            if self.rules[n].kind in PERIODIC_5S:
                self._transmit(n)

    # -- blob callbacks ----------------------------------------------------------
    def _activate(self, n):
        rule = self.rules.get(n)
        if rule is None:
            log.warning("G12 code activated unknown rule #%d", n)
            return
        self.on.add(n)
        if rule.kind in PERIODIC_1S | PERIODIC_5S and n not in self.active:
            self._order += 1
            self.active[n] = self._order
        if rule.kind in PAIRED:
            for m in sorted(k for k, r in self.rules.items() if r.kind == rule.kind):
                self._transmit(m)
            return
        self._transmit(n)
        if rule.kind in TRIPLE:
            self._at(0.25, lambda: self._transmit(n))
            self._at(0.50, lambda: self._transmit(n))

    def _deactivate(self, n):
        self.active.pop(n, None)
        self.on.discard(n)

    def _is_active(self, n):
        return n in self.on

    def _button(self, n):
        """ABI 17: push-button input n's state for the blob (0 = idle)."""
        return self.buttons.get(n, 0)

    def _send_record(self, raw):
        """A frame-table record: pad, DGN low u16, CAN-id high byte, payload in wire order."""
        dgn = raw[1] | raw[2] << 8 | (raw[3] & 3) << 16
        self._emit(dgn, raw[4:12], prio=raw[3] >> 2 & 7)
        if (dgn == rvc.DC_DIMMER_COMMAND_2 and raw[4] == self.cell(0x73) and raw[7] == 3
                and self.cell(0x79) == 1):
            self._abandon_start()

    # -- frames ------------------------------------------------------------------
    def render(self, n):
        """Payload of table-0x62 record ``n`` with the current cell values substituted."""
        rule = self.rules[n]
        p = list(rule.payload)
        for i, width, sel in rule.value_fields:
            v = self.cell(sel)
            p[i] = v & 0xFF
            if width == 2:
                p[i + 1] = v >> 8
        return p

    def _transmit(self, n):
        self._emit(self.rules[n].dgn, self.render(n))

    def _emit(self, dgn, data, prio=6):
        data = bytes(data)
        self.send(dgn, data, prio)
        if dgn == self.WRITE_DGN and data[1] == 0x96 and data[6] in (0xD1, 0xD2):
            self._apply_write(data)
        elif dgn == rvc.DC_DIMMER_COMMAND_2:
            self.outputs.command(data, external=False)  # its own outputs, e.g. interlock
        else:
            self._receive(dgn, self.sa, data)

    # -- input pins (table 0x10 dispatch -> table 0x20 rules) -------------------------
    def set_input(self, pin, active):
        """Drive G12 input pin ``pin`` (a wall switch, or 10 = ignition sense)."""
        state = self._pins.get(pin)
        if active and state is None:
            self._token += 1
            self._pins[pin] = [self._token, False]
            self._fire_pin(pin, self.PRESS)
            self._at(self.HOLD, lambda t=self._token: self._still_held(pin, t))
        elif not active and state is not None:
            del self._pins[pin]
            self._fire_pin(pin, self.HOLD_RELEASE if state[1] else self.TAP_RELEASE)

    def _still_held(self, pin, token):
        state = self._pins.get(pin)
        if state is None or state[0] != token:
            return
        state[1] = True
        self._fire_pin(pin, self.HELD)
        self._at(1.0, lambda: self._still_held(pin, token))

    def _fire_pin(self, pin, phase):
        if "button" in self.logic.callbacks:
            n, state = pin - 1, self.BUTTON_STATE[phase]
            self.buttons[n] = state
            if phase in (self.TAP_RELEASE, self.HOLD_RELEASE):
                self._at(2 * self.TICK, lambda: self.buttons.get(n) == state
                         and self.buttons.pop(n))
        for rule in self.pin_rules.get(pin, ()):
            if rule.mask & phase:
                self._emit(rule.dgn, rule.payload)

    def _broadcast_status(self):
        frames = {}
        for r in self.status:
            if r.direction != "transmit" or r.dgn in STATUS_RECEIVE:
                continue
            if r.dgn == rvc.DATE_TIME_STATUS:
                frames[(r.dgn, None)] = rvc.date_time_status(self.clock())
                continue
            template = STATUS_TEMPLATE.get(r.dgn)
            if template is None:
                continue
            lo, hi = r.instance_range
            instances = [None] if lo == 0xFF else range(lo, max(lo + 1, hi))
            for k, inst in enumerate(instances):
                data = frames.setdefault((r.dgn, inst), bytearray(template))
                if inst is not None:
                    data[0] = inst
                v = self.cell(r.cell + k)
                data[r.payload_index] = v & 0xFF
                if r.kind != 0x24:         # 0x28/0x2C carry a u16
                    data[r.payload_index + 1] = v >> 8
        chassis = self.inputs.get("chassis")
        if chassis is not None:            # app-side: not described by the section
            frames[(0x1AAFD, 2)] = bytearray([2, 0xFF, *rvc.volts(chassis).to_bytes(2, "little"),
                                              0, 0, 0, 0])
        for (dgn, _), data in frames.items():
            self._emit(dgn, data)

    def _feed_inputs(self):
        """Simulated sensors and clock into the cells the G12 application fills."""
        for sensor, raw in self.inputs.get("tanks", {}).items():
            self.set_cell(self.TANK_CELL + sensor - 1, raw)
        if self.inputs.get("house") is not None:
            self.set_cell(self.HOUSE_CELL, rvc.volts(self.inputs["house"]))
        if self.inputs.get("lpg") is not None:  # level | resolution << 8 (coach: 0x6450)
            self.set_cell(self.LPG_CELL, self.inputs["lpg"] | 100 << 8)
        if self.inputs.get("ambient") is not None:
            self.set_cell(self.AMBIENT_CELL, rvc.kelvin_u16(self.inputs["ambient"]))
        t = self.clock()
        self.set_cell(self.CLOCK_CELL, t.tm_hour | t.tm_min << 8)  # byte-swapped vs 2B/2C

    # -- bus input ---------------------------------------------------------------
    def on_frame(self, frame):
        _, dgn, sa = rvc.split_id(frame.can_id)
        d = frame.data
        if dgn == self.WRITE_DGN and len(d) >= 7 and d[1] == 0x96:
            if d[6] in (0xD1, 0xD2):
                self._apply_write(d)
                self._emit(self.CONFIG_DGN, self._report(d[2]))
            elif d[6] == 0xD3:
                self._emit(self.CONFIG_DGN, self._report(d[2]))
            return
        if dgn == rvc.DC_DIMMER_COMMAND_2:
            self.outputs.command(d)
            return
        if dgn == THERMOSTAT_COMMAND:
            self.thermostat.command(d)
            return
        if dgn >> 8 == rvc.REQUEST >> 8 and dgn & 0xFF in (0xFF, self.sa) and len(d) >= 3:
            self._answer_request(d[0] | d[1] << 8 | d[2] << 16)
            return
        self._receive(dgn, sa, d)

    def _receive(self, dgn, sa, d):
        """Table-0x61 receive rules: copy a frame byte (or u16) into a cell."""
        for r in self.status:
            receive = r.direction == "receive" or r.dgn in STATUS_RECEIVE
            if receive and r.dgn == dgn and r.payload_index < len(d):
                lo, hi = r.instance_range
                key = sa if dgn == rvc.DM_RV else d[0]  # DM_RV has no instance (inferred)
                if lo == 0xFF or lo <= key <= hi:
                    v = d[r.payload_index]
                    if r.kind in (0x28, 0x2C) and r.payload_index + 1 < len(d):
                        v |= d[r.payload_index + 1] << 8
                    lo_v, hi_v = r.params
                    if not lo_v <= v <= hi_v:
                        # Out of the record's range -> unset: the WS500's DM_RV
                        # (byte 0 = 5, range 80..) leaves cell 0x53 at FFFF on
                        # the coach (live read-back 2026-10-21).
                        v = 0xFFFF
                    self.set_cell(r.cell, v)

    # -- application: the auto-start watchdog (not in the MCF) ---------------------
    # The blob only prepares it: 0x0A = the mode's voltage (0x55 in AES, 0x56 in
    # AGS), 0x0B = 0x2F ? start-at 0x31 : 0, 0x73 = the start channel (G12-CODE.md).
    # The comparison, the start and the stop are the application's, checked every
    # tick (coach 2026-10-22, run 2; pc39):
    # * start: 0x0C + 5 s after the voltage is below 0x0B with AES enabled -- from
    #   the crossing frame (pc39: 102.655 -> 117.658) or from the enable when it is
    #   already below (15.012 and 15.002 s).  Pending (cell 05 bit 1) at 0x0C s.
    #   Not while cell 05 bit 2 is set (AES enabled with the ignition on): pending
    #   then comes and nothing starts (16:21:20).
    # * stop: the voltage above stop volts 0x0D (strictly: a reading of exactly 0x0D
    #   restarts it) for 0x0E s -> rules #4-#7 (all-off, start channel off, 79 := 0,
    #   7D := 1, which the blob turns into BF 01); AES stays enabled.  0x0E is read
    #   live: changed 900 -> 600 s 20 s into the run, the stop came 600.5 s after
    #   the last reading at 0x0D.
    # * abandoned start: when the blob's own 60 s channel-0x73 OFF fires mid-run (no
    #   engine 60 s after the ignition dropped, 16:25:33), the same stop rules and AES
    #   disabled.
    START_SOURCE = {0x55, 0x56}
    START_EXTRA = 5.0
    STOP_CHANNEL = 20

    def _watchdog(self):
        aes = self.cell(0xEF) in (2, 3)
        v = self.cell(0x55 if aes else 0x56)
        now = self.bus.m.cycles / self.bus.m.cpu_hz
        if self.cell(0x79) == 1:
            self._below = None
            stop_at = self.cell(0x0D)
            if stop_at in (0, 0xFFFF) or v == 0xFFFF or v <= stop_at:
                self._above = None
            elif self._above is None:
                self._above = now
            elif now - self._above >= self.cell(0x0E) - 1e-3:
                self._above = None
                self._auto_stop()
            return
        self._above = None
        limit = self.cell(0x0B)
        if self.cell(0x01) != 1 or limit in (0, 0xFFFF) or v >= limit:
            self._below = None
            return
        if self._below is None:
            self._below = now
        if now - self._below >= self.cell(0x0C) - 1e-3:
            self._aes["pending"] = True    # cell 05 bit 1, 5 s before the start (pc39)
        if (now - self._below >= self.cell(0x0C) + self.START_EXTRA - 1e-3
                and not self._aes["inhibit"]):
            self._below = None
            self._auto_start()

    # -- application: cells 03 and 05 (not in the MCF) -----------------------------
    # Swept on 15FCE every 5 s; the panel never reads 05 (data point 231).  From the
    # captures: 05 = 0x300 | 01 (AES enabled, = cell 0x01) | 02 (the low-volts start
    # pending, set 0x0C s below, kept until AES is disabled or stops the engine: pc39
    # 771, 2026-10-22 16:46:20) | 04 (AES enabled with the ignition on, kept until
    # disabled; blocks the auto-start: 2026-10-22, four enables) | 40 (set by an
    # ignition change while disabled, or by a stop that leaves AES disabled; cleared
    # when AES is enabled or disabled: pc37, pc38, pc48) | 80 (30 s after such an
    # event, an automatic stop, or the ignition coming on during a run; with AES
    # enabled and idle, or the ignition going off mid-run, an ignition change sets
    # nothing: 2026-10-22 16:08:11, 16:17:51, 16:24:33; 28.8-30.5 s from the sweeps).
    # 03 = 720 while enabled, else FFFF.
    # Not fitted: a panel Stop at 16:09:06 left 05 = 0x300, at 16:19:48 0x3C0.
    IGNITION_WINDOW = 30.0

    def _aes_state(self):
        now = self.bus.m.cycles / self.bus.m.cpu_hz
        en, ign = self.cell(0x01), self.cell(0x28)
        if self._aes is None:
            v = self.cell(0x05)
            v = 0 if v == 0xFFFF else v
            self._aes = {"enabled": en, "ignition": ign, "pending": bool(v & 0x02),
                         "inhibit": bool(v & 0x04), "monitor": bool(v & 0x40),
                         "window": now if v & 0x80 else None, "stopped": False}
        a = self._aes
        if en != a["enabled"]:
            a["enabled"], a["monitor"] = en, False
            if en == 1:
                a["inhibit"] = ign == 1
            else:
                a["pending"] = a["inhibit"] = False
        if ign != a["ignition"]:
            if a["ignition"] != 0xFFFF:
                if en != 1:
                    a["window"], a["monitor"] = now, True
                elif self.cell(0x79) == 1 and ign == 1:
                    a["window"] = now
            a["ignition"] = ign
        if a["stopped"]:
            a["window"], a["monitor"], a["stopped"] = now, en != 1, False
        if a["window"] is not None and now - a["window"] >= self.IGNITION_WINDOW:
            a["window"] = None
        self.set_cell(0x05, 0x300 | (en == 1) | a["pending"] << 1 | a["inhibit"] << 2
                      | a["monitor"] << 6 | (a["window"] is not None) << 7)
        self.set_cell(0x03, 720 if en == 1 else 0xFFFF)

    def _auto_start(self):
        """pc39, frame for frame: the start channel on at 200, GENERATOR STOP off, then
        the 1FED9 rules 79 := 1 (#2) and 7D := 2 (#3); the blob turns 7D into BF 02."""
        log.info("G12 auto-start: channel %d", self.cell(0x73))
        self._emit(rvc.DC_DIMMER_COMMAND_2, [self.cell(0x73), 0xFF, 0xC8, 0x01, 0xFF, 0x00, 0xFF, 0xFF])
        self._emit(rvc.DC_DIMMER_COMMAND_2, [self.STOP_CHANNEL, 0xFF, 0x00, 0x03, 0xFF, 0x00, 0xFF, 0xFF])
        self._transmit(2)
        self._transmit(3)

    def _auto_stop(self):
        """2026-10-22 16:46:19.717, frame for frame: rules #4 (all, FF FF C8 01 01 00), #5
        (the start channel OFF), #6 (79 := 0) and #7 (7D := 1 -> BF 01)."""
        log.info("G12 auto-stop")
        self._aes["pending"], self._aes["stopped"] = False, True
        for n in (4, 5, 6, 7):
            self._transmit(n)

    def _abandon_start(self):
        """16:25:33: after the blob's channel-0x73 OFF, the stop rules and AES disabled."""
        self._auto_stop()
        self.set_cell(0x01, 0)
        self._save()

    def _answer_request(self, pgn):
        for dgn, data in APP_RESPONSES.get(pgn, ()):
            self._emit(dgn, bytes.fromhex(data))
        if pgn == 0x0FEEB:  # product identification, by BAM (J1939-21 transport)
            size = len(PRODUCT_ID)
            packets = (size + 6) // 7
            self._emit(0x0ECFF, [0x20, size & 0xFF, size >> 8, packets, 0xFF,
                                 pgn & 0xFF, pgn >> 8 & 0xFF, pgn >> 16])
            for k in range(packets):
                chunk = PRODUCT_ID[7 * k:7 * k + 7].ljust(7, b"\xff")
                self._at(0.046 + 0.05 * k, lambda k=k, chunk=chunk: self._emit(0x0EBFF,
                                                                             [k + 1, *chunk]))

    def _apply_write(self, d):
        sel, func, value = d[2], d[6], d[4] | d[5] << 8
        if func == 0xD2 or sel in self.DELTA:
            delta = value - 0x10000 if value & 0x8000 else value
            value = (self.cell(sel) + delta) & 0xFFFF
        self.set_cell(sel, value)
        log.info("G12 set %02X = %d", sel, value)
        self._save()

    def _report(self, sel):
        """15FCE answer: the selector's sweep template if it has one, else the generic form."""
        for n, rule in self.rules.items():
            if rule.dgn == self.CONFIG_DGN and rule.payload[0] == sel:
                return self.render(n)
        v = self.cell(sel)
        return [sel, 0x00, v & 0xFF, v >> 8, 0, 0, 0, 0]


def from_mcf(mcf_data, **kwargs):
    """A ``G12`` running the MCF's G12 logic, or None if that is not possible."""
    try:
        g12code.blob(mcf_data)
    except Exception as e:  # no G12 section / no code table in this package
        log.info("no G12 logic in this MCF (%s): using the protocol-only G12 model", e)
        return None
    try:
        return G12(mcf_data, **kwargs)
    except G12Error as e:
        log.warning("%s: using the protocol-only G12 model", e)
        return None
