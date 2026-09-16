"""ninklang.tech 免登录牌谱中转客户端（通道A）。"""
import re
import time

import httpx

BASE = "https://ninklang.tech"
_PENDING = {"queued", "fetching", "retry_wait", "converting"}


class NinklangError(RuntimeError):
    pass


def _client() -> httpx.Client:
    return httpx.Client(timeout=30.0)


_SHARE_RE = re.compile(r"^\d{6}-[A-Za-z0-9-]{30,}_\d+$")


def normalize_share_url(text: str) -> str:
    text = text.strip()
    if text.startswith("http"):
        if "paipu=" not in text:
            raise ValueError("不是有效的雀魂牌谱分享链接")
        return text
    if _SHARE_RE.match(text):
        return f"https://game.maj-soul.com/1/?paipu={text}"
    raise ValueError("不是有效的雀魂牌谱分享链接或牌谱ID")


def fetch_tenhou(share_url: str, total_timeout: float = 300.0) -> dict:
    """提交分享链接并轮询取回天凤 JSON。"""
    share_url = normalize_share_url(share_url)
    deadline = time.monotonic() + total_timeout
    with _client() as client:
        resp = client.post(f"{BASE}/api/v1/desktop/requests", json={"share_url": share_url})
        if resp.status_code >= 400:
            raise NinklangError(f"中转服务请求失败 HTTP {resp.status_code}")
        payload = resp.json()
        rid = payload.get("request_id")
        token = payload.get("request_token")
        if not rid or not token:
            raise NinklangError("中转服务返回缺少任务信息")
        headers = {"Authorization": f"Request {token}"}
        while True:
            status = str(payload.get("status") or "")
            if status == "ready":
                break
            if status in ("failed", "expired"):
                err = payload.get("error") or {}
                raise NinklangError(
                    f"牌谱获取失败：{err.get('code', status)} {err.get('message', '')}".strip())
            if status not in _PENDING:
                raise NinklangError(f"中转服务未知状态：{status or 'empty'}")
            if time.monotonic() > deadline:
                raise NinklangError("等待牌谱超时，请稍后重试")
            delay = min(max(int(payload.get("poll_after_ms") or 1000) / 1000, 0.5), 5.0)
            time.sleep(delay)
            resp = client.get(f"{BASE}/api/v1/requests/{rid}", headers=headers)
            if resp.status_code >= 400:
                raise NinklangError(f"查询任务失败 HTTP {resp.status_code}")
            payload = resp.json()

        resp = client.get(f"{BASE}/api/v1/requests/{rid}/result", headers=headers)
        if resp.status_code >= 400:
            raise NinklangError(f"获取结果失败 HTTP {resp.status_code}")
        data = resp.json()
        if not isinstance(data.get("name"), list) or not isinstance(data.get("log"), list):
            raise NinklangError("中转服务返回的牌谱数据不完整")
        return data