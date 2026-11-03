"""candump replay."""
from fireflymcf.emu.replay import CandumpReplay, parse_candump


def write_log(tmp_path):
    p = tmp_path / "bus.log"
    p.write_text("(100.000000) can1 19FFB79C#000003FFFFFFFFFF\n"
                 "(100.500000) can1 19FECA9F#0584FFFFFFFFFFFF\n"   # the recorded panel: skipped
                 "(101.000000) can1 19FFFD80#01FF6E0400000000\n")
    return str(p)


def test_parse_candump(tmp_path):
    frames = parse_candump(write_log(tmp_path))
    assert [round(t, 3) for t, _ in frames] == [0.0, 0.5, 1.0]
    assert frames[0][1].can_id == 0x19FFB79C and frames[0][1].extended
    assert frames[2][1].data == bytes.fromhex("01FF6E0400000000")


def test_replay_skips_panel_and_keeps_timing(tmp_path):
    from fireflymcf.emu.machine import Machine

    m = Machine()
    rep = m.canbus.add_peer(CandumpReplay(write_log(tmp_path)))
    sent = []
    m.canbus.deliver = lambda frame, origin=None: sent.append(frame.can_id)
    assert rep.pump(m.cycles) == m.cpu_hz            # next frame (from 0x80) is 1 s away
    assert sent == [0x19FFB79C]
    m.cycles += m.cpu_hz
    assert rep.pump(m.cycles) is None and sent == [0x19FFB79C, 0x19FFFD80]
