"""RV-C (J1939-based) helpers: 29-bit IDs and encoders for common DGNs.

ID layout: priority(3) | DGN(18, includes the destination for PDU1 DGNs) | SA(8).
Scalings follow the RV-C spec: voltage 0.05 V, current 0.05 A offset -1600 A,
temperature 0.03125 deg C offset -273, percentages 0.5 %.
"""
import struct

DATE_TIME_STATUS = 0x1FFFF
DC_SOURCE_STATUS_1 = 0x1FFFD
THERMOSTAT_AMBIENT_STATUS = 0x1FF9C
DC_DIMMER_STATUS_3 = 0x1FEDA
DC_DIMMER_COMMAND_2 = 0x1FEDB
TANK_STATUS = 0x1FFB7
DM_RV = 0x1FECA
ADDRESS_CLAIMED = 0x0EE00
REQUEST = 0x0EA00
ACKNOWLEDGEMENT = 0x0E800


def can_id(dgn, sa, prio=6):
    return (prio & 7) << 26 | (dgn & 0x3FFFF) << 8 | (sa & 0xFF)


def split_id(ident):
    """-> (priority, dgn, source address); PDU1 DGNs keep their destination byte."""
    return ident >> 26 & 7, ident >> 8 & 0x3FFFF, ident & 0xFF


def dgn_name(dgn):
    names = {v: k for k, v in globals().items() if k.isupper() and isinstance(v, int)}
    if dgn in names:
        return names[dgn]
    if (dgn >> 8) & 0xFF < 0xF0 and (dgn & ~0xFF) in names:  # PDU1: strip destination
        return f"{names[dgn & ~0xFF]}->{dgn & 0xFF:02X}"
    return f"DGN {dgn:05X}"


def pad(data):
    return bytes(data) + b"\xFF" * (8 - len(data))


def volts(v):
    return max(0, min(0xFFFD, round(v / 0.05)))


def amps16(a):
    """RV-C 16-bit current: 0.05 A per bit, offset -1600 A."""
    return max(0, min(0xFFFD, round((a + 1600) / 0.05)))


def amps(a):
    return max(0, min(0xFFFFFFFD, round((a + 2_000_000) / 0.001)))


def kelvin_u16(celsius):
    return max(0, min(0xFFFD, round((celsius + 273) / 0.03125)))


def date_time_status(t):
    """``t`` is a time.struct_time (local time)."""
    return pad([t.tm_year - 2000, t.tm_mon, t.tm_mday, (t.tm_wday + 1) % 7 + 1,
                t.tm_hour, t.tm_min, t.tm_sec])


def dc_source_status_1(instance, voltage, current=0.0, priority=0xFF):
    return pad(struct.pack("<BBHI", instance, priority, volts(voltage), amps(current)))


def thermostat_ambient_status(instance, celsius):
    return pad(struct.pack("<BH", instance, kelvin_u16(celsius)))


def dc_dimmer_status_3(instance, brightness_pct, group=0xFF, last_command=0xFF):
    level = max(0, min(200, round(brightness_pct * 2)))
    return pad([instance, group, level, 0xFC, 0xFF, 0xFF, last_command])


def tank_status(instance, level, resolution=100):
    return pad([instance, level, resolution])
