import gzip, io, time, threading, os, re, gc
from datetime import datetime, timedelta
import pytz
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd
import requests, urllib.parse
from concurrent.futures import ThreadPoolExecutor
import upstox_client

IST = pytz.timezone('Asia/Kolkata')
print("FINAL V36 - RENDER MEMORY LITE + PDH + LTP LIVE FIX")

def is_market_open():
    now = datetime.now(IST)
    if now.weekday() >= 5: return False
    s = now.replace(hour=9, minute=0, second=0, microsecond=0)
    e = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return s <= now <= e

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
def send_telegram_alert(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID: return
    try: requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
    except: pass

SPREADSHEET_NAME = "Dsheet"
SERVICE_ACCOUNT_FILE = os.path.join(os.path.dirname(__file__), "service_account.json")

def get_gspread_client():
    scope = ["https://spreadsheets.google.com/feeds","https://www.googleapis.com/auth/drive"]
    paths = [SERVICE_ACCOUNT_FILE, "./service_account.json", "/etc/secrets/service_account.json", "service_account.json", os.path.join(os.getcwd(), "service_account.json")]
    for p in paths:
        if os.path.exists(p):
            try: return gspread.authorize(ServiceAccountCredentials.from_json_keyfile_name(p, scope))
            except: pass
    env_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "") or os.environ.get("GOOGLE_CREDENTIALS", "") or os.environ.get("SERVICE_ACCOUNT_JSON", "")
    if env_json:
        import json as js
        return gspread.authorize(ServiceAccountCredentials.from_json_keyfile_dict(js.loads(env_json), scope))
    raise Exception("service_account.json missing")

def parse_date(val):
    if not val: return ""
    s = str(val).strip().split()[0].replace("/", "-").replace(".", "-")
    s = re.sub(r'[^0-9\-]', '', s)
    for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
        try: return datetime.strptime(s[:10], fmt).strftime("%Y-%m-%d")
        except: pass
    return s[:10]

def is_token_valid(token):
    if not token or len(token) < 50: return False
    try:
        r = requests.get("https://api.upstox.com/v2/user/profile", headers={"Authorization": f"Bearer {token}"}, timeout=10)
        return r.status_code == 200
    except: return False

def get_automatic_token():
    def ok(t): return t and len(t) > 100 and "eyJ" in str(t)
    if os.path.exists("upstox_token.txt"):
        try:
            with open("upstox_token.txt","r") as f: tok=f.read().strip()
            if ok(tok) and is_token_valid(tok):
                os.environ["UPSTOX_ACCESS_TOKEN"]=tok; return tok
        except: pass
    try:
        gc_temp = get_gspread_client()
        sh = gc_temp.open(SPREADSHEET_NAME)
        b1 = str(sh.sheet1.cell(1,2).value or "").strip()
        if ok(b1) and is_token_valid(b1):
            with open("upstox_token.txt","w") as f: f.write(b1)
            os.environ["UPSTOX_ACCESS_TOKEN"]=b1; return b1
    except: pass
    tok = os.environ.get("UPSTOX_ACCESS_TOKEN","") or os.environ.get("UPSTOX_TOKEN","")
    if ok(tok) and is_token_valid(tok): return tok
    return None

def wait_for_valid_token():
    global UPSTOX_ACCESS_TOKEN
    while True:
        tok=get_automatic_token()
        if tok and is_token_valid(tok):
            UPSTOX_ACCESS_TOKEN=tok; return tok
        time.sleep(60)

UPSTOX_ACCESS_TOKEN = get_automatic_token()
if not UPSTOX_ACCESS_TOKEN or not is_token_valid(UPSTOX_ACCESS_TOKEN):
    UPSTOX_ACCESS_TOKEN = wait_for_valid_token()

gc=sh=sheet=breakout_sheet=None
weekly_from=weekly_to=""; alerted_symbols={}

def connect_sheets():
    global gc, sh, sheet, breakout_sheet, weekly_from, weekly_to
    while True:
        try:
            gc = get_gspread_client()
            sh = gc.open(SPREADSHEET_NAME)
            sheet = sh.sheet1
            try: breakout_sheet = sh.worksheet("BREAKOUT")
            except: breakout_sheet = sh.add_worksheet(title="BREAKOUT", rows="2000", cols="20")
            weekly_from = parse_date(sheet.cell(2,2).value)
            weekly_to = parse_date(sheet.cell(3,2).value)
            print(f"✅ Sheets {weekly_from} to {weekly_to}"); return True
        except Exception as e:
            print(f"Sheet err {e}"); time.sleep(30)

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
df=None
for _ in range(3):
    try:
        r=requests.get("https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz", timeout=30)
        with gzip.GzipFile(fileobj=io.BytesIO(r.content)) as gz:
            df=pd.read_csv(gz, usecols=["tradingsymbol","instrument_key"])
        break
    except: time.sleep(10)

