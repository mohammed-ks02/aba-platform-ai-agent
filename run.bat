@echo off
REM =====================================================================
REM  ABA Fusion AI Agent - start the Web UI (Windows CMD)
REM  Auto-runs setup if needed, then launches http://127.0.0.1:8787
REM =====================================================================
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo No virtual environment found - running setup first...
    call setup.bat || exit /b 1
)

call .venv\Scripts\activate.bat || (echo ERROR: venv activation failed & exit /b 1)

REM Keep deps in sync with requirements.txt on EVERY start (fast no-op when
REM nothing changed). This guarantees the env matches the latest code even
REM after you pull new commits.
echo Syncing dependencies with requirements.txt ...
pip install -q -r requirements.txt

echo Starting ABA Fusion AI Agent Web UI ...
echo Open your browser at:  http://127.0.0.1:8787   (Ctrl+C to stop)
python webui\app.py

endlocal
