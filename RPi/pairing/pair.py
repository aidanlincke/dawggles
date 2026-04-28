#!/usr/bin/env python3
"""
pairing/pair.py — BLE pairing for Dawggles (BlueZ Numeric Comparison).

Exposes two public functions for use by main.py:

    is_paired()                        → bool
    run_pairing_flow(display, confirm_button, cancel_button=None)  → bool
                                        (True = paired successfully)

System packages required (not pip):
    sudo apt install python3-dbus python3-gi
"""

import signal
import sys
import threading
import logging

import dbus
import dbus.service
import dbus.mainloop.glib
from gi.repository import GLib

log = logging.getLogger(__name__)

# ── BlueZ D-Bus constants ──────────────────────────────────────────────────────
BLUEZ_SERVICE        = "org.bluez"
BLUEZ_PATH           = "/org/bluez"
ADAPTER_PATH         = "/org/bluez/hci0"
ADAPTER_IFACE        = "org.bluez.Adapter1"
DEVICE_IFACE         = "org.bluez.Device1"
AGENT_MANAGER_IFACE  = "org.bluez.AgentManager1"
AGENT_IFACE          = "org.bluez.Agent1"
LE_ADV_MANAGER_IFACE = "org.bluez.LEAdvertisingManager1"
LE_ADV_IFACE         = "org.bluez.LEAdvertisement1"
PROPS_IFACE          = "org.freedesktop.DBus.Properties"
OBJ_MANAGER_IFACE    = "org.freedesktop.DBus.ObjectManager"

AGENT_PATH        = "/org/dawggles/agent"
ADV_PATH          = "/org/dawggles/advertisement0"
DEVICE_NAME       = "Dawggles"
PAIR_SERVICE_UUID = "0000d100-0000-1000-8000-00805f9b34fb"
_CONFIRM_TIMEOUT_SECS = 30

# ── Helpers ────────────────────────────────────────────────────────────────────

def get_bonded_devices(bus):
    manager = dbus.Interface(bus.get_object(BLUEZ_SERVICE, "/"), OBJ_MANAGER_IFACE)
    bonded = []
    for path, ifaces in manager.GetManagedObjects().items():
        if DEVICE_IFACE in ifaces:
            d = ifaces[DEVICE_IFACE]
            if d.get("Paired", False):
                bonded.append({
                    "path":    path,
                    "name":    str(d.get("Name", d.get("Alias", "Unknown"))),
                    "address": str(d.get("Address", "??")),
                })
    return bonded


def set_adapter(bus, discoverable, pairable):
    props = dbus.Interface(bus.get_object(BLUEZ_SERVICE, ADAPTER_PATH), PROPS_IFACE)
    props.Set(ADAPTER_IFACE, "Discoverable", dbus.Boolean(discoverable))
    props.Set(ADAPTER_IFACE, "Pairable",     dbus.Boolean(pairable))
    if discoverable:
        props.Set(ADAPTER_IFACE, "DiscoverableTimeout", dbus.UInt32(0))


def adapter_supports_le_advertising(bus):
    manager = dbus.Interface(bus.get_object(BLUEZ_SERVICE, "/"), OBJ_MANAGER_IFACE)
    return LE_ADV_MANAGER_IFACE in manager.GetManagedObjects().get(ADAPTER_PATH, {})


def _register_advertisement(ad_mgr, timeout_seconds=15):
    """Register LE advertisement asynchronously (synchronous call can deadlock BlueZ)."""
    wait_loop = GLib.MainLoop()
    state = {"ok": False, "error": None}

    def on_reply():
        state["ok"] = True
        wait_loop.quit()

    def on_error(err):
        state["error"] = err
        wait_loop.quit()

    def on_timeout():
        state["error"] = TimeoutError(f"RegisterAdvertisement timed out after {timeout_seconds}s")
        wait_loop.quit()
        return False

    timeout_id = GLib.timeout_add_seconds(timeout_seconds, on_timeout)
    ad_mgr.RegisterAdvertisement(ADV_PATH, {}, reply_handler=on_reply, error_handler=on_error)
    wait_loop.run()
    try:
        GLib.source_remove(timeout_id)
    except Exception:
        pass
    if state["ok"]:
        return
    err = state["error"] or RuntimeError("Unknown LE advertisement failure")
    raise err if isinstance(err, Exception) else RuntimeError(str(err))


