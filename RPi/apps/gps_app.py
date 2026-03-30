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
            "app": "gps",
            "gps_data": self.gps_data,
        })

    def on_click(self, click_count):
        if click_count >= 3:
            from app_manager import switch_to_next_app
            switch_to_next_app(self.shared_class)

    def on_message(self, message):
        if "app" in message and message["app"] != self.name:
            from app_manager import start_app
            start_app(message["app"], self.shared_class)
            return
        
        if "data" in message:
            self.gps_data = message.get("data")
            self.update_display()
