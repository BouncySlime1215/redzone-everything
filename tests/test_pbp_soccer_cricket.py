"""scripts/pbp/{statsbomb,wyscout,cricsheet}.py: soccer + cricket converters on fixtures (no network).
StatsBomb uses a trimmed real match (WC 2022 Canada 1-2 Morocco); Wyscout and Cricsheet are hand-built."""
import io
import json
import zipfile
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")
pytest.importorskip("pyarrow")

from scripts.pbp import cricsheet as C, schema, statsbomb as S, wyscout as W  # noqa: E402

FX = json.loads((Path(__file__).parent / "fixtures/pbp/statsbomb_min.json").read_text())


class Resp:
    def __init__(self, body: bytes, status=200):
        self.content, self.status_code = body, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def iter_content(self, n):
        for i in range(0, len(self.content), n):
            yield self.content[i:i + n]


class Sess:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, **kw):
        self.calls.append((url, kw.get("headers", {})))
        for k, v in self.routes.items():
            if url.endswith(k):
                return Resp(v)
        return Resp(b"", 404)


def _zip(files: dict) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for n, obj in files.items():
            z.writestr(n, json.dumps(obj))
    return b.getvalue()


# ---------------- StatsBomb (real trimmed fixture) ----------------

def test_statsbomb_convert():
    rows = S.convert_match(FX["match"], FX["events"], FX["competition"])
    assert len(rows) == len(FX["events"]) == 116 and schema.validate(rows) == []
    r0 = rows[0]
    assert (r0["sport"], r0["league"], r0["level"], r0["season"], r0["night"], r0["home"], r0["away"]) == \
        ("soccer", "fifa_world_cup", "intl", 2022, "20221201", "canada", "morocco")
    assert [r["seq"] for r in rows] == list(range(116)) and {r["period"] for r in rows} == {1, 2}
    assert all(r["wall_ts"] is None and r["clock_s"] is None for r in rows)
    goals = [r for r in rows if r["value"] == 1]
    assert [(r["kind"], r["side"]) for r in goals] == [("shot", "away"), ("shot", "away"), ("own_goal_for", "home")]
    assert (rows[-1]["home_score"], rows[-1]["away_score"]) == (1, 2)
    assert [r["stop"] for r in rows if r["kind"] == "half_end"] == ["none", "halftime", "none", "end_period"]
    assert sum(r["stop"] == "injury" for r in rows) == 4 and any(r["stop"] == "whistle" for r in rows)
    p2 = next(r for r in rows if r["period"] == 2)
    assert p2["kind"] == "half_start" and p2["elapsed_s"] == 0.0  # timestamp resets per period
    passes = [r for r in rows if r["kind"] == "pass" and r["actor2_id"]]
    assert passes and all(0 <= r["x"] <= 1 and 0 <= r["y"] <= 1 for r in passes)
    assert {r["possession"] for r in rows} <= {"home", "away"} and r0["license"] == S.LICENSE and r0["tag"] == ""


def test_statsbomb_pull_all_and_shard(tmp_path):
    c, m = FX["competition"], FX["match"]
    sess = Sess({"competitions.json": json.dumps([c]).encode(),
                 f"matches/{c['competition_id']}/{c['season_id']}.json": json.dumps([m]).encode(),
                 f"events/{m['match_id']}.json": json.dumps(FX["events"]).encode()})
    metas = S.pull_all(tmp_path, rps=0, session=sess)
    assert len(metas) == 1 and metas[0]["events"] == 116 and metas[0]["games"] == 1
    assert metas[0]["data_plan"] == "A/B" and "StatsBomb" in metas[0]["credit"]
    df = pd.read_parquet(tmp_path / "statsbomb/fifa_world_cup/2022.parquet")
    assert len(df) == 116 and int(df.away_score.max()) == 2
    n = len(sess.calls)
    S.pull_all(tmp_path, rps=0, session=sess)  # shard + raw cached: no new fetches
    assert len(sess.calls) == n


# ---------------- Wyscout (hand-built, documented schema) ----------------

WY_MATCH = {"wyId": 500, "label": "Arsenal - Chelsea, 2 - 1", "dateutc": "2018-01-20 15:00:00", "competitionId": 364,
            "seasonId": 181150, "teamsData": {"1609": {"side": "home", "score": 2},
                                              "1610": {"side": "away", "score": 1}}}


