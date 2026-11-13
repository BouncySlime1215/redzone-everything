"""Feed-arrival logger (scripts/feedlag_logger.py): rate limits, backoff, rows on disk. Fake feeds, fake clock, no network."""
import gzip
import io
import json
import ssl
import sys
import unittest.mock as mock
import urllib.error
from datetime import datetime

import pytest

from redzone.lab import feedlag
from redzone.models import League
from redzone.sources import espn, leagues
from scripts import feedlag_logger as fl
from tests.feedlag_fakes import (NIGHT_START, FakeFetcher, FakeTime, cdn_action, cdn_pbp, espn_play, espn_scoreboard,
                                 espn_summary, nba_scoreboard)

T0 = NIGHT_START
SLATE = "2026-11-12"
SB_URL = espn.scoreboard_url(League.NBA, "20261112")
SUM_URL = espn.summary_url(League.NBA, "100")
PBP_URL = leagues.NBA_PBP.format(id="0022600230")
ESPN_LAG, CDN_LAG = 25.0, 8.0          # how late each fake feed publishes a row


def plays_at(now):
    """One play every 10 s since T0-120; ESPN shows each ESPN_LAG after its stamp, the NBA feed CDN_LAG after."""
    stamps = [T0 - 120 + 10 * i for i in range(60)]
    espn_ = [espn_play(f"100{i:03d}", "Personal Foul" if i % 3 == 0 else "Jump Shot", s, scoring=i % 2 == 0)
             for i, s in enumerate(stamps) if s + ESPN_LAG <= now]
    cdn = [cdn_action(i + 1, s, "foul" if i % 3 == 0 else "2pt", "personal" if i % 3 == 0 else "Jump Shot",
                      shot=None if i % 3 == 0 else "Made", edited=s + CDN_LAG) for i, s in enumerate(stamps) if s + CDN_LAG <= now]
    return espn_, cdn


def world(ft, state="in", cdn_from=0.0, **over):
    routes = {
        SB_URL: lambda: espn_scoreboard([("100", state, "CHA", "PHX", T0 - 3600)]),
        leagues.NBA_SCOREBOARD: nba_scoreboard([("0022600230", "CHA", "PHX")]),
        SUM_URL: lambda: espn_summary(plays_at(ft.clock())[0]),
        PBP_URL: lambda: (cdn_pbp(plays_at(ft.clock())[1]) if ft.elapsed() >= cdn_from else fl.Resp(403)),
    }
    routes.update(over)
    return routes


def poller(tmp_path, ft, routes, **kw):
    s = fl.Settings(out_dir=tmp_path, **{k: v for k, v in kw.items() if k in fl.Settings.__dataclass_fields__})
    f = FakeFetcher(ft, routes)
    p = fl.Poller(s, fetcher=f, clock=ft.clock, mono=ft.mono, sleep=ft.sleep, say=lambda m: None)
    return p, f


def events(tmp_path):
    return [json.loads(x) for x in (tmp_path / f"{SLATE}.jsonl").read_text().splitlines()]


def test_settings_cannot_be_made_less_polite_than_the_floor():
    s = fl.Settings(interval=1.0, max_rps=50.0)
    assert s.interval == feedlag.FLOOR_INTERVAL == 10.0 and s.max_rps == feedlag.HARD_RPS == 4.0
    assert fl.Settings(interval=30.0, max_rps=0.5).interval == 30.0


def test_the_user_agent_names_no_person_and_no_project():
    ua = fl.USER_AGENT.lower()
    for word in ("nick", "matta", "redzone", "noggin", "@", "http", "mozilla"):
        assert word not in ua


@pytest.mark.parametrize("status,host,kind", [
    (200, "site.api.espn.com", "ok"), (304, "site.api.espn.com", "unchanged"), (404, "site.api.espn.com", "missing"),
    (403, "nba-prod.s3.amazonaws.com", "missing"), (403, "site.api.espn.com", "host"), (429, "x", "host"),
    (503, "x", "host"), (0, "x", "host"), (400, "x", "missing"),
])
def test_classify(status, host, kind):
    assert fl.classify(status, host) == kind


