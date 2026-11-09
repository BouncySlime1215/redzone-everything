"""[P1-REVIEW] The fresh Rule #1 review of the merged P1 sprint: every finding's regression test
(docs/agents/audit/2026-11-08-rule1-review.md), plus Nick's walk landings in the buffered-jump simulator."""
import asyncio
import dataclasses
import json
import time

import pytest
from test_app import engine  # noqa: F401  (fixture)
from test_control import FakeStick, FakeTuner, ctl, two  # noqa: F401  (fixture)
from test_landing_bars import MU, WALK, cand, decider, fresh, made
from test_p1_rootcause import _engine
from test_switch_path_audit import _jump, _two_live

from redzone import config
from redzone.channels import ChannelMap
from redzone.decider import buffered as bj
from redzone.decider.cuts import DeadTracker, EntryPlan
from redzone.decider.decider import Decision, State
from redzone.models import League


def _plan(ok, rewind=0.0):
    return EntryPlan(ok=ok, rewind_s=rewind, landing=None, why="test")


# ----- 01: whistle-timed leagues are re-planned at the flip ----------------------------------------------------------

@pytest.mark.parametrize("league", [League.NBA, League.NHL, League.MLB])
def test_01_whistle_timed_league_is_replanned_at_the_flip(engine, monkeypatch, league):  # noqa: F811
    """Repro: the flip can come 25 s after the decision and only NFL/CFB re-checked the landing."""
    a, b = _two_live(engine, monkeypatch)
    w = dataclasses.make_dataclass("W", ["target"])
    engine.where[b] = w("youtubetv:b")
    monkeypatch.setattr(engine.feeds, "stale", lambda g: False)
    monkeypatch.setattr(engine.control, "deadline_cost", lambda t: 3.0)
    engine.scored[b] = dataclasses.replace(engine.scored[b], league=league, crunch=False, crunch_now=False)
    engine.decider.timeline(b, league)
    monkeypatch.setattr(engine.decider, "entry_plan", lambda *x, **k: _plan(False))
    d = Decision(time.time(), "switch", b, "better", State.WATCHING, kind="better")
    assert engine._entry_valid(d, "youtubetv:b") is False


def test_01_the_flip_rechecks_on_the_bar_the_decision_used(engine, monkeypatch):  # noqa: F811
    """A crunch entry planned on the thin margin isn't vetoed for not passing the strict one."""
    a, b = _two_live(engine, monkeypatch)
    w = dataclasses.make_dataclass("W", ["target"])
    engine.where[b] = w("youtubetv:b")
    monkeypatch.setattr(engine.feeds, "stale", lambda g: False)
    monkeypatch.setattr(engine.control, "deadline_cost", lambda t: 3.0)
    engine.scored[b] = dataclasses.replace(engine.scored[b], league=League.NBA, crunch=True)
    engine.decider.timeline(b, League.NBA)
    asked = []
    monkeypatch.setattr(engine.decider, "entry_plan", lambda *x, **k: asked.append(k) or _plan(False))
    engine._entry_valid(Decision(time.time(), "switch", b, "better", State.WATCHING, kind="better"), "youtubetv:b")
    assert asked and asked[0]["urgent"] is True


def test_01_resume_and_skip_land_where_they_planned(engine, monkeypatch):  # noqa: F811
    """ENTRY_EXEMPT kinds were never timed on the way in: the re-plan must not start judging them."""
    a, b = _two_live(engine, monkeypatch)
    w = dataclasses.make_dataclass("W", ["target"])
    engine.where[b] = w("youtubetv:b")
    monkeypatch.setattr(engine.feeds, "stale", lambda g: False)
    monkeypatch.setattr(engine.control, "deadline_cost", lambda t: 3.0)
    engine.scored[b] = dataclasses.replace(engine.scored[b], league=League.NBA)
    engine.decider.timeline(b, League.NBA)
    asked = []
    monkeypatch.setattr(engine.decider, "entry_plan", lambda *x, **k: asked.append(k) or _plan(False))
    engine._entry_valid(Decision(time.time(), "switch", b, "resume", State.WATCHING, kind="resume"), "youtubetv:b")
    assert asked == []


