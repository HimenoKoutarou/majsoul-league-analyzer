"""聚合统计（查询层 sum/divide）。"""
from collections import Counter, defaultdict
from datetime import datetime
import re

from sqlalchemy.orm import Session

from app.models import Game, GamePlayer, Kyoku, Player, Team
from app.services.paipu.tiles import tile_deaka
from app.services.score import raw_point_delta

_URA_FAN_RE = re.compile(r"裏ドラ[（(](\d+)飜[）)]")
_YAKU_FAN_SUFFIX_RE = re.compile(r"[（(]\d+飜[）)]$")
_FAN_RE = re.compile(r"[（(](\d+)飜[）)]")


def is_countable_yaku(name: str) -> bool:
    """统计役种时排除没有实际得分的里宝牌。详情页仍保留原始文本。"""
    match = _URA_FAN_RE.search(str(name))
    return not match or int(match.group(1)) > 0


def yaku_stat_name(name: str) -> str:
    """役种统计只保留役名，不把本局飜数拆成不同条目。"""
    return _YAKU_FAN_SUFFIX_RE.sub("", str(name))


def _fan_value(names: list[str], fu_han: str = "") -> int:
    if any("役満" in str(name) for name in names) or "役満" in fu_han:
        return 13
    values = [int(match.group(1)) for match in
              (_FAN_RE.search(str(name)) for name in names) if match]
    if values:
        return sum(values)
    match = re.search(r"(\d+)飜", str(fu_han))
    return int(match.group(1)) if match else 0


def _is_mangan_or_above(agari: dict) -> bool:
    """判断和牌是否达到满贯以上，兼容中日文牌谱文本。"""
    names = [str(name) for name in agari.get("yaku") or []]
    fu_han = str(agari.get("fu_han") or "")
    limit_labels = (
        "満貫", "满贯", "跳満", "跳满", "倍満", "倍满",
        "三倍満", "三倍满", "役満", "役满",
    )
    if any(label in fu_han or any(label in name for name in names)
           for label in limit_labels):
        return True
    return _fan_value(names, fu_han) >= 5


def _tile_index(code: int) -> int | None:
    code = tile_deaka(int(code))
    suit, number = divmod(code, 10)
    if suit in (1, 2, 3) and 1 <= number <= 9:
        return (suit - 1) * 9 + number - 1
    if suit == 4 and 1 <= number <= 7:
        return 27 + number - 1
    return None


def _tile_counts(codes: list[int]) -> list[int]:
    counts = [0] * 34
    for code in codes:
        index = _tile_index(code)
        if index is not None and counts[index] < 4:
            counts[index] += 1
    return counts


def _is_suit(index: int) -> bool:
    return index < 27


def _normal_shanten(counts: list[int], fixed_melds: int = 0) -> int:
    best = 8 - fixed_melds * 2
    work = counts[:]

    def visit(index: int, melds: int, pairs: int, taatsu: int) -> None:
        nonlocal best
        while index < 34 and work[index] == 0:
            index += 1
        if index >= 34:
            usable_taatsu = min(taatsu, 4 - fixed_melds - melds)
            best = min(best, 8 - (fixed_melds + melds) * 2
                       - usable_taatsu - pairs)
            return

        if work[index] >= 3:
            work[index] -= 3
            visit(index, melds + 1, pairs, taatsu)
            work[index] += 3
        if _is_suit(index) and index % 9 <= 6 and work[index + 1] and work[index + 2]:
            work[index] -= 1
            work[index + 1] -= 1
            work[index + 2] -= 1
            visit(index, melds + 1, pairs, taatsu)
            work[index] += 1
            work[index + 1] += 1
            work[index + 2] += 1
        if pairs == 0 and work[index] >= 2:
            work[index] -= 2
            visit(index, melds, 1, taatsu)
            work[index] += 2
        if work[index] >= 2:
            work[index] -= 2
            visit(index, melds, pairs, taatsu + 1)
            work[index] += 2
        if _is_suit(index) and index % 9 <= 7 and work[index + 1]:
            work[index] -= 1
            work[index + 1] -= 1
            visit(index, melds, pairs, taatsu + 1)
            work[index] += 1
            work[index + 1] += 1
        if _is_suit(index) and index % 9 <= 6 and work[index + 2]:
            work[index] -= 1
            work[index + 2] -= 1
            visit(index, melds, pairs, taatsu + 1)
            work[index] += 1
            work[index + 2] += 1
        work[index] -= 1
        visit(index, melds, pairs, taatsu)
        work[index] += 1

    visit(0, 0, 0, 0)
    return best


def _shanten(counts: list[int], fixed_melds: int = 0) -> int:
    normal = _normal_shanten(counts, fixed_melds)
    if fixed_melds:
        return normal
    pairs = sum(value >= 2 for value in counts)
    unique = sum(value > 0 for value in counts)
    chiitoi = 6 - pairs + max(0, 7 - unique)
    terminals = sum(counts[index] > 0 for index in
                    (*range(0, 9, 9), *range(8, 27, 9), *range(27, 34)))
    has_terminal_pair = any(counts[index] >= 2 for index in
                            (*range(0, 9, 9), *range(8, 27, 9), *range(27, 34)))
    kokushi = 13 - terminals - int(has_terminal_pair)
    return min(normal, chiitoi, kokushi)


