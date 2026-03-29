#!/usr/bin/env python3
"""
Act like the phone: connect to Dawggles TCP server, send framed JSON, read framed JSON.

  Terminal 1:  python3 dev_server.py
  Terminal 2:  python3 test_phone_client.py
  Terminal 2:  python3 test_phone_client.py 192.168.1.50 12345   # Pi on LAN

Protocol matches goggles_lib.Server: 4-byte big-endian length + UTF-8 JSON object.
"""
import json
import socket
import struct
import sys

MAX_BODY = 32 * 1024 * 1024


def recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("server closed connection while reading")
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
        raise ValueError("expected JSON object")
    return obj


def send_framed_json(sock: socket.socket, obj: dict) -> None:
    body = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_BODY:
        raise ValueError("payload too large")
    sock.sendall(struct.pack("!I", len(body)) + body)


def main() -> None:
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 12345

    with socket.create_connection((host, port)) as sock:
        print(f"Connected to {host}:{port}")

        # 1) Ask server to send one message back (Pi -> phone)
        send_framed_json(sock, {"_dawggles_ping": True})
        reply = recv_framed_json(sock)
        print("Pi -> phone:", reply)

        # 2) Normal app message (phone -> Pi) — same path you tested before
        send_framed_json(sock, {"data": "from fake phone"})
        # Server does not auto-reply to this; optional read if you add echoes later
        print('Sent {"data": "from fake phone"} (check server terminal for logs).')

        print("Listening for more Pi -> phone frames (Ctrl+C to exit)...")
        while True:
            msg = recv_framed_json(sock)
            print("Pi -> phone:", msg)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nbye")
