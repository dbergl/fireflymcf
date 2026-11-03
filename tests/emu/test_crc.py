from fireflymcf.emu.machine import Machine


def crc_of(m, data, cr, init, pol):
    c = m.periph["CRC"]
    c.bus_write(0x14, 4, pol)
    c.bus_write(0x10, 4, init)
    c.bus_write(0x08, 4, cr | 1)
    for b in data:
        c.bus_write(0x00, 1, b)
    return c.bus_read(0x00, 4)


def test_crc32_mpeg2():
    m = Machine()
    assert crc_of(m, b"123456789", 0, 0xFFFFFFFF, 0x04C11DB7) == 0x0376E6E7


def test_crc16_aug_ccitt():
    # CRC-16/AUG-CCITT: poly 0x1021, init 0x1D0F, no reflection -> 0xE5CC
    m = Machine()
    assert crc_of(m, b"123456789", 1 << 3, 0x1D0F, 0x1021) == 0xE5CC


def test_crc32_iso_hdlc_reflected():
    m = Machine()
    v = crc_of(m, b"123456789", (1 << 5) | (1 << 7), 0xFFFFFFFF, 0x04C11DB7)
    assert v ^ 0xFFFFFFFF == 0xCBF43926
