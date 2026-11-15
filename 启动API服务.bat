@echo off
setlocal
cd /d "%~dp0"
rem ===== HuggingFace mirror settings (required to download models in CN) =====
set "HF_ENDPOINT=https://hf-mirror.com"
set "HF_HUB_DISABLE_XET=1"
set "RUNNER="

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import PyQt5" >nul 2>&1 && set "RUNNER=.venv\Scripts\python.exe"
)
if not defined RUNNER (
    python -c "import PyQt5" >nul 2>&1 && set "RUNNER=python"
)
if not defined RUNNER (
    py -3 -c "import PyQt5" >nul 2>&1 && set "RUNNER=py -3"
)
if not defined RUNNER (
    echo [ERROR] No usable Python with PyQt5 found.
    echo Run: python -m pip install -r requirements.txt
    pause
    exit /b 1
)

echo Starting LingLing API mode ...
echo Web UI: http://127.0.0.1:8000/
echo Keep the desktop pet window open while testing the web page.
echo If the API is exposed outside this machine, set LINGLING_API_TOKEN first.
%RUNNER% main.py --api

echo.
echo ================================================
echo Process exited with code %errorlevel%.
pause
endlocal