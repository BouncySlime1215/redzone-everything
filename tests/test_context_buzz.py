"""BUZZ pullers (Data Crew B, worker B2): offline fixtures written from the documented formats.

Wikimedia REST per-article JSON, hourly pageview dump lines, EventStreams SSE, GDELT GKG 2.1 rows, nflverse
games.csv and an ESPN scoreboard event. The network is never touched."""
from __future__ import annotations

import csv
import gzip
import io
import json
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "context"))

import gdelt_buzz  # noqa: E402
import run_buzz  # noqa: E402
import wiki_edits  # noqa: E402
import wiki_pageviews  # noqa: E402

from redzone.context import buzz  # noqa: E402
from redzone.context.buzz import (  # noqa: E402
    DAY, HOUR, Q15, SRC_ED, SRC_GD, SRC_PV, Cum, Signals, Store, TeamIndex, ckey, load_teams, parse_utc,
)
from redzone.context.tokens import COLUMNS, PERSONAL_USE  # noqa: E402


@pytest.fixture(scope="module")
def idx():
    return TeamIndex(load_teams(path=Path("/nonexistent.csv")))


# ---------------------------------------------------------------- teams seed
def test_seed_has_every_pro_team_once():
    with open(buzz.SEED_CSV, newline="") as f:
        rows = list(csv.DictReader(f))
    active = pd.DataFrame([r for r in rows if r["active"] == "1"])
    assert active.groupby("league").size().to_dict() == {"mlb": 30, "nba": 30, "nfl": 32, "nhl": 32}
    assert not active.duplicated(["league", "abbr"]).any()
    assert not pd.DataFrame(rows)["enwiki"].duplicated().any()
    assert all(" " not in r["enwiki"] for r in rows)
    for abbr in ("ARI", "LA", "LAC", "LV", "WAS", "KC", "GB", "SF"):
        assert abbr in set(active.loc[active.league == "nfl", "abbr"])     # nflverse spellings


def test_resolve_schedule_strings(idx):
    assert idx.resolve("nfl", "OAK") == "LV" and idx.resolve("nfl", "STL") == "LA"
    assert idx.resolve("nba", "Washington Wizards") == "WAS" and idx.resolve("nba", "GS") == "GSW"
    assert idx.resolve("nhl", "Utah Hockey Club") == "UTA"
    assert idx.resolve("mlb", "OAK") == "ATH" and idx.resolve("mlb", "Texas Rangers") == "TEX"
    assert idx.resolve("nfl", "Nowhere Nobodies") is None
    college = TeamIndex([{**{c: "" for c in buzz.TEAM_COLS}, "league": "cbb", "abbr": "North Carolina Tar Heels",
                          "name": "North Carolina Tar Heels", "enwiki": "North_Carolina_Tar_Heels_men's_basketball"},
                         {**{c: "" for c in buzz.TEAM_COLS}, "league": "cbb", "abbr": "NC State Wolfpack",
                          "name": "NC State Wolfpack", "enwiki": "NC_State_Wolfpack_men's_basketball"}])
    assert college.resolve("cbb", "North Carolina") == "North Carolina Tar Heels"


# ---------------------------------------------------------------- pageviews
REST_JSON = {"items": [
    {"project": "en.wikipedia", "article": "Washington_Wizards", "granularity": "daily", "timestamp": "2024010400",
     "access": "all-access", "agent": "user", "views": 4100},
    {"project": "en.wikipedia", "article": "Washington_Wizards", "granularity": "daily", "timestamp": "2024010500",
     "access": "all-access", "agent": "user", "views": 9300}]}


def test_rest_url_and_parse():
    u = wiki_pageviews.rest_url("Athletics (baseball)", parse_utc("2015-07-01"), parse_utc("2015-07-31"))
    assert u.endswith("/user/Athletics_%28baseball%29/daily/20150701/20150731")
    assert wiki_pageviews.parse_rest(REST_JSON) == {parse_utc("2024-01-04"): 4100, parse_utc("2024-01-05"): 9300}
    assert wiki_pageviews.parse_rest(None) == {}


DUMP_LINES = b"""de Washington_Wizards 50 0
en Washington_Wizards 123 0
en.m Washington_Wizards 77 0
en.b Washington_Wizards 999 0
en Washington_Wizards_draft_history 5 0
en Oakland_Raiders 10 0
en Las_Vegas_Raiders 30 0
en Athletics_%28baseball%29 4 0
en.m Washington_Capitals 8 0
en broken_line
""".splitlines()


