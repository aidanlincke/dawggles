#!/usr/bin/env python3
"""
unpair.py — Remove the paired device from the Pi's BlueZ bond store.

System packages required (not pip):
    sudo apt install python3-dbus

Usage:
    python3 unpair.py

NOTE: This cleans up the Pi side only. Also go to iPhone Settings > Bluetooth,
tap the (i) next to Dawggles, and tap "Forget This Device" to clean up iOS.
"""

import sys
import dbus

BLUEZ_SERVICE     = "org.bluez"
ADAPTER_PATH      = "/org/bluez/hci0"
ADAPTER_IFACE     = "org.bluez.Adapter1"
DEVICE_IFACE      = "org.bluez.Device1"
OBJ_MANAGER_IFACE = "org.freedesktop.DBus.ObjectManager"


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


def main():
    bus    = dbus.SystemBus()
    bonded = get_bonded_devices(bus)

    if not bonded:
        print("No paired device found — nothing to do.")
        sys.exit(0)

    adapter = dbus.Interface(
        bus.get_object(BLUEZ_SERVICE, ADAPTER_PATH), ADAPTER_IFACE
    )

    all_ok = True
    for device in bonded:
        print(f"Removing: {device['name']}  ({device['address']})")
        try:
            adapter.RemoveDevice(device["path"])
            print("✅  Removed.")
        except dbus.exceptions.DBusException as e:
            print(f"❌  Failed: {e}")
            all_ok = False

    print()
    if all_ok:
        print("Pi side is clean.")
    else:
        print("Some devices could not be removed. Try running with sudo.")
        sys.exit(1)

    print("Reminder: also go to iPhone Settings > Bluetooth → tap ⓘ next to Dawggles → Forget This Device.")


if __name__ == "__main__":
    main()
