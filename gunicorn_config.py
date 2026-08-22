# Gunicorn config - Fix WORKER TIMEOUT
timeout = 300  # 5 min instead of 30 sec
workers = 1
threads = 2
worker_class = "sync"
preload_app = False  # Don't preload - let threads start after worker boot
keepalive = 2
max_requests = 0  # No restart on requests
graceful_timeout = 30
