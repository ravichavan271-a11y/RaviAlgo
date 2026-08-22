
import os, json, threading, time, gzip, io, re, logging
from datetime import datetime
from collections import deque
import pytz, requests, pandas as pd
from flask import Flask, jsonify, request, render_template_string, redirect
import upstox_client

# --- FAST CONFIG - NO LOGGING ---
logging.getLogger('werkzeug').setLevel(logging.ERROR)
logging.getLogger('upstox_client').setLevel(logging.ERROR)
import warnings
warnings.filterwarnings("ignore")

IST = pytz.timezone('Asia/Kolkata')
TOKEN_FILE = "upstox_token.txt"
PAPER_FILE = "flattrade_paper.json"
KEY_MAP_FILE = "symbol_keys.json"

app = Flask(__name__)
app.config['JSONIFY_PRETTYPRINT_REGULAR'] = False

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN","")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID","")

# --- IN-MEMORY CACHE - NO DISK IO ON EVERY TICK ---
paper_cache = None
paper_cache_lock = threading.Lock()
last_save_time = 0
SAVE_DEBOUNCE = 2.0  # 2 sec debounce - fast movement madhe file write nahi

def get_token():
    tok = os.environ.get("UPSTOX_ACCESS_TOKEN","") or os.environ.get("UPSTOX_TOKEN","")
    if not tok and os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE,"r") as f: tok=f.read().strip()
        except: pass
    return tok

instrument_cache = {}
instrument_list = []
instrument_list_lock = threading.Lock()

def load_instruments():
    global instrument_cache, instrument_list
    if instrument_cache: return instrument_cache
    try:
        r = requests.get("https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz", timeout=30)
        with gzip.GzipFile(fileobj=io.BytesIO(r.content)) as gz:
            df = pd.read_csv(gz, usecols=lambda c: c in ["tradingsymbol","instrument_key","name","short_name","exchange"])
        mp={}
        lst=[]
        for _, row in df.iterrows():
            sym=str(row.get("tradingsymbol","")).strip()
            key=str(row.get("instrument_key","")).strip()
            name=str(row.get("name","") or row.get("short_name","") or "")
            if not sym or not key: continue
            lst.append({"symbol":sym,"key":key,"name":name})
            if "NSE_EQ" in key:
                mp[sym]=key
                if sym.endswith("-EQ"): mp[sym.replace("-EQ","")]=key
            elif "NSE_INDEX" in key:
                mp[sym]=key
                if "Nifty 50" in key: mp["NIFTY 50"]=key; mp["NIFTY"]=key
                if "Nifty Bank" in key: mp["NIFTY BANK"]=key; mp["BANKNIFTY"]=key
                if "Nifty Fin" in key: mp["FINNIFTY"]=key
            elif "NSE_FO" in key:
                mp[sym]=key
            elif sym not in mp:
                mp[sym]=key
        mp["M&M"]="NSE_EQ|INE101A01026"
        mp["NIFTY 50"]="NSE_INDEX|Nifty 50"; mp["NIFTY"]="NSE_INDEX|Nifty 50"
        mp["NIFTY BANK"]="NSE_INDEX|Nifty Bank"; mp["BANKNIFTY"]="NSE_INDEX|Nifty Bank"
        mp["FINNIFTY"]="NSE_INDEX|Nifty Fin Service"
        with instrument_list_lock:
            instrument_cache=mp
            instrument_list=lst
        return mp
    except Exception as e:
        return {}

def load_paper_from_disk():
    if os.path.exists(PAPER_FILE):
        try:
            with open(PAPER_FILE,"r") as f: return json.load(f)
        except: pass
    return {"balance":100000.0, "positions":[], "orders":[], "watchlist":["RELIANCE","INFY","TCS","NIFTY 50","NIFTY BANK","NIFTY 26000 CE","BANKNIFTY 58000 CE"]}

def get_paper():
    global paper_cache
    with paper_cache_lock:
        if paper_cache is None:
            paper_cache = load_paper_from_disk()
        return paper_cache

def save_paper_fast(paper_data=None, force=False):
    global paper_cache, last_save_time
    now = time.time()
    with paper_cache_lock:
        if paper_data is not None:
            paper_cache = paper_data
        # Debounce - fast market madhe har tick la save nako
        if not force and (now - last_save_time) < SAVE_DEBOUNCE:
            return
        last_save_time = now
        data_to_save = paper_cache
    # Async file write - background la
    def _write():
        try:
            with open(PAPER_FILE,"w") as f: json.dump(data_to_save,f)
        except: pass
    threading.Thread(target=_write, daemon=True).start()

