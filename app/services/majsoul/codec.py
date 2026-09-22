"""liqi WebSocket 帧编解码与 RPC 通道。

帧格式：请求 b"\\x02"+seq(2B LE)+Wrapper；响应 b"\\x03"+seq(2B LE)+Wrapper。
通知帧（其他 type / 其他 seq）在 call 内被跳过。
"""
import websockets

from . import liqi_combined_pb2 as pb


class MajsoulApiError(Exception):
    def __init__(self, code: int, message: str = ""):
        self.code = code
        super().__init__(f"{code}: {message}")


# method 别名 → (full_name, 请求消息, 响应消息)
METHODS = {
    "heartbeat": (".lq.Lobby.heatbeat", "ReqHeatBeat", "ResCommon"),
    "login": (".lq.Lobby.login", "ReqLogin", "ResLogin"),
    "fetchGameRecord": (".lq.Lobby.fetchGameRecord", "ReqGameRecord", "ResGameRecord"),
    "loginContestManager": (".lq.CustomizedContestManagerApi.loginContestManager",
                            "ReqContestManageLogin", "ResContestManageLogin"),
    "manageContest": (".lq.CustomizedContestManagerApi.manageContest",
                      "ReqManageContest", "ResManageContest"),
    "fetchContestPlayer": (".lq.CustomizedContestManagerApi.fetchContestPlayer",
                           "ReqCommon", "ResFetchCustomizedContestPlayer"),
    "fetchContestGameRecords": (".lq.CustomizedContestManagerApi.fetchContestGameRecords",
                                "ReqFetchCustomizedContestGameRecordList",
                                "ResFetchCustomizedContestGameRecordList"),
    "searchAccountByNickname": (".lq.CustomizedContestManagerApi.searchAccountByNickname",
                                "ReqSearchAccountByNickname", "ResSearchAccountByNickname"),
    "searchAccountByEid": (".lq.CustomizedContestManagerApi.searchAccountByEid",
                           "ReqSearchAccountByEid", "ResSearchAccountByEid"),
}


class LiqiChannel:
    def __init__(self, url: str):
        self.url = url
        self.ws = None
        self.seq = 0

    async def connect(self):
        self.ws = await websockets.connect(self.url, open_timeout=20, max_size=2 ** 24)

    async def close(self):
        if self.ws:
            await self.ws.close()
            self.ws = None

    async def call(self, method: str, **fields):
        _svc, req_name, _res = METHODS[method]
        req = getattr(pb, req_name)(**fields)
        return await self.call_method(method, req)

    async def call_method(self, method: str, req):
        """method 为别名；req 为已构造的请求消息（用于嵌套消息字段）。"""
        svc_full, _req_name, res_name = METHODS[method]
        wrapper = pb.Wrapper(name=svc_full, data=req.SerializeToString())
        frame = b"\x02" + self.seq.to_bytes(2, "little") + wrapper.SerializeToString()
        await self.ws.send(frame)
        while True:
            raw = await self.ws.recv()
            if raw[0] == 0x03 and int.from_bytes(raw[1:3], "little") == self.seq:
                break
        self.seq += 1
        w = pb.Wrapper()
        w.ParseFromString(raw[3:])
        # 失败时服务端直接在 data 里回一个 Error 消息
        err = pb.Error()
        err.ParseFromString(w.data)
        if err.code:
            raise MajsoulApiError(err.code)
        res = getattr(pb, res_name)()
        res.ParseFromString(w.data)
        if res.error.code:
            raise MajsoulApiError(res.error.code)
        return res
