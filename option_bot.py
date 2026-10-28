import gzip, io, time, threading, os, re, json, html
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd
import requests, urllib.parse
from concurrent.futures import ThreadPoolExecutor

print("V42 - OPEN BASED ATM + OCT EXPIRY ROLLOVER FIX")

SPREADSHEET_NAME = os.getenv("OPTION_SPREADSHEET_NAME", "option Dsheet")
TOKEN_SPREADSHEET_NAME = os.getenv("TOKEN_SPREADSHEET_NAME", "Dsheet")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
GOOGLE_SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
gc = None
_telegram_missing_warned = False
IST = ZoneInfo("Asia/Kolkata")

def send_breakout_telegram_alerts(events):
    """Send a grouped Telegram message for newly entered breakout/breakdown states."""
    global _telegram_missing_warned
    if not events:
        return
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        if not _telegram_missing_warned:
            print("Telegram breakout alerts disabled: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing.", flush=True)
            _telegram_missing_warned = True
        return
    stamp=datetime.now(IST).strftime("%H:%M:%S IST")
    message="🚨 <b>RENDER OPTION DSheet — BREAKOUT / BREAKDOWN</b>\n" + "\n".join(events[:45])
    if len(events)>45:
        message+=f"\n… and {len(events)-45} more signals"
    message+=f"\n\n{stamp}"
    try:
        response=requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id":TELEGRAM_CHAT_ID,"text":message,"parse_mode":"HTML","disable_web_page_preview":True},
            timeout=12,
        )
        if response.status_code==200:
            print(f"Telegram breakout alert sent ({len(events)} signal(s)).", flush=True)
        else:
            print(f"Telegram breakout alert failed: HTTP {response.status_code}; {response.text[:250]}", flush=True)
    except requests.RequestException as exc:
        print(f"Telegram breakout alert request failed: {type(exc).__name__}", flush=True)

