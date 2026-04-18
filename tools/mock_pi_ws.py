#!/usr/bin/env python3
"""
Mock Raspberry Pi WebSocket for Dawggles iOS (local testing without hardware).

  pip install websockets
  pip install opencv-python    # only if you use --video / DAWGGLES_MOCK_VIDEO

  python3 tools/mock_pi_ws.py
  python3 tools/mock_pi_ws.py --video ~/Movies/clip.MOV
  DAWGGLES_MOCK_VIDEO=/path/to/file.MOV python3 tools/mock_pi_ws.py

Xcode (Debug) — Scheme → Run → Arguments → Environment Variables:

  DAWGGLES_MOCK_PI = 1
  DAWGGLES_MOCK_PI_HOST = 127.0.0.1

Use ``127.0.0.1`` for **Simulator** (mock server on the same Mac).
On a **physical iPhone**, use your Mac’s Wi‑Fi IP and ensure the phone can reach it (firewall may need to allow port 8765).

**Video mode:** first decoded frame is sent as ``picture`` JSON (OCR reference); then binary JPEG frames at ``--fps`` (loops the file). If OpenCV cannot open your ``.MOV``, re-encode: ``ffmpeg -i in.MOV -c:v libx264 -pix_fmt yuv420p out.mp4``

**Synthetic mode (default):** sends a test-pattern JPEG, then loops the same frame.

Incoming JSON from the phone is printed (OCR/groupings reply, ``focus``, etc.).
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
from dataclasses import dataclass

try:
    import websockets
except ImportError:
    print("Install: pip install websockets", file=sys.stderr)
    sys.exit(1)


# Tiny valid JPEG (1×1) if Pillow is unavailable
_FALLBACK_JPEG_B64 = "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAv/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFQEBAQAAAAAAAAAAAAAAAAAAAAX/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIRAxEAPwCwAA8A/9k="


def _jpeg_bytes_synthetic() -> bytes:
    try:
        import io
        from PIL import Image

        buf = io.BytesIO()
        # Visible test pattern (not near-white)
        im = Image.new("RGB", (320, 200), (40, 40, 55))
        for x in range(0, 320, 40):
            for y in range(0, 200, 40):
                im.paste((180, 120, 60), (x, y, x + 20, y + 20))
        im.save(buf, format="JPEG", quality=80)
        return buf.getvalue()
    except ImportError:
        return base64.standard_b64decode(_FALLBACK_JPEG_B64)


def _picture_json_from_jpeg(jpeg_bytes: bytes) -> str:
    return json.dumps(
        {
            "app": "translation",
            "event": "picture",
            "format": "jpeg",
            "image_b64": base64.standard_b64encode(jpeg_bytes).decode("ascii"),
            "byte_length": len(jpeg_bytes),
        },
        separators=(",", ":"),
    )


@dataclass
class ServeConfig:
    port: int
    video_path: str | None
    fps: float
    max_width: int
    synthetic_jpeg: bytes
    synthetic_picture_json: str


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Mock Pi WebSocket for Dawggles iOS")
    p.add_argument(
        "--video",
        "-v",
        metavar="PATH",
        help="Video file (.mov, .mp4, …). Overrides DAWGGLES_MOCK_VIDEO / MOCK_PI_VIDEO.",
    )
    p.add_argument("--fps", type=float, default=8.0, help="Video stream target FPS (default 8)")
    p.add_argument(
        "--max-width",
        type=int,
        default=720,
        help="Scale frames so width <= this (0 = no resize). Default 720.",
    )
    p.add_argument("--port", type=int, default=8765, help="Listen port (default 8765)")
    return p.parse_args()


def _resolve_video_path(args: argparse.Namespace) -> str | None:
    raw = args.video or os.environ.get("DAWGGLES_MOCK_VIDEO") or os.environ.get("MOCK_PI_VIDEO")
    if not raw:
        return None
    path = os.path.abspath(os.path.expanduser(raw.strip()))
    if not os.path.isfile(path):
        print(f"mock_pi_ws: not a file: {path}", file=sys.stderr)
        sys.exit(1)
    return path


def build_config(args: argparse.Namespace) -> ServeConfig:
    video_path = _resolve_video_path(args)
    syn = _jpeg_bytes_synthetic()
    return ServeConfig(
        port=args.port,
        video_path=video_path,
        fps=max(0.5, float(args.fps)),
        max_width=int(args.max_width),
        synthetic_jpeg=syn,
        synthetic_picture_json=_picture_json_from_jpeg(syn),
    )


async def _drain_client_messages(ws: websockets.ServerConnection) -> None:
    async for message in ws:
        if isinstance(message, bytes):
            print("ignoring binary from client", len(message))
            continue
        try:
            obj = json.loads(message)
            ev = obj.get("event", "")
            print("from phone:", ev or obj.get("app"), json.dumps(obj)[:500])
        except json.JSONDecodeError:
            print("from phone (raw):", message[:300])


async def _pump_synthetic(ws: websockets.ServerConnection, jpeg: bytes, interval: float) -> None:
    try:
        while True:
            await ws.send(jpeg)
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        print("pump (synthetic):", e)


def _video_encode_jpeg(frame, quality: int = 80) -> bytes:
    import cv2

    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise RuntimeError("jpeg encode failed")
    return buf.tobytes()


def _video_maybe_resize(frame, max_width: int):
    import cv2

    if max_width <= 0:
        return frame
    h, w = frame.shape[:2]
    if w <= max_width:
        return frame
    scale = max_width / float(w)
    return cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)


async def _pump_video(ws: websockets.ServerConnection, config: ServeConfig) -> None:
    import cv2

    path = config.video_path
    assert path is not None
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        print(f"mock_pi_ws: cannot open video: {path}")
        print("Try: ffmpeg -i input.MOV -c:v libx264 -pix_fmt yuv420p /tmp/out.mp4", file=sys.stderr)
        return

    interval = 1.0 / config.fps
    try:
        ok, frame = cap.read()
        if not ok:
            return
        frame = _video_maybe_resize(frame, config.max_width)
        j0 = _video_encode_jpeg(frame)
        await ws.send(_picture_json_from_jpeg(j0))

        while True:
            ok, frame = cap.read()
            if not ok:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, frame = cap.read()
                if not ok:
                    break
            frame = _video_maybe_resize(frame, config.max_width)
            await ws.send(_video_encode_jpeg(frame))
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        print("pump (video):", e)
    finally:
        cap.release()


async def _client_handler(ws: websockets.ServerConnection, config: ServeConfig) -> None:
    path = getattr(ws, "path", "/")
    print(f"client connected {ws.remote_address} path={path}")
    pump: asyncio.Task | None = None
    try:
        if config.video_path:
            try:
                import cv2  # noqa: F401
            except ImportError:
                print("mock_pi_ws: video mode needs: pip install opencv-python", file=sys.stderr)
                return
            pump = asyncio.create_task(_pump_video(ws, config))
        else:
            try:
                await ws.send(config.synthetic_picture_json)
            except Exception as e:
                print("send picture failed:", e)
                return
            pump = asyncio.create_task(_pump_synthetic(ws, config.synthetic_jpeg, 0.12))

        await _drain_client_messages(ws)
    finally:
        if pump is not None:
            pump.cancel()
            try:
                await pump
            except asyncio.CancelledError:
                pass
    print("client disconnected")


async def _main_async(config: ServeConfig) -> None:
    host = "0.0.0.0"

    async def handler(ws: websockets.ServerConnection):
        await _client_handler(ws, config)

    async with websockets.serve(handler, host, config.port, max_size=32 * 1024 * 1024):
        if config.video_path:
            print(
                f"mock Pi WebSocket ws://{host}:{config.port}/  VIDEO={config.video_path} fps={config.fps} max_width={config.max_width or 'full'}"
            )
        else:
            print(f"mock Pi WebSocket ws://{host}:{config.port}/  (synthetic JPEG)")
        print("Connect from app with DAWGGLES_MOCK_PI=1")
        await asyncio.Future()


def main() -> None:
    args = _parse_args()
    config = build_config(args)
    asyncio.run(_main_async(config))


if __name__ == "__main__":
    main()
