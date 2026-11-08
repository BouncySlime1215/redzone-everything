"""[Nick, Nov 8 live, UGA@MISS on 2df593ea] With replay ON a catch-up failed ('replay seek failed; kept current game',
path tuned, 0.0-0.1 s) about every 0.5 s: the park's own 120 s backoff stopped the KEYS but the decider picked the
same replay again every tick, and the queued replay also held jump mode's standby. Every failed replay now backs off
(replay.fail_retry_s, then it leaves the queue after replay.max_fails), and a YouTube TV replay that can't be parked
is refused before the hidden stick is tuned."""
import asyncio
import time

from redzone.decider.decider import Missed
from redzone import standby
from tests.test_control import FakeStick, two  # noqa: F401
from tests.test_decider import cands, dec, start_on  # noqa: F401
from tests.test_mine_catchup_alone import _setup


def test_a_failed_replay_is_not_decided_again_until_it_backs_off(dec, clock, monkeypatch):
    _setup(dec, clock, True)
    monkeypatch.setattr(type(dec), "_best", lambda self, w, exclude=None: None)
    d = dec.decide(cands(a=40, w=10), commit=False)
    assert d.action == "replay"
    dec.reject(d)
    dec.replay_failed(d.replay)
    assert dec.decide(cands(a=40, w=10), commit=False).action != "replay"          # the next tick: no 2 Hz loop
    clock.t += 121
    d2 = dec.decide(cands(a=40, w=10), commit=False)
    assert d2.action == "replay" and d2.replay is d.replay           # tried once more after the back-off
    dec.reject(d2)
    dec.replay_failed(d2.replay)                                     # second failure: it leaves the queue
    clock.t += 500
    assert dec.decide(cands(a=40, w=10), commit=False).action != "replay" and d.replay not in dec.queue


def test_the_standby_planner_skips_a_backing_off_replay():
    m = Missed("w", "g1", "WSH goal", "score", 1000.0, 90.0)
    assert standby.likely_replay([m], 1010.0, 1800) is m
    m.retry_at = 1130.0
    assert standby.likely_replay([m], 1010.0, 1800) is None           # jump mode's rotation isn't held by it


def test_a_youtubetv_replay_too_deep_to_park_never_tunes_the_hidden_stick(two):   # noqa: F811
    c, tuner = two
    out = asyncio.run(c._execute_two("youtubetv:fox", 400.0, lambda: True, time.time(), replay_id="p1"))
    assert not out.ok and out.screen_unchanged and out.path == "refused" and "LEFTs" in out.detail
    assert c.hidden.log == [] and c.main.log == []


def test_a_backing_off_replay_scrub_never_tunes_the_hidden_stick(two):   # noqa: F811
    c, tuner = two
    c._replay_scrub_backoff = {"youtubetv:fox": time.time() + 100}
    out = asyncio.run(c._execute_two("youtubetv:fox", 60.0, lambda: True, time.time(), replay_id="p1"))
    assert not out.ok and out.screen_unchanged and out.path == "refused" and "backing off" in out.detail
    assert c.hidden.log == [] and c.main.log == []


def test_a_catchup_too_old_to_park_is_dropped_not_refused(dec, clock, monkeypatch):
    """[Nick, Nov 8 879d99ed] every catch-up was refused at the stick ('197s back needs 14 LEFTs (> 8)', up to 694 s):
    a YouTube TV play older than replay.scrub_max_play_age_s is never queued or picked."""
    _setup(dec, clock, True)
    dec.cfg.update({"replay": {"scrub_max_play_age_s": 95}}, record=False)   # the shipped default
    monkeypatch.setattr(type(dec), "_best", lambda self, w, exclude=None: None)
    m = dec.queue[0]
    m.t = clock.t - 90                                        # still parkable
    assert dec.decide(cands(a=40, w=10), commit=False).action == "replay"
    m.t = clock.t - 200                                       # past the window: never tried
    assert dec.decide(cands(a=40, w=10), commit=False).action != "replay"
    assert m not in dec.queue


def test_the_age_cap_only_binds_youtubetv_scrub_replays(dec, clock, monkeypatch):
    _setup(dec, clock, True)
    monkeypatch.setattr(type(dec), "_best", lambda self, w, exclude=None: None)
    dec.cfg.update({"replay": {"yttv_seek": "keys", "scrub_max_play_age_s": 95}}, record=False)
    dec.queue[0].t = clock.t - 200
    assert dec.decide(cands(a=40, w=10), commit=False).action == "replay"
