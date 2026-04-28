"""
Translation app — manages its own state, TCP messages, and display hooks.

Modes:
    default — idle; waiting to stream.
    live    — camera streaming while the app is active.
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
        self._start_camera_stream()
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
        """Turn camera off, clear session — back to idle. camera_stopped is sent by the stream loop
        after it finishes its current frame, guaranteeing no stray frames arrive after the signal."""
        self.shared_class.camera_streaming = False
        self.translation_data = None
        self.translation_groupings = None
        self.display_idx = 0
        self.mode = "default"

    def on_websocket_disconnect(self):
        """Phone gone — stop camera stream locally (``camera_streaming`` already cleared by server).
        Keep translation data so the display holds whatever it was showing."""
        if self.mode != "live":
            return
        self.mode = "default"

    def _start_camera_stream(self):
        self.translation_data = None
        self.translation_groupings = None
        self.display_idx = 0
        self.mode = "live"
        self.shared_class.camera_streaming = True
        cc = self.shared_class.camera_client
        if cc:
            cc.ensure_stream_thread()

    def on_click(self, click_count):
        return

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
