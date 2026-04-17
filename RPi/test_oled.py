import board
import busio
import digitalio
import adafruit_ssd1306
import time

# SSD1306 command: segment remap (horizontal flip vs driver default 0xA1)
_SET_SEG_REMAP_A0 = 0xA0


def display_text(disp, message: str, color: int = 1) -> None:
    """Center a string with the framebuffer 8x8 font."""
    disp.fill(0)
    tw = len(message) * 8
    tx = max(0, (disp.width - tw) // 2)
    ty = max(0, (disp.height - 8) // 2)
    disp.text(message, tx, ty, color)
    disp.show()

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
print("Lorem fill test, contrast=20, SEG remap 0xA0. Ctrl+C to stop.")

display.contrast(20)
display.write_cmd(_SET_SEG_REMAP_A0)

display.fill(0)
lorem = "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut"
# 16 chars per row, 8 rows = 128 chars total
for row in range(8):
    display.text(lorem[row * 16 : (row + 1) * 16], 0, row * 8, 1)
display.show()

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    display.fill(0)
    display.show()
    print("Exiting.")
