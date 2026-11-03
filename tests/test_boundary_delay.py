"""Stream delay measured automatically from YouTube TV show changes on the quarter hour.

The two real cases were read off the sticks on Nov 2 2026 (ET, UTC-5)."""
from datetime import datetime, timedelta, timezone

import pytest

from redzone.decider.screen import BOUNDARY_SIGMA_S, MIN_TAP_SIGMA_S, SHARED_MARGIN_S, DelayBook
from redzone.screen.boundary import (BoundaryWatcher, channel_origin, delay_from_origin, flip_sample, in_window,
                                     is_show_change)
from redzone.store import Store

ET = timezone(timedelta(hours=-5))


def at(h, m, s=0.0):
    return datetime(2026, 11, 2, h, m, tzinfo=ET).timestamp() + s


# ------------------------------------------------------------------ the sample math, real cases

def test_trutv_live_flip_is_22s():
    """'Impractical Jokers' -> 'CBB Hall of Fame Series Pregame' read at 19:00:22.081 (reads ~0.2 s apart)."""
    sample, boundary = flip_sample(at(19, 0, 22.081 - 0.1), estimate_s=45.0)
    assert boundary == at(19, 0)
    assert sample == pytest.approx(22.0, abs=0.1)


def test_espn_paused_frame_is_26_4s_behind():
    """ESPN flipped to 'Chicago Bears at Seattle Seahawks' (the 20:15 show), read at 20:17:56.017 with
    position 47160235. Earlier, paused at 20:14:01.768 on position 47075571."""
    origin = channel_origin(at(20, 15), 47160.235)
    assert delay_from_origin(origin, at(20, 14, 1.768), 47075.571) == pytest.approx(26.4, abs=0.05)
    # after the 2.5 min pause the stream is that much further behind: no longer a usable boundary sample
    assert flip_sample(at(20, 17, 56.017), estimate_s=45.0) is None


def test_trutv_origin_reproduces_its_own_delay():
    origin = channel_origin(at(19, 0), 47089.971)
    assert delay_from_origin(origin, at(19, 0, 22.081), 47089.971) == pytest.approx(22.081)


@pytest.mark.parametrize("off_s", [3.0, 130.0, 160.0, 400.0])
def test_rejects_implausible_flips(off_s):
    """Outside 5..120 s, or not near any quarter hour once the current estimate is allowed for."""
    assert flip_sample(at(19, 0, off_s), estimate_s=45.0 if off_s != 3.0 else 0.0) is None


def test_show_change_needs_two_real_different_titles():
    assert is_show_change("Impractical Jokers", "CBB Hall of Fame Series Pregame")
    assert not is_show_change("SportsCenter", "sportscenter")
    assert not is_show_change("", "SportsCenter")
    assert not is_show_change("SportsCenter", "")


def test_window_is_around_the_quarter_hours():
    assert in_window(at(19, 0, -170)) and in_window(at(19, 2, 50)) and not in_window(at(19, 7, 0))


# ------------------------------------------------------------------ the watcher, fed playback reads

class Clocks:
    def __init__(self, wall):
        self.wall, self.mono0, self.stick0 = wall, 5000.0, 9_000_000   # stick: elapsedRealtime ms

    def wall_fn(self):
        return self.wall

    def mono_fn(self):
        return self.mono0 + (self.wall - self.start)


def report(clk, title, position_ms, updated_ms, state="playing", channel="truTV"):
    mono = clk.mono_fn()
    return {"state": state, "pkg": "com.amazon.firetv.youtube.tv", "position": position_ms, "updated": updated_ms,
            "speed": 1.0, "title": title, "subtitle": channel, "read_ok": True,
            "read_started": mono - 0.05, "read_finished": mono}


