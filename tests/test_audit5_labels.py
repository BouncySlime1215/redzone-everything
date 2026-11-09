"""AUDIT-5: a no-bug stretch ends where the reads stopped.

Why: break_on_picture rows are training data. The stick moving away left the stretch open, and the next bug read hours
later closed it, booking a 20-second ad break as a 40-minute one.
"""
import json
from types import SimpleNamespace as NS

from redzone.cheap_labels import CheapLabels


class Cfg:
    def get(self, k, d=None):
        return {"labels.enabled": True}.get(k, d)


BUG = NS(clock="5:00", period="2")


def rows(tmp_path):
    p = tmp_path / "break_on_picture.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def obs(c, t, bug, live=True):
    c.observe_bug("prime:x", t, bug, game="g", league="nba", live=live, visible=True)


def test_a_stretch_ends_at_the_last_read_when_the_reads_stopped(tmp_path):
    """Repro: no bug at 0 and 25, the stick left, bug again at 2400: one row, dur 2400."""
    c = CheapLabels(Cfg(), tmp_path)
    obs(c, 0.0, None)
    obs(c, 25.0, None)
    obs(c, 2400.0, BUG)
    r = rows(tmp_path)
    assert len(r) == 1 and r[0]["dur_s"] == 25.0 and r[0]["end"] == 25.0


def test_a_short_stretch_before_the_reads_stopped_writes_nothing(tmp_path):
    c = CheapLabels(Cfg(), tmp_path)
    obs(c, 0.0, None)
    obs(c, 5.0, None)
    obs(c, 2400.0, BUG)
    assert rows(tmp_path) == []


def test_a_new_stretch_can_open_after_the_gap(tmp_path):
    c = CheapLabels(Cfg(), tmp_path)
    obs(c, 0.0, None)
    obs(c, 2400.0, None)
    obs(c, 2430.0, None)
    obs(c, 2435.0, BUG)
    r = rows(tmp_path)
    assert len(r) == 1 and r[0]["start"] == 2400.0 and r[0]["dur_s"] == 35.0


def test_a_steady_stretch_is_unchanged(tmp_path):
    c = CheapLabels(Cfg(), tmp_path)
    for t in range(0, 121, 5):
        obs(c, float(t), None)
    obs(c, 125.0, BUG)
    r = rows(tmp_path)
    assert len(r) == 1 and r[0]["dur_s"] == 125.0 and r[0]["end"] == 125.0


def test_the_game_ending_closes_the_stretch(tmp_path):
    """Repro: after the final there's no bug and the feed no longer says live; the stretch stayed open all night."""
    c = CheapLabels(Cfg(), tmp_path)
    obs(c, 0.0, None)
    obs(c, 30.0, None)
    obs(c, 35.0, None, live=False)
    r = rows(tmp_path)
    assert len(r) == 1 and r[0]["end"] == 30.0 and c._gap == {}
