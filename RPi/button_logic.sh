#!/bin/bash

# Replace with the MAC address of your specific device
DEVICE_MAC="XX:XX:XX:XX:XX:XX"

# Check if the device is paired by looking at bluetoothctl info
# grep -q runs quietly and returns true if "Paired: yes" is found
if bluetoothctl info "$DEVICE_MAC" | grep -q "Paired: yes"; then
    echo "Device is paired. Executing alternate action..."
    
    # -----------------------------------------
    # PUT YOUR "SOMETHING ELSE" LOGIC HERE
    # Example: Disconnect the device, or launch an app
    # bluetoothctl disconnect "$DEVICE_MAC"
    # python3 /home/pi/my_app.py &
    # -----------------------------------------

else
    echo "Device is NOT paired. Entering pairing mode..."
    
    # -----------------------------------------
    # PUT YOUR PAIRING LOGIC HERE
    # This block turns on Bluetooth, makes the Pi discoverable, and pairable
    # -----------------------------------------
    bluetoothctl power on
    bluetoothctl agent DisplayOnly # or 'on'
    bluetoothctl default-agent
    bluetoothctl pairable on
    bluetoothctl discoverable on
    
    # Optional: Automatically try to pair with the device
    # bluetoothctl pair "$DEVICE_MAC"
fi