@echo off
REM Voiceover Agent - double-click this file and everything starts.
setlocal
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
  echo.
  echo   venv nahi mila. Pehle ye chalayein:
  echo       py -3.11 -m venv venv
  echo       venv\Scripts\python.exe -m pip install -r requirements.txt
  echo.
  pause
  exit /b 1
)

echo.
echo   Voiceover Agent shuru ho raha hai...
echo   Browser khud khul jayega. Band karne ke liye ye window band kar dein.
echo.

venv\Scripts\python.exe server.py

if errorlevel 1 (
  echo.
  echo   Server band ho gaya. Upar wali error dekh lein.
  pause
)
endlocal
