"""Standalone tester for the azimuth/elevation potentiometers.

Run this on the Raspberry Pi that hosts the MCP3008 + pot wiring:

    python3 pot_tester.py

It mirrors the main controller's filtering logic (Arduino-style running
average), prints raw MCP3008 readings, optionally shows calibrated degrees,
and warns when either potentiometer approaches its configured limits.
"""

from __future__ import annotations

import argparse
import time
from typing import Dict

from pot_reader import PotAngleReader

# Reuse the same calibration file as the main server.
CALIBRATION_PATH = "pot_calibration.json"
LOWER_THRESHOLD = 0.03  # warn when within 3% of the lower resistance span
UPPER_THRESHOLD = 0.97  # warn when within 3% of the upper resistance span
JITTER_SAMPLE_COUNT = 12
WARNING_STREAK = 3  # require N consecutive readings near a stop
DEFAULT_INTERVAL = 1.0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitor MCP3008-backed potentiometers")
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL,
        help="Seconds between reports (default: %(default)s)",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=JITTER_SAMPLE_COUNT,
        help="Extra unsmoothed samples per pot for jitter stats (default: %(default)s)",
    )
    parser.add_argument(
        "--show-deg",
        action="store_true",
        help="Also display calibrated degree values",
    )
    parser.add_argument(
        "--calibrate",
        choices=["azimuth", "elevation", "both"],
        help="Run manual calibration for the selected potentiometer(s)",
    )
    return parser.parse_args()


def _format_entry(entry: dict, stats: dict | None, *, show_degrees: bool) -> str:
    if "error" in entry:
        return f"{entry['name']}: ERROR ({entry['error']})"
    raw_value = entry.get("raw")
    if raw_value is None:
        return f"{entry['name']}: unavailable"
    ohms = entry.get("ohms")
    ohms_text = f"{ohms:.0f}Ω" if ohms is not None else "n/a"
    parts = [f"{entry['name']}: raw {raw_value:5d} ({ohms_text})"]
    if show_degrees and "degrees" in entry:
        parts.append(f"({entry['degrees']:.1f}°)")
    if stats:
        parts.append(f"avg {stats['avg']:.0f}Ω span {stats['span']:.0f}Ω")
    return " ".join(parts)


def _compute_stats(samples: list[float]) -> Dict[str, float] | None:
    if not samples:
        return None
    minimum = min(samples)
    maximum = max(samples)
    span = maximum - minimum
    avg = sum(samples) / len(samples)
    return {"min": minimum, "max": maximum, "span": span, "avg": avg}


def _init_warning_state(configs) -> Dict[str, Dict[str, object]]:
    return {
        cfg.name: {
            "lower_count": 0,
            "upper_count": 0,
            "lower_active": False,
            "upper_active": False,
        }
        for cfg in configs
    }


def _update_warnings(name: str, fraction: float, state: Dict[str, Dict[str, object]], warnings: list[str]) -> None:
    tracker = state.setdefault(
        name,
        {"lower_count": 0, "upper_count": 0, "lower_active": False, "upper_active": False},
    )

    # Lower stop hysteresis
    if fraction <= LOWER_THRESHOLD:
        tracker["lower_count"] = min(WARNING_STREAK, tracker["lower_count"] + 1)
        if tracker["lower_count"] >= WARNING_STREAK and not tracker["lower_active"]:
            warnings.append(f"{name} near LOWER stop")
            tracker["lower_active"] = True
    else:
        tracker["lower_count"] = 0
        tracker["lower_active"] = False

    # Upper stop hysteresis
    if fraction >= UPPER_THRESHOLD:
        tracker["upper_count"] = min(WARNING_STREAK, tracker["upper_count"] + 1)
        if tracker["upper_count"] >= WARNING_STREAK and not tracker["upper_active"]:
            warnings.append(f"{name} near UPPER stop")
            tracker["upper_active"] = True
    else:
        tracker["upper_count"] = 0
        tracker["upper_active"] = False


