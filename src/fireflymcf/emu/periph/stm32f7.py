"""STM32F7 on-chip peripherals (RM0385 register layouts)."""
import logging

from .base import Generic, Peripheral
from .bxcan import BxCAN
from .display import DMA2D, LTDC
from .gt911 import GT911
from .i2c import I2C, Memory
from .rfremote import RFRemote

log = logging.getLogger("periph")

PERIPHERAL_NAMES = {
    0x40000000: "TIM2", 0x40000400: "TIM3", 0x40000800: "TIM4", 0x40000C00: "TIM5",
    0x40001000: "TIM6", 0x40001400: "TIM7", 0x40001800: "TIM12", 0x40001C00: "TIM13",
    0x40002000: "TIM14", 0x40002400: "LPTIM1", 0x40002800: "RTC", 0x40002C00: "WWDG",
    0x40003000: "IWDG", 0x40003800: "SPI2", 0x40003C00: "SPI3", 0x40004000: "SPDIFRX",
    0x40004400: "USART2", 0x40004800: "USART3", 0x40004C00: "UART4", 0x40005000: "UART5",
    0x40005400: "I2C1", 0x40005800: "I2C2", 0x40005C00: "I2C3", 0x40006000: "I2C4",
    0x40006400: "CAN1", 0x40006800: "CAN2", 0x40006C00: "CEC", 0x40007000: "PWR",
    0x40007400: "DAC", 0x40007800: "UART7", 0x40007C00: "UART8", 0x40010000: "TIM1",
    0x40010400: "TIM8", 0x40011000: "USART1", 0x40011400: "USART6", 0x40012000: "ADC",
    0x40012C00: "SDMMC1", 0x40013000: "SPI1", 0x40013400: "SPI4", 0x40013800: "SYSCFG",
    0x40013C00: "EXTI", 0x40014000: "TIM9", 0x40014400: "TIM10", 0x40014800: "TIM11",
    0x40015000: "SPI5", 0x40015400: "SPI6", 0x40015800: "SAI1", 0x40015C00: "SAI2",
    0x40016800: "LTDC", 0x40020000: "GPIOA", 0x40020400: "GPIOB", 0x40020800: "GPIOC",
    0x40020C00: "GPIOD", 0x40021000: "GPIOE", 0x40021400: "GPIOF", 0x40021800: "GPIOG",
    0x40021C00: "GPIOH", 0x40022000: "GPIOI", 0x40022400: "GPIOJ", 0x40022800: "GPIOK",
    0x40023000: "CRC", 0x40023800: "RCC", 0x40023C00: "FLASH", 0x40026000: "DMA1",
    0x40026400: "DMA2", 0x40028000: "ETH", 0x4002B000: "DMA2D", 0x40040000: "OTG_HS",
    0x50000000: "OTG_FS", 0x50050000: "DCMI", 0x50060800: "RNG", 0xA0000000: "FMC",
    0xA0001000: "QSPI",
}


class RCC(Peripheral):
    """Clock ready flags follow their enable bits immediately."""

    name = "RCC"

    def reset(self):
        self.set(0x00, 0x00000083)  # CR: HSION | HSIRDY
        self.set(0x04, 0x24003010)  # PLLCFGR reset value
        self.set(0x74, 0x0E000000)  # CSR: reset flags (POR/PIN)

    def write_reg(self, off, value, old):
        if off == 0x00:  # CR: HSI, HSE, PLL, PLLI2S, PLLSAI
            for on in (0, 16, 24, 26, 28):
                value = (value & ~(1 << (on + 1))) | ((value >> on & 1) << (on + 1))
        elif off == 0x08:  # CFGR: SWS mirrors SW
            value = (value & ~0xC) | ((value & 3) << 2)
        elif off == 0x70:  # BDCR: LSERDY follows LSEON
            value = (value & ~2) | ((value & 1) << 1)
        elif off == 0x74:  # CSR: LSIRDY follows LSION; RMVF clears reset flags
            value = (value & ~2) | ((value & 1) << 1)
            if value & (1 << 24):
                value &= 0x00FFFFFF
        self.set(off, value)


class PWR(Peripheral):
    name = "PWR"

    def reset(self):
        self.set(0x04, 1 << 14)  # CSR1.VOSRDY

    def write_reg(self, off, value, old):
        self.set(off, value)
        if off == 0x00:  # CR1: ODEN -> ODRDY, ODSWEN -> ODSWRDY
            csr = self.get(0x04) & ~(3 << 16)
            self.set(0x04, csr | (value & (3 << 16)))


class FlashCtl(Peripheral):
    """Flash interface: never busy.  Programming/erase is not modelled yet."""

    name = "FLASH"

    def read_reg(self, off):
        if off == 0x0C:  # SR
            return 0
        return self.get(off)