mp={}
if df is not None:
    for _, row in df.iterrows():
        sym=str(row.get("tradingsymbol","")).strip(); key=str(row.get("instrument_key","")).strip()
        if not sym or not key: continue
        if "NSE_EQ" in key:
            mp[sym]=key
            if sym.endswith("-EQ"): mp[sym.replace("-EQ","")]=key
        elif sym not in mp: mp[sym]=key
    del df; gc.collect()

mp["NIFTY 50"]="NSE_INDEX|Nifty 50"; mp["NIFTY BANK"]="NSE_INDEX|Nifty Bank"
mp["NIFTY DEFENCE"]="NSE_INDEX|Nifty Ind Defence"; mp["Nifty Mid select"]="NSE_INDEX|NIFTY MID SELECT"
mp["NIFTY FIN SERVICE"]="NSE_INDEX|Nifty Fin Service"; mp["NIFTY IT"]="NSE_INDEX|Nifty IT"
mp["NIFTY AUTO"]="NSE_INDEX|Nifty Auto"; mp["NIFTY METAL"]="NSE_INDEX|Nifty Metal"
mp["NIFTY FMCG"]="NSE_INDEX|Nifty FMCG"; mp["NIFTY ENERGY"]="NSE_INDEX|Nifty Energy"
mp["NIFTY PHARMA"]="NSE_INDEX|Nifty Pharma"
mp["CHOLAFIN"]="NSE_EQ|INE121A01024"; mp["DATAPATTNS"]="NSE_EQ|INE0IX101010"; mp["BDL"]="NSE_EQ|INE171Z01026"
mp["PAYTM"]="NSE_EQ|INE982J01020"; mp["CDSL"]="NSE_EQ|INE736A01011"; mp["ADANIGREEN"]="NSE_EQ|INE364U01010"
mp["JSWENERGY"]="NSE_EQ|INE121E01018"; mp["SHRIRAMFIN"]="NSE_EQ|INE721A01047"; mp["M&M"]="NSE_EQ|INE101A01026"
mp["BOSCHLTD"]="NSE_EQ|INE323A01026"; mp["SOLARINDS"]="NSE_EQ|INE343H01029"; mp["MARUTI"]="NSE_EQ|INE585B01010"
mp["HINDZINC"]="NSE_EQ|INE267A01025"; mp["HINDCOPPER"]="NSE_EQ|INE531E01026"
mp["MCX"]="NSE_EQ|11536"
if "ZYDUSLIFE" in mp: mp["ZYDU"]=mp["ZYDUSLIFE"]

instrument_data={}; all_keys=[]
for sec, stocks in STRUCTURE.items():
    if sec!="MOST LIQUID STOCKS":
        ikey=mp.get(sec)
        if ikey and ikey not in instrument_data:
            instrument_data[ikey]={"symbol":sec,"pdh":0,"pdl":0,"wh":0,"wl":0,"ltp":0,"prev_close":0,"vol":0,"prev_vol":0,"is_index":True,"change":0,"break_time":""}
            all_keys.append(ikey)
    for sym in stocks:
        k=mp.get(sym)
        if k and k not in instrument_data:
            instrument_data[k]={"symbol":sym,"pdh":0,"pdl":0,"wh":0,"wl":0,"ltp":0,"prev_close":0,"vol":0,"prev_vol":0,"is_index":False,"change":0,"break_time":""}
            all_keys.append(k)

def get_candle_fixed(k, from_date, to_date):
    ek=urllib.parse.quote(k, safe='')
    url=f"https://api.upstox.com/v3/historical-candle/{ek}/days/1/{to_date}/{from_date}"
    for _ in range(2):
        try:
            resp=requests.get(url, headers={"Authorization": f"Bearer {UPSTOX_ACCESS_TOKEN}"}, timeout=10)
            if resp.status_code==200:
                c=resp.json().get("data",{}).get("candles",[])
                if c: return k,c
        except: time.sleep(1)
    return k,[]

def get_status(it):
    if it["ltp"]>0 and it["wh"]>0 and it["ltp"]>it["wh"]: return "BREAKOUT"
    if it["ltp"]>0 and it["wl"]>0 and it["ltp"]<it["wl"]: return "BREAKDOWN"
    return ""

