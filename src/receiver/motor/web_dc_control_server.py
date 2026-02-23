"""Web UI driver for DC motors on the Adafruit Motor HAT.

Run this directly on the Raspberry Pi alongside the hat:

    python3 web_dc_control_server.py --host 0.0.0.0 --http-port 8765

Then open the served page locally or use SSH port forwarding from another
computer: ``ssh -L 8765:localhost:8765 pi@your-pi``.
"""

from __future__ import annotations

import argparse
import threading
from pathlib import Path

from flask import Flask, jsonify, request

from dc_control import DCControlState, MotorHatController

app = Flask(__name__)

CONTROL_LOCK = threading.Lock()
CONTROLLER = MotorHatController()
STATE = DCControlState(controller=CONTROLLER, motor_count=CONTROLLER.motor_count)
HTML_PAGE = (Path(__file__).parent / "web_ui_dc.html").read_text(encoding="utf-8")


@app.route("/")
def index():
    return HTML_PAGE


@app.route("/command", methods=["POST"])
def command():
    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()
    with CONTROL_LOCK:
        if text:
            ok, msg = STATE.handle_command(text)
            if msg == "quit":
                ok = False
                msg = "The web server cannot quit the controller"
            if not ok and msg != "quit":
                return jsonify({"error": msg}), 400
            snapshot = STATE.status_payload()
            return jsonify({"ok": ok, "message": msg, "status": snapshot})

        cmd = (payload.get("cmd") or "").lower()
        motor = payload.get("motor")
        amount = payload.get("amount")
        direction = payload.get("direction")

        if cmd == "nudge":
            if motor is None:
                return jsonify({"error": "Motor is required"}), 400
            dir_sign = 1 if (direction or 1) >= 0 else -1
            amt = float(amount) if amount is not None else None
            ok, msg = STATE.nudge(int(motor), dir_sign, source="web", amount=amt)
        elif cmd == "release":
            if motor is None:
                ok, msg = STATE.release_motor()
            else:
                ok, msg = STATE.release_motor(int(motor))
        elif cmd == "stop":
            if motor is None:
                ok, msg = STATE.stop_motor()
            else:
                ok, msg = STATE.stop_motor(int(motor))
        elif cmd == "stopall":
            ok, msg = STATE.stop_motor()
        elif cmd == "increment":
            if amount is None:
                return jsonify({"error": "Amount is required"}), 400
            try:
                amt = float(amount)
            except Exception:
                return jsonify({"error": "Amount must be numeric"}), 400
            ok, msg = STATE.set_increment(amt)
        else:
            return jsonify({"error": f"Unknown command: {cmd}"}), 400

        snapshot = STATE.status_payload()
        if not ok:
            return jsonify({"error": msg, "status": snapshot}), 400
        return jsonify({"ok": True, "message": msg, "status": snapshot})


@app.route("/status")
def status():
    with CONTROL_LOCK:
        return jsonify(STATE.status_payload())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Web UI for DC motors")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind address")
    parser.add_argument("--http-port", type=int, default=8765, help="HTTP port")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print(f"Serving DC motor UI at http://{args.host}:{args.http_port}")
    print("Press Ctrl+C to stop.")
    app.run(host=args.host, port=args.http_port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
