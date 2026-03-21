"""
App Manager - Handles switching between different applications
"""
from apps.gps_app import *
from apps.translation_app import *
# App configuration
APPS = {
    'translation': {
        'button_callback': translation_button_callback,
        'message_handler': translation_message_handler,
        'display_update': translation_display_update,
    },
    'gps': {
        'button_callback': gps_button_callback,
        'message_handler': gps_message_handler,
        'display_update': gps_display_update,
    },
}

def start_app(app_name, shared, button, server):
    """Switch to and start a specific app"""
    if app_name not in APPS:
        print(f"Unknown app: {app_name}")
        return
    
    app_config = APPS[app_name]
    shared.switch_app(app_name)
    
    # Update button callback if available
    if app_config['button_callback']:
        button.update_callback(app_config['button_callback'])
    
    # Update message handler
    server.message_handler = app_config['message_handler']
    
    # Handle camera loop: start for translation, stop for others
    if app_name == 'translation':
        if shared.camera_client and shared.camera_client.camera and not shared.camera_client.running:
            shared.camera_client.start_capture_loop()
            print("Camera capture loop started for translation app")
    else:
        if shared.camera_client and shared.camera_client.running:
            shared.camera_client.stop_capture_loop()
            print("Camera capture loop stopped for non-translation app")
    
    print(f"{app_name} app started!")


