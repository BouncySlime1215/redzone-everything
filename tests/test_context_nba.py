"""NBA context (worker B4): stakes / rest / travel / L2M leak-safety, crosswalk, TV, injury + referee parsers."""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from redzone.context import nba as N
from redzone.context import standings as S
from redzone.context.tokens import PERSONAL_USE, to_frame

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "context"))
import nba_injuries as I  # noqa: E402

# 12 real NBA teams: 6 per conference, so top-6 = everyone, keep the toy target smaller via extra/top targets
TEAMS = ["BOS", "NY", "MIA", "CHI", "CLE", "ATL", "LAL", "GS", "DEN", "DAL", "PHX", "SA"]


def toy_cfg():
    return S.LeagueConfig("nba", S.nba_divisions, lambda s: (0, 2), S.NBA_LICENSE, sims=200, elo_k=10.0,
                          elo_hfa=80.0, extra_targets=lambda s: {"top10": (0, 4)})


def toy_games(flip_from: int | None = None, move_from: int | None = None) -> pd.DataFrame:
    rows, t0, i = [], pd.Timestamp("2024-10-22 23:30", tz="UTC"), 0
    for rnd in range(6):
        for j in range(0, 12, 2):
            h, a = TEAMS[(j + rnd) % 12], TEAMS[(j + 1 + 3 * rnd) % 12]
            if h == a:
                continue
            k = t0 + pd.Timedelta(days=rnd * 2 + (j % 4) // 2)
            hs, as_ = (110, 100) if i % 3 else (95, 104)
            if flip_from is not None and i >= flip_from:
                hs, as_ = as_ + 5, hs
            city = "Los Angeles"
            if move_from is not None and i >= move_from:
                city, k = "London", k + pd.Timedelta(hours=1)
            rows.append({"game_id": f"g{i:03d}", "season": 2024, "game_type": "REG", "kickoff_utc": k,
                         "final_utc": k + pd.Timedelta(hours=3), "home": h, "away": a, "home_score": hs,
                         "away_score": as_, "neutral": False, "venue_city": city if i % 2 else None})
            i += 1
    return pd.DataFrame(rows)


def stakes(games):
    return to_frame(S.compute_season(games, toy_cfg(), S.Elo(toy_cfg()), seed=3))


def test_nba_config_formats():
    assert S.nba_playoff(2024) == (0, 6) and S.nba_extra(2024) == {"top10": (0, 10)}
    assert S.nba_playoff(2016) == (0, 8) and S.nba_extra(2016) == {}
    assert S.nba_playoff(2010) == (1, 7)
    d = S.nba_divisions(2010)
    assert len({t for t in S.NBA_TEAMS if t not in ("NJ", "SEA")}) == 30
    assert d["NJ"] == d["BKN"] and d["SEA"] == d["OKC"]
    assert PERSONAL_USE in S.NBA_LICENSE


def test_stakes_no_result_from_game_or_later_leaks():
    games = toy_games()
    base = stakes(games)
    assert {"top10_odds", "top10_odds_swing", "playin_odds", "playoff_odds_swing"} <= set(base.field)
    for g in (5, 17, 30):
        alt = stakes(toy_games(flip_from=g))
        for gid in games.game_id[:g + 1]:
            a = base[base.game_id == gid].reset_index(drop=True)
            b = alt[alt.game_id == gid].reset_index(drop=True)
            pd.testing.assert_frame_equal(a, b)
    asof = pd.to_datetime(base["asof_utc"], utc=True)
    assert (asof < base["game_id"].map(games.set_index("game_id")["kickoff_utc"])).all()


def _venues():
    return N.load_venues()


def test_rest_travel_uses_only_earlier_games():
    v = _venues()
    games = toy_games()
    base = to_frame(N.rest_travel_facts(games, v))
    assert set(base.kind) == {"derived"} and base.license.str.contains(PERSONAL_USE).all()
    for g in (6, 20):
        alt = to_frame(N.rest_travel_facts(toy_games(move_from=g), v))
        for gid in games.game_id[:g]:
            a = base[base.game_id == gid].sort_values(["team", "field"]).reset_index(drop=True)
            b = alt[alt.game_id == gid].sort_values(["team", "field"]).reset_index(drop=True)
            pd.testing.assert_frame_equal(a, b)
    asof = pd.to_datetime(base["asof_utc"], utc=True)
    assert (asof < base["game_id"].map(games.set_index("game_id")["kickoff_utc"])).all()


def test_back_to_back_travel_and_tz():
    k = pd.Timestamp("2025-01-10 00:30", tz="UTC")          # Jan 9, 7:30 PM ET
    g = pd.DataFrame([
        {"game_id": "a", "season": 2024, "kickoff_utc": k, "home": "BOS", "away": "NY", "neutral": False,
         "venue_city": "Boston"},
        {"game_id": "b", "season": 2024, "kickoff_utc": k + pd.Timedelta(hours=27), "home": "LAL", "away": "BOS",
         "neutral": False, "venue_city": "Los Angeles"},
        {"game_id": "c", "season": 2024, "kickoff_utc": k + pd.Timedelta(days=3, hours=3), "home": "GS", "away": "BOS",
         "neutral": False, "venue_city": "San Francisco"},
    ])
    f = to_frame(N.rest_travel_facts(g, _venues()))
    bos = f[f.team == "BOS"].pivot_table(index="game_id", columns="field", values="value")
    assert bos.loc["b", "back_to_back"] == 1 and bos.loc["b", "rest_days"] == 0
    assert 4100 < bos.loc["b", "travel_km"] < 4250 and bos.loc["b", "tz_shift_h"] == -3
    assert bos.loc["c", "road_trip_game"] == 2 and bos.loc["c", "games_in_last_7"] == 2
    assert bos.loc["c", "rest_days"] == 1
    assert "rest_days" not in bos.columns or pd.isna(bos.loc["a"].get("rest_days"))


def test_crosswalk_by_pair_and_time():
    g = pd.DataFrame({"home": ["GS", "NY"], "away": ["BKN", "BOS"],
                      "kickoff_utc": pd.to_datetime(["2020-12-23T00:00Z", "2020-12-24T00:00Z"], utc=True)})
    cdn = pd.DataFrame({"gameId": [22000001, 22000009], "t0": ["2020-12-23T00:06:19Z", "2020-12-30T00:06Z"],
                        "teams": ["BKN|GSW", "BOS|NYK"]})
    x = N.crosswalk(g, cdn)
    assert x.tolist() == ["0022000001", ""]


def test_l2m_prior_only_and_postgame_asof():
    k = pd.to_datetime(["2019-01-01T00:30Z", "2019-01-03T00:30Z", "2019-01-05T00:30Z"], utc=True)
    games = pd.DataFrame({"game_id": ["e1", "e2", "e3"], "home": ["BOS", "NY", "MIA"], "away": ["NY", "MIA", "BOS"],
                          "kickoff_utc": k, "final_utc": k + pd.Timedelta(hours=3), "nba_game_id": ""})
    l2m = pd.DataFrame({"nba_game_id": ["0021800001", "0021800002", "0021800003"],
                        "date": pd.to_datetime(["2018-12-31", "2019-01-02", "2019-01-04"]),
                        "n_graded": [10, 12, 8], "n_incorrect": [3, 1, 0], "n_ic": [1, 0, 0], "n_inc": [2, 1, 0],
                        "o1": ["11", "11", "33"], "o2": ["22", "44", "22"], "o3": ["55", "66", "77"], "o4": [None] * 3,
                        "crew_chief": ["A", "A", "C"], "home_tri": ["BOS", "NYK", "MIA"],
                        "away_tri": ["NYK", "MIA", "BOS"]})
    f = to_frame(N.l2m_facts(games, l2m))
    r = f[f.field == "l2m_ref_incorrect_rate_prior"].set_index("game_id")["value"]
    assert "e1" not in r.index and r["e2"] == 3.0 and r["e3"] == 3.0     # e3: official 22 from e1 only
    post = f[f.field.str.endswith("_postgame")]
    assert (pd.to_datetime(post.asof_utc, utc=True) > post.game_id.map(games.set_index("game_id")["final_utc"])).all()
    pre = f[~f.field.str.endswith("_postgame")]
    assert (pd.to_datetime(pre.asof_utc, utc=True) < pre.game_id.map(games.set_index("game_id")["kickoff_utc"])).all()
    assert set(f[f.field == "ref_crew_chief_id"].value_str) == {"ref_11", "ref_33"}


def test_national_tv_network():
    assert N.nba_network("NBC/Peacock") == "NBC" and N.nba_network("ABC/ESPN") == "ABC"
    assert N.nba_network("ESPN/ESPN2") == "ESPN" and N.nba_network("TNT/truTV") == "TNT"
    assert N.nba_network("Prime Video") == "Prime Video" and N.nba_network("NBA TV") == "NBA TV"
    g = pd.DataFrame({"kickoff_utc": pd.to_datetime(["2025-01-10T00:30Z"] * 3, utc=True), "home": ["BOS", "TOR", "MIA"],
                      "away": ["NY", "BOS", "ATL"], "broadcast": ["ESPN", "TSN", ""], "broadcast_market": ["", "", ""],
                      "broadcast_name": ["ESPN", "TSN", "FanDuel SN SUN"], "game_id": ["1", "2", "3"],
                      "season": 2024, "game_type": "REG"})
    tv = N.national_tv(g)
    assert tv.national.tolist() == [True, False, False] and tv.network.tolist() == ["ESPN", "", ""]
    assert tv.broadcast_known.all()


# Layout written from the known PDF format (Game Date | Game Time | Matchup | Team | Player Name | Current Status |
# Reason), as pdfplumber prints it. NEEDS LIVE VERIFICATION against a real report.
INJ_FIXTURE = """Injury Report: 01/09/25 05:30 PM
Game Date Game Time Matchup Team Player Name Current Status Reason
01/09/2025 07:30 (ET) NYK@BOS Boston Celtics Tatum, Jayson Out Injury/Illness - Left Ankle; Sprain
Holiday, Jrue Questionable Injury/Illness - Right Hamstring; Strain
New York Knicks Brunson, Jalen Doubtful Injury/Illness - Right Calf; Strain
Hart, Josh Probable Injury/Illness - Left Knee; Soreness
Jackson Jr., Jaren Available Injury/Illness - Left Knee
10:00 (ET) BKN@LAC LA Clippers NOT YET SUBMITTED
Brooklyn Nets Thomas, Cam Out G League - Two-Way
Page 1 of 2
"""


def test_parse_injury_report_fixture():
    rows = I.parse_report(INJ_FIXTURE)
    by = {(r["team"], r["player"]): r for r in rows}
    assert by[("BOS", "Tatum, Jayson")]["status"] == "Out"
    assert by[("BOS", "Holiday, Jrue")]["status"] == "Questionable"
    assert by[("NY", "Brunson, Jalen")]["status"] == "Doubtful" and by[("NY", "Brunson, Jalen")]["home"] == "BOS"
    assert by[("NY", "Jackson Jr., Jaren")]["status"] == "Available"
    assert by[("LAC", None)]["status"] == "NotSubmitted"
    assert by[("BKN", "Thomas, Cam")]["home"] == "LAC" and by[("BKN", "Thomas, Cam")]["time_et"] == "10:00"
    assert I.norm_player("Jackson Jr., Jaren") == "jaren jackson"


def test_injury_facts_only_reports_before_tip():
    games = pd.DataFrame({"game_id": ["401"], "home": ["BOS"], "away": ["NY"],
                          "kickoff_utc": pd.to_datetime(["2025-01-10T00:30Z"], utc=True)})
    rows = I.parse_report(INJ_FIXTURE)
    early = datetime(2025, 1, 9, 17, 30, tzinfo=I.ET)
    late = datetime(2025, 1, 9, 19, 15, tzinfo=I.ET)        # 15 min before tip: must be ignored
    late_rows = [dict(r, status="Out") for r in rows]
    pb = pd.DataFrame({"team": ["BOS", "BOS", "BOS"], "player": ["jayson tatum", "jaylen brown", "jrue holiday"],
                       "minutes": [36, 35, 30], "kick": pd.to_datetime(["2025-01-07T00:30Z"] * 3, utc=True)})
    f = to_frame(I.injury_facts([(early, rows), (late, late_rows)], games, pb))
    bos = f[f.team == "BOS"].set_index("field")["value"]
    assert bos["inj_out_n"] == 1 and bos["inj_questionable_n"] == 1 and bos["inj_star_out"] == 1
    assert (pd.to_datetime(f.asof_utc, utc=True) <= games.kickoff_utc[0] - pd.Timedelta(minutes=30)).all()
    assert set(f.kind) == {"new"} and f.license.str.contains(PERSONAL_USE).all()


def test_injury_slots_and_names():
    t = datetime(2025, 1, 9, 17, 30, tzinfo=I.ET)
    assert I.slot_names(t) == ["05_30PM"]
    assert I.slot_names(t.replace(minute=0)) == ["05_00PM", "05PM"]
    s = I.report_slots(t.date())
    assert s[0] > s[-1] and len(s) == 52


REFS_FIXTURE = """<table><tr><th>Game</th><th>Crew Chief</th><th>Referee</th><th>Umpire</th><th>Alternate</th></tr>
<tr><td>New York @ Boston</td><td>Scott Foster (#48)</td><td>Ben Taylor (#46)</td><td>Jenna Reneau (#93)</td>
<td></td></tr><tr><td>Brooklyn @ LA Clippers</td><td>Zach Zarba (#15)</td><td>A B (#1)</td><td>C D (#2)</td></tr>
</table>"""


def test_parse_refs_fixture():
    a = I.parse_refs(REFS_FIXTURE)
    assert a[0]["home"] == "BOS" and a[0]["officials"][0] == ("Scott Foster", 48) and a[1]["home"] == "LAC"
    games = pd.DataFrame({"game_id": ["9"], "home": ["BOS"], "away": ["NY"],
                          "kickoff_utc": pd.to_datetime(["2025-01-10T00:30Z"], utc=True)})
    f = to_frame(I.ref_facts(a, games, "2025-01-09T15:00:00Z", {"scott foster": "1234"}))
    v = f.set_index("field")["value_str"]
    assert v["ref_crew_chief_id"] == "ref_1234" and v["ref_2_id"] == "refname_ben_taylor"
    assert I.ref_facts(a, games, "2025-01-10T01:00:00Z", {}) == []    # after tip: nothing
