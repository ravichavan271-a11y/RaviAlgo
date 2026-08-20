import pyotp, time, threading, pandas as pd, json, os
from datetime import datetime, timedelta
from pytz import timezone
from SmartApi import SmartConnect
from SmartApi.smartWebSocketV2 import SmartWebSocketV2
import gspread
from oauth2client.service_account import ServiceAccountCredentials

API_KEY="f9323UZL"; CLIENT_ID="R295164"; PIN="2485"; TOTP_SECRET="VREKBLJ6LITKUQLSLAEU55ZYPM"
SERVICE_ACCOUNT_FILE="creds.json"; SPREADSHEET_NAME="KavyaDarsh"; CACHE="hist.json"

NIFTY_INDICES = {"NIFTY 50": "99926000", "NIFTY BANK": "99926009", "NIFTY FIN SERVICE": "99926037","NIFTY IT": "99926008", "NIFTY AUTO": "99926029", "NIFTY PHARMA": "99926023","NIFTY FMCG": "99926021", "NIFTY METAL": "99926030", "NIFTY ENERGY": "99926020","NIFTY PSE": "99926024", "NIFTY MID SELECT": "99926074"}
INDEX_MAP = {"BANK": ["HDFCBANK","ICICIBANK","SBIN","AXISBANK","KOTAKBANK","INDUSINDBK","BANKBARODA","PNB","CANBK","AUBANK","BAJFINANCE"],"IT": ["INFY","TCS","HCLTECH","WIPRO","TECHM","PERSISTENT","COFORGE","OFSS","MPHASIS"],"PHARMA": ["SUNPHARMA","CIPLA","DRREDDY","DIVISLAB","TORNTPHARM","ZYDUSLIFE","LUPIN","MANKIND","ALKEM","AUROPHARMA","MAXHEALTH"],"AUTO": ["MARUTI","M&M","BAJAJ-AUTO","EICHERMOT","TVSMOTOR","HEROMOTOCO","ASHOKLEY","MOTHERSON","BOSCHLTD"],"METAL": ["TATASTEEL","HINDALCO","JSWSTEEL","VEDL","JSL","NMDC","SAIL","APLAPOLLO","HINDZINC"],"ENERGY": ["ONGC","NTPC","POWERGRID","IOC","BPCL","GAIL","ADANIGREEN","ADANIENT","ADANIPORTS","ADANIPOWER","RELIANCE"],"FMCG": ["HINDUNILVR","NESTLEIND","VBL","BRITANNIA","TATACONSUM","DABUR","GODREJCP","MARICO","COLPAL","ITC"],"OTHERS": ["BSE","DIXON","POLYCAB","COCHINSHIP","MAZDOCK","INDHOTEL","VOLTAS","SUPREMEIND","LT","BHARTIARTL"]}
SYM_TO_INDEX = {sym: idx for idx, syms in INDEX_MAP.items() for sym in syms}
BACKUP_TOKENS={"HDFCBANK":"1333","ICICIBANK":"4963","RELIANCE":"2885","INFY":"1594","TCS":"11536","BHARTIARTL":"10604","SBIN":"3045","BAJFINANCE":"317","ITC":"1660","LT":"11483","AXISBANK":"5900","KOTAKBANK":"1922","INDUSINDBK":"5258","BANKBARODA":"4668","PNB":"10666","CANBK":"10794","AUBANK":"21238","HCLTECH":"7229","WIPRO":"3787","TECHM":"13538","PERSISTENT":"18365","COFORGE":"11543","OFSS":"10738","MPHASIS":"4503","SUNPHARMA":"3351","CIPLA":"694","DRREDDY":"881","DIVISLAB":"10940","TORNTPHARM":"3518","ZYDUSLIFE":"7929","LUPIN":"10440","MANKIND":"15380","ALKEM":"11703","AUROPHARMA":"275","MARUTI":"10999","M&M":"2031","BAJAJ-AUTO":"16669","EICHERMOT":"910","TVSMOTOR":"8479","HEROMOTOCO":"1348","ASHOKLEY":"212","MOTHERSON":"4204","BOSCHLTD":"2181","TATASTEEL":"3499","HINDALCO":"1363","JSWSTEEL":"11723","VEDL":"3063","JSL":"11236","NMDC":"15332","SAIL":"2963","APLAPOLLO":"25780","HINDZINC":"1424","ONGC":"2475","NTPC":"11630","POWERGRID":"14977","IOC":"1624","BPCL":"526","GAIL":"4717","ADANIGREEN":"3563","HINDUNILVR":"1394","NESTLEIND":"17963","VBL":"18921","BRITANNIA":"547","TATACONSUM":"3432","DABUR":"772","GODREJCP":"10099","MARICO":"4067","COLPAL":"15141","BSE":"19585","MAXHEALTH":"22377","DIXON":"21690","POLYCAB":"9590","COCHINSHIP":"21508","MAZDOCK":"509","INDHOTEL":"1512","VOLTAS":"3718","SUPREMEIND":"3363","ADANIENT":"25","ADANIPORTS":"15083","ADANIPOWER":"17388"}

