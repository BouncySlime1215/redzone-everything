"""EXP-027 harness: does surprise-valued catch-up (redzone/noggin/surprise.py) lift "Big plays live or
replayed within 3 min" without a single cut-in or mid-play entry? Pre-registration: docs/prereg/exp-027.md.

    python scripts/exp027_surprise.py --data-root <checkout with data/> --out exp027.json

Each league's dev nights (split `fit` = train + valid; the LOCKED `test` split is refused) are replayed
twice per seed through the real backtest (redzone/lab/backtest.run, the scorecards' honest knobs:
measured feed lag + 6 s tune jitter): baseline, and baseline + `noggin.surprise.enabled=true`. Nothing
else changes. Per night it keeps the counts the North Star reads (big, big_seen_3m, entries_mid_play,
plays_missed_entry, crunch, crunch_live) plus the surprise log (valued plays, replays, cut-ins).

Verdict per league (pre-registered): delta = candidate - baseline on big_seen_3m / big (pts), seed mean
>= +2.0 AND the paired night-bootstrap 95 % CI lower bound > 0 (nights resampled whole, counts
averaged over seeds); mid-play entries unchanged (0 cut-ins by surprise); crunch live not lower.
A league whose WP misses EXP-002's ECE <= 0.05, or with < 8 dev nights, is reported as PENDING.
The experiment passes when >= 2 of NFL / NBA / NHL PASS.

It only READS the pinned eval data under --data-root (data/archive, data/eval_config.yaml,
data/_eval_channels.json); it writes nothing but --out.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from redzone.config import Config  # noqa: E402
from redzone.lab import evaldata, splits, sportsgrade  # noqa: E402
from redzone.lab.backtest import run  # noqa: E402
from redzone.models import League  # noqa: E402
from redzone.noggin import surprise  # noqa: E402

LEAGUES = ("nfl", "nba", "nhl")
MIN_NIGHTS = 8            # experiments.md: >= 8 nights per league for a PASS
BAR_PTS = 2.0             # pre-registered: seed-mean lift on big_seen_3m (pts)
# EXP-002 dev ECE (docs/experiments.md row 2, frozen Nov 5 00:20 UTC; locked not read). The analytic WP
# that feeds surprise is trusted only where ECE <= 0.05; NBA passes on point but its dev is 2 nights.
ECE_DEV = {"nfl": 0.047, "nba": 0.047, "nhl": 0.116}
ECE_MAX = 0.05
KNOBS = dict(feed_lag_model="measured", tune_jitter_s=6.0)   # the scorecards' honest knobs (EXP-018)


def load_cfg(data_root: Path, sets: dict) -> Config:
    pinned = data_root / "data/eval_config.yaml"
    cfg = Config(user_path=pinned if pinned.exists() else data_root / "data/config.yaml")
    if pinned.exists() and (cfg.rejected or not cfg.overrides):
        sys.exit(f"{pinned} has settings the app rejects ({cfg.rejected}): refusing to grade on a partial config")
    evaldata.use_shipped_switch_costs(cfg)
    for key, val in sets.items():
        node = cfg.data
        *path, last = key.split(".")
        for part in path:
            node = node.setdefault(part, {})
        node[last] = yaml.safe_load(val) if isinstance(val, str) else val
    return cfg


def nights(league: str, data_root: Path, split: str, limit: int | None = None):
    """(night, replays, targets) for one split of the pinned window, via the scorecards' own loaders."""
    if split == "test":
        sys.exit("EXP-027 never reads the LOCKED test split (docs/grading.md rule 11)")
    if league == "nfl":
        import scripts.eval as E
        E.ROOT = data_root
        import redzone.lab.optimize as O
        end = evaldata.window_end("nfl", E.EVAL_END)
        arch = data_root / "data/archive/nfl"
        slates = O.load_slates(arch, end=end)
        keep = splits.nights_for(arch, split, end)
        night = {gid: nt for nt, ids in splits.archive_nights(arch, end).items() for gid in ids}
        out = [(night.get(s[0].gid), s, None) for s in slates if night.get(s[0].gid) in keep]
    else:
        import scripts.eval_sports as S
        S.ROOT = data_root
        end = evaldata.window_end(league, S.EVAL_END)
        out = list(S.nights(League(league), 10_000, end, split))
    return out[:limit] if limit else out


