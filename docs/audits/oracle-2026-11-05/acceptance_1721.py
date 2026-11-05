"""EXP-1721 acceptance (graded once, FIT nights of eval-data-v1; locked never read): does the v2 oracle reward show
more of the human highlight set (crew H recap plays) than v1? Criteria 1-3 of docs/prereg/exp-1721.md.
v2 thresholds as frozen in the prereg: NBA p99 0.127, NFL p99 = nflverse 2023-24 REG |wpa| p99 = 0.1533.
WP per play: crew G attention table (wp_home, keyed gid + play index). personal-use, not for commercial release."""
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[3]; sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
import eval_sports as ES  # noqa: E402
from redzone.lab import oracle as O, sportsgrade  # noqa: E402


def on_screen(K, t0, step_s, switches, gid_index, blk):
    on = np.full(K, -1); sw = sorted(switches, key=lambda s: s.t)
    for j, s in enumerate(sw):
        k0 = int(round((s.t - t0) / step_s)) + blk
        k1 = int(round((sw[j + 1].t - t0) / step_s)) if j + 1 < len(sw) else K
        if s.to in gid_index:
            on[max(0, k0):max(0, min(K, k1))] = gid_index[s.to]
    return on
from redzone.models import League  # noqa: E402
P99 = {"nba": 0.127, "nfl": 0.1533}
O.OBJECTIVES["v2"] = (1.0, 0.05, 100.0, 0.002, 0.02)        # prereg interim weights (runtime only, file untouched)
HL = Path("/mnt/project-files/wv-loop/highlights")
mine, LAG, TUNE = {"WSH", "UNC"}, 35.0, 5.0
out = {}
for lg in ("nba", "nfl"):
    league = League(lg)
    wp = pd.read_parquet(f"/mnt/project-files/attention-g/table/{lg}.parquet", columns=["gid", "i", "split", "wp_home"])
    wp = {(str(g), int(i)): float(w) for g, i, w in zip(wp.gid, wp.i, wp.wp_home)}
    hl = pd.read_csv(HL / f"{lg}_recap_highlights.csv")
    hl_keys = {(str(g), int(s)) for g, s in zip(hl.gid, hl.seq)}
    hl5 = {(str(g), int(s)) for g, s, m in zip(hl.gid, hl.seq, hl.match_score) if m >= 5}
    per = {}
    zero = {"v1": 0, "v2": 0, "games": 0}
    for day, reps, _ in ES.nights(league, 10_000, None, "fit"):
        t0 = min(r.wall[0] for r in reps) - 600.0; t1 = max(r.wall[-1] for r in reps) + 600.0
        n = O.Night(reps, mine, LAG, TUNE, t0, t1)
        steps = np.arange(t0, t1, O.STEP_S); K = len(steps)
        tab1 = O.tables(n, steps)
        big2 = np.zeros_like(tab1["big"])
        where = []                                               # (game, step, key) of every play
        for g, r in enumerate(reps):
            prev, prev_wp = None, None
            plen = sportsgrade.PLAY_LEN[r.league]
            for i, (w, p) in enumerate(zip(r.wall, r.plays)):
                k = int((w + LAG - plen - t0) // O.STEP_S)
                key = (str(r.gid), int(p.get("sequenceNumber") or -1))
                cur = wp.get((str(r.gid), i))
                d = abs(cur - prev_wp) if cur is not None and prev_wp is not None else 0.0
                live = prev_wp is not None and 0.05 <= prev_wp <= 0.95
                v1 = sportsgrade.is_big(r.league, p, prev)
                scoring = bool(p.get("scoringPlay"))
                b = live and (d >= P99[lg] or ((scoring or v1) and d >= 0.02) or (lg == "nba" and v1))
                if 0 <= k < K:
                    where.append((g, k, key))
                    if b:
                        big2[g, k] += max(d, 0.02)
                prev, prev_wp = p, (cur if cur is not None else prev_wp)
        tab2 = dict(tab1, big=big2)
        for g in range(len(reps)):
            zero["games"] += 1
            zero["v1"] += (3 * tab1["big"][g].sum() + tab1["crunch"][g].sum()) == 0
            zero["v2"] += (big2[g].sum() + 0.05 * tab1["crunch"][g].sum()) == 0
        gi = {r.gid: i for i, r in enumerate(reps)}; blk = 1
        res = {}
        for name, obj, tab in (("v1", "combined", tab1), ("v2", "v2", tab2)):
            on = on_screen(K, t0, O.STEP_S, O.solve(n, obj, tab), gi, blk)
            shown = {key for g, k, key in where if on[k] == g}
            res[name] = (len(shown & hl_keys), len(shown & hl5))
        per[day] = dict(v1=res["v1"], v2=res["v2"], n=len(hl_keys & {key for _, _, key in where}),
                        n5=len(hl5 & {key for _, _, key in where}))
        print(lg, day, per[day], flush=True)
    ns = [d for d in per if per[d]["n"] > 0]
    def rec(name, ix, nk, ks):
        return 100 * sum(per[d][name][ix] for d in ks) / max(1, sum(per[d][nk] for d in ks))
    rng = np.random.default_rng(1721); diffs = []
    for _ in range(5000):
        ks = list(rng.choice(ns, len(ns)))
        diffs.append(rec("v2", 0, "n", ks) - rec("v1", 0, "n", ks))
    out[lg] = dict(nights=len(ns), highlight_plays=sum(per[d]["n"] for d in ns),
                   recall_v1=round(rec("v1", 0, "n", ns), 1), recall_v2=round(rec("v2", 0, "n", ns), 1),
                   diff=round(rec("v2", 0, "n", ns) - rec("v1", 0, "n", ns), 1),
                   ci95=[round(x, 1) for x in np.percentile(diffs, [2.5, 97.5])],
                   strict_match5=dict(recall_v1=round(rec("v1", 1, "n5", ns), 1), recall_v2=round(rec("v2", 1, "n5", ns), 1)),
                   games_zero_value_pct=dict(v1=round(100 * zero["v1"] / zero["games"], 1), v2=round(100 * zero["v2"] / zero["games"], 1)))
out["tag"] = "personal-use, not for commercial release"; out["split"] = "fit (train+valid) of eval-data-v1; locked never read"
print(json.dumps(out, indent=1))
(Path(__file__).parent / "acceptance_1721.json").write_text(json.dumps(out, indent=1))
