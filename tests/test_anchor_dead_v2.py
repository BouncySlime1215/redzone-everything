"""[timing.dead_v2, off] Anchor mode on the class and elapsed-time dead-time table, on small synthetic games and a fixture night.

Why: at the 99% level the pooled table gave an NFL timeout no room for a clip; the feed's own official / team label and the time
a stoppage has already outlasted do (research ledger L1, simulated on recorded nights). Flag off, the anchor reads exactly what
it read before.
"""
from pathlib import Path

import pytest
import yaml

from redzone.decider import screen
from redzone.decider.decider import Decider
from redzone.decider.screen import Event
from redzone.models import League
from redzone.noggin import anchor as A
from redzone.scoring import dead_left
from redzone.scoring.engine import Scored
from tests.test_anchor_decider import anchored, crunch, missed_b
from tests.test_decider import calibrate, cands, dec, start_on  # noqa: F401  (dec is a fixture)
from tests.test_timing_truth import _night_decisions

CONFIG = yaml.safe_load((Path(A.__file__).parent.parent / "default_config.yaml").read_text())


class Cfg:
    def __init__(self, **v):
        self.v = {"timing.measured": True, **v}

    def get(self, k, d=None):
        return self.v.get(k, d)


V1, V2 = Cfg(), Cfg(**{"timing.dead_v2": True})
FLIP, MARGIN = 2.2, 3.0                     # the measured flip (timing.measured on) and anchor.margin_s


def v2_on(dec, **anchor_cfg):
    dec.cfg.data.setdefault("timing", {})["dead_v2"] = True
    dec.cfg.data.setdefault("anchor", {}).update(anchor_cfg)
    return dec


# ----- the planner ------------------------------------------------------------------------------------------------------------------

def test_the_flag_ships_off_beside_the_old_quantile():
    assert CONFIG["timing"]["dead_v2"] is False
    assert CONFIG["anchor"]["quantile"] == 0.10 and CONFIG["anchor"]["alpha"] == 0.01 == A.PRIORS["alpha"]
    assert A.dead_v2(None) is False and A.dead_v2(V1) is False and A.dead_v2(V2) is True


def test_flag_off_the_anchor_reads_the_pooled_quantile_whatever_the_class():
    for cls in (None, "official", "team"):
        assert A.guaranteed_s("nfl", "timeout", 0.0, V1, cls=cls) == 35.0 == dead_left.guaranteed_left("nfl", "timeout", 0.0, 0.10)
        assert A.guaranteed_s("nfl", "timeout", 10.0, V1, 2.0, cls, 30.0) == 35.0 - 10.0 - 4.0
        assert A.gap_budget("nfl", "timeout", 0.0, V1, cls=cls) == pytest.approx(35.0 - 2 * FLIP - MARGIN)


def test_flag_on_the_anchor_reads_the_class_and_the_elapsed_time_at_anchor_alpha():
    assert A.guaranteed_s("nfl", "timeout", 0.0, V2, cls="official") == 29.0
    assert A.guaranteed_s("nfl", "timeout", 0.0, V2, cls="team") == 12.0
    assert A.guaranteed_s("nfl", "timeout", 0.0, V2) == 16.0 == A.guaranteed_s("nfl", "timeout", 0.0, V2, cls="")   # unread: pooled
    assert A.guaranteed_s("nba", "timeout", 0.0, V2) == 3.0 and A.guaranteed_s("nba", "timeout", 30.0, V2) == 54.0
    assert A.gap_budget("nfl", "timeout", 0.0, V2, cls="official") == pytest.approx(29.0 - 2 * FLIP - MARGIN)   # one 14 s clip
    assert A.gap_budget("nfl", "timeout", 0.0, V2, cls="team") == pytest.approx(12.0 - 2 * FLIP - MARGIN)
    assert A.gap_budget("nba", "timeout", 0.0, V2) == 0.0
    loose = Cfg(**{"timing.dead_v2": True, "anchor.alpha": 0.10})
    assert A.guaranteed_s("nfl", "timeout", 0.0, loose, cls="official") == 110.0
    assert A.guaranteed_s("nfl", "none", 0.0, V2) == 0.0 == A.guaranteed_s("nba", "whistle", 30.0, V2)   # still never a gap


