"""[POSITION-FIX items 4 and 7] The delay model keeps a tune's own lag apart from the channel, and a game's K apart from
both. Measured cases are fixtures as numbers only (FINDINGS 5b; ledger L29, L30). Plus: flag off, a night decides as before.
"""
import json
import math
import statistics
from types import SimpleNamespace

import pytest
import yaml

from redzone.decider import tune_offset as TO
from redzone.decider.sync import SIGMA, SyncBook

# FINDINGS 5b: U of each fresh tune (s), one night, hidden 2023 stick
ESPN_U = [25.97, 24.75, 25.42, 24.43, 23.77, 27.40, 24.14, 23.54, 26.59, 23.80, 22.31, 25.16, 25.21, 23.89, 23.14, 25.01,
          23.77, 23.99, 26.76, 25.04, 25.68]
FS1_U = [19.29, 21.75, 16.74, 16.63, 16.94, 17.16, 20.68, 21.06, 21.29, 20.15]
NIGHT = "2026-11-10"


class Cfg(dict):
    def get(self, k, d=None):
        return super().get(k, d)


def test_the_measured_tune_spread_is_the_shipped_one():
    assert statistics.pstdev(ESPN_U) == pytest.approx(1.24, abs=0.01)
    assert statistics.pstdev(FS1_U) == pytest.approx(1.98, abs=0.01)
    assert max(ESPN_U) - min(ESPN_U) == pytest.approx(5.09, abs=0.01)
    from redzone.config import DEFAULTS
    from redzone.tuners.yttv_timing import FLAGS
    cfg = yaml.safe_load(DEFAULTS.read_text())["timing"]
    assert cfg["per_tune_offset_sd_s"] == {"default": 2.0, "espn": 1.24, "fs1": 1.98}
    for flag in FLAGS:                                                     # every POSITION-FIX flag ships off
        assert cfg[flag] is False, flag
    assert cfg["game_offset_file"] == ""


def test_a_fresh_tune_is_unknown_until_one_read():
    to = TO.TuneOffsets({"default": 2.0, "espn": 1.24, "fs1": 1.98})
    assert to.new_tune("youtubetv:espn", 100.0) == 1.24 and to.doubt("youtubetv:espn") == 1.24
    assert to.new_tune("youtubetv:fs1", 100.0) == 1.98 and to.new_tune("youtubetv:tnt", 100.0) == 2.0
    assert to.known("youtubetv:espn") is None
    assert to.read("youtubetv:espn", 24.75, "label", 99.0) is None          # from before this tune: refused
    r = to.read("youtubetv:espn", 24.75, "label", 120.0)
    assert r.sd_s == 0.29 and to.doubt("youtubetv:espn") == 0.29
    assert to.read("youtubetv:fs1", 21.17, "live_latency", 120.0).sd_s == 0.19
    to.new_tune("youtubetv:espn", 200.0)                                   # the next tune forgets it
    assert to.known("youtubetv:espn") is None and to.doubt("youtubetv:espn") == 1.24


def test_the_filter_carries_the_tune_offset_apart_and_one_read_removes_it():
    """FINDINGS 1: channel doubt 4.0 s and per-tune 1.2-2.0 s give 4.2-4.5 s after a fresh tune."""
    book = SyncBook(lambda target: (34.9, 4.0), clock=lambda: 1000.0)
    book.tuned("youtubetv:espn", 1000.0)
    assert book.get("youtubetv:espn").sd(1000.0) == pytest.approx(4.0, abs=0.01)
    book.tune_offset("youtubetv:espn", 1.24, 1000.0)
    assert book.get("youtubetv:espn").sd(1000.0) == pytest.approx(math.hypot(4.0, 1.24), abs=0.01)   # 4.19
    book.tuned("youtubetv:fs1", 1000.0)
    book.tune_offset("youtubetv:fs1", 1.98, 1000.0)
    assert book.get("youtubetv:fs1").sd(1000.0) == pytest.approx(4.46, abs=0.01)
    # one label read (U + K, sd root-sum of 0.29 and the K's doubt) takes the doubt down to about that
    assert book.measure("youtubetv:espn", 24.75 + 2.85, "label", t=1010.0, sd=math.hypot(0.29, 0.37))
    assert book.get("youtubetv:espn").sd(1010.0) < 0.5
    assert SIGMA["label"] == 0.29 and SIGMA["live_latency"] == 0.19


def write_k(tmp_path, games):
    p = tmp_path / "k.json"
    p.write_text(json.dumps({"games": games}))
    return p


def test_game_k_is_per_game_per_night_and_never_zero(tmp_path):
    """L29 espn2 -2.78 s (5 reads, spread 0.50, rated 0.28 s); L30 espn +2.85 s (spread 0.49) two hours later."""
    p = write_k(tmp_path, {"401": {"night": NIGHT, "k_s": -2.78, "sd_s": 0.28, "spread_s": 0.50, "n": 5},
                           "402": {"night": NIGHT, "k_s": 2.85, "sd_s": 0.28, "spread_s": 0.49, "n": 5},
                           "403": {"night": "2026-11-09", "k_s": 1.0, "sd_s": 0.3},
                           "404": {"night": NIGHT, "k_s": "x"}})
    go = TO.GameOffsets.load(p)
    a, b = go.k("401", NIGHT), go.k("402", NIGHT)
    assert a.k_s == -2.78 and b.k_s == 2.85 and b.k_s - a.k_s == pytest.approx(5.63)
    assert a.sd_s == pytest.approx(math.hypot(0.28, 0.25), abs=0.001)     # the spread is carried into the doubt
    assert go.k("403", NIGHT) is None                                       # another night's K: unknown
    assert go.k("404", NIGHT) is None and go.k("999", NIGHT) is None and go.k(None, NIGHT) is None
    assert TO.GameOffsets.load("").k("401", NIGHT) is None                  # empty path = off
    assert TO.GameOffsets.load(tmp_path / "missing.json").k("401", NIGHT) is None
    read = TO.TuneRead(24.75, 0.29, "label", 1.0)
    assert TO.delay_sample(read, None) is None                              # no K: unknown, not U + 0
    z, sd = TO.delay_sample(read, a, behind_s=60.0)
    assert z == pytest.approx(24.75 + 60.0 - 2.78) and sd == pytest.approx(math.hypot(0.29, a.sd_s), abs=0.001)


