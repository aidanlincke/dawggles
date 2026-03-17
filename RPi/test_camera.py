import time
from picamera2 import Picamera2

def test_camera():
    picam2 = Picamera2()

    # We add a size limit here so the Pi Zero 2 W doesn't run out of memory!
    config = picam2.create_still_configuration(main={"size": (1920, 1080)})
    picam2.configure(config)

    print("Starting camera...")
    picam2.start()

    print("Warming up sensor for 2 seconds...")
    time.sleep(2)

    output_filename = "capture.jpg"
    print(f"Taking picture and saving to {output_filename}...")
    picam2.capture_file(output_filename)

    picam2.stop()
    picam2.close()

    print("Success! Camera test complete.")

if __name__ == "__main__":
    test_camera()