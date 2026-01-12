"""Utility helpers to initialise and read the BME280/BMP280 sensor."""

from machine import I2C, Pin  # type: ignore

try:
    from bme280 import BME280, BMP280_I2CADDR
except Exception:
    BME280 = None  # type: ignore
    BMP280_I2CADDR = 0x76  # Default address so helper can still run

_sensor_instance = None


def calculate_baro_altitude(pressure_hpa, reference_pressure_hpa=1013.25, temperature_c=None):
    """Estimate altitude (m) from pressure using the barometric formula."""
    try:
        pressure = float(pressure_hpa)
        reference = float(reference_pressure_hpa)
    except Exception:
        return None
    if pressure <= 0 or reference <= 0:
        return None

    # Standard atmosphere constants
    g = 9.80665  # m/s^2
    L = 0.0065  # K/m
    R = 8.31432  # N·m/(mol·K)
    M = 0.0289644  # kg/mol

    if temperature_c is None:
        temp_k = 288.15  # ISA sea-level temperature in Kelvin
    else:
        try:
            temp_k = 273.15 + float(temperature_c)
        except Exception:
            temp_k = 288.15
    if temp_k <= 0:
        return None

    # Convert hPa to Pa for formula consistency
    pressure_pa = pressure * 100.0
    reference_pa = reference * 100.0
    exponent = (R * L) / (g * M)
    ratio = pressure_pa / reference_pa
    if ratio <= 0:
        return None
    try:
        altitude = (temp_k / L) * (1 - pow(ratio, exponent))
    except Exception:
        return None
    return altitude


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
