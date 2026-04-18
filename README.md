# Dawggles

Dawggles is a Raspberry Pi + iOS project for BLE-assisted setup, hotspot connectivity, and on-device OLED/app workflows.

## Repository Layout

- `RPi/`: Raspberry Pi runtime (`main.py`, pairing flow, OLED, websocket server)
- `iOS/`: iPhone app and AccessorySetupKit pairing flow
- `Mac/`: Mac-side helper/testing scripts
- `WIRING.md`: hardware wiring reference

## Run `main.py` At Boot (systemd)

A ready-to-use unit file is included at:

- `RPi/systemd/dawggles.service`

This unit starts `main.py` as user `dawggles` using:

- Working directory: `/home/dawggles/dawggles/RPi`
- Python: `/home/dawggles/dawggles/RPi/venv/bin/python`

If your Pi paths differ, edit those values before enabling.

### Install Service On Pi

1. Copy the unit file into systemd:

```bash
sudo cp /home/dawggles/dawggles/RPi/systemd/dawggles.service /etc/systemd/system/dawggles.service
```

2. Reload systemd and enable at boot:

```bash
sudo systemctl daemon-reload
sudo systemctl enable dawggles.service
```

3. Start/restart now:

```bash
sudo systemctl restart dawggles.service
```

4. Check status/logs:

```bash
sudo systemctl --no-pager -l status dawggles.service
journalctl -u dawggles.service -f
```

### Common Commands

```bash
sudo systemctl restart dawggles.service
sudo systemctl stop dawggles.service
sudo systemctl disable dawggles.service
```
