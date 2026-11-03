import pytest

from redzone.decider.decider import Candidate, Decider, FocusMode, Missed, State
from redzone.decider.screen import MIN_TAP_SIGMA_S, DelayBook, Event, Timeline
from redzone.models import League, Play, Stoppage
from redzone.scoring.engine import Scored
from tests.conftest import nfl


def sc(gid, score, lock=None, is_break=False, league=League.NFL):
    return Scored(gid, league, score, score, score, 1.0, 0.0, f"reason {gid}", [], is_break,
                  lock, None, 1.0)


@pytest.fixture
def dec(cfg, clock):
    delays = DelayBook({"youtubetv": 40.0, "prime": 35.0}, sigma_default=15.0, clock=clock)
    d = Decider(cfg, delays, clock=clock)
    with_fresh_timing(d, clock)
    return d


def with_fresh_timing(dec, clock):
    """These selection unit tests model continuously polled games, not feed outages.

    Supply that observation explicitly. Outage/no-evidence behavior is covered
    by test_midplay and test_play_evidence, which do not use this fixture wrapper.
    """
    decide = dec.decide

    def observed(candidates, *args, **kwargs):
        for gid, candidate in candidates.items():
            dec.timeline(gid, candidate.scored.league)
        for timeline in dec.timelines.values():
            timeline.fresh_at = clock.t
            if not timeline.events:
                timeline.events.append(Event(clock.t - 120, "play", "previous observed play"))
        return decide(candidates, *args, **kwargs)

    dec.decide = observed


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
    timeline = dec.timeline(gid, League.NFL)
    timeline.fresh_at = clock.t
    if not timeline.events:
        timeline.events.append(Event(clock.t - 120, "play", "previous observed play"))


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
    # End of quarter, not a timeout: with sigma 15 the screen could be 10 s behind, and a timeout can
    # end in 15 s, so only breaks that always run long are provably still on screen (test_midplay).
    tl.events.append(Event(clock.t, "stop_start", "end_period"))
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


def test_better_game_does_not_wait_for_a_viewing_quota(dec, clock):
    calibrate(dec, "youtubetv:a")
    calibrate(dec, "youtubetv:b")
    dec.timeline("b", League.NFL)
    start_on(dec, clock, "a", {})
    assert dec.decide(cands(a=40, b=70)).target == "b"
    clock.t += 30
    d = dec.decide(cands(a=40, b=50, c=90))
    assert d.action == "switch" and d.target == "c"


def _visit_then_resume(dec, clock, home_lock=None):
    """Leave "a" for its quarter break, then the data shows the snap that ends it."""
    calibrate(dec, "youtubetv:a")
    calibrate(dec, "youtubetv:b")
    start_on(dec, clock, "a", {})
    tla = dec.timeline("a", League.NFL)
    dec.timeline("b", League.NFL)
    tla.events.append(Event(clock.t, "stop_start", "end_period"))
    clock.t += 50
    d = dec.decide(cands(a=sc("a", 70, is_break=True), b=55))
    assert d.kind == "break_visit" and d.target == "b"
    clock.t += 60
    tla.events.append(Event(clock.t, "stop_end"))
    tla.events.append(Event(clock.t + 12, "play", "run for 3"))
    d = dec.decide(cands(a=sc("a", 70, lock=home_lock), b=55))
    assert d.action == "stay" and "heading back" not in d.reason
    return lambda: dec.decide(cands(a=sc("a", 70, lock=home_lock), b=55))


def test_break_visit_returns_just_before_a_crunch_snap(dec, clock):
    # Every snap counts in the final two minutes: go back just before it airs.
    again = _visit_then_resume(dec, clock, home_lock="final two minutes")
    clock.t += 30
    d = again()
    assert d.action == "switch" and d.target == "a" and d.kind == "home"
    assert dec.home_break_visit is None


def test_break_visit_skips_the_ad_tail_before_a_routine_snap(dec, clock):
    # A routine snap isn't worth half a minute of commercial: come back once it has aired.
    again = _visit_then_resume(dec, clock)
    clock.t += 30
    assert again().action == "stay"
    clock.t += 30
    d = again()
    assert d.action == "switch" and d.target == "a" and d.kind == "home"


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


