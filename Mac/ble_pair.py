#!/usr/bin/env python3
"""
BLE pairing: same password as DAWGGLES_PAIR_PASSWORD on the Pi → prints token + push_client command.

  pip install -r Mac/requirements.txt
  python3 Mac/ble_pair.py
  python3 Mac/ble_pair.py --scan                    # list devices (Pi often shows as None or raspberrypi)
  python3 Mac/ble_pair.py AA:BB:CC:DD:EE:FF        # connect by address from --scan
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys

from bleak import BleakClient, BleakScanner

PAIR_CHAR_UUID = "0000d101-0000-1000-8000-00805f9b34fb"
NAME_SUBSTRING = "Dawggles"
_MAX_PASSWORD_BYTES = 64


def _password_bytes() -> bytes:
    env = os.environ.get("DAWGGLES_PAIR_PASSWORD")
    if env is not None:
        raw = env.encode("utf-8").strip()
        if not raw or len(raw) > _MAX_PASSWORD_BYTES:
            raise SystemExit("bad DAWGGLES_PAIR_PASSWORD")
        return raw
    pin = os.environ.get("DAWGGLES_PAIR_PIN")
    if pin is not None:
        p = pin.strip()[:6].ljust(6, "0")
        return p.encode("utf-8")
    pw = getpass.getpass("Pi password: ")
    raw = pw.encode("utf-8").strip()
    if not raw or len(raw) > _MAX_PASSWORD_BYTES:
        raise SystemExit("empty or too long")
    return raw


def _parse_read_payload(raw: str) -> tuple[str | None, str | None]:
    token, host = None, None
    if raw and raw not in ("WAIT", "BADPIN", "NOIP"):
        try:
            o = json.loads(raw)
            if isinstance(o, dict):
                token, host = o.get("t"), o.get("h")
        except json.JSONDecodeError:
            pass
    if token is None and raw not in ("WAIT", "BADPIN", "NOIP", "") and raw:
        token, host = raw, None
    return token, host


def _normalize_address(addr: str) -> str:
    return addr.strip().replace("-", ":").upper()


async def _pair(address: str, pw: bytes) -> None:
    async with BleakClient(address) as client:
        try:
            await client.exchange_mtu(247)
        except Exception:
            pass
        await client.write_gatt_char(PAIR_CHAR_UUID, pw, response=True)
        await asyncio.sleep(0.35)
        data = await client.read_gatt_char(PAIR_CHAR_UUID)
    raw = bytes(data).decode("utf-8", errors="replace").strip()
    token, host = _parse_read_payload(raw)

    if raw == "NOIP":
        raise SystemExit("Pi has no LAN IP yet")
    if not token:
        raise SystemExit(f"pairing failed ({raw!r})")

    host = host or "<PI_IP>"
    print(f"export DAWGGLES_TCP_AUTH_TOKEN='{token}'")
    print(f"python3 Mac/push_client.py {host}")


async def _discover_devices(timeout: float):
    """Unfiltered scan — service UUID filters often return nothing on macOS."""
    devices = await BleakScanner.discover(timeout=timeout)
    if not devices:
        await asyncio.sleep(0.5)
        devices = await BleakScanner.discover(timeout=timeout)
    return devices


async def _scan(timeout: float) -> None:
    print(f"Scanning {timeout}s — unfiltered (all nearby LE devices)...\n")
    devices = await _discover_devices(timeout)
    if not devices:
        print(
            "No BLE devices at all.\n"
            "  • macOS: System Settings → Privacy & Security → Bluetooth\n"
            "    Turn ON for Terminal.app (or iTerm). If you run from Cursor, add Cursor too.\n"
            "  • System Settings → Bluetooth: On.\n"
            "  • Pi: Bluetooth up, python3 main.py running, pip install -r requirements.txt (bless).\n"
        )
        return
    for d in sorted(devices, key=lambda x: (x.name or "", x.address)):
        name = d.name if d.name else "(no name)"
        print(f"  {d.address}  {name}")
    print(
        "\nThe Pi often does NOT show the name 'Dawggles' — look for (no name) or 'raspberrypi', "
        "or match the Bluetooth MAC from the Pi (hciconfig / bluetoothctl list).\n"
        "Then: python3 Mac/ble_pair.py <ADDRESS>"
    )


async def _main_async(args: argparse.Namespace) -> None:
    if args.scan:
        await _scan(args.timeout)
        return

    pw = _password_bytes()
    address = args.address

    if not address:
        devices = await _discover_devices(args.timeout)
        found = next(
            (
                d
                for d in devices
                if d.name and NAME_SUBSTRING.lower() in d.name.lower()
            ),
            None,
        )
        if found is None:
            print(
                "No device whose name contains 'Dawggles' "
                f"(saw {len(devices)} other device(s)).\n"
                "Run: python3 Mac/ble_pair.py --scan\n"
                "Then: python3 Mac/ble_pair.py <ADDRESS>",
                file=sys.stderr,
            )
            raise SystemExit(1)
        address = found.address

    await _pair(_normalize_address(address), pw)


def main() -> None:
    p = argparse.ArgumentParser(description="BLE pair with Dawggles Pi")
    p.add_argument(
        "address",
        nargs="?",
        default=None,
        help="BLE address from --scan (e.g. B8:27:EB:…)",
    )
    p.add_argument(
        "--scan",
        action="store_true",
        help="List nearby devices and exit",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="Scan duration (seconds)",
    )
    args = p.parse_args()
    asyncio.run(_main_async(args))


if __name__ == "__main__":
    main()
