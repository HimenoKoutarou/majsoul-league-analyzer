"""OneBot v11 反向 WebSocket 适配器。"""
import asyncio
import json
import uuid

import websockets


class OneBotAdapter:
    def __init__(self, url: str, access_token: str = ""):
        self.url = url
        self.access_token = access_token
        self.ws = None
        self.ready = asyncio.Event()
        self.pending = {}
        self._send_lock = asyncio.Lock()

    async def send_group_message(self, group_id: str, message: str):
        await asyncio.wait_for(self.ready.wait(), timeout=30)
        echo = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending[echo] = future
        async with self._send_lock:
            await self.ws.send(json.dumps({
                "action": "send_group_msg",
                "params": {"group_id": int(group_id), "message": message},
                "echo": echo,
            }, ensure_ascii=False))
        try:
            return await asyncio.wait_for(future, timeout=15)
        finally:
            self.pending.pop(echo, None)

    async def run(self, on_message):
        headers = {"Authorization": f"Bearer {self.access_token}"} if self.access_token else None
        while True:
            try:
                options = {"additional_headers": headers} if headers else {}
                try:
                    async with websockets.connect(self.url, **options) as ws:
                        await self._consume(ws, on_message)
                except TypeError:
                    options = {"extra_headers": headers} if headers else {}
                    async with websockets.connect(self.url, **options) as ws:
                        await self._consume(ws, on_message)
            except Exception as exc:
                self.ready.clear()
                print(f"[onebot] connection error: {type(exc).__name__}: {exc}")
                await asyncio.sleep(3)

    async def _consume(self, ws, on_message):
        self.ws = ws
        self.ready.set()
        try:
            async for raw in ws:
                payload = json.loads(raw)
                echo = payload.get("echo")
                if echo and echo in self.pending:
                    future = self.pending[echo]
                    if not future.done():
                        future.set_result(payload)
                    continue
                if (payload.get("post_type") == "message"
                        and payload.get("message_type") == "group"):
                    await on_message(
                        str(payload.get("group_id")),
                        str(payload.get("raw_message") or payload.get("message") or ""),
                    )
        finally:
            self.ready.clear()
            self.ws = None
