import pytest

from redzone.decider.decider import Candidate, Decider, FocusMode, Missed, State
from redzone.decider.screen import DelayBook, Event, Timeline
from redzone.models import League, Play
from redzone.scoring.engine import Scored
from tests.conftest import nfl


def sc(gid, score, lock=None, is_break=False, league=League.NFL):
    return Scored(gid, league, score, score, score, 1.0, 0.0, f"reason {gid}", [], is_break,
                  lock, None, 1.0)


@pytest.fixture
def dec(cfg, clock):
    delays = DelayBook({"youtubetv": 40.0, "prime": 35.0}, sigma_default=15.0, clock=clock)
    d = Decider(cfg, delays, clock=clock)
    return d


def calibrate(dec, target="youtubetv:a", mu=40.0):
    for v in (mu - 1, mu, mu + 1):
        dec.delays.calibrate(target, v)


def cands(**scores):
    out = {}
    for gid, val in scores.items():
        s = val if isinstance(val, Scored) else sc(gid, val)
        out[gid] = Candidate(s, f"youtubetv:{gid}")
    return out


def start_on(dec, clock, gid, others):
    dec.timeline(gid, League.NFL)
    d = dec.decide(cands(**{gid: 99, **others}))
    assert d.action == "switch" and d.target == gid
    clock.t += 100  # past dwell


def test_never_cuts_while_a_play_is_on_screen(dec, clock):
    calibrate(dec, "youtubetv:a")
    start_on(dec, clock, "a", {})
    tl = dec.timeline("a", League.NFL)
    tl.events.append(Event(clock.t, "play", "run for 4"))
    # The play ends on screen ~40s after we saw it. 35s later it's mid-air.
    clock.t += 35
    d = dec.decide(cands(a=30, b=80))
    assert d.action == "stay" and "play is on screen" in d.reason
    # Once it has aired (+ margin + buffer), we go.
    clock.t += 20
    d = dec.decide(cands(a=30, b=80))
    assert d.action == "switch" and d.target == "b"


def test_uncalibrated_delay_waits_for_official_break(dec, clock):
    start_on(dec, clock, "a", {})  # default sigma 15 > 8: uncalibrated
    d = dec.decide(cands(a=30, b=80))
    assert d.action == "stay" and "calibrated" in d.reason
    tl = dec.timeline("a", League.NFL)
    tl.events.append(Event(clock.t, "stop_start", "timeout"))
    clock.t += 40 + 30 + 1  # mu + 2*sigma
    d = dec.decide(cands(a=sc("a", 30, is_break=True), b=80))
    assert d.action == "switch" and d.kind == "break_visit"


def test_locked_moment_needs_a_much_bigger_one(dec, clock):
    calibrate(dec, "youtubetv:a")
    start_on(dec, clock, "a", {})
    d = dec.decide(cands(a=sc("a", 60, lock="red zone drive"), b=75))
    assert d.action == "stay"
    d = dec.decide(cands(a=sc("a", 60, lock="red zone drive"), b=85))
    assert d.action == "switch"


def test_small_lead_is_not_enough(dec, clock):
    calibrate(dec, "youtubetv:a")
    start_on(dec, clock, "a", {})
    assert dec.decide(cands(a=50, b=60)).action == "stay"     # 10 < margin 10 + cost 3
    assert dec.decide(cands(a=50, b=64)).action == "switch"


def test_min_dwell(dec, clock):
    calibrate(dec, "youtubetv:a")
    calibrate(dec, "youtubetv:b")
    dec.timeline("b", League.NFL)
    start_on(dec, clock, "a", {})
    assert dec.decide(cands(a=40, b=70)).target == "b"
    clock.t += 30
    d = dec.decide(cands(a=40, b=50, c=90))
    assert d.action == "stay" and "giving it a moment" in d.reason


