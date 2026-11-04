"""Scenario-tree lookup (redzone/decider/scenarios.py): branch match, invalidation, re-plan."""
from types import SimpleNamespace as NS

import pytest

from redzone.decider import scenarios as sc


class Cfg(dict):
    def get(self, k, default=None):
        return super().get(k, default)


def cand(lg="nfl", score=50.0, **kw):
    s = dict(crunch=False, personal=0, garbage=False, is_break=False, lock=None, pre_moment=None, score=score,
             league=NS(base=NS(value=lg)))
    s.update(kw)
    return NS(target=f"yt:{lg}{score}", stale=False, scored=NS(**s))


def dec_with(current, states):
    """A decider stand-in whose slate is `states` (gid -> state word), by patching state_of."""
    return NS(current=current, cfg=Cfg(), user=NS(muted=set()), _states=states)


def run(monkeypatch, current, states, cands, table=None):
    monkeypatch.setattr(sc, "state_of", lambda dec, gid, x, now: dec._states[gid])
    s = sc.Scenarios(Cfg({"playbooks.game.enabled": True}))
    if table is not None:
        s.table = table
    d = dec_with(current, states)
    return s, d, s.step(d, cands, 0.0)


@pytest.mark.playbook("GAME-01")
def test_game01_tv_timeout_while_another_game_is_in_the_red_zone(monkeypatch):
    cands = {"A": cand(), "B": cand(score=60), "C": cand(score=90)}
    s, d, p = run(monkeypatch, "A", {"A": "long_break", "B": "redzone", "C": "live"}, cands)
    assert p.branch["id"] == "GAME-01" and p.a == "A" and p.b == "B" and p.branch["back"] == "restart"


@pytest.mark.playbook("GAME-01")
def test_game01_needs_a_long_break_and_an_nfl_red_zone(monkeypatch):
    cands = {"A": cand(), "B": cand()}
    assert run(monkeypatch, "A", {"A": "break", "B": "redzone"}, cands)[2] is None
    cands = {"A": cand(), "B": cand(lg="cfb")}
    assert run(monkeypatch, "A", {"A": "long_break", "B": "redzone"}, cands)[2] is None


@pytest.mark.playbook("GAME-02")
def test_game02_score_then_power_play(monkeypatch):
    cands = {"A": cand("nhl"), "C": cand("nhl", 70)}
    p = run(monkeypatch, "A", {"A": "after_score", "C": "pp"}, cands)[2]
    assert p.branch["id"] == "GAME-02" and p.b == "C"


@pytest.mark.playbook("GAME-03")
def test_game03_your_team_crunch_wins_first(monkeypatch):
    cands = {"A": cand(), "B": cand(), "Y": cand("nba")}
    p = run(monkeypatch, "A", {"A": "long_break", "B": "redzone", "Y": "my_crunch"}, cands)[2]
    assert p.branch["id"] == "GAME-03" and p.branch["do"] == "switch" and p.b == "Y"
    p = run(monkeypatch, "Y", {"A": "long_break", "B": "redzone", "Y": "my_crunch"}, cands)[2]
    assert p.branch["do"] == "hold" and p.b is None


@pytest.mark.playbook("GAME-01", "GAME-03")
def test_plan_is_invalidated_and_replanned(monkeypatch):
    cands = {"A": cand(), "B": cand(), "C": cand()}
    states = {"A": "long_break", "B": "redzone", "C": "live"}
    s, d, p = run(monkeypatch, "A", states, cands)
    assert p.b == "B"
    states.update(B="live", C="redzone")            # B's drive ended, C reached the red zone
    p2 = s.step(d, cands, 1.0)
    assert p2.b == "C"
    states.update(C="live")
    assert s.step(d, cands, 2.0) is None


def test_table_from_a_file(tmp_path):
    f = tmp_path / "t.json"
    f.write_text('[{"id": "X-1", "on": ["live"], "other": [], "do": "hold", "back": ""}]')
    assert sc.Scenarios(Cfg({"playbooks.game.table": str(f)})).table[0]["id"] == "X-1"
    assert sc.Scenarios(Cfg()).table == sc.SEED
    assert not sc.Scenarios(Cfg()).enabled()


@pytest.mark.playbook("GAME-01")
def test_skipped_rows_are_left_out_of_the_table():
    s = sc.Scenarios(Cfg({"playbooks.game.enabled": True, "playbooks.game.skip": ["GAME-01"]}))
    assert all(r["id"] != "GAME-01" for r in s.table) and any(r["id"] == "GAME-03" for r in s.table)
