import io
import json
import socket
import struct
from threading import Lock, Timer, Event, Thread

# Max JSON payload per framed message (bytes after length prefix).
MAX_JSON_MESSAGE_BYTES = 32 * 1024 * 1024

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
  
  def switch_app(self, app_name):
    """Switch to a different app"""
    self.current_app = app_name
    self.mode = 'default'  # Reset to default mode for new app
    self.data = {}  # Reset data for new app
    self.display.reset_display()  # Reset display
    print(f"Switched to app: {app_name}")
  
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

    def __init__(self, shared_class, host='0.0.0.0', port=12345, message_handler=None):
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
                print(f"send_json failed: {e}")
                return False

    def _client_loop(self, client_socket, addr):
        print(f"Session started with {addr}")
        try:
            while True:
                header = self._recv_exact(client_socket, 4)
                (length,) = struct.unpack("!I", header)
                if length == 0 or length > MAX_JSON_MESSAGE_BYTES:
                    print(f"Invalid message length: {length}")
                    break
                raw = self._recv_exact(client_socket, length)
                try:
                    message = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    print("Invalid JSON in framed message")
                    break
                if not isinstance(message, dict):
                    print("Expected JSON object at top level")
                    break
                if self.message_handler:
                    self.message_handler(self.shared_class, message)
                else:
                    print("No message handler provided")
        except ConnectionError as e:
            print(f"Client {addr} disconnected: {e}")
        except OSError as e:
            print(f"Client {addr} socket error: {e}")
        finally:
            self._clear_client_if(client_socket)
            try:
                client_socket.close()
            except OSError:
                pass
            print(f"Session ended with {addr}")

    def _listen_loop(self):
        while True:
            try:
                client_socket, addr = self.server_socket.accept()
                print(f"Connection from {addr}")
                self._replace_client(client_socket)
                Thread(
                    target=self._client_loop,
                    args=(client_socket, addr),
                    daemon=True,
                ).start()
            except Exception as e:
                print(f"Server error: {e}")

# -- Camera Client Class --
class CameraClient:
    def __init__(self, shared_class, config, camera=None):
        self.shared_class = shared_class
        self.camera = camera
        self.capture_thread = None
        self.running = False
        self.initialize_camera(config)

    def initialize_camera(self, config):
        """Initialize Picamera2 and set up camera parameters"""
        from Picamera2 import Picamera2

        if not self.camera:
            self.camera = Picamera2()
        self.camera.configure(self.camera.create_still_configuration(main=config))
        self.camera.start()
        print("Camera initialized in CameraClient")

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
                print("Capture complete! Sending to iPhone...")
                self.send_to_iphone()
                self.shared_class.mode = 'default'
            except Exception as e:
                print(f"Camera Error: {e}")
                self.shared_class.mode = 'default'
            finally:
                self.shared_class.shutter_event.clear()

    def send_to_iphone(self):
        srv = self.shared_class.server
        if srv is not None and srv.send_json({"event": "picture_ready"}):
            print("Picture ready notification sent to iPhone")
        else:
            print("Picture captured (no TCP client connected)")

    def video_loop(self):
        while self.running:
            self.shared_class.video_event.wait()
            if not self.running:
                break
            print("Video recording started...")
            while self.shared_class.video_event.is_set() and self.running:
                pass
            print("Video recording stopped...")
            self.send_video_to_iphone()

    def send_video_to_iphone(self):
        print("Video sent to iPhone!")

# -- Goggle Button Class --
class GoggleButton:
    def __init__(self, shared_class, pin, button_callback):
        from gpiozero import Button

        self.btn = Button(pin)
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
        """Update the display with new data"""
        with self.display_lock:
            self.display_data.update(data)
            # Placeholder: Implement actual display update logic here
            print(f"Display updated: {self.display_data}")
    
    def reset_display(self):
        """Reset the display to a blank state"""
        with self.display_lock:
            self.display_data = {}
            # Placeholder: Implement actual display reset logic here
            print("Display reset")
    
    def get_display_data(self):
        """Get current display data"""
        with self.display_lock:
            return self.display_data.copy()