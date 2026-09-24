import pytest

from app.services.majsoul.rules import contest_rule_to_score_rule


def test_contest_rule_converts_uma_to_four_rank_points():
    rule = contest_rule_to_score_rule({
        "game_rule_setting": {
            "detail_rule_v2": {
                "game_rule": {
                    "init_point": 25000,
                    "jingsuanyuandian": 1000,
                    "shunweima_2": 30,
                    "shunweima_3": 0,
                    "shunweima_4": -30,
                }
            }
        }
    })
    assert rule["rank_points"] == [0, 30, 0, -30]
    assert rule["source"] == "majsoul_contest"
    assert rule["init_point"] == 25000
    assert rule["jingsuanyuandian"] == 1000


def test_contest_rule_accepts_direct_rank_points():
    rule = contest_rule_to_score_rule({"rank_points": [90, 45, 0, -45]})
    assert rule["rank_points"] == [90, 45, 0, -45]


def test_contest_rule_requires_complete_scoring_rule():
    with pytest.raises(ValueError, match="没有完整的顺位分规则"):
        contest_rule_to_score_rule({"game_rule_setting": {}})
