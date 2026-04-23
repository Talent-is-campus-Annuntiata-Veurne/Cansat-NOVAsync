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
import math

import _thread
import machine
import utime

from PicoRobotics import KitronikPicoRobotics

try:
	import data_module
except Exception:
	data_module = None

if data_module is None:
	try:
		if "/receiver" not in sys.path:
			sys.path.append("/receiver")
		import data_module
	except Exception:
		data_module = None


DEFAULT_INCREMENT = 0.12
DEFAULT_MOTOR_COUNT = 4
POT_STREAM_PERIOD_MS = 500
POT_SMOOTHING_ALPHA = 0.35  # Exponential smoothing for raw ADC noise
POT_SAMPLE_PERIOD_MS = 10  # Non-blocking sample cadence from reference article
AUTO_TRACK_PERIOD_MS = 420
GPS_AUTO_PULSE_MS = 1000
GPS_AUTO_SETTLE_MS = 1000
GPS_AUTO_STEP = 0.12
GPS_AUTO_AZ_DEADBAND_DEG = 6.0
GPS_AUTO_EL_DEADBAND_DEG = 4.0
GPS_AUTO_AZ_OPPOSITE_WINDOW_DEG = 12.0
# Coordinate convention is fixed: left=-, right=+, down=-, up=+.
# These multipliers map desired direction to your motor wiring/mechanics.
GPS_AUTO_AZ_MOTOR_SIGN = 1
GPS_AUTO_EL_MOTOR_SIGN = 1
# Antenna boresight offset in degrees between azimuth pot angle and antenna front.
# 0 means azimuth angle already represents the antenna front heading.
AZIMUTH_FRONT_OFFSET_DEG = 0.0
# Bearing frame offset for incoming cansat location.
# Keep default at 0; runtime reverse toggle can add +180 on demand.
GPS_TARGET_BEARING_OFFSET_DEG = 0.0
NORTH_LOCK_TOLERANCE_DEG = 2.5
AZIMUTH_AUTO_CENTER_RAW = 30000
AZIMUTH_AUTO_LIMIT_DEG = 180.0

