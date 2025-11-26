"""Utility helpers to initialise and read the BME280/BMP280 sensor."""

from machine import I2C, Pin  # type: ignore

try:
    from bme280 import BME280, BMP280_I2CADDR
except Exception:
    BME280 = None  # type: ignore
    BMP280_I2CADDR = 0x76  # Default address so helper can still run

_sensor_instance = None


def init_sensor(bus=0, sda=8, scl=9, address=None):
    """Initialise the BME/BMP sensor and cache the instance."""
    global _sensor_instance
    if _sensor_instance:
        return _sensor_instance
    if not BME280:
        return None
    try:
        i2c = I2C(bus, sda=Pin(sda), scl=Pin(scl))
        addr = address if address is not None else BMP280_I2CADDR
        _sensor_instance = BME280(i2c=i2c, address=addr)
    except Exception:
        _sensor_instance = None
    return _sensor_instance


def read_environment(sensor=None):
    """Return (temperature, pressure, humidity) tuple or Nones when unavailable."""
    if sensor is None:
        sensor = _sensor_instance or init_sensor()
    if not sensor:
        return (None, None, None)
    try:
        values = sensor.raw_values
    except Exception:
        return (None, None, None)
    if not isinstance(values, tuple):
        return (None, None, None)
    temp = values[0] if len(values) > 0 else None
    press = values[1] if len(values) > 1 else None
    hum = values[2] if len(values) > 2 else None
    return (temp, press, hum)


if __name__ == "__main__":
    import time

    sensor = init_sensor()
    while True:
        print(read_environment(sensor))
        time.sleep(1)