def _wev(i, period, sec, team, name, sub, tags=(), x=50, y=50):
    return {"id": i, "matchId": 500, "matchPeriod": period, "eventSec": sec, "teamId": team, "playerId": 10 + i,
            "eventId": 0, "eventName": name, "subEventName": sub, "tags": [{"id": t} for t in tags],
            "positions": [{"x": x, "y": y}, {"x": x + 5, "y": y}]}


WY_EVENTS = [
    _wev(1, "1H", 1.0, 1609, "Pass", "Simple pass", x=50),
    _wev(3, "1H", 30.0, 1610, "Foul", "Foul"),
    _wev(2, "1H", 12.0, 1609, "Shot", "Shot", (101, 1801), x=90),
    _wev(4, "1H", 13.0, 1610, "Save attempt", "Reflexes", (101,), x=5),  # keeper concedes: not a goal
    _wev(5, "1H", 2800.0, 1610, "Interruption", "Ball out of the field"),
    _wev(6, "2H", 5.0, 1610, "Free Kick", "Penalty", (101,), x=89),
    _wev(7, "2H", 400.0, 1610, "Others on the ball", "Clearance", (102,)),  # own goal -> home
    _wev(8, "2H", 2900.0, 1609, "Pass", "Simple pass"),
]


def test_wyscout_convert():
    rows = W.convert_match(WY_MATCH, WY_EVENTS, "premier_league", "pro", {1609: "Arsenal", 1610: "Chelsea"})
    assert len(rows) == 8 and schema.validate(rows) == []
    assert [r["seq"] for r in rows] == list(range(8)) and [r["period"] for r in rows] == [1] * 5 + [2] * 3
    assert [r["actor_id"] for r in rows][:4] == ["11", "12", "14", "13"]  # sorted by eventSec
    assert (rows[0]["season"], rows[0]["night"], rows[0]["home"], rows[0]["away"]) == (2018, "20180120", "arsenal",
                                                                                       "chelsea")
    assert (rows[-1]["home_score"], rows[-1]["away_score"]) == (2, 1)
    assert [r["value"] for r in rows] == [0, 1, 0, 0, 0, 1, 0, 0]
    assert rows[6]["sub_kind"] == "own_goal" and (rows[6]["home_score"], rows[6]["away_score"]) == (2, 1)
    assert [r["stop"] for r in rows] == ["none", "none", "none", "whistle", "halftime", "none", "none",
                                         "end_period"]
    assert rows[1]["x"] == pytest.approx(0.9) and rows[1]["possession"] == "home" and rows[0]["tag"] == ""


def test_wyscout_pull_all(tmp_path):
    teams = [{"wyId": 1609, "name": "Arsenal"}, {"wyId": 1610, "name": "Chelsea"}]
    routes = {str(W.FILES["events.zip"]): _zip({"events_England.json": WY_EVENTS}),
              str(W.FILES["matches.zip"]): _zip({"matches_England.json": [WY_MATCH]}),
              str(W.FILES["teams.json"]): json.dumps(teams).encode()}
    raw = tmp_path / "wyscout/_raw"
    raw.mkdir(parents=True)
    body = routes[str(W.FILES["teams.json"])]
    (raw / "teams.json.part").write_bytes(body[:5])  # interrupted download: resumed with a Range header

    class RangeSess(Sess):
        def get(self, url, **kw):
            r = super().get(url, **kw)
            rng = kw.get("headers", {}).get("Range")
            if rng:
                r.content, r.status_code = r.content[int(rng[6:-1]):], 206
            return r

    sess = RangeSess(routes)
    metas = W.pull_all(tmp_path, session=sess)
    assert [(m["league"], m["season"], m["events"]) for m in metas] == [("premier_league", 2018, 8)]
    assert json.loads((raw / "teams.json").read_text()) == teams
    assert any(h.get("Range") == "bytes=5-" for _, h in sess.calls)
    assert pd.read_parquet(tmp_path / "wyscout/premier_league/2018.parquet").home.iloc[0] == "arsenal"


# ---------------- Cricsheet (hand-built v1.1) ----------------

