"""WP-24: the hold-set measurement with the real standby.Planner and on public seasons.

Why: the brain has to beat what the app does today, not closest-score only. These tests pin that the Planner column
ranks with the app's own code, that public rows go through the factory's night rule and eval cut, and that folds
only ever fit on older nights.
"""
import json

import pandas as pd
import pytest

from redzone.models import GameState, League, Phase, Situation, Team
from scripts import holdset, holdset_public

T0 = 1_700_000_000.0   # 2023-11-14 22:13 UTC


class FakeReplay:
    """Two NBA games' worth of fixed states: wall times every 60 s, one state per play."""

    def __init__(self, gid, home, away, hs, as_, period, clock):
        self.gid, self.league = gid, League.NBA
        self.wall = [T0 + 60 * i for i in range(300)]
        self._g = GameState(league=League.NBA, game_id=gid, home=Team(home, score=hs), away=Team(away, score=as_),
                            phase=Phase.LIVE, period=period, clock_s=clock, situation=Situation())

    def state_at(self, i):
        return self._g


def night_rows(*gids):
    return [(g, T0, T0 + 3 * 3600, [], []) for g in gids]


def test_planner_ranker_puts_the_close_late_game_above_a_blowout(monkeypatch):
    reps = {"close": FakeReplay("close", "BOS", "NYK", 100, 99, 4, 60.0),
            "blow": FakeReplay("blow", "LAL", "DEN", 120, 80, 4, 60.0)}
    monkeypatch.setattr(holdset, "games", lambda l, only=None: [("20231114", g, {}) for g in reps])
    monkeypatch.setattr(holdset_public, "load_replay", lambda lg, gid, d: reps[gid])
    rows = night_rows("close", "blow")
    ranks = holdset_public.PlannerRanker(None, poll_s=60)("nba", "20231114", rows)
    times = holdset.decision_times(rows)
    assert times and set(ranks) == set(times)
    r = ranks[times[0]]
    assert r["close"] > r.get("blow", float("-inf"))      # the real options() ranking, best first


def test_planner_scores_ride_in_the_sixth_field_and_missing_games_rank_last():
    night = [("a", 0.0, 4000.0, [(0.0, 1, 3, 0, None), (3000.0, 1, 3, 0, None)], []),
             ("b", 0.0, 4000.0, [(0.0, 1, 3, 0, None), (3000.0, 1, 3, 0, None)], [])]
    t = holdset.decision_times(night)[0]
    got = holdset.decisions("nba", night, {t: {"a": -0.0}})
    row = {x[0]: x for x in got[0]}
    assert len(row["a"]) == 6 and row["a"][5] == 0.0 and row["b"][5] == float("-inf")
    assert all(len(x) == 5 for x in holdset.decisions("nba", night)[0])   # no ranks: unchanged shape


def test_report_adds_the_planner_column_only_when_a_ranker_is_given(monkeypatch):
    rows = {f"2023110{n}": [(f"g{n}{i}", T0, T0 + 3 * 3600, [(T0 + 60 * j, 4, 60.0, 2, None) for j in range(200)],
                              [(T0 + 400 + 300 * i, 1)]) for i in range(3)] for n in range(1, 6)}
    plain = holdset.report("nba", rows)
    assert plain["K2"]["planner_pct"] is None
    got = holdset.report("nba", rows, lambda l, n, nr: {t: {g[0]: -float(i) for i, g in enumerate(nr)}
                                                       for t in holdset.decision_times(nr)})
    assert got["K2"]["planner_pct"] is not None and got["K2"]["nights"] >= 1
    assert "real Planner" in holdset.table({"nba": got}) and "n/a" in holdset.table({"nba": plain})


def test_forward_folds_fit_only_on_older_nights():
    nights = [f"2023{m:02d}01" for m in range(1, 11)]
    folds = holdset_public.forward_folds(nights, 4)
    assert len(folds) == 4
    for fit_n, test_n in folds:
        assert fit_n and test_n and max(fit_n) < min(test_n)
    assert [n for f in folds for n in f[1]] == sorted(nights)[len(folds[0][0]):]    # tests tile the later nights once


