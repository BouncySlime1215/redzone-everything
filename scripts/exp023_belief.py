"""[EXP-023] Belief tracker vs the DelayBook baseline, on DEV nights only (docs/prereg/exp-023.md).

    taskpolicy -b .venv/bin/python scripts/exp023_belief.py --data-root /path/to/redzone/data \
        --leagues nfl nba nhl --seeds 0 1 2 --out /tmp/exp023.json

Both arms run the same stress backtest (backtest.run): --true-delay 41, --lag-jitter, --real-lag, the
live break anchors (sync.break_anchor.enabled=true) and 6 s tune jitter, so both see the SAME anchors:
  baseline   DelayBook + its Kalman sync (redzone/decider/sync.py), what the flag-on anchors path runs
  belief     DelayBook + the belief tracker (redzone/noggin/belief.py)
Every on-screen tick's delay error is kept; the headline is the pooled |error| p95 per league, with a
night-cluster bootstrap 95% CI of the paired delta (belief - baseline). Rule #1 lines and landings come
from sportsgrade.grade against the TRUE screen delay, as scripts/eval_sports.py grades them.

Split: `fit` (train + valid) only. `--split test` is refused: locked nights are never read here.
Read-only on data/: it writes only --out.
Why: EXP-023 grader: belief tracker vs DelayBook on dev nights only (docstring, pre-reg commit).
"""
from __future__ import annotations

import argparse
import contextlib
import json
import random
import sys
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone.config import Config  # noqa: E402
from redzone.lab import backtest, evaldata, splits, sportsgrade  # noqa: E402
from redzone.models import League  # noqa: E402
from redzone.noggin.belief import BeliefDelayBook, FeedLagBelief  # noqa: E402

TRUE_DELAY = 41.0
MIN_NIGHTS = 8
BAR = 2.0          # seconds the p95 must fall by (pre-registered)
EVAL_END_FALLBACK = "20261101"


def load_nights(data: Path, league: League, split: str):
    """Every night of one split, as (night, replays, targets): eval_sports.nights() with a data root."""
    if split not in ("fit", "train", "valid"):
        raise SystemExit(f"split {split!r} refused: EXP-023 reads dev nights only (locked nights are never read)")
    from redzone.channels import ChannelMap
    from redzone.sources import espn
    arch = data / "archive" / league.value
    end = evaldata.window_end(league.value, EVAL_END_FALLBACK)
    keep = splits.nights_for(arch, split, end)
    cmap = ChannelMap(data / "_eval_channels.json")
    for sb in sorted(arch.glob("scoreboard_*.json")):
        night = sb.stem.split("_")[-1]
        if night not in keep:
            continue
        reps, targets = [], {}
        for e in json.loads(sb.read_text()).get("events", []):
            f = arch / f"summary_{e['id']}.json"
            r = backtest.Replay.load(league, f) if f.exists() else None
            if not r:
                continue
            reps.append(r)
            g = espn._parse_event(league, e)
            w = cmap.where(g) if g else None
            app = (w.target or "youtubetv:x").split(":")[0] if w else "youtubetv"
            targets[r.gid] = f"{app}:{r.gid}"
        if len(reps) >= 2:
            yield night, reps, targets


@contextlib.contextmanager
def arm(name: str, seed: int, rows: list):
    """Swap the DelayBook class for the belief arm, and capture the raw honesty rows of either arm."""
    orig_book, orig_hs = backtest.DelayBook, backtest.honesty_summary

    def capture(r):
        rows.extend(r)
        return orig_hs(r)
    backtest.honesty_summary = capture
    if name == "belief":
        BeliefDelayBook.belief_seed = seed
        backtest.DelayBook = BeliefDelayBook
    try:
        yield
    finally:
        backtest.DelayBook, backtest.honesty_summary = orig_book, orig_hs


