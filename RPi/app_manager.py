"""
App Manager - switching between applications
"""
import logging
from app_registry import APP_ORDER
from apps.gps_app import GPSApp
from apps.translation_app import TranslationApp

_APPS = {}
_current_app_instance = None

def initialize_apps(shared_class):
    global _APPS
    _APPS["translation"] = TranslationApp(shared_class)
    _APPS["gps"] = GPSApp(shared_class)
    
    if tuple(_APPS.keys()) != APP_ORDER:
        raise RuntimeError("app_manager._APPS keys must match app_registry.APP_ORDER")

def get_current_app():
    return _current_app_instance

def start_app(app_name, shared_class, button=None, server=None):
    global _current_app_instance
    
    if not _APPS:
        initialize_apps(shared_class)

    if app_name not in _APPS:
        return

    if _current_app_instance:
        _current_app_instance.on_unmount()

    shared_class.current_app = app_name
    _current_app_instance = _APPS[app_name]
    
    logging.info(f"--- Switched to APP: {app_name.upper()} ---")
    
    # We clear the display when switching apps
    if shared_class.display:
        shared_class.display.reset_display()
        shared_class.display.show_temporary_message(app_name.upper(), 2.0)

    # Update hardware callbacks if they exist
    btn = button or shared_class.button
    srv = server or shared_class.server
    
    if btn:
        btn.update_callback(_current_app_instance.on_click)
    if srv:
        srv.message_handler = _current_app_instance.on_message

    def _on_websocket_disconnect():
        app = get_current_app()
        if app is not None:
            try:
                app.on_websocket_disconnect()
            except Exception as e:
                logging.warning("on_websocket_disconnect: %s", e)

    shared_class.websocket_disconnect_callback = _on_websocket_disconnect

    _current_app_instance.on_mount()

def switch_to_next_app(shared_class):
    app_names = list(APP_ORDER)
    current_idx = app_names.index(shared_class.current_app) if shared_class.current_app in app_names else 0
    next_app = app_names[(current_idx + 1) % len(app_names)]
    start_app(next_app, shared_class)
