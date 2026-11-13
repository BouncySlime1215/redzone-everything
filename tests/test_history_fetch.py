"""History fetchers (B-01): pacing, retries, loud failures, fences, resume after a kill, report, licences, shards.

Why: the fetchers run for hours unattended against hosts we must not hammer, so the rate floor, the 403 stop, the
no-silent-empty rule (the venv's urllib has no CA bundle), the evaluation fences and "kill it, rerun it, no duplicates" are
proven here against fake HTTP only. Any real socket connection fails the test (see the `_no_network` fixture).
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import os
import socket
import subprocess
from pathlib import Path

import httpx
import pytest

from scripts import fetch_history, license_check
from scripts.history import datasets, espn, pack, probe
from scripts.history import net as hnet
from scripts.history import store as hstore
from scripts.history.leagues import (EVAL_FIRST_NIGHT, LEAGUES, LOCKED_WINDOW, fenced, night_of, parse_seasons, season_fully_fenced,
                                     season_touches_fence)
from scripts.history.licences import PERSONAL_TAG, SOURCES, gate, manifest_licences

FIX = Path(__file__).parent / "fixtures" / "history"
NOW = dt.datetime(2026, 11, 13, 12, 0, tzinfo=dt.timezone.utc)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a test tried to open a real network connection")
    monkeypatch.setattr(socket.socket, "connect", boom)


class VT:
    """Virtual time: sleeping advances the clock, nothing really waits."""

    def __init__(self):
        self.t = 1000.0
        self.slept: list[float] = []

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += s

    def clock(self) -> float:
        return self.t


def client_for(handler, vt: VT | None = None, **kw) -> tuple[hnet.PoliteClient, VT]:
    vt = vt or VT()
    return hnet.PoliteClient(transport=httpx.MockTransport(handler), sleep=vt.sleep, clock=vt.clock, **kw), vt


# ---------------------------------------------------------------- fake ESPN

def make_play(gid: str, i: int, day: str = "2024-01-16", wall: bool = True) -> dict:
    p = {"id": f"{gid}{i:04d}", "sequenceNumber": str(i), "type": {"id": "42", "text": "Jump Shot", "abbreviation": "js"},
         "text": f"Player {i} makes a jump shot", "homeScore": i, "awayScore": i // 2, "period": {"number": 1 + i // 100, "displayValue": "1st Quarter"},
         "clock": {"displayValue": f"{11 - (i // 20) % 12}:00"}, "scoringPlay": True, "scoreValue": 2, "team": {"id": "13"},
         "participants": [{"athlete": {"id": str(1000 + i), "displayName": "Some Name", "headshot": {"href": "http://x/h.png"}, "links": [{"href": "u"}]}}]}
    if wall:
        p["wallclock"] = f"{day}T01:{i // 60 % 60:02d}:{i % 60:02d}Z"
    return p


def make_summary(gid: str, n: int, *, header_id: str | None = None, wall: bool = True, day: str = "2024-01-16") -> dict:
    return {"header": {"id": header_id or gid, "competitions": [{"competitors": [
        {"homeAway": "home", "team": {"id": "13", "abbreviation": "LAL"}, "score": "101"},
        {"homeAway": "away", "team": {"id": "2", "abbreviation": "BOS"}, "score": "99"}]}]},
        "plays": [make_play(gid, i, day, wall) for i in range(1, n + 1)], "wallclockAvailable": wall,
        "winprobability": [{"homeWinPercentage": 0.5}], "pickcenter": [{"spread": -3}], "boxscore": {"teams": [{"x": 1}]}}


def make_event(gid: str, utc: str, *, state: str = "post", stype: int = 2, year: int = 2024, week: int | None = None) -> dict:
    e = {"id": gid, "date": utc, "shortName": "BOS @ LAL", "season": {"year": year, "type": stype}, "status": {"type": {"state": state}},
         "competitions": [{"competitors": [{"homeAway": "home", "team": {"id": "13", "abbreviation": "LAL"}, "score": "101"},
                                           {"homeAway": "away", "team": {"id": "2", "abbreviation": "BOS"}, "score": "99"}]}]}
    if week is not None:
        e["week"] = {"number": week}
    return e


class Killed(BaseException):
    """Simulates the process dying mid-run (not an Exception, so nothing in the fetcher can swallow it)."""


class FakeEspn:
    """days: {'YYYYMMDD': [event, ...]}; summaries: {gid: summary dict | int status}. Counts every request."""

    def __init__(self, days: dict[str, list[dict]], summaries: dict, *, vt: VT | None = None, year: int = 2024, kind: str = "day",
                 weeks: dict[tuple[int, int], list[dict]] | None = None, robots: int = 403):
        self.days, self.summaries, self.vt, self.year, self.kind, self.weeks, self.robots = days, summaries, vt, year, kind, weeks or {}, robots
        self.log: list[tuple[str, dict]] = []
        self.times: list[float] = []
        self.kill_after_summaries: int | None = None
        self.summary_calls: dict[str, int] = {}
        self.user_agents: set[str] = set()

    def league_obj(self) -> dict:
        if self.kind == "week":
            cal = [{"label": "Regular Season", "value": "2", "startDate": "2023-09-06T07:00Z", "endDate": "2024-01-10T07:00Z",
                    "entries": [{"label": f"Week {w}", "value": str(w), "startDate": f"2023-09-{6 + 7 * (w - 1):02d}T07:00Z"[:20],
                                 "endDate": f"2023-09-{12 + 7 * (w - 1):02d}T06:59Z"[:20]} for w in (1, 2)]}]
            return {"season": {"year": self.year}, "calendarType": "list", "calendarIsWhitelist": False, "calendar": cal}
        cal = [f"{d[:4]}-{d[4:6]}-{d[6:]}T08:00Z" for d in sorted(self.days)]
        return {"season": {"year": self.year}, "calendarType": "day", "calendarIsWhitelist": True, "calendar": cal,
                "calendarStartDate": cal[0], "calendarEndDate": cal[-1]}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.vt:
            self.times.append(self.vt.clock())
        self.user_agents.add(request.headers.get("user-agent", ""))
        q = dict(request.url.params)
        self.log.append((request.url.path, q))
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(self.robots, text="<html>Access Denied</html>")
        if path.endswith("/scoreboard"):
            if self.kind == "week":
                evs = self.weeks.get((int(q.get("seasontype", 2)), int(q.get("week", 1))), []) if "limit" in q else self.weeks.get((2, 1), [])
            else:
                evs = self.days.get(q.get("dates", ""), []) if "limit" in q else self.days.get(q.get("dates", ""), [])
            return httpx.Response(200, json={"leagues": [self.league_obj()], "events": evs})
        if path.endswith("/summary"):
            gid = q["event"]
            self.summary_calls[gid] = self.summary_calls.get(gid, 0) + 1
            if self.kill_after_summaries is not None and sum(self.summary_calls.values()) > self.kill_after_summaries:
                raise Killed()
            s = self.summaries.get(gid, 404)
            if isinstance(s, int):
                return httpx.Response(s, json={"code": s})
            return httpx.Response(200, json=s)
        return httpx.Response(404)

    def summary_requests(self) -> int:
        return sum(1 for p, _ in self.log if p.endswith("/summary"))


def nba_world(n_games: int = 5, plays: int = 6) -> FakeEspn:
    """Two days of NBA games, all final, ids 401000001.. ; day 1 has 3 games, day 2 the rest."""
    gids = [f"4010000{i:02d}" for i in range(1, n_games + 1)]
    d1 = [make_event(g, "2024-01-16T00:30Z") for g in gids[:3]]       # ET 7:30 PM Jan 15 -> night 20240115
    d2 = [make_event(g, "2024-01-17T00:30Z") for g in gids[3:]]       # night 20240116
    sums = {g: make_summary(g, plays + i, day="2024-01-16") for i, g in enumerate(gids)}
    return FakeEspn({"20240115": d1, "20240116": d2}, sums)


def run_espn(tmp_path: Path, fake: FakeEspn, *, vt: VT | None = None, limits: espn.Limits | None = None, leagues=("nba",), seasons=(2024,),
             types=(2, 3), log=lambda s: None, stop_flag=lambda: False) -> tuple[dict, hstore.Store, hnet.PoliteClient]:
    vt = vt or fake.vt or VT()
    fake.vt = vt
    client, _ = client_for(fake, vt)
    store = hstore.Store(tmp_path / "hist")
    run = espn.Run(client, store, limits or espn.Limits(), log=log, now=lambda: NOW, clock=vt.clock, stop_flag=stop_flag, types=types)
    return run.run(list(leagues), list(seasons)), store, client


# ---------------------------------------------------------------- net

def test_interval_below_the_floor_is_refused():
    with pytest.raises(ValueError, match="floor"):
        hnet.PoliteClient(min_interval=0.5, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))


def test_requests_are_spaced_at_least_one_second_apart():
    vt = VT()
    stamps: list[float] = []

    def handler(req):
        stamps.append(vt.clock())
        return httpx.Response(200, json={"ok": 1})
    client, _ = client_for(handler, vt)
    for _ in range(5):
        client.get_json("https://x.test/a")
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert len(gaps) == 4 and all(g >= 1.0 - 1e-9 for g in gaps)


def test_user_agent_is_honest_and_generic():
    seen = []
    client, _ = client_for(lambda r: (seen.append(r.headers["user-agent"]), httpx.Response(200, json={}))[1])
    client.get_json("https://x.test/a")
    ua = seen[0]
    assert ua.startswith("redzone-personal-research/") and "Mozilla" not in ua and "@" not in ua


def test_429_waits_for_retry_after_then_succeeds():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(429, headers={"Retry-After": "30"}) if len(calls) == 1 else httpx.Response(200, json={"ok": 1})
    client, vt = client_for(handler)
    assert client.get_json("https://x.test/a").data == {"ok": 1}
    assert max(vt.slept) >= 30 and client.stats.retries == 1 and client.stats.by_status[429] == 1


def test_server_errors_exhaust_retries_and_raise():
    client, vt = client_for(lambda r: httpx.Response(503))
    with pytest.raises(hnet.FetchError, match="gave up after 6 attempts"):
        client.get_json("https://x.test/a")
    assert client.stats.requests == hnet.MAX_ATTEMPTS
    assert sorted(vt.slept)[-1] > 8        # exponential growth, not a flat hammer


def test_403_stops_at_once_without_retrying():
    client, _ = client_for(lambda r: httpx.Response(403, text="Access Denied"))
    with pytest.raises(hnet.Blocked):
        client.get_json("https://x.test/a")
    assert client.stats.requests == 1


@pytest.mark.parametrize("resp", [httpx.Response(200, text="<html>hi</html>"), httpx.Response(200, content=b""), httpx.Response(200, json=[1, 2])])
def test_a_200_that_is_not_a_json_object_is_an_error_never_an_empty_result(resp):
    client, _ = client_for(lambda r: resp)
    with pytest.raises(hnet.FetchError):
        client.get_json("https://x.test/a")


def test_tls_failure_is_loud_and_not_retried():
    def handler(req):
        raise httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
    client, _ = client_for(handler)
    with pytest.raises(hnet.FetchError, match="certifi"):
        client.get_json("https://x.test/a")
    assert client.stats.requests == 1


def test_a_dropped_connection_is_retried():
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) < 3:
            raise httpx.ReadTimeout("slow")
        return httpx.Response(200, json={"ok": 1})
    client, _ = client_for(handler)
    assert client.get_json("https://x.test/a").status == 200 and client.stats.retries == 2


def test_the_ca_bundle_is_loaded_not_empty():
    ctx, source = hnet.make_ssl_context()
    assert source == "certifi" and len(ctx.get_ca_certs()) > 50


def test_404_is_returned_only_when_allowed():
    client, _ = client_for(lambda r: httpx.Response(404, json={}))
    assert client.get_json("https://x.test/a", allow_404=True).status == 404
    with pytest.raises(hnet.FetchError):
        client.get_json("https://x.test/a")


def test_robots_rules_and_verdicts():
    txt = "User-agent: *\nDisallow: /private\nAllow: /private/ok\nContent-Signal: ai-train=no\n"
    assert hnet.parse_robots("User-agent: *\nDisallow: /private\nAllow: /private/ok\n", "/private/x") == (False, True)
    assert hnet.parse_robots("User-agent: *\nDisallow: /private\nAllow: /private/ok\n", "/private/ok/1") == (True, True)
    assert hnet.parse_robots(txt, "/pub")[1] is False
    assert hnet.parse_robots("User-agent: Bot\nDisallow: /\n", "/x") == (True, True)
    for status, body, verdict in ((403, "no", "unavailable"), (404, "", "allow"), (200, "User-agent: *\nDisallow: /\n", "deny"), (200, "User-agent: *\nAllow: /\n", "allow"), (500, "", "deny"), (401, "", "unavailable")):
        client, _ = client_for(lambda r, s=status, b=body: httpx.Response(s, text=b), VT())
        assert hnet.check_robots(client, "https://h.test/apis/x", "/apis/x")["verdict"] == verdict, status


def test_download_checks_length_and_is_atomic(tmp_path):
    body = b"x" * 5000
    client, _ = client_for(lambda r: httpx.Response(200, content=body, headers={"content-length": "5000"}))
    info = client.download("https://x.test/f.parquet", tmp_path / "f.parquet")
    assert info["bytes"] == 5000 and len(info["sha256"]) == 64 and (tmp_path / "f.parquet").read_bytes() == body
    assert not list(tmp_path.glob(".*tmp"))
    short = lambda r: httpx.Response(200, content=body[:100], headers={"content-length": "5000"})   # noqa: E731
    client, _ = client_for(short)
    with pytest.raises(hnet.FetchError):
        client.download("https://x.test/g.parquet", tmp_path / "g.parquet")
    assert not (tmp_path / "g.parquet").exists() and not list(tmp_path.glob(".*tmp"))
    client, _ = client_for(lambda r: httpx.Response(404))
    with pytest.raises(hnet.NotFound):
        client.download("https://x.test/h.parquet", tmp_path / "h.parquet")


# ---------------------------------------------------------------- store

def test_atomic_write_leaves_nothing_behind_when_the_rename_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        hstore.atomic_write(tmp_path / "a" / "f.json", b"{}")
    assert not (tmp_path / "a" / "f.json").exists() and not list((tmp_path / "a").glob(".*"))


def test_ledger_survives_a_torn_last_line(tmp_path):
    s = hstore.Store(tmp_path)
    s.append_ledger("espn", "nba", {"id": "1", "status": "ok"})
    p = s.ledger_path("espn", "nba")
    with open(p, "ab") as f:
        f.write(b'{"id": "2", "stat')            # killed mid-append
    assert set(s.read_ledger("espn", "nba")) == {"1"}
    s.append_ledger("espn", "nba", {"id": "3", "status": "ok"})
    assert set(s.read_ledger("espn", "nba")) == {"1", "3"}


def test_one_writer_at_a_time_and_stale_temp_files_are_cleaned(tmp_path):
    s = hstore.Store(tmp_path / "h")
    (tmp_path / "h").mkdir()
    (tmp_path / "h" / ".x.json.123.tmp").write_text("partial")
    with s.lock():
        assert not (tmp_path / "h" / ".x.json.123.tmp").exists()
        with pytest.raises(SystemExit, match="another fetch is running"):
            with hstore.Store(tmp_path / "h").lock():
                pass


def test_game_ids_from_the_network_cannot_escape_the_folder(tmp_path):
    s = hstore.Store(tmp_path)
    for bad in ("../../etc/passwd", "12/34", "abc", "", "1" * 40):
        with pytest.raises(ValueError):
            s.game_path("nba", 2024, bad)


def test_a_data_folder_inside_a_repo_must_be_git_ignored(tmp_path):
    if subprocess.run(["git", "--version"], capture_output=True).returncode != 0:
        pytest.skip("git missing")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    with pytest.raises(SystemExit, match="not git-ignored"):
        hstore.ensure_untracked(repo / "data" / "history")
    (repo / ".gitignore").write_text("data/\n")
    hstore.ensure_untracked(repo / "data" / "history")
    hstore.ensure_untracked(tmp_path / "outside" / "history")          # no repo around it: fine


# ---------------------------------------------------------------- espn pieces

def test_day_calendar_uses_the_whitelist_and_rejects_a_wrong_season():
    lg = LEAGUES["nba"]
    data = {"leagues": [{"season": {"year": 2024}, "calendarIsWhitelist": True, "calendar": ["2024-01-15T08:00Z", "2024-01-16T08:00Z"]}]}
    cal = espn.parse_calendar(lg, 2024, data)
    assert [u["date"] for u in cal["units"]] == ["20240115", "20240116"]
    gone = espn.parse_calendar(lg, 1999, data)
    assert gone["units"] == [] and "no such season" in gone["note"]


def test_calendar_without_a_whitelist_walks_the_date_range_and_week_calendars_list_weeks():
    mlb = espn.parse_calendar(LEAGUES["mlb"], 2024, {"leagues": [{"season": {"year": 2024}, "calendarIsWhitelist": False, "calendarStartDate": "2024-03-28T07:00Z",
                                                                 "calendarEndDate": "2024-03-31T07:00Z", "calendar": ["x"]}]})
    assert [u["date"] for u in mlb["units"]] == ["20240328", "20240329", "20240330", "20240331"]
    nfl = espn.parse_calendar(LEAGUES["nfl"], 2023, {"leagues": [FakeEspn({}, {}, kind="week", year=2023).league_obj()]})
    assert [(u["type"], u["week"]) for u in nfl["units"]] == [(2, 1), (2, 2)] and nfl["units"][0]["start"] == "20230906"
    none = espn.parse_calendar(LEAGUES["nfl"], 2023, {"leagues": [{"season": {"year": 2023}}]})
    assert none["source"] == "fallback" and len(none["units"]) == 4 + 18 + 5


def test_the_record_keeps_plays_not_names_and_never_the_hindsight_fields():
    stub = espn.parse_stub(make_event("401000001", "2024-01-16T00:30Z"))
    rec = espn.game_record(LEAGUES["nba"], 2024, stub, make_summary("401000001", 8), "t")
    assert rec["n_plays"] == 8 and rec["night"] == "20240115" and rec["date"] == "2024-01-15" and rec["matchup"] == "BOS @ LAL"
    assert rec["wall_frac"] == 1.0 and rec["clock_frac"] == 1.0 and rec["tag"] == PERSONAL_TAG
    blob = json.dumps(rec)
    for forbidden in ("winprobability", "pickcenter", "boxscore", "headshot", "displayName", "http://x/h.png"):
        assert forbidden not in blob
    assert rec["plays"][0]["participants"][0]["athlete"] == {"id": "1001"}


def test_invalid_and_duplicate_plays_are_dropped_and_counted():
    s = make_summary("401000001", 4)
    s["plays"] += [{"foo": 1}, dict(s["plays"][0])]
    rec = espn.game_record(LEAGUES["nba"], 2024, espn.parse_stub(make_event("401000001", "2024-01-16T00:30Z")), s, "t")
    assert (rec["n_plays"], rec["dropped_invalid"], rec["dropped_duplicate"]) == (4, 1, 1)


def test_football_plays_come_from_the_drives_in_sequence_order():
    summary = json.loads((FIX / "espn_nfl_summary_small.json").read_text())
    plays = espn.flatten_plays(LEAGUES["nfl"], summary)
    seq = [int(p["sequenceNumber"]) for p in plays]
    assert len(plays) == 8 and seq == sorted(seq)
    rec = espn.game_record(LEAGUES["nfl"], 2025, espn.parse_stub(make_event(summary["header"]["id"], "2025-11-30T18:00Z")), summary, "t")
    assert rec["n_plays"] == 8 and "winprobability" not in json.dumps(rec) and "$ref" not in json.dumps(rec)


def test_real_nba_summary_shape_round_trips():
    summary = json.loads((FIX / "espn_nba_summary_small.json").read_text())
    rec = espn.game_record(LEAGUES["nba"], 2026, espn.parse_stub(make_event(summary["header"]["id"], "2025-11-02T02:00Z")), summary, "t")
    assert rec["n_plays"] == 14 and rec["wall_frac"] > 0.9 and rec["home"]["abbr"] and "winprobability" not in json.dumps(rec)


def test_probe_params_and_unit_params():
    assert espn.probe_params(LEAGUES["cbb"], 2024) == {"dates": "20240115", "groups": 50}
    assert espn.probe_params(LEAGUES["nfl"], 2023) == {"dates": "2023", "seasontype": 2, "week": 1}
    assert espn.unit_params(LEAGUES["cfb"], 2023, {"kind": "w", "type": 2, "week": 6}) == {"dates": "2023", "seasontype": 2, "week": 6, "groups": 80, "limit": 1000}


# ---------------------------------------------------------------- fences

def test_fences_match_the_repo_constants():
    assert fenced("nba", "20261020") and fenced("nba", "20261021") and not fenced("nba", "20261019")
    assert fenced("nba", "20260101") and fenced("nba", LOCKED_WINDOW[0]) and fenced("nba", LOCKED_WINDOW[1])
    assert not fenced("nba", "20251211") and not fenced("nba", "20260106") and fenced("nba", None)
    assert fenced("nfl", "20250905") and not fenced("nfl", "20250904") and fenced("mlb", "20250301")
    assert season_fully_fenced("mlb", 2026) and not season_fully_fenced("mlb", 2024)
    assert season_touches_fence("nba", 2026) and not season_touches_fence("nba", 2025) and season_touches_fence("nfl", 2025)


def test_fence_constants_agree_with_the_kaggle_fetch_scripts():
    pytest.importorskip("pyarrow")
    from agent.kaggle import sports_common as sc
    for lg, night in sc.EVAL_FIRST_NIGHT.items():
        if lg in EVAL_FIRST_NIGHT:
            assert EVAL_FIRST_NIGHT[lg] == night, lg


def test_night_is_new_york_minus_five_hours():
    assert night_of("2024-01-16T00:30Z") == "20240115" and night_of("2024-01-16T09:59Z") == "20240115" and night_of("2024-01-16T10:01Z") == "20240116"
    assert night_of("garbage") is None and night_of(None) is None


def test_parse_seasons():
    assert parse_seasons("2025-2023") == [2025, 2024, 2023] and parse_seasons("2019,2024,2022-2021") == [2024, 2022, 2021, 2019]


def test_fenced_nights_are_never_requested(tmp_path):
    ok = make_event("401000001", "2024-01-16T00:30Z")
    eval_night = make_event("401000002", "2026-10-21T00:30Z")           # ET Oct 20 2026: on the first evaluation night
    locked = make_event("401000003", "2025-12-20T01:00Z")               # inside the locked window
    fake = FakeEspn({"20240115": [ok], "20261020": [eval_night], "20251219": [locked]}, {g: make_summary(g, 5) for g in ("401000001", "401000002", "401000003")})
    out, store, _ = run_espn(tmp_path, fake, seasons=(2024,))
    assert fake.summary_calls == {"401000001": 1} and out["skipped_fenced"] >= 2
    assert set(store.read_ledger("espn", "nba")) == {"401000001"}


def test_unfinished_games_and_preseason_are_skipped_by_default(tmp_path):
    evs = [make_event("401000001", "2024-01-16T00:30Z"), make_event("401000002", "2024-01-16T00:30Z", state="in"),
           make_event("401000003", "2024-01-16T00:30Z", stype=1), make_event("401000004", "2024-01-16T00:30Z", state="pre")]
    fake = FakeEspn({"20240115": evs}, {e["id"]: make_summary(e["id"], 5) for e in evs})
    out, _, _ = run_espn(tmp_path, fake)
    assert set(fake.summary_calls) == {"401000001"} and out["skipped_not_final"] == 2 and out["skipped_type"] == 1
    fake2 = FakeEspn({"20240115": evs}, {e["id"]: make_summary(e["id"], 5) for e in evs})
    run_espn(tmp_path / "pre", fake2, types=(1, 2, 3))
    assert set(fake2.summary_calls) == {"401000001", "401000003"}


# ---------------------------------------------------------------- runs, resume, errors

def test_a_run_writes_valid_records_and_a_ledger(tmp_path):
    fake = nba_world()
    out, store, client = run_espn(tmp_path, fake)
    assert out["games_ok"] == 5 and out["stop_reason"] == "complete" and not out["errors"]
    led = store.read_ledger("espn", "nba")
    assert len(led) == 5 and all(e["status"] == "ok" and e["wall"] == 1.0 for e in led.values())
    rec = hstore.read_game(store.game_path("nba", 2024, "401000004"))
    assert rec["n_plays"] == 9 and rec["id"] == "401000004" and all(espn.play_ok(p) for p in rec["plays"])
    assert out["events"] == sum(e["plays"] for e in led.values()) and out["requests"] == client.stats.requests
    assert hstore.verify(store) == []
    gaps = [b - a for a, b in zip(fake.times, fake.times[1:])]
    assert all(g >= 1.0 - 1e-9 for g in gaps)               # 1 request/second overall, whatever the loop does
    assert fake.user_agents == {hnet.UA}


def test_a_second_run_makes_no_requests_at_all(tmp_path):
    fake = nba_world()
    run_espn(tmp_path, fake)
    n = len(fake.log)
    out, store, _ = run_espn(tmp_path, fake)
    assert len(fake.log) == n and out["skipped_done"] == 5 and out["games_ok"] == 0
    assert len(store.read_ledger("espn", "nba")) == 5


def test_kill_midway_then_rerun_gives_no_duplicates_and_no_repeat_requests(tmp_path):
    fake = nba_world(n_games=5)
    fake.kill_after_summaries = 2
    vt = VT()
    fake.vt = vt
    client, _ = client_for(fake, vt)
    store = hstore.Store(tmp_path / "hist")
    with pytest.raises(Killed):
        espn.Run(client, store, now=lambda: NOW, clock=vt.clock, log=lambda s: None).run(["nba"], [2024])
    assert len(store.read_ledger("espn", "nba")) == 2
    # what a real kill can leave: a half-written temp file, a torn ledger line, a stored game whose ledger line never landed
    (store.root / "espn" / "nba" / "2024" / ".401000009.json.gz.1.tmp").write_bytes(b"partial")
    with open(store.ledger_path("espn", "nba"), "ab") as f:
        f.write(b'{"id": "401000003", "stat')
    lost = store.ledger_path("espn", "nba")
    lines = lost.read_bytes().split(b"\n")
    lost.write_bytes(b"\n".join(lines[:1]) + b"\n")           # drop the second finished game's ledger line (file stays)
    fake.kill_after_summaries = None
    with store.lock():                                       # the CLI takes this lock; it also sweeps temp files
        out = espn.Run(client, store, now=lambda: NOW, clock=vt.clock, log=lambda s: None).run(["nba"], [2024])
    assert out["games_ok"] == 3 and out["adopted"] == 1 and not out["errors"]
    led = store.read_ledger("espn", "nba")
    assert set(led) == {f"4010000{i:02d}" for i in range(1, 6)}
    assert hstore.verify(store) == []
    files = sorted((store.root / "espn" / "nba" / "2024").glob("*.json.gz"))
    assert len(files) == 5 and len({f.name for f in files}) == 5
    assert not list(store.root.rglob(".*.tmp"))
    ids = [json.loads(line)["id"] for line in store.ledger_path("espn", "nba").read_text().splitlines() if line.startswith("{") and line.endswith("}")]
    assert len(ids) == len(set(ids))
    assert max(fake.summary_calls.values()) <= 2 and sorted(fake.summary_calls.values()).count(2) == 1     # only the one in flight when killed


def test_transient_failures_are_not_recorded_and_the_next_run_retries_them(tmp_path):
    fake = nba_world()
    fake.summaries["401000002"] = 500
    out, store, _ = run_espn(tmp_path, fake)
    assert out["games_ok"] == 4 and len(out["errors"]) == 1 and "401000002" in out["errors"][0]
    assert "401000002" not in store.read_ledger("espn", "nba")
    fake.summaries["401000002"] = make_summary("401000002", 7)
    out2, store, _ = run_espn(tmp_path, fake)
    assert out2["games_ok"] == 1 and "401000002" in store.read_ledger("espn", "nba")


def test_a_403_stops_the_whole_run(tmp_path):
    fake = nba_world()
    fake.summaries["401000002"] = 403
    out, store, _ = run_espn(tmp_path, fake)
    assert out["stop_reason"] == "blocked" and fake.summary_calls.get("401000002") == 1 and "401000003" not in fake.summary_calls
    assert any("403" in e for e in out["errors"])


def test_the_circuit_breaker_stops_after_consecutive_failures(tmp_path):
    gids = [f"4010{i:04d}" for i in range(20)]
    fake = FakeEspn({"20240115": [make_event(g, "2024-01-16T00:30Z") for g in gids]}, {g: 500 for g in gids})
    out, _, _ = run_espn(tmp_path, fake)
    assert out["stop_reason"] == "breaker" and len(fake.summary_calls) == espn.BREAKER


def test_a_summary_for_another_event_is_never_stored(tmp_path):
    fake = nba_world(n_games=3)
    fake.summaries["401000002"] = make_summary("401000002", 5, header_id="999999")
    out, store, _ = run_espn(tmp_path, fake)
    assert out["games_ok"] == 2 and "401000002" not in store.read_ledger("espn", "nba") and not store.game_path("nba", 2024, "401000002").exists()


def test_a_malformed_summary_is_an_error_for_that_game_not_a_crash(tmp_path):
    fake = nba_world(n_games=3)
    fake.summaries["401000002"]["header"]["competitions"] = [{"competitors": ["oops"]}]
    out, store, _ = run_espn(tmp_path, fake)
    assert out["games_ok"] == 2 and any("401000002" in e for e in out["errors"]) and "401000002" not in store.read_ledger("espn", "nba")


def test_404_and_empty_summaries_are_terminal_states(tmp_path):
    fake = nba_world(n_games=3)
    fake.summaries["401000001"] = 404
    fake.summaries["401000002"] = make_summary("401000002", 0)
    out, store, _ = run_espn(tmp_path, fake)
    led = store.read_ledger("espn", "nba")
    assert (led["401000001"]["status"], led["401000002"]["status"], led["401000003"]["status"]) == ("not_found", "no_plays", "ok")
    assert not store.game_path("nba", 2024, "401000002").exists()
    before = len(fake.log)
    run_espn(tmp_path, fake)
    assert len(fake.log) == before


def test_limits_stop_the_run_cleanly(tmp_path):
    out, store, _ = run_espn(tmp_path, nba_world(), limits=espn.Limits(max_games=2))
    assert out["stop_reason"] == "max_games" and len(store.read_ledger("espn", "nba")) == 2
    out, _, _ = run_espn(tmp_path / "b", nba_world(), limits=espn.Limits(max_requests=3))
    assert out["stop_reason"] == "max_requests" and out["requests"] <= 4
    vt = VT()
    out, _, _ = run_espn(tmp_path / "c", nba_world(), vt=vt, limits=espn.Limits(deadline=vt.clock() + 3))
    assert out["stop_reason"] == "deadline"
    out, _, _ = run_espn(tmp_path / "d", nba_world(), stop_flag=lambda: True)
    assert out["stop_reason"] == "signal" and out["games_ok"] == 0


def test_football_runs_by_week(tmp_path):
    gid = "401772894"
    ev = make_event(gid, "2023-09-08T00:20Z", year=2023, week=1)
    summ = json.loads((FIX / "espn_nfl_summary_small.json").read_text())
    summ["header"]["id"] = gid
    fake = FakeEspn({}, {gid: summ}, kind="week", year=2023, weeks={(2, 1): [ev]})
    out, store, _ = run_espn(tmp_path, fake, leagues=("nfl",), seasons=(2023,))
    assert out["games_ok"] == 1 and store.read_ledger("espn", "nfl")[gid]["plays"] == 8
    assert store.read_json("calendars/espn_nfl_2023.json")["units"][0]["key"] == "w2-01"


def test_a_season_that_espn_does_not_have_is_skipped_quietly(tmp_path):
    fake = nba_world()
    out, store, client = run_espn(tmp_path, fake, seasons=(1990,))
    assert out["games_ok"] == 0 and fake.summary_calls == {} and store.read_json("calendars/espn_nba_1990.json")["units"] == []
    n = len(fake.log)
    run_espn(tmp_path, fake, seasons=(1990,))
    assert len(fake.log) == n                      # the empty calendar is cached too


def test_a_slate_that_fills_the_page_is_an_error_not_a_silent_cut(tmp_path):
    evs = [make_event(f"4020{i:05d}", "2024-01-16T00:30Z") for i in range(espn.SLATE_LIMIT)]
    fake = FakeEspn({"20240115": evs}, {})
    out, _, _ = run_espn(tmp_path, fake)
    assert out["games_ok"] == 0 and any("cut off" in e for e in out["errors"])


def test_recent_days_are_not_fetched_or_cached(tmp_path):
    ev = make_event("401000001", "2026-11-13T00:30Z")                     # ET Nov 12 2026, the day before "now"
    fake = FakeEspn({"20261112": [ev]}, {"401000001": make_summary("401000001", 5)}, year=2027)
    out, _, _ = run_espn(tmp_path, fake, seasons=(2027,))
    assert out["games_ok"] == 0


# ---------------------------------------------------------------- report, verify

def test_report_counts_per_league_and_total(tmp_path, capsys):
    fake = nba_world()
    fake.summaries["401000005"] = make_summary("401000005", 0)
    _, store, _ = run_espn(tmp_path, fake)
    s = hstore.summarize(store, by_season=True)
    nba = s["leagues"]["nba"]
    assert nba["games"] == 4 and nba["no_plays"] == 1 and nba["events"] == s["total_events"] == 6 + 7 + 8 + 9
    assert nba["seasons"] == [2024, 2024] and nba["by_season"]["2024"]["games"] == 4 and nba["wall_games"] == 4
    assert "nba" in hstore.format_report(s) and "of the 30,000,000-event" in hstore.format_report(s)
    assert fetch_history.main(["--report", "--root", str(store.root)]) == 0
    assert "nba" in capsys.readouterr().out
    assert fetch_history.main(["--report", "--json", "--root", str(store.root)]) == 0
    assert json.loads(capsys.readouterr().out)["leagues"]["nba"]["games"] == 4


def test_verify_finds_missing_files_stray_files_and_duplicates(tmp_path):
    _, store, _ = run_espn(tmp_path, nba_world())
    assert hstore.verify(store) == []
    store.game_path("nba", 2024, "401000001").unlink()
    src = store.game_path("nba", 2024, "401000002")
    other = store.game_path("nba", 2023, "401000002")
    other.parent.mkdir(parents=True)
    other.write_bytes(src.read_bytes())
    text = "\n".join(hstore.verify(store))
    assert "401000001 is ok but its file is missing" in text and "stored twice" in text and "header disagrees" in text


def test_a_damaged_game_file_is_refetched_not_trusted(tmp_path):
    fake = nba_world(n_games=3)
    _, store, _ = run_espn(tmp_path, fake)
    p = store.game_path("nba", 2024, "401000001")
    p.write_bytes(b"not gzip")
    led = store.ledger_path("espn", "nba")
    led.write_text("".join(line for line in led.read_text().splitlines(True) if "401000001" not in line))
    out, store, _ = run_espn(tmp_path, fake)
    assert out["games_ok"] == 1 and hstore.read_game(p)["id"] == "401000001"


# ---------------------------------------------------------------- licences

def test_registry_classes_agree_with_terms_json():
    terms = json.loads((Path(license_check.__file__).parent.parent / "docs/datasources/terms.json").read_text())["sources"]
    for key, s in SOURCES.items():
        assert terms[key] == s.terms_class, key
    assert terms["espn"] == "restrictive"


def test_blocked_unverified_and_unacknowledged_sources_refuse_with_the_finding():
    with pytest.raises(SystemExit, match="github.com/robots.txt"):
        gate("nflverse", True)
    with pytest.raises(SystemExit, match="licence for cfbfastR-data"):
        gate("cfbfastr", True)
    with pytest.raises(SystemExit, match="--accept-restrictive"):
        gate("espn", False)
    assert gate("espn", True).key == "espn" and gate("hoopr", True).key == "hoopr"
    with pytest.raises(SystemExit, match="unknown source"):
        gate("sports-reference", True)


def test_every_registered_source_with_restrictive_terms_carries_the_tag():
    m = manifest_licences(False)
    assert all(v["tag"] == PERSONAL_TAG for v in m.values() if v["terms_class"] != "open") and PERSONAL_TAG in SOURCES["espn"].licence


def test_main_refuses_espn_without_the_acknowledgement(tmp_path):
    with pytest.raises(SystemExit, match="--accept-restrictive"):
        fetch_history.main(["--leagues", "nba", "--seasons", "2024", "--root", str(tmp_path / "h")], transport=httpx.MockTransport(nba_world()))
    assert not list((tmp_path / "h").rglob("*.json.gz"))


# ---------------------------------------------------------------- pack, manifest, licence check

def test_pack_is_deterministic_and_the_manifest_passes_the_licence_check(tmp_path):
    _, store, _ = run_espn(tmp_path, nba_world())
    m1 = pack.pack(store)
    sha1 = m1["shards"][0]["sha256"]
    m2 = pack.pack(store)
    assert m2["shards"][0]["sha256"] == sha1 and m1["shards"][0]["games"] == 5
    shard = store.root / m1["shards"][0]["file"]
    lines = gzip.open(shard, "rt").read().splitlines()
    assert [json.loads(x)["id"] for x in lines] == [f"4010000{i:02d}" for i in range(1, 6)]
    row = m2["shards"][0]
    assert row["source"] == "espn" and row["tag"] == PERSONAL_TAG and row["license"]
    terms = json.loads((Path(license_check.__file__).parent.parent / "docs/datasources/terms.json").read_text())
    assert license_check.check([store.root / "MANIFEST.json"], terms) == []
    doc = json.loads((store.root / "MANIFEST.json").read_text())
    doc["shards"][0]["tag"] = ""
    doc["shards"][0]["license"] = "x"
    bad = store.root / "stripped.json"
    bad.write_text(json.dumps(doc))
    assert any(level == "FAIL" for level, _, _ in license_check.check([bad], terms))
    assert doc["licences"]["nflverse"]["status"] == "blocked"


def test_a_packed_shard_is_readable_by_the_existing_tokenizer_reader(tmp_path):
    pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")
    from scripts import noggin_tokens
    _, store, _ = run_espn(tmp_path, nba_world())
    m = pack.pack(store)
    games = list(noggin_tokens.games_of("nba", store.root / m["shards"][0]["file"]))
    assert len(games) == 5 and games[0][0] == "401000001" and games[0][1] == "20240115" and len(games[0][2]) == 6 and (games[0][3], games[0][4]) == ("LAL", "BOS")


# ---------------------------------------------------------------- datasets

class FakeRaw:
    def __init__(self, missing=(), body=b"PAR1" + b"x" * 4000):
        self.calls: list[str] = []
        self.missing, self.body = set(missing), body

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req.url.path)
        if req.url.path == "/robots.txt":
            return httpx.Response(404, text="404: Not Found")
        year = req.url.path.rsplit("_", 1)[1].split(".")[0]
        if year in self.missing:
            return httpx.Response(404)
        return httpx.Response(200, content=self.body, headers={"content-length": str(len(self.body)), "etag": '"abc"'})


def test_dataset_files_are_downloaded_once_with_sha256_and_fenced_seasons_skipped(tmp_path):
    raw = FakeRaw(missing=("2003",))
    client, _ = client_for(raw)
    store = hstore.Store(tmp_path / "h")
    out = datasets.fetch_dataset(client, store, "hoopr_nba", [2026, 2025, 2004, 2003], accepted=True, log=lambda s: None,
                                 facts=lambda p: {"rows": 123, "games": 4})
    assert out["files_ok"] == 2 and out["files_not_found"] == 1 and out["skipped_fenced"] == 1
    led = store.read_ledger("datasets", "hoopr_nba")
    assert led["2025"]["sha256"] and led["2025"]["rows"] == 123 and led["2025"]["etag"] == '"abc"' and led["2025"]["tag"] == PERSONAL_TAG
    assert (store.root / "datasets/hoopr_nba/play_by_play_2025.parquet").read_bytes() == raw.body
    calls = len(raw.calls)
    out2 = datasets.fetch_dataset(client, store, "hoopr_nba", [2026, 2025, 2004, 2003], accepted=True, log=lambda s: None)
    assert len(raw.calls) == calls + 1 and out2["skipped_done"] == 3          # only the robots.txt read repeats
    s = hstore.summarize(store)
    assert s["datasets"]["hoopr_nba"] == {"files": 2, "rows": 246, "gb": s["datasets"]["hoopr_nba"]["gb"]}
    m = pack.write_manifest(store, [], True)
    assert {r["source"] for r in m["shards"]} == {"hoopr"} and license_check.check([store.root / "MANIFEST.json"], json.loads(
        (Path(license_check.__file__).parent.parent / "docs/datasources/terms.json").read_text())) == []


def test_blocked_dataset_sources_never_touch_the_network(tmp_path):
    raw = FakeRaw()
    client, _ = client_for(raw)
    for key, text in (("nflverse_pbp", "blocked"), ("cfbfastr_pbp", "unverified")):
        with pytest.raises(SystemExit, match=text):
            datasets.fetch_dataset(client, hstore.Store(tmp_path), key, [2024], accepted=True)
    with pytest.raises(SystemExit, match="--accept-restrictive"):
        datasets.fetch_dataset(client, hstore.Store(tmp_path), "hoopr_nba", [2024], accepted=False)
    assert raw.calls == []


# ---------------------------------------------------------------- probe and CLI

def test_probe_measures_shape_and_never_samples_fenced_nights(tmp_path):
    fake = nba_world()
    fake.days["20261020"] = [make_event("401000099", "2026-10-21T00:30Z")]
    fake.summaries["401000099"] = make_summary("401000099", 3)
    client, _ = client_for(fake)
    ev = probe.probe(client, ["nba"], [2024], samples=3, log=lambda s: None, per_slate=2)
    row = ev["leagues"]["nba"]["seasons"][0]
    assert row["calendar"]["units"] == 3 and row["calendar"]["whitelist"] is True
    games = probe.season_games(row)
    assert len(games) >= 3 and all(g["plays"] >= 6 and g["wall"] == 1.0 and g["clock"] == 1.0 for g in games)
    st = probe.season_stats(row)
    assert st["with_plays"] == st["sampled"] == len(games) and st["wall"] == 1.0 and st["median_plays"] >= 6
    assert "401000099" not in fake.summary_calls and "median plays" in probe.table(ev)
    assert probe.spread(10, 3) == [1, 5, 8] and probe.spread(2, 5) == [0, 1] and probe.spread(0, 3) == []
    json.dumps(ev)


def test_cli_end_to_end_with_fake_transport(tmp_path, capsys):
    fake = nba_world()
    vt = VT()
    fake.vt = vt
    root = tmp_path / "h"
    rc = fetch_history.main(["--accept-restrictive", "--leagues", "nba", "--seasons", "2024", "--max-games", "4", "--root", str(root)],
                            transport=httpx.MockTransport(fake), sleep=vt.sleep, clock=vt.clock)
    out = capsys.readouterr().out
    assert rc == 0 and "robots.txt" in out and "unavailable" in out and '"stop_reason": "max_games"' in out
    assert len(hstore.Store(root).read_ledger("espn", "nba")) == 4
    assert json.loads((root / "robots_espn.json").read_text())["status"] == 403
    assert list((root / "runs").glob("*.json"))
    rc = fetch_history.main(["--accept-restrictive", "--leagues", "nba", "--seasons", "2024", "--root", str(root)],
                            transport=httpx.MockTransport(fake), sleep=vt.sleep, clock=vt.clock)
    assert rc == 0 and len(hstore.Store(root).read_ledger("espn", "nba")) == 5
    assert max(fake.summary_calls.values()) == 1
    assert fetch_history.main(["--verify", "--root", str(root)]) == 0
    assert fetch_history.main(["--pack", "--root", str(root)]) == 0
    assert fetch_history.main(["--licences", "--root", str(root)]) == 0
    assert "nflverse" in capsys.readouterr().out


def test_cli_exit_codes_for_block_and_errors(tmp_path):
    fake = nba_world(n_games=3)
    fake.summaries["401000002"] = 403
    vt = VT()
    rc = fetch_history.main(["--accept-restrictive", "--leagues", "nba", "--seasons", "2024", "--root", str(tmp_path / "a")],
                            transport=httpx.MockTransport(fake), sleep=vt.sleep, clock=vt.clock)
    assert rc == 3
    fake2 = nba_world(n_games=3)
    fake2.summaries["401000002"] = 500
    rc = fetch_history.main(["--accept-restrictive", "--leagues", "nba", "--seasons", "2024", "--root", str(tmp_path / "b")],
                            transport=httpx.MockTransport(fake2), sleep=vt.sleep, clock=vt.clock)
    assert rc == 1


def test_robots_that_deny_stop_the_run_before_any_game_request(tmp_path):
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /apis/\n")
        raise AssertionError("no request beyond robots.txt expected")
    with pytest.raises(SystemExit, match="disallows"):
        fetch_history.main(["--accept-restrictive", "--leagues", "nba", "--seasons", "2024", "--root", str(tmp_path / "h")],
                           transport=httpx.MockTransport(handler), sleep=VT().sleep)


def test_plan_is_offline_and_lists_every_league(capsys):
    assert fetch_history.main(["--plan", "--seasons", "2025-2016"]) == 0
    out = capsys.readouterr().out
    assert all(lg in out for lg in ("nba", "cbb", "wcbb", "nfl", "cfb", "nhl", "mlb")) and "ESTIMATE" in out and "total" in out
