"""EXP-1722 read (NBA only, v3 NEW fit nights, judge = crew H v3 recap set; harness = acceptance_1721 with data paths changed; the v2 reward now comes from redzone/lab/oracle_v2.py). Was: EXP-1721 acceptance (graded once, FIT nights of eval-data-v1; locked never read): does the v2 oracle reward show
more of the human highlight set (crew H recap plays) than v1? Criteria 1-3 of docs/prereg/exp-1721.md.
v2 thresholds as frozen in the prereg: NBA p99 0.127, NFL p99 = nflverse 2023-24 REG |wpa| p99 = 0.1533.
WP per play: crew G attention table (wp_home, keyed gid + play index). personal-use, not for commercial release."""
import json, os, sys
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[1]; sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
import eval_sports as ES  # noqa: E402
from redzone.lab import oracle as O, oracle_v2 as V, sportsgrade  # noqa: E402


def on_screen(K, t0, step_s, switches, gid_index, blk):
    on = np.full(K, -1); sw = sorted(switches, key=lambda s: s.t)
    for j, s in enumerate(sw):
        k0 = int(round((s.t - t0) / step_s)) + blk
        k1 = int(round((sw[j + 1].t - t0) / step_s)) if j + 1 < len(sw) else K
        if s.to in gid_index:
            on[max(0, k0):max(0, min(K, k1))] = gid_index[s.to]
    return on
from redzone.models import League  # noqa: E402
V.register()                                               # OBJECTIVE_V2 (runtime only, oracle.py untouched)
HL = Path("/mnt/project-files/wv-loop/highlights")
mine, LAG, TUNE = {"WSH", "UNC"}, 35.0, 5.0
out = {}
MC = {}
KEEP = set(open("/mnt/project-files/wv-loop/v3reads/nba_new_nights_2plus.txt").read().split())
for lg in ("nba",):
    league = League(lg)
    wpid = {}   # same source as crew G's wp_home: ESPN summary winprobability, by play id (sportsdata.norm_espn_archive)
    hl = pd.read_csv(HL / f"{lg}_recap_highlights_v3.csv")
    hl_keys = {(str(g), int(s)) for g, s in zip(hl.gid, hl.seq)}
    hl5 = {(str(g), int(s)) for g, s, m in zip(hl.gid, hl.seq, hl.match_score) if m >= 5}
    per = {}
    zero = {"v1": 0, "v2": 0, "games": 0}
    for day, reps, _ in ES.nights(league, 10_000, None, "fit"):
        if day not in KEEP:
            continue
        for r in reps:
            wpid[str(r.gid)] = V.wp_by_play(json.loads((ROOT / "data/archive" / lg / f"summary_{r.gid}.json").read_text()))
        t0 = min(r.wall[0] for r in reps) - 600.0; t1 = max(r.wall[-1] for r in reps) + 600.0
        n = O.Night(reps, mine, LAG, TUNE, t0, t1)
        steps = np.arange(t0, t1, O.STEP_S); K = len(steps)
        tab2 = V.tables(n, steps, wpid, LAG)                     # the frozen module produces the v2 reward
        tab1 = O.tables(n, steps)
        big2 = tab2["big"]
        where = []                                               # (game, step, key) of every play
        for g, r in enumerate(reps):
            plen = sportsgrade.PLAY_LEN[r.league]
            for w, p in zip(r.wall, r.plays):
                k = int((w + LAG - plen - t0) // O.STEP_S)
                if 0 <= k < K:
                    where.append((g, k, (str(r.gid), int(p.get("sequenceNumber") or -1))))
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
            MC.setdefault(name, [0, 0]); MC[name][0] += float(sum(tab1["mine"][on[k], k] for k in range(K) if on[k] >= 0)); MC[name][1] += float(tab1["mine"].sum())
        per[day] = dict(v1=res["v1"], v2=res["v2"], n=len(hl_keys & {key for _, _, key in where}),
                        n5=len(hl5 & {key for _, _, key in where}))
        print(lg, day, per[day], flush=True)
    ns = [d for d in per if per[d]["n"] > 0]
    def rec(name, ix, nk, ks):
        return 100 * sum(per[d][name][ix] for d in ks) / max(1, sum(per[d][nk] for d in ks))
    rng = np.random.default_rng(1722); diffs = []
    for _ in range(5000):
        ks = list(rng.choice(ns, len(ns)))
        diffs.append(rec("v2", 0, "n", ks) - rec("v1", 0, "n", ks))
    out[lg] = dict(nights=len(ns), highlight_plays=sum(per[d]["n"] for d in ns),
                   recall_v1=round(rec("v1", 0, "n", ns), 1), recall_v2=round(rec("v2", 0, "n", ns), 1),
                   diff=round(rec("v2", 0, "n", ns) - rec("v1", 0, "n", ns), 1),
                   ci95=[round(x, 1) for x in np.percentile(diffs, [2.5, 97.5])],
                   strict_match5=dict(recall_v1=round(rec("v1", 1, "n5", ns), 1), recall_v2=round(rec("v2", 1, "n5", ns), 1)),
                   games_zero_value_pct=dict(v1=round(100 * zero["v1"] / zero["games"], 1), v2=round(100 * zero["v2"] / zero["games"], 1)))
out["criterion3_your_team_crunch_plays_on_screen"] = MC
out["criterion3_mid_play_entries"] = "0 for both: the oracle lands only at land_ok (dead-ball) steps, same tables"
out["tag"] = "personal-use, not for commercial release"; out["split"] = "eval-data-v3 fit nights not in v1 (2025-10-21..2026-01-31 + 2026-10-29..31); locked (>=20261101) never read"
print(json.dumps(out, indent=1))
Path(os.environ.get("EXP1722_OUT", "/mnt/project-files/wv-loop/exp1722/exp1722_read_module.json")).write_text(json.dumps(out, indent=1))
