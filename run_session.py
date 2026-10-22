"""Run both trading scripts during the India weekday session."""

from datetime import datetime, time as day_time
import os
from pathlib import Path
import subprocess
import sys
import time
from zoneinfo import ZoneInfo


IST = ZoneInfo("Asia/Kolkata")
START = day_time.fromisoformat(os.environ.get("SESSION_START", "09:00"))
STOP = day_time.fromisoformat(os.environ.get("SESSION_STOP", "15:45"))
ROOT = Path(__file__).resolve().parent


def now_ist():
    return datetime.now(IST)


def stop_processes(processes):
    for process in processes:
        if process.poll() is None:
            process.terminate()
    deadline = time.monotonic() + 30
    for process in processes:
        if process.poll() is None:
            remaining = max(0, deadline - time.monotonic())
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def main():
    now = now_ist()
    if now.weekday() >= 5:
        print(f"Weekend in India ({now:%A}); nothing to run.", flush=True)
        return
    if now.time() >= STOP:
        print(f"Past 3:45 PM IST ({now:%H:%M}); nothing to run.", flush=True)
        return
    if now.time() < START:
        wait_seconds = (datetime.combine(now.date(), START, IST) - now).total_seconds()
        print(f"Waiting {int(wait_seconds)} seconds until 9:00 AM IST.", flush=True)
        time.sleep(wait_seconds)

    if now_ist().weekday() >= 5:
        return

    scripts = [name.strip() for name in os.environ.get(
        "SESSION_SCRIPTS", "option_bot.py,Upstock4.py"
    ).split(",") if name.strip()]
    if not scripts:
        raise RuntimeError("SESSION_SCRIPTS did not name any scripts.")
    processes = []
    try:
        for script in scripts:
            print(f"Starting {script} at {now_ist():%Y-%m-%d %H:%M:%S %Z}.", flush=True)
            processes.append(subprocess.Popen([sys.executable, "-u", str(ROOT / script)], cwd=ROOT))

        while now_ist().time() < STOP:
            failed = [p for p in processes if p.poll() is not None]
            if failed:
                codes = [p.returncode for p in failed]
                raise RuntimeError(f"A trading script exited early (exit codes: {codes}); stopping all scripts.")
            if all(p.poll() is not None for p in processes):
                print("Both scripts exited before 3:45 PM IST.", flush=True)
                return
            time.sleep(5)
        print(f"Stopping both scripts at {now_ist():%H:%M:%S %Z}.", flush=True)
    finally:
        stop_processes(processes)


if __name__ == "__main__":
    main()

