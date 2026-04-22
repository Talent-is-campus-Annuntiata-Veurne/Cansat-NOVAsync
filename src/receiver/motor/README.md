# NOVAsync Ground Station Motor Controller (Pico + Web GUI)

This guide is the full, practical setup for getting the ground station working from scratch.

It covers:
- What hardware to wire
- Which files to upload to the Pico
- How to start the web GUI
- Every command supported by the Pico motor runtime
- Potentiometer calibration for geared azimuth/elevation systems
- Auto tracking controls and troubleshooting

If you follow this document top to bottom, you should end with a working controller.

## 1) System Overview

The motor controller runs on a Raspberry Pi Pico with a Kitronik Pico Robotics board.
A host PC runs a small Flask bridge that serves the GUI and forwards commands over USB serial to the Pico.

Data flow:
1. Pico runs `main.py` in this folder
2. Pico prints motor/angle/telemetry status lines
3. Host `web_control_server.py` reads those lines and exposes `/status`
4. Browser `web_ui_dc.html` polls `/status` and sends `/command`

## 2) Hardware Checklist

Required:
- Raspberry Pi Pico (or Pico W)
- Kitronik Pico Robotics board
- Motor power supply (sized for your motors)
- USB cable to host PC
- 2 potentiometers for feedback:
  - Azimuth: GP26 (ADC0)
  - Elevation: GP27 (ADC1)
- Radio module as used in this project (RFM69 wiring already in project code)

Important:
- Use a common GND between Pico board and potentiometers.
- Verify motor supply polarity before powering.
- Keep hands clear: auto mode pulses motors in short bursts.

## 3) Files To Put On Pico

At minimum upload these to the Pico filesystem:
- `main.py` (this folder version)
- `PicoRobotics.py`
- `data_module.py` (from `src/receiver/data_module.py`)
- Any dependencies used by your radio stack (for example `rfm69.py`)

Recommended:
- Keep a backup copy of your working `main.py` before major edits.

## 4) Host PC Setup (Web GUI)

Install dependencies once:

```bash
pip install flask pyserial
```

Start the bridge (replace COM port):

```bash
python web_control_server.py --serial COM12 --http-port 8765
```

Open browser:

```text
http://127.0.0.1:8765
```

If you need LAN access from another device:

```bash
python web_control_server.py --serial COM12 --host 0.0.0.0 --http-port 8765
```

## 5) Pico Runtime Startup

Use Thonny or mpremote to run Pico `main.py`.

With mpremote:

```bash
mpremote connect COM12 repl
```

Then in REPL:

```python
import main
```

If the file is named `main.py` on the Pico root, it can auto-start at boot.

## 6) Control Modes

Manual control (always available):
- Keyboard hotkeys from the Pico console
- Web buttons/sliders via bridge

Auto tracking (RSSI mode):
- Enable with `auto on`
- Disable with `auto off`
- Auto pulses azimuth/elevation motors and follows RSSI improvement

Safety rule:
- Manual commands disable auto mode immediately.

## 7) Full Pico Command Reference

Type commands at the Pico `motor>` prompt.

General:
- `status` -> print current motor throttles
- `q`, `quit`, `exit` -> quit controller

Throttle and motion:
- `increment <amount>`
- `set <motor> <throttle>`
- `inc <motor> [amount]`
- `dec <motor> [amount]`
- `stop <motor>`
- `stopall`
- `release`
- `release all`
- `release <motor>`

Motor indices:
- Runtime supports motors 1..4

Auto tracking:
- `auto on`
- `auto off`
- `auto status`
- `auto base <lat> <lon> [alt_m]`
- `auto clearbase`

Potentiometer calibration:
- `pot zero <name>`
- `pot min <name>`
- `pot max <name>`
- `pot align <name> <degrees>`
- `pot ratio <name> <ratio>`

Valid names:
- `azimuth`
- `elevation`

## 8) Keyboard Shortcuts (Pico Console)

Direct keys:
- `w` / `s` -> motor 1 +/-
- `i` / `k` -> motor 2 +/-

Arrow escape bindings (console dependent):
- Up/Down -> motor 3 +/-
- Right/Left -> motor 4 +/-
  