def test_dump_filter_keeps_only_our_titles_on_en_and_en_m(idx):
    got = wiki_pageviews.filter_dump(DUMP_LINES, wiki_pageviews.title_bytes(idx))
    assert got[("nba", "WAS")] == 200                   # en + en.m, not de / en.b / other titles
    assert got[("nfl", "LV")] == 40                     # former title Oakland_Raiders summed in
    assert got[("mlb", "ATH")] == 4                     # percent-encoded title
    assert got[("nhl", "WSH")] == 8
    assert len(got) == 4


def test_pull_hour_streams_gzip(idx, tmp_path):
    class Resp:
        def __init__(self, b):
            self.raw = io.BytesIO(gzip.compress(b"\n".join(DUMP_LINES)))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class FakeHttp:
        def get(self, url, stream=False, **kw):
            assert url == "https://dumps.wikimedia.org/other/pageviews/2024/2024-01/pageviews-20240105-010000.gz"
            return Resp(None)

    store = Store(tmp_path)
    assert wiki_pageviews.pull_hour(FakeHttp(), idx, store, parse_utc("2024-01-05T01:00Z"))
    store.flush()
    s = store.signals([SRC_PV], 0, 2**40)
    assert set(s["metric"]) == {"views_h"} and int(s.loc[s.team == "WAS", "value"].sum()) == 200 + 0
    assert store.covered_ts(SRC_PV, 0, 2**40) == {parse_utc("2024-01-05T01:00Z")}
    assert not list(tmp_path.rglob("*.gz"))             # the dump is never written


# ---------------------------------------------------------------- edits (SSE + revisions)
SSE_TEXT = """:ok

event: message
id: [{"topic":"eqiad.mediawiki.recentchange","partition":0,"offset":1}]
data: {"$schema":"/mediawiki/recentchange/1.0.0","meta":{"domain":"en.wikipedia.org","dt":"2024-01-05T01:02:03Z"},
data:  "type":"edit","namespace":0,"title":"Washington Wizards","timestamp":1704416523,"user":"SomeUser",
data:  "comment":"fix score","wiki":"enwiki"}

event: message
id: 2
data: {"meta":{"dt":"2024-01-05T01:20:00Z"},"type":"edit","namespace":0,"title":"Washington Wizards","timestamp":1704417600,"user":"1.2.3.4","wiki":"dewiki"}

event: message
id: 3
data: {"meta":{"dt":"2024-01-05T01:21:00Z"},"type":"edit","namespace":1,"title":"Talk:Washington Wizards","timestamp":1704417660,"user":"x","wiki":"enwiki"}

event: message
id: 4
data: {"meta":{"dt":"2024-01-05T01:22:00Z"},"type":"edit","namespace":0,"title":"Oakland Raiders","timestamp":1704417720,"user":"y","comment":"z","wiki":"enwiki"}

"""


def test_sse_parse_and_filter(idx):
    evs = list(wiki_edits.iter_sse(io.StringIO(SSE_TEXT)))
    assert len(evs) == 4 and evs[1][0] == "2"
    hits = [wiki_edits.edit_of(e, idx) for _, e in evs]
    assert hits[0] == (1704416523, [("nba", "WAS")])
    assert hits[1] is None and hits[2] is None          # dewiki, talk namespace
    assert hits[3] == (1704417720, [("nfl", "LV")])


def test_listen_counts_per_15min_and_stores_no_user_data(idx, tmp_path):
    store = Store(tmp_path)
    n = wiki_edits.listen(None, idx, store, seconds=0.5, lines_factory=lambda last: io.StringIO(SSE_TEXT))
    assert n == 2
    s = store.signals([SRC_ED], 0, 2**40)
    assert list(s.columns) == buzz.SIG_COLS
    assert set(s["ts"]) == {parse_utc("2024-01-05T01:00Z"), parse_utc("2024-01-05T01:15Z")}
    blob = b"".join(p.read_bytes() for p in tmp_path.rglob("*.parquet"))
    for secret in (b"SomeUser", b"1.2.3.4", b"fix score"):
        assert secret not in blob


