@echo off
chcp 65001 >nul
title AI Cover Studio Phase 1

echo.
echo  ============================================
echo   AI Cover Studio Phase 1
echo  ============================================
echo.

cd /d "%~dp0backend"

:: Quick check
python -c "import fastapi,uvicorn" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Dependencies not installed. Run install.bat first
    pause
    exit /b 1
)

if not exist uploads mkdir uploads
if not exist outputs mkdir outputs
if not exist frontend\dist mkdir frontend\dist
copy /y "..\frontend\index.html" "frontend\dist\index.html" >nul 2>&1

echo  Starting server...

:: Open browser after 3 seconds
start "" cmd /c "timeout /t 3 /nobreak >nul && start http://localhost:8080"

echo  Studio UI : http://localhost:8080
echo  API Docs  : http://localhost:8080/docs
echo.
echo  Press Ctrl+C to stop
echo.

python -m uvicorn main:app --host 0.0.0.0 --port 8080 --log-level info

pause
