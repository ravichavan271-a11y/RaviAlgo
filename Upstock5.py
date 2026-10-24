import gzip, io, time, threading, os, re
from datetime import datetime, timedelta
import pytz
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd
import requests, urllib.parse
from concurrent.futures import ThreadPoolExecutor
import upstox_client

print("FINAL V80 - WORKING LIVE + PD FIX")

IST = pytz.timezone('Asia/Kolkata')
SPREADSHEET_NAME = os.environ.get("SPREADSHEET_NAME", "Dsheet")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

ALERTED = set()

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID: return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
        requests.post(url, json=payload, timeout=10)
    except: pass

def get_gspread_client():
    scope = ["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
    import json as js
    env_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "") or os.environ.get("SERVICE_ACCOUNT_JSON", "")
    if env_json:
        try: return gspread.authorize(ServiceAccountCredentials.from_json_keyfile_dict(js.loads(env_json), scope))
        except: pass
    for p in ["service_account.json", "./service_account.json", "/app/service_account.json"]:
        if os.path.exists(p):
            try: return gspread.authorize(ServiceAccountCredentials.from_json_keyfile_name(p, scope))
            except: pass
    raise Exception("GOOGLE_SERVICE_ACCOUNT_JSON missing")

def get_token():
    tok = os.environ.get("UPSTOX_ACCESS_TOKEN", "") or os.environ.get("UPSTOX_TOKEN", "")
    if tok and len(tok)>50: return tok
    try:
        gc_temp=get_gspread_client()
        sh=gc_temp.open(SPREADSHEET_NAME)
        b1=str(sh.sheet1.cell(1,2).value or "").strip()
        if len(b1)>50: return b1
    except: pass
    raise Exception("UPSTOX_ACCESS_TOKEN missing")

UPSTOX_ACCESS_TOKEN = get_token()

def is_market_time():
    now = datetime.now(IST)
    if now.weekday() >= 5: return False
    if 9 <= now.hour < 15: return True
    if now.hour == 15 and now.minute <= 45: return True
    return False

def wait_until_market():
    while True:
        now = datetime.now(IST)
        if now.weekday() >= 5:
            next_monday = now + timedelta(days=(7 - now.weekday()))
            next_monday = next_monday.replace(hour=9, minute=0, second=0, microsecond=0)
            sleep_sec = (next_monday - now).total_seconds()
            print(f"📅 WEEKEND {now.strftime('%A')} - Sleeping {int(sleep_sec/3600)}h")
            time.sleep(min(sleep_sec, 3600))
            continue
        if is_market_time():
            print(f"✅ {now.strftime('%A')} Market Time {now.strftime('%H:%M:%S IST')} - Starting...")
            ALERTED.clear()
            return True
        time.sleep(60)

print("Downloading instruments...")
r = requests.get("https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz", timeout=30)
with gzip.GzipFile(fileobj=io.BytesIO(r.content)) as gz:
    df = pd.read_csv(gz)
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
mp["HINDZINC"]="NSE_EQ|INE267A01025"; mp["HINDCOPPER"]="NSE_EQ|INE531E01026"
for _, row in df.iterrows():
    if str(row.get("tradingsymbol","")).strip() in ["MCX","MCX-EQ"] and "NSE_EQ" in str(row.get("instrument_key","")):
        mp["MCX"]=str(row.get("instrument_key","")).strip(); break
if "MCX" not in mp: mp["MCX"]="NSE_EQ|11536"
if "ZYDUSLIFE" in mp: mp["ZYDU"]=mp["ZYDUSLIFE"]

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
    "MOST LIQUID STOCKS": ["MARUTI","TRENT","POLYCAB","DIXON","BAJAJ-AUTO","PERSISTENT","BSE","INDIGO","BOSCHLTD","OFSS","ABB","SOLARINDS","POWERINDIA"]
}

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

def parse_date(val):
    if not val: return ""
    s = str(val).strip().split()[0].replace("/", "-").replace(".", "-")
    s = re.sub(r'[^0-9\-]', '', s)
    for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
        try: return datetime.strptime(s[:10], fmt).strftime("%Y-%m-%d")
        except: pass
    return s[:10]

