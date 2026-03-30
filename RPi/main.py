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
from pairing.pi_ble_peripheral import run_ble_pairing_background

logging.basicConfig(level=logging.INFO, format="%(message)s")

CAMERA_CONFIG = {"size": (1280, 720)}


def main() -> None:
    shared = SharedClass()
    shared.display = Display(shared)

    # 1. Grab the PIN from the environment (set when you ran the hotspot script)
    pin = os.environ.get("DAWGGLES_AP_PASSWORD", "Unknown")
    
    # 2. Show idle state "PAIR IN APP" on display
    shared.display.update_display({"status": "pairing_idle", "pin": pin})
    logging.info("\n====================================")
    logging.info("  OLED: PAIR IN APP")
    logging.info(f"  APP PAIRING PIN: {pin}")
    logging.info("====================================\n")

    shared.camera_client = CameraClient(shared, CAMERA_CONFIG)

    # Listen on all interfaces so the phone can connect
    bind_host = "0.0.0.0"
    
    # Start BLE in the background to listen for the phone ping
    run_ble_pairing_background(shared)

    shared.server = Server(shared, host=bind_host, port=12345, defer_listen=True)
    shared.server.start_listen()

    # Wait for the phone to connect before starting the apps
    logging.info("Waiting for phone to connect to TCP socket...")
    
    shared.button = GoggleButton(
        shared_class=shared, pin=27, button_callback=None
    )

    while shared.server._client_socket is None:
        time.sleep(0.5)
        
    logging.info("Phone connected! Starting Translation App...")

    # Clear the pairing status so apps can use the display
    shared.display.reset_display()
    
    # 4. Start the app (this assigns the correct button callbacks for the app)
    start_app("translation", shared, shared.button, shared.server)

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