# ── BlueZ Agent1 ───────────────────────────────────────────────────────────────

class DawgglesAgent(dbus.service.Object):
    """
    BlueZ agent with capability "DisplayYesNo".
    Shows the Numeric Comparison passkey on the OLED and waits for button input:
    front/action button confirms, back/cycle button rejects.
    If no choice is made, confirmation times out after 30 seconds.
    """

    def __init__(self, bus, path, loop, display, confirm_button, cancel_button=None):
        super().__init__(bus, path)
        self._bus           = bus
        self._loop          = loop
        self._display       = display
        self._confirm_button = confirm_button
        self._cancel_button  = cancel_button
        self._decision_lock = threading.Lock()
        self._active_confirmation = None

    @staticmethod
    def _as_bluez_error(message: str, error_name: str):
        return dbus.exceptions.DBusException(message, name=error_name)

    def _clear_button_callbacks(self):
        self._confirm_button.update_callback(None)
        if self._cancel_button is not None:
            self._cancel_button.update_callback(None)

    def _complete_active_confirmation(self, accepted: bool, reason: str) -> bool:
        """Complete one in-flight confirmation. Returns True only when one was active."""
        with self._decision_lock:
            pending = self._active_confirmation
            if not pending or pending.get("done"):
                return False
            pending["done"] = True
            self._active_confirmation = None

        timeout_id = pending.get("timeout_id")
        if timeout_id is not None:
            try:
                GLib.source_remove(timeout_id)
            except Exception:
                pass

        signal_cb = pending.get("signal_callback")
        if signal_cb is not None:
            try:
                self._bus.remove_signal_receiver(
                    signal_cb,
                    signal_name="PropertiesChanged",
                    dbus_interface=PROPS_IFACE,
                )
            except Exception:
                pass

        self._clear_button_callbacks()

        code = pending["code"]
        reply_handler = pending["reply_handler"]
        error_handler = pending["error_handler"]

        if accepted:
            log.info("BLE pairing: confirmed by front button")
            self._display.show_pairing_confirmed(code)
            try:
                reply_handler()
            except Exception as e:
                log.warning("BLE pairing: confirm reply failed: %s", e)
            return True

        self._display.show_pairing_waiting()
        if reason == "remote_cancel":
            log.info("BLE pairing: cancelled by iOS — still advertising")
            err = self._as_bluez_error("Canceled", "org.bluez.Error.Canceled")
        elif reason == "remote_disconnect":
            log.info("BLE pairing: iOS disconnected during confirmation — still advertising")
            err = self._as_bluez_error("Canceled", "org.bluez.Error.Canceled")
        elif reason == "timeout":
            log.info("BLE pairing: confirmation timed out — still advertising")
            err = self._as_bluez_error("Timed out", "org.bluez.Error.Rejected")
        elif reason == "back_button":
            log.info("BLE pairing: rejected by button — still advertising")
            err = self._as_bluez_error("Rejected", "org.bluez.Error.Rejected")
        else:
            log.info("BLE pairing: rejected — still advertising")
            err = self._as_bluez_error("Rejected", "org.bluez.Error.Rejected")

        try:
            error_handler(err)
        except Exception as e:
            log.warning("BLE pairing: reject reply failed: %s", e)
        return True

    def _complete_active_confirmation_idle(self, accepted: bool, reason: str):
        self._complete_active_confirmation(accepted, reason)
        return False

    @dbus.service.method(AGENT_IFACE)
    def Release(self):
        pass

    @dbus.service.method(
        AGENT_IFACE,
        in_signature="ou",
        async_callbacks=("reply_handler", "error_handler"),
    )
    def RequestConfirmation(self, device, passkey, reply_handler, error_handler):
        code = f"{int(passkey):06d}"
        log.info(
            "BLE pairing: code %s — front confirms, back rejects (%ds timeout)",
            code,
            _CONFIRM_TIMEOUT_SECS,
        )

        # Defensive reset: if a stale confirmation is left behind, reject it.
        self._complete_active_confirmation(False, "stale")

        pending = {
            "code": code,
            "device": str(device),
            "reply_handler": reply_handler,
            "error_handler": error_handler,
            "timeout_id": None,
            "signal_callback": None,
            "done": False,
        }

        with self._decision_lock:
            self._active_confirmation = pending

        def on_confirm(click_count):
            if click_count >= 1:
                GLib.idle_add(self._complete_active_confirmation_idle, True, "front_button")

        def on_cancel(click_count):
            if click_count >= 1:
                GLib.idle_add(self._complete_active_confirmation_idle, False, "back_button")

        self._confirm_button.update_callback(on_confirm)
        if self._cancel_button is not None:
            self._cancel_button.update_callback(on_cancel)
        self._display.show_pairing_code(code)

        def on_device_props_changed(iface, changed, invalidated, path=None):
            if iface != DEVICE_IFACE or str(path) != str(device):
                return
            # Some iOS dismissal flows never trigger Agent1.Cancel, but do drop
            # the underlying device connection while confirmation is pending.
            if "Connected" in changed and not bool(changed.get("Connected", True)):
                GLib.idle_add(self._complete_active_confirmation_idle, False, "remote_disconnect")

        pending["signal_callback"] = on_device_props_changed
        self._bus.add_signal_receiver(
            on_device_props_changed,
            signal_name="PropertiesChanged",
            dbus_interface=PROPS_IFACE,
            path_keyword="path",
        )

        def on_timeout():
            self._complete_active_confirmation(False, "timeout")
            return False

        timeout_id = GLib.timeout_add_seconds(_CONFIRM_TIMEOUT_SECS, on_timeout)
        with self._decision_lock:
            if self._active_confirmation is pending and not pending.get("done"):
                pending["timeout_id"] = timeout_id
            else:
                try:
                    GLib.source_remove(timeout_id)
                except Exception:
                    pass

    @dbus.service.method(AGENT_IFACE, in_signature="o", out_signature="s")
    def RequestPinCode(self, device):
        raise dbus.exceptions.DBusException("Not supported", name="org.bluez.Error.Rejected")

    @dbus.service.method(AGENT_IFACE, in_signature="os")
    def DisplayPinCode(self, device, pincode):
        log.info("BLE: DisplayPinCode %s", pincode)

    @dbus.service.method(AGENT_IFACE, in_signature="o", out_signature="u")
    def RequestPasskey(self, device):
        raise dbus.exceptions.DBusException("Not supported", name="org.bluez.Error.Rejected")

    @dbus.service.method(AGENT_IFACE, in_signature="ouq")
    def DisplayPasskey(self, device, passkey, entered):
        log.info("BLE: DisplayPasskey %06d (entered: %d)", int(passkey), int(entered))

    @dbus.service.method(AGENT_IFACE, in_signature="o")
    def RequestAuthorization(self, device):
        pass

    @dbus.service.method(AGENT_IFACE, in_signature="os")
    def AuthorizeService(self, device, uuid):
        pass

    @dbus.service.method(AGENT_IFACE)
    def Cancel(self):
        log.info("BLE: pairing cancelled by remote device")
        if not self._complete_active_confirmation(False, "remote_cancel"):
            self._display.show_pairing_waiting()


