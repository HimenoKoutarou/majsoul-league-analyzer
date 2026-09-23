"""本地测试适配器。"""
import asyncio


class DryRunAdapter:
    async def send_group_message(self, group_id: str, message: str):
        print(f"[dry-run][group={group_id}]\n{message}")

    async def run(self, on_message):
        await asyncio.Event().wait()
