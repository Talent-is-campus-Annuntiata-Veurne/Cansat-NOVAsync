"""Minimal helper that returns GPS readings instead of printing them."""

try:
    import utime as time  # type: ignore
except Exception:  # pragma: no cover - fallback for host env
    import time  # type: ignore

try:
    from machine import Pin, UART  # type: ignore
except Exception:  # pragma: no cover - running on host
    Pin = None  # type: ignore
    UART = None  # type: ignore

try:
    import adafruit_gps  # type: ignore
except Exception:  # pragma: no cover - driver missing on host
    adafruit_gps = None  # type: ignore

UART_ID = 0  # UART0 maps to TX=X9 (GP0), RX=X10 (GP1)
UART_TX_PIN = 0
UART_RX_PIN = 1
UART_BAUD = 9600
UART_TIMEOUT_MS = 3000

_gps = None
_latest = {
    "has_fix": False,
    "latitude": None,
    "longitude": None,
    "altitude_m": None,
    "speed_kmh": None,
    "satellites": None,
    "timestamp": None,
}


def init_gps():
    """Create and configure the GPS instance once."""

    global _gps
    if _gps is not None:
        return _gps
    if UART is None or Pin is None or adafruit_gps is None:
        return None

    uart = UART(
        UART_ID,
        baudrate=UART_BAUD,
        timeout=UART_TIMEOUT_MS,
        tx=Pin(UART_TX_PIN),
        rx=Pin(UART_RX_PIN),
    )
    gps = adafruit_gps.GPS(uart)
    gps.send_command("PMTK314,0,1,0,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0")
    gps.send_command("PMTK220,1000")
    _gps = gps
    return _gps


def _knots_to_kmh(knots):
    if knots is None:
        return None
    try:
        return float(knots) * 1.8513
    except Exception:
        return None


def read_gps_data(gps=None, poll_window_ms=200):
    """Poll the GPS for a short window and return the latest values."""

    global _latest, _gps

    # Maintain backward compatibility with callers that previously passed the
    # GPS instance as the first positional argument.
    if isinstance(gps, (int, float)) and poll_window_ms == 200:
        poll_window_ms = int(gps)
        gps = None

    if gps is None:
        gps = init_gps()
    else:
        _gps = gps  # Remember the externally provided instance.

    if gps is None:
        return _latest

    window = int(poll_window_ms) if poll_window_ms else 0
    if window < 0:
        window = 0
    deadline = time.ticks_add(time.ticks_ms(), window)
    while time.ticks_diff(deadline, time.ticks_ms()) >= 0:
        gps.update()

    if not gps.has_fix:
        _latest = {
            "has_fix": False,
            "latitude": None,
            "longitude": None,
            "altitude_m": None,
            "speed_kmh": None,
            "satellites": None,
            "timestamp": None,
        }
        return _latest

    _latest = {
        "has_fix": True,
        "latitude": gps.latitude,
        "longitude": gps.longitude,
        "altitude_m": getattr(gps, "altitude_m", None),
        "speed_kmh": _knots_to_kmh(getattr(gps, "speed_knots", None)),
        "satellites": getattr(gps, "satellites", None),
        "timestamp": getattr(gps, "timestamp_utc", None),
    }
    return _latest


if __name__ == "__main__":
    while True:
        print(read_gps_data())
        time.sleep(1)
