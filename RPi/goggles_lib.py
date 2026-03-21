import io
import json
import socket
from enum import Enum
from gpiozero import Button
from Picamera2 import Picamera2
from threading import Lock, Timer, Event, Thread

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
    def __init__(self, shared_class, host='0.0.0.0', port=12345, message_handler=None):
        self.shared_class = shared_class
        self.host = host
        self.port = port
        self.message_handler = message_handler  # Function to handle incoming messages
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(1)
        self.thread = Thread(target=self._listen_loop)
        self.thread.daemon = True
        self.thread.start()

    def _listen_loop(self):
        while True:
            try:
                client_socket, addr = self.server_socket.accept()
                print(f"Connection from {addr}")
                data = client_socket.recv(1024)
                if data:
                    try:
                        message = json.loads(data.decode('utf-8'))
                        if self.message_handler:
                            self.message_handler(self.shared_class, message)
                        else:
                            print("No message handler provided")
                    except json.JSONDecodeError:
                        print("Invalid JSON received")
                client_socket.close()
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
        if not self.camera:
            self.camera = Picamera2()
        self.camera.configure(self.camera.create_still_configuration(main=self.config))
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
        print("Picture sent to iPhone!")

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