def feed_trutv(watcher, clk, tune, title_before="Impractical Jokers", title_after="CBB Hall of Fame Series Pregame",
               flip_at=None, delay=22.0, pos_at_boundary_ms=47089971 - 22081):
    """Play a channel through a boundary: the app re-posts its state every 2 s; the title flips on screen
    `delay` s after 19:00. Reads every 0.2 s from 18:59:50 to 19:00:40."""
    flip_at = at(19, 0, delay) if flip_at is None else flip_at
    out = []
    # stick clock: wall w <-> stick ms stick0 + (w - start)*1000; stream position at wall w (screen)
    pos = lambda w: pos_at_boundary_ms + (w - at(19, 0, delay)) * 1000.0     # noqa: E731
    stick = lambda w: clk.stick0 + (w - clk.start) * 1000.0                  # noqa: E731
    t = at(18, 59, 50)
    last_post = None
    while t <= at(19, 0, 40):
        clk.wall = t
        post = clk.start + 2.0 * int((t - clk.start) / 2.0)
        if post != last_post:
            last_post = post
        r = report(clk, title_after if t - 0.05 >= flip_at else title_before, int(pos(post)), int(stick(post)))
        got = watcher.observe("stick1", "youtubetv:trutv", tune, r)
        if got:
            out.append(got)
        t += 0.2
    return out


def make(mu=45.0, sigma=15.0):
    book = DelayBook({"youtubetv": mu}, sigma)
    live = {}
    w = BoundaryWatcher(estimate=lambda t: (book.get(t).mu, book.get(t).current_sigma(0)),
                        on_sample=lambda s: book.boundary(s.target, s.sample, s.flip_wall),
                        on_live=lambda t, d: live.__setitem__(t, d))
    return book, w, live


def test_watcher_times_the_trutv_flip_and_learns_the_origin():
    book, w, live = make()
    clk = Clocks(at(18, 59, 50))
    clk.start = clk.wall
    w.clock, w.mono = clk.wall_fn, clk.mono_fn
    got = feed_trutv(w, clk, tune=object())
    assert len(got) == 1
    s = got[0]
    assert s.sample == pytest.approx(22.0, abs=0.3) and s.error_s <= 0.2
    assert s.origin == pytest.approx(channel_origin(at(19, 0), 47089.971 - 22.081), abs=0.6)
    # one sample alone doesn't move the book; the per-tune live delay is reported (about 22 s)
    assert book.get("youtubetv:trutv").mu == 45.0
    assert live["youtubetv:trutv"] == pytest.approx(22.0, abs=0.6)


def test_watcher_ignores_a_new_tune_or_a_pause_between_the_reads():
    book, w, live = make()
    clk = Clocks(at(18, 59, 59))
    clk.start = clk.wall
    w.clock, w.mono = clk.wall_fn, clk.mono_fn
    w.observe("s", "youtubetv:trutv", "tuneA", report(clk, "Impractical Jokers", 1000, 9_000_000))
    clk.wall += 0.5
    assert w.observe("s", "youtubetv:trutv", "tuneB", report(clk, "Pregame", 5000, 9_000_400)) is None
    clk.wall = at(19, 0, 20)
    w.observe("s", "youtubetv:espn", "t", report(clk, "SportsCenter", 1000, 9_000_000, state="paused"))
    clk.wall += 0.5
    assert w.observe("s", "youtubetv:espn", "t", report(clk, "NFL Live", 1000, 9_000_500)) is None
    # a seek: position jumped
    clk.wall += 0.5
    w.observe("s", "youtubetv:espn", "t", report(clk, "NFL Live", 1000, 9_001_000))
    clk.wall += 0.5
    assert w.observe("s", "youtubetv:espn", "t", report(clk, "Monday Night Kickoff", 60_000, 9_001_500)) is None
    assert not w.samples and w.rejected == 4    # incl. the channel change itself


def test_watcher_ignores_slow_reads_and_non_youtube_targets():
    book, w, live = make()
    clk = Clocks(at(19, 0, 15))
    clk.start = clk.wall
    w.clock, w.mono = clk.wall_fn, clk.mono_fn
    w.observe("s", "youtubetv:trutv", "t", report(clk, "A", 1000, 9_000_000))
    clk.wall += 10.0
    assert w.observe("s", "youtubetv:trutv", "t", report(clk, "B", 1000, 9_000_000)) is None   # 10 s gap
    assert w.observe("s", "prime:tnf", "t", report(clk, "A", 1000, 9_000_000)) is None
    clk.wall += 0.3
    assert w.observe("s", "prime:tnf", "t", report(clk, "B", 1300, 9_000_300)) is None


