
import threading
import os
import time
import requests
import json
import shutil
from datetime import datetime
import pytz
from flask import Flask, redirect, request

app = Flask(__name__)
TOKEN_FILE = "upstox_token.txt"
IST = pytz.timezone('Asia/Kolkata')

if os.path.exists("/etc/secrets/service_account.json"):
    try:
        shutil.copy("/etc/secrets/service_account.json", "service_account.json")
    except: pass
elif os.environ.get("GOOGLE_CREDENTIALS"):
    try:
        with open("service_account.json","w") as sf:
            sf.write(os.environ.get("GOOGLE_CREDENTIALS"))
    except: pass

def is_market_hours():
    now = datetime.now(IST)
    if now.weekday() >=5: return False
    return now.replace(hour=9,minute=0,second=0) <= now <= now.replace(hour=15,minute=30,second=0)

@app.route('/')
def home():
    te = "✅" if os.path.exists(TOKEN_FILE) else "❌"
    ce = "✅" if os.path.exists("service_account.json") else "❌"
    tg = "✅ Active" if os.environ.get("TELEGRAM_BOT_TOKEN") else "❌ Token nahi"
    market = "🟢 CHALU" if is_market_hours() else "🔴 BAND"
    now_str = datetime.now(IST).strftime("%d-%m-%Y %H:%M:%S")
    return f"<h1>Ravi Algo LIVE - Telegram Added</h1><p>Vel: {now_str} | Market: {market}</p><p>Google: {ce} | Upstox: {te} | Telegram: {tg}</p><a href='/upstox-login'><button>Upstox Login</button></a><br><small>/ping OK</small>"

@app.route('/ping')
def ping():
    return f"PONG {datetime.now(IST).strftime('%H:%M:%S')}",200

@app.route('/upstox-login')
def upstox_login():
    api_key = os.environ.get("UPSTOX_API_KEY")
    redirect_uri = "https://ravialgo.onrender.com/upstox/callback"
    url = f"https://api.upstox.com/v2/login/authorization/dialog?response_type=code&client_id={api_key}&redirect_uri={redirect_uri}"
    return redirect(url)

@app.route('/upstox/callback')
def upstox_callback():
    code = request.args.get("code")
    api_key = os.environ.get("UPSTOX_API_KEY")
    api_secret = os.environ.get("UPSTOX_API_SECRET")
    redirect_uri = "https://ravialgo.onrender.com/upstox/callback"
    try:
        url = "https://api.upstox.com/v2/login/authorization/token"
        headers = {'accept': 'application/json','Content-Type':'application/x-www-form-urlencoded'}
        data = {'code':code,'client_id':api_key,'client_secret':api_secret,'redirect_uri':redirect_uri,'grant_type':'authorization_code'}
        resp = requests.post(url,headers=headers,data=data)
        token_data = resp.json()
        access_token = token_data.get('access_token')
        if access_token:
            with open(TOKEN_FILE,"w") as f: f.write(access_token)
            os.environ["UPSTOX_ACCESS_TOKEN"]=access_token
            return f"<h1>✅ Token Save!</h1><a href='/'>Home</a>"
        else:
            return f"Error: {token_data}"
    except Exception as e:
        return f"Error: {e}"

def run_kavyadarsh():
    while True:
        try:
            if not is_market_hours():
                time.sleep(60); continue
            print(">>> KavyaDarsh CHALU...")
            import KavyaDarsh
        except Exception as e:
            print(f"KavyaDarsh Error {e}"); time.sleep(10)

def run_upstox():
    while True:
        try:
            if not is_market_hours():
                time.sleep(60); continue
            if not os.path.exists(TOKEN_FILE):
                time.sleep(60); continue
            print(">>> Upstock4 CHALU...")
            import Upstock4
        except Exception as e:
            print(f"Upstox Error {e}"); time.sleep(10)

def keep_alive():
    while True:
        try:
            if is_market_hours():
                try: requests.get('https://ravialgo.onrender.com/ping',timeout=10)
                except: pass
            time.sleep(600)
        except: time.sleep(60)

threading.Thread(target=run_kavyadarsh,daemon=True).start()
threading.Thread(target=run_upstox,daemon=True).start()
threading.Thread(target=keep_alive,daemon=True).start()

if __name__=="__main__":
    port=int(os.environ.get("PORT",10000))
    app.run(host='0.0.0.0',port=port)
