"""Cut timing: dead windows from play-by-play and entering on a dead ball (redzone/decider/cuts.py)."""
from redzone.decider.cuts import DeadTracker, after_kind, plan_entry
from redzone.decider.screen import DelayBook
from redzone.models import League


def nfl_tracker():
    dt = DeadTracker(League.NFL)
    # snaps logged at the whistle: 1000, 1040, 1080; a TV timeout after the third; next snap 1250
    dt.add("1", 1000, 1000, "rush", "run for 3", "Rush", None, 1)
    dt.add("2", 1040, 1040, "pass", "pass for 8", "Pass Reception", None, 1)
    dt.add("3", 1080, 1080, "rush", "run for 1", "Rush", None, 1)
    dt.add("4", 1085, 1085, "timeout", "Timeout", "Official Timeout", None, 1)
    dt.add("5", 1250, 1250, "rush", "run for 2", "Rush", None, 1)
    dt.seen(1300, 10)
    return dt


def test_after_kind_names_the_dead_window():
    assert after_kind(League.NFL, "", "", "Official Timeout") == "tv_timeout"
    assert after_kind(League.NFL, "", "Timeout #1 by MIN at 05:12.", "Timeout") == "team_timeout"
    assert after_kind(League.NFL, "", "Pass complete. The Replay Official reviewed the catch, REVERSED.",
                      "Pass Reception") == "review"
    assert after_kind(League.NBA, "", "x shooting foul", "Shooting Foul") == "shooting_foul"
    assert after_kind(League.NBA, "", "makes free throw 1 of 2", "Free Throw - 1 of 2") == "free_throw"
    assert after_kind(League.NBA, "", "makes free throw 2 of 2", "Free Throw - 2 of 2") is None
    assert after_kind(League.NHL, "", "TV timeout", "Stoppage") == "tv_timeout"
    assert after_kind(League.NHL, "", "Icing", "Stoppage") == "icing"
    assert after_kind(League.NHL, "", "", "Face Off") is None


def test_nfl_windows_run_whistle_to_next_snap():
    w = nfl_tracker().windows()
    # after play 1: 1003 .. 1030 (next snap 1040 - 10 s of play)
    assert (w[0].start, w[0].end, w[0].kind) == (1003, 1030, "huddle")
    brk = w[2]
    assert brk.kind == "tv_timeout" and brk.start == 1083 and brk.end == 1240


def test_landing_mid_play_vs_dead():
    dt = nfl_tracker()
    assert dt.landing(1035, 1036).state == "play"     # the 1030-1040 snap
    assert dt.landing(1010, 1020).state == "dead"
    assert dt.landing(1100, 1200).state == "dead"


def test_plan_enters_as_is_on_a_dead_ball():
    dt = nfl_tracker()
    # the picture comes up at real time now + 5 - 40 = 1015 +- 3: inside the first huddle
    p = plan_entry(dt, 40.0, 3.0, 1050.0, 5.0, can_rewind=True)
    assert p.ok and p.rewind_s == 0


def test_plan_rewinds_to_the_line_up_before_the_play_in_progress():
    dt = nfl_tracker()
    # lands at real 1035 (mid-snap 1030-1040): rewind whole 10 s steps to the huddle before it
    p = plan_entry(dt, 40.0, 3.0, 1070.0, 5.0, can_rewind=True)
    assert p.ok and p.rewind_s == 10.0
    assert dt.landing(1035 - 10 - 3, 1035 - 10 + 3).state == "dead"


def test_plan_waits_when_it_cannot_rewind():
    dt = nfl_tracker()
    p = plan_entry(dt, 40.0, 3.0, 1070.0, 5.0, can_rewind=False)
    assert not p.ok and "waiting for a dead ball" in p.why


def test_nba_restart_worked_back_from_the_clock():
    dt = DeadTracker(League.NBA)
    dt.add("a", 2000, 2000, "jumper", "makes jumper", "Jump Shot", 300.0, 2)
    dt.add("b", 2010, 2010, "foul", "shooting foul", "Shooting Foul", 290.0, 2)
    dt.add("c", 2030, 2030, "free_throw_-_1_of_2", "makes free throw 1 of 2", "Free Throw - 1 of 2", 290.0, 2)
    dt.add("d", 2050, 2050, "free_throw_-_2_of_2", "makes free throw 2 of 2", "Free Throw - 2 of 2", 290.0, 2)
    dt.add("e", 2080, 2080, "jumper", "misses jumper", "Jump Shot", 280.0, 2)
    w = dt.windows()
    # dead from the foul until the last free throw (the clock never ran): the ball is live after it
    assert len(w) == 1 and (w[0].start, w[0].end, w[0].kind) == (2010, 2050, "shooting_foul") and w[0].exact
    # a timeout, then the first clock-moving event at 2300 after 10 s ran: restart ~2290 less a 3 s margin
    dt.add("f", 2100, 2100, "timeout", "Full timeout", "Full Timeout", 270.0, 2)
    dt.add("g", 2101, 2101, "substitution", "enters the game", "Substitution", 270.0, 2)
    dt.add("h", 2300, 2300, "jumper", "makes jumper", "Jump Shot", 260.0, 2)
    w = dt.windows()
    assert (w[-1].start, w[-1].end, w[-1].kind) == (2100, 2287, "timeout_long") and w[-1].exact