def _d(batter, bowler, b, extras=None, wicket=None):
    d = {"batter": batter, "bowler": bowler, "non_striker": "ns",
         "runs": {"batter": b, "extras": sum((extras or {}).values()), "total": b + sum((extras or {}).values())}}
    if extras:
        d["extras"] = extras
    if wicket:
        d["wickets"] = [{"kind": wicket, "player_out": batter}]
    return d


CR = {"meta": {"data_version": "1.1.0"},
      "info": {"dates": ["2023-04-01"], "teams": ["Chennai Super Kings", "Mumbai Indians"], "match_type": "T20",
               "team_type": "club", "gender": "male", "event": {"name": "Indian Premier League"}},
      "innings": [
          {"team": "Chennai Super Kings", "overs": [
              {"over": 0, "deliveries": [_d("a", "x", 0), _d("a", "x", 4), _d("a", "x", 0, {"wides": 1}),
                                         _d("a", "x", 0, wicket="bowled"), _d("b", "x", 6)]}]},
          {"team": "Mumbai Indians", "overs": [
              {"over": 0, "deliveries": [_d("c", "y", 1, {"noballs": 1}), _d("c", "y", 0, {"legbyes": 2}),
                                         _d("c", "y", 0, wicket="retired hurt"), _d("d", "y", 0, wicket="caught")]},
              {"over": 1, "deliveries": [_d("e", "z", 2)]}]}]}


def test_cricsheet_convert():
    rows = C.convert_match(CR, "1359475")
    assert len(rows) == 10 and schema.validate(rows) == []
    r0 = rows[0]
    assert (r0["sport"], r0["league"], r0["level"], r0["season"], r0["night"], r0["home"]) == \
        ("cricket", "ipl", "pro", 2023, "20230401", "chennai_super_kings")
    assert [r["period"] for r in rows] == [1] * 5 + [2] * 5 and [r["seq"] for r in rows] == list(range(10))
    assert [r["kind"] for r in rows] == ["delivery", "delivery", "wide", "wicket", "delivery", "noball", "legbye",
                                         "wicket", "wicket", "delivery"]
    assert [r["sub_kind"] for r in rows][:5] == ["dot", "four", "r1", "bowled", "six"]
    assert [r["outs"] for r in rows] == [0, 0, 0, 0, 1, 0, 0, 0, 0, 1]  # retired hurt not counted
    assert (rows[4]["home_score"], rows[-1]["home_score"], rows[-1]["away_score"]) == (11, 11, 6)
    assert [r["stop"] for r in rows].count("end_period") == 2 and rows[4]["stop"] == rows[-1]["stop"] == "end_period"
    assert [r["side"] for r in rows] == ["home"] * 5 + ["away"] * 5 and rows[5]["possession"] == "away"
    assert (rows[0]["actor_id"], rows[0]["actor2_id"], rows[0]["elapsed_s"]) == ("a", "x", None)


def test_cricsheet_league_level():
    assert C.league_level({"match_type": "ODI", "team_type": "international", "gender": "female"}) == \
        ("odi_female", "intl")
    assert C.league_level({"match_type": "Test", "gender": "male"}) == ("test_male", "intl")
    assert C.league_level({"match_type": "T20", "team_type": "club", "gender": "male"}) == ("t20_male", "pro")


def test_cricsheet_pull_all(tmp_path):
    odi = json.loads(json.dumps(CR))
    odi["info"].update(match_type="ODI", team_type="international", event={"name": "Some Series"},
                       dates=["2022-12-30", "2022-12-31"])
    sess = Sess({"all_json.zip": _zip({"1359475.json": CR, "999.json": odi, "README.txt": {}})})
    metas = C.pull_all(tmp_path, session=sess)
    assert sorted((m["league"], m["season"], m["events"], m["games"]) for m in metas) == \
        [("ipl", 2023, 10, 1), ("odi_male", 2022, 10, 1)]
    assert sess.calls[0][1]["User-Agent"] == C.UA
    shard = tmp_path / "cricsheet/ipl/2023.parquet"
    assert json.loads(Path(str(shard) + ".json").read_text())["license"] == C.LICENSE
    assert pd.read_parquet(shard).game_id.iloc[0] == "1359475"


def test_sources():
    for m in (S, W, C):
        assert set(m.SOURCE) >= {"id", "leagues", "license", "tag", "est_events_per_game"} and m.SOURCE["tag"] == ""
