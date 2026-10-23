@echo off
setlocal EnableDelayedExpansion
title PharmaWatch Launcher
cd /d "%~dp0"

echo ==============================================
echo   PharmaWatch - one-click launcher
echo ==============================================
echo.

REM ---------- 1. Docker ----------
docker info >nul 2>&1
if not errorlevel 1 goto docker_ready

echo [1/4] Starting Docker Desktop...
if exist "%ProgramFiles%\Docker\Docker\Docker Desktop.exe" (
    start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
) else (
    echo ERROR: Docker Desktop not found. Install it from https://www.docker.com/products/docker-desktop/
    pause
    exit /b 1
)

set /a tries=0
:wait_docker
timeout /t 5 /nobreak >nul
docker info >nul 2>&1
if not errorlevel 1 goto docker_ready
set /a tries+=1
if !tries! lss 36 goto wait_docker
echo ERROR: Docker did not start within 3 minutes.
pause
exit /b 1

:docker_ready
echo [1/4] Docker is running.

REM ---------- 2. Kafka stack ----------
echo [2/4] Starting Kafka stack (zookeeper, kafka, ui, producer, consumer)...
set /a attempt=0
:kafka_try
set /a attempt+=1
docker compose -f docker/compose.yml up -d --build zookeeper kafka kafka-ui kafka-producer kafka-consumer
if not errorlevel 1 goto kafka_ok
if !attempt! geq 3 (
    echo WARNING: Kafka failed to start after 3 attempts. Continuing without it.
    goto kafka_done
)
echo Kafka start failed - retrying in 30s ^(stale ZooKeeper session usually clears^)...
timeout /t 30 /nobreak >nul
goto kafka_try
:kafka_ok
echo [2/4] Kafka stack is up.
:kafka_done

REM ---------- 3. Flask app ----------
netstat -ano | findstr /R /C:":5000 .*LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo [3/4] Something is already listening on port 5000 - assuming PharmaWatch is running.
    goto open_browser
)

echo [3/4] Starting PharmaWatch web server...
set "PY=venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
start "PharmaWatch Server" /min cmd /c ""%PY%" backend\app.py"

REM wait until the server answers
set /a tries=0
:wait_app
timeout /t 2 /nobreak >nul
powershell -NoProfile -Command "try{(Invoke-WebRequest http://127.0.0.1:5000 -UseBasicParsing -TimeoutSec 3).StatusCode|Out-Null;exit 0}catch{exit 1}" >nul 2>&1
if not errorlevel 1 goto open_browser
set /a tries+=1
if !tries! lss 30 goto wait_app
echo WARNING: Server did not respond in time. Check the "PharmaWatch Server" window.

:open_browser
echo [4/4] Opening dashboard...
start "" http://127.0.0.1:5000
start "" http://localhost:8086

echo.
echo ==============================================
echo   PharmaWatch   : http://127.0.0.1:5000
echo   Kafka UI      : http://localhost:8086
echo ==============================================
echo.
echo Leave this window open while using the app.
echo Press any key here to STOP everything.
pause >nul

echo Stopping...
taskkill /FI "WINDOWTITLE eq PharmaWatch Server*" /T /F >nul 2>&1
docker compose -f docker/compose.yml stop
echo Done.
timeout /t 3 >nul
