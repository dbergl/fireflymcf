"""G12 node running the MCF's own G12 logic (section table 0x67) against its rule tables."""
import json
import os

import pytest

from fireflymcf.emu import rvc
from fireflymcf.emu.machine import Machine
from fireflymcf.emu.g12 import G12
from fireflymcf.emu.periph.bxcan import Frame

# The research corpus, linked into the repo root by scripts/link-research.sh; without it
# tests/conftest.py does not collect this module.
CORPUS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
MCF = os.path.join(CORPUS, "firefly-13.6", "mcf.bin")
MCF147 = os.path.join(CORPUS, "firefly-14.7 (Terrain)", "mcf.bin")
TANKS = {1: 20000, 2: 30000, 3: 45000, 4: 60000}   # fresh full .. grey 2 empty (high = empty)


class Harness:
    def __init__(self, path=None, cells=None, inputs=None):
        self.m = Machine()
        self.sent = []
        g = G12(open(MCF, "rb").read(), path=path, cells=cells,
                inputs=inputs or {"tanks": TANKS, "house": 13.1})
        g.send = lambda dgn, data, prio=6: self.sent.append((self.m.cycles, dgn, bytes(data)))
        self.g = self.m.canbus.add_peer(g)

    def advance(self, seconds):
        end = self.m.cycles + int(seconds * self.m.cpu_hz)
        while True:
            nxt = self.g.pump(self.m.cycles)
            if nxt is None or self.m.cycles + nxt > end:
                break
            self.m.cycles += nxt
        self.m.cycles = end

    def write(self, sel, value, func=0xD1):
        v = value & 0xFFFF
        self.g.on_frame(Frame(rvc.can_id(0x1FED9, 0x9F),
                              bytes([0xFF, 0x96, sel, 0x0F, v & 0xFF, v >> 8, func, 0xEA]), True))

    def frames(self, dgn, since=0):
        return [d for t, g, d in self.sent if g == dgn and t >= since]


def test_init_seeds_defaults_and_announces():
    h = Harness()
    g = h.g
    assert (g.cell(0xEC), g.cell(0xCC), g.cell(0xF1), g.cell(0xF2)) == (1, 54000, 13, 6)
    assert any(d[0] == 0x00 for d in h.frames(0x15FCE))              # boot settings burst
    h.advance(5.1)
    assert h.frames(0x1FACF)[-1] == bytes.fromhex("49440d080d000006")  # 13.6, floorplan 0


def test_tank_buckets_and_grey2_gated_by_floorplan():
    h = Harness()
    h.advance(3.1)
    t0 = h.m.cycles
    h.advance(1.0)
    levels = {d[0]: (d[1], d[2]) for d in h.frames(rvc.TANK_STATUS, t0)}
    assert levels == {0: (3, 3), 2: (2, 3), 1: (1, 3)}                  # level/resolution 3
    h.write(0xF5, 8)                                                    # WD
    h.advance(2.0)
    t1 = h.m.cycles
    h.advance(1.0)
    assert {d[0] for d in h.frames(rvc.TANK_STATUS, t1)} == {0, 1, 2, 0x12}


def test_black_tank_option_switches_its_broadcast():
    h = Harness()
    h.advance(2.0)
    h.write(0xEC, 0)
    h.advance(1.5)
    t0 = h.m.cycles
    h.advance(2.0)
    assert 1 not in {d[0] for d in h.frames(rvc.TANK_STATUS, t0)}
    h.write(0xEC, 1)
    h.advance(1.5)
    assert 1 in {d[0] for d in h.frames(rvc.TANK_STATUS, h.m.cycles - h.m.cpu_hz)}


def test_config_write_relays_within_milliseconds():
    h = Harness()
    h.advance(3.0)
    before = h.g.cell(0x82)
    t0 = h.m.cycles
    h.write(0x82, 2, func=0xD2)                                         # Low Batt. Cutout +0.1 V
    h.advance(0.010)
    relay = h.frames(0x1FFD0, t0)
    assert relay and relay[0][0] == 1 and relay[0][5] | relay[0][6] << 8 == before + 2
    assert h.frames(0x15FCE, t0)[0][0] == 0x82                           # echo of the write


def test_mode_switch_runs_the_profile_handler():
    h = Harness(cells={0xEF: 3})                                        # AES
    h.advance(1.1)
    assert (h.g.cell(0x0D), h.g.cell(0x16), h.g.cell(0x73)) == (1080, 115, 18)
    h.write(0xEF, 0)                                                    # back to AGS
    h.advance(1.1)
    assert (h.g.cell(0x50), h.g.cell(0x0D)) == (20, 270)                # AGS profile: 13.5 V