# ── LE Advertisement ───────────────────────────────────────────────────────────

class DawgglesAdvertisement(dbus.service.Object):
    """LE advertisement for AccessorySetupKit discovery."""

    def __init__(self, bus, path):
        super().__init__(bus, path)
        self._properties = {
            LE_ADV_IFACE: {
                "Type":           dbus.String("peripheral"),
                "ServiceUUIDs":   dbus.Array([dbus.String(PAIR_SERVICE_UUID)], signature="s"),
                "LocalName":      dbus.String(DEVICE_NAME),
                "IncludeTxPower": dbus.Boolean(True),
            }
        }

    @dbus.service.method(PROPS_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        if interface != LE_ADV_IFACE:
            raise dbus.exceptions.DBusException(
                "InvalidArguments", name="org.freedesktop.DBus.Error.InvalidArgs"
            )
        return self._properties[LE_ADV_IFACE]

    @dbus.service.method(LE_ADV_IFACE)
    def Release(self):
        pass


# ── Public API ─────────────────────────────────────────────────────────────────

def is_paired() -> bool:
    """Return True if at least one device is already BLE-bonded to this adapter."""
    try:
        bus = dbus.SystemBus()
        return len(get_bonded_devices(bus)) > 0
    except Exception as e:
        log.warning("is_paired check failed: %s", e)
        return False


def unpair_all() -> bool:
    """Remove every BLE bond from the adapter. Returns True on success."""
    try:
        bus = dbus.SystemBus()
        devices = get_bonded_devices(bus)
        if not devices:
            log.info("unpair_all: no bonded devices")
            return True
        adapter = dbus.Interface(
            bus.get_object(BLUEZ_SERVICE, ADAPTER_PATH), ADAPTER_IFACE
        )
        for d in devices:
            try:
                adapter.RemoveDevice(d["path"])
                log.info("Removed bonded device: %s (%s)", d["name"], d["address"])
            except Exception as e:
                log.warning("Could not remove %s: %s", d["path"], e)
        return True
    except Exception as e:
        log.error("unpair_all failed: %s", e)
        return False


def perform_unpair_and_restart(shared_class) -> None:
    """Full unpair: drop BLE bonds, drop any active WebSocket, then re-exec
    the process so main.py falls back into the pairing flow. Used by both
    the on-device Settings app and the iOS-initiated unpair message."""
    import os
    import sys
    import time

    display = getattr(shared_class, "display", None)
    if display is not None:
        try:
            display.show_temporary_message(["Unpairing...", "Please wait."], duration=10.0)
        except Exception as e:
            log.warning("unpair: could not show banner: %s", e)

    ok = unpair_all()
    if not ok:
        if display is not None:
            try:
                display.show_temporary_message(["Unable to unpair.", "Please try again."], duration=3.0)
            except Exception:
                pass
        return

    try:
        with open("/tmp/dawggles_force_pair", "w") as f:
            f.write("1")
    except Exception as e:
        log.warning("Could not write force-pair sentinel: %s", e)

    if display is not None:
        try:
            display.show_temporary_message(["Unpaired.", "Restarting..."], duration=10.0)
        except Exception:
            pass
    time.sleep(2.5)

    # Release hardware before re-exec so the new process can claim it cleanly.
    # Each step is best-effort — we always re-exec regardless of outcome.
    camera_client = getattr(shared_class, "camera_client", None)
    if camera_client is not None:
        try:
            camera_client.stop_stream_thread()
        except Exception as e:
            log.warning("unpair cleanup: stop_stream_thread: %s", e)
        camera = getattr(camera_client, "camera", None)
        if camera is not None:
            for method in ("stop", "close"):
                try:
                    getattr(camera, method)()
                except Exception as e:
                    log.warning("unpair cleanup: camera.%s: %s", method, e)

    for attr in ("button", "cycle_button"):
        btn_wrapper = getattr(shared_class, attr, None)
        gpio_btn = getattr(btn_wrapper, "btn", None) if btn_wrapper is not None else None
        if gpio_btn is not None:
            try:
                gpio_btn.close()
            except Exception as e:
                log.warning("unpair cleanup: %s.close: %s", attr, e)

    os.execv(sys.executable, [sys.executable] + sys.argv)


def run_pairing_flow(display, confirm_button, cancel_button=None, shutdown_event=None) -> bool:
    """
    Advertise BLE as "Dawggles" and wait for an iPhone to complete pairing.

    When iOS initiates Numeric Comparison, the 6-digit code is shown on the OLED.
    The user presses the front/action button to confirm or the back/cycle button
    to reject, and the device keeps advertising for the next attempt.

    Blocks until pairing succeeds (returns True) or a fatal BLE setup error
    occurs (returns False).
    """
    try:
        bus = dbus.SystemBus()
    except Exception as e:
        log.error("BLE: cannot connect to D-Bus: %s", e)
        display.show_temporary_message(["BLE UNAVAIL", "No D-Bus"], duration=4.0)
        return False

    loop = GLib.MainLoop()
    result = {"success": False}

    # Name the adapter
    try:
        adapter_props = dbus.Interface(
            bus.get_object(BLUEZ_SERVICE, ADAPTER_PATH), PROPS_IFACE
        )
        adapter_props.Set(ADAPTER_IFACE, "Alias", DEVICE_NAME)
    except Exception as e:
        log.warning("BLE: could not set adapter alias: %s", e)

    # Register agent
    agent = DawgglesAgent(bus, AGENT_PATH, loop, display, confirm_button, cancel_button)
    agent_mgr = dbus.Interface(
        bus.get_object(BLUEZ_SERVICE, BLUEZ_PATH), AGENT_MANAGER_IFACE
    )
    try:
        agent_mgr.RegisterAgent(AGENT_PATH, "DisplayYesNo")
        agent_mgr.RequestDefaultAgent(AGENT_PATH)
        log.info("BLE: agent registered (DisplayYesNo → Numeric Comparison)")
    except Exception as e:
        log.error("BLE: agent registration failed: %s", e)
        display.show_temporary_message(["BLE ERROR", "Agent fail"], duration=4.0)
        return False

    # LE advertising
    if not adapter_supports_le_advertising(bus):
        log.error("BLE: adapter has no LEAdvertisingManager1")
        display.show_temporary_message(["BLE ERROR", "No LE advert"], duration=4.0)
        try:
            agent_mgr.UnregisterAgent(AGENT_PATH)
        except Exception:
            pass
        return False

    ad_mgr = dbus.Interface(
        bus.get_object(BLUEZ_SERVICE, ADAPTER_PATH), LE_ADV_MANAGER_IFACE
    )
    advertisement = DawgglesAdvertisement(bus, ADV_PATH)
    try:
        _register_advertisement(ad_mgr, timeout_seconds=15)
        log.info("BLE: advertising as '%s' (service %s)", DEVICE_NAME, PAIR_SERVICE_UUID)
    except Exception as e:
        log.error("BLE: advertisement failed: %s", e)
        display.show_temporary_message(["BLE ERROR", "Advert fail"], duration=4.0)
        try:
            agent_mgr.UnregisterAgent(AGENT_PATH)
        except Exception:
            pass
        return False

    # Watch for the Paired property flipping True
    def on_props_changed(iface, changed, invalidated, path=None):
        if iface != DEVICE_IFACE or not changed.get("Paired", False):
            return
        try:
            dev_props = dbus.Interface(bus.get_object(BLUEZ_SERVICE, path), PROPS_IFACE)
            dev_props.Set(DEVICE_IFACE, "Trusted", dbus.Boolean(True))
            name = str(dev_props.Get(DEVICE_IFACE, "Name"))
            log.info("BLE: paired with '%s' — marked trusted", name)
        except Exception:
            log.info("BLE: pairing complete")
        result["success"] = True
        loop.quit()

    bus.add_signal_receiver(
        on_props_changed,
        signal_name="PropertiesChanged",
        dbus_interface=PROPS_IFACE,
        path_keyword="path",
    )

    set_adapter(bus, discoverable=True, pairable=True)
    display.show_pairing_waiting()
    log.info("BLE: discoverable — open the Dawggles app and tap 'Pair'")

    def _quit_on_signal():
        log.info("BLE: signal received, stopping pairing")
        if shutdown_event is not None:
            shutdown_event.set()
        loop.quit()
        return GLib.SOURCE_REMOVE

    GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGTERM, _quit_on_signal)
    GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGINT, _quit_on_signal)

    try:
        loop.run()
    except KeyboardInterrupt:
        log.info("BLE: pairing cancelled (interrupt)")
    finally:
        try:
            set_adapter(bus, discoverable=False, pairable=False)
        except Exception:
            pass
        try:
            ad_mgr.UnregisterAdvertisement(ADV_PATH)
        except Exception:
            pass
        try:
            agent_mgr.UnregisterAgent(AGENT_PATH)
        except Exception:
            pass

    return result["success"]


# ── Standalone CLI (original behaviour) ───────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)

    bus = dbus.SystemBus()
    bonded = get_bonded_devices(bus)
    if bonded:
        print("Already paired — run unpair.py first.")
        for d in bonded:
            print(f"  {d['name']}  ({d['address']})")
        sys.exit(1)

    class _CliDisplay:
        def show_pairing_waiting(self):
            print("Advertising… open the Dawggles app and tap 'Pair Dawggles'.")
        def show_pairing_code(self, code):
            print(f"\n┌─────────────────────────────┐")
            print(f"│   PAIRING CODE:  {code}   │")
            print(f"└─────────────────────────────┘")
            print("Press front button to confirm, back button to cancel.")
        def show_pairing_confirmed(self, code):
            print(f"Code confirmed: {code}")
        def show_temporary_message(self, lines, duration=2.0):
            print(" | ".join(lines))

    class _CliButton:
        def update_callback(self, cb):
            if cb is None:
                return
            try:
                input("Press Enter to confirm (or Ctrl+C to reject): ")
                cb(1)
            except (KeyboardInterrupt, EOFError):
                pass

    success = run_pairing_flow(_CliDisplay(), _CliButton())
    sys.exit(0 if success else 1)
