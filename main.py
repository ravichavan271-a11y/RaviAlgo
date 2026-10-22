import os, json, threading, time, logging, sys
from datetime import datetime
import pytz, requests
from flask import Flask, jsonify, request, redirect

logging.getLogger('werkzeug').setLevel(logging.ERROR)
import warnings
warnings.filterwarnings("ignore")

IST = pytz.timezone('Asia/Kolkata')
TOKEN_FILE = "upstox_token.txt"
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN","")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID","")

def is_market_open():
    now = datetime.now(IST)
    if now.weekday() >= 5: return False
    market_start = now.replace(hour=9, minute=0, second=0, microsecond=0)
    market_end = now.replace(hour=15, minute=45, second=0, microsecond=0)
    return market_start <= now <= market_end

def get_market_status_msg():
    now = datetime.now(IST)
    if now.weekday() >=5: return f"Weekend - {now.strftime('%A %H:%M:%S IST')}"
    market_start = now.replace(hour=9, minute=0, second=0, microsecond=0)
    market_end = now.replace(hour=15, minute=45, second=0, microsecond=0)
    if now < market_start: return f"Market Ajun Open Nahi - {now.strftime('%H:%M:%S IST')}"
    elif now > market_end: return f"Market Band - {now.strftime('%H:%M:%S IST')}"
    else: return f"Market Chalu - {now.strftime('%H:%M:%S IST')}"

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
            try: return gspread.authorize(ServiceAccountCredentials.from_json_keyfile_name(SERVICE_FILE, scope))
            except: pass
    env_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "") or os.environ.get("SERVICE_ACCOUNT_JSON", "")
    if env_json:
        try:
            creds_dict = json.loads(env_json)
            return gspread.authorize(ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope))
        except: pass
    raise Exception("service_account.json missing")

def is_token_valid(token):
    if not token or len(token)<50: return False
    try:
        r = requests.get("https://api.upstox.com/v2/user/profile", headers={"Authorization": f"Bearer {token}"}, timeout=10)
        return r.status_code==200
    except: return False

def get_token_automatic():
    def token_ok(t): return t and len(t)>100 and "eyJ" in str(t)
    tok = os.environ.get("UPSTOX_ACCESS_TOKEN","") or os.environ.get("UPSTOX_TOKEN","")
    if token_ok(tok) and is_token_valid(tok): return tok
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE,"r") as f: tok=f.read().strip()
            if token_ok(tok) and is_token_valid(tok): return tok
        except: pass
    try:
        gc = get_gspread_client_main()
        sh = gc.open("Dsheet")
        b1 = str(sh.sheet1.cell(1,2).value or "").strip()
        if token_ok(b1) and is_token_valid(b1):
            with open(TOKEN_FILE,"w") as f: f.write(b1)
            os.environ["UPSTOX_ACCESS_TOKEN"]=b1
            return b1
    except Exception as e: print(f"B1 fetch error: {e}")
    return None

SCRIPTS=["Upstock4.py","option_bot.py"]

file_status = {n: {"running": False, "last_start": "", "error": "", "count": 0} for n in SCRIPTS}

def run_upstox():
    import subprocess
    base=os.path.dirname(os.path.abspath(__file__))
    procs={}; last_try={}
    while True:
        try:
            market=is_market_open()
            for name in SCRIPTS:
                p=procs.get(name); alive=bool(p and p.poll() is None)
                if not market:
                    if alive:
                        print(f"Market closed 3:45 - stopping {name}")
                        p.terminate()
                        try: p.wait(timeout=15)
                        except Exception: p.kill()
                    procs.pop(name,None); file_status[name]["running"]=False
                    continue
                file_status[name]["running"]=alive
                if alive: continue
                path=os.path.join(base,name)
                if not os.path.exists(path):
                    file_status[name]["error"]="file missing"; continue
                if time.time()-last_try.get(name,0)<60: continue
                last_try[name]=time.time()
                tok=get_token_automatic()
                if not tok:
                    print("Waiting for token..."); continue
                env=dict(os.environ, UPSTOX_ACCESS_TOKEN=tok, UPSTOX_TOKEN=tok, PYTHONUNBUFFERED="1")
                print(f"[{datetime.now(IST).strftime('%H:%M:%S')}] Starting {name}")
                procs[name]=subprocess.Popen([sys.executable, path], env=env, cwd=base)
                file_status[name].update(running=True, last_start=datetime.now(IST).isoformat(), error="")
                file_status[name]["count"]+=1
            time.sleep(10)
        except Exception as e:
            print(f"runner error: {e}"); time.sleep(10)

def auto_token_watcher():
    while True:
        try:
            time.sleep(300)
            get_token_automatic()
        except: time.sleep(60)

@app.route('/')
def home():
    tok=get_token_automatic()
    return f"<h1>✅ 9:00-3:45 AUTO MODE</h1><p>Time: {datetime.now(IST)}<br>Token: {bool(tok)}<br><a href='/status'>Status</a></p>"

@app.route('/ping')
def ping(): return f"PONG {datetime.now(IST)}",200

@app.route('/status')
def status():
    tok=get_token_automatic()
    return jsonify({"time": datetime.now(IST).isoformat(), "market_open": is_market_open(), "msg": get_market_status_msg(), "token_exists": bool(tok), "files": file_status})

@app.route('/upstox-login')
def upstox_login():
    api_key=os.environ.get("UPSTOX_API_KEY")
    if not api_key: return "API_KEY not set",400
    redirect_uri="https://ravialgo.onrender.com/upstox/callback"
    url=f"https://api.upstox.com/v2/login/authorization/dialog?response_type=code&client_id={api_key}&redirect_uri={redirect_uri}"
    return redirect(url)

@app.route('/upstox/callback')
def upstox_callback():
    code=request.args.get("code")
    api_key=os.environ.get("UPSTOX_API_KEY"); api_secret=os.environ.get("UPSTOX_API_SECRET")
    redirect_uri="https://ravialgo.onrender.com/upstox/callback"
    try:
        data={'code':code,'client_id':api_key,'client_secret':api_secret,'redirect_uri':redirect_uri,'grant_type':'authorization_code'}
        resp=requests.post("https://api.upstox.com/v2/login/authorization/token", headers={'Content-Type':'application/x-www-form-urlencoded'}, data=data, timeout=10)
        token_data=resp.json()
        access_token=token_data.get('access_token')
        if access_token:
            with open(TOKEN_FILE,"w") as f: f.write(access_token)
            os.environ["UPSTOX_ACCESS_TOKEN"]=access_token
            try:
                gc = get_gspread_client_main()
                sh = gc.open("Dsheet")
                sh.sheet1.update_cell(1,2, access_token)
            except: pass
            return f"<h1>✅ Token Saved!</h1><a href='/'>Home</a>"
        else: return f"Error: {token_data}"
    except Exception as e: return f"Error: {e}"

print("=== STARTING FIXED - UPSTOCK5/4 RUNNER ===")

def delayed_start():
    time.sleep(10)
    threading.Thread(target=run_upstox,daemon=True).start()
    threading.Thread(target=auto_token_watcher,daemon=True).start()
    print("✅ Upstox thread started")

threading.Thread(target=delayed_start,daemon=True).start()

if __name__=="__main__":
    port=int(os.environ.get("PORT",10000))
    app.run(host='0.0.0.0',port=port, threaded=True)
