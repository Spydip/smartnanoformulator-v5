@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python 3.10 or newer is required. Install it from https://www.python.org/downloads/
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Creating a private Python environment for SmartNanoFormulator v5.0.3...
  python -m venv .venv
  if errorlevel 1 (echo Could not create the virtual environment.& pause & exit /b 1)
)
set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
"%PYTHON_EXE%" -c "import reportlab, numpy, pandas, sklearn, joblib, openpyxl" >nul 2>nul
if errorlevel 1 (
  echo Installing application and ML dependencies into .venv...
  "%PYTHON_EXE%" -m pip install --upgrade pip
  if errorlevel 1 (echo pip upgrade failed.& pause & exit /b 1)
  "%PYTHON_EXE%" -m pip install -r requirements.txt
  if errorlevel 1 (echo Dependency installation failed.& pause & exit /b 1)
)
rem Clear any inherited PORT so the dedicated v5 local port is always used.
set "PORT="
set "SMARTNANO_HOST=127.0.0.1"
echo Starting SmartNanoFormulator v5.0.3 on http://127.0.0.1:8005
start "SmartNanoFormulator v5.0.3 - server on port 8005" cmd /k ""%PYTHON_EXE%" "%~dp0server.py" 8005"
echo Waiting for the v5.0.3 server to become ready...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$u='http://127.0.0.1:8005/api/health'; for($i=0;$i -lt 60;$i++){ try { $r=Invoke-WebRequest -UseBasicParsing -Uri $u -TimeoutSec 2; if($r.StatusCode -eq 200 -and $r.Content -match '5.0.3'){ Start-Process 'http://127.0.0.1:8005'; exit 0 } } catch {}; Start-Sleep -Seconds 1 }; Write-Host 'v5.0.3 did not become ready. Check the separate server window.'; exit 1"
if errorlevel 1 (
  echo.
  echo Browser not opened. Check the server window, then try http://127.0.0.1:8005
  pause
  exit /b 1
)
echo.
echo SmartNanoFormulator v5.0.3 is available at http://127.0.0.1:8005
echo Keep the separate server window open while using the app.
exit /b 0
