"""机器人事件消费和命令分发。"""
import asyncio
import logging
from datetime import datetime

from . import config
from .adapters.dry_run import DryRunAdapter
from .adapters.jiuguandao import JiuGuanDaoClient
from .adapters.onebot import OneBotAdapter
from .api_client import SiteApi
from .commands import CommandService
from .renderers import render_daily_digest, render_game_created
from .state import StateStore

log = logging.getLogger("majsoul-bot")


class BotApplication:
    def __init__(self):
        if not config.API_TOKEN:
            raise RuntimeError("BOT_API_TOKEN 未配置")
        self.api = SiteApi(config.API_BASE_URL, config.API_TOKEN)
        self.state = StateStore(config.STATE_PATH)
        self.commands = CommandService(self.api)
        self.group_ids = config.GROUP_IDS
        self.jiuguandao = (
            JiuGuanDaoClient(config.JIUGUANDAO_BASE_URL, config.JIUGUANDAO_TOKEN)
            if config.JIUGUANDAO_ENABLED and config.JIUGUANDAO_TOKEN
            else None
        )
        self.adapter = (DryRunAdapter() if config.DRY_RUN else
                        OneBotAdapter(config.ONEBOT_WS_URL, config.ONEBOT_ACCESS_TOKEN))

    async def run(self):
        try:
            await asyncio.gather(
                self._consume_events(),
                self._digest_loop(),
                self.adapter.run(self._on_message),
            )
        finally:
            await self.api.close()
            if self.jiuguandao:
                await self.jiuguandao.close()

    async def _consume_events(self):
        while True:
            try:
                result = await self.api.events(self.state.event_cursor)
                events = result.get("items") or []
                for event in events:
                    if event.get("event_type") == "game.created":
                        message = render_game_created(event.get("payload") or {})
                        for group_id in self.state.target_groups(self.group_ids):
                            await self.adapter.send_group_message(group_id, message)
                    self.state.event_cursor = max(self.state.event_cursor, int(event["id"]))
                    self.state.save()
                if not events:
                    await asyncio.sleep(config.POLL_INTERVAL)
            except Exception as exc:
                log.exception("事件消费失败：%s", exc)
                await asyncio.sleep(min(30, config.POLL_INTERVAL * 3))

    async def _digest_loop(self):
        while True:
            try:
                now = datetime.now()
                day = now.strftime("%Y-%m-%d")
                if (config.DIGEST_ENABLED and now.strftime("%H:%M") >= config.DIGEST_TIME
                        and not self.state.digest_sent(day)):
                    groups = self.state.target_groups(self.group_ids)
                    if groups:
                        standings, games = await asyncio.gather(
                            self.api.standings("team"), self.api.games(5)
                        )
                        message = render_daily_digest(day, standings, games)
                        for group_id in groups:
                            await self.adapter.send_group_message(group_id, message)
                        self.state.mark_digest_sent(day)
            except Exception as exc:
                log.exception("每日摘要发送失败：%s", exc)
            await asyncio.sleep(config.DIGEST_POLL_INTERVAL)

    def _status_message(self) -> str:
        groups = self.state.target_groups(self.group_ids)
        digest_status = "开启" if config.DIGEST_ENABLED else "关闭"
        return "\n".join([
            "【机器人状态】",
            f"事件游标：{self.state.event_cursor}",
            f"推送群：{len(groups)} 个",
            f"每日摘要：{digest_status}",
            f"摘要时间：{config.DIGEST_TIME}",
            f"最近摘要：{self.state.last_digest_date or '尚未发送'}",
        ])

    async def _on_message(self, group_id: str, text: str):
        if self.group_ids and group_id not in self.group_ids:
            return
        stripped = text.strip()
        if not stripped:
            return
        try:
            if not stripped.startswith(("/", "!")):
                if not self.jiuguandao:
                    return
                response = await self.jiuguandao.chat(group_id, stripped)
                if config.JIUGUANDAO_REPLY_PREFIX:
                    response = f"{config.JIUGUANDAO_REPLY_PREFIX}{response}"
                await self.adapter.send_group_message(group_id, response)
                return
            command = stripped.split(maxsplit=1)[0].lstrip("/!").lower()
            if command in ("订阅", "subscribe"):
                changed = self.state.subscribe(group_id, self.group_ids)
                response = "已订阅本群的新牌谱推送。" if changed else "本群已经订阅新牌谱推送。"
            elif command in ("取消订阅", "退订", "unsubscribe"):
                changed = self.state.unsubscribe(group_id, self.group_ids)
                response = "已取消本群的新牌谱推送。" if changed else "本群当前未订阅新牌谱推送。"
            elif command in ("订阅状态", "substatus"):
                enabled = group_id in self.state.target_groups(self.group_ids)
                response = "本群当前已订阅新牌谱推送。" if enabled else "本群当前未订阅新牌谱推送。"
            elif command in ("机器人状态", "botstatus"):
                response = self._status_message()
            else:
                response = await self.commands.handle(text)
            if response:
                await self.adapter.send_group_message(group_id, response)
        except Exception as exc:
            log.exception("命令处理失败：%s", exc)
            await self.adapter.send_group_message(group_id, "查询失败，请稍后重试。")