# Initial load
load_instruments()
get_paper()

# --- ULTRA FAST LTP - NO LOCK ON READ ---
live_ltp = {}
live_ltp_lock = threading.Lock()
ltp_update_count = 0

def start_streamer():
    token = get_token()
    if not token: return
    mp = load_instruments()
    def get_keys():
        paper = get_paper()
        syms = list(set(paper.get("watchlist",[]) + [p["symbol"] for p in paper.get("positions",[])]))
        keys=[]
        for s in syms:
            k=mp.get(s) or mp.get(s+"-EQ")
            if not k:
                for p in paper.get("positions",[]):
                    if p["symbol"]==s: k=p.get("instrument_key")
            if k: keys.append(k)
        for p in paper.get("positions",[]):
            if p.get("instrument_key") and p.get("instrument_key") not in keys:
                keys.append(p.get("instrument_key"))
        return list(set(keys))[:200]  # 200 keys support - options sathi jast
    
    try:
        cfg = upstox_client.Configuration()
        cfg.access_token = token
        api_client = upstox_client.ApiClient(cfg)
        keys = get_keys()
        if not keys: keys=["NSE_INDEX|Nifty 50","NSE_INDEX|Nifty Bank"]
        # FAST MODE: ltpc mode - only LTP + LTT + CP - sabse fast
        streamer = upstox_client.MarketDataStreamerV3(api_client=api_client, instrumentKeys=keys, mode="ltpc")
        def on_message(msg):
            global ltp_update_count
            feeds=msg.get("feeds",{})
            # Batch update - no print, no logging
            with live_ltp_lock:
                for ikey, feed in feeds.items():
                    try:
                        ltp=None
                        if "ltpc" in feed: 
                            ltp=feed["ltpc"].get("ltp")
                        elif "fullFeed" in feed:
                            ff=feed["fullFeed"]
                            if "marketFF" in ff: ltp=ff["marketFF"].get("ltpc",{}).get("ltp")
                            elif "indexFF" in ff: ltp=ff["indexFF"].get("ltpc",{}).get("ltp")
                        if ltp: 
                            live_ltp[ikey]=float(ltp)
                            ltp_update_count+=1
                    except: pass
        def on_open(): pass  # No print - fast
        streamer.on("open", on_open)
        streamer.on("message", on_message)
        streamer.connect()
    except: pass

threading.Thread(target=start_streamer, daemon=True).start()

# --- ULTRA FAST AUTO MONITOR - 200ms Check ---
def auto_monitor():
    while True:
        try:
            time.sleep(0.2)  # 200ms - fast movement sathi
            paper = get_paper()
            if not paper["positions"]: continue
            with live_ltp_lock: ltp_copy=dict(live_ltp)
            mp = load_instruments()
            to_close=[]
            changed=False
            for idx, pos in enumerate(paper["positions"]):
                key=pos.get("instrument_key") or mp.get(pos["symbol"]) or mp.get(pos["symbol"]+"-EQ")
                ltp=ltp_copy.get(key)
                if not ltp: ltp=pos.get("ltp", pos["entry_price"])
                if not ltp: continue
                # Update LTP in memory only - no disk
                pos["ltp"]=ltp
                pos["pnl"]=(ltp - pos["entry_price"])*pos["qty"] if pos["side"]=="BUY" else (pos["entry_price"]-ltp)*pos["qty"]
                pos["pnl_pct"]=(pos["pnl"]/(pos["entry_price"]*pos["qty"])*100 if pos["entry_price"]>0 else 0)
                # Trail SL
                if pos.get("trail") and pos["side"]=="BUY" and ltp>pos["entry_price"]:
                    new_sl = ltp * 0.995
                    if new_sl > pos.get("sl",0):
                        pos["sl"]=new_sl
                        changed=True
                elif pos.get("trail") and pos["side"]=="SELL" and ltp<pos["entry_price"]:
                    new_sl = ltp * 1.005
                    if pos.get("sl",0)==0 or new_sl < pos["sl"]:
                        pos["sl"]=new_sl
                        changed=True
                # SL/TGT check
                if pos.get("sl",0)>0:
                    if (pos["side"]=="BUY" and ltp <= pos["sl"]) or (pos["side"]=="SELL" and ltp >= pos["sl"]):
                        to_close.append((idx, "SL HIT", ltp))
                if pos.get("target",0)>0:
                    if (pos["side"]=="BUY" and ltp >= pos["target"]) or (pos["side"]=="SELL" and ltp <= pos["target"]):
                        to_close.append((idx, "TARGET HIT", ltp))
            for idx, reason, ltp in sorted(to_close, key=lambda x: x[0], reverse=True):
                if idx < len(paper["positions"]):
                    pos=paper["positions"].pop(idx)
                    pnl=(ltp - pos["entry_price"])*pos["qty"] if pos["side"]=="BUY" else (pos["entry_price"]-ltp)*pos["qty"]
                    paper["balance"]+=pnl
                    paper["orders"].append({"symbol":pos["symbol"],"qty":pos["qty"],"side":"SELL" if pos["side"]=="BUY" else "BUY","price":float(ltp),"sl":pos.get("sl",0),"target":pos.get("target",0),"status":f"{reason} ₹{pnl:.2f}","time":datetime.now(IST).isoformat()})
                    changed=True
            if changed:
                save_paper_fast(paper, force=False)
        except: 
            time.sleep(0.5)

