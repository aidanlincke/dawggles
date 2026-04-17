"""
Dawggles Pi entry point.

Start the hotspot, then run:
  python3 main.py
"""
import logging
import time

from goggles_lib import CameraClient, Display, GoggleButton, WebSocketServer, SharedClass
from app_manager import start_app, switch_to_next_app

logging.basicConfig(level=logging.INFO, format="%(message)s")

CAMERA_CONFIG = {"size": (1280, 720)}


def main() -> None:
    shared = SharedClass()
    shared.display = Display(shared)
    shared.camera_client = CameraClient(shared, CAMERA_CONFIG)
    shared.server = WebSocketServer(shared, host="0.0.0.0", port=8765)

    # Give the server thread a brief moment to bind and expose startup errors early.
    for _ in range(30):
        if shared.server.is_listening:
            break
        if shared.server.startup_error is not None:
            raise RuntimeError(f"WebSocket server failed to start: {shared.server.startup_error}")
        time.sleep(0.1)

    if not shared.server.is_listening:
        logging.warning("WebSocket server did not report listening state yet; continuing")

    logging.info("Waiting for phone to connect via WebSocket...")
    while not shared.server.connected:
        if shared.server.startup_error is not None:
            raise RuntimeError(f"WebSocket server stopped: {shared.server.startup_error}")
        time.sleep(0.5)

    logging.info("Phone connected! Starting Translation App...")
    shared.display.reset_display()

    # App callback button: GPIO 4 (pin 7)
    shared.button = GoggleButton(
        shared_class=shared, pin=4, button_callback=None
    )

    # Cycle apps button: GPIO 23 (pin 16)
    def cycle_callback(click_count):
        if click_count > 0:
            logging.info(f"Cycle button clicked {click_count} times, switching app...")
            switch_to_next_app(shared)

    shared.cycle_button = GoggleButton(
        shared_class=shared, pin=23, button_callback=cycle_callback
    )

    start_app("translation", shared, shared.button, shared.server)

    try:
        while True:
            pass
    except KeyboardInterrupt:
        if shared.camera_client:
            shared.camera_client.stop_capture_loop()
            if shared.camera_client.camera:
                try:
                    shared.camera_client.camera.stop()
                except Exception:
                    pass


if __name__ == "__main__":
    main()