instrument_data={}; nifty_data={}; data_lock=threading.Lock()

def login():
    obj=SmartConnect(api_key=API_KEY); obj.generateSession(CLIENT_ID,PIN,pyotp.TOTP(TOTP_SECRET).now()); print("✅ Login Success"); return obj

def get_dates():
    ist=timezone('Asia/Kolkata'); y=datetime.now(ist)-timedelta(days=1)
    while y.weekday()>=5: y-=timedelta(days=1)
    f=y-timedelta(days=35); return f.strftime("%Y-%m-%d 09:15"), y.strftime("%Y-%m-%d 15:30"), y.strftime("%Y-%m-%d")

def fetch_all(obj):
    from_str,to_str,today=get_dates()
    print(f"📅 FROM: {from_str} TO: {to_str} (Kalcha divas: {today})")
    if os.path.exists(CACHE):
        try:
            with open(CACHE,'r') as f: cj=json.load(f)
            if cj.get('date')!=today:
                print(f"⚠ Date Badalali {cj.get('date')} -> {today} - Cache Delete\n"); os.remove(CACHE)
            else:
                print(f"✅ CACHE {today} vaparat ahe")
                for k,v in cj['data'].items(): instrument_data[k]=v
                for k,v in cj.get('nifty',{}).items(): nifty_data[k]=v
                if len(instrument_data)>50 and len(nifty_data)>=5:
                    print(f"✅ Cache Load: {len(instrument_data)} stocks, {len(nifty_data)} Nifty\n")
                    print("------ NIFTY PD / PW / 1M ------")
                    for it in nifty_data.values():
                        print(f" {it['symbol']} | PD:{it['y_high']}/{it['y_low']} PW:{it['p5_high']}/{it['p5_low']} 1M:{it['m_high']}/{it['m_low']}")
                    print("\n------ STOCKS PD / PW / 1M (ALL) ------")
                    for it in list(instrument_data.values())[:20]:
                        print(f" {it['symbol']} | PD:{it['y_high']}/{it['y_low']} PW:{it['p5_high']}/{it['p5_low']} 1M:{it['m_high']}/{it['m_low']}")
                    print(f"\n... total {len(instrument_data)} loaded - SHEET READY\n")
                    return
                else: os.remove(CACHE); instrument_data.clear(); nifty_data.clear()
        except Exception as e:
            print(f"Cache Error {e}")
            if os.path.exists(CACHE): os.remove(CACHE)
    print(f"⏳ Fetching {len(NIFTY_INDICES)} NIFTY + {len(BACKUP_TOKENS)} Stocks...\n")
    temp_stocks={}; temp_nifty={}
    for name, token in NIFTY_INDICES.items():
        try:
            resp=obj.getCandleData({"exchange":"NSE","symboltoken":str(token),"interval":"ONE_DAY","fromdate":from_str,"todate":to_str})
            if resp and resp.get("data") and len(resp["data"])>=5:
                df=pd.DataFrame(resp["data"],columns=["dt","o","h","l","c","v"]); y_c=df.iloc[-1]; prev5=df.iloc[-6:-1]; last30=df.iloc[-31:-1]
                d={"symbol":name,"token":str(token),"index":"NIFTY","y_high":float(y_c['h']),"y_low":float(y_c['l']),"p5_high":float(prev5['h'].max()),"p5_low":float(prev5['l'].min()),"m_high":float(last30['h'].max()),"m_low":float(last30['l'].min()),"ltp":float(y_c['c']),"break_time":""}
                nifty_data[str(token)]=d; temp_nifty[str(token)]=d
                print(f" ✅ {name} | PD:{d['y_high']}/{d['y_low']} PW:{d['p5_high']}/{d['p5_low']} 1M:{d['m_high']}/{d['m_low']}")
        except Exception as e: print(f" ❌ {name} {e}")
        time.sleep(0.25)
    for sym,tok in BACKUP_TOKENS.items():
        try:
            resp=obj.getCandleData({"exchange":"NSE","symboltoken":str(tok),"interval":"ONE_DAY","fromdate":from_str,"todate":to_str})
            if resp and resp.get("data") and len(resp["data"])>=5:
                df=pd.DataFrame(resp["data"],columns=["dt","o","h","l","c","v"]).sort_values('dt'); y_c=df.iloc[-1]; prev5=df.iloc[-6:-1]; last30=df.iloc[-31:-1]
                it={"symbol":sym,"token":str(tok),"index":SYM_TO_INDEX.get(sym,"OTHERS"),"y_high":float(y_c['h']),"y_low":float(y_c['l']),"p5_high":float(prev5['h'].max()),"p5_low":float(prev5['l'].min()),"m_high":float(last30['h'].max()),"m_low":float(last30['l'].min()),"ltp":float(y_c['c']),"break_time":""}
                with data_lock: instrument_data[str(tok)]=it; temp_stocks[str(tok)]=it
                print(f" ✅ {sym} | PD:{it['y_high']}/{it['y_low']} PW:{it['p5_high']}/{it['p5_low']} 1M:{it['m_high']}/{it['m_low']}")
        except Exception as e: print(f" ❌ {sym} {e}")
        time.sleep(0.25)
    with open(CACHE,'w') as f: json.dump({"date":today,"data":temp_stocks,"nifty":temp_nifty},f)
    print(f"\n💾 Cache Saved {today}\n")

