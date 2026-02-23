"""Interactive DC motor driver for the Kitronik Pico Robotics Board.

This script mirrors the ``bigpiDCmain.py`` command set but runs entirely on the
Raspberry Pi Pico. It keeps the same keyboard shortcuts (W/S/I/K + arrow keys)
for nudging four DC motors and exposes the same textual commands (set, inc,
dec, stop, release, increment, status, etc.).

Two multi-turn potentiometers are read directly via the Pico's built-in ADCs,
so no MCP3008 is required. By default the wipers are expected on GP26 (ADC0)
for azimuth and GP27 (ADC1) for elevation, leaving SPI0 (GP16–GP19) free for a
future RFM69HCW radio.

Usage: copy this file plus ``PicoRobotics.py`` to the Pico, open a Thonny shell
or mpremote REPL, and run ``main.py``. Type commands or use the hotkeys without
pressing Enter. Press ``q`` to quit.
"""

import sys

import machine
import utime

from PicoRobotics import KitronikPicoRobotics


DEFAULT_INCREMENT = 0.12
DEFAULT_MOTOR_COUNT = 4

# Pot configuration (raw spans can be tweaked after a manual sweep)
POT_CHANNELS = (
	{
		"name": "azimuth",
		"pin": 26,  # GP26 / ADC0 (keeps SPI pins free for the RFM69HCW)
		"span_degrees": 540.0,
		"zero_deg": 0.0,
		"raw_min": 1500,
		"raw_max": 64000,
		"invert": False,
	},
	{
		"name": "elevation",
		"pin": 27,  # GP27 / ADC1
		"span_degrees": 180.0,
		"zero_deg": 0.0,
		"raw_min": 2000,
		"raw_max": 63000,
		"invert": False,
	},
)

KEY_BINDS = {
	"w": (1, 1),
	"s": (1, -1),
	"i": (2, 1),
	"k": (2, -1),
}

ESCAPE_BINDS = {
	"\x1b[A": (3, 1),  # Up arrow
	"\x1b[B": (3, -1),  # Down arrow
	"\x1b[C": (4, 1),  # Right arrow
	"\x1b[D": (4, -1),  # Left arrow
	"\x1bOA": (3, 1),
	"\x1bOB": (3, -1),
	"\x1bOC": (4, 1),
	"\x1bOD": (4, -1),
}


class PotChannel:
	"""Represents a single Pico ADC channel backed by a potentiometer."""

	def __init__(
		self,
		*,
		name,
		pin,
		span_degrees,
		zero_deg,
		raw_min,
		raw_max,
		invert,
	):
		self.name = name
		self.span_degrees = span_degrees
		self.zero_deg = zero_deg
		self.raw_min = raw_min
		self.raw_max = raw_max
		self.invert = invert
		self.adc = machine.ADC(pin)

	def read_raw(self):
		return self.adc.read_u16()

	def raw_to_degrees(self, raw):
		span = max(1, self.raw_max - self.raw_min)
		clipped = min(max(raw, self.raw_min), self.raw_max)
		fraction = (clipped - self.raw_min) / span
		if self.invert:
			fraction = 1.0 - fraction
		return self.zero_deg + fraction * self.span_degrees

	def snapshot(self):
		raw = self.read_raw()
		return {
			"name": self.name,
			"raw": raw,
			"degrees": self.raw_to_degrees(raw),
		}


class PotAngleReader:
	def __init__(self, configs):
		self.channels = [PotChannel(**cfg) for cfg in configs]

	def read_angles(self):
		readings = []
		for channel in self.channels:
			try:
				readings.append(channel.snapshot())
			except Exception as exc:
				readings.append({"name": channel.name, "error": str(exc)})
		return readings

	def format_status(self, payload):
		parts = []
		for entry in payload:
			if "error" in entry:
				parts.append(f"{entry['name']}=ERR")
				continue
			parts.append(f"{entry['name']}={entry['degrees']:.1f}deg")
		if not parts:
			parts.append("no-data")
		return "ANGLES," + ",".join(parts)


