""" CANSAT PICO RECEIVER node

Receives message requiring ACK over RFM69HCW SPI module - RECEIVER node
Must be tested togheter with test_emitter

See Tutorial : https://wiki.mchobby.be/index.php?title=ENG-CANSAT-PICO-RFM69HCW-TEST
See GitHub : https://github.com/mchobby/cansat-belgium-micropython/tree/main/test-rfm69

RFM69HCW breakout : https://shop.mchobby.be/product.php?id_product=1390
RFM69HCW breakout : https://www.adafruit.com/product/3071
"""

from machine import SPI, Pin
from rfm69 import RFM69

# Radio settings (MUST match emitter)
FREQ = 433.9
ENCRYPTION_KEY = bytes("CANSAT_2025-2026", "utf-8")
NODE_ID = 125  # ID of this node

spi = SPI(0, miso=Pin(4), mosi=Pin(7), sck=Pin(6), polarity=0, phase=0, firstbit=SPI.MSB)  # baudrate=50000,
nss = Pin(5, Pin.OUT, value=True)
rst = Pin(3, Pin.OUT, value=False)

rfm = RFM69(spi=spi, nss=nss, reset=rst)
rfm.frequency_mhz = FREQ

# Optionally set an encryption key (16 byte AES key). MUST match both
# on the transmitter and receiver (or be set to None to disable/the default).
rfm.encryption_key = (ENCRYPTION_KEY)
rfm.node = NODE_ID  # This instance is the node 123

print("Receiver boot complete, listening...")

RECEIVER_LOGFILE = "receiver_log.txt"
RECEIVER_HEADER = (
    "counter,time_hms,tempC,pressure_hPa,baro_alt_m,humidity_pct_or_-1,"
    "lat_deg,lon_deg,alt_m,speed_kmh,fix_flag,gps_time,rx_rssi_dbm\n"
)
try:
    open(RECEIVER_LOGFILE, "r").close()
except Exception:
    try:
        with open(RECEIVER_LOGFILE, "w") as _f:
            _f.write(RECEIVER_HEADER)
    except Exception:
        pass


def _append_receiver_log(line):
    try:
        with open(RECEIVER_LOGFILE, "a") as _f:
            _f.write(line + "\n")
    except Exception:
        pass

_packet_cache = {}


def _get_entry(counter):
    entry = _packet_cache.get(counter)
    if entry is None:
        entry = {"counter": counter}
        try:
            entry["counter_num"] = int(counter)
        except Exception:
            entry["counter_num"] = None
        _packet_cache[counter] = entry
    return entry


def _flush_entry(counter):
    entry = _packet_cache.pop(counter, None)
    if not entry:
        return
    line = "{},{},{},{},{},{},{},{},{},{},{},{},{}".format(
        counter,
        entry.get("time", "nan"),
        entry.get("temp", "nan"),
        entry.get("pressure", "nan"),
        entry.get("baro", "nan"),
        entry.get("humidity", "nan"),
        entry.get("lat", "nan"),
        entry.get("lon", "nan"),
        entry.get("alt", "nan"),
        entry.get("speed", "nan"),
        entry.get("fix", "0"),
        entry.get("gps_time", "nan"),
        entry.get("rx_rssi", "nan"),
    )
    pretty_parts = [
        "counter:%s" % counter,
        "time:%s" % entry.get("time", "nan"),
        "temp:%s" % entry.get("temp", "nan"),
        "pressure:%s" % entry.get("pressure", "nan"),
        "baro_alt:%s" % entry.get("baro", "nan"),
        "humidity:%s" % entry.get("humidity", "nan"),
        "lat:%s" % entry.get("lat", "nan"),
        "lon:%s" % entry.get("lon", "nan"),
        "altitude:%s" % entry.get("alt", "nan"),
        "speed:%s" % entry.get("speed", "nan"),
        "fix:%s" % entry.get("fix", "0"),
        "gps_time:%s" % entry.get("gps_time", "nan"),
        "rx_rssi:%s" % entry.get("rx_rssi", "nan"),
    ]
    print(", ".join(pretty_parts))
    _append_receiver_log(line)


def _maybe_flush(counter, force=False):
    entry = _packet_cache.get(counter)
    if not entry:
        return
    if not entry.get("sensor_complete"):
        return
    if not entry.get("gps_complete") and not force:
        return
    _flush_entry(counter)


def _flush_stale(current_counter_num):
    if current_counter_num is None:
        return
    for key, entry in list(_packet_cache.items()):
        key_num = entry.get("counter_num")
        if key_num is None:
            continue
        if current_counter_num - key_num > 1:
            _maybe_flush(key, force=True)

# Suppress non-CSV console output
while True:
    packet = rfm.receive(with_ack=True)
    # Optionally change the receive timeout from its default of 0.5 seconds:
    # packet = rfm.receive(timeout=5.0)
    # If no packet was received during the timeout then None is returned.
    if packet is None:
        # Packet has not been received
        pass
    else:
        # Decode to ASCII text and parse our compact key=value payload
        packet_text = str(packet, "ascii")
        parts = packet_text.split(";")
        if not parts:
            continue
        prefix = parts[0]
        data = {}
        try:
            for part in parts[1:]:
                if "=" in part:
                    k, v = part.split("=", 1)
                    data[k] = v
        except Exception:
            data = {}

        if prefix == "SENS" and data:
            # CSV output: counter, tempC, pressure_hPa, baro_alt_m, humidity_pct_or_-1, rssi
            c = data.get("c", "nan")
            t = data.get("t", "nan")
            p = data.get("p", "nan")
            bh = data.get("bh", "nan")
            h = data.get("h", "nan")
            gt = data.get("gt", "NOFIX")
            rfm.sample_rssi()
            rssi = rfm.rssi
            try:
                rssi_str = "%0.1f" % float(rssi)
            except Exception:
                rssi_str = str(rssi)
            entry = _get_entry(c)
            entry.update(
                {
                    "time": gt,
                    "temp": t,
                    "pressure": p,
                    "baro": bh,
                    "humidity": h,
                    "gps_time": gt,
                    "rx_rssi": rssi_str,
                    "sensor_complete": True,
                }
            )
            _maybe_flush(c)
            _flush_stale(entry.get("counter_num"))
        elif prefix in {"GPS", "G", "G1", "G2"} and data:
            c = data.get("c", "nan")
            entry = _get_entry(c)
            updates = {}
            if prefix in {"GPS", "G", "G1"}:
                updates["lat"] = data.get("x", data.get("la", "nan"))
                updates["lon"] = data.get("y", data.get("lo", "nan"))
            if prefix in {"GPS", "G", "G2"}:
                updates["alt"] = data.get("z", data.get("al", "nan"))
                updates["speed"] = data.get("v", data.get("sp", "nan"))
            entry.update(updates)
            if entry.get("lat", "nan") != "nan" and entry.get("lon", "nan") != "nan":
                entry["fix"] = "1"
                entry["gps_complete"] = True
            _maybe_flush(c)
