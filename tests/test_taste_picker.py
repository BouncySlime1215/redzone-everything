"""Picker v2 taste lab (redzone/lab/taste): page-fit port, factorised Bradley-Terry, active pairs, step-0 report.

All answers here are synthetic (planted preferences); Nick's real answers never enter the repo.
"""
from __future__ import annotations

import json

import numpy as np
import pytest

from redzone.lab.taste import active_pairs as ap
from redzone.lab.taste import factorised_bt as fbt
from redzone.lab.taste import picker_report as pr

SPORTS = ["nba", "nfl", "nhl", "mlb", "soc_m", "golf"]
PLANT_SPORT = {"nba": 0.0, "nfl": 0.8, "nhl": -0.4, "mlb": -0.7, "soc_m": -1.2, "golf": -0.9}
PLANT_FAMILY = {"football": 2.0, "hoops": 1.0, "hockey": 1.0, "baseball": 1.0, "soccer": 0.3, "other": 1.0}
PLANT_MOMENT = {"phase:final_minutes": 0.9, "late_and_close": 0.6, "state:dead_timeout": -1.2, "state:big_chance": 0.5,
                "margin:blowout": -1.0, "season:playoff": 0.7}


def _f(rng, sport=None):
    return {"sport_key": sport or str(rng.choice(SPORTS)),
            "season_type": str(rng.choice(["regular", "regular", "playoff", "rivalry_or_national"])),
            "phase": str(rng.choice(["early", "middle", "late", "final_minutes"])),
            "margin_bucket": str(rng.choice(["tied", "one_score", "close", "comfortable", "blowout"])),
            "state": str(rng.choice(["live", "big_chance", "dead_timeout", "just_after_big_play"])),
            "storyline": None, "storyline_strength": None, "my_team": False, "my_bet": "none",
            "peak": False, "teams": ["Alpha", "Beta"]}


def _true_u(f, incumbent=False):
    terms = fbt.card_terms_page(f, incumbent)
    m = sum(PLANT_MOMENT.get(t, 0.0) for t in terms)
    return PLANT_SPORT[f["sport_key"]] + PLANT_FAMILY[fbt.PICKER_SPORT_FAMILY[f["sport_key"]]] * m \
        + (0.3 if incumbent else 0.0)


def synth(n=300, seed=0, repeats=10):
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        rnd = str(rng.choice(["duel", "pick", "stay", "rank", "rate"], p=[0.4, 0.2, 0.2, 0.15, 0.05]))
        k = 2 if rnd in ("duel", "stay") else 3
        same_sport = rng.random() < 0.4
        s0 = str(rng.choice(SPORTS))
        fs = [_f(rng, s0 if same_sport else None) for _ in range(k)]
        cards = [{"cid": f"c{i}-{j}", "f": f, "d": {}} for j, f in enumerate(fs)]
        z = np.array([_true_u(f, rnd == "stay" and j == 0) for j, f in enumerate(fs)]) + rng.gumbel(size=k)
        if rnd == "rank":
            ans = {"order": [int(x) for x in np.argsort(-z)], "choice": int(np.argmax(z))}
        elif rnd == "rate":
            ans = {"rating": 3}
        elif rnd == "stay":
            ans = {"choice": int(np.argmax(z)), "stay": bool(np.argmax(z) == 0)}
        else:
            ans = {"choice": int(np.argmax(z))}
        out.append({"id": f"a{i}", "round": rnd, "mode": "random", "repeatOf": None, "cards": cards, "answer": ans})
    for r in range(repeats):
        o = next(a for a in out[r * 7:] if a["round"] == "duel")
        out.append(dict(o, id=f"r{r}", mode="repeat", repeatOf=o["id"], cards=list(reversed(o["cards"])),
                        answer={"choice": 1 - o["answer"]["choice"]}))
    out.append({"id": "skip1", "round": "duel", "cards": out[0]["cards"], "answer": {"skip": True, "unknownSport": "x"}})
    return out


