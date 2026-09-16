"""管理后台会话鉴权（账号密码登录 + HMAC 签名 cookie）。

同时保留 ADMIN_TOKEN Bearer 兼容通道，方便 CI / API 脚本调用。
"""
import hashlib
import hmac
import time

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Response

import app.config as config

router = APIRouter()

COOKIE_NAME = "mla_session"
_TOKEN_SCHEME = "Bearer "


def _sign(user: str, exp: int) -> str:
    """生成 HMAC-SHA256 签名 cookie 值（user:exp:sig）。"""
    secret = config.ensure_session_secret().encode()
    msg = f"{user}:{exp}".encode()
    sig = hmac.new(secret, msg, hashlib.sha256).hexdigest()
    return f"{user}:{exp}:{sig}"


def _verify(cookie_value: str | None) -> str | None:
    """校验 cookie 签名，返回用户名（失败返回 None）。"""
    if not cookie_value:
        return None
    parts = cookie_value.split(":")
    if len(parts) != 3:
        return None
    user, exp_str, sig = parts
    try:
        exp = int(exp_str)
    except ValueError:
        return None
    if exp < time.time():
        return None
    expected = _sign(user, exp)
    if not hmac.compare_digest(expected, cookie_value):
        return None
    if user != config.ensure_admin_username():
        return None
    return user


def _check_password(password: str) -> bool:
    """常量时间比较密码。"""
    expected = config.ensure_admin_password().encode()
    return hmac.compare_digest(password.encode(), expected)


def current_user(
    mla_session: str | None = Cookie(default=None, alias=COOKIE_NAME),
    authorization: str = Header(default=""),
) -> str:
    """依赖：校验 cookie 或 Bearer token，返回当前用户名。

    无有效会话抛 401。Bearer 通道用于 API/CI 调用。
    """
    user = _verify(mla_session)
    if user:
        return user
    if authorization.startswith(_TOKEN_SCHEME):
        token = authorization[len(_TOKEN_SCHEME):].strip()
        if hmac.compare_digest(token, config.ensure_admin_token()):
            return config.ensure_admin_username()
    raise HTTPException(401, "未登录或会话已过期")


@router.post("/login")
def login(body: dict, response: Response):
    """账号密码登录，成功后下发签名 cookie。"""
    user = str(body.get("username") or "").strip()
    pwd = str(body.get("password") or "")
    if not (user == config.ensure_admin_username() and _check_password(pwd)):
        raise HTTPException(401, "账号或密码错误")
    exp = int(time.time()) + config.SESSION_MAX_AGE
    value = _sign(user, exp)
    response.set_cookie(
        COOKIE_NAME, value,
        max_age=config.SESSION_MAX_AGE,
        httponly=True, samesite="lax", path="/",
    )
    return {"username": user, "expires_in": config.SESSION_MAX_AGE}


@router.post("/logout")
def logout(response: Response):
    """清掉会话 cookie。"""
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: str = Depends(current_user)):
    """探测当前登录态（依赖里抛 401）。"""
    return {"username": user}
