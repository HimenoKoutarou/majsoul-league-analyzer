"""雀魂 DHS / 大厅客户端（真实网络实现；沙箱内不可达，联调留给部署环境）。"""
import hashlib
import hmac
import uuid as uuidlib
from types import SimpleNamespace

import httpx
from google.protobuf.json_format import ParseDict

import app.config as config
from . import liqi_combined_pb2 as pb

from app.services.majsoul.codec import LiqiChannel, MajsoulApiError

MS_HOST = "https://game.maj-soul.com"
RECORD_HOST = "https://record-v2.maj-soul.com:5333/majsoul/game_record"


def majsoul_password_hash(password: str) -> str:
    return hmac.new(b"lailai", password.encode(), hashlib.sha256).hexdigest()


async def discover_lobby_endpoint() -> tuple[str, str]:
    """返回一个当前可用的大厅 WebSocket 地址和版本。"""
    endpoints, version = await discover_lobby_endpoints()
    if not endpoints:
        raise MajsoulApiError(0, "雀魂大厅配置中没有可用网关")
    return endpoints[0], version


async def discover_lobby_endpoints() -> tuple[list[str], str]:
    """读取当前版本配置并返回候选大厅网关。

    旧实现还会调用 gateways 的 recommend_list。该接口目前经常不响应，
    而 version 配置已经直接返回 route-* 网关，因此直接使用配置中的地址。
    """
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            version_response = await client.get(f"{MS_HOST}/1/version.json")
            version_response.raise_for_status()
            version = version_response.json()["version"]
            config_response = await client.get(f"{MS_HOST}/1/v{version}/config.json")
            config_response.raise_for_status()
            conf = config_response.json()
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise MajsoulApiError(0, f"无法读取雀魂大厅配置：{type(exc).__name__}: {exc}") from exc

        region = conf["ip"][0]
        if "gateways" in region:  # 新版结构
            endpoints = []
            for gateway in region["gateways"]:
                url = gateway.get("url", "").strip()
                if not url:
                    continue
                if url.startswith("https://"):
                    url = "wss://" + url[8:]
                elif url.startswith("http://"):
                    url = "ws://" + url[7:]
                endpoints.append(url.rstrip("/") + "/gateway")
        else:  # 旧版结构
            endpoints = []
            for item in region.get("region_urls", []):
                url = item.get("url", "").strip()
                if not url:
                    continue
                if url.startswith("https://"):
                    url = "wss://" + url[8:]
                elif url.startswith("http://"):
                    url = "ws://" + url[7:]
                endpoints.append(url.rstrip("/") + "/gateway")
        return endpoints, version


