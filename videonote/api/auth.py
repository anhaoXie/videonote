# -*- coding: utf-8 -*-
"""单用户登录（给「手机/外网自用」兜底）。

为什么必须有这一层：
这个项目最初只绑 127.0.0.1，靠「只有本机能连」当唯一防线。一旦通过隧道
暴露到公网（比如 cloudflared），那条防线就不存在了；而 /api/fs/list 能列出
本机任意路径 —— 等于把文件系统的一部分挂到了公网上。所以在开隧道**之前**，
先把门装上。

设计取舍：
- **不做用户表**：单人自用，一个密码足够；多用户/权限是另一个量级的事。
- 密码只来自环境变量 `VN_PASSWORD`（写在 .env 里）。**没配就不启用鉴权**，
  本地开发体验完全不变。
- 会话用 HMAC 签名的 cookie，零新依赖（hmac / hashlib / base64）。
- 放行：静态页面、`/api/auth/*`、`/api/health`（启动器的健康检查要用）。
- 远程来源（非回环地址）**额外禁掉 `/api/fs/*`**：远程根本用不到本机文件浏览。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

COOKIE = "vn_session"
MAX_AGE = 60 * 60 * 24 * 30  # 30 天：自用设备，省得天天登录

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# 基础
# ---------------------------------------------------------------------------
def password() -> str:
    return (os.environ.get("VN_PASSWORD") or "").strip()


def enabled() -> bool:
    return bool(password())


def _secret() -> bytes:
    """会话签名密钥：优先 VN_SECRET，否则从密码派生。

    从密码派生有个好处：改了密码，旧会话自动失效，符合直觉。
    """
    s = (os.environ.get("VN_SECRET") or "").strip()
    if s:
        return s.encode("utf-8")
    return hashlib.sha256(("videonote:" + password()).encode("utf-8")).digest()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue() -> str:
    """签发会话：payload 是过期时间戳，后面跟 HMAC 签名。"""
    exp = int(time.time()) + MAX_AGE
    payload = str(exp).encode("ascii")
    sig = hmac.new(_secret(), payload, hashlib.sha256).digest()
    return f"{_b64(payload)}.{_b64(sig)}"


def valid(token: str) -> bool:
    try:
        p, s = (token or "").split(".")
        payload = _unb64(p)
        sig = _unb64(s)
        expect = hmac.new(_secret(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expect):
            return False
        return int(payload.decode("ascii")) > time.time()
    except Exception:  # noqa: BLE001
        return False


def password_ok(pw: str) -> bool:
    real = password()
    if not real:
        return True
    return hmac.compare_digest((pw or "").encode("utf-8"), real.encode("utf-8"))


def is_local(request: Request) -> bool:
    host = (request.client.host if request.client else "") or ""
    return host in ("127.0.0.1", "::1", "localhost")


# ---------------------------------------------------------------------------
# 中间件用：返回 None 表示放行，否则返回要直接发出的响应
# ---------------------------------------------------------------------------
PUBLIC_PATHS = {"/api/health"}


def guard(request: Request):
    if not enabled():
        return None

    path = request.url.path
    if not path.startswith("/api"):
        return None                      # 静态页面（登录页本身要能打开）
    if path.startswith("/api/auth"):
        return None
    if path in PUBLIC_PATHS:
        return None

    if not valid(request.cookies.get(COOKIE, "")):
        return JSONResponse({"detail": "未登录"}, status_code=401)

    # 已登录，但来源不是本机：不允许浏览本机文件
    if path.startswith("/api/fs/") and not is_local(request):
        return JSONResponse(
            {"detail": "远程访问不提供本机文件浏览，请在手机上改用「上传文件」"},
            status_code=403,
        )
    return None


# ---------------------------------------------------------------------------
# 接口
# ---------------------------------------------------------------------------
class LoginIn(BaseModel):
    password: str


@router.get("/status")
def status(request: Request) -> Dict[str, Any]:
    logged = (not enabled()) or valid(request.cookies.get(COOKIE, ""))
    return {"enabled": enabled(), "logged_in": logged, "local": is_local(request)}


@router.post("/login")
def login(body: LoginIn):
    if not enabled():
        return {"ok": True, "auth": False}
    if not password_ok(body.password):
        raise HTTPException(401, "密码不对")
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        COOKIE, issue(), max_age=MAX_AGE,
        httponly=True, samesite="lax", path="/",
        # 隧道通常是 https，但本机是 http；两边都要能用，所以不设 Secure
        secure=False,
    )
    return resp


@router.post("/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE, path="/")
    return resp
