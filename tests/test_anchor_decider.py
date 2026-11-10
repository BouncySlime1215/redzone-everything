"""[anchor.enabled] Anchor mode in the decider, on small synthetic games, and flag off decision-identical on a fixture night.

Why: the anchor holds a crunch game through every live play and airs a clip only inside a stoppage it can surely come back
from; these pin the hold, the fill, the return before the restart, the refusal when the gap is too small, and that the flag off
changes nothing.
"""
import pytest

from redzone.decider.decider import Decider, Decision, Missed, State
from redzone.decider.screen import Event
from redzone.models import League
from redzone.noggin import anchor as A
from redzone.scoring.engine import Scored
from tests.test_decider import calibrate, cands, dec, on_break_now, sc, start_on  # noqa: F401  (dec is a fixture)
from tests.test_timing_truth import _night_decisions


def crunch(gid, score, personal=0.0, is_break=False):
    return Scored(gid, League.NFL, score, score, score, 1.0, personal, f"reason {gid}", [], is_break,
                  None, None, 1.0, crunch=True, crunch_now=True)


def anchored(dec, clock, **cfg):
    """On game a (crunch, the anchor) with b live elsewhere; anchor mode on."""
    dec.cfg.data.setdefault("anchor", {})["enabled"] = True
    for k, v in cfg.items():
        dec.cfg.data["anchor"][k] = v
    calibrate(dec, "youtubetv:a")
    start_on(dec, clock, "a", {})
    dec.timeline("b", League.NFL)
    return dec


def missed_b(clock, age=20.0):
    return Missed("b", "td1", "TD", "td", clock.t - age, 75.0)


def test_the_anchor_holds_through_live_plays(dec, clock):
    anchored(dec, clock)
    dec.queue.append(missed_b(clock))
    dec.timeline("a", League.NFL).events.append(Event(clock.t, "play", "pass for 9"))
    clock.t += 30                                                       # that play is on screen now
    d = dec.decide(cands(a=crunch("a", 40), b=95))
    assert dec._anchor == "a" and dec.anchor_m.is_("holding") and dec.anchor_m.why.startswith("anchor a")
    assert d.action == "stay" and d.target == "a" and d.reason.startswith("anchor")


def test_a_clip_airs_in_an_anchor_stoppage_and_the_screen_returns_before_the_restart(dec, clock):
    anchored(dec, clock)
    m = missed_b(clock)
    dec.queue.append(m)
    on_break_now(dec, clock, "a", "two_minute_warning")
    d = dec.decide(cands(a=crunch("a", 40, is_break=True), b=60))
    assert d.action == "replay" and d.replay is m and dec._anchor_owns(d)
    restart, secs = dec._anchor_back
    flip, margin = A.costs(dec.cfg).flip, A.knob(dec.cfg, "margin_s")
    assert secs == pytest.approx(3.0 + 10.0 + 1.0)                     # lead-in, an NFL play, a second
    assert clock.t + 2 * flip + secs <= restart - margin
    assert dec.replay_until == pytest.approx(clock.t + secs)            # a short clip: exactly its seconds on screen
    assert dec.home_break_visit == "a"
    clock.t += 5
    dec.decide(cands(a=crunch("a", 40, is_break=True), b=60))
    assert dec.anchor_m.is_("filling")
    clock.t = dec.replay_until + 0.1
    d = dec.decide(cands(a=crunch("a", 40, is_break=True), b=60))
    assert d.action == "switch" and d.target == "a"                     # one flip back to the anchor
    assert clock.t + flip <= restart - margin
    dec.decide(cands(a=crunch("a", 40, is_break=True), b=60))
    assert dec.anchor_m.is_("holding")


def test_nothing_starts_when_the_budget_is_too_small(dec, clock):
    anchored(dec, clock)
    dec.queue.append(missed_b(clock))
    on_break_now(dec, clock, "a", "timeout")
    clock.t += 15                                                       # 20 s into a timeout whose q10 is 35 s
    d = dec.decide(cands(a=crunch("a", 40, is_break=True), b=60))
    assert d.action == "stay" and d.target == "a" and d.reason.startswith("anchor")
    assert dec.anchor_counts["no_budget"] + dec.anchor_counts["nothing_fits"] >= 1


def test_a_plain_dead_ball_is_never_filled(dec, clock):
    anchored(dec, clock)
    dec.queue.append(missed_b(clock))
    on_break_now(dec, clock, "a", "none")
    d = dec.decide(cands(a=crunch("a", 40, is_break=True), b=60))
    assert d.action == "stay" and d.target == "a"


