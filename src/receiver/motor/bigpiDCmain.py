"""Interactive DC motor test utility for the Adafruit DC & Stepper Motor HAT."""

from __future__ import annotations

import sys
import termios
import tty
from contextlib import contextmanager

from dc_control import DCControlState, MotorHatController, format_banner

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


@contextmanager
def raw_terminal():
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def _print_banner(state: DCControlState) -> None:
    print(format_banner(state))
    doc = globals().get("__doc__") or ""
    print(doc)


def _report(state: DCControlState) -> None:
    print(state.status_line())


def _execute_binding(binding, state: DCControlState, source: str) -> None:
    motor, direction = binding
    if motor > state.motor_count:
        return
    ok, msg = state.nudge(motor, direction, source=source)
    print(msg)
    if ok:
        _report(state)


def _handle_command(line: str, state: DCControlState) -> bool:
    ok, msg = state.handle_command(line)
    if msg and msg != "quit":
        print(msg)
        if ok:
            _report(state)
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


def _handle_escape(buffer: str, state: DCControlState) -> None:
    binding = ESCAPE_BINDS.get(buffer)
    if binding:
        _execute_binding(binding, state, source="arrow")
    else:
        print(f"Unmapped escape sequence: {buffer!r}")


def _write(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


def _show_prompt(current: str = "") -> None:
    _write("motor> " + current)


def _interactive_loop(state: DCControlState) -> bool:
    buffer = ""
    _show_prompt()
    while True:
        ch = _read_char()
        if not ch:
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
        _write(ch)
        buffer += ch


def main():
    controller = MotorHatController()
    state = DCControlState(controller=controller, motor_count=controller.motor_count)
    _print_banner(state)
    _report(state)
    try:
        with raw_terminal():
            _interactive_loop(state)
    finally:
        state.release_motor()
        print("Controller exited; motors released")


if __name__ == "__main__":
    main()