def test_rows_are_logged_with_their_arrival_window_and_every_limit_holds(tmp_path):
    ft = FakeTime()
    p, f = poller(tmp_path, ft, world(ft), max_rps=2.0)
    p.run(lambda: ft.elapsed() > 180)
    assert f.conditional[SB_URL] is False and f.conditional[leagues.NBA_SCOREBOARD] is False     # a 304 would hide the games
    assert f.conditional[SUM_URL] is True and f.conditional[PBP_URL] is True
    ev = events(tmp_path)
    rows = [e for e in ev if e["ev"] == "row"]
    by_src = {s: [e for e in rows if e["src"] == s] for s in feedlag.FEED_SOURCES}
    assert len(by_src["espn_summary"]) >= 8 and len(by_src["nba_cdn"]) >= 8
    assert {e["ev"] for e in ev} >= {"start", "track", "game", "row", "clock"} - {"start"}
    base = {(e["src"]): e["baseline"] for e in ev if e["ev"] == "game"}
    assert base["espn_summary"] > 0 and base["nba_cdn"] > 0                  # history was remembered, not logged
    for e in rows:
        assert e["prev"] is not None and 0 < e["t"] - e["prev"] <= 11.5     # published inside one poll window
        lag = {"espn_summary": ESPN_LAG, "nba_cdn": CDN_LAG}[e["src"]]
        assert 0 <= (e["t"] - e["stamp"]) - lag <= 12                       # first seen within one gap of publication
        assert abs(((e["t"] + e["prev"]) / 2 - e["stamp"]) - lag) <= 6.5    # the midpoint is within half a gap
    assert [e["row"] for e in by_src["nba_cdn"]] == sorted({e["row"] for e in by_src["nba_cdn"]}, key=int)
    assert len({(e["src"], e["row"]) for e in rows}) == len(rows)           # never logged twice
    # per feed: never two requests less than 10 s apart
    for url in (SUM_URL, PBP_URL):
        ts = f.urls(url)
        assert len(ts) > 10 and all(b - a >= 10.0 - 1e-9 for a, b in zip(ts, ts[1:]))
    # across everything: never closer than 1/max_rps
    allt = sorted(t for t, _ in f.calls)
    assert all(b - a >= 0.5 - 1e-9 for a, b in zip(allt, allt[1:]))


def test_the_global_cap_wins_over_the_per_feed_interval_when_many_games_run(tmp_path):
    ft = FakeTime()
    ids = [str(100 + i) for i in range(8)]
    routes = {SB_URL: lambda: espn_scoreboard([(i, "in", "CHA", "PHX", T0 - 3600) for i in ids]),
              leagues.NBA_SCOREBOARD: nba_scoreboard([])}
    for i in ids:
        routes[espn.summary_url(League.NBA, i)] = espn_summary([])
    p, f = poller(tmp_path, ft, routes, max_rps=0.5, sources=("espn_summary",), max_games=12)   # 8 feeds, but only 1 per 2 s
    p.run(lambda: ft.elapsed() > 100)
    allt = sorted(t for t, _ in f.calls)
    assert all(b - a >= 2.0 - 1e-9 for a, b in zip(allt, allt[1:]))
    for i in ids:
        ts = f.urls(f"event={i}")
        assert all(b - a >= 10.0 - 1e-9 for a, b in zip(ts, ts[1:]))


def test_429_backs_the_whole_host_off_with_doubling_delays_then_recovers(tmp_path):
    ft = FakeTime()
    state = {"bad": True}
    summary = lambda: fl.Resp(429, None, {"retry-after": "5"}) if state["bad"] else espn_summary(plays_at(ft.clock())[0])   # noqa: E731
    p, f = poller(tmp_path, ft, world(ft, **{SUM_URL: summary, SB_URL: lambda: espn_scoreboard([("100", "in", "CHA", "PHX", T0 - 3600)])}),
                  sources=("espn_summary",))
    p.run(lambda: ft.elapsed() > 80)
    errs = [e for e in events(tmp_path) if e["ev"] == "err"]
    assert [e["backoff"] for e in errs][:2] == [30.0, 60.0]                   # 30 s, then double
    espn_calls = [t for t, u in f.calls if "espn.com" in u and "summary" in u]
    assert all(b - a >= 29.9 for a, b in zip(espn_calls, espn_calls[1:]))      # nothing hit the host during the backoff
    state["bad"] = False
    p.run(lambda: ft.elapsed() > 400)
    assert p.host_fails["site.api.espn.com"] == 0                              # a success resets the backoff
    assert [e for e in events(tmp_path) if e["ev"] == "row"]