def test_revision_history_paging(idx, tmp_path, monkeypatch):
    pages = [{"continue": {"rvcontinue": "x", "continue": "||"},
              "query": {"pages": [{"title": "Washington Wizards", "revisions": [{"timestamp": "2024-01-05T01:10:00Z"},
                                                                               {"timestamp": "2024-01-05T01:05:00Z"}]}]}},
             {"query": {"pages": [{"title": "Washington Wizards", "revisions": [{"timestamp": "2024-01-05T00:40:00Z"}]}]}}]

    class FakeHttp:
        calls = []

        def get_json(self, url, params, cache=True):
            self.calls.append(params)
            if params["titles"] != "Washington Wizards":
                return {"query": {"pages": [{"title": params["titles"], "missing": True}]}}
            return pages[1] if "rvcontinue" in params else pages[0]

    monkeypatch.setattr(wiki_edits.time, "time", lambda: parse_utc("2024-02-01"))
    store = Store(tmp_path)
    small = TeamIndex([t for t in idx.teams if t["league"] in ("nba", "nhl") and t["abbr"] in ("WAS", "WSH")])
    h = FakeHttp()
    wiki_edits.pull_history(h, small, store, parse_utc("2024-01-05T00:00Z"), parse_utc("2024-01-05T02:00Z"))
    assert all(p["rvprop"] == "timestamp" for p in h.calls)
    s = store.signals([SRC_ED], 0, 2**40)
    got = dict(zip(s.loc[s.team == "WAS", "ts"], s.loc[s.team == "WAS", "value"]))
    assert got == {parse_utc("2024-01-05T00:30Z"): 1.0, parse_utc("2024-01-05T01:00Z"): 2.0}
    assert len(store.covered_ts(SRC_ED, 0, 2**40)) == 8   # 2 h of 15-min bins, all titles OK
    # second call resumes: nothing left to fetch
    assert wiki_edits.pull_history(h, small, store, parse_utc("2024-01-05T00:00Z"),
                                   parse_utc("2024-01-05T02:00Z")) == 0


# ---------------------------------------------------------------- GDELT
def gkg_row(orgs="", names="", tone="0", title="", url="https://example.com/a"):
    cols = [""] * 27
    cols[0], cols[1], cols[2], cols[3], cols[4] = "20240105011500-1", "20240105011500", "1", "example.com", url
    cols[7] = "SPORTS;"
    cols[13] = orgs
    cols[15] = f"{tone},2.1,3.4,5.5,20.1,0,512"
    cols[23] = names
    cols[26] = f"<PAGE_LINKS></PAGE_LINKS><PAGE_TITLE>{title}</PAGE_TITLE>" if title else ""
    return "\t".join(cols)


GKG_LINES = [
    gkg_row(orgs="washington wizards;nba", names="Washington Wizards,120;Kyle Kuzma,300", tone="-2.5"),
    gkg_row(names="Las Vegas Raiders,15", tone="1.5", title="Raiders hold on"),
    gkg_row(title="Capitals rally: Washington Capitals beat Rangers", tone="4"),
    gkg_row(orgs="washington;giants", names="Giants,4", tone="9"),            # one-word names never match
    gkg_row(orgs="new york giants", tone="3", title="New York Giants news"),  # same article, counted once
]


def test_gkg_parse_counts_articles_and_tone(idx):
    got = gdelt_buzz.parse_gkg(GKG_LINES, idx)
    assert got[("nba", "WAS")] == [1, -2.5]
    assert got[("nfl", "LV")] == [1, 1.5]
    assert got[("nhl", "WSH")] == [1, 4.0]
    assert got[("nfl", "NYG")] == [1, 3.0]
    assert ("mlb", "SF") not in got
    assert len(got) == 4


def test_gdelt_file_names_and_store(idx, tmp_path):
    assert gdelt_buzz.file_url(parse_utc("2024-01-05T01:15Z")).endswith("/20240105011500.gkg.csv.zip")
    last = ("123 abc http://data.gdeltproject.org/gdeltv2/20240105011500.export.CSV.zip\n"
            "456 def http://data.gdeltproject.org/gdeltv2/20240105011500.mentions.CSV.zip\n"
            "789 ghi http://data.gdeltproject.org/gdeltv2/20240105011500.gkg.csv.zip\n")
    assert gdelt_buzz.parse_lastupdate(last) == parse_utc("2024-01-05T01:15Z")
    assert gdelt_buzz.file_times(parse_utc("2024-01-05T00:00Z"), parse_utc("2024-01-05T02:00Z"), stride=4) == [
        parse_utc("2024-01-05T00:15Z"), parse_utc("2024-01-05T01:15Z")]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("20240105011500.gkg.csv", "\n".join(GKG_LINES))

    class R:
        content = buf.getvalue()

    class FakeHttp:
        def get(self, url, **kw):
            return R()

    store = Store(tmp_path)
    assert gdelt_buzz.pull_files(FakeHttp(), idx, store, [parse_utc("2024-01-05T01:15Z")], 5) == 1
    s = store.signals([SRC_GD], 0, 2**40)
    assert set(s["ts"]) == {parse_utc("2024-01-05T01:00Z")}       # bin start = file stamp - 15 min
    blob = b"".join(p.read_bytes() for p in tmp_path.rglob("*.parquet"))
    assert b"example.com" not in blob and b"Raiders hold on" not in blob
    assert not list(tmp_path.rglob("*.zip"))
    assert gdelt_buzz.pull_files(FakeHttp(), idx, store, [parse_utc("2024-01-05T01:15Z")], 5) == 0  # covered


