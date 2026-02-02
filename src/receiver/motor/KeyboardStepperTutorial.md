# Keyboard Control Tutorial for the Kitronik Pico Robotics Board

This guide explains how to jog two CNC3 (1.8°, 200-step) steppers from a PC keyboard using `motorcontrols.py` and the Kitronik Pico Robotics Board.

## 1. Hardware Recap

- **Steppers:** 42 mm CNC3 units, 1.33 A/phase, 2.8 V nominal, 200 steps/rev.
- **Coil colours:**
  - BLK = A, GRN = C, RED = B, BLU = D (per datasheet legend).
- **Wiring to the robotics board:**
  - **Stepper 1** → Motors 1 & 2: `M1+ = red`, `M1- = blue`, `M2+ = black`, `M2- = green`.
  - **Stepper 2** → Motors 3 & 4: `M3+ = red`, `M3- = blue`, `M4+ = black`, `M4- = green`.
- Power the Kitronik board with an adequate supply (able to source ≥1.5 A per motor) *before* connecting USB to the Pico.

## 2. Files to Upload

Copy these two files from the repo onto the Pico (root folder):

1. `src/receiver/motor/PicoRobotics.py` – Kitronik driver.
2. `src/receiver/motor/motorcontrols.py` – keyboard controller.

Use Thonny's **Files** pane (right-hand drop-down → Raspberry Pi Pico) and right-click ➜ *Upload*.

## 3. Connect with `mpremote` (recommended)

Thonny’s regular shell buffers keys, so use [MicroPython’s mpremote](https://docs.micropython.org/en/latest/reference/mpremote.html) for the live REPL and keep Thonny for editing/uploading files.

1. Install mpremote on your PC:
  ```bash
  python -m pip install mpremote
  ```
2. Upload `motorcontrols.py` (or rename it to `main.py`) plus `PicoRobotics.py` via Thonny’s **Files** pane, then stop Thonny’s backend (`Ctrl+F2`) so it releases the COM port.
3. Open a terminal/PowerShell window and connect to the Pico (replace `COM5` with your port):
  ```bash
  mpremote connect COM5 repl
  ```
  On macOS/Linux use `/dev/ttyACM0`, `/dev/ttyUSB0`, etc.
4. When the MicroPython `>>>` prompt appears, soft reboot if desired (`Ctrl+D`) and start the controller:
  ```python
  import motorcontrols
  ```
  If you saved it as `main.py`, it will autostart after each reboot.

> Prefer to stay inside Thonny? Enable `Tools → Options → Interpreter → Open REPL in a terminal window (beta)` to get the same raw console behaviour. The steps below are identical once you see the `>>>` prompt.

## 4. Run the Controller

With `mpremote repl` (or Thonny’s terminal) connected, the script prints its banner and shows the `motor>` prompt. From there the arrow keys and commands described below work immediately. Rename `motorcontrols.py` to `main.py` if you want the controller to launch automatically on boot.

## 5. Controls

### Arrow Keys (no Enter required)

| Key  | Action                                   |
| ---- | ---------------------------------------- |
| ↑    | Stepper 1 forward (red/blue coil first)  |
| ↓    | Stepper 1 reverse                        |
| →    | Stepper 2 forward                        |
| ←    | Stepper 2 reverse                        |

Each press executes the current "chunk" size (default 25 steps). Hold the key or use the Shift modifier on the host to accelerate key-repeat.

### Single-key hotkeys (press key then Enter)

`w`/`s` mirror the arrows for Stepper 1; `i`/`k` mirror them for Stepper 2.

### Console commands

Type the command at `motor>` and press Enter.

| Command | Meaning |
| ------- | ------- |
| `step <motor> <f|r> <steps> [speed_ms]` | Execute an exact number of full steps (motor 1 or 2). |
| `angle <motor> <f|r> <degrees> [speed_ms]` | Move by degrees using 200 steps/rev (can be changed with `stepsperrev`). |
| `speed <ms>` | Set default delay between steps (5–2000 ms, lower is faster). |
| `chunk <steps>` | Change the step count for arrow/hotkey nudges. |
| `hold on|off` | Keep coils energised after motion when `on`. |
| `stepsperrev <value>` | Override motor resolution (default 200). |
| `release` | Immediately de-energise all coils. |
| `zero` | Reset both virtual angle counters to 0°. |
| `?` | Show the help banner again. |
| `q` / `Ctrl+C` / `Ctrl+D` | Quit (coils release automatically). |

### Recommended speed

The CNC3 datasheet’s pull‑out torque curve peaks around 1000 pulses/s, but real hardware plus the Kitronik driver are happiest near **35 ms per step** (default). Use `speed 20`, `speed 10`, etc. to go faster only if your motors stay smooth; increase the value (e.g. `speed 60`) whenever you see stalling.

## 6. Troubleshooting Checklist

- **No motion:** Confirm board power LED is on and supply voltage matches the steppers. `motorcontrols` attempts to talk I²C address 0x6C; if the board is unpowered you will see `OSError: 5`.
- **Wrong direction:** Double-check colour mapping per Section 1. Swapping a coil pair flips direction.
- **Jerky or stalling:** Increase `speed` (e.g., `speed 60`) so the motor has more time per step, or reduce `chunk` for shorter jogs.
- **Arrow keys insert characters instead of jogging:** You are not in the terminal REPL—open the *Terminal* tab or use an external console that forwards escape sequences unchanged.

With everything uploaded and the terminal properly configured, the Pico becomes a responsive two-axis jog controller for the Kitronik board. The script continuously tracks virtual angles (starting at 0° on boot); re-zero with the `zero` command whenever you align the hardware reference mark. Enjoy testing! 😄

## 7. Optional: Web Interface from the Host PC

If you would rather click buttons (or hold the browser’s arrow keys) instead of keeping a raw REPL open, start the tiny Flask bridge in [src/receiver/motor/web_control_server.py](src/receiver/motor/web_control_server.py). It serves [web_ui.html](src/receiver/motor/web_ui.html) locally and forwards each browser event to the Pico over USB serial.

1. Install the host dependencies:
  ```bash
  pip install flask pyserial
  ```
2. Ensure no other program is connected to the Pico’s serial port, then run:
  ```bash
  python web_control_server.py --serial COM5 --http-port 8765
  ```
  Swap `COM5` for the correct port (`/dev/ttyACM0`, etc.).
3. Open `http://localhost:8765` in a browser. You’ll see arrow buttons, a “release” button, a “zero” button, live degree readouts for both motors, **target angle inputs** (type an absolute degree for each stepper and press “Go to angle”), and a custom command input. Keep the tab focused to drive the steppers with the physical arrow keys or WASD.
4. Start `main.py` on the Pico (`import main`) before launching the server. When you’re done, hit `Ctrl+C` in the server terminal to close the connection and return to mpremote/Thonny control.

> The bridge simply injects the same escape sequences/text that the REPL expects, so everything you can type manually (e.g., `speed 35`, `release`, `step 1 f 20`) also works through the web page’s custom command box.
