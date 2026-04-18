# Dawggles

Raspberry Pi smart goggles paired to an iPhone over BLE + Wi-Fi.

## Pi setup

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