def test_page_terms_match_the_page_cardfeats():
    f = {"sport_key": "nfl", "season_type": "playoff", "phase": "final_minutes", "margin_bucket": "one_score",
         "state": "big_chance", "storyline": "comeback", "storyline_strength": "strong", "my_team": True,
         "my_bet": "sweating", "peak": True, "teams": ["Alpha"]}
    assert fbt.card_terms_page(f, incumbent=True) == [
        "sport:nfl", "season:playoff", "phase:final_minutes", "margin:one_score", "late_and_close", "state:big_chance",
        "story:comeback", "story_strong", "my_team", "bet:sweating", "incumbent", "peakx:nfl", "name:nfl:Alpha"]
    ref = {"sport_key": "nba", "season_type": "regular", "phase": "early", "margin_bucket": "close", "state": "live",
           "teams": []}
    assert fbt.card_terms_page(ref) == []


def test_answer_events_follow_the_page_rules():
    rng = np.random.default_rng(1)
    cards = [{"cid": f"c{j}", "f": _f(rng)} for j in range(3)]
    duel = {"id": "d", "round": "duel", "cards": cards[:2], "answer": {"choice": 1}}
    same = {"id": "s", "round": "pick", "cards": cards, "answer": {"same": True}}
    stay = {"id": "t", "round": "stay", "cards": cards[:2], "answer": {"choice": 0, "stay": True}}
    rank = {"id": "r", "round": "rank", "cards": cards, "answer": {"order": [2, 0, 1]}}
    rate = {"id": "x", "round": "rate", "cards": cards[:1], "answer": {"rating": 4}}
    skip = {"id": "k", "round": "duel", "cards": cards[:2], "answer": {"skip": True}}
    assert [list(e.target) for e in fbt.answer_events(duel)] == [[0.0, 1.0]]
    assert np.allclose(fbt.answer_events(same)[0].target, 1 / 3)
    ev = fbt.answer_events(stay)
    assert list(ev[0].target) == [1.0, 0.0] and "incumbent" in ev[0].alts[0].terms
    assert "incumbent" not in ev[0].alts[1].terms
    ev = fbt.answer_events(rank)
    assert [len(e.alts) for e in ev] == [3, 2] and list(ev[0].target) == [0, 0, 1] and list(ev[1].target) == [1, 0]
    assert fbt.answer_events(rate) == [] and fbt.answer_events(skip) == []
    assert len(fbt.answer_pairs(rank)) == 3 and fbt.answer_pairs(same) == [] and len(fbt.answer_pairs(duel)) == 1


def test_repeat_groups_never_split_across_folds():
    ans = synth(120, seed=2)
    root = fbt.repeat_root(ans)
    assert root["r0"] != "r0"
    for seed in range(3):
        fold = pr.grouped_folds(ans, 5, seed)
        by = {}
        for a, f in zip(ans, fold):
            by.setdefault(root[a["id"]], set()).add(f)
        assert all(len(v) == 1 for v in by.values())


def test_page_fit_learns_planted_sport_order():
    ans = synth(400, seed=3)
    fit = fbt.fit_page([e for a in ans for e in fbt.answer_events(a)])
    assert fit.weight("sport:nfl") > 0 > fit.weight("sport:soc_m")
    assert fit.weight("state:dead_timeout") < 0
    only = fbt.fit_page([e for a in ans for e in fbt.answer_events(a)], sport_only=True)
    assert all(n.startswith("sport:") for n in only.names)


def test_factorised_recovers_family_sensitivity_and_beats_league_only():
    train, test = synth(500, seed=4, repeats=0), synth(200, seed=5, repeats=0)
    ev = [e for a in train for e in fbt.answer_events(a)]
    m = fbt.FactorisedBT().fit(ev)
    p = m.named_params()
    assert p["family:football"][0] > p["family:soccer"][0] + 0.5
    assert p["state:dead_timeout"][0] < 0 and p["phase:final_minutes"][0] > 0
    pairs = [x for a in test for x in fbt.answer_pairs(a)]
    acc_f = np.mean(fbt.pairwise_hits(m.utility, pairs))
    acc_l = np.mean(fbt.pairwise_hits(fbt.fit_page(ev, sport_only=True).utility, pairs))
    assert acc_f > acc_l + 0.05
    assert np.all(np.linalg.eigvalsh(m.cov) > 0)


def test_prior_only_model_is_the_prior():
    m = fbt.FactorisedBT(fbt.FactorisedPrior(sport_mean={"sport:nfl": 0.5})).fit([])
    w = m.sport_weights()
    assert w["nfl"][0] == pytest.approx(np.exp(0.5)) and w["mlb"][0] == pytest.approx(1.0)
    assert m.named_params()["family:hoops"][0] == pytest.approx(1.0)


