import os, json, threading, time, logging
from datetime import datetime
import pytz, requests
from flask import Flask, jsonify, request, redirect

# --- CONFIG ---
logging.getLogger('werkzeug').setLevel(logging.ERROR)
import warnings
warnings.filterwarnings("ignore")

IST = pytz.timezone('Asia/Kolkata')
TOKEN_FILE = "upstox_token.txt"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN","")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID","")

app = Flask(__name__)

# --- 24x7 MODE - ALWAYS RUN ---
RUN_24_7 = True  # Sarv time chalnari file

def get_token():
    tok = os.environ.get("UPSTOX_ACCESS_TOKEN","") or os.environ.get("UPSTOX_TOKEN","")
    if not tok and os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE,"r") as f: tok=f.read().strip()
        except: pass
    return tok

# Track status
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
            print(f"[{datetime.now(IST).strftime('%H:%M:%S')}] Starting KavyaDarsh.py - 24x7 MODE - No time check")
            file_status["kavyadarsh"]["running"] = True
            file_status["kavyadarsh"]["last_start"] = datetime.now(IST).isoformat()
            file_status["kavyadarsh"]["count"] += 1
            file_status["kavyadarsh"]["error"] = ""
            
            if os.path.exists("KavyaDarsh.py"):
                # Remove cached module if exists to allow restart
                if 'KavyaDarsh' in globals() or 'KavyaDarsh' in dir():
                    try:
                        import sys
                        if 'KavyaDarsh' in sys.modules:
                            del sys.modules['KavyaDarsh']
                    except: pass
                import KavyaDarsh
            elif os.path.exists("kavyadarsh.py"):
                import sys
                if 'kavyadarsh' in sys.modules:
                    del sys.modules['kavyadarsh']
                import kavyadarsh
            else:
                print("KavyaDarsh.py NOT FOUND in current directory")
                print(f"Files in dir: {os.listdir('.')}")
                file_status["kavyadarsh"]["error"] = "File not found - Upload KavyaDarsh.py to GitHub"
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
            print(f"[{datetime.now(IST).strftime('%H:%M:%S')}] Starting Upstock4.py - 24x7 MODE - No time check")
            file_status["upstock4"]["running"] = True
            file_status["upstock4"]["last_start"] = datetime.now(IST).isoformat()
            file_status["upstock4"]["count"] += 1
            file_status["upstock4"]["error"] = ""
            
            # Token check - log but don't stop, ENV token may exist
            tok = get_token()
            if not tok:
                print("WARNING: Upstox token not found in file or ENV, but still trying to run Upstock4.py")
            else:
                print(f"Token found: {tok[:10]}...{tok[-5:]}")
            
            if os.path.exists("Upstock4.py"):
                import sys
                if 'Upstock4' in sys.modules:
                    del sys.modules['Upstock4']
                import Upstock4
            elif os.path.exists("upstock4.py"):
                import sys
                if 'upstock4' in sys.modules:
                    del sys.modules['upstock4']
                import upstock4
            else:
                print("Upstock4.py NOT FOUND")
                print(f"Files in dir: {os.listdir('.')}")
                file_status["upstock4"]["error"] = "File not found - Upload Upstock4.py to GitHub"
                file_status["upstock4"]["running"] = False
                time.sleep(30)
                continue
                
        except Exception as e:
            import traceback
            err = str(e)[:500]
            tb = traceback.format_exc()[:1000]
            print(f"Upstock4 CRASHED: {err}\n{tb}")
            file_status["upstock4"]["error"] = err
            file_status["upstock4"]["running"] = False
            time.sleep(5)

def send_telegram():
    try:
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            print("Telegram not configured")
            return
        url=f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload={
            "chat_id": TELEGRAM_CHAT_ID, 
            "text": f"✅ 24x7 MODE ACTIVE\n\nUpstock4.py + KavyaDarsh.py donhi chalu aahet!\nPaper trading kadhla aahe\nMobile app kadhla aahe\nTime check OFF - Testing mode\n\nTime: {datetime.now(IST).strftime('%H:%M:%S')}", 
            "parse_mode": "HTML"
        }
        requests.post(url, json=payload, timeout=10)
        print("Telegram 24x7 alert sent")
    except Exception as e:
        print(f"Telegram error: {e}")

def keep_alive():
    while True:
        try:
            # Self ping to keep Render awake
            try: 
                requests.get('https://ravialgo.onrender.com/ping',timeout=5)
                print(f"Keep alive ping {datetime.now(IST).strftime('%H:%M:%S')}")
            except: pass
            time.sleep(300)  # 5 min
        except: time.sleep(60)