def get_google_client():
    if SERVICE_ACCOUNT_JSON:
        credentials = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(SERVICE_ACCOUNT_JSON), GOOGLE_SCOPE)
    else:
        credentials = ServiceAccountCredentials.from_json_keyfile_name(os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json"), GOOGLE_SCOPE)
    return gspread.authorize(credentials)

def _is_upstox_token_valid(token):
    if not token or len(token) < 50:
        return False
    try:
        response = requests.get(
            "https://api.upstox.com/v2/user/profile",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=10,
        )
        return response.status_code == 200
    except requests.RequestException:
        return False

def get_upstox_token():
    # Dsheet is refreshed by the OAuth callback each morning. Check it first so
    # a stale Render environment token cannot hide the newly approved token.
    try:
        global gc
        gc = gc or get_google_client()
        sheet_token = str(gc.open(TOKEN_SPREADSHEET_NAME).sheet1.cell(1, 2).value or "").strip()
        if _is_upstox_token_valid(sheet_token):
            print("Using the current valid Upstox token from Dsheet.")
            return sheet_token
        if sheet_token:
            print("Dsheet Upstox token is expired or invalid; checking fallback sources.")
    except Exception as exc:
        print(f"TOKEN SHEET ERR: {type(exc).__name__}")

    candidates = [
        ("environment", os.getenv("UPSTOX_ACCESS_TOKEN", "") or os.getenv("UPSTOX_TOKEN", "")),
    ]
    try:
        with open(os.path.join(os.path.dirname(__file__), "token.txt"), "r") as token_file:
            candidates.append(("token.txt", token_file.read().strip()))
    except OSError:
        pass
    for source, token in candidates:
        token = token.strip()
        if _is_upstox_token_valid(token):
            print(f"Using the current valid Upstox token from {source}.")
            return token
    return ""

UPSTOX_ACCESS_TOKEN = get_upstox_token()
print(f"TOKEN LEN: {len(UPSTOX_ACCESS_TOKEN)}")
if len(UPSTOX_ACCESS_TOKEN) < 50:
    print("TOKEN EMPTY or expired; update Dsheet cell B1 before running.")
    raise SystemExit(1)

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
    gc = gc or get_google_client()
    sh = gc.open(SPREADSHEET_NAME)
    sheet = sh.sheet1
    sheet2 = sh.worksheet("BREAKOUT")
    if sheet2 is None:
        raise RuntimeError("Sheet 2 worksheet was not found")
    weekly_from, weekly_to = find_weekly_range_top3(sh)
    if not weekly_from or not weekly_to:
        weekly_to = datetime.now().strftime("%Y-%m-%d")
        weekly_from = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    print(f"Weekly {weekly_from} to {weekly_to}")
except Exception as e:
    print(f"SHEET ERR {e}"); raise SystemExit(1)

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
            stock_underlying_data[k]={"symbol":sym,"ltp":0,"open":0,"csv":csv_last.get(k,0) or csv_last.get(sym,0),"prev_close":0,"pdh":0,"pdl":0,"change":0}
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
print("Fetching stock previous close / PDH / PDL...")
with ThreadPoolExecutor(max_workers=10) as ex:
    for k,candles in ex.map(lambda kk:get_candle(kk,pd_day,pd_day),stock_keys):
        if candles and k in stock_underlying_data:
            stock_underlying_data[k]["pdh"]=float(candles[0][2])
            stock_underlying_data[k]["pdl"]=float(candles[0][3])
            stock_underlying_data[k]["prev_close"]=float(candles[0][4])
for it in stock_underlying_data.values():
    it["change"]=(it["ltp"]-it["prev_close"])/it["prev_close"]*100 if it["prev_close"]>0 else 0

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
            option_data[ikey] = {"underlying": stock, "strike": atm_strike, "otype": otype, "expiry": nearest_expiry.strftime("%d-%b-%Y"), "pdh":0,"pdl":0,"wh":0,"wl":0,"ltp":0,"prev_close":0,"change":0,"dist":0,"base":base, "csv":0, "break_ts":None, "break_time":""}
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

def update_break_time(it, old_status):
    new_status=get_status(it)
    if new_status=="BREAKOUT" and old_status!="BREAKOUT":
        now=datetime.now()
        it["break_ts"]=now
        it["break_time"]=now.strftime("%H:%M:%S")
    elif new_status!="BREAKOUT":
        it["break_ts"]=None
        it["break_time"]=""

# If the script starts while a contract is already above its weekly high,
# use startup time as the earliest known breakout time for this run.
for _it in option_data.values():
    if get_status(_it)=="BREAKOUT":
        _now=datetime.now()
        _it["break_ts"]=_now
        _it["break_time"]=_now.strftime("%H:%M:%S")

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


row_map_index={}; row_map_stock={}; dashboard_row_map_index={}; row_map_option={}; option_panel_end_row=5000; option_panel_row_ranges={}
DASHBOARD_START_ROW=17

def build_top_summary():
    """Top stock and index gainers/losers, ranked by spot change."""
    grid=[[""]*22 for _ in range(14)]
    grid[0][0]="TOP 5 GAINERS"
    grid[0][11]="TOP 5 LOSERS"
    grid[0][20]="Updated"
    grid[0][21]=datetime.now().strftime("%H:%M:%S")
    headers=["Stock","Spot LTP","% Change","PDH/PDL","ATM Option","Option %"]
    grid[1][0:6]=headers
    grid[1][11:17]=headers
    candidates=[v for v in stock_underlying_data.values() if v.get("prev_close",0)>0 and v.get("ltp",0)>0]
    gainers=sorted((v for v in candidates if v["change"]>0),key=lambda x:x["change"],reverse=True)[:5]
    losers=sorted((v for v in candidates if v["change"]<0),key=lambda x:x["change"])[:5]
    def make_row(it,otype):
        signal="PDH BREAK" if it.get("pdh",0)>0 and it["ltp"]>it["pdh"] else ("PDL BREAK" if it.get("pdl",0)>0 and it["ltp"]<it["pdl"] else "NO BREAK")
        opt=next((v for v in option_data.values() if v["underlying"]==it["symbol"] and v["otype"]==otype),None)
        return [it["symbol"],it["ltp"],f"{it['change']:+.2f}%",signal,f"{otype} {opt['strike']}" if opt else "",f"{opt['change']:+.2f}%" if opt else ""]
    for i,it in enumerate(gainers): grid[i+2][0:6]=make_row(it,"CE")
    for i,it in enumerate(losers): grid[i+2][11:17]=make_row(it,"PE")
    grid[7][0]="TOP 5 INDEX GAINERS"
    grid[7][11]="TOP 5 INDEX LOSERS"
    index_headers=["Index","Spot LTP","% Change","PDH/PDL","WEEK H/L","Status"]
    grid[8][0:6]=index_headers
    grid[8][11:17]=index_headers
    indices=[v for v in index_data.values() if v.get("prev_close",0)>0 and v.get("ltp",0)>0]
    index_gainers=sorted((v for v in indices if v["change"]>0),key=lambda x:x["change"],reverse=True)[:5]
    index_losers=sorted((v for v in indices if v["change"]<0),key=lambda x:x["change"])[:5]
    def make_index_row(it):
        signal="PDH BREAK" if it.get("pdh",0)>0 and it["ltp"]>it["pdh"] else ("PDL BREAK" if it.get("pdl",0)>0 and it["ltp"]<it["pdl"] else "NO BREAK")
        week=f"{it.get('wh',0):.2f}/{it.get('wl',0):.2f}"
        return [it["symbol"],it["ltp"],f"{it['change']:+.2f}%",signal,week,get_status(it) or "-"]
    for i,it in enumerate(index_gainers): grid[i+9][0:6]=make_index_row(it)
    for i,it in enumerate(index_losers): grid[i+9][11:17]=make_index_row(it)
    return grid

def build_dashboard():
    """Build four live status panels and the index table below them."""
    global dashboard_row_map_index, row_map_option, option_panel_end_row, option_panel_row_ranges
    dashboard_row_map_index={}; row_map_option={}; option_panel_row_ranges={}
    headers=["Stock","Strike","PD High","PD Low","WEEK HIGH","WEEK LOW","LTP","CHANGE %","DIST%","STATUS","BREAK TIME"]
    grid=[]
    def put(row_idx, col_idx, values):
        while len(grid) <= row_idx:
            grid.append([])
        while len(grid[row_idx]) < col_idx + len(values):
            grid[row_idx].append("")
        grid[row_idx][col_idx:col_idx+len(values)] = values
    def panel(title, col, status, otype, title_row):
        put(title_row, col, [title])
        put(title_row+1, col, headers)
        items=[v for v in option_data.values() if v["otype"]==otype and get_status(v)==status]
        if status=="BREAKOUT":
            items.sort(key=lambda x:(x.get("break_ts") or datetime.min,x.get("change",0)), reverse=True)
        else:
            items.sort(key=lambda x:x["change"], reverse=True)
        for offset,it in enumerate(items,1):
            row_idx=title_row+1+offset
            put(row_idx,col,[it["underlying"],it["strike"],it["pdh"],it["pdl"],it["wh"],it["wl"],it["ltp"],f"{it['change']:.2f}%",f"{it['dist']:.1f}%",status,it.get("break_time","")])
            row_map_option[(it["underlying"],otype)]=row_idx+DASHBOARD_START_ROW
        if items:
            start_row_index=DASHBOARD_START_ROW+title_row+1
            option_panel_row_ranges[(otype,status)]=(start_row_index,start_row_index+len(items))
        return len(items)
    ce_bo=panel("CE BREAKOUT",0,"BREAKOUT","CE",0)
    pe_bo=panel("PE BREAKOUT",11,"BREAKOUT","PE",0)
    breakdown_title=2+max(ce_bo,pe_bo)
    ce_bd=panel("CE BREAKDOWN",0,"BREAKDOWN","CE",breakdown_title)
    pe_bd=panel("PE BREAKDOWN",11,"BREAKDOWN","PE",breakdown_title)
    index_title=breakdown_title+2+max(ce_bd,pe_bd)
    option_panel_end_row=index_title+9 # exclusive zero-based row, before index table; dashboard starts at row 10
    put(index_title,0,["=== INDEX ==="])
    put(index_title+1,0,["Symbol","PD High","PD Low","WEEK HIGH","WEEK LOW","LTP (Spot)","CHANGE %","DIST%","STATUS"])
    for offset,it in enumerate(sorted(index_data.values(),key=lambda x:x["change"],reverse=True),2):
        row_idx=index_title+offset
        put(row_idx,0,[it["symbol"],it["pdh"],it["pdl"],it["wh"],it["wl"],it["ltp"],f"{it['change']:.2f}%",f"{it['dist']:.1f}%",get_status(it)])
        dashboard_row_map_index[it["symbol"]]=row_idx+DASHBOARD_START_ROW
    return [row+[""]*(22-len(row)) for row in grid]

def apply_pd_colors():
    dark_green={"red":0.0,"green":0.4,"blue":0.0}
    dark_red={"red":0.5,"green":0.0,"blue":0.0}
    white={"red":1,"green":1,"blue":1}
    rules=[]
    def add_rule(formula,color,col_start,col_end,row_start,row_end):
        if row_end>row_start:
            rules.append((formula,color,col_start,col_end,row_start,row_end))

    # Top 5 signal cells.
    for col,col_index in [("D",3),("O",14)]:
        add_rule(f'=${col}3="PDH BREAK"',dark_green,col_index,col_index+1,2,7)
        add_rule(f'=${col}3="PDL BREAK"',dark_red,col_index,col_index+1,2,7)
        add_rule(f'=${col}10="PDH BREAK"',dark_green,col_index,col_index+1,9,14)
        add_rule(f'=${col}10="PDL BREAK"',dark_red,col_index,col_index+1,9,14)

    # Rank tables: positive change is green, negative change is red.
    for pct_col,option_pct_col in [("C", "F"), ("N", "Q")]:
        add_rule(f'=IFERROR(VALUE(SUBSTITUTE(${pct_col}3,"%","")),0)>0',dark_green,ord(pct_col)-ord("A"),ord(pct_col)-ord("A")+1,2,7)
        add_rule(f'=IFERROR(VALUE(SUBSTITUTE(${pct_col}3,"%","")),0)<0',dark_red,ord(pct_col)-ord("A"),ord(pct_col)-ord("A")+1,2,7)
        add_rule(f'=IFERROR(VALUE(SUBSTITUTE(${option_pct_col}3,"%","")),0)>0',dark_green,ord(option_pct_col)-ord("A"),ord(option_pct_col)-ord("A")+1,2,7)
        add_rule(f'=IFERROR(VALUE(SUBSTITUTE(${option_pct_col}3,"%","")),0)<0',dark_red,ord(option_pct_col)-ord("A"),ord(option_pct_col)-ord("A")+1,2,7)
    for pct_col in ("C", "N"):
        col_index=ord(pct_col)-ord("A")
        add_rule(f'=IFERROR(VALUE(SUBSTITUTE(${pct_col}10,"%","")),0)>0',dark_green,col_index,col_index+1,9,14)
        add_rule(f'=IFERROR(VALUE(SUBSTITUTE(${pct_col}10,"%","")),0)<0',dark_red,col_index,col_index+1,9,14)

    # Apply option colors only to actual data rows in each panel, never to
    # section titles, headers, or blank rows between CE/PE panels.
    for (otype,_status),(row_start,row_end) in option_panel_row_ranges.items():
        first_row=row_start+1
        if otype=="CE":
            ltp,pdh,pdl,week_high,week_low,status_col="G","C","D","E","F","J"
            columns={"pdh":2,"pdl":3,"week_high":4,"week_low":5,"status":9}
        else:
            ltp,pdh,pdl,week_high,week_low,status_col="R","N","O","P","Q","U"
            columns={"pdh":13,"pdl":14,"week_high":15,"week_low":16,"status":20}
        specs=[
            (f'=AND(ISNUMBER(${ltp}{first_row}),ISNUMBER(${pdh}{first_row}),${ltp}{first_row}>=${pdh}{first_row})',dark_green,columns["pdh"]),
            (f'=AND(ISNUMBER(${ltp}{first_row}),ISNUMBER(${pdl}{first_row}),${ltp}{first_row}<=${pdl}{first_row})',dark_red,columns["pdl"]),
            (f'=AND(ISNUMBER(${ltp}{first_row}),ISNUMBER(${week_high}{first_row}),${ltp}{first_row}>=${week_high}{first_row})',dark_green,columns["week_high"]),
            (f'=AND(ISNUMBER(${ltp}{first_row}),ISNUMBER(${week_low}{first_row}),${ltp}{first_row}<=${week_low}{first_row})',dark_red,columns["week_low"]),
            (f'=${status_col}{first_row}="BREAKOUT"',dark_green,columns["status"]),
            (f'=${status_col}{first_row}="BREAKDOWN"',dark_red,columns["status"]),
        ]
        for formula,color,col_start in specs:
            add_rule(formula,color,col_start,col_start+1,row_start,row_end)

    if dashboard_row_map_index:
        first_index_row=min(dashboard_row_map_index.values())
        last_index_row=max(dashboard_row_map_index.values())
        index_specs=[
            (f'=AND(ISNUMBER($F{first_index_row}),ISNUMBER($B{first_index_row}),$F{first_index_row}>=$B{first_index_row})',dark_green,1),
            (f'=AND(ISNUMBER($F{first_index_row}),ISNUMBER($C{first_index_row}),$F{first_index_row}<=$C{first_index_row})',dark_red,2),
            (f'=AND(ISNUMBER($F{first_index_row}),ISNUMBER($D{first_index_row}),$F{first_index_row}>=$D{first_index_row})',dark_green,3),
            (f'=AND(ISNUMBER($F{first_index_row}),ISNUMBER($E{first_index_row}),$F{first_index_row}<=$E{first_index_row})',dark_red,4),
            (f'=$I{first_index_row}="BREAKOUT"',dark_green,8),
            (f'=$I{first_index_row}="BREAKDOWN"',dark_red,8),
        ]
        for formula,color,col_start in index_specs:
            add_rule(formula,color,col_start,col_start+1,first_index_row-1,last_index_row)
    owned_formulas={rule[0] for rule in rules}
    # Remove rules left by the previous BREAKOUT-sheet layout. Those rules
    # referenced the old row-3 table and were duplicated on every old run,
    # causing stale colors to overlap the new dashboard.
    legacy_layout_formulas={
        '=$S3="BREAKDOWN"', '=$S3="BREAKOUT"',
        '=$R3<$Q3', '=$R3>$P3', '=$R3<$O3', '=$R3>$N3',
        '=$I3="BREAKDOWN"', '=$I3="BREAKOUT"',
        '=$H3<$G3', '=$H3>$F3', '=$H3<$E3', '=$H3>$D3',
    }
    previous_pd_formulas={
        '=AND($G12<>"",$C12<>"",$G12>=$C12)',
        '=AND($G12<>"",$D12<>"",$G12<=$D12)',
        '=AND($R12<>"",$N12<>"",$R12>=$N12)',
        '=AND($R12<>"",$O12<>"",$R12<=$O12)',
    }
    def is_owned_color_formula(value):
        # Panel titles can move when the number of breakout rows changes, so
        # match our formula shapes as well as this run's exact formulas.
        if value in owned_formulas or value in legacy_layout_formulas or value in previous_pd_formulas:
            return True
        if re.fullmatch(r'=\$(?:D|O)\d+="PD[HL] BREAK"', value):
            return True
        if re.fullmatch(r'=\$(?:J|U|I|S)\d+="BREAK(?:OUT|DOWN)"', value):
            return True
        if re.match(r'^=AND\(ISNUMBER\(\$(?:G|R|F)\d+\)', value):
            return True
        if re.match(r'^=AND\(\$(?:G|R)\d+<>""', value):
            return True
        if (value.startswith('=AND($F') and (">=$B" in value or "<=$C" in value)):
            return True
        return False
    requests_list=[]
    try:
        metadata=sheet2.spreadsheet.fetch_sheet_metadata(params={"includeGridData":False,"fields":"sheets(properties(sheetId),conditionalFormats)"})
        existing=[]
        for ws in metadata.get("sheets",[]):
            if ws.get("properties",{}).get("sheetId")==sheet2.id:
                existing=ws.get("conditionalFormats",[])
                break
        old_indices=[]
        for idx,rule in enumerate(existing):
            condition=rule.get("booleanRule",{}).get("condition",{})
            values={v.get("userEnteredValue","") for v in condition.get("values",[])}
            if any(is_owned_color_formula(value) for value in values):
                old_indices.append(idx)
        requests_list.extend({"deleteConditionalFormatRule":{"sheetId":sheet2.id,"index":idx}} for idx in reversed(old_indices))
    except Exception as exc:
        print(f"PD COLOR RULE READ ERR {exc}")
    for formula,color,col_start,col_end,row_start,row_end in rules:
        requests_list.append({"addConditionalFormatRule":{"rule":{"ranges":[{"sheetId":sheet2.id,"startRowIndex":row_start,"endRowIndex":row_end,"startColumnIndex":col_start,"endColumnIndex":col_end}],"booleanRule":{"condition":{"type":"CUSTOM_FORMULA","values":[{"userEnteredValue":formula}]},"format":{"backgroundColor":color,"textFormat":{"foregroundColor":white,"bold":True}}}},"index":0}})
    try:
        sheet2.spreadsheet.batch_update({"requests":requests_list})
    except Exception as exc:
        print(f"PD COLOR APPLY ERR {exc}")

def apply_main_index_colors():
    if not row_map_index: return
    first_row=min(row_map_index.values()); last_row=max(row_map_index.values())
    dark_green={"red":0.0,"green":0.4,"blue":0.0}
    dark_red={"red":0.5,"green":0.0,"blue":0.0}
    white={"red":1,"green":1,"blue":1}
    formulas=[
        f'=AND(ISNUMBER($F{first_row}),ISNUMBER($B{first_row}),$F{first_row}>=$B{first_row})',
        f'=AND(ISNUMBER($F{first_row}),ISNUMBER($C{first_row}),$F{first_row}<=$C{first_row})',
        f'=AND(ISNUMBER($F{first_row}),ISNUMBER($D{first_row}),$F{first_row}>=$D{first_row})',
        f'=AND(ISNUMBER($F{first_row}),ISNUMBER($E{first_row}),$F{first_row}<=$E{first_row})',
        f'=$I{first_row}="BREAKOUT"',
        f'=$I{first_row}="BREAKDOWN"',
    ]
    requests_list=[]
    try:
        metadata=sheet.spreadsheet.fetch_sheet_metadata(params={"includeGridData":False,"fields":"sheets(properties(sheetId),conditionalFormats)"})
        rules=[]
        for ws in metadata.get("sheets",[]):
            if ws.get("properties",{}).get("sheetId")==sheet.id:
                rules=ws.get("conditionalFormats",[]); break
        old_indices=[]
        for idx,rule in enumerate(rules):
            condition=rule.get("booleanRule",{}).get("condition",{})
            vals={v.get("userEnteredValue","") for v in condition.get("values",[])}
            ranges=rule.get("ranges",[])
            overlaps_index=any(rg.get("startRowIndex",0)<last_row and rg.get("endRowIndex",0)>first_row-1 for rg in ranges)
            index_columns={rg.get("startColumnIndex") for rg in ranges}
            old_main_index_rule=overlaps_index and (
                (bool(index_columns & {1,2,3,4}) and any("$F" in v for v in vals))
                or (8 in index_columns and (bool(vals & {"BREAKOUT","BREAKDOWN"}) or any("$I" in v for v in vals)))
            )
            if old_main_index_rule or vals & set(formulas):
                old_indices.append(idx)
        requests_list.extend({"deleteConditionalFormatRule":{"sheetId":sheet.id,"index":idx}} for idx in reversed(old_indices))
    except Exception as exc:
        print(f"MAIN INDEX COLOR RULE READ ERR {exc}")
    color_specs=[(formulas[0],dark_green,1),(formulas[1],dark_red,2),(formulas[2],dark_green,3),(formulas[3],dark_red,4),(formulas[4],dark_green,8),(formulas[5],dark_red,8)]
    for formula,color,col_start in color_specs:
        requests_list.append({"addConditionalFormatRule":{"rule":{"ranges":[{"sheetId":sheet.id,"startRowIndex":first_row-1,"endRowIndex":last_row,"startColumnIndex":col_start,"endColumnIndex":col_start+1}],"booleanRule":{"condition":{"type":"CUSTOM_FORMULA","values":[{"userEnteredValue":formula}]},"format":{"backgroundColor":color,"textFormat":{"foregroundColor":white,"bold":True}}}},"index":0}})
    try: sheet.spreadsheet.batch_update({"requests":requests_list})
    except Exception as exc: print(f"MAIN INDEX COLOR APPLY ERR {exc}")

dashboard_last_end_row=0

def write_dashboard(clear_all=False):
    """Refresh the BREAKOUT dashboard in place to avoid a visible blank flash."""
    global dashboard_last_end_row
    summary=build_top_summary()
    rows=build_dashboard()
    end_row=DASHBOARD_START_ROW-1+len(rows)
    title=sheet2.title.replace("'", "''")
    if clear_all:
        # Clear old content only once when the bot initializes the dashboard.
        sheet2.batch_clear(["A1:V5000"])
    updates=[
        {"range":f"'{title}'!A1:V14","values":summary},
    ]
    if rows:
        updates.append({"range":f"'{title}'!A{DASHBOARD_START_ROW}:V{end_row}","values":rows})
    # Write summary and dashboard together. On live status changes, existing
    # cells stay visible while values are replaced; only a stale tail is cleared.
    sh.values_batch_update({"valueInputOption":"RAW","data":updates})
    if not clear_all and dashboard_last_end_row and end_row<dashboard_last_end_row:
        sheet2.batch_clear([f"A{end_row+1}:V{dashboard_last_end_row}"])
    dashboard_last_end_row=end_row
    apply_pd_colors()

print("Writing main sheet and Sheet 2 dashboard...")
try:
    main_rows=build_rows()
    sheet.batch_clear(["A4:Z5000"])
    sheet.update(values=main_rows,range_name="A4")
    sheet.update(values=[[datetime.now().strftime("%H:%M:%S")]],range_name="C3")
    apply_main_index_colors()
    write_dashboard(clear_all=True)
except Exception as e:
    print(f"SHEET WRITE ERR {e}")
    raise
print(f"DONE - Index {len(index_data)} Option {len(option_data)}")

import upstox_client
configuration = upstox_client.Configuration(); configuration.access_token = UPSTOX_ACCESS_TOKEN
api_client = upstox_client.ApiClient(configuration)
all_keys = index_keys + stock_keys + option_keys
if not all_keys:
    print("NO KEYS"); raise SystemExit(1)

streamer = upstox_client.MarketDataStreamerV3(api_client=api_client, instrumentKeys=all_keys, mode="full")
pending={}; lock=threading.Lock()
dashboard_signature=[tuple(sorted((v["underlying"],v["otype"],get_status(v)) for v in option_data.values()))]

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
    initial_alert_scan=[True]
    while True:
        time.sleep(5)
        telegram_events=[]
        with lock:
            if initial_alert_scan[0]:
                previous_index_status={k:"" for k in index_data}
                previous_option_status={k:"" for k in option_data}
                initial_alert_scan[0]=False
            else:
                previous_index_status={k:get_status(v) for k,v in index_data.items()}
                previous_option_status={k:get_status(v) for k,v in option_data.items()}
            for ikey,ltp in list(pending.items()):
                if ikey in stock_underlying_data:
                    stock=stock_underlying_data[ikey]
                    stock["ltp"]=float(ltp)
                    stock["change"]=(stock["ltp"]-stock["prev_close"])/stock["prev_close"]*100 if stock.get("prev_close",0)>0 else 0
                if ikey in option_data:
                    it=option_data[ikey]; old_status=get_status(it); prev=it["prev_close"]
                    it["ltp"]=float(ltp)
                    if prev>0: it["change"]=(ltp-prev)/prev*100
                    if it["wh"]>0: it["dist"]=ltp/it["wh"]*100
                    update_break_time(it,old_status)
                if ikey in index_data:
                    it=index_data[ikey]; prev=it["prev_close"]
                    it["ltp"]=float(ltp)
                    if prev>0: it["change"]=(ltp-prev)/prev*100
                    if it["wh"]>0: it["dist"]=ltp/it["wh"]*100
            pending.clear()
            if loop % 5 == 0:
                try:
                    status_before={k:get_status(v) for k,v in option_data.items()}
                    fetch_ltp(option_keys,option_data)
                    for k,it in option_data.items(): update_break_time(it,status_before.get(k,""))
                    fetch_ltp(index_keys,index_data)
                except: pass
            current_signature=tuple(sorted((v["underlying"],v["otype"],get_status(v)) for v in option_data.values()))
            if current_signature!=dashboard_signature[0]:
                try:
                    write_dashboard()
                    dashboard_signature[0]=current_signature
                except Exception as e: print(f"DASHBOARD REFRESH ERR {e}")
            for ikey,it in index_data.items():
                status=get_status(it)
                if status in ("BREAKOUT","BREAKDOWN") and status!=previous_index_status.get(ikey,"") and it.get("ltp",0)>0:
                    icon="🟢" if status=="BREAKOUT" else "🔴"
                    label=f"INDEX {it['symbol']}"
                    telegram_events.append(f"{icon} <b>{html.escape(label)}</b> — {status} | LTP ₹{it['ltp']:.2f} | {it.get('change',0):+.2f}%")
            for ikey,it in option_data.items():
                status=get_status(it)
                if status in ("BREAKOUT","BREAKDOWN") and status!=previous_option_status.get(ikey,"") and it.get("ltp",0)>0:
                    icon="🟢" if status=="BREAKOUT" else "🔴"
                    label=f"{it['underlying']} {it['otype']} {it['strike']}"
                    telegram_events.append(f"{icon} <b>{html.escape(label)}</b> — {status} | LTP ₹{it['ltp']:.2f} | {it.get('change',0):+.2f}%")
            main_batch=[{"range":"C3","values":[[datetime.now().strftime("%H:%M:%S")]]}]
            for sym,rnum in row_map_index.items():
                for it in index_data.values():
                    if it["symbol"]==sym and it["ltp"]>0:
                        main_batch.append({"range":f"B{rnum}:I{rnum}","values":[[it["pdh"],it["pdl"],it["wh"],it["wl"],it["ltp"],f"{it['change']:.2f}%",f"{it['dist']:.1f}%",get_status(it)]]})
                        break
            for stock,rnum in row_map_stock.items():
                ce=next((v for v in option_data.values() if v["underlying"]==stock and v["otype"]=="CE"),None)
                pe=next((v for v in option_data.values() if v["underlying"]==stock and v["otype"]=="PE"),None)
                if ce and ce["ltp"]>0: main_batch.append({"range":f"G{rnum}:J{rnum}","values":[[ce["ltp"],f"{ce['change']:.2f}%",f"{ce['dist']:.1f}%",get_status(ce)]]})
                if pe and pe["ltp"]>0: main_batch.append({"range":f"R{rnum}:U{rnum}","values":[[pe["ltp"],f"{pe['change']:.2f}%",f"{pe['dist']:.1f}%",get_status(pe)]]})
            dash_batch=[{"range":"A1:V14","values":build_top_summary()}]
            for sym,rnum in dashboard_row_map_index.items():
                for it in index_data.values():
                    if it["symbol"]==sym and it["ltp"]>0:
                        dash_batch.append({"range":f"B{rnum}:I{rnum}","values":[[it["pdh"],it["pdl"],it["wh"],it["wl"],it["ltp"],f"{it['change']:.2f}%",f"{it['dist']:.1f}%",get_status(it)]]})
                        break
            for (stock,otype),rnum in row_map_option.items():
                it=next((v for v in option_data.values() if v["underlying"]==stock and v["otype"]==otype),None)
                if it and it["ltp"]>0:
                    start_col="G" if otype=="CE" else "R"; end_col="K" if otype=="CE" else "V"
                    dash_batch.append({"range":f"{start_col}{rnum}:{end_col}{rnum}","values":[[it["ltp"],f"{it['change']:.2f}%",f"{it['dist']:.1f}%",get_status(it),it.get("break_time","")]]})
            def spreadsheet_ranges(worksheet, updates):
                title=worksheet.title.replace("'", "''")
                return [{"range":f"'{title}'!{item['range']}","values":item["values"]} for item in updates]
            try:
                combined_updates=spreadsheet_ranges(sheet,main_batch)+spreadsheet_ranges(sheet2,dash_batch)
                sh.values_batch_update({"valueInputOption":"RAW","data":combined_updates})
            except Exception as e:
                print(f"Google Sheet update error: {e}")
        if telegram_events:
            send_breakout_telegram_alerts(telegram_events)
        loop+=1

threading.Thread(target=updater, daemon=True).start()
while True: time.sleep(1)
