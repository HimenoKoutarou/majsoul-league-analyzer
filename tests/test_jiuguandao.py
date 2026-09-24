import asyncio
import json

import httpx

from bot.adapters.jiuguandao import JiuGuanDaoClient


def test_jiuguandao_collects_sse_text():
    async def handler(request):
        assert request.url.path == "/api/token/chat"
        payload = json.loads(await request.aread())
        assert payload == {"token": "secret", "message": "你好"}
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=(
                f"data: {json.dumps({'text': '你好'})}\n\n"
                f"data: {json.dumps({'text': '，世界'})}\n\n"
            ).encode(),
        )

    async def run():
        client = JiuGuanDaoClient("https://example.test", "secret")
        await client.close()
        client.http = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            timeout=httpx.Timeout(90.0, connect=10.0),
        )
        try:
            assert await client.chat("group-1", "你好") == "你好，世界"
        finally:
            await client.close()

    asyncio.run(run())
