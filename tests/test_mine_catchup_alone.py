"""[replay.when_alone] North Star: your-team big plays seen within 10 min. Owner: lane 2 (noggin logic).
Nothing else worth switching to is no reason to skip your team's catch-up."""
from redzone.decider.decider import Missed
from tests.test_decider import cands, dec, start_on  # noqa: F401


def _setup(dec, clock, on):
    dec.cfg.update({"replay": {"when_alone": on}}, record=False)
    start_on(dec, clock, "a", {"w": 10})
    dec._yours = {"w"}
    dec.queue.append(Missed("w", "g1", "WSH goal", "score", clock.t - 30, 90.0, mine=True))
    dec._gate = lambda cur, now: __import__("redzone.decider.screen", fromlist=["Gate"]).Gate(True, "stoppage")


def test_your_missed_play_goes_when_nothing_else_is_worth_a_switch(dec, clock, monkeypatch):
    _setup(dec, clock, True)
    monkeypatch.setattr(type(dec), "_best", lambda self, w, exclude=None: None)
    d = dec.decide(cands(a=40, w=10))
    assert d.action == "replay" and d.replay.game_id == "w"


def test_off_by_default_keeps_the_old_stay(dec, clock, monkeypatch):
    _setup(dec, clock, False)
    monkeypatch.setattr(type(dec), "_best", lambda self, w, exclude=None: None)
    d = dec.decide(cands(a=40, w=10))
    assert d.action != "replay"
