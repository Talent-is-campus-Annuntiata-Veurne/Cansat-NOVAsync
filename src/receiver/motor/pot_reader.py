"""ADC-backed helper for reading multi-turn potentiometers as angles."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Sequence

try:
    import board
    import busio
    import digitalio
    import adafruit_mcp3xxx.mcp3008 as MCP
    from adafruit_mcp3xxx.analog_in import AnalogIn
except Exception:  # pragma: no cover - hardware import guard
    board = None  # type: ignore
    busio = None  # type: ignore
    digitalio = None  # type: ignore
    MCP = None  # type: ignore
    AnalogIn = None  # type: ignore


@dataclass
class PotChannelConfig:
    """Calibration info for a single potentiometer channel."""

    name: str
    channel: int
    span_degrees: float = 360.0
    zero_deg: float = 0.0
    raw_min: int = 200  # legacy raw fallback
    raw_max: int = 65500
    total_ohms: float = 10000.0  # Bourns 3590 default resistance
    ohm_min: float = 0.0
    ohm_max: float = 10000.0
    invert: bool = False

    def apply_overrides(self, overrides: Dict[str, object]) -> None:
        for field in (
            "span_degrees",
            "zero_deg",
            "raw_min",
            "raw_max",
            "total_ohms",
            "ohm_min",
            "ohm_max",
            "invert",
        ):
            if field in overrides:
                setattr(self, field, overrides[field])

    def to_dict(self) -> Dict[str, object]:
        data = asdict(self)
        data.pop("name", None)
        data.pop("channel", None)
        return data


DEFAULT_POT_CHANNELS: Sequence[PotChannelConfig] = (
    PotChannelConfig(name="azimuth", channel=0, span_degrees=540.0, zero_deg=0.0),
    PotChannelConfig(name="elevation", channel=1, span_degrees=180.0, zero_deg=0.0),
)


class PotAngleReader:
    """Read MCP3008 channels and convert them into angles and ohms."""

    def __init__(
        self,
        configs: Sequence[PotChannelConfig] | None = None,
        cs_pin=None,
        poll_interval: float = 0.25,
        calibration_path: str | Path | None = None,
        smooth_iterations: int = 12,
        sample_delay: float = 0.005,
        smooth_alpha: float = 0.4,
    ) -> None:
        self.configs: Sequence[PotChannelConfig] = configs or DEFAULT_POT_CHANNELS
        self.poll_interval = poll_interval
        self.available = False
        self.error: str | None = None
        self._channels: Dict[str, AnalogIn] = {}
        self._config_map = {cfg.name: cfg for cfg in self.configs}
        self.calibration_path = Path(calibration_path) if calibration_path else None
        self.smooth_iterations = max(0, int(smooth_iterations))
        self.sample_delay = max(0.0, float(sample_delay))
        self.smooth_alpha = min(max(float(smooth_alpha), 0.0), 1.0)

        if self.calibration_path and self.calibration_path.exists():
            self._load_calibration_file()

        if MCP is None or board is None or busio is None or digitalio is None:
            self.error = "MCP3008/Blinka libraries are not installed"
            return

        try:
            self.spi = busio.SPI(board.SCK, MISO=board.MISO, MOSI=board.MOSI)
            if cs_pin is None:
                cs_pin = board.CE1
            elif isinstance(cs_pin, str):
                cs_pin = getattr(board, cs_pin)
            self.cs = digitalio.DigitalInOut(cs_pin)
            self.mcp = MCP.MCP3008(self.spi, self.cs)
            for cfg in self.configs:
                channel_attr = getattr(MCP, f"P{cfg.channel}")
                self._channels[cfg.name] = AnalogIn(self.mcp, channel_attr)
            self.available = True
        except Exception as exc:  # pragma: no cover - hardware path
            self.error = str(exc)
            self.available = False

    def read_angles(self) -> List[Dict[str, float]]:
        if not self.available:
            return []
        readings: List[Dict[str, float]] = []
        for cfg in self.configs:
            channel = self._channels.get(cfg.name)
            if channel is None:
                continue
            raw_value = self._filtered_value(channel)
            voltage = channel.voltage
            ohms = self._raw_to_ohms(raw_value, cfg)
            degrees = self._raw_to_degrees(raw_value, cfg, ohms)
            readings.append(
                {
                    "name": cfg.name,
                    "degrees": degrees,
                    "raw": raw_value,
                    "ohms": ohms,
                    "voltage": voltage,
                }
            )
        return readings

    @staticmethod
    def _raw_to_degrees(raw: int, cfg: PotChannelConfig, ohms: float | None = None) -> float:
        if ohms is None:
            ohms = PotAngleReader._raw_to_ohms(raw, cfg)
        fraction = PotAngleReader._ohms_to_fraction(ohms, cfg)
        if cfg.invert:
            fraction = 1.0 - fraction
        return cfg.zero_deg + fraction * cfg.span_degrees

    @staticmethod
    def _raw_to_ohms(raw: int, cfg: PotChannelConfig) -> float:
        clamped = max(0, min(65535, raw))
        return (clamped / 65535.0) * cfg.total_ohms

    @staticmethod
    def _ohms_to_fraction(ohms: float, cfg: PotChannelConfig) -> float:
        span = max(cfg.ohm_max - cfg.ohm_min, 1e-6)
        fraction = (ohms - cfg.ohm_min) / span
        return max(0.0, min(1.0, fraction))

    @staticmethod
    def _ohms_to_raw(ohms: float, total_ohms: float) -> int:
        total = max(total_ohms, 1e-6)
        fraction = max(0.0, min(1.0, ohms / total))
        return int(round(fraction * 65535))

    # Calibration helpers -------------------------------------------------
    def get_config(self, name: str) -> PotChannelConfig | None:
        return self._config_map.get(name)

    def update_calibration(
        self,
        name: str,
        *,
        raw_min: int | None = None,
        raw_max: int | None = None,
        ohm_min: float | None = None,
        ohm_max: float | None = None,
        total_ohms: float | None = None,
        zero_deg: float | None = None,
        span_degrees: float | None = None,
        invert: bool | None = None,
    ) -> None:
        cfg = self._config_map.get(name)
        if not cfg:
            raise ValueError(f"Unknown potentiometer '{name}'")
        overrides: Dict[str, object] = {}
        pending_total = float(total_ohms) if total_ohms is not None else cfg.total_ohms
        if total_ohms is not None:
            overrides["total_ohms"] = pending_total
        if raw_min is not None:
            overrides["raw_min"] = int(raw_min)
        if raw_max is not None:
            overrides["raw_max"] = int(raw_max)
        if ohm_min is not None:
            overrides["ohm_min"] = float(ohm_min)
            overrides.setdefault("raw_min", PotAngleReader._ohms_to_raw(float(ohm_min), pending_total))
        if ohm_max is not None:
            overrides["ohm_max"] = float(ohm_max)
            overrides.setdefault("raw_max", PotAngleReader._ohms_to_raw(float(ohm_max), pending_total))
        if zero_deg is not None:
            overrides["zero_deg"] = float(zero_deg)
        if span_degrees is not None:
            overrides["span_degrees"] = float(span_degrees)
        if invert is not None:
            overrides["invert"] = bool(invert)
        cfg.apply_overrides(overrides)

    def save_calibrations(self) -> None:
        if not self.calibration_path:
            return
        payload = {
            cfg.name: cfg.to_dict()
            for cfg in self.configs
        }
        self.calibration_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _load_calibration_file(self) -> None:
        try:
            data = json.loads(self.calibration_path.read_text(encoding="utf-8"))
        except Exception:
            return
        for name, overrides in data.items():
            cfg = self._config_map.get(name)
            if cfg and isinstance(overrides, dict):
                cfg.apply_overrides(overrides)

    def calibration_snapshot(self) -> Dict[str, Dict[str, object]]:
        return {cfg.name: cfg.to_dict() for cfg in self.configs}

    def read_raw(self, name: str) -> int:
        if not self.available:
            raise RuntimeError("Pot reader unavailable")
        channel = self._channels.get(name)
        if channel is None:
            raise ValueError(f"Unknown potentiometer '{name}'")
        return self._filtered_value(channel)

    def raw_to_ohms_value(self, name: str, raw: int) -> float:
        cfg = self.get_config(name)
        if not cfg:
            raise ValueError(f"Unknown potentiometer '{name}'")
        return self._raw_to_ohms(raw, cfg)

    def sample_raw(self, name: str, count: int = 8, delay: float | None = None) -> List[int]:
        if not self.available:
            return []
        channel = self._channels.get(name)
        if channel is None:
            raise ValueError(f"Unknown potentiometer '{name}'")
        delay = self.sample_delay if delay is None else max(0.0, float(delay))
        total = max(1, int(count))
        samples: List[int] = []
        for _ in range(total):
            samples.append(channel.value)
            if delay:
                time.sleep(delay)
        return samples

    def _filtered_value(self, channel: AnalogIn) -> int:
        if self.smooth_iterations <= 0 or self.smooth_alpha <= 0.0:
            return channel.value
        value = float(channel.value)
        alpha = self.smooth_alpha
        delay = self.sample_delay
        for _ in range(self.smooth_iterations):
            if delay:
                time.sleep(delay)
            sample = channel.value
            value = value * (1.0 - alpha) + sample * alpha
        return int(round(value))


__all__ = ["PotAngleReader", "PotChannelConfig", "DEFAULT_POT_CHANNELS"]
