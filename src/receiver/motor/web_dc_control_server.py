"""Web UI driver for DC motors on the Adafruit Motor HAT.

Run this directly on the Raspberry Pi alongside the hat:

    python3 web_dc_control_server.py --host 0.0.0.0 --http-port 8765

Then open the served page locally or use SSH port forwarding from another
computer: ``ssh -L 8765:localhost:8765 pi@your-pi``.
"""

from __future__ import annotations

import argparse
import threading
import time
from pathlib import Path
from typing import Any, Dict

from flask import Flask, jsonify, request

from dc_control import DCControlState, MotorHatController
from pot_reader import PotAngleReader

app = Flask(__name__)

CALIBRATION_FILE = Path(__file__).parent / "pot_calibration.json"
MOTOR_TO_POT = {
    1: "azimuth",
    2: "elevation",
}
AZIMUTH_MOTOR = 1
ELEVATION_MOTOR = 2
CALIBRATION_SPEED = 0.22
CALIBRATION_SAMPLE_INTERVAL = 0.05

CONTROL_LOCK = threading.Lock()
ANGLE_LOCK = threading.Lock()
CALIBRATION_LOCK = threading.Lock()
CALIBRATION_STOP_EVENT = threading.Event()
CONTROLLER = MotorHatController()
STATE = DCControlState(controller=CONTROLLER, motor_count=CONTROLLER.motor_count)
HTML_PAGE = (Path(__file__).parent / "web_ui_dc.html").read_text(encoding="utf-8")
POT_READER = PotAngleReader(calibration_path=CALIBRATION_FILE)
ANGLE_CACHE = {
    "available": POT_READER.available,
    "values": [],
    "updated": 0.0,
    "error": POT_READER.error,
}
CALIBRATION_STATE: Dict[str, Any] = {
    "running": False,
    "motor": None,
    "stage": "idle",
    "progress": 0.0,
    "message": "",
    "error": None,
    "updated": time.time(),
}


@app.route("/")
def index():
    return HTML_PAGE


@app.route("/command", methods=["POST"])
def command():
    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()
    with CONTROL_LOCK:
        if text:
            if CALIBRATION_STATE["running"]:
                return jsonify({"error": "Calibration running; please wait"}), 409
            ok, msg = STATE.handle_command(text)
            if msg == "quit":
                ok = False
                msg = "The web server cannot quit the controller"
            if not ok and msg != "quit":
                return jsonify({"error": msg}), 400
            snapshot = _compose_status()
            return jsonify({"ok": ok, "message": msg, "status": snapshot})

        cmd = (payload.get("cmd") or "").lower()
        motor = payload.get("motor")
        amount = payload.get("amount")
        direction = payload.get("direction")
        method = (payload.get("method") or "auto").lower()
        degrees = payload.get("degrees")
        value = payload.get("value")

        if CALIBRATION_STATE["running"] and cmd not in {"calibrate", "status", "calibration_stop"}:
            return jsonify({"error": "Calibration running; please wait"}), 409

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
        elif cmd == "calibrate":
            if motor is None:
                return jsonify({"error": "Motor is required"}), 400
            deg_value = None
            if degrees is not None:
                try:
                    deg_value = float(degrees)
                except Exception:
                    return jsonify({"error": "Degrees must be numeric"}), 400
            ok, msg = _start_calibration(int(motor), method=method, degrees=deg_value)
        elif cmd == "calibration_stop":
            ok, msg = _signal_calibration_stop()
        elif cmd == "set_throttle":
            if motor is None:
                return jsonify({"error": "Motor is required"}), 400
            if value is None:
                return jsonify({"error": "Throttle value is required"}), 400
            try:
                target = float(value)
            except Exception:
                return jsonify({"error": "Throttle must be numeric"}), 400
            ok, msg = STATE.set_motor_throttle(int(motor), target)
        else:
            return jsonify({"error": f"Unknown command: {cmd}"}), 400

        snapshot = _compose_status()
        if not ok:
            return jsonify({"error": msg, "status": snapshot}), 400
        return jsonify({"ok": True, "message": msg, "status": snapshot})


@app.route("/status")
def status():
    with CONTROL_LOCK:
        return jsonify(_compose_status())


def _compose_status() -> dict:
    base = STATE.status_payload()
    base["angles"] = _angle_snapshot()
    base["calibration"] = calibration_snapshot()
    return base


def _angle_snapshot() -> dict:
    with ANGLE_LOCK:
        return {
            "available": ANGLE_CACHE["available"],
            "values": [dict(entry) for entry in ANGLE_CACHE["values"]],
            "updated": ANGLE_CACHE["updated"],
            "error": ANGLE_CACHE["error"],
        }


def _angle_poll_loop() -> None:
    if not POT_READER.available:
        return
    while True:
        readings = POT_READER.read_angles()
        now = time.time()
        with ANGLE_LOCK:
            ANGLE_CACHE["values"] = readings
            ANGLE_CACHE["updated"] = now
        time.sleep(POT_READER.poll_interval)