class KitronikMotorController:
	"""Translate +/- throttle values into Kitronik motor calls."""

	def __init__(self, board):
		self.board = board

	def set_throttle(self, motor, value):
		if value is None:
			self.board.motorOff(motor)
			return
		value = max(-1.0, min(1.0, value))
		speed = int(abs(value) * 100)
		if speed == 0:
			self.board.motorOff(motor)
			return
		direction = "f" if value > 0 else "r"
		self.board.motorOn(motor, direction, speed)

	def release_all(self, motor_count):
		for motor in range(1, motor_count + 1):
			self.board.motorOff(motor)

	def stop_all(self, motor_count):
		for motor in range(1, motor_count + 1):
			self.board.motorOn(motor, "f", 0)


class PicoDCControlState:
	def __init__(self, controller, motor_count):
		self.controller = controller
		self.motor_count = motor_count
		self.increment = DEFAULT_INCREMENT
		self.throttles = {motor: 0.0 for motor in range(1, motor_count + 1)}

	def _validate_motor(self, motor):
		return motor is not None and 1 <= motor <= self.motor_count

	def _set_throttle(self, motor, value):
		self.controller.set_throttle(motor, value)
		self.throttles[motor] = value
		if value is None:
			return True, f"Motor {motor} released (coast)"
		if value == 0.0:
			return True, f"Motor {motor} stopped (brake)"
		return True, f"Motor {motor} throttle set to {value:+.2f}"

	def status_line(self):
		parts = []
		for motor in range(1, self.motor_count + 1):
			value = self.throttles.get(motor)
			parts.append("REL" if value is None else f"{value:+.2f}")
		return "THROT," + ",".join(parts)

	def nudge(self, motor, direction, *, source, amount=None):
		if not self._validate_motor(motor):
			return False, f"Motor must be 1-{self.motor_count}"
		delta = self.increment if amount is None else abs(amount)
		delta *= 1 if direction >= 0 else -1
		current = self.throttles.get(motor)
		if current is None:
			current = 0.0
		ok, msg = self._set_throttle(motor, current + delta)
		if ok:
			msg = f"({source} motor {motor} {'+' if delta >=0 else '-'}{abs(delta):.2f}) -> {msg}"
		return ok, msg

	def set_increment(self, amount):
		if amount <= 0:
			return False, "Increment must be greater than zero"
		self.increment = min(1.0, amount)
		return True, f"Increment set to {self.increment:.2f}"

	def stop_motor(self, motor=None):
		if motor is None:
			self.controller.stop_all(self.motor_count)
			for idx in range(1, self.motor_count + 1):
				self.throttles[idx] = 0.0
			return True, "All motors stopped"
		if not self._validate_motor(motor):
			return False, f"Motor must be 1-{self.motor_count}"
		return self._set_throttle(motor, 0.0)

	def release_motor(self, motor=None):
		if motor is None:
			self.controller.release_all(self.motor_count)
			for idx in range(1, self.motor_count + 1):
				self.throttles[idx] = None
			return True, "All motors released"
		if not self._validate_motor(motor):
			return False, f"Motor must be 1-{self.motor_count}"
		return self._set_throttle(motor, None)

	def handle_command(self, line):
		parts = line.split()
		cmd = parts[0].lower()
		if cmd in {"q", "quit", "exit"}:
			return False, "quit"
		if cmd == "status":
			return True, self.status_line()
		if cmd == "increment":
			if len(parts) < 2:
				return True, "Usage: increment <amount>"
			try:
				amount = float(parts[1])
			except Exception:
				return True, "Increment must be numeric"
			return self.set_increment(amount)
		if cmd == "set":
			if len(parts) < 3:
				return True, "Usage: set <motor> <throttle>"
			try:
				motor = int(parts[1])
				value = float(parts[2])
			except Exception:
				return True, "Motor/throttle must be numeric"
			if not self._validate_motor(motor):
				return True, f"Motor must be 1-{self.motor_count}"
			return self._set_throttle(motor, value)
		if cmd in {"inc", "dec"}:
			if len(parts) < 2:
				return True, f"Usage: {cmd} <motor> [amount]"
			try:
				motor = int(parts[1])
			except Exception:
				return True, "Motor must be numeric"
			amount = self.increment
			if len(parts) >= 3:
				try:
					amount = abs(float(parts[2]))
				except Exception:
					return True, "Amount must be numeric"
			direction = 1 if cmd == "inc" else -1
			return self.nudge(motor, direction, source="cmd", amount=amount)
		if cmd == "stop":
			if len(parts) < 2:
				return True, "Usage: stop <motor>"
			try:
				motor = int(parts[1])
			except Exception:
				return True, "Motor must be numeric"
			return self.stop_motor(motor)
		if cmd == "stopall":
			return self.stop_motor()
		if cmd == "release":
			if len(parts) == 1 or parts[1].lower() == "all":
				return self.release_motor()
			try:
				motor = int(parts[1])
			except Exception:
				return True, "Motor must be numeric"
			return self.release_motor(motor)
		return True, "Unknown command"


