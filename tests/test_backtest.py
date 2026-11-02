"""The backtest's record of what was on screen must match what the decider actually did."""
from redzone.decider.decider import Decision, Missed, State
from redzone.lab import backtest
from redzone.models import League


class _NoData(backtest.Replay):
    def state_at(self, idx):
        return None


class _Scripted:
    """A decider that plays back fixed decisions, one per poll."""

    def __init__(self, script):
        self.script = list(script)
        self.current = None

    def decide(self, cands, redzone=None):
        d = self.script.pop(0) if self.script else Decision(0, "stay", self.current, "", State.WATCHING)
        if d.action in ("switch", "replay"):
            self.current = d.target
        return d


def test_back_to_live_after_replaying_the_same_game_is_a_switch(cfg, monkeypatch):
    t0 = 1000.0
    m = Missed("g", "p1", "TD", "td", t0, 75)
    script = [Decision(t0, "switch", "g", "start", State.WATCHING, kind="start"),
              Decision(t0, "replay", "g", "catch-up", State.REPLAY, kind="replay", replay=m),
              Decision(t0, "stay", "g", "replay", State.REPLAY, kind="replay", replay=m),
              Decision(t0, "switch", "g", "back to your game, live", State.WATCHING, kind="home")]
    monkeypatch.setattr(backtest, "Decider", lambda *a, **k: _Scripted(script))
    r = _NoData(League.NFL, "g", {}, [{"id": "1", "text": "run", "type": {"text": "Rush"}}], [t0 + 100])
    rep = backtest.run([r], cfg, set(), start=t0, end=t0 + 40)
    assert [s.kind for s in rep.switches] == ["start", "replay", "home"]
