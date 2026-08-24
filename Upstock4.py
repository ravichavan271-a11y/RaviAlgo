import gzip, io, time, threading, os, re, sys
from datetime import datetime, timedelta
import pytz
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd
import requests, urllib.parse
from concurrent.futures import ThreadPoolExecutor
import upstox_client

IST = pytz.timezone('Asia/Kolkata')

print("FINAL V30 - FIXED STRUCTURE MIX - HINDZINC HINDCOPPER - MARKET HOURS 9:00 to 15:30 IST")

# --- MARKET HOURS 9:00 AM to 3:30 PM IST - Mon to Fri ---
def is_market_open():
    now = datetime.now(IST)
    if now.weekday() >= 5:  # Sat, Sun
        return False
    market_start = now.replace(hour=9, minute=0, second=0, microsecond=0)
    market_end = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return market_start <= now <= market_end

def get_market_status_msg():
    now = datetime.now(IST)
    if now.weekday() >=5:
        return f"Weekend - Market Band - {now.strftime('%A %H:%M:%S IST')}"
    market_start = now.replace(hour=9, minute=0, second=0, microsecond=0)
    market_end = now.replace(hour=15, minute=30, second=0, microsecond=0)
    if now < market_start:
        return f"Market Ajun Open Nahi - Open 9:00 AM - Ata {now.strftime('%H:%M:%S IST')}"
    elif now > market_end:
        return f"Market Band Jhala - 3:30 PM - Ata {now.strftime('%H:%M:%S IST')}"
    else:
        return f"Market Chalu Aahe - {now.strftime('%H:%M:%S IST')}"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

def send_telegram_alert(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[TELEGRAM SKIP] {message[:100]}")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            print(f"TELEGRAM: {message}")
        else:
            print(f"Telegram Error: {resp.text}")
    except Exception as e:
        print(f"Telegram Exception: {e}")

SPREADSHEET_NAME = "Dsheet"
SERVICE_ACCOUNT_FILE = os.path.join(os.path.dirname(__file__), "service_account.json")

def get_gspread_client():
    scope = ["https://spreadsheets.google.com/feeds","https://www.googleapis.com/auth/drive"]
    possible_paths = [
        SERVICE_ACCOUNT_FILE,
        "./service_account.json",
        "/etc/secrets/service_account.json",
        "/etc/secrets/SERVICE_ACCOUNT_JSON",
        "service_account.json",
        os.path.join(os.getcwd(), "service_account.json")
    ]
    for path in possible_paths:
        if os.path.exists(path):
            try:
                print(f"✅ Using service_account.json FILE at {path}")
                return gspread.authorize(ServiceAccountCredentials.from_json_keyfile_name(path, scope))
            except Exception as e:
                print(f"❌ File auth failed at {path}: {e}")
    env_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "") or os.environ.get("GOOGLE_CREDENTIALS", "") or os.environ.get("SERVICE_ACCOUNT_JSON", "")
    if env_json:
        try:
            import json as js
            creds_dict = js.loads(env_json)
            print(f"✅ Using service_account from ENV GOOGLE_SERVICE_ACCOUNT_JSON (length {len(env_json)})")
            return gspread.authorize(ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope))
        except Exception as e:
            print(f"❌ ENV auth failed: {e} - length {len(env_json)}")
            try:
                with open(SERVICE_ACCOUNT_FILE, "w") as f:
                    f.write(env_json)
                print(f"✅ Wrote ENV to {SERVICE_ACCOUNT_FILE}, trying file auth")
                return gspread.authorize(ServiceAccountCredentials.from_json_keyfile_name(SERVICE_ACCOUNT_FILE, scope))
            except Exception as e2:
                print(f"❌ ENV to file auth also failed: {e2}")
    print(f"❌ NO service_account found! Checked paths {possible_paths} and ENV GOOGLE_SERVICE_ACCOUNT_JSON")
    try:
        print(f"📁 Current dir files: {os.listdir('.')[:20]}")
        if os.path.exists("/etc/secrets"):
            print(f"📁 /etc/secrets files: {os.listdir('/etc/secrets')[:20]}")
    except: pass
    raise Exception("service_account.json missing - add FILE or ENV GOOGLE_SERVICE_ACCOUNT_JSON")