def _is_agari(counts: list[int], fixed_melds: int = 0) -> bool:
    if sum(counts) != 14 - fixed_melds * 3:
        return False

    def complete_standard(work: list[int], melds: int, pair: bool) -> bool:
        index = next((i for i, value in enumerate(work) if value), None)
        if index is None:
            return melds == 4 - fixed_melds and pair
        if not pair and work[index] >= 2:
            work[index] -= 2
            if complete_standard(work, melds, True):
                work[index] += 2
                return True
            work[index] += 2
        if work[index] >= 3:
            work[index] -= 3
            if complete_standard(work, melds + 1, pair):
                work[index] += 3
                return True
            work[index] += 3
        if _is_suit(index) and index % 9 <= 6 and work[index + 1] and work[index + 2]:
            work[index] -= 1
            work[index + 1] -= 1
            work[index + 2] -= 1
            if complete_standard(work, melds + 1, pair):
                work[index] += 1
                work[index + 1] += 1
                work[index + 2] += 1
                return True
            work[index] += 1
            work[index + 1] += 1
            work[index + 2] += 1
        return False

    if complete_standard(counts[:], 0, False):
        return True
    if not fixed_melds and sum(value >= 2 for value in counts) == 7:
        return True
    terminals = (*range(0, 9, 9), *range(8, 27, 9), *range(27, 34))
    return (not fixed_melds
            and all(counts[index] for index in terminals)
            and any(counts[index] >= 2 for index in terminals))


def _waits(counts: list[int], fixed_melds: int = 0) -> set[int]:
    waits = set()
    if sum(counts) != 13 - fixed_melds * 3:
        return waits
    for index, value in enumerate(counts):
        if value >= 4:
            continue
        counts[index] += 1
        if _is_agari(counts, fixed_melds):
            waits.add(index)
        counts[index] -= 1
    return waits


def _can_partition_standard(counts: list[int], melds: int, pair: bool = True) -> bool:
    """判断剩余牌能否拆成指定数量的面子和一对将。"""
    if sum(counts) != melds * 3 + (2 if pair else 0):
        return False
    index = next((i for i, value in enumerate(counts) if value), None)
    if index is None:
        return melds == 0 and not pair

    if pair and counts[index] >= 2:
        counts[index] -= 2
        if _can_partition_standard(counts, melds, False):
            counts[index] += 2
            return True
        counts[index] += 2
    if melds <= 0:
        return False
    if counts[index] >= 3:
        counts[index] -= 3
        if _can_partition_standard(counts, melds - 1, pair):
            counts[index] += 3
            return True
        counts[index] += 3
    if (_is_suit(index) and index % 9 <= 6
            and counts[index + 1] and counts[index + 2]):
        counts[index] -= 1
        counts[index + 1] -= 1
        counts[index + 2] -= 1
        if _can_partition_standard(counts, melds - 1, pair):
            counts[index] += 1
            counts[index + 1] += 1
            counts[index + 2] += 1
            return True
        counts[index] += 1
        counts[index + 1] += 1
        counts[index + 2] += 1
    return False


