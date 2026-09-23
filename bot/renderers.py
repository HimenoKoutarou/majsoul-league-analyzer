"""群消息格式化。"""
from datetime import datetime


def _number(value, digits: int = 1) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "0"


def _signed_number(value, digits: int = 1) -> str:
    try:
        return f"{float(value):+.{digits}f}"
    except (TypeError, ValueError):
        return "+0" if digits == 0 else f"+{'0.' + '0' * digits}"


def render_game_created(game: dict) -> str:
    start_time = game.get("start_time") or "未知时间"
    try:
        start_time = datetime.fromisoformat(start_time).strftime("%m-%d %H:%M")
    except ValueError:
        pass
    lines = ["【新牌谱】", f"时间：{start_time}", ""]
    for player in sorted(game.get("players") or [], key=lambda item: item.get("seat", 0)):
        team = f"[{player['team_name']}] " if player.get("team_name") else ""
        lines.append(
            f"{player.get('rank', 0)}位 {team}{player.get('nickname', '未知')} "
            f"{player.get('final_score', 0):+d}点 "
            f"({_signed_number(player.get('pt'), 1)}pt)"
        )
    lines.extend(["", f"牌谱：{game.get('uuid', '')}"])
    return "\n".join(lines)


def render_latest(data: dict) -> str:
    items = data.get("items") or []
    if not items:
        return "暂无牌谱。"
    lines = ["【最新牌谱】"]
    for item in items:
        players = sorted(item.get("players") or [], key=lambda row: row.get("rank", 99))
        summary = "、".join(
            f"{p.get('rank', 0)}位{p.get('nickname', '未知')}"
            for p in players[:4]
        )
        lines.append(f"{item.get('start_time') or '未知时间'}\n{summary}\n{item.get('uuid', '')}")
    return "\n".join(lines)


def _game_time(value) -> str:
    if not value:
        return "未知时间"
    try:
        return datetime.fromisoformat(str(value)).strftime("%m-%d %H:%M")
    except ValueError:
        return str(value)


def render_game_detail(game: dict) -> str:
    lines = ["【对局详情】", f"时间：{_game_time(game.get('start_time'))}"]
    for player in sorted(game.get("players") or [], key=lambda item: item.get("seat", 0)):
        team = f"[{player['team_name']}] " if player.get("team_name") else ""
        score = player.get("final_score", 0) or 0
        pt = _signed_number(player.get("pt"), 1)
        lines.append(
            f"{player.get('rank', 0)}位 {team}{player.get('nickname', '未知')} "
            f"{score:+d}点 ({pt}pt)"
        )
    kyoku_count = len(game.get("kyokus") or [])
    if kyoku_count:
        lines.append(f"小局：{kyoku_count}局")
    lines.extend(["", f"牌谱：{game.get('uuid', '')}"])
    return "\n".join(lines)


def render_related_games(title: str, data: dict, limit: int = 5) -> str:
    items = (data.get("items") or [])[:limit]
    if not items:
        return f"{title}\n暂无相关对局。"
    lines = [title]
    for item in items:
        players = sorted(item.get("players") or [], key=lambda row: row.get("rank", 99))
        summary = "、".join(
            f"{p.get('rank', 0)}位{p.get('nickname', '未知')}"
            for p in players[:4]
        )
        lines.append(f"{_game_time(item.get('start_time'))} {summary}\n{item.get('uuid', '')}")
    if data.get("total", len(items)) > len(items):
        lines.append(f"共 {data['total']} 场，仅显示最近 {len(items)} 场。")
    return "\n".join(lines)


def render_profile(name: str, profile: dict, title: str = "选手数据") -> str:
    basic = profile.get("basic") or {}
    lineage = profile.get("lineage") or {}
    return "\n".join([
        f"【{title}】{name}",
        f"对局：{basic.get('games', 0)}",
        f"平均顺位：{_number(basic.get('avg_rank'), 3)}",
        f"一位率：{_number((basic.get('rank_counts') or [0])[0] / max(basic.get('games', 0), 1) * 100, 1)}%",
        f"和率：{_number(basic.get('win_rate', 0) * 100, 1)}%",
        f"放铳率：{_number(basic.get('dealin_rate', 0) * 100, 1)}%",
        f"立直率：{_number(basic.get('riichi_rate', 0) * 100, 1)}%",
        f"平均积分：{_number(basic.get('pt'), 2)}",
        f"起手向听：{_number(lineage.get('start_shanten'), 2)}",
    ])