def test_status_broadcasts():
    h = Harness(inputs={"tanks": TANKS, "house": 13.1, "chassis": 12.6, "ambient": 21.0})
    h.advance(1.5)
    volts = {d[0]: d for d in h.frames(0x1AAFD)}
    assert volts[1] == bytes([1, 0xFF, *rvc.volts(13.1).to_bytes(2, "little"), 0, 0, 0, 0])
    assert volts[2][2] | volts[2][3] << 8 == rvc.volts(12.6)
    raw = {d[0]: d[1] | d[2] << 8 for d in h.frames(0x0BFC1)}
    assert raw == TANKS
    assert h.frames(rvc.DATE_TIME_STATUS)


def test_nvm_persists_per_section(tmp_path):
    path = str(tmp_path / "g12.json")
    h = Harness(path=path)
    h.write(0xF5, 3)                                                    # RD
    assert Harness(path=path).g.cell(0xF5) == 3
    doc = json.load(open(path))
    doc["section"] = "another build"
    json.dump(doc, open(path, "w"))
    assert Harness(path=path).g.cell(0xF5) == 0                         # not this section's NVM


def test_migrates_protocol_model_state(tmp_path):
    path = str(tmp_path / "g12.json")
    json.dump({"F5": 7, "CC": 100}, open(path, "w"))                    # old flat format
    g = Harness(path=path).g
    assert g.cell(0xF5) == 7 and g.cell(0xCC) == 54000                  # demo thresholds dropped


def test_answers_product_id_and_fed8_requests():
    h = Harness()
    t0 = h.m.cycles
    for pgn in (0x1FED8, 0x0FEEB):
        h.g.on_frame(Frame(rvc.can_id(0x0EAFF, 0x9F), bytes([pgn & 0xFF, pgn >> 8 & 0xFF, pgn >> 16]),
                           True))
    h.advance(0.5)
    assert h.frames(0x1FED8, t0) and len(h.frames(0x0FF01, t0)) == 4
    bam = h.frames(0x0ECFF, t0)[0]
    data = b"".join(d[1:] for d in h.frames(0x0EBFF, t0))[:bam[1]]
    assert data == b"Spyder Controls*G12 Panel 2v2 3v0d*0000000*22*" and bam[3] == 7


def test_aes_measured_voltage_comes_from_dc_source_status_1():
    h = Harness(cells={0xEF: 3})
    h.g.on_frame(Frame(rvc.can_id(rvc.DC_SOURCE_STATUS_1, 0x80),
                       rvc.dc_source_status_1(1, 53.85), True))
    h.advance(1.1)
    assert h.g.cell(0x55) == 1077 and h.g.cell(0x0A) == 1077            # pc23: both 1077


def feed_volts(h, volts, seconds):
    """The WS500's 1FFFD instance 1 every 0.5 s."""
    for k in range(int(seconds * 2)):
        h.g.on_frame(Frame(rvc.can_id(rvc.DC_SOURCE_STATUS_1, 0x80),
                           rvc.dc_source_status_1(1, volts), True))
        h.advance(0.5)


def low_volts_run(low_volts_trigger, seconds=20.0, cells=None):
    h = Harness(cells={0xEF: 3, 0x7A: 3, 0x73: 18, 0x01: 1, 0x2F: low_volts_trigger,
                       0x31: 1058, 0x0C: 10, **(cells or {})})
    h.g.set_cell(0x53, 0xFFFF)          # the WS500's DM_RV keeps it unset on the coach
    feed_volts(h, 52.85, seconds)
    return h


def starts(h, since=0):
    return [t for t, g, d in h.sent if g == rvc.DC_DIMMER_COMMAND_2 and t >= since
            and d[:4] == bytes([18, 0xFF, 0xC8, 1])]


def test_low_volts_auto_start_and_the_bf_pulse_on_the_second():
    h = low_volts_run(1)
    hz = h.m.cpu_hz
    start = [t for t, g, d in h.sent if g == rvc.DC_DIMMER_COMMAND_2 and d[:4] == bytes([18, 0xFF, 0xC8, 1])]
    # Below from the first tick (it fills 0x0B = 0x31): 0x0C + 5 s, on the tick (pc39;
    # 2026-10-22: 15.012 and 15.002 s after an enable with the voltage already below)
    assert len(start) == 1 and abs(start[0] / hz - 15.01) < 0.011
    assert [d for t, g, d in h.sent if g == 0x1FED9 and t == start[0]] == [
        bytes.fromhex("ff96790f0100d1ff"), bytes.fromhex("ff967d0f0200d1ff")]
    bf = [(t / hz, d[1]) for t, g, d in h.sent if g == 0x1FBDA and d[0] == 0xBF]
    assert [v for _, v in bf] == [2, 0] and bf[0][0] - start[0] / hz < 0.011
    assert abs(bf[1][0] - round(bf[1][0])) < 0.011                    # BF 00 at the divider wrap
    assert not low_volts_run(0).frames(0x1FBDA)                       # 0x2F = 0: 0x0B = 0, never


