"""MLB Stats API GUMBO converter + puller (scripts/pbp/mlb_statsapi.py): fixture conversion and a fake session."""
import copy
import gzip
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from pbp import mlb_statsapi as m  # noqa: E402
from pbp import schema  # noqa: E402

FIX = ROOT / "tests/fixtures/pbp/gumbo_min.json"


@pytest.fixture
def feed():
    return json.loads(FIX.read_text())


@pytest.fixture
def rows(feed):
    return m.convert(feed, 1)


def test_row_count_seq_and_periods(rows, feed):
    n = sum(len(p["playEvents"]) for p in feed["liveData"]["plays"]["allPlays"])
    assert len(rows) == n == 21
    assert [r["seq"] for r in rows] == list(range(21))
    assert {r["period"] for r in rows[:14]} == {1} and {r["period"] for r in rows[14:]} == {2}
    assert {r["side"] for r in rows[:14]} == {"away"} and {r["side"] for r in rows[14:]} == {"home"}
    assert all(r["possession"] == r["side"] for r in rows)
    assert all(r["night"] == "20240412" and r["home"] == "BOS" and r["away"] == "NYY" for r in rows)
    ts = [r["wall_ts"] for r in rows]
    assert all(t is not None for t in ts) and ts == sorted(ts)


def test_kinds_and_actions(rows):
    assert [r["kind"] for r in rows[:3]] == ["b", "c", "d"]
    assert [r["sub_kind"] for r in rows[:3]] == ["ff", "sl", "single"]
    assert rows[3]["kind"] == "pickoff" and rows[3]["x"] is None
    assert rows[5]["kind"] == "stolen_base_2b" and rows[5]["actor_id"] == "1"
    assert rows[9]["kind"] == "pitching_substitution" and rows[9]["actor_id"] == "901"
    assert 0 <= rows[0]["x"] <= 1 and 0 <= rows[0]["y"] <= 1


def test_count_outs_bases_before_event(rows):
    assert [(r["balls"], r["strikes"]) for r in rows[:3]] == [(0, 0), (1, 0), (1, 1)]
    assert [(r["balls"], r["strikes"]) for r in rows[10:13]] == [(0, 0), (0, 1), (0, 2)]
    assert [r["bases"] for r in rows[3:9]] == [1, 1, 1, 2, 2, 2]  # SB moves runner 1B -> 2B before pitch 6
    assert rows[9]["bases"] == 4 and rows[9]["outs"] == 1
    assert rows[13]["outs"] == 2 and rows[14]["outs"] == 0 and rows[14]["bases"] == 0  # half-inning reset


def test_runs_scores_and_stops(rows):
    assert [r["seq"] for r in rows if r["value"]] == [7, 15]
    assert (rows[7]["away_score"], rows[7]["home_score"]) == (1, 0)
    assert (rows[-1]["away_score"], rows[-1]["home_score"]) == (1, 1)
    assert rows[6]["away_score"] == 0  # score is AFTER the event, not before
    stops = {r["seq"]: r["stop"] for r in rows if r["stop"] != "none"}
    assert stops == {9: "pitching_change", 13: "end_period", 20: "end_period"}


def test_validate_and_shard_roundtrip(rows, tmp_path):
    assert schema.validate(rows) == []
    assert all(r["license"] == m.LICENSE and r["tag"] == schema.PERSONAL for r in rows)
    side = schema.write_shard(rows, tmp_path / "mlb/2024.parquet", {"license": m.LICENSE, "tag": schema.PERSONAL})
    df = pd.read_parquet(tmp_path / "mlb/2024.parquet")
    assert len(df) == side["events"] == 21 and side["games"] == 1
    assert df.kind.tolist() == [r["kind"] for r in rows] and int(df.home_score.iloc[-1]) == 1


def test_mlb_2025_rejected_milb_2025_accepted(feed):
    f = copy.deepcopy(feed)
    f["gameData"]["datetime"]["officialDate"] = "2025-04-12"
    f["gameData"]["game"]["season"] = "2025"
    assert any("locked" in e for e in schema.validate(m.convert(f, 1)))
    milb = m.convert(f, 11)
    assert schema.validate(milb) == [] and milb[0]["league"] == "milb_aaa" and milb[0]["level"] == "minor"


class Resp:
    def __init__(self, code, js=None, headers=None):
        self.status_code, self._js, self.headers = code, js, headers or {}

    def json(self):
        return self._js

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeSession:
    """Schedule: 3 Final games + 1 Preview; game 2 404s, game 3 is broken, game 1 first answers 429 then 503."""

    def __init__(self, feed):
        self.feed, self.headers, self.calls, self.flaky = feed, {}, [], [Resp(429, headers={"Retry-After": "3"}),
                                                                          Resp(503)]

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        if "schedule" in url:
            if params["gameType"] == "P":
                return Resp(200, {"dates": []})
            games = [{"gamePk": pk, "status": {"abstractGameState": st}}
                     for pk, st in ((1, "Final"), (2, "Final"), (3, "Final"), (4, "Preview"))]
            return Resp(200, {"dates": [{"games": games[:2]}, {"games": games[2:] + games[:1]}]})
        pk = int(url.split("/game/")[1].split("/")[0])
        if pk == 1:
            return self.flaky.pop(0) if self.flaky else Resp(200, self.feed)
        if pk == 2:
            return Resp(404)
        return Resp(200, {"gamePk": 3, "gameData": {}, "liveData": {"plays": {"allPlays": []}}})


def test_pull_season_fake_session(feed, tmp_path):
    s, slept = FakeSession(feed), []
    out = m.pull_season(1, 2024, tmp_path, rps=5.0, session=s, sleep=slept.append, keep_raw=True)
    assert s.headers["User-Agent"] == "redzone-personal-research/1.0"
    assert out["scheduled"] == 3 and out["games"] == 1 and out["events"] == 21
    assert out["missing"] == 1 and out["dropped"] == 1
    assert 3.0 in slept and 2 in slept  # Retry-After honoured, then exponential backoff on the 503
    assert all(w <= 1.0 + 1e-9 for w in slept if w < 2)  # rps capped at 1.0 -> gaps never exceed 1 s
    shard = tmp_path / "mlb/2024.parquet"
    meta = json.loads(Path(str(shard) + ".json").read_text())
    assert meta["data_plan"] == "B" and meta["tag"] == schema.PERSONAL and meta["license"] == m.LICENSE
    raw = tmp_path / "raw/1/2024/1.json.gz"
    assert json.loads(gzip.decompress(raw.read_bytes()))["gamePk"] == 745000
    # resumable: a rerun reuses the cached raw feed (no feed GET for game 1), then deletes raw files
    s2 = FakeSession(feed)
    m.pull_season(1, 2024, tmp_path, session=s2, sleep=lambda _: None)
    assert not any("/game/1/" in u for u, _ in s2.calls) and not raw.exists()


def test_rate_cap_and_mlb_window(tmp_path):
    assert m.Client(FakeSession({}), rps=10).gap == pytest.approx(1.0)
    assert m.pull_season(1, 2025, tmp_path, session=FakeSession({}))["skipped"]
    assert m._seasons("2024-2022") == [2024, 2023, 2022] and m._seasons("2019") == [2019]
    assert m.SOURCE["est_events_per_game"] == 290 and set(m.SOURCE["leagues"]) == set(m.LEAGUES.values())
