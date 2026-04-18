import asyncio
import base64
import io
import json
import logging
import queue
import socket
import time
from threading import Event, Lock, Thread, Timer

import websockets

from gpiozero import Button as GPIOButton
from picamera2 import Picamera2

try:
    import board
    import busio
    import digitalio
    import adafruit_ssd1306
    OLED_AVAILABLE = True
except ImportError:
    OLED_AVAILABLE = False

import sys

log = logging.getLogger(__name__)

# Add RPi directory to path so adafruit_framebuf can find font5x8.bin
import os
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

MAX_JSON_MESSAGE_BYTES = 32 * 1024 * 1024

# -- Goggle Shared Class --
class SharedClass:
  def __init__(self):
    self.current_app = 'translation'  # Which app is currently active
    self.data = {}  # Generic data storage
    self.server = None   # Set by app_manager during initialize_system
    self.display = None  # Set by app_manager during initialize_system
    self.button = None   # App action button, set by app_manager during initialize_system
    self.cycle_button = None # App cycling button
    self.camera_client = None  # Set by app_manager during initialize_system
    self.shutter_event = Event()
    self.video_event = Event()
    self.display_lock = Lock()
    # When True, ``CameraClient`` sends low-res JPEG frames to the phone over WebSocket (binary).
    # Apps toggle this; do not confuse with ``video_event`` (used by ``video_loop``).
    self.phone_live_stream = False
    # Registered by ``app_manager`` so apps can reset state when the phone disconnects (no app imports here).
    self.websocket_disconnect_callback = None
    # Set by pairing after BLE knock; main() waits before binding TCP.
    self.paired_tcp_host = None  # str | None — Pi LAN IP to bind (never 0.0.0.0 in production)
    self.tcp_bind_ready = Event()
    # Set by Display.sleep()/wake(); GoggleButton consults it to gate input.
    self.display_sleeping = Event()

# -- WebSocket Server Class --
class WebSocketServer:
    """
    WebSocket server: one persistent client connection at a time, full duplex.
    Runs an asyncio event loop in a daemon thread; all other code stays threaded.
    Inbound: UTF-8 JSON text frames from the phone.
    Outbound: JSON text frames plus optional binary frames (raw JPEG preview bytes).
    """

    def __init__(self, shared_class, host="0.0.0.0", port=8765, message_handler=None):
        self.shared_class = shared_class
        self.message_handler = message_handler
        self._ws = None          # current websocket connection
        self._loop = asyncio.new_event_loop()
        self.message_queue = queue.Queue()
        self._listening_event = Event()
        self._startup_error = None

        self._worker_thread = Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

        t = Thread(target=self._run_loop, args=(host, port), daemon=True)
        t.start()

    @property
    def connected(self):
        return self._ws is not None

    @property
    def is_listening(self):
        return self._listening_event.is_set()

    @property
    def startup_error(self):
        return self._startup_error

    def _run_loop(self, host, port):
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve(host, port))
        except Exception as e:
            self._startup_error = e
            log.exception("websocket server failed to start: %s", e)

    async def _serve(self, host, port):
        # Tight keepalive so a dirty disconnect (phone drops the hotspot without
        # sending FIN, e.g. after unpair) is detected in ~5-8s instead of the
        # library default (~20-40s) that left the connection indicator stale.
        async with websockets.serve(
            self._handler,
            host,
            port,
            max_size=MAX_JSON_MESSAGE_BYTES,
            ping_interval=5,
            ping_timeout=3,
        ):
            self._listening_event.set()
            log.info("websocket server listening on %s:%s", host, port)
            await asyncio.Future()  # run forever

    async def _handler(self, ws):
        self._ws = ws
        log.info("websocket client connected: %s", ws.remote_address)
        try:
            async for raw in ws:
                if isinstance(raw, (bytes, bytearray)):
                    continue
                if not isinstance(raw, str):
                    continue
                try:
                    obj = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(obj, dict):
                    self.message_queue.put(obj)
        except websockets.ConnectionClosed:
            pass
        finally:
            if self._ws is ws:
                self._ws = None
            self.shared_class.phone_live_stream = False
            cb = self.shared_class.websocket_disconnect_callback
            if cb:
                try:
                    cb()
                except Exception as e:
                    log.warning("websocket_disconnect_callback: %s", e)
            log.info("websocket client disconnected")

    def send_json(self, obj):
        """Send a JSON message to the connected client (thread-safe). Returns False if no client."""
        ws = self._ws
        if ws is None:
            return False
        body = json.dumps(obj, separators=(",", ":"))
        if len(body.encode()) > MAX_JSON_MESSAGE_BYTES:
            raise ValueError("JSON message exceeds MAX_JSON_MESSAGE_BYTES")
        future = asyncio.run_coroutine_threadsafe(ws.send(body), self._loop)
        try:
            future.result(timeout=5)
            return True
        except Exception as e:
            log.warning("send_json: %s", e)
            return False

    def send_binary(self, data: bytes):
        """Send a binary frame (e.g. raw JPEG) to the connected client. Thread-safe."""
        ws = self._ws
        if ws is None:
            return False
        future = asyncio.run_coroutine_threadsafe(ws.send(data), self._loop)
        try:
            future.result(timeout=5)
            return True
        except Exception as e:
            log.warning("send_binary: %s", e)
            return False

    def _worker_loop(self):
        while True:
            try:
                message = self.message_queue.get()
                if self.message_handler:
                    try:
                        self.message_handler(message)
                    except Exception as e:
                        log.warning("message_handler error: %s", e)
                self.message_queue.task_done()
            except Exception as e:
                log.warning("worker_loop error: %s", e)