def test_nba_open_window_floors_and_frozen_wallclock():
    dt = DeadTracker(League.NBA)
    dt.add("a", 2000, 2000, "jumper", "makes jumper", "Jump Shot", 300.0, 2)
    dt.add("b", 2010, 2010, "timeout", "Full timeout", "Full Timeout", 290.0, 2)
    dt.seen(2020, 10)
    # the quarter's first timeout: surely dead 100 s even with nothing logged after it
    assert dt.landing(2050, 2100).state == "dead"
    frozen = DeadTracker(League.NBA)
    frozen.add("a", 2000, 2000, "jumper", "makes jumper", "Jump Shot", 300.0, 2)
    frozen.add("b", 2000, 2000, "jumper", "makes jumper", "Jump Shot", 280.0, 2)   # clock ran, wallclock didn't
    frozen.add("c", 2000, 2000, "timeout", "Full timeout", "Full Timeout", 280.0, 2)
    frozen.seen(2020, 10)
    assert frozen.landing(2050, 2060).state != "dead"


def test_nhl_window_ends_at_the_faceoff():
    dt = DeadTracker(League.NHL)
    dt.add("a", 3000, 3000, "shot", "shot on goal", "Shot", 600.0, 1)
    dt.add("b", 3005, 3005, "stoppage", "TV timeout", "Stoppage", 600.0, 1)
    dt.add("c", 3150, 3150, "faceoff", "won faceoff", "Face Off", 600.0, 1)
    w = dt.windows()
    assert (w[0].start, w[0].end, w[0].kind) == (3005, 3150, "tv_timeout")


def test_dead_until_and_resume_eta():
    dt = nfl_tracker()
    assert dt.dead_until(1100) == 1240          # the TV timeout ends at the 1250 snap (minus its 10 s)
    assert dt.dead_until(1035) is None          # mid-snap
    assert dt.resume_eta(1100) == 1240 and dt.resume_eta(1035) == 1035
    nhl = DeadTracker(League.NHL)
    nhl.add("a", 3000, 3000, "shot", "shot on goal", "Shot", 600.0, 1)
    nhl.add("b", 3005, 3005, "stoppage", "TV timeout", "Stoppage", 600.0, 1)
    nhl.seen(3020, 10)
    assert nhl.dead_until(3010) == 3005 + 130   # still open: the shortest TV timeout on record
    assert nhl.resume_eta(3010) == 3005 + 149   # typical


def test_delaybook_behind_is_part_of_mu_until_a_fresh_tune():
    book = DelayBook({"youtubetv": 40.0}, 3.0, clock=lambda: 0.0)
    book.set_behind("youtubetv:espn", 20.0)
    assert book.get("youtubetv:espn").mu == 60.0
    book.calibrate("youtubetv:espn", 41.0)       # a new measurement keeps the rewind on top
    assert book.get("youtubetv:espn").mu == 61.0
    book.set_behind("youtubetv:espn", 0.0)
    assert book.get("youtubetv:espn").mu == 41.0


# ----------------------------------------------------------------------------- the decider


def _decider(cfg, clock):
    from redzone.decider.decider import Decider
    cfg.defaults["league_overrides"] = cfg.data["league_overrides"] = {}   # live cut timing (NFL's RC7 set: tests elsewhere)
    dec = Decider(cfg, DelayBook({"youtubetv": 40.0, "prime": 40.0}, 3.0, clock=clock), clock=clock)
    dec.entry_cost = lambda target: 5.0
    return dec


def _cands(target):
    from redzone.decider.decider import Candidate
    from redzone.scoring.engine import Scored
    s = Scored("b", League.NFL, 80, 80, 80, 1.0, 0.0, "reason b", [], False, None, None, 1.0)
    return {"b": Candidate(s, target)}


def _feed(dec):
    tl = dec.timeline("b", League.NFL)
    for m in nfl_tracker().marks.values():
        tl.dead.add(m.pid, m.lo, m.hi, "rush" if m.after == "huddle" else "timeout", "",
                    "Official Timeout" if m.after == "tv_timeout" else "Rush", None, 1)
    tl.dead.seen(1300, 10)
    tl.fresh_at = 1069.0


