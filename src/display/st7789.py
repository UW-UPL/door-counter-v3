import time

import RPi.GPIO as GPIO
import spidev


WIDTH = 320
HEIGHT = 240

PIN_DC = 25
PIN_RST = 27
PIN_BL = 18

SPI_BUS = 0
SPI_DEVICE = 0
# backed off from 40 MHz, panel was glitching after long runs.
# 24 MHz is the safe headroom most ref drivers use for this ST7789V over dupont wiring
SPI_HZ = 24_000_000


class ST7789:
    def __init__(self, spi_hz: int = SPI_HZ):
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        for pin in (PIN_DC, PIN_RST):
            GPIO.setup(pin, GPIO.OUT)
            GPIO.output(pin, 0)
        GPIO.setup(PIN_BL, GPIO.OUT)
        GPIO.output(PIN_BL, 1)

        self._spi = spidev.SpiDev()
        self._spi.open(SPI_BUS, SPI_DEVICE)
        self._spi.max_speed_hz = spi_hz
        self._spi.mode = 0b00

        self._reset()
        self._init_panel()

    def _reset(self):
        GPIO.output(PIN_RST, 1)
        time.sleep(0.01)
        GPIO.output(PIN_RST, 0)
        time.sleep(0.01)
        GPIO.output(PIN_RST, 1)
        time.sleep(0.12)

    def _cmd(self, c: int):
        GPIO.output(PIN_DC, 0)
        self._spi.writebytes([c & 0xFF])

    def _data(self, data):
        GPIO.output(PIN_DC, 1)
        if isinstance(data, int):
            self._spi.writebytes([data & 0xFF])
        else:
            mv = memoryview(data)
            chunk = 4096
            for i in range(0, len(mv), chunk):
                self._spi.writebytes2(mv[i:i + chunk])

    def _init_panel(self):
        self._cmd(0x36)
        self._data(0x70)

        self._cmd(0x3A)
        self._data(0x05)

        self._cmd(0xB2)
        self._data(bytes([0x0C, 0x0C, 0x00, 0x33, 0x33]))

        self._cmd(0xB7)
        self._data(0x35)

        self._cmd(0xBB)
        self._data(0x19)

        self._cmd(0xC0)
        self._data(0x2C)

        self._cmd(0xC2)
        self._data(0x01)

        self._cmd(0xC3)
        self._data(0x12)

        self._cmd(0xC4)
        self._data(0x20)

        self._cmd(0xC6)
        self._data(0x0F)

        self._cmd(0xD0)
        self._data(bytes([0xA4, 0xA1]))

        self._cmd(0xE0)
        self._data(bytes([0xD0, 0x04, 0x0D, 0x11, 0x13, 0x2B,
                          0x3F, 0x54, 0x4C, 0x18, 0x0D, 0x0B,
                          0x1F, 0x23]))

        self._cmd(0xE1)
        self._data(bytes([0xD0, 0x04, 0x0C, 0x11, 0x13, 0x2C,
                          0x3F, 0x44, 0x51, 0x2F, 0x1F, 0x1F,
                          0x20, 0x23]))

        self._cmd(0x21)
        self._cmd(0x11)
        time.sleep(0.12)
        self._cmd(0x29)

    def set_window(self, x0: int, y0: int, x1: int, y1: int):
        self._cmd(0x2A)
        self._data(bytes([(x0 >> 8) & 0xFF, x0 & 0xFF,
                          (x1 >> 8) & 0xFF, x1 & 0xFF]))
        self._cmd(0x2B)
        self._data(bytes([(y0 >> 8) & 0xFF, y0 & 0xFF,
                          (y1 >> 8) & 0xFF, y1 & 0xFF]))
        self._cmd(0x2C)

    def display(self, rgb565_buf):
        self.set_window(0, 0, WIDTH - 1, HEIGHT - 1)
        self._data(rgb565_buf)

    def clear(self, rgb565: int = 0x0000):
        hi = (rgb565 >> 8) & 0xFF
        lo = rgb565 & 0xFF
        buf = bytes([hi, lo]) * (WIDTH * HEIGHT)
        self.display(buf)

    def backlight(self, on: bool):
        GPIO.output(PIN_BL, 1 if on else 0)

    # full panel re-sync. used by live_view as an hourly watchdog
    # and after any SPI write that raised, to recover from a desynced RAMWR pointer
    def reinit(self):
        self._reset()
        self._init_panel()

    def close(self):
        try:
            GPIO.output(PIN_BL, 0)
        except Exception:
            pass
        try:
            self._spi.close()
        except Exception:
            pass
        try:
            GPIO.cleanup([PIN_DC, PIN_RST, PIN_BL])
        except Exception:
            pass