class QSPI(Peripheral):
    """Quad-SPI controller.  The flash contents are pre-mapped at 0x90000000."""

    name = "QSPI"

    def read_reg(self, off):
        if off == 0x08:  # SR: never busy, transfer complete, FIFO threshold, match
            return (1 << 1) | (1 << 2) | (1 << 3)
        return self.get(off)


class FMC(Peripheral):
    name = "FMC"
    size = 0x1000

    def read_reg(self, off):
        if off == 0x158:  # SDSR: not busy
            return 0
        return self.get(off)


# Clock tree as programmed by this firmware: SYSCLK 160 MHz (SysTick reload
# 159999 for 1 kHz), APB1 40 MHz (timers x2 = 80 MHz: TIM2 PSC 79 -> 1 MHz),
# APB2 80 MHz (timers 160 MHz).  Not derived from RCC registers.
CPU_HZ = 160_000_000
APB1_HZ = 40_000_000
APB1_TIMER_HZ = 80_000_000
APB2_TIMER_HZ = 160_000_000

# name -> (base, IRQn for update / global interrupt, timer clock)
TIMERS = {
    "TIM1": (0x40010000, 25, APB2_TIMER_HZ), "TIM2": (0x40000000, 28, APB1_TIMER_HZ),
    "TIM3": (0x40000400, 29, APB1_TIMER_HZ), "TIM4": (0x40000800, 30, APB1_TIMER_HZ),
    "TIM5": (0x40000C00, 50, APB1_TIMER_HZ), "TIM6": (0x40001000, 54, APB1_TIMER_HZ),
    "TIM7": (0x40001400, 55, APB1_TIMER_HZ), "TIM8": (0x40010400, 44, APB2_TIMER_HZ),
    "TIM9": (0x40014000, 24, APB2_TIMER_HZ),
}
UARTS = {
    "USART1": (0x40011000, 37), "USART2": (0x40004400, 38), "USART3": (0x40004800, 39),
    "UART4": (0x40004C00, 52), "UART5": (0x40005000, 53), "USART6": (0x40011400, 71),
}

I2CS = {
    "I2C1": (0x40005400, 31), "I2C2": (0x40005800, 33), "I2C3": (0x40005C00, 72),
    "I2C4": (0x40006000, 95),
}


