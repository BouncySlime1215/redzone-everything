"""Step-0 re-fit report for Picker v2: counts, repeat consistency, grouped cross-validated pairwise accuracy of the page
fit vs the league-only baseline vs the factorised model, and the active-vs-random replay. Aggregates only.

Why: WORKPLAN v2 D-01 and track D experiment D1 step 0 ("re-fit at the current answers and report repeat-consistency")
plus the D1 bars (+5 points over league-only, active +3 over random, 3 seeds of the split, at 300 situation answers).

Held-out pairwise accuracy: each held-out answer states (winner, loser) card pairs (the chosen card over each other card;
a rank's order over every later card; "same", rate rounds and skips state none); a model scores 1 when its utility
prefers the winner, 0.5 on a tie. Splits are k-fold over GROUPS (an answer and its repeats are one group, so a repeated
question never sits on both sides), one split per seed. The 95% interval is a group bootstrap: resample groups, recompute
each seed's pooled accuracy, average the seeds. Differences are paired (same resample). The report never contains card
text, team names or single answers. Nothing in the show imports this module.
"""
from __future__ import annotations

from collections import Counter

import numpy as np

from redzone.lab.taste.active_pairs import replay_curves
from redzone.lab.taste.factorised_bt import (
    FactorisedBT, FactorisedPrior, answer_events, answer_pairs, fit_page, pairwise_hits, repeat_root, usable,
)

D1_SITUATION_ANSWERS_FOR_BAR = 300


def _chosen_cid(a: dict):
    ans = a.get("answer") or {}
    ch = ans.get("choice")
    cards = a.get("cards") or []
    if isinstance(ch, int) and not isinstance(ch, bool) and 0 <= ch < len(cards):
        return cards[ch].get("cid")
    return None


def repeat_consistency(answers: list[dict]) -> dict:
    """Two definitions. page: the app's own (a repeat whose answer and original both carry a numeric choice and neither is
    "same": same card chosen = agree). two_card: stage5's (two-card duel/stay/pick rounds only)."""
    by = {str(a.get("id")): a for a in answers}
    n = agree = n2 = agree2 = 0
    for a in answers:
        o = by.get(str(a.get("repeatOf"))) if a.get("repeatOf") else None
        if not o:
            continue
        x, y = a.get("answer") or {}, o.get("answer") or {}
        if x.get("skip") or x.get("same") or y.get("skip") or y.get("same"):
            continue
        cx, co = _chosen_cid(a), _chosen_cid(o)
        if cx is None or co is None:
            continue
        n += 1
        agree += int(cx == co)
        if a.get("round") in ("duel", "stay", "pick") and len(a.get("cards") or []) == 2 == len(o.get("cards") or []):
            n2 += 1
            agree2 += int(cx == co)
    return {"page": {"agree": agree, "n": n}, "two_card": {"agree": agree2, "n": n2}}


def answer_counts(answers: list[dict]) -> dict:
    """How many answers exist, which the fit can use, and what kind of comparison each is."""
    fit_ok = [a for a in answers if answer_events(a)]
    cross = within = 0
    s5_situation = 0
    for a in fit_ok:
        cards = a.get("cards") or []
        sports = {(c.get("f") or {}).get("sport_key") for c in cards}
        if len(sports) > 1:
            cross += 1
        else:
            within += 1
        if any((c.get("f") or {}).get("state") not in (None, "live") or (c.get("f") or {}).get("storyline") for c in cards):
            s5_situation += 1
    return {"answers": len(answers), "usable_page": sum(1 for a in answers if usable(a)),
            "usable_fit": len(fit_ok), "hidden": sum(1 for a in answers if a.get("hidden")),
            "skipped": sum(1 for a in answers if (a.get("answer") or {}).get("skip")),
            "same": sum(1 for a in answers if (a.get("answer") or {}).get("same")),
            "repeats": sum(1 for a in answers if a.get("repeatOf")),
            "rounds": dict(Counter(str(a.get("round")) for a in answers)),
            "rounds_fit": dict(Counter(str(a.get("round")) for a in fit_ok)),
            "choice_events": sum(len(answer_events(a)) for a in answers),
            "held_out_pairs_total": sum(len(answer_pairs(a)) for a in answers),
            "sport_vs_sport": cross, "within_sport_situation": within,
            "stage5_situation_def": s5_situation,
            "groups": len(set(repeat_root(answers).values()))}


