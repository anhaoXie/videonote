@echo off
rem ===================================================================
rem  VideoNote background launcher (no console window, logs to data\server.log)
rem  ASCII only on purpose: Chinese comments in .bat break cmd's parser
rem  when the file is UTF-8 but the console codepage is GBK.
rem  Usage: double-click this file. Stop with stop_server.bat
rem ===================================================================
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

if not exist "data" mkdir data
echo. >> "data\server.log"
echo ===== %date% %time% start ===== >> "data\server.log"

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] venv missing, run run.bat first >> "data\server.log"
  exit /b 1
)

".venv\Scripts\python.exe" run.py --no-browser >> "data\server.log" 2>&1
echo ===== %date% %time% exit %ERRORLEVEL% ===== >> "data\server.log"
