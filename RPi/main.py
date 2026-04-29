"""
Dawggles Pi entry point.

Start the hotspot, then run:
  python3 main.py
"""
import logging
import os
import signal
import threading
import subprocess
import time

import dbus.mainloop.glib

from goggles_lib import Display, GoggleButton, WebSocketServer, SharedClass
from home_screen import show_home_screen
from pairing.pair import is_paired, run_pairing_flow

_FORCE_PAIR_SENTINEL = "/tmp/dawggles_force_pair"
_shutdown = threading.Event()

logging.basicConfig(level=logging.INFO, format="%(message)s")

NETWORK_INTERFACES = ("wlan0", "ap0", "uap0")


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
    """Show animated boot loader and block until the network interface is ready."""
    stop_loader = shared.display.show_boot_loading() if shared.display else (lambda: None)
    try:
        while not _network_ready() and not _shutdown.is_set():
            time.sleep(1)
    finally:
        stop_loader()
    logging.info("Network is ready.")


def main() -> None:
    # Must be called before any dbus.SystemBus() connection (pairing uses BlueZ).
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)

    signal.signal(signal.SIGTERM, lambda *_: _shutdown.set())

    # PiSugar custom button → SIGUSR1 → toggle display sleep. The display is
    # blanked via SSD1306 poweroff (buffer preserved) and button input is
    # gated, so the user lands back exactly where they left off on wake.
    def _toggle_display(_signum, _frame):
        try:
            if shared.display is not None:
                shared.display.toggle_sleep()
        except Exception:
            logging.exception("display toggle failed")
    signal.signal(signal.SIGUSR1, _toggle_display)

    shared = SharedClass()
    shared.display = Display(shared)

    # Next button initialised early — needed for pairing code confirmation.
    shared.button = GoggleButton(shared_class=shared, pin=4, button_callback=None)
    # Back button initialised early — used as "back/cancel" during pairing.
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
        run_pairing_flow(shared.display, shared.button, shared.cycle_button, shutdown_event=_shutdown)
        # run_pairing_flow blocks until the device is paired (or a fatal BLE
        # error occurs).  Either way we continue; the WebSocket handshake will
        # surface any remaining issues.

    # ── Normal startup ────────────────────────────────────────────────────────
    if not _shutdown.is_set():
        shared.display.reset_display()
        show_home_screen(shared)

    try:
        _shutdown.wait()
    except KeyboardInterrupt:
        pass
    finally:
        # Stop the stream thread first so nothing redraws over us, then blank the
        # OLED last. SSD1306 pixels latch — if we exit before clearing, the frame
        # stays lit until next power-up.
        if shared.camera_client:
            try:
                shared.camera_client.stop_stream_thread()
            except Exception:
                pass
            if shared.camera_client.camera:
                t = threading.Thread(target=shared.camera_client.camera.stop, daemon=True)
                t.start()
                t.join(timeout=5)
        if shared.display:
            try:
                shared.display.reset_display()
                shared.display.sleep()
            except Exception:
                pass


if __name__ == "__main__":
    main()