# ---------------------------------------------------------------- leak-safe z-scores and facts
def test_window_sums_need_the_whole_bin_inside():
    c = Cum([0, 900, 1800], [1, 2, 4])
    assert list(c.sums(np.array([0, 1800, 2700]), 900)) == [3.0, 4.0]
    assert list(c.sums(np.array([0, 1799]), 900)) == [1.0]        # bin 900 ends at 1800 > 1799


def _daily_frames(views: dict[int, float], league="nba", team="WAS"):
    s = pd.DataFrame([{"source": SRC_PV, "metric": "views", "league": league, "team": team, "ts": t,
                       "bin_s": DAY, "value": v} for t, v in views.items()])
    c = pd.DataFrame([{"source": SRC_PV, "team": ckey(league, team), "ts": t, "bin_s": DAY} for t in views])
    return s, c


GAME = {"game_id": "0022300500", "league": "nba", "home": "WAS", "away": "BOS",
        "start_utc": parse_utc("2024-01-06T00:00Z"), "season": 2023}


def test_pregame_pageviews_z_uses_only_days_before_game_day():
    rng = np.random.default_rng(0)
    d_game = buzz.floor_to(GAME["start_utc"] - 1, DAY)            # 2024-01-05 (kickoff 00:00Z on the 6th)
    days = {d_game - DAY * k: float(1000 + rng.integers(-50, 50)) for k in range(1, 40)}
    s, c = _daily_frames(days)
    base = {f.field: f.value for f in buzz.pregame_facts([GAME], Signals.from_frames(s, c)) if f.team == "WAS"}
    assert "pageviews_z" in base
    # change game day and later: the pregame fact must not move
    later = dict(days) | {d_game: 99999.0, d_game + DAY: 5e5}
    s2, c2 = _daily_frames(later)
    after = {f.field: f.value for f in buzz.pregame_facts([GAME], Signals.from_frames(s2, c2)) if f.team == "WAS"}
    assert after["pageviews_z"] == base["pageviews_z"]
    # but a spike on the day before IS seen, and the 29th day back is not in the baseline
    s3, c3 = _daily_frames(dict(days) | {d_game - DAY: 5000.0})
    spike = [f for f in buzz.pregame_facts([GAME], Signals.from_frames(s3, c3)) if f.field == "pageviews_z"][0]
    assert spike.value > 5
    s4, c4 = _daily_frames(dict(days) | {d_game - 30 * DAY: 1e9})
    far = [f for f in buzz.pregame_facts([GAME], Signals.from_frames(s4, c4)) if f.field == "pageviews_z"][0]
    assert far.value == base["pageviews_z"]


def test_pregame_needs_a_baseline_and_skips_nan():
    s, c = _daily_frames({GAME["start_utc"] - 2 * DAY: 10.0, GAME["start_utc"] - 3 * DAY: 12.0})
    assert [f for f in buzz.pregame_facts([GAME], Signals.from_frames(s, c)) if f.field == "pageviews_z"] == []


def _gdelt_frames(counts: dict[int, int], cover):
    s = pd.DataFrame([{"source": SRC_GD, "metric": m, "league": "nba", "team": "WAS", "ts": t, "bin_s": Q15,
                       "value": v * (1.0 if m == "articles" else -1.0)} for t, v in counts.items()
                      for m in ("articles", "tone_sum")])
    c = pd.DataFrame([{"source": SRC_GD, "team": "*", "ts": t, "bin_s": Q15} for t in cover])
    return s, c


