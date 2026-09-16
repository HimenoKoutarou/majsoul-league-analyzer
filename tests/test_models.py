from app.models import Game, GamePlayer, Kyoku, League, Player, SyncRun, Team


def test_models_create_and_query(db):
    league = League(name="测试联赛")
    db.add(league)
    db.flush()
    assert league.score_rule["rank_points"] == [90, 45, 0, -45]

    team = Team(name="A队", short_name="A", color="#ff0000", sort_order=1)
    db.add(team)
    db.flush()

    player = Player(nickname="测试玩家", account_id=12345, team_id=team.id, contest_registered=True)
    db.add(player)
    db.flush()
    assert player.id is not None

    game = Game(uuid="260915-abc", start_time=None, mode={"disp": "四麻 半庄"},
                raw_head={"name": ["a", "b", "c", "d"]}, fetched_via="ninklang")
    db.add(game)
    db.flush()

    gp = GamePlayer(game_uuid=game.uuid, seat=0, player_id=player.id, nickname="测试玩家",
                    final_score=30000, rank=1, pt=46.2,
                    stats={"kyoku_played": 9, "win": 1})
    db.add(gp)
    db.add(Kyoku(game_uuid=game.uuid, index=0, round_data=[0, 0, 0],
                 data=[[]], summary={"end": "ryukyoku"}))
    db.add(SyncRun(channel="dhs", status="success", games_added=1, errors=[]))
    db.commit()

    assert db.query(GamePlayer).count() == 1
    assert db.query(Kyoku).one().summary["end"] == "ryukyoku"