def test_break_visit_then_return_when_home_resumes(dec, clock):
    calibrate(dec, "youtubetv:a")
    calibrate(dec, "youtubetv:b")
    start_on(dec, clock, "a", {})
    tla = dec.timeline("a", League.NFL)
    dec.timeline("b", League.NFL)
    tla.events.append(Event(clock.t, "stop_start", "timeout"))
    clock.t += 50
    d = dec.decide(cands(a=sc("a", 70, is_break=True), b=55))
    assert d.kind == "break_visit" and d.target == "b"
    # Home's break ends in the data. Stay away until just before the resume airs, then go back.
    clock.t += 60
    tla.events.append(Event(clock.t, "stop_end"))
    d = dec.decide(cands(a=70, b=55))
    assert d.action == "stay" and "heading back" not in d.reason
    clock.t += 30
    d = dec.decide(cands(a=70, b=55))
    assert d.action == "switch" and d.target == "a" and d.kind == "home"
    assert dec.home_break_visit is None


def test_focus_stays_through_live_action(dec, clock):
    calibrate(dec, "youtubetv:a")
    start_on(dec, clock, "a", {})
    dec.user.focus_game, dec.user.focus_mode = "a", FocusMode.FOCUS
    d = dec.decide(cands(a=30, b=89))
    assert d.action == "stay" and d.reason.startswith("focused")


def test_focus_must_see_leaves_for_huge_moment(dec, clock):
    calibrate(dec, "youtubetv:a")
    start_on(dec, clock, "a", {})
    dec.user.focus_game, dec.user.focus_mode = "a", FocusMode.FOCUS_MUST_SEE
    assert dec.decide(cands(a=40, b=85)).action == "stay"
    d = dec.decide(cands(a=40, b=95))
    assert d.action == "switch" and d.kind == "must_see"


def test_ping_pong_freezes(dec, clock):
    for g in "ab":
        calibrate(dec, f"youtubetv:{g}")
    start_on(dec, clock, "a", {})
    dec.timeline("b", League.NFL)
    seq = [("b", dict(a=20, b=60)), ("a", dict(a=80, b=60)), ("b", dict(a=20, b=90)), ("a", dict(a=99, b=60))]
    for expect, sc_ in seq:
        clock.t += 50
        d = dec.decide(cands(**sc_))
        if d.action == "switch":
            assert d.target == expect
    assert dec.frozen_until > clock.t
    clock.t += 50
    assert dec.decide(cands(a=10, b=99)).state == State.FROZEN


def test_rate_limit(dec, clock):
    for g in "abcdef":
        calibrate(dec, f"youtubetv:{g}")
        dec.timeline(g, League.NFL)
    start_on(dec, clock, "a", {})
    switches = 0
    scores = {"a": 10}
    for i, g in enumerate("bcdef"):
        clock.t += 46
        scores = {k: 10 for k in scores}
        scores[g] = 50 + 10 * i
        if dec.decide(cands(**scores)).action == "switch":
            switches += 1
    assert switches <= 4


def test_safe_mode_and_pause(dec, clock):
    start_on(dec, clock, "a", {})
    for _ in range(3):
        dec.note_error()
    assert dec.decide(cands(a=10, b=99)).state == State.SAFE
    dec.resume()
    dec.user.paused = True
    assert dec.decide(cands(a=10, b=99)).state == State.PAUSED


def test_game_ending_moves_on_even_without_gate(dec, clock):
    start_on(dec, clock, "a", {})
    d = dec.decide(cands(b=50))  # "a" vanished (final)
    assert d.action == "switch" and d.target == "b" and d.kind == "ended"


def test_unknown_source_is_never_a_destination(dec, clock):
    calibrate(dec, "youtubetv:a")
    start_on(dec, clock, "a", {})
    cs = cands(a=20)
    cs["b"] = Candidate(sc("b", 99), None)
    assert dec.decide(cs).action == "stay"


def test_muted_game_ignored(dec, clock):
    calibrate(dec, "youtubetv:a")
    start_on(dec, clock, "a", {})
    dec.user.muted.add("b")
    assert dec.decide(cands(a=20, b=99)).action == "stay"


def test_chase_catches_a_big_play_live(dec, clock):
    for g in "ab":
        calibrate(dec, f"youtubetv:{g}")
    start_on(dec, clock, "a", {})
    tlb = dec.timeline("b", League.NFL)
    tlb.events.append(Event(clock.t, "play", "TD pass to the corner", big=True))
    clock.t += 2
    d = dec.decide(cands(a=40, b=45))
    assert d.action == "switch" and d.kind == "chase" and "catch it live" in d.reason