def run_night(reps, targets, cfg, mine, seed: int, name: str) -> dict:
    rows: list = []
    with arm(name, seed, rows):
        rep = backtest.run(reps, cfg, mine, targets=targets, true_delay=TRUE_DELAY, lag_jitter=(3.0, 25.0),
                           feed_lag_model="measured", anchors=True, tune_jitter_s=6.0, seed=seed)
    card = sportsgrade.grade(reps, rep.switches, mine, rep.lag_true, 5.0, rep.t0, rep.t1, rep.replayed)
    cs = card.cut.summary()
    dead, land = card.cut.landings()
    return {"err": [round(abs(e), 3) for e, _ in rows], "within_2sigma": rep.sync_honesty.get("within_2sigma"),
            "err_p95": rep.sync_honesty.get("err_p95"), "entries": cs["entries"],
            "entries_mid_play": cs["entries_mid_play"], "cuts_high": len(card.cuts_high), "cuts": len(card.cuts),
            "landings": land, "landings_dead": dead, "plays_missed_entry": cs["plays_missed_entry"]}


def p95(xs: list[float]) -> float:
    if not xs:
        return float("nan")
    s = sorted(xs)
    return s[min(len(s) - 1, int(0.95 * len(s)))]


def boot(nights: list[str], stat, n: int = 2000, seed: int = 23) -> tuple[float, float]:
    """95% CI of stat(sample of nights), resampling whole nights with replacement."""
    rng = random.Random(seed)
    vals = sorted(stat([rng.choice(nights) for _ in nights]) for _ in range(n))
    return vals[int(0.025 * n)], vals[int(0.975 * n) - 1]


def summarize(per: dict) -> dict:
    """per[arm][night][seed] -> row. Every stat pools the 3 seeds of each night (the night is the cluster)."""
    nights = sorted(set(per["baseline"]) & set(per["belief"]))

    def pooled_p95(a, ns):
        return p95([e for n in ns for r in per[a][n].values() for e in r["err"]])

    def total(a, ns, k):
        return sum(r[k] for n in ns for r in per[a][n].values())

    def dead_rate(a, ns):
        return total(a, ns, "landings_dead") / max(1, total(a, ns, "landings"))

    out = {"nights": len(nights)}
    if not nights:
        return out
    for a in ("baseline", "belief"):
        out[a] = {"err_p95": round(pooled_p95(a, nights), 2),
                  "entries_mid_play": total(a, nights, "entries_mid_play"),
                  "cuts_high": total(a, nights, "cuts_high"), "cuts": total(a, nights, "cuts"),
                  "landings_dead_pct": round(100 * dead_rate(a, nights), 2),
                  "plays_missed_entry": total(a, nights, "plays_missed_entry"),
                  "entries": total(a, nights, "entries"),
                  "within_2sigma": round(sum(r["within_2sigma"] or 0 for n in nights for r in per[a][n].values())
                                         / max(1, sum(len(per[a][n]) for n in nights)), 4)}

    def d_p95(ns):
        return pooled_p95("belief", ns) - pooled_p95("baseline", ns)

    def d_dead(ns):
        return 100 * (dead_rate("belief", ns) - dead_rate("baseline", ns))

    def d_missed(ns):
        return total("belief", ns, "plays_missed_entry") - total("baseline", ns, "plays_missed_entry")
    out["delta_err_p95"] = round(d_p95(nights), 2)
    out["delta_err_p95_ci"] = [round(x, 2) for x in boot(nights, d_p95)]
    out["delta_dead_pct"] = round(d_dead(nights), 2)
    out["delta_dead_pct_ci"] = [round(x, 2) for x in boot(nights, d_dead)]
    out["delta_missed"] = d_missed(nights)
    out["delta_missed_ci"] = list(boot(nights, d_missed))
    c = out["belief"]
    lo, hi = out["delta_err_p95_ci"]
    if len(nights) < MIN_NIGHTS:
        verdict = "PENDING (under 8 dev nights)"
    else:
        ok = (out["delta_err_p95"] <= -BAR and hi < 0 and c["entries_mid_play"] == 0 and c["cuts_high"] == 0
              and out["delta_dead_pct_ci"][1] >= 0 and out["delta_missed_ci"][0] <= 0)
        verdict = "PASS" if ok else "FAIL"
    out["verdict"] = verdict
    return out