Note:
- Use mpremote or a terminal that passes escape sequences correctly.

## 9) Web GUI Controls

Main actions:
- Per-motor slider for throttle magnitude
- Forward/Reverse buttons
- Stop/Release buttons
- Global Stop/Release
- Auto-track toggle (through `/command` -> `auto_track`)

Live panels:
- Angles (`ANGLES,...`)
- Raw pot (`POTRAW,...`)
- Telemetry (`TEL,...`)
- Auto status (`AUTO,...`)

If values appear in server logs but not UI:
- Hard refresh browser (Ctrl+F5)
- Restart web bridge
- Check serial port is not opened by another app

## 10) Gear-Aware Pot Calibration (Important)

Because your pot gears are not 1:1 with antenna gears, set ratio per axis.

Formula used by runtime:

```text
mechanical_angle = pot_angle * gear_ratio
```

So use:

```text
gear_ratio = (pot gear teeth) / (driven gear teeth)
```

Your shared values:
- Azimuth: pot 7, big gear 45 -> ratio `0.15556`
- Elevation: pot 19, big gear 25 -> ratio `0.76`
Commands:

```text
pot ratio azimuth 0.15556
pot ratio elevation 0.76
```

If direction is reversed, use negative ratio.

Example:

```text
pot ratio azimuth -0.15556
```

Then align known references:

```text
pot align azimuth 0
pot align elevation 0
```

Suggested sequence:
1. `pot min azimuth` at one hard limit
2. `pot max azimuth` at opposite limit
3. `pot ratio azimuth <ratio>`
4. `pot align azimuth <known angle>`
5. Repeat for elevation

## 11) Auto Tracking Notes

Auto mode behavior:
- Runs in the background loop
- Uses GPS as coarse guidance (when base coordinates are set)
- Moves slowly for stability: 0.5 s pulse then 1.0 s settle
- Accepts coarse angle tolerance (not sub-degree)
- Uses smoothed RSSI for minor trim corrections

GPS setup for auto:
1. Set your ground station position:
  - `auto base <lat> <lon> [alt_m]`
2. Enable auto:
  - `auto on`
3. Check status:
  - `auto status`

If you move the ground station, clear/re-set base:
- `auto clearbase`
- `auto base <lat> <lon> [alt_m]`

Tune constants in `main.py` if needed:
- `AUTO_TRACK_PERIOD_MS`
- `AUTO_TRACK_PULSE_MS`
- `AUTO_TRACK_SETTLE_MS`
- `AUTO_TRACK_STEP`
- `AUTO_TRACK_MIN_IMPROVEMENT_DB`

If oscillation is too strong:
- Lower `AUTO_TRACK_STEP`
- Increase `AUTO_TRACK_SETTLE_MS`
- Increase `AUTO_TRACK_MIN_IMPROVEMENT_DB`

## 12) Common Problems and Fixes

1. GUI opens but buttons do nothing
- Ensure bridge has the right COM port
- Ensure Pico runtime is actually running
- Ensure preview-only mode in UI is OFF if you want live control

2. Telemetry not updating
- Restart bridge and browser
- Check serial output contains `TEL,` lines
- Confirm no second app is holding COM port

3. Auto command crashes or stops
- Check for `[AUTO] step error:` in Pico console
- Disable auto: `auto off`
- Reduce step size and retry

4. Angles look wrong after gearing changes
- Re-run ratio and align commands
- Re-check sign (positive vs negative ratio)
- Re-check min/max captures

## 13) Recommended First Boot Procedure

1. Upload required files to Pico
2. Run Pico `main.py`
3. Start `web_control_server.py`
4. Open GUI and verify:
   - Angles update
   - Raw pot values update
5. Test manual motor movement at low throttle
6. Set pot ratios and align both axes
7. Test auto mode briefly (`auto on`, then `auto off`)
8. Increase aggressiveness only after stable behavior

## 14) Operational Safety

- Keep motor throttle low during calibration.
- Keep physical end-stop margin.
- Prefer short auto test bursts.
- Always keep manual stop path available:
  - `stopall`
  - `auto off`
  - Cut motor supply if needed