def test_ignition_input_repeats_and_drives_the_awning_interlock():
    h = Harness(cells={0xEF: 3})
    h.advance(2.0)
    t0 = h.m.cycles
    h.g.set_input(10, True)                                             # ignition on
    h.advance(2.5)
    on = [d for d in h.frames(0x1FBDA, t0) if d[1] == 1]
    assert [d[0] for d in on[:2]] == [0xFB, 0xAA] and len(on) == 6     # press + 1 s, 2 s refresh
    assert h.g.cell(0x28) == 1                                          # own frame looped back
    t1 = h.m.cycles
    h.g.set_input(10, False)
    h.advance(0.010)
    assert [d[:2] for d in h.frames(0x1FBDA, t1)] == [bytes([0xFB, 0]), bytes([0xAA, 0])]
    assert bytes.fromhex("03ff0022") in [d[:4] for d in h.frames(rvc.DC_DIMMER_COMMAND_2, t1)]


def test_cell_05_aes_state_and_cell_03():
    # 05 = 0x300 | 01 enabled | 02 start pending | 40 since an ignition change
    # | 80 for 30 s after one; 03 = 720 while enabled (pc27, pc28, pc37-39, pc48).
    h = Harness(cells={0xEF: 3})
    h.advance(2.0)
    assert (h.g.cell(0x05), h.g.cell(0x03)) == (0x300, 0xFFFF)
    h.g.set_input(10, True)                                             # ignition on
    h.advance(1.0)
    assert h.g.cell(0x05) == 0x3C0
    h.advance(29.5)
    assert h.g.cell(0x05) == 0x340                                      # window over, 40 stays
    h.write(0x01, 1)                            # AES enabled, with the ignition on: 04
    h.advance(0.1)
    assert (h.g.cell(0x05), h.g.cell(0x03)) == (0x305, 720)             # 40 cleared
    h.g.set_input(10, False)                    # enabled and idle: nothing (2026-10-22)
    h.advance(0.1)
    assert h.g.cell(0x05) == 0x305


def test_enabling_aes_with_the_ignition_on_blocks_the_auto_start():
    # 2026-10-22 run 2: enabled at 16:20:00 with the ignition on -> 05 bit 2; the start
    # went pending at 16:21:20 (0x307) and never fired.  Stop, then Enable with the
    # ignition off (16:23:03, 16:23:10): 0x381, start 15.0 s later.
    h = low_volts_run(1, seconds=1.0, cells={0x01: 0})
    h.g.set_input(10, True)
    h.advance(0.5)
    h.write(0x01, 1)
    feed_volts(h, 52.85, 25.0)
    assert h.g.cell(0x05) & 0x07 == 0x07 and not starts(h)              # pending, inhibited
    h.write(0x01, 0)
    h.g.set_input(10, False)
    feed_volts(h, 52.85, 1.0)
    t0 = h.m.cycles
    h.write(0x01, 1)
    feed_volts(h, 52.85, 16.0)
    assert h.g.cell(0x05) & 0x04 == 0
    assert [abs((t - t0) / h.m.cpu_hz - 15.0) < 0.011 for t in starts(h, t0)] == [True]


def test_auto_stop_after_time_at_stop_volts_above_stop_volts():
    # 16:46:19.717: rules #4-#7 600.5 s after the last reading at stop volts (0x0D,
    # 54.40 V = 1088), with 0x0E changed to 600 mid-run; AES stays enabled.
    h = low_volts_run(1, seconds=16.0, cells={0x0D: 1088, 0x0E: 900})
    assert starts(h)
    h.g.set_input(10, True)                     # the engine runs (without it: abandoned)
    h.write(0x0E, 600)                          # read live (the blob's floor is 600)
    feed_volts(h, 54.5, 10.0)
    feed_volts(h, 54.4, 0.5)                                            # = 0x0D: restarts
    t0 = h.m.cycles
    feed_volts(h, 54.5, 599.0)
    bf = lambda: [d[1] for d in h.frames(0x1FBDA, t0) if d[0] == 0xBF]  # noqa: E731
    assert not bf()
    feed_volts(h, 54.5, 1.5)
    stop = [d for d in h.frames(rvc.DC_DIMMER_COMMAND_2, t0)] + h.frames(0x1FED9, t0)
    assert bytes.fromhex("ffffc8010100ffff") in stop and bytes.fromhex("12ff0003ff00ffff") in stop
    assert bytes.fromhex("ff967d0f0100d1ff") in stop and h.g.cell(0x79) == 0
    assert bf()[:1] == [1]
    assert h.g.cell(0x01) == 1 and h.g.cell(0x05) & 0x82 == 0x80        # pending cleared


