"""
Camera app — live view on the phone, forward button captures a still and the
phone saves it to the iOS Photos library.

The Pi has no on-device storage role here: it just streams JPEG frames over the
existing camera-stream socket while this app is mounted. When the user clicks
forward, we send the phone a `capture` event and it saves the latest decoded
frame to the camera roll.
"""
from apps.base_app import BaseApp


class CameraApp(BaseApp):
    name = "camera"
    label = "Camera"

    # Status banner shown briefly after a capture.
    _STATUS_DURATION_S = 1.5

    def __init__(self, shared_class):
        super().__init__(shared_class)
        self._status_text = None
        self._status_clear_timer = None

    # ── Mount / unmount ────────────────────────────────────────────────────────

    def on_mount(self):
        self._status_text = None
        self.shared_class.camera_streaming = True
        cc = self.shared_class.camera_client
        if cc:
            cc.ensure_stream_thread()
        self.shared_class.display.update_display({"app": self.name})

    def on_unmount(self):
        self._cancel_status_timer()
        self._status_text = None
        self.shared_class.camera_streaming = False

    # ── Forward button: capture ────────────────────────────────────────────────

    def on_click(self, click_count):
        if click_count <= 0:
            return
        srv = self.shared_class.server
        if srv and srv.connected:
            srv.send_json({"app": self.name, "event": "capture"})
            self._show_status("Saving...")
        else:
            self._show_status("No phone")

    # ── Display ────────────────────────────────────────────────────────────────

    def render_display(self, display):
        if not display.hardware_available:
            return
        display.oled.fill(0)
        display.draw_app_header("Camera")

        content_y = display.HEADER_CONTENT_START_Y
        content_h = display.oled.height - content_y

        if self._status_text:
            msg = self._status_text
        else:
            msg = "Click to capture"

        tx = max(0, (display.oled.width - len(msg) * 6) // 2)
        ty = content_y + (content_h - 8) // 2
        display.oled.text(msg, tx, ty, 1)
        display.oled.show()

    # ── Phone → Pi: capture-saved ack ──────────────────────────────────────────

    def on_message(self, message):
        event = message.get("event")
        if event == "capture_saved":
            self._show_status("Saved")
        elif event == "capture_failed":
            self._show_status("Save failed")

    # ── Status banner helpers ──────────────────────────────────────────────────

    def _show_status(self, text):
        from threading import Timer
        self._cancel_status_timer()
        self._status_text = text
        self.shared_class.display.update_display({"app": self.name})
        t = Timer(self._STATUS_DURATION_S, self._clear_status)
        t.daemon = True
        self._status_clear_timer = t
        t.start()

    def _clear_status(self):
        self._status_text = None
        self._status_clear_timer = None
        try:
            self.shared_class.display.update_display({"app": self.name})
        except Exception:
            pass

    def _cancel_status_timer(self):
        if self._status_clear_timer is not None:
            try:
                self._status_clear_timer.cancel()
            except Exception:
                pass
            self._status_clear_timer = None