def test_the_flip_is_vetoed_once_the_anchor_restarts(dec, clock):
    anchored(dec, clock)
    dec.queue.append(missed_b(clock))
    on_break_now(dec, clock, "a", "two_minute_warning")
    d = dec.decide(cands(a=crunch("a", 40, is_break=True), b=60), commit=False)
    assert d.action == "replay" and dec.anchor_veto(d, clock.t) is False
    restart, secs = dec._anchor_back
    assert dec.anchor_veto(d, restart - secs) is True                  # a flip that late would come back after the restart
    dec.timeline("a", League.NFL).events.append(Event(clock.t + 1, "stop_end"))
    assert dec.anchor_veto(d, clock.t + 1) is True                    # the anchor restarted in the data: nothing starts
    other = Decision(clock.t, "replay", "b", "x", State.REPLAY, kind="replay", replay=missed_b(clock))
    assert dec.anchor_veto(other, clock.t) is False                   # not the anchor's clip: not this rule's business


def test_the_flip_names_the_anchor_veto(tmp_path, monkeypatch):
    """The engine asks Decider.anchor_veto at every poll of the flip, whatever commit_gate.enabled says."""
    import time

    from tests.conftest import nfl
    from tests.test_p1_rootcause import _engine
    e = _engine(tmp_path)
    e.merger.games["b"] = nfl("b")
    got = {}
    monkeypatch.setattr(e.control, "submit", lambda target, back, done, **kw: got.update(done=done, **kw))
    m = Missed("b", "td1", "TD", "td", time.time() - 20, 75.0)
    d = Decision(time.time(), "replay", "b", "catch-up", State.REPLAY, kind="replay", replay=m)
    seen = []
    monkeypatch.setattr(e.decider, "anchor_veto", lambda dd, now: seen.append(dd) or True)
    e._act(d, None, {})
    assert "may_cut" in got
    may_cut = got["may_cut"]
    assert may_cut() is False and may_cut.veto == "anchor_return" and seen[-1] is d


def test_flag_off_the_crunch_rules_still_refuse(dec, clock):
    calibrate(dec, "youtubetv:a")
    start_on(dec, clock, "a", {})
    dec.timeline("b", League.NFL)
    dec.queue.append(missed_b(clock))
    on_break_now(dec, clock, "a", "timeout")
    d = dec.decide(cands(a=crunch("a", 40, is_break=True), b=60))
    assert dec._anchor is None and "anchor" not in d.reason


def test_a_bigger_crunch_takes_the_anchor_only_past_the_hysteresis(dec, clock):
    anchored(dec, clock)
    dec.decide(cands(a=crunch("a", 40), b=60))
    assert dec._anchor == "a"
    dec.timeline("a", League.NFL).scores.append(Event(clock.t, "score", score=(20, 13)))   # a: 7 points, b: 3
    dec.timeline("b", League.NFL).scores.append(Event(clock.t, "score", score=(10, 7)))
    for gid in ("a", "b"):
        dec.timeline(gid, League.NFL).clocks.append((clock.t, 4, 200.0))   # 3:20 left: a 2.2, b 2.3 (not 1.3 x)
    dec.decide(cands(a=crunch("a", 40), b=crunch("b", 50)))
    assert dec._anchor == "a"
    dec.timeline("b", League.NFL).clocks.append((clock.t, 5, 400.0))      # b goes to overtime, a 2-score lead late
    dec.timeline("a", League.NFL).scores.append(Event(clock.t, "score", score=(27, 13)))
    dec.decide(cands(a=crunch("a", 40), b=crunch("b", 50)))
    assert dec._anchor == "b" and "anchor a -> b" in dec.anchor_m.why


# ----- flag off: a whole fixture night decides exactly as before -------------------------------------------------------------------

def test_flag_off_a_night_decides_as_before_the_change(monkeypatch):
    """Same pattern as tests/test_timing_truth.py: with every anchor entry point made to fail, the flag-off night is unchanged,
    so nothing anchor mode added runs with the flag off."""
    after = _night_decisions()
    assert len(after[0]) > 500 and after[1]

    def boom(*a, **k):
        raise AssertionError("anchor code ran with anchor.enabled off")
    for name in ("_anchor_tick", "_anchor_fill", "_anchor_time", "_anchor_gap"):
        monkeypatch.setattr(Decider, name, boom)
    monkeypatch.setattr(A, "pick_anchor", boom)
    monkeypatch.setattr(A, "plan_gap", boom)
    assert _night_decisions() == after


def test_flag_on_the_night_still_runs(monkeypatch):
    on = _night_decisions(**{"anchor.enabled": True})
    assert len(on[0]) > 500
