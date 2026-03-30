"""
Pi: BLE GATT — Mac writes password (UTF-8), reads JSON {"t": token, "h": lan_ip}.

  export DAWGGLES_PAIR_PASSWORD='...'
  pip install -r requirements.txt
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import socket
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


def _expected_password_bytes() -> bytes:
    pw = os.environ.get("DAWGGLES_PAIR_PASSWORD")
    if pw is not None:
        raw = pw.encode("utf-8").strip()
        if len(raw) > _MAX_PASSWORD_BYTES:
            raise ValueError(
                f"DAWGGLES_PAIR_PASSWORD must be <= {_MAX_PASSWORD_BYTES} UTF-8 bytes"
            )
        if not raw:
            raise ValueError("DAWGGLES_PAIR_PASSWORD is empty")
        return raw
    pin = os.environ.get("DAWGGLES_PAIR_PIN", "000000").strip()[:6]
    if len(pin) < 6:
        pin = pin.ljust(6, "0")
    b = pin.encode("utf-8")
    if b == b"000000":
        log.warning("using default PIN 000000 — set DAWGGLES_PAIR_PASSWORD for production")
    return b


def run_ble_pairing_background(shared: Any) -> None:
    def runner() -> None:
        try:
            asyncio.run(_async_main(shared))
        except Exception as e:
            log.exception("BLE thread: %s", e)

    threading.Thread(target=runner, name="DawgglesBLE", daemon=True).start()


def validate_pairing_environment() -> None:
    _expected_password_bytes()


async def _async_main(shared: Any) -> None:
    expected = _expected_password_bytes()
    value = bytearray(b"WAIT")

    def read_request(characteristic: Any, **kwargs: Any) -> bytearray:
        return characteristic.value

    def write_request(characteristic: Any, data: bytearray, **kwargs: Any) -> bytearray:
        nonlocal value
        got = bytes(data).strip()
        if len(got) > _MAX_PASSWORD_BYTES:
            value = bytearray(b"BADPIN")
            characteristic.value = value
            log.warning("ble: password too long")
            return characteristic.value
        if got == expected:
            ip = primary_lan_ipv4()
            if ip == "?":
                value = bytearray(b"NOIP")
                characteristic.value = value
                log.warning("ble: no LAN IPv4")
                return characteristic.value
            token = secrets.token_hex(16)
            payload = json.dumps({"t": token, "h": ip}, separators=(",", ":")).encode(
                "ascii"
            )
            value = bytearray(payload)
            characteristic.value = value
            shared.tcp_auth_token = token
            if not shared.tcp_bind_ready.is_set():
                shared.paired_tcp_host = ip
                shared.tcp_bind_ready.set()
            log.info("ble paired tcp=%s:12345", ip)
        else:
            value = bytearray(b"BADPIN")
            characteristic.value = value
            log.warning("ble: wrong password")
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
