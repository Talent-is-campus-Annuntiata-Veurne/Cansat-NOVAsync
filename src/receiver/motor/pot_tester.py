"""Standalone tester for the azimuth/elevation potentiometers.

Run this on the Raspberry Pi that hosts the MCP3008 + pot wiring:

    python3 pot_tester.py

It prints the raw MCP3008 readings once per second and warns when either
potentiometer is approaching the configured raw_min/raw_max limits.
"""

from __future__ import annotations

import time

from pot_reader import PotAngleReader

# Reuse the same calibration file as the main server.
CALIBRATION_PATH = "pot_calibration.json"
LOWER_THRESHOLD = 0.03  # warn when within 3% of the lower raw span
UPPER_THRESHOLD = 0.97  # warn when within 3% of the upper span


def _format_entry(entry: dict) -> str:
    if "error" in entry:
        return f"{entry['name']}: ERROR ({entry['error']})"
    raw_value = entry.get("raw")
    if raw_value is None:
        return f"{entry['name']}: unavailable"
    return f"{entry['name']}: raw {raw_value}"


def main() -> None:
    reader = PotAngleReader(calibration_path=CALIBRATION_PATH)
    if not reader.available:
        raise SystemExit(f"Potentiometer reader unavailable: {reader.error}")
    print("Potentiometer tester running. Press Ctrl+C to stop.")
    configs = {cfg.name: cfg for cfg in reader.configs}
    try:
        while True:
            readings = reader.read_angles()
            warnings: list[str] = []
            lines = []
            for entry in readings:
                cfg = configs.get(entry.get("name"))
                lines.append(_format_entry(entry))
                if cfg and "raw" in entry:
                    span = max(1, cfg.raw_max - cfg.raw_min)
                    fraction = (entry["raw"] - cfg.raw_min) / span
                    if fraction <= LOWER_THRESHOLD:
                        warnings.append(f"{entry['name']} near LOWER stop")
                    elif fraction >= UPPER_THRESHOLD:
                        warnings.append(f"{entry['name']} near UPPER stop")
            print(" | ".join(lines))
            if warnings:
                print("WARNING: " + "; ".join(warnings))
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nTester stopped by user")


if __name__ == "__main__":
    main()