def test_manual_pick_holds(dec, clock):
    start_on(dec, clock, "a", {})
    dec.manual_switch("b")
    assert dec.current == "b"
    assert dec.decide(cands(a=99, b=10)).state == State.PAUSED


# ----- catch-up replays --------------------------------------------------------

def big(tl, t, pid, kind="td"):
    tl.events.append(Event(t, "play", f"{kind} {pid}", big=True, pid=pid, play_kind=kind))


def test_final_play_of_a_finished_game_is_still_replayed(dec, clock):
    for g in "ab":
        calibrate(dec, f"youtubetv:{g}")
    start_on(dec, clock, "a", {"b": 30})
    # b's walk-off FG is in the data, then b is final (gone from the candidates) before it airs.
    big(dec.timeline("b", League.NFL), clock.t, "fg1", "fg")
    for _ in range(6):
        clock.t += 10
        dec.decide(cands(a=60))
    assert [m.pid for m in dec.queue] == ["fg1"]
    # a ends too: nothing live, so catch up on b's FG.
    d = dec.decide({})
    assert d.action == "replay" and d.target == "b" and d.replay.pid == "fg1"
    assert dec.target_of("b") == "youtubetv:b"  # the engine still knows where to tune


def test_play_only_partly_on_screen_counts_as_missed(dec, clock):
    for g in "ab":
        calibrate(dec, f"youtubetv:{g}")
    start_on(dec, clock, "a", {"b": 20})
    # b's TD is already airing when we tune there: we catch its last few seconds, not the snap.
    big(dec.timeline("b", League.NFL), clock.t - 32, "td1")
    dec.manual_switch("b")
    clock.t += 20
    dec.decide(cands(a=20, b=20))
    assert [m.pid for m in dec.queue] == ["td1"]


def test_last_chance_replay_jumps_the_queue(dec, clock):
    now = clock.t
    fresh_td = Missed("a", "td1", "TD", "td", now - 60, 75)
    old_fg = Missed("b", "fg1", "FG", "fg", now - 1700, 35)
    dec.queue = [fresh_td, old_fg]
    cs = cands(a=10, b=10)
    assert dec._best_replay(cs, now, None, False) is old_fg      # 100s left: show it now
    old_fg.t = now - 300
    assert dec._best_replay(cs, now, None, False) is fresh_td    # plenty of time: value first


@pytest.mark.parametrize("lock, action", [(None, "replay"), ("final two minutes", "switch")])
def test_game_over_catch_up_never_skips_a_locked_moment(dec, clock, lock, action):
    for g in "ab":
        calibrate(dec, f"youtubetv:{g}")
    start_on(dec, clock, "a", {"b": 20})
    dec.queue = [Missed("b", "td1", "TD", "td", clock.t - 100, 75)]
    d = dec.decide(cands(b=sc("b", 50, lock=lock)))  # a is final
    assert d.action == action


def test_game_over_catch_up_waits_for_the_last_play_to_air(dec, clock):
    for g in "ab":
        calibrate(dec, f"youtubetv:{g}")
    start_on(dec, clock, "a", {"b": 20})
    dec.timeline("a", League.NFL).events.append(Event(clock.t, "play", "kneel", pid="k"))
    dec.queue = [Missed("b", "td1", "TD", "td", clock.t - 100, 75)]
    clock.t += 10
    d = dec.decide(cands(b=20))  # a is final in the data, but the kneel hasn't aired yet
    assert d.action == "stay" and "last play" in d.reason
    clock.t += 50
    d = dec.decide(cands(b=20))
    assert d.action == "replay" and d.replay.pid == "td1"


def test_first_poll_keeps_a_play_that_has_not_aired_yet(clock):
    tl = Timeline(League.NFL, clock=clock)
    g = nfl(hs=7)
    g.recent_plays = [Play("old", "run for 3", "rush", wallclock=clock.t - 900),
                      Play("ko", "kickoff return, TOUCHDOWN", "td", True, wallclock=clock.t - 15)]
    tl.observe(g)
    assert [(e.pid, e.big) for e in tl.events if e.kind == "play"] == [("ko", True)]
