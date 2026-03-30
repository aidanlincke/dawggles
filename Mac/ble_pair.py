#!/usr/bin/env python3
"""
BLE pairing: same password as DAWGGLES_PAIR_PASSWORD on the Pi → prints token + push_client command.

  pip install -r Mac/requirements.txt
  python3 Mac/ble_pair.py
"""
from __future__ import annotations

import asyncio
import getpass
import json
import os

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


async def _main() -> None:
    pw = _password_bytes()
    devices = await BleakScanner.discover(timeout=20.0)
    found = next((d for d in devices if d.name and NAME_SUBSTRING in d.name), None)
    if found is None:
        raise SystemExit("no Dawggles device found")

    async with BleakClient(found.address) as client:
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


if __name__ == "__main__":
    asyncio.run(_main())
