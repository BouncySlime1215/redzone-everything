"""[SYNC 1] Flip only onto a steady PLAYING stream; book buffering into the delay; count settles."""
import asyncio

import pytest

from redzone import control as ctl
from redzone.channels import ChannelMap
from redzone.tuners.adb import Stick

T = "youtubetv:fox"


class Scripted(Stick):
    """Plays back a script of reports, one per read (the last one repeats)."""

    def __init__(self, reports):
        super().__init__("b:5555", name="b", os="Fire OS 8")
        self.packages = {"youtubetv": "yttv.pkg"}
        self.reports, self.reads = list(reports), 0

    async def playback(self, package=None):
        r = self.reports[min(self.reads, len(self.reports) - 1)]
        self.reads += 1
        return dict(r, pkg="yttv.pkg")


def pb(state, position, updated):
    return {"state": state, "position": position, "updated": updated}


@pytest.fixture
def c(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(ctl, "FLIP_STEADY_S", 0.05)
    monkeypatch.setattr(ctl, "FLIP_STEADY_WAIT_S", 0.3)
    monkeypatch.setattr(ctl, "RESUME_POLL_S", 0.01)
    ctrl = ctl.Controller(cfg, ChannelMap(tmp_path / "ch.json"))
    ctrl.moves = []
    ctrl.on_behind = lambda target, new, old: ctrl.moves.append((target, new, old))
    return ctrl


def tuned(stick, report=pb("playing", 10_000, 1_000)):
    stick.remember(T, dict(report, pkg="yttv.pkg"))
    return stick


def run(coro):
    return asyncio.run(coro)


def test_steady_stream_is_ready_after_two_reads_with_no_state_change(c):
    s = tuned(Scripted([pb("playing", 10_000, 1_000)]))
    assert run(c._steady_playing(s, T)) is not None
    assert s.reads == 2 and c.moves == []          # nothing buffered, nothing booked


def test_buffering_standby_waits_then_books_the_stall(c):
    # Buffered 2 s while hidden: the clock moved 6 s, the position only 4 s.
    s = tuned(Scripted([pb("buffering", 10_000, 3_000), pb("playing", 14_000, 7_000)]))
    got = run(c._steady_playing(s, T))
    assert got is not None and got["state"] == "playing"
    assert c.moves == [(T, 2.0, 0.0)] and c.held[T] == 2.0
    # Re-anchored: the same stall is never booked twice.
    assert c._book_lag(s, T, dict(got)) == 0.0 and c.held[T] == 2.0


def test_paused_standby_is_refused_at_once(c):
    s = tuned(Scripted([pb("paused", 10_000, 2_000)]))
    assert run(c._steady_playing(s, T)) is None and s.reads == 1


def test_a_stream_that_keeps_changing_state_is_never_flipped_to(c):
    reports = [pb("playing", 10_000 + 10 * i, 1_000 + 10 * i) for i in range(200)]   # new stamp every read
    s = tuned(Scripted(reports))
    assert run(c._steady_playing(s, T)) is None


def test_lag_bounds(c):
    s = tuned(Scripted([]))
    assert c._book_lag(s, T, pb("playing", 20_000, 11_000)) == 0.0      # no lag
    assert c._book_lag(s, T, pb("playing", 20_400, 11_000)) == 0.0      # running fast (catch-up): never booked
    assert c._book_lag(s, T, pb("playing", 20_000, 11_300)) == 0.0      # 0.3 s: read noise
    assert c._book_lag(s, T, pb("playing", 20_000, 41_000)) == 0.0      # 30 s: not a stall
    assert c.moves == []
    assert c._book_lag(s, "youtubetv:espn", pb("playing", 20_000, 14_000)) == 0.0   # not what we tuned


def test_settle_book_counts_playing_within_3s(c):
    s = Scripted([])
    assert c._book_flip(s, T, 1.9, 0.8, "auto") is True
    assert c._book_flip(s, T, 1.9, 3.4, "media_play") is False
    assert c._book_flip(s, T, 1.9, 5.0, None) is False
    assert c._book_flip(s, T, 1.9, None, None) is None                  # nothing checked: not counted
    assert c.settle_book == {"n": 3, "ok": 1}
    assert c.last_flip["settled"] is None and c.last_flip["settle_rate"] == {"n": 3, "ok": 1}
