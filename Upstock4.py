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
print("V34 NO-BLINK + FAST 2 SEC + GAP FIX")

def is_market_open():
    now = datetime.now(IST)
    if now.weekday() >= 5: return False
    return now.replace(hour=9, minute=0) <= now <= now.replace(hour=15, minute=30)

SPREADSHEET_NAME = "Dsheet"
SERVICE_ACCOUNT_FILE = os.path.join(os.path.dirname(__file__), "service_account.json")

def get_gspread_client():
    scope = ["https://spreadsheets.google.com/feeds","https://www.googleapis.com/auth/drive"]
    for path in [SERVICE_ACCOUNT_FILE, "./service_account.json", "service_account.json"]:
        if os.path.exists(path):
            try: return gspread.authorize(ServiceAccountCredentials.from_json_keyfile_name(path, scope))
            except: pass
    env_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "") or os.environ.get("SERVICE_ACCOUNT_JSON", "")
    if env_json:
        import json as js
        creds_dict = js.loads(env_json)
        return gspread.authorize(ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope))
    raise Exception("service_account.json missing")

def get_automatic_token():
    def ok(t): return t and len(t)>100 and "eyJ" in str(t)
    try:
        gc_temp = get_gspread_client()
        b1 = str(gc_temp.open(SPREADSHEET_NAME).sheet1.cell(1,2).value or "").strip()
        if ok(b1): return b1
    except Exception as e: print(f"B1 err {e}")
    tok = os.environ.get("UPSTOX_ACCESS_TOKEN","") or os.environ.get("UPSTOX_TOKEN","")
    if ok(tok): return tok
    return None

def is_token_valid(token):
    try:
        r=requests.get("https://api.upstox.com/v2/user/profile", headers={"Authorization": f"Bearer {token}"}, timeout=10)
        return r.status_code==200
    except: return False

UPSTOX_ACCESS_TOKEN = get_automatic_token()
while not UPSTOX_ACCESS_TOKEN or not is_token_valid(UPSTOX_ACCESS_TOKEN):
    print("Waiting for valid token..."); time.sleep(60)
    UPSTOX_ACCESS_TOKEN = get_automatic_token()

def parse_date(val):
    if not val: return ""
    s = str(val).strip().split()[0].replace("/", "-").replace(".", "-")
    s = re.sub(r'[^0-9\-]', '', s)
    for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
        try: return datetime.strptime(s[:10], fmt).strftime("%Y-%m-%d")
        except: pass
    return s[:10]

gc = get_gspread_client()
sh = gc.open(SPREADSHEET_NAME)
sheet = sh.sheet1

weekly_from = parse_date(sheet.cell(2,2).value)
weekly_to = parse_date(sheet.cell(3,2).value)
if not weekly_from: weekly_from=(datetime.now()-timedelta(days=7)).strftime("%Y-%m-%d")
if not weekly_to: weekly_to=datetime.now().strftime("%Y-%m-%d")

STRUCTURE = {
    "NIFTY 50": ["BHARTIARTL","LT","RELIANCE"],
    "NIFTY BANK": ["ICICIBANK","SBIN","AXISBANK","KOTAKBANK","AUBANK","INDUSINDBK","HDFCBANK"],
    "MOST LIQUID STOCKS": ["MARUTI","TRENT","POLYCAB","DIXON","BAJAJ-AUTO","PERSISTENT","BSE","INDIGO","BOSCHLTD","OFSS","ABB","SOLARINDS"]
}

r=requests.get("https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz", timeout=30)
with gzip.GzipFile(fileobj=io.BytesIO(r.content)) as gz: df=pd.read_csv(gz)

mp={}
for _, row in df.iterrows():
    sym=str(row.get("tradingsymbol","")).strip(); key=str(row.get("instrument_key","")).strip()
    if not sym or not key: continue
    if "NSE_EQ" in key:
        mp[sym]=key
        if sym.endswith("-EQ"): mp[sym.replace("-EQ","")]=key
    elif sym not in mp: mp[sym]=key
mp["NIFTY 50"]="NSE_INDEX|Nifty 50"; mp["NIFTY BANK"]="NSE_INDEX|Nifty Bank"

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

def get_candle(k,fro,to):
    time.sleep(0.5)
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

with ThreadPoolExecutor(max_workers=3) as ex:
    for k,candles in ex.map(lambda kk: get_candle(kk, weekly_from, weekly_to), all_keys):
        if candles:
            d=pd.DataFrame(candles,columns=["datetime","open","high","low","close","volume","oi"])
            instrument_data[k]["wh"]=float(d["high"].max()); instrument_data[k]["wl"]=float(d["low"].min())
            instrument_data[k]["prev_close"]=float(d.iloc[-1]["close"])

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

