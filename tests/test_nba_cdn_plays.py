"""NBA.com cdn actions -> hoopR-schema rows: ESPN-style types, wall clocks kept, home/away from the score columns."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import nba_cdn_plays as C  # noqa: E402


def _rows():
    r = lambda n, a, s, q, h, aw, d, t, c="PT11M50.00S": dict(  # noqa: E731
        actionNumber=n, orderNumber=n * 10, clock=c, timeActual=f"2024-11-13T00:1{n}:00.500Z", period=1, actionType=a,
        subType=s, qualifiers=q, personId=1, scoreHome=h, scoreAway=aw, description=d, teamTricode=t, gameId=22400001)
    return pd.DataFrame([
        r(1, "period", "start", "", 0, 0, "Period Start", None),
        r(2, "2pt", "Jump Shot", "", 2, 0, "Capela 2' Jump Shot", "ATL"),
        r(3, "block", None, "", 2, 0, "Horford BLOCK", "BOS"),
        r(4, "3pt", "Jump Shot", "", 2, 3, "Tatum 26' 3PT", "BOS"),
        r(5, "timeout", "full", "team, mandatory", 2, 3, "ATL Timeout", "ATL"),
        r(6, "rebound", "offensive", "", 2, 3, "TEAM offensive REBOUND", "ATL"),
        r(7, "freethrow", "1 of 2", "", 3, 3, "Young Free Throw 1 of 2", "ATL", "PT00M42.30S"),
        r(8, "timeout", "challenge", "", 3, 3, "BOS Challenge", "BOS"),
        r(9, "period", "end", "", 3, 3, "Period End", None, "PT00M00.00S"),
    ])


def test_convert_types_scores_and_clocks():
    out = C.convert(_rows())
    assert out.type_text.tolist() == ["Jump Shot", "Jump Shot", "Official Timeout", "Offensive Rebound",
                                      "Free Throw - 1 of 2", "Coach's Challenge", "End Period"]
    assert (out.home_team_abbrev == "ATL").all() and (out.away_team_abbrev == "BOS").all()
    assert out.score_value.tolist() == [2, 3, 0, 0, 1, 0, 0] and out.scoring_play.tolist()[:2] == [True, True]
    assert out.text.iloc[3] == "Team Rebound" and out.clock_display_value.iloc[4] == "42.3"
    assert out.clock_display_value.iloc[0] == "11:50" and out.wallclock.iloc[0] == "2024-11-13T00:12:00Z"


def test_tokenizes_with_stoppages(tmp_path):
    import noggin_tokens as T
    p = tmp_path / "cdn_2024.parquet"
    rows = _rows()
    many = pd.concat([rows.iloc[:-1]] * 3 + [rows.iloc[-1:]], ignore_index=True)     # the tokenizer needs >= 10 plays
    many["actionNumber"] = range(1, len(many) + 1)
    many["orderNumber"] = many.actionNumber * 10
    C.convert(many).to_parquet(p, index=False)
    info = T.build("nba", p, 2025, tmp_path / "tok")
    assert info["games"] == 1 and info["source"].startswith("nba-cdn") and info["coverage"]["no_wall_games"] == 0
