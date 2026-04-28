# Dawggles

Raspberry Pi smart goggles paired to an iPhone over BLE + Wi-Fi.

## Hardware wiring

### OLED Display (SSD1306, SPI)

| OLED pin | Wire color | RPi physical pin | RPi signal |
|----------|------------|------------------|------------|
| Data     | Orange     | 19               | SPI MOSI (GPIO 10) |
| Clk      | Yellow     | 23               | SPI SCLK (GPIO 11) |
| DC       | Green      | 13               | GPIO 27 |
| CS       | Blue       | 11               | GPIO 17 |
| Vin      | Purple     | 1                | 3.3V |
| GND      | Gray       | 6                | GND |

Reset pin is left disconnected.

### Buttons

| Button        | RPi physical pin | GPIO |
|---------------|------------------|------|
| App action (shutter, single press = take photo) | 7  | GPIO 4  |
| Cycle apps    | 16               | GPIO 23 |

Both buttons should be wired between the GPIO pin and GND. `gpiozero` enables the internal pull-up resistor automatically.

## Pi setup

- Install [PiSugar](https://docs.pisugar.com/docs/product-wiki/battery/pisugar3/pisugar-3-series#software-installation) (follow their install instructions).

- Create the Python venv and install dependencies:
  ```bash
  cd /home/dawggles/dawggles/RPi
  python3 -m venv --system-site-packages venv
  source venv/bin/activate
  pip install -r requirements.txt
  ```
  > `--system-site-packages` is required so the venv can access the system-installed `libcamera`/`picamera2` packages.

- Install the service:
  ```bash
  sudo cp RPi/systemd/dawggles.service /etc/systemd/system/
  sudo systemctl daemon-reload
  sudo systemctl enable --now dawggles.service
  ```
- Make sure the script is executable:
  ```bash
  chmod +x RPi/systemd/pisugar_display_toggle.sh
  ```
- In the PiSugar dashboard (`http://<pi-ip>:8421`), set a custom button action to:
  ```
  /home/dawggles/dawggles/RPi/systemd/pisugar_display_toggle.sh
  ```
  Tapping it toggles the OLED on/off without losing app state.
