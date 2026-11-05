"""EuroLeague / EuroCup live API converter + puller (scripts/pbp/euroleague.py): inline fixtures, fake sessions."""
import gzip
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from pbp import euroleague as el  # noqa: E402
from pbp import schema  # noqa: E402


def _p(n, pt, team="", pid="", t="", a=None, b=None, minute=None):
    return {"NUMBEROFPLAY": n, "PLAYTYPE": pt + " " * (2 if len(pt) < 4 else 0), "CODETEAM": team + "   " if team else
            "", "PLAYER_ID": (pid + " ") if pid else "", "MARKERTIME": t, "POINTS_A": a, "POINTS_B": b,
            "MINUTE": minute, "PLAYINFO": "free text never stored"}


def _pbp():
    return {"Live": False, "TeamA": "PANATHINAIKOS", "TeamB": "REAL MADRID", "CodeTeamA": "PAN  ", "CodeTeamB": "MAD  ",
            "FirstQuarter": [_p(1, "BP", t=""), _p(2, "2FGM", "PAN", "P001", "09:40", 2, 0),
                             _p(3, "AS", "PAN", "P002", "09:40"), _p(4, "3FGA", "MAD", "P010", "09:20"),
                             _p(5, "D", "PAN", "P003", "09:18"), _p(6, "TO", "PAN", "P001", "09:00"),
                             _p(7, "ST", "MAD", "P011", "09:00"), _p(8, "TOUT", "PAN", t="08:55"),
                             _p(9, "EP", t="00:00")],
            "SecondQuarter": [_p(10, "BP", t="10:00"), _p(12, "FTM", "MAD", "P010", "05:00", 2, 1),
                              _p(11, "CM", "PAN", "P001", "05:00"), _p(13, "EP", t="")],
            "ThirdQuarter": [_p(14, "3FGM", "MAD", "P012", "07:00"), _p(15, "EP", t="00:00")],
            "ForthQuarter": [_p(16, "2FGM", "PAN", "P001", "00:02", 6, 4), _p(17, "TOUT_TV", t="00:02"),
                             _p(18, "EP", t="00:00")],
            "ExtraTime": [_p(19, "BP", t=""), _p(20, "O", "MAD", "P013", "04:00"), _p(21, "EP", t="00:00"),
                          _p(22, "BP", t="05:00"), _p(23, "3FGM", "MAD", "P013", "01:00", 6, 7),
                          _p(24, "EP", t="00:00"), _p(25, "EG", t="")]}


HEADER = {"Live": False, "Date": "12/10/2023", "TeamA": "PANATHINAIKOS", "TeamB": "REAL MADRID",
          "CodeTeamA": "PAN", "CodeTeamB": "MAD", "ScoreA": "6", "ScoreB": "7"}


@pytest.fixture
def rows():
    return el.convert(_pbp(), HEADER, "euroleague", 2024, 7)


def test_counts_seq_ids_and_validate(rows):
    assert len(rows) == 25 and [r["seq"] for r in rows] == list(range(25))
    assert schema.validate(rows) == []
    r = rows[0]
    assert r["game_id"] == "E2023_7" and r["night"] == "20231012" and r["season"] == 2024
    assert r["home"] == "PAN" and r["away"] == "MAD" and r["league"] == "euroleague" and r["level"] == "pro"
    assert r["tag"] == schema.PERSONAL and "§8f" in r["license"]
    assert all(x["wall_ts"] is None for x in rows)
    assert el.seasoncode("eurocup", 2010) == "U2009"


def test_periods_and_clock(rows):
    assert [r["period"] for r in rows] == [1] * 9 + [2] * 4 + [3] * 2 + [4] * 3 + [5] * 3 + [6] * 4
    assert rows[0]["clock_s"] == 600 and rows[1]["clock_s"] == 580 and rows[18]["clock_s"] == 300
    assert rows[12]["clock_s"] == 0           # blank EP marker -> 0
    assert rows[22]["clock_s"] == 60
    # NUMBEROFPLAY ordering inside a quarter: CM (11) before FTM (12)
    assert [rows[10]["kind"], rows[11]["kind"]] == ["cm", "ftm"]


def test_scores_values_and_sides(rows):
    assert rows[1]["home_score"] == 2 and rows[1]["value"] == 2 and rows[1]["side"] == "home"
    assert rows[11]["away_score"] == 1 and rows[11]["value"] == 1
    assert rows[13]["kind"] == "3fgm" and rows[13]["away_score"] == 4 and rows[13]["value"] == 3  # null POINTS fallback
    assert rows[-1]["home_score"] == 6 and rows[-1]["away_score"] == 7
    assert rows[3]["value"] == 0 and rows[0]["side"] == "" and rows[1]["actor_id"] == "P001"
    assert all(r["sub_kind"] == "" for r in rows)


