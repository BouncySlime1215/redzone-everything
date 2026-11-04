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


# ------------------------------------------------------------------ [auto sync] re-sync modelling

class _Clk:
    t = 5000.0

    def __call__(self):
        return self.t


def _resync_book(cfg):
    from redzone.decider.screen import DelayBook
    clk = _Clk()
    book = DelayBook({"youtubetv": 43.0}, sigma_default=3.0, clock=clk, model="channel")
    d = book.get("youtubetv:g")
    d.calibrated_at, d.sigma = clk.t, 3.0
    sync = backtest._attach_resync(book, cfg, pinned={"youtubetv:g": (43.0, 3.0, clk.t)})
    return book, sync, clk


def test_resync_keeps_the_kickoff_calibration_until_the_filter_has_read_the_screen(cfg):
    book, sync, clk = _resync_book(cfg)
    t0 = clk.t
    clk.t += 600
    book.tuned("youtubetv:g")                           # a tune refreshes: the pin must come back, not the default
    d = book.get("youtubetv:g")
    assert d.source != "sync" and d.mu == 43.0 and d.sigma == 3.0 and d.calibrated_at == t0


def test_resync_reads_the_channel_on_screen_after_the_picture_is_up(cfg):
    import random
    book, sync, clk = _resync_book(cfg)
    log = [backtest.SwitchLog(clk.t, None, "g", "start", "")]
    tgt = {"g": "youtubetv:g"}
    sw = Decision(clk.t, "switch", "g", "start", State.WATCHING, kind="start")
    backtest._resync_tick(book, sync, sw, log, tgt, 43.0, 5.0, clk.t, random.Random(0))
    assert sync.filters["youtubetv:g"].updated is None or sync.filters["youtubetv:g"].updated < clk.t
    stay = Decision(clk.t, "stay", "g", "", State.WATCHING)
    for _ in range(8):
        clk.t += 10
        backtest._resync_tick(book, sync, stay, log, tgt, 43.0, 5.0, clk.t, random.Random(clk.t))
    d = book.get("youtubetv:g")
    assert d.source == "sync" and abs(d.mu - 43.0) < 2.0 and d.sigma == 4.0   # the live floor, not below
    assert d.calibrated_at == clk.t                     # fresh while watched: its doubt doesn't grow


def test_resync_takes_no_reading_during_a_replay(cfg):
    import random
    book, sync, clk = _resync_book(cfg)
    m = Missed("g", "p1", "TD", "td", clk.t, 75)
    log = [backtest.SwitchLog(clk.t - 60, None, "g", "replay", "")]
    rp = Decision(clk.t, "stay", "g", "replay", State.REPLAY, kind="replay", replay=m)
    for _ in range(6):
        clk.t += 10
        backtest._resync_tick(book, sync, rp, log, {"g": "youtubetv:g"}, 43.0, 5.0, clk.t, random.Random(0))
    assert book.get("youtubetv:g").source != "sync"


def test_resync_is_off_by_default(cfg):
    seen = []
    r = _NoData(League.NFL, "g", {}, [{"id": "1", "text": "run", "type": {"text": "Rush"}}], [1100.0])
    backtest.run([r], cfg, set(), start=1000.0, end=1040.0, on_tick=lambda t, dec: seen.append(dec.delays.sync))
    assert seen and all(s is None for s in seen)
    seen.clear()
    backtest.run([r], cfg, set(), start=1000.0, end=1040.0, resync=True,
                 on_tick=lambda t, dec: seen.append(dec.delays.sync))
    assert seen and all(s is not None for s in seen)


def test_decide_step_ticks_between_feed_polls(cfg):
    """[decide step] The sim can decide as often as the live app (engine._decide_loop, 0.5 s) while the
    feed is still polled every polling.live_s; off (None) it ticks once per poll, as before."""
    r = _NoData(League.NFL, "g", {}, [{"id": "1", "text": "run", "type": {"text": "Rush"}}], [1100.0])
    ticks = []
    backtest.run([r], cfg, set(), start=1000.0, end=1040.0, on_tick=lambda t, dec: ticks.append(t))
    assert ticks == [1000.0, 1010.0, 1020.0, 1030.0, 1040.0]
    ticks.clear()
    backtest.run([r], cfg, set(), start=1000.0, end=1040.0, decide_step=1.0, on_tick=lambda t, dec: ticks.append(t))
    assert len(ticks) == 41 and ticks[1] - ticks[0] == 1.0
