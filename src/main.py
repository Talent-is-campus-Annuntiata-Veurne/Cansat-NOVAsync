""" CANSAT PICO Emitter ORIGINAL SCRIPT BY MCHOBBY - EDITED FOR OUR CANSAT NOVAsync PROJECT BY LUCA ROMAGNANI 
main emitter script
"""

print("main.py boot up")

from machine import SPI, Pin, RTC
from rfm69 import RFM69
import time
import sys
try:
    import uselect  # type: ignore
except Exception:
    uselect = None
try:
    import os  # MicroPython provides a lightweight os module
except Exception:
    os = None
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
FREQ = 435
ENCRYPTION_KEY = bytes("CANSAT_2025-2026", "utf-8")

NODE_ID = 120  # ID of this node
BASESTATION_ID = 100  # ID of the node (base station) to be contacted

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

# Suppress non-CSV console output

# --- Local logging setup ---
# Each emitted CSV line is also appended to LOGFILE.
# If the file does not exist, create it with a header.
LOGFILE = "sensor_log.txt"
try:
    open(LOGFILE, "r").close()  # Exists
except Exception:
    try:
        with open(LOGFILE, "w") as _f:
            _f.write(
                "counter,time_hms,tempC,pressure_hPa,baro_alt_m,humidity_pct_or_-1,last_ack_rssi_or_nan,lat_deg,lon_deg,alt_m,speed_kmh,satellites,fix_flag,gps_time\n"
            )
    except Exception:
        pass  # Ignore filesystem errors (e.g., read-only FS)

# --- RTC synchronisation ---
rtc = None
rtc_synced = False
LOCAL_TIME_OFFSET_MIN = 60  # Veurne is UTC+1 in winter; set 120 for UTC+2 (summer)
BARO_DEFAULT_REF_HPA = 1013.25  # Used until a local baseline is learnt
BARO_BASELINE_SAMPLES = 20  # Number of pressure samples to average for baseline
try:
    rtc = RTC()
except Exception:
    rtc = None


def _normalize_epoch(value):
    try:
        val = float(value)
    except Exception:
        raise ValueError("not a number")
    if val < 0:
        raise ValueError("negative timestamp")
    # Accept millisecond or microsecond stamps by scaling down to seconds.
    # Cut down by factors of 1000 until within year 3000 (~3.25e10 seconds).
    limit = 32503680000  # 3000-01-01 UTC
    while val > limit:
        val /= 1000.0
    return int(val)


def _set_rtc_from_epoch(epoch_s):
    global rtc_synced
    if rtc is None:
        return False
    try:
        tm = time.localtime(epoch_s)
    except Exception:
        return False
    try:
        rtc.datetime((tm[0], tm[1], tm[2], tm[6], tm[3], tm[4], tm[5], 0))
        rtc_synced = True
        return True
    except Exception:
        return False


def sync_rtc_with_host(timeout_ms=10000):
    """Request UNIX timestamp over USB serial and program the RTC."""
    if rtc is None or uselect is None:
        return False
    poller = uselect.poll()
    poller.register(sys.stdin, uselect.POLLIN)
    deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
    print("TIME_SYNC: send UNIX timestamp (seconds or milliseconds) and press enter...")
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        if poller.poll(250):
            line = sys.stdin.readline()
            if not line:
                continue
            line = line.strip()
            if not line:
                continue
            try:
                epoch = _normalize_epoch(line)
            except Exception:
                print("TIME_SYNC: invalid timestamp '%s'" % line)
                continue
            if _set_rtc_from_epoch(epoch):
                print("TIME_SYNC: RTC set to %s" % format_timestamp())
                return True
            print("TIME_SYNC: failed to set RTC")
            return False
        time.sleep_ms(50)
    print("TIME_SYNC: timeout waiting for timestamp")
    return False


def format_timestamp():
    if not rtc_synced:
        return "UNSYNCED"
    tm = time.localtime()
    return "%04d-%02d-%02dT%02d:%02d:%02d" % (tm[0], tm[1], tm[2], tm[3], tm[4], tm[5])


def format_time_only():
    if not rtc_synced:
        return "UNSYNCED"
    tm = time.localtime()
    return "%02d:%02d:%02d" % (tm[3], tm[4], tm[5])


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


sync_rtc_with_host()

# --- Sensor setup (BME280/BMP280 on I2C0, SDA=GP8, SCL=GP9) ---
sensor = init_sensor() if callable(init_sensor) else None

