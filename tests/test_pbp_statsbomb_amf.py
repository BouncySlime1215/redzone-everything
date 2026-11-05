"""StatsBomb AMF open data converter: trimmed REAL fixture (ATL @ TB, 2021-09-19, final TB 48 ATL 25, with two
Mike Edwards pick-sixes). Fixture keeps every non-tracking event of the game (Pathing/Engagement rows only for the
two lft plays), uuids shortened, plus the lft freeze frames of those two plays."""
from __future__ import annotations

import gzip
import io
import json
import sys
from collections import Counter
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from pbp import schema  # noqa: E402
from pbp import statsbomb_amf as S  # noqa: E402

FX = Path(__file__).parent / "fixtures" / "pbp" / "statsbomb_amf_min"
GID = "1502070"


def _raw(ext: str) -> bytes:
    p = Path(str(FX) + ext)
    return gzip.decompress(p.read_bytes()) if ext.endswith(".gz") else p.read_bytes()


@pytest.fixture(scope="module")
def data():
    plays = S._csv(_raw(".plays.csv.gz"), S.PLAY_COLS + ("play_home_score", "play_away_score"))
    events = S._csv(_raw(".events.csv.gz"), S.EVENT_COLS)
    games = json.loads(_raw(".games.json"))
    out, season = S.convert_season(plays, events, games)
    return plays, events, games, out, season


def test_counts_seq_and_validate(data):
    _, events, _, out, season = data
    assert season == 2021 and list(out) == [GID]
    rows = out[GID]
    assert len(rows) == len(events) == 1724
    assert [r["seq"] for r in rows] == list(range(len(rows)))
    assert schema.validate(rows) == []
    r0 = rows[0]
    assert (r0["source"], r0["sport"], r0["league"], r0["level"]) == ("statsbomb_amf", "football", "nfl", "pro")
    assert (r0["home"], r0["away"], r0["night"]) == ("TB", "ATL", "20210919")
    assert r0["tag"] == schema.PERSONAL and r0["license"] == S.LICENSE
    assert r0["wall_ts"] is None and r0["elapsed_s"] is None


def test_quarters_and_clock(data):
    rows = data[3][GID]
    periods = [r["period"] for r in rows]
    assert periods == sorted(periods) and set(periods) == {1, 2, 3, 4}
    clocks = [r["clock_s"] for r in rows if r["clock_s"] is not None]
    assert all(0 <= c <= 900 for c in clocks)
    q1 = [r["clock_s"] for r in rows if r["period"] == 1 and r["clock_s"] is not None]
    assert q1[0] >= 890 and q1[-1] < q1[0]


def test_scores_match_plays_and_final(data):
    plays, events, _, out, _ = data
    rows = out[GID]
    assert (rows[-1]["home_score"], rows[-1]["away_score"]) == (48, 25)     # real final: TB 48, ATL 25
    ev = events.sort_values("event_game_index").reset_index(drop=True)
    first = {}
    for i, u in enumerate(ev["play_uuid"]):
        if isinstance(u, str) and u not in first:
            first[u] = i
    checked = 0
    for u, h, a in zip(plays["play_uuid"], plays["play_home_score"], plays["play_away_score"]):
        i = first.get(u)
        if i:
            assert (rows[i - 1]["home_score"], rows[i - 1]["away_score"]) == (h, a), u
            checked += 1
    assert checked > 150
    td = [r for r in rows if r["kind"] == "touchdown"]
    assert sum(r["value"] for r in td) == 6 * len(td) and all(r["value"] >= 0 for r in rows)


