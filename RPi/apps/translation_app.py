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
        # Trigger the display to re-render using our state
        self.shared_class.display.update_display({
            "app": "translation"
        })

    def render_display(self, display):
        if not self.translation_data:
            if display.hardware_available:
                display.oled.fill(0)
                # Show a default prompt when no translation is actively being viewed
                display._render_text(["TRANSLATION", "", "Press button", "to scan"])
                display.oled.show()
            return
            
        # Basic placeholder for viewing translation data
        data = str(self.translation_data)
        display._render_text([data[:15], data[15:30]])

    def on_click(self, click_count):
        if click_count >= 3:
            from app_manager import switch_to_next_app
            switch_to_next_app(self.shared_class)
            return

        with self.shared_class.display_lock:
            if click_count == 1:
                import logging
                logging.info("TranslationApp: Shutter button pressed!")
                if self.mode == "default":
                    self.mode = "capturing"
                    self.shared_class.display.show_temporary_message("CAPTURING...", 1.5)
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
        self.shared_class.display.show_temporary_message("SENT TO APP", 1.5)

    def on_message(self, message):
        if message.get("_dawggles_ping") is True and self.shared_class.server:
            self.shared_class.server.send_json({"_dawggles_pong": True})
            return

        if "app" in message and message["app"] != self.name:
            from app_manager import start_app
            start_app(message["app"], self.shared_class)
            # Re-inject the message so the newly active app can process its payload
            if "data" in message and self.shared_class.server:
                self.shared_class.server.message_queue.put(message)
            return

        if "data" in message:
            self.translation_data = message.get("data")
            self.translation_groupings = message.get("groupings")
            self.display_idx = 0
            # Switch out of default mode to display translations
            self.mode = "viewing"
            self.update_display()
