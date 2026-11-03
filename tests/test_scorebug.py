"""Score-bug delay: real Prime screenshots from the stick (Nov 2 2026, Wizards at Magic), the parser,
the matching math, and the 'screen' evidence in the delay book."""
from __future__ import annotations

import platform
from pathlib import Path

import pytest

from redzone.decider.screen import SCREEN_SIGMA_S, DelayBook, Event, Timeline
from redzone.models import GameState, League, Phase, Play, Team
from redzone.screen import scorebug
from redzone.screen.scorebug import Bug, ScoreBugWatcher, match_change, parse_bug, read_png
from redzone.screen_ocr import TextBox, recognize

FIX = Path(__file__).parent / "fixtures" / "scorebug"
HOME, AWAY = Team("ORL", "Magic"), Team("WSH", "Wizards")
VISION = platform.system() == "Darwin" and (Path(__file__).parents[1] / "scripts" / "vision_ocr.swift").exists()

# What Vision read on clean.png (1000x562 downscale of the stick's 1920x1080), for machines without it.
CLEAN_OCR = [
    TextBox("tic", 1.0, .824, .338, .046, .075), TextBox("mastel", 1.0, .894, .356, .104, .075),
    TextBox("22", 1.0, .306, .530, .068, .114), TextBox("HADTEN", .3, .905, .535, .093, .052),
    TextBox("MI MONUMENTAL SPORTS", 1.0, .380, .762, .238, .039), TextBox("60 59", 1.0, .442, .814, .120, .072),
    TextBox("ORLANDO", 1.0, .682, .828, .062, .030), TextBox("WASHINGTON", 1.0, .254, .828, .082, .034),
    TextBox("MAGICY", 1.0, .638, .856, .136, .063), TextBox("WIZARDS", 1.0, .256, .858, .152, .043),
    TextBox("HALFTIME", 1.0, .464, .897, .074, .021),
]


class _OCR:
    def __init__(self, rows):
        self.rows = rows

    def __call__(self, path, timeout=8.0):
        class R:
            read_ok = bool(self.rows)
            observations = tuple(self.rows)
        return R()


def _png(name: str) -> bytes:
    return (FIX / f"{name}.png").read_bytes()


def test_clean_frame_reads_the_score_bug_with_recorded_ocr():
    bug = read_png(_png("clean"), HOME, AWAY, _OCR(CLEAN_OCR))
    assert bug == Bug(home=59, away=60, period="HALFTIME", clock="")


@pytest.mark.skipif(not VISION, reason="needs macOS Vision")
@pytest.mark.parametrize("name,want", [("clean", Bug(59, 60, "HALFTIME", "")), ("ov", None),
                                       ("p150b", None), ("espn150", None)])
def test_real_screenshots_with_vision(name, want):
    assert read_png(_png(name), HOME, AWAY, recognize, timeout=20) == want


def test_black_youtube_tv_frame_is_skipped_without_ocr():
    def boom(*a, **k):
        raise AssertionError("OCR must not run on a blanked frame")
    assert scorebug.is_uniform(_png("espn150"))
    assert not scorebug.is_uniform(_png("clean"))
    assert read_png(_png("espn150"), HOME, AWAY, boom) is None


def test_detail_page_and_player_overlay_give_no_bug():
    detail = [TextBox("Wizards vs. Magic", 1, .075, .187, .263, .058),
              TextBox("Watch the Washington Wizards vs. the Orlando Magic live", 1, .075, .342, .405, .031),
              TextBox("Included with your Monumental+ subscription", 1, .087, .604, .235, .022),
              TextBox("Need help?", 1, .150, .813, .077, .027), TextBox("Related", 1, .078, .813, .055, .022)]
    assert parse_bug(detail, HOME, AWAY) is None
    overlay = [TextBox("Wizards vs. Magic", 1, .030, .057, .264, .060), TextBox("1:20:16", 1, .050, .790, .040, .018),
               TextBox("17 POINTS", 1, .390, .811, .090, .025), TextBox("11 POINTS", 1, .570, .811, .088, .028)]
    assert parse_bug(overlay, HOME, AWAY) is None


def test_period_and_clock_and_abbreviations():
    rows = [TextBox("WSH", 1, .30, .85, .05, .04), TextBox("72", 1, .37, .84, .04, .06),
            TextBox("ORL", 1, .60, .85, .05, .04), TextBox("70", 1, .55, .84, .04, .06),
            TextBox("3RD 4:04", 1, .45, .92, .08, .03), TextBox(":14", 1, .54, .92, .03, .03)]
    assert parse_bug(rows, HOME, AWAY) == Bug(home=70, away=72, period="3RD", clock="4:04")
    # A third number as big as the scores: can't tell which two are scores.
    assert parse_bug(rows + [TextBox("99", 1, .45, .80, .04, .06)], HOME, AWAY) is None
    # Only one team named: not this game's bug.
    assert parse_bug([r for r in rows if r.text != "ORL"], HOME, AWAY) is None


