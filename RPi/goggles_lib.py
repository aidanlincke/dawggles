import asyncio
import base64
import io
import json
import logging
import queue
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
    # Set by pairing after BLE knock; main() waits before binding TCP.
    self.paired_tcp_host = None  # str | None — Pi LAN IP to bind (never 0.0.0.0 in production)
    self.tcp_bind_ready = Event()

# -- WebSocket Server Class --
class WebSocketServer:
    """
    WebSocket server: one persistent client connection at a time, full duplex.
    Runs an asyncio event loop in a daemon thread; all other code stays threaded.
    Message format: UTF-8 JSON text frames (same schema as before).
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
        async with websockets.serve(self._handler, host, port, max_size=MAX_JSON_MESSAGE_BYTES):
            self._listening_event.set()
            log.info("websocket server listening on %s:%s", host, port)
            await asyncio.Future()  # run forever

    async def _handler(self, ws):
        self._ws = ws
        log.info("websocket client connected: %s", ws.remote_address)
        try:
            async for raw in ws:
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
                transform=Transform(hflip=True, vflip=True),
            )
            log.info("camera: applying 180-degree transform (hflip + vflip)")
        except Exception as e:
            log.warning("camera: could not apply 180-degree transform, using default orientation: %s", e)
            still_config = self.camera.create_still_configuration(main=config)

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
        """Push JPEG (or ready signal) over TCP using shared.current_app — no app-specific names here."""
        srv = self.shared_class.server
        app = self.shared_class.current_app
        if srv is None:
            return
        pic = self.shared_class.data.get("picture")
        if not pic:
            srv.send_json({"app": app, "event": "picture_ready", "format": "jpeg"})
            return
        
        # Bypass Pillow and send the JPEG raw bytes
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
            srv.send_json({"app": app, "event": "picture_ready", "format": "jpeg"})

    def video_loop(self):
        while self.running:
            self.shared_class.video_event.wait()
            if not self.running:
                break
            while self.shared_class.video_event.is_set() and self.running:
                pass
            self.send_video_to_client()

    def send_video_to_client(self):
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
            
        if self.button_callback:
            self.button_callback(count)

    def update_callback(self, button_callback):
        """Update the button callback (useful when switching apps)"""
        self.button_callback = button_callback

# -- Display Class --
class Display:
    def __init__(self, shared_class):
        self.shared_class = shared_class
        self.display_data = {}
        self.display_lock = Lock()
        self.temp_message_timer = None
        self._last_connected = None
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
        """Refresh the connection indicator whenever the WebSocket state changes."""
        while True:
            time.sleep(1)
            if not self.hardware_available or self.shared_class.server is None:
                continue
            connected = self.shared_class.server.connected
            if connected == self._last_connected:
                continue
            self._last_connected = connected
            with self.display_lock:
                # Clear the indicator bounding box, redraw, and push to display
                self.oled.fill_rect(119, 0, 9, 8, 0)
                self._draw_status_bar()
                self.oled.show()

    def _draw_status_bar(self):
        """Small connection indicator in the top-right corner. No-op during pairing."""
        if not self.hardware_available or self.shared_class.server is None:
            return
        connected = self.shared_class.server.connected
        # 3 signal bars, bottom-aligned, rightmost corner (x=119..126, y=1..7)
        # bar heights: 3px, 5px, 7px with 1px gaps
        bx = [119, 122, 125]
        bh = [3, 5, 7]
        base_y = 7
        if connected:
            for x, h in zip(bx, bh):
                self.oled.fill_rect(x, base_y - h + 1, 2, h, 1)
        else:
            # × in the same bounding box (119,1)–(126,7)
            self.oled.line(119, 1, 126, 7, 1)
            self.oled.line(126, 1, 119, 7, 1)

    def _render_text(self, lines: list, color: int = 1):
        if not self.hardware_available:
            return
        try:
            self.oled.fill(0)

            if isinstance(lines, str):
                lines = [lines]

            total_height = len(lines) * 10 # 8px font + 2px padding
            start_y = max(0, (self.oled.height - total_height) // 2)

            for i, line in enumerate(lines):
                tw = len(line) * 8
                tx = max(0, (self.oled.width - tw) // 2)
                ty = start_y + (i * 10)
                self.oled.text(line, tx, ty, color)

            self._draw_status_bar()
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
                    self._draw_status_bar()
                    self.oled.show()

    def show_pairing_waiting(self):
        """Display 'OPEN APP / to pair' while BLE is advertising."""
        with self.display_lock:
            if self.temp_message_timer:
                self.temp_message_timer.cancel()
                self.temp_message_timer = None
            if not self.hardware_available:
                log.info("OLED [pairing]: OPEN APP to pair")
                return
            try:
                self.oled.fill(0)
                lines = ["OPEN APP", "to pair"]
                total_h = len(lines) * 10
                sy = max(0, (self.oled.height - total_h) // 2)
                for i, line in enumerate(lines):
                    tx = max(0, (self.oled.width - len(line) * 6) // 2)
                    self.oled.text(line, tx, sy + i * 10, 1)
                self.oled.show()
            except Exception as e:
                log.warning("OLED pairing waiting: %s", e)

    def show_pairing_code(self, code: str, secs_left: int = 30):
        """Display the 6-digit BLE pairing code in large text with live countdown."""
        with self.display_lock:
            if self.temp_message_timer:
                self.temp_message_timer.cancel()
                self.temp_message_timer = None
            if not self.hardware_available:
                log.info("OLED [pairing code]: %s  %ds", code, secs_left)
                return
            try:
                self.oled.fill(0)

                label = "PAIR CODE"
                self.oled.text(label, max(0, (self.oled.width - len(label) * 6) // 2), 2, 1)

                # Large code — size=2 gives 12px-wide, 16px-tall chars
                try:
                    cx = max(0, (self.oled.width - len(code) * 12) // 2)
                    self.oled.text(code, cx, 16, 1, size=2)
                except TypeError:
                    # Older adafruit-framebuf without size param
                    cx = max(0, (self.oled.width - len(code) * 6) // 2)
                    self.oled.text(code, cx, 18, 1)

                hint = "BTN = CONFIRM"
                self.oled.text(hint, max(0, (self.oled.width - len(hint) * 6) // 2), 40, 1)

                # Countdown — right-align so digits don't jump around
                countdown = f"{secs_left:2d}s"
                self.oled.text(countdown, max(0, (self.oled.width - len(countdown) * 6) // 2), 52, 1)

                self.oled.show()
            except Exception as e:
                log.warning("OLED pairing code: %s", e)

    def show_pairing_confirmed(self):
        """Display a checkmark and 'WAITING...' after the user confirms the pairing code."""
        with self.display_lock:
            if self.temp_message_timer:
                self.temp_message_timer.cancel()
                self.temp_message_timer = None
            if not self.hardware_available:
                log.info("OLED [pairing]: confirmed — waiting for iPhone")
                return
            try:
                self.oled.fill(0)

                # Checkmark: two line segments drawn 3× for ~3px thickness
                # p1 → p2 is the short down-right stroke; p2 → p3 is the long up-right stroke
                p1 = (28, 28)
                p2 = (46, 44)
                p3 = (90, 10)
                for dy in (-1, 0, 1):
                    self.oled.line(p1[0], p1[1] + dy, p2[0], p2[1] + dy, 1)
                    self.oled.line(p2[0], p2[1] + dy, p3[0], p3[1] + dy, 1)

                msg = "WAITING..."
                self.oled.text(msg, max(0, (self.oled.width - len(msg) * 6) // 2), 54, 1)

                self.oled.show()
            except Exception as e:
                log.warning("OLED pairing confirmed: %s", e)

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
            if self.hardware_available:
                self.oled.fill(0)
                self.oled.show()
    
    def get_display_data(self):
        """Get current display data"""
        with self.display_lock:
            return self.display_data.copy()
