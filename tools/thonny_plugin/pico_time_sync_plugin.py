"""Thonny plug-in to auto-reply to TIME_SYNC prompts from the Pico.

Copy this file into your Thonny user plug-in directory:
    Windows: %APPDATA%\Thonny\plugins\
    macOS:   ~/Library/Application Support/Thonny/plugins/
    Linux:   ~/.thonny/plugins/

After placing it there, restart Thonny. Whenever the shell receives
"TIME_SYNC" from the MicroPython program, the plug-in injects the
current host UNIX timestamp automatically.
"""

import subprocess
import sys
import time
from typing import Optional, TextIO

from thonny import get_runner, get_shell, get_workbench

SYNC_TOKEN = "TIME_SYNC"
_COOLDOWN_MS = 1500
_BUFFER_LIMIT = 256
USE_LOCAL_TIME = True

_last_send_ms: Optional[int] = None
_recent_buffer: str = ""
_pending = False
_logger_proc: Optional[subprocess.Popen] = None
_logger_stream: Optional[TextIO] = None
_logger_failed_once = False


def _ensure_logger() -> None:
    global _logger_proc, _logger_stream, _logger_failed_once
    if _logger_proc is not None and _logger_proc.poll() is None:
        return

    _logger_proc = None
    _logger_stream = None

    log_code = (
        "import sys,time\n"
        "print('Pico Time Sync Plugin Logger', flush=True)\n"
        "print('Started at %s' % time.strftime('%Y-%m-%d %H:%M:%S'), flush=True)\n"
        "print('-'*60, flush=True)\n"
        "for line in sys.stdin:\n"
        "    sys.stdout.write(line)\n"
        "    sys.stdout.flush()\n"
    )

    popen_kwargs = {
        "stdin": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
    }

    creation_flag = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    if creation_flag:
        popen_kwargs["creationflags"] = creation_flag

    try:
        _logger_proc = subprocess.Popen(
            [sys.executable, "-u", "-c", log_code],
            **popen_kwargs,
        )
        _logger_stream = _logger_proc.stdin
    except Exception as exc:
        if not _logger_failed_once:
            print("[pico_time_sync] logger launch failed:", repr(exc))
            _logger_failed_once = True
        _logger_proc = None
        _logger_stream = None
        raise


def _log(message: str) -> None:
    global _logger_failed_once, _logger_stream
    try:
        _ensure_logger()
    except Exception:
        # Fall back to printing in Thonny shell if logger window can't be created
        _logger_stream = None

    if _logger_stream is None:
        print(f"[pico_time_sync] {message}")
        return

    timestamp = time.strftime("%H:%M:%S")
    try:
        _logger_stream.write(f"[{timestamp}] {message}\n")
        _logger_stream.flush()
    except Exception as exc:
        if not _logger_failed_once:
            print("[pico_time_sync] logger write failed:", repr(exc))
            _logger_failed_once = True


def _now_ms() -> int:
    return int(time.time() * 1000)


def _current_timestamp() -> int:
    base = time.time()
    if not USE_LOCAL_TIME:
        return int(base)

    offset = time.timezone
    if time.daylight and time.localtime().tm_isdst:
        offset = time.altzone
    # time.timezone/altzone are seconds west of UTC; subtract to convert to local
    adjusted = base - offset
    _log(
        "computed local timestamp "
        f"(base={int(base)}, offset={offset}, result={int(adjusted)})"
    )
    return int(adjusted)


def _schedule_send(delay_ms: int = 150) -> None:
    global _pending, _recent_buffer
    _log(f"schedule_send requested (pending={_pending})")
    if _pending:
        _log("skipping scheduling because a send is already pending")
        return
    workbench = get_workbench()
    if workbench is None:
        _log("workbench unavailable; cannot schedule send")
        return

    def _do_send():
        global _last_send_ms, _pending
        _pending = False
        runner = get_runner()
        if runner is None:
            _log("runner unavailable when trying to send timestamp")
            return
        now = _now_ms()
        if _last_send_ms is not None and now - _last_send_ms < _COOLDOWN_MS:
            _log(
                f"cooldown active ({now - _last_send_ms} ms since last send); skipping"
            )
            return
        stamp_value = _current_timestamp()
        stamp = str(stamp_value) + "\n"
        try:
            runner.send_program_input(stamp)
            _log(f"timestamp sent via runner: {stamp.strip()}")
        except AssertionError:
            shell = get_shell()
            if shell is not None and hasattr(shell, "submit_input"):
                shell.submit_input(stamp)
                _log(f"timestamp sent via shell fallback: {stamp.strip()}")
            else:
                _log("failed to send timestamp; neither runner nor shell accepted input")
        _last_send_ms = now
        _log(f"timestamp send complete; last_send_ms updated to {now}")

    _pending = True
    _recent_buffer = ""
    _log(f"scheduling timestamp send in {delay_ms} ms")
    workbench.after(delay_ms, _do_send)


def _handle_program_output(event) -> None:
    global _recent_buffer
    data = getattr(event, "data", "")
    stream_name = getattr(event, "stream_name", "stdout")
    if stream_name != "stdout":
        return
    if not data:
        return
    snippet = data.replace("\r", "\\r").replace("\n", "\\n")
    if len(snippet) > 120:
        snippet = snippet[:117] + "..."
    _log(f"ProgramOutput received ({len(data)} chars): '{snippet}'")
    _recent_buffer = (_recent_buffer + data)[-_BUFFER_LIMIT:]
    if SYNC_TOKEN in _recent_buffer:
        _log(
            f"SYNC_TOKEN detected in buffer (length {_BUFFER_LIMIT}); initiating schedule"
        )
        _schedule_send()


def load_plugin() -> None:
    workbench = get_workbench()
    if workbench is None:
        return
    try:
        _ensure_logger()
    except Exception:
        pass
    _log("load_plugin invoked; binding ProgramOutput handler")
    workbench.bind("ProgramOutput", _handle_program_output, True)
