import base64
import io
import json
import logging
import socket
import struct
from threading import Event, Lock, Thread, Timer

from gpiozero import Button as GPIOButton
from picamera2 import Picamera2
from PIL import Image

log = logging.getLogger(__name__)

# Max JSON payload per framed message (bytes after length prefix).
MAX_JSON_MESSAGE_BYTES = 32 * 1024 * 1024

# JPEG pipeline before base64/TCP (Pi Zero 2 W: smaller files = less CPU + less Wi‑Fi time).
TRANSFER_JPEG_MAX_EDGE_PX = 1280
TRANSFER_JPEG_QUALITY_START = 82
TRANSFER_JPEG_QUALITY_MIN = 52
TRANSFER_JPEG_QUALITY_STEP = 6
TRANSFER_JPEG_TARGET_BYTES = 400_000


def compress_jpeg_for_transfer(jpeg_bytes: bytes) -> bytes:
    """Downscale/re-encode with Pillow; on error return original bytes."""
    try:
        im = Image.open(io.BytesIO(jpeg_bytes))
        im = im.convert("RGB")
    except Exception:
        return jpeg_bytes

    max_edge = TRANSFER_JPEG_MAX_EDGE_PX
    w, h = im.size
    if max(w, h) > max_edge:
        try:
            resample = Image.Resampling.BILINEAR
        except AttributeError:
            resample = Image.BILINEAR
        im.thumbnail((max_edge, max_edge), resample)

    buf = io.BytesIO()
    q = TRANSFER_JPEG_QUALITY_START
    best = jpeg_bytes
    while q >= TRANSFER_JPEG_QUALITY_MIN:
        buf.seek(0)
        buf.truncate()
        im.save(buf, format="JPEG", quality=q, optimize=True)
        candidate = buf.getvalue()
        if len(candidate) <= TRANSFER_JPEG_TARGET_BYTES or q == TRANSFER_JPEG_QUALITY_MIN:
            best = candidate
            break
        q -= TRANSFER_JPEG_QUALITY_STEP
        best = candidate

    return best


# -- Goggle Shared Class --
class SharedClass:
  def __init__(self):
    self.current_app = 'translation'  # Which app is currently active
    self.mode = 'default'  # App-specific state (e.g., 'default', 'capturing', 'processing')
    self.data = {}  # Generic data storage for app-specific data
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

  def switch_app(self, app_name):
    """Switch to a different app"""
    self.current_app = app_name
    self.mode = 'default'  # Reset to default mode for new app
    self.data = {}  # Reset data for new app
    self.display.reset_display()
  
  def reset_app_data(self, app_name):
    """Reset data for a specific app (if needed)"""
    if self.current_app == app_name:
      self.data = {}

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
        self.message_handler = message_handler  # (shared_class, dict) -> None
        self._client_socket = None
        self._client_lock = Lock()
        self._send_lock = Lock()
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(1)
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
                    if message.get("auth_token") != token:
                        log.warning("tcp auth failed")
                        break
                    auth_ok = True
                    message.pop("auth_token", None)
                    if not message:
                        continue
                if self.message_handler:
                    self.message_handler(self.shared_class, message)
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
        if self.capture_thread:
            self.capture_thread.join(timeout=1)

    def capture_loop(self):
        while self.running:
            self.shared_class.shutter_event.wait()
            if not self.running:
                break
            try:
                stream = io.BytesIO()
                self.camera.capture_file(stream, format='jpeg')
                self.shared_class.data['picture'] = stream.getvalue()
                self.send_captured_jpeg_to_client()
                self.shared_class.mode = 'default'
            except Exception as e:
                log.warning("camera: %s", e)
                self.shared_class.mode = 'default'
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
        pic_send = compress_jpeg_for_transfer(pic)
        b64 = base64.standard_b64encode(pic_send).decode("ascii")
        payload = {
            "app": app,
            "event": "picture",
            "format": "jpeg",
            "image_b64": b64,
            "byte_length": len(pic_send),
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
        self.btn = GPIOButton(pin)
        self.shared_class = shared_class
        self.button_callback = button_callback

        # Set up the button with a wrapper to inject shared_class
        self.btn.when_pressed = self._wrapped_callback

    def _wrapped_callback(self):
        """Wrapper to inject shared_class into the app-specific callback"""
        self.button_callback(self.shared_class)

    def update_callback(self, button_callback):
        """Update the button callback (useful when switching apps)"""
        self.button_callback = button_callback

# -- Display Class --
class Display:
    def __init__(self, shared_class):
        self.display_data = {}  # Store current display state
        self.display_lock = Lock()
    
    def update_display(self, data):
        with self.display_lock:
            self.display_data.update(data)

    def reset_display(self):
        with self.display_lock:
            self.display_data = {}
    
    def get_display_data(self):
        """Get current display data"""
        with self.display_lock:
            return self.display_data.copy()