def calibration_snapshot() -> Dict[str, Any]:
    with CALIBRATION_LOCK:
        return dict(CALIBRATION_STATE)


def _update_calibration_state(**kwargs) -> None:
    with CALIBRATION_LOCK:
        CALIBRATION_STATE.update(kwargs)
        CALIBRATION_STATE["updated"] = time.time()


def _signal_calibration_stop() -> tuple[bool, str]:
    if not CALIBRATION_STATE["running"]:
        return False, "No calibration in progress"
    CALIBRATION_STOP_EVENT.set()
    return True, "Stop signal sent"


def _start_calibration(
    motor: int,
    *,
    method: str = "auto",
    degrees: float | None = None,
) -> tuple[bool, str]:
    if not POT_READER.available:
        return False, "Potentiometer reader unavailable"
    pot_name = MOTOR_TO_POT.get(motor)
    if not pot_name:
        return False, f"No potentiometer mapped to motor {motor}"
    cfg = POT_READER.get_config(pot_name)
    if not cfg:
        return False, f"Missing potentiometer config for '{pot_name}'"
    method = (method or "auto").lower()
    if motor == AZIMUTH_MOTOR and method != "auto":
        return False, "Azimuth motor only supports automatic calibration"
    if motor == ELEVATION_MOTOR and method not in {"auto", "manual", "degrees"}:
        return False, "Unknown calibration method"
    if method == "degrees":
        if degrees is None:
            return False, "Degrees value required for degree-limited calibration"
        if degrees <= 0:
            return False, "Degrees must be positive"
        if degrees >= cfg.span_degrees:
            return False, f"Degrees must be less than {cfg.span_degrees:.1f}"
    with CALIBRATION_LOCK:
        if CALIBRATION_STATE["running"]:
            return False, "Calibration already running"
        CALIBRATION_STATE.update(
            {
                "running": True,
                "motor": motor,
                "stage": "starting",
                "progress": 0.02,
                "message": "Starting calibration",
                "error": None,
                "updated": time.time(),
            }
        )
    CALIBRATION_STOP_EVENT.clear()
    if motor == ELEVATION_MOTOR and method in {"manual", "degrees"}:
        threading.Thread(
            target=_calibration_elevation_worker,
            args=(motor, pot_name, method, degrees),
            daemon=True,
        ).start()
    else:
        zero_mode = "center" if motor == AZIMUTH_MOTOR else "lower"
        threading.Thread(
            target=_calibration_worker,
            args=(motor, pot_name, zero_mode),
            daemon=True,
        ).start()
    return True, "Calibration started"


def _calibration_worker(motor: int, pot_name: str, zero_mode: str) -> None:
    try:
        min_raw = _sweep_to_stop(motor, pot_name, direction=-1, stage="seeking minimum", progress=0.2)
        time.sleep(0.3)
        max_raw = _sweep_to_stop(motor, pot_name, direction=1, stage="seeking maximum", progress=0.6)
        if max_raw < min_raw:
            min_raw, max_raw = max_raw, min_raw
        mid_raw = (min_raw + max_raw) / 2.0
        _update_calibration_state(stage="centering", progress=0.75, message="Moving to midpoint")
        _move_to_raw(motor, pot_name, mid_raw)
        cfg = POT_READER.get_config(pot_name)
        if not cfg:
            raise RuntimeError(f"Missing config for pot '{pot_name}'")
        if zero_mode == "center":
            zero_deg = -cfg.span_degrees / 2.0
        elif zero_mode == "lower":
            zero_deg = 0.0
        else:
            zero_deg = cfg.zero_deg
        min_ohms = POT_READER.raw_to_ohms_value(pot_name, int(min_raw))
        max_ohms = POT_READER.raw_to_ohms_value(pot_name, int(max_raw))
        POT_READER.update_calibration(
            pot_name,
            ohm_min=min_ohms,
            ohm_max=max_ohms,
            zero_deg=zero_deg,
        )
        POT_READER.save_calibrations()
        _update_calibration_state(
            running=False,
            motor=None,
            stage="done",
            progress=1.0,
            message=f"Calibration complete for motor {motor}",
            error=None,
        )
    except Exception as exc:  # pragma: no cover - hardware runtime
        _apply_throttle(motor, 0.0)
        _update_calibration_state(
            running=False,
            stage="error",
            motor=None,
            progress=0.0,
            message="Calibration failed",
            error=str(exc),
        )


