@echo off
rem Development launcher (Windows): opens the desktop HUD, which starts the
rem Python backend by itself. One-time setup:
rem   python -m venv .venv ^&^& .venv\Scripts\pip install -e ".[dev]"
cd /d "%~dp0..\desktop"
if not exist node_modules call npm install
npm start
