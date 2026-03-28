"""
Translation App - Functions and utilities for translation-based applications
"""
from threading import Timer


def translation_display_update(shared_class):
    """
    Update display for translation app with current data
    """
    translation_data = shared_class.data.get('translation_data')
    display_idx = shared_class.data.get('display_idx', 0)
    
    # Update display with translation info
    display_content = {
        'app': 'translation',
        'translation_data': translation_data,
        'display_idx': display_idx,
    }
    shared_class.display.update_display(display_content)
    print(f"Translation display updated: idx={display_idx}, has_data={translation_data is not None}")

def translation_button_callback(shared_class):
    """
    Button callback for translation mode - handles navigating between translations and app switching
    """
    click_count = shared_class.data.get('click_count', 0) + 1
    timer = shared_class.data.get('timer', None)
    shared_class.data['click_count'] = click_count

    # triple-click should always switch app regardless of current app mode
    if click_count >= 3:
        from app_manager import APPS, start_app

        app_names = list(APPS.keys())
        current_idx = app_names.index(shared_class.current_app) if shared_class.current_app in app_names else 0
        next_app = app_names[(current_idx + 1) % len(app_names)]
        start_app(next_app, shared_class, shared_class.button, shared_class.server)

        # Reset click tracking and clear timer if active
        shared_class.data['click_count'] = 0
        if timer:
            timer.cancel()
            shared_class.data['timer'] = None
        print(f"Switched to app: {next_app} via triple click")
        return

    if timer is None:
        timer = Timer(0.4, lambda: _process_translation_clicks(shared_class))
        timer.start()
        shared_class.data['timer'] = timer

    if shared_class.mode == 'default':
        print("Button Pressed: Switching to CAPTURING mode, capturing picture...")
        shared_class.mode = 'capturing'
        shared_class.shutter_event.set()

    elif shared_class.mode == 'capturing':
        print("Button Pressed: Already in CAPTURING mode, display not ready...")

    elif shared_class.mode == 'translation':
        print(f"Button Pressed: Processing translation actions... Click count: {click_count}")

    else:
        print(f"Button Pressed: Unknown mode {shared_class.mode}")

def _process_translation_clicks(shared_class):
    """
    Process translation button clicks for navigating translations or switching apps
    """
    with shared_class.display_lock:
        click_count = shared_class.data.get('click_count', 0)

        if click_count == 1:
            # Skip to next translation
            display_idx = shared_class.data.get('display_idx', 0)
            shared_class.data['display_idx'] = display_idx + 1
            print(f"Skipped forward to translation: {shared_class.data['display_idx']}")
            shared_class.display.update_display({'display_idx': shared_class.data['display_idx']})

        elif click_count == 2 and shared_class.data.get('display_idx', 0) > 0:
            # Go back to previous translation
            display_idx = shared_class.data.get('display_idx', 0)
            shared_class.data['display_idx'] = display_idx - 1
            print(f"Skipped backward to translation: {shared_class.data['display_idx']}")
            shared_class.display.update_display({'display_idx': shared_class.data['display_idx']})

    shared_class.data['click_count'] = 0
    shared_class.data['timer'] = None

def translation_message_handler(shared_class, message):
    """
    Message handler for translation app messages
    """
    # Check for app switching from phone
    if 'app' in message and message['app'] != shared_class.current_app:
        from app_manager import start_app

        start_app(message['app'], shared_class, shared_class.button, shared_class.server)
        return  # Don't process other message data when switching apps
    
    # Store translation data if provided
    if 'data' in message:
        shared_class.data['translation_data'] = message.get('data')
        shared_class.data['translation_groupings'] = message.get('groupings')
        shared_class.data['display_idx'] = 0
        print("Received translation data")
        # Update display with translation data
        shared_class.display.update_display({
            'translation_data': shared_class.data['translation_data'],
            'display_idx': shared_class.data['display_idx']
        })
