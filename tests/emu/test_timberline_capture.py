"""The Timberline peer against a coach's own unit: a 90 s receive-only capture of source 0x65
(captures/timberline-sa65-2026-11-18.log, heater idle, nothing transmitted)."""
from collections import Counter
from pathlib import Path

from test_timberline import bus_with, run

from fireflymcf.emu.peers import Timberline

CAPTURE = Path(__file__).resolve().parents[2] / "captures" / "timberline-sa65-2026-11-18.log"


def _rows(lines):
    out = []
    for line in lines:
        ts, _ch, frame = line.split()
        ident, _, data = frame.partition("#")
        out.append((float(ts.strip("()")), ident, data))
    return out


def test_the_peer_sends_what_the_unit_sent():
    """Every identifier at the captured rate, and every payload the idle unit sent that does not
    carry a live measurement (the temperatures, ambient and the 0x87 minute count drift on the
    coach) byte for byte."""
    cap = _rows(CAPTURE.read_text().splitlines())
    span = cap[-1][0] - cap[0][0]
    m, bus, seen = bus_with(Timberline())
    run(m, bus, span)
    ours = [("%08X" % f.can_id, f.data.hex().upper()) for _t, f in seen]

    def key(ident, data):
        return ident + (data[:2] if ident == "19EF6565" else "")
    want = Counter(key(i, d) for _t, i, d in cap)
    got = Counter(key(i, d) for i, d in ours)
    assert set(got) == set(want)
    skipped = {"19FECA65": 3, "19FFE465": 1}   # cycles the capture lacks them (docstring)
    for k in want:
        assert -1 <= got[k] - want[k] <= 1 + skipped.get(k, 0), (k, got[k], want[k])
    live = ("19FFF765", "19FF9C65", "19EF656584", "19EF656587")
    fixed = {(i, d) for _t, i, d in cap if not key(i, d).startswith(live)}
    assert fixed <= set(ours)


TOGGLE = CAPTURE.with_name("timberline-element-toggle-2026-11-18.log")


def test_on_change_shape_is_the_units():
    """The element toggled twice on the unit's own panel (120 s of can1, receive only): each
    change is two 1FFF7 out of the cycle `REPEAT_S` apart, byte 6 as the last slot's, and the next
    slot carries the new byte 6. The peer's own two copies match."""
    rows = [(t, d) for t, i, d in _rows(TOGGLE.read_text().splitlines()) if i == "19FFF765"]
    changes = [k for k in range(1, len(rows)) if rows[k][1][2:4] != rows[k - 1][1][2:4]]
    assert len(changes) == 2
    for k in changes:
        (t0, d0), (t1, d1), (t2, d2) = rows[k], rows[k + 1], rows[k + 2]
        assert d1 == d0 and abs((t1 - t0) - Timberline.REPEAT_S) < 0.006
        assert d0[12:14] == rows[k - 1][1][12:14] and d2[12:14] != d0[12:14]
        assert 4.9 < t2 - rows[k - 1][0] < 5.1                 # the cycle's phase kept
    m, bus, seen = bus_with(Timberline(element_s=0.5))
    run(m, bus, 1.0)
    bus.peers[0].panel_set_mode(2)
    run(m, bus, 4.5)
    ours = [(t, f.data.hex().upper()) for t, f in seen if rvc_dgn(f) == Timberline.WH_STATUS]
    (a, da), (b, db), (c, dc) = ours[1:4]
    assert da == db and abs((b - a) - Timberline.REPEAT_S) < 0.002 and da[12:14] != dc[12:14]


def rvc_dgn(frame):
    from fireflymcf.emu import rvc
    return rvc.split_id(frame.can_id)[1]


COMMANDED = CAPTURE.with_name("timberline-command-toggle-2026-11-18.log")


def test_a_command_is_answered_once_within_10_ms():
    """The element switched by 1FFF6 from source 0x82, twice: each command is followed by one
    1FFF7 with the new mode within 10 ms, byte 6 as the last slot's; no ACK from 0x65."""
    rows = _rows(COMMANDED.read_text().splitlines())
    cmds = [(t, d) for t, i, d in rows if i[2:6] == "FFF6"]
    assert len(cmds) == 2
    status = [(t, d) for t, i, d in rows if i == "19FFF765"]
    for t, d in cmds:
        after = [(u, e) for u, e in status if t <= u < t + 1.0]
        before = [e for u, e in status if u < t][-1]
        assert len(after) == 1, after
        u, e = after[0]
        assert u - t <= 0.010 and e[2:4] == d[2:4] and e[12:14] == before[12:14]
    assert not [i for _t, i, _d in rows if i[2:4] == "E8" and "65" in (i[4:6], i[6:8])]
