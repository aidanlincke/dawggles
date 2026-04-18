"""Settings app — info and device management."""
import logging

from apps.base_app import BaseApp

log = logging.getLogger(__name__)


class SettingsApp(BaseApp):
    def __init__(self, shared_class):
        super().__init__(shared_class)
        self.name = "settings"

    def on_mount(self):
        # Cycle button goes home; action button executes the single item
        self.shared_class.cycle_button.update_callback(self._on_cycle)
        self._render()

    def on_unmount(self):
        pass

    def on_click(self, click_count):
        if click_count > 0:
            self._do_unpair()

    def _on_cycle(self, click_count):
        if click_count > 0:
            from home_screen import show_home_screen
            show_home_screen(self.shared_class)

    def render_display(self, display):
        self._render()

    def _render(self):
        d = self.shared_class.display
        if not d.hardware_available:
            log.info("Settings: UNPAIR")
            return
        with d.display_lock:
            d.oled.fill(0)
            d.draw_app_header("Settings")

            d.oled.text("> Unpair", 0, 16, 1)

            d.oled.show()

    def _do_unpair(self):
        from pairing.pair import perform_unpair_and_restart
        perform_unpair_and_restart(self.shared_class)