def test_abandoned_start_disables_aes():
    # 2026-10-22: started 16:23:25.5; the ignition came on 57.6 s later and dropped
    # 10.6 s after that without the engine running; 59.7 s after the drop (on the
    # timer bank's wrap) the blob's channel-0x73 OFF, then the stop rules and AES
    # disabled (05 = 0x3C0).
    h = Harness(cells={0xEF: 3, 0x7A: 3, 0x73: 18, 0x01: 0, 0x2F: 1, 0x31: 1058, 0x0C: 10})
    h.g.set_cell(0x53, 0xFFFF)          # the WS500's DM_RV keeps it unset on the coach
    feed_volts(h, 52.85, 2.0)
    h.write(0x01, 1)
    feed_volts(h, 52.85, 16.0)
    assert starts(h)
    h.advance(57.6 - 1.0)
    h.g.set_input(10, True)
    h.advance(10.6)
    h.g.set_input(10, False)
    t0 = h.m.cycles
    h.advance(59.0)
    assert h.g.cell(0x01) == 1
    h.advance(1.5)
    cmds = h.frames(rvc.DC_DIMMER_COMMAND_2, t0)
    assert [d.hex() for d in cmds[1:4]] == ["12ff00030000ffff", "ffffc8010100ffff",
                                           "12ff0003ff00ffff"]
    assert h.g.cell(0x01) == 0
    h.advance(0.1)
    assert h.g.cell(0x05) == 0x3C0


def test_cell_05_marks_the_low_volts_start_pending_5_s_ahead():
    h = low_volts_run(1, seconds=11.0)              # below from 0.5 s, 0x0C = 10
    assert h.g.cell(0x05) & 0x02 and not [d for t, g, d in h.sent if g == rvc.DC_DIMMER_COMMAND_2
                                          and d[:4] == bytes([18, 0xFF, 0xC8, 1])]
    h.write(0x01, 0)
    h.advance(0.1)
    assert h.g.cell(0x05) & 0x03 == 0


def test_wall_switch_tap_and_hold():
    h = Harness()
    h.advance(1.5)
    t0 = h.m.cycles
    h.g.set_input(1, True)
    h.advance(0.3)
    h.g.set_input(1, False)                                             # tap -> toggle light 0x16
    cmds = [d for d in h.frames(rvc.DC_DIMMER_COMMAND_2, t0) if d[0] == 0x16]
    assert [d[3] for d in cmds] == [5]
    h.advance(0.1)
    t1 = h.m.cycles
    h.g.set_input(1, True)
    h.advance(2.2)
    h.g.set_input(1, False)                                             # hold -> ramp, then stop
    cmds = [d[3] for d in h.frames(rvc.DC_DIMMER_COMMAND_2, t1) if d[0] == 0x16]
    assert cmds == [21, 21, 4]


@pytest.mark.parametrize("floorplan,channel", [(8, 25), (5, 35)])
def test_pin2_is_a_dimmer_switch_whose_channel_follows_the_floorplan(floorplan, channel):
    # Table 0x61 #10 loops the G12's own 1FBDA A2 into cell 0x4A; +0x1048/+0x17AE
    # toggle on a tap, ramp from 1 s and every 0.5 s while held, stop on release.
    def press(hold):
        h = Harness(cells={0xF5: floorplan})
        h.advance(2.0)
        t0 = h.m.cycles
        h.g.set_input(2, True)
        h.advance(hold)
        h.g.set_input(2, False)
        h.advance(1.0)
        return [(round((t - t0) / h.m.cpu_hz, 2), d[0], d[2], d[3])
                for t, g, d in h.sent if g == rvc.DC_DIMMER_COMMAND_2 and t >= t0]
    assert press(0.3) == [(0.31, channel, 0xFA, 5)]
    assert press(2.2) == [(1.01, channel, 0, 21), (1.51, channel, 0, 21), (2.01, channel, 0, 21),
                          (2.21, channel, 0, 4)]


def test_unknown_context_abi_falls_back():
    from fireflymcf.emu.g12 import G12Logic, from_mcf
    data = bytearray(open(MCF, "rb").read())
    from fireflymcf import g12code
    blob = g12code.blob(bytes(data))
    off = bytes(data).find(blob)
    data[off + 4] = 99                                                  # no such layout
    assert from_mcf(bytes(data)) is None
    with pytest.raises(Exception):
        G12Logic(g12code.blob(bytes(data)), {})