# ----- 02: one tap waits for a dead ball, whatever the app (N4) --------------------------------------------------------

def _tap(engine, monkeypatch, plan):  # noqa: F811
    a, _ = _two_live(engine, monkeypatch)
    monkeypatch.setattr(engine.decider, "entry_plan", lambda *x, **k: plan)
    return Decision(time.time(), "switch", a, "you selected this game", State.PAUSED, kind="user")


def test_02_default_times_a_tap_on_every_app():
    assert config.default("decider.entry.user_timed_targets") == []


def test_02_one_prime_tap_mid_play_waits(engine, monkeypatch):  # noqa: F811
    """Repro: user_timed_targets [youtubetv] let a single tap onto Prime join with no landing check."""
    d = _tap(engine, monkeypatch, _plan(False))
    assert engine._tap_lands_dead(d, "prime:b") is False


def test_02_one_tap_with_no_play_by_play_waits(engine, monkeypatch):  # noqa: F811
    """Repro: plan None (no timing evidence) returned True: a tap onto a game with no data joined blind."""
    d = _tap(engine, monkeypatch, None)
    assert engine._tap_lands_dead(d, "youtubetv:b") is False


def test_02_second_tap_forces(engine, monkeypatch):  # noqa: F811
    d = _tap(engine, monkeypatch, _plan(False))
    engine._tap_forced = d
    assert engine._forced_go(d) is True


# ----- 03: nothing rewinds on the visible screen ------------------------------------------------------------------------

def _keys(stick):
    return [c for c in stick.log if isinstance(c, str) and "KEYCODE" in c]


def _one_stick(cfg, tmp_path, monkeypatch):
    cfg.update({"mode": {"dry_run": False}})
    ch = ChannelMap(tmp_path / "ch.json")
    ch.codes.update({"espn": "AAAAAAAAAAA"})
    c = ctl.Controller(cfg, ch)
    c.sticks = [FakeStick("a:5555", display={"input": "HDMI1"}, hdmi="HDMI1")]
    c.hdmi = {"a:5555": "HDMI1"}
    monkeypatch.setitem(ctl.TUNERS, "youtubetv", FakeTuner())
    return c


def test_03_default_cue_is_hidden():
    assert config.default("decider.entry.cue") == "hidden"


def test_03_entry_rewind_never_runs_on_the_visible_stick(two):  # noqa: F811
    """Repro: an entry rewind flipped to the live edge, then rewound in front of Nick."""
    c, _ = two
    before, hid = c.main, c.hidden
    out = asyncio.run(c.execute("youtubetv:espn", may_cut=lambda: True, entry_rewind_s=18.0))
    assert out.ok and "before the flip" in out.detail
    names = [n for n, _ in out.marks]
    assert names.index("rewind_done") < names.index("flip_start")
    assert any("KEYCODE_MEDIA_REWIND" in k for k in _keys(hid)) and not any("REWIND" in k for k in _keys(before))


def test_03_one_stick_refuses_the_rewind_with_the_screen_untouched(cfg, tmp_path, monkeypatch):
    """No hidden stick: refused before anything is sent, so the decider waits for a landing with no rewind."""
    c = _one_stick(cfg, tmp_path, monkeypatch)
    out = asyncio.run(c.execute("youtubetv:espn", may_cut=lambda: True, entry_rewind_s=18.0))
    assert not out.ok and out.screen_unchanged and "never rewound on screen" in out.detail
    assert _keys(c.main) == []


def test_03_a_tap_arming_a_rewind_during_the_wait_is_refused_too(cfg, tmp_path, monkeypatch):
    """The tap's may_cut arms the rewind at the cut, after the early check."""
    c = _one_stick(cfg, tmp_path, monkeypatch)

    def may_cut():
        c.arm_entry_rewind(18.0)
        return True
    out = asyncio.run(c.execute("youtubetv:espn", may_cut=may_cut))
    assert not out.ok and out.screen_unchanged and _keys(c.main) == []


# ----- 04: the restart hold, end to end ------------------------------------------------------------------------------------

