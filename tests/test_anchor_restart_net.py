"""[anchor.enabled, Rule #1 net] A gap clip stops the moment the feed shows the anchor's restart, on small synthetic games.

Why: no dead-time bound is certain (research ledger L1: 2 of 598 one-clip trips came back late at the 99% level, simulated on
recorded nights), so while a gap clip is on screen the anchor's stop_end, or a next snap closer than one flip back plus
anchor.margin_s, stops the clip and flips back on that tick; with timing.dead_v2 on nothing starts that the net would stop at once.
"""
import pytest

from redzone.decider.decider import Decider, Missed
from redzone.decider.screen import Event
from redzone.models import League
from redzone.noggin import anchor as A
from tests.test_anchor_dead_v2 import v2_on
from tests.test_anchor_decider import anchored, crunch, missed_b
from tests.test_decider import calibrate, cands, dec, on_break_now, start_on  # noqa: F401  (dec is a fixture)
from tests.test_timing_truth import _night_decisions


def _clip_on_screen(dec, clock, **cfg):
    anchored(dec, clock, **cfg)
    m = missed_b(clock)
    dec.queue.append(m)
    on_break_now(dec, clock, "a", "two_minute_warning")
    d = dec.decide(cands(a=crunch("a", 40, is_break=True), b=60))
    assert d.action == "replay" and d.replay is m and dec.replaying is m
    assert dec._anchor_stop == ("a", dec.timeline("a", League.NFL).last("stop_start"))     # the stoppage the clip was planned in
    clock.t += 4
    dec.decide(cands(a=crunch("a", 40, is_break=True), b=60))
    assert dec.anchor_m.is_("filling") and dec.replaying is m and not dec.anchor_counts["aborted_restart"]
    return m


def _stopped_and_back(dec, clock, m, why):
    d = dec.decide(cands(a=crunch("a", 40, is_break=True), b=60))
    assert d.action == "switch" and d.target == "a"                           # one flip back, on this very tick
    assert dec.anchor_counts["aborted_restart"] == 1 and dec.replaying is None
    assert dec.anchor_m.is_("holding") and why in dec.anchor_m.why
    assert any(to == "after_replay" and "interrupted: anchor restart" in said for _, _, to, said in dec.screen_m.history)
    assert dec.catchup_m.is_("idle")
    assert m in dec.queue and not m.replayed and (m.offset_s, m.piece_s) == (0.0, 0.0)   # still owed, from its top
    assert dec._anchor_clip is None and dec._anchor_back is None and dec._anchor_stop is None
    return d


def test_a_stop_end_in_the_feed_stops_the_clip_and_flips_back_at_once(dec, clock):
    m = _clip_on_screen(dec, clock)
    assert clock.t < dec.replay_until - 5                                     # the clip had seconds left to run
    dec.timeline("a", League.NFL).events.append(Event(clock.t, "stop_end"))
    _stopped_and_back(dec, clock, m, "two_minute_warning ended in the feed")
    clock.t += 1
    d = dec.decide(cands(a=crunch("a", 40), b=60))
    assert d.target == "a" and d.action != "replay" and dec.anchor_counts["aborted_restart"] == 1


def test_a_snap_about_to_air_stops_the_clip(dec, clock):
    m = _clip_on_screen(dec, clock)
    delay = dec.delays.get("youtubetv:a")
    to_snap_air = delay.mu - 2 * delay.current_sigma(clock.t) - 10.0          # play logged -> its snap airs (an NFL play: 10 s)
    dec.timeline("a", League.NFL).events.append(Event(clock.t - to_snap_air + 4.0, "play", "pass for 9"))   # its snap airs in 4 s
    assert dec._next_snap_s("a", cands(a=crunch("a", 40)), clock.t) == pytest.approx(4.0)
    _stopped_and_back(dec, clock, m, "next snap airs in 4 s")


def test_a_newer_stoppage_means_the_planned_one_ended(dec, clock):
    m = _clip_on_screen(dec, clock)
    dec.timeline("a", League.NFL).events.append(Event(clock.t, "stop_start", "timeout"))
    _stopped_and_back(dec, clock, m, "two_minute_warning ended in the feed")


