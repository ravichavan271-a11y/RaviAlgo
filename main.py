import subprocess
import time

print("Starting दोन्ही स्कॅनर्स...")

p1 = subprocess.Popen(["python", "Upstock4.py"])
p2 = subprocess.Popen(["python", "KavyaDarsh.py"])

try:
    p1.wait()
    p2.wait()
except KeyboardInterrupt:
    print("Stopping Scanners...")