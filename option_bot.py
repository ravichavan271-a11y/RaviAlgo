import gzip, io, time, threading, os, re
from datetime import datetime, timedelta
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd
import requests, urllib.parse
from concurrent.futures import ThreadPoolExecutor

print("V42 - OPEN BASED ATM + OCT EXPIRY ROLLOVER FIX")

UPSTOX_ACCESS_TOKEN = os.getenv("UPSTOX_TOKEN", "").strip()
if not UPSTOX_ACCESS_TOKEN:
    try:
        with open(os.path.join(os.path.dirname(__file__), "token.txt"), "r") as f:
            UPSTOX_ACCESS_TOKEN = f.read().strip()
    except: pass

print(f"TOKEN LEN: {len(UPSTOX_ACCESS_TOKEN)}")
if len(UPSTOX_ACCESS_TOKEN) < 50:
    print("TOKEN EMPTY"); exit()

SPREADSHEET_NAME = "option Dsheet"
SERVICE_ACCOUNT_FILE = r"C:\Users\Admin\Desktop\Angel\service_account.json"

def parse_date(val):
    if not val: return ""
    s = str(val).strip().split()[0].replace("/", "-").replace(".", "-")
    s = re.sub(r'[^0-9\-]', '', s)
    for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
        try: return datetime.strptime(s[:10], fmt).strftime("%Y-%m-%d")
        except: pass
    return s[:10]

def find_weekly_range_top3(sh):
    try:
        data = sh.sheet1.get_values("A1:Z10")
        wf=""; wt=""
        for row in data[:10]:
            row_str = " ".join([str(x) for x in row])
            if "FROM" in row_str.upper():
                dates = re.findall(r'\d{4}[-/]\d{1,2}[-/]\d{1,2}', row_str)
                if dates: wf=parse_date(dates[0])
            if "TO" in row_str.upper():
                dates = re.findall(r'\d{4}[-/]\d{1,2}[-/]\d{1,2}', row_str)
                if dates: wt=parse_date(dates[-1])
        return wf, wt
    except: return "", ""

try:
    scope = ["https://spreadsheets.google.com/feeds","https://www.googleapis.com/auth/drive"]
    gc = gspread.authorize(ServiceAccountCredentials.from_json_keyfile_name(SERVICE_ACCOUNT_FILE, scope))
    sh = gc.open(SPREADSHEET_NAME)
    sheet = sh.sheet1
    weekly_from, weekly_to = find_weekly_range_top3(sh)
    if not weekly_from or not weekly_to:
        weekly_to = datetime.now().strftime("%Y-%m-%d")
        weekly_from = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    print(f"Weekly {weekly_from} to {weekly_to}")
except Exception as e:
    print(f"SHEET ERR {e}"); exit()

RAW_STRUCTURE = {
    "NIFTY 50": ["BHARTIARTL","LT","RELIANCE"],
    "NIFTY BANK": ["ICICIBANK","SBIN","AXISBANK","KOTAKBANK","AUBANK","INDUSINDBK","HDFCBANK"],
    "Nifty Mid select": ["MCX","CDSL","BSE","PAYTM","INDIGO","POLYCAB","INDUSTOWER","ABB","TRENT","DIXON","ASIANPAINT"],
    "NIFTY FIN SERVICE": ["CHOLAFIN","BAJFINANCE","BAJAJFINSV","HDFCLIFE","SBILIFE","MFSL","SHRIRAMFIN"],
    "NIFTY IT": ["INFY","TCS","HCLTECH","WIPRO","TECHM","LTIM","PERSISTENT","OFSS","COFORGE"],
    "NIFTY AUTO": ["MARUTI","TATAMOTORS","M&M","BAJAJ-AUTO","EICHERMOT","TIINDIA","HEROMOTOCO"],
    "NIFTY DEFENCE": ["HAL","BEL","GRSE","COCHINSHIP","MAZDOCK","BDL","DATAPATTNS"],
    "NIFTY METAL": ["JSWSTEEL","HINDALCO","VEDL","JINDALSTEL","NATIONALUM","TITAN"],
    "NIFTY FMCG": ["HINDUNILVR","NESTLEIND","BRITANNIA","TATACONSUM","VBL","GODREJCP","COLPAL"],
    "NIFTY ENERGY": ["POWERGRID","COALINDIA","CGPOWER","ADANIGREEN","JSWENERGY"],
    "NIFTY PHARMA": ["DRREDDY","TORNTPHARM","LUPIN","SUNPHARMA","CIPLA","DIVISLAB","GLENMARK","ZYDU","LAURUSLABS"],
    "MOST LIQUID STOCKS": ["MARUTI","TRENT","POLYCAB","DIXON","BAJAJ-AUTO","PERSISTENT","BSE","INDIGO","BOSCHLTD","OFSS","ABB","SOLARINDS"]
}