def test_the_net_also_guards_a_dead_v2_clip(dec, clock):
    anchored(dec, clock)
    v2_on(dec)
    m = missed_b(clock)
    dec.queue.append(m)
    dec.timeline("a", League.NFL).events.append(Event(clock.t, "stop_start", "two_minute_warning", stop_class="two_minute_warning"))
    clock.t += 41                                                              # 1 s in: 55 s promised at the 99% level
    d = dec.decide(cands(a=crunch("a", 40, is_break=True), b=60))
    assert d.action == "replay" and "two_minute_warning" in dec._anchor_gap("a", cands(a=crunch("a", 40)), clock.t)[2]
    clock.t += 4
    dec.timeline("a", League.NFL).events.append(Event(clock.t, "stop_end"))
    _stopped_and_back(dec, clock, m, "ended in the feed")


def test_a_clip_runs_to_its_end_while_the_feed_shows_no_restart(dec, clock):
    m = _clip_on_screen(dec, clock)
    for _ in range(3):
        clock.t += 2
        d = dec.decide(cands(a=crunch("a", 40, is_break=True), b=60))
        assert d.action == "stay" and dec.replaying is m
    clock.t = dec.replay_until + 0.1
    d = dec.decide(cands(a=crunch("a", 40, is_break=True), b=60))
    assert d.action == "switch" and d.target == "a" and m.replayed
    assert not dec.anchor_counts["aborted_restart"]


def test_a_replay_that_is_not_the_anchors_clip_is_not_this_nets_business(dec, clock):
    anchored(dec, clock)
    dec.decide(cands(a=crunch("a", 40), b=60))
    assert dec._anchor == "a"
    other = Missed("b", "td9", "TD", "td", clock.t - 20, 75.0)
    dec._anchor_stop = ("a", Event(clock.t - 100, "stop_start", "timeout"))   # a stale plan; no anchor clip is on screen
    assert dec._anchor_restart_known(cands(a=crunch("a", 40), b=60), clock.t) != ""
    dec._start_replay(other, cands(a=crunch("a", 40), b=60), clock.t)
    clock.t += 2
    dec.decide(cands(a=crunch("a", 40), b=60))
    assert dec.replaying is other and not dec.anchor_counts["aborted_restart"]


# ----- with timing.dead_v2 on, the net needs the feed to lead the screen ------------------------------------------------------------

@pytest.mark.parametrize("mu,airs", [(40.0, True), (22.0, False)])
def test_flag_on_nothing_starts_when_the_feed_leads_the_screen_by_too_little(dec, clock, mu, airs):
    """The net needs the feed to show the restart a flip and a margin before it airs; with less lead a clip would only be stopped."""
    dec.cfg.data.setdefault("anchor", {})["enabled"] = True
    v2_on(dec)
    dec.cfg.data["decider"]["crunch"]["shortest_break_s"]["two_minute_warning"] = 0     # no prior: only the feed's own lead counts
    calibrate(dec, "youtubetv:a", mu)
    start_on(dec, clock, "a", {})
    dec.timeline("b", League.NFL)
    dec.queue.append(missed_b(clock))
    dec.timeline("a", League.NFL).events.append(Event(clock.t, "stop_start", "two_minute_warning"))
    clock.t += mu + 3
    d = dec.decide(cands(a=crunch("a", 40, is_break=True), b=60))
    assert (d.action == "replay") is airs
    assert dec.anchor_counts["vetoed_restart"] == (0 if airs else 1)


# ----- the net is anchor mode's alone ---------------------------------------------------------------------------------------------

def test_without_anchor_mode_the_net_never_runs(monkeypatch):
    after = _night_decisions()
    assert len(after[0]) > 500

    def boom(*a, **k):
        raise AssertionError("the anchor's restart net ran with anchor.enabled off")
    monkeypatch.setattr(Decider, "_anchor_restart_known", boom)
    assert _night_decisions() == after
    assert _night_decisions(**{"timing.dead_v2": True}) == after
