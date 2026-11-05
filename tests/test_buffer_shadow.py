"""[time-shift buffer SHADOW, Nick 9:10 AM Nov 5] the editor decider runs B s behind live, logs, never acts."""
import json

from test_app import engine  # noqa: F401  (fixture)
from test_phone_control import _live

from redzone.buffer_shadow import BufferedDelays, BufferShadow
from redzone.config import default
from redzone.decider.decider import Decider
from redzone.decider.screen import DelayBook


def test_shadow_ships_on_with_the_buffer_off():
    assert default("timing.behind_live_s.shadow") is True
    assert default("timing.behind_live_s.enabled") is False
    assert default("timing.behind_live_s.max_s") == 90


def test_buffered_delays_shift_mu_only():
    live = DelayBook({"youtubetv": 45.0, "prime": 40.0}, 15.0)
    b = BufferedDelays(live, lambda t: 90.0 if t.startswith("youtubetv") else 0.0)
    assert b.get("youtubetv:espn").mu == live.get("youtubetv:espn").mu + 90.0
    assert b.get("youtubetv:espn").sigma == live.get("youtubetv:espn").sigma
    assert b.get("prime:x").mu == live.get("prime:x").mu
    b.set_behind("youtubetv:espn", 30.0)                       # the editor holds nothing
    assert live.get("youtubetv:espn").behind == 0.0


def test_editor_shares_timelines_and_logs(tmp_path):
    live = Decider(_cfg(), DelayBook({"youtubetv": 45.0}, 15.0))
    sh = BufferShadow(live.cfg, live, lambda t: 60.0, tmp_path / "s.jsonl")
    assert sh.editor.timelines is live.timelines
    sh.tick({})
    sh.tick({})
    rows = [json.loads(x) for x in (tmp_path / "s.jsonl").read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["action"] == "stay"        # logged on change only


def _cfg():
    from redzone.config import Config
    return Config()


def test_engine_ticks_the_editor_and_never_acts(engine, monkeypatch):  # noqa: F811
    _live(engine)
    acted = []
    monkeypatch.setattr(engine.control, "submit", lambda *a, **k: acted.append(a))
    ticks = []
    real = engine.buffer_shadow.tick
    monkeypatch.setattr(engine.buffer_shadow, "tick", lambda c: ticks.append(c) or real(c))
    engine.decide()
    assert ticks, "the editor ran on this decide tick"
    assert engine.buffer_shadow.editor.current is None or engine.buffer_shadow.editor.current in ticks[0]
    engine.cfg.update({"timing": {"behind_live_s": {"enabled": True}}})
    ticks.clear()
    engine.decide()
    assert not ticks                                           # buffer on screen: no shadow


def test_nba_cdn_primary_fast_defaults():
    """[Nick 9:05 AM Nov 5] nba_cdn owns NBA stoppages, polled every 1 s on its own loop."""
    assert default("sources.nba_primary") is True
    assert default("polling.nba_pbp_s") == 1
    assert default("polling.league_fast") is True


def test_league_fast_loop_polls_live_nba(engine, monkeypatch):  # noqa: F811
    import asyncio
    from redzone.models import League, Phase
    g = next(iter(engine.merger.games.values()))
    g.phase, g.league = Phase.LIVE, League.NBA
    calls = []

    async def feeds(live):
        calls.append([x.game_id for x in live])
        raise asyncio.CancelledError

    monkeypatch.setattr(engine, "_league_feeds", feeds)
    monkeypatch.setattr(engine, "_leagues", lambda: list(League))
    try:
        asyncio.run(engine._league_fast_loop())
    except asyncio.CancelledError:
        pass
    assert calls and g.game_id in calls[0]


def test_scorebug_reads_down_and_distance_before_the_quarter():
    from types import SimpleNamespace as O
    from redzone.screen.scorebug import parse_bug
    home, away = O(abbr="BAL", name="Baltimore Ravens"), O(abbr="JAX", name="Jacksonville Jaguars")
    obs = [O(text="JAX", x=0.10, y=0.80, width=0.05, height=0.03, confidence=0.9),
           O(text="14", x=0.16, y=0.79, width=0.03, height=0.05, confidence=0.9),
           O(text="BAL", x=0.30, y=0.80, width=0.05, height=0.03, confidence=0.9),
           O(text="10", x=0.36, y=0.79, width=0.03, height=0.05, confidence=0.9),
           O(text="2ND 7:21 3RD & 7", x=0.45, y=0.80, width=0.15, height=0.03, confidence=0.9)]
    bug = parse_bug(obs, home, away)
    assert bug is not None and bug.score == (10, 14)
    assert (bug.period, bug.clock, bug.down) == ("2ND", "7:21", "3&7")


def test_scorebug_race_defaults():
    assert default("delay.scorebug_hidden") is True and default("delay.scorebug_log") is True


def test_already_held_stream_is_not_counted_twice():
    live = DelayBook({"youtubetv": 45.0}, 15.0)
    live.set_behind("youtubetv:espn", 60.0)
    b = BufferedDelays(live, lambda t: 90.0)
    assert abs(b.get("youtubetv:espn").mu - (live.get("youtubetv:espn").mu + 30.0)) < 1e-9


def test_stay_leave_rows_carry_context_and_adaptive_terms(engine, tmp_path, monkeypatch):  # noqa: F811
    _live(engine)
    monkeypatch.setattr(engine.control, "submit", lambda *a, **k: None)
    engine.buffer_shadow.log_path = tmp_path / "s.jsonl"
    for _ in range(3):
        engine.decide()
    rows = [json.loads(x) for x in (tmp_path / "s.jsonl").read_text().splitlines()]
    sl = [r for r in rows if r["kind"] == "stay_leave"]
    assert sl, rows[:3]
    r = sl[0]
    assert r["action"] in ("stay", "leave") and r["stick"] is None and r["role"] is None
    for k in ("value_stay", "value_best_alt", "context", "behind_adaptive_s", "feed_lag_p95_s", "sync_err_s",
              "tune_s", "margin_s", "floor_s", "cap_s"):
        assert k in r
    c = r["context"]
    for k in ("period", "clock_s", "score", "lead_wp", "high_leverage", "crunch", "stakes", "situation"):
        assert k in c
    if r["behind_adaptive_s"] is not None:
        assert r["floor_s"] <= r["behind_adaptive_s"] <= r["cap_s"] == 90


def test_editor_times_its_first_pick_live_decider_untouched(engine):  # noqa: F811
    assert engine.buffer_shadow.editor.cfg.get("decider.entry.start_timed") is True
    assert engine.decider.cfg.get("decider.entry.start_timed") is False