def test_changing_game_evidence_does_not_trigger_a_blind_freeze(dec, clock):
    for g in "ab":
        calibrate(dec, f"youtubetv:{g}")
    start_on(dec, clock, "a", {})
    dec.timeline("b", League.NFL)
    seq = [("b", dict(a=20, b=60)), ("a", dict(a=80, b=60)), ("b", dict(a=20, b=90)), ("a", dict(a=99, b=60))]
    for expect, sc_ in seq:
        clock.t += 50
        d = dec.decide(cands(**sc_))
        assert d.action == "switch" and d.target == expect
    assert dec.frozen_until == 0
    clock.t += 50
    assert dec.decide(cands(a=10, b=99)).action == "switch"


def test_no_hard_number_of_switches_per_five_minutes(dec, clock):
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
    assert switches == 5


def test_safe_mode_and_pause(dec, clock):
    start_on(dec, clock, "a", {})
    for _ in range(3):
        dec.note_error()
    assert dec.decide(cands(a=10, b=99)).state == State.SAFE
    dec.resume()
    dec.user.paused = True
    assert dec.decide(cands(a=10, b=99)).state == State.PAUSED


def test_game_ending_moves_on_once_its_last_play_has_aired(dec, clock):
    start_on(dec, clock, "a", {})
    tl = dec.timeline("a", League.NFL)
    tl.final_at, tl.end_t = clock.t, clock.t - 5  # final in the data; last play 5 s ago (test_midplay)
    d = dec.decide(cands(b=50))  # "a" left the candidates (final)
    assert d.action == "stay"    # its last play hasn't reached your screen yet
    clock.t += 40 + 30 + 3       # mu + 2*sigma + buffer
    d = dec.decide(cands(b=50))
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
    tl = dec.timeline("a", League.NFL)
    tl.events.append(Event(clock.t, "play", "kneel"))
    tl.final_at, tl.end_t = clock.t + 1, clock.t   # the engine keeps observing finals: it sees POST
    clock.t += 10
    d = dec.decide(cands(b=50))  # "a" went final in the data; the kneel airs in ~30s
    assert d.action == "stay" and "last play" in d.reason
    clock.t += 45   # kneel seen + 40 s delay + 2*sigma (tap floor) + buffer has passed
    d = dec.decide(cands(b=50))
    assert d.action == "switch" and d.target == "b" and d.kind == "ended"


def test_game_over_goes_to_crunch_time_even_mid_two_minute_warning(dec, clock):
    start_on(dec, clock, "a", {})
    tl = dec.timeline("a", League.NFL)
    tl.final_at, tl.end_t = clock.t - 120, clock.t - 120   # "a" is over and its last play has aired
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
    dec.timeline("b", League.NFL)   # every live game has a timeline (it's how we know b can be left)
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
    # The resume has reached the feed, but is still a stream-delay ahead of TV.
    # An average timeout duration alone cannot promise room for the round trip.
    dec.timeline("a", League.NFL).events.append(Event(clock.t, "stop_end"))
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
    assert dec._next_snap_s("a", cs, clock.t) == pytest.approx(150 + 40 - 2 * MIN_TAP_SIGMA_S - 10, abs=0.5)
    tl.events.append(Event(clock.t + 5, "play", "kickoff", play_kind="kickoff"))
    assert dec._next_snap_s("a", cs, clock.t) == pytest.approx(5 + 25 + 40 - 2 * MIN_TAP_SIGMA_S - 10, abs=0.5)
    tl.events.append(Event(clock.t + 6, "play", "run for 2"))
    assert dec._next_snap_s("a", cs, clock.t) == pytest.approx(6 + 40 - 2 * MIN_TAP_SIGMA_S - 10, abs=0.5)


def test_crunch_hops_are_not_flip_flopping(dec, clock):
    start_on(dec, clock, "a", {})
    for g in "baba":
        clock.t += 25
        dec._commit(g, kind="crunch")
    assert dec.frozen_until <= clock.t