# -- Camera Client Class --
class CameraClient:
    def __init__(self, shared_class, config, camera=None):
        self.shared_class = shared_class
        self.camera = camera
        self.capture_thread = None
        self.running = False
        self._capture_lock = Lock()
        self._preview_thread = None
        self._preview_running = False
        self._has_lores_stream = False
        self.initialize_camera(config)

    def initialize_camera(self, config):
        if not self.camera:
            self.camera = Picamera2()

        still_config = None
        try:
            # PiCamera2 rotates 180 degrees by applying both horizontal and vertical flips.
            from libcamera import Transform
            still_config = self.camera.create_still_configuration(
                main=config,
                lores={"size": (640, 360)},
                transform=Transform(hflip=True, vflip=True),
            )
            self._has_lores_stream = True
            log.info("camera: applying 180-degree transform (hflip + vflip), lores preview640x360")
        except Exception as e:
            log.warning("camera: could not apply 180-degree transform, using default orientation: %s", e)
            try:
                still_config = self.camera.create_still_configuration(
                    main=config,
                    lores={"size": (640, 360)},
                )
                self._has_lores_stream = True
                log.info("camera: lores preview 640x360")
            except Exception as e2:
                log.warning("camera: no lores stream: %s", e2)
                still_config = self.camera.create_still_configuration(main=config)
                self._has_lores_stream = False

        self.camera.configure(still_config)
        self.camera.start()

        # Pi Camera 3 (IMX708): explicitly use the full sensor area for maximum FOV.
        # Without this picamera2 may default to a centred crop, narrowing the field of view.
        try:
            max_crop = self.camera.camera_properties.get('ScalerCropMaximum')
            if max_crop:
                self.camera.set_controls({'ScalerCrop': max_crop})
                log.info("camera: ScalerCrop set to full sensor area %s for maximum FOV", max_crop)
        except Exception as e:
            log.warning("camera: could not set ScalerCrop: %s", e)

    def start_capture_loop(self):
        if not self.camera:
            raise RuntimeError("Camera not initialized")
        if self.capture_thread and self.capture_thread.is_alive():
            return
        self.running = True
        self.shared_class.shutter_event.clear()
        self.capture_thread = Thread(target=self.capture_loop, daemon=True)
        self.capture_thread.start()

    def stop_capture_loop(self):
        self.running = False
        self._preview_running = False
        self.shared_class.shutter_event.set() # Wake up the thread so it can exit cleanly
        if self.capture_thread:
            self.capture_thread.join(timeout=1)

    def capture_loop(self):
        while self.running:
            self.shared_class.shutter_event.wait()
            if not self.running:
                break
            try:
                stream = io.BytesIO()
                with self._capture_lock:
                    # Rely on hardware to encode JPEG at optimal size
                    self.camera.capture_file(stream, format='jpeg')
                self.shared_class.data['picture'] = stream.getvalue()
                self.send_captured_jpeg_to_client()
                
                # Notify the app that capture completed
                import app_manager
                app_inst = app_manager.get_current_app()
                if app_inst and hasattr(app_inst, 'on_capture_complete'):
                    app_inst.on_capture_complete()

            except Exception as e:
                log.warning("camera: %s", e)
                import app_manager
                app_inst = app_manager.get_current_app()
                if app_inst and hasattr(app_inst, 'on_capture_complete'):
                    app_inst.on_capture_complete()
            finally:
                self.shared_class.shutter_event.clear()

    def send_captured_jpeg_to_client(self):
        """Push JPEG over TCP using shared.current_app — no app-specific names here."""
        srv = self.shared_class.server
        app = self.shared_class.current_app
        if srv is None:
            return
        pic = self.shared_class.data.get("picture")
        if not pic:
            return
        b64 = base64.standard_b64encode(pic).decode("ascii")
        payload = {
            "app": app,
            "event": "picture",
            "format": "jpeg",
            "image_b64": b64,
            "byte_length": len(pic),
            "source_bytes": len(pic),
        }
        try:
            if not srv.send_json(payload):
                log.warning("picture: send_json failed (no tcp client?)")
        except ValueError:
            log.warning("picture: payload too large to send")

    def video_loop(self):
        while self.running:
            self.shared_class.video_event.wait()
            if not self.running:
                break
            while self.shared_class.video_event.is_set() and self.running:
                pass
            self.send_video_to_client()

    def ensure_preview_thread(self):
        if self._preview_thread is not None and self._preview_thread.is_alive():
            return
        self._preview_running = True
        self._preview_thread = Thread(target=self._preview_loop, daemon=True)
        self._preview_thread.start()

    def _preview_loop(self):
        target_period = 1.0 / 12.0
        while self._preview_running:
            if not self.shared_class.phone_live_stream:
                time.sleep(0.05)
                continue
            srv = self.shared_class.server
            if srv is None or not srv.connected:
                time.sleep(0.05)
                continue
            try:
                stream = io.BytesIO()
                with self._capture_lock:
                    if self._has_lores_stream:
                        self.camera.capture_file(stream, format="jpeg", name="lores")
                    else:
                        self.camera.capture_file(stream, format="jpeg")
                jpeg = stream.getvalue()
                if jpeg and not srv.send_binary(jpeg):
                    log.debug("preview: send_binary skipped (no client)")
            except Exception as e:
                log.warning("preview: %s", e)
            time.sleep(target_period)

    def send_video_to_client(self):
        """Legacy hook — preview is driven by `_preview_loop` + ``SharedClass.phone_live_stream``."""
        pass

