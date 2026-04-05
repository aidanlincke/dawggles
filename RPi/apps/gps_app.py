"""
GPS App - Stores its own GPS data and display updates
"""
from apps.base_app import BaseApp

class GPSApp(BaseApp):
    def __init__(self, shared_class):
        super().__init__(shared_class)
        self.name = "gps"
        self.gps_data = None

    def on_mount(self):
        # Stop camera since GPS doesn't need it
        if self.shared_class.camera_client and self.shared_class.camera_client.running:
            self.shared_class.camera_client.stop_capture_loop()
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
            oled.hline(x + 2, y + 2, 6, 1)
            oled.vline(x + 2, y + 2, 6, 1)
            oled.line(x + 2, y + 2, x + 6, y + 6, 1)
            oled.hline(x + 2, y + 8, 8, 1)
            oled.vline(x + 10, y + 8, 4, 1)
        elif icon_type == "turn_right":
            oled.hline(x + 4, y + 2, 6, 1)
            oled.vline(x + 10, y + 2, 6, 1)
            oled.line(x + 10, y + 2, x + 6, y + 6, 1)
            oled.hline(x + 2, y + 8, 8, 1)
            oled.vline(x + 2, y + 8, 4, 1)
        elif icon_type == "straight":
            oled.hline(x + 4, y + 2, 5, 1)
            oled.line(x + 6, y + 2, x + 3, y + 5, 1)
            oled.line(x + 6, y + 2, x + 9, y + 5, 1)
            oled.vline(x + 6, y + 2, 10, 1)
        elif icon_type in ("subway", "train"):
            oled.rect(x + 2, y + 2, 8, 8, 1)
            oled.rect(x + 3, y + 4, 2, 2, 1)
            oled.rect(x + 7, y + 4, 2, 2, 1)
            oled.pixel(x + 3, y + 10, 1)
            oled.pixel(x + 8, y + 10, 1)
        elif icon_type == "bus":
            oled.rect(x + 1, y + 3, 10, 6, 1)
            oled.rect(x + 2, y + 4, 2, 2, 1)
            oled.rect(x + 5, y + 4, 2, 2, 1)
            oled.rect(x + 8, y + 4, 2, 2, 1)
            oled.pixel(x + 3, y + 9, 1)
            oled.pixel(x + 8, y + 9, 1)
        elif icon_type == "walk":
            oled.pixel(x + 6, y + 2, 1)
            oled.vline(x + 6, y + 3, 5, 1)
            oled.line(x + 6, y + 4, x + 3, y + 6, 1)
            oled.line(x + 6, y + 4, x + 9, y + 6, 1)
            oled.line(x + 6, y + 8, x + 4, y + 11, 1)
            oled.line(x + 6, y + 8, x + 8, y + 11, 1)
        else:
            oled.fill_rect(x + 4, y + 4, 4, 4, 1)

    def render_display(self, display):
        if not display.hardware_available:
            return
            
        display.oled.fill(0)
        
        if not self.gps_data:
            display.oled.show()
            return
            
        # Header Bar (Y=0 to 12)
        icon_type = self.gps_data.get("icon_type", "")
        self._draw_gps_icon(display, icon_type, 0, 0)
        
        distance_str = str(self.gps_data.get("distance", ""))[:5]
        if distance_str:
            display.oled.text(distance_str, 15, 2, 1)
            
        street_str = str(self.gps_data.get("street", ""))[:12]
        if street_str:
            display.oled.text(street_str, 50, 2, 1)
        
        # Horizontal Divider
        display.oled.hline(0, 13, 128, 1)
        
        # Minimap Area (Y=14 to 63)
        # Player Triangle at (64, 55) facing up
        display.oled.line(64, 50, 60, 58, 1)
        display.oled.line(60, 58, 68, 58, 1)
        display.oled.line(68, 58, 64, 50, 1)
        
        # Path Lines
        lines = self.gps_data.get("lines", [])
        for line in lines:
            if len(line) == 4:
                x1, y1, x2, y2 = line
                display.oled.line(int(x1), int(y1), int(x2), int(y2), 1)
                
        display.oled.show()

    def on_click(self, click_count):
        if click_count >= 3:
            from app_manager import switch_to_next_app
            switch_to_next_app(self.shared_class)

    def on_message(self, message):
        if "app" in message and message["app"] != self.name:
            from app_manager import start_app
            start_app(message["app"], self.shared_class)
            # Re-inject the message so the newly active app can process its payload
            if "data" in message and self.shared_class.server:
                self.shared_class.server.message_queue.put(message)
            return
        
        if "data" in message:
            self.gps_data = message.get("data")
            self.update_display()
