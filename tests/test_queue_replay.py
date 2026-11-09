"""Night mode of scripts/queue_replay.py: queue on vs off through a fake stick, no archive, no TV.

Why: the queue's top goal is queueing games fast; these pin the measure itself (ready in time, wanted-to-ready
seconds, idle sticks, swaps, failures) on tiny hand-built nights, so a number that moves is the queue or the
planner moving and not the harness. Every stick time here is modeled.
"""
import importlib.util
import sys
import types
from pathlib import Path

import pytest

from redzone import standby
from redzone.config import Config
from redzone.noggin import gamequeue as gq

_spec = importlib.util.spec_from_file_location("queue_replay", Path(__file__).resolve().parent.parent / "scripts" / "queue_replay.py")
qr = importlib.util.module_from_spec(_spec)
sys.modules["queue_replay"] = qr
_spec.loader.exec_module(qr)


@pytest.fixture
def cfg(tmp_path):
    return Config(user_path=tmp_path / "config.yaml", history_dir=tmp_path / "hist")


def cand(gid, score, app="youtubetv"):
    return types.SimpleNamespace(scored=types.SimpleNamespace(score=score, is_break=False), target=f"{app}:{gid}",
                                 stale=False)


def night(plan, games, switches, app=lambda g: "youtubetv", step=10.0, ticks=60, wants=None):
    """plan: {tick index: [ranked game ids]} for the planner's options; games: ids live on the slate; a game the
    slate drops at tick n is {id: n}. wants: {tick index: game} the decider is waiting to cut to."""
    cur, tr = "a", qr.Trace("n1", [], [dict(sw, t=1000.0 + step * sw["t"]) for sw in switches])
    for i in range(ticks):
        live = [g for g, until in games.items() if until is None or i < until]
        cands = {g: cand(g, 70 - 5 * k, app(g)) for k, g in enumerate(live)}
        ranked = next((v for k, v in sorted(plan.items(), reverse=True) if k <= i), [])
        for s in [s for s in tr.switches if s["t"] == 1000.0 + step * i]:
            cur = s["to"]
        opts = [standby.Option(g, f"{app(g)}:{g}", standby.BEST, 70.0 - k, "x")
                for k, g in enumerate(ranked) if g in cands and g != cur]
        tr.ticks.append({"t": 1000.0 + step * i, "cands": cands, "current": cur, "want": (wants or {}).get(i), "opts": opts})
    return tr


def both(tr, cfg, **kw):
    return {a: qr.score_arm(tr, qr.simulate(a, tr, cfg, **kw), 90.0) for a in ("off", "on")}


def test_a_game_loaded_ahead_is_ready_when_the_cut_comes(cfg):
    tr = night({0: ["b"]}, {"a": None, "b": None}, [{"t": 30, "frm": "a", "to": "b", "kind": "switch"}])
    for arm, x in both(tr, cfg).items():
        assert (x["cuts"], x["ready"]) == (1, 1), arm
        assert x["waits"] == [0.0], arm


def test_a_cut_to_an_unloaded_game_waits_the_modeled_prep(cfg):
    tr = night({0: []}, {"a": None, "b": None}, [{"t": 30, "frm": "a", "to": "b", "kind": "switch"}])
    x = qr.score_arm(tr, qr.simulate("off", tr, cfg), 90.0)
    assert x["ready"] == 0
    assert x["waits"] == [pytest.approx(4.0 + 56.0)]   # gamequeue.Params youtubetv tune + buffer


def test_prime_costs_its_hold_not_its_buffer(cfg):
    tr = night({0: []}, {"a": None, "b": None}, [{"t": 30, "frm": "a", "to": "b", "kind": "switch"}],
               app=lambda g: "prime" if g == "b" else "youtubetv")
    x = qr.score_arm(tr, qr.simulate("on", tr, cfg), 90.0)
    assert x["waits"] == [pytest.approx(45.0 + 75.0)]   # tune 45 + max(buffer 56, behind_s 75)


def test_replays_and_resumes_are_not_live_cuts(cfg):
    tr = night({0: []}, {"a": None, "b": None},
               [{"t": 20, "frm": "a", "to": "b", "kind": "replay"}, {"t": 30, "frm": "b", "to": "a", "kind": "resume"}])
    assert qr.score_arm(tr, qr.simulate("on", tr, cfg), 90.0)["cuts"] == 0


def test_wanted_time_is_when_the_decider_started_waiting(cfg):
    tr = night({0: ["b"]}, {"a": None, "b": None}, [{"t": 40, "frm": "a", "to": "b", "kind": "switch"}],
               wants={i: "b" for i in range(30, 40)})
    raw = qr.simulate("on", tr, cfg)
    assert raw["cuts"][0]["wanted_at"] == 1000.0 + 10 * 30
    assert raw["wanted_ticks"] == 10


def test_idle_stick_while_a_game_is_wanted_is_counted(cfg):
    # the planner is told nothing is worth loading, a cut is wanted from tick 20: the stick sits free
    tr = night({0: []}, {"a": None, "b": None}, [{"t": 30, "frm": "a", "to": "b", "kind": "switch"}],
               wants={i: "b" for i in range(20, 30)})
    raw = qr.simulate("off", tr, cfg)
    assert raw["wanted_ticks"] == 10 and raw["idle_ticks"] == 10


