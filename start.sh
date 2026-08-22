#!/bin/bash
# Fix WORKER TIMEOUT - use custom gunicorn config with 300 sec timeout
echo "Starting with gunicorn_config.py (timeout 300 sec, workers 1)"
gunicorn --config gunicorn_config.py main:app
