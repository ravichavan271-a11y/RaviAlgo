"""Silly runner: schedule only Upstock5.py on Indian weekdays."""

from datetime import datetime, time as day_time, timedelta
import os
import subprocess
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo


IST = ZoneInfo("Asia/Kolkata")
START = day_time(9, 1)
STOP = day_time(15, 45)
SCRIPT = Path(__file__).resolve().parent / "Upstock5.py"


def now_ist():
    return datetime.now(IST)


def stop_process(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def run_market_session():
    """Run exactly one data bot here; Render owns option_bot.py."""
    while now_ist().time() < STOP:
        process = None
        try:
            if not SCRIPT.is_file():
                raise FileNotFoundError("Upstock5.py is missing")
            print(f"Starting Upstock5.py at {now_ist():%H:%M:%S IST}.", flush=True)
            process = subprocess.Popen(
                [sys.executable, "-u", str(SCRIPT)],
                cwd=SCRIPT.parent,
                env=os.environ.copy(),
            )
            while now_ist().time() < STOP and process.poll() is None:
                time.sleep(3)
            if process.poll() is None:
                print("Stopping Upstock5.py at the 15:45 IST session end.", flush=True)
                break
            print(
                f"Upstock5.py exited ({process.returncode}); retrying in 30 seconds.",
                flush=True,
            )
            time.sleep(30)
        except Exception as exc:
            print(f"Upstock5.py start failed: {type(exc).__name__}; retrying in 30 seconds.", flush=True)
            time.sleep(30)
        finally:
            stop_process(process)


def next_start(now):
    candidate = datetime.combine(now.date(), START, IST)
    if now >= candidate:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def main():
    print("Silly is assigned Upstock5.py only; Render is assigned option_bot.py.", flush=True)
    session_date = None
    while True:
        now = now_ist()
        if now.weekday() < 5 and START <= now.time() < STOP and session_date != now.date():
            session_date = now.date()
            run_market_session()
            continue

        target = next_start(now)
        delay = max(1, (target - now).total_seconds())
        print(f"Waiting until {target:%A %H:%M IST} to start Upstock5.py.", flush=True)
        time.sleep(min(delay, 3600))


if __name__ == "__main__":
    main()

