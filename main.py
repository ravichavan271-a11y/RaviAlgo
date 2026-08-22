
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
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
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
    te = "✅ Token aahe" if os.path.exists(TOKEN_FILE) else "❌ Token nahi ( /upstox-login kara )"
    ce = "✅ Service Account aahe" if os.path.exists("service_account.json") or os.path.exists("/etc/secrets/service_account.json") else "❌ Google Creds nahi"
    tg = "✅ Active" if TELEGRAM_BOT_TOKEN else "❌ Token nahi"
    market = "🟢 Market CHALU - Script chalat aahe" if is_market_hours() else "🔴 Market BAND - Script sleeping (9:00 la auto chalu hoil)"
    now_str = datetime.now(IST).strftime("%d-%m-%Y %H:%M:%S IST")
    # Check last log from threads
    import threading
    threads = threading.enumerate()
    thread_list = ", ".join([t.name for t in threads])
    return f"""
    <html><head><meta http-equiv='refresh' content='30'></head><body style='font-family: sans-serif; padding:20px;'>
    <h1>✅ Ravi Algo LIVE</h1>
    <p><b>Vel:</b> {now_str} (Auto refresh 30 sec)</p>
    <p><b>Market Status:</b> {market}</p>
    <hr>
    <h3>System Check:</h3>
    <p>📄 Google Sheet: {ce}</p>
    <p>📈 Upstox Token: {te}</p>
    <p>📱 Telegram: {tg} -> Chat TELEGRAM_CHAT_ID or "Not Set"</p>
    <p>🧵 Threads: {len(threads)} active ({thread_list})</p>
    <p>🔄 Keep Alive: Active (10 min self ping)</p>
    <p>🌐 UptimeRobot: Active (5 min ping to /ping)</p>
    <hr>
    <h3>Kasa samjaycha Script chaltey ka?</h3>
    <ul>
        <li>Market CHALU asel tar: <b>Google Sheet madhe LIVE TIME badalat asel</b></li>
        <li>Market BAND asel tar: <b>Waiting... disat asel logs madhe</b></li>
        <li>Telegram var Deploy message yeto</li>
        <li>/ping var PONG yeto</li>
    </ul>
    <hr>
    <a href='/upstox-login'><button style='padding:12px 24px; font-size:16px; cursor:pointer;'>🔐 Upstox Login Kara</button></a>
    &nbsp; <a href='/ping'><button style='padding:12px 24px;'>Ping Test</button></a>
    &nbsp; <a href='/status'><button style='padding:12px 24px;'>Detailed Status</button></a>
    <br><br><small>PC Shutdown nantar pan chalu rahil - Render + UptimeRobot var chaltey</small>
    </body></html>
    """

@app.route('/status')
def status():
    te = os.path.exists(TOKEN_FILE)
    ce = os.path.exists("service_account.json") or os.path.exists("/etc/secrets/service_account.json")
    market = is_market_hours()
    now_str = datetime.now(IST).strftime("%d-%m-%Y %H:%M:%S IST")
    import threading
    thread_info = "<br>".join([f"- {t.name} : {'alive' if t.is_alive() else 'dead'}" for t in threading.enumerate()])
    # Try to get google sheet last update
    sheet_status = "Unknown"
    try:
        if ce:
            sheet_status = "Google Sheet connected"
    except:
        pass
    return f"""
    <h2>🔍 Detailed Status - {now_str}</h2>
    <p><b>Market Hours:</b> {market} (9:00-15:30 Mon-Fri)</p>
    <p><b>Upstox Token Exists:</b> {te}</p>
    <p><b>Google Creds Exists:</b> {ce}</p>
    <p><b>Telegram Config:</b> Token={ "Set" if TELEGRAM_BOT_TOKEN else "Not Set"}... Chat={ TELEGRAM_CHAT_ID if TELEGRAM_CHAT_ID else "Not Set"}</p>
    <p><b>Active Threads:</b><br>{thread_info}</p>
    <p><b>Sheet Status:</b> {sheet_status}</p>
    <hr>
    <p><b>PC Shutdown nantar chaltey ka?</b><br>
    HO! Ha Render.com var chaltoy, tujhya PC var nahi. UptimeRobot dar 5 min la ping karto mhanun band padat nahi.</p>
    <a href='/'>Back to Home</a>
    """


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

def send_telegram_startup():
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": f"✅ <b>Ravi Algo DEPLOYED - {__import__('datetime').datetime.now(__import__('pytz').timezone('Asia/Kolkata')).strftime('%d-%m %H:%M IST')}</b>\n\nService LIVE aahe! Market chalu jhalyavar screener suru hoil.\nUptimeRobot: Active\nPing: /ping OK",
            "parse_mode": "HTML"
        }
        import requests
        resp = requests.post(url, json=payload, timeout=10)
        print(f"TELEGRAM DEPLOY MSG: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        print(f"Telegram startup error: {e}")

def keep_alive():
    while True:
        try:
            if is_market_hours():
                try: requests.get('https://ravialgo.onrender.com/ping',timeout=10)
                except: pass
            time.sleep(600)
        except: time.sleep(60)

threading.Thread(target=run_kavyadarsh,daemon=True).start()
threading.Thread(target=send_telegram_startup,daemon=True).start()
threading.Thread(target=run_upstox,daemon=True).start()
threading.Thread(target=keep_alive,daemon=True).start()

if __name__=="__main__":
    port=int(os.environ.get("PORT",10000))
    app.run(host='0.0.0.0',port=port)
