@echo off
REM ==========================================================================
REM  Voiceover Agent - one-time setup.
REM  Checks what is already on this PC and installs ONLY what is missing,
REM  then builds the Python environment. Run it from inside the project folder.
REM ==========================================================================
setlocal
cd /d "%~dp0"

echo.
echo ==========================================================
echo   Voiceover Agent - checking your PC
echo ==========================================================
echo.

set NEED_RESTART=0

REM --- winget (needed to install the rest) -------------------------------
where winget >nul 2>&1
if errorlevel 1 (
  echo [X] winget not found. Update "App Installer" from the Microsoft Store,
  echo     then run this again. Or install the tools by hand - see README.md.
  echo.
  pause
  exit /b 1
)

REM --- Python 3.11 ------------------------------------------------------
py -3.11 --version >nul 2>&1
if errorlevel 1 (
  echo [ ] Python 3.11 ... not found, installing
  winget install -e --id Python.Python.3.11 --accept-source-agreements --accept-package-agreements
  set NEED_RESTART=1
) else (
  for /f "tokens=*" %%v in ('py -3.11 --version') do echo [OK] %%v already installed
)

REM --- Git -------------------------------------------------------------
where git >nul 2>&1
if errorlevel 1 (
  echo [ ] Git ... not found, installing
  winget install -e --id Git.Git --accept-source-agreements --accept-package-agreements
  set NEED_RESTART=1
) else (
  echo [OK] Git already installed
)

REM --- FFmpeg ----------------------------------------------------------
where ffmpeg >nul 2>&1
if errorlevel 1 (
  echo [ ] FFmpeg ... not found, installing
  winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
  set NEED_RESTART=1
) else (
  echo [OK] FFmpeg already installed
)

REM --- Visual C++ Redistributable (torch needs it) --------------------
REM  winget skips this automatically if it is already present.
echo [..] Visual C++ Redistributable ... making sure it is present
winget install -e --id Microsoft.VCRedist.2015+.x64 --accept-source-agreements --accept-package-agreements >nul 2>&1

echo.
if "%NEED_RESTART%"=="1" (
  echo ==========================================================
  echo   Some tools were just installed.
  echo   CLOSE this window, open a NEW Command Prompt, and run
  echo   setup.bat again so Windows picks up the new programs.
  echo ==========================================================
  echo.
  pause
  exit /b 0
)

echo ==========================================================
echo   All tools present. Building the Python environment...
echo ==========================================================
echo.

if not exist "venv\Scripts\python.exe" (
  echo [..] Creating virtual environment
  py -3.11 -m venv venv
)

echo [..] Upgrading pip
venv\Scripts\python.exe -m pip install --upgrade pip

echo [..] Installing torch (CPU build - this is a large download, be patient)
venv\Scripts\python.exe -m pip install torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cpu
if errorlevel 1 goto failed

echo [..] Installing the rest of the dependencies
venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto failed

echo.
echo ==========================================================
echo   Setup complete. Double-click start.bat to run the app.
echo   (The first video you make downloads the AI models, ~7 GB,
echo    one time, then cached.)
echo ==========================================================
echo.
pause
exit /b 0

:failed
echo.
echo   Something failed during install. Check the error above.
echo   The most common cause is the Visual C++ Redistributable - if
echo   torch would not import, install it and run setup.bat again.
echo.
pause
exit /b 1
