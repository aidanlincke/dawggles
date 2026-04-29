"""
Translation app — sub-menu → Text (camera OCR) or Speech (todo).

Modes:
    submenu — scrollable list: Text | Speech | Back
    text    — camera streaming + OCR translation display
    speech  — placeholder (todo)
"""
from apps.base_app import BaseApp

_SUBMENU = ["Text", "Speech", "Back"]


class TranslationApp(BaseApp):
    name = "translation"
    label = "Translate"

    def __init__(self, shared_class):
        super().__init__(shared_class)
        self.mode = "submenu"
        self.submenu_idx = 0
        self.translation_data = None
        self.translation_groupings = None
        self.display_idx = 0

    # ── Mount / unmount ────────────────────────────────────────────────────────

    def on_mount(self):
        self._show_submenu()

    def on_unmount(self):
        if self.mode == "text":
            self._stop_text_session()

    # ── Sub-menu ───────────────────────────────────────────────────────────────

    def _show_submenu(self):
        self.mode = "submenu"
        self.submenu_idx = 0
        self.shared_class.display.update_display({"app": self.name})
        self.shared_class.cycle_button.update_callback(self._on_submenu_cycle)

    def _on_submenu_cycle(self, click_count):
        if click_count > 0:
            self.submenu_idx = (self.submenu_idx + 1) % len(_SUBMENU)
            self.shared_class.display.update_display({"app": self.name})

    # ── Next button (select) ───────────────────────────────────────────────────

    def on_click(self, click_count):
        if click_count <= 0:
            return
        if self.mode == "submenu":
            choice = _SUBMENU[self.submenu_idx]
            if choice == "Text":
                self._enter_text_mode()
            elif choice == "Speech":
                self._enter_speech_mode()
            elif choice == "Back":
                from home_screen import show_home_screen
                show_home_screen(self.shared_class)

    # ── Text mode ──────────────────────────────────────────────────────────────

    def _enter_text_mode(self):
        self.translation_data = None
        self.translation_groupings = None
        self.display_idx = 0
        self.mode = "text"
        self.shared_class.camera_streaming = True
        cc = self.shared_class.camera_client
        if cc:
            cc.ensure_stream_thread()
        self.shared_class.display.update_display({"app": self.name})
        self.shared_class.cycle_button.update_callback(self._on_back_to_submenu)

    def _stop_text_session(self):
        self.shared_class.camera_streaming = False
        self.translation_data = None
        self.translation_groupings = None
        self.display_idx = 0
        self.mode = "submenu"

    # ── Speech mode ────────────────────────────────────────────────────────────

    def _enter_speech_mode(self):
        self.mode = "speech"
        self.shared_class.display.update_display({"app": self.name})
        self.shared_class.cycle_button.update_callback(self._on_back_to_submenu)

    # ── Back button (from text/speech → sub-menu) ──────────────────────────────

    def _on_back_to_submenu(self, click_count):
        if click_count > 0:
            if self.mode == "text":
                self._stop_text_session()
            self._show_submenu()

    # ── Display rendering ──────────────────────────────────────────────────────

    def render_display(self, display):
        if not display.hardware_available:
            return
        if self.mode == "submenu":
            self._render_submenu(display)
        elif self.mode == "text":
            self._render_text(display)
        elif self.mode == "speech":
            self._render_speech(display)

    def _render_submenu(self, display):
        display.draw_menu("Translate", _SUBMENU, self.submenu_idx)

    def _render_text(self, display):
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

    def _render_speech(self, display):
        display.oled.fill(0)
        display.draw_app_header("Translate")
        display.oled.text("[todo]", 0, display.HEADER_CONTENT_START_Y, 1)
        display.oled.show()

    # ── WebSocket / message handling ───────────────────────────────────────────

    def on_websocket_disconnect(self):
        if self.mode != "text":
            return
        self.mode = "submenu"

    def on_message(self, message):
        if "app" in message and message["app"] != self.name:
            from app_manager import start_app
            start_app(message["app"], self.shared_class)
            if "data" in message and self.shared_class.server:
                self.shared_class.server.message_queue.put(message)
            return

        if "data" in message:
            if self.mode != "text":
                return
            self.translation_data = message.get("data")
            self.translation_groupings = message.get("groupings")
            self.display_idx = 0
            self.shared_class.display.update_display({"app": self.name})