def start_ws(obj):
    all_tokens = list(nifty_data.keys()) + list(instrument_data.keys())
    ft=obj.getfeedToken(); sws=SmartWebSocketV2(auth_token=obj.access_token,api_key=API_KEY,client_code=CLIENT_ID,feed_token=ft)
    def on_data(wsapp,msg):
        for t in ([msg] if isinstance(msg,dict) else msg):
            token=str(t.get("token","")); raw=t.get("last_traded_price")
            if raw and token in all_tokens:
                price=float(raw)/100.0
                with data_lock:
                    if token in instrument_data: instrument_data[token]["ltp"]=price
                    if token in nifty_data: nifty_data[token]["ltp"]=price
    def on_open(wsapp): print("✅ WS Connected"); sws.subscribe("live_data",1,[{"exchangeType":1,"tokens":all_tokens}])
    sws.on_open=on_open; sws.on_data=on_data
    threading.Thread(target=sws.connect,daemon=True).start(); time.sleep(2)

def chg(it): return ((it['ltp']-it['y_low'])/it['y_low']*100) if it['y_low'] else 0

def build_main_rows():
    with data_lock:
        rows=[]; rows.append(["--- NIFTY INDICES ---","","","","","","","","","","",""])
        for it in sorted(nifty_data.values(), key=lambda x: chg(x), reverse=True):
            if it['ltp']>it['p5_high']:
                status="🚀 BREAKOUT"
                if it['break_time']=="": it['break_time']=datetime.now().strftime("%H:%M:%S")
            elif it['ltp']<it['p5_low']:
                status="🔻 BREAKDOWN"
                if it['break_time']=="": it['break_time']=datetime.now().strftime("%H:%M:%S")
            else: status="RANGE BOUND"; it['break_time']=""
            rows.append([it['index'], it['symbol'], it['y_high'], it['y_low'], it['p5_high'], it['p5_low'], it['ltp'], f"{chg(it):+.2f}%", status, it['break_time'], it['m_high'], it['m_low']])
        rows.append(["","","","","","","","","","","",""])
        for sec in ["BANK","IT","PHARMA","AUTO","METAL","ENERGY","FMCG","OTHERS"]:
            sec_stocks = [v for v in instrument_data.values() if v.get('index')==sec]
            if not sec_stocks: continue
            sec_stocks.sort(key=lambda x: chg(x), reverse=True)
            rows.append([f"--- {sec} ---","","","","","","","","","","",""])
            for it in sec_stocks:
                if it['ltp']>it['p5_high']:
                    status="🚀 BREAKOUT"
                    if it['break_time']=="": it['break_time']=datetime.now().strftime("%H:%M:%S")
                elif it['ltp']<it['p5_low']:
                    status="🔻 BREAKDOWN"
                    if it['break_time']=="": it['break_time']=datetime.now().strftime("%H:%M:%S")
                else: status="RANGE BOUND"; it['break_time']=""
                rows.append([it['index'], it['symbol'], it['y_high'], it['y_low'], it['p5_high'], it['p5_low'], it['ltp'], f"{chg(it):+.2f}%", status, it['break_time'], it['m_high'], it['m_low']])
            rows.append(["","","","","","","","","","","",""])
    return rows