print(f"Fetching weekly {weekly_from} to {weekly_to}")
with ThreadPoolExecutor(max_workers=5) as ex:
    for k,candles in ex.map(lambda kk: get_candle_fixed(kk, weekly_from, weekly_to), all_keys):
        if candles:
            try:
                instrument_data[k]["wh"]=max(float(x[2]) for x in candles)
                instrument_data[k]["wl"]=min(float(x[3]) for x in candles)
            except: pass
gc.collect()

def find_actual_last_trading_day():
    print("🔍 Finding last trading day...")
    test_key=mp.get("NIFTY 50")
    for i in range(1,15):
        d=datetime.now(IST)-timedelta(days=i)
        if d.weekday()>=5: continue
        d_str=d.strftime("%Y-%m-%d")
        _,c=get_candle_fixed(test_key, d_str, d_str)
        if c and len(c[0])>=6:
            h=float(c[0][2]); l=float(c[0][3])
            if h>0 and l>0 and h!=l:
                print(f" ✅ LAST DAY {d_str}"); return d_str
    return (datetime.now(IST)-timedelta(days=1)).strftime("%Y-%m-%d")

pd_day=find_actual_last_trading_day()
print(f"Fetching PD {pd_day}...")
with ThreadPoolExecutor(max_workers=5) as ex:
    results=list(ex.map(lambda kk: get_candle_fixed(kk, pd_day, pd_day), all_keys))
for k,candles in results:
    if candles:
        try:
            instrument_data[k]["pdh"]=float(candles[0][2]); instrument_data[k]["pdl"]=float(candles[0][3])
            instrument_data[k]["prev_close"]=float(candles[0][4]); instrument_data[k]["prev_vol"]=int(float(candles[0][5]))
        except: pass
del results; gc.collect()
print(f"✅ PD LOCKED {pd_day}")

def fetch_ltp(keys):
    qs="&".join([f"instrument_key={urllib.parse.quote(k)}" for k in keys])
    url=f"https://api.upstox.com/v3/market-quote/ltp?{qs}"
    try:
        resp=requests.get(url, headers={"Authorization": f"Bearer {UPSTOX_ACCESS_TOKEN}"}, timeout=15)
        if resp.status_code==200:
            js=resp.json()
            for k in keys:
                if k in js.get("data",{}):
                    lp=js["data"][k].get("last_price")
                    if lp: instrument_data[k]["ltp"]=float(lp)
    except: pass

for i in range(0,len(all_keys),20): fetch_ltp(all_keys[i:i+20])
today=datetime.now(IST).strftime("%Y-%m-%d")
remaining=[k for k,v in instrument_data.items() if v["ltp"]==0]
if remaining:
    with ThreadPoolExecutor(max_workers=5) as ex:
        for k,candles in ex.map(lambda kk: get_candle_fixed(kk, today, today), remaining):
            if candles:
                try: instrument_data[k]["ltp"]=float(candles[0][4]); instrument_data[k]["vol"]=int(float(candles[0][5]))
                except: pass

for v in instrument_data.values():
    if v["ltp"]==0 and v["prev_close"]>0: v["ltp"]=v["prev_close"]
    if v["wh"]==0: v["wh"]=v["ltp"]
    if v["wl"]==0: v["wl"]=v["ltp"]
    if v["pdh"]==0: v["pdh"]=v["ltp"]
    if v["pdl"]==0: v["pdl"]=v["ltp"]
    v["change"]=(v["ltp"]-v["prev_close"])/v["prev_close"]*100 if v["prev_close"]>0 else 0

