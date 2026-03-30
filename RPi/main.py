"""
Dawggles Pi entry: BLE pairing gate, then TCP on paired LAN IP only.
"""
import logging
import time
from threading import Thread

from goggles_lib import CameraClient, Display, GoggleButton, Server, SharedClass
from pairing.pi_ble_peripheral import run_ble_pairing_background, validate_pairing_environment
from apps.translation_app import translation_button_callback
from app_manager import start_app

logging.basicConfig(level=logging.INFO, format="%(message)s")

CAMERA_CONFIG = {"size": (1280, 720)}
AUTO_CAPTURE_ON_START = True
AUTO_CAPTURE_WARMUP_SEC = 2


def main() -> None:
    shared = SharedClass()

    try:
        validate_pairing_environment()
        run_ble_pairing_background(shared)
    except Exception as e:
        raise SystemExit(
            f"BLE setup failed ({e}). export DAWGGLES_PAIR_PASSWORD=… "
            "and pip install -r requirements.txt"
        ) from e

    shared.display = Display(shared)
    shared.camera_client = CameraClient(shared, CAMERA_CONFIG)

    logging.info("waiting for BLE (Mac: python3 Mac/ble_pair.py)")
    shared.tcp_bind_ready.wait()
    bind_host = shared.paired_tcp_host
    if not bind_host:
        raise SystemExit("paired_tcp_host missing after BLE")

    shared.server = Server(shared, host=bind_host, port=12345)

    shared.button = GoggleButton(
        shared_class=shared, pin=27, button_callback=translation_button_callback
    )
    start_app("translation", shared, shared.button, shared.server)

    if AUTO_CAPTURE_ON_START:

        def _auto_capture() -> None:
            time.sleep(AUTO_CAPTURE_WARMUP_SEC)
            shared.mode = "capturing"
            shared.shutter_event.set()

        Thread(target=_auto_capture, daemon=True).start()

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