def test_flag_on_delay_doubt_reads_the_least_bound_over_the_elapsed_window():
    assert A.guaranteed_s("nfl", "timeout", 0.0, V2, 3.0, "official") == pytest.approx(29.0 - 6.0)      # 2 sigma later
    assert A.guaranteed_s("nba", "timeout", 30.0, V2, 3.0) == pytest.approx(29.0 - 10.0)   # it may only have run 24 s: the 20 s cell
    assert A.guaranteed_s("nba", "timeout", 36.0, V2, 3.0) == pytest.approx(54.0 - 12.0)   # surely past 30 s now
    assert A.guaranteed_s("nba", "timeout", 36.0, V2, 3.0, spread_s=10.0) == pytest.approx(29.0 - 10.0)   # its start is unsure by 10 s
    for e in range(0, 140, 7):
        assert A.guaranteed_s("nfl", "timeout", e, V2, 4.0, "official", 5.0) <= dead_left.guaranteed_left_v2("nfl", "timeout", e, 0.01, "official")


# ----- the decider: the class and the elapsed time decide the gap -------------------------------------------------------------------

def _timeout(dec, clock, cls, seconds_in=5.0):
    """Game a's timeout of this class, `seconds_in` on screen (the fixture's delay is 40 s)."""
    dec.timeline("a", League.NFL).events.append(Event(clock.t, "stop_start", "timeout", stop_class=cls))
    clock.t += 40 + seconds_in


@pytest.mark.parametrize("cls,airs", [("official", True), ("team", False), ("", False)])
def test_an_official_timeout_holds_a_clip_and_a_team_or_unread_one_does_not(dec, clock, cls, airs):
    v2_on(anchored(dec, clock), alpha=0.05)             # at the 95% level: official 70 s, team 21 s, pooled 28 s at the start
    m = missed_b(clock)
    dec.queue.append(m)
    _timeout(dec, clock, cls)
    d = dec.decide(cands(a=crunch("a", 40, is_break=True), b=60))
    if airs:
        assert d.action == "replay" and d.replay is m and dec._anchor_owns(d)
        restart, secs = dec._anchor_back
        assert clock.t + 2 * A.costs(dec.cfg).flip + secs <= restart - A.knob(dec.cfg, "margin_s")
    else:
        assert d.action == "stay" and d.target == "a" and d.reason.startswith("anchor")
        assert dec.anchor_counts["no_budget"] + dec.anchor_counts["nothing_fits"] == 1 and not dec.anchor_counts["clips"]


def test_flag_off_the_class_changes_nothing(dec, clock):
    """Anchor mode without timing.dead_v2: the same official timeout reads the pooled q10 less the elapsed time, as before."""
    anchored(dec, clock)
    dec.queue.append(missed_b(clock))
    _timeout(dec, clock, "official")
    boom = pytest.MonkeyPatch()
    boom.setattr(A, "guaranteed_left_v2_within", lambda *a, **k: (_ for _ in ()).throw(AssertionError("v2 read with the flag off")))
    try:
        d = dec.decide(cands(a=crunch("a", 40, is_break=True), b=60))
        gap = dec._anchor_gap("a", cands(a=crunch("a", 40, is_break=True), b=60), clock.t)
    finally:
        boom.undo()
    assert d.action == "stay" and gap[2].startswith("timeout: ")              # the old why text, no class in it
    sigma = dec.delays.get("youtubetv:a").current_sigma(clock.t)
    assert gap[1] - clock.t == pytest.approx(min(35.0 - 5.0 - 2 * sigma, dec._next_snap_s("a", cands(a=crunch("a", 40)), clock.t)))


