"""Feed-arrival logger (scripts/feedlag_logger.py): rate limits, backoff, rows on disk. Fake feeds, fake clock, no network."""
import gzip
import io
import json
import urllib.error

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
    assert "would follow 2 feeds" in capsys.readouterr().out
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

    def urlopen(req, timeout=None):
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
    def boom(req, timeout=None):
        raise urllib.error.URLError("no route")
    monkeypatch.setattr(fl.urllib.request, "urlopen", boom)
    r = fl.UrlFetcher().get("https://example.invalid/x")
    assert r.status == 0 and r.err == "URLError"
    monkeypatch.setattr(fl.urllib.request, "urlopen", lambda req, timeout=None: FakeResponse(b"<html>nope"))
    assert fl.UrlFetcher().get("https://example.invalid/x").err == "bad_body"
    monkeypatch.setattr(fl.urllib.request, "urlopen", lambda req, timeout=None: FakeResponse(b"x" * (fl.MAX_BYTES + 1)))
    assert fl.UrlFetcher().get("https://example.invalid/x").err == "too_big"
    err = urllib.error.HTTPError("https://x", 429, "Too Many", {"Retry-After": "7"}, io.BytesIO(b""))
    monkeypatch.setattr(fl.urllib.request, "urlopen", lambda req, timeout=None: (_ for _ in ()).throw(err))
    r = fl.UrlFetcher().get("https://example.invalid/x")
    assert r.status == 429 and r.headers["retry-after"] == "7"