def test_a_nba_feed_file_that_is_not_up_yet_backs_off_that_game_only(tmp_path):
    ft = FakeTime()
    p, f = poller(tmp_path, ft, world(ft, state="pre", cdn_from=120.0), lead=7200.0)     # file appears 2 minutes in
    p.run(lambda: ft.elapsed() > 240)
    cdn = f.urls("playbyplay")
    espn_ = f.urls("summary")
    assert 3 <= len([t for t in cdn if t - f.calls[0][0] < 120]) <= 6          # 15 s, 30 s, 60 s, 60 s...: few asks while missing
    assert len(espn_) > len(cdn) / 2                                           # ESPN kept its 10 s rhythm
    assert not [e for e in events(tmp_path) if e["ev"] == "err"]               # a missing file is not a host error
    first_cdn = [e for e in events(tmp_path) if e["ev"] == "row" and e["src"] == "nba_cdn"][0]
    assert first_cdn["prev"] is not None and first_cdn["t"] - first_cdn["prev"] <= 61   # bounded by the last miss


def test_a_failed_nba_scoreboard_lookup_does_not_drop_a_running_feed(tmp_path):
    ft = FakeTime()
    state = {"down": False}
    routes = world(ft, **{leagues.NBA_SCOREBOARD: lambda: fl.Resp(503) if state["down"] else nba_scoreboard([("0022600230", "CHA", "PHX")])})
    p, f = poller(tmp_path, ft, routes, discover_every=30.0)
    p.run(lambda: ft.elapsed() > 40)
    assert (feedlag.SRC_CDN, "100") in p.jobs
    state["down"] = True
    p.run(lambda: ft.elapsed() > 400)
    assert (feedlag.SRC_CDN, "100") in p.jobs and not [e for e in events(tmp_path) if e["ev"] == "drop"]
    assert [e for e in events(tmp_path) if e["ev"] == "err" and e["src"] == "nba_scoreboard"]


def test_a_restart_appends_and_never_logs_a_row_twice(tmp_path):
    ft = FakeTime()
    p, _ = poller(tmp_path, ft, world(ft))
    p.run(lambda: ft.elapsed() > 120)
    first = [e for e in events(tmp_path) if e["ev"] == "row"]
    p2, _ = poller(tmp_path, ft, world(ft))                                    # same night, same folder, new process
    p2.run(lambda: ft.elapsed() > 240)
    rows = [e for e in events(tmp_path) if e["ev"] == "row"]
    assert len(rows) > len(first) and rows[:len(first)] == first               # appended, earlier lines untouched
    assert len({(e["src"], e["game"], e["row"]) for e in rows}) == len(rows)


def test_exit_when_done_stops_after_the_last_game_and_a_finished_game_gets_a_grace_period(tmp_path):
    ft = FakeTime()
    state = {"s": "in"}
    routes = world(ft, **{SB_URL: lambda: espn_scoreboard([("100", state["s"], "CHA", "PHX", T0 - 3600)])})
    p, f = poller(tmp_path, ft, routes, exit_when_done=True, discover_every=60.0, post_grace=200.0)
    p.run(lambda: ft.elapsed() > 100)
    assert p.jobs and not p.done
    state["s"] = "post"
    why = p.run(lambda: ft.elapsed() > 1000)
    assert why == "done"
    end = [e for e in events(tmp_path) if e["ev"] == "drop"]
    assert len(end) == 2 and ft.elapsed() > 150                                # polled on through the grace period first


