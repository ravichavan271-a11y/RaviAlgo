import os, json, threading, time, logging, sys, subprocess
from datetime import datetime
import pytz, requests
from flask import Flask, jsonify, request, redirect

# --- AUTO-INSTALL MISSING PACKAGES (pyotp fix) ---
def auto_install(package):
    try:
        __import__(package)
        print(f"✅ {package} already installed")
    except ImportError:
        print(f"⚠️ {package} not found - installing...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", package])
            print(f"✅ {package} installed successfully!")
        except Exception as e:
            print(f"❌ Failed to install {package}: {e}")

# Try to auto-install critical packages
for pkg in ["pyotp", "pandas", "gspread", "upstox_client"]:
    try:
        auto_install(pkg)
    except: pass

logging.getLogger('werkzeug').setLevel(logging.ERROR)
import warnings
warnings.filterwarnings("ignore")

IST = pytz.timezone('Asia/Kolkata')
TOKEN_FILE = "upstox_token.txt"
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN","")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID","")

app = Flask(__name__)
RUN_24_7 = True

def send_telegram_msg(text):
    try:
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID: return
        url=f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=10)
    except: pass

def get_token_automatic():
    tok = os.environ.get("UPSTOX_ACCESS_TOKEN","") or os.environ.get("UPSTOX_TOKEN","")
    if tok and len(tok) > 50:
        return tok
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE,"r") as f: 
                tok=f.read().strip()
            if tok and len(tok) > 50:
                os.environ["UPSTOX_ACCESS_TOKEN"]=tok
                return tok
        except: pass
    try:
        import gspread
        from oauth2client.service_account import ServiceAccountCredentials
        scope = ["https://spreadsheets.google.com/feeds","https://www.googleapis.com/auth/drive"]
        gc = gspread.authorize(ServiceAccountCredentials.from_json_keyfile_name("service_account.json", scope))
        sh = gc.open("Dsheet")
        try:
            b1 = sh.sheet1.cell(1,2).value
            if b1 and "eyJ" in str(b1) and len(str(b1))>100:
                with open(TOKEN_FILE,"w") as f: f.write(str(b1).strip())
                os.environ["UPSTOX_ACCESS_TOKEN"]=str(b1).strip()
                return str(b1).strip()
        except: pass
    except: pass
    return ""

def is_token_valid(token):
    try:
        r = requests.get("https://api.upstox.com/v2/user/profile", headers={"Authorization": f"Bearer {token}"}, timeout=10)
        return r.status_code == 200
    except:
        return True

def get_token():
    return get_token_automatic()

file_status = {
    "kavyadarsh": {"running": False, "last_start": "", "error": "", "count": 0, "file_exists": False},
    "upstock4": {"running": False, "last_start": "", "error": "", "count": 0, "file_exists": False}
}

def check_files():
    file_status["kavyadarsh"]["file_exists"] = os.path.exists("KavyaDarsh.py") or os.path.exists("kavyadarsh.py")
    file_status["upstock4"]["file_exists"] = os.path.exists("Upstock4.py") or os.path.exists("upstock4.py")

def run_kavyadarsh():
    global file_status
    check_files()
    while True:
        try:
            print(f"[{datetime.now(IST).strftime('%H:%M:%S')}] Starting KavyaDarsh.py - 24x7 MODE (Angel One - Auto TOTP)")
            # Auto-install pyotp before import
            try:
                import pyotp
            except ImportError:
                print("pyotp missing in KavyaDarsh - installing...")
                subprocess.check_call([sys.executable, "-m", "pip", "install", "pyotp"])
                import pyotp
            file_status["kavyadarsh"]["running"] = True
            file_status["kavyadarsh"]["last_start"] = datetime.now(IST).isoformat()
            file_status["kavyadarsh"]["count"] += 1
            file_status["kavyadarsh"]["error"] = ""
            if os.path.exists("KavyaDarsh.py"):
                if 'KavyaDarsh' in sys.modules: del sys.modules['KavyaDarsh']
                import KavyaDarsh
            elif os.path.exists("kavyadarsh.py"):
                if 'kavyadarsh' in sys.modules: del sys.modules['kavyadarsh']
                import kavyadarsh
            else:
                file_status["kavyadarsh"]["error"] = "File not found"
                file_status["kavyadarsh"]["running"] = False
                time.sleep(30)
                continue
        except Exception as e:
            import traceback
            err = str(e)[:500]
            tb = traceback.format_exc()[:1000]
            print(f"KavyaDarsh CRASHED: {err}\n{tb}")
            file_status["kavyadarsh"]["error"] = err
            file_status["kavyadarsh"]["running"] = False
            time.sleep(5)

