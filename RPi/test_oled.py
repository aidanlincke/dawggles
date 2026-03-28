# Data - green - pin 19
# Clk - yellow - pin 23
# DC - orange - pin 13
# CS - red - pin 11
# Vin - brown - pin 1
# GND - black - pin 6

import board
import busio
import digitalio
import adafruit_ssd1306
import time

# 1. Initialize the Hardware SPI bus
# board.SCK automatically uses GPIO 11 (Physical Pin 23)
# board.MOSI automatically uses GPIO 10 (Physical Pin 19)
spi = busio.SPI(board.SCK, MOSI=board.MOSI)

# 2. Define your specific arbitrary pins! 
cs = digitalio.DigitalInOut(board.D17)  # GPIO 17 (Physical Pin 11)
dc = digitalio.DigitalInOut(board.D27)  # GPIO 27 (Physical Pin 13)

# We are intentionally leaving the reset pin disconnected to save wires
reset = None 

# 3. Create the display object (for a standard 128x64 resolution OLED)
display = adafruit_ssd1306.SSD1306_SPI(128, 64, spi, dc, reset, cs)

# --- LET'S TEST IT! ---
print("Initializing display...")

# Clear the display (fill with black)
display.fill(0)
display.show()
time.sleep(0.5)

# Fill the entire screen with white pixels
print("Flashing screen white...")
display.fill(1)
display.show()
time.sleep(1)

# Clear it again to black
print("Clearing screen...")
display.fill(0)
display.show()

print("Done! If your screen just flashed white, your SPI wiring is perfect!")