def _is_ryanmen_completion(counts: list[int], fixed_melds: int,
                           wait: int) -> bool:
    """判断该等待牌是否存在两面（含多面中的两面）完成方式。"""
    if wait >= 27:
        return False
    rank = wait % 9
    for start in range(max(0, rank - 2), min(6, rank) + 1):
        sequence = (start, start + 1, start + 2)
        if wait not in sequence:
            continue
        wait_position = sequence.index(wait)
        # 123 等 3、789 等 7 是边张，不属于两面。
        if wait_position == 1:
            continue
        if wait_position == 0 and start == 6:
            continue
        if wait_position == 2 and start == 0:
            continue
        needed = [0] * 34
        for index in sequence:
            needed[index + wait // 9 * 9] += 1
        completed = counts[:]
        completed[wait] += 1
        for index, amount in enumerate(needed):
            completed[index] -= amount
        if any(value < 0 for value in completed):
            continue
        if _can_partition_standard(completed, 3 - fixed_melds, True):
            return True
    return False


def _wait_shape(counts: list[int], waits: set[int],
                fixed_melds: int = 0) -> tuple[bool, bool]:
    """返回 (多面, 好型)，好型定义为两面或三面以上等待。"""
    multi = len(waits) >= 3
    good = multi or any(
        _is_ryanmen_completion(counts, fixed_melds, wait)
        for wait in waits
    )
    return multi, good


def _meld_info(value: str) -> tuple[list[int], int, str] | None:
    if not isinstance(value, str):
        return None
    if value.startswith("c"):
        tokens = value[1:]
        tiles = [int(tokens[i:i + 2]) for i in range(0, len(tokens), 2)]
        return tiles, 0, "chi"
    tokens = re.findall(r"(?:[pmka])?\d{2}", value)
    if not tokens:
        return None
    tiles = [int(token[-2:]) for token in tokens]
    marker = next((i for i, token in enumerate(tokens) if token[0] in "pmk"), -1)
    if "a" in value:
        return tiles, -1, "ankan"
    if "k" in value:
        return tiles, marker, "kakan"
    if "m" in value:
        return tiles, marker, "minkan"
    return tiles, marker, "pon"


def _remove_code(codes: list[int], target: int) -> None:
    normalized = tile_deaka(target)
    for index, code in enumerate(codes):
        if tile_deaka(code) == normalized:
            codes.pop(index)
            return


def _riichi_states(
    raw: list, seat: int
) -> tuple[list[int], list[tuple[list[int], set[int], int]], int, list[int]]:
    """还原起手牌，以及每次立直时的手牌、等待牌和副露数。"""
    hand = list(raw[4 + 3 * seat] or [])
    draws = raw[5 + 3 * seat] or []
    discards = raw[6 + 3 * seat] or []
    discard_index = 0
    last_draw = None
    fixed_melds = 0
    discarded_tiles: list[int] = []
    riichi_states: list[tuple[list[int], set[int], int]] = []

    def consume_meld_discard(value: object) -> None:
        nonlocal fixed_melds
        info = _meld_info(value)
        if not info:
            return
        tiles, called_index, meld_type = info
        if meld_type == "ankan":
            for tile in tiles:
                _remove_code(hand, tile)
            fixed_melds += 1
        elif meld_type == "kakan":
            _remove_code(hand, tiles[called_index if called_index >= 0 else -1])
        elif meld_type in ("chi", "pon", "minkan"):
            fixed_melds += 1

    def next_discard() -> object:
        nonlocal discard_index
        while discard_index < len(discards):
            value = discards[discard_index]
            discard_index += 1
            if value == 0:
                continue
            if isinstance(value, str) and not re.fullmatch(r"r?(?:\d{2}|60)", value):
                consume_meld_discard(value)
                continue
            return value
        return None

    def consume_discard(value: object, drawn: int | None) -> None:
        if value is None or value == 0:
            return
        marked_riichi = isinstance(value, str) and value.startswith("r")
        body = value[1:] if marked_riichi else value
        # 天凤格式的摸切标记既可能是整数 60，也可能是字符串 "60"。
        tile = drawn if str(body) == "60" else int(body)
        if tile is None:
            return
        _remove_code(hand, tile)
        tile_index = _tile_index(tile)
        if tile_index is not None:
            discarded_tiles.append(tile_index)
        if marked_riichi:
            riichi_hand = list(hand)
            riichi_states.append((
                riichi_hand,
                _waits(_tile_counts(riichi_hand), fixed_melds),
                fixed_melds,
            ))

    for draw in draws:
        if isinstance(draw, int):
            hand.append(draw)
            last_draw = draw
            consume_discard(next_discard(), draw)
            continue
        info = _meld_info(draw)
        if not info:
            continue
        tiles, called_index, meld_type = info
        concealed = tiles if called_index < 0 else [
            tile for index, tile in enumerate(tiles) if index != called_index
        ]
        for tile in concealed:
            _remove_code(hand, tile)
        if meld_type != "kakan":
            fixed_melds += 1
        if meld_type in ("chi", "pon"):
            consume_discard(next_discard(), None)

    return list(raw[4 + 3 * seat] or []), riichi_states, fixed_melds, discarded_tiles


def _profile_rows(db: Session, by: str, target_id: int | None) -> list[GamePlayer]:
    if by == "team":
        rows = (_rows_for(db, "team", target_id) if target_id is not None
                else _rows_for(db, "team", None))
        return [gp for gp, _player, _team in rows]
    return _player_rows(db, target_id)


def _empty_profile() -> dict:
    return {
        "basic": {}, "riichi": {}, "more": {}, "distribution": {},
        "lineage": {}, "recent_large": [], "tablemates": [],
    }


def profile_stats(db: Session, by: str, target_id: int | None = None) -> dict:
    """提供牌谱屋风格的分栏统计。

    起手向听、振听、多面和好型等字段从牌谱原始的摸牌/舍牌序列中计算。
    """
    rows = _profile_rows(db, by, target_id)
    if not rows:
        return _empty_profile()

    game_ids = {row.game_uuid for row in rows}
    games_by_id = {
        game.uuid: game
        for game in db.query(Game).filter(Game.uuid.in_(game_ids)).all()
    }
    all_games = db.query(Game).all()
    all_games.sort(key=lambda game: (
        game.start_time is None,
        game.start_time or datetime.min,
        game.uuid,
    ))
    hanchan_by_game: dict[str, int] = {}
    hanchan_count_by_date: Counter = Counter()
    for game in all_games:
        if game.start_time is None:
            continue
        match_date = game.start_time.date().isoformat()
        hanchan_count_by_date[match_date] += 1
        hanchan_by_game[game.uuid] = hanchan_count_by_date[match_date]
    kyokus = (db.query(Kyoku).filter(Kyoku.game_uuid.in_(game_ids))
              .order_by(Kyoku.game_uuid, Kyoku.index).all())
    kyoku_by_game: dict[str, list[Kyoku]] = defaultdict(list)
    for kyoku in kyokus:
        kyoku_by_game[kyoku.game_uuid].append(kyoku)

    counters = Counter()
    win_points = 0
    dealin_points = 0
    initial_score_total = 0
    riichi_income = 0
    riichi_expense = 0
    riichi_turns = []
    fan_values = []
    recent_wins = []
    recent_losses = []
    start_shanten_sum = 0
    start_shanten_count = 0
    dealer_start_shanten_sum = 0
    dealer_start_shanten_count = 0
    nondealer_start_shanten_sum = 0
    nondealer_start_shanten_count = 0
    max_consecutive_dealer = 0
    tablemate_games: Counter = Counter()
    tablemate_names: dict[object, str] = {}
    tablemate_wins: Counter = Counter()
    tablemate_self_pt: Counter = Counter()
    tablemate_opponent_pt: Counter = Counter()
    target_game_ids = {row.game_uuid for row in rows}
    all_players_by_game: dict[str, list[GamePlayer]] = defaultdict(list)
    if target_game_ids:
        for gp in db.query(GamePlayer).filter(GamePlayer.game_uuid.in_(target_game_ids)).all():
            all_players_by_game[gp.game_uuid].append(gp)
    player_team_ids = {
        player.id: player.team_id
        for player in db.query(Player).all()
    }

    def match_context(row: GamePlayer) -> dict:
        game = games_by_id.get(row.game_uuid)
        match_date = game.start_time.date().isoformat() if game and game.start_time else None
        opponents = []
        for player in all_players_by_game.get(row.game_uuid, []):
            if player.seat == row.seat:
                continue
            if (by == "team" and target_id is not None
                    and player_team_ids.get(player.player_id) == target_id):
                continue
            if player.nickname not in opponents:
                opponents.append(player.nickname)
        return {
            "match_date": match_date,
            "hanchan": hanchan_by_game.get(row.game_uuid),
            "opponents": opponents,
        }

    for row in rows:
        counters["games"] += 1
        counters[f"rank_{row.rank}"] += 1
        counters["raw_points"] += raw_point_delta(row.final_score)
        counters["final_score_total"] += row.final_score or 0
        counters["pt"] += row.pt or 0
        first_kyoku = kyoku_by_game.get(row.game_uuid, [])
        first_summary = first_kyoku[0].summary if first_kyoku else {}
        start_scores = (first_summary or {}).get("start_scores") or []
        initial_score_total += (
            int(start_scores[row.seat])
            if row.seat < len(start_scores) and start_scores[row.seat] is not None
            else 25000
        )
        for mate in all_players_by_game.get(row.game_uuid, []):
            if mate.seat == row.seat:
                continue
            key = mate.player_id if mate.player_id is not None else f"guest:{mate.nickname}"
            tablemate_games[key] += 1
            tablemate_names[key] = mate.nickname
            tablemate_wins[key] += int(row.rank == 1)
            tablemate_self_pt[key] += float(row.pt or 0)
            tablemate_opponent_pt[key] += float(mate.pt or 0)

        dealer_run = 0
        max_dealer_run = 0
        for kyoku in kyoku_by_game.get(row.game_uuid, []):
            summary = kyoku.summary or {}
            seats = summary.get("seats") or []
            seat = seats[row.seat] if row.seat < len(seats) else {}
            riichi = bool(seat.get("riichi"))
            callouts = int(seat.get("callouts") or 0)
            raw = kyoku.data or []
            if raw:
                start_codes, riichi_states, _fixed_melds, discarded_tiles = _riichi_states(
                    raw, row.seat)
                start_shanten = _shanten(_tile_counts(start_codes))
                start_shanten_sum += start_shanten
                start_shanten_count += 1
                dealer = int((summary.get("round") or [0])[0]) % 4
                if dealer == row.seat:
                    dealer_start_shanten_sum += start_shanten
                    dealer_start_shanten_count += 1
                else:
                    nondealer_start_shanten_sum += start_shanten
                    nondealer_start_shanten_count += 1
                discarded = set(discarded_tiles)
                for riichi_hand, waits, riichi_fixed_melds in riichi_states:
                    if waits & discarded:
                        counters["riichi_furiten"] += 1
                    multi_wait, good_wait = _wait_shape(
                        _tile_counts(riichi_hand), waits, riichi_fixed_melds)
                    if multi_wait:
                        counters["riichi_multi_wait"] += 1
                    if good_wait:
                        counters["riichi_good_wait"] += 1
            else:
                riichi_states = []
            counters["kyoku_played"] += 1
            if riichi:
                counters["riichi"] += 1
                turn = seat.get("riichi_turn")
                if turn is not None:
                    riichi_turns.append(int(turn) + 1)
                other_turns = [
                    other.get("riichi_turn") for index, other in enumerate(seats)
                    if index != row.seat and other.get("riichi_turn") is not None
                ]
                if not other_turns or int(turn or 0) < min(other_turns):
                    counters["riichi_first"] += 1
                if any(int(other) < int(turn or 0) for other in other_turns):
                    counters["riichi_chase"] += 1
                if any(int(other) > int(turn or 0) for other in other_turns):
                    counters["riichi_chased"] += 1
                delta = (summary.get("deltas") or [0, 0, 0, 0])[row.seat]
                if delta >= 0:
                    riichi_income += delta
                else:
                    riichi_expense += abs(delta)
            if callouts:
                counters["callout_kyoku"] += 1

            if summary.get("end") in ("ryukyoku", "nagashi"):
                counters["ryukyoku"] += 1
                if riichi:
                    counters["riichi_ryukyoku"] += 1
                if callouts:
                    counters["callout_ryukyoku"] += 1

            dealer = int((summary.get("round") or [0])[0]) % 4
            dealer_won = any(
                agari.get("winner") == row.seat
                for agari in summary.get("agari") or []
            )
            if dealer == row.seat and dealer_won:
                dealer_run += 1
                max_dealer_run = max(max_dealer_run, dealer_run)
            else:
                dealer_run = 0

            for agari in summary.get("agari") or []:
                winner = agari.get("winner")
                loser = agari.get("loser")
                if agari.get("tsumo") and winner != row.seat:
                    counters["tsumo_loss_count"] += 1
                    if _is_mangan_or_above(agari):
                        counters["bust_count"] += 1
                        counters["bust_score_total"] += int(agari.get("score") or 0)
                if winner == row.seat:
                    counters["win"] += 1
                    win_points += int(agari.get("score") or 0)
                    if agari.get("tsumo"):
                        counters["tsumo"] += 1
                    names = [str(name) for name in agari.get("yaku") or []]
                    if riichi:
                        counters["riichi_win"] += 1
                        if any("一発" in name or "一发" in name for name in names):
                            counters["ippatsu"] += 1
                    else:
                        counters["silent_win"] += 1
                    if callouts:
                        counters["callout_win"] += 1
                    if riichi and any(
                        _URA_FAN_RE.search(name) and not name.endswith("(0飜)")
                        and not name.endswith("（0飜）") for name in names
                    ):
                        counters["ura_hits"] += 1
                    fan = _fan_value(names, agari.get("fu_han", ""))
                    fan_values.append(fan)
                    if fan >= 13:
                        counters["yakuman"] += 1
                    if fan > counters["max_fan"]:
                        counters["max_fan"] = fan
                    if any("两立直" in name for name in names):
                        counters["double_riichi"] += 1
                    if any("流し満貫" in name or "流し满贯" in name for name in names):
                        counters["nagashi_mangan"] += 1
                    recent_wins.append({
                        **match_context(row),
                        "score": int(agari.get("score") or 0),
                        "fu_han": agari.get("fu_han", ""),
                        "yaku": names,
                        "hand_data": raw,
                        "winner_seat": winner,
                        "loser_seat": loser,
                        "tsumo": bool(agari.get("tsumo")),
                    })
                if not agari.get("tsumo") and loser == row.seat:
                    counters["dealin"] += 1
                    dealin_points += int(agari.get("score") or 0)
                    if riichi:
                        counters["riichi_dealin"] += 1
                    if callouts:
                        counters["callout_dealin"] += 1
                    else:
                        counters["menzen_dealin"] += 1
                    recent_losses.append({
                        **match_context(row),
                        "score": int(agari.get("score") or 0),
                        "fu_han": agari.get("fu_han", ""),
                        "yaku": [str(name) for name in agari.get("yaku") or []],
                        "hand_data": raw,
                        "winner_seat": winner,
                        "loser_seat": loser,
                        "tsumo": False,
                    })
        max_consecutive_dealer = max(max_consecutive_dealer, max_dealer_run)

    games = counters["games"]
    kyoku_played = counters["kyoku_played"] or 1
    riichi_count = counters["riichi"] or 1
    callout_count = counters["callout_kyoku"] or 1
    wins = counters["win"] or 1
    dealins = counters["dealin"] or 1
    ranks = [counters[f"rank_{index}"] for index in range(1, 5)]
    total_hands = counters["win"] + counters["dealin"] + counters["ryukyoku"]
    profile = _empty_profile()
    profile["basic"] = {
        "games": games,
        "kyoku_played": counters["kyoku_played"],
        "rank_counts": ranks,
        "raw_points": counters["raw_points"],
        "pt": counters["pt"],
        "win_rate": counters["win"] / kyoku_played,
        "dealin_rate": counters["dealin"] / kyoku_played,
        "tsumo_rate": counters["tsumo"] / wins,
        "silent_win_rate": counters["silent_win"] / kyoku_played,
        "ryukyoku_rate": counters["ryukyoku"] / kyoku_played,
        "callout_rate": counters["callout_kyoku"] / kyoku_played,
        "riichi_rate": counters["riichi"] / kyoku_played,
        "avg_win_score": win_points / wins,
        "avg_dealin_score": dealin_points / dealins,
        "avg_rank": sum((index + 1) * value for index, value in enumerate(ranks)) / games
        if games else 0,
        "negative_rate": sum(1 for row in rows if (row.final_score or 0) <= 0) / games
        if games else 0,
        "bust_rate": counters["bust_count"] / counters["tsumo_loss_count"]
        if counters["tsumo_loss_count"] else 0,
        "avg_bust_score": counters["bust_score_total"] / counters["bust_count"]
        if counters["bust_count"] else 0,
        "expected_pt": counters["pt"] / games if games else 0,
    }
    profile["riichi"] = {
        "count": counters["riichi"],
        "win_rate": counters["riichi_win"] / riichi_count,
        "dealin_rate": counters["riichi_dealin"] / riichi_count,
        "ryukyoku_rate": counters["riichi_ryukyoku"] / riichi_count,
        "first_rate": counters["riichi_first"] / riichi_count,
        "chase_rate": counters["riichi_chase"] / riichi_count,
        "chased_rate": counters["riichi_chased"] / riichi_count,
        "avg_turn": sum(riichi_turns) / len(riichi_turns) if riichi_turns else None,
        "ippatsu_rate": counters["ippatsu"] / riichi_count,
        "furiten_rate": counters["riichi_furiten"] / riichi_count,
        "multi_wait_rate": counters["riichi_multi_wait"] / riichi_count,
        "good_wait_rate": counters["riichi_good_wait"] / riichi_count,
        "ura_hit_rate": counters["ura_hits"] / counters["riichi_win"]
        if counters["riichi_win"] else 0,
        "ryukyoku": counters["riichi_ryukyoku"],
        "income": riichi_income,
        "expense": riichi_expense,
        "balance": riichi_income - riichi_expense,
    }
    profile["more"] = {
        "highest_score": max((row.final_score or 0 for row in rows), default=0),
        "ura_hit_rate": profile["riichi"]["ura_hit_rate"],
        "dealin_riichi_rate": counters["riichi_dealin"] / dealins,
        "dealin_callout_rate": counters["callout_dealin"] / dealins,
        "post_callout_dealin_rate": counters["callout_dealin"] / callout_count,
        "post_callout_win_rate": counters["callout_win"] / callout_count,
        "post_callout_ryukyoku_rate": counters["callout_ryukyoku"] / callout_count,
        "point_efficiency": win_points / kyoku_played,
        "point_loss": dealin_points / kyoku_played,
        "net_point_efficiency": (win_points - dealin_points) / kyoku_played,
        # 局收支按牌谱屋口径统计为每局平均持点变化，包含自摸支付、棒和流局收支。
        "round_balance": (counters["final_score_total"] - initial_score_total) / kyoku_played,
        "total_kyoku": counters["kyoku_played"],
        "max_level": None,
        "max_raw_points": max((row.final_score or 0 for row in rows), default=0),
        "max_consecutive_dealer": max_consecutive_dealer,
    }
    profile["distribution"] = {
        "win": {
            "riichi": counters["riichi_win"],
            "callout": counters["callout_win"],
            "silent": counters["silent_win"],
        },
        "dealin": {
            "riichi": counters["riichi_dealin"],
            "callout": counters["callout_dealin"],
            "menzen": counters["menzen_dealin"],
        },
    }
    profile["lineage"] = {
        "yakuman": counters["yakuman"],
        "cumulative_yakuman": counters["yakuman"],
        "max_fan": counters["max_fan"],
        "nagashi_mangan": counters["nagashi_mangan"],
        "double_riichi": counters["double_riichi"],
        "start_shanten": start_shanten_sum / start_shanten_count
        if start_shanten_count else None,
        "dealer_start_shanten": dealer_start_shanten_sum / dealer_start_shanten_count
        if dealer_start_shanten_count else None,
        "nondealer_start_shanten": nondealer_start_shanten_sum / nondealer_start_shanten_count
        if nondealer_start_shanten_count else None,
    }
    profile["recent_large"] = sorted(
        recent_wins, key=lambda item: item["score"], reverse=True)[:1]
    profile["recent_losses"] = sorted(
        recent_losses, key=lambda item: item["score"], reverse=True)[:1]
    if by == "player":
        profile["tablemates"] = [
            {
                "player_id": key if isinstance(key, int) else None,
                "nickname": tablemate_names[key],
                "games": count,
                "share": count / games if games else 0,
                "win_rate": tablemate_wins[key] / count if count else 0,
                "self_avg_pt": tablemate_self_pt[key] / count if count else 0,
                "opponent_avg_pt": tablemate_opponent_pt[key] / count if count else 0,
            }
            for key, count in sorted(
                tablemate_games.items(),
                key=lambda item: (-item[1], str(tablemate_names[item[0]]).casefold()),
            )[:20]
        ]
    profile["meta"] = {"total_hands": total_hands, "fan_values": fan_values}
    return profile


def _ura_metrics(summary: dict, seat: int) -> tuple[int, int]:
    riichi_wins = 0
    ura_hits = 0
    seats = summary.get("seats") or []
    for agari in summary.get("agari") or []:
        if agari.get("winner") != seat:
            continue
        if seat >= len(seats) or not seats[seat].get("riichi"):
            continue
        riichi_wins += 1
        if any(
            _URA_FAN_RE.search(str(name))
            and not str(name).endswith("(0飜)")
            and not str(name).endswith("（0飜）")
            for name in agari.get("yaku") or []
        ):
            ura_hits += 1
    return riichi_wins, ura_hits


def _derived_kyoku_metrics(summary: dict, seat: int) -> Counter:
    """从局摘要补齐旧牌谱也能得到的个人统计。"""
    metrics = Counter()
    seats = summary.get("seats") or []
    player = seats[seat] if seat < len(seats) else {}
    riichi = bool(player.get("riichi"))
    callouts = int(player.get("callouts") or 0)
    if summary.get("end") in ("ryukyoku", "nagashi"):
        if riichi:
            metrics["riichi_ryukyoku"] += 1
        if callouts:
            metrics["callout_ryukyoku"] += 1
    for agari in summary.get("agari") or []:
        if agari.get("tsumo") and agari.get("winner") != seat:
            metrics["tsumo_loss_count"] += 1
            if _is_mangan_or_above(agari):
                metrics["bust_count"] += 1
                metrics["bust_score_total"] += int(agari.get("score") or 0)
        if agari.get("winner") == seat:
            if riichi:
                metrics["riichi_wins"] += 1
            else:
                metrics["silent_wins"] += 1
            if callouts:
                metrics["callout_wins"] += 1
        if not agari.get("tsumo") and agari.get("loser") == seat:
            if riichi:
                metrics["riichi_dealin"] += 1
            if callouts:
                metrics["callout_dealin"] += 1
    return metrics


def enriched_game_player_stats(db: Session, rows: list[GamePlayer]) -> list[dict]:
    """合并动态计算的里宝命中数据，兼容旧版已入库 stats。"""
    game_ids = {row.game_uuid for row in rows}
    kyokus = db.query(Kyoku).filter(Kyoku.game_uuid.in_(game_ids)).all() if game_ids else []
    by_game: dict[str, list[Kyoku]] = {}
    for kyoku in kyokus:
        by_game.setdefault(kyoku.game_uuid, []).append(kyoku)
    enriched = []
    for row in rows:
        riichi_wins = 0
        ura_hits = 0
        derived = Counter()
        for kyoku in by_game.get(row.game_uuid, []):
            wins, hits = _ura_metrics(kyoku.summary or {}, row.seat)
            riichi_wins += wins
            ura_hits += hits
            summary = kyoku.summary or {}
            derived.update(_derived_kyoku_metrics(summary, row.seat))
        stats = dict(row.stats or {})
        stats["riichi_wins"] = riichi_wins
        stats["ura_hits"] = ura_hits
        for key in ("silent_wins", "callout_wins", "riichi_dealin",
                    "callout_dealin", "riichi_ryukyoku", "callout_ryukyoku",
                    "tsumo_loss_count", "bust_count", "bust_score_total"):
            stats[key] = derived[key]
        enriched.append(stats)
    return enriched


def aggregate_games(stats_list: list[dict]) -> dict:
    """把多场 game stats 聚合为总量+比率。"""
    total = Counter()
    yaku = Counter()
    rank_counts = [0, 0, 0, 0]
    raw_points = 0.0
    final_score_total = 0
    highest_score = 0
    pt = 0.0
    negative_games = 0
    for s in stats_list:
        for k, v in s.items():
            if k == "yaku":
                yaku.update(v)
            else:
                total[k] += v
        raw_points += s.get("raw_points", 0)
        final_score = s.get("final_score", s.get("raw_points", 0))
        final_score_total += final_score
        highest_score = max(highest_score, final_score)
        pt += s.get("pt", 0.0)
        if final_score <= 0:
            negative_games += 1
        rank_counts[s["rank"] - 1] += 1
    games = len(stats_list)
    kp = total["kyoku_played"] or 1
    return {
        "games": games,
        "rank_counts": rank_counts,
        "avg_rank": round(sum((i + 1) * c for i, c in enumerate(rank_counts)) / games, 3) if games else 0,
        "kyoku_played": total["kyoku_played"],
        "win": total["win"], "tsumo": total["tsumo"], "dealin": total["dealin"],
        "silent_wins": total["silent_wins"],
        "riichi": total["riichi"], "callout_kyoku": total["callout_kyoku"],
        "callout_wins": total["callout_wins"],
        "riichi_dealin": total["riichi_dealin"],
        "callout_dealin": total["callout_dealin"],
        "riichi_ryukyoku": total["riichi_ryukyoku"],
        "callout_ryukyoku": total["callout_ryukyoku"],
        "ryukyoku": total["ryukyoku"],
        "win_score": total["win_score"], "dealin_score": total["dealin_score"],
        "riichi_wins": total["riichi_wins"], "ura_hits": total["ura_hits"],
        "raw_points_sum": round(raw_points, 3),
        "final_score_sum": final_score_total,
        "avg_final_score": round(final_score_total / games, 3) if games else 0,
        "pt_sum": pt,
        "highest_score": highest_score,
        "win_rate": round(total["win"] / kp, 4),
        "silent_win_rate": round(total["silent_wins"] / kp, 4),
        "tsumo_share": round(total["tsumo"] / total["win"], 4) if total["win"] else 0,
        "dealin_rate": round(total["dealin"] / kp, 4),
        "ryukyoku_rate": round(total["ryukyoku"] / kp, 4),
        "riichi_rate": round(total["riichi"] / kp, 4),
        "riichi_win_rate": round(total["riichi_wins"] / total["riichi"], 4)
        if total["riichi"] else 0,
        "callout_win_rate": round(total["callout_wins"] / total["callout_kyoku"], 4)
        if total["callout_kyoku"] else 0,
        "riichi_dealin_rate": round(total["riichi_dealin"] / total["riichi"], 4)
        if total["riichi"] else 0,
        "callout_dealin_rate": round(total["callout_dealin"] / total["callout_kyoku"], 4)
        if total["callout_kyoku"] else 0,
        "riichi_ryukyoku_rate": round(total["riichi_ryukyoku"] / total["riichi"], 4)
        if total["riichi"] else 0,
        "callout_ryukyoku_rate": round(total["callout_ryukyoku"] / total["callout_kyoku"], 4)
        if total["callout_kyoku"] else 0,
        "ura_hit_rate": round(total["ura_hits"] / total["riichi_wins"], 4)
        if total["riichi_wins"] else 0,
        "callout_rate": round(total["callout_kyoku"] / kp, 4),
        "avg_win_score": round(total["win_score"] / total["win"]) if total["win"] else 0,
        "avg_dealin_score": round(total["dealin_score"] / total["dealin"]) if total["dealin"] else 0,
        "expected_pt": round(pt / games, 3) if games else 0,
        "tsumo_loss_count": total["tsumo_loss_count"],
        "busts": total["bust_count"],
        "bust_rate": round(total["bust_count"] / total["tsumo_loss_count"], 4)
        if total["tsumo_loss_count"] else 0,
        "avg_bust_score": round(total["bust_score_total"] / total["bust_count"])
        if total["bust_count"] else 0,
        "negative_games": negative_games,
        "negative_rate": round(negative_games / games, 4) if games else 0,
    }


def _rows_for(db: Session, by: str, target_id: int | None):
    q = (db.query(GamePlayer, Player, Team)
         .join(Player, GamePlayer.player_id == Player.id, isouter=True)
         .join(Team, Player.team_id == Team.id, isouter=True))
    if by == "team":
        if target_id is not None:
            q = q.filter(Player.team_id == target_id)
    elif target_id is not None:
        q = q.filter(GamePlayer.player_id == target_id)
    return q.all()


def _player_rows(db: Session, player_id: int | None):
    q = db.query(GamePlayer)
    if player_id is not None:
        q = q.filter(GamePlayer.player_id == player_id)
    return q.order_by(GamePlayer.game_uuid).all()


def player_stats(db: Session, player_id: int | None) -> dict:
    rows = _player_rows(db, player_id)
    enriched = [dict(stats, rank=gp.rank,
                     raw_points=raw_point_delta(gp.final_score),
                     final_score=gp.final_score, pt=gp.pt)
                for gp, stats in zip(rows, enriched_game_player_stats(db, rows))]
    return aggregate_games(enriched)


def team_stats(db: Session, team_id: int) -> dict:
    rows = _rows_for(db, "team", team_id)
    game_players = [gp for gp, _p, _t in rows]
    stats_by_id = {id(gp): stats for gp, stats in zip(
        game_players, enriched_game_player_stats(db, game_players))}
    enriched = [dict(stats_by_id[id(gp)], rank=gp.rank,
                     raw_points=raw_point_delta(gp.final_score),
                     final_score=gp.final_score, pt=gp.pt)
                for gp, _p, _t in rows]
    return aggregate_games(enriched)


def yaku_stats(db: Session, by: str, target_id: int | None = None) -> dict:
    counter = Counter()
    for gp, _player, _team in _rows_for(db, by, target_id):
        counter.update({yaku_stat_name(name): count
                        for name, count in (gp.stats.get("yaku") or {}).items()
                        if is_countable_yaku(name)})
    return dict(counter.most_common())