def _hoops(gid, score, is_break=False):
    return Scored(gid, League.NBA, score, score, score, 1.0, 0.0, f"reason {gid}", [], is_break, None, None, 1.0,
                  crunch=True, crunch_now=True)


def test_a_timeout_with_no_room_at_its_start_gets_a_clip_once_it_has_outlasted_30_s(dec, clock):
    """The plan is made again every tick: an NBA timeout promises 3 s at its start and 54 s once it has run 30 s (99% level).
    The stream runs 60 s behind: in a continuous sport the leave gate only calls a timeout on screen while the feed covers it."""
    dec.cfg.data.setdefault("anchor", {})["enabled"] = True
    v2_on(dec)
    tl = dec.timeline("a", League.NBA)
    calibrate(dec, "youtubetv:a", 60.0)
    start_on(dec, clock, "a", {})
    dec.timeline("b", League.NFL)
    tl.events.append(Event(clock.t, "stop_start", "timeout", stop_class="timeout"))
    clock.t += 60 + 10
    dec.queue.append(missed_b(clock))
    d = dec.decide(cands(a=_hoops("a", 40, is_break=True), b=60))
    assert d.action == "stay" and d.target == "a" and d.reason.startswith("anchor") and dec.anchor_counts["no_budget"] == 1
    clock.t += 20                                      # 30 s in: with the delay's doubt it may still be short of 30 s
    assert dec.decide(cands(a=_hoops("a", 40, is_break=True), b=60)).action == "stay"
    assert dec.anchor_counts["nothing_fits"] == 1 and not dec.anchor_counts["clips"]     # 19 s sure: no room for a 14 s clip
    clock.t += 15                                      # 45 s in: surely past 30 s
    m = missed_b(clock)
    dec.queue.append(m)
    d = dec.decide(cands(a=_hoops("a", 40, is_break=True), b=60))
    assert d.action == "replay" and dec._anchor_owns(d) and dec.anchor_counts["clips"] == 1
    restart, secs = dec._anchor_back
    assert restart - clock.t > 25.0 and clock.t + 2 * A.costs(dec.cfg).flip + secs <= restart - A.knob(dec.cfg, "margin_s")


# ----- flag off: a whole fixture night decides exactly as before --------------------------------------------------------------------

def test_flag_off_a_night_decides_as_before_the_change(monkeypatch):
    """Same pattern as tests/test_timing_truth.py. The class beside the label and the v2 table change no decision while
    timing.dead_v2 and anchor.enabled are off; timing.dead_v2 alone (no anchor mode) changes none either."""
    after = _night_decisions()
    assert len(after[0]) > 500 and after[1]
    assert _night_decisions(**{"timing.dead_v2": True}) == after

    def boom(*a, **k):
        raise AssertionError("dead-time v2 code ran with its flags off")
    for name in ("guaranteed_left_v2", "guaranteed_left_v2_within", "cell_v2"):
        monkeypatch.setattr(dead_left, name, boom)
    monkeypatch.setattr(A, "guaranteed_left_v2_within", boom)
    monkeypatch.setattr(Decider, "_anchor_restart_known", boom)                # the restart net is anchor mode's alone
    monkeypatch.setattr(screen, "stoppage_class", lambda *a, **k: "")          # before: no class beside the label
    assert _night_decisions() == after


def test_anchor_mode_without_dead_v2_never_reads_the_v2_table(monkeypatch):
    on = _night_decisions(**{"anchor.enabled": True})
    assert len(on[0]) > 500

    def boom(*a, **k):
        raise AssertionError("the v2 table was read with timing.dead_v2 off")
    monkeypatch.setattr(A, "guaranteed_left_v2_within", boom)
    monkeypatch.setattr(screen, "stoppage_class", lambda *a, **k: "")
    assert _night_decisions(**{"anchor.enabled": True}) == on


def test_flag_on_the_night_still_runs():
    on = _night_decisions(**{"anchor.enabled": True, "timing.dead_v2": True})
    assert len(on[0]) > 500
