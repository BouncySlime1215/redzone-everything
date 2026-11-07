"""WV loop cycle 6 diagnosis: split each mistake-ledger episode's value lost (full oracle, combined objective) into
its parts: big plays, crunch plays, your-team crunch, dead-air steps, switch cost. FIT nights only, locked never read.
Needs redzone/lab/mistake_ledger.py (PR 349). Run from the repo root. personal-use, not for commercial release."""
import json, math, sys
from pathlib import Path
import numpy as np
ROOT = Path.cwd(); sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
from redzone.config import Config
from redzone.lab import mistake_ledger as ML, oracle as O, splits
from redzone.lab.backtest import feed_lag_knobs, run
from redzone.models import League
import eval_sports as ES, buffer_sweep as BS
lgn = sys.argv[1] if len(sys.argv) > 1 else "nba"; lg = League(lgn); buf = 90.0
fl = BS.feed_lag_for(lgn, "p90")
sp = splits.league_split(lgn, splits.archive_nights(ROOT / "data/archive" / lgn, None)); locked = set(sp.test)
cfg = Config(user_path=ROOT / "data/eval_config.yaml"); mine = {"WSH", "UNC"}
wb, wc, wm, wd, ws = O.OBJECTIVES["combined"]
out, tot = [], {}
for d, reps, targets in ES.nights(lg, 10_000, None, "fit"):
    ML.assert_not_locked(d, locked)
    rep = run(reps, cfg, mine, targets=targets, **feed_lag_knobs(fl, buf))
    n = O.Night(reps, mine, rep.lag_true, 5.0, rep.t0, rep.t1); steps = np.arange(n.t0, n.t1, O.STEP_S); tab = O.tables(n, steps)
    orc = O.solve(n, "combined", tab)
    R = wb * tab["big"] + wc * tab["crunch"] + wm * tab["mine"] - wd * tab["dead"]
    gids = [r.gid for r in reps]; gi = {g: i for i, g in enumerate(gids)}
    blk = max(1, int(math.ceil(n.tune_s / O.STEP_S))); K = len(steps)
    a_on = ML.on_screen(K, n.t0, O.STEP_S, rep.switches, gi, blk); o_on = ML.on_screen(K, n.t0, O.STEP_S, orc, gi, blk)
    sw = lambda ss: {int(round((s.t - n.t0) / O.STEP_S)) for s in ss if s.frm is not None}
    rows = ML.ledger(d, lgn, R, tab, gids, a_on, o_on, n.t0, O.STEP_S, locked=locked,
                     app_switch_steps=sw(rep.switches), oracle_switch_steps=sw(orc), blk=blk)
    def part(on, key, k0, k1):
        return float(sum(tab[key][on[k], k] for k in range(k0, k1) if on[k] >= 0))
    for r in rows:
        p = {key: round(w * (part(o_on, key, r.k0, r.k1) - part(a_on, key, r.k0, r.k1)), 3)
             for key, w in (("big", wb), ("crunch", wc), ("mine", wm), ("dead", -wd))}
        p["blackout"] = round(-sum(wd * 1 for k in range(r.k0, r.k1) if a_on[k] < 0) + sum(wd for k in range(r.k0, r.k1) if o_on[k] < 0), 3)
        out.append(dict(night=d, cause=r.cause, value_lost=round(r.value_lost, 3), steps=r.k1 - r.k0, **p))
        for key in ("big", "crunch", "mine", "dead"):
            tot.setdefault(r.cause, {}).setdefault(key, 0.0); tot[r.cause][key] += p[key]
    print(d, len(rows), flush=True)
Path("/mnt/project-files/wv-loop/decomp").mkdir(exist_ok=True)
Path(f"/mnt/project-files/wv-loop/decomp/episodes_{lgn}.jsonl").write_text("".join(json.dumps(x) + "\n" for x in out))
summ = {c: {k: round(v, 2) for k, v in t.items()} for c, t in tot.items()}
summ["_all"] = {k: round(sum(t[k] for t in tot.values()), 2) for k in ("big", "crunch", "mine", "dead")}
summ["_nights"] = len({x["night"] for x in out}); summ["_tag"] = "personal-use, not for commercial release"
Path(f"/mnt/project-files/wv-loop/decomp/summary_{lgn}.json").write_text(json.dumps(summ, indent=1)); print(json.dumps(summ, indent=1))
