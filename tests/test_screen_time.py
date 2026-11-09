"""Screen-time clock (flag views.screen_clock, code default OFF). Why: the TV runs behind the feed, so the
feed's clock is a spoiler; with the flag on the phone shows the clock as of now - delay, or none."""
from types import SimpleNamespace

from redzone import views
from redzone.models import GameState, League, Phase

T0 = 10_000.0


def _g(period, clock_s, phase=Phase.LIVE):
    g = GameState.__new__(GameState)
    g.period, g.clock_s, g.phase, g.league = period, clock_s, phase, League.NBA
    return g


def _item(clock="2:00", period=4, phase="live"):
    return {"id": "g1", "phase": phase, "clock": clock, "period": period, "score": 5}


def _tape():
    t = views.ClockTape()
    for i, c in enumerate(range(600, 480, -10)):      # feed clock falls 10 s every 10 s of wall time
        t.record("g1", _g(2, c), T0 + i * 10)
    return t


def test_flag_off_views_output_unchanged():
    item = _item()
    before = dict(item)
    assert views.screen_clock_on(SimpleNamespace(get=lambda k, d=None: d)) is False
    out = views.screen_item(item, "g1", enabled=False, tape=_tape(), delay_s=75, now=T0 + 110)
    assert out is item and item == before
    assert views.clock_text(_g(4, 120)) == "2:00"


def test_flag_reads_views_screen_clock_default_off():
    cfg = SimpleNamespace(get=lambda k, d=None: {"views.screen_clock": True}.get(k, d))
    assert views.screen_clock_on(cfg) is True
    assert views.SCREEN_CLOCK_FLAG == "views.screen_clock"


def test_flag_on_clock_lags_feed_by_delay():
    tape = _tape()
    now = T0 + 110                                    # feed shows 490 (8:10)
    out = views.screen_item(_item(), "g1", enabled=True, tape=tape, delay_s=0, now=now)
    assert out["clock"] == "8:10"
    out = views.screen_item(_item(), "g1", enabled=True, tape=tape, delay_s=60, now=now)
    assert out["clock"] == "9:10" and out["screen_clock"] == "ok" and out["screen_delay_s"] == 60.0
    assert out["period"] == 2


def test_unknown_delay_shows_no_clock_not_live():
    out = views.screen_item(_item(), "g1", enabled=True, tape=_tape(), delay_s=None, now=T0 + 110)
    assert out["clock"] == "" and out["screen_clock"] == "unknown"


def test_tape_too_young_shows_no_clock():
    out = views.screen_item(_item(), "g1", enabled=True, tape=_tape(), delay_s=500, now=T0 + 110)
    assert out["clock"] == "" and out["screen_clock"] == "unknown"


def test_non_live_untouched_when_on():
    item = _item(clock="", phase="post")
    assert views.screen_item(item, "g1", enabled=True, tape=_tape(), delay_s=60, now=T0) is item


def test_known_delay_default_source_is_unknown():
    d = lambda src: SimpleNamespace(book={"a:x": SimpleNamespace(mu=70.0, source=src)})  # noqa: E731
    assert views.known_delay_s(d("default"), "a:x") is None
    assert views.known_delay_s(d("tap"), "a:x") == 70.0
    assert views.known_delay_s(d("tap"), None) is None
    assert views.known_delay_s(d("tap"), "b:y") is None


def test_tape_trims_old_rows():
    t = views.ClockTape(keep_s=50)
    for i in range(30):
        t.record("g1", _g(1, 900 - i), T0 + i * 10)
    assert len(t.rows["g1"]) < 12
    assert t.at("g1", T0 + 290 - 40) is not None


def test_engine_card_goes_through_the_tape_only_with_the_flag_on():
    """Why: the views side did nothing until the Engine called it (WP-21 review)."""
    from types import SimpleNamespace as NS

    from redzone.engine import Engine

    class Tape:
        calls = 0

        def record(self, gid, g):
            Tape.calls += 1

    item = {"clock": "8:10", "period": 1}
    off = NS(cfg=NS(get=lambda k, d=None: d), clock_tape=Tape(), delays=None)
    assert Engine._screen_item(off, item, "g", object(), None) is item and Tape.calls == 0

