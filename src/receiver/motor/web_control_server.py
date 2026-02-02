"""Local web interface to drive the Pico stepper controller via USB serial.

Run this alongside the MicroPython `main.py` controller that is already on the
Pico. The server opens the Pico's COM port, serves a small web page with arrow
buttons, and forwards button/keyboard events from the browser to the Pico as if
they were typed directly into the REPL.

Example usage:

    python web_control_server.py --serial COM5 --http-port 8765

Dependencies:
    pip install flask pyserial
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, request
import serial

app = Flask(__name__)

SERIAL_LOCK = threading.Lock()
SERIAL_CONN: serial.Serial | None = None
LATEST_POS = {"m1": 0.0, "m2": 0.0, "updated": 0.0}

# Arrow keys map to the escape sequences that `main.py` expects.
COMMAND_PAYLOADS = {
    "up": b"\x1b[A",
    "down": b"\x1b[B",
    "right": b"\x1b[C",
    "left": b"\x1b[D",
    "release": b"release\n",
    "zero": b"zero\n",
}

HTML_PAGE = (Path(__file__).parent / "web_ui.html").read_text(encoding="utf-8")


def send_payload(raw: bytes) -> None:
    """Write raw bytes to the Pico (thread-safe)."""
    global SERIAL_CONN
    with SERIAL_LOCK:
        if SERIAL_CONN is None or not SERIAL_CONN.is_open:
            raise RuntimeError("Serial port is not open")
        SERIAL_CONN.write(raw)
        SERIAL_CONN.flush()


def serial_reader() -> None:
    """Continuously print Pico output to the host console for debugging."""
    global SERIAL_CONN
    while True:
        conn = SERIAL_CONN
        if conn is None or not conn.is_open:
            time.sleep(0.2)
            continue
        try:
            line = conn.readline()
        except Exception:
            time.sleep(0.2)
            continue
        if line:
            try:
                decoded = line.decode("utf-8", errors="replace")
            except Exception:
                decoded = repr(line)
            stripped = decoded.strip()
            if stripped.startswith("POS,"):
                parts = stripped.split(",")
                if len(parts) >= 3:
                    try:
                        LATEST_POS["m1"] = float(parts[1])
                        LATEST_POS["m2"] = float(parts[2])
                        LATEST_POS["updated"] = time.time()
                    except Exception:
                        pass
            print(f"[PICO] {decoded}", end="")


@app.route("/")
def index():
    return HTML_PAGE


@app.route("/command", methods=["POST"])
def command():
    data = request.get_json(silent=True) or {}
    cmd = (data.get("cmd") or "").strip().lower()
    text = data.get("text")
    if text:
        payload = text.strip() + "\n"
        try:
            send_payload(payload.encode("utf-8"))
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
        return jsonify({"status": f"Sent '{text.strip()}'"})

    payload = COMMAND_PAYLOADS.get(cmd)
    if not payload:
        return jsonify({"error": f"Unknown command: {cmd}"}), 400
    try:
        send_payload(payload)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"status": f"Sent {cmd}"})


@app.route("/status")
def status():
    return jsonify({
        "m1": LATEST_POS["m1"],
        "m2": LATEST_POS["m2"],
        "updated": LATEST_POS["updated"],
    })


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Web UI for Pico steppers")
    parser.add_argument("--serial", required=True, help="Serial/COM port of the Pico (e.g. COM5 or /dev/ttyACM0)")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate (default: 115200)")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind address (default: 127.0.0.1)")
    parser.add_argument("--http-port", type=int, default=8765, help="HTTP port for the web server (default: 8765)")
    return parser.parse_args()


def main() -> None:
    global SERIAL_CONN
    args = parse_args()
    print(f"Opening serial port {args.serial} @ {args.baud}...")
    SERIAL_CONN = serial.Serial(args.serial, args.baud, timeout=0.1)
    print("Serial connection established. Launching reader thread...")
    threading.Thread(target=serial_reader, daemon=True).start()
    print(f"Serving web UI at http://{args.host}:{args.http_port}")
    print("Press Ctrl+C to stop.")
    app.run(host=args.host, port=args.http_port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