# --- ROUTES - NO PAPER TRADING, NO MOBILE APP ---

@app.route('/')
def home():
    return f'''
    <h1>✅ 24x7 Mode - Upstock4 + KavyaDarsh</h1>
    <p>Time: {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S IST')}</p>
    <p>RUN_24_7: {RUN_24_7} - Sarv time chalnari</p>
    <p><b>Paper trading:</b> Kadhlay ✅</p>
    <p><b>Mobile app:</b> Kadhlay ✅</p>
    <hr>
    <p><a href="/status">/status - JSON Status</a></p>
    <p><a href="/logs">/logs - Logs</a></p>
    <p><a href="/ping">/ping</a></p>
    <p><a href="/upstox-login">/upstox-login - Upstox Login</a></p>
    <hr>
    <p>Files: {os.listdir('.')[:15]}</p>
    '''

@app.route('/ping')
def ping():
    return f"PONG {datetime.now(IST).strftime('%H:%M:%S')} RUN_24_7={RUN_24_7} Upstock4={file_status['upstock4']['running']} KavyaDarsh={file_status['kavyadarsh']['running']}",200

@app.route('/status')
def status():
    check_files()
    token_exists = os.path.exists(TOKEN_FILE)
    env_token = bool(os.environ.get("UPSTOX_ACCESS_TOKEN") or os.environ.get("UPSTOX_TOKEN"))
    return jsonify({
        "time": datetime.now(IST).isoformat(),
        "run_24_7": RUN_24_7,
        "mode": "NO PAPER TRADING, NO MOBILE APP - ONLY Upstock4 + KavyaDarsh",
        "files": file_status,
        "token_file_exists": token_exists,
        "env_token_exists": env_token,
        "telegram_configured": bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID),
        "dir_files": [f for f in os.listdir('.') if f.endswith('.py')][:20]
    })

@app.route('/logs')
def logs():
    check_files()
    html = f'''
    <html><head><meta http-equiv="refresh" content="10"></head><body>
    <h2>🔥 24x7 Status - {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')} (Auto refresh 10s)</h2>
    <p><b>RUN_24_7:</b> {RUN_24_7} - Sarv time chalnar, time check OFF</p>
    <p><b>Paper trading:</b> ❌ Kadhlay | <b>Mobile app:</b> ❌ Kadhlay</p>
    <hr>
    <h3>Upstock4.py</h3>
    <p>File exists: {file_status["upstock4"]["file_exists"]} | Running: {file_status["upstock4"]["running"]} | Count: {file_status["upstock4"]["count"]}<br>
    Last start: {file_status["upstock4"]["last_start"]}<br>
    Error: {file_status["upstock4"]["error"]}</p>
    <hr>
    <h3>KavyaDarsh.py</h3>
    <p>File exists: {file_status["kavyadarsh"]["file_exists"]} | Running: {file_status["kavyadarsh"]["running"]} | Count: {file_status["kavyadarsh"]["count"]}<br>
    Last start: {file_status["kavyadarsh"]["last_start"]}<br>
    Error: {file_status["kavyadarsh"]["error"]}</p>
    <hr>
    <p><b>Token:</b> File={os.path.exists(TOKEN_FILE)} ENV={bool(os.environ.get("UPSTOX_ACCESS_TOKEN"))}</p>
    <p><a href="/status">JSON Status</a> | <a href="/ping">Ping</a> | <a href="/">Home</a></p>
    <p>All PY files: {[f for f in os.listdir('.') if f.endswith('.py')]}</p>
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
            return f"<h1>✅ Token Save!</h1><p>24x7 mode active - Both files will restart with new token</p><a href='/logs'>Logs</a> | <a href='/status'>Status</a>"
        else: return f"Error: {token_data}"
    except Exception as e: return f"Error: {e}"

# --- START THREADS ---
print("=== STARTING 24x7 MODE - NO PAPER TRADING, NO MOBILE APP ===")
print(f"Time: {datetime.now(IST)}")
check_files()
print(f"Files: KavyaDarsh exists={file_status['kavyadarsh']['file_exists']} Upstock4 exists={file_status['upstock4']['file_exists']}")

threading.Thread(target=run_kavyadarsh,daemon=True).start()
threading.Thread(target=run_upstox,daemon=True).start()
threading.Thread(target=send_telegram,daemon=True).start()
threading.Thread(target=keep_alive,daemon=True).start()

if __name__=="__main__":
    port=int(os.environ.get("PORT",10000))
    print(f"Flask starting on port {port} - 24x7 mode")
    app.run(host='0.0.0.0',port=port, threaded=True)
