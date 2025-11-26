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
import time

# Radio settings (MUST match emitter)
FREQ = 435
ENCRYPTION_KEY = bytes("CANSAT_2025-2026", "utf-8")
NODE_ID = 100  # ID of this node

spi = SPI(0, miso=Pin(4), mosi=Pin(7), sck=Pin(6), polarity=0, phase=0, firstbit=SPI.MSB)  # baudrate=50000,
nss = Pin(5, Pin.OUT, value=True)
rst = Pin(3, Pin.OUT, value=False)

rfm = RFM69(spi=spi, nss=nss, reset=rst)
rfm.frequency_mhz = FREQ

# Optionally set an encryption key (16 byte AES key). MUST match both
# on the transmitter and receiver (or be set to None to disable/the default).
rfm.encryption_key = (ENCRYPTION_KEY)
rfm.node = NODE_ID  # This instance is the node 123

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
        data = {}
        try:
            if packet_text.startswith("SENS;"):
                for part in packet_text.split(";")[1:]:
                    if "=" in part:
                        k, v = part.split("=", 1)
                        data[k] = v
        except Exception:
            data = {}
        if data:
            # CSV output only: counter, tempC, pressure_hPa, humidity_pct_or_-1, rssi
            c = data.get("c", "nan")
            t = data.get("t", "nan")
            p = data.get("p", "nan")
            h = data.get("h", "nan")
            rfm.sample_rssi()
            rssi = rfm.rssi
            try:
                print("%s,%s,%s,%s,%0.1f" % (c, t, p, h, float(rssi)))
            except Exception:
                # Fallback without formatting if conversion fails
                print("%s,%s,%s,%s,%s" % (c, t, p, h, rssi))