def build_action_rows():
    # Fakt BREAKOUT/BREAKDOWN, varati BREAKOUT latest, khali BREAKDOWN latest
    with data_lock:
        breakout_list=[]
        breakdown_list=[]
        all_items = list(nifty_data.values()) + list(instrument_data.values())
        for it in all_items:
            if it['ltp']>it['p5_high']:
                status="🚀 BREAKOUT"
                if it['break_time']=="": it['break_time']=datetime.now().strftime("%H:%M:%S")
                breakout_list.append(it)
            elif it['ltp']<it['p5_low']:
                status="🔻 BREAKDOWN"
                if it['break_time']=="": it['break_time']=datetime.now().strftime("%H:%M:%S")
                breakdown_list.append(it)

        # Latest break_time varati yeil
        def sort_key(x):
            try: return datetime.strptime(x['break_time'], "%H:%M:%S")
            except: return datetime.min
        breakout_list.sort(key=sort_key, reverse=True)
        breakdown_list.sort(key=sort_key, reverse=True)

        rows=[]
        rows.append(["--- 🚀 BREAKOUT (Latest Varati) ---","","","","","","","","","","",""])
        for it in breakout_list:
            rows.append([it['index'], it['symbol'], it['y_high'], it['y_low'], it['p5_high'], it['p5_low'], it['ltp'], f"{chg(it):+.2f}%", "🚀 BREAKOUT", it['break_time'], it['m_high'], it['m_low']])
        rows.append(["","","","","","","","","","","",""])
        rows.append(["--- 🔻 BREAKDOWN (Latest Varati) ---","","","","","","","","","","",""])
        for it in breakdown_list:
            rows.append([it['index'], it['symbol'], it['y_high'], it['y_low'], it['p5_high'], it['p5_low'], it['ltp'], f"{chg(it):+.2f}%", "🔻 BREAKDOWN", it['break_time'], it['m_high'], it['m_low']])
    return rows