def one(league: str, reps, targets, cfg, mine: set[str], seed: int, sink: dict) -> dict:
    sink.update(valued=0, replays=0, holds=0, cutins=0)
    if league == "nfl":
        r = run(reps, cfg, mine, seed=seed, **KNOBS)
        cs = r.cut.summary()
        row = dict(big=r.big_plays, big_seen_3m=r.big_seen_3m, crunch=r.crunch, crunch_live=r.crunch_live,
                   entries=cs["entries"], entries_mid_play=cs["entries_mid_play"],
                   plays_missed_entry=getattr(r.cut, "plays_missed_entry", 0), cuts=len(r.mid_play_cuts),
                   replays=sum(1 for s in r.switches if s.kind == "replay"))
    else:
        rep = run(reps, cfg, mine, targets=targets, seed=seed, **KNOBS)
        card = sportsgrade.grade(reps, rep.switches, mine, rep.lag_true, 5.0, rep.t0, rep.t1, rep.replayed)
        cs = card.cut.summary()
        row = dict(big=card.big, big_seen_3m=card.big_seen_3m, crunch=card.crunch, crunch_live=card.crunch_live,
                   entries=cs["entries"], entries_mid_play=cs["entries_mid_play"],
                   plays_missed_entry=card.cut.plays_missed_entry, cuts=len(card.cuts),
                   replays=sum(1 for s in rep.switches if s.kind == "replay"))
    row.update(surprise_valued=sink["valued"], surprise_replays=sink["replays"], surprise_cutins=sink["cutins"])
    return row


def pct(a: float, b: float) -> float | None:
    return 100.0 * a / b if b else None


def boot_delta(rows: list[tuple[float, float, float]], n: int = 2000, seed: int = 27):
    """Paired night bootstrap of 100 * (sum on - sum off) / sum big. rows: (on, off, big) per night."""
    if not rows:
        return None, None, None
    point = pct(sum(r[0] for r in rows) - sum(r[1] for r in rows), sum(r[2] for r in rows))
    rng = random.Random(seed)
    ds = []
    for _ in range(n):
        s = [rows[rng.randrange(len(rows))] for _ in rows]
        b = sum(r[2] for r in s)
        if b:
            ds.append(100.0 * (sum(r[0] for r in s) - sum(r[1] for r in s)) / b)
    ds.sort()
    if not ds:
        return point, None, None
    return point, ds[int(0.025 * len(ds))], ds[min(len(ds) - 1, int(0.975 * len(ds)))]