def test_the_tick_runs_every_10_ms_and_divides_down_the_timer_bank():
    h = Harness()
    count = h.g.logic.COUNT
    start = h.g.cell(count - 1)                                         # init arms 100
    h.advance(0.25)
    assert h.g.cell(count - 1) == start - 25


def test_147_abi17_buttons_drive_dimmers_and_report_1ffb8():
    m, sent = Machine(), []
    g = G12(open(MCF147, "rb").read())
    g.send = lambda dgn, data, prio=6: sent.append((m.cycles / m.cpu_hz, dgn, bytes(data)))
    m.canbus.add_peer(g)

    def advance(seconds):
        end = m.cycles + int(seconds * m.cpu_hz)
        while (nxt := g.pump(m.cycles)) is not None and m.cycles + nxt <= end:
            m.cycles += nxt
        m.cycles = end

    advance(1.0)
    sent.clear()
    g.buttons[0] = 2                                                    # tap input 0
    advance(0.05)
    assert (0x1FEDB, bytes.fromhex("16fffa05ff00ffff")) in [(d, p) for _, d, p in sent]
    assert (0x1FFB8, bytes.fromhex("0100ff02ffffffff")) in [(d, p) for _, d, p in sent]
    sent.clear()
    g.buttons[0] = 3                                                    # hold: ramp once a second
    advance(3.0)
    ramps = [t for t, d, p in sent if d == 0x1FEDB and p[0] == 0x16 and p[3] == 21]
    assert len(ramps) == 3 and all(abs((b - a) - 1.0) < 0.02 for a, b in zip(ramps, ramps[1:]))


# --- output driver (g12out.py) -------------------------------------------------------

def dimmer(inst, level, cmd, duration=0xFF):
    return Frame(rvc.can_id(rvc.DC_DIMMER_COMMAND_2, 0x9F),
                 bytes([inst, 0xFF, level, cmd, duration, 0, 0xFF, 0xFF]), True)


def test_output_toggle_ack_status_and_level_report():
    h = Harness()
    h.advance(1.0)
    t0 = h.m.cycles
    h.g.on_frame(dimmer(0x20, 0xFA, 5))                                 # toggle on (remembered)
    assert h.frames(0x0E8FF, t0) == [bytes.fromhex("00ffffffffdbfe01")]
    assert h.frames(rvc.DC_DIMMER_STATUS_3, t0)[0] == bytes.fromhex("20ffc8fcff050400")
    assert h.frames(0x16F84, t0)[0] == bytes.fromhex("ff20ffff007d0000")  # edge: not lit yet
    h.advance(2.1)
    assert bytes.fromhex("ff20ffff0f7d0500") in h.frames(0x16F84, t0)    # faded in: load 0F
    periodic = [d for d in h.frames(rvc.DC_DIMMER_STATUS_3, t0) if d[0] == 0x20]
    assert len(periodic) == 2                                           # + one 2 s status
    t1 = h.m.cycles
    h.g.on_frame(dimmer(0x20, 0, 3))                                    # OFF: no 16F84 edge
    h.advance(3.0)
    assert h.frames(rvc.DC_DIMMER_STATUS_3, t1) == [bytes.fromhex("20ff00fcff030000")]
    assert not [d for d in h.frames(0x16F84, t1) if d[1] == 0x20]


def test_undercurrent_latches_at_half_second_checks():
    # Checked at the 1 s tick and 0.5 s later, once on for 0.23 s; command
    # statuses do not check (pc8 0.254 s latched, pc13 off at 0.378 s clear).
    h = Harness()
    h.advance(1.25)
    status = lambda inst, since: [d for d in h.frames(rvc.DC_DIMMER_STATUS_3, since) if d[0] == inst]
    t0 = h.m.cycles
    h.g.on_frame(dimmer(0x26, 0xC8, 5))                                 # on at x.25: checked at x.5
    h.advance(1.3)
    assert [d[6] for d in status(0x26, t0)] == [0x04, 0x44]             # command, 2 s status
    h.advance(0.75)                                                     # 3.30
    t1 = h.m.cycles
    h.g.on_frame(dimmer(0x18, 0xC8, 0))                                 # on 0.20 s at the 3.5 check
    h.advance(0.38)
    h.g.on_frame(dimmer(0x18, 0, 3))                                    # off before 4.0
    assert [d[6] for d in status(0x18, t1)] == [0x04, 0x00]


