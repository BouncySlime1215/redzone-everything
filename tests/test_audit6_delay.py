"""AUDIT-6: the delay book keeps what it measures relative to the live edge.

A sample read off a picture (a tap, the score bug, a show change, the stream origin) is the whole delay of that
picture, the rewind or hold included. The book adds `behind` back when it builds mu, so a sample stored whole counts
the rewind twice: the app believed the screen ran that much further behind than it did."""
import pytest

from redzone.decider.screen import DelayBook

T = "prime:wiz"
NOW = 1_800_000_000.0


def _book():
    return DelayBook({"prime": 40.0, "youtubetv": 40.0}, 8.0, clock=lambda: NOW)


@pytest.mark.parametrize("add", ["calibrate", "screen"])
def test_a_sample_from_a_held_picture_does_not_count_the_hold_twice(add):
    b = _book()
    b.get(T)
    b.set_behind(T, 60.0)
    # the edge runs 40 s behind real life and this picture 60 s behind that
    getattr(b, add)(T, 100.0)
    assert b.get(T).mu == pytest.approx(100.0)


def test_a_show_change_on_a_held_picture_does_not_count_the_hold_twice():
    t = "youtubetv:espn"
    b = _book()
    b.get(t)
    b.set_behind(t, 60.0)
    b.boundary(t, 100.0, NOW - 900)
    b.boundary(t, 100.0, NOW)
    assert b.get(t).mu == pytest.approx(100.0)


def test_the_next_tune_at_the_live_edge_gets_the_edge_delay():
    b = _book()
    b.get(T)
    b.set_behind(T, 60.0)
    b.screen(T, 100.0)
    b.set_behind(T, 0.0)
    assert b.get(T).mu == pytest.approx(40.0)


def test_a_sample_at_the_live_edge_is_unchanged():
    b = _book()
    b.get(T)
    b.screen(T, 42.0)
    assert b.get(T).mu == pytest.approx(42.0)


def test_this_tunes_stream_origin_does_not_count_the_hold_twice():
    t = "youtubetv:espn"
    b = _book()
    b.boundary("youtubetv:fs1", 40.0, NOW - 900)
    b.boundary("youtubetv:fs1", 40.0, NOW)
    b.get(t)
    b.set_behind(t, 10.0)
    b.set_live(t, 52.0)              # the origin reads the whole delay of this picture: 42 at the edge + 10 held
    assert b.get(t).mu == pytest.approx(52.0)
