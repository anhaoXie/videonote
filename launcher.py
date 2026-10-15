# -*- coding: utf-8 -*-
"""VideoNote 桌面启动器 —— 双击快捷方式就是在跑这个。

它做三件事：
  1. 检查服务在不在；不在就悄悄把它启动起来（不弹黑窗口）
  2. 等它就绪（最多 90 秒）
  3. 打开一个**原生应用窗口**（pywebview）；如果没有 WebView2 运行时就退回
     Chrome/Edge 的「应用模式」（同样没有地址栏，看起来就是个 App）

用法：
    pythonw.exe launcher.py          # 正常启动（无控制台窗口）
    pythonw.exe launcher.py --stop   # 关闭服务
    python.exe  launcher.py --console # 带控制台调试
"""
from __future__ import annotations

import argparse
import ctypes
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = 8756
URL = f"http://{HOST}:{PORT}/"
HEALTH = URL + "api/health"
LOG = ROOT / "data" / "server.log"
ICON = ROOT / "web" / "icon.ico"
APP_TITLE = "VideoNote · 视频学习工作台"

BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


# --------------------------------------------------------------------------
def msgbox(text: str, title: str = APP_TITLE, style: int = 0x40) -> None:
    """没有控制台时，错误也要让用户看得见。"""
    try:
        ctypes.windll.user32.MessageBoxW(0, text, title, style)
    except Exception:  # noqa: BLE001
        print(text)


def healthy(timeout: float = 2.0) -> bool:
    try:
        import httpx

        return httpx.get(HEALTH, timeout=timeout).status_code == 200
    except Exception:  # noqa: BLE001
        return False


def python_exe() -> Path:
    for name in ("python.exe", "pythonw.exe"):
        p = ROOT / ".venv" / "Scripts" / name
        if p.exists():
            return p
    return Path(sys.executable)


def start_server() -> bool:
    """脱离启动服务：这个启动器退出后服务继续跑。"""
    LOG.parent.mkdir(parents=True, exist_ok=True)
    exe = python_exe()
    flags = 0x00000008 | 0x00000200          # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    logf = open(LOG, "ab", buffering=0)
    logf.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} 由桌面启动器启动 =====\n".encode())
    try:
        subprocess.Popen(
            [str(exe), str(ROOT / "run.py"), "--no-browser"],
            cwd=str(ROOT), env=env, stdout=logf, stderr=logf,
            stdin=subprocess.DEVNULL, creationflags=flags, close_fds=True,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        msgbox(f"启动失败：{exc}\n\n请先双击项目里的 run.bat 初始化环境。", style=0x10)
        return False


def wait_ready(seconds: int = 90) -> bool:
    t0 = time.time()
    while time.time() - t0 < seconds:
        if healthy(1.5):
            return True
        time.sleep(1.0)
    return False


def stop_server() -> int:
    """只结束本项目 venv 里的 python 进程，不误伤别的 Python 程序。"""
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" | "
        "Where-Object { $_.CommandLine -like '*videonote*' } | "
        "ForEach-Object { Write-Host ('  结束 PID ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force }"
    )
    print("正在停止 VideoNote…")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], cwd=str(ROOT))
    time.sleep(1.5)
    print("已停止。" if not healthy(2) else "似乎还在运行，请再试一次。")
    return 0


# --------------------------------------------------------------------------
SPLASH = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{height:100%;margin:0;background:#0b0d12;color:#e6e9ef;
  font-family:"Microsoft YaHei",system-ui,sans-serif;display:flex;
  align-items:center;justify-content:center}