def test_decider_rewinds_into_the_huddle_instead_of_joining_mid_play(cfg, clock):
    dec = _decider(cfg, clock)
    cfg.update({"decider": {"entry": {"cue": "seek"}}}, record=False)   # the planner's stall model; the rewind itself always runs hidden (P1-REVIEW-03)
    _feed(dec)
    clock.t = 1070.0        # the picture would come up at real 1035: the 1030-1040 snap
    d = dec.decide(_cands("youtubetv:b"))
    # [seek model] one 15 s step + the 3 s mid stall (live lab, Nov 4: the stall lands it further back)
    assert d.action == "switch" and d.target == "b" and d.rewind_s == 18.0
    assert dec.delays.get("youtubetv:b").behind == 18.0 and dec.delays.get("youtubetv:b").mu == 58.0


def test_decider_waits_for_a_dead_ball_where_it_cannot_rewind(cfg, clock):
    dec = _decider(cfg, clock)
    _feed(dec)
    clock.t = 1070.0
    d = dec.decide(_cands("prime:b"))
    assert d.action == "stay" and d.want == "b" and "waiting for a dead ball" in d.reason
    clock.t = 1095.0        # now it lands at real 1060: the huddle after the 1040 snap
    d = dec.decide(_cands("prime:b"))
    assert d.action == "switch" and d.target == "b" and d.rewind_s == 0.0


def test_team_timeout_is_ridden_out_but_a_tv_timeout_is_worth_a_visit(cfg, clock):
    from redzone.decider.screen import Event
    from redzone.scoring.engine import Scored
    s = Scored("b", League.NFL, 50, 50, 50, 1.0, 0.0, "r", [], True, None, None, 1.0)
    for ttext, text, short in (("Timeout", "Timeout #1 by MIN", True), ("Official Timeout", "Timeout", False)):
        dec = _decider(cfg, clock)
        tl = dec.timeline("b", League.NFL)
        tl.dead.add("1", 1000, 1000, "rush", "run for 3", "Rush", None, 1)
        tl.dead.add("2", 1002, 1002, "timeout", text, ttext, None, 1)
        tl.dead.seen(1010, 8)
        tl.events.append(Event(1002, "stop_start", "timeout"))
        dec._known["b"] = _cands("youtubetv:b")["b"]
        clock.t = 1050.0      # the screen (40 s behind) shows real 1010: ten seconds into the stop
        assert dec._short_stop("b", s) is short


def test_short_stops_are_ridden_out_in_basketball_but_not_hockey(cfg, clock):
    """NBA joins the NFL (scorecards, Nov 3: mid-play entries 44 -> 36, nothing else changed);
    hockey's whistles still get a visit."""
    from redzone.decider.screen import Event
    from redzone.scoring.engine import Scored
    for league, short in ((League.NBA, True), (League.NHL, False)):
        dec = _decider(cfg, clock)
        tl = dec.timeline("b", league)
        tl.dead.add("1", 1000, 1000, "jumpshot", "makes 18-foot jumper", "Jump Shot", None, 1)
        tl.dead.add("2", 1002, 1002, "timeout", "timeout", "Timeout", None, 1)
        tl.dead.seen(1010, 8)
        tl.events.append(Event(1002, "stop_start", "timeout"))
        dec._known["b"] = _cands("youtubetv:b")["b"]
        clock.t = 1050.0
        s = Scored("b", league, 50, 50, 50, 1.0, 0.0, "r", [], True, None, None, 1.0)
        assert dec._short_stop("b", s) is short


def test_hockey_waits_for_a_whistle_on_the_way_in_but_never_into_crunch(cfg, clock):
    """[nhl pp] Never inside crunch's last 2:00 (or OT). The 3:00-2:00 run-up waits for a whistle
    like any entry: a whistle comes before 2:00, and crunch entries there were most of NHL's
    mid-play landings (PR #3 review)."""
    from types import SimpleNamespace as NS
    from redzone.decider.decider import Decision, State
    from redzone.scoring.engine import Scored
    dec = _decider(cfg, clock)

    def x(league, crunch=False, now=False):
        return NS(scored=Scored("b", league, 50, 50, 50, 1.0, 0.0, "r", [], False, None, None, 1.0,
                                crunch=crunch, crunch_now=now))

    def d(kind):
        return Decision(0.0, "switch", "b", "r", State.WATCHING, "", kind=kind)
    assert dec._entry_only(x(League.NHL), d("better"))
    # [D-01] the last 2:00 waits like any entry: no go-in-live exit without decider.entry.allow_live_fallback
    assert dec._crunch_entry_mode() == "wait"
    assert dec._entry_only(x(League.NHL, crunch=True, now=True), d("crunch"))
    assert dec._entry_only(x(League.NHL, crunch=True), d("crunch"))           # the run-up waits
    assert dec._entry_only(x(League.NHL), d("start"))          # [D-02] every restart is a 'start': it waits too
    assert dec._entry_only(x(League.NBA), d("better"))         # NBA waits too since Nov 3 (capped: max_wait_s)
    assert dec._entry_only(x(League.MLB), d("better"))         # [D-02] MLB waits between pitches / batters