def run_upstox():
    global file_status
    check_files()
    while True:
        try:
            print(f"[{datetime.now(IST).strftime('%H:%M:%S')}] Starting Upstock4.py - AUTOMATIC TOKEN MODE (Magachya sheet sarkha)")
            file_status["upstock4"]["running"] = True
            file_status["upstock4"]["last_start"] = datetime.now(IST).isoformat()
            file_status["upstock4"]["count"] += 1
            file_status["upstock4"]["error"] = ""
            tok = get_token_automatic()
            if not tok:
                print("WARNING: Token not found - will try auto fetch from sheet/file")
                send_telegram_msg(f"⚠️ Upstox Token missing! Login: https://ravialgo.onrender.com/upstox-login")
            else:
                print(f"Token found: {tok[:15]}... valid check...")
                if not is_token_valid(tok):
                    print("Token INVALID/EXPIRED - sending Telegram")
                    send_telegram_msg(f"⚠️ <b>Upstox Token Expired!</b>\nLogin: https://ravialgo.onrender.com/upstox-login\nTime: {datetime.now(IST).strftime('%H:%M:%S')}")
            if os.path.exists("Upstock4.py"):
                import sys
                if 'Upstock4' in sys.modules: del sys.modules['Upstock4']
                import Upstock4
            elif os.path.exists("upstock4.py"):
                import sys
                if 'upstock4' in sys.modules: del sys.modules['upstock4']
                import upstock4
            else:
                file_status["upstock4"]["error"] = "File not found"
                file_status["upstock4"]["running"] = False
                time.sleep(30)
                continue
        except Exception as e:
            import traceback
            err = str(e)[:500]
            print(f"Upstock4 CRASHED: {err}\n{traceback.format_exc()[:500]}")
            file_status["upstock4"]["error"] = err
            file_status["upstock4"]["running"] = False
            time.sleep(5)

def send_telegram():
    try:
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID: return
        url=f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload={"chat_id": TELEGRAM_CHAT_ID, "text": f"✅ <b>24x7 AUTOMATIC TOKEN MODE ACTIVE</b>\n\nUpstock4.py (Upstox - Auto Token from Sheet/ENV/File) - Magachya sheet sarkha!\nKavyaDarsh.py (Angel One - Auto TOTP)\n\nUpstox Login: https://ravialgo.onrender.com/upstox-login\n\nTime: {datetime.now(IST).strftime('%H:%M:%S')}", "parse_mode": "HTML"}
        requests.post(url, json=payload, timeout=10)
        print("Telegram 24x7 alert sent")
    except Exception as e:
        print(f"Telegram error: {e}")

def keep_alive():
    while True:
        try:
            try: requests.get('https://ravialgo.onrender.com/ping',timeout=5)
            except: pass
            time.sleep(300)
        except: time.sleep(60)

def auto_token_watcher():
    while True:
        try:
            time.sleep(300)
            tok = get_token_automatic()
            if not tok or not is_token_valid(tok):
                print("Auto Watcher: Token expired/missing - Telegram alert")
                send_telegram_msg(f"⚠️ <b>Auto Token Watcher</b>\nUpstox token expired/missing!\n\n1-click Login: https://ravialgo.onrender.com/upstox-login\n\nTumhi Dsheet madhe B1 cell madhe navin token takla tar auto gheil!\nTime: {datetime.now(IST).strftime('%H:%M:%S IST')}")
        except Exception as e:
            print(f"Auto watcher error: {e}")
            time.sleep(60)