liquid_set = set(RAW_STRUCTURE["MOST LIQUID STOCKS"])
STRUCTURE = {sec: ([s for s in stocks if s not in liquid_set] if sec!="MOST LIQUID STOCKS" else stocks) for sec, stocks in RAW_STRUCTURE.items()}
INDEX_LIST = [k for k in RAW_STRUCTURE.keys() if k!= "MOST LIQUID STOCKS"]

print("Downloading instruments...")
r = requests.get("https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz", timeout=40)
with gzip.GzipFile(fileobj=io.BytesIO(r.content)) as gz:
    df = pd.read_csv(gz, low_memory=False)

col_map = {c.lower(): c for c in df.columns}
def get_col(*names):
    for n in names:
        if n.lower() in col_map: return col_map[n.lower()]
    return None

strike_col = get_col("strike"); expiry_col = get_col("expiry")
instrument_key_col = get_col("instrument_key"); tradingsymbol_col = get_col("tradingsymbol")
last_price_col = get_col("last_price"); option_type_col = get_col("option_type")
exchange_col = get_col("exchange"); instrument_type_col = get_col("instrument_type")

mp={}; csv_last={}
for _, row in df.iterrows():
    sym=str(row.get(tradingsymbol_col,"")).strip(); key=str(row.get(instrument_key_col,"")).strip()
    lp=row.get(last_price_col,0)
    if not sym or not key: continue
    if "NSE_EQ" in key:
        mp[sym]=key
        try: csv_last[key]=float(lp) if pd.notna(lp) else 0
        except: csv_last[key]=0
        if sym.endswith("-EQ"):
            base=sym.replace("-EQ","")
            mp[base]=key; csv_last[base]=csv_last[key]
    elif sym not in mp: mp[sym]=key

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
if "ZYDUSLIFE" in mp: mp["ZYDU"]=mp["ZYDUSLIFE"]

index_data={}; index_keys=[]
for idx in INDEX_LIST:
    k=mp.get(idx)
    if k:
        index_data[k]={"symbol":idx,"pdh":0,"pdl":0,"wh":0,"wl":0,"ltp":0,"prev_close":0,"change":0,"dist":0,"break_time":""}
        index_keys.append(k)

stock_underlying_data={}; stock_keys=[]
for sec, stocks in STRUCTURE.items():
    for sym in stocks:
        k=mp.get(sym)
        if k and k not in stock_underlying_data:
            stock_underlying_data[k]={"symbol":sym,"ltp":0,"open":0,"csv": csv_last.get(k,0) or csv_last.get(sym,0)}
            stock_keys.append(k)

def get_candle(k, fro, to):
    ek=urllib.parse.quote(k, safe=''); url=f"https://api.upstox.com/v3/historical-candle/{ek}/days/1/{to}/{fro}"
    try:
        resp=requests.get(url, headers={"Authorization": f"Bearer {UPSTOX_ACCESS_TOKEN}"}, timeout=10)
        if resp.status_code==200: return k, resp.json().get("data",{}).get("candles",[])
    except: pass
    return k, []