# -- Goggle Button Class --
class GoggleButton:
    def __init__(self, shared_class, pin, button_callback):
        # bounce_time=0.05 gives 50ms hardware debounce
        self.btn = GPIOButton(pin, bounce_time=0.05)
        self.shared_class = shared_class
        self.button_callback = button_callback
        
        self.click_count = 0
        self.click_timer = None
        self.timer_lock = Lock()

        self.btn.when_pressed = self._on_press

    def _on_press(self):
        with self.timer_lock:
            self.click_count += 1
            if self.click_timer is None:
                self.click_timer = Timer(0.4, self._process_clicks)
                self.click_timer.start()

    def _process_clicks(self):
        with self.timer_lock:
            count = self.click_count
            self.click_count = 0
            self.click_timer = None

        # Display asleep (toggled via PiSugar custom button) — swallow input so
        # we act as a true "screen off" with no side effects. App state is frozen
        # exactly where it was; wake restores both panel and button handling.
        if self.shared_class.display_sleeping.is_set():
            return

        if self.button_callback:
            self.button_callback(count)

    def update_callback(self, button_callback):
        """Update the button callback (useful when switching apps)"""
        self.button_callback = button_callback

# -- Display Class --
def _read_pisugar_battery() -> float | None:
    """Query the PiSugar server (port 8423) for battery percentage.
    Returns 0–100 float, or None if unavailable."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.0)
        s.connect(("127.0.0.1", 8423))
        s.sendall(b"get battery\n")
        response = s.recv(64).decode("utf-8", errors="ignore")
        s.close()
        # response is like "battery: 85.50\n"
        return max(0.0, min(100.0, float(response.split(":")[1].strip())))
    except Exception:
        return None


class Display:
    def __init__(self, shared_class):
        self.shared_class = shared_class
        self.display_data = {}
        self.display_lock = Lock()
        self.temp_message_timer = None
        self._last_connected = None
        self._battery_level = _read_pisugar_battery()
        # Connection indicator is coupled 1:1 to the title bar — it only shows
        # when draw_app_header() has drawn a header. Any full-screen redraw
        # that doesn't call draw_app_header() must clear this flag so the
        # status poll loop doesn't repaint an orphaned indicator.
        self._has_title_bar = False
        Thread(target=self._status_poll_loop, daemon=True).start()
        
        if not OLED_AVAILABLE:
            log.warning("OLED libraries (board, busio) not found. Display will print to terminal instead.")
            return

        # Initialize hardware OLED
        try:
            self.spi = busio.SPI(board.SCK, MOSI=board.MOSI)
            self.cs = digitalio.DigitalInOut(board.D17)
            self.dc = digitalio.DigitalInOut(board.D27)
            
            # 128x64 standard OLED. We must use adafruit_ssd1306.SSD1306_SPI
            # Provide font5x8.bin explicitly if needed, but it should fallback to internal.
            self.oled = adafruit_ssd1306.SSD1306_SPI(128, 64, self.spi, self.dc, None, self.cs)
            self.oled.contrast(5)
            self.oled.write_cmd(0xA0) # Seg remap
            self.oled.fill(0)
            self.oled.show()
            self.hardware_available = True
        except Exception as e:
            log.warning(f"OLED init failed, continuing without display: {e}")
            self.hardware_available = False
    
    def _status_poll_loop(self):
        """Refresh status bar icons whenever connection state or battery level changes."""
        battery_tick = 0
        while True:
            time.sleep(1)
            if not self.hardware_available or self.shared_class.server is None:
                continue
            if not self._has_title_bar:
                continue

            connected = self.shared_class.server.connected
            conn_changed = connected != self._last_connected
            if conn_changed:
                self._last_connected = connected

            batt_changed = False
            battery_tick += 1
            if battery_tick >= 30:
                battery_tick = 0
                new_level = _read_pisugar_battery()
                if new_level != self._battery_level:
                    self._battery_level = new_level
                    batt_changed = True

            if conn_changed or batt_changed:
                with self.display_lock:
                    # Clear the full status icon zone (wifi + battery), redraw, push
                    self.oled.fill_rect(105, 0, 23, 8, 0)
                    self._draw_status_bar()
                    self.oled.show()

    def _draw_battery_icon(self):
        """Battery icon at x=117–127, y=1–6. WiFi sits to its left.
        Body outline: x=117–124 (8px wide, 6px tall).
        Nub: x=125–127, y=2–5.
        Interior fill: x=118–123 (6px), y=2–5 — filled left-to-right by level."""
        o = self.oled
        pct = self._battery_level

        # Body outline
        o.rect(117, 1, 8, 6, 1)
        # Nub (positive terminal on the right)
        o.vline(125, 2, 4, 1)
        o.vline(126, 2, 4, 1)
        o.pixel(127, 3, 1)
        o.pixel(127, 4, 1)

        # Interior fill (6px wide, 4px tall)
        if pct is not None:
            fill_w = round(pct / 100 * 6)
            if fill_w > 0:
                o.fill_rect(118, 2, fill_w, 4, 1)

    def _draw_status_bar(self):
        """Status icons in top-right corner. Only drawn when a title bar is present.
        Layout: wifi (x=105–113) · gap (x=114–116) · battery (x=117–127)."""
        if not self.hardware_available or self.shared_class.server is None:
            return
        if not self._has_title_bar:
            return

        connected = self.shared_class.server.connected
        # WiFi icon at x=105–113 (9px wide), 3px gap before battery at x=117
        # Outer arc (y=0–2)
        self.oled.hline(107, 0, 5, 1)   # top: x=107-111
        self.oled.pixel(106, 1, 1)
        self.oled.pixel(112, 1, 1)
        self.oled.pixel(105, 2, 1)
        self.oled.pixel(113, 2, 1)
        # Middle arc (y=3–4)
        self.oled.hline(108, 3, 3, 1)   # x=108-110
        self.oled.pixel(107, 4, 1)
        self.oled.pixel(111, 4, 1)
        # Dot (y=6)
        self.oled.pixel(109, 6, 1)

        if not connected:
            self.oled.line(105, 0, 113, 7, 1)

        self._draw_battery_icon()

    def _render_text(self, lines: list, color: int = 1):
        if not self.hardware_available:
            return
        try:
            self.oled.fill(0)
            # Centered text screens (boot messages, temp banners) have no title bar.
            self._has_title_bar = False

            if isinstance(lines, str):
                lines = [lines]

            total_height = len(lines) * 10 # 8px font + 2px padding
            start_y = max(0, (self.oled.height - total_height) // 2)

            for i, line in enumerate(lines):
                tw = len(line) * 6
                tx = max(0, (self.oled.width - tw) // 2)
                ty = start_y + (i * 10)
                self.oled.text(line, tx, ty, color)

            self.oled.show()
        except OSError as e:
            if "No such file or directory: 'font5x8.bin'" in str(e):
                log.error("OLED font file missing! Please ensure font5x8.bin is in the current directory.")
            else:
                log.warning(f"OLED render failed: {e}")
        except Exception as e:
            log.warning(f"OLED render failed: {e}")

    def show_temporary_message(self, lines, duration=2.0):
        """Shows a message for a short duration, then reverts to normal display state."""
        with self.display_lock:
            if self.temp_message_timer:
                self.temp_message_timer.cancel()
            self._render_text(lines)
            
            def _clear():
                with self.display_lock:
                    self.temp_message_timer = None
                    self._render_current_state()
                    
            self.temp_message_timer = Timer(duration, _clear)
            self.temp_message_timer.start()

    def _render_current_state(self):
        """Renders the actual display state based on display_data."""
        if self.display_data.get("status") == "pairing_idle":
            self._render_text("PAIR IN APP")
        elif self.display_data.get("app"):
            import app_manager
            app_inst = app_manager.get_current_app()
            if app_inst and hasattr(app_inst, 'render_display'):
                app_inst.render_display(self)
            else:
                # Default app state is a blank screen so you can see through the goggles
                if self.hardware_available:
                    self.oled.fill(0)
                    self._has_title_bar = False
                    self.oled.show()

    def show_pairing_waiting(self):
        """Welcome screen shown while BLE is advertising."""
        with self.display_lock:
            self.display_data["status"] = "pairing_waiting"
            if self.temp_message_timer:
                self.temp_message_timer.cancel()
                self.temp_message_timer = None
            if not self.hardware_available:
                log.info("OLED [pairing]: waiting for iPhone")
                return
            try:
                self.oled.fill(0)
                self._has_title_bar = False

                # Rounded phone shell (left panel)
                px, py, pw, ph = 2, 8, 30, 48
                self.oled.hline(px + 3, py, pw - 6, 1)
                self.oled.hline(px + 3, py + ph - 1, pw - 6, 1)
                self.oled.vline(px, py + 3, ph - 6, 1)
                self.oled.vline(px + pw - 1, py + 3, ph - 6, 1)

                # Rounded corners
                self.oled.pixel(px + 1, py + 1, 1)
                self.oled.pixel(px + 2, py, 1)
                self.oled.pixel(px + pw - 2, py + 1, 1)
                self.oled.pixel(px + pw - 3, py, 1)
                self.oled.pixel(px + 1, py + ph - 2, 1)
                self.oled.pixel(px + 2, py + ph - 1, 1)
                self.oled.pixel(px + pw - 2, py + ph - 2, 1)
                self.oled.pixel(px + pw - 3, py + ph - 1, 1)

                # Edge-to-edge display + small notch (modern phone look)
                sx, sy, sw, sh = px + 3, py + 4, pw - 6, ph - 8
                self.oled.rect(sx, sy, sw, sh, 1)
                notch_w = 8
                notch_x = px + (pw - notch_w) // 2
                self.oled.fill_rect(notch_x, sy, notch_w, 2, 0)
                self.oled.hline(notch_x + 1, sy + 2, notch_w - 2, 1)

                # Bluetooth symbol (larger + mirrored left/right geometry)
                cx, cy = px + pw // 2, py + 22
                self.oled.vline(cx, cy - 10, 21, 1)
                self.oled.line(cx - 7, cy - 4, cx, cy, 1)
                self.oled.line(cx, cy, cx - 7, cy + 4, 1)
                self.oled.line(cx, cy - 10, cx + 7, cy - 4, 1)
                self.oled.line(cx + 7, cy - 4, cx, cy, 1)
                self.oled.line(cx, cy, cx + 7, cy + 4, 1)
                self.oled.line(cx + 7, cy + 4, cx, cy + 10, 1)

                # Pairing instruction copy
                self.oled.text("Open the", 52, 20, 1)
                self.oled.text("Dawggles app", 43, 32, 1)

                self.oled.show()
            except Exception as e:
                log.warning("OLED pairing waiting: %s", e)

    def show_pairing_code(self, code: str):
        """Display the 6-digit BLE pairing code and confirmation controls."""
        with self.display_lock:
            self.display_data["status"] = "pairing_code"
            if self.temp_message_timer:
                self.temp_message_timer.cancel()
                self.temp_message_timer = None
            if not self.hardware_available:
                log.info("OLED [pairing code]: %s", code)
                return
            try:
                self.oled.fill(0)
                self._has_title_bar = False

                line1 = "Press the next button"
                line2 = "if the codes match."
                x1 = max(0, (self.oled.width - len(line1) * 6) // 2)
                x2 = max(0, (self.oled.width - len(line2) * 6) // 2)
                self.oled.text(line1, x1, 4, 1)
                self.oled.text(line2, x2, 14, 1)

                # Box framing the code (x=22–105, y=30–53)
                box_x, box_y, box_w, box_h = 22, 30, 84, 24
                self.oled.rect(box_x, box_y, box_w, box_h, 1)
                self.oled.rect(box_x + 1, box_y + 1, box_w - 2, box_h - 2, 1)

                # Large code centred inside box — size=2: 12px wide, 16px tall
                cx = max(0, (self.oled.width - len(code) * 12) // 2)
                code_y = box_y + 5
                self.oled.text(code, cx, code_y, 1, size=2)

                self.oled.show()
            except Exception as e:
                log.warning("OLED pairing code: %s", e)

    def show_pairing_confirmed(self, code: str):
        """Display confirmed messaging while keeping the same code visible."""
        with self.display_lock:
            self.display_data["status"] = "pairing_confirmed"
            if self.temp_message_timer:
                self.temp_message_timer.cancel()
                self.temp_message_timer = None
            if not self.hardware_available:
                log.info("OLED [pairing confirmed]: %s", code)
                return
            try:
                self.oled.fill(0)
                self._has_title_bar = False

                line1 = "Code confirmed."

                x1 = max(0, (self.oled.width - len(line1) * 6) // 2)
                self.oled.text(line1, x1, 4, 1)

                # Keep the exact same code box and code position as pairing.
                box_x, box_y, box_w, box_h = 22, 30, 84, 24
                self.oled.rect(box_x, box_y, box_w, box_h, 1)
                self.oled.rect(box_x + 1, box_y + 1, box_w - 2, box_h - 2, 1)

                cx = max(0, (self.oled.width - len(code) * 12) // 2)
                code_y = box_y + 5
                self.oled.text(code, cx, code_y, 1, size=2)

                self.oled.show()
            except Exception as e:
                log.warning("OLED pairing confirmed: %s", e)

    def draw_app_header(self, label: str):
        """Standard top bar: label at left, divider at y=11, status bar at right.
        Call after fill(0), before drawing content (which should start at y=14)."""
        self._has_title_bar = True
        self.oled.text(label, 0, 2, 1)
        self.oled.hline(0, 11, self.oled.width, 1)
        self._draw_status_bar()

    def update_display(self, data):
        with self.display_lock:
            self.display_data.update(data)
            if not self.temp_message_timer:
                self._render_current_state()

    def reset_display(self):
        with self.display_lock:
            if self.temp_message_timer:
                self.temp_message_timer.cancel()
                self.temp_message_timer = None
            self.display_data = {}
            self._has_title_bar = False
            if self.hardware_available:
                self.oled.fill(0)
                self.oled.show()

    def sleep(self):
        """Blank the panel without touching the frame buffer or app state.
        SSD1306's 0xAE (display-off) preserves DDRAM, so wake() is instant
        and shows whatever the running app has drawn in the meantime."""
        self.shared_class.display_sleeping.set()
        if self.hardware_available:
            with self.display_lock:
                try:
                    self.oled.poweroff()
                except Exception as e:
                    log.warning("display sleep: poweroff failed: %s", e)

    def wake(self):
        self.shared_class.display_sleeping.clear()
        if self.hardware_available:
            with self.display_lock:
                try:
                    self.oled.poweron()
                except Exception as e:
                    log.warning("display wake: poweron failed: %s", e)

    def toggle_sleep(self):
        if self.shared_class.display_sleeping.is_set():
            self.wake()
        else:
            self.sleep()

    def show_boot_loading(self):
        """Animated ski-goggles boot icon: a single wide visor outline with
        softly curved corners and a small nose notch in the bottom-middle.
        An indeterminate progress sweep fills the visor interior left-to-right
        then clears left-to-right, repeating.

        Returns a callable that stops the animation and blanks the screen.
        Safe to call without hardware — it just returns a no-op stop fn."""
        if not self.hardware_available:
            return lambda: None

        import math
        stop_event = Event()

        W = self.oled.width
        H = self.oled.height

        left_x = 26
        right_x = W - 1 - 26   # 101
        top_y = H // 2 - 13    # 19
        bot_y = H // 2 + 12    # 44
        r = 5                  # corner radius
        notch_cx = W // 2      # 64

        def top_edge(x: int) -> int:
            if left_x <= x <= left_x + r:
                dx = x - (left_x + r)
                return (top_y + r) - int(round(math.sqrt(max(0, r * r - dx * dx))))
            if right_x - r <= x <= right_x:
                dx = x - (right_x - r)
                return (top_y + r) - int(round(math.sqrt(max(0, r * r - dx * dx))))
            return top_y

        def bot_edge(x: int) -> int:
            if left_x <= x <= left_x + r:
                dx = x - (left_x + r)
                return (bot_y - r) + int(round(math.sqrt(max(0, r * r - dx * dx))))
            if right_x - r <= x <= right_x:
                dx = x - (right_x - r)
                return (bot_y - r) + int(round(math.sqrt(max(0, r * r - dx * dx))))
            dx = abs(x - notch_cx)
            if dx <= 3:
                return bot_y - 6
            if dx == 4:
                return bot_y - 4
            if dx == 5:
                return bot_y - 2
            return bot_y

        outline = set()
        for x in range(left_x, right_x + 1):
            outline.add((x, top_edge(x)))
            outline.add((x, bot_edge(x)))
        # Patch vertical jumps between adjacent columns so the outline stays 4-connected.
        for x in range(left_x, right_x):
            t0, t1 = top_edge(x), top_edge(x + 1)
            for y in range(min(t0, t1), max(t0, t1) + 1):
                outline.add((x + 1, y))
            b0, b1 = bot_edge(x), bot_edge(x + 1)
            for y in range(min(b0, b1), max(b0, b1) + 1):
                outline.add((x + 1, y))
        # Left/right side verticals close the shape.
        for y in range(top_edge(left_x), bot_edge(left_x) + 1):
            outline.add((left_x, y))
        for y in range(top_edge(right_x), bot_edge(right_x) + 1):
            outline.add((right_x, y))

        fill_cols = []
        col_ys = {}
        for x in range(left_x + 1, right_x):
            ys = list(range(top_edge(x) + 1, bot_edge(x)))
            if ys:
                fill_cols.append(x)
                col_ys[x] = ys
        fill_w = len(fill_cols)

        def draw(step: int) -> None:
            with self.display_lock:
                if stop_event.is_set():
                    return
                self.oled.fill(0)
                self._has_title_bar = False

                for (px, py) in outline:
                    self.oled.pixel(px, py, 1)

                cycle = 2 * fill_w
                k = step % cycle
                active = fill_cols[:k] if k < fill_w else fill_cols[k - fill_w:]
                for col in active:
                    for y in col_ys[col]:
                        self.oled.pixel(col, y, 1)

                self.oled.show()

        def animate():
            step = 0
            while True:
                draw(step)
                step += 2
                if stop_event.wait(1 / 30):
                    break

        Thread(target=animate, daemon=True).start()

        def stop():
            stop_event.set()
            try:
                with self.display_lock:
                    self.oled.fill(0)
                    self._has_title_bar = False
                    self.oled.show()
            except Exception:
                pass

        return stop
    
    def get_display_data(self):
        """Get current display data"""
        with self.display_lock:
            return self.display_data.copy()
