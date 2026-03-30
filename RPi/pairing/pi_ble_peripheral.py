"""
Pi: BLE GATT — Mac writes password (UTF-8), reads JSON {"t": token, "h": lan_ip}.

  export DAWGGLES_PAIR_PASSWORD='...'
  pip install -r requirements.txt

  Dawggles Wi‑Fi hotspot (see network/setup_dawggles_hotspot.sh), then either:
    export DAWGGLES_AP_INTERFACE=wlan0
  or pin the AP IP:
    export DAWGGLES_TCP_HOST=10.42.0.1
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import socket
import struct
import threading
from typing import Any

from bless import BlessServer
from bless import GATTAttributePermissions
from bless import GATTCharacteristicProperties

log = logging.getLogger("dawggles.ble")

SERVICE_UUID = "0000d100-0000-1000-8000-00805f9b34fb"
PAIR_CHAR_UUID = "0000d101-0000-1000-8000-00805f9b34fb"
ADVERTISE_NAME = "Dawggles"
_MAX_PASSWORD_BYTES = 64


def primary_lan_ipv4() -> str:
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        if ip.startswith("127."):
            return "?"
        return ip
    except Exception:
        return "?"
    finally:
        if s is not None:
            try:
                s.close()
            except OSError:
                pass


def _ipv4_for_interface_linux(ifname: str) -> str | None:
    """Best-effort Linux SIOCGIFADDR (Pi AP / wlan0). Returns None if unavailable."""
    if not ifname or len(ifname) >= 256:
        return None
    try:
        import fcntl
    except ImportError:
        return None
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        packed = fcntl.ioctl(
            s.fileno(),
            0x8915,  # SIOCGIFADDR
            struct.pack("256s", ifname.encode("utf-8")[:15]),
        )
        ip = socket.inet_ntoa(packed[20:24])
        if ip.startswith("127."):
            return None
        return ip
    except OSError:
        return None
    finally:
        s.close()


def tcp_bind_ipv4() -> str:
    """
    Address advertised over BLE and used to bind TCP.

    Env (Pi):
      DAWGGLES_TCP_HOST=192.168.4.1   — fixed IP (e.g. your AP address)
      DAWGGLES_AP_INTERFACE=wlan0     — use this interface's IPv4 (hotspot/AP)
    If unset, falls back to primary_lan_ipv4() (default route trick).
    """
    host = os.environ.get("DAWGGLES_TCP_HOST", "").strip()
    if host and host != "?" and not host.startswith("127."):
        return host
    iface = os.environ.get("DAWGGLES_AP_INTERFACE", "").strip()
    if iface:
        ip = _ipv4_for_interface_linux(iface)
        if ip:
            return ip
    return primary_lan_ipv4()


def _expected_password_bytes() -> bytes:
    # We no longer use a static BLE password because Wi-Fi WPA2 provides the security.
    # The BLE server just waits for an empty knock from the phone.
    return b""

def run_ble_pairing_background(shared: Any) -> None:
    def runner() -> None:
        try:
            asyncio.run(_async_main(shared))
        except Exception as e:
            log.exception("BLE thread: %s", e)

    threading.Thread(target=runner, name="DawgglesBLE", daemon=True).start()

def validate_pairing_environment() -> None:
    pass

async def _async_main(shared: Any) -> None:
    value = bytearray(b"WAIT")

    def read_request(characteristic: Any, **kwargs: Any) -> bytearray:
        return characteristic.value

    def write_request(characteristic: Any, data: bytearray, **kwargs: Any) -> bytearray:
        nonlocal value
        
        # When ANY BLE connection / write is attempted, wake up the OLED to show the PIN!
        shared.display.update_display({"status": "pairing_pin"})
        log.info("ble: Received knock from phone! Waking up OLED to show Wi-Fi PIN.")
        
        ip = tcp_bind_ipv4()
        if ip == "?":
            value = bytearray(b"NOIP")
            characteristic.value = value
            log.warning("ble: no LAN IPv4")
            return characteristic.value
            
        # SUCCESS! Send back IP address so the phone knows where to connect the TCP socket.
        payload = json.dumps({"h": ip}, separators=(",", ":")).encode("ascii")
        value = bytearray(payload)
        characteristic.value = value
        
        return characteristic.value

    loop = asyncio.get_running_loop()
    server = BlessServer(name=ADVERTISE_NAME, loop=loop)
    server.read_request_func = read_request
    server.write_request_func = write_request

    props = GATTCharacteristicProperties.read | GATTCharacteristicProperties.write
    perms = GATTAttributePermissions.readable | GATTAttributePermissions.writeable

    await server.add_new_service(SERVICE_UUID)
    await server.add_new_characteristic(
        SERVICE_UUID,
        PAIR_CHAR_UUID,
        props,
        bytearray(b"WAIT"),
        perms,
    )
    try:
        await server.start()
    except Exception as e:
        _advertise_failed_hint(e)
        raise
    log.info("ble advertising %s", ADVERTISE_NAME)
    await asyncio.Event().wait()


def _advertise_failed_hint(err: Exception) -> None:
    log.error("BLE advertisement failed: %s", err)
    print(
        "\n*** BLE: failed to register advertisement (BlueZ) ***\n"
        "  1) sudo bluetoothctl power on\n"
        "  2) sudo rfkill unblock bluetooth\n"
        "  3) sudo systemctl restart bluetooth\n"
        "  4) Unplug other BLE tools using the adapter; only one advertiser at a time.\n"
        "  5) Retry; if still failing, test once with root (keeps your env):\n"
        "       sudo -E $(which python3) main.py\n"
        "     Or add user to group: sudo usermod -aG bluetooth $USER  (then log out/in)\n",
        flush=True,
    )
