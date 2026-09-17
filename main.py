import os, json, threading, time, logging, sys, subprocess
from datetime import datetime, timedelta
import pytz, requests
from flask import Flask, jsonify, request, redirect

logging.getLogger('werkzeug').setLevel(logging.ERROR)
import warnings
warnings.filterwarnings("ignore")

IST = pytz.timezone('Asia/Kolkata')
TOKEN_FILE = "upstox_token.txt"
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN","")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID","")

# --- MARKET HOURS 9:00 AM to 3:45 PM IST - Mon to Fri (FIXED AS PER YOUR REQUIREMENT) ---
def is_market_open():
    now = datetime.now(IST)
    if now.weekday() >= 5:  # Sat, Sun
        return False
    market_start = now.replace(hour=9, minute=0, second=0, microsecond=0)
    market_end = now.replace(hour=15, minute=45, second=0, microsecond=0)  # FIXED 3:45
    return market_start <= now <= market_end

def get_market_status_msg():
    now = datetime.now(IST)
    if now.weekday() >=5:
        return f"Weekend - Market Band - {now.strftime('%A %H:%M:%S IST')}"
    market_start = now.replace(hour=9, minute=0, second=0, microsecond=0)
    market_end = now.replace(hour=15, minute=45, second=0, microsecond=0)
    if now < market_start:
        return f"Market Ajun Open Nahi - Open 9:00 AM - Ata {now.strftime('%H:%M:%S IST')}"
    elif now > market_end:
        return f"Market Band Jhala - 3:45 PM - Ata {now.strftime('%H:%M:%S IST')}"
    else:
        return f"Market Chalu Aahe - {now.strftime('%H:%M:%S IST')}"

app = Flask(__name__)

def send_telegram_msg(text):
    try:
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID: return
        url=f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=10)
    except: pass

def get_gspread_client_main():
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials
    scope = ["https://spreadsheets.google.com/feeds","https://www.googleapis.com/auth/drive"]
    for SERVICE_FILE in ["service_account.json", "./service_account.json", "/etc/secrets/service_account.json"]:
        if os.path.exists(SERVICE_FILE):
            try:
                print(f"✅ Using service_account.json FILE at {SERVICE_FILE}")
                return gspread.authorize(ServiceAccountCredentials.from_json_keyfile_name(SERVICE_FILE, scope))
            except Exception as e:
                print(f"❌ File auth failed at {SERVICE_FILE}: {e}")
    env_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "") or os.environ.get("SERVICE_ACCOUNT_JSON", "")
    if env_json:
        try:
            import json as js
            creds_dict = js.loads(env_json)
            print(f"✅ Using service_account from ENV length {len(env_json)}")
            return gspread.authorize(ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope))
        except Exception as e:
            print(f"❌ ENV auth failed: {e}")
    raise Exception("service_account.json missing")

def is_token_valid(token):
    if not token or len(token)<50: return False
    try:
        r = requests.get("https://api.upstox.com/v2/user/profile", headers={"Authorization": f"Bearer {token}"}, timeout=10)
        if r.status_code==200:
            print("✅ Token VALID")
            return True
        else:
            print(f"❌ Token INVALID: {r.status_code} - {r.text[:100]}")
            return False
    except Exception as e:
        print(f"Token check error: {e}")
        return False  # FIXED: was return True before - BUG

def get_token_automatic():
    def token_ok(t): return t and len(t)>100 and "eyJ" in str(t)
    
    # 1. ENV first (fastest)
    tok = os.environ.get("UPSTOX_ACCESS_TOKEN","") or os.environ.get("UPSTOX_TOKEN","")
    if token_ok(tok) and is_token_valid(tok):
        return tok
    
    # 2. FILE
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE,"r") as f: 
                tok=f.read().strip()
            if token_ok(tok) and is_token_valid(tok):
                os.environ["UPSTOX_ACCESS_TOKEN"]=tok
                return tok
        except: pass
    
    # 3. SHEET B1 - MOST IMPORTANT FOR AUTOMATIC
    try:
        gc = get_gspread_client_main()
        sh = gc.open("Dsheet")
        b1 = str(sh.sheet1.cell(1,2).value or "").strip()
        if token_ok(b1):
            if is_token_valid(b1):
                print(f"✅ Token from SHEET B1 length {len(b1)}")
                with open(TOKEN_FILE,"w") as f: f.write(b1)
                os.environ["UPSTOX_ACCESS_TOKEN"]=b1
                return b1
            else:
                print("B1 token invalid/expired")
    except Exception as e:
        print(f"B1 fetch error: {e}")
    
    print("⚠ No valid token found - will wait for /upstox-login")
    return None

file_status = {
    "kavyadarsh": {"running": False, "last_start": "", "error": "", "count": 0},
    "upstock4": {"running": False, "last_start": "", "error": "", "count": 0}
}

def run_kavyadarsh():
    while True:
        if not is_market_open():
            file_status["kavyadarsh"]["running"]=False
            print(f"[{datetime.now(IST).strftime('%H:%M:%S')}] {get_market_status_msg()} - KavyaDarsh Sleep 60s")
            time.sleep(60)
            continue
        try:
            print(f"[{datetime.now(IST).strftime('%H:%M:%S')}] Starting KavyaDarsh.py - 9:00-3:45")
            file_status["kavyadarsh"]["running"]=True
            file_status["kavyadarsh"]["last_start"]=datetime.now(IST).isoformat()
            file_status["kavyadarsh"]["count"]+=1
            if 'KavyaDarsh' in sys.modules: del sys.modules['KavyaDarsh']
            import KavyaDarsh
        except Exception as e:
            import traceback
            print(f"KavyaDarsh CRASHED: {e}\n{traceback.format_exc()[:500]}")
            file_status["kavyadarsh"]["running"]=False
            time.sleep(10)

