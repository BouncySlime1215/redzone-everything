"""[EXP-036] Phone commitments: only my teams, stop after this game, end at a time. North Star: P20/P19/P17
(finale) via the phone; guardrail never join mid-play. Owner: lane 2 (decider); lane 5 UI, lane 3 command path (#194, UserState.commit).
Controls only, never a training signal. Flag decider.commitment.enabled ships OFF."""
import dataclasses

from redzone.decider.decider import State
from redzone.decider.screen import Event
from redzone.models import League
from tests.test_decider import calibrate, cands, dec, sc, start_on  # noqa: F401


def _on(dec, **cm):
    dec.cfg.update({"decider": {"commitment": {"enabled": True}}}, record=False)
    dec.user.commit = cm


def _mine(gid, score):
    return dataclasses.replace(sc(gid, score), personal=10.0)


def _ready(dec, clock, **others):
    calibrate(dec, "youtubetv:a")
    calibrate(dec, "youtubetv:b")
    dec.timeline("b", League.NFL)
    start_on(dec, clock, "a", others)


def _play_on_screen(dec, clock):
    dec.timeline("a", League.NFL).events.append(Event(clock.t, "play", "run for 4"))
    clock.t += 35   # mid-air on screen (see test_never_cuts_while_a_play_is_on_screen)


def test_off_by_default_ignores_a_commitment(dec, clock):
    _ready(dec, clock)
    dec.user.commit = {"mode": "my_teams"}
    assert dec.decide(cands(a=30, b=80)).target == "b"


def test_my_teams_never_switches_to_another_game(dec, clock):
    _ready(dec, clock)
    _on(dec, mode="my_teams")
    d = dec.decide(cands(a=30, b=80))
    assert d.action == "stay" and d.target == "a"


def test_my_teams_picks_your_game_over_a_better_one(dec, clock):
    calibrate(dec, "youtubetv:c")
    dec.timeline("c", League.NFL)
    _ready(dec, clock)
    _on(dec, mode="my_teams")
    d = dec.decide(cands(a=30, b=95, c=_mine("c", 80)))
    assert d.action == "switch" and d.target == "c"


def test_my_teams_still_never_joins_mid_play(dec, clock):
    calibrate(dec, "youtubetv:c")
    dec.timeline("c", League.NFL)
    _ready(dec, clock)
    _on(dec, mode="my_teams")
    _play_on_screen(dec, clock)
    d = dec.decide(cands(a=30, c=_mine("c", 90)))
    assert d.action == "stay"


def test_end_at_stops_switching_then_completes_on_a_dead_ball(dec, clock):
    _ready(dec, clock)
    _on(dec, mode="end_at", at=clock.t)
    _play_on_screen(dec, clock)
    d = dec.decide(cands(a=30, b=80))
    assert d.action == "stay" and "next dead ball" in d.reason and dec.slate_done is None
    clock.t += 20   # the play has aired: dead ball on screen
    d = dec.decide(cands(a=30, b=80))
    assert (d.action, d.kind, d.state) == ("stay", "slate_complete", State.IDLE) and dec.slate_done
    clock.t += 300
    assert dec.decide(cands(a=10, b=99)).kind == "slate_complete"


def test_end_at_before_the_time_changes_nothing(dec, clock):
    _ready(dec, clock)
    _on(dec, mode="end_at", at=clock.t + 3600)
    assert dec.decide(cands(a=30, b=80)).target == "b"


def test_stop_after_completes_when_your_game_ends(dec, clock):
    _ready(dec, clock)
    _on(dec, mode="stop_after", game_id="a")
    assert dec.decide(cands(a=60, b=62)).action == "stay"      # still on, nothing changes
    clock.t += 200
    d = dec.decide(cands(b=80))                                # "a" final and long since aired
    assert d.kind == "slate_complete" and dec.slate_done == "your game ended"


def test_stop_after_waits_while_another_game_is_mid_play(dec, clock):
    calibrate(dec, "youtubetv:c")
    _ready(dec, clock)
    _on(dec, mode="stop_after", game_id="c")
    dec._known["c"] = cands(c=50)["c"]
    _play_on_screen(dec, clock)
    d = dec.decide(cands(a=30, b=31))                          # "c" is over, but "a" is mid-play
    assert d.action == "stay" and dec.slate_done is None


def test_a_user_lock_beats_the_commitment_and_clearing_reopens(dec, clock):
    _ready(dec, clock)
    _on(dec, mode="end_at", at=clock.t)
    clock.t += 100
    assert dec.decide(cands(a=30, b=80)).kind == "slate_complete"
    dec.user.commit = None
    assert dec.slate_done is not None
    d = dec.decide(cands(a=30, b=80))
    assert d.kind != "slate_complete" and dec.slate_done is None
