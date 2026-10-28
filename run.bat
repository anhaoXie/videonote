@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [1/2] first run: creating venv...
  python -m venv .venv
  echo [2/2] installing dependencies: about 300MB
  ".venv\Scripts\python.exe" -m pip install -U pip -i https://pypi.tuna.tsinghua.edu.cn/simple
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
)
set PYTHONUTF8=1

rem No args: use the desktop launcher, which opens a chrome-less app window.
rem Previously this ran run.py, which called webbrowser.open -> a browser TAB,
rem so you had to click start AND find the tab. Now it matches the desktop icon.
if "%~1"=="" (
  start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0launcher.py"
  exit /b 0
)

rem With args: keep the old dev/service mode.
rem   run.bat --no-browser    start server only
rem   run.bat --reload        auto-reload on code change
"%~dp0.venv\Scripts\python.exe" "%~dp0run.py" %*
pause
