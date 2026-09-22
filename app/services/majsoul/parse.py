"""雀魂 protobuf 对局记录 → 天凤格式（转换逻辑参考 tensoul，MIT）。

v1 限制：四麻；不实现包牌（大三元/大四喜责任払），pao 字段恒为和了家。
打点公式与 kyoku.py 对齐：荣和/自摸的 honba 均为 300/本（雀魂实际规则，样本验证）。
"""
from datetime import datetime

from . import liqi_combined_pb2 as pb

from app.services.paipu.yaku_names import YAKU_NAMES

_WINDS = "东南西北"


def tile_enc(s: str) -> int:
    """雀魂字符串牌（"5m"/"0p"/"1z"）→ 天凤数字编码。"""
    n, suit = int(s[0]), s[1]
    if n == 0:
        return {"m": 51, "p": 52, "s": 53}[suit]
    return {"m": 1, "p": 2, "s": 3, "z": 4}[suit] * 10 + n


def tile_deaka(code: int) -> int:
    return code - 51 + 15 if code in (51, 52, 53) else code


def relative_seating(a: int, b: int) -> int:
    """a 在 b 的第几个顺位（0上家 1对家 2下家），tensoul 口径。"""
    return (a - b + 3) % 4


def yaku_display(yaku_id: int, val: int, kyoku_index: int, seat: int, yiman: bool) -> str:
    if yaku_id == 10:
        name = f"自風 {_WINDS[(seat + kyoku_index) % 4]}"
    elif yaku_id == 11:
        name = f"場風 {_WINDS[kyoku_index // 4]}"
    elif yaku_id == 18:
        name = "両立直"
    else:
        name = YAKU_NAMES.get(yaku_id, f"役种{yaku_id}")
    return f"{name}({'役満' if yiman else f'{val}飜'})"


