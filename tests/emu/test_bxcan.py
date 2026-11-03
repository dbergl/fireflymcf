"""bxCAN acceptance filtering with the filter set the LCD firmware programs."""
from fireflymcf.emu.machine import Machine
from fireflymcf.emu.periph import bxcan
from fireflymcf.emu.periph.bxcan import Frame


def can_with_firmware_filters():
    m = Machine()
    c = m.periph["CAN1"]
    banks = [(0x0777FCFC, 0x1FFFFFFC), (0x0757F804, 0x1FFFF804), (0x0754F804, 0x1FFFF804),
             (0x0747F804, 0x1FFFF804), (0x00000004, 0x00000004)]
    for i, (r1, r2) in enumerate(banks):
        c.set(bxcan.FBANK + 8 * i, r1)
        c.set(bxcan.FBANK + 8 * i + 4, r2)
    c.set(bxcan.FS1R, 0x1F)
    c.set(bxcan.FFA1R, 0x10)
    c.set(bxcan.FA1R, 0x1F)
    return c


def test_address_conflict_matches_bank0():
    c = can_with_firmware_filters()
    assert c.match(Frame(0x18EEFF9F, b"", True)) == (0, 0)


def test_requests_and_acks_go_to_fifo0():
    c = can_with_firmware_filters()
    assert c.match(Frame(0x18EAFF42, b"", True)) == (0, 1)
    assert c.match(Frame(0x18EA9F42, b"", True)) == (0, 2)
    assert c.match(Frame(0x18E8FF42, b"", True)) == (0, 3)


def test_other_traffic_goes_to_fifo1_fmi0():
    c = can_with_firmware_filters()
    assert c.match(Frame(0x19FFFD81, b"", True)) == (1, 0)
    assert c.match(Frame(0x123, b"", False)) is None   # bank 4 requires IDE


def test_rdtr_reports_fmi():
    c = can_with_firmware_filters()
    c.set(bxcan.MCR, 0)
    c.receive(Frame(0x19FFFD81, b"\x01\x02", True))
    assert c.read_reg(bxcan.RF1R) == 1
    assert c.read_reg(bxcan.RX_BASE + 0x10 + 4) == 2   # FMI 0, DLC 2
