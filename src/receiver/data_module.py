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
        self._last_emitted_counter_num = None
        self._rssi_ema = None
        self._rssi_alpha = 0.35
        self._last_rssi_sample = None
        self._last_gps_time = "NOFIX"
        self._last_fix = "0"
        self._last_lat = "nan"
        self._last_lon = "nan"
        self._last_alt = "nan"
        self._last_speed = "nan"
        self._last_temp = "nan"
        self._last_pressure = "nan"
        self._last_baro = "nan"
        self._last_humidity = "nan"

    def _emit(self, text):
        if callable(self._emit_line):
            self._emit_line(text)
        else:
            print(text)

    def _is_stale_counter(self, counter):
        try:
            counter_num = int(counter)
        except Exception:
            return False
        if not isinstance(self._last_emitted_counter_num, int):
            return False
        return counter_num < self._last_emitted_counter_num

    def _mark_emitted_counter(self, counter):
        try:
            counter_num = int(counter)
        except Exception:
            return
        self._last_emitted_counter_num = counter_num

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

        rssi_raw, rssi_avg = self._update_rssi()
        if rssi_avg is not None:
            rssi_str = "%0.1f" % rssi_avg
        elif rssi_raw is not None:
            rssi_str = "%0.1f" % rssi_raw
        else:
            rssi_str = "nan"

        gps_time = data.get("gt", "NOFIX")
        if gps_time in {"nan", "", None}:
            gps_time = self._last_gps_time

        temp = data.get("t", "nan")
        pressure = data.get("p", "nan")
        baro = data.get("bh", "nan")
        humidity = data.get("h", "nan")
        if temp != "nan":
            self._last_temp = temp
        if pressure != "nan":
            self._last_pressure = pressure
        if baro != "nan":
            self._last_baro = baro
        if humidity != "nan":
            self._last_humidity = humidity

        entry = {
            "counter": counter,
            "time": gps_time,
            "temp": self._last_temp,
            "pressure": self._last_pressure,
            "baro": self._last_baro,
            "humidity": self._last_humidity,
            "lat": self._last_lat,
            "lon": self._last_lon,
            "alt": self._last_alt,
            "speed": self._last_speed,
            "fix": self._last_fix,
            "gps_time": gps_time,
            "rx_rssi": rssi_str,
        }
        self._emit(_format_telemetry_line(entry))
        self._mark_emitted_counter(counter)

    def _handle_beacon(self, data):
        # Beacon packets update RSSI tracking state only. Emitting a serial line
        # per beacon can saturate the USB link and stall the runtime.
        self._update_rssi()

    def _handle_gps(self, prefix, data):
        counter = data.get("c", "nan")
        if self._is_stale_counter(counter):
            return
        gps_time = data.get("gt", self._last_gps_time)
        if gps_time not in {"nan", "", None, "NOFIX"}:
            self._last_gps_time = gps_time

        lat = data.get("x", data.get("la", "nan")) if prefix in {"GPS", "G", "G1"} else "nan"
        lon = data.get("y", data.get("lo", "nan")) if prefix in {"GPS", "G", "G1"} else "nan"
        alt = data.get("z", data.get("al", "nan")) if prefix in {"GPS", "G", "G2"} else "nan"
        speed = data.get("v", data.get("sp", "nan")) if prefix in {"GPS", "G", "G2"} else "nan"

        if lat != "nan":
            self._last_lat = lat
        if lon != "nan":
            self._last_lon = lon
        if alt != "nan":
            self._last_alt = alt
        if speed != "nan":
            self._last_speed = speed

        if prefix in {"GPS", "G", "G1"}:
            has_latlon = lat != "nan" and lon != "nan"
            fix = "1" if has_latlon else "0"
            self._last_fix = fix
        else:
            fix = self._last_fix

        entry = {
            "counter": counter,
            "time": gps_time,
            "temp": self._last_temp,
            "pressure": self._last_pressure,
            "baro": self._last_baro,
            "humidity": self._last_humidity,
            "lat": self._last_lat,
            "lon": self._last_lon,
            "alt": self._last_alt,
            "speed": self._last_speed,
            "fix": fix,
            "gps_time": gps_time,
            "rx_rssi": "nan",
        }
        self._emit(_format_telemetry_line(entry))
        self._mark_emitted_counter(counter)

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