def test_half_bridge_command_statuses_hide_the_undercurrent_latch():
    # Output 9's ON re-sent every 5 s while running: 04, and 44 in the periodic
    # status just after -- the latch stays (pc38 874.346 / 874.349).
    h = Harness()
    h.advance(1.0)
    h.g.on_frame(dimmer(0x09, 0xC8, 1))
    h.advance(1.6)                                                      # 2.6: latched at 2.5
    t0 = h.m.cycles
    h.g.on_frame(dimmer(0x09, 0xC8, 1))
    h.advance(1.9)                                                      # 4.5: periodic status
    assert [d[6] for d in h.frames(rvc.DC_DIMMER_STATUS_3, t0) if d[0] == 9] == [0x04, 0x44]


def test_off_with_a_duration_is_a_delayed_off_counted_in_ticks():
    # pc34 285.821: 0B OFF 0F answers 0F with the output still on, the 2 s statuses
    # count floor(15 - elapsed) down to 00, and it goes off 15.0 s after the command.
    h = Harness()
    h.advance(1.0)
    h.g.on_frame(dimmer(0x0B, 0xC8, 1))
    h.advance(0.3)                                                      # 1.3
    t0 = h.m.cycles
    h.g.on_frame(dimmer(0x0B, 0, 3, duration=15))
    h.advance(15.2)
    st = [(round((t - t0) / h.m.cpu_hz, 2), d[2], d[4], d[6] & 4) for t, g, d in h.sent
          if g == rvc.DC_DIMMER_STATUS_3 and d[0] == 0x0B and t >= t0]
    assert st[0] == (0.0, 200, 15, 4)
    assert [s[2] for s in st[1:-1]] == [13, 11, 9, 7, 5, 3, 1]          # periodic at 1.2, 3.2, ...
    assert st[-1] == (15.0, 0, 0xFF, 0)
    assert sum(1 for t, g, d in h.sent if g == 0x16F84 and d[1] == 0x0B and t >= t0) == 15


def test_master_memory_restores_only_what_was_on():
    h = Harness()
    g = h.g
    for inst in (0x16, 0x18):
        g.on_frame(dimmer(inst, 0xFB, 0))                               # master ON
    g.on_frame(dimmer(0x18, 0xFA, 5))                                   # one switched off
    for inst in (0x16, 0x18):
        g.on_frame(dimmer(inst, 0, 6))                                  # master OFF (memory)
    for inst in (0x16, 0x18):
        g.on_frame(dimmer(inst, 0xFB, 0))                               # master ON
    assert (g.outputs.out[0x16].level, g.outputs.out[0x18].level) == (200, 0)


def test_relays_and_lock():
    h = Harness()
    g = h.g
    g.on_frame(dimmer(5, 0, 1))                                         # relay: cmd 1 = on
    assert g.outputs.out[5].level == 200
    g.on_frame(dimmer(3, 0, 33))                                        # lock
    t0 = h.m.cycles
    g.on_frame(dimmer(3, 200, 1, duration=2))
    assert g.outputs.out[3].level == 0
    assert h.frames(rvc.DC_DIMMER_STATUS_3, t0)[-1] == bytes.fromhex("03ff00fdff0100ff")
    g.on_frame(dimmer(3, 0, 34))                                        # unlock
    g.on_frame(dimmer(3, 200, 1, duration=2))                           # awning: 2 s pulse
    assert g.outputs.out[3].level == 200
    h.advance(3.0)
    assert g.outputs.out[3].level == 0


def test_ramp_steps_with_the_coach_dwell_and_stops():
    from fireflymcf.emu.g12out import load_current, step_dwell
    # sum of the dwells from 200 down to 10 = the live 3.694 s
    assert abs(sum(step_dwell(lv) for lv in range(200, 10, -2)) - 3.694) < 0.002
    assert (load_current(0x0F, 200), load_current(0x0F, 124)) == (15, 9)  # pc15 ch 32
    h = Harness()
    g = h.g
    g.on_frame(dimmer(0x20, 0xC8, 5))                                   # on at full
    for _ in range(2):                                                  # hold ~2 s
        g.on_frame(dimmer(0x20, 0xC8, 21, duration=0))
        h.advance(1.0)
    assert 60 <= g.outputs.out[0x20].level <= 75                        # pc15: ~0x46 after 2 s
    assert g.outputs.out[0x20].level % 2 == 0                           # whole percent
    g.on_frame(dimmer(0x20, 0xFF, 4))                                   # stop
    level = g.outputs.out[0x20].level
    h.advance(1.0)
    assert g.outputs.out[0x20].level == level


