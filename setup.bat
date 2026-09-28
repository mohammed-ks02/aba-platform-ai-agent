@echo off
REM =====================================================================
REM  ABA Fusion AI Agent - one-click setup (Windows CMD)
REM  Creates .venv, installs requirements.txt, installs Playwright Chromium.
REM  Safe to re-run any time: it always syncs with the CURRENT
REM  requirements.txt, so after every project update just run setup.bat
REM  again and the environment stays in sync automatically.
REM =====================================================================
setlocal
cd /d "%~dp0"

echo [1/4] Checking Python...
where python >nul 2>nul || (echo ERROR: python not found on PATH. Install Python 3.10+ from python.exe & exit /b 1)
python --version

if not exist ".venv" (
    echo [2/4] Creating virtual environment .venv ...
    python -m venv .venv || (echo ERROR: could not create venv & exit /b 1)
) else (
    echo [2/4] Virtual environment already exists - reusing it.
)

call .venv\Scripts\activate.bat || (echo ERROR: venv activation failed & exit /b 1)

echo [3/4] Installing/updating dependencies from requirements.txt ...
python -m pip install --upgrade pip >nul
pip install -r requirements.txt || (echo ERROR: dependency install failed & exit /b 1)

echo [4/5] Ensuring Playwright Chromium browser is installed ...
python -m playwright install chromium || (echo WARNING: chromium download failed - live/visual testing will fall back to urllib & exit /b 1)

echo [5/5] Preparing .env config file...
if not exist ".env" if exist "env.example" (
    copy "env.example" ".env" >nul
    echo  Created .env from env.example -- EDIT IT with your real keys and password!
) else (
    echo  .env already exists - keeping it untouched.
)

echo.
echo =====================================================================
echo  Setup complete.  Start the web interface with:  run.bat
echo  (or double-click run.bat)
echo =====================================================================
endlocal