def build_sorted(instrument_data):
    rows=[]; rows.append(["Symbol","PD High","PD Low","WEEK HIGH","WEEK LOW","LTP","CHANGE %","VOLUME","PREV VOL","VOL X","DIST%","STATUS","BREAK TIME","LIVE TIME"])
    indices=[it for it in instrument_data.values() if it.get("is_index")]; indices.sort(key=lambda x: x["change"], reverse=True)
    for it in indices:
        dist=it["ltp"]/it["wh"]*100 if it["wh"]>0 else 0; volx=it["vol"]/it["prev_vol"] if it["prev_vol"]>0 else 0
        st=get_status(it)
        if st and not it["break_time"]: it["break_time"]=datetime.now(IST).strftime("%H:%M:%S")
        rows.append([it["symbol"],it["pdh"],it["pdl"],it["wh"],it["wl"],it["ltp"],f"{it['change']:.2f}%",it["vol"],it["prev_vol"],f"{volx:.1f}X",f"{dist:.1f}%",st,it["break_time"],datetime.now(IST).strftime("%H:%M:%S")])
    rows.append([])
    for sec_name in STRUCTURE.keys():
        rows.append([sec_name])
        stocks=[instrument_data[mp.get(sym)] for sym in STRUCTURE[sec_name] if mp.get(sym) in instrument_data]; stocks.sort(key=lambda x: x["change"], reverse=True)
        for it in stocks:
            dist=it["ltp"]/it["wh"]*100 if it["wh"]>0 else 0; volx=it["vol"]/it["prev_vol"] if it["prev_vol"]>0 else 0
            st=get_status(it)
            if st and not it["break_time"]: it["break_time"]=datetime.now(IST).strftime("%H:%M:%S")
            rows.append([it["symbol"],it["pdh"],it["pdl"],it["wh"],it["wl"],it["ltp"],f"{it['change']:.2f}%",it["vol"],it["prev_vol"],f"{volx:.1f}X",f"{dist:.1f}%",st,it["break_time"],datetime.now(IST).strftime("%H:%M:%S")])
        rows.append([])
    return rows

def build_breakout(instrument_data):
    rows=[["Symbol","PD High","PD Low","WEEK HIGH","WEEK LOW","LTP","CHANGE %","VOLUME","PREV VOL","VOL X","DIST%","STATUS","BREAK TIME","LIVE TIME","PARENT INDEX"]]
    has=False
    for sec_name in STRUCTURE.keys():
        if sec_name=="MOST LIQUID STOCKS": continue
        ikey=mp.get(sec_name)
        if not ikey or ikey not in instrument_data: continue
        it=instrument_data[ikey]; st=get_status(it)
        if st in ["BREAKOUT","BREAKDOWN"]:
            has=True; dist=it["ltp"]/it["wh"]*100 if it["wh"]>0 else 0; volx=it["vol"]/it["prev_vol"] if it["prev_vol"]>0 else 0
            rows.append([it["symbol"],it["pdh"],it["pdl"],it["wh"],it["wl"],it["ltp"],f"{it['change']:.2f}%",it["vol"],it["prev_vol"],f"{volx:.1f}X",f"{dist:.1f}%",st,it["break_time"],datetime.now(IST).strftime("%H:%M:%S"),"INDEX"])
            rows.append([f"--- {sec_name} MADHLE {st} STOCKS ---"])
            sl=[instrument_data[mp.get(sym)] for sym in STRUCTURE[sec_name] if mp.get(sym) in instrument_data and get_status(instrument_data[mp.get(sym)])==st]
            sl.sort(key=lambda x: x["change"], reverse=True)
            for s in sl:
                dist=s["ltp"]/s["wh"]*100 if s["wh"]>0 else 0; volx=s["vol"]/s["prev_vol"] if s["prev_vol"]>0 else 0
                rows.append([s["symbol"],s["pdh"],s["pdl"],s["wh"],s["wl"],s["ltp"],f"{s['change']:.2f}%",s["vol"],s["prev_vol"],f"{volx:.1f}X",f"{dist:.1f}%",get_status(s),s["break_time"],datetime.now(IST).strftime("%H:%M:%S"),sec_name])
            rows.append([])
    if not has: rows.append([]); rows.append(["SADHYA KUTLACH INDEX BREAKOUT/BREAKDOWN NAHI"])
    return rows

