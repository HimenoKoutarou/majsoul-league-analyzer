"""天凤格式牌编码工具。

编码：11-19万 21-29饼 31-39索 41-47字(东南西北白发中) 51/52/53红5；60=摸切。
"""
TSUMOGIRI = 60


def tile_display(code: int) -> str:
    """数字编码 → 显示串（"1m"/"9m"/"5p"/"1z"/"0m"红5），与雀魂字符串一致。"""
    if code in (51, 52, 53):
        return f"0{'mps'[code - 51]}"
    suit = "mpsz"[(code // 10) - 1]
    return str(code % 10) + suit


def tile_deaka(code: int) -> int:
    """红5 → 普通5（51→15, 52→25, 53→35）。"""
    if code in (51, 52, 53):
        return 15 + (code - 51) * 10
    return code


def discard_parts(sym) -> tuple[int | None, bool, bool]:
    """切牌符号 → (牌编码|None, 是否摸切, 是否立直宣言)。0=大明杠占位。"""
    if isinstance(sym, str):
        riichi = sym.startswith("r")
        body = sym[1:] if riichi else sym
        if body == "60":
            return None, True, riichi
        return int(body), False, riichi
    if sym == TSUMOGIRI:
        return None, True, False
    if sym == 0:
        return None, False, False
    return int(sym), False, False


def is_callout(sym) -> bool:
    """摸牌序列符号是否为鸣牌（吃/碰/大明杠/加杠/暗杠；拔北 f44 不算）。"""
    return isinstance(sym, str) and any(ch in sym for ch in "cpmka")