def test_news_and_live_facts_are_leak_free():
    k = GAME["start_utc"]
    cover = list(range(k - 30 * DAY, k + 4 * HOUR, Q15))
    rng = np.random.default_rng(1)
    counts = {t: int(rng.poisson(2)) for t in cover}
    s, c = _gdelt_frames(counts, cover)
    S = Signals.from_frames(s, c)
    pre = {(f.team, f.field): f for f in buzz.pregame_facts([GAME], S)}
    live = buzz.live_facts([GAME], S)
    assert pre[("WAS", "news_volume_z")].asof_utc == "2024-01-05T23:59:59Z"
    assert pre[("WAS", "news_tone")].value == -1.0
    # perturb everything at/after kickoff - 1 s: pregame facts identical
    s2, c2 = _gdelt_frames({t: (v if t + Q15 <= k - 1 else 500) for t, v in counts.items()}, cover)
    S2 = Signals.from_frames(s2, c2)
    pre2 = {(f.team, f.field): f for f in buzz.pregame_facts([GAME], S2)}
    assert pre2[("WAS", "news_volume_z")].value == pre[("WAS", "news_volume_z")].value
    # every live fact at asof e is unchanged when data after e changes
    by = {(f.field, f.asof_utc): f.value for f in live if f.team == "WAS"}
    e_mid = k + HOUR
    s3, c3 = _gdelt_frames({t: (v if t + Q15 <= e_mid else 999) for t, v in counts.items()}, cover)
    for f in buzz.live_facts([GAME], Signals.from_frames(s3, c3)):
        if f.team == "WAS" and parse_utc(f.asof_utc) <= e_mid:
            assert by[(f.field, f.asof_utc)] == f.value
    asofs = sorted({parse_utc(f.asof_utc) for f in live})
    assert asofs[0] == k - 6 * HOUR + Q15 and asofs[-1] == k + 3 * HOUR     # NBA window, bin ends


def test_live_pageviews_z_uses_daily_baseline_before_the_day():
    k = GAME["start_utc"]
    d0 = buzz.floor_to(k, DAY)
    days = {d0 - DAY * i: 2400.0 + 10 * (i % 3) for i in range(1, 35)}
    s, c = _daily_frames(days)
    h = k + HOUR                                        # hour [01:00, 02:00) on game day
    s = pd.concat([s, pd.DataFrame([{"source": SRC_PV, "metric": "views_h", "league": "nba", "team": "WAS",
                                     "ts": h, "bin_s": HOUR, "value": 500.0}])], ignore_index=True)
    c = pd.concat([c, pd.DataFrame([{"source": SRC_PV, "team": "*", "ts": h, "bin_s": HOUR}])], ignore_index=True)
    S = Signals.from_frames(s, c)
    f = [x for x in buzz.live_facts([GAME], S) if x.field == "pageviews_live_z" and x.team == "WAS"]
    assert len(f) == 1 and f[0].asof_utc == "2024-01-06T02:00:00Z" and f[0].value > 5
    s_bad = pd.concat([s, pd.DataFrame([{"source": SRC_PV, "metric": "views", "league": "nba", "team": "WAS",
                                         "ts": d0, "bin_s": DAY, "value": 1e9}])], ignore_index=True)
    c_bad = pd.concat([c, pd.DataFrame([{"source": SRC_PV, "team": "nba:WAS", "ts": d0, "bin_s": DAY}])])
    f2 = [x for x in buzz.live_facts([GAME], Signals.from_frames(s_bad, c_bad))
          if x.field == "pageviews_live_z" and x.team == "WAS"]
    assert f2[0].value == f[0].value                    # game day's own (unfinished) total is never used


