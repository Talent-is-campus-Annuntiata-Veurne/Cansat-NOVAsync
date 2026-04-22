""" CANSAT PICO Emitter ORIGINAL SCRIPT BY MCHOBBY - EDITED FOR OUR CANSAT NOVAsync PROJECT BY LUCA ROMAGNANI 
main emitter script
"""

print("main.py boot up")

from machine import SPI, Pin, I2C
from rfm69 import RFM69
import time
try:
    import os
except Exception:
    os = None
try:
    import framebuf
except Exception:
    framebuf = None
try:
    from ssd1306 import SSD1306_I2C
except Exception:
    SSD1306_I2C = None
try:
    from sh1106 import SH1106_I2C
except Exception:
    SH1106_I2C = None
try:
    from bmptest_edit import (
        init_sensor,
        read_environment,
        calculate_baro_altitude,
    )
except Exception:
    init_sensor = None
    read_environment = None
    calculate_baro_altitude = None
try:
    from gps_helper import init_gps, read_gps_data
except Exception:
    init_gps = None
    read_gps_data = None

led = Pin(25, Pin.OUT)

# Radio settings (MUST match receiver)
FREQ = 433.9
ENCRYPTION_KEY = bytes("CANSAT_2025-2026", "utf-8")

NODE_ID = 67  # ID of this node (six seven :D)
BASESTATION_ID = 125  # ID of the node (base station) to be contacted

spi = SPI(0, miso=Pin(4), mosi=Pin(7), sck=Pin(6), baudrate=50000, polarity=0, phase=0, firstbit=SPI.MSB)
nss = Pin(5, Pin.OUT, value=True)
rst = Pin(3, Pin.OUT, value=False)

rfm = RFM69(spi=spi, nss=nss, reset=rst)
rfm.frequency_mhz = FREQ
rfm.tx_power = 20  # 20 dBm (maximum)

# Optionally set an encryption key (16 byte AES key). MUST match both
# on the transmitter and receiver (or be set to None to disable/the default).
rfm.encryption_key = (ENCRYPTION_KEY)
rfm.node = NODE_ID  # This instance is the node 120
radiopayload_max_bytes = 60
RADIO_RETRY_MS = 3000
_radio_next_retry_ms = 0


def _reinit_radio():
    global rfm
    try:
        rfm = RFM69(spi=spi, nss=nss, reset=rst)
        rfm.frequency_mhz = FREQ
        rfm.tx_power = 20
        rfm.encryption_key = ENCRYPTION_KEY
        rfm.node = NODE_ID
        rfm.destination = BASESTATION_ID
        print("RADIO: reinit OK")
        return True
    except Exception as exc:
        print("RADIO WARN: reinit failed:", exc)
        return False

# Fast lightweight beacon packets used by the ground station RSSI tracker.
BEACON_ENABLED = False
BEACON_INTERVAL_MS = 180

