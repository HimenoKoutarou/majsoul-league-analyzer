"""雀魂 DHS / 大厅客户端。"""
import hashlib
import hmac
import uuid as uuidlib
from types import SimpleNamespace

import httpx
from google.protobuf.json_format import MessageToDict

import app.config as config
from . import liqi_combined_pb2 as pb

from app.services.majsoul.codec import LiqiChannel, MajsoulApiError
from .rules import contest_rule_to_score_rule

MS_HOST = "https://game.maj-soul.com"
RECORD_HOST = "https://record-v2.maj-soul.com:5333/majsoul/game_record"


class RecordNotReadyError(Exception):
    """牌谱已被发现，但雀魂尚未提供完整数据。"""


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

        regions = conf.get("ip") or []
        if not regions:
            raise MajsoulApiError(0, "雀魂大厅配置中没有 player 区域")
        region = next((item for item in regions if item.get("name") == "player"), regions[0])
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
                endpoint = url.rstrip("/")
                endpoints.append(endpoint if endpoint.endswith("/gateway") else endpoint + "/gateway")
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
                endpoint = url.rstrip("/")
                endpoints.append(endpoint if endpoint.endswith("/gateway") else endpoint + "/gateway")
        return endpoints, version


class DHSClient:
    """赛事管理客户端，使用当前 DHS HTTP Token API。"""

    def __init__(self):
        # HTTP API 用于赛事同步；账号验证仍使用赛事管理 WebSocket RPC。
        self.channel = LiqiChannel(config.DHS_WS)
        self.http = None
        self.token = None
        self.contest = None
        self.contest_rule = None
        self.contest_rule_raw = None
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
        self.contest_rule_raw = self.contest
        self.contest_rule = await self.fetch_contest_rule()
        self.seasons = await self._get("/api/contest/fetch_contest_season_list",
                                      unique_id=unique_id)
        if isinstance(self.seasons, dict):
            self.seasons = self.seasons.get("list", [])
        if not self.seasons and isinstance(self.contest, dict):
            self.seasons = self.contest.get("season_list", []) or []
        if self.seasons:
            current = next((item for item in self.seasons if item.get("state") not in (3, 4)),
                           self.seasons[0])
            self.season_id = current.get("season_id")
        return self.contest

    async def close(self):
        if self.channel:
            await self.channel.close()
        if self.http:
            await self.http.aclose()
            self.http = None

    async def fetch_contest_info(self):
        return self.contest

    async def fetch_contest_rule(self):
        """拉取当前赛事场的完整规则，而不是使用站内默认值。"""
        unique_id = self.contest.get("unique_id") if self.contest else None
        if not unique_id:
            raise MajsoulApiError(0, "赛事场规则请求缺少 unique_id")
        try:
            self.contest_rule = contest_rule_to_score_rule(self.contest)
            return self.contest_rule
        except ValueError:
            pass
        last_error = None
        for path in ("/api/contest/fetch_contest_game_rule",
                     "/api/contest/fetch_contest_game_rule_setting"):
            try:
                payload = await self._get(path, unique_id=unique_id)
                self.contest_rule_raw = payload
                self.contest_rule = contest_rule_to_score_rule(payload)
                return self.contest_rule
            except (MajsoulApiError, ValueError) as exc:
                last_error = exc
        raise MajsoulApiError(0, f"赛事场规则拉取失败：{last_error}") from last_error

    async def fetch_players(self) -> list[dict]:
        if not self.season_id:
            return []
        data = await self._get("/api/contest/contest_season_player_list",
                               unique_id=self.contest.get("unique_id"),
                               season_id=self.season_id, search="", state=2,
                               offset=0, limit=10000)
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            data = data["data"]
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
        if self.http and self.contest:
            seasons = self.seasons or self.contest.get("season_list", [])
            unique_id = self.contest.get("unique_id")
            for nickname in nicknames:
                for season in seasons or []:
                    season_id = season.get("season_id") if isinstance(season, dict) else None
                    if not season_id:
                        continue
                    data = await self._get(
                        "/api/contest/contest_season_player_list",
                        unique_id=unique_id,
                        season_id=season_id,
                        search=nickname,
                        state=2,
                        offset=0,
                        limit=100,
                    )
                    if isinstance(data, dict) and isinstance(data.get("data"), dict):
                        data = data["data"]
                    rows = data.get("list", []) if isinstance(data, dict) else data
                    matches = [
                        {"account_id": row.get("account_id"), "nickname": row.get("nickname")}
                        for row in rows or []
                        if row.get("account_id") and row.get("nickname")
                    ]
                    if matches:
                        return matches
            return []

        res = await self.channel.call("searchAccountByNickname",
                                      query_nicknames=nicknames)
        return [{"account_id": i.account_id, "nickname": i.nickname}
                for i in res.search_result]

    async def search_by_account_id(self, account_id: int) -> list[dict]:
        """通过当前赛事的 HTTP 名单接口查找账号，避免依赖旧 WebSocket 网关。"""
        if self.http and self.contest:
            seasons = self.seasons or self.contest.get("season_list", [])
            unique_id = self.contest.get("unique_id")
            for season in seasons or []:
                season_id = season.get("season_id") if isinstance(season, dict) else None
                if not season_id:
                    continue
                data = await self._get(
                    "/api/contest/contest_season_player_list",
                    unique_id=unique_id,
                    season_id=season_id,
                    search=str(account_id),
                    state=2,
                    offset=0,
                    limit=100,
                )
                if isinstance(data, dict) and isinstance(data.get("data"), dict):
                    data = data["data"]
                rows = data.get("list", []) if isinstance(data, dict) else data
                matches = [
                    {"account_id": row.get("account_id"), "nickname": row.get("nickname")}
                    for row in rows or []
                    if int(row.get("account_id", 0) or 0) == account_id
                    and row.get("nickname")
                ]
                if matches:
                    return matches
            return []

        if not self.channel:
            raise MajsoulApiError(0, "赛事场验证客户端未连接")
        res = await self.channel.call("searchAccountByEid", eids=[account_id])
        return [{"account_id": i.account_id, "nickname": i.nickname}
                for i in res.search_result]


