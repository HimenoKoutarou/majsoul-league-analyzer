import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app" / "services" / "majsoul"))

import liqi_combined_pb2 as pb  # noqa: E402

from app.services.majsoul.parse import (  # noqa: E402
    TenhouKyokuBuilder, parse_detail_records, record_head_to_game, tile_enc,
)


def _new_round(chang=0, ju=0, ben=0, liqibang=0, dora="1z"):
    m = pb.RecordNewRound()
    m.chang, m.ju, m.ben, m.liqibang = chang, ju, ben, liqibang
    m.dora = dora
    m.scores.extend([25000] * 4)
    for i in range(4):
        tiles = getattr(m, f"tiles{i}")
        # 14 张（庄家末张为首摸移到摸牌序列，配牌剩 13 张），末张必须是 4p
        tiles.extend(["1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m", "4z",
                      "1p", "2p", "3p", "4p"])
    return m


def test_tile_enc():
    assert tile_enc("1m") == 11
    assert tile_enc("9m") == 19
    assert tile_enc("5p") == 25
    assert tile_enc("0s") == 53
    assert tile_enc("7z") == 47


def test_full_kyoku_ron_flow():
    b = TenhouKyokuBuilder()
    b.feed(pb.RecordNewRound, _new_round())
    # 庄家(座位0)第一切：手切 4p
    d = pb.RecordDiscardTile()
    d.seat, d.tile, d.moqie = 0, "4p", False
    b.feed(pb.RecordDiscardTile, d)
    # 座位1 摸牌
    t = pb.RecordDealTile()
    t.seat, t.tile = 1, "5s"
    b.feed(pb.RecordDealTile, t)
    # 座位1 立直宣言切牌（摸切）
    d2 = pb.RecordDiscardTile()
    d2.seat, d2.tile, d2.moqie, d2.is_liqi = 1, "5s", True, True
    b.feed(pb.RecordDiscardTile, d2)
    # 座位2 摸牌（立直成立）
    t2 = pb.RecordDealTile()
    t2.seat, t2.tile = 2, "1z"
    b.feed(pb.RecordDealTile, t2)
    # 座位2 手切
    d3 = pb.RecordDiscardTile()
    d3.seat, d3.tile, d3.moqie = 2, "1z", False
    b.feed(pb.RecordDiscardTile, d3)
    # 座位3 荣和：30符1飜1000点
    h = pb.RecordHule()
    info = h.hules.add()
    info.seat, info.zimo, info.qinjia = 3, False, False
    info.fu, info.count = 30, 1
    info.point_rong = 1000
    fan = info.fans.add()
    fan.id, fan.val = 7, 1  # 役牌 白（测试用）
    b.feed(pb.RecordHule, h)

    out = b.out
    assert len(out) == 1
    k = out[0]
    assert k[0] == [0, 0, 0]
    assert k[1] == [25000] * 4
    assert k[2] == [41]
    # 座位0: 配牌13张（庄家第14张移到摸牌序列）+ 首切摸切
    assert len(k[4]) == 13
    assert k[5] == [24]           # 4p 庄家第一摸
    assert k[6] == [60]           # 首切即摸切（tensoul 口径）
    # 座位1: 立直摸切
    assert k[9][-1] == "r60"
    # 结果
    assert k[16][0] == "和了"
    delta = k[16][1]
    assert delta[3] == 1000 + 1000  # 点数+立直棒
    assert delta[2] == -1000
    detail = k[16][2]
    assert detail[0] == 3 and detail[1] == 2 and detail[2] == 3
    assert detail[3] == "30符1飜1000点"
    assert detail[4] == "役牌 白(1飜)"


