"""Home screen — scrollable app list rendered on the OLED."""
import logging

from apps.translation_app import TranslationApp
from apps.live_captions_app import LiveCaptionsApp
from apps.gps_app import GPSApp
from apps.settings_app import SettingsApp

log = logging.getLogger(__name__)

_APP_CLASSES = [TranslationApp, GPSApp, LiveCaptionsApp, SettingsApp]

_selected = 0


def show_home_screen(shared):
    """Unmount current app (if any), render home menu, wire buttons."""
    global _selected

    from app_manager import clear_current_app
    clear_current_app()

    shared.display.reset_display()
    _selected = 0
    _render(shared)

    shared.button.update_callback(lambda clicks: _on_select(clicks, shared))
    shared.cycle_button.update_callback(lambda clicks: _on_scroll(clicks, shared))


def _render(shared):
    d = shared.display
    if not d.hardware_available:
        log.info("Home: %s (cursor=%d)", [cls.label for cls in _APP_CLASSES], _selected)
        return
    with d.display_lock:
        d.oled.fill(0)
        d.draw_app_header("Home")
        for i, cls in enumerate(_APP_CLASSES):
            y = 16 + i * 12
            prefix = ">" if i == _selected else " "
            d.oled.text(f"{prefix} {cls.label}", 0, y, 1)
        d.oled.show()


def _on_scroll(click_count, shared):
    global _selected
    if click_count > 0:
        _selected = (_selected + 1) % len(_APP_CLASSES)
        _render(shared)


def _on_select(click_count, shared):
    if click_count > 0:
        app_key = _APP_CLASSES[_selected].name
        from app_manager import start_app
        start_app(app_key, shared, shared.button, shared.server)
        # start_app rewires shared.button → app.on_click; wire cycle button → back home
        shared.cycle_button.update_callback(lambda clicks: _go_home(clicks, shared))


def _go_home(click_count, shared):
    if click_count > 0:
        show_home_screen(shared)
