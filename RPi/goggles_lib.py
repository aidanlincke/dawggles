import io
from enum import Enum
from gpiozero import Button
from threading import Lock, Timer, Event

# -- Goggle States --
class GoggleState(Enum):
    DISCONNECTED = -1
    DEFAULT = 0
    CAPTURING = 1
    TRANSLATING = 2

# -- Goggle Shared Class --
class SharedClass:
  def __init__(self):
    self.iPhone_IP = None
    self.goggle_state = GoggleState.DISCONNECTED
    self.gps_flag = False
    self.gps_data = None
    self.translation_data = None
    self.text_size = 12
    self.shutter_event = Event()
    self.picture = None
    self.translation_groupings = None
    self.display_idx = 0
    self.display_lock = Lock()

# -- Goggle Button Class --
class GoggleButton:
    def __init__(self, pin, shared_class):
        self.btn = Button(pin)
        self.btn.when_pressed = self._button_callback
        self.click_count = 0
        self.timer = None
        self.shared_class = shared_class

    def _button_callback(self):
        if self.shared_class.goggle_state == GoggleState.DEFAULT:
            print("Button Pressed: Switching to CAPTURING mode, capturing picture...")
            self.shared_class.goggle_state = GoggleState.CAPTURING
            self.shared_class.shutter_event.set() 

        elif self.shared_class.goggle_state == GoggleState.CAPTURING:
            print("Button Pressed: Already in CAPTURING mode, display not ready...")

        else:
            print("Display is ready, processing button press for TRANSLATING...")
            self.click_count += 1
            if self.timer is None:
                self.timer = Timer(0.4, self._process_clicks)
                self.timer.start()
            print(f"Button Pressed: Already in TRANSLATE mode. Click count: {self.click_count}")
            
    def _process_clicks(self):
        with self.shared_class.display_lock:
            if self.click_count == 1:
                # Skip to next translation
                self.shared_class.display_idx = (self.shared_class.display_idx + 1)
                print(f"Skipped forward to translation: {self.shared_class.display_idx}")

            elif (self.shared_class.display_idx > 0):
                # Go back to previous translation
                self.shared_class.display_idx = (self.shared_class.display_idx - 1)
                print(f"Skipped backward to translation: {self.shared_class.display_idx}")
        
        self.click_count = 0
        self.timer = None

class CameraClient:
    def __init__(self, camera, shared_class):
        self.camera = camera
        self.shared_class = shared_class

    def capture_loop(self):
        while True:
            # 1. Thread hibernates here with 0% CPU until button sets the event
            self.shared_class.shutter_event.wait() 
            
            try:
                # 2. Capture to RAM (BytesIO) using GPU for JPEG compression
                stream = io.BytesIO()
                self.camera.capture_file(stream, format='jpeg')
                
                # 3. Store raw bytes for the WiFi thread
                self.shared_class.picture = stream.getvalue()
                
                # 4. Trigger the networking part
                print("Capture complete! Sending to iPhone...")
                self.send_to_iphone()
            
            except Exception as e:
                # Handle camera errors gracefully
                print(f"Camera Error: {e}")
                self.shared_class.goggle_state = GoggleState.DEFAULT
                
            finally:
                # Reset event to go back to sleep
                self.shared_class.shutter_event.clear()
    
    def send_to_iphone(self):
        # Placeholder function to send picture data to iPhone
        # Uses self.shared_class.picture and self.iPhone_IP
        print("Picture sent to iPhone!")