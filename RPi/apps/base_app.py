class BaseApp:
    name = "base"
    label = "Base"

    def __init__(self, shared_class):
        self.shared_class = shared_class

    def on_mount(self):
        """Called when the app becomes the active app."""
        pass

    def on_unmount(self):
        """Called when the app is no longer the active app."""
        pass

    def on_click(self, click_count):
        """Called when the goggle button is clicked."""
        pass

    def on_message(self, message):
        """Called when a TCP message is received for this app."""
        pass

    def on_capture_complete(self):
        """Called when the camera finishes capturing a picture."""
        pass

    def render_display(self, display):
        """Called when the display needs to re-render for this app."""
        pass
