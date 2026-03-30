#!/bin/bash
# Raspberry Pi OS (Bookworm) + NetworkManager: create AP SSID "Dawggles".
# Run on the Pi with sudo. Single-radio Pis cannot be home-WiFi client and AP at once.
#
#   export DAWGGLES_AP_PASSWORD='your-wpa2-pass-phrase'
#   sudo -E ./network/setup_dawggles_hotspot.sh
#
# Optional: DAWGGLES_AP_IFACE=wlan0  DAWGGLES_AP_SSID=Dawggles
#
# Then on the Pi before main.py:
#   export DAWGGLES_AP_INTERFACE="${DAWGGLES_AP_IFACE:-wlan0}"
# or set DAWGGLES_TCP_HOST to the printed AP IPv4.
#
# Switch back to CMU-DEVICE: see restore_cmu_wifi.sh

set -euo pipefail

IFACE="${DAWGGLES_AP_IFACE:-wlan0}"
SSID="${DAWGGLES_AP_SSID:-Dawggles}"
PASS="${DAWGGLES_AP_PASSWORD:?Set DAWGGLES_AP_PASSWORD (8+ chars for WPA2)}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run with sudo (nmcli needs root for hotspot)."
  exit 1
fi

if ! command -v nmcli >/dev/null 2>&1; then
  echo "nmcli not found. Enable NetworkManager on Pi OS, or configure hostapd + dnsmasq yourself."
  exit 1
fi

nmcli connection delete Dawggles-ap 2>/dev/null || true
nmcli device wifi hotspot \
  ifname "$IFACE" \
  ssid "$SSID" \
  password "$PASS" \
  con-name Dawggles-ap \
  band bg

IP=""
IP=$(ip -4 -o addr show dev "$IFACE" 2>/dev/null | awk '{print $4}' | head -1 | cut -d/ -f1) || true

echo ""
echo "Hotspot up: SSID=$SSID  interface=$IFACE"
echo "Pi IPv4 on this interface: ${IP:-run: ip -4 addr show dev $IFACE}"
echo ""
echo "Join that Wi‑Fi from phone/Mac, then BLE pair and TCP as before."
echo "Recommended for main.py:"
echo "  export DAWGGLES_AP_INTERFACE=$IFACE"
if [[ -n "${IP:-}" ]]; then
  echo "  export DAWGGLES_TCP_HOST=$IP"
fi
