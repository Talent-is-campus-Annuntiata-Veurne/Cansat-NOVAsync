"""Shared helpers for controlling DC motors on the Adafruit Motor HAT.

This module keeps the throttle bookkeeping logic isolated so it can be reused
by both the keyboard CLI (``bigpiDCmain.py``) and any HTTP servers.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from adafruit_motorkit import MotorKit

DEFAULT_INCREMENT = 0.10
DEFAULT_MOTOR_COUNT = 4


class MotorHatController:
    """Thin wrapper around ``MotorKit`` with a predictable motor map."""

    def __init__(self, address: int = 0x60, motor_count: int = DEFAULT_MOTOR_COUNT):
        self.kit = MotorKit(address=address)
        self.motor_count = max(1, min(4, motor_count))
        self.motors = {
            1: self.kit.motor1,
            2: self.kit.motor2,
            3: self.kit.motor3,
            4: self.kit.motor4,
        }

    def set_throttle(self, motor: int, value: float | None) -> None:
        hw = self.motors.get(motor)
        if hw is None:
            raise ValueError(f"Invalid motor index: {motor}")
        hw.throttle = value

    def release(self, motor: int) -> None:
        self.set_throttle(motor, None)

    def release_all(self, motor_count: int) -> None:
        for motor in range(1, motor_count + 1):
            self.release(motor)

    def stop_all(self, motor_count: int) -> None:
        for motor in range(1, motor_count + 1):
            self.set_throttle(motor, 0.0)


@dataclass
class DCControlState:
    controller: MotorHatController
    increment: float = DEFAULT_INCREMENT
    throttles: Dict[int, float | None] = field(default_factory=dict)
    motor_count: int = DEFAULT_MOTOR_COUNT
    updated: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not self.throttles:
            self.throttles = {motor: 0.0 for motor in range(1, self.motor_count + 1)}

    # Core helpers ---------------------------------------------------------
    def _clamp(self, value: float) -> float:
        return max(-1.0, min(1.0, value))

    def _set_throttle(self, motor: int, target: float | None) -> Tuple[bool, str]:
        if target is not None:
            target = self._clamp(target)
        self.controller.set_throttle(motor, target)
        self.throttles[motor] = target
        self.updated = time.time()
        if target is None:
            return True, f"Motor {motor} released (coast)"
        if target == 0.0:
            return True, f"Motor {motor} stopped (brake)"
        return True, f"Motor {motor} throttle set to {target:+.2f}"

    def status_line(self) -> str:
        values: List[str] = []
        for motor in range(1, self.motor_count + 1):
            val = self.throttles.get(motor)
            values.append("REL" if val is None else f"{val:+.2f}")
        return "THROT," + ",".join(values)

    def status_payload(self) -> Dict[str, object]:
        return {
            "updated": self.updated,
            "increment": self.increment,
            "motors": [
                {
                    "motor": motor,
                    "throttle": self.throttles.get(motor),
                }
                for motor in range(1, self.motor_count + 1)
            ],
        }

    def nudge(
        self,
        motor: int,
        direction: int,
        source: str = "hotkey",
        amount: float | None = None,
    ) -> Tuple[bool, str]:
        if motor < 1 or motor > self.motor_count:
            return False, f"Motor must be between 1 and {self.motor_count}"
        base = self.increment if amount is None else abs(amount)
        delta = base * (1 if direction >= 0 else -1)
        current = self.throttles.get(motor)
        if current is None:
            current = 0.0
        ok, msg = self._set_throttle(motor, current + delta)
        if ok:
            sign = "+" if delta >= 0 else "-"
            msg = f"({source} motor {motor} {sign}{abs(delta):.2f}) -> {msg}"
        return ok, msg

    def set_increment(self, amount: float) -> Tuple[bool, str]:
        if not amount or amount <= 0:
            return False, "Increment must be greater than zero"
        self.increment = min(1.0, amount)
        return True, f"Increment set to {self.increment:.2f}"

    def release_motor(self, motor: int | None = None) -> Tuple[bool, str]:
        if motor is None:
            self.controller.release_all(self.motor_count)
            for idx in range(1, self.motor_count + 1):
                self.throttles[idx] = None
            self.updated = time.time()
            return True, "All motors released (coast)"
        return self._set_throttle(motor, None)

    def stop_motor(self, motor: int | None = None) -> Tuple[bool, str]:
        if motor is None:
            self.controller.stop_all(self.motor_count)
            for idx in range(1, self.motor_count + 1):
                self.throttles[idx] = 0.0
            self.updated = time.time()
            return True, "All motors stopped (brake)"
        return self._set_throttle(motor, 0.0)

    # Command parsing ------------------------------------------------------
    def _parse_motor(self, token: str) -> int | None:
        try:
            motor = int(token)
        except Exception:
            return None
        if motor < 1 or motor > self.motor_count:
            return None
        return motor

    def handle_command(self, line: str) -> Tuple[bool, str]:
        if not line:
            return True, ""
        parts = line.split()
        command = parts[0].lower()
        if command in {"q", "quit", "exit"}:
            return False, "quit"
        if command == "?":
            return True, "Type status to view current throttles."
        if command == "status":
            return True, self.status_line()
        if command == "increment":
            if len(parts) < 2:
                return True, "Usage: increment <amount>"
            try:
                amount = float(parts[1])
            except Exception:
                return True, "Increment must be numeric"
            return self.set_increment(amount)
        if command == "set":
            if len(parts) < 3:
                return True, "Usage: set <motor> <throttle>"
            motor = self._parse_motor(parts[1])
            if motor is None:
                return True, f"Motor must be between 1 and {self.motor_count}"
            try:
                value = float(parts[2])
            except Exception:
                return True, "Throttle must be numeric"
            return self._set_throttle(motor, value)
        if command in {"inc", "dec"}:
            if len(parts) < 2:
                return True, f"Usage: {command} <motor> [amount]"
            motor = self._parse_motor(parts[1])
            if motor is None:
                return True, f"Motor must be between 1 and {self.motor_count}"
            amount = self.increment
            if len(parts) >= 3:
                try:
                    amount = abs(float(parts[2]))
                except Exception:
                    return True, "Amount must be numeric"
            direction = 1 if command == "inc" else -1
            return self.nudge(motor, direction, source="cmd", amount=amount)
        if command == "stop":
            if len(parts) < 2:
                return True, "Usage: stop <motor>"
            motor = self._parse_motor(parts[1])
            if motor is None:
                return True, f"Motor must be between 1 and {self.motor_count}"
            return self.stop_motor(motor)
        if command == "stopall":
            return self.stop_motor()
        if command == "release":
            if len(parts) == 1 or parts[1].lower() == "all":
                return self.release_motor()
            motor = self._parse_motor(parts[1])
            if motor is None:
                return True, f"Motor must be between 1 and {self.motor_count}"
            return self.release_motor(motor)
        return True, "Unknown command"


def format_banner(state: DCControlState) -> str:
    return (
        "\nAdafruit Motor HAT DC control\n"
        f"Default increment: {state.increment:.2f}\n"
        f"Motors detected: {state.motor_count}\n"
    )
