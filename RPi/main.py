"""
Main entry point for Dawggles application
"""
from goggles_lib import SharedClass, Display, Server, GoggleButton, CameraClient
from apps.translation_app import translation_button_callback, translation_message_handler
from app_manager import start_app

# -- Camera Setup --
# Configure for 1080p - fast and enough for OCR translation
CAMERA_CONFIG = {"size": (1920, 1080)}

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

