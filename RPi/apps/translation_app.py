"""
Translation app — manages its own state, TCP messages, and display hooks.
"""
from apps.base_app import BaseApp

class TranslationApp(BaseApp):
    def __init__(self, shared_class):
        super().__init__(shared_class)
        self.name = "translation"
        self.mode = "default"
        self.translation_data = None
        self.translation_groupings = None
        self.display_idx = 0

    def on_mount(self):
        # Start camera if needed
        if self.shared_class.camera_client and self.shared_class.camera_client.camera:
            if not self.shared_class.camera_client.running:
                self.shared_class.camera_client.start_capture_loop()
        self.update_display()

    def on_unmount(self):
        pass

    def update_display(self):
        self.shared_class.display.update_display({
            "app": "translation",
            "translation_data": self.translation_data,
            "display_idx": self.display_idx,
        })

    def on_click(self, click_count):
        if click_count >= 3:
            from app_manager import switch_to_next_app
            switch_to_next_app(self.shared_class)
            return

        with self.shared_class.display_lock:
            if click_count == 1:
                if self.mode == "default":
                    self.mode = "capturing"
                    self.shared_class.shutter_event.set()
                else:
                    self.display_idx += 1
                    self.update_display()
            elif click_count == 2:
                if self.mode != "default" and self.display_idx > 0:
                    self.display_idx -= 1
                    self.update_display()

    def on_capture_complete(self):
        # We sent the picture, user can take another one if they want while waiting
        self.mode = "default"

    def on_message(self, message):
        if message.get("_dawggles_ping") is True and self.shared_class.server:
            self.shared_class.server.send_json({"_dawggles_pong": True})
            return

        # Remote shutter (Mac / iOS)
        if message.get("dawggles_shutter") is True:
            if self.mode == "default":
                self.mode = "capturing"
                self.shared_class.shutter_event.set()
            return

        if "app" in message and message["app"] != self.name:
            from app_manager import start_app
            start_app(message["app"], self.shared_class)
            return

        if "data" in message:
            self.translation_data = message.get("data")
            self.translation_groupings = message.get("groupings")
            self.display_idx = 0
            # Switch out of default mode to display translations
            self.mode = "viewing"
            self.update_display()