row_map={}
def build_sorted():
    global row_map; row_map.clear(); rows=[]; rnum=5
    rows.append(["Symbol","PD High","PD Low","WEEK HIGH","WEEK LOW","LTP","CHANGE %","VOLUME","PREV VOL","VOL X","DIST%","STATUS","BREAK TIME","LIVE TIME"])
    indices=[it for it in instrument_data.values() if it.get("is_index")]; indices.sort(key=lambda x: x["change"], reverse=True)
    for it in indices:
        dist=it["ltp"]/it["wh"]*100 if it["wh"]>0 else 0; volx=it["vol"]/it["prev_vol"] if it["prev_vol"]>0 else 0
        st=get_status(it)
        if st and not it["break_time"]: it["break_time"]=datetime.now(IST).strftime("%H:%M:%S")
        rows.append([it["symbol"],it["pdh"],it["pdl"],it["wh"],it["wl"],it["ltp"],f"{it['change']:.2f}%",it["vol"],it["prev_vol"],f"{volx:.1f}X",f"{dist:.1f}%",st,it["break_time"],datetime.now(IST).strftime("%H:%M:%S")])
        row_map.setdefault(it["symbol"], []).append(rnum); rnum+=1
    rows.append([]); rnum+=1
    for sec_name in STRUCTURE.keys():
        rows.append([sec_name]); rnum+=1
        stocks=[instrument_data[mp.get(sym)] for sym in STRUCTURE[sec_name] if mp.get(sym) in instrument_data]
        stocks.sort(key=lambda x: x["change"], reverse=True)
        for it in stocks:
            dist=it["ltp"]/it["wh"]*100 if it["wh"]>0 else 0; volx=it["vol"]/it["prev_vol"] if it["prev_vol"]>0 else 0
            st=get_status(it)
            if st and not it["break_time"]: it["break_time"]=datetime.now(IST).strftime("%H:%M:%S")
            rows.append([it["symbol"],it["pdh"],it["pdl"],it["wh"],it["wl"],it["ltp"],f"{it['change']:.2f}%",it["vol"],it["prev_vol"],f"{volx:.1f}X",f"{dist:.1f}%",st,it["break_time"],datetime.now(IST).strftime("%H:%M:%S")])
            row_map.setdefault(it["symbol"], []).append(rnum); rnum+=1
        rows.append([]); rnum+=1
    return rows

def build_breakout_sheet():
    rows=[["Symbol","PD High","PD Low","WEEK HIGH","WEEK LOW","LTP","CHANGE %","VOLUME","PREV VOL","VOL X","DIST%","STATUS","BREAK TIME","LIVE TIME","PARENT INDEX"]]
    has=False
    for sec_name in STRUCTURE.keys():
        if sec_name=="MOST LIQUID STOCKS": continue
        ikey=mp.get(sec_name)
        if not ikey or ikey not in instrument_data: continue
        index_it=instrument_data[ikey]; index_status=get_status(index_it)
        if index_status in ["BREAKOUT","BREAKDOWN"]:
            has=True
            dist=index_it["ltp"]/index_it["wh"]*100 if index_it["wh"]>0 else 0; volx=index_it["vol"]/index_it["prev_vol"] if index_it["prev_vol"]>0 else 0
            rows.append([index_it["symbol"],index_it["pdh"],index_it["pdl"],index_it["wh"],index_it["wl"],index_it["ltp"],f"{index_it['change']:.2f}%",index_it["vol"],index_it["prev_vol"],f"{volx:.1f}X",f"{dist:.1f}%",index_status,index_it["break_time"],datetime.now(IST).strftime("%H:%M:%S"),"INDEX"])
            rows.append([f"--- {sec_name} MADHLE {index_status} STOCKS ---"])
            sl=[instrument_data[mp.get(sym)] for sym in STRUCTURE[sec_name] if mp.get(sym) in instrument_data and get_status(instrument_data[mp.get(sym)])==index_status]
            sl.sort(key=lambda x: x["change"], reverse=True)
            for it in sl:
                dist=it["ltp"]/it["wh"]*100 if it["wh"]>0 else 0; volx=it["vol"]/it["prev_vol"] if it["prev_vol"]>0 else 0
                rows.append([it["symbol"],it["pdh"],it["pdl"],it["wh"],it["wl"],it["ltp"],f"{it['change']:.2f}%",it["vol"],it["prev_vol"],f"{volx:.1f}X",f"{dist:.1f}%",get_status(it),it["break_time"],datetime.now(IST).strftime("%H:%M:%S"),sec_name])
            rows.append([])
    if not has: rows.append(["SADHYA KUTLACH INDEX BREAKOUT/BREAKDOWN NAHI"])
    return rows

def safe_sheet_update():
    for _ in range(3):
        try:
            full=build_sorted(); sheet.update(values=full, range_name="A4")
            bd=build_breakout_sheet(); breakout_sheet.clear(); breakout_sheet.update(values=bd, range_name="A1")
            print(f"DONE {pd_day}"); return True
        except Exception as e:
            print(f"Sheet fail {e}"); time.sleep(10)
            try: connect_sheets()
            except: pass
    return False

safe_sheet_update()