def get_automatic_token():
    def token_looks_ok(t):
        return t and len(t) > 100 and "eyJ" in str(t)
    if os.path.exists("upstox_token.txt"):
        try:
            with open("upstox_token.txt","r") as f:
                tok = f.read().strip()
            if token_looks_ok(tok):
                print(f"✅ Token from FILE: {tok[:15]}...")
                if is_token_valid(tok):
                    os.environ["UPSTOX_ACCESS_TOKEN"] = tok
                    return tok
                else:
                    print(f"⚠ FILE token expired - trying other sources")
                    try: os.remove("upstox_token.txt")
                    except: pass
        except Exception as e:
            print(f"File token error: {e}")
    try:
        gc_temp = get_gspread_client()
        sh = gc_temp.open(SPREADSHEET_NAME)
        try:
            sheet1 = sh.sheet1
            b1_token = str(sheet1.cell(1,2).value or "").strip()
            if token_looks_ok(b1_token):
                print(f"✅ Token from SHEET B1: {b1_token[:15]}...")
                if is_token_valid(b1_token):
                    with open("upstox_token.txt","w") as f:
                        f.write(b1_token)
                    os.environ["UPSTOX_ACCESS_TOKEN"] = b1_token
                    return b1_token
                else:
                    print(f"⚠ SHEET B1 token expired")
        except Exception as e:
            print(f"B1 token fetch error: {e}")
        try:
            token_sheet = sh.worksheet("TOKEN")
            sheet_token = str(token_sheet.cell(1,1).value or token_sheet.cell(1,2).value or "").strip()
            if token_looks_ok(sheet_token):
                print(f"✅ Token from TOKEN sheet: {sheet_token[:15]}...")
                if is_token_valid(sheet_token):
                    with open("upstox_token.txt","w") as f:
                        f.write(sheet_token)
                    os.environ["UPSTOX_ACCESS_TOKEN"] = sheet_token
                    return sheet_token
                else:
                    print(f"⚠ TOKEN sheet token expired")
        except Exception as e:
            print(f"TOKEN sheet fetch error: {e}")
    except Exception as e:
        print(f"Sheet token fetch error: {e}")
    tok = os.environ.get("UPSTOX_ACCESS_TOKEN", "") or os.environ.get("UPSTOX_TOKEN", "")
    if token_looks_ok(tok):
        print(f"✅ Token from ENV: {tok[:15]}... checking validity")
        if is_token_valid(tok):
            return tok
        else:
            print(f"❌ ENV Token INVALID 401 - clearing ENV, will need new login")
            os.environ.pop("UPSTOX_ACCESS_TOKEN", None)
            os.environ.pop("UPSTOX_TOKEN", None)
    print("⚠ No valid token found - will wait for /upstox-login - https://ravialgo.onrender.com/upstox-login")
    return ""

def is_token_valid(token):
    if not token or len(token) < 50:
        print("❌ Token empty/invalid")
        return False
    try:
        url = "https://api.upstox.com/v2/user/profile"
        resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=10)
        if resp.status_code == 200:
            print("✅ Token VALID")
            return True
        else:
            print(f"❌ Token INVALID: {resp.status_code} - {resp.text[:100]}")
            return False
    except Exception as e:
        print(f"Token valid check error: {e}")
        return False

def wait_for_valid_token():
    global UPSTOX_ACCESS_TOKEN
    while True:
        tok = get_automatic_token()
        if tok and is_token_valid(tok):
            UPSTOX_ACCESS_TOKEN = tok
            print(f"✅ Valid token ready: {tok[:15]}...")
            return tok
        print("⏳ No valid token - waiting 60 sec for /upstox-login... (Telegram alert sent)")
        send_telegram_alert(f"⚠ <b>Upstox Token Missing/Expired!</b>\n\nLogin kara:\nhttps://ravialgo.onrender.com/upstox-login\n\nTime: {datetime.now(IST).strftime('%H:%M:%S')}")
        time.sleep(60)

UPSTOX_ACCESS_TOKEN = get_automatic_token()
if not UPSTOX_ACCESS_TOKEN or not is_token_valid(UPSTOX_ACCESS_TOKEN):
    print("⚠ No valid token at startup - entering wait loop...")
    UPSTOX_ACCESS_TOKEN = wait_for_valid_token()

alerted_symbols = {}

def parse_date(val):
    if not val: return ""
    s = str(val).strip().split()[0].replace("/", "-").replace(".", "-")
    s = re.sub(r'[^0-9\-]', '', s)
    for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
        try: return datetime.strptime(s[:10], fmt).strftime("%Y-%m-%d")
        except: pass
    return s[:10]

gc = None
sh = None
sheet = None
breakout_sheet = None
weekly_from = ""
weekly_to = ""

def connect_sheets():
    global gc, sh, sheet, breakout_sheet, weekly_from, weekly_to
    retry = 0
    while True:
        try:
            print(f"[{datetime.now(IST).strftime('%H:%M:%S')}] Connecting to Google Sheets... attempt {retry+1}")
            gc = get_gspread_client()
            sh = gc.open(SPREADSHEET_NAME)
            sheet = sh.sheet1
            try:
                breakout_sheet = sh.worksheet("BREAKOUT")
            except gspread.exceptions.WorksheetNotFound:
                breakout_sheet = sh.add_worksheet(title="BREAKOUT", rows="2000", cols="20")
            weekly_from = parse_date(sheet.cell(2,2).value)
            weekly_to = parse_date(sheet.cell(3,2).value)
            print(f"✅ Sheets connected! Weekly From: {weekly_from} To: {weekly_to}")
            return True
        except Exception as e:
            retry += 1
            print(f"❌ GOOGLE SHEET ERROR (attempt {retry}): {e} - Retrying in 30 sec... (NO EXIT - 24x7)")
            time.sleep(30)
            if retry % 10 == 0:
                send_telegram_alert(f"⚠ Google Sheet connect fail {retry} times: {str(e)[:100]}")

