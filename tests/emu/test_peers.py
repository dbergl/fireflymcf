"""Simulated RV-C peers."""
from fireflymcf.emu import rvc
from fireflymcf.emu.machine import Machine
from fireflymcf.emu.peers import DimmerModule
from fireflymcf.emu.periph.bxcan import Frame


def test_dimmer_toggle_and_status():
    m = Machine()
    sent = []
    dim = m.canbus.add_peer(DimmerModule([0x20]))
    dim.send = lambda dgn, data, prio=6: sent.append((dgn, bytes(data)))
    cmd = lambda level, c: Frame(rvc.can_id(rvc.DC_DIMMER_COMMAND_2, 0x9F),  # noqa: E731
                                 bytes([0x20, 0xFF, level, c, 0xFF, 0, 0xFF, 0xFF]), True)
    dim.on_frame(cmd(0xFA, DimmerModule.TOGGLE))
    assert sent[-1] == (rvc.DC_DIMMER_STATUS_3, bytes([0x20, 0xFF, 200, 0, 0xFF, 0xFF, 0x04, 0xFF]))
    dim.on_frame(cmd(0xFA, DimmerModule.TOGGLE))
    assert sent[-1][1][2] == 0 and sent[-1][1][6] == 0x00
    dim.on_frame(cmd(100, DimmerModule.SET))
    assert sent[-1][1][2] == 100


def test_can_id_round_trip():
    ident = rvc.can_id(rvc.DC_DIMMER_COMMAND_2, 0x9F, prio=6)
    assert ident == 0x19FEDB9F
    assert rvc.split_id(ident) == (6, rvc.DC_DIMMER_COMMAND_2, 0x9F)


def g12_with_capture(path=None):
    from fireflymcf.emu.peers import ProtocolG12 as G12
    m = Machine()
    g = m.canbus.add_peer(G12(path=path))
    sent = []
    g.send = lambda dgn, data, prio=6: sent.append((dgn, list(data)))
    return g, sent


def write(sel, value, func=0xD1):
    v = value & 0xFFFF
    return Frame(rvc.can_id(0x1FED9, 0x9F), bytes([0xFF, 0x96, sel, 0x0F, v & 0xFF, v >> 8, func, 0xEA]),
                 True)


def test_g12_set_query_and_floorplan(tmp_path):
    path = str(tmp_path / "g12.json")
    g, sent = g12_with_capture(path)
    g.on_frame(write(0xF5, 3))                                   # select RD
    assert g.sel[0xF5] == 3
    assert sent[-1] == (0x15FCE, [0xF5, 0, 3, 0, 0, 0, 0, 0])
    g.on_frame(Frame(rvc.can_id(0x1FED9, 0x9F), bytes([0xFF, 0x96, 0xF1, 0x0F, 0xFF, 0xFF, 0xD3, 0xFF]),
                     True))
    assert sent[-1] == (0x15FCE, [0xF1, 0, 0x0D, 0, 0, 0, 0, 0])  # QUERY answers, no change
    assert g._heartbeat()[1][5] == 3                             # 1FACF carries F5 at byte 5
    g2, _ = g12_with_capture(path)                               # persisted like the real G12
    assert g2.sel[0xF5] == 3


def test_g12_delta_selectors():
    g, _ = g12_with_capture()
    g.sel[0xCC] = 30
    g.on_frame(write(0xCC, 5))
    assert g.sel[0xCC] == 35
    g.on_frame(write(0xCC, -10))
    assert g.sel[0xCC] == 25
    g.on_frame(write(0x16, 90))                                  # absolute selector
    assert g.sel[0x16] == 90


def test_inverter_obeys_command():
    from fireflymcf.emu.peers import Inverter
    m = Machine()
    inv = m.canbus.add_peer(Inverter())
    sent = []
    inv.send = lambda dgn, data, prio=6: sent.append((dgn, list(data)))
    inv.on_frame(Frame(rvc.can_id(0x1FFD3, 0x9F), bytes([1, 0x11, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x11]), True))
    assert inv.enabled and sent[-1][1][:3] == [1, 1, 0x50]
    inv.on_frame(Frame(rvc.can_id(0x1FFD3, 0x9F), bytes([1, 0x10, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x11]), True))
    assert not inv.enabled and sent[-1][1][1] == 0


