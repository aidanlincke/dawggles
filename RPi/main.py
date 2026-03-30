"""
Dawggles Pi entry: Dynamic Wi-Fi Hotspot, then TCP.

Setup the network FIRST:
  export DAWGGLES_AP_PASSWORD="your-pin"
  sudo -E ./network/setup_dawggles_hotspot.sh

Then run:
  python3 main.py

Leave AP for CMU-DEVICE: sudo ./network/restore_cmu_wifi.sh
"""
import logging
import os
import signal
import time

from goggles_lib import CameraClient, Display, GoggleButton, Server, SharedClass
from app_manager import start_app

logging.basicConfig(level=logging.INFO, format="%(message)s")

CAMERA_CONFIG = {"size": (1280, 720)}


def main() -> None:
    shared = SharedClass()
    shared.display = Display(shared)

    # 1. Grab the PIN from the environment (set when you ran the hotspot script)
    pin = os.environ.get("DAWGGLES_AP_PASSWORD", "Unknown")
    
    # 2. Show PIN on display for the user to type into the app
    shared.display.update_display({"status": "pairing", "pin": pin})
    logging.info(f"\n====================================\nAPP PAIRING PIN: {pin}\n====================================\n")

    shared.camera_client = CameraClient(shared, CAMERA_CONFIG)

    # Listen on all interfaces so the phone can connect
    bind_host = "0.0.0.0"
    
    shared.server = Server(shared, host=bind_host, port=12345, defer_listen=True)

    shared.button = GoggleButton(
        shared_class=shared, pin=27, button_callback=None
    )
    
    # 4. Start the app and the TCP Server
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
