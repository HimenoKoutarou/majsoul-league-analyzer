"""酒馆岛卡片 Token API 适配器。"""
import json
import asyncio
from collections import defaultdict

import httpx


class JiuGuanDaoClient:
    """调用酒馆岛的卡片 Token SSE 对话接口。"""

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.http = httpx.AsyncClient(
            timeout=httpx.Timeout(90.0, connect=10.0),
            headers={"Accept": "text/event-stream"},
        )
        self._locks = defaultdict(asyncio.Lock)

    async def close(self):
        await self.http.aclose()

    async def chat(self, conversation_id: str, message: str) -> str:
        async with self._locks[conversation_id]:
            response = await self.http.post(
                f"{self.base_url}/api/token/chat",
                json={"token": self.token, "message": message},
            )
            response.raise_for_status()
            reply = []
            full_text = None
            buffer = ""
            async for chunk in response.aiter_text():
                buffer += chunk
                while "\n\n" in buffer:
                    event, buffer = buffer.split("\n\n", 1)
                    for line in event.splitlines():
                        if not line.startswith("data:"):
                            continue
                        data = json.loads(line[5:].strip())
                        text = data.get("text")
                        if text:
                            reply.append(str(text))
                        if data.get("full_text") is not None:
                            full_text = str(data["full_text"])
            if buffer.strip().startswith("data:"):
                data = json.loads(buffer.strip()[5:].strip())
                if data.get("text"):
                    reply.append(str(data["text"]))
                if data.get("full_text") is not None:
                    full_text = str(data["full_text"])
            result = (full_text if full_text is not None else "".join(reply)).strip()
            if not result:
                raise RuntimeError("酒馆岛返回了空回复")
            return result
