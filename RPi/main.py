"""
Dawggles Pi entry point.

Start the hotspot, then run:
  python3 main.py
"""
import logging
import os
import signal
import subprocess
import time

import dbus.mainloop.glib

from goggles_lib import CameraClient, Display, GoggleButton, WebSocketServer, SharedClass
from home_screen import show_home_screen
from pairing.pair import is_paired, run_pairing_flow

_FORCE_PAIR_SENTINEL = "/tmp/dawggles_force_pair"

logging.basicConfig(level=logging.INFO, format="%(message)s")

CAMERA_CONFIG = {"size": (1280, 720)}
NETWORK_INTERFACES = ("wlan0", "ap0", "uap0")


def _show_boot_wait_screen(display: Display) -> None:
    if not display or not getattr(display, "hardware_available", False):
        return
    with display.display_lock:
        display.oled.fill(0)
        display.oled.text("Starting up", 28, 20, 1)
        display.oled.text("Waiting for network", 4, 32, 1)
        display.oled.show()


def _network_ready() -> bool:
    """Return True when hotspot/network interface has an IPv4 address."""
    try:
        result = subprocess.run(
            ["ip", "-4", "-o", "addr", "show", "scope", "global"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except Exception:
        return False

    if result.returncode != 0:
        return False

    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[1] in NETWORK_INTERFACES and parts[2] == "inet":
            return True
    return False


def _wait_for_network(shared: SharedClass) -> None:
    """Show boot feedback and block until expected network interface is ready."""
    _show_boot_wait_screen(shared.display)
    while not _network_ready():
        time.sleep(1)
    logging.info("Network is ready.")


def main() -> None:
    # Must be called before any dbus.SystemBus() connection (pairing uses BlueZ).
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)

    # systemctl stop sends SIGTERM, which by default kills us without running
    # the KeyboardInterrupt cleanup below — leaving the last frame latched on
    # the OLED. Translate SIGTERM into the same KeyboardInterrupt path so
    # reset_display() always runs on shutdown.
    def _term(_signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _term)

    shared = SharedClass()
    shared.display = Display(shared)

    # Action button initialised early — needed for pairing code confirmation.
    shared.button = GoggleButton(shared_class=shared, pin=4, button_callback=None)
    # Cycle button initialised early — used as "back/cancel" during pairing.
    shared.cycle_button = GoggleButton(shared_class=shared, pin=23, button_callback=None)

    # ── Always-on WebSocket ─────────────────────────────────────────────────
    # Bring up the socket server immediately so it is available during pairing
    # and remains up for the entire process lifetime.
    shared.server = WebSocketServer(shared, host="0.0.0.0", port=8765)

    for _ in range(30):
        if shared.server.is_listening:
            break
        if shared.server.startup_error is not None:
            raise RuntimeError(f"WebSocket server failed to start: {shared.server.startup_error}")
        time.sleep(0.1)

    if not shared.server.is_listening:
        logging.warning("WebSocket server did not report listening state yet; continuing")

    # Wait for AP/network readiness before proceeding to pairing and app startup.
    _wait_for_network(shared)

    # ── Pairing ──────────────────────────────────────────────────────────────
    force_pair = os.path.exists(_FORCE_PAIR_SENTINEL)
    if force_pair:
        try:
            os.remove(_FORCE_PAIR_SENTINEL)
        except OSError:
            pass

    if force_pair or not is_paired():
        logging.info("Starting pairing flow%s.", " (forced after unpair)" if force_pair else "")
        run_pairing_flow(shared.display, shared.button, shared.cycle_button)
        # run_pairing_flow blocks until the device is paired (or a fatal BLE
        # error occurs).  Either way we continue; the WebSocket handshake will
        # surface any remaining issues.

    # ── Normal startup ────────────────────────────────────────────────────────
    shared.camera_client = CameraClient(shared, CAMERA_CONFIG)

    shared.display.reset_display()

    show_home_screen(shared)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        # Stop the capture thread first so nothing redraws over us, then blank the
        # OLED last. SSD1306 pixels latch — if we exit before clearing, the frame
        # stays lit until next power-up.
        if shared.camera_client:
            try:
                shared.camera_client.stop_capture_loop()
            except Exception:
                pass
            if shared.camera_client.camera:
                try:
                    shared.camera_client.camera.stop()
                except Exception:
                    pass
        if shared.display:
            try:
                shared.display.reset_display()
            except Exception:
                pass


if __name__ == "__main__":
    main()
