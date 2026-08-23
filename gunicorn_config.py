import os
# Gunicorn config - Fix WORKER TIMEOUT + PORT BIND
bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"
timeout = 300  # 5 min instead of 30 sec
workers = 1
threads = 4  # Increased threads for Flask + background
worker_class = "sync"
preload_app = False  # Don't preload - let threads start after worker boot
keepalive = 5
max_requests = 0  # No restart on requests
graceful_timeout = 30
loglevel = "info"
accesslog = "-"
errorlog = "-"