def test_g12_d2_is_delta_like_the_panel_steppers():
    g, _ = g12_with_capture()
    g.sel[0x31] = 1000                                           # 50.0 V
    g.on_frame(write(0x31, 2, func=0xD2))                        # panel "+": +0.1 V
    g.on_frame(write(0x31, 0, func=0xD2))                        # zero-delta commit
    assert g.sel[0x31] == 1002
    g.on_frame(write(0x31, -2, func=0xD2))
    assert g.sel[0x31] == 1000


def test_g12_tank_relay_buckets_like_the_coach():
    """High raw means empty (BUS-BASELINE PC.10): 0 % at or above CC, 33 % from CD up to CC,
    67 % from CE up to CD, 100 % below CE. The boundaries are those the G12's own bucketer gives
    when the MCF's G12 blob runs (`tests/emu/test_g12.py::test_bucketer_boundaries`)."""
    from fireflymcf.emu.peers import ProtocolG12 as G12
    g = G12(tanks={1: 0})
    want = {65535: 0, 54001: 0, 54000: 0, 53999: 33, 40000: 33, 39999: 67, 25000: 67,
            24999: 100, 0: 100}
    got = {}
    for raw in want:
        g.tanks[1] = raw
        got[raw] = g.tank_percent(1)
    assert got == want


def test_g12_tank_thresholds_default_to_the_coach():
    """Unset thresholds are the coach's (PC.10: CC/CD/CE = 54000/40000/25000), and the demo
    network's selectors and tanks are on that scale."""
    from fireflymcf.emu.peers import DEMO_G12_SELECTORS, DEMO_TANKS, ProtocolG12 as G12
    assert {k: G12().sel[k] for k in (0xCC, 0xCD, 0xCE)} == {0xCC: 54000, 0xCD: 40000,
                                                             0xCE: 25000}
    assert {k: DEMO_G12_SELECTORS[k] for k in (0xCC, 0xCD, 0xCE)} == {0xCC: 54000, 0xCD: 40000,
                                                                      0xCE: 25000}
    g = G12(DEMO_G12_SELECTORS, tanks=DEMO_TANKS)
    assert [g.tank_percent(i) for i in (1, 2, 3, 4)] == [100, 67, 33, 0]
    g = G12({0xCC: 60000, 0xCD: 30000, 0xCE: 1000}, tanks={1: 45000})   # given ones win
    assert (g.sel[0xCC], g.tank_percent(1)) == (60000, 33)


def test_g12_tank_broadcasts_at_one_hertz():
    """Each tank's 0BFC1 raw report and its 1FFB7 TANK_STATUS go out about once a second, as on
    the coach (PC.62: "four instances x 1 Hz")."""
    from fireflymcf.emu.peers import ProtocolG12 as G12
    m = Machine()
    g = m.canbus.add_peer(G12(tanks={1: 20000, 2: 30000, 3: 45000, 4: 60000}))
    sent = []
    g.send = lambda dgn, data, prio=6: sent.append((dgn, list(data)))
    for k in range(1, 1001):                                      # 10 s in 10 ms steps
        m.cycles = k * m.cpu_hz // 100
        m.canbus.next_event()
    for dgn in (G12.TANK_DGN, rvc.TANK_STATUS):
        per = {}
        for d, data in sent:
            if d == dgn:
                per[data[0]] = per.get(data[0], 0) + 1
        assert len(per) == 4 and all(9 <= n <= 11 for n in per.values()), (hex(dgn), per)


def test_g12_relays_grey2_as_tank_instance_18():
    from fireflymcf.emu.peers import ProtocolG12 as G12
    assert G12.TANK_STATUS_INSTANCE[4] == 18


def test_dimmer_memory_off_and_resume():
    m = Machine()
    sent = []
    dim = m.canbus.add_peer(DimmerModule([0x16]))
    dim.send = lambda dgn, data, prio=6: sent.append(bytes(data))
    cmd = lambda level, c: Frame(rvc.can_id(rvc.DC_DIMMER_COMMAND_2, 0x9F),  # noqa: E731
                                 bytes([0x16, 0xFF, level, c, 0xFF, 0, 0xFF, 0xFF]), True)
    dim.on_frame(cmd(120, DimmerModule.SET))                 # 60 %
    dim.on_frame(cmd(0, DimmerModule.MEMORY_OFF))            # Light Master OFF
    assert sent[-1][2] == 0 and sent[-1][6] == 0x00
    dim.on_frame(cmd(DimmerModule.RESUME, DimmerModule.SET))  # Light Master ON
    assert sent[-1][2] == 120
