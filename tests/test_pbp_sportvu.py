"""Data Division A: SportVU 2015-16 tracking -> 1 Hz play-table summary (scripts/pbp/sportvu.py)."""
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
pytest.importorskip("pyarrow")

from pbp import schema, sportvu  # noqa: E402

H, V = 1610612761, 1610612766
T0 = 1451695252000      # whole second


def _moment(i, clock_runs_until=50):
    """25 Hz moment i: clock runs for the first 2 s (i < 50), then stops; home player 1 holds the ball."""
    gc = 720.0 - min(i, clock_runs_until) * 0.04
    bx = 10.0 + i * 0.4                       # ball moves 10 ft/s along x
    ents = [[-1, -1, bx, 25.0, 3.0],
            [H, 1, bx + 1.0, 25.0, 0.0], [H, 2, 40.0, 10.0, 0.0], [H, 3, 40.0, 40.0, 0.0],
            [H, 4, 60.0, 10.0, 0.0], [H, 5, 60.0, 40.0, 0.0],
            [V, 6, 80.0, 25.0, 0.0], [V, 7, 70.0, 10.0, 0.0], [V, 8, 70.0, 40.0, 0.0],
            [V, 9, 85.0, 10.0, 0.0], [V, 10, 85.0, 40.0, 0.0]]
    return [1, T0 + i * 40, gc, 20.0 - i * 0.04 if i < 50 else None, None, ents]


def _game():
    team = lambda tid, ab: {"teamid": tid, "abbreviation": ab, "players": []}  # noqa: E731
    ms = [_moment(i) for i in range(75)]   # 3 s at 25 Hz
    evs = [{"eventId": "1", "home": team(H, "TOR"), "visitor": team(V, "CHA"), "moments": ms[:50]},
           {"eventId": "2", "home": team(H, "TOR"), "visitor": team(V, "CHA"), "moments": ms[30:]}]  # overlap
    return {"gameid": "0021500492", "gamedate": "2016-01-01", "events": evs}


def test_dedupe_and_1hz_live_dead_handler():
    g = _game()
    assert len(sportvu.dedupe(g["events"])) == 75
    rows, extra = sportvu.convert_game(g)
    assert schema.validate(rows) == []
    assert [r["kind"] for r in rows] == ["track_live", "track_live", "track_dead"]
    assert [r["stop"] for r in rows] == ["none", "none", "whistle"]
    assert [r["sub_kind"] for r in rows] == ["sc18-24", "sc18-24", "off"]
    r0 = rows[0]
    assert r0["side"] == "home" and r0["possession"] == "home" and r0["actor_id"] == "1"
    assert r0["night"] == "20160101" and r0["game_id"] == "0021500492" and r0["home"] == "TOR"
    assert r0["wall_ts"] == T0 / 1000 and r0["home_score"] is None and r0["tag"] == schema.PERSONAL
    assert r0["value"] == pytest.approx(10.0, abs=0.1) and 0 < r0["x"] < 1 and r0["y"] == pytest.approx(0.5)
    assert len(extra) == 3 and extra[0]["seq"] == 0 and extra[1]["wall_ms"] == T0 + 1000
    assert extra[0]["home_speed"] == pytest.approx(10.0 / 5, abs=0.01)   # only player 1 moves
    assert extra[0]["away_speed"] == 0 and extra[0]["ball_z"] == 3.0 and extra[0]["away_spacing"] > 0


def test_no_handler_beyond_4ft():
    g = _game()
    for ev in g["events"]:
        for m in ev["moments"]:
            m[5][1][2] = m[5][0][2] + 10.0
    rows, _ = sportvu.convert_game(g)
    assert rows[0]["side"] == "" and rows[0]["actor_id"] == ""


def test_write_shard_roundtrip(tmp_path):
    import pandas as pd
    rows, _ = sportvu.convert_game(_game())
    side = schema.write_shard(rows, tmp_path / "nba_sportvu/2016.parquet",
                              {"source": "sportvu", "league": "nba", "season": 2016, "license": sportvu.LICENSE,
                               "tag": schema.PERSONAL, "data_plan": "B"})
    df = pd.read_parquet(tmp_path / "nba_sportvu/2016.parquet")
    assert side["events"] == 3 and list(df.kind) == ["track_live", "track_live", "track_dead"]


def test_schedule_names():
    csv = (b"GAME_ID,GAME_DATE,HTM,VTM\n21500492,20160101,TOR,CHA\n21500492,20160101,TOR,CHA\n"
           b"21500999,20160301,BOS,NYK\n")
    gs = sportvu.schedule(csv)
    assert gs == [{"game_id": "0021500492", "date": "20160101", "home": "TOR", "away": "CHA",
                   "file": "01.01.2016.CHA.at.TOR.7z"}]


class _Resp:
    def __init__(self, code, content=b""):
        self.status_code, self.content = code, content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class _Session:
    def __init__(self, files):
        self.files, self.calls = files, []

    def get(self, url, timeout=None):
        self.calls.append(url)
        for k, v in self.files.items():
            if url.endswith(k):
                return _Resp(200, v)
        return _Resp(404)


def test_puller_fake_session(tmp_path):
    py7zr = pytest.importorskip("py7zr")
    buf = io.BytesIO()
    with py7zr.SevenZipFile(buf, "w") as z:
        z.writestr(json.dumps(_game()), "0021500492.json")
    csv = b"GAME_ID,GAME_DATE,HTM,VTM\n21500492,20160101,TOR,CHA\n21500493,20160101,BOS,NYK\n"
    s = _Session({"shots.csv": csv, "01.01.2016.CHA.at.TOR.7z": buf.getvalue()})
    meta, xmeta = sportvu.pull_all(tmp_path, rps=0, session=s)
    assert meta["games"] == 1 and meta["events"] == 3 and meta["missing_files"] == 1 and meta["complete"]
    assert meta["tag"] == schema.PERSONAL and meta["data_plan"] == "B" and xmeta["events"] == 0
    import pandas as pd
    df = pd.read_parquet(tmp_path / "nba_sportvu/2016.parquet")
    x = pd.read_parquet(tmp_path / "sportvu_extra/nba/2016.parquet")
    assert len(df) == 3 and list(x.seq) == [0, 1, 2] and x.wall_ms.iloc[2] == T0 + 2000 and df.league.iloc[0] == "nba"
    assert not list((tmp_path / "nba_sportvu/raw").rglob("*.json*")) or \
        {p.name for p in (tmp_path / "nba_sportvu/raw").rglob("*.json")} == {"games.json", "status.json"}
    n = len(s.calls)
    assert sportvu.pull_all(tmp_path, rps=0, session=s)[0]["complete"] and len(s.calls) == n   # resumed, no fetch
