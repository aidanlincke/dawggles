# Dawggles Wiring

## OLED Display (SSD1306, SPI)

| OLED pin | Wire color | RPi physical pin | RPi signal |
|----------|------------|------------------|------------|
| Data     | Green      | 19               | SPI MOSI (GPIO 10) |
| Clk      | Yellow     | 23               | SPI SCLK (GPIO 11) |
| DC       | Orange     | 13               | GPIO 27 |
| CS       | Red        | 11               | GPIO 17 |
| Vin      | Brown      | 1                | 3.3V |
| GND      | Black      | 6                | GND |

Reset pin is left disconnected.

## Buttons

| Button        | RPi physical pin | GPIO |
|---------------|------------------|------|
| App action (shutter, single press = take photo) | 7  | GPIO 4  |
| Cycle apps    | 16               | GPIO 23 |

Both buttons should be wired between the GPIO pin and GND. `gpiozero` enables the internal pull-up resistor automatically.