class FakeEngine:
    """Engine's tune-offset methods on a stub: the sync filter and the game lookup recorded."""
    from redzone.engine import Engine as _E
    tune_offset_read = _E.tune_offset_read
    _probe_tune_offset = _E._probe_tune_offset
    _sync_tune = _E._sync_tune

    def __init__(self, tune_offsets, game_offsets=None, game="401"):
        self.tune_offsets, self.game_offsets, self.game = tune_offsets, game_offsets, game
        self.fed, self.sync_calls = [], []
        self.delays = SimpleNamespace(clear_label=lambda t: None)
        self.sync = SimpleNamespace(tuned=lambda *a, **k: self.sync_calls.append(("tuned", a)),
                                    tune_offset=lambda *a: self.sync_calls.append(("tune_offset", a)))
        hid = SimpleNamespace(host="hidden")
        self.control = SimpleNamespace(hidden=hid, hidden_target="youtubetv:espn2", main=None, on_target=None)

    def _game_on(self, target, visible):
        return self.game

    def _sync_measure(self, target, z, kind, t, sd=None):
        self.fed.append((target, z, kind, t, sd))


def test_engine_feeds_a_delay_only_when_the_games_k_is_known(tmp_path):
    from redzone.sync_edge import night_of
    import datetime as dt
    wall = dt.datetime(2026, 11, 10, 20, 0).timestamp()
    night = night_of(wall)
    go = TO.GameOffsets.load(write_k(tmp_path, {"401": {"night": night, "k_s": -2.78, "sd_s": 0.28, "spread_s": 0.5}}))
    e = FakeEngine(TO.TuneOffsets({"default": 2.0}), go)
    e._sync_tune("youtubetv:espn2", wall - 30, True)
    assert ("tune_offset", ("youtubetv:espn2", 2.0, wall - 30)) in e.sync_calls
    z = e.tune_offset_read("youtubetv:espn2", 24.0, "label", wall, 0.29, behind_s=60.0)
    assert z[0] == pytest.approx(24.0 + 60.0 - 2.78) and e.fed[-1][2] == "label"
    e2 = FakeEngine(TO.TuneOffsets({"default": 2.0}), go, game="999")
    e2._sync_tune("youtubetv:espn2", wall - 30, True)
    assert e2.tune_offset_read("youtubetv:espn2", 24.0, "label", wall) is None and e2.fed == []
    assert e2.tune_offsets.known("youtubetv:espn2").u_s == 24.0           # the tune's lag is still kept
    # the label probe's hand-off finds the target the stick plays
    e._probe_tune_offset("hidden", 23.5, 0.0, 0.29, wall + 1)
    assert e.tune_offsets.known("youtubetv:espn2").u_s == 23.5
    # a seek or pause (same stream) keeps the tune's read; prime targets are untouched
    e._sync_tune("youtubetv:espn2", wall + 2, False)
    assert e.tune_offsets.known("youtubetv:espn2").u_s == 23.5
    n = len(e.sync_calls)
    e._sync_tune("prime:tnf", wall + 3, True)
    assert [c[0] for c in e.sync_calls[n:]] == ["tuned"]


def test_engine_flag_off_has_no_tune_model():
    e = FakeEngine(None)
    e._sync_tune("youtubetv:espn2", 1.0, True)
    assert [c[0] for c in e.sync_calls] == ["tuned"]
    assert e.tune_offset_read("youtubetv:espn2", 24.0, "label", 2.0) is None and e.fed == []


# ---------------------------------------------------------------------------------- flag off: a night as before
FLAGS_OFF = {f"timing.{k}": False for k in __import__("redzone.tuners.yttv_timing", fromlist=["FLAGS"]).FLAGS}


def test_flag_off_a_night_decides_as_before(monkeypatch):
    """The recorded NBA night (tests/test_timing_truth.py): every new flag at its shipped default decides exactly as with
    the keys absent, and none of the new entry points is reached on the way."""
    from tests.test_timing_truth import _night_decisions
    from redzone.tuners import yttv_nerds, yttv_timing
    before = _night_decisions()
    assert len(before[0]) > 500 and before[1]

    def boom(*a, **k):
        raise AssertionError("a POSITION-FIX path ran with its flag off")
    monkeypatch.setattr(TO.TuneOffsets, "new_tune", boom)
    monkeypatch.setattr(TO.GameOffsets, "load", boom)
    monkeypatch.setattr(SyncBook, "tune_offset", boom)
    monkeypatch.setattr(yttv_nerds, "read_live_latency", boom)
    monkeypatch.setattr(yttv_timing, "read_session", boom)
    monkeypatch.setattr(yttv_timing, "paused_span", boom)
    assert _night_decisions(**FLAGS_OFF) == before