def test_receive_out_of_range_marks_the_cell_unset():
    h = Harness()
    h.g.on_frame(Frame(rvc.can_id(rvc.DM_RV, 0x80), bytes.fromhex("054cffffffffffff"), True))
    assert h.g.cell(0x53) == 0xFFFF                 # WS500 DM_RV byte 0 = 5, range 80..
    h.g.on_frame(Frame(rvc.can_id(0x1FBDA, 0x9F), bytes.fromhex("fb01ffffffffffff"), True))
    assert h.g.cell(0x28) == 1                      # in range 0..257


def test_every_hold_starts_down_bounces_at_the_bottom_and_stays_at_full():
    h = Harness()
    g = h.g
    o = g.outputs.out[0x15]
    g.on_frame(dimmer(0x15, 0xFA, 5))                                   # on at full
    for _ in range(9):                                                  # hold 4.5 s
        g.on_frame(dimmer(0x15, 0xC8, 21, duration=0))
        h.advance(0.5)
    assert o.ramp_dir == 1 and o.level < 40                             # bounced at ~4
    g.on_frame(dimmer(0x15, 0xFF, 4))
    start = o.level
    g.on_frame(dimmer(0x15, 0xC8, 21, duration=0))                      # new hold: down first
    h.advance(0.1)                                                      # one dwell (~73 ms at 16)
    assert o.level < start
    for _ in range(12):                                                 # up to full ...
        g.on_frame(dimmer(0x15, 0xC8, 21, duration=0))
        h.advance(0.5)
    assert o.level == 200                                               # ... and stays there


def test_147_wall_switch_pins_become_button_states():
    m, sent = Machine(), []
    g = G12(open(MCF147, "rb").read())
    g.send = lambda dgn, data, prio=6: sent.append((m.cycles / m.cpu_hz, dgn, bytes(data)))
    m.canbus.add_peer(g)

    def advance(seconds):
        end = m.cycles + int(seconds * m.cpu_hz)
        while (nxt := g.pump(m.cycles)) is not None and m.cycles + nxt <= end:
            m.cycles += nxt
        m.cycles = end

    advance(1.0)
    sent.clear()
    g.set_input(2, True)                                                # pin 2 = button 1: tap
    advance(0.3)
    g.set_input(2, False)
    advance(0.1)
    cmds = [p.hex() for _, d, p in sent if d == 0x1FEDB]
    assert cmds == ["23fffa05ff00ffff"] and g.buttons.get(1) is None    # toggle 35, back to idle


# --- thermostat (g12thermo.py) -------------------------------------------------------

def thermostat(h, b1=0xFF, speed=0xFF, heat=0xFFFF, cool=0xFFFF):
    h.g.on_frame(Frame(rvc.can_id(0x1FEF9, 0x9F), bytes([0, b1, speed, heat & 0xFF, heat >> 8,
                                                         cool & 0xFF, cool >> 8, 0xFF]), True))


def ac(h, since):
    return [((t - since) / h.m.cpu_hz, d[0], d[2], d[3], d[4]) for t, g, d in h.sent
            if g == rvc.DC_DIMMER_COMMAND_2 and d[0] in (0x2B, 0x09, 0x0A, 0x0B, 0x0C) and t >= since]


def thermo_harness(ambient=30.0, status="000000e024e02400"):
    h = Harness(inputs={"tanks": TANKS, "house": 13.1, "ambient": ambient,
                        "thermostat": bytes.fromhex(status)})
    h.advance(2.0)
    return h


def test_thermostat_boots_everything_off():
    h = Harness()
    assert [(d[0], d[3], d[4]) for t, g, d in h.sent if g == rvc.DC_DIMMER_COMMAND_2] == [
        (0x0B, 3, 0x0F), (0x0C, 3, 0x0F), (0x2B, 3, 0xFF), (0x09, 3, 0xFF), (0x0A, 3, 0xFF)]


def test_thermostat_fan_only_runs_through_the_blob_and_off_again():
    h = thermo_harness(status="0000640125012500")
    t0 = h.m.cycles
    thermostat(h, 0xD5, 100)                                          # pc8 1108.431: fan on, low
    assert h.frames(0x1FFE2, t0)[0] == bytes.fromhex("0015640125012500")
    now = [(c, lv, cmd, dur) for _, c, lv, cmd, dur in ac(h, t0)]
    assert now == [(0x0B, 0, 3, 0xFF), (0x0C, 200, 1, 0xFF)]         # the pair, with the command
    h.advance(0.05)
    assert [(c, lv, cmd, dur) for _, c, lv, cmd, dur in ac(h, t0)][2:] == [(0x0B, 0, 3, 0x0F)]
    t1 = h.m.cycles
    thermostat(h, speed=200)                                          # pc8 1117.819: low -> high
    h.advance(0.05)
    assert [(c, lv, cmd, dur) for _, c, lv, cmd, dur in ac(h, t1)] == [
        (0x0C, 0, 3, 0x0F), (0x0B, 200, 1, 0xFF), (0x0C, 0, 3, 0xFF), (0x0C, 0, 3, 0x0F)]
    t2 = h.m.cycles
    thermostat(h, 0xC0)                                               # pc8 1124.823: back to auto
    h.advance(0.05)
    assert [(c, cmd, dur) for _, c, lv, cmd, dur in ac(h, t2)] == [(0x0B, 3, 0x0F)]