class LobbyClient:
    """牌谱详情客户端。

    普通大厅账号客户端。赛事场组织者账号不应传到这里；赛事历史同步仍可
    在没有大厅账号时使用固定牌谱服务作为兼容回退。
    """

    def __init__(self, *, record_host: str = RECORD_HOST):
        self.channel = None
        self.http = None
        self.version = ""
        self.client_version_string = ""
        self.record_host = record_host.rstrip("/")
        self.account_id = None

    async def connect_login(self, username: str | None = None,
                            password: str | None = None,
                            access_token: str | None = None,
                            endpoints: list[str] | None = None):
        """连接并登录普通大厅；不传账号时仅初始化 HTTP 回退客户端。"""
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=15.0))
        if not username and not access_token:
            return None
        endpoints, self.version = (endpoints, "") if endpoints else await discover_lobby_endpoints()
        if not endpoints:
            raise MajsoulApiError(0, "雀魂大厅配置中没有可用网关")
        last_error = None
        for endpoint in endpoints:
            channel = LiqiChannel(endpoint)
            try:
                await channel.connect()
                # version.json 当前返回形如 0.11.252.w，但大厅 RPC
                # 的 client_version_string 不包含末尾的 .w。
                version = self.version.removesuffix(".w")
                self.client_version_string = f"web-{version}"
                req = pb.ReqLogin(
                    reconnect=False,
                    device=pb.ClientDeviceInfo(
                        platform="pc", hardware="pc", os="windows", os_version="win10",
                        is_browser=True, software="Chrome", sale_platform="web",
                        screen_width=1920, screen_height=1080,
                    ),
                    random_key=str(uuidlib.uuid4()),
                    currency_platforms=[2],
                    type=0,
                    tag="cn",
                    client_version_string=self.client_version_string,
                    gen_access_token=True,
                )
                if access_token:
                    check = await channel.call(
                        "oauth2Check", type=config.MS_OAUTH_TYPE,
                        access_token=access_token,
                    )
                    if not check.has_account:
                        raise MajsoulApiError(0, "大厅 access_token 无效或尚未绑定账号")
                    oauth_req = pb.ReqOauth2Login(
                        type=config.MS_OAUTH_TYPE,
                        access_token=access_token,
                        reconnect=False,
                        device=req.device,
                        random_key=req.random_key,
                        currency_platforms=list(req.currency_platforms),
                        tag=req.tag,
                        client_version_string=req.client_version_string,
                    )
                    result = await channel.call_method("oauth2Login", oauth_req)
                else:
                    req.account = username
                    req.password = majsoul_password_hash(password or "")
                    result = await channel.call_method("login", req)
                if not result.account_id:
                    raise MajsoulApiError(0, "大厅登录未返回 account_id")
                self.channel = channel
                self.account_id = result.account_id
                await channel.start_heartbeat()
                return result
            except Exception as exc:
                last_error = exc
                await channel.close()
        raise MajsoulApiError(0, f"雀魂大厅登录失败：{last_error}") from last_error

    async def close(self):
        if self.channel:
            await self.channel.close()
            self.channel = None
        if self.http:
            await self.http.aclose()
            self.http = None

    @staticmethod
    def _record_head(raw: dict | object | None, game_uuid: str):
        """将赛事 API 的 JSON 摘要转换成旧解析器使用的 RecordGame。"""
        if isinstance(raw, pb.RecordGame):
            return raw
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

    async def fetch_game_live_list(self, filter_id: int):
        if not self.channel:
            raise MajsoulApiError(0, "大厅 WebSocket 未登录")
        return await self.channel.call("fetchGameLiveList", filter_id=int(filter_id))

    async def search_by_account_id(self, account_id: int) -> list[dict]:
        """通过大厅协议查询全局雀魂账号。"""
        if not self.channel:
            raise MajsoulApiError(0, "大厅 WebSocket 未登录")
        result = await self.channel.call("searchAccountById", account_id=int(account_id))
        player = result.player
        if not player.account_id:
            return []
        return [{"account_id": player.account_id, "nickname": player.nickname}]

    @staticmethod
    def _parse_detail_payload(data: bytes):
        detail = pb.GameDetailRecords()
        try:
            detail.ParseFromString(data)
            if detail.version or detail.records or detail.actions:
                return detail
        except Exception:
            pass
        wrapper = pb.Wrapper()
        wrapper.ParseFromString(data)
        detail.ParseFromString(wrapper.data or data)
        return detail

    async def fetch_record(self, game_uuid: str, raw: dict | None = None) -> dict:
        """从当前牌谱服务下载详情并返回统一牌谱结构。"""
        from app.services.majsoul.parse import parse_detail_records, record_head_to_game

        head = None
        data = None
        if self.channel:
            response = await self.channel.call(
                "fetchGameRecord",
                game_uuid=game_uuid,
                client_version_string=self.client_version_string,
            )
            head = response.head
            data = bytes(response.data or b"")
            if not data and response.data_url:
                if not self.http:
                    self.http = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=15.0))
                download = await self.http.get(response.data_url)
                download.raise_for_status()
                data = download.content
            if not head or not data:
                raise RecordNotReadyError(f"牌谱 {game_uuid} 尚未完成")
        else:
            # DHS 历史同步的兼容回退，不要求赛事场账号具备大厅登录权限。
            if not self.http:
                self.http = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=15.0))
            response = await self.http.get(f"{self.record_host}/{game_uuid}")
            if response.is_error:
                raise MajsoulApiError(response.status_code, f"牌谱下载失败：HTTP {response.status_code}")
            data = response.content

        if head is not None and (not head.end_time or not data):
            raise RecordNotReadyError(f"牌谱 {game_uuid} 尚未结束")
        detail = self._parse_detail_payload(data)
        out = record_head_to_game(self._record_head(head or raw, game_uuid))
        out["log"] = parse_detail_records(detail)
        return out

    @staticmethod
    def live_head_to_dict(head) -> dict:
        return MessageToDict(head, preserving_proto_field_name=True)

    async def fetch_record_http(self, game_uuid: str, raw: dict | None = None) -> dict:
        """显式使用旧牌谱服务，供 DHS 历史补齐和兼容测试使用。"""
        if not self.http:
            self.http = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=15.0))
        response = await self.http.get(f"{self.record_host}/{game_uuid}")
        if response.is_error:
            raise MajsoulApiError(response.status_code, f"牌谱下载失败：HTTP {response.status_code}")
        data = response.content
        detail = self._parse_detail_payload(data)
        from app.services.majsoul.parse import parse_detail_records, record_head_to_game
        out = record_head_to_game(self._record_head(raw, game_uuid))
        out["log"] = parse_detail_records(detail)
        return out