def test_the_plan_names_every_game_with_its_phase_start_and_reason(tmp_path):
    ft = FakeTime()                                                                 # 23:00 ET
    routes = {SB_URL: espn_scoreboard([("1", "post", "AAA", "BBB", T0 - 4 * 3600), ("2", "in", "CHA", "PHX", T0 - 3600),
                                       ("3", "pre", "EEE", "FFF", T0 + 10 * 60), ("4", "pre", "GGG", "HHH", T0 + 3 * 3600)]),
              leagues.NBA_SCOREBOARD: nba_scoreboard([("0022600230", "CHA", "PHX")])}
    p, _ = poller(tmp_path, ft, routes)
    p.dry = True
    p.discover()
    plan = {e["game"]: e for e in p.plan}
    assert [plan[g]["phase"] for g in "1234"] == ["post", "live", "pre", "pre"]
    assert [plan[g]["follow"] for g in "1234"] == [False, True, True, False]
    assert plan["1"]["why"] == "final, not followed" and plan["2"]["why"] == "in progress"
    assert "tips in 10 min" in plan["3"]["why"] and "tips in 180 min" in plan["4"]["why"] and "15 min before tip" in plan["4"]["why"]
    assert {k for k in p.jobs} == {("espn_summary", "2"), ("nba_cdn", "2"), ("espn_summary", "3")}
    assert all(e["start"] for e in plan.values())


def test_a_run_started_at_5pm_picks_up_the_evening_games_and_exit_when_done_waits_for_them(tmp_path):
    ft = FakeTime(datetime(2026, 11, 12, 17, 0, tzinfo=feedlag.EASTERN).timestamp())
    tip = ft.wall + 2 * 3600
    state = {"s": "pre"}
    routes = {SB_URL: lambda: espn_scoreboard([("100", state["s"], "CHA", "PHX", tip), ("101", "pre", "MIN", "DAL", tip + 3600)]),
              leagues.NBA_SCOREBOARD: nba_scoreboard([("0022600230", "CHA", "PHX")]),
              SUM_URL: espn_summary([]), PBP_URL: lambda: fl.Resp(404)}
    p, _ = poller(tmp_path, ft, routes, exit_when_done=True)
    assert p.run(lambda: ft.wall > tip - 20 * 60) == "signal"                        # all games still scheduled: no early exit
    assert not p.jobs and not p.done
    assert p.run(lambda: ft.wall > tip + 30 * 60) == "signal" and not p.done        # still running half an hour after the tip
    tracked = [e for e in events(tmp_path) if e["ev"] == "track" and e["game"] == "100"]
    assert {e["src"] for e in tracked} == {"espn_summary", "nba_cdn"}
    assert all(-900 <= e["t"] - tip <= -600 for e in tracked)                       # found 10 to 15 minutes before the tip
    assert ("espn_summary", "101") not in p.jobs                                    # the 2nd game (tip +60 min) waits for its own lead
    p.run(lambda: ft.wall > tip + 55 * 60)
    assert ("espn_summary", "101") in p.jobs


def test_a_day_with_no_games_ends_at_once_when_asked(tmp_path):
    ft = FakeTime()
    p, _ = poller(tmp_path, ft, {SB_URL: espn_scoreboard([])}, exit_when_done=True)
    assert p.run(lambda: ft.elapsed() > 100) == "done"


def test_a_bug_in_one_parse_is_logged_and_the_night_goes_on(tmp_path, monkeypatch):
    ft = FakeTime()
    p, _ = poller(tmp_path, ft, world(ft))
    real = feedlag.cdn_rows
    calls = {"n": 0}

    def flaky(d):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("boom")
        return real(d)
    monkeypatch.setattr(feedlag, "cdn_rows", flaky)
    p.run(lambda: ft.elapsed() > 150)
    assert [e for e in events(tmp_path) if e["ev"] == "err" and "boom" in e["what"]]
    assert [e for e in events(tmp_path) if e["ev"] == "row" and e["src"] == "nba_cdn"]