def test_my_teams_break_never_strands_me_in_another_basketball_game(dec, clock):
    """Wizards timeout -> a visit to another NBA game can only be left at a whistle, which can take
    minutes; that cost 6 crunch plays in a backtest. Visit only places with frequent clean exits."""
    from redzone.decider.screen import Event as Ev
    for g in ("w", "o", "f"):
        calibrate(dec, f"youtubetv:{g}")
    dec.timeline("o", League.NBA)
    dec.timeline("f", League.NFL)
    start_on(dec, clock, "w", {})
    dec.timeline("w", League.NBA).events.append(Ev(clock.t, "stop_start", "timeout"))
    clock.t += 50
    mine = Scored("w", League.NBA, 60, 60, 60, 1.0, 20.0, "WSH game", ["WSH"], True, None, None, 1.0)
    other_nba = sc("o", 70, league=League.NBA)
    nfl_game = sc("f", 40)
    cs = {"w": Candidate(mine, "youtubetv:w"), "o": Candidate(other_nba, "youtubetv:o"),
          "f": Candidate(nfl_game, "youtubetv:f")}
    d = dec.decide(cs)
    assert d.action == "switch" and d.target == "f"   # the football game, not the better NBA game


def test_halftime_visit_plans_its_trip_back(dec, clock):
    """Halftime: visit a live game, and head back well before the break can end on screen."""
    from redzone.decider.screen import Event as Ev
    for g in ("h", "o"):
        calibrate(dec, f"youtubetv:{g}")
        dec.timeline(g, League.NBA)
    start_on(dec, clock, "h", {})
    dec.timeline("h", League.NBA).events.append(Ev(clock.t, "stop_start", "halftime"))
    clock.t += 60   # halftime is on screen
    cs = {"h": Candidate(sc("h", 30, is_break=True, league=League.NBA), "youtubetv:h"),
          "o": Candidate(sc("o", 12, league=League.NBA), "youtubetv:o")}   # dull, but live
    d = dec.decide(cs)
    assert d.action == "switch" and d.target == "o" and d.kind == "break_visit"
    assert dec.home_return_at is not None and dec.home_return_at - clock.t > 400


def _nba_short_timeout(dec, clock, dest_cost, dest_score=40):
    """Home NBA game in its 3rd timeout of the period (~107 s of ads); another NBA game is live on Prime."""
    from redzone.decider.screen import Event as Ev
    for g in ("h", "o"):
        calibrate(dec, f"youtubetv:{g}", mu=60.0)
        dec.timeline(g, League.NBA)
    start_on(dec, clock, "h", {})
    dec.timeline("h", League.NBA).period_timeouts = 3
    dec.deadline_cost = lambda t: dest_cost if t == "youtubetv:o" else 6.0
    dec.return_cost = lambda t: 6.0   # two sticks: home keeps playing on the stick we leave
    tl = dec.timeline("h", League.NBA)
    tl.events.append(Ev(clock.t, "stop_start", "timeout"))
    tl.stoppage = Stoppage.TIMEOUT
    clock.t += 75   # on screen now: ~92 s of it left
    return dec.decide({"h": Candidate(sc("h", 50, is_break=True, league=League.NBA), "youtubetv:h"),
                       "o": Candidate(sc("o", dest_score, league=League.NBA), "youtubetv:o")})


def test_short_timeout_is_not_spent_on_a_cold_prime_tune(dec, clock):
    """A 76 s cold Prime tune (measured p90 + flip) and the flip back don't fit a ~107 s timeout."""
    d = _nba_short_timeout(dec, clock, dest_cost=76.0)
    assert d.action == "stay"


def test_same_short_timeout_visits_a_preloaded_stick(dec, clock):
    d = _nba_short_timeout(dec, clock, dest_cost=6.0)
    assert d.action == "switch" and d.target == "o" and d.kind == "break_visit"