def get_candle_with_fallback(k, base_day):
    for i in range(0, 6):
        d = (datetime.strptime(base_day, "%Y-%m-%d") - timedelta(days=i)).strftime("%Y-%m-%d")
        if datetime.strptime(d,"%Y-%m-%d").weekday() >= 5: continue
        ek=urllib.parse.quote(k, safe=''); url=f"https://api.upstox.com/v3/historical-candle/{ek}/days/1/{d}/{d}"
        try:
            resp=requests.get(url, headers={"Authorization": f"Bearer {UPSTOX_ACCESS_TOKEN}"}, timeout=10)
            if resp.status_code==200:
                candles = resp.json().get("data",{}).get("candles",[])
                if candles: return k, candles
        except: pass
    return k, []

def fetch_ltp(keys, data_dict):
    cnt=0
    for i in range(0, len(keys), 20):
        batch = keys[i:i+20]
        qs = "&".join([f"instrument_key={urllib.parse.quote(k)}" for k in batch])
        url = f"https://api.upstox.com/v3/market-quote/ltp?{qs}"
        try:
            resp = requests.get(url, headers={"Authorization": f"Bearer {UPSTOX_ACCESS_TOKEN}", "Accept":"application/json"}, timeout=15)
            if resp.status_code==200:
                d = resp.json().get("data",{})
                for ret_key, ret_val in d.items():
                    lp = ret_val.get("last_price",0)
                    inst_token = ret_val.get("instrument_token","")
                    if lp==0: continue
                    if inst_token in data_dict:
                        data_dict[inst_token]["ltp"]=float(lp); cnt+=1
                    elif ret_key in data_dict:
                        data_dict[ret_key]["ltp"]=float(lp); cnt+=1
                    else:
                        for bkey in batch:
                            if bkey==inst_token:
                                data_dict[bkey]["ltp"]=float(lp); cnt+=1; break
                if cnt==0 and len(d)>0:
                    vals=list(d.values())
                    for idx, k in enumerate(batch):
                        if idx < len(vals):
                            lp=vals[idx].get("last_price",0)
                            if lp>0 and k in data_dict:
                                data_dict[k]["ltp"]=float(lp); cnt+=1
        except Exception as e:
            print(f"LTP err {e}")
    for k,v in data_dict.items():
        if v["ltp"]==0 and v.get("csv",0)>0: v["ltp"]=v["csv"]
    print(f"LTP LIVE:{cnt} TOTAL:{sum(1 for v in data_dict.values() if v['ltp']>0)}/{len(keys)}")
    return cnt

print("Fetching LTP...")
fetch_ltp(index_keys, index_data)
fetch_ltp(stock_keys, stock_underlying_data)

today = datetime.now().strftime("%Y-%m-%d")
with ThreadPoolExecutor(max_workers=10) as ex:
    for k,candles in ex.map(lambda kk: get_candle(kk, today, today), stock_keys):
        if candles: stock_underlying_data[k]["open"]=float(candles[0][1])
for k,v in stock_underlying_data.items():
    if v["open"]==0: v["open"]=v["ltp"] or v["csv"] or 0 # OPEN nasel tar 0 thevla

pd_day=None
for i in range(1,8):
    d=(datetime.now()-timedelta(days=i)).strftime("%Y-%m-%d")
    if datetime.strptime(d,"%Y-%m-%d").weekday()<5: pd_day=d; break

print(f"PD Day: {pd_day}")

# Weekly WH/WL
with ThreadPoolExecutor(max_workers=10) as ex:
    for k,candles in ex.map(lambda kk: get_candle(kk, weekly_from, weekly_to), index_keys):
        if candles:
            d=pd.DataFrame(candles,columns=["dt","open","high","low","close","vol","oi"])
            if k in index_data:
                index_data[k]["wh"]=float(d["high"].max()); index_data[k]["wl"]=float(d["low"].min())
with ThreadPoolExecutor(max_workers=10) as ex:
    for k,candles in ex.map(lambda kk: get_candle_with_fallback(kk, pd_day), index_keys):
        if candles and k in index_data:
            index_data[k]["pdh"]=float(candles[0][2]); index_data[k]["pdl"]=float(candles[0][3])
            index_data[k]["prev_close"]=float(candles[0][4])