def grouped_folds(answers: list[dict], k: int, seed: int) -> list[int]:
    """Fold number per answer; every answer of a repeat group lands in the same fold."""
    root = repeat_root(answers)
    groups = sorted(set(root.values()))
    perm = np.random.default_rng(seed).permutation(len(groups))
    fold_of = {g: int(perm[i] % k) for i, g in enumerate(groups)}
    return [fold_of[root[str(a.get("id"))]] for a in answers]


def _models(prior: dict | None) -> dict:
    fp = FactorisedPrior(sport_mean=dict(prior or {}))
    return {
        "league_only": lambda ev: fit_page(ev, prior, sport_only=True).utility,
        "page_fit": lambda ev: fit_page(ev, prior).utility,
        "factorised": lambda ev: FactorisedBT(fp).fit(ev).utility,
    }


def cross_validate(answers: list[dict], prior: dict | None, seeds=(0, 1, 2), k: int = 5, tag: str = "") -> list[tuple]:
    """(seed, group, key, hit) for every held-out pair of every model over k grouped folds per seed."""
    root = repeat_root(answers)
    out = []
    models = _models(prior)
    for seed in seeds:
        fold = grouped_folds(answers, k, seed)
        for f in range(k):
            train = [a for a, x in zip(answers, fold) if x != f]
            test = [a for a, x in zip(answers, fold) if x == f]
            ev = [e for a in train for e in answer_events(a)]
            for name, make in models.items():
                u = make(ev)
                for a in test:
                    for h in pairwise_hits(u, answer_pairs(a)):
                        out.append((seed, root[str(a.get("id"))], name + tag, h))
    return out


def active_replay(answers: list[dict], prior: dict | None, seeds=(0, 1, 2), k: int = 5,
                  budgets=(10, 20, 30, 40, 60, 80)) -> tuple[list[tuple], dict]:
    """(seed, group, 'arm@budget', hit) records of the replay, plus the mean cards per selected answer by arm."""
    root = repeat_root(answers)
    fp = FactorisedPrior(sport_mean=dict(prior or {}))
    out, sizes = [], {}
    min_pool = None
    for seed in seeds:
        fold = grouped_folds(answers, k, seed)
        for f in range(k):
            train = [a for a, x in zip(answers, fold) if x != f]
            test = [a for a, x in zip(answers, fold) if x == f]
            pool_n = sum(1 for a in train if answer_events(a))
            min_pool = pool_n if min_pool is None else min(min_pool, pool_n)
            rng = np.random.default_rng(1000 * seed + f)
            curves = replay_curves(train, test, [b for b in budgets if b < pool_n], rng, fp)
            gids = [root[str(a.get("id"))] for a in test for _ in answer_pairs(a)]
            for c in curves:
                sizes.setdefault(c.arm, []).append(c.cards_per_answer)
                for b, hits in c.hits.items():
                    key = f"{c.arm}@{b if b in budgets else 'all'}"
                    out.extend((seed, g, key, h) for g, h in zip(gids, hits))
    return out, {"cards_per_answer_first20": {k: round(float(np.mean(v)), 3) for k, v in sizes.items()},
                 "min_train_pool": min_pool}


def bootstrap(records: list[tuple], keys: list[str], diffs: list[tuple[str, str]], n_boot: int = 2000,
              seed: int = 7) -> dict:
    """Mean over seeds of each key's pooled accuracy, with a 95% group-bootstrap interval; paired differences a - b."""
    groups = sorted({r[1] for r in records})
    gi = {g: i for i, g in enumerate(groups)}
    seeds = sorted({r[0] for r in records})
    si = {s: i for i, s in enumerate(seeds)}
    allk = sorted(set(keys) | {x for d in diffs for x in d})
    ki = {k: i for i, k in enumerate(allk)}
    S = np.zeros((len(allk), len(seeds), len(groups)))
    N = np.zeros_like(S)
    for s, g, k, h in records:
        if k in ki:
            S[ki[k], si[s], gi[g]] += h
            N[ki[k], si[s], gi[g]] += 1
    rng = np.random.default_rng(seed)
    W = rng.multinomial(len(groups), np.full(len(groups), 1.0 / len(groups)), size=n_boot).astype(float)

    def acc(w):                         # w: (B, G) -> (K, B)
        num = np.einsum("ksg,bg->ksb", S, w)
        den = np.einsum("ksg,bg->ksb", N, w)
        return (num / np.maximum(den, 1e-12)).mean(axis=1)
    point = acc(np.ones((1, len(groups))))[:, 0]
    boot = acc(W)
    out = {"keys": {}, "diffs": {}, "groups": len(groups), "seeds": len(seeds), "n_boot": n_boot}
    for k in keys:
        i = ki[k]
        lo, hi = np.percentile(boot[i], [2.5, 97.5])
        out["keys"][k] = {"acc": round(float(point[i]), 4), "lo": round(float(lo), 4), "hi": round(float(hi), 4),
                          "pairs_per_seed": int(N[i].sum() / len(seeds))}
    for a, b in diffs:
        d = boot[ki[a]] - boot[ki[b]]
        lo, hi = np.percentile(d, [2.5, 97.5])
        out["diffs"][f"{a} - {b}"] = {"diff": round(float(point[ki[a]] - point[ki[b]]), 4), "lo": round(float(lo), 4),
                                      "hi": round(float(hi), 4)}
    return out


