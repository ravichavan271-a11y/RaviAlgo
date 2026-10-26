"""Render controller for Upstox OAuth and the option_bot only."""

from datetime import datetime, time as day_time
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from zoneinfo import ZoneInfo

from flask import Flask, redirect, request

from oauth_flow import build_login_url, handle_callback, send_login_reminder


IST = ZoneInfo("Asia/Kolkata")
LOGIN_AT = day_time(8, 30)
START_AT = day_time(9, 1)
STOP_AT = day_time(15, 45)
ROOT = Path(__file__).resolve().parent
app = Flask(__name__)
_scheduler_started = False
_scheduler_lock = threading.Lock()
BOT_STATUS = {"running": False, "last_start": None, "restarts": 0, "last_exit": None}


def now_ist():
    return datetime.now(IST)


def run_option_bot_session():
    """Supervise only option_bot.py on Render; never start Upstock5 here."""
    script = ROOT / "option_bot.py"
    while now_ist().time() < STOP_AT:
        process = None
        try:
            if not script.is_file():
                raise FileNotFoundError("option_bot.py is missing")
            BOT_STATUS.update({"running": True, "last_start": now_ist().isoformat()})
            print(f"Starting option_bot.py at {now_ist():%H:%M:%S IST}.", flush=True)
            process = subprocess.Popen(
                [sys.executable, "-u", str(script)], cwd=ROOT, env=os.environ.copy()
            )
            while now_ist().time() < STOP_AT and process.poll() is None:
                time.sleep(2)
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                print("Stopped option_bot.py at the 15:45 IST session end.", flush=True)
                break

            BOT_STATUS["last_exit"] = process.returncode
            BOT_STATUS["running"] = False
            if now_ist().time() < STOP_AT:
                BOT_STATUS["restarts"] += 1
                print(
                    f"option_bot.py exited ({process.returncode}); retrying in 30 seconds.",
                    flush=True,
                )
                time.sleep(30)
        except Exception as exc:
            BOT_STATUS["running"] = False
            BOT_STATUS["last_exit"] = type(exc).__name__
            BOT_STATUS["restarts"] += 1
            print(f"option_bot.py start failed: {type(exc).__name__}; retrying in 30 seconds.", flush=True)
            time.sleep(30)
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    BOT_STATUS["running"] = False


def daily_scheduler():
    login_sent_date = None
    session_started_date = None
    while True:
        now = now_ist()
        if now.weekday() < 5:
            if LOGIN_AT <= now.time() < START_AT and login_sent_date != now.date():
                try:
                    if send_login_reminder("RENDER"):
                        login_sent_date = now.date()
                    else:
                        print("Login reminder was not sent; retrying in 30 seconds.", flush=True)
                        time.sleep(30)
                except Exception as exc:
                    print(f"Login reminder failed: {type(exc).__name__}; retrying in 30 seconds.", flush=True)
                    time.sleep(30)

            if (
                START_AT <= now.time() < STOP_AT
                and session_started_date != now.date()
            ):
                session_started_date = now.date()
                with _scheduler_lock:
                    run_option_bot_session()

        time.sleep(5)


@app.get("/")
def home():
    return (
        "<h2>RaviAlgo controller</h2>"
        f"<p>Time: {now_ist():%Y-%m-%d %H:%M:%S IST}</p>"
        "<p>Render runs option_bot.py; Silly runs Upstock5.py.</p>"
        "<p><a href='/status'>Status</a></p>"
    )


@app.get("/ping")
def ping():
    return "ok", 200


@app.get("/status")
def status():
    return {
        "time_ist": now_ist().isoformat(),
        "market_session": "09:01-15:45 IST, weekdays",
        "render_bot": "option_bot.py",
        "silly_bot": "Upstock5.py",
        "option_bot": dict(BOT_STATUS),
    }


@app.get("/upstox-login")
def upstox_login():
    try:
        return redirect(build_login_url(), code=302)
    except Exception as exc:
        print(f"Could not create Upstox login link: {type(exc).__name__}.", flush=True)
        return "Upstox login link is not configured. Contact the app administrator.", 503


@app.get("/upstox/callback")
@app.get("/oauth/callback")
def upstox_callback():
    status_code, body = handle_callback(request.full_path)
    return body, status_code, {"Content-Type": "text/plain; charset=utf-8"}


def start_scheduler_once():
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True
    threading.Thread(target=daily_scheduler, name="ravi-daily-scheduler", daemon=True).start()


start_scheduler_once()