def apply_dark_colours(sheet):
    DARK_GREEN = {"red": 0.0, "green": 0.4, "blue": 0.0}
    DARK_RED = {"red": 0.5, "green": 0.0, "blue": 0.0}
    LIGHT_GREEN_VOL = {"red": 0.6, "green": 0.85, "blue": 0.6}
    YELLOW_VOL = {"red": 1.0, "green": 1.0, "blue": 0.5}
    WHITE_TEXT = {"red": 1, "green": 1, "blue": 1}
    BLACK_TEXT = {"red": 0, "green": 0, "blue": 0}
    HEADER_BG = {"red": 0.12, "green": 0.2, "blue": 0.6}
    try:
        del_req = [{"deleteConditionalFormatRule": {"sheetId": sheet.id, "index": 0}} for _ in range(60)]
        sheet.spreadsheet.batch_update({"requests": del_req})
    except: pass
    time.sleep(0.5)
    requests_list = [
        {"addConditionalFormatRule": {"rule": {"ranges": [{"sheetId": sheet.id, "startRowIndex": 4, "endRowIndex": 2000, "startColumnIndex": 1, "endColumnIndex": 2}], "booleanRule": {"condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": "=AND($F5<>\"\",$B5<>\"\",$F5>=$B5)"}]}, "format": {"backgroundColor": DARK_GREEN, "textFormat": {"foregroundColor": WHITE_TEXT, "bold": True}}}}, "index": 0}},
        {"addConditionalFormatRule": {"rule": {"ranges": [{"sheetId": sheet.id, "startRowIndex": 4, "endRowIndex": 2000, "startColumnIndex": 2, "endColumnIndex": 3}], "booleanRule": {"condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": "=AND($F5<>\"\",$C5<>\"\",$F5<=$C5)"}]}, "format": {"backgroundColor": DARK_RED, "textFormat": {"foregroundColor": WHITE_TEXT, "bold": True}}}}, "index": 0}},
        {"addConditionalFormatRule": {"rule": {"ranges": [{"sheetId": sheet.id, "startRowIndex": 4, "endRowIndex": 2000, "startColumnIndex": 3, "endColumnIndex": 4}], "booleanRule": {"condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": "=AND($F5<>\"\",$D5<>\"\",$F5>=$D5)"}]}, "format": {"backgroundColor": DARK_GREEN, "textFormat": {"foregroundColor": WHITE_TEXT, "bold": True}}}}, "index": 0}},
        {"addConditionalFormatRule": {"rule": {"ranges": [{"sheetId": sheet.id, "startRowIndex": 4, "endRowIndex": 2000, "startColumnIndex": 4, "endColumnIndex": 5}], "booleanRule": {"condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": "=AND($F5<>\"\",$E5<>\"\",$F5<=$E5)"}]}, "format": {"backgroundColor": DARK_RED, "textFormat": {"foregroundColor": WHITE_TEXT, "bold": True}}}}, "index": 0}},
        {"addConditionalFormatRule": {"rule": {"ranges": [{"sheetId": sheet.id, "startRowIndex": 4, "endRowIndex": 2000, "startColumnIndex": 11, "endColumnIndex": 12}], "booleanRule": {"condition": {"type": "TEXT_CONTAINS", "values": [{"userEnteredValue": "BREAKOUT"}]}, "format": {"backgroundColor": DARK_GREEN, "textFormat": {"foregroundColor": WHITE_TEXT, "bold": True}}}}, "index": 0}},
        {"addConditionalFormatRule": {"rule": {"ranges": [{"sheetId": sheet.id, "startRowIndex": 4, "endRowIndex": 2000, "startColumnIndex": 11, "endColumnIndex": 12}], "booleanRule": {"condition": {"type": "TEXT_CONTAINS", "values": [{"userEnteredValue": "BREAKDOWN"}]}, "format": {"backgroundColor": DARK_RED, "textFormat": {"foregroundColor": WHITE_TEXT, "bold": True}}}}, "index": 0}},
        {"addConditionalFormatRule": {"rule": {"ranges": [{"sheetId": sheet.id, "startRowIndex": 4, "endRowIndex": 2000, "startColumnIndex": 9, "endColumnIndex": 10}], "booleanRule": {"condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": "=IFERROR(VALUE(SUBSTITUTE($J5,\"X\",\"\"))>=3, FALSE)"}]}, "format": {"backgroundColor": DARK_GREEN, "textFormat": {"foregroundColor": WHITE_TEXT, "bold": True}}}}, "index": 0}},
        {"addConditionalFormatRule": {"rule": {"ranges": [{"sheetId": sheet.id, "startRowIndex": 4, "endRowIndex": 2000, "startColumnIndex": 9, "endColumnIndex": 10}], "booleanRule": {"condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": "=IFERROR(VALUE(SUBSTITUTE($J5,\"X\",\"\"))>=2, FALSE)"}]}, "format": {"backgroundColor": LIGHT_GREEN_VOL, "textFormat": {"foregroundColor": BLACK_TEXT, "bold": True}}}}, "index": 0}},
        {"addConditionalFormatRule": {"rule": {"ranges": [{"sheetId": sheet.id, "startRowIndex": 4, "endRowIndex": 2000, "startColumnIndex": 9, "endColumnIndex": 10}], "booleanRule": {"condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": "=IFERROR(VALUE(SUBSTITUTE($J5,\"X\",\"\"))>=1, FALSE)"}]}, "format": {"backgroundColor": YELLOW_VOL, "textFormat": {"foregroundColor": BLACK_TEXT, "bold": False}}}}, "index": 0}},
    ]
    try:
        sheet.spreadsheet.batch_update({"requests": requests_list})
        sheet.format("A4:N4", {"backgroundColor": HEADER_BG, "textFormat": {"foregroundColor": WHITE_TEXT, "bold": True}})
    except Exception as e:
        print(f"Colour Error {sheet.title}: {e}")

