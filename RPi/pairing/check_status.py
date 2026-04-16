#!/usr/bin/env python3
"""
check_status.py — Show Bluetooth adapter state and all known/bonded devices.

System packages required (not pip):
    sudo apt install python3-dbus

Usage:
    python3 check_status.py
"""

import dbus

BLUEZ_SERVICE     = "org.bluez"
ADAPTER_PATH      = "/org/bluez/hci0"
ADAPTER_IFACE     = "org.bluez.Adapter1"
DEVICE_IFACE      = "org.bluez.Device1"
PROPS_IFACE       = "org.freedesktop.DBus.Properties"
OBJ_MANAGER_IFACE = "org.freedesktop.DBus.ObjectManager"

SEP = "─" * 46


def yn(val):
    return "yes" if val else "no "


def main():
    bus = dbus.SystemBus()

    # ── Adapter ────────────────────────────────────────────────────────────────
    adapter_props = dbus.Interface(
        bus.get_object(BLUEZ_SERVICE, ADAPTER_PATH), PROPS_IFACE
    )
    a = adapter_props.GetAll(ADAPTER_IFACE)

    print(SEP)
    print("  ADAPTER")
    print(SEP)
    print(f"  Name          {a.get('Name',  '?')}")
    print(f"  Alias         {a.get('Alias', '?')}")
    print(f"  Address       {a.get('Address', '?')}")
    print(f"  Powered       {yn(a.get('Powered',      False))}")
    print(f"  Discoverable  {yn(a.get('Discoverable', False))}")
    print(f"  Pairable      {yn(a.get('Pairable',     False))}")

    # ── Known devices ──────────────────────────────────────────────────────────
    manager = dbus.Interface(bus.get_object(BLUEZ_SERVICE, "/"), OBJ_MANAGER_IFACE)
    objects = manager.GetManagedObjects()

    devices = [
        (path, ifaces[DEVICE_IFACE])
        for path, ifaces in objects.items()
        if DEVICE_IFACE in ifaces
    ]
    devices.sort(key=lambda x: str(x[1].get("Name", x[1].get("Alias", ""))))

    print(f"\n{SEP}")
    print(f"  KNOWN DEVICES  ({len(devices)})")
    print(SEP)

    paired_name = None

    if not devices:
        print("  (none)")
    else:
        for path, d in devices:
            name      = str(d.get("Name",      d.get("Alias", "Unknown")))
            address   = str(d.get("Address",   "??"))
            paired    = bool(d.get("Paired",    False))
            connected = bool(d.get("Connected", False))
            trusted   = bool(d.get("Trusted",   False))

            print(f"\n  {name}  ({address})")
            print(f"    Paired     {yn(paired)}")
            print(f"    Connected  {yn(connected)}")
            print(f"    Trusted    {yn(trusted)}")

            if paired:
                paired_name = name

    # ── Summary ────────────────────────────────────────────────────────────────
    print(f"\n{SEP}")
    if paired_name:
        print(f"  STATUS  ✅  Paired to \"{paired_name}\"")
    else:
        print("  STATUS  —   No paired device")
    print(SEP)


if __name__ == "__main__":
    main()
