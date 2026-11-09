"""Tests for the sports fetch scripts with fake fetchers: no network, no download.

Why: data job 14: the scripts run once and unattended in Kaggle, so the locked-night cut, the personal-use tag, the
robots and rate rules and resume must be proven here before any bulk request is made.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import urllib.error
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from agent.kaggle import data_fetch as df
from agent.kaggle import fetch_sports_cfb, fetch_sports_mlb, fetch_sports_nfl, fetch_sports_nhl
from agent.kaggle import sports_common as sc


def run(spec, fetcher, tmp_path: Path, *argv: str) -> dict:
    args = df.parser(spec).parse_args(["--out-dir", str(tmp_path), "--no-sleep", *argv])
    return df.Run(spec, args, fetcher).run()


def rows_of(tmp_path: Path, name: str) -> pa.Table:
    files = sorted((tmp_path / name / "shards").glob("*.parquet"))
    return pa.concat_tables([pq.read_table(f) for f in files]) if files else pa.table({})


def parquet_bytes(table: pa.Table) -> bytes:
    b = io.BytesIO()
    pq.write_table(table, b)
    return b.getvalue()


class BlobFetcher(df.Fetcher):
    def __init__(self, blobs, jsons=None):
        self.blobs, self.jsons, self.calls, self.json_calls = blobs, jsons or {}, [], []

    def get_json(self, url):
        self.json_calls.append(url)
        if url not in self.jsons:
            raise AssertionError("no json expected")
        return self.jsons[url]

    def size(self, url):
        return None

    def download(self, url, dest, start):
        self.calls.append(url)
        if url not in self.blobs:
            raise urllib.error.HTTPError(url, 404, "nope", None, None)
        Path(dest).write_bytes(self.blobs[url])


NFL_REPO, NFL_TAG = "nflverse/nflverse-data", "https://api.github.com/repos/nflverse/nflverse-data/releases/tags/pbp"
CFB_REPO, CFB_TAG = "sportsdataverse/sportsdataverse-data", "https://api.github.com/repos/sportsdataverse/sportsdataverse-data/releases/tags/espn_cfb_pbp"
CFB_RAW = "https://raw.githubusercontent.com/sportsdataverse/cfbfastR-data/main/pbp/parquet/play_by_play_{y}.parquet"


def asset(aid, repo=NFL_REPO):
    return f"https://api.github.com/repos/{repo}/releases/assets/{aid}{sc.ASSET_FRAGMENT}"


def listing(repo, ids, sized=False):
    """What GET /repos/<repo>/releases/tags/<tag> returns: {season: asset id} (sizes only when asked: the fake blobs are tiny)."""
    return {"assets": [{"id": i, "name": f"play_by_play_{y}.parquet", **({"size": 1000 + y} if sized else {})} for y, i in ids.items()]
            + [{"id": 1, "name": "other.csv", "size": 5}]}


def test_cuts_match_the_factory():
    from redzone.noggin import factory
    assert sc.EVAL_FIRST_NIGHT == factory.EVAL_FIRST_NIGHT


def test_night_boundaries():
    assert sc.night_of("2024-12-18T00:30:00Z") == "20241217"   # 7:30 PM ET is still the 17th
    assert sc.night_of("2024-07-04") == "20240704"
    assert sc.night_of(dt.date(2024, 7, 4)) == "20240704"
    assert sc.night_of(None) is None and sc.night_of("bad") is None
    assert sc.is_locked("mlb", "20250301") and not sc.is_locked("mlb", "20241231") and sc.is_locked("mlb", None)


def test_night_of_early_morning_is_previous_night():
    assert sc.night_of("2024-12-18T08:00:00Z") == "20241217"


def test_lock_filter_tags_and_drops():
    f = sc.lock_filter("nfl", "nflverse_pbp", "lic", ("game_date",))
    t = pa.table({"game_id": ["a", "b", "c", "d"], "game_date": ["2024-09-08", "2025-09-05", "2025-12-01", None],
                  "league": ["x"] * 4})
    out = f(t)
    assert out.column("game_id").to_pylist() == ["a"]
    r = out.to_pylist()[0]
    assert r["source_night_id"] == "nfl:20240908" and r["night"] == "20240908"
    assert r["tag"] == sc.PERSONAL_TAG and r["plan"] == "B" and r["league"] == "nfl" and r["night_exact"] == "1"
    assert out.column_names.count("league") == 1


def test_lock_filter_season_fallback_for_undated_college_football():
    f = sc.lock_filter("cfb", "espn_cfb", "lic")
    out = f(pa.table({"season": [2023, 2024, 2025, 2026], "game_id": ["a", "b", "c", "d"]}))
    assert out.column("game_id").to_pylist() == ["a", "b"]
    assert set(out.column("night_exact").to_pylist()) == {"0"}
    assert out.column("source_night_id").to_pylist() == ["cfb:20230901", "cfb:20240901"]


def test_release_script_end_to_end_resume_and_manifest(tmp_path):
    t24 = pa.table({"game_id": ["g1", "g1", "g2"], "game_date": ["2024-09-08", "2024-09-08", "2025-09-06"], "season": [2024] * 3})
    t23 = pa.table({"game_id": ["g0"], "game_date": ["2023-09-10"], "season": [2023]})
    f = BlobFetcher({asset(11): parquet_bytes(t24), asset(10): parquet_bytes(t23)}, {NFL_TAG: listing(NFL_REPO, {2025: 12, 2024: 11, 2023: 10})})
    spec = fetch_sports_nfl.SPEC
    row = run(spec, f, tmp_path, "--first-season", "2023", "--last-season", "2025")
    # --last-season 2025 is capped at 2024 (2025 is the locked season: never requested); the locked row never lands
    assert row["rows"] == 3 and row["tag"] == sc.PERSONAL_TAG and row["plan"] == "B"
    assert row["status"] == "complete" and row["items_done"] == 2 and f.calls == [asset(11), asset(10)]   # 2025 (locked) never requested
    assert f.json_calls == [NFL_TAG]                              # one listing request, then one request per season
    assert not any("releases/download" in u for u in f.calls + f.json_calls)
    t = rows_of(tmp_path, spec.name)
    assert sorted(t.column("source_night_id").to_pylist()) == ["nfl:20230910", "nfl:20240908", "nfl:20240908"]
    n = len(f.calls)
    run(spec, f, tmp_path, "--first-season", "2023", "--last-season", "2024")
    assert len(f.calls) == n                      # finished items are not fetched again


def test_cfb_script_defaults_skip_locked_season():
    f = BlobFetcher({}, {CFB_TAG: listing(CFB_REPO, {y: 100 + y for y in range(2022, 2026)})})
    args = df.parser(fetch_sports_cfb.SPEC).parse_args([])
    items = fetch_sports_cfb.SPEC.list_items(f, args)
    names = [i.name for i in items]
    assert names[0] == "cfb-2024" and names[-1] == "cfb-2002" and len(names) == 23
    urls = {i.name: i.urls[0] for i in items}
    # 2022-2024: API asset endpoint; 2002-2021: in-repo parquet on raw.githubusercontent.com
    assert urls["cfb-2024"] == asset(2124, CFB_REPO) and urls["cfb-2022"] == asset(2122, CFB_REPO)
    assert urls["cfb-2021"] == CFB_RAW.format(y=2021) and urls["cfb-2002"] == CFB_RAW.format(y=2002)
    assert f.json_calls == [CFB_TAG]
    assert not any("github.com/" in u and "api.github.com" not in u for u in urls.values())


def test_cfb_in_repo_seasons_need_no_api_listing():
    f = BlobFetcher({})
    args = df.parser(fetch_sports_cfb.SPEC).parse_args(["--first-season", "2010", "--last-season", "2021"])
    assert len(fetch_sports_cfb.SPEC.list_items(f, args)) == 12 and f.json_calls == []


def test_cfb_tag_license_and_cut_are_kept(tmp_path):
    t = pa.table({"game_id": ["a", "b"], "season": [2021, 2026]})
    f = BlobFetcher({CFB_RAW.format(y=2021): parquet_bytes(t)})
    row = run(fetch_sports_cfb.SPEC, f, tmp_path, "--first-season", "2021", "--last-season", "2021")
    r = rows_of(tmp_path, fetch_sports_cfb.SPEC.name).to_pylist()
    assert row["tag"] == sc.PERSONAL_TAG and [x["source_night_id"] for x in r] == ["cfb:20210901"]
    assert "ESPN terms" in r[0]["license"] and r[0]["tag"] == sc.PERSONAL_TAG
    assert f.calls == [CFB_RAW.format(y=2021)]


def test_nfl_license_is_cc_by_with_the_personal_tag(tmp_path):
    t = pa.table({"game_id": ["g"], "game_date": ["2023-09-10"], "season": [2023]})
    f = BlobFetcher({asset(10): parquet_bytes(t)}, {NFL_TAG: listing(NFL_REPO, {2023: 10})})
    run(fetch_sports_nfl.SPEC, f, tmp_path, "--first-season", "2023", "--last-season", "2023")
    r = rows_of(tmp_path, fetch_sports_nfl.SPEC.name).to_pylist()[0]
    assert r["license"].startswith("CC-BY-4.0") and r["tag"] == sc.PERSONAL_TAG and r["source_night_id"] == "nfl:20230910"


class FakeNhl(sc.JsonCsvFetcher):
    def __init__(self, flat, cols, payloads):
        super().__init__(flat, cols, min_interval=0)
        self.payloads, self.asked = payloads, []

    def get_json(self, url):
        self.asked.append(url)
        if url not in self.payloads:
            raise urllib.error.HTTPError(url, 404, "nope", None, None)
        return self.payloads[url]


def nhl_world():
    sched1 = {"nextStartDate": "2024-10-05", "gameWeek": [{"date": "2024-10-04", "games": [
        {"id": 2024020001, "gameType": 2, "gameState": "OFF", "startTimeUTC": "2024-10-04T23:00:00Z"},
        {"id": 2024020002, "gameType": 1, "gameState": "OFF", "startTimeUTC": "2024-10-04T23:00:00Z"}]}]}
    sched2 = {"nextStartDate": None, "gameWeek": [{"date": "2024-10-05", "games": [
        {"id": 2024020003, "gameType": 2, "gameState": "OFF", "startTimeUTC": "2024-10-05T23:00:00Z"}]}]}
    pbp = lambda gid: {"id": gid, "season": 20242025, "gameType": 2, "startTimeUTC": "2024-10-04T23:00:00Z",
                       "homeTeam": {"id": 1}, "awayTeam": {"id": 2},
                       "plays": [{"eventId": 1, "sortOrder": 10, "periodDescriptor": {"number": 1, "periodType": "REG"},
                                  "timeInPeriod": "00:00", "timeRemaining": "20:00", "situationCode": "1551",
                                  "typeCode": 516, "typeDescKey": "period-start", "details": {}},
                                 {"eventId": 2, "sortOrder": 20, "periodDescriptor": {"number": 1, "periodType": "REG"},
                                  "timeInPeriod": "01:10", "timeRemaining": "18:50", "situationCode": "1551",
                                  "typeCode": 506, "typeDescKey": "shot-on-goal",
                                  "details": {"xCoord": 40, "yCoord": -3, "shotType": "wrist", "extraThing": 7}}]}
    return {f"{sc_}/schedule/2024-09-20": sched1 for sc_ in [fetch_sports_nhl.WEB]} | {
        f"{fetch_sports_nhl.WEB}/schedule/2024-10-05": sched2,
        f"{fetch_sports_nhl.WEB}/gamecenter/2024020001/play-by-play": pbp(2024020001)}


def test_nhl_plays_listing_rows_and_missing_game(tmp_path):
    world = nhl_world()
    f = FakeNhl(fetch_sports_nhl.flatten_plays, fetch_sports_nhl.PLAYS_COLS, world)
    spec = fetch_sports_nhl.make_spec("plays")
    row = run(spec, f, tmp_path, "--first-season", "2024", "--last-season", "2024")
    assert row["tag"] == sc.PERSONAL_TAG and row["rows"] == 2          # game 3 is a 404: header only, game 2 is preseason
    assert len(f.missing) == 1 and f.missing[0].startswith(f"{fetch_sports_nhl.WEB}/gamecenter/2024020003")
    t = rows_of(tmp_path, spec.name).to_pylist()
    assert {r["source_night_id"] for r in t} == {"nhl:20241004"}
    shot = [r for r in t if r["type_desc"] == "shot-on-goal"][0]
    assert shot["d_xCoord"] == "40" and json.loads(shot["details_json"]) == {"extraThing": 7}


def test_nhl_never_requests_a_locked_game():
    world = nhl_world()
    late = {"nextStartDate": None, "gameWeek": [{"date": "2026-10-25", "games": [
        {"id": 2026020100, "gameType": 2, "gameState": "OFF", "startTimeUTC": "2026-10-25T23:00:00Z"}]}]}
    world[f"{fetch_sports_nhl.WEB}/schedule/2026-09-20"] = late
    f = FakeNhl(fetch_sports_nhl.flatten_plays, fetch_sports_nhl.PLAYS_COLS, world)
    args = df.parser(fetch_sports_nhl.make_spec("plays")).parse_args(["--first-season", "2026", "--last-season", "2026"])
    assert fetch_sports_nhl.make_spec("plays").list_items(f, args) == []


def test_nhl_shifts_flatten():
    url = "https://api.nhle.com/stats/rest/en/shiftcharts?cayenneExp=gameId=1" + sc.fragment(night="20241004", season=2024)
    rows = fetch_sports_nhl.flatten_shifts(url, {"data": [{"gameId": 1, "playerId": 9, "period": 1, "startTime": "00:10",
                                                           "endTime": "00:50", "duration": "00:40", "eventDetails": None}]})
    assert rows[0]["night"] == "20241004" and rows[0]["playerId"] == 9 and rows[0]["eventDetails_json"] == ""


class FakeDays(sc.CsvDayFetcher):
    def __init__(self, bodies):
        super().__init__("game_date", min_interval=0)
        self.bodies, self.asked = bodies, []

    def download(self, url, dest, start):
        self.asked.append(url)
        Path(dest).write_text(self.bodies.get(url.split("game_date_gt=")[1][:10], ""))
        if Path(dest).stat().st_size == 0:
            Path(dest).write_text(self.header + "\n")


def test_statcast_days_empty_day_locked_year_and_rows(tmp_path):
    body = "game_date,pitch_type,game_pk\n2024-07-04,FF,1\n2024-07-04,SL,1\n"
    f = FakeDays({"2024-07-04": body})
    spec = fetch_sports_mlb.SPEC
    row = run(spec, f, tmp_path, "--first-season", "2024", "--last-season", "2024")
    assert row["rows"] == 2 and row["tag"] == sc.PERSONAL_TAG
    assert {r["source_night_id"] for r in rows_of(tmp_path, spec.name).to_pylist()} == {"mlb:20240704"}
    args = df.parser(spec).parse_args(["--first-season", "2025", "--last-season", "2025"])
    assert spec.list_items(f, args) == []                       # 2025 is the held-out year: not one request


def test_robots_content_signal_and_wildcards():
    txt = "User-agent: *\nContent-Signal: search=yes,ai-train=no,use=reference\nAllow: /\n"
    assert sc.parse_robots(txt, "/v1/x") == (True, False)
    assert sc.parse_robots("User-agent: *\nDisallow:\n", "/a") == (True, True)
    gh = "User-agent: *\nDisallow: /*/*/download$\n"
    assert sc.parse_robots(gh, "/a/b/download") == (False, True)
    assert sc.parse_robots(gh, "/a/releases/download/t/f.parquet") == (True, True)
    assert sc.parse_robots("User-agent: Bot\nDisallow: /\n", "/a") == (True, True)


class RobotsFetcher(sc.PoliteFetcher):
    def __init__(self, text, **kw):
        super().__init__(**kw)
        self.text, self.opened = text, []

    def _read_robots(self, host_url):
        return self.text

    def _raw_open(self, url, start=0, method="GET"):
        self.opened.append(url)
        return io.BytesIO(b"{}")


def test_ai_train_no_blocks_before_any_request_and_stops_the_run(tmp_path):
    f = RobotsFetcher("User-agent: *\nContent-Signal: ai-train=no\n", min_interval=0)
    with pytest.raises(sc.Blocked):
        f.get_json("https://api-web.nhle.com/v1/schedule/2024-09-20")
    assert f.opened == []
    spec = fetch_sports_nhl.make_spec("plays")
    args = df.parser(spec).parse_args(["--out-dir", str(tmp_path), "--first-season", "2024", "--last-season", "2024"])
    with pytest.raises(df.StopRun):
        df.Run(spec, args, f).run()
    assert sc.main(spec, lambda a: f, ["--out-dir", str(tmp_path), "--first-season", "2024", "--last-season", "2024"]) == 2


def test_rate_limit_and_retry_after():
    now, slept = [0.0], []
    def sleep(s):
        slept.append(s)
        now[0] += s

    class F(RobotsFetcher):
        n = 0
        def _raw_open(self, url, start=0, method="GET"):
            F.n += 1
            if F.n == 2:
                raise urllib.error.HTTPError(url, 429, "slow", {"Retry-After": "7"}, None)
            return io.BytesIO(b"{}")

    f = F("", min_interval=1.0, sleep=sleep, clock=lambda: now[0])
    f._robots["https://x.test"] = None
    f.get_json("https://x.test/a")
    f.get_json("https://x.test/b")          # first try 429 -> sleeps the Retry-After, then succeeds
    assert 7.0 in slept and F.n == 3
    f.get_json("https://x.test/c")
    assert all(s >= 0 for s in slept) and slept[-1] <= 1.0


def test_403_is_a_block_not_retried():
    class F(RobotsFetcher):
        def _raw_open(self, url, start=0, method="GET"):
            self.opened.append(url)
            raise urllib.error.HTTPError(url, 403, "no", {}, None)

    f = F("", min_interval=0)
    f._robots["https://x.test"] = None
    with pytest.raises(sc.Blocked):
        f.get_json("https://x.test/a")
    assert len(f.opened) == 1


def test_nba_ncaam_use_raw_githubusercontent_in_repo_parquet(tmp_path):
    from agent.kaggle import fetch_sports_nba, fetch_sports_ncaam
    for mod, repo, league, first_night in ((fetch_sports_nba, "hoopR-nba-data", "nba", "20261020"),
                                           (fetch_sports_ncaam, "hoopR-mbb-data", "cbb", "20261020")):
        base = f"https://raw.githubusercontent.com/sportsdataverse/{repo}/main/{league if league == 'nba' else 'mbb'}/pbp/parquet/play_by_play_{{y}}.parquet"
        t = pa.table({"game_id": ["a", "b"], "game_date": ["2024-01-10", "2026-11-01"], "season": [2024, 2024]})
        f = BlobFetcher({base.format(y=2024): parquet_bytes(t)})
        row = run(mod.SPEC, f, tmp_path, "--first-season", "2024", "--last-season", "2024")
        assert f.calls == [base.format(y=2024)]              # only the new host
        assert row["tag"] == sc.PERSONAL_TAG and row["rows"] == 1   # the locked-night row is dropped
        assert rows_of(tmp_path, mod.SPEC.name).column("source_night_id").to_pylist() == [f"{league}:20240110"]


def test_disallow_on_the_new_host_still_stops_the_run(tmp_path):
    from agent.kaggle import fetch_sports_nba, fetch_sports_ncaam
    for mod, repo in ((fetch_sports_nba, "hoopR-nba-data"), (fetch_sports_ncaam, "hoopR-mbb-data")):
        f = RobotsFetcher("User-agent: *\nDisallow: /sportsdataverse/\n", min_interval=0)
        args = df.parser(mod.SPEC).parse_args(["--out-dir", str(tmp_path / repo), "--first-season", "2024", "--last-season", "2024"])
        with pytest.raises(sc.Blocked):
            df.Run(mod.SPEC, args, f).run()
        assert f.opened == []
        assert sc.main(mod.SPEC, lambda a: f, ["--out-dir", str(tmp_path / repo), "--first-season", "2024", "--last-season", "2024"]) == 2


# ---------- ApiAssetFetcher: api.github.com release assets, no token, 60 requests an hour ----------

class ApiFetcher(sc.ApiAssetFetcher):
    """Robots text per host is given; the network is replaced by the test's `answers` (url -> bytes or HTTPError)."""

    def __init__(self, robots=None, **kw):
        super().__init__(**kw)
        self.robots_text = robots or {}
        self.waited = []

    def _read_robots(self, host_url):
        self.waited.append(("robots", host_url))
        return self.robots_text.get(host_url)