connect_sheets()

STRUCTURE = {
    "NIFTY 50": ["BHARTIARTL","LT","RELIANCE"],
    "NIFTY BANK": ["ICICIBANK","SBIN","AXISBANK","KOTAKBANK","AUBANK","INDUSINDBK","HDFCBANK"],
    "Nifty Mid select": ["MCX","CDSL","BSE","PAYTM","INDIGO","POLYCAB","INDUSTOWER","ABB","TRENT","DIXON","ASIANPAINT"],
    "NIFTY FIN SERVICE": ["CHOLAFIN","BAJFINANCE","BAJAJFINSV","HDFCLIFE","SBILIFE","MFSL","SHRIRAMFIN"],
    "NIFTY IT": ["INFY","TCS","HCLTECH","WIPRO","TECHM","LTIM","PERSISTENT","OFSS","COFORGE"],
    "NIFTY AUTO": ["MARUTI","TATAMOTORS","M&M","BAJAJ-AUTO","EICHERMOT","TIINDIA","HEROMOTOCO"],
    "NIFTY DEFENCE": ["HAL","BEL","GRSE","COCHINSHIP","MAZDOCK","BDL","DATAPATTNS"],
    "NIFTY METAL": ["JSWSTEEL","HINDALCO","VEDL","JINDALSTEL","NATIONALUM","TITAN","HINDZINC","HINDCOPPER"],
    "NIFTY FMCG": ["HINDUNILVR","NESTLEIND","BRITANNIA","TATACONSUM","VBL","GODREJCP","COLPAL"],
    "NIFTY ENERGY": ["POWERGRID","COALINDIA","CGPOWER","ADANIGREEN","JSWENERGY"],
    "NIFTY PHARMA": ["DRREDDY","TORNTPHARM","LUPIN","SUNPHARMA","CIPLA","DIVISLAB","GLENMARK","ZYDU","LAURUSLABS"],
    "MOST LIQUID STOCKS": ["MARUTI","TRENT","POLYCAB","DIXON","BAJAJ-AUTO","PERSISTENT","BSE","INDIGO","BOSCHLTD","OFSS","ABB","SOLARINDS"]
}

print("Downloading instrument list...")
for attempt in range(5):
    try:
        r = requests.get("https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz", timeout=30)
        with gzip.GzipFile(fileobj=io.BytesIO(r.content)) as gz:
            df = pd.read_csv(gz)
        print(f"✅ Instrument list downloaded: {len(df)} rows")
        break
    except Exception as e:
        print(f"Instrument download fail {attempt+1}: {e} - retry 10 sec")
        time.sleep(10)
else:
    print("❌ Failed to download instruments after 5 attempts - exiting loop will retry via main.py")
    df = pd.DataFrame()

mp={}
for _, row in df.iterrows():
    sym=str(row.get("tradingsymbol","")).strip(); key=str(row.get("instrument_key","")).strip()
    if not sym or not key: continue
    if "NSE_EQ" in key:
        mp[sym]=key
        if sym.endswith("-EQ"): mp[sym.replace("-EQ","")]=key
    elif sym not in mp:
        mp[sym]=key

mp["NIFTY 50"]="NSE_INDEX|Nifty 50"; mp["NIFTY BANK"]="NSE_INDEX|Nifty Bank"
mp["NIFTY DEFENCE"]="NSE_INDEX|Nifty Ind Defence"; mp["Nifty Mid select"]="NSE_INDEX|NIFTY MID SELECT"
mp["NIFTY FIN SERVICE"]="NSE_INDEX|Nifty Fin Service"; mp["NIFTY IT"]="NSE_INDEX|Nifty IT"
mp["NIFTY AUTO"]="NSE_INDEX|Nifty Auto"; mp["NIFTY METAL"]="NSE_INDEX|Nifty Metal"
mp["NIFTY FMCG"]="NSE_INDEX|Nifty FMCG"; mp["NIFTY ENERGY"]="NSE_INDEX|Nifty Energy"
mp["NIFTY PHARMA"]="NSE_INDEX|Nifty Pharma"
mp["CHOLAFIN"]="NSE_EQ|INE121A01024"; mp["DATAPATTNS"]="NSE_EQ|INE0IX101010"
mp["BDL"]="NSE_EQ|INE171Z01026"; mp["PAYTM"]="NSE_EQ|INE982J01020"
mp["CDSL"]="NSE_EQ|INE736A01011"; mp["ADANIGREEN"]="NSE_EQ|INE364U01010"
mp["JSWENERGY"]="NSE_EQ|INE121E01018"; mp["SHRIRAMFIN"]="NSE_EQ|INE721A01047"
mp["M&M"]="NSE_EQ|INE101A01026"; mp["BOSCHLTD"]="NSE_EQ|INE323A01026"
mp["SOLARINDS"]="NSE_EQ|INE343H01029"; mp["MARUTI"]="NSE_EQ|INE585B01010"
# Explicit mapping for new metal stocks (fallback if CSV miss)
mp["HINDZINC"]="NSE_EQ|INE267A01025"
mp["HINDCOPPER"]="NSE_EQ|INE531E01026"

