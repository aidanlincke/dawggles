"""
GPS App - Stores its own GPS data and display updates
"""
from apps.base_app import BaseApp

class GPSApp(BaseApp):
    name = "gps"
    label = "Navigate"

    def __init__(self, shared_class):
        super().__init__(shared_class)
        self.gps_data = None

    def on_mount(self):
        # Stop camera stream since GPS doesn't need it
        self.shared_class.camera_streaming = False
        self.update_display()

    def on_unmount(self):
        pass

    def update_display(self):
        self.shared_class.display.update_display({
            "app": "gps"
        })

    def _draw_gps_icon(self, display, icon_type, x, y):
        """Draws a simple 12x12 vector icon for GPS instructions."""
        icon_type = str(icon_type).lower()
        oled = display.oled
        
        if icon_type == "turn_left":
            oled.hline(x + 0, y + 2, 6, 1)
            oled.vline(x + 0, y + 2, 6, 1)
            oled.line(x + 0, y + 2, x + 4, y + 6, 1)
            oled.hline(x + 0, y + 8, 8, 1)
        elif icon_type == "turn_right":
            oled.hline(x + 2, y + 2, 6, 1)
            oled.vline(x + 8, y + 2, 6, 1)
            oled.line(x + 8, y + 2, x + 4, y + 6, 1)
            oled.hline(x + 0, y + 8, 8, 1)
        elif icon_type == "straight":
            oled.line(x + 4, y + 2, x + 1, y + 5, 1)
            oled.line(x + 4, y + 2, x + 7, y + 5, 1)
            oled.vline(x + 4, y + 2, 7, 1)
        elif icon_type in ("subway", "train"):
            oled.rect(x + 0, y + 2, 8, 8, 1)
            oled.rect(x + 1, y + 4, 2, 2, 1)
            oled.rect(x + 5, y + 4, 2, 2, 1)
            oled.pixel(x + 1, y + 10, 1)
            oled.pixel(x + 6, y + 10, 1)
        elif icon_type == "bus":
            oled.rect(x + 0, y + 3, 10, 6, 1)
            oled.rect(x + 1, y + 4, 2, 2, 1)
            oled.rect(x + 4, y + 4, 2, 2, 1)
            oled.rect(x + 7, y + 4, 2, 2, 1)
            oled.pixel(x + 2, y + 9, 1)
            oled.pixel(x + 7, y + 9, 1)
        elif icon_type == "walk":
            oled.pixel(x + 4, y + 2, 1)
            oled.vline(x + 4, y + 3, 5, 1)
            oled.line(x + 4, y + 4, x + 1, y + 6, 1)
            oled.line(x + 4, y + 4, x + 7, y + 6, 1)
            oled.line(x + 4, y + 8, x + 2, y + 11, 1)
            oled.line(x + 4, y + 8, x + 6, y + 11, 1)
        else:
            oled.fill_rect(x + 2, y + 4, 4, 4, 1)

    def render_display(self, display):
        if not display.hardware_available:
            return

        display.oled.fill(0)
        display.draw_app_header(self.label)

        if not self.gps_data:
            display.oled.text("Enter destination in", 0, 26, 1)
            display.oled.text("Dawggles app", 0, 38, 1)
            display.oled.show()
            return

        # Nav info row (Y=14 to 26)
        icon_type = self.gps_data.get("icon_type", "")
        self._draw_gps_icon(display, icon_type, 0, 14)

        distance_str = str(self.gps_data.get("distance", ""))[:5]
        if distance_str:
            display.oled.text(distance_str, 13, 16, 1)

        street_str = str(self.gps_data.get("street", ""))[:12]
        if street_str:
            display.oled.text(street_str, 48, 16, 1)

        # Minimap Area (Y=29 to 63)
        display.oled.line(64, 50, 60, 58, 1)
        display.oled.line(60, 58, 68, 58, 1)
        display.oled.line(68, 58, 64, 50, 1)

        lines = self.gps_data.get("lines", [])
        for line in lines:
            if len(line) == 4:
                x1, y1, x2, y2 = line
                display.oled.line(int(x1), int(y1), int(x2), int(y2), 1)

        display.oled.show()

    def on_click(self, click_count):
        pass

    def on_message(self, message):
        if "data" in message:
            self.gps_data = message.get("data")
            self.update_display()