def page_reproduction(answers: list[dict], prior: dict | None, exported: dict | None) -> dict:
    """Fit the ported page model on every answer and compare its weights with the page's own export (non-name terms)."""
    fit = fit_page([e for a in answers for e in answer_events(a)], prior)
    out = {"events": sum(len(answer_events(a)) for a in answers)}
    if exported:
        diffs = [abs(fit.weight(x["name"]) - float(x["weight"])) for x in exported.get("features", [])
                 if not str(x.get("name", "")).startswith("name:") and x.get("name") in fit.index]
        out.update({"compared_terms": len(diffs), "max_abs_weight_diff": round(max(diffs), 5) if diffs else None,
                    "page_choice_events": exported.get("choiceEvents"), "page_answers_used": exported.get("answersUsed"),
                    "page_consistency": exported.get("consistency")})
    return out


def factorised_summary(answers: list[dict], prior: dict | None) -> dict:
    """The factorised model fitted on every answer: sport weights with 95% intervals, family sensitivities, interactions."""
    m = FactorisedBT(FactorisedPrior(sport_mean=dict(prior or {}))).fit([e for a in answers for e in answer_events(a)])
    sw = {k: {"w": round(w, 3), "lo": round(float(w * np.exp(-1.96 * sd)), 3), "hi": round(float(w * np.exp(1.96 * sd)), 3)}
          for k, (w, sd) in m.sport_weights().items()}
    p = m.named_params()
    keep = [k for k in p if k.startswith("family:") or k in ("late_and_close*stakes", "big_chance*late",
                                                             "my_team*late_and_close", "incumbent")]
    return {"sport_weights": sw,
            "params": {k: {"mean": round(p[k][0], 3), "sd": round(p[k][1], 3)} for k in keep}}


def build_report(answers: list[dict], prior: dict | None, exported: dict | None = None, seeds=(0, 1, 2), k: int = 5,
                 n_boot: int = 2000) -> dict:
    """Everything the step-0 / D1 report needs, aggregates only."""
    counts = answer_counts(answers)
    rec = cross_validate(answers, prior, seeds, k) + cross_validate(answers, None, seeds, k, tag="_noprior")
    keys = ["league_only", "page_fit", "factorised", "league_only_noprior", "page_fit_noprior", "factorised_noprior"]
    cv = bootstrap(rec, keys, [("factorised", "league_only"), ("page_fit", "league_only"), ("factorised", "page_fit"),
                               ("factorised_noprior", "league_only_noprior")], n_boot=n_boot)
    rrec, sizes = active_replay(answers, prior, seeds, k)
    rkeys = sorted({r[2] for r in rrec}, key=lambda s: (s.split("@")[0], int(s.split("@")[1]) if s.split("@")[1].isdigit() else 10**6))
    budgets = sorted({x.split("@")[1] for x in rkeys}, key=lambda b: int(b) if b.isdigit() else 10**6)
    rdiffs = [(f"{arm}@{b}", f"random@{b}") for b in budgets for arm in ("bald", "max_var")
              if f"{arm}@{b}" in rkeys and f"random@{b}" in rkeys]
    replay = bootstrap(rrec, rkeys, rdiffs, n_boot=n_boot)
    replay.update(sizes)
    situ = counts["within_sport_situation"]
    return {"counts": counts, "consistency": repeat_consistency(answers), "cv": cv, "replay": replay,
            "page_reproduction": page_reproduction(answers, prior, exported),
            "factorised_full_fit": factorised_summary(answers, prior),
            "d1_bar": {"situation_answers": situ, "needed": D1_SITUATION_ANSWERS_FOR_BAR,
                       "judgeable": situ >= D1_SITUATION_ANSWERS_FOR_BAR},
            "settings": {"folds": k, "seeds": list(seeds), "n_boot": n_boot}}
