@echo off
REM Voiceover Agent - double-click this file and everything starts.
setlocal
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
  echo.
  echo   No venv found. Run these first:
  echo       py -3.11 -m venv venv
  echo       venv\Scripts\python.exe -m pip install -r requirements.txt
  echo.
  pause
  exit /b 1
)

echo.
echo   Starting Voiceover Agent...
echo   The browser opens by itself. Close this window to stop the app.
echo.

venv\Scripts\python.exe server.py

if errorlevel 1 (
  echo.
  echo   The server stopped. Check the error above.
  pause
)
endlocal
