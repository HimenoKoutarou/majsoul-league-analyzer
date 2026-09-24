"""雀魂赛事场规则转换。"""

from collections.abc import Iterator


def _walk_dicts(value: object) -> Iterator[dict]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _number(mapping: dict, *keys: str) -> int | float | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        return value
    return None


def _rank_points(payload: object) -> list[int | float] | None:
    for mapping in _walk_dicts(payload):
        points = mapping.get("rank_points")
        if isinstance(points, list) and len(points) == 4:
            if all(isinstance(item, (int, float)) and not isinstance(item, bool)
                   for item in points):
                return points

        first = _number(mapping, "shunweima_1", "shunweima1", "uma_1", "uma1")
        second = _number(mapping, "shunweima_2", "shunweima2", "uma_2", "uma2")
        third = _number(mapping, "shunweima_3", "shunweima3", "uma_3", "uma3")
        fourth = _number(mapping, "shunweima_4", "shunweima4", "uma_4", "uma4")
        if second is not None and third is not None and fourth is not None:
            # 雀魂赛事场只返回二到四位的马点；一位不是用总和为 0
            # 推导，而是赛事场规则中的固定 +50。
            return [50 if first is None else first, second, third, fourth]
    return None


def contest_rule_to_score_rule(payload: object) -> dict:
    """将赛事场返回的 game_rule_setting 转成站内统一积分规则。

    赛事场通常不直接传一位顺位分，只有二到四位的顺位马。
    当前赛事场格式的一位马点为 +50，不能按四项总和为 0 反推。
    """
    points = _rank_points(payload)
    if points is None:
        raise ValueError("赛事场返回数据中没有完整的顺位分规则")

    rule = {
        "rank_points": points,
        "allow_negative": True,
        "tiebreak": "raw_points",
        "source": "majsoul_contest",
    }
    for mapping in _walk_dicts(payload):
        for key in ("init_point", "jingsuanyuandian", "fandian",
                    "shunweima_2", "shunweima_3", "shunweima_4"):
            value = _number(mapping, key)
            if value is not None:
                rule[key] = value
        if "can_jifei" in mapping and isinstance(mapping["can_jifei"], bool):
            rule["can_jifei"] = mapping["can_jifei"]
    return rule
