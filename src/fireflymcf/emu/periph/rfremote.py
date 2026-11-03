"""Wireless (RF) control panel reaching the LCD through the radio module on UART4.

Protocol from the firmware's receiver FUN_0021ce28 (ev_4dfe4d57) and UART4 ISR
FUN_00216b1c (ev_1e27027b): the module sends ``F0 0F`` followed by a 10-byte
packet at 50 kbaud:

  byte 0      unknown (sent as 0)
  bytes 1-4   remote ID (u32 LE), matched against paired IDs kept in EEPROM
  byte 5      type: 0xA5 = status, 0xFA = button events
  status:     bytes 6-7 temperature, signed 12-bit, 1/16 deg C
              bytes 8-9 battery voltage, mV
  buttons:    bytes 6-9 bitmaps (press kinds 1..4)

Every 10 s the LCD reports each paired remote's battery on DC_SOURCE_STATUS_1
(instance from its config, 0x81 on this coach: (mV + 25) / 50 in 0.05 V units)
and, if configured, its temperature on THERMOSTAT_AMBIENT_STATUS.  A blank EEPROM
pairs ID 0xFFFFFFFF.

The LCD loops its own 1FFFD into GUI data point 16.  From 10 minutes after boot
the FAULTS list (variable 237) shows "<switch> has a dead battery or is
disconnected." while that point is stale (30 s without a report), 0, or 3.6 V and
up: ``battery_mv=None`` (no remote) shows it in the emulator.  A remote that goes
silent after reporting does not: the LCD keeps re-sending its last reading (as the
coach did with the battery pulled, 2026-10-22).
"""
import struct


class RFRemote:
    STATUS = 0xA5

    def __init__(self, machine, uart, remote_id=0xFFFFFFFF, battery_mv=2800, temp_c=21.0,
                 period=5.0):
        self.m = machine
        self.uart = uart
        self.remote_id = remote_id
        self.battery_mv = battery_mv
        self.temp_c = temp_c
        self.period = int(period * machine.cpu_hz)
        self.next_due = machine.cycles + self.period
        self.packets = 0

    def status_packet(self):
        temp = round(self.temp_c * 16) & 0xFFF
        return bytes([0xF0, 0x0F, 0x00]) + struct.pack("<IBHH", self.remote_id, self.STATUS,
                                                       temp, self.battery_mv)

    def next_event(self):
        now = self.m.cycles
        if self.battery_mv is None:     # no remote
            return None
        # Silent (no scheduled events) until the firmware enables UART4 reception.
        if self.uart.get(self.uart.CR1) & 0x24 != 0x24:
            self.next_due = now + self.period
            return None
        if now >= self.next_due:
            self.uart.feed(self.status_packet())
            self.packets += 1
            self.next_due = now + self.period
        return self.next_due - now
