@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Creating the build environment...
  py -3.11 -m venv .venv 2>nul || py -m venv .venv
)

echo Installing build tools...
".venv\Scripts\python.exe" -m pip install -r requirements-build.txt
if errorlevel 1 goto :failed

echo Building CastDeck.exe...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed --name CastDeck --add-data "public;public" launcher.py
if errorlevel 1 goto :failed

echo.
echo Build complete: %CD%\dist\CastDeck.exe
pause
exit /b 0

:failed
echo.
echo The build failed. Review the messages above.
pause
exit /b 1