class TenhouKyokuBuilder:
    """逐条 feed Record* 消息，产出天凤 kyoku 数组列表。"""

    def __init__(self):
        self.out: list[list] = []
        self.cur: dict | None = None

    def feed(self, msg_cls, msg):
        name = msg_cls.DESCRIPTOR.name
        handler = {
            "RecordNewRound": self._new_round,
            "RecordDealTile": self._deal,
            "RecordDiscardTile": self._discard,
            "RecordChiPengGang": self._naki,
            "RecordAnGangAddGang": self._kan,
            "RecordBaBei": self._babei,
            "RecordLiuJu": self._liuju,
            "RecordNoTile": self._notile,
            "RecordHule": self._hule,
        }[name]
        handler(msg)

    # ---- 每局状态 ----
    def _new_round(self, m):
        haipais = [[tile_enc(t) for t in getattr(m, f"tiles{i}")] for i in range(4)]
        self.cur = {
            "round": [4 * m.chang + m.ju, m.ben, m.liqibang],
            "scores": list(m.scores),
            "doras": [tile_enc(t) for t in m.doras] if m.doras else [tile_enc(m.dora)],
            "uras": [], "haipais": haipais,
            "draws": [[] for _ in range(4)], "discards": [[] for _ in range(4)],
        }
        self.dealer = m.ju
        self.popped = haipais[m.ju].pop()  # 庄家第14张视为首摸
        self.cur["draws"][m.ju].append(self.popped)
        self.ldseat = -1
        self.priichi = False
        self.nriichi = 0
        self.nkan = 0

    def _accept_riichi(self):
        if self.priichi:
            self.priichi = False
            self.nriichi += 1

    def _update_doras(self, m):
        doras = getattr(m, "doras", None)
        if doras and len(doras) > len(self.cur["doras"]):
            self.cur["doras"] = [tile_enc(t) for t in doras]

    def _discard(self, m):
        t = tile_enc(m.tile)
        tsumogiri = m.moqie or (
            m.seat == self.dealer and len(self.cur["discards"][m.seat]) == 0
            and t == self.popped)
        if m.is_liqi:
            self.priichi = True
            sym = "r60" if tsumogiri else f"r{t}"
        else:
            sym = 60 if tsumogiri else t
        self.cur["discards"][m.seat].append(sym)
        self.ldseat = m.seat
        self._update_doras(m)

    def _deal(self, m):
        self._accept_riichi()
        self.cur["draws"][m.seat].append(tile_enc(m.tile))
        self._update_doras(m)

    def _naki(self, m):
        self._accept_riichi()
        tiles = [tile_enc(t) for t in m.tiles]
        if m.type == 0:  # 吃：tiles=[手1,手2,被叫]
            self.cur["draws"][m.seat].append(f"c{tiles[2]}{tiles[0]}{tiles[1]}")
        elif m.type == 1:  # 碰
            idx = relative_seating(m.seat, self.ldseat)
            parts = [str(tiles[0]), str(tiles[1])]
            parts.insert(idx, f"p{tiles[2]}")
            self.cur["draws"][m.seat].append("".join(parts))
        elif m.type == 2:  # 大明杠
            idx = relative_seating(m.seat, self.ldseat)
            pos = 3 if idx == 2 else idx
            parts = [str(tiles[0]), str(tiles[1]), str(tiles[2])]
            parts.insert(pos, f"m{tiles[3]}")
            self.cur["draws"][m.seat].append("".join(parts))
            self.cur["discards"][m.seat].append(0)
            self.nkan += 1
        else:
            raise ValueError(f"未知 RecordChiPengGang.type={m.type}")

    def _kan(self, m):
        t = tile_enc(m.tiles)
        if m.type == 3:  # 暗杠
            deaka = tile_deaka(t)
            sym = f"{t}{deaka}{deaka}a{deaka}" if t != deaka else f"{deaka}{deaka}{deaka}a{deaka}"
            self.cur["discards"][m.seat].append(sym)
            self.nkan += 1
        elif m.type == 2:  # 加杠：找到原碰，产出 kakan 符号进切牌序列
            for sym in self.cur["draws"][m.seat]:
                if not isinstance(sym, str) or "p" not in sym:
                    continue
                idx = sym.index("p")
                hand = sym[:idx]
                pon_tile = int(sym[idx + 1:])
                if tile_deaka(pon_tile) == tile_deaka(t):
                    parts = [hand[0:2], hand[2:4], str(pon_tile)]
                    parts.insert(idx // 2 if idx % 2 == 0 else idx // 2,
                                 f"k{t}")
                    self.cur["discards"][m.seat].append("".join(parts))
                    self.nkan += 1
                    break
        else:
            raise ValueError(f"未知 RecordAnGangAddGang.type={m.type}")

    def _babei(self, m):
        self.cur["discards"][m.seat].append("f44")

    def _liuju(self, m):
        self._accept_riichi()
        if m.type == 1:
            name = "九種九牌"
        elif m.type == 2:
            name = "四風連打"
        elif self.nriichi == 4:
            name = "四家立直"
        elif self.nkan == 4:
            name = "四開槓"
        else:
            raise ValueError(f"未知途中流局 type={m.type} riichi={self.nriichi} kan={self.nkan}")
        self._finish([name])

    def _notile(self, m):
        delta = [0, 0, 0, 0]
        for s in m.scores:
            if not s.delta_scores:
                continue
            if len(s.delta_scores) == 4:
                for i, g in enumerate(s.delta_scores):
                    delta[i] += g
            else:
                delta[s.seat] += s.delta_scores[0]
        self._finish(["流し満貫" if m.liujumanguan else "流局", delta])

    # ---- 和了（点数计算参考 tensoul，已用真实样本验证口径）----
    def _hule_delta(self, h, rp: int, hb: int) -> list[int]:
        if h.zimo:
            delta = [-hb - h.point_zimo_xian] * 4
            if h.qinjia:
                delta[h.seat] = rp + 3 * (hb + h.point_zimo_xian)
            else:
                delta[h.seat] = rp + hb + h.point_zimo_qin + 2 * (hb + h.point_zimo_xian)
                delta[self.dealer] = -hb - h.point_zimo_qin
        else:
            delta = [0] * 4
            delta[h.seat] = rp + 3 * hb + h.point_rong
            delta[self.ldseat] = -(3 * hb + h.point_rong)
        return delta

    def _hule_detail(self, h, delta: list[int]) -> list:
        loser = h.seat if h.zimo else self.ldseat
        if h.zimo:
            point = (f"{h.point_zimo_xian}点∀" if h.qinjia
                     else f"{h.point_zimo_xian}-{h.point_zimo_qin}点")
        else:
            point = f"{h.point_rong}点"
        fuhan = f"{h.fu}符{h.count}飜"
        total = (3 * h.point_zimo_xian if h.qinjia
                 else 2 * h.point_zimo_xian + h.point_zimo_qin) if h.zimo else h.point_rong
        prefix = ""
        if total >= 32000:
            prefix = "数え役満" if h.count >= 13 and not h.yiman else "役満"
            fuhan = ""
        elif total >= 24000:
            prefix, fuhan = "三倍満", ""
        elif total >= 16000:
            prefix, fuhan = "倍満", ""
        elif total >= 12000:
            prefix, fuhan = "跳満", ""
        elif total >= 8000:
            prefix = "満貫"
            if not (h.count >= 5 or (h.count >= 4 and h.fu >= 40)
                    or (h.count >= 3 and h.fu >= 70)):
                prefix = "切り上げ満貫"
            fuhan = ""
        detail = [h.seat, loser, h.seat, fuhan + prefix + point]
        kyoku_index = self.cur["round"][0]
        for fan in h.fans:
            detail.append(yaku_display(fan.id, fan.val, kyoku_index, h.seat, h.yiman))
        return detail

    def _hule(self, m):
        self._accept_riichi()
        rp = 1000 * (self.nriichi + self.cur["round"][2])
        hb = 100 * self.cur["round"][1]
        taken = False
        result = ["和了"]
        for h in m.hules:
            delta = self._hule_delta(h, rp if not taken else 0, hb)
            taken = True
            result.append(delta)
            result.append(self._hule_detail(h, delta))
            if h.li_doras and len(self.cur["uras"]) < len(h.li_doras):
                self.cur["uras"] = [tile_enc(t) for t in h.li_doras]
        self._finish(result)

    def _finish(self, result):
        entry = [self.cur["round"], self.cur["scores"], list(self.cur["doras"]),
                 list(self.cur["uras"])]
        for i in range(4):
            entry.append(list(self.cur["haipais"][i]))
            entry.append(list(self.cur["draws"][i]))
            entry.append(list(self.cur["discards"][i]))
        entry.append(result)
        self.out.append(entry)


