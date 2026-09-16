"""天凤格式 kyoku 数组 → 结构化摘要（统计口径与前端展示共用）。

打点口径：score = delta[winner] - 1000*立直棒 - 300*honba（荣和/自摸通用，
双响仅头跳和者得立直棒；honba 荣和300/自摸300，与雀魂实际结算一致，已用真实样本验证）。
"""
from app.services.paipu.tiles import discard_parts, is_callout

_WINDS_KYOKU = "一二三四"
_WINDS_BA = "东南西北"


def seat_title(kyoku_index: int, honba: int) -> str:
    title = f"{_WINDS_BA[kyoku_index // 4]}{_WINDS_KYOKU[kyoku_index % 4]}局"
    if honba:
        title += f" {honba}本"
    return title


def _parse_agari(tail: list, total_sticks: int, honba: int) -> list[dict]:
    items = tail[1:]
    out = []
    sticks_taken = False
    for delta, detail in zip(items[0::2], items[1::2]):
        winner, loser, pao = detail[0], detail[1], detail[2]
        tsumo = winner == loser
        sticks_gain = 0 if sticks_taken else total_sticks * 1000
        sticks_taken = True
        score = delta[winner] - sticks_gain - 300 * honba
        out.append({
            "winner": winner, "loser": loser, "pao": pao, "tsumo": tsumo,
            "score": score, "delta": list(delta),
            "fu_han": detail[3] if len(detail) > 3 else "",
            "yaku": list(detail[4:]) if len(detail) > 4 else [],
        })
    return out


def analyze_kyoku(k: list) -> dict:
    """解析一局。输入为天凤格式 kyoku 数组。"""
    kyoku_index, honba, sticks_start = k[0]
    result = {
        "round": [kyoku_index, honba, sticks_start],
        "title": seat_title(kyoku_index, honba),
        "start_scores": list(k[1]),
        "doras": list(k[2]),
        "uras": list(k[3]),
        "seats": [],
        "end": "", "agari": [], "abortive": None, "deltas": [0, 0, 0, 0],
    }
    riichi_total = 0
    for i in range(4):
        draws = k[5 + 3 * i]
        discards = k[6 + 3 * i]
        riichi_turns = [j for j, d in enumerate(discards) if isinstance(d, str) and d.startswith("r")]
        riichi_total += len(riichi_turns)
        result["seats"].append({
            "haipai": list(k[4 + 3 * i]),
            "riichi": bool(riichi_turns),
            "riichi_turn": riichi_turns[0] if riichi_turns else None,
            "callouts": sum(1 for d in draws if is_callout(d)),
        })

    tail = k[16]
    name = tail[0]
    if name == "和了":
        result["end"] = "agari"
        result["agari"] = _parse_agari(tail, sticks_start + riichi_total, honba)
        result["deltas"] = [sum(a["delta"][i] for a in result["agari"]) for i in range(4)]
    elif name == "流局":
        result["end"] = "ryukyoku"
        result["deltas"] = list(tail[1])
    elif name == "流し満貫":
        result["end"] = "nagashi"
        result["deltas"] = list(tail[1])
    else:
        result["end"] = "abortive"
        result["abortive"] = name
    return result