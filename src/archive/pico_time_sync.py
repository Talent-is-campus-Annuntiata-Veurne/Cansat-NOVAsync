"""Automatically feed the host timestamp to a connected Raspberry Pi Pico.

Run this script from your computer (not the Pico) before or right after the
Pico boots. It waits for the TIME_SYNC prompt emitted by the Micropython
firmware and immediately replies with the current UNIX timestamp.

Example:
    python tools/pico_time_sync.py COM5
"""

import argparse
import sys
import time
from typing import Optional

try:
    import serial  # type: ignore
except ImportError as exc:  # pragma: no cover - host side helper
    raise SystemExit(
        "pyserial is required. Install with 'pip install pyserial'."
    ) from exc


def wait_for_prompt(port: serial.Serial, timeout: float) -> bool:
    """Read from the serial port until the TIME_SYNC prompt is seen."""
    deadline = time.time() + timeout
    buffer = b""
    while time.time() < deadline:
        chunk = port.read(port.in_waiting or 1)
        if not chunk:
            continue
        sys.stdout.write(chunk.decode("utf-8", errors="replace"))
        sys.stdout.flush()
        buffer += chunk
        if b"TIME_SYNC" in buffer:
            return True
    return False


def send_timestamp(port: serial.Serial) -> int:
    """Send the current UNIX timestamp (seconds) over the serial link."""
    stamp = int(time.time())
    payload = f"{stamp}\n".encode("ascii")
    port.write(payload)
    port.flush()
    return stamp


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port", help="Serial port of the Pico (e.g. COM5 or /dev/ttyACM0)")
    parser.add_argument(
        "--baud", type=int, default=115200, help="Serial baud rate (default: 115200)"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="Seconds to wait for the TIME_SYNC prompt before aborting",
    )
    parser.add_argument(
        "--send-now",
        action="store_true",
        help="Do not wait for the prompt; send the timestamp immediately",
    )
    args = parser.parse_args(argv)

    try:
        with serial.Serial(
            args.port,
            args.baud,
            timeout=0.2,
            write_timeout=2.0,
            dsrdtr=False,
            rtscts=False,
        ) as ser:
            # Drain any leftover bytes so the prompt detection sees fresh data.
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            if not args.send_now:
                print("Waiting for TIME_SYNC prompt...")
                if not wait_for_prompt(ser, args.timeout):
                    print("No prompt received within timeout. Use --send-now to force.")
                    return 1
            stamp = send_timestamp(ser)
            print(f"\nSent timestamp {stamp}")
            # Give the Pico a moment to respond with confirmation.
            time.sleep(0.5)
            remaining = ser.read(ser.in_waiting or 1)
            if remaining:
                sys.stdout.write(remaining.decode("utf-8", errors="replace"))
                sys.stdout.flush()
    except serial.SerialException as exc:  # pragma: no cover - host serial issues
        print(f"Serial error: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