for k,v in index_data.items():
    if v["wh"]==0: v["wh"]=v["ltp"]
    if v["wl"]==0: v["wl"]=v["ltp"]
    if v["pdh"]==0: v["pdh"]=v["ltp"]
    if v["pdl"]==0: v["pdl"]=v["ltp"]
    if v["prev_close"]==0: v["prev_close"]=v["ltp"]
    v["change"]=(v["ltp"]-v["prev_close"])/v["prev_close"]*100 if v["prev_close"]>0 else 0
    v["dist"]=v["ltp"]/v["wh"]*100 if v["wh"]>0 else 0

print("Building Options ATM - OPEN BASED + OCT FIX...")
opt_df = df[(df[exchange_col]=="NSE_FO")].copy() if exchange_col else df[df[instrument_key_col].str.contains('NSE_FO', na=False)].copy()
opt_df['expiry_dt'] = pd.to_datetime(opt_df[expiry_col], errors='coerce')
opt_df = opt_df.dropna(subset=['expiry_dt'])
yesterday = pd.Timestamp.now().normalize() - pd.Timedelta(days=1)
opt_df = opt_df[opt_df['expiry_dt'] >= yesterday]
print(f"Available expiries: {sorted(opt_df['expiry_dt'].dt.strftime('%d-%b-%Y').unique())[:5]}")

if instrument_type_col:
    opt_df = opt_df[opt_df[instrument_type_col].astype(str).str.contains('OPTSTK', na=False)]

stock_to_options = {}
for stock in set([s for lst in STRUCTURE.values() for s in lst]):
    f = opt_df[opt_df[tradingsymbol_col].astype(str).str.startswith(stock)]
    if not f.empty: stock_to_options[stock] = f

option_data = {}; option_keys = []
for stock, odf in stock_to_options.items():
    uk = mp.get(stock); ud = stock_underlying_data.get(uk, {})

    # === MAIN FIX: FAKT MORNING OPEN PASUN STRIKE ===
    base = ud.get("open",0)
    if base==0 or base==100:
        print(f"{stock} SKIP - OPEN not available")
        continue

    nearest_expiry = odf['expiry_dt'].min()
    expiry_df = odf[odf['expiry_dt'] == nearest_expiry].copy()
    if strike_col not in expiry_df.columns: continue
    expiry_df['strike_diff'] = abs(expiry_df[strike_col] - base)
    atm_strike = int(float(expiry_df.sort_values('strike_diff').iloc[0][strike_col]))
    atm_df = expiry_df[expiry_df[strike_col]==atm_strike]
    print(f"{stock} OPEN {base:.1f} -> ATM {atm_strike} Expiry {nearest_expiry.strftime('%d-%b-%Y')}")
    for _, row in atm_df.iterrows():
        otype = str(row.get(option_type_col,"")).upper()
        ikey = row[instrument_key_col]
        if ikey not in option_data:
            option_data[ikey] = {"underlying": stock, "strike": atm_strike, "otype": otype, "expiry": nearest_expiry.strftime("%d-%b-%Y"), "pdh":0,"pdl":0,"wh":0,"wl":0,"ltp":0,"prev_close":0,"change":0,"dist":0,"base":base, "csv":0}
            option_keys.append(ikey)

print(f"Option Keys: {len(option_keys)}")

with ThreadPoolExecutor(max_workers=10) as ex:
    for k,candles in ex.map(lambda kk: get_candle(kk, weekly_from, weekly_to), option_keys):
        if candles:
            d=pd.DataFrame(candles,columns=["dt","open","high","low","close","vol","oi"])
            if k in option_data:
                option_data[k]["wh"]=float(d["high"].max()); option_data[k]["wl"]=float(d["low"].min())

