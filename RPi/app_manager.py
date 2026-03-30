"""
App Manager - switching between applications
"""
from app_registry import APP_ORDER
from apps.gps_app import *
from apps.translation_app import *

APPS = {
    "translation": {
        "button_callback": translation_button_callback,
        "message_handler": translation_message_handler,
        "display_update": translation_display_update,
    },
    "gps": {
        "button_callback": gps_button_callback,
        "message_handler": gps_message_handler,
        "display_update": gps_display_update,
    },
}

if tuple(APPS.keys()) != APP_ORDER:
    raise RuntimeError("app_manager.APPS keys must match app_registry.APP_ORDER")


def start_app(app_name, shared, button, server):
    if app_name not in APPS:
        return

    app_config = APPS[app_name]
    shared.switch_app(app_name)

    if app_config["button_callback"]:
        button.update_callback(app_config["button_callback"])

    server.message_handler = app_config["message_handler"]

    if app_name == "translation":
        if shared.camera_client and shared.camera_client.camera and not shared.camera_client.running:
            shared.camera_client.start_capture_loop()
    else:
        if shared.camera_client and shared.camera_client.running:
            shared.camera_client.stop_capture_loop()
