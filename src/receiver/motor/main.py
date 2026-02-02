"""Interactive stepper test utility for the Kitronik Pico Robotics Board.

Copy this file together with PicoRobotics.py to the Raspberry Pi Pico and
start it from the Thonny shell (or mpremote) while the board is powered. The
script keeps running and waits for keyboard commands typed in the shell.

You can now nudge the two CNC3 (1.8°, 200 step) steppers with the **arrow
keys without pressing Enter**. Wiring reminder for your current build:

	Stepper 1 -> Motors 1+2 (M1 = red/blue, M2 = black/green)
	Stepper 2 -> Motors 3+4 (M3 = red/blue, M4 = black/green)

Arrow behaviour (hold Shift for faster key-repeat on the host PC):

	Up    = Stepper 1 forward (red-leading coil energised first)
	Down  = Stepper 1 reverse
	Right = Stepper 2 forward
	Left  = Stepper 2 reverse

Classic bindings (press the key then Enter) remain available:

	w = Stepper 1 forward (motors 1+2)
	s = Stepper 1 reverse
	i = Stepper 2 forward (motors 3+4)
	k = Stepper 2 reverse
	angle <motor> <f|r> <deg> [speed] -> move exact angle
	step <motor> <f|r> <count> [speed] -> move raw steps
	speed <ms> -> set default delay between steps (lower = faster)
	chunk <count> -> set default number of steps for hotkeys
	hold on|off -> keep coils energised after stepping
	release -> de-energise all coils immediately
	? -> print this help
	q -> quit (also releases coils)

"""

import sys
from PicoRobotics import KitronikPicoRobotics


DEFAULT_CHUNK = 25
# Default jog speed (ms delay between steps). Increase if motors stall.
DEFAULT_SPEED_MS = 35
DEFAULT_STEPS_PER_REV = 200

KEY_BINDS = {
	"w": (1, "f"),
	"s": (1, "r"),
	"i": (2, "f"),
	"k": (2, "r"),
}

ESCAPE_BINDS = {
	"\x1b[A": (1, "f"),  # Up arrow
	"\x1b[B": (1, "r"),  # Down arrow
	"\x1b[C": (2, "f"),  # Right arrow
	"\x1b[D": (2, "r"),  # Left arrow
	"\x1bOA": (1, "f"),
	"\x1bOB": (1, "r"),
	"\x1bOC": (2, "f"),
	"\x1bOD": (2, "r"),
}


def _print_banner(state):
	print("\nPico Robotics stepper control")
	print("Board initialised at I2C addr 0x6C (default wiring)")
	print(
		"Current defaults -> chunk: {chunk} steps, speed: {speed} ms, hold: {hold}".format(
			chunk=state["chunk_steps"],
			speed=state["speed_ms"],
			hold="on" if state["hold"] else "off",
		)
	)
	doc = globals().get("__doc__") or ""
	print(doc)


def _release_all(board):
	for motor in range(1, 5):
		try:
			board.motorOff(motor)
		except Exception:
			pass


def _execute_binding(binding, board, state, source="hotkey"):
	motor, direction = binding
	_step(board, motor, direction, state["chunk_steps"], state)
	print("({} -> stepper {} {})".format(source, motor, direction))


def _step(board, motor, direction, steps, state, speed_override=None):
	speed = speed_override if speed_override is not None else state["speed_ms"]
	hold = state["hold"]
	if steps <= 0:
		print("Ignoring zero or negative step request")
		return
	board.step(motor, direction, steps, speed=speed, holdPosition=hold)
	_update_angles(motor, direction, steps, state)
	_report_angles(state)
	print(
		"Stepper {m} {dir} for {steps} steps (speed {spd} ms, hold {hold})".format(
			m=motor,
			dir="forward" if direction == "f" else "reverse",
			steps=steps,
			spd=speed,
			hold="on" if hold else "off",
		)
	)


def _step_angle(board, motor, direction, angle, state, speed_override=None):
	steps = int(angle / (360 / state["steps_per_rev"]))
	if steps == 0:
		print("Requested angle below single-step resolution; nothing to do")
		return
	_step(board, motor, direction, steps, state, speed_override=speed_override)


def _update_angles(motor, direction, steps, state):
	angles = state["angles"]
	sign = 1 if direction == "f" else -1
	deg_per_step = 360 / state["steps_per_rev"]
	angles[motor] += sign * steps * deg_per_step


def _report_angles(state):
	angles = state["angles"]
	print("POS,{:.2f},{:.2f}".format(angles[1], angles[2]))


def _parse_int(value, label):
	try:
		return int(value)
	except Exception:
		print("Could not parse {}".format(label))
		return None


