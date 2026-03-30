"""
Dawggles Pi entry: BLE pairing gate, then TCP on paired LAN IP only.

Debug snap over existing TCP client (second SSH shell on Pi):
  export DAWGGLES_SIGUSR1_SNAP=1
  python3 main.py
  # Mac: push_client connected + authenticated
  kill -USR1 <pid>    # or: python3 trigger_snap_send.py
"""
import logging
import os
import signal

from goggles_lib import CameraClient, Display, GoggleButton, Server, SharedClass
from pairing.pi_ble_peripheral import run_ble_pairing_background, validate_pairing_environment
from apps.translation_app import translation_button_callback
from app_manager import start_app

logging.basicConfig(level=logging.INFO, format="%(message)s")

CAMERA_CONFIG = {"size": (1280, 720)}


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

    shared.server = Server(shared, host=bind_host, port=12345, defer_listen=True)

    shared.button = GoggleButton(
        shared_class=shared, pin=27, button_callback=translation_button_callback
    )
    start_app("translation", shared, shared.button, shared.server)
    shared.server.start_listen()

    _env_snap = os.environ.get("DAWGGLES_SIGUSR1_SNAP", "").lower() in (
        "1",
        "true",
        "yes",
    )

    def _sigusr1_snap(_signum, _frame):
        cc = shared.camera_client
        if not cc or not cc.running:
            logging.warning("SIGUSR1: camera capture loop not running")
            return
        shared.shutter_event.set()
        logging.info("SIGUSR1: capture + send to TCP client")

    if _env_snap:
        signal.signal(signal.SIGUSR1, _sigusr1_snap)
        logging.info(
            "DAWGGLES_SIGUSR1_SNAP: run  kill -USR1 %s  or  python3 trigger_snap_send.py",
            os.getpid(),
        )
    else:
        signal.signal(signal.SIGUSR1, signal.SIG_IGN)

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
