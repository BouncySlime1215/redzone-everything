"""NFM event tokens (scripts/noggin_tokens.py): fixed layout, stable ids, uint16, eval window never tokenized."""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import noggin_tokens as NT  # noqa: E402


def _plays():
    return [{"id": "1", "type": {"text": "Jump Shot"}, "period": {"number": 1}, "clock": {"displayValue": "11:30"},
             "wallclock": "2025-01-05T00:10:00Z", "scoringPlay": True, "scoreValue": 2, "homeScore": 2, "awayScore": 0},
            {"id": "2", "type": {"text": "Timeout"}, "period": {"number": 1}, "clock": {"displayValue": "11:10"},
             "wallclock": "2025-01-05T00:10:30Z", "homeScore": 2, "awayScore": 0},
            {"id": "3", "type": {"text": "Three Point Jumper"}, "period": {"number": 4}, "clock": {"displayValue": "0:05"},
             "wallclock": "2025-01-05T02:20:00Z", "scoringPlay": True, "scoreValue": 3, "homeScore": 2, "awayScore": 3}]


def test_layout_and_ranges():
    t = NT.encode_game("nba", _plays(), "WSH", "BOS")
    assert t.dtype == np.uint16 and NT.VOCAB_SIZE < 65536
    assert t[0] == NT.BOS0 + NT.LEAGUES.index("nba") and t[-1] == NT.EOS
    assert len(t) == 3 + 3 * NT.TOKENS_PER_EVENT + 1
    ev = t[3:-1].reshape(3, NT.TOKENS_PER_EVENT)
    assert (ev[:, 0] >= NT.KIND0).all() and (ev[:, 0] < NT.DT0).all()
    assert ev[0, 1] == NT.dt_token(None) and ev[1, 1] == NT.dt_token(30.0)       # first event: no delta
    assert ev[1, 5] == NT.STOP0 + NT.STOPS.index("timeout")
    assert ev[2, 3] == NT.MARGIN0 + NT.MARGIN_CAP - 1                            # home trails by 1
    assert ev[2, 4] == NT.PTS0 + 8 + 3 and ev[0, 4] == NT.PTS0 + 2               # away 3, home 2
    assert ev[2, 2] // NT.CLOCK_BUCKETS == (NT.CLOCK0 // NT.CLOCK_BUCKETS) + 3 or ev[2, 2] >= NT.CLOCK0 + 3 * NT.CLOCK_BUCKETS
    assert (t < NT.VOCAB_SIZE).all()
    assert np.array_equal(t, NT.encode_game("nba", _plays(), "WSH", "BOS"))      # stable (crc32, not hash())


def test_teams_and_leagues_are_distinct_tokens():
    a = NT.encode_game("nba", _plays(), "WSH", "BOS")
    b = NT.encode_game("cbb", _plays(), "UNC", "DUKE")
    assert a[0] != b[0] and a[1] != b[1]


def test_coverage_tags_for_the_ledger():
    t = NT.encode_game("nba", _plays(), "WSH", "BOS")
    tg = NT.game_tags("nba", t)
    assert tg["events"] == 3 and tg["crunch_events"] == 1          # Q4 0:05, within 1
    assert not tg["ot"] and not tg["blowout"] and not tg["comeback"]


def _old_nfl(tmp_path, date):
    """A 1999-style nflverse season: no time_of_day at all, only game_date."""
    import pandas as pd
    rows = []
    for i, (q, t, h, a) in enumerate([(1, "15:00", 0, 0), (1, "10:00", 7, 0), (2, "05:00", 7, 3), (4, "01:00", 14, 3)]
                                     * 4):
        rows.append(dict(game_id="1999_01_WAS_DAL", play_id=i + 1, time_of_day=None, time=t, qtr=q, play_type="run",
                         desc="run", timeout=0, touchdown=0, field_goal_result=None, extra_point_result=None,
                         interception=0, fumble_lost=0, sack=0, complete_pass=0, safety=0, sp=0, total_home_score=h,
                         total_away_score=a, season_type="REG", two_point_attempt=0, home_team="WAS",
                         away_team="DAL", game_date=date))
    p = tmp_path / "pbp_1999.parquet"
    pd.DataFrame(rows).to_parquet(p)
    return p


def test_seasons_without_wall_clocks_are_dated_by_game_date(tmp_path):
    info = NT.build("nfl", _old_nfl(tmp_path, "1999-09-12"), 1999, tmp_path / "tok")
    assert info["games"] == 1 and info["index"][0][1] == "19990912"
    assert info["coverage"]["no_wall_games"] == 1
    t = np.load(tmp_path / "tok" / "nfl" / "1999.npy")
    assert (t[3:-1].reshape(-1, NT.TOKENS_PER_EVENT)[1:, 1] != NT.UNK_DT).all()     # DT from the game clock


def test_game_date_fallback_still_respects_the_eval_cut(tmp_path):
    info = NT.build("nfl", _old_nfl(tmp_path, "2025-12-01"), 1999, tmp_path / "tok")
    assert info["games"] == 0