def test_trip_budget_skips_no_game_we_would_stay_on(dec, clock):
    """NHL Oct 20 scorecard: a goal break with 15 s left was skipped as "can't fit" for WPG @ STL
    (up 1, 3:57 left), which the old code visited and kept. A destination clearly better than home
    is no round trip (decide() releases home there), so the trip back never has to fit."""
    assert _nba_short_timeout(dec, clock, dest_cost=76.0).action == "stay"   # o at 40: no trip
    watchable = {"h": Candidate(sc("h", 50, is_break=True, league=League.NBA), "youtubetv:h"),
                 "o": Candidate(sc("o", 80, league=League.NBA), "youtubetv:o")}
    assert dec._break_destination(watchable, exclude="h") == "o"


def _routine_snap_break(dec, clock, end_ago, snap_ago):
    """NFL home h in a timeout whose resume (stop_end) and routine snap are already in the data."""
    calibrate(dec, "youtubetv:h", mu=40.0)
    start_on(dec, clock, "h", {"o": 50})
    tl = dec.timeline("h", League.NFL)
    dec.deadline_cost = lambda t: 6.0
    tl.events.append(Event(clock.t - 90, "stop_start", "timeout"))
    tl.events.append(Event(clock.t - end_ago, "stop_end"))
    tl.events.append(Event(clock.t - snap_ago, "play", "routine snap"))
    return dec._visit_watch("h", "o", clock.t)


def test_routine_snap_visit_is_budgeted_to_when_we_really_come_back(dec, clock):
    """CLE @ CHI scorecard: the resume of a routine-snap break was logged 40 s ago and its snap
    15 s ago, so the snap airs in ~25 s and _home_due brings us back only once it has. Budgeting
    the visit to the early edge of the resume instead called it negative and skipped it."""
    assert dec._visit_watch is not None
    assert _routine_snap_break(dec, clock, end_ago=40, snap_ago=15) > 0


def test_routine_snap_visit_that_cant_show_anything_is_still_skipped(dec, clock):
    # The snap has already aired at home: a visit would land after we're due back.
    assert _routine_snap_break(dec, clock, end_ago=70, snap_ago=60) < 0


def test_nba_timeouts_are_counted_per_period(clock):
    from redzone.models import GameState, Phase, Stoppage as St, Team
    tl = Timeline(League.NBA, clock=clock)

    def poll(pid, kind, stop):
        p = Play(id=pid, text=kind, kind=kind, wallclock=clock.t - 8)
        g = GameState(league=League.NBA, game_id="g", home=Team("WAS", score=0), away=Team("BOS", score=0),
                      phase=Phase.LIVE, period=1, clock_s=600.0, stoppage=stop, last_play=p, recent_plays=[p])
        tl.observe(g)
        clock.t += 10
    poll("1", "jump_ball", St.NONE)
    for i in range(3):
        poll(f"t{i}", "full_timeout", St.TIMEOUT)
        assert tl.period_timeouts == i + 1 and tl.break_kind == "full_timeout"
        poll(f"p{i}", "jump_shot", St.NONE)
    poll("e", "end_period", St.END_PERIOD)
    assert tl.period_timeouts == 0


def _timeout_two_dests(dec, clock, c_scored, c_cost):
    """Home NBA game in the period's 1st timeout; c (behind `c_cost` s) vs neutral o (preloaded, 6 s)."""
    for g in ("h", "c", "o"):
        calibrate(dec, f"youtubetv:{g}", mu=60.0)
        dec.timeline(g, League.NBA)
    dec.decide({"h": Candidate(sc("h", 99, league=League.NBA), "youtubetv:h")})
    clock.t += 100
    tl = dec.timeline("h", League.NBA)
    tl.period_timeouts = 1
    dec.deadline_cost = lambda t: c_cost if t == "youtubetv:c" else 6.0
    dec.return_cost = lambda t: 6.0
    tl.events.append(Event(clock.t, "stop_start", "timeout"))
    tl.stoppage = Stoppage.TIMEOUT
    clock.t += 80
    return dec.decide({"h": Candidate(sc("h", 50, is_break=True, league=League.NBA), "youtubetv:h"),
                       "c": Candidate(c_scored, "youtubetv:c"),
                       "o": Candidate(sc("o", 40, league=League.NBA), "youtubetv:o")})


def _nba(gid, score, personal=0.0, lock=None, crunch=False):
    return Scored(gid, League.NBA, score, score, score, 1.0, personal, f"reason {gid}", [], False,
                  lock, None, 1.0, crunch=crunch)