mcx_found = False
for _, row in df.iterrows():
    tsym = str(row.get("tradingsymbol","")).strip()
    ikey = str(row.get("instrument_key","")).strip()
    if tsym in ["MCX", "MCX-EQ"] and "NSE_EQ" in ikey:
        mp["MCX"] = ikey; mcx_found = True; print(f"MCX Key Found: {ikey}"); break
if not mcx_found: mp["MCX"] = "NSE_EQ|11536"

if "ZYDUSLIFE" in mp: mp["ZYDU"]=mp["ZYDUSLIFE"]
for _, row in df.iterrows():
    if str(row.get("tradingsymbol","")).strip()=="LAURUSLABS" and "NSE_EQ" in str(row.get("instrument_key","")):
        mp["LAURUSLABS"]=str(row.get("instrument_key","")).strip(); break

instrument_data={}; all_keys=[]
for sec, stocks in STRUCTURE.items():
    if sec!= "MOST LIQUID STOCKS":
        ikey=mp.get(sec)
        if ikey and ikey not in instrument_data:
            instrument_data[ikey]={"symbol":sec,"pdh":0,"pdl":0,"wh":0,"wl":0,"ltp":0,"prev_close":0,"vol":0,"prev_vol":0,"is_index":True,"change":0,"break_time":""}
            all_keys.append(ikey)
    for sym in stocks:
        k=mp.get(sym)
        if k and k not in instrument_data:
            instrument_data[k]={"symbol":sym,"pdh":0,"pdl":0,"wh":0,"wl":0,"ltp":0,"prev_close":0,"vol":0,"prev_vol":0,"is_index":False,"change":0,"break_time":""}
            all_keys.append(k)

print(f"Total Instruments: {len(all_keys)} - Includes HINDZINC, HINDCOPPER")

def get_candle(k, fro, to):
    ek=urllib.parse.quote(k, safe=''); url=f"https://api.upstox.com/v3/historical-candle/{ek}/days/1/{to}/{fro}"
    try:
        resp=requests.get(url, headers={"Authorization": f"Bearer {UPSTOX_ACCESS_TOKEN}"}, timeout=10)
        if resp.status_code==200: return k, resp.json().get("data",{}).get("candles",[])
    except: pass
    return k, []

def get_status(it):
    if it["ltp"]>0 and it["wh"]>0 and it["ltp"]>it["wh"]: return "BREAKOUT"
    if it["ltp"]>0 and it["wl"]>0 and it["ltp"]<it["wl"]: return "BREAKDOWN"
    return ""

print("Fetching weekly high/low...")
with ThreadPoolExecutor(max_workers=10) as ex:
    for k,candles in ex.map(lambda kk: get_candle(kk, weekly_from, weekly_to), all_keys):
        if candles:
            d=pd.DataFrame(candles,columns=["datetime","open","high","low","close","volume","oi"])
            instrument_data[k]["wh"]=float(d["high"].max()); instrument_data[k]["wl"]=float(d["low"].min())
            instrument_data[k]["prev_close"]=float(d.iloc[-1]["close"])

pd_day=None
for i in range(1,8):
    d=(datetime.now(IST)-timedelta(days=i)).strftime("%Y-%m-%d")
    if datetime.strptime(d,"%Y-%m-%d").weekday()<5: pd_day=d; break
print(f"PD Day: {pd_day}")
with ThreadPoolExecutor(max_workers=10) as ex:
    for k,candles in ex.map(lambda kk: get_candle(kk, pd_day, pd_day), all_keys):
        if candles:
            instrument_data[k]["pdh"]=float(candles[0][2]); instrument_data[k]["pdl"]=float(candles[0][3])
            instrument_data[k]["prev_vol"]=int(candles[0][5])
            if instrument_data[k]["prev_close"]==0: instrument_data[k]["prev_close"]=float(candles[0][4])

def fetch_ltp(keys):
    qs = "&".join([f"instrument_key={urllib.parse.quote(k)}" for k in keys])
    url = f"https://api.upstox.com/v3/market-quote/ltp?{qs}"
    try:
        resp = requests.get(url, headers={"Authorization": f"Bearer {UPSTOX_ACCESS_TOKEN}"}, timeout=15)
        if resp.status_code==200:
            js = resp.json()
            for k in keys:
                if k in js.get("data",{}):
                    lp = js["data"][k].get("last_price")
                    if lp: instrument_data[k]["ltp"]=float(lp)
    except Exception as e:
        print(f"LTP ERR {e}")

