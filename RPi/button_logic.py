import subprocess

# Replace with the MAC address of your specific device
DEVICE_MAC = "XX:XX:XX:XX:XX:XX"

def is_device_paired(mac_address):
    """
    Runs 'bluetoothctl info' and checks if the device is paired.
    """
    try:
        # Run the command and capture the output
        result = subprocess.run(
            ["bluetoothctl", "info", mac_address],
            capture_output=True,
            text=True,
            check=False
        )
        # Check if the specific pairing string is in the output
        if "Paired: yes" in result.stdout:
            return True
        return False
        
    except FileNotFoundError:
        print("Error: bluetoothctl not found. Is BlueZ installed?")
        return False
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return False

def enable_pairing_mode():
    """
    Executes the sequence of commands to make the Pi discoverable and pairable.
    """
    print("Enabling Bluetooth and entering pairing mode...")
    
    # List of commands to run sequentially
    commands = [
        ["bluetoothctl", "power", "on"],
        ["bluetoothctl", "agent", "DisplayOnly"], # Or 'on' depending on your device
        ["bluetoothctl", "default-agent"],
        ["bluetoothctl", "pairable", "on"],
        ["bluetoothctl", "discoverable", "on"]
    ]
    
    for cmd in commands:
        try:
            # We don't necessarily need the output here, just need them to run
            subprocess.run(cmd, check=True, capture_output=True)
            print(f"  -> Executed: {' '.join(cmd)}")
        except subprocess.CalledProcessError as e:
            print(f"  -> Failed to execute {' '.join(cmd)}: {e}")

def main():
    if is_device_paired(DEVICE_MAC):
        print("Device is paired. Executing alternate action...")
        
        # -----------------------------------------
        # PUT YOUR "SOMETHING ELSE" LOGIC HERE
        # Example: Disconnect the device
        # subprocess.run(["bluetoothctl", "disconnect", DEVICE_MAC])
        # -----------------------------------------
        
    else:
        print("Device is NOT paired.")
        enable_pairing_mode()
        
        # Optional: Automatically try to pair
        # print(f"Attempting to pair with {DEVICE_MAC}...")
        # subprocess.run(["bluetoothctl", "pair", DEVICE_MAC])

if __name__ == "__main__":
    main()