def test_thermostat_cools_after_3_s_and_runs_the_compressor_for_120_s():
    h = thermo_harness()
    t0 = h.m.cycles
    thermostat(h, 0xF1)                                               # cool (pc8 1133.901)
    h.advance(10.5)
    on = [(round(t, 3), c) for t, c, lv, cmd, dur in ac(h, t0) if lv == 200]
    assert on[:4] == [(3.0, 0x0C), (3.0, 0x2B), (3.0, 0x09), (3.014, 0x0B)]
    assert [t for t, c in on if c == 0x2B] == [3.0, 8.0]             # re-sent every 5 s
    thermostat(h, 0xC0)                                               # off at 10.5 s
    h.advance(120.0)
    offs = [(round(t, 3), c) for t, c, lv, cmd, dur in ac(h, t0) if cmd == 3 and c in (0x2B, 0x09)]
    assert offs == [(123.0, 0x2B), (123.0, 0x09), (123.25, 0x2B), (123.25, 0x09),
                    (123.5, 0x2B), (123.5, 0x09)]                     # 120 s run, OFF 3x


def test_thermostat_heat_mode_above_setpoint_and_setpoint_steps():
    h = thermo_harness(status="0000640125012500")
    t0 = h.m.cycles
    thermostat(h, 0xC2)                                               # heat at 30 degC: nothing
    h.advance(4.0)
    assert ac(h, t0) == []
    thermostat(h, cool=0xFFFA)                                        # +1 degF (pc8 1097.487)
    assert h.frames(0x1FFE2)[-1] == bytes.fromhex("0002640125132500")
    h.advance(5.0)                                                    # heat follows cool
    assert h.frames(0x1FFE2)[-1] == bytes.fromhex("0002641325132500")
    thermostat(h, cool=0xFFF9)
    thermostat(h, cool=0xFFF9)
    assert h.frames(0x1FFE2)[-1][5:7] == bytes.fromhex("f024")        # 2513 -> 2501 -> 24F0


def test_thermostat_heat_pump_needs_the_option_and_drives_0a_alone():
    # Coach tests 2026-10-22 run 3 (option on, 20 degC room, heat at 78 degF): 3.0 s after
    # Heat, the cooling fan pair and 0A -- no compressor -- 0A re-sent every 5 s.
    for option, expect in ((0, []), (1, [(3.0, 0x0C), (3.0, 0x0A), (3.014, 0x0B)])):
        h = Harness(cells={0xEB: option}, inputs={"tanks": TANKS, "house": 13.1, "ambient": 15.0,
                                                  "thermostat": bytes.fromhex("000064e024e02400")})
        h.advance(2.0)
        t0 = h.m.cycles
        thermostat(h, 0xC2)                                           # Heat Pump mode, 15 degC
        h.advance(4.0)
        assert [(round(t, 3), c) for t, c, lv, cmd, dur in ac(h, t0) if lv == 200] == expect


def test_thermostat_rests_120_s_after_a_run():
    # Run 3: heat ended 17:39:19.04; Auto at 17:40:15.47 started at 17:41:19.04, 120.00 s
    # after the end rather than 3 s after the command.
    h = Harness(cells={0xEB: 1}, inputs={"tanks": TANKS, "house": 13.1, "ambient": 15.0,
                                         "thermostat": bytes.fromhex("000064e024e02400")})
    h.advance(2.0)
    t0 = h.m.cycles
    thermostat(h, 0xC2)
    h.advance(10.0)
    thermostat(h, 0xC0)                                               # off: ends at 123 s
    h.advance(130.0)
    offs = [round(t, 3) for t, c, lv, cmd, dur in ac(h, t0) if c == 0x0A and cmd == 3]
    assert offs == [123.0, 123.25, 123.5]
    thermostat(h, 0xC3)                                               # auto at 133 s
    h.advance(120.0)
    starts = [round(t, 3) for t, c, lv, cmd, dur in ac(h, t0) if c == 0x0A and lv == 200]
    assert starts[0] == 3.0 and [t for t in starts if t > 130][0] == 243.0