"""Interactive Pico script to inspect azimuth/elevation potentiometers.

Copy this file plus ``PicoRobotics.py`` (for shared constants) to the Pico, run it
from the REPL (``python pot_test.py``), and watch it stream both raw ohms and
angle values. Type commands such as ``zero azimuth`` or ``zero elevation`` to
set the current position as 0° for that channel without rebooting.
"""

import sys
import time
import uselect

import machine


POT_CHANNELS = (
    {
        "name": "azimuth",
        "pin": 26,  # GP26 / ADC0
        "span_degrees": 540.0,
        "zero_deg": 0.0,
        "total_ohms": 10000.0,
        "ohm_min": 0.0,
        "ohm_max": 10000.0,
        "invert": False,
    },
    {
        "name": "elevation",
        "pin": 27,  # GP27 / ADC1
        "span_degrees": 270.0,
        "zero_deg": 0.0,
        "total_ohms": 56000.0,
        "ohm_min": 0.0,
        "ohm_max": 56000.0,
        "invert": False,
    },
)


class PotChannel:
    def __init__(self, *, name, pin, span_degrees, zero_deg, total_ohms, ohm_min, ohm_max, invert):
        self.name = name
        self.span_degrees = span_degrees
        self.zero_deg = zero_deg
        self.total_ohms = total_ohms
        self.ohm_min = ohm_min
        self.ohm_max = ohm_max
        self.invert = invert
        self._zero_offset = 0.0
        self.adc = machine.ADC(pin)

    def read_raw(self):
        return self.adc.read_u16()

    def raw_to_ohms(self, raw):
        clamped = max(0, min(65535, raw))
        return (clamped / 65535.0) * self.total_ohms

    def raw_to_degrees(self, raw):
        ohms = self.raw_to_ohms(raw)
        span = max(self.ohm_max - self.ohm_min, 1e-6)
        fraction = (ohms - self.ohm_min) / span
        fraction = max(0.0, min(1.0, fraction))
        if self.invert:
            fraction = 1.0 - fraction
        value = self.zero_deg + fraction * self.span_degrees
        return value - self._zero_offset

    def snapshot(self):
        raw = self.read_raw()
        return {
            "name": self.name,
            "raw": raw,
            "ohms": self.raw_to_ohms(raw),
            "degrees": self.raw_to_degrees(raw),
        }

    def set_zero_here(self):
        reading = self.snapshot()
        self._zero_offset = reading["degrees"]
        return reading["degrees"]


CHANNELS = [PotChannel(**cfg) for cfg in POT_CHANNELS]
POLL = uselect.poll()
POLL.register(sys.stdin, uselect.POLLIN)


def _print_header():
    print("Pot tester ready. Commands: 'zero azimuth', 'zero elevation', 'status'.")
    print("Press Ctrl+C to exit.\n")


def _handle_command(line: str):
    line = line.strip().lower()
    if not line:
        return
    if line.startswith("zero "):
        target = line.split()[1]
        for channel in CHANNELS:
            if channel.name == target:
                zero = channel.set_zero_here()
                print("[ZERO] {} reference set (offset {:.2f} deg)".format(channel.name, zero))
                return
        print("[WARN] Unknown potentiometer '{}'.".format(target))
    elif line == "status":
        _print_snapshot()
    else:
        print("[WARN] Unknown command '{}'.".format(line))


def _print_snapshot():
    entries = [ch.snapshot() for ch in CHANNELS]
    for entry in entries:
        print(
            "{name:9s} raw={raw:5d} ohms={ohms:8.1f} angle={degrees:7.2f}".format(
                **entry
            )
        )
    print("-")


def main():
    _print_header()
    try:
        while True:
            if POLL.poll(0):
                _handle_command(sys.stdin.readline())
            _print_snapshot()
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nExiting pot tester.")


if __name__ == "__main__":
    main()