def start_streamer_with_reconnect():
    while True:
        if not is_market_open():
            print(f"[{datetime.now(IST).strftime('%H:%M:%S IST')}] Market Band - Sleep 60s"); time.sleep(60); continue
        try:
            print(f"[{datetime.now(IST).strftime('%H:%M:%S')}] Starting V36 Streamer...")
            global UPSTOX_ACCESS_TOKEN
            UPSTOX_ACCESS_TOKEN=os.environ.get("UPSTOX_ACCESS_TOKEN","")
            if not UPSTOX_ACCESS_TOKEN and os.path.exists("upstox_token.txt"):
                with open("upstox_token.txt","r") as f: UPSTOX_ACCESS_TOKEN=f.read().strip()
            configuration=upstox_client.Configuration(); configuration.access_token=UPSTOX_ACCESS_TOKEN
            api_client=upstox_client.ApiClient(configuration)
            streamer=upstox_client.MarketDataStreamerV3(api_client=api_client, instrumentKeys=all_keys, mode="full")
            pending={}; lock=threading.Lock(); last_keys=""
            def on_message(msg):
                feeds=msg.get("feeds",{})
                for ikey, feed in feeds.items():
                    if ikey not in instrument_data: continue
                    ltp=None; vol=None
                    try:
                        if "fullFeed" in feed:
                            ff=feed["fullFeed"]
                            if "marketFF" in ff:
                                mff=ff["marketFF"]; ltp=mff.get("ltpc",{}).get("ltp") or mff.get("ltp"); vol=mff.get("vtt")
                            elif "indexFF" in ff:
                                idx=ff["indexFF"]; ltp=idx.get("ltpc",{}).get("ltp") or idx.get("last_price"); vol=0
                        if not ltp and "ltpc" in feed: ltp=feed["ltpc"].get("ltp")
                        with lock:
                            if ltp:
                                instrument_data[ikey]["ltp"]=float(ltp)
                                if instrument_data[ikey]["prev_close"]>0:
                                    instrument_data[ikey]["change"]=(float(ltp)-instrument_data[ikey]["prev_close"])/instrument_data[ikey]["prev_close"]*100
                                pending[ikey]=float(ltp)
                                if get_status(instrument_data[ikey]) and not instrument_data[ikey]["break_time"]:
                                    instrument_data[ikey]["break_time"]=datetime.now(IST).strftime("%H:%M:%S")
                            if vol and not instrument_data[ikey]["is_index"]:
                                try:
                                    vi=int(float(vol))
                                    if vi>0: instrument_data[ikey]["vol"]=vi
                                except: pass
                    except: pass
            def on_open():
                print("✅ LIVE CONNECTED V36"); send_telegram_alert("✅ <b>V36 LIVE CONNECTED - MEMORY LITE</b>")

            def updater():
                nonlocal last_keys
                last_sort=time.time()
                print("📊 Updater Started...")
                while True:
                    try:
                        time.sleep(1)
                        if time.time()-last_sort>=3:
                            with lock:
                                if pending:
                                    for ik, lt in list(pending.items()):
                                        if instrument_data[ik]["prev_close"]>0:
                                            instrument_data[ik]["change"]=(lt-instrument_data[ik]["prev_close"])/instrument_data[ik]["prev_close"]*100
                                    pending.clear()
                                cur="".join([f"{k}{v['change']:.2f}" for k,v in sorted(instrument_data.items(), key=lambda x: x[1]["change"], reverse=True)][:5])
                                if cur!=last_keys:
                                    last_keys=cur
                                    full=build_sorted()
                                    try: sheet.update(values=full, range_name="A4")
                                    except:
                                        try: connect_sheets()
                                        except: pass
                                else:
                                    batch=[]; now_str=datetime.now(IST).strftime("%H:%M:%S")
                                    for ik, it in instrument_data.items():
                                        if it["symbol"] in row_map:
                                            for rnum in row_map[it["symbol"]]:
                                                dist=it["ltp"]/it["wh"]*100 if it["wh"]>0 else 0; volx=it["vol"]/it["prev_vol"] if it["prev_vol"]>0 else 0
                                                batch.append({"range": f"F{rnum}:N{rnum}", "values": [[it["ltp"], f"{it['change']:.2f}%", it["vol"], it["prev_vol"], f"{volx:.1f}X", f"{dist:.1f}%", get_status(it), it["break_time"], now_str]]})
                                    if batch:
                                        try:
                                            for i in range(0,len(batch),30):
                                                sheet.batch_update(batch[i:i+30])
                                                time.sleep(0.3)
                                        except:
                                            try: connect_sheets()
                                            except: pass
                            last_sort=time.time()
                            gc.collect()
                    except Exception as e:
                        print(f"Updater err {e}"); time.sleep(2)
            threading.Thread(target=updater, daemon=True).start()
            streamer.on("open", on_open); streamer.on("message", on_message)
            streamer.connect()
            while True: time.sleep(10)
        except Exception as e:
            print(f"Crash {e}"); time.sleep(15)

start_streamer_with_reconnect()