threading.Thread(target=auto_monitor, daemon=True).start()

# --- FLASK ROUTES - NO LOGGING ---
@app.route('/manifest.json')
def manifest():
    return jsonify({"name":"Ravi Flattrade FAST","short_name":"Flattrade","start_url":"/flattrade","display":"standalone","background_color":"#0a0a0a","theme_color":"#ff5722"})

@app.route('/')
@app.route('/flattrade')
@app.route('/mobile')
@app.route('/paper-trading')
def home():
    html = open('/mnt/data/mobile_app.html', encoding='utf-8').read()
    return render_template_string(html)

@app.route('/api/search')
def api_search():
    q = request.args.get('q','').strip().upper()
    if not q: return jsonify({"results":[]})
    mp = load_instruments()
    with instrument_list_lock:
        ilist = list(instrument_list)
    results=[]
    for item in ilist:
        sym=item["symbol"]
        if q in sym:
            if "NSE_FO" in item["key"]:
                opt_type="CE" if sym.endswith("CE") else "PE" if sym.endswith("PE") else "FUT"
                results.append({"symbol":sym,"key":item["key"],"type":"OPT" if opt_type in ["CE","PE"] else "FUT","opt_type":opt_type,"name":item["name"]})
            elif "NSE_INDEX" in item["key"]:
                results.append({"symbol":sym,"key":item["key"],"type":"INDEX","name":item["name"]})
            elif "NSE_EQ" in item["key"]:
                results.append({"symbol":sym.replace("-EQ",""),"key":item["key"],"type":"EQ","name":item["name"]})
            if len(results)>=40: break
    if len(results)<5:
        for k,v in mp.items():
            if q in k and k not in [r["symbol"] for r in results]:
                results.append({"symbol":k,"key":v,"type":"OPT" if "FO" in v else "INDEX" if "INDEX" in v else "EQ","opt_type":"CE" if "CE" in k else "PE","name":""})
            if len(results)>=40: break
    with live_ltp_lock: ltp_copy=dict(live_ltp)
    for r in results:
        r["ltp"]=ltp_copy.get(r["key"],0)
    return jsonify({"results":results[:25]})

@app.route('/api/ltp')
def api_ltp():
    key=request.args.get('key','')
    with live_ltp_lock: ltp=live_ltp.get(key,0)
    if ltp==0:
        token=get_token()
        if token and key:
            try:
                url=f"https://api.upstox.com/v2/market-quote/ltp?instrument_key={key}"
                headers={"Authorization":f"Bearer {token}"}
                rr=requests.get(url,headers=headers,timeout=3)
                if rr.status_code==200:
                    j=rr.json()
                    ltp=j.get("data",{}).get(key,{}).get("last_price",0)
                    if ltp:
                        with live_ltp_lock: live_ltp[key]=float(ltp)
            except: pass
    return jsonify({"ltp":ltp,"key":key})

