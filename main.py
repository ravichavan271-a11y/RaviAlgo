import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import subprocess

# 1. Render Web Service जिवंत ठेवण्यासाठी Keep-Alive HTTP Server
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Algo Scanner is Active & Running!")

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
    print(f"Web server active on port {port}")
    server.serve_forever()

# Background thread मध्ये सर्व्हर सुरू करा
threading.Thread(target=run_web_server, daemon=True).start()

# 2. दोन्ही स्कॅनर्स (Upstock4.py आणि KavyaDarsh.py) एकाच वेळी चालवणे
def start_scanners():
    print("Starting दोन्ही स्कॅनर्स...")
    p1 = subprocess.Popen(["python", "Upstock4.py"])
    p2 = subprocess.Popen(["python", "KavyaDarsh.py"])
    p1.wait()
    p2.wait()

if __name__ == "__main__":
    start_scanners()
