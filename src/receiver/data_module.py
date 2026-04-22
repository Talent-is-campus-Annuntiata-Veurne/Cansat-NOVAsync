"""Import-only telemetry receiver module for the Pico base station.

This module is a reusable copy of ``src/receiver/main.py`` logic, refactored so
it can be imported by another runtime (for example ``motor/main.py``).
It does not run anything on import.
"""

from machine import SPI, Pin
from rfm69 import RFM69

# Radio settings (MUST match emitter)
FREQ = 433.9
ENCRYPTION_KEY = bytes("CANSAT_2025-2026", "utf-8")
NODE_ID = 125


def _build_radio():
    spi = SPI(
        0,
        miso=Pin(4),
        mosi=Pin(7),
        sck=Pin(6),
        polarity=0,
        phase=0,
        firstbit=SPI.MSB,
    )
    nss = Pin(5, Pin.OUT, value=True)
    rst = Pin(3, Pin.OUT, value=False)

    rfm = RFM69(spi=spi, nss=nss, reset=rst)
    rfm.frequency_mhz = FREQ
    rfm.encryption_key = ENCRYPTION_KEY
    rfm.node = NODE_ID
    return rfm


def _parse_packet_text(packet_text):
    parts = packet_text.split(";")
    if not parts:
        return None, {}
    prefix = parts[0]
    data = {}
    try:
        for part in parts[1:]:
            if "=" in part:
                key, value = part.split("=", 1)
                data[key] = value
    except Exception:
        data = {}
    return prefix, data


def _format_telemetry_line(entry):
    return "TEL,c={c},time={time},temp={temp},pressure={pressure},baro={baro},humidity={humidity},lat={lat},lon={lon},alt={alt},speed={speed},fix={fix},gps_time={gps_time},rx_rssi={rx_rssi}".format(
        c=entry.get("counter", "nan"),
        time=entry.get("time", "nan"),
        temp=entry.get("temp", "nan"),
        pressure=entry.get("pressure", "nan"),
        baro=entry.get("baro", "nan"),
        humidity=entry.get("humidity", "nan"),
        lat=entry.get("lat", "nan"),
        lon=entry.get("lon", "nan"),
        alt=entry.get("alt", "nan"),
        speed=entry.get("speed", "nan"),
        fix=entry.get("fix", "0"),
        gps_time=entry.get("gps_time", "nan"),
        rx_rssi=entry.get("rx_rssi", "nan"),
    )


