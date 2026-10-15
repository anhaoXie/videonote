# VideoNote 桌面快捷方式安装脚本
# 在桌面和开始菜单创建「VideoNote」图标（一键启动原生应用窗口）
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'
$launcher = Join-Path $root 'launcher.py'
$icon = Join-Path $root 'web\icon.ico'
$stopBat = Join-Path $root 'stop_server.bat'

if (-not (Test-Path $pythonw)) { $pythonw = Join-Path $root '.venv\Scripts\python.exe' }
if (-not (Test-Path $pythonw)) { throw "找不到虚拟环境，请先运行 run.bat 初始化" }

$desktop = [Environment]::GetFolderPath('Desktop')
$startMenu = Join-Path ([Environment]::GetFolderPath('Programs')) 'VideoNote'
New-Item -ItemType Directory -Force -Path $startMenu | Out-Null

$shell = New-Object -ComObject WScript.Shell

function New-Link($path, $target, $arguments, $workdir, $iconPath, $desc) {
    $sc = $shell.CreateShortcut($path)
    $sc.TargetPath = $target
    if ($arguments) { $sc.Arguments = $arguments }
    $sc.WorkingDirectory = $workdir
    if ($iconPath -and (Test-Path $iconPath)) { $sc.IconLocation = "$iconPath,0" }
    $sc.Description = $desc
    $sc.WindowStyle = 1
    $sc.Save()
    Write-Host "  已创建: $path"
}

$args = '"' + $launcher + '"'
Write-Host '正在创建快捷方式…'

# 桌面
New-Link (Join-Path $desktop 'VideoNote.lnk') $pythonw $args $root $icon '视频学习工作台：识别 / 摘要 / 字幕 / 跟读'
New-Link (Join-Path $desktop '停止 VideoNote.lnk') $stopBat '' $root $icon '停止 VideoNote 后台服务'

# 开始菜单
New-Link (Join-Path $startMenu 'VideoNote.lnk') $pythonw $args $root $icon '视频学习工作台'
New-Link (Join-Path $startMenu '停止 VideoNote.lnk') $stopBat '' $root $icon '停止 VideoNote 后台服务'

Write-Host ''
Write-Host '完成！现在可以：'
Write-Host '  · 双击桌面上的「VideoNote」图标启动（会打开一个独立窗口，像 App 一样）'
Write-Host '  · 双击「停止 VideoNote」图标关闭后台服务'
