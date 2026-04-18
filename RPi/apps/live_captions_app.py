from apps.base_app import BaseApp


class LiveCaptionsApp(BaseApp):
    name = "live_captions"
    label = "Live Captions"

    def on_mount(self):
        self.update_display()

    def update_display(self):
        self.shared_class.display.update_display({"app": self.name})

    def render_display(self, display):
        if not display.hardware_available:
            return
        display.oled.fill(0)
        display.draw_app_header(self.label)
        display.oled.text("[todo]", 0, 29, 1)
        display.oled.show()
