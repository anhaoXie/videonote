@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [1/2] 首次运行，创建虚拟环境...
  python -m venv .venv
  echo [2/2] 安装依赖（约 300MB，国内走清华镜像）...
  ".venv\Scripts\python.exe" -m pip install -U pip -i https://pypi.tuna.tsinghua.edu.cn/simple
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
)
set PYTHONUTF8=1
".venv\Scripts\python.exe" run.py %*
pause