def run_upstox():
    while True:
        if not is_market_open():
            file_status["upstock4"]["running"]=False
            print(f"[{datetime.now(IST).strftime('%H:%M:%S')}] {get_market_status_msg()} - Upstock4 Sleep 60s")
            time.sleep(60)
            continue
        try:
            print(f"[{datetime.now(IST).strftime('%H:%M:%S')}] Starting Upstock4.py - 9:00-3:45")
            file_status["upstock4"]["running"]=True
            file_status["upstock4"]["last_start"]=datetime.now(IST).isoformat()
            file_status["upstock4"]["count"]+=1
            tok = get_token_automatic()
            if not tok:
                send_telegram_msg(f"⚠ Upstox Token missing! Login: https://ravialgo.onrender.com/upstox-login")
                print("Waiting 60s for token...")
                time.sleep(60)
                continue
            if 'Upstock4' in sys.modules: del sys.modules['Upstock4']
            import Upstock4
        except Exception as e:
            import traceback
            print(f"Upstock4 CRASHED: {e}\n{traceback.format_exc()[:500]}")
            file_status["upstock4"]["running"]=False
            time.sleep(10)

def auto_token_watcher():
    while True:
        try:
            time.sleep(300) # 5 min
            tok = get_token_automatic()
            if not tok:
                send_telegram_msg(f"⚠ <b>Token Expired</b>\nLogin: https://ravialgo.onrender.com/upstox-login\nTime: {datetime.now(IST).strftime('%H:%M:%S')}")
        except Exception as e:
            print(f"Watcher err {e}"); time.sleep(60)

@app.route('/')
def home():
    tok=get_token_automatic()
    return f"<h1>✅ 9:00-3:45 AUTO MODE</h1><p>Time: {datetime.now(IST)}<br>Token Exists: {bool(tok)}<br><a href='/upstox-login'>Login</a> | <a href='/status'>Status</a></p>"

@app.route('/ping')
def ping(): return f"PONG {datetime.now(IST)} MarketOpen={is_market_open()}",200

@app.route('/status')
def status():
    tok=get_token_automatic()
    return jsonify({"time": datetime.now(IST).isoformat(), "market_open": is_market_open(), "market_msg": get_market_status_msg(), "token_exists": bool(tok), "files": file_status})

@app.route('/upstox-login')
def upstox_login():
    api_key=os.environ.get("UPSTOX_API_KEY")
    if not api_key: return "UPSTOX_API_KEY not set",400
    redirect_uri="https://ravialgo.onrender.com/upstox/callback"
    url=f"https://api.upstox.com/v2/login/authorization/dialog?response_type=code&client_id={api_key}&redirect_uri={redirect_uri}"
    return redirect(url)

@app.route('/upstox/callback')
def upstox_callback():
    code=request.args.get("code")
    api_key=os.environ.get("UPSTOX_API_KEY"); api_secret=os.environ.get("UPSTOX_API_SECRET")
    redirect_uri="https://ravialgo.onrender.com/upstox/callback"
    try:
        url="https://api.upstox.com/v2/login/authorization/token"
        data={'code':code,'client_id':api_key,'client_secret':api_secret,'redirect_uri':redirect_uri,'grant_type':'authorization_code'}
        resp=requests.post(url, headers={'accept':'application/json','Content-Type':'application/x-www-form-urlencoded'}, data=data, timeout=10)
        token_data=resp.json()
        access_token=token_data.get('access_token')
        if access_token:
            with open(TOKEN_FILE,"w") as f: f.write(access_token)
            os.environ["UPSTOX_ACCESS_TOKEN"]=access_token
            try:
                gc = get_gspread_client_main()
                sh = gc.open("Dsheet")
                sh.sheet1.update_cell(1,2, access_token)
                print("✅ Token saved to SHEET B1")
            except Exception as e: print(f"Sheet save err: {e}")
            send_telegram_msg(f"✅ Token Saved Auto! Time: {datetime.now(IST)}")
            return f"<h1>✅ Token Saved to Sheet B1!</h1><p>9:00-3:45 auto chalel</p><a href='/'>Home</a>"
        else: return f"Error: {token_data}"
    except Exception as e: return f"Error: {e}"

print("=== STARTING 9:00-3:45 AUTO TOKEN MODE - FIXED ===")

def delayed_start():
    print("⏳ Waiting 10 sec for Flask bind...")
    time.sleep(10)
    threading.Thread(target=run_kavyadarsh,daemon=True).start()
    threading.Thread(target=run_upstox,daemon=True).start()
    threading.Thread(target=auto_token_watcher,daemon=True).start()
    print("✅ All threads started - 9:00-3:45 - Token Auto from B1")

threading.Thread(target=delayed_start,daemon=True).start()

if __name__=="__main__":
    port=int(os.environ.get("PORT",10000))
    app.run(host='0.0.0.0',port=port, threaded=True)
