@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Python 3.11 or newer is required. Install it from https://www.python.org/downloads/
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Setting up CastDeck for the first time...
  py -3.11 -m venv .venv 2>nul || py -m venv .venv
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    pause
    exit /b 1
  )
)
start "" http://127.0.0.1:4173
".venv\Scripts\python.exe" server.py --host 0.0.0.0
endlocal
