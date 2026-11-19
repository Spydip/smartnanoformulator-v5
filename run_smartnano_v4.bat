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
  echo Creating a private Python environment for SmartNanoFormulator v4.0.0...
  python -m venv .venv
  if errorlevel 1 (
    echo Could not create the virtual environment.
    pause
    exit /b 1
  )
)
set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"

"%PYTHON_EXE%" -c "import reportlab, numpy, pandas, sklearn, joblib, openpyxl" >nul 2>nul
if errorlevel 1 (
  echo Installing application and ML dependencies into .venv...
  "%PYTHON_EXE%" -m pip install --upgrade pip
  if errorlevel 1 (
    echo Could not upgrade pip. Check your internet connection.
    pause
    exit /b 1
  )
  "%PYTHON_EXE%" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Dependency installation failed. Check your internet connection and Python installation.
    pause
    exit /b 1
  )
)

set "PORT=8003"
set "SMARTNANO_HOST=127.0.0.1"
echo Starting SmartNanoFormulator v4.0.0 on http://127.0.0.1:8003
start "SmartNanoFormulator v4.0.0 - server on port 8003" cmd /k ""%PYTHON_EXE%" "%~dp0server.py" 8003"
timeout /t 3 /nobreak >nul
start "" "http://127.0.0.1:8003"
echo.
echo v4.0.0 is running at http://127.0.0.1:8003
echo Close the separate server window to stop v4.0.0.
exit /b 0
