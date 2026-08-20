import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import subprocess

# 1. Render साठी Keep-Alive Web Server
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Algo Scanner is Running Live!")

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
    print(f"Web server started on port {port}")
    server.serve_forever()

# Background मध्ये वेब सर्व्हर चालू करा
threading.Thread(target=run_web_server, daemon=True).start()

# 2. तुमचे दोन्ही स्कॅनर्स रन करा
def run_scanners():
    print("Starting दोन्ही स्कॅनर्स...")
    # Upstock4.py आणि KavyaDarsh.py एकाच वेळी चालू राहतील
    p1 = subprocess.Popen(["python", "Upstock4.py"])
    p2 = subprocess.Popen(["python", "KavyaDarsh.py"])
    p1.wait()
    p2.wait()

if __name__ == "__main__":
    run_scanners()
