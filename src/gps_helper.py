"""Utility helpers to interface with the CanSat GPS parachute module.

The module relies on the ``adafruit_gps`` MicroPython driver.  Place both
``adafruit_gps.py`` and ``gps_config.py`` under ``lib/`` on the Pico before
importing this helper.  All functions are safe to call even when the GPS library
is missing: they will simply return ``None`` so the main loop can keep running.
"""

import time

try:
    from machine import Pin, UART  # type: ignore
except Exception:  # pragma: no cover - running on host
    Pin = None  # type: ignore
    UART = None  # type: ignore

try:
    from adafruit_gps import GPS  # type: ignore
except Exception:  # pragma: no cover - driver not deployed yet
    GPS = None  # type: ignore

# Default wiring taken from the CanSat GPS documentation (UART0 on GP0/GP1).
UART_ID = 0
UART_TX_PIN = 0
UART_RX_PIN = 1
UART_BAUD = 9600
UART_TIMEOUT_MS = 3000

_gps = None
_latest_fix = {
    "has_fix": False,
    "latitude": None,
    "longitude": None,
    "altitude_m": None,
    "speed_kmh": None,
    "satellites": None,
    "timestamp": None,
}


def init_gps(
    uart_id: int = UART_ID,
    tx_pin: int = UART_TX_PIN,
    rx_pin: int = UART_RX_PIN,
    baudrate: int = UART_BAUD,
    timeout: int = UART_TIMEOUT_MS,
):
    """Initialise the GPS module and return the cached GPS instance."""

    global _gps
    if _gps is not None:
        return _gps
    if GPS is None or UART is None or Pin is None:
        return None
    try:
        uart = UART(
            uart_id,
            baudrate=baudrate,
            tx=Pin(tx_pin),
            rx=Pin(rx_pin),
            timeout=timeout,
        )
        gps = GPS(uart, debug=False)
        # Enable the standard RMC (Recommended Minimum) and GGA (Fix info)
        gps.send_command("PMTK314,0,1,0,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0")
        # Update rate = 1 Hz for deterministic telemetry
        gps.send_command("PMTK220,1000")
        _gps = gps
    except Exception:
        _gps = None
    return _gps


def _knots_to_kmh(knots):
    if knots is None:
        return None
    try:
        return float(knots) * 1.852
    except Exception:
        return None


def read_gps_data(gps=None, poll_window_ms: int = 200):
    """Poll the GPS for fresh data and return the latest fix dictionary."""

    global _latest_fix
    gps = gps or _gps or init_gps()
    if gps is None:
        return _latest_fix

    end = time.ticks_add(time.ticks_ms(), poll_window_ms)
    while time.ticks_diff(end, time.ticks_ms()) > 0:
        try:
            gps.update()
        except Exception:
            break

    try:
        has_fix = bool(getattr(gps, "has_fix", False))
        timestamp = getattr(gps, "timestamp_utc", None)
        data = {
            "has_fix": has_fix,
            "latitude": getattr(gps, "latitude", None),
            "longitude": getattr(gps, "longitude", None),
            "altitude_m": getattr(gps, "altitude_m", None),
            "speed_kmh": _knots_to_kmh(getattr(gps, "speed_knots", None)),
            "satellites": getattr(gps, "satellites", None),
            "timestamp": timestamp if has_fix else None,
        }
    except Exception:
        data = _latest_fix
    else:
        _latest_fix = data
    return _latest_fix


if __name__ == "__main__":
    gps = init_gps()
    while True:
        fix = read_gps_data(gps)
        print(fix)
        time.sleep(1)
