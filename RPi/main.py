from enum import Enum
from gpiozero import Button
from threading import Thread
from signal import pause
from goggles_lib import GoggleState, SharedClass, GoggleButton, CameraClient
from picamera2 import Picamera2

# -- GPIO Setup --
BTN_GPIO = 23  # GPIO 23 (Physical Pin 26)

# -- Camera Setup --
# Configure for 1080p - fast and enough for OCR translation
CAMERA_CONFIG = {"size": (1920, 1080)}

def main():
    print("Starting RPi Main Program...")
    # Shared Class Initialization
    shared_class = SharedClass()

    # Goggle Button Initialization
    goggle_button = GoggleButton(BTN_GPIO, shared_class)

    # Goggle Camera Initialization
    picam2 = Picamera2()
    config = picam2.create_still_configuration(main=CAMERA_CONFIG)
    picam2.configure(config)
    picam2.start()
    camera_client = CameraClient(picam2, shared_class)
    goggle_camera = Thread(target=camera_client.capture_loop, daemon=True)
    goggle_camera.start()

    while True:
        if shared_class.goggle_state == GoggleState.DISCONNECTED:
            # Display Waiting for Connection Message (Placeholder)
            # Fix this
            shared_class.goggle_state = GoggleState.DEFAULT
            print("Goggles connected! State set to DEFAULT.")