def apply_colour_formatting(sheet):
    GREEN_BG = {"red": 0.0, "green": 0.8, "blue": 0.2}
    RED_BG = {"red": 0.9, "green": 0.2, "blue": 0.2}
    WHITE_BG = {"red": 1, "green": 1, "blue": 1}
    WHITE_TEXT = {"red": 1, "green": 1, "blue": 1}
    BLACK_TEXT = {"red": 0, "green": 0, "blue": 0}
    try:
        del_requests = []
        for _ in range(30):
            del_requests.append({"deleteConditionalFormatRule": {"sheetId": sheet.id, "index": 0}})
        sheet.spreadsheet.batch_update({"requests": del_requests})
    except: pass
    time.sleep(0.5)
    try:
        sheet.format(f"G4:G1000", {"backgroundColor": WHITE_BG, "textFormat": {"foregroundColor": BLACK_TEXT, "bold": False}})
    except: pass
    time.sleep(0.5)
    requests = [
        {"addConditionalFormatRule": {"rule": {"ranges": [{"sheetId": sheet.id, "startRowIndex": 3, "endRowIndex": 1000, "startColumnIndex": 2, "endColumnIndex": 3}], "booleanRule": {"condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": "=AND($G4<>\"\",$C4<>\"\",$G4>=$C4)"}]}, "format": {"backgroundColor": GREEN_BG, "textFormat": {"foregroundColor": WHITE_TEXT, "bold": True}}}}, "index": 0}},
        {"addConditionalFormatRule": {"rule": {"ranges": [{"sheetId": sheet.id, "startRowIndex": 3, "endRowIndex": 1000, "startColumnIndex": 3, "endColumnIndex": 4}], "booleanRule": {"condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": "=AND($G4<>\"\",$D4<>\"\",$G4<=$D4)"}]}, "format": {"backgroundColor": RED_BG, "textFormat": {"foregroundColor": WHITE_TEXT, "bold": True}}}}, "index": 0}},
        {"addConditionalFormatRule": {"rule": {"ranges": [{"sheetId": sheet.id, "startRowIndex": 3, "endRowIndex": 1000, "startColumnIndex": 4, "endColumnIndex": 5}], "booleanRule": {"condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": "=AND($G4<>\"\",$E4<>\"\",$G4>=$E4)"}]}, "format": {"backgroundColor": GREEN_BG, "textFormat": {"foregroundColor": WHITE_TEXT, "bold": True}}}}, "index": 0}},
        {"addConditionalFormatRule": {"rule": {"ranges": [{"sheetId": sheet.id, "startRowIndex": 3, "endRowIndex": 1000, "startColumnIndex": 5, "endColumnIndex": 6}], "booleanRule": {"condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": "=AND($G4<>\"\",$F4<>\"\",$G4<=$F4)"}]}, "format": {"backgroundColor": RED_BG, "textFormat": {"foregroundColor": WHITE_TEXT, "bold": True}}}}, "index": 0}},
        {"addConditionalFormatRule": {"rule": {"ranges": [{"sheetId": sheet.id, "startRowIndex": 3, "endRowIndex": 1000, "startColumnIndex": 10, "endColumnIndex": 11}], "booleanRule": {"condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": "=AND($G4<>\"\",$K4<>\"\",$G4>=$K4)"}]}, "format": {"backgroundColor": GREEN_BG, "textFormat": {"foregroundColor": WHITE_TEXT, "bold": True}}}}, "index": 0}},
        {"addConditionalFormatRule": {"rule": {"ranges": [{"sheetId": sheet.id, "startRowIndex": 3, "endRowIndex": 1000, "startColumnIndex": 11, "endColumnIndex": 12}], "booleanRule": {"condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": "=AND($G4<>\"\",$L4<>\"\",$G4<=$L4)"}]}, "format": {"backgroundColor": RED_BG, "textFormat": {"foregroundColor": WHITE_TEXT, "bold": True}}}}, "index": 0}},
        {"addConditionalFormatRule": {"rule": {"ranges": [{"sheetId": sheet.id, "startRowIndex": 3, "endRowIndex": 1000, "startColumnIndex": 8, "endColumnIndex": 9}],"booleanRule": {"condition": {"type": "TEXT_CONTAINS", "values": [{"userEnteredValue": "BREAKOUT"}]},"format": {"backgroundColor": GREEN_BG, "textFormat": {"foregroundColor": WHITE_TEXT, "bold": True}}}},"index": 0}},
        {"addConditionalFormatRule": {"rule": {"ranges": [{"sheetId": sheet.id, "startRowIndex": 3, "endRowIndex": 1000, "startColumnIndex": 8, "endColumnIndex": 9}],"booleanRule": {"condition": {"type": "TEXT_CONTAINS", "values": [{"userEnteredValue": "BREAKDOWN"}]},"format": {"backgroundColor": RED_BG, "textFormat": {"foregroundColor": WHITE_TEXT, "bold": True}}}},"index": 0}}
    ]
    try:
        sheet.spreadsheet.batch_update({"requests": requests})
        sheet.format("A3:L3", {"backgroundColor": {"red": 0.12, "green": 0.2, "blue": 0.6}, "textFormat": {"foregroundColor": WHITE_TEXT, "bold": True}})
        print(f"✅ Colour Applied to {sheet.title}")
    except Exception as e: print(f"Colour Error {sheet.title}:", e)