@app.route('/api/optionchain')
def api_optionchain():
    underlying = request.args.get('underlying','NIFTY').upper()
    mp = load_instruments()
    with instrument_list_lock: ilist = list(instrument_list)
    fo_list = [x for x in ilist if "NSE_FO" in x["key"] and x["symbol"].startswith(underlying)]
    strikes={}
    underlying_key = mp.get(underlying) or (mp.get("NIFTY 50") if underlying=="NIFTY" else mp.get("NIFTY BANK") if underlying=="BANKNIFTY" else None)
    with live_ltp_lock: ltp_copy=dict(live_ltp)
    underlying_ltp=ltp_copy.get(underlying_key,0) if underlying_key else 0
    pattern = re.compile(r'(\d+)(CE|PE)$')
    for item in fo_list:
        m=pattern.search(item["symbol"])
        if not m: continue
        try: strike=int(m.group(1))
        except: continue
        opt_type=m.group(2)
        if strike not in strikes:
            strikes[strike]={"strike":strike,"ce_symbol":None,"pe_symbol":None,"ce_key":None,"pe_key":None,"ce_ltp":0,"pe_ltp":0}
        if opt_type=="CE":
            strikes[strike]["ce_symbol"]=item["symbol"]
            strikes[strike]["ce_key"]=item["key"]
            strikes[strike]["ce_ltp"]=ltp_copy.get(item["key"],0)
        else:
            strikes[strike]["pe_symbol"]=item["symbol"]
            strikes[strike]["pe_key"]=item["key"]
            strikes[strike]["pe_ltp"]=ltp_copy.get(item["key"],0)
    sorted_strikes=sorted(strikes.values(), key=lambda x: x["strike"])
    if underlying_ltp>0 and sorted_strikes:
        atm_idx=min(range(len(sorted_strikes)), key=lambda i: abs(sorted_strikes[i]["strike"]-underlying_ltp))
        start=max(0, atm_idx-12)
        end=min(len(sorted_strikes), atm_idx+13)
        sorted_strikes=sorted_strikes[start:end]
    else:
        sorted_strikes=sorted_strikes[:25]
    return jsonify({"underlying":underlying,"underlying_ltp":underlying_ltp,"strikes":sorted_strikes})

@app.route('/api/flattrade/data')
def api_data():
    paper = get_paper()
    mp = load_instruments()
    with live_ltp_lock: ltp_copy=dict(live_ltp)
    symbol_ltp={}
    keys_map={}
    for sym in set(paper.get("watchlist",[]) + [p["symbol"] for p in paper.get("positions",[])]):
        key=None
        for p in paper.get("positions",[]):
            if p["symbol"]==sym and p.get("instrument_key"): key=p.get("instrument_key")
        if not key: 
            key=mp.get(sym) or mp.get(sym+"-EQ")
            # check key file
            if not key and os.path.exists(KEY_MAP_FILE):
                try:
                    with open(KEY_MAP_FILE,"r") as f: km=json.load(f)
                    key=km.get(sym)
                except: pass
        if key:
            keys_map[sym]=key
            if key in ltp_copy: symbol_ltp[sym]=ltp_copy[key]
    # Update pnl in memory - no disk
    for pos in paper["positions"]:
        key=pos.get("instrument_key") or mp.get(pos["symbol"])
        ltp=ltp_copy.get(key, pos.get("ltp", pos["entry_price"]))
        if ltp:
            pos["ltp"]=ltp
            pos["pnl"]=(ltp - pos["entry_price"])*pos["qty"] if pos["side"]=="BUY" else (pos["entry_price"]-ltp)*pos["qty"]
            pos["pnl_pct"]=(pos["pnl"]/(pos["entry_price"]*pos["qty"])*100 if pos["entry_price"]>0 else 0)
    return jsonify({"balance":paper["balance"],"watchlist":paper["watchlist"],"positions":paper["positions"],"orders":paper["orders"][-80:],"symbolLTP":symbol_ltp,"keys":keys_map,"ltp_count":len(ltp_copy)})

