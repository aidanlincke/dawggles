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

    @staticmethod
    def _parse_active_idx(idx):
        if idx is None or isinstance(idx, bool):
            return None
        if isinstance(idx, int):
            return idx
        if isinstance(idx, float):
            return int(idx) if idx.is_integer() else None
        return None

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
        self.update_display()

    def on_unmount(self):
        if self.mode == "live":
            self._stop_live_session()

    def update_display(self):
        self.shared_class.display.update_display({
            "app": "translation"
        })

    def render_display(self, display):
        if self.mode == "live" and not self.translation_groupings:
            display._render_text(["LIVE", "", "Processing...", ""])
            return

        if not self.translation_data and not self.translation_groupings:
            if display.hardware_available:
                display.oled.fill(0)
                display._render_text(["TRANSLATION", "", "Press button", "to scan"])
                display.oled.show()
            return

        if self.translation_groupings:
            idx = max(0, min(self.display_idx, len(self.translation_groupings) - 1))
            g = self.translation_groupings[idx]
            line = str(g.get("translated_text") or "")
            if not line.strip():
                line = str(self.translation_data or "")
        else:
            line = str(self.translation_data or "")

        display._render_text([line[:15], line[15:30]])

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
        """Phone gone — drop live session locally (``phone_live_stream`` already cleared by server)."""
        if self.mode != "live":
            return
        self.translation_data = None
        self.translation_groupings = None
        self.display_idx = 0
        self.mode = "default"
        if self.shared_class.display:
            self.update_display()

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
                if self.mode == "live":
                    logging.info("TranslationApp: stopping live session")
                    self._stop_live_session()
                    self.update_display()
                    return
                if self.mode == "capturing":
                    return
                self.mode = "capturing"
                self.shared_class.display.show_temporary_message("CAPTURING...", 1.5)
                self.shared_class.shutter_event.set()
            elif click_count == 2:
                if self.mode != "live":
                    return
                if self.display_idx > 0:
                    self.display_idx -= 1
                    self.update_display()

    def on_capture_complete(self):
        # Picture is on the wire; start preview immediately (phone OCR runs in parallel).
        self._start_live_preview()
        self.shared_class.display.show_temporary_message("SENT TO APP", 1.5)
        self.update_display()

    def on_message(self, message):
        if message.get("_dawggles_ping") is True and self.shared_class.server:
            self.shared_class.server.send_json({"_dawggles_pong": True})
            return

        if "app" in message and message["app"] != self.name:
            from app_manager import start_app
            start_app(message["app"], self.shared_class)
            if "data" in message and self.shared_class.server:
                self.shared_class.server.message_queue.put(message)
            return

        evt = message.get("event")
        if evt == "focus":
            if self.mode != "live":
                return
            g = self.translation_groupings
            if not g:
                return
            idx = message.get("active_idx")
            if isinstance(idx, bool):
                return
            if isinstance(idx, float):
                if not idx.is_integer():
                    return
                idx = int(idx)
            elif not isinstance(idx, int):
                return
            if idx < 0 or idx >= len(g):
                return
            if idx != self.display_idx:
                self.display_idx = idx
                self.update_display()
            return

        if message.get("event") == "preview_start":
            if self.mode == "live":
                cc = self.shared_class.camera_client
                if cc:
                    cc.ensure_preview_thread()
            return

        if "data" in message:
            # Ignore stray translation payloads when not in an active session (e.g. late OCR after stop).
            if self.mode != "live":
                return
            self.translation_data = message.get("data")
            self.translation_groupings = message.get("groupings")
            idx = message.get("active_idx")
            g = self.translation_groupings
            parsed = self._parse_active_idx(idx)
            if parsed is not None and g and 0 <= parsed < len(g):
                self.display_idx = parsed
            else:
                self.display_idx = 0
            self.update_display()
