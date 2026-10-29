# -*- coding: utf-8 -*-
"""开一个临时公网隧道，把本机 VideoNote 变成手机能打开的 https 地址（仅自己用）。

用法：
    .\\.venv\\Scripts\\python.exe tools\\tunnel.py

它会：
  1. 检查「鉴权是否已开启」——没开就**拒绝启动**（否则等于把本机文件浏览挂到公网上）
  2. 检查服务在不在，不在就先拉起来
  3. 用 cloudflared 建一条临时隧道，打印 https://xxxx.trycloudflare.com
  4. 按 Ctrl+C 结束，地址随隧道一起失效

为什么用 trycloudflare 的随机域名：
  你的域名 NS 在 Vercel，cloudflared 的固定域名模式要求把域名的 NS 托管到
  Cloudflare（等于迁移 DNS）。只想自己用的话，随机地址完全够——把它存成
  手机书签即可，只是每次重启会变。

安全边界（重要）：
  - 隧道只把 127.0.0.1:8756 转出去，不改服务监听地址
  - 必须设了 VN_PASSWORD 才允许开；远程访问还会额外禁掉 /api/fs/*
  - 别忘了：服务在跑 = 你电脑在跑，关机就断
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLOUDFLARED = ROOT / "tools" / "bin" / "cloudflared.exe"
URL = "http://127.0.0.1:8756"
TUNNEL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def _env_password() -> str:
    """从 .env 里读 VN_PASSWORD（不依赖 python-dotenv）。"""
    env = ROOT / ".env"
    if not env.exists():
        return ""
    for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() == "VN_PASSWORD":
            return v.strip().strip('"').strip("'")
    return ""


def healthy() -> bool:
    try:
        import httpx

        return httpx.get(URL + "/api/health", timeout=2).status_code == 200
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    pw = _env_password()
    if not pw:
        print("✗ 先设密码再开公网。")
        print(f"  打开 {ROOT / '.env'}，把这一行改成你的密码（去掉行首的 #）：")
        print("    VN_PASSWORD=你的强密码")
        print("  改完重启一次服务（双击桌面 VideoNote 图标）再跑本脚本。")
        return 1

    if not CLOUDFLARED.exists():
        print(f"✗ 缺少 cloudflared：{CLOUDFLARED}")
        print("  下载：https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe")
        print(f"  存到：{CLOUDFLARED}")
        return 1

    if not healthy():
        print("服务没在跑，先拉起来…")
        subprocess.Popen(
            [str(ROOT / ".venv" / "Scripts" / "pythonw.exe"), str(ROOT / "launcher.py")],
            cwd=str(ROOT),
        )
        for _ in range(60):
            time.sleep(1)
            if healthy():
                break
        else:
            print("✗ 服务没起来，先双击桌面图标确认能用再来")
            return 1

    print("正在建立隧道…（第一次会有几秒）")
    proc = subprocess.Popen(
        [str(CLOUDFLARED), "tunnel", "--url", URL, "--no-autoupdate"],
        cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )

    shown = False
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            m = TUNNEL_RE.search(line or "")
            if m and not shown:
                shown = True
                addr = m.group(0)
                print("\n" + "=" * 56)
                print("  手机 / 外网打开：", addr)
                print("  打开后会要求输入 .env 里的 VN_PASSWORD")
                print("=" * 56)
                print("  按 Ctrl+C 结束隧道（地址随即失效）\n")
            elif not shown and line.strip():
                print("  " + line.strip()[:100])
    except KeyboardInterrupt:
        print("\n正在关闭隧道…")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            proc.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