# Pot configuration (raw spans can be tweaked after a manual sweep)
POT_CHANNELS = (
	{
		"name": "azimuth",
		"pin": 26,  # GP26 / ADC0 (keeps SPI pins free for the RFM69HCW)
		"span_degrees": 3600.0,
		"zero_deg": 0.0,
		"total_ohms": 100000,  # 10-turn precision pot (~10 kΩ end-to-end)
		"ohm_min": 0.0,
		"ohm_max": 100000.0,
		"gear_ratio": 0.15556,
		"wrap_degrees": 360.0,
		"center_raw": 30000,
		"signed_output": True,
		"angle_smoothing_window": 1,
		"angle_filter_alpha": 0.55,
		"invert": False,
		"enabled": True,
	},
	{
		"name": "elevation",
		"pin": 27,  # GP27 / ADC1
		"span_degrees": 270.0,  # single-turn pot covering roughly 3/4 rotation
		"zero_deg": 0.0,
		"total_ohms": 50000.0,
		"ohm_min": 0.0,
		"ohm_max": 50000.0,
		"gear_ratio": 0.76,
		"wrap_degrees": None,
		"center_raw": None,
		"signed_output": False,
		"angle_smoothing_window": 3,
		"angle_filter_alpha": 0.45,
		"invert": True,
		"enabled": True,
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
		total_ohms,
		ohm_min,
		ohm_max,
		gear_ratio,
		wrap_degrees,
		center_raw,
		signed_output,
		angle_smoothing_window,
		angle_filter_alpha,
		invert,
		sample_period_ms=POT_SAMPLE_PERIOD_MS,
	):
		self.name = name
		self.span_degrees = span_degrees
		self.zero_deg = zero_deg
		self.total_ohms = total_ohms
		self.ohm_min = ohm_min
		self.ohm_max = ohm_max
		self.gear_ratio = gear_ratio
		self.wrap_degrees = wrap_degrees
		self.center_raw = center_raw
		self.signed_output = bool(signed_output)
		self.angle_smoothing_window = max(1, int(angle_smoothing_window))
		self.angle_filter_alpha = min(1.0, max(0.0, float(angle_filter_alpha)))
		self.invert = invert
		self.sample_period_ms = sample_period_ms
		self.adc = machine.ADC(pin)
		self._zero_offset = 0.0
		self._filtered_raw = None
		self._last_sample_ticks = utime.ticks_ms()
		self._angle_history = []
		self._filtered_angle = None

	def read_raw(self):
		return self.adc.read_u16()

	def _maybe_sample(self):
		now = utime.ticks_ms()
		if (
			self._filtered_raw is None
			or utime.ticks_diff(now, self._last_sample_ticks) >= self.sample_period_ms
		):
			self._last_sample_ticks = now
			raw = self.read_raw()
			if self._filtered_raw is None:
				self._filtered_raw = raw
			else:
				self._filtered_raw = int(
					(self._filtered_raw * (1.0 - POT_SMOOTHING_ALPHA))
					+ (raw * POT_SMOOTHING_ALPHA)
				)

	def read_filtered_raw(self):
		self._maybe_sample()
		return self._filtered_raw if self._filtered_raw is not None else 0

	def raw_to_ohms(self, raw):
		clamped = max(0, min(65535, raw))
		return (clamped / 65535.0) * self.total_ohms

	def _normalize_degrees(self, value):
		if self.wrap_degrees is None:
			return value
		wrap = float(self.wrap_degrees)
		if wrap <= 0:
			return value
		if self.signed_output:
			half = wrap / 2.0
			v = ((value + half) % wrap) - half
			return v
		v = value % wrap
		if v < 0:
			v += wrap
		return v

	def _raw_to_base_degrees(self, raw):
		ohms = self.raw_to_ohms(raw)
		span = max(self.ohm_max - self.ohm_min, 1e-6)
		fraction = (ohms - self.ohm_min) / span
		fraction = max(0.0, min(1.0, fraction))
		if self.invert:
			fraction = 1.0 - fraction
		return (self.zero_deg + fraction * self.span_degrees) * self.gear_ratio

	def _raw_to_centered_base(self, raw):
		base = self._raw_to_base_degrees(raw)
		if self.center_raw is not None:
			try:
				base -= self._raw_to_base_degrees(float(self.center_raw))
			except Exception:
				pass
		return base

	def relative_degrees_from_raw_center(self, raw, center_raw):
		current_base = self._raw_to_base_degrees(raw)
		center_base = self._raw_to_base_degrees(center_raw)
		return current_base - center_base

	def raw_to_degrees(self, raw):
		base = self._raw_to_centered_base(raw)
		mapped = base - self._zero_offset
		return self._normalize_degrees(mapped)

	def _smooth_angle(self, angle):
		if self.angle_smoothing_window <= 1:
			return angle
		self._angle_history.append(angle)
		if len(self._angle_history) > self.angle_smoothing_window:
			self._angle_history.pop(0)
		return sum(self._angle_history) / len(self._angle_history)

	def _filter_angle(self, angle):
		alpha = self.angle_filter_alpha
		if alpha <= 0.0:
			return angle
		if self._filtered_angle is None:
			self._filtered_angle = angle
			return angle

		if self.wrap_degrees is None:
			self._filtered_angle = (alpha * angle) + ((1.0 - alpha) * self._filtered_angle)
			return self._filtered_angle

		# Wrap-aware low-pass filter for circular angles (e.g. 359 -> 0 transition).
		wrap = float(self.wrap_degrees)
		prev = self._filtered_angle
		diff = angle - prev
		if diff > (wrap / 2.0):
			diff -= wrap
		elif diff < -(wrap / 2.0):
			diff += wrap
		self._filtered_angle = prev + (alpha * diff)
		return self._normalize_degrees(self._filtered_angle)

	def snapshot(self):
		raw = self.read_filtered_raw()
		ohms = self.raw_to_ohms(raw)
		degrees = self.raw_to_degrees(raw)
		degrees = self._smooth_angle(degrees)
		degrees = self._filter_angle(degrees)
		return {
			"name": self.name,
			"raw": raw,
			"ohms": ohms,
			"degrees": degrees,
		}

	def set_zero_here(self):
		return self.align_to(0.0)

	def align_to(self, target_degrees):
		raw = self.read_filtered_raw()
		base = self._raw_to_centered_base(raw)
		self._zero_offset = base - float(target_degrees)
		self._angle_history = []
		self._filtered_angle = None
		return self.raw_to_degrees(raw)

	def set_gear_ratio(self, ratio):
		raw = self.read_filtered_raw()
		current_mech = self.raw_to_degrees(raw)
		self.gear_ratio = float(ratio)
		base = self._raw_to_centered_base(raw)
		self._zero_offset = base - current_mech
		self._angle_history = []
		self._filtered_angle = None
		return self.gear_ratio

	def capture_bound(self, kind):
		reading = self.snapshot()
		value = reading["ohms"]
		if kind == "min":
			self.ohm_min = value
		elif kind == "max":
			self.ohm_max = value
		return value


class PotAngleReader:
	def __init__(self, configs):
		self.channels = []
		self.disabled = []
		self._lookup = {}
		for cfg in configs:
			cfg_copy = dict(cfg)
			enabled = cfg_copy.pop("enabled", True)
			name = cfg_copy.get("name", "pot")
			if not enabled:
				self.disabled.append(name)
				continue
			channel = PotChannel(**cfg_copy)
			self.channels.append(channel)
			self._lookup[channel.name] = channel

	def read_angles(self):
		readings = []
		for channel in self.channels:
			try:
				readings.append(channel.snapshot())
			except Exception as exc:
				readings.append({"name": channel.name, "error": str(exc)})
		for name in self.disabled:
			readings.append({"name": name, "error": "disabled"})
		return readings

	def _get_channel(self, name):
		return self._lookup.get(name)

	def set_zero(self, name):
		channel = self._get_channel(name)
		if not channel:
			return False, f"Unknown potentiometer '{name}'"
		value = channel.set_zero_here()
		return True, f"Pot '{name}' zero set (offset {value:.2f}°)"

	def set_bound(self, name, bound):
		channel = self._get_channel(name)
		if not channel:
			return False, f"Unknown potentiometer '{name}'"
		value = channel.capture_bound(bound)
		return True, f"Pot '{name}' {bound} recorded at {value:.1f} Ω"

	def align_angle(self, name, degrees):
		channel = self._get_channel(name)
		if not channel:
			return False, f"Unknown potentiometer '{name}'"
		value = channel.align_to(degrees)
		return True, f"Pot '{name}' aligned to {value:.2f}°"

	def set_ratio(self, name, ratio):
		channel = self._get_channel(name)
		if not channel:
			return False, f"Unknown potentiometer '{name}'"
		try:
			ratio_val = float(ratio)
		except Exception:
			return False, "Ratio must be numeric"
		if ratio_val == 0:
			return False, "Ratio cannot be zero"
		value = channel.set_gear_ratio(ratio_val)
		return True, f"Pot '{name}' gear ratio set to {value:.5f}"

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

	def format_raw_status(self, payload):
		parts = []
		for entry in payload:
			if "error" in entry:
				parts.append(f"{entry['name']}=ERR")
				continue
			raw = int(entry.get("raw", 0))
			ohms = entry.get("ohms")
			ohms_part = f"{ohms:.0f}" if isinstance(ohms, (int, float)) else "nan"
			parts.append(f"{entry['name']}={raw}:{ohms_part}")
		if not parts:
			parts.append("no-data")
		return "POTRAW," + ",".join(parts)


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
		self.auto_enabled = False
		self.auto_reverse_az = False
		self.auto_base_lat = None
		self.auto_base_lon = None
		self.auto_base_alt = 0.0

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
			self.auto_enabled = False
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
			self.auto_enabled = False
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
		if cmd == "pot":
			if len(parts) < 3:
				return True, "Usage: pot <zero|min|max|align|ratio> <name> [value]"
			action = parts[1].lower()
			target = parts[2].lower()
			if action == "zero":
				return POT_READER.set_zero(target)
			if action in {"min", "max"}:
				return POT_READER.set_bound(target, action)
			if action == "align":
				if len(parts) < 4:
					return True, "Usage: pot align <name> <degrees>"
				try:
					deg = float(parts[3])
				except Exception:
					return True, "Degrees must be numeric"
				return POT_READER.align_angle(target, deg)
			if action == "ratio":
				if len(parts) < 4:
					return True, "Usage: pot ratio <name> <ratio>"
				return POT_READER.set_ratio(target, parts[3])
			return True, "Unknown pot action"
		if cmd == "stop":
			if len(parts) < 2:
				return True, "Usage: stop <motor>"
			self.auto_enabled = False
			try:
				motor = int(parts[1])
			except Exception:
				return True, "Motor must be numeric"
			return self.stop_motor(motor)
		if cmd == "stopall":
			self.auto_enabled = False
			return self.stop_motor()
		if cmd == "release":
			self.auto_enabled = False
			if len(parts) == 1 or parts[1].lower() == "all":
				return self.release_motor()
			try:
				motor = int(parts[1])
			except Exception:
				return True, "Motor must be numeric"
			return self.release_motor(motor)
		if cmd == "auto":
			if len(parts) == 1 or parts[1].lower() == "status":
				if self.auto_base_lat is None or self.auto_base_lon is None:
					base_text = "unset"
				else:
					base_text = "%.6f,%.6f,%.1f" % (self.auto_base_lat, self.auto_base_lon, self.auto_base_alt)
				rev_text = "ON" if self.auto_reverse_az else "OFF"
				return True, "AUTO=%s base=%s reverseaz=%s" % (("ON" if self.auto_enabled else "OFF"), base_text, rev_text)
			mode = parts[1].lower()
			if mode in {"reverseaz", "reverse", "raz"}:
				if len(parts) < 3:
					self.auto_reverse_az = not self.auto_reverse_az
				else:
					arg = parts[2].lower()
					if arg in {"on", "1", "true"}:
						self.auto_reverse_az = True
					elif arg in {"off", "0", "false"}:
						self.auto_reverse_az = False
					elif arg in {"toggle", "flip"}:
						self.auto_reverse_az = not self.auto_reverse_az
					elif arg == "status":
						pass
					else:
						return True, "Usage: auto reverseaz <on|off|toggle|status>"
				return True, "Auto reverse azimuth %s" % ("enabled" if self.auto_reverse_az else "disabled")
			if mode == "base":
				if len(parts) < 4:
					return True, "Usage: auto base <lat> <lon> [alt_m]"
				try:
					self.auto_base_lat = float(parts[2])
					self.auto_base_lon = float(parts[3])
					if len(parts) >= 5:
						self.auto_base_alt = float(parts[4])
				except Exception:
					return True, "Base coordinates must be numeric"
				return True, "Auto base set to %.6f, %.6f, %.1f m" % (self.auto_base_lat, self.auto_base_lon, self.auto_base_alt)
			if mode in {"clearbase", "baseclear"}:
				self.auto_base_lat = None
				self.auto_base_lon = None
				self.auto_base_alt = 0.0
				return True, "Auto base cleared"
			if mode in {"on", "1", "true"}:
				self.auto_enabled = True
				return True, "Auto tracking enabled (GPS mode)"
			if mode in {"off", "0", "false"}:
				self.auto_enabled = False
				self.stop_motor(1)
				self.stop_motor(2)
				return True, "Auto tracking disabled"
			return True, "Usage: auto <on|off|status|base|clearbase|reverseaz>"
		return True, "Unknown command"


class RSSIAutoTracker:
	def __init__(self, state, pot_reader, telemetry_getter, poll_hook=None):
		self.state = state
		self.pot_reader = pot_reader
		self.telemetry_getter = telemetry_getter
		self.poll_hook = poll_hook
		self.last_step_ticks = utime.ticks_ms()
		self.axis_order = [1, 2]
		self.axis_index = 0
		self.direction = {1: 1, 2: 1}
		self._last_status_ticks = utime.ticks_ms()
		self._last_status_state = None
		self._last_az_dir_desired = 1

	def _emit_status(self, state="idle", motor=0, direction=0, rssi=None, delta=None, force=False):
		now = utime.ticks_ms()
		if not force:
			if state == self._last_status_state and utime.ticks_diff(now, self._last_status_ticks) < 1200:
				return
		self._last_status_state = state
		self._last_status_ticks = now
		rssi_text = "nan" if rssi is None else "%0.1f" % rssi
		delta_text = "nan" if delta is None else "%0.2f" % delta
		print(
			"AUTO,enabled=%d,state=%s,motor=%d,dir=%d,rssi=%s,delta=%s,reverseaz=%d"
			% (
				1 if self.state.auto_enabled else 0,
				state,
				motor,
				direction,
				rssi_text,
				delta_text,
				1 if bool(getattr(self.state, "auto_reverse_az", False)) else 0,
			)
		)

	def _enforce_azimuth_limit(self, dir_sign):
		channel = self.pot_reader._get_channel("azimuth")
		if channel is None:
			return dir_sign
		raw = channel.read_filtered_raw()
		rel_deg = channel.relative_degrees_from_raw_center(raw, AZIMUTH_AUTO_CENTER_RAW)
		if dir_sign > 0 and rel_deg >= AZIMUTH_AUTO_LIMIT_DEG:
			self._emit_status(state="az_limit_pos", motor=1, direction=dir_sign, delta=rel_deg, force=True)
			return 0
		if dir_sign < 0 and rel_deg <= -AZIMUTH_AUTO_LIMIT_DEG:
			self._emit_status(state="az_limit_neg", motor=1, direction=dir_sign, delta=rel_deg, force=True)
			return 0
		return dir_sign

	def _choose_azimuth_dir(self, az_err):
		if abs(az_err) <= GPS_AUTO_AZ_DEADBAND_DEG:
			return 0

		# Ambiguous case near 180° (target on opposite side): steer away from
		# whichever physical azimuth limit we are closest to.
		if abs(abs(az_err) - 180.0) <= GPS_AUTO_AZ_OPPOSITE_WINDOW_DEG:
			channel = self.pot_reader._get_channel("azimuth")
			if channel is not None:
				try:
					raw = channel.read_filtered_raw()
					rel_deg = channel.relative_degrees_from_raw_center(raw, AZIMUTH_AUTO_CENTER_RAW)
					if rel_deg > 0:
						dir_choice = -1
					elif rel_deg < 0:
						dir_choice = 1
					else:
						dir_choice = self._last_az_dir_desired
				except Exception:
					dir_choice = self._last_az_dir_desired
			else:
				dir_choice = self._last_az_dir_desired
		else:
			dir_choice = 1 if az_err > 0 else -1

		if dir_choice != 0:
			self._last_az_dir_desired = dir_choice
		return dir_choice

	def _normalize360(self, value):
		v = value % 360.0
		if v < 0:
			v += 360.0
		return v

	def _shortest_diff(self, target_deg, current_deg):
		diff = self._normalize360(target_deg) - self._normalize360(current_deg)
		if diff > 180.0:
			diff -= 360.0
		elif diff < -180.0:
			diff += 360.0
		return diff

	def _haversine_m(self, lat1, lon1, lat2, lon2):
		r = 6371000.0
		p1 = math.radians(lat1)
		p2 = math.radians(lat2)
		dp = math.radians(lat2 - lat1)
		dl = math.radians(lon2 - lon1)
		a = (math.sin(dp / 2.0) ** 2) + (math.cos(p1) * math.cos(p2) * (math.sin(dl / 2.0) ** 2))
		c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(1e-12, 1.0 - a)))
		return r * c

	def _bearing_deg(self, lat1, lon1, lat2, lon2):
		p1 = math.radians(lat1)
		p2 = math.radians(lat2)
		dl = math.radians(lon2 - lon1)
		y = math.sin(dl) * math.cos(p2)
		x = (math.cos(p1) * math.sin(p2)) - (math.sin(p1) * math.cos(p2) * math.cos(dl))
		return self._normalize360(math.degrees(math.atan2(y, x)))

	def _get_angle(self, readings, name):
		for entry in readings:
			if entry.get("name") == name and "degrees" in entry:
				try:
					return float(entry.get("degrees"))
				except Exception:
					return None
		return None

	def _azimuth_heading_deg(self, azimuth_deg, elevation_deg):
		# Convention: 0 deg azimuth at 0 deg elevation is true North.
		# Snap near-origin readings to 0 to avoid jitter ambiguity around the home pose.
		if (
			abs(azimuth_deg) <= NORTH_LOCK_TOLERANCE_DEG
			and abs(elevation_deg) <= NORTH_LOCK_TOLERANCE_DEG
		):
			return self._normalize360(0.0 + AZIMUTH_FRONT_OFFSET_DEG)
		return self._normalize360(azimuth_deg + AZIMUTH_FRONT_OFFSET_DEG)

	def _compute_gps_guidance(self):
		if self.state.auto_base_lat is None or self.state.auto_base_lon is None:
			return None
		tel = self.telemetry_getter() if callable(self.telemetry_getter) else None
		if not isinstance(tel, dict):
			return None
		if int(tel.get("fix") or 0) != 1:
			return None
		try:
			target_lat = float(tel.get("lat"))
			target_lon = float(tel.get("lon"))
		except Exception:
			return None
		baro_alt = None
		gps_alt = None
		try:
			baro_alt = float(tel.get("baro"))
			if not math.isfinite(baro_alt):
				baro_alt = None
		except Exception:
			baro_alt = None
		try:
			gps_alt = float(tel.get("alt"))
			if not math.isfinite(gps_alt):
				gps_alt = None
		except Exception:
			gps_alt = None

		# Elevation needs a height DIFFERENCE over horizontal distance.
		# Use barometric height as relative height above launch (preferred),
		# then GPS altitude fallback in base-altitude frame when baro is bad.
		if baro_alt is not None and baro_alt >= -1.0:
			height_delta = baro_alt
		elif gps_alt is not None:
			height_delta = gps_alt - self.state.auto_base_alt
		else:
			height_delta = 0.0

		readings = self.pot_reader.read_angles()
		az_now = self._get_angle(readings, "azimuth")
		el_now = self._get_angle(readings, "elevation")
		if az_now is None or el_now is None:
			return None
		heading_now = self._azimuth_heading_deg(az_now, el_now)

		target_bearing_offset = GPS_TARGET_BEARING_OFFSET_DEG
		if bool(getattr(self.state, "auto_reverse_az", False)):
			target_bearing_offset += 180.0
		target_az = self._normalize360(
			self._bearing_deg(self.state.auto_base_lat, self.state.auto_base_lon, target_lat, target_lon)
			+ target_bearing_offset
		)
		horizontal = self._haversine_m(self.state.auto_base_lat, self.state.auto_base_lon, target_lat, target_lon)
		target_el = math.degrees(math.atan2(height_delta, max(1.0, horizontal)))

		az_err = self._shortest_diff(target_az, heading_now)
		el_err = target_el - el_now
		az_dir_desired = self._choose_azimuth_dir(az_err)
		el_dir_desired = 0 if abs(el_err) <= GPS_AUTO_EL_DEADBAND_DEG else (1 if el_err > 0 else -1)
		az_dir = az_dir_desired * GPS_AUTO_AZ_MOTOR_SIGN
		el_dir = el_dir_desired * GPS_AUTO_EL_MOTOR_SIGN
		return {
			"az_err": az_err,
			"el_err": el_err,
			"az_dir": az_dir,
			"el_dir": el_dir,
			"az_dir_desired": az_dir_desired,
			"el_dir_desired": el_dir_desired,
		}

	def _choose_gps_move(self, gps):
		az_norm = abs(gps["az_err"]) / max(1.0, GPS_AUTO_AZ_DEADBAND_DEG)
		el_norm = abs(gps["el_err"]) / max(1.0, GPS_AUTO_EL_DEADBAND_DEG)
		if gps["az_dir"] != 0 and (gps["el_dir"] == 0 or az_norm >= el_norm):
			return 1, gps["az_dir"], gps["az_err"]
		if gps["el_dir"] != 0:
			return 2, gps["el_dir"], gps["el_err"]
		return 0, 0, 0.0

	def _pulse_motor(self, motor, direction, step, pulse_ms, settle_ms):
		dir_sign = direction
		if motor == 1:
			dir_sign = self._enforce_azimuth_limit(dir_sign)
		if dir_sign == 0:
			return {"dir": 0, "retarget": False}
		self.state._set_throttle(motor, dir_sign * step)
		pulse_deadline = utime.ticks_add(utime.ticks_ms(), pulse_ms)
		retarget = False
		retarget_target = (motor, dir_sign, 0.0)
		while utime.ticks_diff(pulse_deadline, utime.ticks_ms()) > 0:
			if callable(self.poll_hook):
				try:
					self.poll_hook()
				except Exception:
					pass
			gps = self._compute_gps_guidance()
			if gps is not None:
				next_motor, next_dir, next_err = self._choose_gps_move(gps)
				if next_motor == 0:
					retarget = True
					retarget_target = (0, 0, 0.0)
					break
				if next_motor != motor or next_dir != dir_sign:
					retarget = True
					retarget_target = (next_motor, next_dir, next_err)
					break
			utime.sleep_ms(50)
		self.state._set_throttle(motor, 0.0)
		if retarget:
			return {
				"dir": dir_sign,
				"retarget": True,
				"next_motor": retarget_target[0],
				"next_dir": retarget_target[1],
				"next_err": retarget_target[2],
			}

		settle_deadline = utime.ticks_add(utime.ticks_ms(), settle_ms)
		while utime.ticks_diff(settle_deadline, utime.ticks_ms()) > 0:
			if callable(self.poll_hook):
				try:
					self.poll_hook()
				except Exception:
					pass
			utime.sleep_ms(50)
		return {"dir": dir_sign, "retarget": False}

	def maybe_step(self):
		now = utime.ticks_ms()
		if utime.ticks_diff(now, self.last_step_ticks) < AUTO_TRACK_PERIOD_MS:
			return
		self.last_step_ticks = now

		if not self.state.auto_enabled:
			self._emit_status(state="off")
			return

		gps = self._compute_gps_guidance()
		if gps is not None:
			if gps["az_dir"] == 0 and gps["el_dir"] == 0:
				self._emit_status(state="gps_hold", delta=0.0)
				return
			motor, dir_sign, err_value = self._choose_gps_move(gps)
			if motor == 0:
				self._emit_status(state="gps_hold", delta=0.0)
				return
			pulse_result = self._pulse_motor(motor, dir_sign, GPS_AUTO_STEP, GPS_AUTO_PULSE_MS, GPS_AUTO_SETTLE_MS)
			if pulse_result.get("retarget"):
				next_motor = pulse_result.get("next_motor", 0)
				next_dir = pulse_result.get("next_dir", 0)
				next_err = pulse_result.get("next_err", 0.0)
				self._emit_status(state="gps_retarget", motor=next_motor, direction=next_dir, rssi=None, delta=next_err, force=True)
				self.last_step_ticks = utime.ticks_add(utime.ticks_ms(), -AUTO_TRACK_PERIOD_MS)
				return
			self._emit_status(state="gps_align", motor=motor, direction=pulse_result.get("dir", dir_sign), rssi=None, delta=err_value, force=True)
			return

		self._emit_status(state="wait_gps")
		return