# --- GPS setup (UART0 on GP0/GP1) ---
gps = init_gps() if callable(init_gps) else None
GPS_SLEEP_SLICE_MS = 50
LOOP_PERIOD_MS = 1000  # Target 1 Hz telemetry cadence
baro_ref_hpa = None
_baro_baseline_sum = 0.0
_baro_baseline_count = 0

# Send a packet and waits for its ACK.
# Note you can only send a packet up to 60 bytes in length.
counter = 1
last_rssi = None  # Capture the RSSI when receing ack
rfm.ack_retries = 1  # Limit retries so a missing ACK doesn't stall the loop
rfm.ack_wait = 0.2  # Shorter wait keeps loop close to 1 Hz
rfm.destination = BASESTATION_ID  # Send to specific node 100
while True:
    loop_start = time.ticks_ms()
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
                _baro_baseline_sum += float(p)
                _baro_baseline_count += 1
            except Exception:
                pass
            if _baro_baseline_count >= BARO_BASELINE_SAMPLES:
                baro_ref_hpa = _baro_baseline_sum / _baro_baseline_count
                print("BARO REF locked at %0.2f hPa" % baro_ref_hpa)
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

    gps_info = read_gps_data(gps) if callable(read_gps_data) else None
    lat = _fmt_coord(gps_info.get("latitude")) if gps_info else "nan"
    lon = _fmt_coord(gps_info.get("longitude")) if gps_info else "nan"
    alt = _fmt(gps_info.get("altitude_m"), 1) if gps_info else "nan"
    spd = _fmt(gps_info.get("speed_kmh"), 1) if gps_info else "nan"
    sats = (
        str(gps_info.get("satellites"))
        if gps_info and gps_info.get("satellites") is not None
        else "nan"
    )
    fix_flag = "1" if gps_info and gps_info.get("has_fix") else "0"
    gps_time = format_gps_time(gps_info.get("timestamp") if gps_info else None)

    msg = "SENS;c=%d;t=%s;p=%s;bh=%s;h=%s;lr=%s" % (
        counter,
        _fmt(t, 1),
        _fmt(p, 1),
        _fmt(baro_alt, 1),
        _fmt(-1 if h is None else h, 1),
        "nan" if last_rssi is None else _fmt(last_rssi, 1),
    )
    ack = rfm.send_with_ack(bytes(msg, "utf-8"))
    if gps_info and gps_info.get("has_fix"):
        def _fmt_radio(value, decimals=4):
            try:
                return ("%0." + str(decimals) + "f") % float(value)
            except:
                return "nan"

        gps_msg = "GPS;c=%d;la=%s;lo=%s;al=%s;sp=%s;sa=%s" % (
            counter,
            _fmt_radio(gps_info.get("latitude"), 4),
            _fmt_radio(gps_info.get("longitude"), 4),
            _fmt_radio(gps_info.get("altitude_m"), 1),
            _fmt_radio(gps_info.get("speed_kmh"), 1),
            sats,
        )
        rfm.send_with_ack(bytes(gps_msg, "utf-8"))
    # CSV output only: counter, time_hms, tempC, pressure_hPa, humidity_pct_or_-1,
    # last_ack_rssi_or_nan, lat_deg, lon_deg, alt_m, speed_kmh, satellites,
    # fix_flag, gps_time
    time_str = format_time_only()
    csv = "%d,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s" % (
        counter,
        time_str,
        _fmt(t, 1),
        _fmt(p, 1),
        _fmt(baro_alt, 1),
        _fmt(-1 if h is None else h, 1),
        "nan" if last_rssi is None else _fmt(last_rssi, 1),
        lat,
        lon,
        alt,
        spd,
        sats,
        fix_flag,
        gps_time,
    )
    print(csv)
    # Append to log file
    try:
        with open(LOGFILE, "a") as _f:
            _f.write(csv + "\n")
    except Exception:
        pass  # Ignore write errors to keep radio loop running
    # Get the RSSI value when received the ACK --> to send within the next MSG
    if ack:
        last_rssi = rfm.rssi
    counter += 1
    # Stay close to the 1 Hz cadence by servicing the GPS until the next slot.
    deadline = time.ticks_add(loop_start, LOOP_PERIOD_MS)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        if callable(read_gps_data):
            read_gps_data(gps, poll_window_ms=0)
        time.sleep_ms(GPS_SLEEP_SLICE_MS)