def render_standings(data: dict) -> str:
    rows = data.get("rows") or []
    if not rows:
        return "暂无排名数据。"
    lines = ["【队伍排名】"]
    for index, row in enumerate(rows, 1):
        lines.append(
            f"{index}. {row.get('name', '未分组')} "
            f"{_number(row.get('points'), 1)}分 "
            f"{_number(row.get('raw_points'), 2)}素点"
        )
    return "\n".join(lines)


def render_daily_digest(day: str, standings: dict, games: dict) -> str:
    """渲染每日摘要，内容固定为队伍排名和最近牌谱。"""
    lines = [f"【每日赛事摘要】 {day}", "", render_standings(standings), ""]
    latest = render_latest(games)
    lines.append(latest)
    return "\n".join(lines)


def render_riichi_profile(name: str, profile: dict) -> str:
    riichi = profile.get("riichi") or {}
    return "\n".join([
        f"【立直数据】{name}",
        f"立直次数：{riichi.get('count', 0)}",
        f"立直和牌率：{_number(riichi.get('win_rate', 0) * 100, 1)}%",
        f"立直放铳率：{_number(riichi.get('dealin_rate', 0) * 100, 1)}%",
        f"立直流局率：{_number(riichi.get('ryukyoku_rate', 0) * 100, 1)}%",
        f"先制率：{_number(riichi.get('first_rate', 0) * 100, 1)}%",
        f"追立率：{_number(riichi.get('chase_rate', 0) * 100, 1)}%",
        f"被追率：{_number(riichi.get('chased_rate', 0) * 100, 1)}%",
        f"立直巡目：{_number(riichi.get('avg_turn'), 2)}",
        f"一发率：{_number(riichi.get('ippatsu_rate', 0) * 100, 1)}%",
        f"振听率：{_number(riichi.get('furiten_rate', 0) * 100, 1)}%",
        f"立直多面：{_number(riichi.get('multi_wait_rate', 0) * 100, 1)}%",
        f"立直好型：{_number(riichi.get('good_wait_rate', 0) * 100, 1)}%",
        f"中里率：{_number(riichi.get('ura_hit_rate', 0) * 100, 1)}%",
        f"立直收支：{_signed_number(riichi.get('balance', 0), 0)}",
    ])


def _render_recent_record(label: str, item: dict) -> list[str]:
    date = item.get("match_date") or "日期未知"
    half = f"第{item['hanchan']}半庄" if item.get("hanchan") else "半庄未知"
    opponents = "、".join(item.get("opponents") or []) or "对战人未知"
    score = _signed_number(item.get("score", 0), 0)
    details = [label, f"{date} · {half}", f"对战：{opponents}", f"得点：{score}"]
    if item.get("fu_han"):
        details.append(f"符番：{item['fu_han']}")
    if item.get("yaku"):
        details.append(f"役种：{'、'.join(item['yaku'])}")
    return details


def render_recent_profile(name: str, profile: dict) -> str:
    lines = [f"【最近和铳】{name}"]
    wins = profile.get("recent_large") or []
    losses = profile.get("recent_losses") or []
    if wins:
        lines.extend(_render_recent_record("最近大额和牌", wins[0]))
    else:
        lines.append("最近大额和牌：暂无数据")
    lines.append("")
    if losses:
        lines.extend(_render_recent_record("最近大额放铳", losses[0]))
    else:
        lines.append("最近大额放铳：暂无数据")
    return "\n".join(lines)


def render_tablemates(name: str, profile: dict) -> str:
    mates = profile.get("tablemates") or []
    if not mates:
        return f"【最常同桌】{name}\n暂无数据。"
    lines = [f"【最常同桌】{name}"]
    for index, item in enumerate(mates[:10], 1):
        lines.extend([
            f"{index}. {item.get('nickname', '未知选手')}：{item.get('games', 0)}局",
            f"胜率：{_number(item.get('win_rate', 0) * 100, 1)}%",
            f"我场均积分：{_signed_number(item.get('self_avg_pt', 0), 1)}，"
            f"对方场均积分：{_signed_number(item.get('opponent_avg_pt', 0), 1)}",
        ])
    return "\n".join(lines)
