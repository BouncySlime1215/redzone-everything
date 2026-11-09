"""scripts/queue_trace.py: why the queue beats the planner in the night replay, on tiny hand-built nights.

Why: the NFL replay said queue 26.8% vs planner 12.3% ready-in-time and the wired arm 12.3%; the trace found the gain
was the RedZone channel (a pseudo-game the replay's planner could not list) left on the stick. These pin that the
trace names it, and that a planner given the channel (as the TV gives it) closes the gap.
"""
import importlib.util
import sys
import types
from pathlib import Path

import pytest

from redzone import standby
from redzone.config import Config

_scripts = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_scripts))
_spec = importlib.util.spec_from_file_location("queue_trace", _scripts / "queue_trace.py")
qt = importlib.util.module_from_spec(_spec)
sys.modules["queue_trace"] = qt
_spec.loader.exec_module(qt)
qr = qt.qr


@pytest.fixture
def cfg(tmp_path):
    return Config(user_path=tmp_path / "config.yaml", history_dir=tmp_path / "hist")


def cand(gid, score):
    return types.SimpleNamespace(scored=types.SimpleNamespace(score=score, is_break=False), target=f"youtubetv:{gid}",
                                 stale=False)


def night(switches, ranked, ticks=70, step=10.0):
    """Games a and b live all night; the decider starts on a. `ranked`: the planner's list of game ids."""
    cur, sw = "a", [dict(s, t=1000.0 + step * s["t"]) for s in switches]
    tr = qr.Trace("n1", [], sw)
    for i in range(ticks):
        t = 1000.0 + step * i
        for s in sw:
            if s["t"] == t:
                cur = s["to"]
        cands = {g: cand(g, 70 - 5 * k) for k, g in enumerate(("a", "b"))}
        opts = [standby.Option(g, f"youtubetv:{g}", standby.BEST, 70.0 - k, "x") for k, g in enumerate(ranked)
                if g != cur]
        tr.ticks.append({"t": t, "cands": cands, "current": cur, "want": None, "opts": opts})
    return tr


RZ = [{"t": 10, "frm": "a", "to": "redzone", "kind": "break_visit"},
      {"t": 20, "frm": "redzone", "to": "a", "kind": "return"},
      {"t": 55, "frm": "a", "to": "redzone", "kind": "break_visit"}]


def test_the_gain_is_the_redzone_channel_left_on_the_stick(cfg):
    out = qt.trace_cuts(night(RZ, ["b"]), cfg)
    assert [(g["game"], g["to_is_redzone"], g["rung"], g["reaches"]) for g in out["gains"]] == \
        [("redzone", True, "LEFT_BEHIND", False)]
    assert out["gains"][0]["held"] == "game_not_cut_soon"   # the planner loaded b, which the show never cut to
    assert out["ready"]["on"] > out["ready"]["off"] == out["ready"]["wired"]


def test_a_planner_that_lists_the_channel_closes_the_gap(cfg):
    for mode in ("ranked", "first"):
        out = qt.trace_cuts(night(RZ, ["b"]), cfg, redzone=mode)
        assert not [g for g in out["gains"] if g["to_is_redzone"]], mode
        assert out["ready"]["off"] >= out["ready"]["on"], mode


def test_with_redzone_leaves_a_night_with_no_channel_cut_alone():
    tr = night([{"t": 30, "frm": "a", "to": "b", "kind": "switch"}], ["b"])
    assert qt.with_redzone(tr, "first") is tr and qt.with_redzone(tr, "none") is tr


def test_with_redzone_adds_it_only_while_the_decider_uses_it():
    out = qt.with_redzone(night(RZ, ["b"]), "ranked")
    has = [any(o.game_id == "redzone" for o in tk["opts"]) for tk in out.ticks]
    assert has[25] and not has[2] and not has[12] and not has[65]   # on screen 10-19; not used before 10 or after 55
    assert not has[10] and has[20] and has[54]    # on the channel itself (10-19) it is no option; from the return (20) it is
    assert all("redzone" not in tk["cands"] for tk in out.ticks[:10] + out.ticks[56:])
    assert all("redzone" in tk["cands"] for tk in out.ticks[10:56])


def test_a_real_game_the_queue_holds_is_not_called_redzone(cfg):
    # b is loaded early by the planner and left behind by the cut away from it; no channel anywhere
    sw = [{"t": 20, "frm": "a", "to": "b", "kind": "switch"}, {"t": 30, "frm": "b", "to": "a", "kind": "return"},
          {"t": 60, "frm": "a", "to": "b", "kind": "switch"}]
    out = qt.trace_cuts(night(sw, ["b"]), cfg)
    assert not [g for g in out["gains"] if g["to_is_redzone"]]


def _s(cuts, on, off, wired, rz, real, lost):
    return {"cuts": cuts, "ready_in_time_pct": {}, "queue_losses_vs_planner": lost, "gain_target_is_redzone_channel": rz,
            "gains_real_games": real, "gains": rz + real, "real_game_gain_pts": round(100 * (real - lost) / (cuts or 1), 1)}


def test_recommend_judges_real_games_net_of_losses_not_the_channel():
    assert qt.recommend({"ranked": _s(1000, 0, 0, 0, 400, 5, 40)}).startswith("no")
    assert qt.recommend({"ranked": _s(1000, 0, 0, 0, 0, 60, 10)}).startswith("yes")
    assert qt.recommend({"none": _s(0, 0, 0, 0, 0, 0, 0)}) == "no data"


def test_summarize_counts_gains_by_where_they_came_from(cfg):
    p = qt.trace_cuts(night(RZ, ["b"]), cfg)
    s = qt.summarize([p])
    assert s["gain_target_is_redzone_channel"] == s["gains"] == 1 and s["gains_real_games"] == 0
    assert s["gains_lost_by_the_wiring"] == 1 and s["by_rung"] == {"LEFT_BEHIND": 1}


def test_the_table_says_the_times_are_modeled():
    assert "MODELED" in qt.table({"leagues": {}})