def fetch_ltp_v3(keys, instrument_data):
    if not keys: return 0
    got=0
    for i in range(0, len(keys), 50):
        batch = keys[i:i+50]
        try:
            ik_str = ",".join(batch)
            encoded = urllib.parse.quote(ik_str, safe=',')
            url = f"https://api.upstox.com/v3/market-quote/ltp?instrument_key={encoded}"
            r = requests.get(url, headers={"Authorization": f"Bearer {UPSTOX_ACCESS_TOKEN}", "Accept": "application/json"}, timeout=10)
            if r.status_code==200:
                js = r.json()
                data=js.get("data",{})
                for data_key, d in data.items():
                    token = d.get('instrument_token')
                    ltp = d.get('last_price')
                    cp = d.get('cp')
                    vol = d.get('volume')
                    target_key = None
                    if token and token in instrument_data:
                        target_key = token
                    if not target_key and data_key in instrument_data:
                        target_key = data_key
                    if not target_key:
                        sym = data_key.split(":")[-1] if ":" in data_key else data_key
                        if sym in mp and mp[sym] in instrument_data:
                            target_key = mp[sym]
                    if target_key:
                        if ltp and float(ltp)>0:
                            instrument_data[target_key]["ltp"]=float(ltp)
                            got+=1
                        if cp and float(cp)>0:
                            instrument_data[target_key]["prev_close"]=float(cp)
                            if ltp:
                                try: instrument_data[target_key]["change"]=(float(ltp)-float(cp))/float(cp)*100
                                except: pass
                        if vol is not None:
                            try: instrument_data[target_key]["vol"]=int(float(vol))
                            except: pass
        except Exception as e:
            print(f"REST LTP err {e}")
        time.sleep(0.2)
    return got

def smooth_update_sheet(sheet, rows):
    try:
        end_row = 4 + len(rows)
        sheet.update(values=rows, range_name=f"A4:N{end_row}", value_input_option="RAW")
        return True
    except Exception as e:
        print(f"Smooth update err {e}")
        try:
            sheet.update(values=rows, range_name="A4")
            return True
        except:
            return False

