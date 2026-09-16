import json
from pathlib import Path

from app.services.paipu.kyoku import analyze_kyoku, seat_title

SAMPLE = json.loads((Path(__file__).resolve().parents[1] / "sample_paipu.json").read_text(encoding="utf-8"))
KYOKUS = SAMPLE["log"]


def test_seat_title():
    assert seat_title(0, 0) == "东一局"
    assert seat_title(1, 2) == "东二局 2本"
    assert seat_title(4, 1) == "南一局 1本"
    assert seat_title(7, 0) == "南四局"


def test_analyze_ryukyoku_kyoku():
    # 样本第0局：东一局，流局，座位3立直(r12)
    s = analyze_kyoku(KYOKUS[0])
    assert s["round"] == [0, 0, 0]
    assert s["title"] == "东一局"
    assert s["end"] == "ryukyoku"
    assert s["deltas"] == [-1500, -1500, 1500, 1500]
    assert s["seats"][3]["riichi"] is True
    assert s["seats"][3]["riichi_turn"] == 6
    assert s["seats"][3]["callouts"] == 1  # "47p4747" 碰
    assert s["agari"] == []
    assert s["uras"] == []


def test_analyze_agari_kyoku():
    # 样本第2局：东二局2本，座位3荣和2000点+1000棒+600本=3600
    s = analyze_kyoku(KYOKUS[2])
    assert s["round"] == [1, 2, 1]
    assert s["end"] == "agari"
    assert len(s["agari"]) == 1
    a = s["agari"][0]
    assert a["winner"] == 3
    assert a["loser"] == 0
    assert a["tsumo"] is False
    assert a["score"] == 2000  # 打点 = 3600 - 1000棒 - 300*2本
    assert a["delta"][3] == 3600
    assert "役牌 白(1飜)" in a["yaku"]
    assert a["fu_han"] == "30符2飜2000点"
    assert s["deltas"] == [-2600, 0, 0, 3600]


def test_analyze_all_kyokus_no_crash():
    for k in KYOKUS:
        s = analyze_kyoku(k)
        assert s["end"] in ("agari", "ryukyoku", "nagashi", "abortive")
        assert len(s["seats"]) == 4
        assert len(s["deltas"]) == 4


def test_double_ron_sticks_atamahane():
    # 构造双响：第一和者拿立直棒，第二和者不拿
    k = [
        [0, 0, 1], [25000] * 4, [11], [],
        [11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 23, 24],
        [], ["r19"],
        [11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 23, 24],
        [], [19],
        [11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 23, 24],
        [], [19],
        [11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 23, 24],
        [], [19],
        ["和了", [0, -1000, 4000, -3000], [2, 3, 2, "30符1飜1000点", "立直(1飜)"],
         [0, -3000, 4000, -1000], [0, 3, 0, "30符1飜1000点", "役牌 白(1飜)"]],
    ]
    s = analyze_kyoku(k)
    assert len(s["agari"]) == 2
    # 第一和者(座位2)：4000 - 1000(局始棒) - 1000(局中宣言立直) = 2000
    assert s["agari"][0]["score"] == 2000
    # 第二和者(座位0)：4000 - 0 - 0 = 4000
    assert s["agari"][1]["score"] == 4000
    assert s["deltas"] == [0, -4000, 8000, -4000]