def _print_banner(state):
	print("\nKitronik DC motor control (Pico)")
	print(f"Motors available: {state.motor_count}")
	print(f"Default increment: {state.increment:.2f}")
	print("Hotkeys -> W/S (motor1), I/K (motor2), arrows (motor3/4). Type 'status' or '?' for help.")


def _report(state, pot_reader):
	readings = pot_reader.read_angles()
	print(state.status_line())
	print(pot_reader.format_status(readings))
	print(pot_reader.format_raw_status(readings))
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
	state.auto_enabled = False
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
	flush = getattr(sys.stdout, "flush", None)
	if callable(flush):
		flush()


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
	global _POT_STREAM_RUNNING
	_POT_STREAM_RUNNING = True
	_thread.start_new_thread(_background_stream_loop, (state,))
	try:
		_interactive_loop(state)
	finally:
		_POT_STREAM_RUNNING = False
		state.release_motor()
		print("Controller exited; motors released")


POT_READER = PotAngleReader(POT_CHANNELS)
_POT_STREAM_RUNNING = False
_LATEST_TELEMETRY = None
_LATEST_TELEMETRY_COUNTER = None


def _receiver_emit(line):
	global _LATEST_TELEMETRY, _LATEST_TELEMETRY_COUNTER
	print(line)
	if not isinstance(line, str) or not line.startswith("TEL,"):
		return
	prev_payload = _LATEST_TELEMETRY if isinstance(_LATEST_TELEMETRY, dict) else {}
	payload = dict(prev_payload)
	for token in line.split(",")[1:]:
		if "=" not in token:
			continue
		key, value = token.split("=", 1)
		key = key.strip()
		value = value.strip()
		if value in {"nan", "NOFIX", "nl", ""}:
			payload[key] = None
			continue
		if key in {"c", "fix"}:
			try:
				payload[key] = int(value)
			except Exception:
				payload[key] = None
		elif key in {"lat", "lon", "alt", "baro", "rx_rssi"}:
			try:
				payload[key] = float(value)
			except Exception:
				payload[key] = None
		else:
			payload[key] = value

	payload_counter = payload.get("c")
	if isinstance(payload_counter, int):
		if isinstance(_LATEST_TELEMETRY_COUNTER, int) and payload_counter < _LATEST_TELEMETRY_COUNTER:
			# Ignore delayed telemetry frames so auto-tracking doesn't chase old positions.
			return
		_LATEST_TELEMETRY_COUNTER = payload_counter
	_LATEST_TELEMETRY = payload