def run_once():
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
    gc = get_gspread_client()
    sh = gc.open(SPREADSHEET_NAME)
    sheet = sh.sheet1
    try: breakout_sheet = sh.worksheet("BREAKOUT")
    except: breakout_sheet = sh.add_worksheet(title="BREAKOUT", rows="2000", cols="20")
    b2_val = sheet.cell(2,2).value
    b3_val = sheet.cell(3,2).value
    weekly_from = parse_date(b2_val)
    weekly_to = parse_date(b3_val)
    print(f"Weekly {weekly_from} to {weekly_to}")

    # === V80 PD FIX - AAJ + KAL BAND TAR PARVA ===
    def get_previous_trading_day(from_date_str):
        sample_key = mp.get("NIFTY 50")
        d = datetime.strptime(from_date_str, "%Y-%m-%d")
        for _ in range(15):
            d = d - timedelta(days=1)
            if d.weekday() >= 5: continue
            s = d.strftime("%Y-%m-%d")
            _, candles = get_candle(sample_key, s, s)
            if candles:
                return s
            print(f"❌ {s} sutti, parva check...")
        return from_date_str

    def find_real_trading_days():
        today = datetime.now(IST)
        while today.weekday() >= 5:
            today = today - timedelta(days=1)
        ltp_day = today.strftime("%Y-%m-%d")
        pd_day = get_previous_trading_day(ltp_day)
        print(f"✅ FINAL -> LTP={ltp_day} (AAJ) | PD={pd_day} (KAL BAND TAR PARVA)")
        return ltp_day, pd_day

    final_ltp_day, final_pd_day = find_real_trading_days()

    with ThreadPoolExecutor(max_workers=10) as ex:
        for k,candles in ex.map(lambda kk: get_candle(kk, weekly_from, weekly_to), all_keys):
            if candles:
                d=pd.DataFrame(candles,columns=["datetime","open","high","low","close","volume","oi"])
                d["high"]=pd.to_numeric(d["high"], errors='coerce'); d["low"]=pd.to_numeric(d["low"], errors='coerce')
                instrument_data[k]["wh"]=float(d["high"].max()); instrument_data[k]["wl"]=float(d["low"].min())
                instrument_data[k]["prev_close"]=float(d.iloc[0]["close"]) if len(d)>0 else 0

    with ThreadPoolExecutor(max_workers=10) as ex:
        for k,candles in ex.map(lambda kk: get_candle(kk, final_pd_day, final_pd_day), all_keys):
            if candles:
                instrument_data[k]["pdh"]=float(candles[0][2]); instrument_data[k]["pdl"]=float(candles[0][3])
                instrument_data[k]["prev_vol"]=int(candles[0][5])
                if instrument_data[k]["prev_close"]==0: instrument_data[k]["prev_close"]=float(candles[0][4])

    for i in range(0, len(all_keys), 50):
        fetch_ltp_v3(all_keys[i:i+50], instrument_data); time.sleep(0.5)

    rem = [k for k,v in instrument_data.items() if v["ltp"]==0]
    if rem:
        with ThreadPoolExecutor(max_workers=10) as ex:
            for k,candles in ex.map(lambda kk: get_candle(kk, final_ltp_day, final_ltp_day), rem):
                if candles: instrument_data[k]["ltp"]=float(candles[0][4]); instrument_data[k]["vol"]=int(candles[0][5])

    for k,v in instrument_data.items():
        if v["ltp"]==0 and v["prev_close"]>0: v["ltp"]=v["prev_close"]
        if v["wh"]==0: v["wh"]=v["ltp"]
        if v["wl"]==0: v["wl"]=v["ltp"]
        if v["pdh"]==0: v["pdh"]=v["ltp"]
        if v["pdl"]==0: v["pdl"]=v["ltp"]
        if v["change"]==0 and v["prev_close"]>0: v["change"]=(v["ltp"]-v["prev_close"])/v["prev_close"]*100

    smooth_update_sheet(sheet, build_sorted(instrument_data))
    try:
        breakout_sheet.clear()
        breakout_sheet.update(values=build_breakout(instrument_data), range_name="A1")
    except: pass
    apply_dark_colours(sheet)
    try: apply_dark_colours(breakout_sheet)
    except: pass
    print(f"✅ Sheet Updated {datetime.now(IST).strftime('%H:%M:%S')} - SMOOTH No Blink")
    return instrument_data, all_keys, sh, sheet, breakout_sheet