print("Fetching LTP...")
for i in range(0, len(all_keys), 20): fetch_ltp(all_keys[i:i+20])
today = datetime.now(IST).strftime("%Y-%m-%d")
remaining = [k for k,v in instrument_data.items() if v["ltp"]==0]
if remaining:
    print(f"Fetching remaining {len(remaining)} from candle...")
    with ThreadPoolExecutor(max_workers=10) as ex:
        for k,candles in ex.map(lambda kk: get_candle(kk, today, today), remaining):
            if candles:
                instrument_data[k]["ltp"]=float(candles[0][4])
                instrument_data[k]["vol"]=int(candles[0][5])

for k,v in instrument_data.items():
    if v["ltp"]==0 and v["prev_close"]>0: v["ltp"]=v["prev_close"]
    if v["wh"]==0: v["wh"]=v["ltp"]
    if v["wl"]==0: v["wl"]=v["ltp"]
    if v["pdh"]==0: v["pdh"]=v["ltp"]
    if v["pdl"]==0: v["pdl"]=v["ltp"]
    v["change"]=(v["ltp"]-v["prev_close"])/v["prev_close"]*100 if v["prev_close"]>0 else 0

row_map={}
def build_sorted():
    global row_map
    row_map.clear()
    rows=[]; rnum=5
    rows.append(["Symbol","PD High","PD Low","WEEK HIGH","WEEK LOW","LTP","CHANGE %","VOLUME","PREV VOL","VOL X","DIST%","STATUS","BREAK TIME","LIVE TIME"])
    indices=[it for it in instrument_data.values() if it.get("is_index")]
    indices.sort(key=lambda x: x["change"], reverse=True)
    for it in indices:
        dist=it["ltp"]/it["wh"]*100 if it["wh"]>0 else 0; volx=it["vol"]/it["prev_vol"] if it["prev_vol"]>0 else 0
        status=get_status(it)
        if status and not it["break_time"]: it["break_time"]=datetime.now(IST).strftime("%H:%M:%S")
        rows.append([it["symbol"],it["pdh"],it["pdl"],it["wh"],it["wl"],it["ltp"],f"{it['change']:.2f}%",it["vol"],it["prev_vol"],f"{volx:.1f}X",f"{dist:.1f}%",status,it["break_time"],datetime.now(IST).strftime("%H:%M:%S")])
        # row_map as list for duplicate symbols (e.g. MARUTI in AUTO and MOST LIQUID)
        if it["symbol"] not in row_map:
            row_map[it["symbol"]] = []
        row_map[it["symbol"]].append(rnum)
        rnum+=1
    rows.append([]); rnum+=1
    for sec_name in STRUCTURE.keys():
        rows.append([sec_name]); rnum+=1
        stocks=[instrument_data[mp.get(sym)] for sym in STRUCTURE[sec_name] if mp.get(sym) in instrument_data]
        stocks.sort(key=lambda x: x["change"], reverse=True)
        for it in stocks:
            dist=it["ltp"]/it["wh"]*100 if it["wh"]>0 else 0; volx=it["vol"]/it["prev_vol"] if it["prev_vol"]>0 else 0
            status=get_status(it)
            if status and not it["break_time"]: it["break_time"]=datetime.now(IST).strftime("%H:%M:%S")
            rows.append([it["symbol"],it["pdh"],it["pdl"],it["wh"],it["wl"],it["ltp"],f"{it['change']:.2f}%",it["vol"],it["prev_vol"],f"{volx:.1f}X",f"{dist:.1f}%",status,it["break_time"],datetime.now(IST).strftime("%H:%M:%S")])
            if it["symbol"] not in row_map:
                row_map[it["symbol"]] = []
            row_map[it["symbol"]].append(rnum)
            rnum+=1
        rows.append([]); rnum+=1
    return rows

def build_breakout_sheet():
    rows=[]
    rows.append(["Symbol","PD High","PD Low","WEEK HIGH","WEEK LOW","LTP","CHANGE %","VOLUME","PREV VOL","VOL X","DIST%","STATUS","BREAK TIME","LIVE TIME","PARENT INDEX"])
    has_data=False
    for sec_name in STRUCTURE.keys():
        if sec_name == "MOST LIQUID STOCKS": continue
        ikey=mp.get(sec_name)
        if not ikey or ikey not in instrument_data: continue
        index_it=instrument_data[ikey]
        index_status=get_status(index_it)
        if index_status in ["BREAKOUT","BREAKDOWN"]:
            has_data=True
            dist= index_it["ltp"]/index_it["wh"]*100 if index_it["wh"]>0 else 0
            volx= index_it["vol"]/index_it["prev_vol"] if index_it["prev_vol"]>0 else 0
            rows.append([index_it["symbol"],index_it["pdh"],index_it["pdl"],index_it["wh"],index_it["wl"],index_it["ltp"],f"{index_it['change']:.2f}%",index_it["vol"],index_it["prev_vol"],f"{volx:.1f}X",f"{dist:.1f}%",index_status,index_it["break_time"],datetime.now(IST).strftime("%H:%M:%S"),"INDEX"])
            rows.append([f"--- {sec_name} MADHLE {index_status} STOCKS ---"])
            stock_list=[]
            for sym in STRUCTURE[sec_name]:
                k=mp.get(sym)
                if k in instrument_data:
                    it=instrument_data[k]
                    st=get_status(it)
                    if st==index_status:
                        stock_list.append(it)
            stock_list.sort(key=lambda x: x["change"], reverse=True)
            if stock_list:
                for it in stock_list:
                    dist=it["ltp"]/it["wh"]*100 if it["wh"]>0 else 0; volx=it["vol"]/it["prev_vol"] if it["prev_vol"]>0 else 0
                    rows.append([it["symbol"],it["pdh"],it["pdl"],it["wh"],it["wl"],it["ltp"],f"{it['change']:.2f}%",it["vol"],it["prev_vol"],f"{volx:.1f}X",f"{dist:.1f}%",get_status(it),it["break_time"],datetime.now(IST).strftime("%H:%M:%S"),sec_name])
            else:
                rows.append([f"INDEX {index_status} AAHE PAN STOCK EK PANI NAHI"])
            rows.append([])
    if not has_data:
        rows.append([])
        rows.append(["SADHYA KUTLACH INDEX BREAKOUT/BREAKDOWN NAHI"])
    return rows