def test_chi_pon_flow():
    b = TenhouKyokuBuilder()
    b.feed(pb.RecordNewRound, _new_round())
    # 座位0 切 1m → 座位1 吃
    d = pb.RecordDiscardTile()
    d.seat, d.tile, d.moqie = 0, "1m", False
    b.feed(pb.RecordDiscardTile, d)
    n = pb.RecordChiPengGang()
    n.seat, n.type = 1, 0  # 吃
    n.tiles.extend(["2m", "3m", "1m"])  # 手牌1,手牌2,被叫牌
    b.feed(pb.RecordChiPengGang, n)
    # 座位0 切 2z → 座位2 碰
    d2 = pb.RecordDiscardTile()
    d2.seat, d2.tile, d2.moqie = 0, "2z", False
    b.feed(pb.RecordDiscardTile, d2)
    n2 = pb.RecordChiPengGang()
    n2.seat, n2.type = 2, 1  # 碰
    n2.tiles.extend(["2z", "2z", "2z"])  # 雀魂字符串牌 → tile_enc 得 42
    b.feed(pb.RecordChiPengGang, n2)
    k = b.cur
    assert k["draws"][1][-1] == "c111213"       # c+被叫1m+手2m+手3m
    # 碰：座位2 在座位0 的下家 → feeder_relative = (2-0+3)%4 = 1
    assert k["draws"][2][-1] == "42p4242"


def test_kan_and_ryukyoku():
    b = TenhouKyokuBuilder()
    b.feed(pb.RecordNewRound, _new_round())
    # 座位0 暗杠 9m
    a = pb.RecordAnGangAddGang()
    a.seat, a.type, a.tiles = 0, 3, "9m"
    b.feed(pb.RecordAnGangAddGang, a)
    assert b.cur["discards"][0][-1] == "191919a19"
    # 荒牌流局
    nt = pb.RecordNoTile()
    s = nt.scores.add()
    s.seat = 0
    s.delta_scores.append(-1500)
    s = nt.scores.add()
    s.seat = 1
    s.delta_scores.append(-1500)
    s = nt.scores.add()
    s.seat = 2
    s.delta_scores.append(1500)
    s = nt.scores.add()
    s.seat = 3
    s.delta_scores.append(1500)
    b.feed(pb.RecordNoTile, nt)
    k = b.out[0]
    assert k[16][0] == "流局"
    assert k[16][1] == [-1500, -1500, 1500, 1500]


def test_parse_detail_records_actions_path():
    # 新版协议：actions[].result 内嵌 Wrapper
    detail = pb.GameDetailRecords()
    detail.version = 220101
    w = pb.Wrapper(name=".lq.RecordNewRound", data=_new_round().SerializeToString())
    act = detail.actions.add()
    act.result = w.SerializeToString()
    # 再补一个荒牌流局让其成局
    nt = pb.RecordNoTile()
    for seat in range(4):
        s = nt.scores.add()
        s.seat = seat
        s.delta_scores.append(0)
    w2 = pb.Wrapper(name=".lq.RecordNoTile", data=nt.SerializeToString())
    act2 = detail.actions.add()
    act2.result = w2.SerializeToString()
    out = parse_detail_records(detail)
    assert len(out) == 1
    assert out[0][0] == [0, 0, 0]


def test_record_head_to_game():
    head = pb.RecordGame()
    head.uuid = "260915-test-uuid"
    head.start_time = 1700000000
    head.end_time = 1700003600
    for seat, (nick, aid) in enumerate([("a", 1), ("b", 2), ("c", 3), ("d", 4)]):
        acc = head.accounts.add()
        acc.seat, acc.nickname, acc.account_id = seat, nick, aid
    res = head.result.players.add()
    res.seat, res.part_point_1, res.total_point = 0, 26200, 46200
    res = head.result.players.add()
    res.seat, res.part_point_1, res.total_point = 1, 24400, -35600

    data = record_head_to_game(head)
    assert data["ref"] == "260915-test-uuid"
    assert data["name"] == ["a", "b", "c", "d"]
    # sc 按 [score_i, pt_i] 交错排列（与 ingest 读取口径一致）
    assert data["sc"] == [26200, 46.2, 24400, -35.6, 0, 0, 0, 0]
    assert data["account_ids"] == {0: 1, 1: 2, 2: 3, 3: 4}
    assert data["title"][1].startswith("20")