def _hoops_df(start, gid="g1", n=50):
    rows = []
    for i in range(n):
        w = pd.Timestamp(start + 100 * i, unit="s", tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
        rows.append(dict(id=f"{gid}{i}", sequence_number=i, type_text="Jump Shot", text="x", period_number=1 + i // 12,
                         clock_display_value="5:00", wallclock=w, scoring_play=(i % 3 == 0), score_value=2,
                         home_score=i, away_score=i, game_id=gid, shooting_play=True, season=2024, season_type=2))
    return pd.DataFrame(rows)


def test_public_rows_apply_the_night_rule_and_the_eval_cut():
    ok = _hoops_df(1_700_000_000.0, "old")                  # Nov 2023
    locked = _hoops_df(1_794_000_000.0, "lock")             # Nov 2026: on or after EVAL_FIRST_NIGHT["nba"]
    rows = holdset_public.public_rows("nba", pd.concat([ok, locked], ignore_index=True))
    assert [g[0] for v in rows.values() for g in v] == ["old"]
    assert list(rows) == ["20231114"]


def test_nfl_summary_reads_back_through_the_football_detector():
    base = dict(game_id="2024_01_A_B", desc="x", timeout=0, play_type="pass", field_goal_result=None,
                extra_point_result=None, interception=0, fumble_lost=0, sack=0, complete_pass=1, safety=0, touchdown=0,
                two_point_attempt=0, sp=0, total_home_score=0, total_away_score=0, qtr=1, time="10:00", drive=1,
                down=1, ydstogo=10, yardline_100=18.0, yards_gained=5)
    rows = []
    for i in range(50):
        r = dict(base, play_id=i + 1, time_of_day=pd.Timestamp(T0 + 100 * i, unit="s", tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
                 drive=1 + i // 8)
        if i == 20:
            r.update(touchdown=1, sp=1, total_home_score=7)
        rows.append(r)
    d = holdset_public.nfl_summary(pd.DataFrame(rows))
    E = []
    gid = holdset.football("nfl", "g", d, E, {})
    assert gid and any(e[3] == "td" for e in E) and any(e[3] == "rz_trip" for e in E)
    assert not any(e[3] == "wp5" for e in E)                  # the published WP column is never read (leakage)


def test_archive_mode_refuses_locked_leagues(tmp_path, capsys):
    out = tmp_path / "o.json"
    holdset_public.main(["archive", "--archive", str(tmp_path), "--out", str(out), "--leagues", "nfl"])
    assert "REFUSED" in capsys.readouterr().out and json.loads(out.read_text()) == {}


def test_hold_decisions_unchanged_shape_for_existing_callers():
    dec = [[("a", (1, 0, 0, 0), 1.0, 5.0, 0), ("b", (1, 0, 1, 0), 2.0, 0.0, 0), ("c", (1, 0, 2, 0), 3.0, 1.0, 0)]]
    assert holdset.capture(dec, lambda x: x[3], 2)[:2] == (6.0, 6.0)
    with pytest.raises(IndexError):
        holdset.capture(dec, lambda x: x[5], 1)


def test_public_report_gives_mean_and_worst_fold():
    rows = {f"2023{m:02d}{d:02d}": [(f"g{m}{d}{i}", T0, T0 + 3 * 3600, [(T0 + 60 * j, 4, 60.0, 2 + i, None) for j in range(200)],
                                      [(T0 + 400 + 300 * i, 1)]) for i in range(4)] for m in (1, 2) for d in range(1, 6)}
    res = holdset_public.public_report("nba", rows, folds=3)
    assert len(res["folds"]) == 3 and res["K2"]["folds"] == 3
    assert res["K2"]["worst_fold_table_pct"] <= res["K2"]["state_table_pct"] + 1e-9
    assert res["K2"]["planner_pct"] is None
