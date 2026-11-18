"""Director v1 (redzone/noggin/director.py): (a) a big moment pending, (b) freshness, (c) Nick's league weights, as
multipliers on what the PARK planner values; the driver each jump's warrant names; the flag; and, on a replayed night
with fake sticks, Rule #1 by the frozen grader with the director on.

Why: Director v1 is the baseline a later AI director must beat; its weights, its warrant and its flag-off identity are
pinned here so the AI is measured against the same thing.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from redzone import config
from redzone.noggin import director as dr
from redzone.noggin.park_core import Arr

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
needs_archive = pytest.mark.skipif(not (ROOT / "data/archive/nba").exists(), reason="pinned eval data not fetched (scripts/fetch_eval_data.py)")
TABLE = (("cbb", 1.0), ("mlb", 0.9), ("nba", 1.05), ("nfl", 1.15))


# ---------------------------------------------------------------------------------------------- the flag and the numbers
def test_the_flags_are_off_by_default_and_the_config_accepts_every_director_key(tmp_path):
    assert config.default("park.director_v1") is False and config.default("park.seen") is False
    (tmp_path / "config.yaml").write_text("park:\n  seen: true\n  director_v1: true\n  director:\n    big_w: 4\n    fresh_w: 2.0\n")
    c = config.Config(user_path=tmp_path / "config.yaml", history_dir=tmp_path / "hist")
    assert c.rejected == [] and c.get("park.director_v1") is True and c.get("park.director.big_w") == 4
    p = dr.DirectorParams.from_config(c.get)
    assert (p.big_w, p.fresh_w, p.stale_ref, p.big_look, p.taste) == (4.0, 2.0, 600.0, 90, True)


def test_the_numbers_come_from_park_director_and_the_league_weights_from_score_league_weight():
    p = dr.DirectorParams.from_config(config.default)
    assert p == dr.DirectorParams(league_weight=p.league_weight)            # the YAML holds the code's own defaults
    assert dict(p.league_weight)["nfl"] == 1.15 and dict(p.league_weight)["nba"] == 1.05
    bad = dr.DirectorParams.from_config(lambda k, d=None: {"park.director.big_w": "lots", "park.director.taste": 1}.get(k, d))
    assert bad.big_w == 3.0 and bad.taste is True                           # a value of the wrong kind keeps the default


def test_park_run_builds_director_params_only_with_the_flag_on(cfg):
    from redzone.park_run import director_params
    assert director_params(cfg) is None
    cfg.update({"park": {"director_v1": True}})
    assert isinstance(director_params(cfg), dr.DirectorParams)


# ---------------------------------------------------------------------------------------------- the three goals
def test_taste_is_each_league_over_the_best_on_the_slate_so_one_league_is_unchanged():
    assert dr.taste_weights(["nba", "nba", "nba"], TABLE) == [1.0, 1.0, 1.0]
    w = dr.taste_weights(["nfl", "nba", "xyz"], TABLE)
    assert w[0] == 1.0 and w[1] == pytest.approx(1.05 / 1.15) and w[2] == pytest.approx(1.0 / 1.15)
    assert dr.taste_weights(["nba"], (("nba", 9.0),)) == [1.0] and dr.taste_weights(["a", "b"], (("a", 9.0), ("b", 0.1))) == [1.0, 0.5 / 1.5]


def test_freshness_rises_in_a_straight_line_to_its_cap():
    p = dr.DirectorParams(fresh_w=1.0, stale_ref=600.0)
    assert dr.freshness(0.0, p) == 1.0 and dr.freshness(300.0, p) == 1.5 and dr.freshness(600.0, p) == 2.0 and dr.freshness(5000.0, p) == 2.0
    assert dr.freshness(100.0, dr.DirectorParams(stale_ref=0.0)) == 1.0


def game(hl=(), t0=10, t1=390):
    return Arr({"gid": "g", "t0": t0, "t1": t1, "dead": [], "brk": [], "shots": [], "big": [], "hl": list(hl), "lev": [], "v2": []},
               0, 400, False)


def test_a_big_moment_is_pending_while_a_high_leverage_play_is_in_the_feeds_last_look():
    x = game(hl=[(200, 207)])
    assert dr.pending(x, 210, 210, 90) and dr.pending(x, 250, 290, 90)
    assert not dr.pending(x, 300, 300, 90)                                  # gone from the last 90 s
    assert not dr.pending(x, 150, 150, 90)                                  # not yet reported
    assert not dr.pending(game(hl=[(200, 207)], t1=205), 210, 210, 90)      # the game is over


def test_the_driver_is_a_for_a_pending_big_moment_then_the_stronger_of_freshness_and_taste():
    here, w = dr.Weigh(), dr.Weigh
    assert dr.driver(here, w(a=3.0, b=1.0)) == "a"
    assert dr.driver(w(a=3.0), w(a=3.0, b=1.5)) == "b"                      # both pending: the moment is not what moved it
    assert dr.driver(here, w(b=1.2, c=1.0)) == "b" and dr.driver(here, w(b=1.05, c=1.1)) == "c"
    assert dr.driver(here, w(), "the better play is there") == "c" and dr.driver(here, w(), "left dead time for live play") == "b"
    assert dr.driver_text("b", w(b=1.5, stale=410.0, unseen_s=395), "nba") == "b: keep every game fresh (unseen 6 min 35 s, staleness 410)"
    assert dr.driver_text("b", w(), "nba").endswith("(not seen yet tonight)") and dr.driver_text("a", w(), "nba").startswith("a: never miss")
    assert dr.driver_text("c", w(c=0.91), "nba").startswith("c: Nick's taste (NBA weighted 0.91")


def test_the_director_weighs_each_game_and_the_one_on_screen_is_fresh():
    seen = SimpleNamespace(staleness=lambda g: [50.0, 600.0, 300.0][g], last_seen_s=lambda g: [0, None, 120][g])
    d = dr.Director(["nfl", "nba", "nba"], dr.DirectorParams(league_weight=TABLE))
    ws = d.weigh(220, 0, [game(), game(hl=[(200, 207)]), game()], [230, 230, 230], seen)
    assert d.at == 220 and [x.b for x in ws] == [1.0, 2.0, 1.5] and [x.a for x in ws] == [1.0, 3.0, 1.0]
    assert ws[1].c == pytest.approx(1.05 / 1.15) and ws[1].w == pytest.approx(3.0 * 2.0 * 1.05 / 1.15)
    assert d.why(0, 1)["driver"] == "a" and d.why(0, 2)["driver"] == "b"
    assert dr.Director(["nba"]).why(0, 0, "the better play is there") == {"driver": "c", "director": "c: Nick's taste"}


# ---------------------------------------------------------------------------------------------- on a replayed night (fake sticks)
@needs_archive
def test_flag_off_builds_nothing_and_flag_on_keeps_rule_1_and_names_a_goal_in_every_warrant():
    import park_replay as pr
    loaded = pr.load_replay("20261023", 2)
    off = pr.replay("20261023", 2, loaded=loaded, minutes=45)
    assert off.director is None and off.seen_state is None and all(off.pref(g) == off.prefs[g] for g in range(off.N))
    on = pr.replay("20261023", 2, loaded=loaded, minutes=45, seen=True, director=True)
    fg = pr.graded(on)
    assert (fg["grader_entries_mid_play"], fg["grader_entries_mid_play_strict"], fg["grader_leaves_mid_play"]) == (0, 0, 0)
    j = on.judge
    assert (j.mid_entry, j.mid_leave, j.hl_leave) == (0, 0, 0) and on.jumps_done >= 5
    assert all(r["warrant"]["ok"] and r["warrant"]["driver"] in ("a", "b", "c") and r["warrant"]["director"][0] == r["warrant"]["driver"]
               for r in on.records)
    assert sum(on.drivers.values()) == on.jumps_done
    sc = pr.seen_card(on)
    assert sc["drivers"] == on.drivers and sc["worst_unseen_s"] >= sc["mean_unseen_s"] >= 0 and sc["big_seen_live"] <= sc["big_total"]
