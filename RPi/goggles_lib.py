import base64
import io
import json
import logging
import socket
import struct
import queue
from threading import Event, Lock, Thread, Timer

from gpiozero import Button as GPIOButton
from picamera2 import Picamera2

import board
import busio
import digitalio
import adafruit_ssd1306

log = logging.getLogger(__name__)

# Max JSON payload per framed message (bytes after length prefix).
MAX_JSON_MESSAGE_BYTES = 32 * 1024 * 1024

# -- Goggle Shared Class --
class SharedClass:
  def __init__(self):
    self.current_app = 'translation'  # Which app is currently active
    self.data = {}  # Generic data storage
    self.server = None   # Set by app_manager during initialize_system
    self.display = None  # Set by app_manager during initialize_system
    self.button = None   # Set by app_manager during initialize_system
    self.camera_client = None  # Set by app_manager during initialize_system
    self.shutter_event = Event()
    self.video_event = Event()
    self.display_lock = Lock()
    # From BLE on Pi. When set, first inbound TCP JSON must include auth_token.
    self.tcp_auth_token = None  # str | None
    # Set by pairing after BLE password OK; main() waits before binding TCP.
    self.paired_tcp_host = None  # str | None — Pi LAN IP to bind (never 0.0.0.0 in production)
    self.tcp_bind_ready = Event()

# -- Server Class --
class Server:
    """
    TCP server: one persistent phone connection at a time, full duplex.

    Wire format (both directions): 4-byte big-endian length + UTF-8 JSON bytes.
    The phone must use the same framing when sending; use send_json() when pushing from the Pi.
    """

    def __init__(
        self,
        shared_class,
        host="127.0.0.1",
        port=12345,
        message_handler=None,
        defer_listen=False,
    ):
        self.shared_class = shared_class
        self.host = host
        self.port = port
        self.message_handler = message_handler  # (dict) -> None
        self._client_socket = None
        self._client_lock = Lock()
        self._send_lock = Lock()
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(1)
        
        # Message queue for non-blocking TCP receive
        self.message_queue = queue.Queue()
        self.worker_thread = Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()

        self.thread = None
        if not defer_listen:
            self.start_listen()

    def start_listen(self):
        if self.thread is not None:
            return
        self.thread = Thread(target=self._listen_loop, daemon=True)
        self.thread.start()

    @staticmethod
    def _recv_exact(sock, n):
        buf = bytearray()
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("connection closed while reading")
            buf += chunk
        return bytes(buf)

    def _replace_client(self, new_sock):
        with self._client_lock:
            old = self._client_socket
            self._client_socket = new_sock
        if old is not None:
            try:
                old.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                old.close()
            except OSError:
                pass

    def _clear_client_if(self, sock):
        with self._client_lock:
            if self._client_socket is sock:
                self._client_socket = None

    def send_json(self, obj):
        """Send one framed JSON message to the connected phone (thread-safe). Returns False if no client."""
        body = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        if len(body) > MAX_JSON_MESSAGE_BYTES:
            raise ValueError("JSON message exceeds MAX_JSON_MESSAGE_BYTES")
        frame = struct.pack("!I", len(body)) + body
        with self._send_lock:
            sock = self._client_socket
            if sock is None:
                return False
            try:
                sock.sendall(frame)
                return True
            except OSError as e:
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

    def _client_loop(self, client_socket, addr):
        sc = self.shared_class
        token = getattr(sc, "tcp_auth_token", None)
        auth_ok = token is None
        try:
            while True:
                header = self._recv_exact(client_socket, 4)
                (length,) = struct.unpack("!I", header)
                if length == 0 or length > MAX_JSON_MESSAGE_BYTES:
                    break
                raw = self._recv_exact(client_socket, length)
                try:
                    message = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    break
                if not isinstance(message, dict):
                    break
                if not auth_ok:
                    # Backward compatibility for BLE tokens if someone forces it,
                    # otherwise skip auth checking since Wi-Fi WPA2 is the security layer.
                    if token and message.get("auth_token") != token:
                        log.warning("tcp auth failed")
                        break
                    auth_ok = True
                    message.pop("auth_token", None)
                    if not message:
                        continue
                
                # Push to worker thread so network loop isn't blocked
                self.message_queue.put(message)
        except (ConnectionError, OSError):
            pass
        finally:
            self._clear_client_if(client_socket)
            try:
                client_socket.close()
            except OSError:
                pass

    def _listen_loop(self):
        while True:
            try:
                client_socket, addr = self.server_socket.accept()
                log.info("tcp client %s", addr)
                self._replace_client(client_socket)
                Thread(
                    target=self._client_loop,
                    args=(client_socket, addr),
                    daemon=True,
                ).start()
            except Exception as e:
                log.warning("server accept: %s", e)

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
        self.camera.configure(self.camera.create_still_configuration(main=config))
        self.camera.start()

    def start_capture_loop(self):
        if not self.camera:
            raise RuntimeError("Camera not initialized")
        if self.capture_thread and self.capture_thread.is_alive():
            return
        self.running = True
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
        self.display_data = {}  # Store current display state
        self.display_lock = Lock()
        
        # Initialize hardware OLED
        try:
            self.spi = busio.SPI(board.SCK, MOSI=board.MOSI)
            self.cs = digitalio.DigitalInOut(board.D17)
            self.dc = digitalio.DigitalInOut(board.D27)
            
            # 128x64 standard OLED
            self.oled = adafruit_ssd1306.SSD1306_SPI(128, 64, self.spi, self.dc, None, self.cs)
            self.oled.contrast(5)
            self.oled.write_cmd(0xA0) # Seg remap
            self.oled.fill(0)
            self.oled.show()
            self.hardware_available = True
        except Exception as e:
            log.warning(f"OLED init failed, continuing without display: {e}")
            self.hardware_available = False
    
    def _render_text(self, message: str, color: int = 1):
        if not self.hardware_available:
            return
        try:
            self.oled.fill(0)
            tw = len(message) * 8
            tx = max(0, (self.oled.width - tw) // 2)
            ty = max(0, (self.oled.height - 8) // 2)
            self.oled.text(message, tx, ty, color)
            self.oled.show()
        except Exception as e:
            log.warning(f"OLED render failed: {e}")

    def update_display(self, data):
        with self.display_lock:
            self.display_data.update(data)
            
            # If we are in pairing mode, show the PIN
            if self.display_data.get("status") == "pairing":
                pin = self.display_data.get("pin", "")
                self._render_text(f"PIN: {pin}")
            # If connected, maybe show the app name
            elif self.display_data.get("app"):
                app_name = self.display_data.get("app").upper()
                self._render_text(f"{app_name}")

    def reset_display(self):
        with self.display_lock:
            self.display_data = {}
            if self.hardware_available:
                self.oled.fill(0)
                self.oled.show()
    
    def get_display_data(self):
        """Get current display data"""
        with self.display_lock:
            return self.display_data.copy()