class ReceiverDataModule:
    def __init__(self, emit_line=None, on_rssi_sample=None):
        self._emit_line = emit_line
        self._on_rssi_sample = on_rssi_sample
        self._rfm = _build_radio()
        self._packet_cache = {}
        self._last_emitted_counter_num = None
        self._rssi_ema = None
        self._rssi_alpha = 0.35
        self._last_rssi_sample = None

    def _emit(self, text):
        if callable(self._emit_line):
            self._emit_line(text)
        else:
            print(text)

    def _get_entry(self, counter):
        entry = self._packet_cache.get(counter)
        if entry is None:
            entry = {"counter": counter}
            try:
                entry["counter_num"] = int(counter)
            except Exception:
                entry["counter_num"] = None
            self._packet_cache[counter] = entry
        return entry

    def _flush_entry(self, counter):
        entry = self._packet_cache.pop(counter, None)
        if not entry:
            return
        counter_num = entry.get("counter_num")
        if (
            isinstance(counter_num, int)
            and isinstance(self._last_emitted_counter_num, int)
            and counter_num <= self._last_emitted_counter_num
        ):
            return
        self._emit(_format_telemetry_line(entry))
        if isinstance(counter_num, int):
            self._last_emitted_counter_num = counter_num

    def _is_stale_counter(self, counter):
        try:
            counter_num = int(counter)
        except Exception:
            return False
        if not isinstance(self._last_emitted_counter_num, int):
            return False
        return counter_num <= self._last_emitted_counter_num

    def _maybe_flush(self, counter, force=False):
        entry = self._packet_cache.get(counter)
        if not entry:
            return
        if not entry.get("sensor_complete"):
            return
        if not entry.get("gps_complete") and not force:
            return
        self._flush_entry(counter)

    def _flush_stale(self, current_counter_num):
        if current_counter_num is None:
            return
        for key, entry in list(self._packet_cache.items()):
            key_num = entry.get("counter_num")
            if key_num is None:
                continue
            if current_counter_num - key_num > 1:
                self._maybe_flush(key, force=True)

    def _as_float_or_none(self, value):
        try:
            parsed = float(value)
        except Exception:
            return None
        return parsed

    def _update_rssi(self):
        rssi_raw = self._as_float_or_none(self._rfm.rssi)
        if rssi_raw is None:
            return None, None
        if self._rssi_ema is None:
            self._rssi_ema = rssi_raw
        else:
            alpha = self._rssi_alpha
            self._rssi_ema = (alpha * rssi_raw) + ((1.0 - alpha) * self._rssi_ema)
        self._last_rssi_sample = {
            "raw": rssi_raw,
            "avg": self._rssi_ema,
        }
        if callable(self._on_rssi_sample):
            try:
                self._on_rssi_sample(self._last_rssi_sample)
            except Exception:
                pass
        return rssi_raw, self._rssi_ema

    def _handle_sens(self, data):
        counter = data.get("c", "nan")
        if self._is_stale_counter(counter):
            return
        entry = self._get_entry(counter)

        rssi_raw, rssi_avg = self._update_rssi()
        if rssi_avg is not None:
            rssi_str = "%0.1f" % rssi_avg
        elif rssi_raw is not None:
            rssi_str = "%0.1f" % rssi_raw
        else:
            rssi_str = "nan"

        entry.update(
            {
                "time": data.get("gt", "NOFIX"),
                "temp": data.get("t", "nan"),
                "pressure": data.get("p", "nan"),
                "baro": data.get("bh", "nan"),
                "humidity": data.get("h", "nan"),
                "gps_time": data.get("gt", "NOFIX"),
                "rx_rssi": rssi_str,
                "sensor_complete": True,
            }
        )
        self._maybe_flush(counter)
        self._flush_stale(entry.get("counter_num"))

    def _handle_beacon(self, data):
        # Beacon packets update RSSI tracking state only. Emitting a serial line
        # per beacon can saturate the USB link and stall the runtime.
        self._update_rssi()

    def _handle_gps(self, prefix, data):
        counter = data.get("c", "nan")
        if self._is_stale_counter(counter):
            return
        entry = self._get_entry(counter)
        updates = {}

        if prefix in {"GPS", "G", "G1"}:
            updates["lat"] = data.get("x", data.get("la", "nan"))
            updates["lon"] = data.get("y", data.get("lo", "nan"))
            if updates.get("lat", "nan") != "nan" and updates.get("lon", "nan") != "nan":
                entry["gps_primary_complete"] = True
        if prefix in {"GPS", "G", "G2"}:
            updates["alt"] = data.get("z", data.get("al", "nan"))
            updates["speed"] = data.get("v", data.get("sp", "nan"))
            # Mark secondary part complete on arrival of G2-style packet.
            entry["gps_secondary_complete"] = True

        # Combined legacy payload may carry all fields in one packet.
        if prefix in {"GPS", "G"}:
            if updates.get("lat", "nan") != "nan" and updates.get("lon", "nan") != "nan":
                entry["gps_primary_complete"] = True
            if (
                "z" in data
                or "al" in data
                or "v" in data
                or "sp" in data
            ):
                entry["gps_secondary_complete"] = True

        entry.update(updates)
        if entry.get("lat", "nan") != "nan" and entry.get("lon", "nan") != "nan":
            entry["fix"] = "1"
        if entry.get("gps_primary_complete") and entry.get("gps_secondary_complete"):
            entry["gps_complete"] = True
        self._maybe_flush(counter)

    def handle_packet(self, packet):
        if packet is None:
            return
        try:
            packet_text = str(packet, "ascii")
        except Exception:
            return
        prefix, data = _parse_packet_text(packet_text)
        if not data:
            return
        if prefix == "SENS":
            self._handle_sens(data)
        elif prefix == "BCN":
            self._handle_beacon(data)
        elif prefix in {"GPS", "G", "G1", "G2"}:
            self._handle_gps(prefix, data)

    def latest_rssi_avg(self):
        if not isinstance(self._last_rssi_sample, dict):
            return None
        return self._last_rssi_sample.get("avg")

    def poll_once(self, timeout=0.05):
        packet = self._rfm.receive(timeout=timeout, with_ack=False)
        self.handle_packet(packet)

    def run_forever(self, should_stop=None):
        while True:
            if callable(should_stop) and should_stop():
                return
            packet = self._rfm.receive(with_ack=True)
            self.handle_packet(packet)


def run_receiver_loop(emit_line=None, should_stop=None):
    module = ReceiverDataModule(emit_line=emit_line)
    module.run_forever(should_stop=should_stop)