def test_the_queue_does_not_churn_the_stick_like_the_planner(cfg):
    # the ranking flips every tick between two games; the planner's hold timer lets it re-tune, the queue holds
    plan = {i: ["b", "c"] if i % 3 else ["c", "b"] for i in range(60)}
    tr = night(plan, {"a": None, "b": None, "c": None}, [])
    assert qr.simulate("on", tr, cfg)["loads"] <= qr.simulate("off", tr, cfg)["loads"]


def test_a_hidden_game_that_left_the_slate_blocks_the_queue_but_not_the_fix(cfg):
    """The flaw this harness found: gamequeue.tick says hidden_unknown when the stick holds a game that is no longer
    an entry, and only an owner may take the stick, so a plain want never does."""
    games = {"a": None, "b": 12, "c": None}   # b is loaded first, then ends at tick 12
    plan = {0: ["b"], 13: ["c"]}
    tr = night(plan, games, [{"t": 40, "frm": "a", "to": "c", "kind": "switch"}])
    stock = qr.simulate("on", tr, cfg)
    fixed = qr.simulate("on", tr, cfg, free_gone="any")
    off = qr.simulate("off", tr, cfg)
    assert off["cuts"][0]["ready"] and fixed["cuts"][0]["ready"]
    assert not stock["cuts"][0]["ready"]
    assert "hidden_unknown" in stock["why"]
    assert qr.simulate("on", tr, cfg, free_gone="ended")["cuts"][0]["ready"]   # b is final, so this is the safe form
    assert not qr.flaws(tr, fixed, off) and qr.flaws(tr, stock, off)[0]["at_window_start"]["why"] == "hidden_unknown"


def test_a_swap_just_before_the_game_is_needed_is_counted(cfg):
    # b is ready, the planner swaps it for c at tick 20, the decider cuts to b at tick 25
    tr = night({0: ["b"], 20: ["c"]}, {"a": None, "b": None, "c": None},
               [{"t": 25, "frm": "a", "to": "b", "kind": "switch"}])
    x = qr.score_arm(tr, qr.simulate("off", tr, cfg), 90.0)
    assert x["swaps_wanted_soon"] >= 1 and x["ready_swaps_wanted_soon"] >= 1


def test_failures_are_drawn_the_same_for_both_arms_and_the_queue_answers_by_the_table(cfg):
    games = {"a": None, "b": None}
    tr = night({0: ["b"]}, games, [{"t": 55, "frm": "a", "to": "b", "kind": "switch"}])
    seen = set()
    for seed in range(60):
        on = qr.simulate("on", tr, cfg, fail_on=True, seed=seed)
        off = qr.simulate("off", tr, cfg, fail_on=True, seed=seed)
        first = lambda r: [(f["game"], f["kind"]) for f in r["failures"][:1]]  # noqa: E731
        assert first(on) == first(off)   # the first draw is keyed on (night, game, attempt)
        for f in on["failures"]:
            if "step" in f:
                seen.add((f["kind"], f["step"]))
    assert ("never_started", "retry") in seen
    assert all(step == gq.on_failure(gq.Entry("b", (gq.Route("youtubetv", "b"),)), kind).step.value
               or kind == "never_started" for kind, step in seen)


def test_a_walled_route_is_not_pressed_again_by_the_queue(cfg, monkeypatch):
    monkeypatch.setattr(qr, "FAIL_RATES", {"youtubetv": ((gq.Failure.WALLED, 1.0),)})
    tr = night({0: ["b"]}, {"a": None, "b": None}, [], ticks=80)
    on, off = qr.simulate("on", tr, cfg, fail_on=True), qr.simulate("off", tr, cfg, fail_on=True)
    assert on["loads"] == 1                      # on_failure(WALLED) drops the route: never pressed again
    assert off["loads"] > on["loads"]            # today's path sits out 120 s, then tries the same door
    assert {f["step"] for f in on["failures"] if "step" in f} == {"stop"}


def test_summarize_reports_the_rates_and_labels_nothing_it_cannot_count():
    rows = [{"cuts": 4, "ready": 3, "waits": [0.0, 0.0, 10.0, 60.0], "wanted_ticks": 10, "idle_ticks": 1, "swaps": 5,
             "swaps_wanted_soon": 1, "ready_swaps": 2, "ready_swaps_wanted_soon": 1, "loads": 6, "failures": []}]
    s = qr.summarize(rows)
    assert s["ready_in_time_pct"] == 75.0 and s["idle_while_wanted_pct"] == 10.0
    assert (s["wanted_to_ready_p50_s"], s["wanted_to_ready_p90_s"]) == (10.0, 60.0)
    assert qr.summarize([{"cuts": 0, "ready": 0, "waits": [], "wanted_ticks": 0, "idle_ticks": 0, "swaps": 0,
                          "swaps_wanted_soon": 0, "ready_swaps": 0, "ready_swaps_wanted_soon": 0, "loads": 0,
                          "failures": []}])["ready_in_time_pct"] is None


def test_the_table_says_the_times_are_modeled():
    assert "MODELED" in qr.table({"leagues": {}})


def test_the_scenario_replay_still_runs():
    assert len(qr.replay(qr.BUILTIN)) == len(qr.BUILTIN["ticks"])