def setup_permanent_colors():
    try:
        sh.batch_update({"requests": [{"deleteConditionalFormatRule": {"index": 0, "sheetId": sheet.id}} for _ in range(15)]})
    except: pass
    try:
        sh.batch_update({"requests": [{"deleteConditionalFormatRule": {"index": 0, "sheetId": breakout_sheet.id}} for _ in range(15)]})
    except: pass
    try:
        requests_main = [
            {"addConditionalFormatRule": {"rule": {"ranges": [{"sheetId": sheet.id, "startRowIndex": 4, "startColumnIndex": 11, "endColumnIndex": 12}], "booleanRule": {"condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": "BREAKOUT"}]}, "format": {"backgroundColor": {"red": 0.0, "green": 0.8, "blue": 0.0}, "textFormat": {"bold": True, "foregroundColor": {"red":1,"green":1,"blue":1}}}}}, "index": 0}},
            {"addConditionalFormatRule": {"rule": {"ranges": [{"sheetId": sheet.id, "startRowIndex": 4, "startColumnIndex": 11, "endColumnIndex": 12}], "booleanRule": {"condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": "BREAKDOWN"}]}, "format": {"backgroundColor": {"red": 1.0, "green": 0.2, "blue": 0.2}, "textFormat": {"bold": True, "foregroundColor": {"red":1,"green":1,"blue":1}}}}}, "index": 1}},
            {"addConditionalFormatRule": {"rule": {"ranges": [{"sheetId": sheet.id, "startRowIndex": 4, "startColumnIndex": 9, "endColumnIndex": 10}], "booleanRule": {"condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": "=VALUE(SUBSTITUTE(INDIRECT(\"R\"&ROW()&\"C\"&COLUMN(),FALSE),\"X\",\"\"))>=2"}]}, "format": {"backgroundColor": {"red": 0.0, "green": 0.6, "blue": 0.0}, "textFormat": {"bold": True, "foregroundColor": {"red":1,"green":1,"blue":1}}}}}, "index": 2}},
            {"addConditionalFormatRule": {"rule": {"ranges": [{"sheetId": sheet.id, "startRowIndex": 4, "startColumnIndex": 9, "endColumnIndex": 10}], "booleanRule": {"condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": "=VALUE(SUBSTITUTE(INDIRECT(\"R\"&ROW()&\"C\"&COLUMN(),FALSE),\"X\",\"\"))>=1"}]}, "format": {"backgroundColor": {"red": 0.7, "green": 1.0, "blue": 0.7}, "textFormat": {"bold": True}}}}, "index": 3}},
        ]
        sh.batch_update({"requests": requests_main})
        requests_break = [
            {"addConditionalFormatRule": {"rule": {"ranges": [{"sheetId": breakout_sheet.id, "startRowIndex": 0, "startColumnIndex": 11, "endColumnIndex": 12}], "booleanRule": {"condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": "BREAKOUT"}]}, "format": {"backgroundColor": {"red": 0.0, "green": 0.8, "blue": 0.0}, "textFormat": {"bold": True}}}}, "index": 0}},
            {"addConditionalFormatRule": {"rule": {"ranges": [{"sheetId": breakout_sheet.id, "startRowIndex": 0, "startColumnIndex": 11, "endColumnIndex": 12}], "booleanRule": {"condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": "BREAKDOWN"}]}, "format": {"backgroundColor": {"red": 1.0, "green": 0.2, "blue": 0.2}, "textFormat": {"bold": True, "foregroundColor": {"red":1,"green":1,"blue":1}}}}}, "index": 1}},
            {"addConditionalFormatRule": {"rule": {"ranges": [{"sheetId": breakout_sheet.id, "startRowIndex": 0, "startColumnIndex": 9, "endColumnIndex": 10}], "booleanRule": {"condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": "=VALUE(SUBSTITUTE(INDIRECT(\"R\"&ROW()&\"C\"&COLUMN(),FALSE),\"X\",\"\"))>=2"}]}, "format": {"backgroundColor": {"red": 0.0, "green": 0.6, "blue": 0.0}, "textFormat": {"bold": True, "foregroundColor": {"red":1,"green":1,"blue":1}}}}}, "index": 2}},
            {"addConditionalFormatRule": {"rule": {"ranges": [{"sheetId": breakout_sheet.id, "startRowIndex": 0, "startColumnIndex": 9, "endColumnIndex": 10}], "booleanRule": {"condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": "=VALUE(SUBSTITUTE(INDIRECT(\"R\"&ROW()&\"C\"&COLUMN(),FALSE),\"X\",\"\"))>=1"}]}, "format": {"backgroundColor": {"red": 0.7, "green": 1.0, "blue": 0.7}}}}, "index": 3}},
        ]
        sh.batch_update({"requests": requests_break})
        print("COLOR RULES DONE")
    except Exception as e:
        print(f"Color rule err {e}")