@pytest.mark.parametrize("c_cost", [40.0, 76.0])
def test_break_visit_never_trades_your_team_for_a_preloaded_neutral_game(dec, clock, c_cost):
    """Review blocker (break-visit-costs): score x watch let a preloaded neutral game beat the
    user's team (WSH/UNC) behind a cold Prime tune. The break visit is how the viewer reaches a
    Prime team game at all (the switch cost blocks the ordinary 'better' switch)."""
    d = _timeout_two_dests(dec, clock, _nba("c", 60, personal=8), c_cost)
    assert d.action == "switch" and d.target == "c" and d.kind == "break_visit"


@pytest.mark.parametrize("c_cost", [40.0, 76.0])
@pytest.mark.parametrize("how", ["lock", "flag"])
def test_break_visit_crunch_destination_outranks_a_preloaded_game(dec, clock, c_cost, how):
    """Review blocker: the fit filter let crunch through 'whatever is left', then the ranking
    multiplied its score by a small or negative watch time and a preloaded non-crunch game won."""
    c = _nba("c", 70, lock="crunch time") if how == "lock" else _nba("c", 70, crunch=True)
    d = _timeout_two_dests(dec, clock, c, c_cost)
    assert d.action == "switch" and d.target == "c" and d.kind in ("break_visit", "crunch")


def test_break_visit_still_prefers_the_preloaded_stick_among_neutral_games(dec, clock):
    d = _timeout_two_dests(dec, clock, _nba("c", 45), 76.0)
    assert d.action == "switch" and d.target == "o" and d.kind == "break_visit"


def test_your_team_late_and_close_is_never_left_for_a_lesser_game():
    """Oct 26 replay: WSH @ CHA, down 9 with 8:23 left in Q4, was left for a neutral comeback and we
    got back with 0:33 left (0 of 13 crunch plays). Late, not garbage, your team: stay."""
    from redzone.decider.decider import Decider
    from types import SimpleNamespace as NS
    d = Decider.__new__(Decider)
    d.cfg = NS(get=lambda k, default=None: {"decider.crunch.hold_personal": 5,
                                            "decider.mine_late_closeness": 0.2}.get(k, default))
    mine = NS(late=True, garbage=False, personal=7.5, closeness=0.3)
    assert d._mine_late(mine)
    assert not d._mine_late(NS(late=False, garbage=False, personal=25.0, closeness=1.0))   # not late yet
    assert not d._mine_late(NS(late=True, garbage=True, personal=25.0, closeness=0.1))     # garbage time
    assert not d._mine_late(NS(late=True, garbage=False, personal=0.0, closeness=0.9))     # not your team


def test_your_teams_missed_play_goes_before_neutral_catch_ups(dec, clock):
    # CLE @ CHI slate: stale neutral field goals (urgent, about to expire) pushed WSH's fumble and
    # touchdown out of the catch-up window. Your team's missed play now goes first.
    now = clock.t
    mine = Missed("w", "td1", "TD", "td", now - 60, 100, mine=True)
    old_fg = Missed("b", "fg1", "FG", "fg", now - 1700, 35)
    dec.queue = [old_fg, mine]
    assert dec._best_replay(cands(w=10, b=10), now, None, False) is mine
    dec.cfg.data["replay"]["mine_first"] = False
    assert dec._best_replay(cands(w=10, b=10), now, None, False) is old_fg


def test_crunch_time_is_not_left_for_a_higher_scoring_non_crunch_game(dec, clock):
    for g in "ab":
        calibrate(dec, f"youtubetv:{g}")
    crunch = sc("a", 60)
    crunch.crunch = True
    start_on(dec, clock, "a", {"b": 10})
    d = dec.decide(cands(a=crunch, b=95))   # b: your team late, floored at 85+, but not crunch
    assert d.action == "stay" and d.target == "a", d
    dec.cfg.data["decider"]["crunch"]["keep_over_noncrunch"] = False
    d = dec.decide(cands(a=crunch, b=95))
    assert d.action == "switch" and d.target == "b", d   # the old rule: a big enough lead wins
