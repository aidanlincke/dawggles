"""
Dawggles Pi entry: Dynamic Wi-Fi Hotspot, then TCP.

Debug snap over existing TCP client (second SSH shell on Pi):
  export DAWGGLES_SIGUSR1_SNAP=1
  python3 main.py
  # Mac: push_client connected + authenticated
  kill -USR1 <pid>    # or: python3 trigger_snap_send.py

Leave AP for CMU-DEVICE: sudo ./network/restore_cmu_wifi.sh
"""
import logging
import os
import signal
import subprocess
import random
import time

from goggles_lib import CameraClient, Display, GoggleButton, Server, SharedClass
from app_manager import start_app

logging.basicConfig(level=logging.INFO, format="%(message)s")

CAMERA_CONFIG = {"size": (1280, 720)}


def start_hotspot(pin: str):
    script_path = os.path.join(os.path.dirname(__file__), "network", "setup_dawggles_hotspot.sh")
    env = os.environ.copy()
    env["DAWGGLES_AP_PASSWORD"] = pin
    logging.info(f"Starting hotspot with PIN: {pin}...")
    
    # This script must be run with sudo!
    result = subprocess.run(["sudo", "-E", script_path], env=env, capture_output=True, text=True)
    if result.returncode != 0:
        logging.error(f"Hotspot script failed:\n{result.stderr}")
        raise SystemExit("Failed to start Wi-Fi hotspot.")
    logging.info("Hotspot is up!")


def main() -> None:
    shared = SharedClass()
    shared.display = Display(shared)

    # 1. Generate 8-digit PIN for the hotspot
    pin = f"{random.randint(0, 99999999):08d}"
    
    # 2. Show PIN on display for the user to type into the app
    shared.display.update_display({"status": "pairing", "pin": pin})
    logging.info(f"\n====================================\nPAIRING PIN: {pin}\n====================================\n")

    # 3. Start the Wi-Fi Hotspot
    start_hotspot(pin)

    shared.camera_client = CameraClient(shared, CAMERA_CONFIG)

    # Listen on all interfaces so the phone can connect when it joins the hotspot
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
