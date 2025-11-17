""" CANSAT PICO Emitter node (CanSat)

Emit message to the base station and wait for ACK (500ms max) over
RFM69HCW SPI module - EMITTER node
Must be tested togheter with test_receiver

See Tutorial : https://wiki.mchobby.be/index.php?title=ENG-CANSAT-PICO-RFM69HCW-TEST
See GitHub : https://github.com/mchobby/cansat-belgium-micropython/tree/main/test-rfm69

RFM69HCW breakout : https://shop.mchobby.be/product.php?id_product=1390
RFM69HCW breakout : https://www.adafruit.com/product/3071
"""

from machine import SPI, Pin, I2C
from rfm69 import RFM69
import time
try:
    # BME280 also works for BMP280
    from bme280 import BME280, BMP280_I2CADDR
except Exception as _e:
    BME280 = None  # Sensor library not present

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

print('Freq            :', rfm.frequency_mhz)
print('NODE            :', rfm.node)
print('BaseStation NODE:', BASESTATION_ID)

# --- Sensor setup (BME280/BMP280 on I2C0, SDA=GP8, SCL=GP9) ---
sensor = None
try:
    i2c = I2C(0, sda=Pin(8), scl=Pin(9))
    if BME280:
        sensor = BME280(i2c=i2c, address=BMP280_I2CADDR)
        print("BME/BMP sensor initialized")
except Exception as e:
    print("Sensor init failed:", e)

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
    try:
        if sensor:
            vals = sensor.raw_values
            if isinstance(vals, tuple) and len(vals) >= 2:
                t, p = vals[0], vals[1]
                h = vals[2] if len(vals) > 2 else None
    except Exception as e:
        print("Sensor read error:", e)

    def _fmt(v, nd=1):
        try:
            return ("%0." + str(nd) + "f") % float(v)
        except:
            return "nan"

    msg = "SENS;c=%d;t=%s;p=%s;h=%s;lr=%s" % (
        counter,
        _fmt(t, 1),
        _fmt(p, 1),
        _fmt(-1 if h is None else h, 1),
        "nan" if last_rssi is None else _fmt(last_rssi, 1),
    )
    print("TX:", msg)
    ack = rfm.send_with_ack(bytes(msg, "utf-8"))
    print("   +->", "ACK received" if ack else "ACK missing")
    # Get the RSSI value when received the ACK --> to send within the next MSG
    if ack:
        last_rssi = rfm.rssi
    counter += 1
    time.sleep(1)
