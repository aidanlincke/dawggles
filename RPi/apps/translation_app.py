"""
Translation app — button, TCP messages, display hooks.
"""
from threading import Timer

from app_registry import APP_ORDER


def translation_display_update(shared_class):
    translation_data = shared_class.data.get("translation_data")
    display_idx = shared_class.data.get("display_idx", 0)
    shared_class.display.update_display(
        {
            "app": "translation",
            "translation_data": translation_data,
            "display_idx": display_idx,
        }
    )


def translation_button_callback(shared_class):
    click_count = shared_class.data.get("click_count", 0) + 1
    timer = shared_class.data.get("timer", None)
    shared_class.data["click_count"] = click_count

    if click_count >= 3:
        from app_manager import start_app

        app_names = list(APP_ORDER)
        current_idx = (
            app_names.index(shared_class.current_app)
            if shared_class.current_app in app_names
            else 0
        )
        next_app = app_names[(current_idx + 1) % len(app_names)]
        start_app(next_app, shared_class, shared_class.button, shared_class.server)
        shared_class.data["click_count"] = 0
        if timer:
            timer.cancel()
            shared_class.data["timer"] = None
        return

    if timer is None:
        timer = Timer(0.4, lambda: _process_translation_clicks(shared_class))
        timer.start()
        shared_class.data["timer"] = timer

    if shared_class.mode == "default":
        shared_class.mode = "capturing"
        shared_class.shutter_event.set()


def _process_translation_clicks(shared_class):
    with shared_class.display_lock:
        click_count = shared_class.data.get("click_count", 0)

        if click_count == 1:
            display_idx = shared_class.data.get("display_idx", 0)
            shared_class.data["display_idx"] = display_idx + 1
            shared_class.display.update_display(
                {"display_idx": shared_class.data["display_idx"]}
            )

        elif click_count == 2 and shared_class.data.get("display_idx", 0) > 0:
            display_idx = shared_class.data.get("display_idx", 0)
            shared_class.data["display_idx"] = display_idx - 1
            shared_class.display.update_display(
                {"display_idx": shared_class.data["display_idx"]}
            )

    shared_class.data["click_count"] = 0
    shared_class.data["timer"] = None


def translation_message_handler(shared_class, message):
    if message.get("_dawggles_ping") is True and shared_class.server:
        shared_class.server.send_json({"_dawggles_pong": True})
        return

    # Remote shutter (Mac / iOS): same path as the physical button in default mode
    if message.get("dawggles_shutter") is True:
        if shared_class.current_app != "translation":
            return
        if shared_class.mode != "default":
            return
        shared_class.mode = "capturing"
        shared_class.shutter_event.set()
        return

    if "app" in message and message["app"] != shared_class.current_app:
        from app_manager import start_app

        start_app(
            message["app"], shared_class, shared_class.button, shared_class.server
        )
        return

    if "data" in message:
        shared_class.data["translation_data"] = message.get("data")
        shared_class.data["translation_groupings"] = message.get("groupings")
        shared_class.data["display_idx"] = 0
        shared_class.display.update_display(
            {
                "translation_data": shared_class.data["translation_data"],
                "display_idx": shared_class.data["display_idx"],
            }
        )
