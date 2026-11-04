"""[one brain, step 7] Typed contracts + the per-tick WorldState, in shadow: built beside the legacy path,
never changing what it decides (parity exact), and off by default."""
import json
from dataclasses import FrozenInstanceError

import pytest
from conftest import nfl

from redzone.contracts import scored as S
from redzone.scoring.engine import Personal, Scorer


def _engine(tmp_path, shadow: bool):
    from redzone.config import Config
    from redzone.engine import Engine
    from redzone.store import Store
    (tmp_path / "c.yaml").write_text(f"onebrain:\n  world_shadow: {'true' if shadow else 'false'}\n")
    e = Engine(Config(user_path=tmp_path / "c.yaml", history_dir=tmp_path / "h"), Store(tmp_path / "db"))
    e.toast = lambda *a, **k: None
    return e


def _night(e):
    games = [nfl("a", "WSH", "DAL", 21, 17, 4, 95.0, ytg=12), nfl("b", "KC", "BUF", 3, 31, 2, 400.0),
             nfl("c", "NYG", "PHI", 10, 10, 3, 600.0)]
    out = []
    for step, g in enumerate(games * 2):
        e.merger.update("t", g)
        e.decide()
        d = e.last_decision
        out.append((step, d.action if d else None, d.target if d else None, d.reason if d else None))
    return out


def test_the_contracts_are_frozen_copies_of_the_live_types():
    from redzone.config import Config
    sc = Scorer(Config()).score(nfl("a", "WSH", "DAL", 21, 17, 4, 95.0, ytg=12), Personal())
    c = S.from_scored(sc)
    assert (c.game_id, c.league, c.score, c.crunch, c.lock) == (sc.game_id, "nfl", sc.score, sc.crunch, sc.lock)
    with pytest.raises(FrozenInstanceError):
        c.score = 1.0
    with pytest.raises(TypeError):
        c.factors["x"] = 1.0          # a read-only mapping, not the scorer's dict
    sc.factors["late_add"] = 9.0
    assert "late_add" not in c.factors


async def test_shadow_off_by_default_builds_nothing(tmp_path):
    e = _engine(tmp_path, shadow=False)
    _night(e)
    assert e.world is None and not (tmp_path / "world_shadow.jsonl").exists()


async def test_shadow_never_changes_the_decision_and_logs_the_world(tmp_path):
    off = _night(_engine(tmp_path / "off", shadow=False)) if (tmp_path / "off").mkdir() is None else None
    (tmp_path / "on").mkdir()
    e = _engine(tmp_path / "on", shadow=True)
    on = _night(e)
    assert on == off                                   # parity: exact
    w = e.world
    assert w is not None and set(w.games) == {"a", "b", "c"} and w.tick == e._tick
    assert w.games["a"].scored.game_id == "a" and isinstance(w.games["a"].scored, S.Scored)
    rows = [json.loads(x) for x in (tmp_path / "on" / "world_shadow.jsonl").read_text().splitlines()]
    assert rows and rows[0]["live"] == 1 and "legacy" in rows[0]      # first tick, then once a minute
    from redzone import worldstate
    sm = worldstate.summary(w, e.last_decision)
    assert sm["live"] == 3 and len(sm["top"]) == 3 and sm["top"][0]["value"] >= sm["top"][-1]["value"]
    assert sm["legacy"]["action"] == e.last_decision.action


async def test_a_broken_shadow_never_breaks_the_tick(tmp_path, monkeypatch):
    from redzone import worldstate
    e = _engine(tmp_path, shadow=True)
    monkeypatch.setattr(worldstate, "build", lambda *a, **k: 1 / 0)
    _night(e)                                          # no exception
    assert e.last_decision is not None
