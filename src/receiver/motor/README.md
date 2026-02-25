# Potentiometer Wiring Notes

This recorder summarizes how the two multi-turn potentiometers in this project are wired so the Raspberry Pi + Motor HAT can read absolute azimuth/elevation feedback through an MCP3008 ADC while leaving room for the future RFM69HCW radio.

## Connections at a Glance

| Item | Signal | Connects To |
|------|--------|-------------|
| Pot #1 (azimuth) | CW leg | Raspberry Pi 3V3 rail |
| Pot #1 (azimuth) | CCW leg | Raspberry Pi GND rail |
| Pot #1 (azimuth) | Wiper | MCP3008 CH0 |
| Pot #2 (elevation) | CW leg | Raspberry Pi 3V3 rail |
| Pot #2 (elevation) | CCW leg | Raspberry Pi GND rail |
| Pot #2 (elevation) | Wiper | MCP3008 CH1 |
| MCP3008 VDD + VREF | — | Raspberry Pi 3V3 |
| MCP3008 AGND + DGND | — | Raspberry Pi GND |
| MCP3008 CLK | — | Raspberry Pi SCLK (BCM 11) |
| MCP3008 DIN | — | Raspberry Pi MOSI (BCM 10) |
| MCP3008 DOUT | — | Raspberry Pi MISO (BCM 9) |
| MCP3008 CS/SHDN | — | Raspberry Pi CE1 (BCM 7) |

> **Why CE1?** Keeping the ADC on CE1 leaves CE0 (BCM 8) free so the RFM69HCW can share the same SPI bus later with its own chip-select and IRQ line.

## Wiring Diagram (text)

```
3.3V ──┬───────────── azimuth pot ──────────────┬─────────── elevation pot ──────────────┐
       │          (outer leg)             (outer)│            (outer leg)             (outer)
       │                                        │                                        │
       │              wiper ────── CH0          │                wiper ────── CH1        │
       │                                        │                                        │
GND ───┴───────────── azimuth pot ──────────────┴─────────── elevation pot ──────────────┘

             MCP3008 (top view)
        ┌────────────────────────┐
        │ VDD  VREF  AGND  DGND │── to Pi 3V3 and GND rails
        │ CH0  CH1   CH2  … CH7 │── CH0=Wiper azimuth, CH1=Wiper elevation
        │ CLK  DOUT  DIN  CS    │── CLK→BCM11, DOUT→BCM9, DIN→BCM10, CS→BCM7 (CE1)
        └────────────────────────┘
```

## Step-by-step Summary

1. **Power rails**: Tie both potentiometer outer legs to 3.3 V and GND so they act as voltage dividers inside the ADC’s 0–3.3 V input range.
2. **Wipers to ADC**: Send the azimuth wiper to MCP3008 CH0 and the elevation wiper to CH1.
3. **ADC power**: Feed MCP3008 VDD and VREF from the Pi’s 3.3 V pin, and connect AGND/DGND to the common ground.
4. **SPI bus**: Hook CLK→BCM11, DIN→BCM10, DOUT→BCM9, CS→BCM7 (CE1). MOSI/DIN and MISO/DOUT share the Pi’s SPI0 bus.
5. **Future radio**: Leave CE0 (BCM8) for the RFM69HCW. Only SCLK/MOSI/MISO are shared; CS lines keep devices isolated.

Once wired this way the existing `PotAngleReader` (MCP3008 driver) will report angles for both pots, enabling the web UI and calibration logic to work exactly like on the Raspberry Pi DC controller.
