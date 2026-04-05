import socket
import struct
import json
import sys
import threading
import select
import base64
import os
import time

def send_framed_json(sock, payload):
    """Sends a JSON dictionary with a 4-byte big-endian length prefix."""
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    frame = struct.pack("!I", len(body)) + body
    sock.sendall(frame)
    print(f"Sent: {json.dumps(payload)}")

def recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Socket closed")
        buf += chunk
    return bytes(buf)

def receive_loop(sock, stop_event):
    """Background thread to receive and print incoming messages from the Pi."""
    while not stop_event.is_set():
        readable, _, _ = select.select([sock], [], [], 0.5)
        if not readable:
            continue
        try:
            hdr = recv_exact(sock, 4)
            (length,) = struct.unpack("!I", hdr)
            
            raw = recv_exact(sock, length)
            msg = json.loads(raw.decode("utf-8"))
            
            # Don't flood the terminal with base64 image data
            if msg.get("event") == "picture":
                b64 = msg.get("image_b64")
                if b64:
                    save_dir = "dawggles_incoming"
                    os.makedirs(save_dir, exist_ok=True)
                    path = os.path.join(save_dir, f"picture_{int(time.time() * 1000)}.jpg")
                    with open(path, "wb") as f:
                        f.write(base64.standard_b64decode(b64))
                    print(f"\n[Received] Picture saved to {path} ({msg.get('byte_length')} bytes)")
                else:
                    print(f"\n[Received] Picture ready signal! (No image data)")
            else:
                print(f"\n[Received] {json.dumps(msg)}")
                
        except Exception as e:
            if not stop_event.is_set():
                print(f"\n[Receiver Thread Error] {e}")
            break

def main():
    # Use localhost for local testing, or pass the Pi's IP as an argument
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = 12345

    print(f"Connecting to {host}:{port}...")
    
    try:
        sock = socket.create_connection((host, port))
        print("Connected! Connection will remain open.")
        
        # Start a background thread to listen for incoming messages (like pictures or pongs)
        stop_event = threading.Event()
        receiver_thread = threading.Thread(target=receive_loop, args=(sock, stop_event), daemon=True)
        receiver_thread.start()

        print("\nPress keys to send payloads over this persistent connection:")
        print("  --- GPS App ---")
        print("  1: Right Turn (500ft, Main St)")
        print("  2: Walk (1 min, Park Path)")
        print("  3: Subway (0ft, L Train)")
        print("  --- Translation App ---")
        print("  4: Send Mock Translation Data")
        print("  --- General ---")
        print("  p: Ping (_dawggles_ping: true)")
        print("  q: Quit")
        
        while True:
            choice = input("\nEnter choice: ").strip().lower()
            
            if choice == '1':
                payload = {
                    "app": "gps",
                    "data": {
                        "icon_type": "turn_right",
                        "distance": "500ft",
                        "street": "Main St",
                        "lines": [[64, 50, 64, 30], [64, 30, 90, 30]]
                    }
                }
                send_framed_json(sock, payload)
            
            elif choice == '2':
                payload = {
                    "app": "gps",
                    "data": {
                        "icon_type": "walk",
                        "distance": "1 min",
                        "street": "Park Path",
                        "lines": [[64, 50, 50, 40], [50, 40, 40, 20]]
                    }
                }
                send_framed_json(sock, payload)
                
            elif choice == '3':
                payload = {
                    "app": "gps",
                    "data": {
                        "icon_type": "subway",
                        "distance": "0ft",
                        "street": "L Train",
                        "lines": [[64, 50, 64, 20]]
                    }
                }
                send_framed_json(sock, payload)
                
            elif choice == '4':
                payload = {
                    "app": "translation",
                    "data": "Hola mundo -> Hello world\nGracias -> Thank you",
                    "groupings": []
                }
                send_framed_json(sock, payload)
                
            elif choice == 'p':
                payload = {"_dawggles_ping": True}
                send_framed_json(sock, payload)
                
            elif choice == 'q':
                stop_event.set()
                break
            else:
                print("Invalid choice.")
                
    except ConnectionRefusedError:
        print(f"Failed to connect to {host}:{port}. Is the Dawggles server running?")
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        if 'sock' in locals():
            sock.close()
            print("Connection closed.")

if __name__ == "__main__":
    main()