with ThreadPoolExecutor(max_workers=15) as ex:
    for k,candles in ex.map(lambda kk: get_candle_with_fallback(kk, pd_day), option_keys):
        if candles and k in option_data:
            option_data[k]["pdh"]=float(candles[0][2]); option_data[k]["pdl"]=float(candles[0][3])
            option_data[k]["prev_close"]=float(candles[0][4])

print("Fetching Option LTP LIVE OCT...")
fetch_ltp(option_keys, option_data)

for k,v in option_data.items():
    if v["wh"]==0: v["wh"]=v["ltp"] or 1
    if v["wl"]==0: v["wl"]=v["ltp"] or 1
    if v["pdh"]==0: v["pdh"]=v["ltp"]
    if v["pdl"]==0: v["pdl"]=v["ltp"]
    if v["prev_close"]==0: v["prev_close"]=v["ltp"]
    v["change"]=(v["ltp"]-v["prev_close"])/v["prev_close"]*100 if v["prev_close"]>0 else 0
    v["dist"]=v["ltp"]/v["wh"]*100 if v["wh"]>0 else 0

def get_status(it):
    if it["ltp"]>it["wh"]: return "BREAKOUT"
    if it["ltp"]<it["wl"]: return "BREAKDOWN"
    return ""

row_map_index={}; row_map_stock={}

def build_rows():
    rows=[]; rnum=4
    rows.append(["CE SECTION: Stock","Strike","PD High","PD Low","WEEK HIGH","WEEK LOW","LTP","CHANGE %","DIST%","STATUS","","PE SECTION: Stock","Strike","PD High","PD Low","WEEK HIGH","WEEK LOW","LTP","CHANGE %","DIST%","STATUS"])
    rnum+=1
    for sec_name in STRUCTURE.keys():
        if not STRUCTURE[sec_name]: continue
        rows.append([sec_name]); rnum+=1
        sector_stocks=[]
        for stock in STRUCTURE[sec_name]:
            ce=None; pe=None
            for k,v in option_data.items():
                if v["underlying"]==stock:
                    if v["otype"]=="CE": ce=v
                    if v["otype"]=="PE": pe=v
            if ce or pe: sector_stocks.append((stock,ce,pe))
        sector_stocks.sort(key=lambda x: (x[1]["change"] if x[1] else (x[2]["change"] if x[2] else -9999)), reverse=True)
        for stock, ce_it, pe_it in sector_stocks:
            ce_strike = ce_it["strike"] if ce_it else (pe_it["strike"] if pe_it else 0)
            rows.append([stock, ce_strike, ce_it["pdh"] if ce_it else 0, ce_it["pdl"] if ce_it else 0, ce_it["wh"] if ce_it else 0, ce_it["wl"] if ce_it else 0, ce_it["ltp"] if ce_it else 0, f"{ce_it['change']:.2f}%" if ce_it else "", f"{ce_it['dist']:.1f}%" if ce_it else "", get_status(ce_it) if ce_it else "", "", stock, pe_it["strike"] if pe_it else ce_strike, pe_it["pdh"] if pe_it else 0, pe_it["pdl"] if pe_it else 0, pe_it["wh"] if pe_it else 0, pe_it["wl"] if pe_it else 0, pe_it["ltp"] if pe_it else 0, f"{pe_it['change']:.2f}%" if pe_it else "", f"{pe_it['dist']:.1f}%" if pe_it else "", get_status(pe_it) if pe_it else ""])
            row_map_stock[stock]=rnum; rnum+=1
        rows.append([]); rnum+=1
    rows.append([]); rnum+=1
    rows.append(["=== INDEX ==="]); rnum+=1
    rows.append(["Symbol","PD High","PD Low","WEEK HIGH","WEEK LOW","LTP (Spot)","CHANGE %","DIST%","STATUS"]); rnum+=1
    for it in sorted(index_data.values(), key=lambda x: x["change"], reverse=True):
        rows.append([it["symbol"],it["pdh"],it["pdl"],it["wh"],it["wl"],it["ltp"],f"{it['change']:.2f}%",f"{it['dist']:.1f}%",get_status(it)])
        row_map_index[it["symbol"]]=rnum; rnum+=1
    return rows

