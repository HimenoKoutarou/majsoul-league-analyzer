from app.services.paipu.tiles import (
    discard_parts, is_callout, tile_deaka, tile_display,
)
from app.services.paipu.yaku_names import YAKU_NAMES


def test_tile_display():
    assert tile_display(11) == "1m"
    assert tile_display(19) == "9m"
    assert tile_display(25) == "5p"
    assert tile_display(33) == "3s"
    assert tile_display(41) == "1z"
    assert tile_display(47) == "7z"
    assert tile_display(51) == "0m"  # 红5万
    assert tile_display(52) == "0p"
    assert tile_display(53) == "0s"


def test_discard_parts():
    assert discard_parts(15) == (15, False, False)          # 手切
    assert discard_parts(60) == (None, True, False)         # 摸切
    assert discard_parts("r12") == (12, False, True)        # 立直手切
    assert discard_parts("r60") == (None, True, True)       # 立直摸切
    assert discard_parts(0) == (None, False, False)         # 大明杠占位


def test_is_callout():
    assert is_callout("c282930")            # 吃
    assert is_callout("47p4747")            # 碰
    assert is_callout("16m161616")          # 大明杠
    assert is_callout("1717k17")            # 加杠
    assert is_callout("151515a15")          # 暗杠
    assert not is_callout(28)               # 普通摸牌
    assert not is_callout("f44")            # 拔北不算副露


def test_tile_deaka():
    assert tile_deaka(51) == 15
    assert tile_deaka(53) == 35
    assert tile_deaka(15) == 15
    assert tile_deaka(41) == 41


def test_yaku_names_table():
    assert YAKU_NAMES[37] == "大三元"
    assert YAKU_NAMES[50] == "大四喜"
    assert YAKU_NAMES[31] == "ドラ"
    assert len(YAKU_NAMES) >= 60