OLED_WIDTH = 128
OLED_HEIGHTS = (32, 64)
OLED_ADDRS = (0x3C, 0x3D)
OLED_FREQ = 400000
_oled = None
BOOT_BITMAP = bytes([
    0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
    0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
    0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
    0xff, 0xff, 0xfd, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
    0xff, 0xff, 0xcd, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
    0xff, 0xff, 0x81, 0x3f, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
    0xff, 0xf9, 0xf0, 0x7f, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
    0xff, 0xee, 0xdf, 0xbf, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
    0xff, 0xff, 0xb6, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
    0xff, 0xb7, 0x7b, 0xff, 0xe3, 0x0f, 0x87, 0xc3, 0x0f, 0x01, 0xf8, 0x1f, 0xff, 0xff, 0xff, 0xff,
    0xff, 0xed, 0x8e, 0xff, 0xc3, 0x07, 0x01, 0xc3, 0x0f, 0x01, 0xe0, 0x1f, 0xff, 0xff, 0xff, 0xff,
    0xff, 0x7f, 0x66, 0xff, 0xe1, 0x06, 0x01, 0xc2, 0x0f, 0x01, 0xe0, 0x1f, 0xff, 0xff, 0xff, 0xff,
    0xff, 0x71, 0x53, 0x3f, 0xe0, 0x06, 0x00, 0xc0, 0x0e, 0x00, 0xe1, 0x90, 0xc2, 0x01, 0xe0, 0x7f,
    0xff, 0xdd, 0x4c, 0x3f, 0xc0, 0x04, 0x00, 0xc0, 0x1e, 0x00, 0xe0, 0xf8, 0x06, 0x00, 0xc0, 0x7f,
    0xfe, 0xf6, 0x6d, 0xbf, 0xe0, 0x04, 0x10, 0x60, 0x1e, 0x00, 0xe0, 0x38, 0x06, 0x00, 0xc0, 0x7f,
    0xff, 0xc1, 0x9c, 0x3f, 0xe0, 0x04, 0x30, 0x60, 0x1c, 0x10, 0xe0, 0x18, 0x06, 0x10, 0xc0, 0x7f,
    0xff, 0x19, 0xbe, 0x7f, 0xc0, 0x04, 0x00, 0xe0, 0x1c, 0x10, 0x78, 0x0c, 0x0e, 0x10, 0x83, 0xff,
    0xff, 0x64, 0x3a, 0x7f, 0xe0, 0x06, 0x00, 0xf0, 0x3c, 0x00, 0x6c, 0x0c, 0x0e, 0x18, 0xc3, 0x7f,
    0xff, 0x85, 0x72, 0xff, 0xe0, 0x06, 0x00, 0xf0, 0x3c, 0x00, 0x64, 0x0c, 0x0e, 0x10, 0xc0, 0x7f,
    0xff, 0xd1, 0x6d, 0xff, 0xc1, 0x0e, 0x00, 0xf0, 0x38, 0x08, 0x20, 0x1e, 0x1e, 0x10, 0xc0, 0x7f,
    0xff, 0xf2, 0x65, 0xff, 0xe1, 0x87, 0x01, 0xf8, 0x38, 0x38, 0x00, 0x1e, 0x1e, 0x10, 0xe0, 0x7f,
    0xff, 0xfe, 0x9b, 0xff, 0xe1, 0x8f, 0xc7, 0xf8, 0x78, 0x7c, 0x70, 0x7c, 0x1e, 0x38, 0xf0, 0xff,
    0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xfc, 0x3f, 0xff, 0xff, 0xff,
    0xff, 0x22, 0xda, 0x23, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xf8, 0x3f, 0xff, 0xff, 0xff,
    0xff, 0x6a, 0x56, 0xb7, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xfe, 0xff, 0xff, 0xff, 0xff,
    0xff, 0x62, 0x9a, 0x37, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
    0xff, 0x2a, 0xd6, 0xb7, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
    0xff, 0x32, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
    0xf5, 0x56, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
    0xf5, 0x32, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
    0xff, 0x56, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
    0xff, 0x32, 0x3f, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
])
OLED_BUS_CANDIDATES = (
    (0, 8, 9),   # I2C0 default in this project (BMP/BME on 0x77)
    (1, 10, 11), # I2C1 on GP10/GP11 (matches alternate wiring area)
    (1, 2, 3),   # I2C1 common wiring fallback
)


def _oled_trim(value, limit):
    text = value if value not in (None, "", "nan") else "--"
    text = str(text)
    if len(text) > limit:
        return text[:limit]
    return text


