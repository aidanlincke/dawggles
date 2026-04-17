#!/usr/bin/env python3
"""
pairing/pair.py — BLE pairing for Dawggles (BlueZ Numeric Comparison).

Exposes two public functions for use by main.py:

    is_paired()                        → bool
    run_pairing_flow(display, button)  → bool   (True = paired successfully)

System packages required (not pip):
    sudo apt install python3-dbus python3-gi
"""

import sys
import threading
import time
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
    Shows the Numeric Comparison passkey on the OLED and waits for a button press
    (or a 30-second timeout) to confirm or reject the pairing.
    """

    def __init__(self, bus, path, loop, display, button):
        super().__init__(bus, path)
        self._loop    = loop
        self._display = display
        self._button  = button

    @dbus.service.method(AGENT_IFACE)
    def Release(self):
        pass

    @dbus.service.method(AGENT_IFACE, in_signature="ou")
    def RequestConfirmation(self, device, passkey):
        code = f"{int(passkey):06d}"
        log.info("BLE pairing: code %s — press button to confirm (%ds timeout)",
                 code, _CONFIRM_TIMEOUT_SECS)

        confirmed = threading.Event()

        def on_button(click_count):
            if click_count >= 1:
                confirmed.set()

        self._button.update_callback(on_button)
        self._display.show_pairing_code(code)

        try:
            accepted = confirmed.wait(timeout=_CONFIRM_TIMEOUT_SECS)
        finally:
            self._button.update_callback(None)

        if not accepted:
            log.info("BLE pairing: timed out — rejecting, still advertising")
            self._display.show_pairing_waiting()
            raise dbus.exceptions.DBusException(
                "Timed out", name="org.bluez.Error.Rejected"
            )

        log.info("BLE pairing: confirmed by button press")

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


def run_pairing_flow(display, button) -> bool:
    """
    Advertise BLE as "Dawggles" and wait for an iPhone to complete pairing.

    When iOS initiates Numeric Comparison, the 6-digit code is shown on the OLED.
    The user presses the action button to confirm; the pairing is rejected after
    30 seconds of inactivity and the device keeps advertising for the next attempt.

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
    agent = DawgglesAgent(bus, AGENT_PATH, loop, display, button)
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
            display.show_temporary_message(["PAIRED!", name[:12]], duration=2.0)
        except Exception:
            log.info("BLE: pairing complete")
            display.show_temporary_message(["PAIRED!"], duration=2.0)
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
            print("Check that this matches the code on your iPhone.")
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
