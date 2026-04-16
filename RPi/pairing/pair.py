#!/usr/bin/env python3
"""
pair.py — Advertise "Dawggles" over BLE and pair with one iPhone via Numeric Comparison.

System packages required (not pip):
    sudo apt install python3-dbus python3-gi

Usage:
    python3 pair.py
    # If permission errors: sudo python3 pair.py
    # Or add your user to the bluetooth group: sudo usermod -aG bluetooth $USER

The Pi will appear as "Dawggles" in iPhone Settings > Bluetooth.
Tap it, confirm the 6-digit code matches on both screens, done.
"""

import sys
import dbus
import dbus.service
import dbus.mainloop.glib
from gi.repository import GLib

# ── BlueZ D-Bus constants ──────────────────────────────────────────────────────
BLUEZ_SERVICE       = "org.bluez"
BLUEZ_PATH          = "/org/bluez"
ADAPTER_PATH        = "/org/bluez/hci0"
ADAPTER_IFACE       = "org.bluez.Adapter1"
DEVICE_IFACE        = "org.bluez.Device1"
AGENT_MANAGER_IFACE = "org.bluez.AgentManager1"
AGENT_IFACE         = "org.bluez.Agent1"
PROPS_IFACE         = "org.freedesktop.DBus.Properties"
OBJ_MANAGER_IFACE   = "org.freedesktop.DBus.ObjectManager"

AGENT_PATH  = "/org/dawggles/agent"
DEVICE_NAME = "Dawggles"


# ── Helpers ────────────────────────────────────────────────────────────────────

def get_bonded_devices(bus):
    manager = dbus.Interface(bus.get_object(BLUEZ_SERVICE, "/"), OBJ_MANAGER_IFACE)
    objects = manager.GetManagedObjects()
    bonded = []
    for path, ifaces in objects.items():
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
        # 0 = no timeout, stay discoverable until we turn it off manually
        props.Set(ADAPTER_IFACE, "DiscoverableTimeout", dbus.UInt32(0))


# ── BlueZ Agent1 implementation ────────────────────────────────────────────────

