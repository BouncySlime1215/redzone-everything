import pytest

from redzone.decider.decider import Candidate, Decider, FocusMode, Missed, State
from redzone.decider.screen import DelayBook, Event
from redzone.models import League
from redzone.scoring.engine import Scored


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


# --- crunch time: final 2:00 of a one-score game, or OT ------------------------------

def csc(gid, score, personal=0.0, is_break=False, closeness=1.0):
    return Scored(gid, League.NFL, score, score, score, 1.0, personal, f"reason {gid}", [], is_break,
                  None, None, closeness, crunch=True)


def on_break_now(dec, clock, gid, label="timeout"):
    """Put gid's stoppage on screen (mu 40 + 2 sigma)."""
    dec.timeline(gid, League.NFL).events.append(Event(clock.t, "stop_start", label))
    clock.t += 45


def test_a_final_is_not_left_while_its_last_play_still_airs(dec, clock):
    calibrate(dec, "youtubetv:a")
    start_on(dec, clock, "a", {})
    dec.timeline("a", League.NFL).events.append(Event(clock.t, "play", "kneel"))
    clock.t += 10
    d = dec.decide(cands(b=50))  # "a" went final in the data; the kneel airs in ~30s
    assert d.action == "stay" and "final play" in d.reason
    clock.t += 40
    d = dec.decide(cands(b=50))
    assert d.action == "switch" and d.target == "b" and d.kind == "ended"


def test_game_over_goes_to_crunch_time_even_mid_two_minute_warning(dec, clock):
    start_on(dec, clock, "a", {})
    d = dec.decide(cands(b=60, w=csc("w", 50, personal=22.5, is_break=True)))
    assert d.action == "switch" and d.target == "w" and "crunch time" in d.reason


def test_gets_to_crunch_time_at_the_first_safe_gap(dec, clock):
    calibrate(dec, "youtubetv:a")
    start_on(dec, clock, "a", {})
    on_break_now(dec, clock, "a")
    # Without crunch time this would be a break visit to b; crunch time elsewhere wins, even mid-stoppage.
    d = dec.decide(cands(a=sc("a", 60, is_break=True), b=80, c=csc("c", 40, is_break=True)))
    assert d.action == "switch" and d.target == "c" and d.kind == "crunch"


def test_waits_for_a_gap_to_get_to_crunch_time(dec, clock):
    calibrate(dec, "youtubetv:a")
    start_on(dec, clock, "a", {})
    dec.timeline("a", League.NFL).events.append(Event(clock.t, "play", "run for 4"))
    clock.t += 35
    d = dec.decide(cands(a=30, c=csc("c", 40)))
    assert d.action == "stay" and d.reason.startswith("want crunch time")


def test_your_team_crunch_is_held_through_its_stoppages(dec, clock):
    calibrate(dec, "youtubetv:w")
    start_on(dec, clock, "w", {})
    on_break_now(dec, clock, "w")
    d = dec.decide(cands(w=csc("w", 70, personal=22.5, is_break=True), b=89, c=csc("c", 80)))
    assert d.action == "stay" and d.reason.startswith("crunch time")


def test_neutral_crunch_held_through_two_minute_warning_but_not_a_timeout(dec, clock):
    calibrate(dec, "youtubetv:a")
    start_on(dec, clock, "a", {})
    on_break_now(dec, clock, "a", "two_minute_warning")
    d = dec.decide(cands(a=csc("a", 60, is_break=True), b=80))
    assert d.action == "stay" and d.reason.startswith("crunch time")
    on_break_now(dec, clock, "a", "timeout")
    d = dec.decide(cands(a=csc("a", 60, is_break=True), b=80))
    assert d.action == "switch" and d.kind == "break_visit"


def test_overlapping_crunch_hops_while_this_one_is_stopped(dec, clock):
    calibrate(dec, "youtubetv:a")
    start_on(dec, clock, "a", {})
    on_break_now(dec, clock, "a")
    d = dec.decide(cands(a=csc("a", 60, is_break=True), b=csc("b", 55)))
    assert d.action == "switch" and d.target == "b" and d.kind == "crunch"


def test_your_team_crunch_wins_an_overlap(dec, clock):
    calibrate(dec, "youtubetv:a")
    start_on(dec, clock, "a", {})
    on_break_now(dec, clock, "a")
    d = dec.decide(cands(a=csc("a", 80, is_break=True), w=csc("w", 50, personal=22.5, is_break=True)))
    assert d.action == "switch" and d.target == "w"


def test_after_a_replay_go_straight_to_crunch_time(dec, clock):
    start_on(dec, clock, "a", {})
    dec.replaying = Missed("b", "p1", "TD", "td", clock.t - 100, 75)
    dec.replay_until = clock.t - 1
    dec.current = "b"
    d = dec.decide(cands(a=70, b=50, c=csc("c", 40, is_break=True)))
    assert d.action == "switch" and d.target == "c"


def test_next_snap_estimate(dec, clock):
    calibrate(dec, "youtubetv:a")
    start_on(dec, clock, "a", {})
    cs = cands(a=csc("a", 60, is_break=True))
    tl = dec.timeline("a", League.NFL)
    tl.events.append(Event(clock.t, "stop_start", "two_minute_warning"))
    # No play yet: at least the warning's shortest length, then the delay (minus its error bars).
    assert dec._next_snap_s("a", cs, clock.t) == pytest.approx(150 + 40 - 4 - 10, abs=0.5)
    tl.events.append(Event(clock.t + 5, "play", "kickoff", play_kind="kickoff"))
    assert dec._next_snap_s("a", cs, clock.t) == pytest.approx(5 + 25 + 26, abs=0.5)
    tl.events.append(Event(clock.t + 6, "play", "run for 2"))
    assert dec._next_snap_s("a", cs, clock.t) == pytest.approx(6 + 26, abs=0.5)


def test_crunch_hops_are_not_flip_flopping(dec, clock):
    start_on(dec, clock, "a", {})
    for g in "baba":
        clock.t += 25
        dec._commit(g, kind="crunch")
    assert dec.frozen_until <= clock.t