print("Writing to sheet...")
try: sheet.batch_clear(["A4:Z5000"])
except: pass
final_rows = build_rows()
sheet.update(values=final_rows, range_name="A4")
sheet.update(values=[[datetime.now().strftime("%H:%M:%S")]], range_name="C3")
print(f"DONE - Index {len(index_data)} Option {len(option_data)}")

import upstox_client
configuration = upstox_client.Configuration(); configuration.access_token = UPSTOX_ACCESS_TOKEN
api_client = upstox_client.ApiClient(configuration)
all_keys = index_keys + option_keys
if not all_keys:
    print("NO KEYS"); time.sleep(10); exit()

streamer = upstox_client.MarketDataStreamerV3(api_client=api_client, instrumentKeys=all_keys, mode="full")
pending={}; lock=threading.Lock()

def on_message(msg):
    feeds=msg.get("feeds",{})
    for ikey, feed in feeds.items():
        try:
            ltp=None
            if "fullFeed" in feed:
                ff=feed["fullFeed"]
                if "marketFF" in ff: ltp=ff["marketFF"].get("ltpc",{}).get("ltp")
                elif "indexFF" in ff: ltp=ff["indexFF"].get("ltpc",{}).get("ltp")
            if ltp and float(ltp)>0:
                with lock: pending[ikey]=float(ltp)
        except: pass

def on_open():
    print(f"LIVE STARTED - {len(all_keys)} keys OCT {datetime.now()}")

streamer.on("open", on_open); streamer.on("message", on_message); streamer.connect()

def updater():
    loop=0
    while True:
        time.sleep(2)
        with lock:
            for ikey, ltp in list(pending.items()):
                if ikey in option_data:
                    prev=option_data[ikey]["prev_close"]
                    option_data[ikey]["ltp"]=float(ltp)
                    if prev>0: option_data[ikey]["change"]=(ltp-prev)/prev*100
                    if option_data[ikey]["wh"]>0: option_data[ikey]["dist"]=ltp/option_data[ikey]["wh"]*100
                if ikey in index_data:
                    prev=index_data[ikey]["prev_close"]
                    index_data[ikey]["ltp"]=float(ltp)
                    if prev>0: index_data[ikey]["change"]=(ltp-prev)/prev*100
                    if index_data[ikey]["wh"]>0: index_data[ikey]["dist"]=ltp/index_data[ikey]["wh"]*100
            pending.clear()
            if loop % 5 == 0:
                try:
                    fetch_ltp(option_keys, option_data)
                    fetch_ltp(index_keys, index_data)
                except: pass
            batch=[]
            batch.append({"range": f"C3", "values": [[datetime.now().strftime("%H:%M:%S")]]})
            for sym, rnum in row_map_index.items():
                for k,v in index_data.items():
                    if v["symbol"]==sym and v["ltp"]>0:
                        batch.append({"range": f"B{rnum}:I{rnum}", "values": [[v["pdh"], v["pdl"], v["wh"], v["wl"], v["ltp"], f"{v['change']:.2f}%", f"{v['dist']:.1f}%", get_status(v)]]})
            for stock, rnum in row_map_stock.items():
                ce=None; pe=None
                for k,v in option_data.items():
                    if v["underlying"]==stock:
                        if v["otype"]=="CE": ce=v
                        if v["otype"]=="PE": pe=v
                if ce and ce["ltp"]>0:
                    batch.append({"range": f"G{rnum}:J{rnum}", "values": [[ce["ltp"], f"{ce['change']:.2f}%", f"{ce['dist']:.1f}%", get_status(ce)]]})
                if pe and pe["ltp"]>0:
                    batch.append({"range": f"R{rnum}:U{rnum}", "values": [[pe["ltp"], f"{pe['change']:.2f}%", f"{pe['dist']:.1f}%", get_status(pe)]]})
            if batch:
                try: sheet.batch_update(batch)
                except: pass
        loop+=1

threading.Thread(target=updater, daemon=True).start()
while True: time.sleep(1)