def feedlag_report(data: Path, league: League, split: str, cfg) -> dict:
    """[report-only] Per-game feed-lag belief vs the fixed 8 s the app assumes: |predicted - realized|
    for each play's lag, drawn from the league's MEASURED profile (as --real-lag does), in order."""
    from redzone.lab.timing import LagSampler, sources_in_use
    sampler = LagSampler(use=sources_in_use(cfg))
    if not sampler.has(league.value):
        return {"n": 0}
    e_fixed, e_belief = [], []
    for night, reps, _ in load_nights(data, league, split):
        for r in reps:
            rng = random.Random(1_000_003 + zlib.crc32(r.gid.encode()))
            fb = FeedLagBelief(seed=zlib.crc32(r.gid.encode()))
            prev = float("-inf")
            for w in r.wall:
                if not w:
                    continue
                prev = max(prev, w + sampler.draw(league.value, rng))
                lag = prev - w
                if fb.n >= 5:
                    e_belief.append(abs(fb.estimate()[0] - lag))
                    e_fixed.append(abs(8.0 - lag))
                fb.observe(lag, prev)
    return {"n": len(e_fixed), "fixed8_p95": round(p95(e_fixed), 2), "belief_p95": round(p95(e_belief), 2),
            "fixed8_p50": round(sorted(e_fixed)[len(e_fixed) // 2], 2) if e_fixed else None,
            "belief_p50": round(sorted(e_belief)[len(e_belief) // 2], 2) if e_belief else None}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", type=Path, default=ROOT / "data")
    ap.add_argument("--leagues", nargs="+", default=["nfl", "nba", "nhl"])
    ap.add_argument("--seeds", nargs="+", type=int, default=[0, 1, 2])
    ap.add_argument("--split", default="fit", help="fit (default), train or valid; test is refused")
    ap.add_argument("--nfl-split", default="valid",
                    help="NFL's split (pre-registered: valid, 11 Sundays; fit is ~6 h on the Mac)")
    ap.add_argument("--max-nights", type=int, default=0, help="smoke runs only: the first N nights")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    data = a.data_root
    pinned = data / "eval_config.yaml"
    cfg = Config(user_path=pinned if pinned.exists() else data / "config.yaml")
    if pinned.exists() and (cfg.rejected or not cfg.overrides):
        sys.exit(f"{pinned} has settings the app rejects ({cfg.rejected}): refusing to grade on a partial config")
    evaldata.use_shipped_switch_costs(cfg)
    cfg.data.setdefault("sync", {}).setdefault("break_anchor", {})["enabled"] = True
    result = {"exp": "EXP-023", "split": a.split, "seeds": a.seeds, "true_delay": TRUE_DELAY, "leagues": {}}
    t0 = time.time()
    for lg_name in a.leagues:
        lg = League(lg_name)
        mine = set(cfg.get(f"teams.{lg.value}") or ["WSH"])
        split = a.nfl_split if lg is League.NFL else a.split
        per = {"baseline": {}, "belief": {}}
        for k, (night, reps, targets) in enumerate(load_nights(data, lg, split)):
            if a.max_nights and k >= a.max_nights:
                break
            for seed in a.seeds:
                for name in ("baseline", "belief"):
                    per[name].setdefault(night, {})[seed] = run_night(reps, targets, cfg, mine, seed, name)
            print(f"{lg_name} {night}: base p95 "
                  f"{p95([e for r in per['baseline'][night].values() for e in r['err']]):.2f} belief p95 "
                  f"{p95([e for r in per['belief'][night].values() for e in r['err']]):.2f} "
                  f"({time.time() - t0:.0f}s)", flush=True)
        s = summarize(per)
        s["split"] = split
        s["feed_lag"] = feedlag_report(data, lg, split, cfg)
        s["per_night"] = {n: {name: {str(sd): {k: v for k, v in r.items() if k != "err"}
                                     for sd, r in per[name][n].items()} for name in per} for n in sorted(per["baseline"])}
        result["leagues"][lg_name] = s
        print(json.dumps({k: v for k, v in s.items() if k != "per_night"}, indent=1), flush=True)
        a.out.write_text(json.dumps(result, indent=1))
    a.out.write_text(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