def test_fact_rows_and_shards(tmp_path):
    k = GAME["start_utc"]
    cover = list(range(k - 30 * DAY, k + 4 * HOUR, Q15))
    s, c = _gdelt_frames({t: 1 + (t // Q15) % 3 for t in cover}, cover)
    facts = buzz.pregame_facts([GAME], Signals.from_frames(s, c))
    assert facts and all(f.kind == "new" and PERSONAL_USE in f.license for f in facts)
    assert {f.source for f in facts} == {SRC_GD}
    counts = buzz.write_shards(facts, [GAME], tmp_path)
    assert list(counts) == ["nba_2023"]
    df = pd.read_parquet(tmp_path / "nba_2023.parquet")
    assert list(df.columns) == COLUMNS and df["value"].notna().all() and df["bucket"].isna().all()
    buzz.write_shards(facts, [GAME], tmp_path)          # idempotent merge
    assert len(pd.read_parquet(tmp_path / "nba_2023.parquet")) == len(df)
    assert counts["nba_2023"]["new"] == len(df) and counts["nba_2023"]["derived"] == 0


# ---------------------------------------------------------------- schedules
def test_nflverse_and_espn_schedules(idx):
    text = ("game_id,season,game_type,week,gameday,weekday,gametime,away_team,away_score,home_team\n"
            "2023_01_DET_KC,2023,REG,1,2023-09-07,Thursday,20:20,DET,21,KC\n"
            "2016_01_OAK_NO,2016,REG,1,2016-09-11,Sunday,13:00,OAK,35,NO\n")
    rows = run_buzz.nflverse_rows(text)
    assert rows[0]["start_utc"] == parse_utc("2023-09-08T00:20Z")            # EDT -> UTC
    games = buzz.normalize_schedule(rows, idx)
    assert games[1]["away"] == "LV" and games[0]["season"] == 2023
    ev = {"events": [{"id": "401585601", "date": "2024-01-06T00:00Z", "season": {"year": 2024},
                      "competitions": [{"competitors": [
                          {"homeAway": "home", "team": {"abbreviation": "WSH", "displayName": "Washington Wizards"}},
                          {"homeAway": "away", "team": {"abbreviation": "BOS", "displayName": "Boston Celtics"}}]}]}]}
    g = buzz.normalize_schedule(run_buzz.espn_rows("nba", ev), idx)
    assert g == [{"game_id": "espn_nba_401585601", "league": "nba", "home": "WAS", "away": "BOS",
                  "start_utc": parse_utc("2024-01-06T00:00Z"), "season": 2023}]


def test_rest_fixture_is_valid_json():
    assert json.loads(json.dumps(REST_JSON))["items"][0]["agent"] == "user"


def test_run_buzz_backfill_end_to_end_offline(tmp_path, monkeypatch):
    """Runner wiring: schedule CSV -> stored signals -> shards + manifest (pullers stubbed, no network)."""
    k = GAME["start_utc"]
    cover = list(range(k - 30 * DAY, k + 4 * HOUR, Q15))
    store = Store(tmp_path)
    for t in cover:
        store.add(SRC_GD, "articles", "nba", "WAS", t, Q15, 1 + (t // Q15) % 3)
        store.add(SRC_GD, "tone_sum", "nba", "WAS", t, Q15, 0.5)
        store.cover(SRC_GD, t, Q15)
    store.flush()
    for mod, fn in ((wiki_pageviews, "pull_history"), (wiki_pageviews, "pull_hours"), (wiki_edits, "pull_history"),
                    (gdelt_buzz, "pull_files")):
        monkeypatch.setattr(mod, fn, lambda *a, **kw: 0)
    sched = tmp_path / "sched.csv"
    sched.write_text("game_id,league,home,away,start_utc\n0022300500,nba,Washington Wizards,BOS,2024-01-06T00:00:00Z\n"
                     "0022300501,nba,Nowhere Nobodies,BOS,2024-01-06T00:00:00Z\n")
    run_buzz.main(["backfill", "--from", "2024-01-01", "--to", "2024-01-10", "--leagues", "nba", "--no-nflverse",
                   "--schedule", str(sched), "--out", str(tmp_path), "--teams", str(tmp_path / "none.csv")])
    df = pd.read_parquet(tmp_path / "facts" / "nba_2023.parquet")
    assert set(df["game_id"]) == {"0022300500"}
    assert {"news_volume_z", "news_tone", "news_volume_live_z", "news_tone_live"} <= set(df["field"])
    assert (df["asof_utc"] < "2024-01-06T03:00:01Z").all()
    man = json.loads((tmp_path / "manifest_backfill.json").read_text())
    assert man["games"] == 1 and man["facts_this_run"] == len(df)


def test_rest_history_covers_only_up_to_the_newest_returned_day(idx, tmp_path, monkeypatch):
    class R:
        def json(self):
            return REST_JSON

    class FakeHttp:
        def get(self, url, **kw):
            return R() if "Washington_Wizards" in url else None

    monkeypatch.setattr(wiki_pageviews.time, "time", lambda: parse_utc("2024-01-20"))
    store = Store(tmp_path)
    wiki_pageviews.pull_history(FakeHttp(), idx, store, parse_utc("2024-01-01"), parse_utc("2024-01-10"),
                                teams=[("nba", "WAS"), ("nba", "BOS")])
    cov = store.covered_ts(SRC_PV, 0, 2**40, team="nba:WAS")
    assert cov == {parse_utc(f"2024-01-0{d}") for d in range(1, 6)}      # 1st..5th; 6th-9th not yet known
    assert store.covered_ts(SRC_PV, 0, 2**40, team="nba:BOS") == set()
