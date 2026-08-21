
import threading
import os
import time
import requests
from datetime import datetime, timedelta
import pytz
from flask import Flask, redirect, request

app = Flask(__name__)
TOKEN_FILE = "upstox_token.txt"
IST = pytz.timezone('Asia/Kolkata')

def is_market_hours():
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    market_start = now.replace(hour=9, minute=0, second=0, microsecond=0)
    market_end = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return market_start <= now <= market_end

@app.route('/')
def home():
    token_exists = os.path.exists(TOKEN_FILE)
    token_status = "✅ Token aahe" if token_exists else "❌ Token nahi"
    market = "🟢 Market CHALU" if is_market_hours() else "🔴 Market BAND"
    now_str = datetime.now(IST).strftime("%d-%m-%Y %H:%M:%S IST")
    return f"""
    <h1>Ravi Algo LIVE - Timing Control</h1>
    <p><b>Vel:</b> {now_str}</p>
    <p><b>Market:</b> {market} (9:00 AM - 3:30 PM)</p>
    <hr>
    <p><b>Angel KavyaDarsh:</b> Auto 9 to 3:30</p>
    <p><b>Upstox:</b> {token_status} (Auto 9 to 3:30)</p>
    <hr>
    <a href='/upstox-login'><button style='padding:10px 20px;'>Upstox Login Kara</button></a>
    """

@app.route('/upstox-login')
def upstox_login():
    api_key = os.environ.get("UPSTOX_API_KEY")
    if not api_key:
        return "UPSTOX_API_KEY Environment Variable madhe takla nahi."
    redirect_uri = "https://ravialgo.onrender.com/upstox/callback"
    url = f"https://api.upstox.com/v2/login/authorization/dialog?response_type=code&client_id={api_key}&redirect_uri={redirect_uri}"
    return redirect(url)

@app.route('/upstox/callback')
def upstox_callback():
    code = request.args.get("code")
    if not code:
        return "Code nahi milala"
    api_key = os.environ.get("UPSTOX_API_KEY")
    api_secret = os.environ.get("UPSTOX_API_SECRET")
    redirect_uri = "https://ravialgo.onrender.com/upstox/callback"
    try:
        url = "https://api.upstox.com/v2/login/authorization/token"
        headers = {'accept': 'application/json', 'Content-Type': 'application/x-www-form-urlencoded'}
        data = {'code': code, 'client_id': api_key, 'client_secret': api_secret, 'redirect_uri': redirect_uri, 'grant_type': 'authorization_code'}
        resp = requests.post(url, headers=headers, data=data)
        token_data = resp.json()
        access_token = token_data.get('access_token')
        if access_token:
            with open(TOKEN_FILE, "w") as f:
                f.write(access_token)
            os.environ["UPSTOX_ACCESS_TOKEN"] = access_token
            return f"<h1>✅ Token Save Jhala!</h1><p>Udya 9:00 te 3:30 aapoaap chalel.</p><a href='/'>Home</a>"
        else:
            return f"Error: {token_data}"
    except Exception as e:
        return f"Error: {e}"

def run_kavyadarsh():
    while True:
        try:
            if not is_market_hours():
                print(f"[KavyaDarsh] Market BAND. Waiting... {datetime.now(IST).strftime('%H:%M')}")
                time.sleep(60)
                continue
            print(">>> KavyaDarsh 9:00 AM CHALU...")
            import KavyaDarsh
            print(">>> KavyaDarsh 3:30 PM BAND...")
        except Exception as e:
            print(f"KavyaDarsh Error: {e}")
            time.sleep(10)

def run_upstox():
    while True:
        try:
            if not is_market_hours():
                print(f"[Upstox] Market BAND. Waiting...")
                time.sleep(60)
                continue
            if not os.path.exists(TOKEN_FILE):
                print("⚠️ Upstox token nahi, /upstox-login kara.")
                time.sleep(60)
                continue
            print(">>> Upstock4 9:00 AM CHALU...")
            import Upstock4
            print(">>> Upstox 3:30 PM BAND...")
        except Exception as e:
            print(f"Upstox Error: {e}")
            time.sleep(10)

threading.Thread(target=run_kavyadarsh, daemon=True).start()
threading.Thread(target=run_upstox, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
