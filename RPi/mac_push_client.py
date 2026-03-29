#!/usr/bin/env python3
"""
Run on your Mac. Framed JSON over TCP (same as goggles_lib.Server).

Pi → Mac (photos from goggles after capture):
  python3 mac_push_client.py HOST listen
  Pi: python3 main.py   then press shutter — JPEGs land in ./dawggles_incoming/

Mac → Pi anytime (Pi never asks first):
  python3 mac_push_client.py HOST json '{"data":"hi"}'
  python3 mac_push_client.py HOST file ./photo.jpg          # Pi writes under /tmp/
  python3 mac_push_client.py HOST repl

Both directions at once:
  python3 mac_push_client.py HOST duplex

Protocol: 4-byte big-endian length + UTF-8 JSON object.
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
        raise SystemExit(
            f"Frame too large ({len(body)} bytes). Use a smaller file or bump MAX on Pi."
        )
    sock.sendall(struct.pack("!I", len(body)) + body)


def recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("peer closed connection while reading")
        buf += chunk
    return bytes(buf)


def recv_framed_json(sock: socket.socket) -> dict:
    hdr = recv_exact(sock, 4)
    (length,) = struct.unpack("!I", hdr)
    if length == 0 or length > MAX_BODY:
        raise ValueError(f"bad frame length: {length}")
    raw = recv_exact(sock, length)
    obj = json.loads(raw.decode("utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("top-level JSON must be an object")
    return obj


def _save_jpeg_from_message(msg: dict, save_dir: str) -> str | None:
    """If message carries a JPEG (Pi capture path), write file and return path."""
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


def recv_loop(sock: socket.socket, save_dir: str, stop: threading.Event) -> None:
    """Wait for full frames only after select says data is available (avoids partial-read desync)."""
    while not stop.is_set():
        readable, _, _ = select.select([sock], [], [], 0.5)
        if stop.is_set():
            break
        if not readable:
            continue
        try:
            hdr = recv_exact(sock, 4)
            (length,) = struct.unpack("!I", hdr)
            if length == 0 or length > MAX_BODY:
                print(f"[recv] bad length {length}", file=sys.stderr)
                break
            raw = recv_exact(sock, length)
            msg = json.loads(raw.decode("utf-8"))
        except ConnectionError:
            break
        except (json.JSONDecodeError, ValueError, UnicodeDecodeError, OSError) as e:
            if not stop.is_set():
                print(f"[recv] {e}", file=sys.stderr)
            break

        if not isinstance(msg, dict):
            continue
        saved = _save_jpeg_from_message(msg, save_dir)
        if saved:
            print(f"[recv] saved JPEG -> {saved}")
            continue
        if msg.get("event") == "picture_ready":
            print("[recv] Pi: picture_ready (no image bytes in this message)")
            continue
        line = json.dumps(msg, indent=None)
        if len(line) > 400:
            line = line[:400] + "..."
        print(f"[recv] {line}")


def cmd_repl(sock: socket.socket) -> None:
    print("Enter one JSON object per line; empty line = quit.")
    while True:
        try:
            line = input()
        except EOFError:
            break
        if not line.strip():
            break
        send_framed_json(sock, json.loads(line))
        print("  sent")


def cmd_listen(sock: socket.socket, save_dir: str) -> None:
    """Pi → Mac only: stay connected and save incoming JPEGs (no typing to Pi)."""
    stop = threading.Event()

    def run() -> None:
        recv_loop(sock, save_dir, stop)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    print(
        "Pi → Mac: waiting for frames; JPEGs →",
        os.path.abspath(save_dir),
        "\nOn Pi run main.py and capture. Ctrl+C to quit.",
    )
    try:
        while t.is_alive():
            t.join(timeout=0.4)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        t.join(timeout=2.0)


def cmd_duplex(sock: socket.socket, save_dir: str) -> None:
    stop = threading.Event()
    t = threading.Thread(
        target=recv_loop,
        args=(sock, save_dir, stop),
        daemon=True,
    )
    t.start()
    print(
        "Duplex: Pi→Mac messages print here; JPEGs ->",
        os.path.abspath(save_dir),
        "\nType JSON lines to send to Pi (empty line = quit).",
    )
    try:
        cmd_repl(sock)
    finally:
        stop.set()
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


def main() -> None:
    p = argparse.ArgumentParser(
        description="Mac <-> Dawggles Pi framed JSON (listen = Pi→Mac photos; duplex = both ways)."
    )
    p.add_argument("host", help="Hostname or IP, e.g. raspberrypi.local")
    p.add_argument("--port", type=int, default=12345)
    p.add_argument(
        "--save-dir",
        default="dawggles_incoming",
        help="For listen/duplex: where to save Pi JPEGs (default: ./dawggles_incoming)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser(
        "listen",
        help="Pi → Mac only: save incoming JPEGs (run this, then capture on Pi)",
    )
    sub.add_parser("repl", help="Push only: stdin lines as JSON")
    sub.add_parser(
        "duplex",
        help="Push + receive: stdin lines to Pi; Pi JPEGs saved in background",
    )
    jp = sub.add_parser("json", help="Send one JSON object then exit")
    jp.add_argument("payload", help='JSON string, e.g. \'{"data":"hi"}\'')
    fp = sub.add_parser("file", help="Send file as _dawggles_test_upload (Pi writes /tmp/)")
    fp.add_argument("path")
    args = p.parse_args()

    sock = socket.create_connection((args.host, args.port))
    try:
        if args.cmd == "listen":
            cmd_listen(sock, args.save_dir)
        elif args.cmd == "repl":
            cmd_repl(sock)
        elif args.cmd == "duplex":
            cmd_duplex(sock, args.save_dir)
        elif args.cmd == "json":
            send_framed_json(sock, json.loads(args.payload))
            print("sent.")
        elif args.cmd == "file":
            path = args.path
            with open(path, "rb") as f:
                data = f.read()
            b64 = base64.standard_b64encode(data).decode("ascii")
            send_framed_json(
                sock,
                {
                    "_dawggles_test_upload": True,
                    "bytes_b64": b64,
                    "save_as": os.path.basename(path),
                },
            )
            print(f"sent {len(data)} bytes ({path}).")
    finally:
        try:
            sock.close()
        except OSError:
            pass


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nbye", file=sys.stderr)
