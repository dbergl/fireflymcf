"""The Timberline peer: its transmit schedule and its command handling, on a fake clock."""
from fireflymcf.emu import rvc
from fireflymcf.emu.canbus import CanBus
from fireflymcf.emu.peers import Timberline
from fireflymcf.emu.periph.bxcan import Frame

HZ = 1_000_000


class _Can:
    sink = None

    def receive(self, frame):
        pass

    def bit_cycles(self):
        return 0


class _Machine:
    cpu_hz = HZ
    cycles = 0

    def reschedule(self):
        pass


def bus_with(peer):
    m = _Machine()
    bus = CanBus(m, _Can())
    bus.add_peer(peer)
    seen = []
    deliver = bus.deliver
    bus.deliver = lambda f, o=None: (seen.append((m.cycles / HZ, f)), deliver(f, o))
    return m, bus, seen


def run(m, bus, seconds, step=0.001):
    end = m.cycles + int(seconds * HZ)
    while m.cycles < end:
        bus.next_event()
        m.cycles += int(step * HZ)


def cmd(dgn, payload):
    return Frame(rvc.can_id(dgn, 0x9F), bytes.fromhex(payload), True)


def wh_status(seen, since=0.0):
    return [(t, f.data) for t, f in seen
            if t >= since and rvc.split_id(f.can_id)[1] == Timberline.WH_STATUS]


def test_one_cycle_matches_the_capture_order_and_payloads():
    """Idle, one 4.992 s cycle is the captured slots, 416 ms apart, byte for byte (2026-11-18,
    source 0x65), with 0x85 every second besides."""
    m, bus, seen = bus_with(Timberline())
    run(m, bus, 4.99)
    slots = [(round(t / 0.416), "%08X#%s" % (f.can_id, f.data.hex().upper()))
             for t, f in seen if f.data[:1] != b"\x85"]
    assert slots == [
        (0, "19FFF765#0100602D5323C300"), (1, "19FE9965#01FF7FFFFFFFFFFF"),
        (2, "19FFE265#0132FFA024FFFFFF"), (3, "19FEFA65#010002FFFFFFFFFF"),
        (6, "19FE9765#01F0FFFFFFFFFFFF"), (7, "19FF9C65#01C622FFFFFFFFFF"),
        (8, "19EF6565#84F016242023BAFF"), (9, "19EF6565#8A201C1EFFFFFFFF"),
        (10, "19FECA65#0165FFFFFFFFFFFF"), (11, "19FFE465#01FC00FFFFFFFFFF")]
    timers = [t for t, f in seen if f.data[:1] == b"\x85"]
    assert len(timers) == 5 and all(abs((b - a) - 1.0) < 0.002 for a, b in zip(timers, timers[1:]))


def test_without_report_on_change_a_command_shows_at_the_next_slot():
    m, bus, seen = bus_with(Timberline(report_on_change=False))
    run(m, bus, 1.0)                       # past slot 0 at t=0
    bus.peers[0].on_frame(cmd(Timberline.WH_COMMAND, "0101ffffffffffff"))
    run(m, bus, 3.0)
    assert wh_status(seen, since=0.5) == []                        # nothing until the slot
    run(m, bus, 1.5)
    (t, d), = wh_status(seen, since=0.5)
    assert abs(t - 4.992) < 0.002 and d[1] == 1


def test_a_command_reports_once_at_once_with_byte_6_from_the_last_slot():
    """As the unit answered 1FFF6 from another node (2026-11-18): one 1FFF7 out of the cycle
    within 10 ms, byte 6 unchanged until the next periodic slot, the cycle's phase kept."""
    tl = Timberline(element_s=0.5)
    m, bus, seen = bus_with(tl)
    run(m, bus, 1.0)
    tl.on_frame(cmd(Timberline.WH_COMMAND, "0102ffffffffffff"))
    run(m, bus, 4.5)
    got = [(round(t, 2), d[1], d[6]) for t, d in wh_status(seen, since=0.5)]
    assert got == [(1.0, 2, 0xC3), (4.99, 2, 0xD3)], got
    tl.on_frame(cmd(Timberline.WH_COMMAND, "0102ffffffffffff"))    # no change: no report
    run(m, bus, 1.0)
    assert len(wh_status(seen, since=0.5)) == 2


def test_a_change_on_the_units_own_panel_reports_twice():
    tl = Timberline()
    m, bus, seen = bus_with(tl)
    run(m, bus, 1.0)
    tl.panel_set_mode(2)
    run(m, bus, 0.5)
    got = [(round(t, 3), d[1], d[6]) for t, d in wh_status(seen, since=0.5)]
    assert got == [(1.0, 2, 0xC3), (1.086, 2, 0xC3)], got


def test_burner_and_element_bits_follow_the_mode_after_their_delays():
    tl = Timberline(apply_s=0, ignition_s=10.0, element_s=2.0)
    m, bus, seen = bus_with(tl)
    run(m, bus, 0.1)
    tl.on_frame(cmd(Timberline.WH_COMMAND, "0103ffffffffffff"))
    run(m, bus, 15.0)
    byte6 = [d[6] for t, d in wh_status(seen, since=0.5) if t > 1]   # slots at 4.99, 9.98, 14.98
    assert byte6 == [0xC3 | 1 << 4, 0xC3 | 1 << 4, 0xC3 | 1 << 2 | 1 << 4]
    tl.on_frame(cmd(Timberline.WH_COMMAND, "0102ffffffffffff"))
    run(m, bus, 5.0)
    assert wh_status(seen)[-1][1][6] == 0xC3 | 1 << 4


def test_thermostat_hot_water_and_pump_commands():
    tl = Timberline(apply_s=0)
    m, bus, seen = bus_with(tl)
    tl.on_frame(cmd(Timberline.TH_COMMAND, "01f0ffffffffffff"))
    assert (tl.th_mode, tl.th_schedule, tl.setpoint) == (0, 0, 0x24A0)
    tl.on_frame(cmd(Timberline.TH_COMMAND, "017fffffffffffff"))
    assert (tl.th_mode, tl.th_schedule) == (0, 1)
    tl.on_frame(cmd(Timberline.TH_COMMAND, "01ffff0000ffffff"))     # far below 10 C
    assert tl.setpoint == rvc.kelvin_u16(10.0)
    tl.on_frame(cmd(Timberline.PROPRIETARY, "83fdffffffffffff"))     # domestic water priority
    assert tl._wh_status_2()[1][2] >> 6 == 0
    tl.on_frame(cmd(Timberline.PROPRIETARY, "83fcffffffffffff"))     # heating priority
    assert tl._wh_status_2()[1][2] >> 6 == 1
    tl.on_frame(cmd(Timberline.PUMP_COMMAND, "0101ffffffffffff"))
    assert tl._pump_status()[1][1] == 0xF1
    tl.on_frame(cmd(Timberline.WH_COMMAND, "0201ffffffffffff"))      # another instance
    assert tl.mode == 0
    assert [name for _t, name, _d in tl.log] == ["1FEF9", "1FEF9", "1FEF9", "1EF65", "1EF65",
                                                 "1FE96"]


def test_info_messages_rotate_every_20_s():
    m, bus, seen = bus_with(Timberline())
    run(m, bus, 70.0)
    info = [(round(t, 2), f.data[0]) for t, f in seen if f.data[:1] in (b"\x86", b"\x87", b"\x88")]
    assert info == [(11.56, 0x87), (31.56, 0x88), (51.56, 0x86)]
