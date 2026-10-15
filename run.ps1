# VideoNote 启动脚本（PowerShell）
# 用法: 右键「使用 PowerShell 运行」，或  .\run.ps1
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
Set-Location $PSScriptRoot

$py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) {
    Write-Host '[1/2] 创建虚拟环境...' -ForegroundColor Cyan
    python -m venv .venv
    Write-Host '[2/2] 安装依赖（约 300MB，走清华镜像）...' -ForegroundColor Cyan
    & $py -m pip install -U pip -i https://pypi.tuna.tsinghua.edu.cn/simple
    & $py -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
}

& $py run.py @args
