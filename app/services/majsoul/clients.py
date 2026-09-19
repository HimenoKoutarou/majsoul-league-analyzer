"""雀魂 DHS / 大厅客户端（真实网络实现；沙箱内不可达，联调留给部署环境）。"""
import hashlib
import hmac
import uuid as uuidlib

import httpx

import liqi_combined_pb2 as pb

from app.services.majsoul.codec import LiqiChannel, MajsoulApiError

DHS_WS = "wss://common-v2.maj-soul.com/contest_ws_gateway"
MS_HOST = "https://game.maj-soul.com"


def majsoul_password_hash(password: str) -> str:
    return hmac.new(b"lailai", password.encode(), hashlib.sha256).hexdigest()


async def discover_lobby_endpoint() -> tuple[str, str]:
    """返回 (wss_endpoint, version)。"""
    async with httpx.AsyncClient(timeout=20.0) as client:
        version = (await client.get(f"{MS_HOST}/1/version.json")).json()["version"]
        conf = (await client.get(f"{MS_HOST}/1/v{version}/config.json")).json()
        region = conf["ip"][0]
        if "gateways" in region:  # 新版结构
            rec_url = region["gateways"][0]["url"] + "/api/v0/recommend_list"
        else:  # 旧版结构
            rec_url = region["region_urls"][1]["url"]
        servers = (await client.get(
            rec_url + "?service=ws-gateway&protocol=ws&ssl=true")).json()["servers"]
        return f"wss://{servers[0]}/gateway", version


class DHSClient:
    """赛事管理（DHS）网关客户端。"""

    def __init__(self):
        self.channel = LiqiChannel(DHS_WS)
        self.contest = None

    async def connect_login(self, contest_id: int, username: str, password: str):
        await self.channel.connect()
        await self.channel.call("loginContestManager", account=username,
                                password=majsoul_password_hash(password), type=0)
        res = await self.channel.call("manageContest", unique_id=contest_id)
        self.contest = res.contest
        return self.contest

    async def close(self):
        await self.channel.close()

    async def fetch_contest_info(self):
        return self.contest

    async def fetch_players(self) -> list[dict]:
        res = await self.channel.call("fetchContestPlayer")
        return [{"account_id": p.account_id, "nickname": p.nickname} for p in res.players]

    async def fetch_record_list(self) -> list:
        out, last, guard = [], 0, 0
        while guard < 200:
            guard += 1
            res = await self.channel.call("fetchContestGameRecords", last_index=last)
            out.extend(item.record for item in res.record_list)
            nxt = res.next_index
            if not nxt or nxt == last:
                break
            last = nxt
        return out

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
    """大厅网关客户端（fetchGameRecord 拿完整牌谱）。"""

    def __init__(self):
        self.channel = None

    async def connect_login(self, username: str, password: str):
        endpoint, version = await discover_lobby_endpoint()
        self.channel = LiqiChannel(endpoint)
        await self.channel.connect()
        req = pb.ReqLogin(account=username, password=majsoul_password_hash(password),
                          random_key=str(uuidlib.uuid1()), gen_access_token=True,
                          client_version_string=f"web-{version.replace('.w', '')}")
        req.device.is_browser = True
        req.currency_platforms.append(2)
        res = await self.channel.call_method("login", req)
        if not res.access_token:
            raise MajsoulApiError(0, "登录失败（账号密码错误？）")
        return res

    async def close(self):
        if self.channel:
            await self.channel.close()

    async def fetch_record(self, game_uuid: str) -> dict:
        """返回 record_head_to_game 输出（含 log）。"""
        from app.services.majsoul.parse import parse_detail_records, record_head_to_game

        res = await self.channel.call("fetchGameRecord", game_uuid=game_uuid,
                                      client_version_string="")
        data = res.data
        if not data and res.data_url:
            async with httpx.AsyncClient(timeout=30.0) as client:
                data = (await client.get(res.data_url)).content
        if not data:
            raise MajsoulApiError(0, f"牌谱 {game_uuid} 无数据")
        wrapper = pb.Wrapper()
        wrapper.ParseFromString(data)
        detail = pb.GameDetailRecords()
        detail.ParseFromString(wrapper.data)
        out = record_head_to_game(res.head)
        out["log"] = parse_detail_records(detail)
        return out