def test_origin_dropped_when_inconsistent():
    book, w, live = make()
    clk = Clocks(at(18, 59, 50))
    clk.start = clk.wall
    w.clock, w.mono = clk.wall_fn, clk.mono_fn
    tune = object()
    feed_trutv(w, clk, tune)
    assert "youtubetv:trutv" in w.origins
    # the stream restarted: a new timeline 300 s further on at the same wall time
    clk.wall += 0.2
    w.observe("stick1", "youtubetv:trutv", object(),
              report(clk, "CBB Hall of Fame Series Pregame", 47_500_000, int(clk.stick0 + (clk.wall - clk.start) * 1000)))
    clk.wall += 0.2
    w.observe("stick1", "youtubetv:trutv", object(),
              report(clk, "CBB Hall of Fame Series Pregame", 47_500_000, int(clk.stick0 + (clk.wall - clk.start) * 1000)))
    assert "youtubetv:trutv" not in w.origins and live["youtubetv:trutv"] is None


# ------------------------------------------------------------------ the delay book

class Clock:
    def __init__(self, t=1_000_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def book(c):
    return DelayBook({"youtubetv": 45.0}, 15.0, clock=c)


def test_one_boundary_sample_never_narrows_the_default():
    b = book(Clock())
    d = b.boundary("youtubetv:trutv", 22.0)
    assert (d.mu, d.sigma, d.source) == (45.0, 15.0, "default")


def test_two_agreeing_samples_measure_the_app_with_the_boundary_sigma():
    b = book(Clock())
    b.boundary("youtubetv:trutv", 22.0)
    b.boundary("youtubetv:espn", 24.0)
    d = b.get("youtubetv:trutv")
    assert d.source == "boundary" and d.calibrated_at is not None
    assert d.sigma == BOUNDARY_SIGMA_S + SHARED_MARGIN_S > MIN_TAP_SIGMA_S     # pooled across channels
    assert 22.0 <= d.mu <= 24.0          # its own 22, pulled a little toward ESPN's 24
    b.boundary("youtubetv:trutv", 23.0)
    assert b.get("youtubetv:trutv").sigma == BOUNDARY_SIGMA_S                  # own two: no shared margin
    assert b.boundary_count("youtubetv") == 3


def test_contradicting_boundary_samples_widen():
    b = book(Clock())
    for s in (22.0, 23.0, 60.0):
        b.boundary("youtubetv:trutv", s)
    d = b.get("youtubetv:trutv")
    assert d.sigma >= 19.0 and d.mu + 2 * d.sigma >= 60.0 and d.mu - 2 * d.sigma <= 22.0


def test_taps_still_win():
    b = book(Clock())
    for s in (22.0, 23.0):
        b.boundary("youtubetv:trutv", s)
    b.calibrate("youtubetv:espn", 30.0)
    assert b.get("youtubetv:espn").source == "tap"
    assert b.get("youtubetv:fox").source == "app" and b.get("youtubetv:fox").mu == 30.0   # a tap beats show changes
    b.calibrate("youtubetv:trutv", 25.0)
    assert b.get("youtubetv:trutv").source == "tap" and b.get("youtubetv:trutv").mu == 25.0


def test_own_show_changes_beat_another_channels_tap():
    """[per-channel delay] The real case: truTV measured at 22 s by its own show change, FS1 tapped
    at 42 s. The old book told truTV 42 +-16 (26..58): its real 22 fell outside."""
    b = book(Clock())
    for s in (22.0, 23.0):
        b.boundary("youtubetv:trutv", s)
    b.calibrate("youtubetv:fs1", 42.0)
    d = b.get("youtubetv:trutv")
    assert d.source == "boundary" and 22.0 <= d.mu <= 27.0
    assert d.mu - 2 * d.sigma <= 22.0 and d.sigma >= BOUNDARY_SIGMA_S
    legacy = DelayBook({"youtubetv": 45.0}, 15.0, clock=Clock(), model="legacy")
    for s in (22.0, 23.0):
        legacy.boundary("youtubetv:trutv", s)
    legacy.calibrate("youtubetv:fs1", 42.0)
    old = legacy.get("youtubetv:trutv")
    assert old.mu == 42.0 and old.mu - 2 * old.sigma > 22.0                 # the bug this fixes


def test_live_origin_delay_recentres_but_never_narrows():
    b = book(Clock())
    for s in (22.0, 23.0):
        b.boundary("youtubetv:trutv", s)
    sigma = b.get("youtubetv:trutv").sigma
    b.set_live("youtubetv:trutv", 25.0)
    d = b.get("youtubetv:trutv")
    assert d.mu == 25.0 and d.sigma == sigma and d.live == 25.0
    b.set_live("youtubetv:trutv", 80.0)                                   # inconsistent: ignored
    assert b.get("youtubetv:trutv").mu in (22.0, 23.0)
    b.set_live("youtubetv:trutv", None)
    assert b.get("youtubetv:trutv").live is None


def test_boundary_samples_persist_and_reload(tmp_path):
    c = Clock()
    store = Store(tmp_path / "rz.db")
    b = book(c)
    for s in (22.0, 24.0):
        d = b.boundary("youtubetv:trutv", s)
        store.calibration("youtubetv:trutv", s, d.mu, d.sigma, kind="boundary")
    store.calibration("youtubetv:espn", 30.0, 30.0, 4.0)                  # an old-style tap row
    rows = store.calibrations(since=0)
    assert [r[3] for r in rows] == ["boundary", "boundary", "tap"]
    b2 = book(c)
    assert b2.load([r for r in rows if r[3] == "boundary"]) == 2
    assert b2.get("youtubetv:trutv").source == "boundary"
    assert b2.load(rows[2:]) == 1 and b2.get("youtubetv:espn").source == "tap"
    assert b2.get("youtubetv:trutv").source == "boundary"     # its own show changes, with ESPN's tap as prior


def test_store_migrates_an_old_calibrations_table(tmp_path):
    import sqlite3
    db = tmp_path / "old.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE calibrations (ts REAL, target TEXT, sample REAL, mu REAL, sigma REAL)")
    con.execute("INSERT INTO calibrations VALUES (1.0, 'youtubetv:espn', 30.0, 30.0, 4.0)")
    con.commit()
    con.close()
    assert Store(db).calibrations() == [(1.0, "youtubetv:espn", 30.0, "tap")]


