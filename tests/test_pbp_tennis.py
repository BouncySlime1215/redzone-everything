"""Tennis slam point-by-point converter (scripts/pbp/tennis.py).

Fixture tests/fixtures/pbp/tennis_min_{matches,points}.csv is SYNTHETIC in the tennis_slam_pointbypoint layout
(the GitHub repo returned 404 from the build box on 2026-11-05): one men's (1101) and one women's (2101) match,
3 sets each, with "0X" set-marker rows.
"""
import collections
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from pbp import schema, tennis  # noqa: E402

FIX = Path(__file__).parent / "fixtures" / "pbp"
MEN, WOMEN = "2019-wimbledon-1101", "2019-wimbledon-2101"


def _csv(name):
    return pd.read_csv(FIX / name, dtype=str, keep_default_na=False).to_dict("records")


@pytest.fixture(scope="module")
def games():
    matches = {m["match_id"]: m for m in _csv("tennis_min_matches.csv")}
    pts = _csv("tennis_min_points.csv")
    return {mid: (tennis.convert_match(m, [p for p in pts if p["match_id"] == mid]),
                  [p for p in pts if p["match_id"] == mid]) for mid, m in matches.items()}


def test_row_counts_and_markers_skipped(games):
    for mid, (rows, raw) in games.items():
        real = [p for p in raw if p["PointNumber"].isdigit() and int(p["PointNumber"]) > 0]
        assert len(rows) == len(real) < len(raw)       # the 0X markers are dropped
        assert [r["seq"] for r in rows] == list(range(len(rows)))
    assert len(games[MEN][0]) == 194 and len(games[WOMEN][0]) == 173


def test_league_season_night_players(games):
    r = games[MEN][0][0]
    assert (r["league"], r["season"], r["night"], r["level"], r["sport"]) == \
        ("wimbledon_men", 2019, "20190701", "pro", "tennis")
    assert (r["home"], r["away"]) == ("novak_djokovic", "philipp_kohlschreiber")
    assert games[WOMEN][0][0]["league"] == "wimbledon_women"
    assert tennis.gender("2019-usopen-2213") == "women" and tennis.gender("x", "1999") == "men"
    assert tennis.night_of(2020, "frenchopen") == "20200927" and tennis.night_of(2031, "usopen") == "20310828"


def test_sets_elapsed_per_set(games):
    rows, raw = games[MEN]
    assert sorted({r["period"] for r in rows}) == [1, 2, 3]
    for s in (1, 2, 3):
        sr = [r for r in rows if r["period"] == s]
        assert sr[0]["elapsed_s"] == 0.0
        el = [r["elapsed_s"] for r in sr]
        assert el == sorted(el)
        pr = [p for p in raw if p["SetNo"] == str(s) and p["PointNumber"].isdigit()]
        assert el[-1] == tennis._secs(pr[-1]["ElapsedTime"]) - tennis._secs(pr[0]["ElapsedTime"])
    assert all(r["clock_s"] is None and r["wall_ts"] is None for r in rows)


def test_scores_are_games_in_set(games):
    rows, raw = games[MEN]
    real = [p for p in raw if p["PointNumber"].isdigit()]
    for r, p in zip(rows, real):
        # our GameWinner count agrees with the source's P*GamesWon after the point
        assert (r["home_score"], r["away_score"]) == (int(p["P1GamesWon"]), int(p["P2GamesWon"]))
    ends = [(r["period"], r["home_score"], r["away_score"]) for r in rows if r["stop"] == "end_period"]
    assert ends == [(1, 6, 4), (2, 6, 7), (3, 3, 6)]


def test_stop_and_kind_labels(games):
    rows, raw = games[MEN]
    c = collections.Counter(r["stop"] for r in rows)
    assert c["end_period"] == 3 and c["score_break"] == 15 and set(c) == {"none", "score_break", "end_period"}
    for r in rows:
        if r["stop"] == "score_break":
            assert (r["home_score"] + r["away_score"]) % 2 == 1
    real = [p for p in raw if p["PointNumber"].isdigit()]
    for r, p in zip(rows, real):
        w = int(p["PointWinner"])
        lo = 3 - w
        if p[f"P{w}Ace"] == "1":
            assert r["kind"] == "ace"
        elif p[f"P{lo}DoubleFault"] == "1":
            assert r["kind"] == "double_fault"
        assert r["side"] == r["possession"] == ("home" if p["PointServer"] == "1" else "away")
        assert r["sub_kind"] == f"{p['P1Score']}-{p['P2Score']}" and r["value"] == 1.0
    assert set(r["kind"] for r in rows) <= {"ace", "double_fault", "winner", "unforced_error", "point"}


def test_validate_and_shard_roundtrip(games, tmp_path):
    rows = games[MEN][0] + games[WOMEN][0]
    for mid in (MEN, WOMEN):
        assert schema.validate(games[mid][0]) == []
    meta = schema.write_shard(games[MEN][0], tmp_path / "s.parquet", {"license": tennis.LICENSE, "tag": ""})
    df = pd.read_parquet(tmp_path / "s.parquet")
    assert len(df) == meta["events"] == 194 and meta["games"] == 1
    assert list(df.columns) == list(schema.COLUMNS) and len(rows) == 367


class _Resp:
    def __init__(self, status, content=b""):
        self.status_code, self.content = status, content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class _Session:
    def __init__(self):
        self.calls = []
        self.files = {"2019-wimbledon-points.csv": (FIX / "tennis_min_points.csv").read_bytes(),
                      "2019-wimbledon-matches.csv": (FIX / "tennis_min_matches.csv").read_bytes()}

    def get(self, url, timeout=None):
        self.calls.append(url)
        name = url.rsplit("/", 1)[-1]
        return _Resp(200, self.files[name]) if name in self.files else _Resp(404)


def test_pull_all_fake_session(tmp_path):
    s = _Session()
    metas = tennis.pull_all(tmp_path, years=[2019, 2018], slams=["wimbledon", "usopen"], session=s, rps=0)
    assert sorted((m["league"], m["season"], m["games"], m["events"]) for m in metas) == \
        [("wimbledon_men", 2019, 1, 194), ("wimbledon_women", 2019, 1, 173)]
    side = json.loads((tmp_path / "tennis/wimbledon_men/2019.parquet.json").read_text())
    assert side["source"] == "tennis_slam_pbp" and side["data_plan"] == "A/B" and side["dropped_games"] == 0
    assert side["license"] == tennis.LICENSE and side["tag"] == "" and "Sackmann" in side["credit"]
    assert not list((tmp_path / "tennis/_raw").glob("*.csv"))      # raw deleted
    assert len(tennis.pull_all.missing) == 3                          # 404s tolerated
    n = len(s.calls)
    again = tennis.pull_all(tmp_path, years=[2019], slams=["wimbledon"], session=s, rps=0)
    assert len(s.calls) == n and len(again) == 2                      # shards exist -> no fetch