class FakeNet:
    def __init__(self, monkeypatch, redirect="https://release-assets.githubusercontent.com/signed?x=1"):
        self.opened, self.headers, self.redirect = [], [], redirect
        self.fail = []                                  # HTTPErrors to raise on the next api opens, in order
        net = self

        class Opener:
            def open(self, req, timeout=0):
                net.opened.append(req.full_url)
                net.headers.append(dict(req.header_items()))
                if net.fail:
                    raise net.fail.pop(0)
                raise urllib.error.HTTPError(req.full_url, 302, "found", {"Location": net.redirect}, None)

        monkeypatch.setattr(sc.urllib.request, "build_opener", lambda *a: Opener())

        def urlopen(req, timeout=0):
            net.opened.append(req.full_url)
            net.headers.append(dict(req.header_items()))
            return io.BytesIO(b"PARQUET")
        monkeypatch.setattr(sc.urllib.request, "urlopen", urlopen)


def test_api_asset_request_asks_for_octet_stream_with_no_token_and_checks_robots_on_the_redirect_host(monkeypatch, tmp_path):
    net = FakeNet(monkeypatch)
    f = ApiFetcher({}, min_interval=0, api_interval=0, sleep=lambda s: None)
    f._robots["https://api.github.com"] = None
    f.download(asset(11), tmp_path / "x.part", 0)
    assert (tmp_path / "x.part").read_bytes() == b"PARQUET"
    assert net.opened == [asset(11), "https://release-assets.githubusercontent.com/signed?x=1"]
    assert net.headers[0]["Accept"] == "application/octet-stream"
    assert not any(k.lower() in ("authorization", "cookie") for h in net.headers for k in h)
    assert ("robots", "https://release-assets.githubusercontent.com") in f.waited      # robots first, before the bytes
    assert not any("github.com/" in u and "releases/download" in u for u in net.opened)