def test_college_basketball_is_espn_only_and_capped(tmp_path):
    ft = FakeTime()
    cbb_sb = espn.scoreboard_urls(League.CBB, None, "20261112")[0]
    ids = [str(900 + i) for i in range(6)]
    data = espn_scoreboard([(i, "in", "UNC", "DUKE", T0 - 3600) for i in ids])
    for ev in data["events"]:                                                    # make them ranked so they are kept
        for c in ev["competitions"][0]["competitors"]:
            c["curatedRank"] = {"current": 5}
    routes = {cbb_sb: data}
    for i in ids:
        routes[espn.summary_url(League.CBB, i)] = espn_summary([])
    p, f = poller(tmp_path, ft, routes, leagues=("cbb",), cbb_max_games=3)
    p.run(lambda: ft.elapsed() > 40)
    assert len(p.jobs) == 3 and all(k[0] == "espn_summary" for k in p.jobs)
    assert not f.urls("nba-prod")


def test_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    ft = FakeTime()
    f = FakeFetcher(ft, world(ft))
    monkeypatch.setattr(fl, "UrlFetcher", lambda *a, **k: f)
    assert fl.main(["--dry-run", "--out-dir", str(tmp_path / "out"), "--date", SLATE]) == 0
    out = capsys.readouterr().out
    assert "1 games on the scoreboard, would follow 1 now" in out and "in progress -> espn_summary+nba_cdn" in out
    assert not list((tmp_path / "out").glob("*.jsonl"))


# --- the HTTP layer ------------------------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, body, headers=None, status=200):
        self._b, self.headers, self.status = body, headers or {}, status

    def read(self, n=-1):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_fetcher_sends_conditional_headers_and_reads_gzip(monkeypatch):
    seen = []
    body = gzip.compress(json.dumps({"a": 1}).encode())

    def urlopen(req, timeout=None, context=None):
        seen.append(dict(req.header_items()))
        if len(seen) == 1:
            return FakeResponse(body, {"Content-Encoding": "gzip", "ETag": '"abc"', "Last-Modified": "Fri, 13 Nov 2026 04:00:00 GMT"})
        raise urllib.error.HTTPError(req.full_url, 304, "Not Modified", {"ETag": '"abc"'}, io.BytesIO(b""))
    monkeypatch.setattr(fl.urllib.request, "urlopen", urlopen)
    f = fl.UrlFetcher()
    r1 = f.get("https://example.invalid/x")
    assert r1.status == 200 and r1.data == {"a": 1}
    r2 = f.get("https://example.invalid/x")
    assert r2.status == 304 and r2.data is None
    assert seen[1]["If-none-match"] == '"abc"' and seen[1]["If-modified-since"].startswith("Fri, 13 Nov")
    assert seen[0]["User-agent"] == fl.USER_AGENT and "gzip" in seen[0]["Accept-encoding"]


def test_fetcher_turns_failures_into_status_zero_not_exceptions(monkeypatch):
    def boom(req, timeout=None, context=None):
        raise urllib.error.URLError("no route")
    monkeypatch.setattr(fl.urllib.request, "urlopen", boom)
    r = fl.UrlFetcher().get("https://example.invalid/x")
    assert r.status == 0 and r.err.startswith("URLError: ") and "no route" in r.err
    monkeypatch.setattr(fl.urllib.request, "urlopen", lambda req, timeout=None, context=None: FakeResponse(b"<html>nope"))
    assert fl.UrlFetcher().get("https://example.invalid/x").err == "bad_body"
    monkeypatch.setattr(fl.urllib.request, "urlopen", lambda req, timeout=None, context=None: FakeResponse(b"x" * (fl.MAX_BYTES + 1)))
    assert fl.UrlFetcher().get("https://example.invalid/x").err == "too_big"
    err = urllib.error.HTTPError("https://x", 429, "Too Many", {"Retry-After": "7"}, io.BytesIO(b""))
    monkeypatch.setattr(fl.urllib.request, "urlopen", lambda req, timeout=None, context=None: (_ for _ in ()).throw(err))
    r = fl.UrlFetcher().get("https://example.invalid/x")
    assert r.status == 429 and r.headers["retry-after"] == "7"


# --- a failed scoreboard read is never an empty slate -----------------------------------------------------------

