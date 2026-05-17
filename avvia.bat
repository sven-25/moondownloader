@echo off
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo [ERRORE] Python non trovato nel PATH.
    pause
    exit /b 1
)

python -c "import aiohttp, playwright, PIL" >nul 2>&1
if errorlevel 1 (
    echo Installazione dipendenze...
    python -m pip install -r requirements.txt || goto :err
    python -m playwright install chromium || goto :err
)

if not exist "%LOCALAPPDATA%\ms-playwright" (
    echo Installazione browser Chromium...
    python -m playwright install chromium || goto :err
)

start "" pythonw gen_1.py
exit /b 0

:err
echo [ERRORE] Installazione fallita.
pause
exit /b 1