@app.route('/')
def home():
    tok = get_token_automatic()
    valid = is_token_valid(tok) if tok else False
    return f'''
    <h1>✅ 24x7 AUTOMATIC TOKEN MODE - Magachya Sheet Sarkha</h1>
    <p>Time: {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S IST')}</p>
    <p><b>Angel One (KavyaDarsh):</b> Auto TOTP - No daily token needed ✅ - Roj lagat nahi!</p>
    <p><b>Upstox (Upstock4):</b> Automatic Token - Sheet/ENV/File ✅ - Roj lagto pan AUTOMATIC!</p>
    <p><b>Token Status:</b> Exists={bool(tok)} Valid={valid} File={os.path.exists(TOKEN_FILE)}</p>
    <hr>
    <p><a href="/status">/status - JSON Status</a></p>
    <p><a href="/logs">/logs - Logs</a></p>
    <p><a href="/ping">/ping</a></p>
    <p><a href="/upstox-login"><b>/upstox-login - 1 Click Login (Daily 8:30 AM)</b></a></p>
    <hr>
    <p><b>Automatic Logic (Magachya sheet sarkha):</b></p>
    <p>1. ENV token -> 2. File token -> 3. Sheet B1 cell token -> Auto!</p>
    '''

@app.route('/ping')
def ping():
    return f"PONG {datetime.now(IST).strftime('%H:%M:%S')} AutoToken={bool(get_token_automatic())} Upstock4={file_status['upstock4']['running']} KavyaDarsh={file_status['kavyadarsh']['running']}",200

@app.route('/status')
def status():
    check_files()
    tok = get_token_automatic()
    return jsonify({
        "time": datetime.now(IST).isoformat(),
        "mode": "AUTOMATIC TOKEN - Sheet/ENV/File - Magachya sheet sarkha",
        "angel_one": "Auto TOTP - No daily token",
        "upstox": "Roj token lagto - pan AUTOMATIC from Sheet/ENV/File",
        "token_automatic": {
            "exists": bool(tok),
            "valid": is_token_valid(tok) if tok else False,
            "source": "ENV" if os.environ.get("UPSTOX_ACCESS_TOKEN") else ("FILE" if os.path.exists(TOKEN_FILE) else "NONE"),
            "preview": f"{tok[:15]}...{tok[-5:]}" if tok else "NO TOKEN"
        },
        "files": file_status,
        "token_file_exists": os.path.exists(TOKEN_FILE),
        "env_token_exists": bool(os.environ.get("UPSTOX_ACCESS_TOKEN")),
        "telegram_configured": bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID),
    })

@app.route('/logs')
def logs():
    check_files()
    tok = get_token_automatic()
    valid = is_token_valid(tok) if tok else False
    html = f'''
    <html><head><meta http-equiv="refresh" content="10"></head><body>
    <h2>🔥 AUTOMATIC TOKEN - {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')} (Auto refresh 10s)</h2>
    <p><b>Angel One:</b> Auto TOTP (Token lagat nahi) | <b>Upstox:</b> Automatic (Sheet/ENV/File) - Roj lagto pan auto!</p>
    <p><b>Token:</b> Exists={bool(tok)} Valid={valid} Source={"ENV" if os.environ.get("UPSTOX_ACCESS_TOKEN") else "FILE" if os.path.exists(TOKEN_FILE) else "NONE"}</p>
    <hr>
    <h3>Upstock4.py (Upstox - Roj token lagto - AUTOMATIC - Magachya sheet sarkha)</h3>
    <p>Running: {file_status["upstock4"]["running"]} | Count: {file_status["upstock4"]["count"]}<br>Error: {file_status["upstock4"]["error"]}</p>
    <hr>
    <h3>KavyaDarsh.py (Angel One - Token lagat nahi - Auto)</h3>
    <p>Running: {file_status["kavyadarsh"]["running"]} | Count: {file_status["kavyadarsh"]["count"]}<br>Error: {file_status["kavyadarsh"]["error"]}</p>
    <hr>
    <p><b>How Automatic Works (Magachya sheet sarkha):</b><br>
    1. ENV var UPSTOX_ACCESS_TOKEN check<br>
    2. File upstox_token.txt check<br>
    3. Dsheet Google Sheet B1 cell check (tu tithe token taklas tar auto gheil)<br>
    4. /upstox-login ne login kelas tar auto save to FILE + ENV + SHEET B1</p>
    <p><a href="/status">JSON</a> | <a href="/ping">Ping</a> | <a href="/">Home</a> | <a href="/upstox-login"><b>LOGIN</b></a></p>
    </body></html>
    '''
    return html