def _calibration_elevation_worker(motor: int, pot_name: str, method: str, degrees: float | None) -> None:
    try:
        cfg = POT_READER.get_config(pot_name)
        if not cfg:
            raise RuntimeError(f"Missing config for pot '{pot_name}'")
        _update_calibration_state(stage="marking lower", progress=0.1, message="Capturing manual lower bound")
        _apply_throttle(motor, 0.0)
        min_raw = POT_READER.read_raw(pot_name)
        target_delta_raw = None
        span_guess = max(cfg.raw_max - cfg.raw_min, 1)
        if method == "degrees" and degrees is not None:
            fraction = min(degrees / cfg.span_degrees, 1.0)
            target_delta_raw = fraction * span_guess
            sweep_msg = f"Sweeping upward ~{degrees:.1f}°"
        else:
            sweep_msg = "Sweeping upward (manual stop)"
        _update_calibration_state(stage="sweeping upper", progress=0.35, message=sweep_msg)
        _apply_throttle(motor, CALIBRATION_SPEED)
        start = time.time()
        timeout = 35.0
        stall_time = 0.8
        last_change = start
        last_raw = min_raw
        max_raw = min_raw
        exit_reason = "timeout"
        while time.time() - start < timeout:
            if CALIBRATION_STOP_EVENT.is_set():
                exit_reason = "manual stop"
                break
            time.sleep(CALIBRATION_SAMPLE_INTERVAL)
            raw = POT_READER.read_raw(pot_name)
            if raw > max_raw:
                max_raw = raw
            if abs(raw - last_raw) > 80:
                last_raw = raw
                last_change = time.time()
            elif time.time() - last_change > stall_time:
                exit_reason = "stall"
                break
            if target_delta_raw is not None and raw - min_raw >= target_delta_raw:
                exit_reason = "target reached"
                break
        _apply_throttle(motor, 0.0)
        if max_raw - min_raw < 800:
            raise RuntimeError("Captured sweep range is too small; try again")
        _update_calibration_state(stage="saving", progress=0.85, message="Writing calibration data")
        min_ohms = POT_READER.raw_to_ohms_value(pot_name, int(min_raw))
        max_ohms = POT_READER.raw_to_ohms_value(pot_name, int(max_raw))
        POT_READER.update_calibration(
            pot_name,
            ohm_min=min_ohms,
            ohm_max=max_ohms,
            zero_deg=0.0,
        )
        POT_READER.save_calibrations()
        exit_labels = {
            "manual stop": "Calibration complete (manual stop)",
            "target reached": "Calibration complete (target degrees reached)",
            "stall": "Calibration complete (stall detected)",
            "timeout": "Calibration complete (timeout limit)",
        }
        msg = exit_labels.get(exit_reason, "Calibration complete")
        _update_calibration_state(
            running=False,
            motor=None,
            stage="done",
            progress=1.0,
            message=msg,
            error=None,
        )
    except Exception as exc:  # pragma: no cover - hardware runtime
        _apply_throttle(motor, 0.0)
        _update_calibration_state(
            running=False,
            stage="error",
            motor=None,
            progress=0.0,
            message="Calibration failed",
            error=str(exc),
        )


def _sweep_to_stop(motor: int, pot_name: str, direction: int, stage: str, progress: float) -> float:
    _update_calibration_state(stage=stage, progress=progress - 0.1, message=f"{stage.title()}...")
    _apply_throttle(motor, direction * CALIBRATION_SPEED)
    start = time.time()
    last_change = start
    last_raw = POT_READER.read_raw(pot_name)
    best_raw = last_raw
    threshold = 120
    stall_time = 0.7
    timeout = 30.0
    while time.time() - start < timeout:
        time.sleep(CALIBRATION_SAMPLE_INTERVAL)
        raw = POT_READER.read_raw(pot_name)
        if abs(raw - last_raw) > threshold:
            last_change = time.time()
            last_raw = raw
            best_raw = raw
        elif time.time() - last_change > stall_time:
            best_raw = raw
            break
    _apply_throttle(motor, 0.0)
    _update_calibration_state(stage=stage, progress=progress, message=f"{stage.title()} captured")
    return float(best_raw)


def _move_to_raw(motor: int, pot_name: str, target_raw: float) -> None:
    timeout = 20.0
    start = time.time()
    tolerance = 200
    while time.time() - start < timeout:
        raw = POT_READER.read_raw(pot_name)
        error = target_raw - raw
        if abs(error) <= tolerance:
            break
        direction = 1 if error > 0 else -1
        magnitude = min(CALIBRATION_SPEED, max(0.08, abs(error) / 15000.0))
        _apply_throttle(motor, direction * magnitude)
        time.sleep(CALIBRATION_SAMPLE_INTERVAL)
    _apply_throttle(motor, 0.0)


def _apply_throttle(motor: int, value: float) -> None:
    with CONTROL_LOCK:
        STATE.controller.set_throttle(motor, value)
        STATE.throttles[motor] = value
        STATE.updated = time.time()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Web UI for DC motors")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind address")
    parser.add_argument("--http-port", type=int, default=8765, help="HTTP port")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print(f"Serving DC motor UI at http://{args.host}:{args.http_port}")
    print("Press Ctrl+C to stop.")
    if POT_READER.available:
        threading.Thread(target=_angle_poll_loop, daemon=True).start()
    elif POT_READER.error:
        print(f"[DC server] Pot reader disabled: {POT_READER.error}")
    app.run(host=args.host, port=args.http_port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
