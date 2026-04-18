"""
Translation app — manages its own state, TCP messages, and display hooks.
"""
from apps.base_app import BaseApp


def _wrap(text, width=16):
    """Word-wrap text to lines of at most `width` characters."""
    words = text.split()
    lines = []
    current = ""
    for word in words:
        candidate = (current + " " + word).strip()
        if len(candidate) <= width:
            current = candidate
        else:
            if current:
                lines.append(current)
            # If a single word is longer than width, hard-slice it
            while len(word) > width:
                lines.append(word[:width])
                word = word[width:]
            current = word
    if current:
        lines.append(current)
    return lines

class TranslationApp(BaseApp):
    name = "translation"
    label = "Translate"

    def __init__(self, shared_class):
        super().__init__(shared_class)
        self.mode = "default"
        self.translation_data = None
        self.translation_groupings = None

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
        if not display.hardware_available:
            return
        display.oled.fill(0)
        display.draw_app_header(self.label)
        if self.mode == "default":
            display.oled.text("Press the button", 0, 29, 1)
            display.oled.text("to scan.", 0, 41, 1)
        elif self.mode == "translating":
            display.oled.text("Translating...", 0, 29, 1)
        elif self.mode == "viewing" and self.translation_data:
            lines = _wrap(str(self.translation_data), width=16)
            for i, line in enumerate(lines[:3]):
                display.oled.text(line, 0, 16 + i * 12, 1)
        display.oled.show()

    def on_click(self, click_count):
        if click_count == 1:
            if self.mode == "default":
                self.mode = "capturing"
                self.shared_class.shutter_event.set()
            elif self.mode == "viewing":
                self.translation_data = None
                self.mode = "default"
                self.update_display()

    def on_capture_complete(self):
        self.mode = "translating"
        self.update_display()

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
            self.mode = "viewing"
            self.update_display()