# ------------------------------------------------------------------ wiring

def test_engine_saves_boundary_samples_and_reloads_them(tmp_path):
    from redzone.config import Config
    from redzone.engine import Engine
    from redzone.screen.boundary import Sample
    store = Store(tmp_path / "rz.db")
    eng = Engine(Config(user_path=tmp_path / "config.yaml", history_dir=tmp_path / "hist"), store)
    assert eng.control.on_playback == eng.boundary.observe_stick
    for x in (22.0, 23.0):
        eng.boundary.on_sample(Sample("youtubetv:trutv", x, at(19, 0), at(19, 0, x), 0.1, "A", "B"))
    assert [r[3] for r in store.calibrations()] == ["boundary", "boundary"]
    again = Engine(Config(user_path=tmp_path / "config.yaml", history_dir=tmp_path / "hist"), store)
    d = again.delays.get("youtubetv:espn")
    assert d.source == "boundary" and d.mu in (22.0, 23.0)
    row = next(x for x in again.snapshot()["delays"] if x["target"] == "youtubetv:espn")
    assert row["boundary_samples"] == 2 and row["source"] == "boundary"


def test_stick_playback_reports_reach_the_observer():
    import asyncio

    from redzone.tuners.adb import RunResult, Stick
    seen = []
    st = Stick("1.2.3.4:5555", observer=lambda s, r: seen.append((s, r)))

    async def shell(cmd, timeout=8.0):
        return RunResult(True, "package=com.amazon.firetv.youtube.tv\n"
                         "state=PlaybackState {state=3, position=47089971, buffered position=0, speed=1.0, "
                         "updated=9000000, actions=0}\n"
                         "metadata: size=3, description=CBB Hall of Fame Series Pregame, truTV, null\n", 0.01)
    st.shell = shell
    r = asyncio.run(st.playback("com.amazon.firetv.youtube.tv"))
    assert seen and seen[0][1] is r and r["title"] == "CBB Hall of Fame Series Pregame" and r["subtitle"] == "truTV"