def _print_banner(state):
	print("\nKitronik DC motor control (Pico)")
	print(f"Motors available: {state.motor_count}")
	print(f"Default increment: {state.increment:.2f}")
	print("Hotkeys -> W/S (motor1), I/K (motor2), arrows (motor3/4). Type 'status' or '?' for help.")


def _report(state, pot_reader):
	readings = pot_reader.read_angles()
	print(state.status_line())
	print(pot_reader.format_status(readings))
	print(_format_pos_line(readings))


def _format_pos_line(readings):
	values = [entry["degrees"] for entry in readings if "degrees" in entry]
	if not values:
		values = [0.0, 0.0]
	elif len(values) == 1:
		values.append(values[0])
	return "POS,{:.2f},{:.2f}".format(values[0], values[1])


def _execute_binding(binding, state, source):
	motor, direction = binding
	ok, msg = state.nudge(motor, direction, source=source)
	print(msg)
	if ok:
		_report(state, POT_READER)


def _handle_command(line, state):
	if not line:
		return True
	if line.strip() == "?":
		_print_banner(state)
		_report(state, POT_READER)
		return True
	ok, msg = state.handle_command(line)
	if msg and msg != "quit":
		print(msg)
		_report(state, POT_READER)
	return ok and msg != "quit"


def _read_char():
	ch = sys.stdin.read(1)
	if isinstance(ch, bytes):
		try:
			ch = ch.decode("utf-8")
		except Exception:
			ch = ""
	return ch


def _capture_escape_sequence():
	buffer = "\x1b"
	while True:
		nxt = _read_char()
		if not nxt:
			break
		buffer += nxt
		if nxt.isalpha() or nxt == "~":
			break
	return buffer


def _handle_escape(buffer, state):
	binding = ESCAPE_BINDS.get(buffer)
	if binding:
		_execute_binding(binding, state, source="arrow")
	else:
		print(f"Unmapped escape sequence: {repr(buffer)}")


def _write(text: str) -> None:
	sys.stdout.write(text)
	sys.stdout.flush()


def _show_prompt(current=""):
	_write("motor> " + current)


def _interactive_loop(state):
	buffer = ""
	_show_prompt()
	while True:
		ch = _read_char()
		if not ch:
			utime.sleep_ms(5)
			continue
		if ch == "\x03":
			print("^C")
			return True
		if ch == "\x04":
			print("^D")
			return False
		if ch == "\x1b":
			print("")
			seq = _capture_escape_sequence()
			_handle_escape(seq, state)
			buffer = ""
			_show_prompt()
			continue
		if ch in ("\r", "\n"):
			print("")
			cmd = buffer.strip()
			buffer = ""
			if cmd:
				if not _handle_command(cmd, state):
					return False
			_show_prompt()
			continue
		if ch in ("\x08", "\x7f"):
			if buffer:
				buffer = buffer[:-1]
				_write("\b \b")
			continue
		if ch in KEY_BINDS and len(buffer) == 0:
			_execute_binding(KEY_BINDS[ch], state, source="key")
			_show_prompt()
			continue
		_write(ch)
		buffer += ch


def main():
	board = KitronikPicoRobotics()
	controller = KitronikMotorController(board)
	state = PicoDCControlState(controller, motor_count=DEFAULT_MOTOR_COUNT)
	_print_banner(state)
	_report(state, POT_READER)
	try:
		_interactive_loop(state)
	finally:
		state.release_motor()
		print("Controller exited; motors released")


POT_READER = PotAngleReader(POT_CHANNELS)


if __name__ == "__main__":
	main()
