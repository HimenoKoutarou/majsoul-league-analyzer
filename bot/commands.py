"""机器人交互命令。"""
from .renderers import (render_game_detail, render_latest, render_profile,
                         render_recent_profile, render_riichi_profile,
                         render_related_games, render_standings,
                         render_tablemates)


class CommandService:
    def __init__(self, api):
        self.api = api

    async def handle(self, text: str) -> str:
        parts = text.strip().split(maxsplit=1)
        if not parts:
            return ""
        command = parts[0].lstrip("/!").lower()
        argument = parts[1].strip() if len(parts) > 1 else ""
        if command in ("帮助", "help"):
            return ("【命令】\n"
                    "/最新牌谱 [数量]\n"
                    "/对局 <牌谱UUID>\n"
                    "/选手数据 <昵称>\n"
                    "/立直数据 <昵称>\n"
                    "/最近和铳 <昵称>\n"
                    "/最常同桌 <昵称>\n"
                    "/选手对局 <昵称> [数量]\n"
                    "/队伍数据 <队名>\n"
                    "/队伍对局 <队名> [数量]\n"
                    "/队伍排名\n"
                    "/机器人状态\n"
                    "/订阅 /取消订阅 /订阅状态")
        if command in ("最新牌谱", "最近牌谱", "latest"):
            try:
                size = min(max(int(argument or 5), 1), 10)
            except ValueError:
                size = 5
            return render_latest(await self.api.games(size))
        if command in ("对局", "牌谱", "game"):
            if not argument:
                return "用法：/对局 <牌谱UUID>"
            return render_game_detail(await self.api.game(argument))
        if command in ("队伍排名", "排名", "standings"):
            return render_standings(await self.api.standings("team"))
        if command in ("选手数据", "选手", "player"):
            if not argument:
                return "用法：/选手数据 <昵称>"
            return await self._player(argument)
        if command in ("立直数据", "riichi"):
            return await self._profile_section(argument, render_riichi_profile,
                                               "/立直数据")
        if command in ("最近和铳", "最近和铳数据", "recent"):
            return await self._profile_section(argument, render_recent_profile,
                                               "/最近和铳")
        if command in ("最常同桌", "同桌", "tablemates"):
            return await self._profile_section(argument, render_tablemates,
                                               "/最常同桌")
        if command in ("选手对局", "playergames"):
            return await self._related_player_games(argument)
        if command in ("队伍数据", "team"):
            return await self._team(argument, related=False)
        if command in ("队伍对局", "teamgames"):
            return await self._team(argument, related=True)
        return "未知命令，发送 /帮助 查看可用命令。"

    async def _player(self, nickname: str) -> str:
        matches = await self._find_players(nickname)
        if not matches:
            return f"找不到选手：{nickname}"
        if len(matches) > 1:
            return "匹配到多个选手：\n" + "\n".join(
                f"- {p.get('nickname')}（ID {p.get('id')}）" for p in matches[:10]
            )
        player = matches[0]
        return render_profile(player.get("nickname", nickname),
                              await self.api.profile(int(player["id"])))

    async def _find_players(self, nickname: str) -> list[dict]:
        needle = nickname.casefold()
        return [
            player
            for team in await self.api.teams()
            for player in team.get("players") or []
            if needle in str(player.get("nickname", "")).casefold()
        ]

    async def _profile_section(self, nickname: str, renderer, usage: str) -> str:
        if not nickname:
            return f"用法：{usage} <昵称>"
        matches = await self._find_players(nickname)
        if not matches:
            return f"找不到选手：{nickname}"
        if len(matches) > 1:
            return "匹配到多个选手：\n" + "\n".join(
                f"- {p.get('nickname')}（ID {p.get('id')}）" for p in matches[:10]
            )
        player = matches[0]
        profile = await self.api.profile(int(player["id"]))
        return renderer(player.get("nickname", nickname), profile)

    @staticmethod
    def _split_limit(argument: str) -> tuple[str, int] | tuple[None, None]:
        if not argument:
            return None, None
        parts = argument.rsplit(maxsplit=1)
        try:
            limit = min(max(int(parts[-1]), 1), 10)
            if len(parts) > 1:
                return parts[0].strip(), limit
        except ValueError:
            pass
        return argument, 5

    async def _related_player_games(self, argument: str) -> str:
        nickname, limit = self._split_limit(argument)
        if not nickname:
            return "用法：/选手对局 <昵称> [数量]"
        matches = await self._find_players(nickname)
        if not matches:
            return f"找不到选手：{nickname}"
        if len(matches) > 1:
            return "匹配到多个选手：\n" + "\n".join(
                f"- {p.get('nickname')}（ID {p.get('id')}）" for p in matches[:10]
            )
        player = matches[0]
        return render_related_games(
            f"【选手对局】{player.get('nickname', nickname)}",
            await self.api.related_games("player", int(player["id"])),
            limit,
        )

    async def _team(self, argument: str, related: bool) -> str:
        command = "/队伍对局" if related else "/队伍数据"
        if not argument:
            return f"用法：{command} <队名>" + (" [数量]" if related else "")
        name, limit = self._split_limit(argument)
        matches = [
            team for team in await self.api.teams()
            if name.casefold() in str(team.get("name", "")).casefold()
            or name.casefold() in str(team.get("short_name", "")).casefold()
        ]
        if not matches:
            return f"找不到队伍：{name}"
        if len(matches) > 1:
            return "匹配到多个队伍：\n" + "\n".join(
                f"- {team.get('name')}（ID {team.get('id')}）" for team in matches[:10]
            )
        team = matches[0]
        if related:
            return render_related_games(
                f"【队伍对局】{team.get('name', name)}",
                await self.api.related_games("team", int(team["id"])),
                limit,
            )
        return render_profile(
            team.get("name", name), await self.api.profile(int(team["id"])),
            title="队伍数据")
