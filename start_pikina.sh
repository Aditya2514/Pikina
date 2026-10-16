#!/usr/bin/env bash
# Pikina OS launcher for Linux: starts the Python backend, then the Electron HUD.
# Closing the HUD stops the backend.
set -euo pipefail
cd "$(dirname "$0")"

PY=python3
if [ -x venv/bin/python ]; then PY=venv/bin/python; fi
if [ -x .venv/bin/python ]; then PY=.venv/bin/python; fi

"$PY" backend_server.py &
BACKEND_PID=$!
trap 'kill "$BACKEND_PID" 2>/dev/null || true' EXIT

# Wait (max ~15s) for the backend to accept connections
PORT="${BACKEND_PORT:-5001}"
for _ in $(seq 1 30); do
  if (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then break; fi
  sleep 0.5
done

cd frontend
[ -d node_modules ] || npm install
npm start