def test_stops_and_possession(rows):
    stops = {i: r["stop"] for i, r in enumerate(rows) if r["stop"] != "none"}
    assert stops == {7: "timeout", 8: "end_period", 12: "halftime", 14: "end_period", 16: "timeout",
                     17: "end_period", 20: "end_period", 23: "end_period", 24: "end_period"}
    poss = [r["possession"] for r in rows[:8]]
    assert poss == ["", "away", "away", "away", "home", "away", "away", "away"]
    assert rows[19]["possession"] == "away"          # offensive rebound keeps it with MAD


def test_minute_drives_overtime_number():
    pbp = {"CodeTeamA": "A", "CodeTeamB": "B",
           "FirstQuarter": [_p(1, "2FGM", "A", "x", "09:00", 2, 0)],
           "ExtraTime": [_p(2, "2FGM", "B", "y", "04:00", 2, 2, minute=41),
                         _p(3, "2FGM", "B", "y", "04:00", 2, 4, minute=46)]}
    rows = el.convert(pbp, {"Date": "01/02/2010"}, "eurocup", 2010, 3)
    assert [r["period"] for r in rows] == [1, 5, 6] and rows[0]["game_id"] == "U2009_3"


def test_write_shard_roundtrip(rows, tmp_path):
    pytest.importorskip("pyarrow")
    side = schema.write_shard(rows, tmp_path / "euroleague/2024.parquet", {"license": el.LICENSE,
                                                                           "tag": schema.PERSONAL})
    df = pd.read_parquet(tmp_path / "euroleague/2024.parquet")
    assert len(df) == side["events"] == 25 and side["games"] == 1
    assert df.clock_s.iloc[1] == 580 and df.kind.iloc[1] == "2fgm"


class _R:
    def __init__(self, status, js=None, text=None):
        self.status_code, self._js, self.headers = status, js, {}
        self.text = text if text is not None else json.dumps(js) if js is not None else ""

    def json(self):
        if self._js is None:
            raise ValueError("no json")
        return self._js

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeSession:
    """Games at codes 1, 2 and 4 (3 is a gap), everything after is empty; code 2 answers with a 429 once."""

    def __init__(self, games=(1, 2, 4), live=()):
        self.games, self.live, self.calls, self.headers, self.hit429 = set(games), set(live), [], {}, False

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        n = int((params or {}).get("gamecode", 0))
        if n == 2 and not self.hit429:
            self.hit429 = True
            r = _R(429)
            r.headers = {"Retry-After": "3"}
            return r
        if n not in self.games:
            return _R(404) if n % 2 else _R(200, text="")      # 404s and empty 200 bodies both count as empty
        if url == el.PBP_URL:
            return _R(200, _pbp())
        return _R(200, dict(HEADER, Live=n in self.live))


def test_pull_season_stops_after_consecutive_empties(tmp_path):
    pytest.importorskip("pyarrow")
    s, slept = FakeSession(), []
    out = el.pull_season("euroleague", 2024, tmp_path, session=s, sleep=slept.append)
    assert out["games"] == 3 and out["missing"] == 1 and out["dropped"] == 0
    assert out["probed"] == 4 + el.STOP_AFTER_EMPTY
    assert out["events"] == 75 and out["tag"] == schema.PERSONAL
    assert 3 in slept                                     # Retry-After honoured
    assert all(w <= 1.0 + 1e-6 for w in slept if w != 3)  # rate gap never above 1 s per request
    assert all(p["seasoncode"] == "E2023" for _, p in s.calls)
    df = pd.read_parquet(tmp_path / "euroleague/2024.parquet")
    assert sorted(df.game_id.unique()) == ["E2023_1", "E2023_2", "E2023_4"]
    assert not (tmp_path / "euroleague/raw/E2023").exists()       # raw deleted after the shard


def test_pull_season_resumes_from_raw_and_skips_live(tmp_path):
    pytest.importorskip("pyarrow")
    out = el.pull_season("eurocup", 2024, tmp_path, session=FakeSession(live=(4,)), sleep=lambda s: None,
                         keep_raw=True)
    assert out["games"] == 2 and out["live"] == 1
    raw = tmp_path / "eurocup/raw/U2023"
    assert sorted(p.name for p in raw.iterdir()) == ["1.json.gz", "2.json.gz"]
    assert json.loads(gzip.decompress((raw / "1.json.gz").read_bytes()))["header"]["Date"] == "12/10/2023"
    s2 = FakeSession()
    out2 = el.pull_season("eurocup", 2024, tmp_path, session=s2, sleep=lambda s: None, max_games=2)
    assert out2["games"] == 2 and s2.calls == []           # both games came from the raw cache


def test_client_caps_rps():
    c = el.Client(FakeSession(), rps=50, sleep=lambda s: None)
    assert c.gap == 1.0
    assert el.SOURCE["id"] == "euroleague" and el.SOURCE["est_events_per_game"] == 450
    assert set(el.SOURCE["leagues"]) == {"euroleague", "eurocup"}