def test_04_restart_holds_a_high_leverage_game_until_the_hold_runs_out(tmp_path, monkeypatch):
    """P-04 end to end: the old Engine saves the screen, a new one boots with it, and 'start' stays (jump mode off)."""
    from types import SimpleNamespace
    e = _engine(tmp_path)
    e.decider.current = "g1"
    e.scored["g1"] = SimpleNamespace(high_leverage=True, crunch=False, late=False, garbage=False, personal=0, closeness=0)
    e._save_screen_state()
    e2 = _engine(tmp_path)
    assert e2.decider.current is None and not e2.decider.jump_on()
    e2._restore_screen_state()
    dec = e2.decider
    dec.timeline("b", League.NFL)
    monkeypatch.setattr(dec, "entry_plan", lambda *x, **k: _plan(True))
    d = dec.decide({"b": cand("b", 90, League.NFL)})
    assert d.action == "stay" and "restart" in d.reason and d.want == "b"
    gid, _, lev = dec._restored_screen
    dec._restored_screen = (gid, time.time() - 121, lev)   # restart_hold_s has passed
    d = dec.decide({"b": cand("b", 90, League.NFL)})
    assert d.action == "switch" and d.kind == "start" and d.target == "b"


# ----- 05: a dark feed times out to a safe choice, never freezes the screen ------------------------------------------------

def _dead(dec, gid, league, t):
    tl = dec.timeline(gid, league)
    tl.dead.add(f"{gid}-1", t, t, "timeout", "timeout", "Timeout", None, 2)


def _finished_screen(cfg, clock):
    dec = decider(cfg, clock, "a", "b", "c", jump=False)
    dec.current = "a"
    dec.timelines["a"].final_at, dec.timelines["a"].end_t = clock.t - 600, clock.t - 600   # over, its last play long since aired: nothing keeps the screen on it
    return dec


def test_05_no_play_by_play_gives_way_to_the_next_game_with_data(cfg, clock):
    """Repro: the top game had no marks: 'stay' for 4 hours of clock on a finished game, want stuck on it."""
    dec = _finished_screen(cfg, clock)
    _dead(dec, "c", League.CFB, clock.t - 40)
    fresh(dec, clock)
    x = {"b": cand("b", 90, League.CFB), "c": cand("c", 50, League.CFB)}
    d = dec.decide(x)
    assert d.action == "stay" and d.want == "b"
    clock.t += 46
    fresh(dec, clock)
    d = dec.decide(x)
    assert "c" in (d.target, d.want) and d.want != "b"


def test_05_a_dark_feed_gives_way_too(cfg, clock):
    """Repro: marks exist but the feed stopped: the wait for a dead ball that will never be logged never ended."""
    dec = _finished_screen(cfg, clock)
    _dead(dec, "b", League.CFB, clock.t - 400)
    _dead(dec, "c", League.CFB, clock.t - 40)
    fresh(dec, clock)
    dec.timelines["b"].fresh_at = clock.t - 300
    dec.timelines["b"].dead.seen(clock.t - 300, 15.0)
    x = {"b": cand("b", 90, League.CFB), "c": cand("c", 50, League.CFB)}
    assert dec.decide(x).want == "b"
    clock.t += 46
    dec.timelines["c"].fresh_at = clock.t
    dec.timelines["c"].dead.seen(clock.t, 15.0)
    d = dec.decide(x)
    assert "c" in (d.target, d.want) and d.want != "b"


def test_05_a_feed_that_comes_back_is_back_in_the_pick(cfg, clock):
    dec = _finished_screen(cfg, clock)
    fresh(dec, clock)
    x = {"b": cand("b", 90, League.CFB)}
    dec.decide(x)
    assert "b" in dec._dark_since
    _dead(dec, "b", League.CFB, clock.t - 40)
    fresh(dec, clock)
    dec.decide(x)
    assert "b" not in dec._dark_since


def test_05_alone_it_keeps_waiting_and_says_so_once(cfg, clock):
    """Rule #1 stands: nothing else to go to means no blind join, and one phone message, not one a tick."""
    dec = _finished_screen(cfg, clock)
    fresh(dec, clock)
    x = {"b": cand("b", 90, League.CFB)}
    assert dec.decide(x).action == "stay" and dec.entry_stuck is None
    clock.t += 46
    fresh(dec, clock)
    d = dec.decide(x)
    assert d.action == "stay" and d.want == "b" and dec.entry_stuck == "b"
    dec.entry_stuck = None
    clock.t += 5
    fresh(dec, clock)
    assert dec.decide(x).action == "stay" and dec.entry_stuck is None


