#!/usr/bin/env python3
"""
Duplex TCP client for the Pi (same framing as RPi/goggles_lib.Server).

  export DAWGGLES_TCP_AUTH_TOKEN='...'   # after Mac/ble_pair.py
  python3 Mac/push_client.py HOST

  --port 12345  --save-dir ./dawggles_incoming  --verbose

Wire: 4-byte big-endian length + UTF-8 JSON object. Stdin: one JSON object per line; empty line exits.
Background thread receives Pi→Mac frames (JPEGs saved under --save-dir).

Trigger a capture on the Pi (translation app, idle):
  {"dawggles_shutter":true}
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import select
import socket
import struct
import sys
import threading
import time

MAX_BODY = 32 * 1024 * 1024


def send_framed_json(sock: socket.socket, obj: dict) -> None:
    body = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_BODY:
        raise SystemExit("frame too large")
    sock.sendall(struct.pack("!I", len(body)) + body)


def recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("closed")
        buf += chunk
    return bytes(buf)


def save_jpeg_if_any(msg: dict, save_dir: str) -> str | None:
    if msg.get("event") != "picture" or msg.get("format", "jpeg") != "jpeg":
        return None
    b64 = msg.get("image_b64")
    if not b64:
        return None
    raw = base64.standard_b64decode(b64)
    os.makedirs(save_dir, exist_ok=True)
    path = os.path.join(save_dir, f"picture_{int(time.time() * 1000)}.jpg")
    with open(path, "wb") as f:
        f.write(raw)
    return path


def recv_loop(
    sock: socket.socket, save_dir: str, stop: threading.Event, verbose: bool
) -> None:
    while not stop.is_set():
        readable, _, _ = select.select([sock], [], [], 0.5)
        if stop.is_set() or not readable:
            continue
        try:
            hdr = recv_exact(sock, 4)
            (length,) = struct.unpack("!I", hdr)
            if length == 0 or length > MAX_BODY:
                print("push_client: bad length", length, file=sys.stderr)
                break
            raw = recv_exact(sock, length)
            msg = json.loads(raw.decode("utf-8"))
        except (ConnectionError, OSError):
            break
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as e:
            print("push_client:", e, file=sys.stderr)
            break

        if not isinstance(msg, dict):
            continue
        path = save_jpeg_if_any(msg, save_dir)
        if path:
            print(path)
            continue
        if verbose:
            line = json.dumps(msg, separators=(",", ":"))
            if len(line) > 500:
                line = line[:500] + "…"
            print(line, file=sys.stderr)


def stdin_loop(sock: socket.socket) -> None:
    while True:
        try:
            line = input()
        except EOFError:
            break
        if not line.strip():
            break
        send_framed_json(sock, json.loads(line))


def main() -> None:
    p = argparse.ArgumentParser(description="Duplex framed JSON to Dawggles Pi.")
    p.add_argument("host")
    p.add_argument("--port", type=int, default=12345)
    p.add_argument("--auth", default=None, help="or DAWGGLES_TCP_AUTH_TOKEN")
    p.add_argument("--save-dir", default="dawggles_incoming")
    p.add_argument(
        "-v", "--verbose", action="store_true", help="log non-JPEG JSON to stderr"
    )
    args = p.parse_args()

    token = args.auth or os.environ.get("DAWGGLES_TCP_AUTH_TOKEN")
    sock = socket.create_connection((args.host, args.port))
    if token:
        send_framed_json(sock, {"auth_token": token})

    stop = threading.Event()
    t = threading.Thread(
        target=recv_loop,
        args=(sock, args.save_dir, stop, args.verbose),
        daemon=True,
    )
    t.start()
    try:
        stdin_loop(sock)
    finally:
        stop.set()
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