def test_stops_kind_and_xy(data):
    rows = data[3][GID]
    stops = Counter(r["stop"] for r in rows)
    assert stops["halftime"] == 1 and stops["end_period"] >= 3
    assert stops["timeout"] >= 1 and stops["two_minute_warning"] >= 1
    ends = [r for r in rows if r["kind"].split("+").count("play_end")]
    assert ends and all(r["stop"] in ("whistle", "score_break") for r in ends)
    assert sum(r["stop"] == "score_break" for r in ends) >= 10
    assert all(set(r["kind"]) <= set("abcdefghijklmnopqrstuvwxyz0123456789_+") for r in rows)
    assert {r["sub_kind"] for r in rows} <= {"success", "fail", ""}
    xs = [r["x"] for r in rows if r["x"] is not None]
    assert xs and all(0 <= x <= 1 for x in xs)
    assert {r["possession"] for r in rows[5:]} <= {"home", "away"}
    passes = [r for r in rows if r["kind"] == "pass" and r["actor2_id"]]
    assert passes and all(r["actor_id"] for r in passes)


def test_tags():
    assert S.tags('{"From Snap",Catch}') == ["catch", "from_snap"]
    assert S.tags("{Pathing}") == ["pathing"] and S.tags("") == []


def test_lft_summary(data):
    plays, events, games, out, season = data
    lft = pd.read_parquet(io.BytesIO(_raw(".lft.parquet"))).merge(events[["event_uuid", "game_id"]], on="event_uuid")
    off = {u: S._id(t) for u, t in zip(plays["play_uuid"], plays["play_offense_team_id"])}
    info = S.game_info(games, plays)
    rows = S.convert_lft_game(GID, info[GID], season, events, lft, off, out[GID])
    assert schema.validate(rows) == []
    assert 4 <= len(rows) <= 30 and {r["kind"] for r in rows} == {"track_lft"}
    assert sum(r["stop"] == "whistle" for r in rows) == 2          # two plays
    assert {r["sub_kind"] for r in rows} <= {"pre_snap", "live", "post_play"} and "live" in {r["sub_kind"] for r in rows}
    sp = [r["value"] for r in rows if r["value"] is not None]
    assert sp and all(0 <= v < 15 for v in sp)
    assert all(r["side"] == r["possession"] != "" for r in rows)


class _Resp:
    def __init__(self, content, code=200):
        self.content, self.status_code = content, code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class _Session:
    def __init__(self):
        p = S._paths(2021)
        self.files = {"games.json": _raw(".games.json"), p["plays"]: _raw(".plays.csv.gz"),
                      p["events"]: _raw(".events.csv.gz"), p["lft"]: _raw(".lft.parquet")}
        self.calls = []

    def get(self, url, timeout=None):
        rel = url[len(S.BASE):]
        self.calls.append(rel)
        return _Resp(self.files[rel]) if rel in self.files else _Resp(b"404: Not Found", 404)


def test_pull_all_and_write_shard_roundtrip(tmp_path):
    s = _Session()
    metas = S.pull_all(tmp_path, rps=0, session=s)
    ev = tmp_path / "statsbomb_amf" / "nfl" / "2021.parquet"
    lf = tmp_path / "statsbomb_amf_lft" / "nfl" / "2021.parquet"
    assert ev.exists() and lf.exists() and len(metas) == 2
    m = json.loads(Path(str(ev) + ".json").read_text())
    assert (m["source"], m["league"], m["season"], m["data_plan"], m["games"]) == ("statsbomb_amf", "nfl", 2021, "B", 1)
    assert m["events"] == 1724 and m["dropped_games"] == 0 and m["credit"] and m["tag"] == schema.PERSONAL
    lm = json.loads(Path(str(lf) + ".json").read_text())
    assert lm["rows_are"] == "low-frequency tracking summaries (not pbp events)"
    df = pd.read_parquet(ev)
    assert list(df.columns) == list(schema.COLUMNS) and len(df) == 1724
    assert int(df.home_score.iloc[-1]) == 48 and int(df.away_score.iloc[-1]) == 25
    assert not (tmp_path / "statsbomb_amf" / "_raw" / S._paths(2021)["events"]).exists()   # raw deleted
    n = len(s.calls)
    S.pull_all(tmp_path, rps=0, session=s)            # 2021 shards exist -> its files are not fetched again
    assert not [c for c in s.calls[n:] if "2021_2022" in c]
