#!/bin/bash
# Pi: stop Dawggles hotspot (NetworkManager) and join CMU-DEVICE as a client.
#
#   sudo ./network/restore_cmu_wifi.sh
#
# Optional (if CMU-DEVICE requires a key):
#   export CMU_WIFI_PASSWORD='...'
#   sudo -E ./network/restore_cmu_wifi.sh
#
# Optional: DELETE_HOTSPOT_PROFILE=1  — also remove NM profiles Dawggles-ap / Dawggles
#
# After this, unset on the Pi if you used AP mode:
#   unset DAWGGLES_AP_INTERFACE DAWGGLES_TCP_HOST

set -euo pipefail

SSID="CMU-DEVICE"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run with sudo."
  exit 1
fi

if ! command -v nmcli >/dev/null 2>&1; then
  echo "nmcli not found."
  exit 1
fi

IFACE="${DAWGGLES_AP_IFACE:-wlan0}"
PASS="${CMU_WIFI_PASSWORD-}"

echo "Bringing down hotspot on $IFACE (if active)..."
nmcli connection down Dawggles-ap 2>/dev/null || true
nmcli connection down Dawggles 2>/dev/null || true
nmcli device disconnect "$IFACE" 2>/dev/null || true

if [[ "${DELETE_HOTSPOT_PROFILE:-}" == "1" ]]; then
  echo "Deleting hotspot connection profiles..."
  nmcli connection delete Dawggles-ap 2>/dev/null || true
  nmcli connection delete Dawggles 2>/dev/null || true
fi

echo "Scanning and connecting to $SSID ..."
nmcli device wifi rescan
sleep 2
if [[ -n "${PASS}" ]]; then
  nmcli device wifi connect "$SSID" password "$PASS" ifname "$IFACE"
else
  echo "(open network — no password)"
  nmcli device wifi connect "$SSID" ifname "$IFACE"
fi

echo ""
echo "Done. Check: ip -4 addr show dev $IFACE"
echo "For main.py: unset DAWGGLES_AP_INTERFACE DAWGGLES_TCP_HOST (or omit them)."
