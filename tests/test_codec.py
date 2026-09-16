import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app" / "services" / "majsoul"))

import liqi_combined_pb2 as pb  # noqa: E402

from app.services.majsoul.codec import METHODS, MajsoulApiError  # noqa: E402


def test_methods_registry():
    for name, (svc, req, res) in METHODS.items():
        assert svc.startswith(".lq.")
        assert hasattr(pb, req), f"缺少消息 {req}"
        assert hasattr(pb, res), f"缺少消息 {res}"


@pytest.mark.asyncio
async def test_channel_call_roundtrip():
    from app.services.majsoul.codec import LiqiChannel

    class FakeWS:
        def __init__(self):
            self.sent = []
            self.n = 0

        async def send(self, data):
            self.sent.append(data)
            # 构造对应响应帧
            w = pb.Wrapper()
            w.ParseFromString(data[3:])
            res = pb.ReqHeatBeat()  # 任意空消息作为响应体
            rw = pb.Wrapper(name=w.name, data=res.SerializeToString())
            self.response = b"\x03" + data[1:3] + rw.SerializeToString()

        async def recv(self):
            self.n += 1
            if self.n == 1:
                return b"\x01\x00\x00" + b"notification"  # 先收到通知帧，应被跳过
            return self.response

        async def close(self):
            pass

    ch = LiqiChannel("wss://example")
    ch.ws = FakeWS()
    res = await ch.call("heartbeat")
    assert res is not None
    frame = ch.ws.sent[0]
    assert frame[0] == 0x02
    assert int.from_bytes(frame[1:3], "little") == 0
    w = pb.Wrapper()
    w.ParseFromString(frame[3:])
    assert w.name == ".lq.Lobby.heatbeat"
    assert ch.seq == 1


@pytest.mark.asyncio
async def test_channel_error_code():
    from app.services.majsoul.codec import LiqiChannel

    class FakeWS:
        async def send(self, data):
            w = pb.Wrapper()
            w.ParseFromString(data[3:])
            err = pb.Error()
            err.code = 2504
            rw = pb.Wrapper(name=w.name, data=err.SerializeToString())
            self.response = b"\x03" + data[1:3] + rw.SerializeToString()

        async def recv(self):
            return self.response

        async def close(self):
            pass

    ch = LiqiChannel("wss://example")
    ch.ws = FakeWS()
    with pytest.raises(MajsoulApiError) as e:
        await ch.call("heartbeat")
    assert e.value.code == 2504