CERT_ERROR = urllib.error.URLError(ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"))


def raise_cert_error(req, timeout=None, context=None):
    raise CERT_ERROR


def test_certifi_bundle_is_used_when_importable_else_the_default_context(monkeypatch):
    seen = []
    monkeypatch.setattr(fl.ssl, "create_default_context", lambda *a, **k: seen.append(k) or "ctx")
    monkeypatch.setitem(sys.modules, "certifi", type("Fake", (), {"where": staticmethod(lambda: "/some/cacert.pem")}))
    assert fl.make_ssl_context() == ("ctx", "certifi") and seen[-1] == {"cafile": "/some/cacert.pem"}
    monkeypatch.setitem(sys.modules, "certifi", None)                          # not installed: ImportError
    assert fl.make_ssl_context() == ("ctx", "default") and seen[-1] == {}
    assert fl.UrlFetcher().ca == "default"                                      # the fetcher carries the choice


def test_a_certificate_failure_keeps_its_class_and_message():
    with mock.patch.object(fl.urllib.request, "urlopen", raise_cert_error):
        r = fl.UrlFetcher().get("https://example.invalid/x")
    assert r.status == 0 and "URLError" in r.err and "CERTIFICATE_VERIFY_FAILED" in r.err


def test_an_unreadable_scoreboard_is_loud_in_a_real_run_and_never_an_empty_plan(tmp_path, monkeypatch):
    monkeypatch.setattr(fl.urllib.request, "urlopen", raise_cert_error)
    ft = FakeTime()
    said = []
    s = fl.Settings(out_dir=tmp_path)
    p = fl.Poller(s, clock=ft.clock, mono=ft.mono, sleep=ft.sleep, say=said.append)      # the real fetcher, a failing opener
    p.run(lambda: ft.elapsed() > 150)
    assert p.plan == [] and not p.jobs and p.scoreboard_error and "CERTIFICATE_VERIFY_FAILED" in p.scoreboard_error
    errs = [e for e in events(tmp_path) if e["ev"] == "err" and e["src"] == "scoreboard"]
    assert errs and "CERTIFICATE_VERIFY_FAILED" in errs[0]["what"] and "URLError" in errs[0]["what"]
    assert any(m.startswith("SCOREBOARD UNREADABLE") for m in said)                      # said at once, not only at the minute
    assert any("SCOREBOARD UNREADABLE (not an empty slate)" in m and m.startswith("[") for m in said)    # the status line
    assert not p.done                                                                    # and --exit-when-done must not fire


def test_dry_run_on_an_unreadable_scoreboard_prints_the_error_and_exits_nonzero(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(fl.urllib.request, "urlopen", raise_cert_error)
    assert fl.main(["--dry-run", "--date", SLATE, "--out-dir", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "scoreboard unreadable (this is NOT an empty slate)" in out and "URLError" in out
    assert "CERTIFICATE_VERIFY_FAILED" in out and "certifi" in out
    assert "games on the scoreboard" not in out


def test_a_scoreboard_that_answers_without_events_is_an_error_not_an_empty_day(tmp_path):
    ft = FakeTime()
    p, _ = poller(tmp_path, ft, {SB_URL: {"unexpected": True}}, exit_when_done=True)
    p.run(lambda: ft.elapsed() > 130)
    assert p.scoreboard_error and "without an events list" in p.scoreboard_error and not p.done


def test_a_genuinely_empty_day_is_reported_as_such_with_exit_zero(tmp_path, monkeypatch, capsys):
    ft = FakeTime()
    monkeypatch.setattr(fl, "UrlFetcher", lambda *a, **k: FakeFetcher(ft, {SB_URL: espn_scoreboard([])}))
    assert fl.main(["--dry-run", "--date", SLATE, "--out-dir", str(tmp_path)]) == 0
    assert "no games on the scoreboard for this date (the scoreboard was read fine)" in capsys.readouterr().out


def test_a_scoreboard_that_recovers_clears_the_alarm(tmp_path):
    ft = FakeTime()
    state = {"bad": True}
    routes = {SB_URL: lambda: fl.Resp(0, None, {}, "URLError: boom") if state["bad"] else espn_scoreboard([])}
    p, _ = poller(tmp_path, ft, routes)
    p.run(lambda: ft.elapsed() > 100)
    assert "boom" in p.scoreboard_error
    state["bad"] = False
    p.run(lambda: ft.elapsed() > 400)
    assert p.scoreboard_error is None
