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
    from bmptest_edit import init_sensor, read_environment
except Exception:
    init_sensor = None
    read_environment = None
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
                "counter,time_hms,tempC,pressure_hPa,humidity_pct_or_-1,last_ack_rssi_or_nan,lat_deg,lon_deg,alt_m,speed_kmh,satellites,fix_flag,gps_time\n"
            )
    except Exception:
        pass  # Ignore filesystem errors (e.g., read-only FS)

# --- RTC synchronisation ---
rtc = None
rtc_synced = False
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
        y, m, d, hh, mm, ss = timestamp_tuple
        return "%02d:%02d:%02d" % (hh, mm, ss)
    except Exception:
        return "NOFIX"


sync_rtc_with_host()

# --- Sensor setup (BME280/BMP280 on I2C0, SDA=GP8, SCL=GP9) ---
sensor = init_sensor() if callable(init_sensor) else None

# --- GPS setup (UART0 on GP0/GP1) ---
gps = init_gps() if callable(init_gps) else None

# Send a packet and waits for its ACK.
# Note you can only send a packet up to 60 bytes in length.
counter = 1
last_rssi = None  # Capture the RSSI when receing ack
rfm.ack_retries = 3  # 3 attempts to receive ACK
rfm.ack_wait = 0.5  # 500ms, time to wait for ACK
rfm.destination = BASESTATION_ID  # Send to specific node 100
while True:
    led.toggle()
    # Read sensor values (temperature C, pressure hPa, humidity %)
    # Some BMP280 variants don't return humidity (None)
    t = p = h = None
    if callable(read_environment):
        try:
            t, p, h = read_environment(sensor)
        except Exception:
            t = p = h = None

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

    msg = "SENS;c=%d;t=%s;p=%s;h=%s;lr=%s" % (
        counter,
        _fmt(t, 1),
        _fmt(p, 1),
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
    csv = "%d,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s" % (
        counter,
        time_str,
        _fmt(t, 1),
        _fmt(p, 1),
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
    time.sleep(1)