@app.route('/api/flattrade/order', methods=['POST'])
def api_order():
    data=request.json
    sym=data.get("symbol","").strip().upper()
    key=data.get("key","").strip()
    qty=int(data.get("qty",1))
    side=data.get("side","BUY")
    sl=float(data.get("sl",0) or 0)
    target=float(data.get("target",0) or 0)
    trail=bool(data.get("trail",False))
    mp=load_instruments()
    if not key: key=mp.get(sym) or mp.get(sym+"-EQ") or ""
    if not key:
        with instrument_list_lock:
            for item in instrument_list:
                if item["symbol"]==sym: key=item["key"]; break
    if not key: return jsonify({"ok":False,"error":"Symbol not found - Search kara"})
    with live_ltp_lock: ltp=live_ltp.get(key,0)
    if ltp==0:
        token=get_token()
        if token:
            try:
                url=f"https://api.upstox.com/v2/market-quote/ltp?instrument_key={key}"
                headers={"Authorization":f"Bearer {token}"}
                r=requests.get(url,headers=headers,timeout=3)
                if r.status_code==200:
                    j=r.json()
                    ltp=j.get("data",{}).get(key,{}).get("last_price",0)
                    if ltp:
                        with live_ltp_lock: live_ltp[key]=float(ltp)
            except: pass
    if ltp==0: return jsonify({"ok":False,"error":"LTP 0 - Market band, 9:15 la try"})
    paper = get_paper()
    paper["positions"].append({"symbol":sym,"instrument_key":key,"qty":qty,"side":side,"entry_price":float(ltp),"ltp":float(ltp),"sl":sl,"target":target,"trail":trail,"pnl":0.0,"pnl_pct":0.0,"time":datetime.now(IST).isoformat()})
    paper["orders"].append({"symbol":sym,"qty":qty,"side":side,"price":float(ltp),"sl":sl,"target":target,"status":"OPEN","time":datetime.now(IST).isoformat()})
    save_paper_fast(paper, force=True)  # force save on order
    return jsonify({"ok":True})

@app.route('/api/flattrade/close', methods=['POST'])
def api_close():
    idx=request.json.get("idx")
    paper = get_paper()
    if 0 <= idx < len(paper["positions"]):
        with live_ltp_lock: ltp_copy=dict(live_ltp)
        pos=paper["positions"].pop(idx)
        ltp=ltp_copy.get(pos["instrument_key"], pos["ltp"])
        pnl=(ltp - pos["entry_price"])*pos["qty"] if pos["side"]=="BUY" else (pos["entry_price"]-ltp)*pos["qty"]
        paper["balance"]+=pnl
        paper["orders"].append({"symbol":pos["symbol"],"qty":pos["qty"],"side":"SELL" if pos["side"]=="BUY" else "BUY","price":float(ltp),"sl":pos.get("sl",0),"target":pos.get("target",0),"status":f"CLOSE ₹{pnl:.2f}","time":datetime.now(IST).isoformat()})
        save_paper_fast(paper, force=True)
    return jsonify({"ok":True})

@app.route('/api/flattrade/swap', methods=['POST'])
def api_swap():
    idx=request.json.get("idx")
    paper = get_paper()
    if 0 <= idx < len(paper["positions"]):
        with live_ltp_lock: ltp_copy=dict(live_ltp)
        pos=paper["positions"].pop(idx)
        ltp=ltp_copy.get(pos["instrument_key"], pos["ltp"])
        pnl=(ltp - pos["entry_price"])*pos["qty"] if pos["side"]=="BUY" else (pos["entry_price"]-ltp)*pos["qty"]
        paper["balance"]+=pnl
        paper["orders"].append({"symbol":pos["symbol"],"qty":pos["qty"],"side":"SELL" if pos["side"]=="BUY" else "BUY","price":float(ltp),"status":f"SWAP CLOSE ₹{pnl:.2f}","time":datetime.now(IST).isoformat()})
        new_side="SELL" if pos["side"]=="BUY" else "BUY"
        paper["positions"].append({"symbol":pos["symbol"],"instrument_key":pos["instrument_key"],"qty":pos["qty"],"side":new_side,"entry_price":float(ltp),"ltp":float(ltp),"sl":0,"target":0,"trail":False,"pnl":0.0,"pnl_pct":0.0,"time":datetime.now(IST).isoformat()})
        paper["orders"].append({"symbol":pos["symbol"],"qty":pos["qty"],"side":new_side,"price":float(ltp),"status":"SWAP OPEN","time":datetime.now(IST).isoformat()})
        save_paper_fast(paper, force=True)
    return jsonify({"ok":True})