if __name__ == "__main__":
    print("V80 READY - WORKING + PD FIX")
    send_telegram("✅ <b>V80 READY - WORKING LIVE + PD FIX</b>\nKal band asel tar Parva")
    while True:
        try:
            wait_until_market()
            instrument_data, all_keys, sh, sheet, breakout_sheet = run_once()
            print("🔴 LIVE STARTED - MON-FRI 9 AM to 3:40 PM + TELEGRAM + REST FALLBACK")
            
            state = {"ws_connected": False, "pending": {}, "streamer": None}
            lock = threading.Lock()
            
            def start_websocket_thread():
                try:
                    configuration = upstox_client.Configuration()
                    configuration.access_token = UPSTOX_ACCESS_TOKEN
                    api_client = upstox_client.ApiClient(configuration)
                    streamer_keys = all_keys[:90]
                    try:
                        from upstox_client.websocket.market_data_streamer_v3 import MarketDataStreamerV3 as StreamerV3
                        streamer = StreamerV3(api_client=api_client, instrumentKeys=streamer_keys, mode="ltpc")
                    except:
                        streamer = upstox_client.MarketDataStreamerV3(api_client=api_client, instrumentKeys=streamer_keys, mode="ltpc")
                    state["streamer"] = streamer
                    
                    def on_message(msg):
                        state["ws_connected"] = True
                        for ikey, feed in msg.get("feeds",{}).items():
                            if ikey not in instrument_data: continue
                            ltp=None
                            try:
                                if "ltpc" in feed: ltp=feed["ltpc"].get("ltp")
                                if not ltp and "fullFeed" in feed:
                                    ff=feed["fullFeed"]
                                    if "marketFF" in ff: ltp=ff["marketFF"].get("ltpc",{}).get("ltp")
                                    elif "indexFF" in ff: ltp=ff["indexFF"].get("ltpc",{}).get("ltp")
                                with lock:
                                    if ltp: 
                                        instrument_data[ikey]["ltp"]=float(ltp)
                                        state["pending"][ikey]=float(ltp)
                            except: pass
                    
                    def on_open():
                        state["ws_connected"] = True
                        print("✅ WebSocket OPEN - Live via WebSocket")
                    def on_error(e):
                        state["ws_connected"] = False
                        print(f"⚠ WS Err {e} - Switching to REST fallback")
                    def on_close(code, reason):
                        state["ws_connected"] = False
                        print(f"WS Closed {code} - REST fallback active")
                    
                    streamer.on("open", on_open)
                    streamer.on("message", on_message)
                    streamer.on("error", on_error)
                    streamer.on("close", on_close)
                    try:
                        streamer.on("reconnecting", lambda *a, **k: print("Reconnecting..."))
                        streamer.auto_reconnect(True, 5, 20)
                    except: pass
                    streamer.connect()
                except Exception as e:
                    print(f"WebSocket thread failed {e} - REST only will work")
                    state["ws_connected"] = False
            
            ws_thread = threading.Thread(target=start_websocket_thread, daemon=True)
            ws_thread.start()
            
            last=time.time()
            last_rest_fetch = time.time()
            rest_count = 0
            
            while is_market_time():
                time.sleep(1)
                now = time.time()
                
                if now - last >= 5:
                    with lock:
                        for k, ltp in list(state["pending"].items()):
                            pc=instrument_data[k]["prev_close"]
                            instrument_data[k]["change"]=(ltp-pc)/pc*100 if pc>0 else 0
                        state["pending"].clear()
                        
                        if not state["ws_connected"] or (now - last_rest_fetch >= 10):
                            print(f"🔄 REST Fallback fetching LTP... (WS Connected: {state['ws_connected']})")
                            try:
                                got = fetch_ltp_v3(all_keys, instrument_data)
                                print(f"  REST got {got} LTPs - Price change honar!")
                                last_rest_fetch = now
                                rest_count += 1
                            except Exception as e:
                                print(f"REST err {e}")
                        
                        try:
                            smooth_update_sheet(sheet, build_sorted(instrument_data))
                            try:
                                breakout_sheet.clear()
                                breakout_sheet.update(values=build_breakout(instrument_data), range_name="A1")
                            except: pass
                            status = "WS" if state["ws_connected"] else "REST"
                            print(f"🔄 Updated {datetime.now(IST).strftime('%H:%M:%S')} via {status} - Smooth No Blink")
                        except Exception as e:
                            print(f"Update err {e}")
                    last = now
            
            print("Market closed or Weekend - Going to sleep till next MON-FRI 9 AM")
            send_telegram(f"😴 <b>Market Closed</b>\n📅 {datetime.now(IST).strftime('%A %d %b %H:%M IST')}\nNext run: Next MON-FRI 9 AM\nREST Fallbacks: {rest_count}")
            time.sleep(60)
            
        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback; traceback.print_exc()
            time.sleep(60)

