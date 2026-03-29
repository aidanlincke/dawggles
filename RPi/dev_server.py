"""
Run the real goggles_lib Server + SharedClass + translation app on a laptop (no GPIO/camera).

Server always takes a shared object so handlers keep the same (shared, message) signature as on the Pi.
Here that object is a normal SharedClass with a stub button and no camera — same wiring idea as main.py.

  python dev_server.py

  # One-way send (zsh-safe: use >I not !I inside double quotes):
  python3 -c 'import json,socket,struct;c=socket.create_connection(("127.0.0.1",12345));b=json.dumps({"data":"hello"}).encode();c.sendall(struct.pack(">I",len(b))+b);c.close()'

  # Pi -> phone + phone -> Pi (uses _dawggles_ping / _dawggles_pong in translation handler):
  python3 test_phone_client.py
"""
from goggles_lib import Display, Server, SharedClass
from app_manager import start_app


class StubButton:
    def update_callback(self, button_callback):
        pass


def main():
    shared = SharedClass()
    shared.display = Display(shared)
    shared.button = StubButton()
    shared.camera_client = None
    # 0.0.0.0 = accept from LAN (phone / laptop). Use 127.0.0.1 only for same-machine tests.
    shared.server = Server(shared, host="0.0.0.0", port=12345)

    start_app("translation", shared, shared.button, shared.server)

    print("Dev server listening on 0.0.0.0:12345 — connect from another device using this Pi's IP (Ctrl+C to stop)")
    try:
        while True:
            pass
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