for i in range(0,len(all_keys),10):
    fetch_ltp(all_keys[i:i+10]); time.sleep(0.8)

for k,v in instrument_data.items():
    if v["ltp"]==0 and v["prev_close"]>0: v["ltp"]=v["prev_close"]
    v["change"]=(v["ltp"]-v["prev_close"])/v["prev_close"]*100 if v["prev_close"]>0 else 0

row_map={}; last_row_count=0
def build_sorted():
    global row_map
    row_map.clear()
    rows=[]; rnum=5
    rows.append(["Symbol","PD High","PD Low","WEEK HIGH","WEEK LOW","LTP","CHANGE %","VOLUME","PREV VOL","VOL X","DIST%","STATUS","BREAK TIME"])
    indices=[it for it in instrument_data.values() if it.get("is_index")]
    indices.sort(key=lambda x: x["change"], reverse=True)
    for it in indices:
        dist=it["ltp"]/it["wh"]*100 if it["wh"]>0 else 0
        status=get_status(it)
        rows.append([it["symbol"],it["pdh"],it["pdl"],it["wh"],it["wl"],it["ltp"],f"{it['change']:.2f}%",0,0,"N/A",f"{dist:.1f}%",status,it["break_time"]])
        row_map.setdefault(it["symbol"], []).append(rnum); rnum+=1
    rows.append([""]); rnum+=1
    for sec_name in STRUCTURE.keys():
        rows.append([sec_name]); rnum+=1
        stocks=[instrument_data[mp.get(sym)] for sym in STRUCTURE[sec_name] if mp.get(sym) in instrument_data]
        stocks.sort(key=lambda x: x["change"], reverse=True)
        for it in stocks:
            dist=it["ltp"]/it["wh"]*100 if it["wh"]>0 else 0; volx=it["vol"]/it["prev_vol"] if it["prev_vol"]>0 else 0
            status=get_status(it)
            rows.append([it["symbol"],it["pdh"],it["pdl"],it["wh"],it["wl"],it["ltp"],f"{it['change']:.2f}%",it["vol"],it["prev_vol"],f"{volx:.1f}X",f"{dist:.1f}%",status,it["break_time"]])
            row_map.setdefault(it["symbol"], []).append(rnum); rnum+=1
        rows.append([""]); rnum+=1
    return rows

def safe_sheet_update():
    global last_row_count
    full=build_sorted()
    sheet.update(values=full, range_name="A4")
    if last_row_count>0 and len(full)<last_row_count:
        sheet.batch_clear([f"A{4+len(full)}:Z{4+last_row_count+5}"])
    last_row_count=len(full)
    print(f"NO-BLINK UPDATE {len(full)} rows")

safe_sheet_update()

configuration = upstox_client.Configuration(); configuration.access_token=UPSTOX_ACCESS_TOKEN
api_client = upstox_client.ApiClient(configuration)
streamer = upstox_client.MarketDataStreamerV3(api_client=api_client, instrumentKeys=all_keys, mode="full")
pending={}; lock=threading.Lock()

def on_message(msg):
    for ikey,feed in msg.get("feeds",{}).items():
        if ikey not in instrument_data: continue
        try:
            ltp=None
            if "fullFeed" in feed:
                ff=feed["fullFeed"]
                if "marketFF" in ff: ltp=ff["marketFF"].get("ltpc",{}).get("ltp")
                elif "indexFF" in ff: ltp=ff["indexFF"].get("ltpc",{}).get("ltp")
            if ltp:
                with lock:
                    instrument_data[ikey]["ltp"]=float(ltp)
                    pending[ikey]=float(ltp)
        except: pass

def on_open(): print("LIVE CONNECTED V34 NO-BLINK")
streamer.on("open", on_open); streamer.on("message", on_message); streamer.connect()

def sheet_updater():
    global last_row_count
    while True:
        time.sleep(2)
        with lock:
            for ikey,ltp in list(pending.items()):
                instrument_data[ikey]["change"]=(ltp-instrument_data[ikey]["prev_close"])/instrument_data[ikey]["prev_close"]*100 if instrument_data[ikey]["prev_close"]>0 else 0
            pending.clear()
            full=build_sorted()
            try:
                sheet.update(values=full, range_name="A4")
                sheet.update(values=[[datetime.now(IST).strftime("%H:%M:%S")]], range_name="C3")
                if len(full)<last_row_count:
                    sheet.batch_clear([f"A{4+len(full)}:Z{4+last_row_count+5}"])
                last_row_count=len(full)
                print(f"FAST 2SEC NO BLINK {datetime.now(IST).strftime('%H:%M:%S')}")
            except Exception as e: print(f"Updater err {e}")

threading.Thread(target=sheet_updater, daemon=True).start()
while True: time.sleep(1)
