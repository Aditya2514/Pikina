#!/usr/bin/env bash
# Development launcher (Linux/macOS): opens the desktop HUD, which starts the
# Python backend by itself. One-time setup:
#   python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
set -euo pipefail
cd "$(dirname "$0")/../desktop"
[ -d node_modules ] || npm install
npm start
