"""[replay and resume] A catch-up costs buffer, not live play: pause the game on screen at a dead ball,
show the replay, resume on the same frame; repay by skipping ahead in its next break. Flag off."""
from types import SimpleNamespace as NS

from redzone.decider.decider import Decider, Missed
from redzone.lab.backtest import SwitchLog, behind_extra, weighted_pct


def _dec(cfg=None):
    dec = Decider.__new__(Decider)
    dec.cfg = {"replay.resume": True, **(cfg or {})}
    dec.entry_cost = lambda target: 5.0
    dec.delays = NS(held={}, get=lambda t: NS(behind=0.0, current_sigma=lambda now: 1.0))
    dec.target_of = lambda gid: "yt:" + gid
    dec._known = {"g": NS(target="yt:g", scored=NS(league=NS(base=NS(value="nhl"))))}
    dec.resume, dec.resume_log, dec.home_break_visit = None, [], None
    return dec


def test_resume_lands_on_the_frame_we_left():
    """Rule #1: we leave on a dead ball and come back to that same frame (time away + the flip back)."""
    dec = _dec()
    dec.resume = ("g", 1000.0, 12.0)              # paused 12 s behind its buffer already
    d = dec._resume_back({}, 1040.0)
    assert d.action == "switch" and d.target == "g" and d.kind == "resume"
    assert d.rewind_s == 12.0 + 40.0 + 5.0 and dec.resume is None


def test_resume_scopes_to_its_leagues():
    dec = _dec({"replay.resume_leagues": ["nfl"]})
    assert not dec._resume_on(dec._known["g"])
    dec.cfg["replay.resume_leagues"] = ["nhl"]
    assert dec._resume_on(dec._known["g"])
    dec.cfg["replay.resume"] = False
    assert not dec._resume_on(dec._known["g"])


def test_resume_replay_never_pauses_crunch_time_or_past_the_cap():
    dec = _dec({"replay.resume_cap_s": 90})
    dec.current, dec.user = "g", NS(locked_game=None)
    dec.queue = [Missed("h", "p", "goal", "goal", 900.0, 90.0, due_s=600.0, aired=990.0)]
    dec._replay_pool = lambda cands: {"h": object()}
    dec._replayable = lambda x: True
    dec._replay_cost = lambda m, cands: 30.0
    dec._replay_overrun = lambda m, cands, now: 0.0
    safe = NS(safe=True)
    calm = NS(lock=None, crunch=False, crunch_now=False, reason="g")
    d = dec._resume_replay({}, calm, safe, None, "", 1000.0)
    assert d is not None and d.kind == "replay_resume" and d.target == "h"
    assert dec._resume_replay({}, NS(lock=None, crunch=True, crunch_now=True, reason="g"), safe, None, "", 1000.0) is None
    assert dec._resume_replay({}, calm, NS(safe=False), None, "", 1000.0) is None     # only at a dead ball
    dec.cfg["replay.resume_cap_s"] = 30
    assert dec._resume_replay({}, calm, safe, None, "", 1000.0) is None                 # 35 s > the cap


def test_behind_extra_rows():
    sw = [SwitchLog(0.0, None, "a", "live", ""), SwitchLog(100.0, "a", "b", "replay", ""),
          SwitchLog(120.0, "b", "a", "resume", "", 25.0), SwitchLog(200.0, "a", "a", "skip", "", 5.0)]
    rows = behind_extra(sw, 300.0)
    assert rows == [(100.0, 0.0), (80.0, 25.0), (100.0, 5.0)]
    assert weighted_pct(rows, .95) == 25.0 and weighted_pct([], .5) == 0.0


def test_nba_technical_is_a_whistle():
    """[rule #1 grading] 'Defensive 3-Seconds Technical' (DET @ NY, Oct 27) stops play: the grader called a
    leave 72 s later mid-play because the type text has no 'foul'."""
    from redzone.models import League, Stoppage
    from redzone.sources import espn
    for t in ("Defensive 3-Seconds Technical", "Coach Technical", "Non-Unsportsmanlike Technical", "Flagrant Foul Type 1"):
        assert espn.stoppage_for(League.NBA, {"type": {"text": t}, "text": t}) == Stoppage.WHISTLE