.box{text-align:center}
h1{font-size:20px;margin:0 0 6px;font-weight:600}
p{color:#8b93a7;font-size:13px;margin:0}
.bar{width:260px;height:5px;background:#181c25;border-radius:5px;overflow:hidden;margin:22px auto 12px}
.bar>i{display:block;height:100%;width:0;background:linear-gradient(90deg,#4f8cff,#7dd3fc);
  transition:width .3s}
.tip{color:#8b93a7;font-size:12px;line-height:1.8;margin-top:18px}
code{background:#181c25;padding:2px 6px;border-radius:5px}
</style></head><body><div class="box">
<h1>VideoNote 正在启动…</h1>
<p id="st">正在唤醒后台服务</p>
<div class="bar"><i id="p"></i></div>
<div class="tip">第一次启动要加载语音模型，可能多等十几秒<br>
如果一直停在这里：按 Esc 关掉本窗口，双击桌面 <b>VideoNote</b> 图标重试</div>
</div>
<script>
var t0=Date.now(),p=document.getElementById('p'),st=document.getElementById('st');
var timer=setInterval(function(){
  var s=(Date.now()-t0)/1000;
  p.style.width=Math.min(95, s*3)+'%';
  st.textContent='已等待 '+s.toFixed(0)+' 秒';
  fetch('/api/health',{cache:'no-store'}).then(function(r){
    if(r.ok){clearInterval(timer);p.style.width='100%';st.textContent='就绪，正在进入…';
      setTimeout(function(){location.replace('/');},300);}
  }).catch(function(){});
},1000);
</script></body></html>"""


def open_native_window() -> bool:
    """原生窗口（pywebview + WebView2）。成功返回 True，不可用返回 False。"""
    try:
        import webview
    except Exception:  # noqa: BLE001
        return False
    try:
        win = webview.create_window(
            APP_TITLE, html=SPLASH, width=1520, height=960,
            min_size=(1080, 700), background_color="#0b0d12",
        )
        kwargs = {}
        if ICON.exists():
            kwargs["icon"] = str(ICON)
        webview.start(**kwargs)
        return True
    except Exception as exc:  # noqa: BLE001
        log = LOG.parent / "launcher.log"
        log.write_text(f"pywebview 失败，改用浏览器应用模式：{exc}\n", encoding="utf-8")
        return False


def open_browser_app(hold_seconds: int = 0) -> bool:
    """退回方案：Chrome/Edge 的应用模式，同样没有地址栏，像个 App。

    hold_seconds > 0 时只用来自检：到了时间就关掉窗口。
    """
    for path in BROWSERS:
        if not path or not Path(path).exists():
            continue
        profile = ROOT / "data" / "appprofile"
        profile.mkdir(parents=True, exist_ok=True)
        args = [
            path, f"--app={URL}",
            f"--user-data-dir={profile}",
            "--window-size=1520,960",
            "--no-first-run", "--no-default-browser-check",
            "--disable-features=Translate",
        ]
        try:
            proc = subprocess.Popen(args, cwd=str(ROOT), close_fds=True)
        except Exception:  # noqa: BLE001
            continue
        log_note(f"已用 {Path(path).name} 打开应用窗口（PID {proc.pid}）")
        if hold_seconds > 0:
            time.sleep(hold_seconds)
            try:
                proc.terminate()
            except Exception:  # noqa: BLE001
                pass
        return True
    return False


def log_note(text: str) -> None:
    try:
        p = ROOT / "data" / "launcher.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {text}\n")
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description="VideoNote 桌面启动器")
    ap.add_argument("--stop", action="store_true", help="停止后台服务")
    ap.add_argument("--console", action="store_true", help="带控制台输出（调试用）")
    ap.add_argument("--url-only", action="store_true", help="只用系统默认浏览器打开")
    ap.add_argument("--serve-only", action="store_true", help="只把服务拉起来，不打开窗口（自检用）")
    ap.add_argument("--test-window", type=int, default=0, metavar="秒",
                    help="自检：打开应用窗口 N 秒后自动关闭")
    args = ap.parse_args()

    if args.stop:
        return stop_server()

    if not healthy():
        start_server()
        if not wait_ready():                      # 服务没起来
            tail = ""
            if LOG.exists():
                try:
                    tail = "".join(LOG.read_text(encoding="utf-8", errors="replace").splitlines(True)[-8:])
                except Exception:  # noqa: BLE001
                    pass
            msgbox("VideoNote 启动失败（90 秒内没起来）。\n\n"
                   f"日志文件：{LOG}\n\n最近几行：\n{tail[-600:]}", style=0x10)
            return 1

    if args.serve_only:
        print("服务已就绪:", URL)
        return 0

    if args.test_window:
        # 自检：先试原生窗口（若有），否则浏览器应用模式；到点自动关
        import threading

        if not args.url_only:
            def _try_native() -> None:
                open_native_window()

            t = threading.Thread(target=_try_native, daemon=True)
            t.start()
            time.sleep(3)
            if t.is_alive():
                log_note("原生窗口启动成功（自检模式 3 秒后退出，窗口可能仍显示）")
                return 0
        open_browser_app(hold_seconds=args.test_window)
        log_note("浏览器应用模式自检完成")
        return 0

    if args.url_only:
        import webbrowser

        webbrowser.open(URL)
        return 0

    if open_native_window():
        return 0
    if open_browser_app():
        return 0
    import webbrowser

    webbrowser.open(URL)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