def test_a_redirect_host_that_disallows_stops_before_any_byte(monkeypatch, tmp_path):
    net = FakeNet(monkeypatch)
    f = ApiFetcher({"https://release-assets.githubusercontent.com": "User-agent: *\nDisallow: /\n"}, min_interval=0, api_interval=0,
                   sleep=lambda s: None)
    f._robots["https://api.github.com"] = None
    with pytest.raises(sc.Blocked):
        f.download(asset(11), tmp_path / "x.part", 0)
    assert net.opened == [asset(11)] and not (tmp_path / "x.part").exists()


def test_robots_on_api_host_is_checked_and_a_disallow_there_stops_the_run(monkeypatch):
    net = FakeNet(monkeypatch)
    f = ApiFetcher({"https://api.github.com": "User-agent: *\nDisallow: /repos/\n"}, min_interval=0, api_interval=0, sleep=lambda s: None)
    with pytest.raises(sc.Blocked):
        f.get_json(NFL_TAG)
    assert net.opened == []


def test_api_host_is_spaced_at_least_a_minute_and_other_hosts_use_min_interval():
    f = sc.ApiAssetFetcher(min_interval=1.0, api_interval=5.0)        # asking for less than 60 s is raised to 60 s
    assert f.api_interval == 60.0 and sc.API_MIN_INTERVAL >= 60.0
    assert f._interval(asset(1)) == 60.0 and f._interval(NFL_TAG) == 60.0
    assert f._interval(CFB_RAW.format(y=2010)) == 1.0 and f._interval("https://raw.githubusercontent.com") == 1.0
    now, slept = [0.0], []

    def sleep(s):
        slept.append(s)
        now[0] += s
    g = ApiFetcher({}, min_interval=1.0, api_interval=62.0, sleep=sleep, clock=lambda: now[0])
    g._wait(asset(1))
    g._wait(asset(2))
    assert slept[-1] == pytest.approx(62.0)