def _get_latest_telemetry():
	return _LATEST_TELEMETRY


def _background_stream_loop(state):
	global _POT_STREAM_RUNNING
	receiver = None
	rx_retry_ms = 2000
	next_rx_retry_ticks = utime.ticks_ms()

	def _try_init_receiver():
		nonlocal receiver, next_rx_retry_ticks
		if data_module is None:
			return
		try:
			receiver = data_module.ReceiverDataModule(
				emit_line=_receiver_emit,
			)
			print("[RX] receiver ready")
		except Exception as exc:
			receiver = None
			print("[RX] receiver init error:", exc)
			next_rx_retry_ticks = utime.ticks_add(utime.ticks_ms(), rx_retry_ms)

	def _poll_receiver_once():
		nonlocal receiver, next_rx_retry_ticks
		if receiver is None:
			return
		try:
			receiver.poll_once(timeout=0)
		except Exception as exc:
			print("[RX] receiver poll error (hook):", exc)
			receiver = None
			next_rx_retry_ticks = utime.ticks_add(utime.ticks_ms(), rx_retry_ms)

	auto_tracker = RSSIAutoTracker(state, POT_READER, _get_latest_telemetry, poll_hook=_poll_receiver_once)
	last_pot_ticks = utime.ticks_ms()
	if data_module is None:
		print("[RX] data_module import failed; telemetry receiver disabled")
	else:
		_try_init_receiver()

	while _POT_STREAM_RUNNING:
		now = utime.ticks_ms()
		if receiver is None and data_module is not None:
			if utime.ticks_diff(now, next_rx_retry_ticks) >= 0:
				_try_init_receiver()
				if receiver is None:
					next_rx_retry_ticks = utime.ticks_add(now, rx_retry_ms)

		if receiver is not None:
			try:
				receiver.poll_once(timeout=0.01)
			except Exception as exc:
				print("[RX] receiver poll error:", exc)
				receiver = None
				next_rx_retry_ticks = utime.ticks_add(utime.ticks_ms(), rx_retry_ms)

		try:
			auto_tracker.maybe_step()
		except Exception as exc:
			state.auto_enabled = False
			print("[AUTO] step error:", exc)

		now = utime.ticks_ms()
		if utime.ticks_diff(now, last_pot_ticks) >= POT_STREAM_PERIOD_MS:
			last_pot_ticks = now
			try:
				readings = POT_READER.read_angles()
				print(POT_READER.format_status(readings))
				print(POT_READER.format_raw_status(readings))
			except Exception as exc:
				print("[POT] stream error:", exc)
		utime.sleep_ms(2)


if __name__ == "__main__":
	main()
