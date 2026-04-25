"""
Translation app — manages its own state, TCP messages, and display hooks.

Modes:
  default   — idle; press button to capture.
  capturing — shutter in flight.
  live      — still sent; preview streaming until user presses button to end session.
"""
from apps.base_app import BaseApp


class TranslationApp(BaseApp):
    name = "translation"
    label = "Translate"

    def __init__(self, shared_class):
        super().__init__(shared_class)
        self.mode = "default"
        self.translation_data = None
        self.translation_groupings = None
        self.display_idx = 0

    def on_mount(self):
        if self.shared_class.camera_client and self.shared_class.camera_client.camera:
            if not self.shared_class.camera_client.running:
                self.shared_class.camera_client.start_capture_loop()
        self._trigger_capture()

    def _trigger_capture(self):
        self.translation_data = None
        self.translation_groupings = None
        self.display_idx = 0
        self.mode = "capturing"
        self.shared_class.shutter_event.set()
        self.update_display()

    def on_unmount(self):
        if self.mode == "live":
            self._stop_live_session()

    def update_display(self):
        self.shared_class.display.update_display({
            "app": "translation"
        })

    def render_display(self, display):
        if not display.hardware_available:
            return

        content_y = display.HEADER_CONTENT_START_Y
        content_h = display.oled.height - content_y

        if self.translation_groupings:
            idx = max(0, min(self.display_idx, len(self.translation_groupings) - 1))
            line = str(self.translation_groupings[idx].get("translated_text") or self.translation_data or "")
        else:
            line = str(self.translation_data or "")

        display.oled.fill(0)
        display.draw_app_header("Translate")

        if line.strip():
            for i, chunk in enumerate([line[:15], line[15:30]]):
                if chunk.strip():
                    display.oled.text(chunk, 0, content_y + i * 10, 1)
        else:
            msg = "Processing..."
            tx = max(0, (display.oled.width - len(msg) * 6) // 2)
            ty = content_y + (content_h - 8) // 2
            display.oled.text(msg, tx, ty, 1)

        display.oled.show()

    def _stop_live_session(self):
        """End preview, notify phone, clear session — back to idle."""
        self.shared_class.phone_live_stream = False
        self.translation_data = None
        self.translation_groupings = None
        self.display_idx = 0
        self.mode = "default"
        srv = self.shared_class.server
        if srv:
            srv.send_json({"app": self.name, "event": "preview_stopped"})

    def on_websocket_disconnect(self):
        """Phone gone — stop live stream locally (``phone_live_stream`` already cleared by server).
        Keep translation data so the display holds whatever it was showing."""
        if self.mode != "live":
            return
        self.mode = "default"

    def _start_live_preview(self):
        self.mode = "live"
        self.shared_class.phone_live_stream = True
        cc = self.shared_class.camera_client
        if cc:
            cc.ensure_preview_thread()

    def on_click(self, click_count):
        if click_count >= 3:
            from app_manager import switch_to_next_app
            switch_to_next_app(self.shared_class)
            return

        with self.shared_class.display_lock:
            if click_count == 1:
                import logging
                logging.info("TranslationApp: Shutter button pressed!")
                if self.mode == "capturing":
                    return
                if self.mode == "live":
                    self._stop_live_session()
                self._trigger_capture()
            elif click_count == 2:
                if self.mode != "live":
                    return
                if self.display_idx > 0:
                    self.display_idx -= 1
                    self.update_display()

    def on_capture_complete(self):
        self._start_live_preview()
        self.update_display()

    def on_message(self, message):
        if "app" in message and message["app"] != self.name:
            from app_manager import start_app
            start_app(message["app"], self.shared_class)
            if "data" in message and self.shared_class.server:
                self.shared_class.server.message_queue.put(message)
            return

        if "data" in message:
            # Ignore stray translation payloads when not in an active session (e.g. late OCR after stop).
            if self.mode != "live":
                return
            self.translation_data = message.get("data")
            self.translation_groupings = message.get("groupings")
            self.display_idx = 0
            self.update_display()
