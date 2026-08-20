import threading, os
from flask import Flask
app = Flask(__name__)

@app.route('/')
def home():
    return "Screener LIVE - KavyaDarsh + Upstox"

def run_kavya():
    import KavyaDarsh
def run_upstox():
    import Upstock4

threading.Thread(target=run_kavya, daemon=True).start()
threading.Thread(target=run_upstox, daemon=True).start()

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