def summarize(league: str, per: dict) -> dict:
    """per[seed][arm] = [row per night] (same night order in every arm)."""
    seeds = sorted(per)
    nights_ = [r["night"] for r in per[seeds[0]]["off"]]
    tot = lambda arm, k: sum(r[k] for s in seeds for r in per[s][arm])
    seed_deltas = []
    for s in seeds:
        big = sum(r["big"] for r in per[s]["off"])
        seed_deltas.append(pct(sum(r["big_seen_3m"] for r in per[s]["on"]) - sum(r["big_seen_3m"] for r in per[s]["off"]), big))
    # Cluster by night (an NFL Sunday holds two slates): average over seeds, then sum per night.
    def clustered(k_on: str, k_off: str, k_n: str) -> list[tuple[float, float, float]]:
        acc: dict = {}
        for i, night in enumerate(nights_):
            avg = lambda arm, k: sum(per[s][arm][i][k] for s in seeds) / len(seeds)
            a = acc.setdefault(night, [0.0, 0.0, 0.0])
            a[0] += avg("on", k_on)
            a[1] += avg("off", k_off)
            a[2] += avg("off", k_n)
        return [tuple(v) for v in acc.values()]

    point, lo, hi = boot_delta(clustered("big_seen_3m", "big_seen_3m", "big"))
    c_point, c_lo, c_hi = boot_delta(clustered("crunch_live", "crunch_live", "crunch"))
    mean_delta = sum(d for d in seed_deltas if d is not None) / max(1, sum(d is not None for d in seed_deltas))
    out = {
        "nights": len(set(nights_)), "units": len(nights_), "night_ids": nights_, "seeds": seeds,
        "big": tot("off", "big") / len(seeds),
        "big_seen_3m_off_pct": pct(tot("off", "big_seen_3m"), tot("off", "big")),
        "big_seen_3m_on_pct": pct(tot("on", "big_seen_3m"), tot("on", "big")),
        "delta_pts_seed_mean": mean_delta, "delta_pts_per_seed": seed_deltas,
        "delta_pts_ci95": [lo, hi], "delta_pts_pooled": point,
        "crunch_live_off_pct": pct(tot("off", "crunch_live"), tot("off", "crunch")),
        "crunch_live_on_pct": pct(tot("on", "crunch_live"), tot("on", "crunch")),
        "crunch_delta_pts": c_point, "crunch_delta_ci95": [c_lo, c_hi],
        "entries_mid_play_off": tot("off", "entries_mid_play"), "entries_mid_play_on": tot("on", "entries_mid_play"),
        "plays_missed_entry_off": tot("off", "plays_missed_entry"), "plays_missed_entry_on": tot("on", "plays_missed_entry"),
        "replays_off": tot("off", "replays"), "replays_on": tot("on", "replays"),
        "surprise_valued": tot("on", "surprise_valued"), "surprise_replays": tot("on", "surprise_replays"),
        "surprise_cutins": tot("on", "surprise_cutins") + tot("off", "surprise_cutins"),
        "ece_dev": ECE_DEV.get(league),
    }
    guard_ok = (out["entries_mid_play_on"] <= out["entries_mid_play_off"] and out["surprise_cutins"] == 0
                and (out["crunch_live_on_pct"] or 0) >= (out["crunch_live_off_pct"] or 0) - 1e-9)
    lift_ok = mean_delta >= BAR_PTS and lo is not None and lo > 0
    if not guard_ok:
        verdict = "FAIL (guardrail)"
    elif ECE_DEV.get(league, 1.0) > ECE_MAX or len(set(nights_)) < MIN_NIGHTS:
        verdict = "PENDING" + (" (FAIL on the bar)" if not lift_ok else " (bar met)")
    else:
        verdict = "PASS" if lift_ok else "FAIL"
    out["verdict"] = verdict
    out["guardrails_ok"] = guard_ok
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data-root", type=Path, default=ROOT, help="checkout whose data/ holds the pinned eval set")
    ap.add_argument("--leagues", nargs="+", default=list(LEAGUES), choices=LEAGUES)
    ap.add_argument("--seeds", nargs="+", type=int, default=[0, 1, 2])
    ap.add_argument("--split", default="fit", choices=("train", "valid", "fit"))
    ap.add_argument("--limit", type=int, default=None, help="first N nights only (smoke runs; not a result)")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()
    sink: dict = {}
    uninstall = surprise.install(sink=sink)
    res = {"exp": "EXP-027", "split": a.split, "knobs": KNOBS, "seeds": a.seeds,
           "bar": {"delta_pts_seed_mean": BAR_PTS, "ci_lo_gt": 0.0, "leagues_needed": 2},
           "leagues": {}}
    t0 = time.time()
    try:
        for lg in a.leagues:
            ns = nights(lg, a.data_root, a.split, a.limit)
            per: dict = {}
            for seed in a.seeds:
                per[seed] = {}
                for arm, sets in (("off", {}), ("on", {surprise.FLAG: True})):
                    cfg = load_cfg(a.data_root, sets)
                    mine = set(cfg.get(f"teams.{lg}") or ["WSH"])
                    rows = []
                    for night, reps, targets in ns:
                        rows.append({"night": night, **one(lg, reps, targets, cfg, mine, seed, sink)})
                    per[seed][arm] = rows
                    print(f"[{time.time() - t0:6.0f}s] {lg} seed {seed} {arm}: "
                          f"{sum(r['big_seen_3m'] for r in rows)}/{sum(r['big'] for r in rows)} big<=3m, "
                          f"mid-play {sum(r['entries_mid_play'] for r in rows)}, "
                          f"surprise valued {sum(r['surprise_valued'] for r in rows)} "
                          f"replays {sum(r['surprise_replays'] for r in rows)} "
                          f"cut-ins {sum(r['surprise_cutins'] for r in rows)}", flush=True)
            res["leagues"][lg] = {"summary": summarize(lg, per), "per_seed": per}
    finally:
        uninstall()
    passes = [lg for lg, v in res["leagues"].items() if v["summary"]["verdict"] == "PASS"]
    res["passes"] = passes
    res["verdict"] = "PASS" if len(passes) >= 2 else (
        "PENDING" if any(v["summary"]["verdict"].startswith("PENDING") for v in res["leagues"].values())
        and len(passes) + sum(v["summary"]["verdict"] == "PENDING (bar met)" for v in res["leagues"].values()) >= 2
        else "FAIL")
    text = json.dumps(res, indent=1, default=float)
    if a.out:
        a.out.write_text(text)
    print(json.dumps({lg: v["summary"] | {"night_ids": None} for lg, v in res["leagues"].items()}
                     | {"verdict": res["verdict"]}, indent=1, default=float))


if __name__ == "__main__":
    main()