class DHSClient:
    """赛事管理客户端，使用当前 DHS HTTP Token API。"""

    def __init__(self):
        self.http = None
        self.token = None
        self.contest = None
        self.seasons = []
        self.season_id = None

    async def _get(self, path: str, **params):
        response = await self.http.get(path, params=params)
        try:
            data = response.json()
        except ValueError:
            data = response.text
        if response.is_error:
            detail = data.get("error", data) if isinstance(data, dict) else data
            raise MajsoulApiError(response.status_code,
                                  f"赛事 API 请求失败：{detail}")
        if isinstance(data, dict) and data.get("error"):
            raise MajsoulApiError(0, str(data["error"]))
        return data

    async def connect_login(self, contest_id: int, username: str, password: str):
        self.http = httpx.AsyncClient(
            base_url=config.DHS_API,
            timeout=httpx.Timeout(30.0, connect=15.0),
            headers={"X-Web-Client-Version": "v0.15.0-1-g8a62552"},
        )
        response = await self.http.post(
            "/api/login",
            json={
                "account": username,
                "password": majsoul_password_hash(password),
                "type": 1 if username.isdigit() and len(username) == 11 else 0,
            },
        )
        payload = response.json()
        if response.is_error:
            detail = payload.get("error", payload) if isinstance(payload, dict) else payload
            if isinstance(detail, dict) and detail.get("code") == "Err1003":
                raise MajsoulApiError(response.status_code, "赛事场账号或密码错误（Err1003）")
            raise MajsoulApiError(response.status_code, f"赛事场登录被拒绝：{detail}")
        token = None
        if isinstance(payload, dict):
            token = payload.get("token") or (payload.get("data") or {}).get("token")
        if not token:
            raise MajsoulApiError(0, f"赛事场登录失败：{payload}")
        self.token = token
        self.http.headers.update({"Authorization": f"Majsoul {token}"})
        contests = await self._get("/api/contest/fetch_contest_list")
        if isinstance(contests, dict):
            contests = contests.get("list", contests.get("data", []))
        contests = contests or []
        def _number(item, key):
            try:
                return int(item.get(key, -1))
            except (TypeError, ValueError):
                return -1

        matched = next((item for item in contests
                        if _number(item, "unique_id") == contest_id), None)
        if not matched:
            # 老页面展示的可能是 contest_id，当前 API 查询需要 unique_id。
            matched = next((item for item in contests
                            if _number(item, "contest_id") == contest_id
                            or _number(item, "id") == contest_id), None)
        unique_id = int(matched.get("unique_id")) if matched and matched.get("unique_id") else contest_id
        self.contest = await self._get("/api/contest/fetch_contest_detail", unique_id=unique_id)
        if isinstance(self.contest, dict) and isinstance(self.contest.get("data"), dict):
            self.contest = self.contest["data"]
        self.contest.setdefault("unique_id", unique_id)
        self.seasons = await self._get("/api/contest/fetch_contest_season_list",
                                      unique_id=unique_id)
        if isinstance(self.seasons, dict):
            self.seasons = self.seasons.get("list", [])
        if self.seasons:
            current = next((item for item in self.seasons if item.get("state") not in (3, 4)),
                           self.seasons[0])
            self.season_id = current.get("season_id")
        return self.contest

    async def close(self):
        if self.http:
            await self.http.aclose()
            self.http = None

    async def fetch_contest_info(self):
        return self.contest

    async def fetch_players(self) -> list[dict]:
        if not self.season_id:
            return []
        data = await self._get("/api/contest/contest_season_player_list",
                               unique_id=self.contest.get("unique_id"),
                               season_id=self.season_id, search="", state=2,
                               offset=0, limit=10000)
        rows = data.get("list", []) if isinstance(data, dict) else data
        return [{"account_id": row.get("account_id"), "nickname": row.get("nickname")}
                for row in rows or [] if row.get("account_id") and row.get("nickname")]

    async def fetch_record_list(self) -> list:
        if not self.season_id:
            return []
        out, offset, token = [], 0, None
        unique_id = self.contest.get("unique_id")
        while True:
            params = {"unique_id": unique_id, "season_id": self.season_id,
                      "offset": offset, "limit": 100}
            if token:
                params["token"] = token
            data = await self._get("/api/contest/fetch_contest_game_records", **params)
            rows = data.get("record_list", []) if isinstance(data, dict) else []
            out.extend(SimpleNamespace(uuid=self._record_uuid(row), raw=row) for row in rows)
            total = data.get("total", len(out)) if isinstance(data, dict) else len(out)
            if not rows or len(out) >= total:
                break
            offset += len(rows)
            token = data.get("token")
        return out

    @staticmethod
    def _record_uuid(row):
        basic = row.get("basic", {}) if isinstance(row, dict) else {}
        return row.get("uuid") or basic.get("uuid") or row.get("game_uuid")

    async def search_by_nickname(self, nicknames: list[str]) -> list[dict]:
        res = await self.channel.call("searchAccountByNickname",
                                      query_nicknames=nicknames)
        return [{"account_id": i.account_id, "nickname": i.nickname}
                for i in res.search_result]

    async def search_by_account_id(self, account_id: int) -> list[dict]:
        res = await self.channel.call("searchAccountByEid", eids=[account_id])
        return [{"account_id": i.account_id, "nickname": i.nickname}
                for i in res.search_result]


class LobbyClient:
    """牌谱详情客户端。

    赛事管理账号不一定具备普通大厅登录权限，且大厅登录可能返回 151。
    当前牌谱服务可按 UUID 直接下载完整牌谱，因此这里不再依赖大厅登录。
    """

    def __init__(self):
        self.channel = None
        self.http = None

    async def connect_login(self, username: str, password: str):
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=15.0))
        return None

    async def close(self):
        if self.channel:
            await self.channel.close()
            self.channel = None
        if self.http:
            await self.http.aclose()
            self.http = None

    @staticmethod
    def _record_head(raw: dict | None, game_uuid: str):
        """将赛事 API 的 JSON 摘要转换成旧解析器使用的 RecordGame。"""
        head = pb.RecordGame()
        raw = raw if isinstance(raw, dict) else {}
        source = raw.get("head") if isinstance(raw.get("head"), dict) else raw
        basic = source.get("basic", {}) if isinstance(source.get("basic"), dict) else {}
        head.uuid = str(source.get("uuid") or basic.get("uuid") or game_uuid)
        for field in ("start_time", "end_time"):
            value = source.get(field, basic.get(field, 0))
            if value:
                setattr(head, field, int(value))
        for row in source.get("accounts", basic.get("accounts", [])) or []:
            account = head.accounts.add()
            account.account_id = int(row.get("account_id", 0) or 0)
            account.seat = int(row.get("seat", 0) or 0)
            account.nickname = str(row.get("nickname", ""))
        result = source.get("result", {})
        for row in (result.get("players", []) if isinstance(result, dict) else []) or []:
            player = head.result.players.add()
            for field in ("seat", "part_point_1", "total_point"):
                if row.get(field) is not None:
                    setattr(player, field, row[field])
        return head

    async def fetch_record(self, game_uuid: str, raw: dict | None = None) -> dict:
        """从当前牌谱服务下载详情并返回统一牌谱结构。"""
        from app.services.majsoul.parse import parse_detail_records, record_head_to_game

        if not self.http:
            self.http = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=15.0))
        response = await self.http.get(f"{RECORD_HOST}/{game_uuid}")
        if response.is_error:
            raise MajsoulApiError(response.status_code, f"牌谱下载失败：HTTP {response.status_code}")
        data = response.content
        wrapper = pb.Wrapper()
        wrapper.ParseFromString(data)
        detail = pb.GameDetailRecords()
        detail.ParseFromString(wrapper.data or data)
        out = record_head_to_game(self._record_head(raw, game_uuid))
        out["log"] = parse_detail_records(detail)
        return out