@app.route('/api/flattrade/modify', methods=['POST'])
def api_modify():
    data=request.json
    idx=data.get("idx")
    sl=float(data.get("sl",0) or 0)
    target=float(data.get("target",0) or 0)
    paper = get_paper()
    if 0 <= idx < len(paper["positions"]):
        paper["positions"][idx]["sl"]=sl
        paper["positions"][idx]["target"]=target
        save_paper_fast(paper, force=True)
    return jsonify({"ok":True})

@app.route('/api/flattrade/watchlist', methods=['POST'])
def api_watch():
    sym=request.json.get("symbol","").strip().upper()
    key=request.json.get("key","").strip()
    paper = get_paper()
    if sym and sym not in paper["watchlist"]:
        paper["watchlist"].insert(0,sym)
        paper["watchlist"]=paper["watchlist"][:100]
        try:
            km={}
            if os.path.exists(KEY_MAP_FILE):
                with open(KEY_MAP_FILE,"r") as f: km=json.load(f)
            if key: km[sym]=key
            with open(KEY_MAP_FILE,"w") as f: json.dump(km,f)
        except: pass
        save_paper_fast(paper, force=True)
    return jsonify({"ok":True})

@app.route('/api/flattrade/reset', methods=['POST'])
def api_reset():
    global paper_cache
    with paper_cache_lock:
        paper_cache=None
    if os.path.exists(PAPER_FILE): 
        try: os.remove(PAPER_FILE)
        except: pass
    if os.path.exists(KEY_MAP_FILE):
        try: os.remove(KEY_MAP_FILE)
        except: pass
    return jsonify({"ok":True})

@app.route('/api/flattrade/squareoff', methods=['POST'])
def api_squareoff():
    paper = get_paper()
    total=0
    with live_ltp_lock: ltp_copy=dict(live_ltp)
    for pos in paper["positions"]:
        ltp=ltp_copy.get(pos["instrument_key"], pos["ltp"])
        pnl=(ltp - pos["entry_price"])*pos["qty"] if pos["side"]=="BUY" else (pos["entry_price"]-ltp)*pos["qty"]
        total+=pnl
    paper["balance"]+=total
    paper["positions"]=[]
    paper["orders"].append({"symbol":"ALL","qty":0,"side":"SQUAREOFF","price":0,"status":f"SQUAREOFF ALL ₹{total:.2f}","time":datetime.now(IST).isoformat()})
    save_paper_fast(paper, force=True)
    return jsonify({"ok":True})

@app.route('/ping')
def ping():
    with live_ltp_lock: cnt=len(live_ltp)
    return f"PONG {datetime.now(IST).strftime('%H:%M:%S')} LTP:{cnt}",200

@app.route('/upstox-login')
def upstox_login():
    api_key=os.environ.get("UPSTOX_API_KEY")
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
            return f"<h1>✅ Token Save!</h1><a href='/flattrade'>FAST App la ja</a>"
        else: return f"Error: {token_data}"
    except Exception as e: return f"Error: {e}"

def is_market_hours():
    now=datetime.now(IST)
    if now.weekday()>=5: return False
    return now.replace(hour=9,minute=0,second=0) <= now <= now.replace(hour=15,minute=30,second=0)

def run_kavyadarsh():
    while True:
        try:
            if not is_market_hours(): time.sleep(60); continue
            import KavyaDarsh
        except: time.sleep(10)

def run_upstox():
    while True:
        try:
            if not is_market_hours(): time.sleep(60); continue
            if not os.path.exists(TOKEN_FILE): time.sleep(60); continue
            import Upstock4
        except: time.sleep(10)

def send_telegram():
    try:
        url=f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload={"chat_id": TELEGRAM_CHAT_ID, "text": f"✅ FAST Flattrade DEPLOYED - No Logging, 200ms Speed", "parse_mode": "HTML"}
        requests.post(url, json=payload, timeout=5)
    except: pass

def keep_alive():
    while True:
        try:
            if is_market_hours():
                try: requests.get('https://ravialgo.onrender.com/ping',timeout=5)
                except: pass
            time.sleep(600)
        except: time.sleep(60)

threading.Thread(target=run_kavyadarsh,daemon=True).start()
threading.Thread(target=send_telegram,daemon=True).start()
threading.Thread(target=run_upstox,daemon=True).start()
threading.Thread(target=keep_alive,daemon=True).start()

if __name__=="__main__":
    port=int(os.environ.get("PORT",10000))
    app.run(host='0.0.0.0',port=port, threaded=True)