class DawgglesAgent(dbus.service.Object):
    """
    BlueZ agent with capability "DisplayYesNo".

    When iOS initiates pairing, BlueZ calls RequestConfirmation() with a
    6-digit code that is independently derived from the ECDH key exchange on
    both sides. If the codes match, the link is authenticated (MITM-protected).
    The user confirms on each device — no code is typed, just verified visually.
    """

    def __init__(self, bus, path, loop):
        super().__init__(bus, path)
        self._loop = loop

    @dbus.service.method(AGENT_IFACE)
    def Release(self):
        print("Agent released by BlueZ.")

    # ── Numeric Comparison — the method that actually fires ────────────────────
    @dbus.service.method(AGENT_IFACE, in_signature="ou")
    def RequestConfirmation(self, device, passkey):
        code = int(passkey)
        print()
        print("┌─────────────────────────────┐")
        print(f"│   PAIRING CODE:  {code:06d}   │")
        print("└─────────────────────────────┘")
        print("Check that this matches the code on your iPhone.")
        print("Press Enter to confirm, or type 'no' + Enter to reject.\n")
        try:
            answer = input("> ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print("\nRejecting.")
            raise dbus.exceptions.DBusException(
                "Rejected", name="org.bluez.Error.Rejected"
            )
        if answer == "no":
            raise dbus.exceptions.DBusException(
                "Rejected by user", name="org.bluez.Error.Rejected"
            )
        print("Confirmed — finishing pairing handshake...")

    # ── Remaining Agent1 methods (required by interface, not used for our flow) ─

    @dbus.service.method(AGENT_IFACE, in_signature="o", out_signature="s")
    def RequestPinCode(self, device):
        raise dbus.exceptions.DBusException(
            "Not supported", name="org.bluez.Error.Rejected"
        )

    @dbus.service.method(AGENT_IFACE, in_signature="os")
    def DisplayPinCode(self, device, pincode):
        print(f"[PIN code: {pincode}]")

    @dbus.service.method(AGENT_IFACE, in_signature="o", out_signature="u")
    def RequestPasskey(self, device):
        raise dbus.exceptions.DBusException(
            "Not supported", name="org.bluez.Error.Rejected"
        )

    @dbus.service.method(AGENT_IFACE, in_signature="ouq")
    def DisplayPasskey(self, device, passkey, entered):
        # Fallback if Numeric Comparison somehow isn't negotiated
        print(f"[Passkey: {int(passkey):06d}  entered so far: {int(entered)}]")

    @dbus.service.method(AGENT_IFACE, in_signature="o")
    def RequestAuthorization(self, device):
        pass  # accept

    @dbus.service.method(AGENT_IFACE, in_signature="os")
    def AuthorizeService(self, device, uuid):
        pass  # accept

    @dbus.service.method(AGENT_IFACE)
    def Cancel(self):
        print("Pairing request cancelled by remote device.")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    # Must be called before creating any D-Bus connections
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus  = dbus.SystemBus()
    loop = GLib.MainLoop()

    # ── 1. Enforce single pairing ──────────────────────────────────────────────
    bonded = get_bonded_devices(bus)
    if bonded:
        print("Already paired to a device — run unpair.py first.\n")
        for d in bonded:
            print(f"  {d['name']}  ({d['address']})")
        sys.exit(1)

    # ── 2. Set the adapter name ────────────────────────────────────────────────
    adapter_props = dbus.Interface(
        bus.get_object(BLUEZ_SERVICE, ADAPTER_PATH), PROPS_IFACE
    )
    adapter_props.Set(ADAPTER_IFACE, "Alias", DEVICE_NAME)

    # ── 3. Register our agent with "DisplayYesNo" capability ───────────────────
    #    DisplayYesNo + iOS's KeyboardDisplay → BlueZ negotiates Numeric Comparison
    agent     = DawgglesAgent(bus, AGENT_PATH, loop)
    agent_mgr = dbus.Interface(
        bus.get_object(BLUEZ_SERVICE, BLUEZ_PATH), AGENT_MANAGER_IFACE
    )
    agent_mgr.RegisterAgent(AGENT_PATH, "DisplayYesNo")
    agent_mgr.RequestDefaultAgent(AGENT_PATH)
    print("Agent registered  (capability: DisplayYesNo  →  Numeric Comparison)")

    # ── 4. Listen for the Paired property flipping True ────────────────────────
    def on_props_changed(iface, changed, invalidated, path=None):
        if iface != DEVICE_IFACE:
            return
        if not changed.get("Paired", False):
            return
        try:
            dev_props = dbus.Interface(
                bus.get_object(BLUEZ_SERVICE, path), PROPS_IFACE
            )
            name = str(dev_props.Get(DEVICE_IFACE, "Name"))
            addr = str(dev_props.Get(DEVICE_IFACE, "Address"))
            print(f"\n✅  Paired with: {name}  ({addr})")
            dev_props.Set(DEVICE_IFACE, "Trusted", dbus.Boolean(True))
            print("✅  Marked as trusted (auto-reconnect enabled)")
        except Exception:
            print("\n✅  Pairing complete!")
        loop.quit()

    bus.add_signal_receiver(
        on_props_changed,
        signal_name="PropertiesChanged",
        dbus_interface=PROPS_IFACE,
        path_keyword="path",
    )

    # ── 5. Go discoverable and block until pairing completes or user cancels ───
    set_adapter(bus, discoverable=True, pairable=True)
    print(f'\nDawggles is discoverable. Open iPhone Settings > Bluetooth and tap "{DEVICE_NAME}".')
    print("Ctrl+C to cancel.\n")

    try:
        loop.run()
    except KeyboardInterrupt:
        print("\nCancelled.")
    finally:
        try:
            set_adapter(bus, discoverable=False, pairable=False)
            agent_mgr.UnregisterAgent(AGENT_PATH)
        except Exception:
            pass
        print("Discoverable mode off.")


if __name__ == "__main__":
    main()