def _parse_float(value, label):
	try:
		return float(value)
	except Exception:
		print("Could not parse {}".format(label))
		return None


def _parse_speed(value):
	parsed = _parse_int(value, "speed")
	if parsed is None:
		return None
	if parsed < 5:
		parsed = 5
	if parsed > 2000:
		parsed = 2000
	return parsed


def _handle_line(line, board, state):
	if not line:
		return True
	lower = line.lower()
	if lower in ("q", "quit", "exit"):
		return False
	if lower == "?":
		_print_banner(state)
		return True
	if lower == "release":
		_release_all(board)
		print("All coils released")
		return True
	if lower == "zero":
		state["angles"][1] = 0.0
		state["angles"][2] = 0.0
		print("Angles reset to zero")
		_report_angles(state)
		return True
	if lower.startswith("hold "):
		state["hold"] = lower.endswith("on")
		print("Hold {}".format("enabled" if state["hold"] else "disabled"))
		return True
	if lower.startswith("speed "):
		speed = _parse_speed(lower.split()[1])
		if speed is not None:
			state["speed_ms"] = speed
			print("Default step delay set to {} ms".format(speed))
		return True
	if lower.startswith("chunk "):
		chunk = _parse_int(lower.split()[1], "chunk size")
		if chunk is not None and chunk > 0:
			state["chunk_steps"] = chunk
			print("Default chunk set to {} steps".format(chunk))
		return True
	if lower.startswith("stepsperrev "):
		spr = _parse_int(lower.split()[1], "steps per revolution")
		if spr is not None and spr > 0:
			state["steps_per_rev"] = spr
			print("Steps/rev set to {}".format(spr))
		return True
	if lower.startswith("step ") or lower.startswith("angle "):
		parts = lower.split()
		if len(parts) < 4:
			print("Usage: {} <motor> <f|r> <value> [speed]".format(parts[0]))
			return True
		motor = _parse_int(parts[1], "motor")
		if motor not in (1, 2):
			print("Motor must be 1 or 2")
			return True
		direction = parts[2]
		if direction not in ("f", "r"):
			print("Direction must be 'f' or 'r'")
			return True
		if parts[0] == "angle":
			value = _parse_float(parts[3], parts[0])
		else:
			value = _parse_int(parts[3], parts[0])
		if value is None or value <= 0:
			print("Value must be positive")
			return True
		speed_override = None
		if len(parts) >= 5:
			speed_override = _parse_speed(parts[4])
		if parts[0] == "step":
			_step(board, motor, direction, value, state, speed_override)
		else:
			_step_angle(board, motor, direction, value, state, speed_override)
		return True
	if lower in KEY_BINDS:
		_execute_binding(KEY_BINDS[lower], board, state, source="key")
		return True
	print("Unknown command. Type ? for help.")
	return True


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


def _handle_escape(buffer, board, state):
	binding = ESCAPE_BINDS.get(buffer)
	if binding:
		_execute_binding(binding, board, state, source="arrow")
	else:
		print("Unmapped escape sequence: {}".format(repr(buffer)))


def _write(text):
	stream = sys.stdout
	stream.write(text)
	flush = getattr(stream, "flush", None)
	if flush:
		flush()


def _show_prompt(current=""):
	_write("motor> " + current)


def _interactive_loop(board, state):
	buffer = ""
	_show_prompt()
	while True:
		ch = _read_char()
		if not ch:
			continue
		if ch == "\x03":  # Ctrl+C
			print("^C")
			return True
		if ch == "\x04":  # Ctrl+D
			print("^D")
			return False
		if ch == "\x1b":
			print("")
			seq = _capture_escape_sequence()
			_handle_escape(seq, board, state)
			buffer = ""
			_show_prompt()
			continue
		if ch in ("\r", "\n"):
			print("")
			cmd = buffer.strip()
			buffer = ""
			if cmd:
				if not _handle_line(cmd, board, state):
					return False
			_show_prompt()
			continue
		if ch in ("\x08", "\x7f"):
			if buffer:
				buffer = buffer[:-1]
				_write("\b \b")
			continue
		_write(ch)
		buffer += ch


def main():
	board = KitronikPicoRobotics()
	state = {
		"chunk_steps": DEFAULT_CHUNK,
		"speed_ms": DEFAULT_SPEED_MS,
		"hold": False,
		"steps_per_rev": DEFAULT_STEPS_PER_REV,
		"angles": {1: 0.0, 2: 0.0},
	}
	_print_banner(state)
	_report_angles(state)
	try:
		_interactive_loop(board, state)
	finally:
		_release_all(board)
		print("Controller exited; coils released")


if __name__ == "__main__":
	main()
