#!/usr/bin/env bash
# Every Codespace start: launch the engine (port 8000) and the app (port 8501) in the background.
cd "$(dirname "$0")/.."
mkdir -p /tmp/filinglens
pkill -f "uvicorn api.main:app" 2>/dev/null || true
pkill -f "streamlit run web/app.py" 2>/dev/null || true
setsid nohup python -m uvicorn api.main:app --host 127.0.0.1 --port 8000 > /tmp/filinglens/api.log 2>&1 < /dev/null &
for _ in $(seq 1 90); do curl -fs http://127.0.0.1:8000/status > /dev/null && break; sleep 1; done
setsid nohup python -m streamlit run web/app.py --server.port 8501 --server.headless true \
    --browser.gatherUsageStats false > /tmp/filinglens/app.log 2>&1 < /dev/null &
for _ in $(seq 1 60); do curl -fs http://127.0.0.1:8501/ > /dev/null && break; sleep 1; done
echo "FilingLens is starting: the app opens in a new tab (Ports panel → 8501). Logs: /tmp/filinglens/"