def test_posterior_tightens_with_more_answers():
    ans = synth(400, seed=6, repeats=0)
    ev = [e for a in ans for e in fbt.answer_events(a)]
    few = fbt.FactorisedBT().fit(ev[:60])
    many = fbt.FactorisedBT().fit(ev)
    assert np.trace(many.cov) < np.trace(few.cov)


def test_bald_gain_shape():
    assert ap.bald_gain(np.array([0.0]), np.array([0.0]))[0] == pytest.approx(0.0, abs=1e-9)
    g = ap.bald_gain(np.array([0.0, 3.0, 0.0]), np.array([2.0, 2.0, 8.0]))
    assert g[0] > g[1] and g[2] > g[0] and np.all(g >= 0)


def test_selector_prefers_the_uncertain_comparable_pair():
    ans = synth(200, seed=7, repeats=0)
    m = fbt.FactorisedBT().fit([e for a in ans for e in fbt.answer_events(a) if
                                all(c.sport != "golf" for c in e.alts)])
    nba = fbt.Card("nba", ())
    golf = fbt.Card("golf", ("sport:golf",))
    nfl = fbt.Card("nfl", ("sport:nfl",))
    # nba vs golf is unseen (golf never trained) -> most uncertain; nba vs nfl was seen many times
    assert ap.next_pair_index(m, [nba, nba], [nfl, golf], "bald") == 1
    assert ap.next_pair_index(m, [nba, nba], [nfl, golf], "max_var") == 1
    with pytest.raises(ValueError):
        ap.pair_gains(m, [nba], [nfl], "nope")


def test_replay_arms_share_start_and_meet_at_full_pool():
    train, test = synth(60, seed=8, repeats=0), synth(30, seed=9, repeats=0)
    curves = ap.replay_curves(train, test, [10, 20], np.random.default_rng(0))
    assert {c.arm for c in curves} == {"random", "bald", "max_var"}
    full = max(curves[0].hits)
    assert all(c.hits[full] == curves[0].hits[full] for c in curves)
    assert all(set(c.hits) == {10, 20, full} for c in curves)


def test_consistency_and_counts():
    ans = synth(80, seed=10, repeats=4)
    c = pr.repeat_consistency(ans)
    assert c["page"] == {"agree": 4, "n": 4} and c["two_card"]["n"] == 4
    k = pr.answer_counts(ans)
    assert k["answers"] == 85 and k["skipped"] == 1 and k["repeats"] == 4
    assert k["sport_vs_sport"] + k["within_sport_situation"] == k["usable_fit"]


def test_bootstrap_identical_keys_give_zero_difference():
    rec = [(s, g, key, float((g + s) % 3 == 0)) for s in range(2) for g in range(30) for key in ("a", "b")]
    out = pr.bootstrap(rec, ["a", "b"], [("a", "b")], n_boot=100)
    assert out["diffs"]["a - b"] == {"diff": 0.0, "lo": 0.0, "hi": 0.0}
    assert 0 < out["keys"]["a"]["lo"] <= out["keys"]["a"]["acc"] <= out["keys"]["a"]["hi"] < 1


def test_report_is_aggregates_only():
    ans = synth(90, seed=11, repeats=3)
    rep = pr.build_report(ans, {"sport:nfl": 0.3}, None, seeds=(0,), k=3, n_boot=50)
    text = json.dumps(rep)
    assert "Alpha" not in text and "Beta" not in text and '"cid"' not in text
    assert rep["d1_bar"]["judgeable"] is False
    assert set(rep["cv"]["keys"]) >= {"league_only", "page_fit", "factorised"}
    assert any(k.startswith("bald@") for k in rep["replay"]["keys"])


def test_script_runs_on_a_synthetic_file(tmp_path, capsys):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "picker_report_script", Path(__file__).resolve().parents[1] / "scripts" / "picker_report.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    p = tmp_path / "picks.json"
    p.write_text(json.dumps(synth(70, seed=12, repeats=2)))
    out = tmp_path / "r.json"
    assert mod.main([str(p), "--seeds", "1", "--folds", "3", "--n-boot", "30", "--out", str(out)]) == 0
    assert json.loads(out.read_text())["counts"]["answers"] == 73
    assert mod.main(["--package-pick-schema"]) == 0
    assert "PackagePickLabel" in capsys.readouterr().out
