"""Catch-up the RedZone way (replay.redzone_way, Nick Nov 4): T2 lull, T3 meanwhile, T4 collision,
T5 montage and the 10-minute value curve. Off by default; these pin the behaviour when it is on."""
import pytest

from redzone.decider.decider import Missed
from redzone.decider.screen import Event
from redzone.models import League
from tests.test_decider import cands, dec, sc  # noqa: F401  (fixture)


@pytest.fixture
def rw(dec):  # noqa: F811
    dec.cfg.update({"replay": {"redzone_way": True}}, record=False)
    return dec


def test_off_by_default(dec):  # noqa: F811
    assert dec.cfg.get("replay.redzone_way") is False and not dec._rw()


def test_value_halves_every_ten_minutes_and_urgency_hits_the_due_time(rw, clock):
    now = clock.t
    m = Missed("b", "td1", "TD", "td", now - 600, 80, due_s=600)
    assert rw._replay_value(m, now) == pytest.approx(80 * 0.5 + 40.0)      # half value, fully due
    m.t = now - 299
    assert rw._urgency(m, now) == 0.0
    m.t = now - 450
    assert rw._urgency(m, now) == pytest.approx(0.5)


def test_collision_goes_to_the_head_of_the_queue(rw, clock):
    now = clock.t
    plain = Missed("b", "td1", "TD", "td", now - 60, 90, due_s=600)
    hit = Missed("c", "td2", "TD", "td", now - 60, 60, due_s=180, must=True)
    rw.queue = [plain, hit]
    cs = cands(b=10, c=10)
    assert rw._best_replay(cs, now, None, False) is hit
    # ...and it clears the "worth leaving live play" bar that the plain one can't
    assert rw._best_replay(cs, now, None, False, top=200.0) is hit


def test_collided_reads_what_was_on_screen(rw, clock):
    now = clock.t
    for g in "ab":
        rw.timeline(g, League.NFL)
        rw._known[g] = cands(**{g: 50})[g]
    d = rw.delays.get("youtubetv:a")
    rw._screen_log.append((now - 200, "a"))
    rw.timelines["a"].events.append(Event(now - 30 - d.mu, "play", "TD a", big=True, pid="x"))
    assert rw._collided("b", now - 30, 8.0)
    assert not rw._collided("b", now - 120, 8.0)


def test_meanwhile_after_a_score_on_screen(rw, clock):
    now = clock.t
    rw.current = "a"
    rw.timeline("a", League.NFL)
    rw._known["a"] = cands(a=50)["a"]
    d = rw.delays.get("youtubetv:a")
    rw.timelines["a"].events.append(Event(now - 30 - d.mu, "play", "TD a", big=True, pid="x"))
    same_time = Missed("b", "td1", "TD", "td", now - 30 - d.mu + 20, 40, due_s=600)
    earlier = Missed("c", "td2", "TD", "td", now - 400, 90, due_s=600)
    rw.queue = [earlier, same_time]
    assert rw._best_replay(cands(b=10, c=10), now, None, False) is same_time


def test_montage_reaches_old_plays_only_when_nothing_is_live(rw, clock):
    now = clock.t
    old = Missed("b", "td1", "TD", "td", now - 5000, 75, due_s=600)
    rw.queue = [old]
    cs = cands(b=10)
    assert rw._best_replay(cs, now, None, False) is None
    assert rw._best_replay(cs, now, None, False, montage=True) is old


def test_lull_needs_every_game_quiet(rw):
    assert rw._lull(cands(a=20, b=30))
    assert not rw._lull(cands(a=20, b=60))
    assert not rw._lull({"a": cands(a=sc("a", 20, lock="final two minutes"))["a"]})


def test_drive_in_20_off_by_default_and_picks_the_red_zone_game(dec, clock):  # noqa: F811
    cs = cands(a=40, b=sc("b", 50, lock="red zone drive"), c=sc("c", 70, lock="final two minutes"))
    dec.current = "a"
    assert dec._rz_drive(cs, clock.t) is None
    dec.cfg.update({"redzone_mode": {"drive_in_20": True}}, record=False)
    assert dec._rz_drive(cs, clock.t) == "b"
    dec.current = "b"
    assert dec._rz_drive(cs, clock.t) is None          # already there
    dec.current = "a"
    cs["b"].scored.factors["break_p"] = 0.9             # a field goal try / commercial coming: stay put
    assert dec._rz_drive(cs, clock.t) is None
