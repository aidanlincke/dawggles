"""Settings app — submenu-style: Unpair | Back."""
import logging

from apps.base_app import BaseApp

log = logging.getLogger(__name__)

_SUBMENU = ["Unpair", "Back"]


class SettingsApp(BaseApp):
    name = "settings"
    label = "Settings"

    def __init__(self, shared_class):
        super().__init__(shared_class)
        self.submenu_idx = 0

    def on_mount(self):
        self.submenu_idx = 0
        self.shared_class.display.update_display({"app": self.name})
        self.shared_class.cycle_button.update_callback(self._on_cycle)

    def on_unmount(self):
        pass

    def render_display(self, display):
        if not display.hardware_available:
            log.info("Settings: %s (cursor=%d)", _SUBMENU, self.submenu_idx)
            return
        display.draw_menu(self.label, _SUBMENU, self.submenu_idx)

    def _on_cycle(self, click_count):
        if click_count > 0:
            self.submenu_idx = (self.submenu_idx + 1) % len(_SUBMENU)
            self.shared_class.display.update_display({"app": self.name})

    def on_click(self, click_count):
        if click_count <= 0:
            return
        choice = _SUBMENU[self.submenu_idx]
        if choice == "Unpair":
            self._do_unpair()
        elif choice == "Back":
            from home_screen import show_home_screen
            show_home_screen(self.shared_class)

    def _do_unpair(self):
        from pairing.pair import perform_unpair_and_restart
        perform_unpair_and_restart(self.shared_class)