obj=login(); fetch_all(obj); start_ws(obj)
scope=["https://spreadsheets.google.com/feeds","https://www.googleapis.com/auth/drive"]
creds=ServiceAccountCredentials.from_json_keyfile_name(SERVICE_ACCOUNT_FILE, scope)
gc=gspread.authorize(creds)
spreadsheet = gc.open(SPREADSHEET_NAME)
sheet1 = spreadsheet.sheet1

try:
    sheet2 = spreadsheet.worksheet("ACTION_LIVE")
except:
    sheet2 = spreadsheet.add_worksheet(title="ACTION_LIVE", rows=1000, cols=20)

header_main = [["KAVYADARSH FINAL - "+datetime.now().strftime("%d-%b %H:%M")],["FROM",get_dates()[0],"TO",get_dates()[1]],["INDEX","Symbol","PD High","PD Low","PW High (P5)","PW Low (P5)","LTP","CHANGE %","STATUS (Weekly)","BREAK TIME","1M High","1M Low"]]
header_action = [["ACTION LIVE - BREAKOUT VARATI / BREAKDOWN KHALI - "+datetime.now().strftime("%d-%b %H:%M")],["FROM",get_dates()[0],"TO",get_dates()[1]],["INDEX","Symbol","PD High","PD Low","PW High (P5)","PW Low (P5)","LTP","CHANGE %","STATUS","BREAK TIME","1M High","1M Low"]]

sheet1.clear(); sheet1.update(values=header_main+build_main_rows(), range_name="A1")
apply_colour_formatting(sheet1)

sheet2.clear(); sheet2.update(values=header_action+build_action_rows(), range_name="A1")
apply_colour_formatting(sheet2)

print("\n▶ LIVE STARTED - SHEET1: MAIN | SHEET2: ACTION_LIVE (Breakout varati, Breakdown khali, Latest Time)\n")
while True:
    try:
        sheet1.update(values=build_main_rows(), range_name="A4")
        sheet2.update(values=build_action_rows(), range_name="A4")
        print(f"🔄 {datetime.now().strftime('%H:%M:%S')} Updated | MAIN:{len(build_main_rows())} ACTION:{len(build_action_rows())}")
        time.sleep(5)
    except Exception as e: print(e); time.sleep(5)