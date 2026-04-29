"""Home screen — scrollable app list rendered on the OLED."""
import logging

from apps.translation_app import TranslationApp
from apps.gps_app import GPSApp
from apps.settings_app import SettingsApp

log = logging.getLogger(__name__)

_APP_CLASSES = [TranslationApp, GPSApp, SettingsApp]

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
    labels = [cls.label for cls in _APP_CLASSES]
    if not d.hardware_available:
        log.info("Home: %s (cursor=%d)", labels, _selected)
        return
    with d.display_lock:
        d.draw_menu("Home", labels, _selected)


def _on_scroll(click_count, shared):
    global _selected
    if click_count > 0:
        _selected = (_selected + 1) % len(_APP_CLASSES)
        _render(shared)


def _on_select(click_count, shared):
    if click_count > 0:
        app_key = _APP_CLASSES[_selected].name
        from app_manager import start_app
        # Pass _go_home as back_callback; start_app wires it before on_mount so the
        # app can override it (e.g. TranslationApp replaces it with submenu cycling).
        start_app(app_key, shared, shared.button, shared.server,
                  back_callback=lambda clicks: _go_home(clicks, shared))


def _go_home(click_count, shared):
    if click_count > 0:
        show_home_screen(shared)