@app.route('/upstox-login')
def upstox_login():
    api_key=os.environ.get("UPSTOX_API_KEY")
    if not api_key:
        return "UPSTOX_API_KEY not set in ENV", 400
    redirect_uri="https://ravialgo.onrender.com/upstox/callback"
    url=f"https://api.upstox.com/v2/login/authorization/dialog?response_type=code&client_id={api_key}&redirect_uri={redirect_uri}"
    return redirect(url)

@app.route('/upstox/callback')
def upstox_callback():
    code=request.args.get("code")
    api_key=os.environ.get("UPSTOX_API_KEY")
    api_secret=os.environ.get("UPSTOX_API_SECRET")
    redirect_uri="https://ravialgo.onrender.com/upstox/callback"
    try:
        url="https://api.upstox.com/v2/login/authorization/token"
        headers={'accept':'application/json','Content-Type':'application/x-www-form-urlencoded'}
        data={'code':code,'client_id':api_key,'client_secret':api_secret,'redirect_uri':redirect_uri,'grant_type':'authorization_code'}
        resp=requests.post(url,headers=headers,data=data,timeout=10)
        token_data=resp.json()
        access_token=token_data.get('access_token')
        if access_token:
            with open(TOKEN_FILE,"w") as f: f.write(access_token)
            os.environ["UPSTOX_ACCESS_TOKEN"]=access_token
            try:
                import gspread
                from oauth2client.service_account import ServiceAccountCredentials
                scope = ["https://spreadsheets.google.com/feeds","https://www.googleapis.com/auth/drive"]
                gc = gspread.authorize(ServiceAccountCredentials.from_json_keyfile_name("service_account.json", scope))
                sh = gc.open("Dsheet")
                sh.sheet1.update_cell(1,2, access_token)
                print("Token also saved to Sheet B1 for automatic!")
            except Exception as e:
                print(f"Sheet save error (ignore): {e}")
            send_telegram_msg(f"✅ <b>Upstox Token Saved Automatically!</b>\nMagachya sheet sarkha automatic zala!\nTime: {datetime.now(IST).strftime('%H:%M:%S')}")
            return f"<h1>✅ Token Save! AUTOMATIC MODE - Magachya Sheet Sarkha</h1><p>Token saved to FILE + ENV + SHEET B1!</p><p>Upstock4.py auto-restart hoil!</p><a href='/logs'>Logs</a> | <a href='/status'>Status</a>"
        else: return f"Error: {token_data}"
    except Exception as e: return f"Error: {e}"

print("=== STARTING 24x7 AUTOMATIC TOKEN MODE - MAGACHYA SHEET SARKHA ===")
check_files()
threading.Thread(target=run_kavyadarsh,daemon=True).start()
threading.Thread(target=run_upstox,daemon=True).start()
threading.Thread(target=send_telegram,daemon=True).start()
threading.Thread(target=keep_alive,daemon=True).start()
threading.Thread(target=auto_token_watcher,daemon=True).start()

if __name__=="__main__":
    port=int(os.environ.get("PORT",10000))
    print(f"Flask starting on port {port} - AUTOMATIC TOKEN MODE")
    app.run(host='0.0.0.0',port=port, threaded=True)
