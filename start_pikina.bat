@echo off
cd /d "%~dp0"
start /B python backend_server.py

cd frontend
start /B npm start