def test_05_your_locked_game_is_never_dropped(cfg, clock):
    dec = _finished_screen(cfg, clock)
    _dead(dec, "c", League.CFB, clock.t - 40)
    dec.user.locked_game = "b"
    x = {"b": cand("b", 90, League.CFB), "c": cand("c", 50, League.CFB)}
    dec._dark_since["b"] = clock.t - 100
    fresh(dec, clock)
    assert "b" in dec._without_dark_targets(x, clock.t)


def test_05_the_engine_toasts_it_once(engine, monkeypatch):  # noqa: F811
    said = []
    monkeypatch.setattr(engine, "toast", lambda text, level="info": said.append(text))
    engine.decider.entry_stuck = "g9"
    engine._entry_stuck()
    engine._entry_stuck()
    assert len(said) == 1 and "no play-by-play" in said[0] and engine.decider.entry_stuck is None


# ----- 06: the jump's flip re-checks the hold on fresh scores -----------------------------------------------------------------

def test_06_a_screen_game_turning_crunch_during_the_wait_vetoes_the_jump(engine, monkeypatch):  # noqa: F811
    """Repro: S-2's fresh scores reached the leave gate only: at a dead ball a game now in crunch still left."""
    a, b = _two_live(engine, monkeypatch)
    monkeypatch.setattr(engine.decider, "jump_may_leave", lambda *x: True)
    got, _ = _jump(engine, monkeypatch, a, b)
    assert got["may_cut"]() is True
    engine.scored[a] = dataclasses.replace(engine.scored[a], crunch=True, is_break=False)
    assert got["may_cut"]() is False and got["may_cut"].veto == "hold"


# ----- 07: a park in the walk survives key latency -----------------------------------------------------------------------------

def test_07_a_park_inside_a_walk_lands_when_the_pause_is_late():
    """Repro: pause_at was exactly x1 = w.end: 0.05 s of key latency failed lands(), and the park sat until it expired."""
    dt = DeadTracker(League.NBA)
    made(dt, "s1", 1000.0)
    dt.add("next", 1030.0, 1030.0, "jump_shot", "Curry misses", "Jump Shot", None, 2)
    dt.seen(1200.0, 15.0)
    h = bj.Pic(dt, MU, 1.0, "b", WALK)
    now = 1001.0 + MU
    pin = bj.park_in(h, now)
    assert pin is not None
    paused = now + pin + 0.3
    assert bj.lands(bj.frozen(h, paused + 1.0, paused), paused + 1.0, 2.0, 3.2, 2.0, 2.0)[0]


# ----- Nick's rule: a walk landing is allowed, and counted apart ----------------------------------------------------------------

def test_sim_counts_a_landing_in_the_walk_apart_from_a_mid_play_join():
    import scripts.buffered_jump_sim as sim
    plays = [(1000.0, "s1", "jump_shot", "Curry makes 26-foot three", "Jump Shot", 2),
             (1030.0, "s2", "jump_shot", "Green misses", "Jump Shot", 2),
             (1031.0, "s3", "defensive_rebound", "Green defensive rebound", "Defensive Rebound", 2),
             (1060.0, "s4", "jump_shot", "Tatum misses", "Jump Shot", 2)]
    spans = sim.walk_spans(plays, WALK, League.NBA)
    assert spans and spans[0][0] == 1001.0 and sim.in_walk(spans, 1005.0, 1005.5)
    assert not sim.in_walk(spans, 1020.0, 1020.5)                       # past the walk: a real mid-play join
    assert not sim.in_walk(spans, spans[0][1] - 0.2, spans[0][1] + 0.3)   # straddling the walk's end
    assert sim.walk_spans(plays, None, League.NBA) == []                 # football: no walk


def test_defaults_are_written_down():
    assert config.default("decider.entry.no_data_wait_s") == 45
    assert json.dumps(config.default("decider.entry.user_timed_targets")) == "[]"
