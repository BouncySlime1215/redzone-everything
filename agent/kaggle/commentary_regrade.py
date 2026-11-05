#!/usr/bin/env python3
"""[EXP-1404, Data planner E] Re-grade the EXP-1402 commentary importance on REAL outcomes (EXP-302 y_out).

EXP-1402 graded the Teacher's causal 0-10 importance against `big_next`, and basketball came back at 0.955 (NBA) and
0.957 (CBB) while every other league sat near 0.58. For NBA and CBB, `big_next` is not a real outcome: a "big" play
is a field goal in the last 2:00 of the 4th quarter (CBB: 2nd half) with the margin within 5
(redzone.grading.is_big), so the label is our own clock rule and the Teacher, which sees period, clock and score,
only has to read it back (the Nov 4 CBB lock leak again). This job re-labels the exact same plays with lane 3's
outcome label (redzone.lab.outcome_label, #278: lead change or a 20-point win-probability swing in the next few
minutes) and asks whether importance ranks that, and whether it does so beyond period + clock + margin.

Two steps, so the private Teacher answers never leave this side and ESPN is only read where it is reachable:

    # Kaggle / Colab CPU (ESPN public site API): label every play of the answered games
    python agent/kaggle/commentary_regrade.py label --games GAMES.json --out labels.jsonl.gz
    # anywhere: join answers to labels and grade
    python agent/kaggle/commentary_regrade.py grade --answers teacher_out.jsonl --labels labels.jsonl.gz --out OUT

GAMES.json = {league: {espn game id: [play index, ...]}} (`games` writes it from teacher_out.jsonl; ids only, no text).
labels rows: id (league:gid:play index, the same index the moment builder uses), night (ET), period, clock_s,
abs_diff, crunch, big_next (the old label, rebuilt here as a join check), y_out, flip, wp_swing, close_finish.

Pre-registered (docs/experiments.md EXP-1404): per league, importance AUC on y_out with a 95% night-bootstrap CI,
and the within-context AUC (pairs only inside the same period / 2-minute clock / margin bin). Importance is
"real signal" in a league only if the within-context AUC CI lower bound is > 0.50; the 0.95 basketball number is
"our rule" if the old label's context-only AUC is >= 0.95 (outcome_label.CONTEXT_AUC_MAX). The join check must
reproduce EXP-1402's big_next AUC within 0.02 or the grade is VOID.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

SEED = 1404
LICENSE = "personal-use, not for commercial release"
OLD_H = {"nfl": 180.0, "cfb": 180.0, "nba": 120.0, "cbb": 120.0, "nhl": 180.0, "mlb": 300.0}   # reasoner_data.HORIZON_S
MIN_N = 600


# ------------------------------------------------------------------------------------------------ games
def games(answers: Path) -> dict[str, dict[str, list[int]]]:
    """{league: {game id: [play index, ...]}}: the answered moments, ids only (no Teacher text)."""
    out: dict[str, dict] = defaultdict(lambda: defaultdict(set))
    for line in open(answers):
        if line.strip():
            lg, gid, m = json.loads(line)["id"].split("|")[0].split(":")
            out[lg][gid].add(int(m))
    return {lg: {g: sorted(v) for g, v in sorted(d.items())} for lg, d in sorted(out.items())}


# ------------------------------------------------------------------------------------------------ label
def _night(data: dict) -> str:
    import datetime as dt
    try:
        d = data["header"]["competitions"][0]["date"]
        t = dt.datetime.fromisoformat(d.replace("Z", "+00:00")) - dt.timedelta(hours=5)
        return t.strftime("%Y%m%d")
    except (KeyError, IndexError, TypeError, ValueError):
        return "unknown"


def label_replay(lg: str, r, night: str) -> list[dict]:
    """Every play of one game: context at the play, the old big_next (join check) and the outcome label."""
    from redzone.grading import play_clock
    from redzone.lab import outcome_label as O
    from redzone.lab import sportsgrade
    from redzone.models import League
    from redzone.sources import espn
    league = League(lg)
    lab = O.label_game(league, r.plays, r.wall)
    big, prev = [], None
    for p in r.plays:
        big.append(bool(sportsgrade.is_big(league, p, prev)))
        prev = p
    n, h, out = len(r.plays), OLD_H[lg], []
    j = 0
    nxt = [math.inf] * (n + 1)
    for k in range(n - 1, -1, -1):
        nxt[k] = r.wall[k] if big[k] else nxt[k + 1]
    for i, p in enumerate(r.plays):
        hs, as_ = espn._int(p.get("homeScore")), espn._int(p.get("awayScore"))
        j = nxt[i + 1]
        out.append({"id": f"{lg}:{r.gid}:{i}", "night": night,
                    "period": espn._int(espn._get(p, "period", "number")),
                    "clock_s": play_clock(league, p),
                    "abs_diff": abs(hs - as_) if hs is not None and as_ is not None else None,
                    "crunch": int(bool(sportsgrade.is_crunch(league, p))),
                    "big_next": int(j - r.wall[i] <= h), **lab[i]})
    return out


def label(games_path: Path, out: Path, workers: int = 6) -> dict:
    import os
    import tempfile
    from concurrent.futures import ThreadPoolExecutor

    from agent.kaggle.build_moments_kaggle import ESPN, _client, _college_rules, _get
    from redzone.lab.backtest import Replay
    from redzone.models import League
    _college_rules()                      # the moment builder's college big/crunch rules (CBB halves -> Q2/Q4)
    want = json.loads(games_path.read_text())
    todo = [(lg, g) for lg, ids in want.items() for g in ids]

    def one(item):
        lg, gid = item
        keep = set(want[lg][gid]) if isinstance(want[lg], dict) else None
        with _client() as c:
            data = _get(c, f"https://site.api.espn.com/apis/site/v2/sports/{ESPN[lg]}/summary", {"event": gid})
        if not data or str((data.get("header") or {}).get("id", "")) != gid:
            return lg, gid, []
        fd, tmp = tempfile.mkstemp(suffix=f"_{gid}.json")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f)
            r = Replay.load(League(lg), Path(tmp))
            rows = label_replay(lg, r, _night(data)) if r else []
            return lg, gid, [x for x in rows if keep is None or int(x["id"].rsplit(":", 1)[1]) in keep]
        finally:
            os.unlink(tmp)

    stats: dict[str, dict] = defaultdict(lambda: {"games": 0, "missing": 0, "plays": 0})
    with gzip.open(out, "wt") as f, ThreadPoolExecutor(workers) as ex:
        for k, (lg, gid, rows) in enumerate(ex.map(one, todo)):
            s = stats[lg]
            s["games" if rows else "missing"] += 1
            s["plays"] += len(rows)
            for row in rows:
                f.write(json.dumps(row) + "\n")
            if k % 200 == 0:
                print(f"{k}/{len(todo)}", dict(stats), flush=True)
    return dict(stats)


# ------------------------------------------------------------------------------------------------ grade
def _auc(pairs: list[tuple[float, int]]) -> float:
    from redzone.lab.outcome_label import auc
    return auc([y for _, y in pairs], [s for s, _ in pairs]) if pairs else float("nan")


def ctx_bin(r: dict) -> tuple:
    c = r.get("clock_s")
    d = r.get("abs_diff")
    return (r.get("period"), -1 if c is None else int(c // 120), -1 if d is None else min(int(d), 12) // 3)


def within_auc(rows: list[dict], key: str) -> float:
    """AUC counting only (positive, negative) pairs that share a context bin: does importance rank the label
    beyond what period, clock and margin already say? Ties count half."""
    by: dict[tuple, list] = defaultdict(list)
    for r in rows:
        by[ctx_bin(r)].append((r["imp"], int(r[key])))
    num = den = 0.0
    for v in by.values():
        pos = sorted(s for s, y in v if y)
        neg = sorted(s for s, y in v if not y)
        if not pos or not neg:
            continue
        import bisect
        for s in pos:
            lo, hi = bisect.bisect_left(neg, s), bisect.bisect_right(neg, s)
            num += lo + 0.5 * (hi - lo)
        den += len(pos) * len(neg)
    return num / den if den else float("nan")


def context_auc_xfit(rows: list[dict], key: str) -> float:
    """period + clock + margin alone, rates fit on half the nights and scored on the other half (both ways)."""
    from redzone.lab.outcome_label import _bin, auc
    nights = sorted({r["night"] for r in rows})
    half = set(nights[::2])
    a = [r for r in rows if r["night"] in half]
    b = [r for r in rows if r["night"] not in half]
    ys, ps = [], []
    for fit, test in ((a, b), (b, a)):
        tot, pos = defaultdict(int), defaultdict(int)
        for r in fit:
            k = _bin(r.get("period"), r.get("clock_s"), r.get("abs_diff"))
            tot[k] += 1
            pos[k] += int(r[key])
        base = (sum(pos.values()) + 1) / (sum(tot.values()) + 2)
        for r in test:
            k = _bin(r.get("period"), r.get("clock_s"), r.get("abs_diff"))
            ps.append((pos[k] + base) / (tot[k] + 1) if k in tot else base)
            ys.append(int(r[key]))
    return auc(ys, ps)


def boot(rows: list[dict], fn, draws: int = 1000, seed: int = SEED) -> list[float] | None:
    by: dict[str, list] = defaultdict(list)
    for r in rows:
        by[r["night"]].append(r)
    nights = sorted(by)
    if len(nights) < 2:
        return None
    rng = random.Random(seed)
    vals = []
    for _ in range(draws):
        v = fn([x for _ in nights for x in by[rng.choice(nights)]])
        if v == v:
            vals.append(v)
    vals.sort()
    return [round(vals[int(0.025 * len(vals))], 4), round(vals[min(len(vals) - 1, int(0.975 * len(vals)))], 4)] \
        if vals else None


def grade(answers: Path, labels: Path, reported: dict | None = None, draws: int = 1000) -> dict:
    from redzone.noggin.synth import _json
    imp = {}
    for line in open(answers):
        if not line.strip():
            continue
        a = json.loads(line)
        j = _json(a.get("text") or "")
        if j is not None and isinstance(j.get("importance"), (int, float)):
            imp[a["id"].split("|")[0]] = float(j["importance"])
    rows: dict[str, list] = defaultdict(list)
    with gzip.open(labels, "rt") as f:
        for line in f:
            r = json.loads(line)
            if r["id"] in imp:
                rows[r["id"].split(":")[0]].append({**r, "imp": imp[r["id"]]})
    out = {}
    for lg, rs in sorted(rows.items()):
        pairs = lambda k, xs: [(x["imp"], int(x[k])) for x in xs]      # noqa: E731
        old = _auc(pairs("big_next", rs))
        rep = (reported or {}).get(lg)
        join_ok = rep is None or abs(old - rep) <= 0.02
        y = _auc(pairs("y_out", rs))
        w = within_auc(rs, "y_out")
        nc = [x for x in rs if not x["crunch"]]
        e = {"n": len(rs), "nights": len({x["night"] for x in rs}), "answered": sum(k.startswith(lg + ":") for k in imp),
             "y_out_rate": round(sum(x["y_out"] for x in rs) / len(rs), 4),
             "crunch_share": round(sum(x["crunch"] for x in rs) / len(rs), 4),
             "old_big_next": {"auc_rebuilt": round(old, 4), "auc_reported": rep, "join_ok": join_ok,
                              "context_only_auc": round(context_auc_xfit(rs, "big_next"), 4),
                              "auc_outside_crunch": round(_auc(pairs("big_next", nc)), 4)},
             "y_out": {"auc": round(y, 4), "ci95": boot(rs, lambda xs: _auc(pairs("y_out", xs)), draws),
                       "within_context_auc": round(w, 4), "within_ci95": boot(rs, lambda xs: within_auc(xs, "y_out"), draws),
                       "context_only_auc": round(context_auc_xfit(rs, "y_out"), 4),
                       "auc_outside_crunch": round(_auc(pairs("y_out", nc)), 4)}}
        ci = e["y_out"]["within_ci95"]
        e["old_label_is_our_rule"] = e["old_big_next"]["context_only_auc"] >= 0.95
        e["verdict"] = ("VOID (join check failed)" if not join_ok else
                        f"PENDING (n < {MIN_N})" if len(rs) < MIN_N else
                        "REAL SIGNAL beyond clock/score" if ci and ci[0] > 0.5 else "NO SIGNAL beyond clock/score")
        out[lg] = e
    return {"exp": "EXP-1404", "label": "y_out (EXP-302 outcome_label, #278)", "license": LICENSE, "seed": SEED,
            "bar": "within-context AUC CI lower > 0.50 = real signal; old label context-only AUC >= 0.95 = our rule; "
                   "rebuilt big_next AUC must match EXP-1402 within 0.02",
            "per_league": out}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("games")
    g.add_argument("--answers", type=Path, required=True)
    g.add_argument("--out", type=Path, required=True)
    lb = sub.add_parser("label")
    lb.add_argument("--games", type=Path, required=True)
    lb.add_argument("--out", type=Path, required=True)
    gr = sub.add_parser("grade")
    gr.add_argument("--answers", type=Path, required=True)
    gr.add_argument("--labels", type=Path, required=True)
    gr.add_argument("--reported", type=Path, default=None, help="EXP-1402 grades.json (for the join check)")
    gr.add_argument("--out", type=Path, required=True)
    gr.add_argument("--draws", type=int, default=1000)
    a = ap.parse_args(argv)
    if a.cmd == "games":
        a.out.write_text(json.dumps(games(a.answers)) + "\n")
    elif a.cmd == "label":
        print(json.dumps(label(a.games, a.out), indent=1))
    else:
        rep = None
        if a.reported:
            rep = {lg: e.get("importance_auc_vs_real_big_next")
                   for lg, e in json.loads(a.reported.read_text())["per_league"].items()}
        res = grade(a.answers, a.labels, rep, a.draws)
        a.out.mkdir(parents=True, exist_ok=True)
        (a.out / "grades.json").write_text(json.dumps(res, indent=1) + "\n")
        print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