def safe_sheet_update():
    for attempt in range(3):
        try:
            full = build_sorted()
            try:
                sheet.clear()
            except: pass
            sheet.update(values=full, range_name="A4")
            breakout_data = build_breakout_sheet()
            breakout_sheet.clear()
            breakout_sheet.update(values=breakout_data, range_name="A1")
            setup_permanent_colors()
            print(f"DONE {len(row_map)} rows - Includes HINDZINC, HINDCOPPER")
            return True
        except Exception as e:
            print(f"Sheet update fail {attempt+1}: {e} - retry 10 sec")
            time.sleep(10)
            try:
                connect_sheets()
            except: pass
    return False

safe_sheet_update()

def start_streamer_with_reconnect():
    while True:
        if not is_market_open():
            print(f"[{datetime.now(IST).strftime('%H:%M:%S IST')}] {get_market_status_msg()} - Upstock4 Streamer Sleep 60 sec...")
            time.sleep(60)
            continue
        try:
            print(f"[{datetime.now(IST).strftime('%H:%M:%S')}] Starting Upstox Streamer - MARKET HOURS 9:00-15:30... V29 Metal 8 stocks")
            global UPSTOX_ACCESS_TOKEN
            UPSTOX_ACCESS_TOKEN = os.environ.get("UPSTOX_ACCESS_TOKEN", "")
            if not UPSTOX_ACCESS_TOKEN and os.path.exists("upstox_token.txt"):
                try:
                    with open("upstox_token.txt","r") as f:
                        UPSTOX_ACCESS_TOKEN = f.read().strip()
                except: pass
            if not UPSTOX_ACCESS_TOKEN:
                print("❌ No token - waiting 60 sec...")
                time.sleep(60)
                continue
            configuration = upstox_client.Configuration()
            configuration.access_token = UPSTOX_ACCESS_TOKEN
            api_client = upstox_client.ApiClient(configuration)
            streamer = upstox_client.MarketDataStreamerV3(api_client=api_client, instrumentKeys=all_keys, mode="full")
            pending_updates={}; lock=threading.Lock()
            last_sorted_keys=""
            def on_message(message):
                feeds=message.get("feeds",{})
                for ikey, feed in feeds.items():
                    if ikey not in instrument_data: continue
                    ltp = None; vol = None
                    try:
                        if "fullFeed" in feed:
                            ff = feed["fullFeed"]
                            if "marketFF" in ff:
                                mff = ff["marketFF"]
                                ltp = mff.get("ltpc",{}).get("ltp")
                                vol = mff.get("vtt")
                                if not vol:
                                    ohlc_list = mff.get("marketOHLC",{}).get("ohlc",[])
                                    if ohlc_list: vol = ohlc_list[0].get("volume")
                                if not vol: vol = mff.get("volume")
                            elif "indexFF" in ff:
                                ltp = ff["indexFF"].get("ltpc",{}).get("ltp"); vol = 0
                        if not ltp and "ltpc" in feed: ltp = feed["ltpc"].get("ltp")
                        with lock:
                            if ltp:
                                prev_status = get_status(instrument_data[ikey])
                                instrument_data[ikey]["ltp"]=float(ltp)
                                if instrument_data[ikey]["prev_close"]>0:
                                    instrument_data[ikey]["change"]=(float(ltp)-instrument_data[ikey]["prev_close"])/instrument_data[ikey]["prev_close"]*100
                                pending_updates[ikey]=float(ltp)
                                new_status=get_status(instrument_data[ikey])
                                if new_status and new_status != prev_status and not instrument_data[ikey]["break_time"]:
                                    instrument_data[ikey]["break_time"]=datetime.now(IST).strftime("%H:%M:%S")
                                if new_status in ["BREAKOUT","BREAKDOWN"] and prev_status != new_status:
                                    sym = instrument_data[ikey]["symbol"]
                                    last_alert = alerted_symbols.get(sym)
                                    if last_alert != new_status:
                                        volx = instrument_data[ikey]["vol"]/instrument_data[ikey]["prev_vol"] if instrument_data[ikey]["prev_vol"]>0 else 0
                                        dist = instrument_data[ikey]["ltp"]/instrument_data[ikey]["wh"]*100 if instrument_data[ikey]["wh"]>0 else 0
                                        emoji = "🚀" if new_status=="BREAKOUT" else "🔻"
                                        chg_val = instrument_data[ikey]['change']
                                        msg = (
                                            f"{emoji} <b>{new_status} ALERT - {sym}</b> {emoji}\n\n"
                                            f"💰 LTP: {instrument_data[ikey]['ltp']:.2f}\n"
                                            f"📈 Change: {chg_val:.2f}%\n"
                                            f"📊 WH: {instrument_data[ikey]['wh']:.2f} | WL: {instrument_data[ikey]['wl']:.2f}\n"
                                            f"📦 Vol X: {volx:.1f}X | Dist: {dist:.1f}%\n"
                                            f"⏰ Time: {instrument_data[ikey]['break_time']} IST\n"
                                            f"🔍 Type: {'INDEX' if instrument_data[ikey]['is_index'] else 'STOCK'}"
                                        )
                                        threading.Thread(target=send_telegram_alert, args=(msg,), daemon=True).start()
                                        alerted_symbols[sym] = new_status
                            if vol is not None:
                                try:
                                    v_int = int(float(vol))
                                    if v_int >=0 and not instrument_data[ikey]["is_index"]:
                                        instrument_data[ikey]["vol"] = v_int
                                except: pass
                    except: pass
            def on_open():
                print("✅ LIVE CONNECTED - V29 WITH TELEGRAM - HINDZINC + HINDCOPPER Added")
                send_telegram_alert("✅ <b>Ravi Algo LIVE CONNECTED - V29 - Metal + HINDZINC + HINDCOPPER</b>\nMarket screener chalu!")
            streamer.on("open", on_open)
            streamer.on("message", on_message)
            streamer.connect()
            def sheet_updater():
                nonlocal last_sorted_keys
                last_sort=time.time()
                while True:
                    time.sleep(1)
                    if time.time()-last_sort>=5:
                        with lock:
                            for ikey, ltp in list(pending_updates.items()):
                                instrument_data[ikey]["change"]=(ltp-instrument_data[ikey]["prev_close"])/instrument_data[ikey]["prev_close"]*100 if instrument_data[ikey]["prev_close"]>0 else 0
                            pending_updates.clear()
                            current_order="".join([f"{k}{v['change']:.2f}" for k,v in sorted(instrument_data.items(), key=lambda x: x[1]["change"], reverse=True)][:10])
                            if current_order!=last_sorted_keys:
                                print("RANK CHANGE - RE-SORTING...")
                                last_sorted_keys=current_order
                                full_sorted=build_sorted()
                                breakout_sorted=build_breakout_sheet()
                                try:
                                    try:
                                        sheet.clear()
                                    except: pass
                                    sheet.update(values=full_sorted, range_name="A4")
                                    breakout_sheet.clear()
                                    breakout_sheet.update(values=breakout_sorted, range_name="A1")
                                except Exception as e: 
                                    print(f"Sort err {e} - reconnecting sheets")
                                    try: connect_sheets()
                                    except: pass
                            else:
                                batch=[]
                                for ikey, it in instrument_data.items():
                                    sym=it["symbol"]
                                    if sym in row_map:
                                        rnums = row_map[sym] if isinstance(row_map[sym], list) else [row_map[sym]]
                                        for rnum in rnums:
                                            dist=it["ltp"]/it["wh"]*100 if it["wh"]>0 else 0
                                            volx=it["vol"]/it["prev_vol"] if it["prev_vol"]>0 else 0
                                            status=get_status(it)
                                            batch.append({"range": f"F{rnum}:N{rnum}", "values": [[it["ltp"], f"{it['change']:.2f}%", it["vol"], it["prev_vol"], f"{volx:.1f}X", f"{dist:.1f}%", status, it["break_time"], datetime.now(IST).strftime("%H:%M:%S")]]})
                                if batch:
                                    try:
                                        sheet.batch_update(batch)
                                        breakout_sorted=build_breakout_sheet()
                                        breakout_sheet.clear()
                                        breakout_sheet.update(values=breakout_sorted, range_name="A1")
                                    except Exception as e:
                                        print(f"Batch update err {e} - reconnecting")
                                        try: connect_sheets()
                                        except: pass
                        last_sort=time.time()
            threading.Thread(target=sheet_updater,daemon=True).start()
            while True:
                time.sleep(10)
        except Exception as e:
            import traceback
            print(f"❌ Streamer crashed: {e} - Reconnecting in 15 sec...\n{traceback.format_exc()[:500]}")
            send_telegram_alert(f"⚠ Streamer crash: {str(e)[:100]} - Reconnecting...")
            time.sleep(15)

start_streamer_with_reconnect()