class Timer(Peripheral):
    """Up-counting timer with update interrupt (enough for HAL timebase/PWM setup)."""

    CR1, DIER, SR, EGR, CNT, PSC, ARR = 0x00, 0x0C, 0x10, 0x14, 0x24, 0x28, 0x2C

    def __init__(self, m, base, name, irq, clock):
        self.irq = irq
        self.scale = CPU_HZ / clock  # CPU cycles per timer clock
        super().__init__(m, base, name)
        self.origin = 0  # machine cycle at which CNT was last 0

    def _period(self):
        """CPU cycles per counter overflow."""
        return (self.get(self.PSC) + 1) * (self.get(self.ARR) + 1) * self.scale

    def running(self):
        return self.get(self.CR1) & 1 and self.get(self.ARR)

    def _sync(self):
        """Account for overflows since ``origin``; returns cycles into the period."""
        per = self._period()
        elapsed = self.m.cycles - self.origin
        if elapsed >= per:
            n = int(elapsed // per)
            self.origin += n * per
            self.set(self.SR, self.get(self.SR) | 1)
            if self.get(self.DIER) & 1:
                self.m.core.irq(self.irq)
            elapsed -= n * per
        return per, elapsed

    def next_event(self):
        # Without the update interrupt an overflow only sets UIF, which _sync
        # catches up on when SR is read; scheduling it would wake the idle skip
        # at the PWM rate (TIM3 runs at ~20 kHz for the backlight).
        if not self.running():
            return None
        per, elapsed = self._sync()
        if not self.get(self.DIER) & 1:
            return None
        return max(1, int(per - elapsed))

    def read_reg(self, off):
        if off == self.CNT and self.running():
            ticks = (self.m.cycles - self.origin) / self.scale / (self.get(self.PSC) + 1)
            return int(ticks) % (self.get(self.ARR) + 1)
        if off == self.SR and self.running():
            self._sync()
        return self.get(off)

    def write_reg(self, off, value, old):
        if off == self.SR:  # rc_w0
            value &= old
        elif off == self.EGR:
            if value & 1:
                self.origin = self.m.cycles
            return
        elif off == self.CR1 and value & 1 and not old & 1:
            self.origin = self.m.cycles
        elif off == self.CNT:
            self.origin = self.m.cycles - value * (self.get(self.PSC) + 1) * self.scale
        self.set(off, value)
        self.m.reschedule()


class USART(Peripheral):
    """STM32F7 USART: transmit is instant; received bytes are queued with feed()."""

    CR1, ISR, ICR, RDR, TDR = 0x00, 0x1C, 0x20, 0x24, 0x28

    def __init__(self, m, base, name, irq):
        self.irq = irq
        self.rx = bytearray()
        self.tx_line = bytearray()
        self.sink = None  # optional callable(bytes)
        super().__init__(m, base, name)

    def feed(self, data):
        self.rx += data
        self._update_irq()

    def _isr(self):
        cr1 = self.get(self.CR1)
        v = (1 << 7) | (1 << 6)  # TXE, TC
        if cr1 & 8:
            v |= 1 << 21  # TEACK
        if cr1 & 4:
            v |= 1 << 22  # REACK
        if self.rx:
            v |= 1 << 5  # RXNE
        return v

    def _update_irq(self):
        cr1 = self.get(self.CR1)
        isr = self._isr()
        if cr1 & 1 and ((cr1 & isr & (1 << 7)) or (cr1 & isr & (1 << 6)) or (cr1 & isr & (1 << 5))):
            self.m.core.irq(self.irq)

    def read_reg(self, off):
        if off == self.ISR:
            return self._isr()
        if off == self.RDR:
            if self.rx:
                b = self.rx.pop(0)
                self._update_irq()
                return b
            return 0
        return self.get(off)

    def write_reg(self, off, value, old):
        if off == self.TDR:
            self._tx(value & 0xFF)
            self._update_irq()
            return
        self.set(off, value)
        if off == self.CR1:
            self._update_irq()

    def _tx(self, b):
        if self.sink:
            self.sink(bytes([b]))
        if b == 0x0A or len(self.tx_line) > 200:
            log.info("%s tx: %r", self.name, self.tx_line.decode("latin-1"))
            self.tx_line.clear()
        elif b != 0x0D:
            self.tx_line.append(b)


class SYSCFG(Peripheral):
    name = "SYSCFG"

    def read_reg(self, off):
        if off == 0x20:  # CMPCR: compensation cell ready once powered
            v = self.get(off)
            return v | (v & 1) << 8
        return self.get(off)


class CRC(Peripheral):
    """CRC unit with programmable polynomial size, init and bit reversal (RM0385 s.13)."""

    name = "CRC"
    DR, IDR, CR, INIT, POL = 0x00, 0x04, 0x08, 0x10, 0x14

    def reset(self):
        self.set(self.DR, 0xFFFFFFFF)
        self.set(self.INIT, 0xFFFFFFFF)
        self.set(self.POL, 0x04C11DB7)

    def _width(self):
        return (32, 16, 8, 7)[(self.get(self.CR) >> 3) & 3]

    @staticmethod
    def _reflect(v, bits):
        return int(f"{v:0{bits}b}"[::-1], 2)

    def _feed(self, value, nbytes):
        cr = self.get(self.CR)
        width = self._width()
        poly = self.get(self.POL) & ((1 << width) - 1)
        crc = self.get(self.DR) & ((1 << width) - 1)
        rev_in = (cr >> 5) & 3
        nbits = nbytes * 8
        if rev_in == 1:  # by byte
            value = int.from_bytes(bytes(self._reflect(b, 8) for b in value.to_bytes(nbytes, "little")), "little")
        elif rev_in == 2:  # by half-word
            value = sum(self._reflect((value >> s) & 0xFFFF, 16) << s for s in range(0, nbits, 16)) if nbits >= 16 else self._reflect(value, 8)
        elif rev_in == 3:
            value = self._reflect(value, nbits)
        top = 1 << (width - 1)
        for i in range(nbits - 1, -1, -1):
            bit = (value >> i) & 1
            fb = bool(crc & top) ^ bit
            crc = (crc << 1) & ((1 << width) - 1)
            if fb:
                crc ^= poly
        self.set(self.DR, crc)

    def read_reg(self, off):
        if off == self.DR:
            width = self._width()
            crc = self.get(self.DR) & ((1 << width) - 1)
            if self.get(self.CR) & (1 << 7):  # REV_OUT
                crc = self._reflect(crc, width)
            return crc
        return self.get(off)

    def write_reg(self, off, value, old):
        if off == self.CR:
            if value & 1:  # RESET
                self.set(self.DR, self.get(self.INIT))
            self.set(off, value & ~1)
        else:
            self.set(off, value)

    def bus_write(self, off, size, value):
        if off == self.DR:  # data width is the access width
            self._feed(value & ((1 << (size * 8)) - 1), size)
        else:
            super().bus_write(off, size, value)


class GPIO(Peripheral):
    """GPIO port.  IDR = outputs for output pins, else external level, else pull."""

    MODER, PUPDR, IDR, ODR, BSRR = 0x00, 0x0C, 0x10, 0x14, 0x18

    def reset(self):
        self.external = {}  # pin -> 0/1 driven from outside the MCU

    def drive(self, pin, level):
        self.external[pin] = 1 if level else 0

    def read_reg(self, off):
        if off == self.IDR:
            moder, pupdr, odr = self.get(self.MODER), self.get(self.PUPDR), self.get(self.ODR)
            v = 0
            for pin in range(16):
                mode = (moder >> (2 * pin)) & 3
                if mode == 1:
                    bit = odr >> pin & 1
                elif pin in self.external:
                    bit = self.external[pin]
                else:
                    bit = 1 if (pupdr >> (2 * pin)) & 3 == 1 else 0
                v |= bit << pin
            return v
        return self.get(off)

    def write_reg(self, off, value, old):
        if off == self.BSRR:
            odr = self.get(self.ODR)
            odr = (odr | (value & 0xFFFF)) & ~(value >> 16)
            self.set(self.ODR, odr)
            return
        self.set(off, value)


class OTG(Peripheral):
    """USB OTG core, unplugged: resets complete instantly and the AHB is idle."""

    size = 0x40000
    GRSTCTL, GINTSTS = 0x10, 0x14

    def read_reg(self, off):
        if off == self.GRSTCTL:
            return self.get(off) & ~0x3F | (1 << 31)  # CSRST/FIFO flush done, AHBIDL
        if off == self.GINTSTS:
            return self.get(off)  # device mode (CMOD=0), nothing pending
        return self.get(off)

    def write_reg(self, off, value, old):
        if off == self.GINTSTS:  # rc_w1
            self.set(off, old & ~value)
            return
        self.set(off, value)


class EXTI(Peripheral):
    """External interrupt controller: lines are raised with trigger(line)."""

    name = "EXTI"
    IMR, EMR, RTSR, FTSR, SWIER, PR = 0x00, 0x04, 0x08, 0x0C, 0x10, 0x14

    @staticmethod
    def irq_for(line):
        if line < 5:
            return 6 + line
        return 23 if line < 10 else 40

    def trigger(self, line, rising=True):
        edge = self.RTSR if rising else self.FTSR
        if self.get(edge) >> line & 1:
            self.set(self.PR, self.get(self.PR) | 1 << line)
            if self.get(self.IMR) >> line & 1:
                self.m.core.irq(self.irq_for(line))

    def write_reg(self, off, value, old):
        if off == self.PR:  # rc_w1
            self.set(off, old & ~value)
            return
        if off == self.SWIER:
            for line in range(23):
                if value >> line & 1 and not old >> line & 1:
                    self.set(self.PR, self.get(self.PR) | 1 << line)
                    if self.get(self.IMR) >> line & 1:
                        self.m.core.irq(self.irq_for(line))
        self.set(off, value)


def attach_all(m):
    """Instantiate modelled peripherals; everything else becomes a logging Generic."""
    made = {}
    for cls, base in ((RCC, 0x40023800), (PWR, 0x40007000), (FlashCtl, 0x40023C00),
                      (QSPI, 0xA0001000), (FMC, 0xA0000000),
                      (SYSCFG, 0x40013800), (CRC, 0x40023000),
                      (OTG, 0x50000000), (OTG, 0x40040000)):
        p = m.bus.attach(cls(m, base, PERIPHERAL_NAMES.get(base)))
        made[p.name] = p
    for name, (base, irq, clock) in TIMERS.items():
        made[name] = m.bus.attach(Timer(m, base, name, irq, clock))
    for name, (base, irq) in UARTS.items():
        made[name] = m.bus.attach(USART(m, base, name, irq))
    for name, (base, irq) in I2CS.items():
        made[name] = m.bus.attach(I2C(m, base, name, irq))
    # Board EEPROM probed by the firmware at I2C4/0x50 (blank until we learn its layout).
    m.eeprom = made["I2C4"].devices[0x50] = Memory(32 * 1024, addr_bytes=2)
    m.touch = made["I2C2"].devices[0x14] = GT911()
    made["CAN1"] = m.bus.attach(BxCAN(m, 0x40006400, "CAN1", 19, 20, 21, 22))
    made["LTDC"] = m.bus.attach(LTDC(m, 0x40016800, 88, CPU_HZ))
    made["DMA2D"] = m.bus.attach(DMA2D(m, 0x4002B000, 90))
    made["EXTI"] = m.bus.attach(EXTI(m, 0x40013C00))
    # GT911 INT -> EXTI line 5 (the firmware enables only line 5, rising edge).
    m.touch.on_irq = lambda: made["EXTI"].trigger(5)
    m.touch.bind(m)
    made["touch"] = m.touch  # in periph so the machine schedules its scans
    # Wireless control panel paired over the RF module on UART4.
    m.rfremote = made["rfremote"] = RFRemote(m, made["UART4"])
    for base, name in PERIPHERAL_NAMES.items():
        if name.startswith("GPIO"):
            made[name] = m.bus.attach(GPIO(m, base, name))
    return made

