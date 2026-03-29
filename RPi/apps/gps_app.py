"""
GPS App - Functions and utilities for GPS-based applications
"""
from threading import Timer


def gps_display_update(shared_class):
    """
    Update display for GPS app with current data
    """
    gps_data = shared_class.data.get('gps_data')
    
    # Update display with GPS info
    display_content = {
        'app': 'gps',
        'gps_data': gps_data,
    }
    shared_class.display.update_display(display_content)
    print(f"GPS display updated: {gps_data is not None}")

def gps_button_callback(shared_class):
    """
    Button callback for GPS app - handles GPS actions, capturing, and app switching
    """
    click_count = shared_class.data.get('click_count', 0)
    timer = shared_class.data.get('timer', None)
    
    print("Button Pressed: Processing GPS actions...")
    click_count += 1
    shared_class.data['click_count'] = click_count
        
    if timer is None:
        timer = Timer(0.4, lambda: _process_gps_clicks(shared_class))
        timer.start()
        shared_class.data['timer'] = timer
        print(f"Button Pressed: GPS mode. Click count: {click_count}")

def _process_gps_clicks(shared_class):
    """
    Process GPS button clicks for GPS actions or switching apps
    """
    from app_manager import APPS, start_app

    if shared_class.data.get('click_count', 0) >= 3:
        # Triple click or more: Switch to next app
        app_names = list(APPS.keys())
        current_idx = app_names.index(shared_class.current_app) if shared_class.current_app in app_names else 0
        next_app = app_names[(current_idx + 1) % len(app_names)]
        start_app(next_app, shared_class, shared_class.button, shared_class.server)
        print(f"Switched to app: {next_app}")

    shared_class.data['click_count'] = 0
    shared_class.data['timer'] = None

def gps_message_handler(shared_class, message):
    """
    Message handler for GPS app messages
    """
    # Check for app switching from phone
    if 'app' in message and message['app'] != shared_class.current_app:
        from app_manager import start_app

        start_app(message['app'], shared_class, shared_class.button, shared_class.server)
        return  # Don't process other message data when switching apps
    
    # Store GPS data if provided
    if 'data' in message:
        shared_class.data['gps_data'] = message.get('data')
        print("Received GPS data")
        shared_class.display.update_display({'gps_data': shared_class.data['gps_data']})