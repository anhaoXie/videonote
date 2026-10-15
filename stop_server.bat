@echo off
rem Stop the VideoNote server (only the python processes of this project).
chcp 65001 >nul
cd /d "%~dp0"
echo Stopping VideoNote server...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*videonote*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo Done.
timeout /t 3 >nul