def _oled_show_boot():
    if not _oled:
        return
    _oled.fill(0)
    if framebuf is not None and len(BOOT_BITMAP) == (OLED_WIDTH * 32 // 8):
        try:
            logo = framebuf.FrameBuffer(bytearray(BOOT_BITMAP), OLED_WIDTH, 32, framebuf.MONO_HLSB)
            _oled.blit(logo, 0, 0)
        except Exception:
            _oled.text("CANSAT", 0, 0)
            _oled.text("NOVAsync", 0, 12)
            _oled.text("booting...", 0, 24)
    else:
        _oled.text("CANSAT", 0, 0)
        _oled.text("NOVAsync", 0, 12)
        _oled.text("booting...", 0, 24)
    _oled.show()


def _oled_update(lat_text, lon_text, temp_text):
    if not _oled:
        return
    try:
        _oled.fill(0)
        _oled.text("Lat:%s" % _oled_trim(lat_text, 9), 0, 0)
        _oled.text("Lon:%s" % _oled_trim(lon_text, 9), 0, 12)
        _oled.text("T:%sC" % _oled_trim(temp_text, 6), 0, 24)
        _oled.show()
    except Exception as exc:
        print("OLED WARN: update failed:", exc)


def _init_oled():
    global _oled
    if SSD1306_I2C is None and SH1106_I2C is None:
        print("OLED: no driver found (need ssd1306.py or sh1106.py on Pico)")
        return
    try:
        last_error = None
        saw_any_device = False
        for bus_id, sda_pin, scl_pin in OLED_BUS_CANDIDATES:
            try:
                i2c = I2C(bus_id, scl=Pin(scl_pin), sda=Pin(sda_pin), freq=OLED_FREQ)
                found = i2c.scan()
            except Exception as exc:
                print("OLED I2C%d init failed (SDA GP%d/SCL GP%d):" % (bus_id, sda_pin, scl_pin), exc)
                continue

            found_hex = ["0x%02X" % addr for addr in found]
            print("OLED I2C%d scan (SDA GP%d/SCL GP%d):" % (bus_id, sda_pin, scl_pin), found_hex)
            if found:
                saw_any_device = True

            candidate_addrs = [addr for addr in OLED_ADDRS if addr in found]
            if not candidate_addrs:
                continue

            for addr in candidate_addrs:
                for height in OLED_HEIGHTS:
                    try:
                        if SSD1306_I2C is not None:
                            _oled = SSD1306_I2C(OLED_WIDTH, height, i2c, addr=addr)
                        else:
                            _oled = SH1106_I2C(OLED_WIDTH, height, i2c, addr=addr)
                        try:
                            _oled.contrast(0x7F)
                        except Exception:
                            pass
                        _oled_show_boot()
                        time.sleep_ms(3000)
                        print(
                            "OLED ready on I2C%d (SDA GP%d/SCL GP%d): addr=0x%02X, %dx%d"
                            % (bus_id, sda_pin, scl_pin, addr, OLED_WIDTH, height)
                        )
                        return
                    except Exception as exc:
                        last_error = exc

            if SH1106_I2C is not None:
                for addr in candidate_addrs:
                    for height in OLED_HEIGHTS:
                        try:
                            _oled = SH1106_I2C(OLED_WIDTH, height, i2c, addr=addr)
                            _oled_show_boot()
                            time.sleep_ms(3000)
                            print(
                                "OLED ready (SH1106) on I2C%d (SDA GP%d/SCL GP%d): addr=0x%02X, %dx%d"
                                % (bus_id, sda_pin, scl_pin, addr, OLED_WIDTH, height)
                            )
                            return
                        except Exception as exc:
                            last_error = exc

        if not saw_any_device:
            print("OLED: no I2C device found on tested buses")
        else:
            print("OLED: no OLED at 0x3C/0x3D on tested buses")
        if last_error is not None:
            print("OLED init failed:", last_error)
        _oled = None
    except Exception as exc:
        print("OLED init failed:", exc)
        _oled = None


def _send_packet(text):
    global _radio_next_retry_ms
    data = bytes(text, "utf-8")
    if len(data) > radiopayload_max_bytes:
        print("RADIO WARN: payload too large (%d bytes) -> %s" % (len(data), text))
        return False
    if rfm is None:
        now = time.ticks_ms()
        if time.ticks_diff(now, _radio_next_retry_ms) >= 0:
            _reinit_radio()
            _radio_next_retry_ms = time.ticks_add(now, RADIO_RETRY_MS)
        return False
    try:
        return bool(rfm.send(data))
    except Exception as exc:
        print("RADIO WARN: send failed:", exc)
        _radio_next_retry_ms = time.ticks_add(time.ticks_ms(), RADIO_RETRY_MS)
        try:
            _reinit_radio()
        except Exception:
            pass
        return False

# Suppress non-CSV console output

# --- Local logging setup ---
# Each emitted CSV line is also appended to LOGFILE.
# If the file does not exist, create it with a header.
LOGFILE = "sensor_log.txt"
LOG_MAX_BYTES = 128 * 1024  # Rotate at boot / stop at runtime once this size is reached
LOGGING_ENABLED = True
LOG_HEADER = (
    "counter,time_hms,tempC,pressure_hPa,baro_alt_m,humidity_pct_or_-1,"
    "lat_deg,lon_deg,alt_m,speed_kmh,fix_flag,gps_time\n"
)


def _log_size_bytes(path):
    if os is None:
        return None
    try:
        st = os.stat(path)
        # MicroPython stat tuple: size is usually index 6.
        if isinstance(st, (tuple, list)) and len(st) > 6:
            return int(st[6])
    except Exception:
        return None
    return None


def _log_append(line):
    global LOGGING_ENABLED
    if not LOGGING_ENABLED:
        return
    size_now = _log_size_bytes(LOGFILE)
    if size_now is not None and size_now >= LOG_MAX_BYTES:
        LOGGING_ENABLED = False
        print("LOG WARN: disabled (size limit reached)")
        return
    try:
        with open(LOGFILE, "a") as _f:
            _f.write(line + "\n")
    except Exception:
        # Disable further writes on persistent filesystem errors.
        LOGGING_ENABLED = False
        print("LOG WARN: disabled (write error)")


def _reset_log_file():
    with open(LOGFILE, "w") as _f:
        _f.write(LOG_HEADER)


try:
    open(LOGFILE, "r").close()  # Exists
except Exception:
    try:
        _reset_log_file()
    except Exception:
        pass  # Ignore filesystem errors (e.g., read-only FS)

size_at_boot = _log_size_bytes(LOGFILE)
if size_at_boot is not None and size_at_boot >= LOG_MAX_BYTES:
    try:
        _reset_log_file()
        print("LOG WARN: oversized at boot, log file reset")
    except Exception:
        LOGGING_ENABLED = False
        print("LOG WARN: oversized at boot, reset failed -> logging disabled")

_init_oled()

# --- Timing & barometer configuration ---
LOCAL_TIME_OFFSET_MIN = 60  # Veurne is UTC+1 in winter; set 120 for UTC+2 (summer)
BARO_DEFAULT_REF_HPA = 1013.25  # Used until a local baseline is learnt
BARO_BASELINE_SAMPLES = 20  # Number of pressure samples to average for baseline
BARO_BASELINE_MAX_DEVIATION_HPA = 1.5  # Reject startup pressure outliers


def format_gps_time(timestamp_tuple):
    if not timestamp_tuple:
        return "NOFIX"
    try:
        hh = mm = ss = None
        if hasattr(timestamp_tuple, "tm_hour"):
            hh = int(getattr(timestamp_tuple, "tm_hour", 0) or 0)
            mm = int(getattr(timestamp_tuple, "tm_min", 0) or 0)
            ss = int(getattr(timestamp_tuple, "tm_sec", 0) or 0)
        elif isinstance(timestamp_tuple, (tuple, list)) and len(timestamp_tuple) >= 6:
            hh = int(timestamp_tuple[3] or 0)
            mm = int(timestamp_tuple[4] or 0)
            ss = int(timestamp_tuple[5] or 0)
        if hh is None:
            return "NOFIX"
        offset_seconds = LOCAL_TIME_OFFSET_MIN * 60
        total_seconds = (hh * 3600 + mm * 60 + ss + offset_seconds) % 86400
        local_h = total_seconds // 3600
        local_m = (total_seconds % 3600) // 60
        local_s = total_seconds % 60
        return "%02d:%02d:%02d" % (local_h, local_m, local_s)
    except Exception:
        pass
    return "NOFIX"

# --- Sensor setup (BME280/BMP280 on I2C0, SDA=GP8, SCL=GP9) ---
sensor = init_sensor() if callable(init_sensor) else None

# --- GPS setup (UART0 on GP0/GP1) ---
gps = init_gps() if callable(init_gps) else None
GPS_SLEEP_SLICE_MS = 50
LOOP_PERIOD_MS = 1000  # Target 1 Hz telemetry cadence
baro_ref_hpa = None
_baro_baseline_samples = []

# Send a packet and waits for its ACK.
# Note you can only send a packet up to 60 bytes in length.
counter = 1
rfm.destination = BASESTATION_ID  # Send to specific node 100
last_beacon_ms = time.ticks_ms()
while True:
    loop_start = time.ticks_ms()
    try:
        led.toggle()
        # Read sensor values (temperature C, pressure hPa, humidity %)
        # Some BMP280 variants don't return humidity (None)
        t = p = h = None
        if callable(read_environment):
            try:
                t, p, h = read_environment(sensor)
            except Exception:
                t = p = h = None
        baro_alt = None
        if p is not None:
            if baro_ref_hpa is None:
                try:
                    p_now = float(p)
                    # Ignore obviously invalid startup values.
                    if 850.0 <= p_now <= 1100.0:
                        _baro_baseline_samples.append(p_now)
                except Exception:
                    pass
                if len(_baro_baseline_samples) >= BARO_BASELINE_SAMPLES:
                    sorted_samples = sorted(_baro_baseline_samples)
                    mid = len(sorted_samples) // 2
                    if len(sorted_samples) % 2:
                        median = sorted_samples[mid]
                    else:
                        median = (sorted_samples[mid - 1] + sorted_samples[mid]) / 2.0
                    filtered = [
                        v for v in sorted_samples
                        if abs(v - median) <= BARO_BASELINE_MAX_DEVIATION_HPA
                    ]
                    if len(filtered) >= max(5, BARO_BASELINE_SAMPLES // 2):
                        baro_ref_hpa = sum(filtered) / len(filtered)
                    else:
                        baro_ref_hpa = sum(sorted_samples) / len(sorted_samples)
                    print("BARO REF locked at %0.2f hPa (n=%d)" % (baro_ref_hpa, len(_baro_baseline_samples)))
            ref_hpa = baro_ref_hpa if baro_ref_hpa is not None else BARO_DEFAULT_REF_HPA
            if callable(calculate_baro_altitude):
                try:
                    baro_alt = calculate_baro_altitude(p, reference_pressure_hpa=ref_hpa, temperature_c=t)
                except Exception:
                    baro_alt = None

        def _fmt(v, nd=1):
            try:
                return ("%0." + str(nd) + "f") % float(v)
            except:
                return "nan"

        def _fmt_coord(v):
            try:
                return ("%0.5f" % float(v)).rstrip("0").rstrip(".")
            except:
                return "nan"

        baro_locked = baro_ref_hpa is not None
        if baro_locked and baro_alt is not None:
            baro_alt_display = _fmt(baro_alt, 1)
        elif not baro_locked:
            baro_alt_display = "nl" #not locked
        else:
            baro_alt_display = "nan"

        gps_info = None
        if callable(read_gps_data):
            try:
                gps_info = read_gps_data(gps)
            except Exception as exc:
                print("GPS WARN: read failed:", exc)
                gps_info = None
        lat = _fmt_coord(gps_info.get("latitude")) if gps_info else "nan"
        lon = _fmt_coord(gps_info.get("longitude")) if gps_info else "nan"
        alt = _fmt(gps_info.get("altitude_m"), 1) if gps_info else "nan"
        spd = _fmt(gps_info.get("speed_kmh"), 1) if gps_info else "nan"
        fix_flag = "1" if gps_info and gps_info.get("has_fix") else "0"
        gps_time = format_gps_time(gps_info.get("timestamp") if gps_info else None)
        temp_display = _fmt(t, 1)
        _oled_update(lat, lon, temp_display)

        msg = "SENS;c=%d;t=%s;p=%s;bh=%s;h=%s;gt=%s" % (
            counter,
            temp_display,
            _fmt(p, 1),
            baro_alt_display,
            _fmt(-1 if h is None else h, 1),
            gps_time,
        )
        _send_packet(msg)
        if gps_info and gps_info.get("has_fix"):
            def _fmt_radio(value, decimals=3):
                try:
                    return ("%0." + str(decimals) + "f") % float(value)
                except:
                    return "nan"

            gps_msg_primary = "G1;c=%d;x=%s;y=%s" % (
                counter,
                _fmt_radio(gps_info.get("latitude"), 3),
                _fmt_radio(gps_info.get("longitude"), 3),
            )
            gps_msg_secondary = "G2;c=%d;z=%s;v=%s" % (
                counter,
                _fmt_radio(gps_info.get("altitude_m"), 1),
                _fmt_radio(gps_info.get("speed_kmh"), 1),
            )
            _send_packet(gps_msg_primary)
            _send_packet(gps_msg_secondary)
        # CSV output only: counter, time_hms, tempC, pressure_hPa, humidity_pct_or_-1,
        # lat_deg, lon_deg, alt_m, speed_kmh, fix_flag, gps_time
        time_str = gps_time
        csv = "%d,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s" % (
            counter,
            time_str,
            _fmt(t, 1),
            _fmt(p, 1),
            baro_alt_display,
            _fmt(-1 if h is None else h, 1),
            lat,
            lon,
            alt,
            spd,
            fix_flag,
            gps_time,
        )
        print(csv)
        # Append to log file (bounded for Pico storage safety)
        _log_append(csv)
        counter += 1
    except Exception as exc:
        print("LOOP WARN: recovered from runtime exception:", exc)
        try:
            _send_packet("STAT;id=%d;err=loop" % NODE_ID)
        except Exception:
            pass
    # Stay close to the 1 Hz cadence by servicing the GPS until the next slot.
    deadline = time.ticks_add(loop_start, LOOP_PERIOD_MS)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        if BEACON_ENABLED:
            now = time.ticks_ms()
            if time.ticks_diff(now, last_beacon_ms) >= BEACON_INTERVAL_MS:
                _send_packet("BCN;c=%d;id=%d" % (counter, NODE_ID))
                last_beacon_ms = now
        if callable(read_gps_data):
            try:
                read_gps_data(gps, poll_window_ms=0)
            except Exception:
                pass
        time.sleep_ms(GPS_SLEEP_SLICE_MS)
