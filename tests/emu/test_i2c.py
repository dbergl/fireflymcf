"""I2C controller: HAL-style interrupt-driven memory read from the GT911."""
from fireflymcf.emu.machine import Machine
from fireflymcf.emu.periph import i2c

I2C2_EV = 16 + 33


def test_mem_read_it_raises_interrupts():
    m = Machine()
    bus = m.periph["I2C2"]
    m.core.enabled.add(I2C2_EV)
    m.touch.touch([(400, 425)])
    w = bus.bus_write
    w(i2c.CR1, 4, 1 | 0x76)                            # PE + TXIE RXIE STOPIE TCIE NACKIE
    # Write phase: register address 0x814E, software end (TC)
    w(i2c.CR2, 4, (0x14 << 1) | (2 << 16) | i2c.START)
    assert I2C2_EV in m.core.pending and bus.isr & i2c.TXIS
    m.core.pending.clear()
    w(i2c.TXDR, 4, 0x81)
    w(i2c.TXDR, 4, 0x4E)
    assert bus.isr & i2c.TC and I2C2_EV in m.core.pending
    # Read phase: 6 bytes, autoend
    m.core.pending.clear()
    w(i2c.CR2, 4, (0x14 << 1) | i2c.RD_WRN | (6 << 16) | i2c.AUTOEND | i2c.START)
    assert I2C2_EV in m.core.pending
    data = bytes(bus.bus_read(i2c.RXDR, 1) for _ in range(6))
    assert data == bytes([0x81, 0, 0x90, 0x01, 0xA9, 0x01])  # 1 point at (400, 425)
    assert bus.isr & i2c.STOPF


def test_eeprom_persists_to_file(tmp_path):
    path = str(tmp_path / "eeprom.bin")
    m = Machine()
    m.eeprom.attach_file(path)
    m.eeprom.write(bytes([0x00, 0x7F, 0x00]))      # terms accepted flag
    m2 = Machine()
    m2.eeprom.attach_file(path)
    m2.eeprom.write(bytes([0x00, 0x7F]))
    assert m2.eeprom.read(1) == b"\x00"


def test_gt911_keeps_reporting_while_held():
    m = Machine()
    gt = m.touch
    pulses = []
    gt.on_irq = lambda: pulses.append(m.cycles)
    scan = m.cpu_hz // 100
    gt.touch([(135, 303)])
    assert gt.regs[0x814E] == 0x81 and len(pulses) == 1
    for _ in range(3):                      # host reads and acks; next scan re-reports
        gt.write(bytes([0x81, 0x4E, 0]))
        m.cycles += scan
        gt.next_event()
        assert gt.regs[0x814E] == 0x81
    assert len(pulses) == 4
    gt.touch([])                            # release: one empty report, then quiet
    assert gt.regs[0x814E] == 0x80
    gt.write(bytes([0x81, 0x4E, 0]))
    m.cycles += scan
    assert gt.next_event() is None and len(pulses) == 5


def test_rf_remote_status_packet():
    m = Machine()
    pkt = m.rfremote.status_packet()
    # sync, byte0, ID FFFFFFFF, type A5, 21.0 C = 336/16 (0x150), 2800 mV (0x0AF0)
    assert pkt.hex() == "f00f00ffffffffa55001f00a"
    assert (2800 + 25) // 50 == 0x38                 # what the panel reports (0.05 V units)