def test_used_up_quota_waits_for_the_reset_and_resumes(monkeypatch, tmp_path):
    net = FakeNet(monkeypatch)
    slept = []
    quota = urllib.error.HTTPError(asset(11), 403, "rate limit", {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1100"}, None)
    net.fail = [quota]
    f = ApiFetcher({}, min_interval=0, api_interval=0, sleep=slept.append, wall=lambda: 1000.0)
    f._robots["https://api.github.com"] = None
    f._robots["https://release-assets.githubusercontent.com"] = None
    f.download(asset(11), tmp_path / "x.part", 0)
    assert 101.0 in slept                                              # reset - now + 1 s
    assert (tmp_path / "x.part").read_bytes() == b"PARQUET" and net.opened.count(asset(11)) == 2


def test_a_403_that_is_not_the_quota_is_still_a_block(monkeypatch, tmp_path):
    net = FakeNet(monkeypatch)
    net.fail = [urllib.error.HTTPError(asset(11), 403, "no", {"X-RateLimit-Remaining": "37"}, None)]
    f = ApiFetcher({}, min_interval=0, api_interval=0, sleep=lambda s: None)
    f._robots["https://api.github.com"] = None
    with pytest.raises(sc.Blocked):
        f.download(asset(11), tmp_path / "x.part", 0)
    assert net.opened == [asset(11)]


def test_api_listing_is_a_plain_json_get_without_the_octet_stream_header(monkeypatch):
    seen = []

    class F(ApiFetcher):
        def _raw_open(self, url, start=0, method="GET"):
            seen.append(url)
            return io.BytesIO(json.dumps(listing(NFL_REPO, {2024: 11}, sized=True)).encode())
    f = F({}, min_interval=0, api_interval=0, sleep=lambda s: None)
    f._robots["https://api.github.com"] = None
    assert sc.asset_index(f, NFL_REPO, "pbp") == {"play_by_play_2024.parquet": (11, 3024), "other.csv": (1, 5)}
    assert seen == [NFL_TAG]


def test_a_fetch_run_stops_cleanly_on_a_block_and_resumes_from_the_unfinished_season(tmp_path):
    t = pa.table({"game_id": ["g"], "game_date": ["2023-09-10"], "season": [2023]})
    jsons = {NFL_TAG: listing(NFL_REPO, {2024: 11, 2023: 10})}
    f = BlobFetcher({asset(10): parquet_bytes(t)}, jsons)
    row = run(fetch_sports_nfl.SPEC, f, tmp_path, "--first-season", "2023", "--last-season", "2024")
    state = json.loads((tmp_path / fetch_sports_nfl.SPEC.name / "state.json").read_text())
    assert list(state["items"]) == ["nfl-2023"] and "nfl-2024" in state["failed"]       # 2024 had no file: not done, not fatal
    f2 = BlobFetcher({asset(10): parquet_bytes(t), asset(11): parquet_bytes(t)}, jsons)
    row = run(fetch_sports_nfl.SPEC, f2, tmp_path, "--first-season", "2023", "--last-season", "2024")
    assert asset(10) not in f2.calls                                                  # the finished season is not fetched again


def test_no_fetch_script_requests_a_github_release_download_url(tmp_path):
    from agent.kaggle import fetch_sports_nba, fetch_sports_ncaam
    jsons = {NFL_TAG: listing(NFL_REPO, {y: y for y in range(1999, 2025)}),
             CFB_TAG: listing(CFB_REPO, {y: y for y in range(2022, 2025)}),
             "https://api.github.com/repos/sportsdataverse/sportsdataverse-data/releases/tags/espn_nba_pbp": {"assets": []}}
    for mod in (fetch_sports_nfl, fetch_sports_cfb, fetch_sports_nba, fetch_sports_ncaam):
        f = BlobFetcher({}, jsons)
        args = df.parser(mod.SPEC).parse_args([])
        urls = [u for i in mod.SPEC.list_items(f, args) for u in i.urls] + f.json_calls
        assert urls and all(u.startswith(("https://api.github.com/", "https://raw.githubusercontent.com/")) for u in urls), mod.__name__
        assert not any("github.com/" in u and "releases/download" in u for u in urls)