def _scores(*rows):
    """('home-away', wall, spread) -> score events oldest first."""
    return [Event(w, "score", label=s, spread=sp, score=tuple(int(x) for x in s.split("-")))
            for s, w, sp in rows]


def test_match_math_and_rejections():
    tl = type("TL", (), {})()
    tl.scores = _scores(("57-58", 900.0, 0), ("59-60", 1000.0, 0))
    w = ScoreBugWatcher()
    tune = object()
    got = []
    w.on_sample = got.append
    # frames at 1020 (old), 1022 (new), 1024 (new): onset 1021 -> 21 s behind the 1000 play.
    for t, bug in ((1020.0, Bug(57, 58)), (1022.0, Bug(59, 60)), (1024.0, Bug(59, 60))):
        w.observe("stick", "prime:abc", tune, t, bug, tl)
    assert len(got) == 1 and got[0].sample == pytest.approx(21.0) and got[0].error_s == pytest.approx(1.0)
    # And again later: no second sample from the same change.
    assert w.observe("stick", "prime:abc", tune, 1026.0, Bug(59, 60), tl) is None

    e, why = match_change(_scores(("57-58", 995.0, 0), ("59-60", 1000.0, 0)), (57, 58),
                          (59, 60), 1021.0)
    assert e is None and "10 s" in why
    e, why = match_change(_scores(("55-58", 900.0, 0), ("59-60", 1000.0, 0)), (57, 58), (59, 60), 1021.0)
    assert e is None and "previous" in why
    e, why = match_change(_scores(("57-58", 900.0, 0), ("59-60", 1000.0, 0)), (57, 58), (61, 60), 1021.0)
    assert e is None


def test_out_of_range_tune_change_and_unread_frames_give_no_sample():
    tl = type("TL", (), {})()
    tl.scores = _scores(("57-58", 900.0, 0), ("59-60", 1000.0, 0))
    w = ScoreBugWatcher()
    t1 = object()
    for t, bug in ((1002.0, Bug(57, 58)), (1004.0, Bug(59, 60)), (1006.0, Bug(59, 60))):
        assert w.observe("s", "prime:abc", t1, t, bug, tl) is None   # 3 s behind: out of range
    for t, bug in ((1020.0, Bug(57, 58)), (1022.0, None), (1024.0, Bug(59, 60)), (1026.0, Bug(59, 60))):
        w.observe("s", "prime:abc", t1, t, bug, tl)
    assert not w.samples   # an unread frame between the scores: the change isn't bracketed
    w.observe("s", "prime:abc", t1, 1030.0, Bug(57, 58), tl)
    w.observe("s", "prime:abc", object(), 1032.0, Bug(59, 60), tl)  # a new tune: frames start over
    w.observe("s", "prime:abc", object(), 1034.0, Bug(59, 60), tl)
    assert not w.samples
    assert w.observe("s", "youtubetv:espn", t1, 1036.0, Bug(59, 60), tl) is None


def test_timeline_keeps_every_score_change_including_free_throws():
    tl = Timeline(League.NBA, clock=lambda: 2000.0)
    g = GameState(League.NBA, "1", Team("ORL", "Magic", 50), Team("WSH", "Wizards", 50), phase=Phase.LIVE,
                  updated_at=2000.0)
    g.recent_plays = [Play("a", "old", "jumper", False, 1900.0)]
    tl.observe(g)
    g.recent_plays = [Play("a", "old", "jumper", False, 1900.0),
                      Play("b", "makes layup", "layup", True, 1980.0, score=(52, 50)),
                      Play("c", "free throw 1 of 2", "free_throw_-_1_of_2", True, 1990.0, score=(53, 50))]
    g.home.score = 53
    tl.observe(g)
    assert [(e.score, e.seen) for e in tl.scores] == [((52, 50), 1980.0), ((53, 50), 1990.0)]


def test_screen_samples_settle_an_app_on_their_own_and_persist():
    now = 10_000.0
    book = DelayBook({"prime": 40.0}, 12.0, clock=lambda: now)
    d = book.get("prime:abc")
    for i, x in enumerate((24.0, 25.0, 24.5)):
        book.screen("prime:abc", x, now - 60 + i)
    assert d.source == "screen" and d.mu == pytest.approx(24.5) and d.sigma == pytest.approx(SCREEN_SIGMA_S)
    assert book.settled("prime") and book.measured("prime:abc")
    other = book.get("prime:xyz")   # another Prime game: the app's pooled samples, with the shared margin
    assert other.source == "app" and other.mu == pytest.approx(24.5)
    # Two only: still the tap floor.
    b2 = DelayBook({"prime": 40.0}, 12.0, clock=lambda: now)
    b2.load([(now - 5, "prime:abc", 24.0, "screen"), (now - 4, "prime:abc", 25.0, "screen")])
    assert b2.get("prime:abc").sigma == pytest.approx(4.0) and not b2.settled("prime")
    # Taps still count alongside.
    b2.calibrate("prime:abc", 26.0)
    assert b2.settled("prime") and b2.get("prime:abc").source == "tap"