def _capture_bound(reader: PotAngleReader, name: str, *, mode: str) -> tuple[float, Dict[str, float] | None]:
    sample_count = max(32, JITTER_SAMPLE_COUNT * 2)
    raw_values = reader.sample_raw(name, sample_count, delay=reader.sample_delay)
    if not raw_values:
        raise RuntimeError(f"No samples captured for {name}")
    cfg = reader.get_config(name)
    if not cfg:
        raise RuntimeError(f"Missing config for {name}")
    ohm_values = [reader.raw_to_ohms_value(name, raw) for raw in raw_values]
    stats = _compute_stats(ohm_values)
    estimate = min(ohm_values) if mode == "lower" else max(ohm_values)
    return estimate, stats


def _prompt_override(prompt: str, default: float) -> float:
    while True:
        entry = input(f"  {prompt} [{default:.0f}Ω] -> ").strip()
        if not entry:
            return default
        try:
            return float(entry)
        except ValueError:
            print("    Invalid number; please try again")


def _interactive_calibration(reader: PotAngleReader, target: str) -> None:
    mapping = {
        "azimuth": ["azimuth"],
        "elevation": ["elevation"],
        "both": [cfg.name for cfg in reader.configs],
    }
    names = mapping.get(target, [])
    print("Manual calibration mode. Follow the prompts to capture raw limits.")
    for name in names:
        cfg = reader.get_config(name)
        if not cfg:
            print(f"Skipping {name}: missing config")
            continue
        print(f"\nChannel: {name}")
        input("  Rotate to the LOWER mechanical stop, then press Enter...")
        lower_estimate, lower_stats = _capture_bound(reader, name, mode="lower")
        if lower_stats:
            print(
                f"  Samples min/max/span (Ω): {lower_stats['min']:.0f}/"
                f"{lower_stats['max']:.0f}/{lower_stats['span']:.0f}"
            )
        lower = _prompt_override("Lower bound", lower_estimate)
        input("  Rotate to the UPPER mechanical stop, then press Enter...")
        upper_estimate, upper_stats = _capture_bound(reader, name, mode="upper")
        if upper_stats:
            print(
                f"  Samples min/max/span (Ω): {upper_stats['min']:.0f}/"
                f"{upper_stats['max']:.0f}/{upper_stats['span']:.0f}"
            )
        upper = _prompt_override("Upper bound", upper_estimate)
        if upper - lower < 50:
            print("  WARNING: span seems small (<50Ω); consider repeating this channel")
        reader.update_calibration(name, ohm_min=lower, ohm_max=upper, zero_deg=0.0)
        print(f"  Calibration updated for {name}")
    reader.save_calibrations()
    print(f"\nCalibration saved to {CALIBRATION_PATH}. Rerun without --calibrate to monitor readings.")


def main() -> None:
    args = _parse_args()
    reader = PotAngleReader(calibration_path=CALIBRATION_PATH)
    if not reader.available:
        raise SystemExit(f"Potentiometer reader unavailable: {reader.error}")
    if args.calibrate:
        _interactive_calibration(reader, args.calibrate)
        return
    print("Potentiometer tester running. Press Ctrl+C to stop.")
    configs = {cfg.name: cfg for cfg in reader.configs}
    warning_state = _init_warning_state(reader.configs)
    sample_count = max(1, int(args.samples))
    try:
        while True:
            readings = reader.read_angles()
            warnings: list[str] = []
            lines = []
            for entry in readings:
                cfg = configs.get(entry.get("name"))
                stats = None
                if cfg:
                    raw_samples = reader.sample_raw(cfg.name, sample_count)
                    ohm_samples = [reader.raw_to_ohms_value(cfg.name, raw) for raw in raw_samples]
                    stats = _compute_stats(ohm_samples)
                lines.append(_format_entry(entry, stats, show_degrees=args.show_deg))
                if cfg and "ohms" in entry:
                    span = max(cfg.ohm_max - cfg.ohm_min, 1e-6)
                    fraction = (entry["ohms"] - cfg.ohm_min) / span
                    _update_warnings(entry["name"], fraction, warning_state, warnings)
            print(" | ".join(lines))
            if warnings:
                print("WARNING: " + "; ".join(warnings))
            time.sleep(max(0.1, float(args.interval)))
    except KeyboardInterrupt:
        print("\nTester stopped by user")


if __name__ == "__main__":
    main()