def parse_detail_records(detail) -> list[list]:
    """GameDetailRecords → 天凤 kyoku 列表（兼容旧 records / 新 actions 两种格式）。"""
    builder = TenhouKyokuBuilder()

    def _iter_wrappers():
        if detail.version < 210715 and len(detail.records) > 0:
            for raw in detail.records:
                w = pb.Wrapper()
                w.ParseFromString(raw)
                yield w
        else:
            for act in detail.actions:
                if not act.result:
                    continue
                w = pb.Wrapper()
                w.ParseFromString(act.result)
                yield w

    for w in _iter_wrappers():
        cls_name = w.name[len(".lq."):]
        msg_cls = getattr(pb, cls_name, None)
        if msg_cls is None:
            continue
        msg = msg_cls()
        msg.ParseFromString(w.data)
        builder.feed(msg_cls, msg)
    return builder.out


def record_head_to_game(head) -> dict:
    """RecordGame（对局头）→ 天凤 JSON 顶部字段 + account_ids。

    返回 dict 可直接喂给 ingest_tenhou_game（补上 log 后）。
    """
    scores = [0, 0, 0, 0]
    pts = [0.0, 0.0, 0.0, 0.0]
    for p in head.result.players:
        scores[p.seat] = p.part_point_1
        pts[p.seat] = p.total_point / 1000.0
    account_ids = {acc.seat: acc.account_id for acc in head.accounts if acc.seat is not None}
    names = [""] * 4
    for acc in head.accounts:
        names[acc.seat] = acc.nickname
    sc = []
    for i in range(4):
        sc.extend([scores[i], pts[i]])
    start = datetime.fromtimestamp(head.start_time).strftime("%Y-%m-%d %H:%M:%S") if head.start_time else ""
    return {
        "ver": "2.3",
        "ref": head.uuid,
        "name": names,
        "sc": sc,
        "title": ["大会戦", start],
        "rule": {"disp": "四麻 半庄", "aka51": 1, "aka52": 1, "aka53": 1},
        "account_ids": account_ids,
    }
