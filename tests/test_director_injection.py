"""park.director_v2 (task A-04): a director object handed to `park_show.Show` is used only with the flag on.

Pinned here: Director v1 has the shape `director_protocol.ShowDirector` names; flag off, a handed-over object is never
touched and the show builds its own Director v1 as before; flag on, the handed-over object gets the very calls Director
v1 would get; flag on with nothing handed over is as before; and on a replayed night (fake sticks) flag off decides
byte for byte the same with and without an object handed over.

Why: task A-04 (redzone-agents/v2/WORKPLAN.md): the seam for track A's trained director must not move a single
decision while its flag is off.
"""
from __future__ import annotations

import functools
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from redzone import config
from redzone.lab.gym2 import env as gym
from redzone.noggin import director as dr
from redzone.noggin import director_protocol as dp
from redzone.noggin.park_show import Show

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
NIGHT, GAMES, MINUTES = "20261023", 2, 30
needs_archive = pytest.mark.skipif(not (ROOT / "data/archive/nba").exists(), reason="pinned eval data not fetched (scripts/fetch_eval_data.py)")


class Untouchable:
    """Has the director's shape; fails the run if the show ever reads or calls it."""

    def __init__(self):
        self.calls = 0

    @property
    def at(self):
        raise AssertionError("the show read `at` of a director it must ignore")

    @property
    def now(self):
        raise AssertionError("the show read `now` of a director it must ignore")

    def weigh(self, *a, **k):
        raise AssertionError("the show weighed with a director it must ignore")

    def why(self, *a, **k):
        raise AssertionError("the show asked a director it must ignore")


class Recording(dr.Director):
    """Director v1 that keeps every call made to it (arguments as text, so later edits cannot leak in)."""

    log: list | None = None

    def weigh(self, t, on, arrs, edges, seen):
        Recording.log.append(("weigh", t, on, len(arrs), tuple(edges), type(seen).__name__))
        return super().weigh(t, on, arrs, edges, seen)

    def why(self, g1, g2, base_why=""):
        out = super().why(g1, g2, base_why)
        Recording.log.append(("why", g1, g2, base_why, tuple(sorted(out.items()))))
        return out


@functools.lru_cache(maxsize=1)
def _night():
    return gym.replay_module().load_replay(NIGHT, GAMES)


def _run(pr, monkeypatch, *, director: bool, **show_kw):
    """One replay of NIGHT, recorded as the parity gate records it (Show built with `show_kw` added)."""
    monkeypatch.setattr(pr, "Show", functools.partial(Show, **show_kw) if show_kw else Show)
    rec = gym.Recorder()
    show = pr.replay(NIGHT, GAMES, minutes=MINUTES, seen=director, director=director, say=rec.say, emit=rec.emit,
                     loaded=_night())
    return show, gym.digest(gym.decision_list(show, rec))


# ---------------------------------------------------------------------------------------------- the shape and the flag
def test_director_v1_has_the_shape_the_show_uses():
    d = dr.Director(["nba", "nfl"])
    assert dp.is_show_director(d) and isinstance(d, dp.ShowDirector)
    seen = SimpleNamespace(staleness=lambda g: 0.0, last_seen_s=lambda g: None)
    ws = d.weigh(0, 0, [], [], seen)
    assert list(ws) == [] and d.at == 0
    assert all(isinstance(w, dp.GameWeight) for w in [dr.Weigh(), dr.Weigh(a=3.0)])
    assert set(d.why(0, 1, "the better play is there")) == {"driver", "director"}
    assert d.why(0, 1)["driver"] in dp.DRIVERS
    assert not dp.is_show_director(object()) and not dp.is_show_director(SimpleNamespace(at=None, now=[], weigh=1, why=2))


def test_the_flag_is_off_by_default_and_read_only_as_a_literal_true(tmp_path):
    assert config.default("park.director_v2") is False
    assert dp.director_v2_on({}.get) is False and dp.director_v2_on({"park.director_v2": "yes"}.get) is False
    assert dp.director_v2_on({"park.director_v2": True}.get) is True
    (tmp_path / "config.yaml").write_text("park:\n  director_v2: true\n")
    c = config.Config(user_path=tmp_path / "config.yaml")
    assert c.rejected == [] and dp.director_v2_on(c.get) is True


@needs_archive
def test_flag_on_refuses_an_object_without_the_shape(monkeypatch):
    pr = gym.replay_module()
    monkeypatch.setattr(pr, "Show", functools.partial(Show, director_v2=True, director_obj=object()))
    with pytest.raises(TypeError, match="park.director_v2"):
        pr.build(NIGHT, GAMES, minutes=MINUTES, loaded=_night())


# ---------------------------------------------------------------------------------------------- on a replayed night (fake sticks)
@needs_archive
def test_flag_off_ignores_a_handed_over_object_and_builds_its_own_director(monkeypatch):
    pr = gym.replay_module()
    _, plain = _run(pr, monkeypatch, director=False)
    bomb = Untouchable()
    show, with_bomb = _run(pr, monkeypatch, director=False, director_v2=False, director_obj=bomb)
    assert show.director is None and with_bomb == plain
    _, v1 = _run(pr, monkeypatch, director=True)
    show, v1_bomb = _run(pr, monkeypatch, director=True, director_v2=False, director_obj=bomb)
    assert type(show.director) is dr.Director and v1_bomb == v1 and v1 != plain and show.jumps_done >= 3


@needs_archive
def test_flag_on_hands_the_object_exactly_the_calls_director_v1_gets(monkeypatch):
    pr = gym.replay_module()
    Recording.log, v1_cls = [], dr.Director
    monkeypatch.setattr(dr, "Director", Recording)               # Director v1 built by the show, as before (flag off)
    own_show, own = _run(pr, monkeypatch, director=True)
    calls_own = list(Recording.log)
    monkeypatch.setattr(dr, "Director", v1_cls)
    Recording.log = []
    injected = Recording(own_show.leagues, pr._director_params())
    show, handed = _run(pr, monkeypatch, director=False, director_v2=True, director_obj=injected)
    assert show.director is injected and handed == own and Recording.log == calls_own
    assert any(c[0] == "weigh" for c in calls_own) and any(c[0] == "why" for c in calls_own)
    assert sum(show.drivers.values()) == show.jumps_done >= 3


@needs_archive
def test_flag_on_with_nothing_handed_over_is_as_before(monkeypatch):
    pr = gym.replay_module()
    _, plain = _run(pr, monkeypatch, director=False)
    _, plain_on = _run(pr, monkeypatch, director=False, director_v2=True)
    assert plain_on == plain
    _, v1 = _run(pr, monkeypatch, director=True)
    show, v1_on = _run(pr, monkeypatch, director=True, director_v2=True, director_obj=None)
    assert v1_on == v1 and type(show.director) is dr.Director


@needs_archive
def test_flag_off_decisions_are_identical_with_and_without_an_object(monkeypatch):
    pr = gym.replay_module()
    show_a, a = _run(pr, monkeypatch, director=False)
    show_b, b = _run(pr, monkeypatch, director=False, director_v2=False, director_obj=Untouchable())
    assert a == b and show_a.records == show_b.records and show_a.jump_at == show_b.jump_at and show_a.jumps_done >= 3
