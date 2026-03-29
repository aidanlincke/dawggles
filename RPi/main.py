"""
Main entry point for Dawggles application
"""
import time
from threading import Thread

from goggles_lib import SharedClass, Display, Server, GoggleButton, CameraClient
from apps.translation_app import translation_button_callback
from app_manager import start_app

# -- Camera Setup --
# 1280x720: lighter on Pi Zero 2 W (sensor + encode); still fine for OCR on phone.
# Full-res 1080p stills cost more RAM/CPU; transfer path also downscales in goggles_lib.
CAMERA_CONFIG = {"size": (1280, 720)}

# Like test_camera.py: warm up sensor, then fire one capture without pressing the button.
# Set False for normal use (button-only).
AUTO_CAPTURE_ON_START = True
AUTO_CAPTURE_WARMUP_SEC = 2

def main():
    print("Starting Dawggles...")
    
    # Initialize shared class, server, display, button, and camera client
    shared = SharedClass()
    shared.server = Server(shared, host='0.0.0.0', port=12345)
    shared.display = Display(shared)
    shared.button = GoggleButton(shared_class=shared, pin=27, button_callback=translation_button_callback)
    shared.camera_client = CameraClient(shared, CAMERA_CONFIG)

    # Start with translation app
    start_app('translation', shared, shared.button, shared.server)

    if AUTO_CAPTURE_ON_START:

        def _auto_capture() -> None:
            time.sleep(AUTO_CAPTURE_WARMUP_SEC)
            print("AUTO_CAPTURE_ON_START: taking picture (same path as shutter button)...")
            shared.mode = "capturing"
            shared.shutter_event.set()

        Thread(target=_auto_capture, daemon=True).start()

    print(f"Current app: {shared.current_app}")
    print("Dawggles ready - button and server are listening...")
    
    # Keep the program running
    try:
        while True:
            pass
    except KeyboardInterrupt:
        print("\nShutting down...")
        if shared.camera_client:
            shared.camera_client.stop_capture_loop()
            if shared.camera_client.camera:
                try:
                    shared.camera_client.camera.stop()
                except Exception:
                    pass

if __name__ == "__main__":
    main()

